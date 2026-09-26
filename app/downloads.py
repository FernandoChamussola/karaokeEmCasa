"""Descarregar o áudio de um link (YouTube, SoundCloud, ...) com o yt-dlp.

Só fica guardado o áudio em mp3; o vídeo é apagado logo a seguir à extração.
Os downloads têm uma fila própria, para não ficarem à espera da separação de voz.
"""
import logging
import os
import queue
import threading
import time
import uuid

from . import audio, lyrics, store, worker

log = logging.getLogger("karaoke.downloads")
_queue: "queue.Queue[str]" = queue.Queue()
KEEP = {"audio.mp3", "meta.json"}
# Cookies de uma conta do YouTube (formato Netscape). Em servidores/VPS o YouTube
# pede "Sign in to confirm you're not a bot" e só deixa descarregar com sessão iniciada.
COOKIES = store.DATA_DIR / "cookies.txt"
BOT_CHECK_HINT = ("O YouTube bloqueou este download por agora. Tenta outra vez mais tarde "
                  "ou experimenta outro link da mesma música.")
# Serviço que gera os "PO tokens" do YouTube (contentor pot-provider no docker-compose)
POT_PROVIDER_URL = os.environ.get("POT_PROVIDER_URL", "")
# Opcional: passar os downloads por um proxy (ex.: um IP de casa), se o YouTube insistir
YTDLP_PROXY = os.environ.get("YTDLP_PROXY", "")


def cookies_status() -> dict:
    if not COOKIES.exists():
        return {"active": False}
    return {"active": True, "updated": COOKIES.stat().st_mtime}


def save_cookies(data: bytes) -> dict:
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")
             or l.startswith("#HttpOnly_")]
    if not any(len(l.split("\t")) == 7 for l in lines):
        raise ValueError("Isto não parece um cookies.txt (formato Netscape). "
                         "Exporta-o com a extensão \"Get cookies.txt LOCALLY\".")
    if not any("youtube.com" in l for l in lines):
        raise ValueError("O ficheiro não tem cookies do youtube.com")
    if not text.startswith("# Netscape HTTP Cookie File"):  # o yt-dlp exige este cabeçalho
        text = "# Netscape HTTP Cookie File\n" + text
    tmp = COOKIES.with_suffix(".tmp")
    tmp.write_text(text, "utf-8")
    os.replace(tmp, COOKIES)
    return cookies_status()


def delete_cookies() -> dict:
    COOKIES.unlink(missing_ok=True)
    return cookies_status()


def add(url: str, auto_karaoke: bool, language: str) -> dict:
    meta = {
        "id": uuid.uuid4().hex[:12],
        "url": url,
        "title": "",
        "artist": "",
        "duration": 0,
        "auto_karaoke": auto_karaoke,
        "language": language,
        "song_id": None,
        "status": "queued",
        "stage": "Na fila",
        "progress": 0,
        "error": None,
        "created": time.time(),
    }
    store.downloads.save(meta)
    _queue.put(meta["id"])
    return meta


def create_karaoke(dl_id: str, title: str | None = None, artist: str | None = None,
                   language: str | None = None, force_transcribe: bool = False) -> dict:
    """Cria uma música de karaoke a partir de um download (o download continua guardado)."""
    meta = store.downloads.load(dl_id)
    if meta is None or meta.get("status") != "done":
        raise ValueError("Este download ainda não está pronto")
    name = " - ".join(x for x in (meta.get("artist"), meta.get("title")) if x) or "download"
    song = worker.create_song(
        store.downloads.dir(dl_id) / "audio.mp3",
        original_name=f"{name}.mp3",
        title=meta.get("title", "") if title is None else title,
        artist=meta.get("artist", "") if artist is None else artist,
        language=meta.get("language", "") if language is None else language,
        force_transcribe=force_transcribe,
        keep_source=True,
        source_url=meta.get("webpage_url") or meta["url"],
        download_id=dl_id,
    )
    store.downloads.update(dl_id, song_id=song["id"])
    return song


def start() -> None:
    for meta in reversed(store.downloads.all()):
        if meta.get("status") not in ("done", "error"):
            store.downloads.update(meta["id"], status="queued", stage="Na fila", progress=0)
            _queue.put(meta["id"])
    threading.Thread(target=_loop, daemon=True, name="downloads").start()


def _loop() -> None:
    while True:
        dl_id = _queue.get()
        try:
            _download(dl_id)
        except Exception as e:
            log.exception("Erro no download %s", dl_id)
            msg = str(e).removeprefix("ERROR: ")
            if "confirm you" in msg and "not a bot" in msg:
                msg = BOT_CHECK_HINT
            store.downloads.update(dl_id, status="error", stage="Erro", error=msg[-600:])


def _guess_names(info: dict) -> tuple[str, str]:
    # YouTube Music e afins já trazem artista e faixa separados
    track, artist = info.get("track"), info.get("artist") or info.get("creator")
    if track and artist:
        return lyrics.clean_name(artist.split(",")[0]), lyrics.clean_name(track)
    artist, title = lyrics.guess_from_title(info.get("title") or "")
    if not artist:
        uploader = (info.get("uploader") or info.get("channel") or "").strip()
        if uploader.endswith(" - Topic"):
            artist = uploader.removesuffix(" - Topic")
        elif uploader.upper().endswith("VEVO"):
            artist = uploader[:-4]
    return artist.strip(), title


def _download(dl_id: str) -> None:
    import yt_dlp

    dls = store.downloads
    meta = dls.load(dl_id)
    if meta is None:
        return
    d = dls.dir(dl_id)
    for f in d.iterdir():  # restos de uma tentativa anterior
        if f.name != "meta.json":
            f.unlink()

    dls.update(dl_id, status="downloading", stage="A descarregar…", progress=0, error=None)
    last = -1

    def hook(h):
        nonlocal last
        if h["status"] == "downloading":
            total = h.get("total_bytes") or h.get("total_bytes_estimate")
            if total:
                pct = min(99, int(h.get("downloaded_bytes", 0) * 100 / total))
                if pct != last:
                    last = pct
                    dls.update(dl_id, progress=pct)
        elif h["status"] == "finished":
            dls.update(dl_id, stage="A extrair o áudio…", progress=100)

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(d / "audio.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "no_color": True,
        "progress_hooks": [hook],
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                            "preferredquality": "192"}],
    }
    if POT_PROVIDER_URL:
        opts["extractor_args"] = {"youtubepot-bgutilhttp": {"base_url": [POT_PROVIDER_URL]}}
    if YTDLP_PROXY:
        opts["proxy"] = YTDLP_PROXY
    if COOKIES.exists():
        opts["cookiefile"] = str(COOKIES)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(meta["url"], download=True)

    mp3 = d / "audio.mp3"
    if not mp3.exists():
        raise RuntimeError("O download terminou mas o áudio não foi encontrado")
    for f in d.iterdir():  # apaga o vídeo e outros ficheiros intermédios
        if f.name not in KEEP:
            f.unlink()

    artist, title = _guess_names(info)
    meta = dls.update(
        dl_id, status="done", stage="Pronto", progress=100,
        title=title or info.get("title") or "Sem título", artist=artist,
        duration=audio.probe(mp3)["duration"],
        thumbnail=info.get("thumbnail"), webpage_url=info.get("webpage_url"),
        site=info.get("extractor_key"),
    )
    log.info("Download pronto: %s - %s", artist, title)
    if meta and meta.get("auto_karaoke"):
        create_karaoke(dl_id)
