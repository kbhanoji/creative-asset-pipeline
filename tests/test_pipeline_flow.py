"""End-to-end pipeline flow with in-memory BigQuery/GCS and a stubbed critic/image model."""
from pathlib import Path

import pytest

from cap import config as cap_config
from cap import generation, scoring
from cap.pipeline import Pipeline, PipelineError
from cap.routing import Status

SBD = Path(__file__).resolve().parents[1] / "config" / "customers" / "sbd-dewalt" / "customer.yaml"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + b"\x00" * 13 + b"\x00\x00\x00\x00"


class FakeLineage:
    def __init__(self):
        self.t = {}

    def insert(self, table, *rows):
        self.t.setdefault(table, []).extend(dict(r) for r in rows)

    def rows(self, table):
        return self.t.get(table, [])

    def query(self, sql, **params):
        return []

    def next_batch_seq(self, prefix):
        return 1 + len({r["batch_run_id"] for r in self.rows("batch_run")})

    def get_prompt(self, pid):
        return next((r for r in self.rows("enriched_prompt") if r["prompt_id"] == pid), None)

    def get_prompt_by_hash(self, h):
        return next((r for r in reversed(self.rows("enriched_prompt")) if r["prompt_sha256"] == h), None)

    def latest_prompt_version(self, lid):
        return max([r["prompt_version"] for r in self.rows("enriched_prompt") if r["prompt_lineage_id"] == lid] or [0])

    def find_prompt_lineage(self, sku, a, s, v, ar):
        for r in reversed(self.rows("enriched_prompt")):
            if (r["sku"], r["asset_type"], r["shot_type"], r["variant_index"], r["aspect_ratio"]) == (sku, a, s, v, ar):
                return r["prompt_lineage_id"]
        return None

    def get_generation(self, gid, version=None):
        g = [r for r in self.rows("generation") if r["generation_id"] == gid and (version is None or r["version"] == version)]
        return max(g, key=lambda r: r["version"]) if g else None

    def generation_by_source(self, uri):
        return next((r for r in self.rows("generation") if uri in (r.get("source_uri"), r.get("gcs_uri"))), None)

    def count_generations(self):
        return len(self.rows("generation"))

    def current_status(self, gid, v):
        d = [r for r in self.rows("disposition") if r["entity_id"] == gid and r["version"] == v]
        return d[-1]["status"] if d else None


class FakeStorage:
    def __init__(self, cfg):
        from cap.storage import Storage
        self.real = Storage(cfg)
        self.cfg = cfg
        self.objects = {}

    def __getattr__(self, name):  # object_path, file_name, split_uri
        return getattr(self.real, name)

    def upload_bytes(self, purpose, path, data, content_type, metadata=None):
        uri = f"gs://{self.cfg.bucket(purpose)}/{path}"
        self.objects[uri] = (data, content_type, dict(metadata or {}))
        return uri

    def upload_json(self, purpose, path, obj, metadata=None):
        return self.upload_bytes(purpose, path, b"{}", "application/json", metadata)

    def download(self, uri):
        return self.objects[uri]

    def set_metadata(self, uri, md):
        self.objects[uri][2].update(md)


def critic(score):
    return scoring.CriticResult({"prompt_adherence": score, "brand_compliance": score, "artifact_freedom": score,
                                 "safety_and_constraints": score}, f"all {score}", "stub", "stub-model", {})


@pytest.fixture
def env(monkeypatch):
    cfg = cap_config.load(SBD)
    bq = FakeLineage()
    pipe = Pipeline(cfg, bq, FakeStorage(cfg))
    scores = []
    monkeypatch.setattr(scoring, "score_image", lambda *a, **k: critic(scores.pop(0)))
    monkeypatch.setattr(generation, "generate_images",
                        lambda cfg, p, n, refs=None: [generation.GeneratedImage(PNG, "image/png", "stub-img")] * n)
    return cfg, bq, pipe, scores


def test_manual_flow_revise_then_approve(env):
    cfg, bq, pipe, scores = env
    br = pipe.start_batch(["DCD800B"], "designer@x.com")
    p = pipe.save_prompts([{"sku": "DCD800B", "prompt_text": "DEWALT DCD800B hero shot", "shot_type": "hero"}],
                          "designer@x.com", br)[0]
    assert p["prompt_version"] == 1 and "paste exactly" in p["copy_text"]

    # user pastes into Creative Studio; router matches by prompt hash
    found = bq.get_prompt_by_hash(p["prompt_sha256"])
    scores.append(0.75)
    g = pipe.register_image(data=PNG, mime_type="image/png", prompt_id=found["prompt_id"], user="designer@x.com",
                            tool="CREATIVE_STUDIO", match_method="PROMPT_HASH", source_uri="gs://cs/img1.png")
    assert g["version"] == 1 and g["decision"]["status"] == Status.NEEDS_REVISION

    # human edits the prompt; manual mode returns a copy block
    r = pipe.revise(g["generation_id"], "DEWALT DCD800B hero shot, fix chuck shape", "reviewer@x.com", "chuck distorted")
    assert r["prompt"]["prompt_version"] == 2 and r["prompt"]["parent_prompt_id"] == p["prompt_id"]
    assert r["prompt"]["prompt_lineage_id"] == p["prompt_lineage_id"]

    # regenerated image in Creative Studio links to the same generation as v2
    scores.append(0.9)
    g2 = pipe.register_image(data=PNG, mime_type="image/png", prompt_id=r["prompt"]["prompt_id"], user="reviewer@x.com",
                             tool="CREATIVE_STUDIO", match_method="PROMPT_HASH", source_uri="gs://cs/img2.png")
    assert g2["generation_id"] == g["generation_id"] and g2["version"] == 2
    assert g2["decision"]["status"] == Status.APPROVED
    approved = [u for u in pipe.gcs.objects if "-approved/" in u]
    assert approved and b"gcc_scoring_payload" in pipe.gcs.objects[approved[0]][0]
    assert [e["action"] for e in bq.rows("edit_event")] == ["REGISTER", "REVISE_PROMPT", "REGENERATE"]
    assert {s["score_type"] for s in bq.rows("score")} == {"initial", "rescore"}
    assert any("-audit/" in u for u in pipe.gcs.objects)


def test_automatic_generation_and_fail(env, monkeypatch):
    cfg, bq, pipe, scores = env
    monkeypatch.setattr(cfg.generation, "mode", cap_config.GenerationMode.automatic)
    p = pipe.save_prompts([{"sku": "DCD800B", "prompt_text": "p", "shot_type": "hero"}], "u@x.com")[0]
    scores.extend([0.95, 0.70, 0.40, 0.88])
    gens = pipe.generate_for_prompt(p["prompt_id"], "u@x.com")
    assert [g["decision"]["status"] for g in gens] == [Status.APPROVED, Status.NEEDS_REVISION, Status.FAILED_QC,
                                                        Status.APPROVED]
    assert len({g["generation_id"] for g in gens}) == 4
    assert any("-rejected/" in u for u in pipe.gcs.objects)
    with pytest.raises(PipelineError):
        pipe.decide(gens[2]["generation_id"], "APPROVE", "u@x.com")   # FAILED_QC is final
    with pytest.raises(PipelineError):
        pipe.revise(gens[0]["generation_id"], "x", "u@x.com")          # APPROVED can't be revised

    # automatic revise regenerates immediately as v2
    scores.append(0.86)
    r = pipe.revise(gens[1]["generation_id"], "better prompt", "u@x.com")
    assert r["generations"][0]["version"] == 2 and r["generations"][0]["decision"]["status"] == Status.APPROVED


def test_prompt_versions_accumulate_per_sku(env):
    cfg, bq, pipe, scores = env
    a = pipe.save_prompts([{"sku": "DCD800B", "prompt_text": "v1", "shot_type": "hero"}], "u")[0]
    b = pipe.save_prompts([{"sku": "DCD800B", "prompt_text": "v2", "shot_type": "hero"}], "u")[0]
    c = pipe.save_prompts([{"sku": "DCD800B", "prompt_text": "x", "shot_type": "lifestyle"}], "u")[0]
    assert a["prompt_lineage_id"] == b["prompt_lineage_id"] and b["prompt_version"] == 2
    assert c["prompt_lineage_id"] != a["prompt_lineage_id"] and c["prompt_version"] == 1


def test_unmatched_and_ceiling(env, monkeypatch):
    cfg, bq, pipe, scores = env
    u = pipe.register_image(data=PNG, mime_type="image/png", prompt_id=None, user="cs", tool="CREATIVE_STUDIO",
                            match_method="UNMATCHED", source_uri="gs://cs/orphan.png")
    assert u["status"] == Status.UNMATCHED
    with pytest.raises(PipelineError):
        pipe.decide(u["generation_id"], "APPROVE", "u")
    monkeypatch.setattr(cfg.generation, "max_assets_total", 1)
    p = pipe.save_prompts([{"sku": "DCD800B", "prompt_text": "p", "shot_type": "hero"}], "u")[0]
    with pytest.raises(PipelineError, match="ceiling"):
        pipe.generate_for_prompt(p["prompt_id"], "u", n=1)
