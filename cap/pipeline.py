"""Pipeline orchestration: prompts -> images -> scores -> routing -> storage, all with lineage.

Used by the prompt agent's tools, the lineage router service and the CLI, so
every entry point records the same IDs and rows.

Flow (TDD 3.3, SOW 3.4.1):
  save_prompts()        prompt versions per SKU slot (PR-/PL- IDs) -> GCS prompts + BigQuery
  generate_for_prompt() automatic mode: image model -> intermediate bucket -> register
  register_image()      manual mode: Creative Studio output (via router) or manual upload -> register
  score_and_route()     critic -> composite/hallucination -> APPROVED | NEEDS_REVISION | FAILED_QC | FLAGGED
  revise()              HITL: human edits the prompt -> new prompt version -> regenerate -> version n+1
  decide()              HITL: human approves as-is or rejects
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from . import ids
from .config import Config, GenerationMode
from .imaging import embed_payload, embed_png_text, image_size, resolution_class  # noqa: F401
from .lineage import Lineage, now
from .routing import FINAL_STATUSES, Decision, Status, decide as route
from .storage import Storage

EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


class PipelineError(RuntimeError):
    pass


class Pipeline:
    def __init__(self, cfg: Config, lineage: Lineage | None = None, storage: Storage | None = None):
        self.cfg = cfg
        self.bq = lineage or Lineage(cfg)
        self.gcs = storage or Storage(cfg)

    # =====================================================================
    # Batches
    # =====================================================================
    def start_batch(self, skus: list[str], user: str, trigger_type: str = "CLI", notes: str = "") -> str:
        for s in skus:
            self.cfg.sku(s)  # validates
        day = datetime.now(timezone.utc)
        prefix = f"BR-{day:%Y%m%d}-{self.cfg.customer.brand_code}-"
        batch_id = ids.batch_run_id(self.cfg.customer.brand_code, self.bq.next_batch_seq(prefix), day)
        snapshot = self.cfg.model_dump(mode="json", exclude={"base_dir", "skus"})
        cfg_bytes = json.dumps(snapshot, sort_keys=True).encode()
        snap_uri = self.gcs.upload_json("config", f"snapshots/{batch_id}/config.json", snapshot)
        self.bq.insert("batch_run", {
            "batch_run_id": batch_id, "status": "STARTED", "trigger_type": trigger_type,
            "requested_by_user": user, "brand": self.cfg.customer.brand, "skus": skus,
            "generation_mode": self.cfg.generation.mode.value, "config_sha256": hashlib.sha256(cfg_bytes).hexdigest(),
            "config_snapshot_uri": snap_uri, "notes": notes, "created_at": now(),
        })
        return batch_id

    # =====================================================================
    # Prompts (versioned per SKU slot)
    # =====================================================================
    def save_prompts(self, prompts: list[dict[str, Any]], user: str, batch_run_id: str | None = None,
                     origin: str = "AGENT") -> list[dict[str, Any]]:
        """Persist enriched prompts. Each dict needs at least sku + prompt_text; everything else defaults from config.

        A prompt for the same SKU/asset type/shot type/variant/aspect ratio joins the existing prompt
        family (prompt_lineage_id) as the next version, so prompt history accumulates per SKU.
        """
        g = self.cfg.generation
        skills_versions = {k: _skill_version(v) for k, v in self.cfg.skills().items()}
        saved = []
        for i, p in enumerate(prompts, 1):
            if not (p.get("prompt_text") or "").strip():
                raise PipelineError(f"prompt {i}: prompt_text is empty")
            sku = self.cfg.sku(p["sku"])
            asset_type = p.get("asset_type") or "base_image"
            shot_type = p.get("shot_type") or "hero"
            if shot_type not in self.cfg.catalog.shot_types:
                raise PipelineError(f"shot_type {shot_type!r} not in catalog.shot_types {self.cfg.catalog.shot_types}")
            if asset_type not in self.cfg.catalog.asset_types:
                raise PipelineError(f"asset_type {asset_type!r} not in catalog.asset_types")
            aspect = p.get("aspect_ratio") or g.default_aspect_ratio
            if aspect not in g.allowed_aspect_ratios:
                raise PipelineError(f"aspect_ratio {aspect!r} not in generation.allowed_aspect_ratios")
            variant_index = int(p.get("variant_index") or i)

            lineage_id = p.get("prompt_lineage_id") or self.bq.find_prompt_lineage(
                sku.sku, asset_type, shot_type, variant_index, aspect)
            version = (self.bq.latest_prompt_version(lineage_id) + 1) if lineage_id else 1
            lineage_id = lineage_id or ids.prompt_lineage_id()
            pid = ids.prompt_id()
            row = {
                "prompt_id": pid, "prompt_lineage_id": lineage_id, "prompt_version": version,
                "parent_prompt_id": p.get("parent_prompt_id"), "batch_run_id": batch_run_id or p.get("batch_run_id"),
                "parent_asset_id": sku.parent_asset_id, "sku": sku.sku, "brand": self.cfg.customer.brand,
                "category": sku.category, "subcategory": sku.subcategory, "asset_type": asset_type,
                "shot_type": shot_type, "variant_index": variant_index,
                "variants_total": int(p.get("variants_total") or g.variants_per_prompt),
                "resolution": p.get("resolution") or g.default_resolution, "aspect_ratio": aspect,
                "region": p.get("region") or self.cfg.customer.region_market, "persona": p.get("persona"),
                "base_prompt": p.get("base_prompt"), "prompt_text": p["prompt_text"].strip(),
                "negative_constraints": p.get("negative_constraints") or self.cfg.brand.negative_constraints,
                "reference_images": p.get("reference_images") or sku.reference_images,
                "prompt_sha256": ids.prompt_sha256(p["prompt_text"]), "skills_versions": skills_versions,
                "model": p.get("model") or self.cfg.models.prompt_agent, "origin": origin,
                "revision_reason": p.get("revision_reason"), "source_generation_id": p.get("source_generation_id"),
                "created_by": user, "created_at": now(),
            }
            path = self.gcs.object_path(sku=sku.sku, batch_run_id=row["batch_run_id"], entity_id=lineage_id,
                                        version=version, file=f"{pid}.json")
            row["gcs_uri"] = self.gcs.upload_json("prompts", path, row, {
                "prompt-id": pid, "prompt-lineage-id": lineage_id, "prompt-version": version,
                "batch-run-id": row["batch_run_id"], "parent-asset-id": sku.parent_asset_id, "created-by": user})
            self.bq.insert("enriched_prompt", row)
            self._disposition("PROMPT", pid, version, "CREATED", "PROMPT_SAVED", origin, False, row["gcs_uri"],
                              row["batch_run_id"], user, "USER" if origin != "SYSTEM" else "SYSTEM")
            saved.append({**row, "copy_text": self.copy_block(row)})
        return saved

    def copy_block(self, p: dict[str, Any]) -> str:
        """Markdown block the user works from in Creative Studio (manual mode).

        One variant per block, and the prompt alone inside a code box: pasting headers or several
        variants together makes the model draw a collage, and one image must map to one Prompt ID.
        """
        refs = ", ".join(p.get("reference_images") or []) or "none"
        return (
            f"**Variant {p.get('variant_index') or 1}: Prompt ID `{p['prompt_id']}`** "
            f"(family `{p['prompt_lineage_id']}`, v{p['prompt_version']}, batch `{p.get('batch_run_id') or 'adhoc'}`)\n\n"
            f"Creative Studio settings: model **{self.cfg.models.image_generation}** · aspect ratio **{p['aspect_ratio']}** · "
            f"resolution **{p['resolution']}** · reference images: {refs}\n\n"
            f"Copy **only** the text in the box (nothing above or below it) into one Creative Studio generation:\n\n"
            f"```text\n{p['prompt_text']}\n```\n"
        )

    # =====================================================================
    # Images
    # =====================================================================
    def generate_for_prompt(self, prompt_id: str, user: str, n: int | None = None,
                            generation_id: str | None = None) -> list[dict[str, Any]]:
        """Automatic mode: call the image model, store outputs as intermediates, then score and route each."""
        from .generation import generate_images

        prompt = self._prompt(prompt_id)
        n = 1 if generation_id else (n or int(prompt.get("variants_total") or self.cfg.generation.variants_per_prompt))
        self._check_ceiling(n)
        refs = self._load_refs(prompt.get("reference_images") or [])
        images = generate_images(self.cfg, prompt, n, refs)
        results = []
        for idx, img in enumerate(images, 1):
            results.append(self.register_image(
                data=img.data, mime_type=img.mime_type, prompt_id=prompt_id, user=user, tool="PIPELINE_AUTO",
                match_method="PIPELINE", model=img.model, generation_id=generation_id,
                prompt=prompt))
        return results

    def register_image(self, *, prompt_id: str | None, user: str, tool: str, match_method: str,
                       data: bytes | None = None, mime_type: str | None = None, source_uri: str | None = None,
                       model: str | None = None, generation_id: str | None = None, variant_index: int | None = None,
                       prompt: dict[str, Any] | None = None, score: bool = True) -> dict[str, Any]:
        """Record one image version and (by default) score and route it.

        New image          -> new GEN- ID, version 1.
        Revision of image  -> same GEN- ID, version = latest + 1. The generation is found from generation_id,
                              or from the prompt's source_generation_id (prompt created by revise()).
        """
        if data is None:
            if not source_uri:
                raise PipelineError("register_image needs data or source_uri")
            existing = self.bq.generation_by_source(source_uri)
            if existing:
                return {"skipped": True, "reason": "already registered", **existing}
            data, mime_type, _ = self.gcs.download(source_uri)
        mime_type = mime_type or "image/png"

        prompt = prompt or (self._prompt(prompt_id) if prompt_id else None)
        if prompt is None:
            return self._register_unmatched(data, mime_type, source_uri, user, tool)

        generation_id = generation_id or prompt.get("source_generation_id")
        parent_generation_id = None
        if generation_id:
            prev = self.bq.get_generation(generation_id)
            if not prev:
                raise PipelineError(f"generation {generation_id} not found")
            version = int(prev["version"]) + 1
            parent_generation_id = generation_id
            variant_index = prev.get("variant_index")
        else:
            self._check_ceiling(1)
            generation_id, version = ids.generation_id(), 1
        if variant_index is None:
            variant_index = prompt.get("variant_index")  # the prompt slot this image belongs to

        # name and record the ACTUAL pixel size (Creative Studio may ignore the requested 2K/4K)
        size = image_size(data)
        actual_res = resolution_class(*size) if size else ""
        requested_res = prompt.get("resolution") or ""
        ext = EXT.get(mime_type, "png")
        file = self.gcs.file_name(sku=prompt["sku"], shot_type=prompt.get("shot_type") or "hero",
                                  aspect=prompt.get("aspect_ratio") or "1:1", resolution=actual_res or requested_res,
                                  version=version, ext=ext)
        path = self.gcs.object_path(sku=prompt["sku"], batch_run_id=prompt.get("batch_run_id"),
                                    entity_id=generation_id, version=version, file=file)
        base_md = self._base_metadata(prompt, generation_id, version, user)
        size_md = {"width": size[0], "height": size[1], "resolution-actual": actual_res,
                   "resolution-requested": requested_res} if size else {"resolution-requested": requested_res}
        uri = self.gcs.upload_bytes("intermediate", path, data, mime_type,
                                    {**base_md, **size_md, "status": Status.PENDING_SCORE})

        row = {
            "generation_id": generation_id, "version": version, "parent_generation_id": parent_generation_id,
            "prompt_id": prompt["prompt_id"], "prompt_lineage_id": prompt.get("prompt_lineage_id"),
            "parent_asset_id": prompt["parent_asset_id"], "sku": prompt["sku"], "batch_run_id": prompt.get("batch_run_id"),
            "variant_index": variant_index, "gcs_uri": uri, "sha256": ids.sha256_bytes(data), "mime_type": mime_type,
            "width": size[0] if size else None, "height": size[1] if size else None,
            "resolution_actual": actual_res or None,
            # only record a model we know was used: the pipeline's own call, or one passed in explicitly.
            # Creative Studio's model comes from its database once the router reads it.
            "model": model or (self.cfg.models.image_generation if tool == "PIPELINE_AUTO" else None), "tool": tool,
            "generation_mode": self.cfg.generation.mode.value, "match_method": match_method, "source_uri": source_uri,
            "created_by": user, "created_at": now(),
        }
        self.bq.insert("generation", row)
        self.bq.insert("edit_event", {
            "event_id": ids.event_id(), "generation_id": generation_id,
            "prev_version": version - 1 if version > 1 else None, "new_version": version,
            "prev_prompt_id": None, "new_prompt_id": prompt["prompt_id"],
            "action": "REGENERATE" if version > 1 else "REGISTER", "prompt_text": prompt["prompt_text"],
            "tool": tool, "notes": f"match_method={match_method}", "editor_identity": user, "created_at": now()})
        self._disposition("GENERATION", generation_id, version, Status.PENDING_SCORE, "REGISTERED", None, False, uri,
                          prompt.get("batch_run_id"), user, "USER" if tool != "PIPELINE_AUTO" else "SYSTEM")
        if size and requested_res and actual_res != requested_res:
            row["notice"] = (f"Requested {requested_res} but the image is {size[0]}x{size[1]} ({actual_res}). "
                             "Set the resolution in the generation tool.")
        if version > self.cfg.scoring.hitl.edit_notice_at:
            row["notice"] = (f"This image has {version - 1} revisions. Each regeneration consumes model capacity "
                             f"billed to {self.cfg.customer.name}.")
        if score:
            row["decision"] = self.score_and_route(row, data=data, prompt=prompt).__dict__
        return row

    def _register_unmatched(self, data: bytes, mime_type: str, source_uri: str | None, user: str, tool: str) -> dict:
        gid = ids.generation_id()
        path = f"unmatched/{datetime.now(timezone.utc):%Y%m%d}/{gid}.{EXT.get(mime_type, 'png')}"
        uri = self.gcs.upload_bytes("quarantine", path, data, mime_type, {"generation-id": gid, "status": Status.UNMATCHED,
                                                                          "source-uri": source_uri, "ai-generated": "true"})
        self.bq.insert("generation", {
            "generation_id": gid, "version": 1, "parent_asset_id": "UNKNOWN", "gcs_uri": uri,
            "sha256": ids.sha256_bytes(data), "mime_type": mime_type, "tool": tool,
            "generation_mode": self.cfg.generation.mode.value, "match_method": "UNMATCHED", "source_uri": source_uri,
            "created_by": user, "created_at": now()})
        self._disposition("GENERATION", gid, 1, Status.UNMATCHED, "LINK_REQUIRED",
                          "no Prompt ID could be matched; link with `cap image link`", False, uri, None, user, "SYSTEM")
        return {"generation_id": gid, "version": 1, "status": Status.UNMATCHED, "gcs_uri": uri}

    # =====================================================================
    # Scoring + Gate 1 routing
    # =====================================================================
    def score_and_route(self, gen: dict[str, Any], data: bytes | None = None,
                        prompt: dict[str, Any] | None = None) -> Decision:
        from .scoring import score_image

        prompt = prompt or self._prompt(gen["prompt_id"])
        if data is None:
            data, _, _ = self.gcs.download(gen["gcs_uri"])
        refs = self._load_refs(prompt.get("reference_images") or [])
        critic = score_image(self.cfg, data, gen.get("mime_type") or "image/png", prompt, refs)
        d = route(critic.sub_scores, self.cfg.scoring)
        version = int(gen["version"])
        self.bq.insert("score", {
            "score_id": ids.event_id(), "generation_id": gen["generation_id"], "version": version,
            "score_type": "rescore" if version > 1 else "initial", "composite": d.composite,
            **{k: critic.sub_scores.get(k) for k in self.cfg.scoring.weights},
            "hallucination_risk": d.hallucination_risk, "hallucination_band": d.hallucination_band, "tier": d.tier,
            "decision_status": d.status, "decision_action": d.action, "decision_reason": d.reason,
            "rationale": critic.rationale, "critic_provider": critic.provider, "critic_model": critic.model,
            "weights": self.cfg.scoring.weights, "thresholds": self.cfg.scoring.thresholds.model_dump(),
            "raw": critic.raw, "scored_at": now()})

        score_md = {
            "composite-score": f"{d.composite / 100:.2f}", "status": d.status, "tier": d.tier,
            **{f"score-{k.replace('_', '-')}": f"{critic.sub_scores.get(k, 0):.2f}" for k in self.cfg.scoring.weights},
            "hallucination-risk": d.hallucination_risk, "hallucination-band": d.hallucination_band,
            "critic-rationale": critic.rationale,
        }
        self.gcs.set_metadata(gen["gcs_uri"], score_md)

        final_uri = gen["gcs_uri"]
        if d.status in (Status.APPROVED, Status.FAILED_QC):
            final_uri = self._publish(gen, data, prompt, d, critic, "SYSTEM")
        self._disposition("GENERATION", gen["generation_id"], version, d.status, d.action, d.reason,
                          d.status in FINAL_STATUSES, final_uri, gen.get("batch_run_id"), "system:scoring-router", "SYSTEM")
        if d.status in FINAL_STATUSES:
            self._audit_package(gen["generation_id"])
        return d

    def rescore(self, generation_id: str, version: int | None = None) -> Decision:
        gen = self.bq.get_generation(generation_id, version)
        if not gen:
            raise PipelineError(f"generation {generation_id} not found")
        return self.score_and_route(gen)

    # =====================================================================
    # HITL
    # =====================================================================
    def revise(self, generation_id: str, new_prompt_text: str, user: str, reason: str = "",
               force: bool = False) -> dict[str, Any]:
        """Human edits the prompt for an image in NEEDS_REVISION (or FLAGGED) and regenerates.

        Creates the next prompt version (parent_prompt_id -> previous prompt). Automatic mode regenerates
        immediately as version n+1 of the same Generation ID; manual mode returns the block to paste into
        Creative Studio and the router links the result back through the prompt hash.
        """
        gen = self.bq.get_generation(generation_id)
        if not gen:
            raise PipelineError(f"generation {generation_id} not found")
        status = self.bq.current_status(generation_id, int(gen["version"]))
        allowed = {Status.NEEDS_REVISION, Status.FLAGGED}
        if status not in allowed and not force:
            raise PipelineError(f"{generation_id} v{gen['version']} is {status}; revise is allowed for {sorted(allowed)} "
                                "(use force to override)")
        old = self._prompt(gen["prompt_id"])
        new = self.save_prompts([{
            **{k: old.get(k) for k in ("sku", "asset_type", "shot_type", "variant_index", "variants_total", "resolution",
                                       "aspect_ratio", "region", "persona", "negative_constraints", "reference_images",
                                       "prompt_lineage_id", "batch_run_id")},
            "variants_total": 1, "base_prompt": old.get("prompt_text"), "prompt_text": new_prompt_text,
            "parent_prompt_id": old["prompt_id"], "revision_reason": reason, "source_generation_id": generation_id,
        }], user=user, batch_run_id=old.get("batch_run_id"), origin="HITL_REVISION")[0]
        self.bq.insert("edit_event", {
            "event_id": ids.event_id(), "generation_id": generation_id, "prev_version": int(gen["version"]),
            "new_version": None, "prev_prompt_id": old["prompt_id"], "new_prompt_id": new["prompt_id"],
            "action": "REVISE_PROMPT", "prompt_text": new_prompt_text, "tool": "CAP", "notes": reason,
            "editor_identity": user, "created_at": now()})
        result: dict[str, Any] = {"prompt": new}
        if self.cfg.generation.mode == GenerationMode.automatic:
            result["generations"] = self.generate_for_prompt(new["prompt_id"], user, generation_id=generation_id)
        else:
            result["next_step"] = ("Paste the prompt below into Creative Studio (edit the existing image or generate "
                                   "in the batch folder). The new image is linked as the next version automatically.")
        return result

    def decide(self, generation_id: str, decision: str, user: str, reason: str = "",
               version: int | None = None) -> dict[str, Any]:
        """Human approves as-is (allowed for NEEDS_REVISION/FLAGGED) or rejects."""
        decision = decision.upper()
        if decision not in ("APPROVE", "REJECT"):
            raise PipelineError("decision must be APPROVE or REJECT")
        gen = self.bq.get_generation(generation_id, version)
        if not gen:
            raise PipelineError(f"generation {generation_id} not found")
        v = int(gen["version"])
        status = self.bq.current_status(generation_id, v)
        if status in (Status.UNMATCHED, Status.PENDING_SCORE):
            raise PipelineError(f"{generation_id} v{v} is {status}; link/score it before deciding")
        if status in FINAL_STATUSES:
            raise PipelineError(f"{generation_id} v{v} is already final ({status})")
        prompt = self._prompt(gen["prompt_id"])
        data, _, _ = self.gcs.download(gen["gcs_uri"])
        new_status = Status.APPROVED if decision == "APPROVE" else Status.REJECTED
        uri = self._publish(gen, data, prompt, None, None, user, human_status=new_status, reason=reason)
        self.bq.insert("edit_event", {
            "event_id": ids.event_id(), "generation_id": generation_id, "prev_version": v, "new_version": v,
            "action": decision, "tool": "CAP", "notes": reason, "editor_identity": user, "created_at": now()})
        self._disposition("GENERATION", generation_id, v, new_status, f"HUMAN_{decision}", reason, True, uri,
                          gen.get("batch_run_id"), user, "USER")
        self._audit_package(generation_id)
        return {"generation_id": generation_id, "version": v, "status": new_status, "gcs_uri": uri}

    def link(self, generation_id: str, prompt_id: str, user: str) -> dict[str, Any]:
        """Link an UNMATCHED image to its prompt, then score it (TDD 6.4 orphan handling)."""
        gen = self.bq.get_generation(generation_id, 1)
        if not gen or gen.get("match_method") != "UNMATCHED":
            raise PipelineError(f"{generation_id} is not an unmatched image")
        data, mt, _ = self.gcs.download(gen["gcs_uri"])
        self._disposition("GENERATION", generation_id, 1, "SUPERSEDED", "LINKED", f"re-registered under {prompt_id}",
                          True, gen["gcs_uri"], None, user, "USER")
        return self.register_image(data=data, mime_type=mt, prompt_id=prompt_id, user=user,
                                   tool=gen.get("tool") or "CREATIVE_STUDIO", match_method="MANUAL_LINK",
                                   source_uri=gen.get("source_uri"))

    # =====================================================================
    # helpers
    # =====================================================================
    def _publish(self, gen, data, prompt, d: Decision | None, critic, set_by: str,
                 human_status: str | None = None, reason: str = "") -> str:
        """Copy the image to the approved or rejected bucket with the scoring payload embedded."""
        status = human_status or d.status
        purpose = "approved" if status == Status.APPROVED else "rejected"
        payload = {
            "scoring_version": "1.0", "generation_id": gen["generation_id"], "version": int(gen["version"]),
            "prompt_id": gen.get("prompt_id"), "batch_run_id": gen.get("batch_run_id"),
            "status": status, "set_by": set_by, "reason": reason or (d.reason if d else ""),
        }
        if d and critic:
            payload.update({"composite_score": round(d.composite / 100, 4), "action_taken": d.action,
                            "hallucination_risk": d.hallucination_risk, "critic_model": critic.model,
                            "dissection": {k: {"score": critic.sub_scores.get(k), "weight": w}
                                           for k, w in self.cfg.scoring.weights.items()},
                            "rationale": critic.rationale})
        payload["timestamp"] = now()
        data = embed_payload(data, gen.get("mime_type") or "image/png", "gcc_scoring_payload", json.dumps(payload))
        _, name = self.gcs.split_uri(gen["gcs_uri"])
        md = {**self._base_metadata(prompt, gen["generation_id"], int(gen["version"]), gen.get("created_by")),
              "status": status, "decided-by": set_by}
        return self.gcs.upload_bytes(purpose, name, data, gen.get("mime_type") or "image/png", md)

    def _audit_package(self, generation_id: str) -> str:
        q = lambda t, extra="": self.bq.query(  # noqa: E731
            f"SELECT * FROM `${{project}}.${{dataset}}.{t}` WHERE generation_id = @g {extra}", g=generation_id)
        gens = q("generation", "ORDER BY version")
        prompt_ids = [g["prompt_id"] for g in gens if g.get("prompt_id")]
        prompts = [self.bq.get_prompt(p) for p in dict.fromkeys(prompt_ids)]
        pkg = {
            "generation_id": generation_id, "generated_at": now(),
            "versions": gens, "prompts": prompts, "scores": q("score", "ORDER BY scored_at"),
            "edits": q("edit_event", "ORDER BY created_at"),
            "dispositions": self.bq.query(
                "SELECT * FROM `${project}.${dataset}.disposition` WHERE entity_id = @g ORDER BY created_at",
                g=generation_id),
        }
        first = gens[0] if gens else {}
        path = (self.gcs.object_path(sku=first["sku"], batch_run_id=first.get("batch_run_id"), entity_id=generation_id,
                                     version=int(gens[-1]["version"]), file="audit.json")
                if first.get("sku") else f"unmatched/{generation_id}/audit.json")
        # audit objects are write-once (bucket retention policy): one file per final decision
        path = path.replace("audit.json", f"audit-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.json")
        return self.gcs.upload_json("audit", path, pkg, {"generation-id": generation_id})

    def _disposition(self, entity_type, entity_id, version, status, action, reason, is_final, uri, batch_run_id,
                     set_by, set_by_type) -> None:
        self.bq.insert("disposition", {
            "disposition_id": ids.event_id(), "entity_type": entity_type, "entity_id": entity_id, "version": version,
            "status": status, "action": action, "reason": reason, "is_final": is_final, "gcs_uri": uri,
            "batch_run_id": batch_run_id, "set_by": set_by, "set_by_type": set_by_type, "created_at": now()})

    def _base_metadata(self, prompt: dict[str, Any], generation_id: str, version: int, user: str | None) -> dict:
        return {"batch-run-id": prompt.get("batch_run_id"), "parent-asset-id": prompt.get("parent_asset_id"),
                "sku": prompt.get("sku"), "generation-id": generation_id, "version": version,
                "prompt-id": prompt.get("prompt_id"), "prompt-lineage-id": prompt.get("prompt_lineage_id"),
                "created-by": user, "ai-generated": "true"}

    def _prompt(self, prompt_id: str) -> dict[str, Any]:
        p = self.bq.get_prompt(prompt_id)
        if not p:
            raise PipelineError(f"prompt {prompt_id} not found")
        return p

    def _load_refs(self, uris: list[str]) -> list[tuple[bytes, str]]:
        out = []
        for u in uris:
            if u.startswith("gs://"):
                data, mt, _ = self.gcs.download(u)
                out.append((data, mt))
        return out

    def _check_ceiling(self, n: int) -> None:
        cap = self.cfg.generation.max_assets_total
        used = self.bq.count_generations()
        if used + n > cap:
            raise PipelineError(f"asset ceiling reached: {used} generated + {n} requested > max_assets_total {cap}")


def _skill_version(text: str) -> str:
    for line in text.splitlines():
        if "version:" in line.lower():
            return line.lower().split("version:")[-1].strip().split()[0]
    return hashlib.sha256(text.encode()).hexdigest()[:12]
