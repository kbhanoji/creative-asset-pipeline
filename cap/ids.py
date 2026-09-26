"""Identifiers (TDD 6.1).

ULIDs are time-sortable and safe to generate in parallel without coordination.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime, timezone

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(ts_ms: int | None = None) -> str:
    ts = int(time.time() * 1000) if ts_ms is None else ts_ms
    value = (ts << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_CROCKFORD[(value >> (5 * i)) & 31] for i in reversed(range(26)))


def batch_run_id(brand_code: str, seq: int, day: datetime | None = None) -> str:
    """BR-{yyyymmdd}-{brand3}-{seq3}, e.g. BR-20261005-DWT-001."""
    day = day or datetime.now(timezone.utc)
    return f"BR-{day:%Y%m%d}-{brand_code}-{seq:03d}"


def prompt_id() -> str:
    return f"PR-{ulid()}"


def prompt_lineage_id() -> str:
    """One per prompt family (SKU x asset type x shot type x variant slot); all versions share it."""
    return f"PL-{ulid()}"


def generation_id() -> str:
    return f"GEN-{ulid()}"


def asset_group_id() -> str:
    return f"AG-{ulid()}"


def event_id() -> str:
    return f"EV-{ulid()}"


def normalize_prompt(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def prompt_sha256(text: str) -> str:
    """Hash used to match a Creative Studio image back to the enriched prompt that was pasted."""
    return hashlib.sha256(normalize_prompt(text).encode()).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


ID_RE = re.compile(r"\b(PR|GEN|PL|AG|EV)-[0-9A-HJKMNP-TV-Z]{26}\b")
BATCH_RE = re.compile(r"\bBR-\d{8}-[A-Z0-9]{3}-\d{3}\b")


def find_ids(text: str) -> dict[str, str]:
    """Extract known IDs from a path or string (used by the lineage router)."""
    out: dict[str, str] = {}
    for m in ID_RE.finditer(text):
        out.setdefault(m.group(1), m.group(0))
    if b := BATCH_RE.search(text):
        out["BR"] = b.group(0)
    return out
