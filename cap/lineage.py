"""Append-only writer/reader for the BigQuery lineage dataset (TDD 6.2).

Rows are only ever inserted; "current state" comes from the v_* views, so
superseded versions and decisions are retained (SOW acceptance 78).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import cached_property
from typing import Any

from .config import Config


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(row: dict[str, Any], json_cols: set[str]) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        if v is None:
            continue
        out[k] = json.dumps(v, default=str) if k in json_cols and not isinstance(v, str) else v
    return out


JSON_COLUMNS = {"skills_versions", "weights", "thresholds", "raw", "checks"}


class Lineage:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    @cached_property
    def client(self):
        from google.cloud import bigquery
        return bigquery.Client(project=self.cfg.gcp.project_id, location=self.cfg.gcp.bigquery_location)

    # ---- writes ---------------------------------------------------------
    def insert(self, table: str, *rows: dict[str, Any]) -> None:
        payload = [_jsonable(r, JSON_COLUMNS) for r in rows]
        errors = self.client.insert_rows_json(self.cfg.table(table), payload)
        if errors:
            raise RuntimeError(f"BigQuery insert into {table} failed: {errors}")

    # ---- reads ----------------------------------------------------------
    def query(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        from google.cloud import bigquery
        qp = []
        for k, v in params.items():
            typ = "INT64" if isinstance(v, int) and not isinstance(v, bool) else "STRING"
            qp.append(bigquery.ScalarQueryParameter(k, typ, v))
        sql = sql.replace("${project}", self.cfg.gcp.project_id).replace("${dataset}", self.cfg.bigquery.dataset)
        job = self.client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=qp))
        return [dict(r.items()) for r in job.result()]

    def one(self, sql: str, **params: Any) -> dict[str, Any] | None:
        rows = self.query(sql, **params)
        return rows[0] if rows else None

    def next_batch_seq(self, day_prefix: str) -> int:
        row = self.one(
            "SELECT COUNT(DISTINCT batch_run_id) AS n FROM `${project}.${dataset}.batch_run` "
            "WHERE STARTS_WITH(batch_run_id, @p)", p=day_prefix)
        return int(row["n"]) + 1 if row else 1

    def get_prompt(self, prompt_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM `${project}.${dataset}.enriched_prompt` WHERE prompt_id = @id LIMIT 1", id=prompt_id)

    def get_prompt_by_hash(self, sha: str) -> dict[str, Any] | None:
        return self.one(
            "SELECT * FROM `${project}.${dataset}.enriched_prompt` WHERE prompt_sha256 = @h "
            "ORDER BY created_at DESC LIMIT 1", h=sha)

    def latest_prompt_version(self, prompt_lineage_id: str) -> int:
        row = self.one(
            "SELECT MAX(prompt_version) AS v FROM `${project}.${dataset}.enriched_prompt` "
            "WHERE prompt_lineage_id = @l", l=prompt_lineage_id)
        return int(row["v"] or 0) if row else 0

    def find_prompt_lineage(self, sku: str, asset_type: str, shot_type: str, variant_index: int,
                            aspect_ratio: str) -> str | None:
        """Reuse the prompt family for the same SKU slot so prompt versions accumulate per SKU."""
        row = self.one(
            "SELECT prompt_lineage_id FROM `${project}.${dataset}.enriched_prompt` "
            "WHERE sku = @sku AND asset_type = @a AND shot_type = @s AND variant_index = @v AND aspect_ratio = @ar "
            "ORDER BY created_at DESC LIMIT 1",
            sku=sku, a=asset_type, s=shot_type, v=variant_index, ar=aspect_ratio)
        return row["prompt_lineage_id"] if row else None

    def get_generation(self, generation_id: str, version: int | None = None) -> dict[str, Any] | None:
        if version is None:
            return self.one(
                "SELECT * FROM `${project}.${dataset}.generation` WHERE generation_id = @g "
                "ORDER BY version DESC LIMIT 1", g=generation_id)
        return self.one(
            "SELECT * FROM `${project}.${dataset}.generation` WHERE generation_id = @g AND version = @v LIMIT 1",
            g=generation_id, v=version)

    def generation_by_source(self, source_uri: str) -> dict[str, Any] | None:
        return self.one(
            "SELECT * FROM `${project}.${dataset}.generation` WHERE source_uri = @u OR gcs_uri = @u LIMIT 1",
            u=source_uri)

    def count_generations(self) -> int:
        row = self.one("SELECT COUNT(*) AS n FROM `${project}.${dataset}.generation`")
        return int(row["n"]) if row else 0

    def current_status(self, generation_id: str, version: int) -> str | None:
        row = self.one(
            "SELECT status FROM `${project}.${dataset}.disposition` WHERE entity_type = 'GENERATION' "
            "AND entity_id = @g AND version = @v ORDER BY created_at DESC LIMIT 1", g=generation_id, v=version)
        return row["status"] if row else None
