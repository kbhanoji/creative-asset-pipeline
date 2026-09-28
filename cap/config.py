"""Customer configuration: load, interpolate ${ENV} values, validate.

The YAML in config/customers/<id>/customer.yaml is the single source of truth
for every customer-specific value. Terraform variables, agent settings and
pipeline behaviour are all derived from the model below.
"""
from __future__ import annotations

import csv
import os
import re
from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")

CRITIC_DIMENSIONS = ["prompt_adherence", "brand_compliance", "artifact_freedom", "safety_and_constraints"]

BUCKET_PURPOSES = [
    "landing", "config", "prompts", "intermediate", "approved", "rejected",
    "quarantine", "audit", "backgrounds", "assetgroups", "creative-studio",
]


class Customer(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,11}$")
    name: str
    brand: str
    brand_code: str = Field(pattern=r"^[A-Z0-9]{3}$")
    region_market: str = "NA"
    language: str = "en-US"


class Gcp(BaseModel):
    admin_account: str
    create_project: bool = True
    project_id: str = Field(pattern=r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
    project_name: str = ""
    billing_account: str = ""
    org_id: str = ""
    folder_id: str = ""
    region: str = "us-central1"
    zone: str = "us-central1-a"
    bigquery_location: str = "US"
    labels: dict[str, str] = {}

    @model_validator(mode="after")
    def _check(self) -> "Gcp":
        if self.org_id and self.folder_id:
            raise ValueError("gcp: set only one of org_id / folder_id")
        return self


class AccessUser(BaseModel):
    member: str = Field(pattern=r"^(user|group|serviceAccount|domain):.+")
    personas: list[str]


class Access(BaseModel):
    users: list[AccessUser] = []
    personas: dict[str, list[str]] = {}

    @model_validator(mode="after")
    def _personas_exist(self) -> "Access":
        for u in self.users:
            missing = [p for p in u.personas if p not in self.personas]
            if missing:
                raise ValueError(f"access: {u.member} uses undefined persona(s) {missing}")
        return self


class Storage(BaseModel):
    bucket_prefix: str = ""
    landing_retention_days: int = 180
    quarantine_retention_days: int = 90
    lock_audit_retention: bool = False
    audit_retention_days: int = 2555
    bucket_names: dict[str, str] = {}
    container_images_keep: int = Field(5, ge=0)  # Artifact Registry: keep N most recent images; 0 = keep all
    path_pattern: str = "{brand}/{category}/{subcategory}/{sku}/{batch_run_id}/{entity_id}/v{version}/{file}"
    file_pattern: str = "{sku}_{shot_type}_{aspect}_{resolution}_v{version}.{ext}"

    @field_validator("bucket_names")
    @classmethod
    def _known(cls, v: dict[str, str]) -> dict[str, str]:
        unknown = set(v) - set(BUCKET_PURPOSES)
        if unknown:
            raise ValueError(f"storage.bucket_names: unknown purpose(s) {sorted(unknown)}")
        return v


class BigQuery(BaseModel):
    dataset: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")


class Models(BaseModel):
    prompt_agent: str
    image_generation: str
    critic: str
    model_location: str = "global"


class GenerationMode(str, Enum):
    manual = "manual"
    automatic = "automatic"


class Generation(BaseModel):
    mode: GenerationMode = GenerationMode.manual
    variants_per_prompt: int = Field(4, ge=1, le=8)
    base_images_per_sku: int = Field(6, ge=1)
    max_assets_total: int = Field(750, ge=1)
    default_resolution: Literal["1K", "2K", "4K"] = "2K"
    default_aspect_ratio: str = "1:1"
    allowed_aspect_ratios: list[str] = ["1:1", "16:9", "9:16", "4:5", "4:3", "3:4"]
    output_mime_type: str = "image/png"


class Thresholds(BaseModel):
    auto_approve_min: float = Field(85, ge=0, le=100)
    revision_min: float = Field(65, ge=0, le=100)
    below_revision_action: Literal["FAIL", "FLAG_FOR_HITL"] = "FAIL"
    hallucination_high: float = Field(60, ge=0, le=100)
    hallucination_medium: float = Field(30, ge=0, le=100)
    high_hallucination_blocks_auto_approve: bool = True

    @model_validator(mode="after")
    def _order(self) -> "Thresholds":
        if not self.revision_min <= self.auto_approve_min:
            raise ValueError("scoring.thresholds: revision_min must be <= auto_approve_min")
        if not self.hallucination_medium <= self.hallucination_high:
            raise ValueError("scoring.thresholds: hallucination_medium must be <= hallucination_high")
        return self


class Hitl(BaseModel):
    edit_notice_at: int = 10


class Scoring(BaseModel):
    provider: Literal["gemini_critic", "gcc_endpoint"] = "gemini_critic"
    gcc_endpoint_url: str = ""
    weights: dict[str, float]
    thresholds: Thresholds = Thresholds()
    hitl: Hitl = Hitl()

    @model_validator(mode="after")
    def _check(self) -> "Scoring":
        if set(self.weights) != set(CRITIC_DIMENSIONS):
            raise ValueError(f"scoring.weights keys must be exactly {CRITIC_DIMENSIONS}")
        if abs(sum(self.weights.values()) - 1.0) > 1e-6:
            raise ValueError(f"scoring.weights must sum to 1.0 (got {sum(self.weights.values()):.3f})")
        if self.provider == "gcc_endpoint" and not self.gcc_endpoint_url:
            raise ValueError("scoring.gcc_endpoint_url is required when provider=gcc_endpoint")
        return self


class Sku(BaseModel):
    sku: str
    parent_asset_id: str
    product_name: str
    category: str
    subcategory: str
    key_features: list[str] = []
    reference_images: list[str] = []


class Catalog(BaseModel):
    skus_file: str = "skus.csv"
    shot_types: list[str]
    asset_types: list[str]


class Brand(BaseModel):
    skills_dir: str = "skills"
    guidelines_files: list[str] = []
    negative_constraints: list[str] = []
    safety_constraints: list[str] = []


class AutoSchedule(BaseModel):
    """Development only: stop (and optionally start) GCC's Cloud SQL database on a schedule."""
    enabled: bool = False
    stop_time: str = "17:00"                    # HH:MM, 24h, in `timezone`
    stop_days: list[str] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    start_time: str = ""                        # blank = start manually with `cap gcc wake`
    start_days: list[str] = ["mon", "tue", "wed", "thu", "fri"]
    timezone: str = ""                          # blank = timezone of gcp.region (where GCC is hosted)
    idle_stop_minutes: int = Field(30, ge=0)    # stop after N minutes without backend requests; 0 = off
    idle_check_every_minutes: int = Field(15, ge=5, le=60)

    @model_validator(mode="after")
    def _check(self) -> "AutoSchedule":
        from .schedule import cron
        cron(self.stop_time, self.stop_days)
        if self.start_time:
            cron(self.start_time, self.start_days)
        return self


class CreativeStudio(BaseModel):
    # cloud_run = Google's own GCC deployment (Cloud Run backend + Firebase Hosting frontend + Cloud SQL),
    #             installed with the repo's bootstrap.sh into the same project. Pipeline Terraform creates no VM.
    # vm        = GCC in Docker on a Compute Engine VM created by the pipeline Terraform (SOW 8.1 wording).
    # none      = no Creative Studio (use generation.mode = automatic).
    deployment: Literal["cloud_run", "vm", "none"] = "cloud_run"
    gcc_environment: str = "development"        # GCC tfvars `environment` (names the media bucket)
    gcc_env_folder: str = "dev-infra"           # folder infra/environments/<name> created by GCC's bootstrap.sh
    output_bucket: str = ""                     # GCC media bucket; default {project_id}-cs-{gcc_environment}-bucket
    backend_service: str = "cstudio-be"         # names GCC's bootstrap.sh gives the services
    frontend_service: str = "cstudio-fe"
    sql_instance_prefix: str = "creative-studio-db"
    auto_schedule: AutoSchedule = AutoSchedule()
    repo_url: str = "https://github.com/GoogleCloudPlatform/gcc-creative-studio"
    fork_url: str = ""
    branch_by_environment: dict[str, str] = {"dev": "develop", "uat": "test", "prod": "main"}
    repo_ref: str = ""                          # explicit branch/tag/commit; overrides branch_by_environment
    enabled: bool = True
    machine_type: str = "e2-standard-8"
    boot_disk_gb: int = 100
    image_family: str = "debian-12"
    image_project: str = "debian-cloud"
    public_ip: bool = False
    install_command: str = "docker compose up -d"
    ports: list[int] = [22, 8080]                # opened to the IAP range only
    prompt_metadata_key: str = "prompt"
    user_metadata_key: str = "user_email"
    ignore_suffixes: tuple[str, ...] = ("_thumbnail",)   # objects the router skips (GCC preview copies)
    # --- GCC database integration (router only; needs Cloud SQL reachability) ---
    db_integration: bool = True                 # link images via GCC media_items, real user/model, write-back
    db_instance: str = ""                       # project:region:instance; blank = discover by sql_instance_prefix
    db_name: str = "creative_studio"
    db_user: str = "studio_user"
    db_password_secret: str = "creative-studio-db-password"
    link_wait_seconds: int = 90                 # GCC fills media_items.gcs_uris just after the upload
    writeback_critique: bool = True             # write our score into media_items.critique (shown in GCC gallery)
    use_gcc_brand_guidelines: bool = True       # critic uses the guideline GCC extracted from the brand PDF
    folder_pattern: str = "{batch_run_id}/{sku}/"


class AgentRuntime(str, Enum):
    local = "local"                          # ADK dev UI / terminal on the developer's machine
    cloud_run = "cloud_run"                  # ADK web UI + API on Cloud Run (scales to zero), private
    agent_engine = "agent_engine"            # Vertex AI Agent Engine, no Gemini Enterprise
    gemini_enterprise = "gemini_enterprise"  # Agent Engine + Gemini Enterprise registration


class Agent(BaseModel):
    display_name: str = "Prompt Enrichment Agent"
    description: str = ""
    agent_engine_location: str = "us-central1"
    staging_bucket_purpose: str = "config"


class GeminiEnterprise(BaseModel):
    app_id: str = ""
    location: str = "global"


class Config(BaseModel):
    customer: Customer
    environment: Literal["dev", "uat", "prod"] = "dev"
    gcp: Gcp
    access: Access = Access()
    storage: Storage = Storage()
    bigquery: BigQuery
    models: Models
    generation: Generation = Generation()
    scoring: Scoring
    catalog: Catalog
    brand: Brand = Brand()
    creative_studio: CreativeStudio
    agent_runtime: AgentRuntime = AgentRuntime.local
    agent: Agent = Agent()
    gemini_enterprise: GeminiEnterprise = GeminiEnterprise()

    # populated by load(); not part of the YAML
    base_dir: Path = Path(".")
    skus: list[Sku] = []

    # ---- derived values -------------------------------------------------
    def bucket(self, purpose: str) -> str:
        if purpose not in BUCKET_PURPOSES:
            raise KeyError(f"unknown bucket purpose {purpose!r}")
        if purpose in self.storage.bucket_names:
            return self.storage.bucket_names[purpose]
        if purpose == "creative-studio" and self.creative_studio.deployment == "cloud_run":
            return (self.creative_studio.output_bucket
                    or f"{self.gcp.project_id}-cs-{self.creative_studio.gcc_environment}-bucket")
        prefix = self.storage.bucket_prefix or f"{self.customer.id}-cs-{self.environment}"
        return f"{prefix}-{purpose}"

    def gcc_idle_stop_enabled(self) -> bool:
        s = self.creative_studio.auto_schedule
        return self.creative_studio.deployment == "cloud_run" and s.enabled and s.idle_stop_minutes > 0

    def schedule_timezone(self) -> str:
        from .schedule import region_timezone
        return self.creative_studio.auto_schedule.timezone or region_timezone(self.gcp.region)

    def gcc_ref(self) -> str:
        """GCC branch/tag/commit for this environment: repo_ref if set, else branch_by_environment[environment]."""
        cs = self.creative_studio
        return cs.repo_ref or cs.branch_by_environment.get(self.environment, "main")

    def pipeline_buckets(self) -> dict[str, str]:
        """Buckets the pipeline Terraform creates (GCC's own bucket is created by GCC's bootstrap)."""
        return {p: b for p, b in self.all_buckets().items()
                if not (p == "creative-studio" and self.creative_studio.deployment != "vm")}

    def all_buckets(self) -> dict[str, str]:
        return {p: self.bucket(p) for p in BUCKET_PURPOSES}

    def sku(self, sku: str) -> Sku:
        for s in self.skus:
            if s.sku.lower() == sku.lower():
                return s
        raise KeyError(f"SKU {sku!r} is not in {self.catalog.skus_file}")

    def skills(self) -> dict[str, str]:
        d = self.base_dir / self.brand.skills_dir
        return {p.stem: p.read_text() for p in sorted(d.glob("*.md"))} if d.is_dir() else {}

    def table(self, name: str) -> str:
        return f"{self.gcp.project_id}.{self.bigquery.dataset}.{name}"

    def infra_problems(self) -> list[str]:
        """Checks only needed when provisioning (runtime services don't need the billing account)."""
        problems = []
        if self.gcp.create_project and not re.fullmatch(r"[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}", self.gcp.billing_account):
            problems.append("gcp.billing_account must look like XXXXXX-XXXXXX-XXXXXX when create_project=true "
                            "(set CAP_BILLING_ACCOUNT or put it in the YAML)")
        if not self.skus:
            problems.append(f"no SKUs in {self.catalog.skus_file}")
        if not self.access.users:
            problems.append("access.users is empty: nobody will be granted access")
        if self.agent_runtime == AgentRuntime.gemini_enterprise and not self.gemini_enterprise.app_id:
            problems.append("agent_runtime=gemini_enterprise but gemini_enterprise.app_id is empty: create the app in "
                            "the Gemini Enterprise console, or use agent_runtime local/agent_engine for development")
        return problems


def _interpolate(obj):
    if isinstance(obj, str):
        def repl(m: re.Match) -> str:
            return os.environ.get(m.group(1), "")
        return _ENV_RE.sub(repl, obj)
    if isinstance(obj, dict):
        return {k: _interpolate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate(v) for v in obj]
    return obj


def _split(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split("|") if x.strip()]


def load_skus(path: Path) -> list[Sku]:
    if not path.exists():
        raise FileNotFoundError(f"SKU catalog not found: {path}")
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    skus = [
        Sku(
            sku=r["sku"].strip(),
            parent_asset_id=r["parent_asset_id"].strip(),
            product_name=r["product_name"].strip(),
            category=r["category"].strip(),
            subcategory=r["subcategory"].strip(),
            key_features=_split(r.get("key_features")),
            reference_images=_split(r.get("reference_images")),
        )
        for r in rows if (r.get("sku") or "").strip()
    ]
    dupes = {s.sku for s in skus if [x.sku for x in skus].count(s.sku) > 1}
    if dupes:
        raise ValueError(f"duplicate SKUs in {path}: {sorted(dupes)}")
    return skus


def load(path: str | os.PathLike | None = None) -> Config:
    """Load a customer config. Falls back to $CAP_CONFIG."""
    path = Path(path or os.environ.get("CAP_CONFIG", ""))
    if not path.is_file():
        raise FileNotFoundError(f"config file not found: {path!s} (pass --config or set CAP_CONFIG)")
    raw = _interpolate(yaml.safe_load(path.read_text()))
    cfg = Config.model_validate(raw)
    cfg.base_dir = path.parent.resolve()
    cfg.skus = load_skus(cfg.base_dir / cfg.catalog.skus_file)
    return cfg
