"""Read/write Creative Studio's (GCC) database from the lineage router.

GCC keeps each generation in its Cloud SQL table `media_items` (user, model, the prompt
as typed = `original_prompt`, the files = `gcs_uris`) and brand guidelines extracted
from PDFs in `brand_guidelines`. The router uses this to:
  - link a new GCC image to its Prompt ID automatically (prompt-hash match),
  - record the real user and model,
  - score against the brand guidelines GCC extracted (one source of brand truth),
  - write our score back into `media_items.critique`, which GCC's gallery shows.

Runs where Cloud SQL is reachable (Cloud Run). Connects with the Cloud SQL Python
Connector as GCC's own DB user; the password comes from GCC's Secret Manager secret.
"""
from __future__ import annotations

import base64
import time
from functools import cached_property
from typing import Any

from .config import Config

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
MEDIA_COLS = ("id, workspace_id, user_email, model, prompt, original_prompt, rewritten_prompt, "
              "status, gcs_uris, resolution, aspect_ratio")


def _session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    creds, _ = google.auth.default(scopes=SCOPES)
    return AuthorizedSession(creds)


class GccDb:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cs = cfg.creative_studio
        self._guideline_cache: dict[Any, tuple[float, dict | None]] = {}

    # ---- connection ---------------------------------------------------------
    @cached_property
    def instance_connection_name(self) -> str:
        if self.cs.db_instance:
            return self.cs.db_instance
        from .gcc_idle import find_instance
        inst = find_instance(self.cfg, _session())
        if not inst:
            raise RuntimeError("Creative Studio Cloud SQL instance not found")
        return inst["connectionName"]

    @cached_property
    def _password(self) -> str:
        url = (f"https://secretmanager.googleapis.com/v1/projects/{self.cfg.gcp.project_id}/secrets/"
               f"{self.cs.db_password_secret}/versions/latest:access")
        r = _session().get(url, timeout=30)
        r.raise_for_status()
        return base64.b64decode(r.json()["payload"]["data"]).decode()

    @cached_property
    def _connector(self):
        from google.cloud.sql.connector import Connector
        return Connector()

    def _conn(self):
        return self._connector.connect(self.instance_connection_name, "pg8000", user=self.cs.db_user,
                                       password=self._password, db=self.cs.db_name)

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = self._conn()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            if cur.description is None:
                conn.commit()
                return []
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    # ---- media items --------------------------------------------------------
    def media_item_for_uri(self, uri: str) -> dict | None:
        rows = self._query(f"SELECT {MEDIA_COLS} FROM media_items WHERE %s = ANY(gcs_uris) AND deleted_at IS NULL "
                           "ORDER BY id DESC LIMIT 1", (uri,))
        return rows[0] if rows else None

    def wait_media_item(self, uri: str, timeout_s: int | None = None, interval_s: int = 5) -> dict | None:
        """GCC uploads the file first and fills gcs_uris moments later, so poll briefly."""
        deadline = time.monotonic() + (self.cs.link_wait_seconds if timeout_s is None else timeout_s)
        while True:
            item = self.media_item_for_uri(uri)
            if item or time.monotonic() >= deadline:
                return item
            time.sleep(interval_s)

    def write_critique(self, media_item_id: int, text: str) -> None:
        self._query("UPDATE media_items SET critique = %s, updated_at = now() WHERE id = %s", (text, media_item_id))

    # ---- brand guidelines ---------------------------------------------------
    def brand_guideline(self, workspace_id: int | None, ttl_s: int = 300) -> dict | None:
        """Latest completed guideline for the workspace (else a global one), cached for ttl_s."""
        hit = self._guideline_cache.get(workspace_id)
        if hit and time.monotonic() - hit[0] < ttl_s:
            return hit[1]
        rows = self._query(
            "SELECT id, name, workspace_id, color_palette, tone_of_voice_summary, visual_style_summary, "
            "guideline_text, updated_at FROM brand_guidelines "
            "WHERE status::text ILIKE 'completed' AND (workspace_id = %s OR workspace_id IS NULL) "
            "ORDER BY (workspace_id IS NULL), updated_at DESC LIMIT 1", (workspace_id,))
        g = rows[0] if rows else None
        self._guideline_cache[workspace_id] = (time.monotonic(), g)
        return g


# ---- pure helpers (unit-tested) ---------------------------------------------
def prompt_text_for_matching(media: dict) -> str | None:
    """The prompt as the user typed it (before GCC's enhancement / brand-guideline prepend)."""
    return media.get("original_prompt") or media.get("prompt")


def brand_context(guideline: dict | None, max_chars: int = 12000) -> tuple[str, str] | None:
    """(source label, text) for the critic, from a GCC brand guideline row."""
    if not guideline:
        return None
    parts = [f"Brand guideline: {guideline.get('name')}"]
    if guideline.get("color_palette"):
        parts.append("Colour palette: " + ", ".join(guideline["color_palette"]))
    for key, label in (("visual_style_summary", "Visual style"), ("tone_of_voice_summary", "Tone of voice"),
                       ("guideline_text", "Guideline text")):
        if guideline.get(key):
            parts.append(f"{label}: {guideline[key]}")
    text = "\n".join(parts)
    return f"gcc_brand_guideline:{guideline.get('id')}", text[:max_chars]


def critique_text(decision: dict, sub_scores: dict | None, rationale: str, gen: dict) -> str:
    """What GCC's gallery shows in its 'Critique' box."""
    verdict = {"APPROVED": "APPROVED", "NEEDS_REVISION": "NEEDS REVISION (edit the prompt and regenerate)",
               "FAILED_QC": "FAILED quality check", "FLAGGED": "FLAGGED for review",
               "REJECTED": "REJECTED"}.get(decision.get("status"), decision.get("status", ""))
    lines = [f"Pipeline score {decision.get('composite', 0):.0f}/100 · {verdict} · "
             f"hallucination risk {decision.get('hallucination_band', '?')}"]
    if sub_scores:
        lines.append(" · ".join(f"{k.replace('_', ' ')} {v:.2f}" for k, v in sub_scores.items()
                                if k != "hallucination_risk" and v is not None))
    if rationale:
        lines.append(rationale.strip())
    lines.append(f"{gen.get('generation_id')} v{gen.get('version')} · prompt {gen.get('prompt_id')} · "
                 f"batch {gen.get('batch_run_id') or '-'}")
    return "\n".join(lines)
