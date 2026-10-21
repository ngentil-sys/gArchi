"""Read/write generated Terraform projects under the output directory."""

from __future__ import annotations

import os
from pathlib import Path

from ..config import PROJECT_DIR

ALLOWED_SUFFIXES = (".tf", ".tfvars", ".md", ".json", ".yaml", ".yml", ".tftpl", ".txt", ".sh")


def output_root() -> Path:
    return Path(os.getenv("OUTPUT_DIR", PROJECT_DIR / "output")).expanduser().resolve()


def project_dir(slug: str) -> Path:
    return output_root() / slug / "terraform"


def _safe_rel(path: str) -> Path:
    rel = Path(path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"invalid relative path: {path}")
    if rel.suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"unsupported file type: {path} (allowed: {ALLOWED_SUFFIXES})")
    return rel


def write_files(slug: str, files: dict[str, str]) -> Path:
    root = project_dir(slug)
    root.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        rel = _safe_rel(name)
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        text = content if content.endswith("\n") else content + "\n"
        target.write_text(text)
    return root


def read_files(slug: str) -> dict[str, str]:
    root = project_dir(slug)
    if not root.exists():
        return {}
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix in ALLOWED_SUFFIXES and ".terraform" not in p.parts:
            out[str(p.relative_to(root))] = p.read_text()
    return out


def tree(slug: str) -> list[str]:
    return sorted(read_files(slug).keys())
