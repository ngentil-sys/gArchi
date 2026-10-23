"""Read/write generated Terraform projects under the output directory.

OUTPUT_DIR may be a local path (default <repo>/output) or a Cloud Storage prefix
(gs://bucket/prefix) for hosted deployments where the container disk is ephemeral.
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path, PurePosixPath

from ..config import PROJECT_DIR

ALLOWED_SUFFIXES = (".tf", ".tfvars", ".md", ".json", ".yaml", ".yml", ".tftpl", ".txt", ".sh")


def output_root() -> str:
    raw = os.getenv("OUTPUT_DIR", str(PROJECT_DIR / "output"))
    if raw.startswith("gs://"):
        return raw.rstrip("/")
    return str(Path(raw).expanduser().resolve())


def is_gcs() -> bool:
    return output_root().startswith("gs://")


def project_dir(slug: str) -> Path | str:
    root = output_root()
    if root.startswith("gs://"):
        return f"{root}/{slug}/terraform"
    return Path(root) / slug / "terraform"


def _safe_rel(path: str) -> PurePosixPath:
    rel = PurePosixPath(path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"invalid relative path: {path}")
    if rel.suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"unsupported file type: {path} (allowed: {ALLOWED_SUFFIXES})")
    return rel


def _split_gs(uri: str) -> tuple[str, str]:
    rest = uri[len("gs://"):]
    bucket, _, prefix = rest.partition("/")
    return bucket, prefix.strip("/")


def _bucket(name: str):
    from google.cloud import storage

    return storage.Client().bucket(name)


def write_files(slug: str, files: dict[str, str]) -> Path | str:
    root = project_dir(slug)
    if isinstance(root, str):  # gs://
        bucket_name, prefix = _split_gs(root)
        bucket = _bucket(bucket_name)
        for name, content in files.items():
            rel = _safe_rel(name)
            text = content if content.endswith("\n") else content + "\n"
            bucket.blob(f"{prefix}/{rel}").upload_from_string(text, content_type="text/plain; charset=utf-8")
        return root
    root.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        rel = _safe_rel(name)
        target = root / Path(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = content if content.endswith("\n") else content + "\n"
        target.write_text(text)
    return root


def read_files(slug: str) -> dict[str, str]:
    root = project_dir(slug)
    out: dict[str, str] = {}
    if isinstance(root, str):
        bucket_name, prefix = _split_gs(root)
        for blob in _bucket(bucket_name).list_blobs(prefix=prefix + "/"):
            rel = blob.name[len(prefix) + 1:]
            if rel and PurePosixPath(rel).suffix in ALLOWED_SUFFIXES and ".terraform/" not in rel:
                out[rel] = blob.download_as_text()
        return dict(sorted(out.items()))
    if not root.exists():
        return {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix in ALLOWED_SUFFIXES and ".terraform" not in p.parts:
            out[str(p.relative_to(root)).replace(os.sep, "/")] = p.read_text()
    return out


def tree(slug: str) -> list[str]:
    return sorted(read_files(slug).keys())


def zip_bytes(slug: str) -> bytes:
    """Zip the whole project (works for local and GCS roots)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in read_files(slug).items():
            zf.writestr(f"{slug}/terraform/{name}", content)
    return buf.getvalue()


def local_checkout(slug: str) -> Path | None:
    """Return a local directory with the project files (download from GCS into a temp dir)."""
    root = project_dir(slug)
    if isinstance(root, Path):
        return root if root.exists() else None
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix=f"garchi-{slug}-"))
    files = read_files(slug)
    if not files:
        return None
    for name, content in files.items():
        p = tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tmp
