"""Lineage & Scoring Router (TDD S2), Cloud Run service behind Eventarc.

Receives 'object finalized' events for:
  - the creative-studio bucket (images generated in Creative Studio, manual mode), and
  - the intermediate bucket under inbox/ (images a user uploads manually).
Links each image to its Prompt ID (TDD 6.4 ordered rules), records the
generation, scores it and routes it. Everything else is ignored, so the
pipeline's own writes never re-trigger it.
"""
from __future__ import annotations

import logging
import os
import re

from fastapi import FastAPI, Request

from cap import config as cap_config
from cap import ids
from cap.pipeline import Pipeline, PipelineError

logging.basicConfig(level=logging.INFO, format='{"severity":"%(levelname)s","message":"%(message)s"}')
log = logging.getLogger("lineage-router")

CFG = cap_config.load(os.environ.get("CAP_CONFIG"))
PIPE = Pipeline(CFG)
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")
app = FastAPI()


@app.get("/healthz")
def healthz():
    return {"ok": True, "customer": CFG.customer.id, "env": CFG.environment}


@app.post("/tasks/gcc-idle-check")
def gcc_idle_check():
    """Cloud Scheduler (dev): stop Creative Studio's Cloud SQL after idle_stop_minutes without backend requests."""
    from cap.gcc_idle import check_and_stop
    result = check_and_stop(CFG)
    log.info(f"gcc idle check: {result}")
    return result


@app.post("/")
async def on_event(request: Request):
    body = await request.json()
    obj = body.get("data", body)  # structured or binary CloudEvent
    bucket, name = obj.get("bucket", ""), obj.get("name", "")
    ctype = obj.get("contentType", "")
    metadata = obj.get("metadata") or {}
    uri = f"gs://{bucket}/{name}"

    watched = (bucket == CFG.bucket("creative-studio")) or (bucket == CFG.bucket("intermediate") and name.startswith("inbox/"))
    if not watched or not ctype.startswith(IMAGE_TYPES):
        return {"ignored": uri}

    prompt, method = match_prompt(name, metadata)
    user = (metadata.get(CFG.creative_studio.user_metadata_key) or metadata.get("created-by")
            or ("creative-studio" if bucket == CFG.bucket("creative-studio") else "manual-upload"))
    tool = "CREATIVE_STUDIO" if bucket == CFG.bucket("creative-studio") else "MANUAL_UPLOAD"
    try:
        result = PIPE.register_image(prompt_id=prompt["prompt_id"] if prompt else None, prompt=prompt,
                                     source_uri=uri, user=user, tool=tool, match_method=method)
    except PipelineError as e:
        log.error(f"{uri}: {e}")
        return {"error": str(e), "uri": uri}  # 200 so Eventarc doesn't retry a business-rule failure
    log.info(f"{uri} -> {result.get('generation_id')} v{result.get('version')} {method} "
             f"{(result.get('decision') or {}).get('status', result.get('status'))}")
    return {"uri": uri, "generation_id": result.get("generation_id"), "match_method": method}


def match_prompt(name: str, metadata: dict[str, str]):
    """TDD 6.4: explicit ID in path/metadata -> prompt-hash -> batch/SKU folder -> unmatched."""
    found = ids.find_ids(name + " " + " ".join(metadata.values()))
    if "PR" in found and (p := PIPE.bq.get_prompt(found["PR"])):
        return p, "PATH_ID"

    prompt_text = metadata.get(CFG.creative_studio.prompt_metadata_key)
    if prompt_text and (p := PIPE.bq.get_prompt_by_hash(ids.prompt_sha256(prompt_text))):
        return p, "PROMPT_HASH"

    if "BR" in found:
        sku_match = re.search(re.escape(found["BR"]) + r"/([^/]+)/", name)
        if sku_match:
            rows = PIPE.bq.query(
                "SELECT p.* FROM `${project}.${dataset}.enriched_prompt` p "
                "LEFT JOIN `${project}.${dataset}.generation` g USING (prompt_id) "
                "WHERE p.batch_run_id = @b AND LOWER(p.sku) = LOWER(@s) AND g.generation_id IS NULL",
                b=found["BR"], s=sku_match.group(1))
            if len(rows) == 1:  # only link by folder when it is unambiguous
                return rows[0], "FOLDER"
    return None, "UNMATCHED"
