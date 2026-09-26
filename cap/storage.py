"""GCS paths, uploads and copies with lineage metadata (TDD 5.2-5.3)."""
from __future__ import annotations

import json
from functools import cached_property
from typing import Any

from .config import Config


def _slug(v: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in str(v).strip().lower()) or "na"


class Storage:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    @cached_property
    def client(self):
        from google.cloud import storage
        return storage.Client(project=self.cfg.gcp.project_id)

    # ---- naming ---------------------------------------------------------
    def object_path(self, *, sku: str, batch_run_id: str | None, entity_id: str, version: int, file: str) -> str:
        s = self.cfg.sku(sku)
        return self.cfg.storage.path_pattern.format(
            brand=_slug(self.cfg.customer.brand), category=_slug(s.category), subcategory=_slug(s.subcategory),
            sku=s.sku, batch_run_id=batch_run_id or "adhoc", entity_id=entity_id, version=version, file=file)

    def file_name(self, *, sku: str, shot_type: str, aspect: str, resolution: str, version: int, ext: str) -> str:
        return self.cfg.storage.file_pattern.format(
            sku=sku, shot_type=_slug(shot_type), aspect=aspect.replace(":", "x"), resolution=resolution,
            version=version, ext=ext)

    @staticmethod
    def split_uri(uri: str) -> tuple[str, str]:
        if not uri.startswith("gs://"):
            raise ValueError(f"not a gs:// URI: {uri}")
        bucket, _, name = uri[5:].partition("/")
        return bucket, name

    # ---- io -------------------------------------------------------------
    def upload_bytes(self, purpose: str, path: str, data: bytes, content_type: str,
                     metadata: dict[str, Any] | None = None) -> str:
        blob = self.client.bucket(self.cfg.bucket(purpose)).blob(path)
        blob.metadata = _meta(metadata)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{blob.bucket.name}/{blob.name}"

    def upload_json(self, purpose: str, path: str, obj: Any, metadata: dict[str, Any] | None = None) -> str:
        return self.upload_bytes(purpose, path, json.dumps(obj, indent=2, default=str).encode(),
                                 "application/json", metadata)

    def download(self, uri: str) -> tuple[bytes, str, dict[str, str]]:
        bucket, name = self.split_uri(uri)
        blob = self.client.bucket(bucket).get_blob(name)
        if blob is None:
            raise FileNotFoundError(uri)
        return blob.download_as_bytes(), blob.content_type or "application/octet-stream", dict(blob.metadata or {})

    def copy(self, src_uri: str, purpose: str, path: str, metadata: dict[str, Any] | None = None) -> str:
        """Copy (never move) so the intermediate original stays in place for the audit trail."""
        bucket, name = self.split_uri(src_uri)
        src_blob = self.client.bucket(bucket).blob(name)
        dst_bucket = self.client.bucket(self.cfg.bucket(purpose))
        new = self.client.bucket(bucket).copy_blob(src_blob, dst_bucket, path)
        if metadata:
            new.metadata = {**(new.metadata or {}), **_meta(metadata)}
            new.patch()
        return f"gs://{dst_bucket.name}/{path}"

    def set_metadata(self, uri: str, metadata: dict[str, Any]) -> None:
        bucket, name = self.split_uri(uri)
        blob = self.client.bucket(bucket).get_blob(name)
        if blob is not None:
            blob.metadata = {**(blob.metadata or {}), **_meta(metadata)}
            blob.patch()


def _meta(md: dict[str, Any] | None) -> dict[str, str]:
    """GCS custom metadata (x-goog-meta-*): strings only, keys kebab-case, values capped."""
    out = {}
    for k, v in (md or {}).items():
        if v is None:
            continue
        out[k.replace("_", "-")] = (v if isinstance(v, str) else json.dumps(v, default=str))[:2000]
    return out
