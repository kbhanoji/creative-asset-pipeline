"""Prompt Enrichment Agent (SOW Agent 1, TDD 7) built with ADK.

Deployed to Vertex AI Agent Engine and registered in Gemini Enterprise by
`cap deploy agent`. Every tool writes lineage through cap.pipeline, so the
user who prompted, every prompt version and every image are traceable in
BigQuery. Nothing here is customer-specific: brand, SKUs, models, thresholds
and generation mode all come from the customer config ($CAP_CONFIG).
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from google.adk.agents import Agent
from google.adk.tools import ToolContext

from cap import config as cap_config
from cap.config import GenerationMode
from cap.pipeline import Pipeline, PipelineError


@lru_cache(maxsize=1)
def _cfg():
    return cap_config.load(os.environ.get("CAP_CONFIG"))


@lru_cache(maxsize=1)
def _pipe() -> Pipeline:
    return Pipeline(_cfg())


def _user(tool_context: ToolContext | None) -> str:
    """End-user identity recorded as created_by.

    gemini_enterprise: the signed-in Gemini Enterprise user. local / agent_engine: the ADK session user,
    or CAP_USER (set by `cap agent web|chat` to your gcloud account) when the dev UI sends its placeholder "user".
    """
    uid = getattr(tool_context, "user_id", None) if tool_context else None
    if not uid and tool_context is not None:
        uid = getattr(getattr(tool_context, "_invocation_context", None), "user_id", None)
    if uid in (None, "", "user", "unknown") and os.environ.get("CAP_USER"):
        uid = os.environ["CAP_USER"]
    return uid or "unknown"


def _err(e: Exception) -> dict[str, Any]:
    return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------- tools ----
def list_skus() -> dict[str, Any]:
    """List the SKUs available for prompt enrichment."""
    return {"skus": [{"sku": s.sku, "product_name": s.product_name, "category": s.category,
                      "subcategory": s.subcategory} for s in _cfg().skus]}


def get_sku_context(sku: str) -> dict[str, Any]:
    """Return the product facts, reference images and generation options for one SKU.

    Args:
        sku: Product SKU, e.g. DCD800B.
    """
    try:
        cfg = _cfg()
        s = cfg.sku(sku)
        g = cfg.generation
        return {
            "status": "ok", "sku": s.sku, "parent_asset_id": s.parent_asset_id, "product_name": s.product_name,
            "brand": cfg.customer.brand, "category": s.category, "subcategory": s.subcategory,
            "key_features": s.key_features, "reference_images": s.reference_images,
            "region": cfg.customer.region_market, "language": cfg.customer.language,
            "shot_types": cfg.catalog.shot_types, "asset_types": cfg.catalog.asset_types,
            "allowed_aspect_ratios": g.allowed_aspect_ratios, "default_aspect_ratio": g.default_aspect_ratio,
            "default_resolution": g.default_resolution, "variants_per_prompt": g.variants_per_prompt,
            "negative_constraints": cfg.brand.negative_constraints, "safety_constraints": cfg.brand.safety_constraints,
            "generation_mode": g.mode.value, "image_model": cfg.models.image_generation,
        }
    except KeyError as e:
        return _err(e)


def start_batch(skus: list[str], tool_context: ToolContext) -> dict[str, Any]:
    """Start a batch run for one or more SKUs and return its Batch Run ID. Call once per working session.

    Args:
        skus: SKUs included in this batch.
    """
    try:
        return {"status": "ok", "batch_run_id": _pipe().start_batch(skus, _user(tool_context), trigger_type="AGENT")}
    except (KeyError, PipelineError) as e:
        return _err(e)


def save_enriched_prompt(sku: str, prompt_text: str, shot_type: str, variant_index: int,
                         tool_context: ToolContext, batch_run_id: str = "", base_prompt: str = "",
                         asset_type: str = "base_image", aspect_ratio: str = "", resolution: str = "",
                         persona: str = "") -> dict[str, Any]:
    """Save ONE enriched prompt (one per variant). Assigns the Prompt ID, prompt family and version.

    In automatic mode this also generates the images, scores them and returns the routing decisions.
    In manual mode it returns the copy-ready block for the user to paste into Creative Studio.

    Args:
        sku: Product SKU.
        prompt_text: The full enriched prompt.
        shot_type: One of the configured shot types (e.g. hero, lifestyle).
        variant_index: 1-based index of this variant.
        batch_run_id: Batch Run ID from start_batch.
        base_prompt: The user's original short prompt.
        asset_type: One of the configured asset types (default base_image).
        aspect_ratio: One of the allowed aspect ratios; empty = default.
        resolution: 1K, 2K or 4K; empty = default.
        persona: Brand persona used, if any.
    """
    user = _user(tool_context)
    try:
        pipe = _pipe()
        saved = pipe.save_prompts([{
            "sku": sku, "prompt_text": prompt_text, "shot_type": shot_type, "variant_index": variant_index,
            "base_prompt": base_prompt, "asset_type": asset_type, "aspect_ratio": aspect_ratio or None,
            "resolution": resolution or None, "persona": persona or None,
        }], user=user, batch_run_id=batch_run_id or None, origin="AGENT")[0]
        out: dict[str, Any] = {
            "status": "ok", "prompt_id": saved["prompt_id"], "prompt_lineage_id": saved["prompt_lineage_id"],
            "prompt_version": saved["prompt_version"], "gcs_uri": saved["gcs_uri"],
            "generation_mode": _cfg().generation.mode.value,
        }
        if _cfg().generation.mode == GenerationMode.automatic:
            gens = pipe.generate_for_prompt(saved["prompt_id"], user)
            out["images"] = [_summarize(g) for g in gens]
        else:
            out["copy_text"] = saved["copy_text"]
        return out
    except (KeyError, PipelineError) as e:
        return _err(e)


def revise_prompt(generation_id: str, new_prompt_text: str, reason: str,
                  tool_context: ToolContext) -> dict[str, Any]:
    """Human-in-the-loop: save an edited prompt for an image that needs revision and regenerate it.

    Args:
        generation_id: GEN- ID of the image to fix.
        new_prompt_text: The corrected prompt written or approved by the user.
        reason: What the user is correcting.
    """
    try:
        r = _pipe().revise(generation_id, new_prompt_text, _user(tool_context), reason)
        out = {"status": "ok", "prompt_id": r["prompt"]["prompt_id"], "prompt_version": r["prompt"]["prompt_version"]}
        if "generations" in r:
            out["images"] = [_summarize(g) for g in r["generations"]]
        else:
            out["next_step"] = r["next_step"]
            out["copy_text"] = r["prompt"]["copy_text"]
        return out
    except PipelineError as e:
        return _err(e)


def decide_image(generation_id: str, decision: str, reason: str, tool_context: ToolContext) -> dict[str, Any]:
    """Human-in-the-loop: approve an image as-is or reject it.

    Args:
        generation_id: GEN- ID of the image.
        decision: APPROVE or REJECT.
        reason: Reason for the decision (required for REJECT).
    """
    try:
        return {"status": "ok", **_pipe().decide(generation_id, decision, _user(tool_context), reason)}
    except PipelineError as e:
        return _err(e)


def get_status(sku: str = "", batch_run_id: str = "", generation_id: str = "") -> dict[str, Any]:
    """Look up image status, scores and locations by SKU, batch or generation ID (at least one).

    Args:
        sku: Filter by SKU.
        batch_run_id: Filter by batch.
        generation_id: Filter by generation.
    """
    if not (sku or batch_run_id or generation_id):
        return _err(ValueError("give sku, batch_run_id or generation_id"))
    rows = _pipe().bq.query(
        "SELECT generation_id, version, sku, prompt_id, prompt_version, prompted_by, composite, hallucination_risk, "
        "current_status, output_stage, status_uri, intermediate_uri FROM `${project}.${dataset}.v_asset_lifecycle` "
        "WHERE (@s = '' OR sku = @s) AND (@b = '' OR batch_run_id = @b) AND (@g = '' OR generation_id = @g) "
        "ORDER BY created_at DESC LIMIT 50", s=sku, b=batch_run_id, g=generation_id)
    return {"status": "ok", "images": rows}


def get_prompt_history(sku: str) -> dict[str, Any]:
    """Every prompt version saved for a SKU, newest first.

    Args:
        sku: Product SKU.
    """
    rows = _pipe().bq.query(
        "SELECT prompt_id, prompt_lineage_id, prompt_version, parent_prompt_id, origin, shot_type, variant_index, "
        "aspect_ratio, created_by, created_at, prompt_text FROM `${project}.${dataset}.enriched_prompt` "
        "WHERE sku = @s ORDER BY created_at DESC LIMIT 50", s=sku)
    return {"status": "ok", "prompts": rows}


def _summarize(g: dict[str, Any]) -> dict[str, Any]:
    d = g.get("decision") or {}
    return {"generation_id": g.get("generation_id"), "version": g.get("version"), "gcs_uri": g.get("gcs_uri"),
            "composite": d.get("composite"), "hallucination_risk": d.get("hallucination_risk"),
            "status": d.get("status") or g.get("status"), "reason": d.get("reason"), "notice": g.get("notice")}


# ----------------------------------------------------------- instructions --
def _instruction() -> str:
    cfg = _cfg()
    t = cfg.scoring.thresholds
    skills = "\n\n".join(f"### {k}\n{v}" for k, v in cfg.skills().items())
    below = "fails quality control" if t.below_revision_action == "FAIL" else "is flagged for mandatory human review"
    manual = cfg.generation.mode == GenerationMode.manual
    return f"""ROLE
You are the {cfg.customer.brand} prompt-enrichment agent for {cfg.customer.name}. You turn a short base prompt
into detailed, brand-compliant, safety-compliant image generation prompts for Google image models.

WORKFLOW
1. Ask which SKU(s) and shot type(s) the user wants if they haven't said. Use list_skus / get_sku_context.
2. Call start_batch once for the session's SKUs and keep the Batch Run ID.
3. For each requested variant (default {cfg.generation.variants_per_prompt}), write an enriched prompt and call
   save_enriched_prompt once per variant. Vary only background, camera angle or environment between variants.
4. {"Show the user each copy_text block exactly as returned and tell them to paste it unchanged into Creative Studio." if manual else "Report each image's score and status."}
5. Scores: composite >= {t.auto_approve_min:g} is auto-approved; {t.revision_min:g} to {t.auto_approve_min:g} needs revision
   (help the user rewrite the prompt, then call revise_prompt, or decide_image to approve as-is);
   below {t.revision_min:g} {below}.
6. Use get_status and get_prompt_history when the user asks what happened to an asset.

RULES
- Use ONLY facts from get_sku_context. Never invent features, specifications, labels or claims. If data is missing,
  say which field is missing instead of guessing.
- Depict the product accurately (shape, colour, branding placement). No competitor branding.
- Always show safe use: correct PPE, guards in place, safe hand positions.
- Respect the requested shot type, aspect ratio and resolution. State them in the prompt.
- Always show Prompt IDs and Generation IDs to the user; they are the audit keys.

BRAND, SAFETY AND PROMPT-CRAFT SKILLS
{skills or "(no skills configured)"}
"""


root_agent = Agent(
    name="prompt_enrichment_agent",
    model=os.environ.get("CAP_PROMPT_MODEL") or _cfg().models.prompt_agent,
    description=_cfg().agent.description or "Brand-compliant prompt enrichment with lineage.",
    instruction=_instruction(),
    tools=[list_skus, get_sku_context, start_batch, save_enriched_prompt, revise_prompt, decide_image,
           get_status, get_prompt_history],
)
