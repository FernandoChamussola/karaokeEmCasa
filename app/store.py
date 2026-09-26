"""Armazenamento em pastas: cada item é DATA_DIR/<coleção>/<id>/ com um meta.json."""
import json
import os
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
TMP_DIR = DATA_DIR / "tmp"
TMP_DIR.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data) -> None:
    """Escrita atómica, para a API nunca ler um ficheiro a meio."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)


class Collection:
    def __init__(self, name: str):
        self.root = DATA_DIR / name
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def dir(self, item_id: str) -> Path:
        return self.root / item_id

    def load(self, item_id: str) -> dict | None:
        try:
            return json.loads((self.dir(item_id) / "meta.json").read_text("utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def save(self, meta: dict) -> None:
        d = self.dir(meta["id"])
        d.mkdir(parents=True, exist_ok=True)
        write_json(d / "meta.json", meta)

    def update(self, item_id: str, **fields) -> dict | None:
        with self._lock:
            meta = self.load(item_id)
            if meta is None:  # entretanto foi apagado
                return None
            meta.update(fields)
            self.save(meta)
            return meta

    def all(self) -> list[dict]:
        items = [m for d in self.root.iterdir() if d.is_dir() and (m := self.load(d.name))]
        return sorted(items, key=lambda m: m.get("created", 0), reverse=True)


songs = Collection("songs")
downloads = Collection("downloads")
