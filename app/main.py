import hashlib
import hmac
import logging
import os
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import downloads, store, worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

STATIC = Path(__file__).parent / "static"
ALLOWED_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma",
               ".webm", ".mp4", ".mkv", ".mov"}
AUDIO_FILES = {"instrumental": "instrumental.mp3", "vocals": "vocals.mp3"}

# Senha de acesso. Vazia = sem login (só para uso local).
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SESSION_COOKIE = "karaoke_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 90  # 90 dias
PUBLIC_PATHS = {"/login", "/style.css"}


@asynccontextmanager
async def lifespan(_app):
    worker.start()
    downloads.start()
    yield


app = FastAPI(title="Karaoke em Casa", lifespan=lifespan)


# ---------------------------------------------------------------- login

def _session_token() -> str:
    # muda sozinho se a senha mudar, o que termina as sessões antigas
    return hmac.new(APP_PASSWORD.encode(), b"karaoke-session", hashlib.sha256).hexdigest()


@app.middleware("http")
async def require_login(request: Request, call_next):
    if not APP_PASSWORD or request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    if hmac.compare_digest(request.cookies.get(SESSION_COOKIE, ""), _session_token()):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Sessão terminada, entra outra vez"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.get("/login")
def login_page():
    return FileResponse(STATIC / "login.html")


@app.post("/login")
def login(request: Request, password: str = Form("")):
    if not APP_PASSWORD or not hmac.compare_digest(password.encode(), APP_PASSWORD.encode()):
        return RedirectResponse("/login?erro=1", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, _session_token(), max_age=SESSION_MAX_AGE, httponly=True,
                    samesite="lax", secure=request.url.scheme == "https"
                    or request.headers.get("x-forwarded-proto") == "https")
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


def _get(collection: store.Collection, item_id: str, what: str) -> dict:
    meta = collection.load(item_id) if re.fullmatch(r"[0-9a-f]{12}", item_id) else None
    if meta is None:
        raise HTTPException(404, f"{what} não encontrado")
    return meta


def _song(song_id: str) -> dict:
    return _get(store.songs, song_id, "Música")


def _download(dl_id: str) -> dict:
    return _get(store.downloads, dl_id, "Download")


# ---------------------------------------------------------------- músicas

@app.get("/api/songs")
def list_songs():
    return store.songs.all()


@app.post("/api/songs")
def add_song(
    file: UploadFile = File(...),
    title: str = Form(""),
    artist: str = Form(""),
    language: str = Form(""),
    force_transcribe: bool = Form(False),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Formato não suportado ({ext or 'sem extensão'})")

    tmp = store.TMP_DIR / f"{uuid.uuid4().hex}{ext}"
    try:
        with tmp.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        try:
            return worker.create_song(tmp, original_name=file.filename, title=title,
                                      artist=artist, language=language,
                                      force_transcribe=force_transcribe)
        except Exception:
            raise HTTPException(400, "Não consegui ler este ficheiro de áudio")
    finally:
        tmp.unlink(missing_ok=True)


@app.get("/api/songs/{song_id}")
def get_song(song_id: str):
    return _song(song_id)


@app.post("/api/songs/{song_id}/redo-lyrics")
def redo_lyrics(
    song_id: str,
    title: str | None = Form(None),
    artist: str | None = Form(None),
    language: str | None = Form(None),
    force_transcribe: bool = Form(False),
):
    meta = _song(song_id)
    if meta["status"] not in ("done", "error"):
        raise HTTPException(409, "Esta música ainda está a ser processada")
    fields = {"force_transcribe": force_transcribe, "status": "queued", "stage": "Na fila",
              "progress": 0, "error": None}
    for key, value in (("title", title), ("artist", artist), ("language", language)):
        if value is not None:
            fields[key] = value.strip()
    store.songs.update(song_id, **fields)
    worker.enqueue(song_id)
    return store.songs.load(song_id)


@app.delete("/api/songs/{song_id}")
def delete_song(song_id: str):
    _song(song_id)
    shutil.rmtree(store.songs.dir(song_id), ignore_errors=True)
    return {"ok": True}


@app.get("/api/songs/{song_id}/lyrics")
def get_lyrics(song_id: str):
    _song(song_id)
    path = store.songs.dir(song_id) / "lyrics.json"
    if not path.exists():
        raise HTTPException(404, "A letra ainda não está pronta")
    return FileResponse(path, media_type="application/json")


@app.get("/api/songs/{song_id}/audio/{kind}")
def get_audio(song_id: str, kind: str):
    _song(song_id)
    if kind not in AUDIO_FILES:
        raise HTTPException(404)
    path = store.songs.dir(song_id) / AUDIO_FILES[kind]
    if not path.exists():
        raise HTTPException(404, "Áudio ainda não está pronto")
    return FileResponse(path, media_type="audio/mpeg")


# ---------------------------------------------------------------- downloads

@app.get("/api/downloads")
def list_downloads():
    return store.downloads.all()


@app.post("/api/downloads")
def add_download(
    url: str = Form(...),
    auto_karaoke: bool = Form(True),
    language: str = Form(""),
):
    url = url.strip()
    if not re.match(r"https?://\S+$", url):
        raise HTTPException(400, "Isso não parece um link válido (tem de começar por http)")
    return downloads.add(url, auto_karaoke, language)


@app.post("/api/downloads/{dl_id}/karaoke")
def download_to_karaoke(
    dl_id: str,
    title: str | None = Form(None),
    artist: str | None = Form(None),
    language: str | None = Form(None),
    force_transcribe: bool = Form(False),
):
    _download(dl_id)
    try:
        # campos vazios = usar o que veio do download
        return downloads.create_karaoke(dl_id, title or None, artist or None,
                                        language or None, force_transcribe)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/api/downloads/{dl_id}/audio")
def download_audio(dl_id: str):
    _download(dl_id)
    path = store.downloads.dir(dl_id) / "audio.mp3"
    if not path.exists():
        raise HTTPException(404, "Download ainda não está pronto")
    return FileResponse(path, media_type="audio/mpeg")


@app.delete("/api/downloads/{dl_id}")
def delete_download(dl_id: str):
    _download(dl_id)
    shutil.rmtree(store.downloads.dir(dl_id), ignore_errors=True)
    return {"ok": True}


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
