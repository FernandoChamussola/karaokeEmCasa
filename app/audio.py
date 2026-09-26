"""Leitura de ficheiros de áudio e separação voz/instrumental com o Demucs."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

DEMUCS_MODEL = os.environ.get("DEMUCS_MODEL", "htdemucs")


def probe(path: Path) -> dict:
    """Duração e tags (título/artista) do ficheiro, via ffprobe."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    fmt = json.loads(r.stdout).get("format", {})
    duration = float(fmt.get("duration") or 0)
    if duration <= 0:
        raise ValueError("ficheiro sem duração válida")
    tags = {k.lower(): v.strip() for k, v in (fmt.get("tags") or {}).items()}
    return {"duration": duration, "tags": tags}


def separate(src: Path, out_dir: Path, on_progress: Callable[[int], None]) -> None:
    """Gera out_dir/vocals.mp3 e out_dir/instrumental.mp3."""
    work = out_dir / "_demucs"
    shutil.rmtree(work, ignore_errors=True)
    cmd = [
        sys.executable, "-m", "demucs.separate",
        "-n", DEMUCS_MODEL,
        "--two-stems", "vocals",
        "--mp3", "--mp3-bitrate", "256",
        "-o", str(work),
        str(src),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    # O Demucs mostra uma barra tqdm ("45%|████ ..."); lemos a percentagem dela.
    buf, tail, last = b"", [], -1
    while chunk := proc.stdout.read1(512):
        buf += chunk
        *parts, buf = re.split(rb"[\r\n]", buf)
        for raw in parts:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            tail = (tail + [line])[-15:]
            if m := re.search(r"(\d+)%\|", line):
                pct = int(m.group(1))
                if pct != last:
                    last = pct
                    on_progress(pct)

    if proc.wait() != 0:
        raise RuntimeError("Falhou a separação da voz:\n" + "\n".join(tail))

    stems = work / DEMUCS_MODEL / src.stem
    shutil.move(stems / "vocals.mp3", out_dir / "vocals.mp3")
    shutil.move(stems / "no_vocals.mp3", out_dir / "instrumental.mp3")
    shutil.rmtree(work, ignore_errors=True)
