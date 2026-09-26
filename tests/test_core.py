import struct
import zlib
from pathlib import Path

import pytest

from cap import config as cap_config
from cap import ids
from cap.pipeline import embed_png_text
from cap.routing import Status, decide
from cap.storage import Storage

ROOT = Path(__file__).resolve().parents[1]
SBD = ROOT / "config" / "customers" / "sbd-dewalt" / "customer.yaml"


@pytest.fixture(scope="module")
def cfg():
    return cap_config.load(SBD)


def subs(pa, bc, af, sc):
    return {"prompt_adherence": pa, "brand_compliance": bc, "artifact_freedom": af, "safety_and_constraints": sc}


# ---- config ---------------------------------------------------------------
def test_sbd_config_loads(cfg):
    assert cfg.customer.brand == "DEWALT"
    assert cfg.bucket("approved") == "sbd-cs-dev-kbhanoji-approved"
    assert cfg.sku("dcd800b").parent_asset_id == "SAL-4411872"
    assert "brand" in cfg.skills()
    assert cfg.generation.mode.value == "manual"


def test_example_config_is_valid(tmp_path):
    (tmp_path / "customer.yaml").write_text((ROOT / "config" / "customer.example.yaml").read_text())
    (tmp_path / "skus.csv").write_text("sku,parent_asset_id,product_name,category,subcategory,key_features,reference_images\n"
                                       "X1,P1,Thing,cat,sub,a|b,\n")
    c = cap_config.load(tmp_path / "customer.yaml")
    assert c.sku("X1").key_features == ["a", "b"]


def test_weights_must_sum_to_one(cfg):
    raw = cfg.scoring.model_dump()
    raw["weights"]["prompt_adherence"] = 0.9
    with pytest.raises(ValueError):
        cap_config.Scoring.model_validate(raw)


# ---- routing (SOW 3.4.5 tiers, user decision: < 65 fails) -----------------
def test_auto_approve_at_85(cfg):
    d = decide(subs(0.85, 0.85, 0.85, 0.85), cfg.scoring)
    assert d.composite == 85 and d.status == Status.APPROVED


def test_needs_revision_between_65_and_85(cfg):
    assert decide(subs(0.84, 0.84, 0.84, 0.84), cfg.scoring).status == Status.NEEDS_REVISION
    assert decide(subs(0.65, 0.65, 0.65, 0.65), cfg.scoring).status == Status.NEEDS_REVISION


def test_below_65_fails_by_default(cfg):
    d = decide(subs(0.6, 0.6, 0.6, 0.6), cfg.scoring)
    assert d.status == Status.FAILED_QC and d.action == "FAILED_QC"


def test_below_65_can_flag_instead(cfg):
    s = cfg.scoring.model_copy(deep=True)
    s.thresholds.below_revision_action = "FLAG_FOR_HITL"
    assert decide(subs(0.6, 0.6, 0.6, 0.6), s).status == Status.FLAGGED


def test_high_hallucination_blocks_auto_approve(cfg):
    sc = {**subs(1, 1, 1, 1), "hallucination_risk": 75}
    d = decide(sc, cfg.scoring)
    assert d.composite == 100 and d.hallucination_band == "HIGH" and d.status == Status.FLAGGED


def test_hallucination_derived_from_artifact_freedom(cfg):
    d = decide(subs(1, 1, 0.5, 1), cfg.scoring)
    assert d.hallucination_risk == 50 and d.hallucination_band == "MEDIUM"


def test_thresholds_are_configurable(cfg):
    s = cfg.scoring.model_copy(deep=True)
    s.thresholds.auto_approve_min = 90
    assert decide(subs(0.88, 0.88, 0.88, 0.88), s).status == Status.NEEDS_REVISION


# ---- ids / storage / embedding -------------------------------------------
def test_ids():
    a, b = ids.generation_id(), ids.generation_id()
    assert a != b and a.startswith("GEN-") and len(a) == 30
    assert ids.batch_run_id("DWT", 1).endswith("-DWT-001")
    assert ids.prompt_sha256("A  red\nDrill ") == ids.prompt_sha256("a red drill")
    p = ids.prompt_id()
    found = ids.find_ids(f"BR-20261005-DWT-001/DCD800B/{p}/img.png")
    assert found["PR"] == p and found["BR"] == "BR-20261005-DWT-001"


def test_object_path(cfg):
    s = Storage(cfg)
    path = s.object_path(sku="DCD800B", batch_run_id="BR-20261005-DWT-001", entity_id="GEN-X", version=2,
                         file=s.file_name(sku="DCD800B", shot_type="hero", aspect="16:9", resolution="4K", version=2, ext="png"))
    assert path == "dewalt/power-tools/drills/DCD800B/BR-20261005-DWT-001/GEN-X/v2/DCD800B_hero_16x9_4K_v2.png"


def _png():
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def test_png_payload_embedding():
    out = embed_png_text(_png(), "gcc_scoring_payload", '{"composite_score": 0.9}')
    assert b"iTXt" in out and b"gcc_scoring_payload" in out and out.endswith(_png()[-12:])
    assert embed_png_text(b"not a png", "k", "v") == b"not a png"


def test_agent_runtime_defaults(cfg, tmp_path):
    assert cfg.agent_runtime.value == "cloud_run"                 # SBD dev sandbox: personal account
    assert not any("gemini_enterprise.app_id" in p for p in cfg.infra_problems())
    (tmp_path / "customer.yaml").write_text((ROOT / "config" / "customer.example.yaml").read_text())
    (tmp_path / "skus.csv").write_text("sku,parent_asset_id,product_name,category,subcategory,key_features,reference_images\n"
                                       "X1,P1,Thing,cat,sub,,\n")
    c = cap_config.load(tmp_path / "customer.yaml")
    assert c.agent_runtime.value == "gemini_enterprise"             # customer template
    assert any("gemini_enterprise.app_id" in p for p in c.infra_problems())


def test_gcc_cloud_run_bucket_not_created_by_pipeline(cfg):
    assert cfg.creative_studio.deployment == "cloud_run"
    assert cfg.bucket("creative-studio") == "sbd-cs-dev-kbhanoji-cs-development-bucket"   # GCC's own bucket
    assert "creative-studio" not in cfg.pipeline_buckets()
    assert len(cfg.pipeline_buckets()) == 10


def test_gcc_branch_per_environment(cfg):
    assert cfg.environment == "dev" and cfg.gcc_ref() == "developlocal"
    for env, branch in [("uat", "test"), ("prod", "main")]:
        assert cfg.model_copy(update={"environment": env}).gcc_ref() == branch
    pinned = cfg.model_copy(deep=True)
    pinned.creative_studio.repo_ref = "1f19478"
    assert pinned.gcc_ref() == "1f19478"


def test_dev_auto_schedule(cfg):
    from cap.schedule import cron
    sch = cfg.creative_studio.auto_schedule
    assert sch.enabled and sch.stop_time == "17:00" and sch.start_time == ""
    assert cfg.schedule_timezone() == "America/Chicago"                  # us-central1 (Iowa)
    assert cron("17:00", sch.stop_days) == "0 17 * * *"
    assert cron("08:30", ["mon", "tue", "wed", "thu", "fri"]) == "30 8 * * 1,2,3,4,5"
    with pytest.raises(ValueError):
        cron("5pm", ["mon"])
