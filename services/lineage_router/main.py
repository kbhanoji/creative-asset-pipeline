"""Lineage & Scoring Router (TDD S2), Cloud Run service behind Eventarc.

Receives 'object finalized' events for:
  - the creative-studio bucket (images generated in Creative Studio, manual mode), and
  - the intermediate bucket under inbox/ (images a user uploads manually).
Links each image to its Prompt ID, records the generation, scores it and routes it.
Everything else is ignored, so the pipeline's own writes never re-trigger it.

Creative Studio images: the router reads GCC's `media_items` row for the file (prompt as
typed, user, model, workspace), links by prompt hash, scores against the brand guideline
GCC extracted from the brand PDF, and writes the score back to `media_items.critique`
(shown in GCC's gallery). See cap/gcc_db.py.
"""
from __future__ import annotations

import logging
import os
import re

from fastapi import FastAPI, Request

from cap import config as cap_config
from cap import gcc_db, ids
from cap.pipeline import Pipeline, PipelineError

logging.basicConfig(level=logging.INFO, format='{"severity":"%(levelname)s","message":"%(message)s"}')
log = logging.getLogger("lineage-router")

CFG = cap_config.load(os.environ.get("CAP_CONFIG"))
PIPE = Pipeline(CFG)
CS = CFG.creative_studio
GCC = gcc_db.GccDb(CFG) if (CS.deployment == "cloud_run" and CS.db_integration) else None
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")
app = FastAPI()


def _safe(fn, *args, default=None, what=""):
    """GCC database calls are best-effort: lineage and scoring must not fail because of them."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        log.warning(f"GCC database {what} failed: {e}")
        return default


def _brand_for(media: dict | None):
    if not (GCC and media and CS.use_gcc_brand_guidelines):
        return None
    return gcc_db.brand_context(_safe(GCC.brand_guideline, media.get("workspace_id"), what="brand guideline"))


def _writeback(media: dict | None, result: dict) -> None:
    if not (GCC and media and CS.writeback_critique and result.get("decision")):
        return
    text = gcc_db.critique_text(result["decision"], result.get("sub_scores"), result.get("rationale", ""), result)
    if _safe(GCC.write_critique, media["id"], text, default=False, what="critique write-back") is not False:
        log.info(f"critique written to GCC media_item {media['id']} for {result.get('generation_id')}")


@app.get("/healthz")
def healthz():
    return {"ok": True, "customer": CFG.customer.id, "env": CFG.environment, "gcc_db": bool(GCC)}


@app.post("/tasks/gcc-idle-check")
def gcc_idle_check():
    """Cloud Scheduler (dev): stop Creative Studio's Cloud SQL after idle_stop_minutes without backend requests."""
    from cap.gcc_idle import check_and_stop
    result = check_and_stop(CFG)
    log.info(f"gcc idle check: {result}")
    return result


@app.post("/tasks/link-image")
async def link_image(request: Request):
    """Link an UNMATCHED image to a Prompt ID (called by `cap image link`), with GCC context + write-back."""
    body = await request.json()
    gen = PIPE.bq.get_generation(body["generation_id"], 1)
    if not gen:
        return {"error": f"generation {body['generation_id']} not found"}
    media = _safe(GCC.media_item_for_uri, gen.get("source_uri"), what="lookup") if GCC and gen.get("source_uri") else None
    try:
        result = PIPE.link(body["generation_id"], body["prompt_id"],
                           (media or {}).get("user_email") or body.get("user") or "unknown",
                           brand_context=_brand_for(media), model=(media or {}).get("model"))
    except PipelineError as e:
        return {"error": str(e)}
    _writeback(media, result)
    return _public(result)


@app.post("/tasks/gcc-writeback")
async def gcc_writeback(request: Request):
    """Write the latest pipeline score of a generation into GCC's media_items.critique (backfill / re-sync)."""
    body = await request.json()
    gen = PIPE.bq.get_generation(body["generation_id"], body.get("version"))
    if not (gen and GCC and gen.get("source_uri")):
        return {"error": "generation not found, not from Creative Studio, or GCC integration off"}
    media = _safe(GCC.media_item_for_uri, gen["source_uri"], what="lookup")
    s = PIPE.bq.one("SELECT * FROM `${project}.${dataset}.v_latest_score` WHERE generation_id = @g AND version = @v",
                    g=gen["generation_id"], v=int(gen["version"]))
    if not (media and s):
        return {"error": "no GCC media item or no score for this generation"}
    decision = {"composite": s["composite"], "status": s["decision_status"], "hallucination_band": s["hallucination_band"]}
    subs = {k: s.get(k) for k in CFG.scoring.weights}
    _writeback(media, {**gen, "decision": decision, "sub_scores": subs, "rationale": s.get("rationale") or ""})
    return {"generation_id": gen["generation_id"], "media_item_id": media["id"], "written": True}


@app.post("/")
async def on_event(request: Request):
    body = await request.json()
    obj = body.get("data", body)  # structured or binary CloudEvent
    bucket, name = obj.get("bucket", ""), obj.get("name", "")
    ctype = obj.get("contentType", "")
    metadata = obj.get("metadata") or {}
    uri = f"gs://{bucket}/{name}"

    from_gcc = bucket == CFG.bucket("creative-studio")
    watched = from_gcc or (bucket == CFG.bucket("intermediate") and name.startswith("inbox/"))
    if not watched or not ctype.startswith(IMAGE_TYPES):
        return {"ignored": uri}
    if name.endswith(CS.ignore_suffixes):  # e.g. Creative Studio's <id>_thumbnail previews
        return {"ignored": uri, "reason": "thumbnail"}
    if PIPE.bq.generation_by_source(uri):  # redelivered event
        return {"ignored": uri, "reason": "already registered"}

    media = _safe(GCC.wait_media_item, uri, what="lookup") if (GCC and from_gcc) else None
    prompt, method = match_prompt(name, metadata, media)
    user = ((media or {}).get("user_email") or metadata.get(CS.user_metadata_key) or metadata.get("created-by")
            or ("creative-studio" if from_gcc else "manual-upload"))
    tool = "CREATIVE_STUDIO" if from_gcc else "MANUAL_UPLOAD"
    try:
        result = PIPE.register_image(prompt_id=prompt["prompt_id"] if prompt else None, prompt=prompt,
                                     source_uri=uri, user=user, tool=tool, match_method=method,
                                     model=(media or {}).get("model"),
                                     brand_context=_brand_for(media) if prompt else None)
    except PipelineError as e:
        log.error(f"{uri}: {e}")
        return {"error": str(e), "uri": uri}  # 200 so Eventarc doesn't retry a business-rule failure
    if media and prompt:
        _writeback(media, result)
    elif media and GCC and CS.writeback_critique:
        _safe(GCC.write_critique, media["id"],
              f"Not linked to a pipeline prompt yet ({result.get('generation_id')}). Paste an enriched prompt "
              f"unchanged, or link it with `cap image link {result.get('generation_id')} <PR-id>`.", what="note")
    log.info(f"{uri} -> {result.get('generation_id')} v{result.get('version')} {method} "
             f"{(result.get('decision') or {}).get('status', result.get('status'))} user={user}")
    return {"uri": uri, "generation_id": result.get("generation_id"), "match_method": method}


def match_prompt(name: str, metadata: dict[str, str], media: dict | None = None):
    """GCC media_items prompt hash -> ID in path/metadata -> metadata prompt hash -> batch/SKU folder -> unmatched."""
    typed = gcc_db.prompt_text_for_matching(media) if media else None
    if typed and (p := PIPE.bq.get_prompt_by_hash(ids.prompt_sha256(typed))):
        return p, "GCC_PROMPT_HASH"

    found = ids.find_ids(name + " " + " ".join(metadata.values()))
    if "PR" in found and (p := PIPE.bq.get_prompt(found["PR"])):
        return p, "PATH_ID"

    prompt_text = metadata.get(CS.prompt_metadata_key)
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


def _public(result: dict) -> dict:
    keys = ("generation_id", "version", "prompt_id", "gcs_uri", "resolution_actual", "created_by", "model",
            "decision", "notice", "error")
    return {k: result[k] for k in keys if k in result}
