#!/usr/bin/env python3
"""Download the official Google Cloud icon pack and (optionally) mirror it to a GCS bucket.

Usage:
  python scripts/setup_icons.py                       # download + build assets/icons/manifest.json
  python scripts/setup_icons.py --bucket my-icons     # ...and upload to gs://my-icons/icons/
  python scripts/setup_icons.py --bucket my-icons --create --public
  python scripts/setup_icons.py --bucket my-icons --signer icon-signer@PROJECT.iam.gserviceaccount.com --create-signer

Why a bucket? The Slides API fetches images from a public URL at insertion time (no auth, no
SVG), so the PNG icons must be reachable over HTTPS. A public bucket is simplest; if your
organization policy forbids public objects, use a private bucket + a signer service account
(the agent then mints short-lived V4 signed URLs).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gcp_architect.config import ICONS_DIR, PROJECT_DIR, load_settings  # noqa: E402

OFFICIAL_ZIP = "https://cloud.google.com/static/icons/files/google-cloud-icons.zip"
OFFICIAL_SOURCE = "https://cloud.google.com/icons"

# Generic (non-product) icons from Google's open-source Material Design icon set (Apache 2.0).
MATERIAL_BASE = "https://raw.githubusercontent.com/google/material-design-icons/master/png"
GENERIC_ICONS = {
    "generic_user": ("social", "person"),
    "generic_users": ("social", "groups"),
    "generic_internet": ("social", "public"),
    "generic_laptop": ("hardware", "laptop"),
    "generic_mobile": ("hardware", "smartphone"),
    "generic_datacenter": ("communication", "business"),
    "generic_servers": ("action", "dns"),
    "generic_database": ("device", "storage"),
    "generic_router": ("hardware", "router"),
    "generic_developer": ("action", "code"),
    "generic_iot": ("hardware", "memory"),
    "generic_saas": ("action", "extension"),
    "generic_cloud": ("file", "cloud"),
    "generic_idp": ("action", "verified_user"),
}


def material_url(category: str, name: str) -> str:
    return f"{MATERIAL_BASE}/{category}/{name}/materialicons/48dp/2x/baseline_{name}_black_48dp.png"


def download_official(client: httpx.Client, manifest: dict) -> int:
    print(f"Downloading official icon pack from {OFFICIAL_ZIP} ...")
    resp = client.get(OFFICIAL_ZIP)
    resp.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    count = 0
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        icon_id = Path(name).stem
        ext = Path(name).suffix.lower()
        if ext not in (".png", ".svg"):
            continue
        out = ICONS_DIR / f"{icon_id}{ext}"
        out.write_bytes(zf.read(name))
        if ext == ".png":
            manifest["icons"][icon_id] = {"file": out.name, "source": OFFICIAL_SOURCE, "official": True}
            count += 1
    print(f"  {count} official product icons")
    return count


def download_generic(client: httpx.Client, manifest: dict) -> int:
    count = 0
    for icon_id, (cat, name) in GENERIC_ICONS.items():
        url = material_url(cat, name)
        resp = client.get(url)
        if resp.status_code != 200:
            print(f"  WARN: {icon_id} ({url}) -> HTTP {resp.status_code}")
            continue
        out = ICONS_DIR / f"{icon_id}.png"
        out.write_bytes(resp.content)
        manifest["icons"][icon_id] = {"file": out.name, "source": url, "official": False, "license": "Apache-2.0 (Material Design Icons)"}
        count += 1
    print(f"  {count} generic icons (Material Design)")
    return count


def gcloud() -> str:
    for cand in (shutil.which("gcloud"), "/google/data/ro/teams/cloud-sdk/gcloud"):
        if cand and Path(cand).exists():
            return cand
    raise SystemExit("gcloud not found in PATH")


def upload(bucket: str, prefix: str, project: str | None, create: bool, public: bool, location: str) -> None:
    from google.api_core.exceptions import Forbidden, NotFound
    from google.cloud import storage

    client = storage.Client(project=project)
    try:
        b = client.get_bucket(bucket)
    except NotFound:
        if not create:
            raise SystemExit(f"Bucket {bucket} does not exist; re-run with --create")
        print(f"Creating bucket gs://{bucket} in {location} ...")
        b = storage.Bucket(client, name=bucket)
        b.iam_configuration.uniform_bucket_level_access_enabled = True
        b = client.create_bucket(b, location=location)
    if public:
        print("Granting allUsers objectViewer ...")
        try:
            policy = b.get_iam_policy(requested_policy_version=3)
            policy.bindings.append({"role": "roles/storage.objectViewer", "members": {"allUsers"}})
            b.set_iam_policy(policy)
        except Forbidden as exc:
            print(f"  Could not make the bucket public (likely an org policy): {exc.message}")
            print("  Use a private bucket with a signer service account instead (--signer / ICON_SIGNER_SA).")
    print(f"Uploading icons to gs://{bucket}/{prefix} ...")
    n = 0
    for png in sorted(ICONS_DIR.glob("*.png")):
        blob = b.blob(f"{prefix}{png.name}")
        blob.cache_control = "public, max-age=86400"
        blob.upload_from_filename(str(png), content_type="image/png")
        n += 1
    print(f"  uploaded {n} files")


def create_signer(project: str, signer: str, bucket: str) -> None:
    g = gcloud()
    sa_name = signer.split("@")[0]
    user = subprocess.run([g, "config", "get-value", "account"], capture_output=True, text=True).stdout.strip()
    cmds = [
        [g, "iam", "service-accounts", "create", sa_name, "--project", project, "--display-name", "GCP Architect icon signer"],
        [g, "storage", "buckets", "add-iam-policy-binding", f"gs://{bucket}", f"--member=serviceAccount:{signer}", "--role=roles/storage.objectViewer"],
        [g, "iam", "service-accounts", "add-iam-policy-binding", signer, "--project", project, f"--member=user:{user}", "--role=roles/iam.serviceAccountTokenCreator"],
        [g, "services", "enable", "iamcredentials.googleapis.com", "--project", project],
    ]
    for c in cmds:
        print("$", " ".join(c))
        r = subprocess.run(c, capture_output=True, text=True)
        if r.returncode != 0 and "already exists" not in r.stderr:
            print(r.stderr.strip())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bucket", help="GCS bucket to mirror icons to")
    ap.add_argument("--prefix", default=None, help="object prefix (default: ICON_PREFIX or icons/)")
    ap.add_argument("--create", action="store_true", help="create the bucket if missing")
    ap.add_argument("--public", action="store_true", help="grant allUsers objectViewer on the bucket")
    ap.add_argument("--location", default="US", help="bucket location when creating (default US)")
    ap.add_argument("--signer", help="service account email used to sign URLs (private bucket mode)")
    ap.add_argument("--create-signer", action="store_true", help="create the signer SA and grant IAM bindings")
    ap.add_argument("--skip-download", action="store_true")
    args = ap.parse_args()

    settings = load_settings()
    prefix = args.prefix or settings.icon_prefix
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = ICONS_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"icons": {}}

    if not args.skip_download:
        with httpx.Client(follow_redirects=True, timeout=60) as client:
            download_official(client, manifest)
            download_generic(client, manifest)
        manifest["official_source"] = OFFICIAL_SOURCE
        manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))
        print(f"Wrote {manifest_path} ({len(manifest['icons'])} icons)")

    if args.bucket:
        upload(args.bucket, prefix, settings.project, args.create, args.public, args.location)
        if args.signer and args.create_signer:
            if not settings.project:
                raise SystemExit("GOOGLE_CLOUD_PROJECT must be set to create the signer")
            create_signer(settings.project, args.signer, args.bucket)
        print("\nAdd to your .env:")
        print("ICON_HOST=gcs")
        print(f"ICON_BUCKET={args.bucket}")
        print(f"ICON_PREFIX={prefix}")
        if args.signer:
            print(f"ICON_SIGNER_SA={args.signer}")
        else:
            print(f"# test: curl -I https://storage.googleapis.com/{args.bucket}/{prefix}cloud_run.png")


if __name__ == "__main__":
    main()
