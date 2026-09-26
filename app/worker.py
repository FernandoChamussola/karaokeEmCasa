"""Fila de processamento: uma música de cada vez (o CPU não aguenta mais)."""
import logging
import queue
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import audio, lyrics, store

log = logging.getLogger("karaoke.worker")
_queue: "queue.Queue[str]" = queue.Queue()


def enqueue(song_id: str) -> None:
    _queue.put(song_id)


def create_song(src: Path, *, original_name: str, title: str = "", artist: str = "",
                language: str = "", force_transcribe: bool = False,
                keep_source: bool = False, **extra) -> dict:
    """Cria uma música na biblioteca a partir de um ficheiro e põe-na na fila."""
    info = audio.probe(src)  # falha se não for áudio válido
    song_id = uuid.uuid4().hex[:12]
    d = store.songs.dir(song_id)
    d.mkdir(parents=True)
    dest = d / f"original{src.suffix.lower()}"
    (shutil.copy2 if keep_source else shutil.move)(src, dest)

    guess_artist, guess_title = lyrics.guess_from_filename(original_name)
    meta = {
        "id": song_id,
        "title": title.strip() or info["tags"].get("title") or guess_title,
        "artist": artist.strip() or info["tags"].get("artist") or guess_artist,
        "language": language,
        "force_transcribe": force_transcribe,
        "filename": dest.name,
        "original_name": original_name,
        "duration": info["duration"],
        "status": "queued",
        "stage": "Na fila",
        "progress": 0,
        "error": None,
        "created": time.time(),
        **extra,
    }
    store.songs.save(meta)
    enqueue(song_id)
    return meta


def start() -> None:
    # Retoma o que ficou a meio (ex.: o contentor foi reiniciado)
    for meta in reversed(store.songs.all()):
        if meta.get("status") not in ("done", "error"):
            store.songs.update(meta["id"], status="queued", stage="Na fila", progress=0)
            enqueue(meta["id"])
    threading.Thread(target=_loop, daemon=True, name="worker").start()


def _loop() -> None:
    while True:
        song_id = _queue.get()
        try:
            _process(song_id)
        except Exception as e:
            log.exception("Erro a processar %s", song_id)
            store.songs.update(song_id, status="error", stage="Erro", error=str(e)[-600:])


def _process(song_id: str) -> None:
    songs = store.songs
    meta = songs.load(song_id)
    if meta is None:
        return
    d = songs.dir(song_id)
    progress = lambda p: songs.update(song_id, progress=p)
    stage = lambda s: songs.update(song_id, stage=s, progress=0)

    if not ((d / "instrumental.mp3").exists() and (d / "vocals.mp3").exists()):
        songs.update(song_id, status="separating", stage="A separar a voz do instrumental…",
                     progress=0, error=None)
        audio.separate(d / meta["filename"], d, progress)

    if songs.update(song_id, status="lyrics", progress=0, error=None) is None:
        return
    result = lyrics.build(meta, d / "vocals.mp3", stage, progress)
    if not d.exists():
        return
    store.write_json(d / "lyrics.json", result)
    songs.update(song_id, status="done", stage="Pronta", progress=100,
                 lyrics_source=result["source"], lyrics_matched=result.get("matched"),
                 lyrics_lines=len(result["lines"]))
    log.info("Pronta: %s (%s, %d linhas)", meta.get("title"), result["source"], len(result["lines"]))
