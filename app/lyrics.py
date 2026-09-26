"""Obter a letra sincronizada: primeiro no LRCLIB, senão transcrição com Whisper."""
import logging
import os
import re
from pathlib import Path
from typing import Callable

import httpx

log = logging.getLogger("karaoke.lyrics")

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "medium")
WHISPER_THREADS = int(os.environ.get("WHISPER_THREADS", max(2, (os.cpu_count() or 4) // 2)))
LRCLIB = "https://lrclib.net/api"
LRCLIB_HEADERS = {"User-Agent": "karaoke-em-casa/0.1 (https://github.com/)"}
# Diferença máxima de duração para aceitar uma letra do LRCLIB (versões diferentes
# da música, com intro mais longa, por ex., ficariam dessincronizadas).
DURATION_TOLERANCE = 4.0

# Frases que o Whisper "inventa" em silêncios (vêm das legendas com que foi treinado)
HALLUCINATIONS = (
    "amara.org", "legendas pela", "legendado por", "obrigado por assistir",
    "inscreva-se", "subtítulos", "subtitles by", "thanks for watching",
    "thank you for watching", "sous-titres",
)

_JUNK = re.compile(
    r"[\(\[][^\)\]]*(official|oficial|video|vídeo|lyric|letra|audio|áudio|hd|4k|clip|"
    r"visualizer|remaster|karaoke)[^\)\]]*[\)\]]",
    re.I,
)


def clean_name(s: str) -> str:
    s = _JUNK.sub("", s or "").replace("_", " ")
    return re.sub(r"\s+", " ", s).strip(" -")


def guess_from_title(text: str) -> tuple[str, str]:
    """'Artista - Título (Official Video)' -> ('Artista', 'Título')."""
    parts = re.split(r"\s+[-–—]\s+", text or "", maxsplit=1)
    if len(parts) == 2:
        return clean_name(parts[0]), clean_name(parts[1])
    return "", clean_name(text)


def guess_from_filename(filename: str) -> tuple[str, str]:
    return guess_from_title(Path(filename or "").stem)


# ---------------------------------------------------------------- LRCLIB

def search_lrclib(artist: str, title: str, duration: float) -> dict | None:
    artist, title = clean_name(artist), clean_name(title)
    if not title:
        return None
    queries = []
    if artist:
        queries.append({"track_name": title, "artist_name": artist})
    queries.append({"q": f"{artist} {title}".strip()})

    candidates = []
    with httpx.Client(timeout=15, headers=LRCLIB_HEADERS) as client:
        for params in queries:
            try:
                r = client.get(f"{LRCLIB}/search", params=params)
                r.raise_for_status()
            except httpx.HTTPError as e:
                log.warning("LRCLIB falhou: %s", e)
                continue
            candidates = [x for x in r.json() if x.get("syncedLyrics") and not x.get("instrumental")]
            if duration:
                candidates = [x for x in candidates
                              if abs((x.get("duration") or 0) - duration) <= DURATION_TOLERANCE]
            if candidates:
                break

    if not candidates:
        return None
    return min(candidates, key=lambda x: abs((x.get("duration") or 0) - (duration or 0)))


_TS = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")
_WORD_TS = re.compile(r"<\d+:\d+(?:\.\d+)?>")


def parse_lrc(text: str, duration: float) -> list[dict]:
    stamps = []
    for raw in text.splitlines():
        times = [int(m) * 60 + float(s.replace(":", ".")) for m, s in _TS.findall(raw)]
        body = _WORD_TS.sub("", _TS.sub("", raw)).strip()
        stamps += [(t, body) for t in times]
    stamps.sort(key=lambda x: x[0])

    lines = []
    for i, (start, body) in enumerate(stamps):
        if not body:
            continue  # linhas vazias só marcam o fim da anterior
        nxt = stamps[i + 1][0] if i + 1 < len(stamps) else (duration or start + 8)
        words = body.split()
        # O LRC só diz quando a linha começa; estimamos quando acaba para não
        # "esticar" a linha por cima de um solo instrumental.
        end = max(start + 0.5, min(nxt, start + 2 + 0.7 * len(words)))
        lines.append({"start": round(start, 3), "end": round(end, 3), "text": body,
                      "words": _spread_words(words, start, end)})
    return lines


def _spread_words(words: list[str], start: float, end: float) -> list[dict]:
    """Distribui o tempo da linha pelas palavras, proporcional ao tamanho."""
    weights = [len(w) + 1 for w in words]
    total, t, out = sum(weights), start, []
    for w, wt in zip(words, weights):
        dur = (end - start) * wt / total
        out.append({"start": round(t, 3), "end": round(t + dur, 3), "text": w})
        t += dur
    return out


# ---------------------------------------------------------------- Whisper

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                              cpu_threads=WHISPER_THREADS)
    return _model


def transcribe(vocals: Path, language: str, duration: float,
               on_stage: Callable[[str], None], on_progress: Callable[[int], None]):
    on_stage(f"A carregar o modelo Whisper '{WHISPER_MODEL}' "
             "(na 1.ª vez faz download, pode demorar)…")
    model = _get_model()
    on_stage("A transcrever a voz…")
    segments, info = model.transcribe(
        str(vocals),
        language=language or None,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 700},
        condition_on_previous_text=False,  # evita repetições em loop
        beam_size=5,
    )
    total = info.duration or duration or 1
    words = []
    for seg_no, seg in enumerate(segments):  # é um gerador: a transcrição acontece aqui
        on_progress(min(99, int(seg.end / total * 100)))
        if any(h in seg.text.lower() for h in HALLUCINATIONS):
            continue
        if seg.no_speech_prob > 0.7 and seg.avg_logprob < -1:
            continue
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"start": round(w.start, 3),
                              "end": round(min(w.end, w.start + 3), 3),
                              "text": text, "seg": seg_no})
    return _group_lines(words), info.language


def _group_lines(words: list[dict]) -> list[dict]:
    """Junta as palavras em linhas curtas, boas para ler no ecrã."""
    groups, cur = [], []
    for w in words:
        if cur:
            prev = cur[-1]
            if (w["start"] - prev["end"] > 0.9
                    or len(cur) >= 9
                    or (w["seg"] != prev["seg"] and len(cur) >= 2)
                    or (prev["text"][-1] in ".,!?;:" and len(cur) >= 4)):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return [{
        "start": g[0]["start"],
        "end": g[-1]["end"],
        "text": " ".join(w["text"] for w in g),
        "words": [{"start": w["start"], "end": w["end"], "text": w["text"]} for w in g],
    } for g in groups]


# ---------------------------------------------------------------- tudo junto

def build(meta: dict, vocals: Path,
          on_stage: Callable[[str], None], on_progress: Callable[[int], None]) -> dict:
    duration = meta.get("duration") or 0
    if not meta.get("force_transcribe"):
        on_stage("A procurar a letra sincronizada no LRCLIB…")
        try:
            hit = search_lrclib(meta.get("artist", ""), meta.get("title", ""), duration)
        except Exception:
            log.exception("Erro ao procurar no LRCLIB")
            hit = None
        if hit and (lines := parse_lrc(hit["syncedLyrics"], duration)):
            return {"source": "lrclib", "matched": f'{hit.get("artistName")} - {hit.get("trackName")}',
                    "word_timing": "estimated", "lines": lines}

    lines, lang = transcribe(vocals, meta.get("language", ""), duration, on_stage, on_progress)
    return {"source": "whisper", "model": WHISPER_MODEL, "language": lang,
            "word_timing": "whisper", "lines": lines}
