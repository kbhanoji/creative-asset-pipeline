"""Project lifecycle outside Terraform: create, label, link billing, state bucket, teardown.

The installer owns the project (so it can delete it cleanly); Terraform owns what
is inside it. A project created here carries the label
created-by=creative-asset-pipeline, which teardown requires before deleting it.
"""
from __future__ import annotations

import json
import subprocess

from .config import Config

CREATED_BY_KEY, CREATED_BY_VALUE = "created-by", "creative-asset-pipeline"


def _gcloud(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gcloud", *args], check=check, text=True, capture_output=True)


def state_bucket(cfg: Config) -> str:
    return f"{cfg.gcp.project_id}-cap-tfstate"


def state_prefix(cfg: Config) -> str:
    return f"cap/{cfg.customer.id}-{cfg.environment}"


def describe_project(project_id: str) -> dict | None:
    r = _gcloud("projects", "describe", project_id, "--format=json", check=False)
    return json.loads(r.stdout) if r.returncode == 0 else None


def is_ours(project: dict | None) -> bool:
    return bool(project) and (project.get("labels") or {}).get(CREATED_BY_KEY) == CREATED_BY_VALUE


def ensure_project(cfg: Config, log=print) -> str:
    """Create (if configured and missing), label and link billing. Returns 'created' | 'exists'."""
    g = cfg.gcp
    proj = describe_project(g.project_id)
    if proj and proj.get("lifecycleState") == "DELETE_REQUESTED":
        raise RuntimeError(f"project {g.project_id} is pending deletion; `gcloud projects undelete {g.project_id}` "
                           f"or choose another project_id (IDs can't be reused for 30 days)")
    status = "exists"
    if not proj:
        if not g.create_project:
            raise RuntimeError(f"project {g.project_id} not found (or no access) and gcp.create_project is false")
        labels = {**{k: v for k, v in g.labels.items()}, "customer": cfg.customer.id, "environment": cfg.environment,
                  CREATED_BY_KEY: CREATED_BY_VALUE}
        args = ["projects", "create", g.project_id, f"--name={g.project_name or g.project_id}",
                "--labels=" + ",".join(f"{k}={v}" for k, v in labels.items())]
        if g.org_id:
            args.append(f"--organization={g.org_id}")
        elif g.folder_id:
            args.append(f"--folder={g.folder_id}")
        log(f"Creating project {g.project_id} ...")
        r = _gcloud(*args, check=False)
        if r.returncode != 0:
            raise RuntimeError(f"project creation failed: {r.stderr.strip()}")
        status = "created"
    if g.create_project and g.billing_account:
        billing = _gcloud("billing", "projects", "describe", g.project_id, "--format=value(billingAccountName)",
                          check=False).stdout.strip()
        if not billing.endswith(g.billing_account):
            log(f"Linking billing account {g.billing_account} ...")
            _gcloud("billing", "projects", "link", g.project_id, f"--billing-account={g.billing_account}")
    return status


def ensure_state_bucket(cfg: Config, log=print) -> str:
    b = state_bucket(cfg)
    if _gcloud("storage", "buckets", "describe", f"gs://{b}", f"--project={cfg.gcp.project_id}",
               "--format=value(name)", check=False).returncode != 0:
        log(f"Creating Terraform state bucket gs://{b} ...")
        _gcloud("services", "enable", "storage.googleapis.com", f"--project={cfg.gcp.project_id}")
        _gcloud("storage", "buckets", "create", f"gs://{b}", f"--project={cfg.gcp.project_id}",
                f"--location={cfg.gcp.region}", "--uniform-bucket-level-access", "--public-access-prevention")
        _gcloud("storage", "buckets", "update", f"gs://{b}", "--versioning")
    return b


def label_as_ours(cfg: Config) -> None:
    """Mark an existing project as created by this pipeline (only when we did create it).

    GA `gcloud projects update` has no label flags, so use the Resource Manager v3 API.
    """
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    s = AuthorizedSession(creds)
    url = f"https://cloudresourcemanager.googleapis.com/v3/projects/{cfg.gcp.project_id}"
    labels = {**(s.get(url, timeout=30).json().get("labels") or {}), CREATED_BY_KEY: CREATED_BY_VALUE}
    r = s.patch(url, params={"updateMask": "labels"}, json={"labels": labels}, timeout=30)
    r.raise_for_status()


def inventory(cfg: Config) -> dict[str, list[str]]:
    """What exists in the project that this pipeline (and GCC) created; used by teardown's dry run."""
    p, r = cfg.gcp.project_id, cfg.gcp.region

    def lines(*args: str) -> list[str]:
        out = _gcloud(*args, f"--project={p}", check=False)
        return [x for x in out.stdout.split("\n") if x.strip()] if out.returncode == 0 else []

    return {
        "Cloud Run services": lines("run", "services", "list", f"--region={r}", "--format=value(metadata.name)"),
        "Cloud SQL instances": lines("sql", "instances", "list", "--format=value(name)"),
        "Buckets": lines("storage", "buckets", "list", "--format=value(name)"),
        "BigQuery datasets": _bq_datasets(p),
        "Scheduler jobs": lines("scheduler", "jobs", "list", f"--location={r}", "--format=value(ID)"),
        "Eventarc triggers": lines("eventarc", "triggers", "list", f"--location={r}", "--format=value(name.basename())"),
        "Service accounts": lines("iam", "service-accounts", "list", "--format=value(email)"),
        "Artifact Registry repos": lines("artifacts", "repositories", "list", f"--location={r}", "--format=value(name.basename())"),
    }


def _bq_datasets(project_id: str) -> list[str]:
    r = subprocess.run(["bq", f"--project_id={project_id}", "ls", "--format=json"], text=True, capture_output=True)
    try:
        return [d["datasetReference"]["datasetId"] for d in json.loads(r.stdout or "[]")]
    except (ValueError, KeyError):
        return []


def teardown_plan(cfg: Config, project: dict | None, allow_prod: bool = False) -> tuple[str, str]:
    """Decide what teardown may do. Returns (mode, reason); mode is 'project' | 'resources' | 'refuse'.

    - 'project'   : the installer created it (create_project + our label) -> delete the whole project
    - 'resources' : an existing project (e.g. customer landing zone) -> delete only what we created
    - 'refuse'    : prod without allow_prod, project missing/pending deletion, or not ours
    """
    pid = cfg.gcp.project_id
    if cfg.environment == "prod" and not allow_prod:
        return "refuse", "environment is prod: pass --allow-prod to tear it down"
    if not project:
        return "refuse", f"project {pid} not found or no access"
    if project.get("lifecycleState") == "DELETE_REQUESTED":
        return "refuse", f"project {pid} is already pending deletion"
    if cfg.gcp.create_project:
        if not is_ours(project):
            return "refuse", (f"project {pid} lacks label {CREATED_BY_KEY}={CREATED_BY_VALUE}; it was not created by "
                              f"this installer, so it won't be deleted (set gcp.create_project=false to remove only "
                              f"the pipeline's resources)")
        return "project", f"project {pid} was created by the installer: delete the whole project"
    return "resources", f"project {pid} is an existing project: delete only the resources this pipeline created"


def confirmation_phrase(cfg: Config) -> str:
    return f"delete {cfg.gcp.project_id}"


def delete_project(project_id: str) -> None:
    _gcloud("projects", "delete", project_id, "--quiet")
