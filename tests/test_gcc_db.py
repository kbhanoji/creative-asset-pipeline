import importlib
import os
import sys
from pathlib import Path

from cap import config as cap_config
from cap import gcc_db, ids, scoring

ROOT = Path(__file__).resolve().parents[1]
SBD = ROOT / "config" / "customers" / "sbd-dewalt" / "customer.yaml"


def test_prompt_used_for_matching_is_the_typed_one():
    assert gcc_db.prompt_text_for_matching({"original_prompt": "typed", "prompt": "enhanced+brand"}) == "typed"
    assert gcc_db.prompt_text_for_matching({"original_prompt": None, "prompt": "p"}) == "p"


def test_brand_context_and_critic_instructions():
    g = {"id": 7, "name": "DEWALT 2026", "color_palette": ["#FEBD17", "#000000"],
         "visual_style_summary": "Rugged, high contrast", "tone_of_voice_summary": "Confident",
         "guideline_text": "Logo never distorted."}
    src, text = gcc_db.brand_context(g)
    assert src == "gcc_brand_guideline:7" and "#FEBD17" in text and "Logo never distorted." in text
    assert gcc_db.brand_context(None) is None
    cfg = cap_config.load(SBD)
    instr = scoring._critic_instructions(cfg, {"prompt_text": "x"}, (src, text))
    assert "Official brand guideline (extracted from the brand PDF" in instr and "Rugged, high contrast" in instr
    assert "product-safety" in instr or "PPE" in instr            # skills still included for safety rules


def test_critique_text():
    t = gcc_db.critique_text({"composite": 95.0, "status": "APPROVED", "hallucination_band": "LOW"},
                             {"prompt_adherence": 1.0, "brand_compliance": 0.9, "artifact_freedom": 0.8,
                              "safety_and_constraints": 1.0, "hallucination_risk": 15},
                             "Minor garbled clip text.", {"generation_id": "GEN-X", "version": 1,
                                                          "prompt_id": "PR-X", "batch_run_id": "BR-1"})
    assert t.startswith("Pipeline score 95/100 · APPROVED") and "brand compliance 0.90" in t
    assert "hallucination risk" in t and "GEN-X v1 · prompt PR-X · batch BR-1" in t


def test_router_matches_gcc_prompt_hash(monkeypatch):
    os.environ["CAP_CONFIG"] = str(SBD)
    sys.path.insert(0, str(ROOT / "services" / "lineage_router"))
    router = importlib.import_module("main")
    typed = "DEWALT DCD800B hero shot, grey background."
    stored = {"prompt_id": "PR-1", "prompt_sha256": ids.prompt_sha256(typed)}

    class FakeBQ:
        def get_prompt_by_hash(self, h):
            return stored if h == stored["prompt_sha256"] else None

        def get_prompt(self, pid):
            return None

    monkeypatch.setattr(router.PIPE, "bq", FakeBQ())
    media = {"original_prompt": "  DEWALT DCD800B hero shot,\n grey background. ", "prompt": "BRAND RULES... " + typed}
    assert router.match_prompt("gemini_images/uuid", {}, media) == (stored, "GCC_PROMPT_HASH")
    assert router.match_prompt("gemini_images/uuid", {}, {"original_prompt": "something else"})[1] == "UNMATCHED"
