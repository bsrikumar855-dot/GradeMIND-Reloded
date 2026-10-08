"""Process rule 12: resolved config, not requested config. Helpers shared by spike runners (stdlib only)."""

from __future__ import annotations

import hashlib
from pathlib import Path


class ConfigMismatch(SystemExit):
    pass


def sha256_file(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def dir_fingerprint(d: Path, suffixes: tuple[str, ...] = (".pdiparams", ".json", ".yml", ".safetensors", ".bin")) -> dict:
    files = sorted(p for p in Path(d).rglob("*") if p.is_file() and p.suffix in suffixes and ".cache" not in p.parts)
    return {str(p.relative_to(d)): sha256_file(p) for p in files}


def require_equal(field: str, requested, resolved) -> None:
    if requested != resolved:
        raise ConfigMismatch(f"RULE 12 VIOLATION: {field}: requested={requested!r} resolved={resolved!r}")
