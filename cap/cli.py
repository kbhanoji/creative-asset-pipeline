"""`cap`: the one command-line entry point for every customer.

  cap init                         ask the setup questions, write config/customers/<id>/
  cap validate                     check the config (and GCP login) before touching anything
  cap infra plan|apply|destroy     Terraform: project, APIs, IAM, buckets, BigQuery, network, Creative Studio VM
  cap deploy agent|router|all      prompt agent per agent_runtime; lineage router -> Cloud Run + Eventarc
  cap agent web|open|chat          prompt agent locally, on Cloud Run (open), or Agent Engine (no Gemini Enterprise)
  cap batch start                  open a batch run
  cap prompt save|history          save/version prompts from the CLI (the agent does this in normal use)
  cap image generate|register|link automatic generation, manual upload, linking orphans
  cap hitl revise|approve|reject   human-in-the-loop actions
  cap status / cap report          lineage queries
  cap gcc install|status|sleep|wake  Creative Studio on Cloud Run: install (configured branch), state, pause, resume
  cap gcc schedule [--remove]      DEV: auto-stop Cloud SQL daily (default 17:00 region-local), optional auto-start
  cap gcc ssh|tunnel|logs          Creative Studio VM access through IAP (deployment=vm)

Every command takes --config (or $CAP_CONFIG).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table

from . import config as cap_config

ROOT = Path(__file__).resolve().parents[1]
TF_DIR = ROOT / "infra" / "terraform"
STATE_DIR = ROOT / ".state"

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)
infra = typer.Typer(no_args_is_help=True, help="Provision infrastructure with Terraform.")
deploy = typer.Typer(no_args_is_help=True, help="Deploy the agent and services.")
batch = typer.Typer(no_args_is_help=True, help="Batch runs.")
prompt = typer.Typer(no_args_is_help=True, help="Prompts (versioned per SKU).")
image = typer.Typer(no_args_is_help=True, help="Images.")
hitl = typer.Typer(no_args_is_help=True, help="Human-in-the-loop actions.")
gcc = typer.Typer(no_args_is_help=True, help="Creative Studio VM.")
agent = typer.Typer(no_args_is_help=True, help="Use the prompt agent without Gemini Enterprise (dev).")
for name, sub in [("infra", infra), ("deploy", deploy), ("agent", agent), ("batch", batch), ("prompt", prompt), ("image", image),
                  ("hitl", hitl), ("gcc", gcc)]:
    app.add_typer(sub, name=name)

con = Console()
ConfigOpt = typer.Option(None, "--config", "-c", envvar="CAP_CONFIG", help="Path to customer.yaml")


def _load(path: Optional[Path]) -> cap_config.Config:
    try:
        return cap_config.load(path)
    except Exception as e:  # noqa: BLE001 - show config errors plainly
        con.print(f"[red]Config error:[/red] {e}")
        raise typer.Exit(2)


def _run(cmd: list[str], cwd: Path | None = None, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    con.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=capture)


def _whoami() -> str:
    try:
        return subprocess.run(["gcloud", "config", "get-value", "account"], capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        return ""


def _user(user: Optional[str]) -> str:
    return user or _whoami() or os.environ.get("USER", "unknown")


def _pipe(cfg):
    from .pipeline import Pipeline
    return Pipeline(cfg)


def _print(obj) -> None:
    con.print_json(json.dumps(obj, default=str))


# =============================================================================
# init / validate
# =============================================================================
@app.command()
def init(customer: str = typer.Option(..., prompt="Customer id (short slug, e.g. sbd)"),
         force: bool = typer.Option(False, help="Overwrite an existing config")):
    """Ask the setup questions and write config/customers/<customer>/ (customer.yaml, skus.csv, skills/)."""
    dest = ROOT / "config" / "customers" / customer
    if (dest / "customer.yaml").exists() and not force:
        con.print(f"[yellow]{dest/'customer.yaml'} exists; use --force to overwrite.[/yellow]")
        raise typer.Exit(1)
    data = yaml.safe_load((ROOT / "config" / "customer.example.yaml").read_text())
    ask = typer.prompt
    env = ask("Environment (dev/uat/prod)", default="dev")
    data["environment"] = env
    c = data["customer"]
    c["id"] = customer
    c["name"] = ask("Customer display name", default=customer.upper())
    c["brand"] = ask("Brand name used in prompts", default=c["name"])
    c["brand_code"] = ask("3-letter brand code for Batch IDs", default=c["brand"][:3].upper())
    g = data["gcp"]
    g["admin_account"] = ask("Google account that will run the setup", default=_whoami() or "you@example.com")
    g["create_project"] = typer.confirm("Create a NEW GCP project? (No = use an existing project)", default=True)
    g["project_id"] = ask("GCP project ID", default=f"{customer}-cs-{env}-001")
    g["project_name"] = ask("GCP project display name", default=f"{c['name']} Creative Studio {env}")
    if g["create_project"]:
        g["billing_account"] = ask("Billing account ID (XXXXXX-XXXXXX-XXXXXX), or ${ENV_VAR}",
                                   default="${CAP_BILLING_ACCOUNT}")
        parent = ask("Parent: org:<id>, folder:<id>, or none", default="none")
        g["org_id"] = parent.split(":", 1)[1] if parent.startswith("org:") else ""
        g["folder_id"] = parent.split(":", 1)[1] if parent.startswith("folder:") else ""
    g["region"] = ask("Region", default="us-central1")
    g["zone"] = ask("Zone for the Creative Studio VM", default=f"{g['region']}-a")
    g["labels"] = {"app": "creative-asset-pipeline", "customer": customer}
    users = ask("Users to grant access (comma-separated emails; admin first)", default=g["admin_account"])
    emails = [u.strip() for u in users.split(",") if u.strip()]
    data["access"]["users"] = [{"member": f"user:{emails[0]}", "personas": ["platform_admin"]}] + \
                              [{"member": f"user:{e}", "personas": ["creative_user", "reviewer"]} for e in emails[1:]]
    data["storage"]["bucket_prefix"] = f"{customer}-cs-{env}"
    data["bigquery"]["dataset"] = f"{customer.replace('-', '_')}_cs_lineage"
    data["generation"]["mode"] = ask("Generation step mode (manual/automatic)", default="manual")
    t = data["scoring"]["thresholds"]
    t["auto_approve_min"] = float(ask("Auto-approve at composite score >=", default="85"))
    t["revision_min"] = float(ask("Needs human revision at score >=", default="65"))
    t["below_revision_action"] = ask("Below that: FAIL or FLAG_FOR_HITL", default="FAIL")
    data["creative_studio"]["deployment"] = ask("Creative Studio deployment: cloud_run (Google's bootstrap) / vm / none",
                                                default="cloud_run")
    data["agent_runtime"] = ask("Where does the prompt agent run? local / cloud_run (dev, scales to zero) / "
                                "agent_engine / gemini_enterprise (customer with Gemini Enterprise seats)",
                                default="cloud_run" if env == "dev" else "gemini_enterprise")
    if data["agent_runtime"] == "gemini_enterprise":
        data["gemini_enterprise"]["app_id"] = ask("Gemini Enterprise app ID (blank if not created yet)", default="")
    data["agent"]["display_name"] = f"{c['brand']} Prompt Enrichment Agent"

    dest.mkdir(parents=True, exist_ok=True)
    (dest / "customer.yaml").write_text(
        f"# Generated by `cap init` for {c['name']}. See config/customer.example.yaml for every option.\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    if not (dest / "skus.csv").exists():
        (dest / "skus.csv").write_text("sku,parent_asset_id,product_name,category,subcategory,key_features,reference_images\n")
    if not (dest / "skills").exists():
        shutil.copytree(ROOT / "config" / "templates" / "skills", dest / "skills")
    con.print(f"[green]Wrote {dest}[/green]. Next: add SKUs to skus.csv, replace skills/*.md with the customer's "
              f"brand and safety rules, then `cap validate -c {dest/'customer.yaml'}`.")


@app.command()
def validate(config: Optional[Path] = ConfigOpt):
    """Validate the config and print what will be created."""
    cfg = _load(config)
    t = Table(title=f"{cfg.customer.name} / {cfg.environment}", show_header=False)
    rows = [
        ("GCP project", f"{cfg.gcp.project_id} ({'create' if cfg.gcp.create_project else 'existing'})"),
        ("Admin account", f"{cfg.gcp.admin_account} (gcloud active: {_whoami() or 'not logged in'})"),
        ("Region / zone", f"{cfg.gcp.region} / {cfg.gcp.zone}"),
        ("BigQuery dataset", f"{cfg.bigquery.dataset} ({cfg.gcp.bigquery_location})"),
        ("Buckets", ", ".join(cfg.all_buckets().values())),
        ("SKUs", f"{len(cfg.skus)}: {', '.join(s.sku for s in cfg.skus[:10])}{' ...' if len(cfg.skus) > 10 else ''}"),
        ("Users", ", ".join(f"{u.member}{u.personas}" for u in cfg.access.users)),
        ("Generation mode", cfg.generation.mode.value),
        ("Thresholds", f"approve >= {cfg.scoring.thresholds.auto_approve_min:g}, revise >= "
                       f"{cfg.scoring.thresholds.revision_min:g}, below -> {cfg.scoring.thresholds.below_revision_action}"),
        ("Agent runtime", cfg.agent_runtime.value + (f" (app {cfg.gemini_enterprise.app_id or 'NOT SET'})"
                                                      if cfg.agent_runtime.value == "gemini_enterprise" else "")),
        ("Models", f"agent {cfg.models.prompt_agent}, image {cfg.models.image_generation}, critic {cfg.models.critic}"),
        ("Creative Studio", {"cloud_run": f"Cloud Run (GCC bootstrap), media bucket {cfg.bucket('creative-studio')}",
                             "vm": f"VM {cfg.creative_studio.machine_type}",
                             "none": "none"}[cfg.creative_studio.deployment]
         + (f", GCC ref '{cfg.gcc_ref()}' from {cfg.creative_studio.fork_url or cfg.creative_studio.repo_url}"
            if cfg.creative_studio.deployment != "none" else "")),
        ("Skills", ", ".join(cfg.skills()) or "none"),
    ]
    for k, v in rows:
        t.add_row(k, v)
    con.print(t)
    problems = cfg.infra_problems()
    if _whoami() and _whoami() != cfg.gcp.admin_account:
        problems.append(f"gcloud is logged in as {_whoami()}, config admin_account is {cfg.gcp.admin_account}")
    for p in problems:
        con.print(f"[yellow]! {p}[/yellow]")
    if not problems:
        con.print("[green]Config OK[/green]")


# =============================================================================
# infra (Terraform)
# =============================================================================
def _tfvars(cfg) -> dict:
    members = [{"member": u.member, "role": r} for u in cfg.access.users for p in u.personas for r in cfg.access.personas[p]]
    cs = cfg.creative_studio
    return {
        "customer_id": cfg.customer.id, "environment": cfg.environment,
        "project_id": cfg.gcp.project_id,
        "region": cfg.gcp.region, "zone": cfg.gcp.zone, "labels": cfg.gcp.labels,
        "bigquery_location": cfg.gcp.bigquery_location, "dataset": cfg.bigquery.dataset,
        "deletion_protection": cfg.environment == "prod",
        "buckets": cfg.pipeline_buckets(),
        "landing_retention_days": cfg.storage.landing_retention_days,
        "quarantine_retention_days": cfg.storage.quarantine_retention_days,
        "audit_retention_days": cfg.storage.audit_retention_days,
        "lock_audit_retention": cfg.storage.lock_audit_retention,
        "iam_members": members,
        "gcc_idle_stop": cfg.gcc_idle_stop_enabled(),
        "container_images_keep": cfg.storage.container_images_keep,
        "creative_studio": {
            "enabled": cs.deployment == "vm" and cs.enabled, "machine_type": cs.machine_type, "boot_disk_gb": cs.boot_disk_gb,
            "image_family": cs.image_family, "image_project": cs.image_project, "public_ip": cs.public_ip,
            "repo_url": cs.fork_url or cs.repo_url, "repo_ref": cfg.gcc_ref(), "install_command": cs.install_command, "ports": cs.ports,
        },
    }


def _tf(cfg, *args: str, bootstrap: bool = True) -> None:
    """Run Terraform against the GCS state bucket in the project.

    bootstrap=True (plan/apply): create the project if configured, link billing and create the
    state bucket first. The project itself is never managed by Terraform (see cap/gcp_project.py).
    """
    from . import gcp_project as gp
    if not shutil.which("terraform"):
        con.print("[red]terraform not found. Install it (macOS: brew install hashicorp/tap/terraform; "
                  "Cloud Shell: installer/install.sh installs it).[/red]")
        raise typer.Exit(1)
    if bootstrap:
        try:
            if gp.ensure_project(cfg, log=con.print) == "created":
                con.print(f"[green]Project {cfg.gcp.project_id} created.[/green]")
            gp.ensure_state_bucket(cfg, log=con.print)
        except Exception as e:  # noqa: BLE001
            con.print(f"[red]{e}[/red]")
            raise typer.Exit(2)
    STATE_DIR.mkdir(exist_ok=True)
    key = f"{cfg.customer.id}-{cfg.environment}"
    var_file = STATE_DIR / f"{key}.tfvars.json"
    var_file.write_text(json.dumps(_tfvars(cfg), indent=2))
    backend = [f"-backend-config=bucket={gp.state_bucket(cfg)}", f"-backend-config=prefix={gp.state_prefix(cfg)}"]
    local_state = STATE_DIR / f"{key}.tfstate"
    if local_state.exists():
        _migrate_local_state(cfg, local_state, backend)
    _run(["terraform", "init", "-input=false", "-reconfigure", *backend], cwd=TF_DIR)
    _run(["terraform", *args, f"-var-file={var_file}"], cwd=TF_DIR)


def _migrate_local_state(cfg, local_state: Path, backend: list[str]) -> None:
    """One-time move of pre-GCS local state into the bucket.

    init against GCS -> `state push` the local file -> drop the project resource (the installer
    owns the project now) and label the project as created by this pipeline.
    """
    from . import gcp_project as gp
    con.print(f"[yellow]Migrating local Terraform state {local_state.name} to gs://{gp.state_bucket(cfg)} ...[/yellow]")
    _run(["terraform", "init", "-input=false", "-reconfigure", *backend], cwd=TF_DIR)
    remote = _run(["terraform", "state", "list"], cwd=TF_DIR, capture=True, check=False).stdout.split()
    if remote:  # already pushed (e.g. an earlier, interrupted migration): finish it, never overwrite
        if not any(a.startswith("google_project.this") for a in remote):
            gp.label_as_ours(cfg)
            local_state.rename(local_state.with_suffix(".tfstate.migrated"))
            con.print("[green]Remote state already present; local state retired.[/green]")
        else:
            con.print("[yellow]Remote state already has resources; not overwriting. Local file kept.[/yellow]")
        return
    _run(["terraform", "state", "push", str(local_state)], cwd=TF_DIR)
    for addr in [a for a in _run(["terraform", "state", "list"], cwd=TF_DIR, capture=True).stdout.split()
                 if a.startswith("google_project.this")]:
        _run(["terraform", "state", "rm", addr], cwd=TF_DIR)
        gp.label_as_ours(cfg)  # the old Terraform created this project
    local_state.rename(local_state.with_suffix(".tfstate.migrated"))
    con.print("[green]State migrated to GCS.[/green]")


@infra.command("render")
def infra_render(config: Optional[Path] = ConfigOpt):
    """Print the Terraform variables derived from the config."""
    _print(_tfvars(_load(config)))


@infra.command("plan")
def infra_plan(config: Optional[Path] = ConfigOpt):
    """Show what Terraform will create/change."""
    cfg = _load(config)
    for p in cfg.infra_problems():
        con.print(f"[yellow]! {p}[/yellow]")
    _tf(cfg, "plan", "-input=false")


@infra.command("apply")
def infra_apply(config: Optional[Path] = ConfigOpt, yes: bool = typer.Option(False, "--yes", help="Skip Terraform's approval prompt")):
    """Create/update the project, APIs, IAM, buckets, BigQuery, network and Creative Studio VM."""
    cfg = _load(config)
    blocking = [p for p in cfg.infra_problems() if "billing_account" in p or "no SKUs" in p]
    if blocking:
        for p in blocking:
            con.print(f"[red]{p}[/red]")
        raise typer.Exit(2)
    _tf(cfg, "apply", "-input=false", *(["-auto-approve"] if yes else []))


@infra.command("destroy")
def infra_destroy(config: Optional[Path] = ConfigOpt):
    """Destroy the pipeline resources Terraform created (not the project; see `cap teardown`)."""
    _tf(_load(config), "destroy", "-input=false", bootstrap=False)


# =============================================================================
# package
# =============================================================================
@app.command()
def package(out: Path = typer.Option(ROOT / "dist", help="Output folder")):
    """Build the distributable zip (git-tracked files of HEAD) + SHA-256, for customers without GitHub access."""
    import hashlib
    import tomllib
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    sha = _run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture=True).stdout.strip()
    if _run(["git", "status", "--porcelain"], cwd=ROOT, capture=True).stdout.strip():
        con.print("[yellow]Uncommitted changes are NOT included (the zip is built from HEAD).[/yellow]")
    out.mkdir(parents=True, exist_ok=True)
    zip_path = out / f"creative-asset-pipeline-{version}-{sha}.zip"
    _run(["git", "archive", "--format=zip", "--prefix=creative-asset-pipeline/", f"--output={zip_path}", "HEAD"], cwd=ROOT)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (zip_path.with_suffix(".zip.sha256")).write_text(f"{digest}  {zip_path.name}\n")
    con.print(f"[green]{zip_path}[/green] ({zip_path.stat().st_size // 1024} KiB)\nsha256 {digest}")
    con.print("Cloud Shell: Upload the zip (⋮ → Upload), then: "
              f"sha256sum -c {zip_path.name}.sha256 && unzip {zip_path.name} && cd creative-asset-pipeline && "
              "./installer/install.sh")


# =============================================================================
# teardown
# =============================================================================
@app.command()
def teardown(config: Optional[Path] = ConfigOpt,
             execute: bool = typer.Option(False, "--execute", help="Actually delete (default: dry run)"),
             confirm: str = typer.Option("", help='Non-interactive confirmation, must equal "delete <project-id>"'),
             allow_prod: bool = typer.Option(False, "--allow-prod", help="Allow tearing down environment: prod")):
    """Delete what the installer created. Dry run by default; --execute asks you to type `delete <project-id>`.

    Project created by the installer -> the whole project is deleted (recoverable for 30 days).
    Existing project -> only this pipeline's and Creative Studio's resources are removed.
    """
    from . import gcp_project as gp
    cfg = _load(config)
    pid = cfg.gcp.project_id
    proj = gp.describe_project(pid)
    mode, reason = gp.teardown_plan(cfg, proj, allow_prod)
    t = Table(title=f"Teardown: {pid} ({cfg.environment})", show_header=False)
    t.add_row("Mode", f"[bold]{mode}[/bold]: {reason}")
    if mode != "refuse":
        for kind, items in gp.inventory(cfg).items():
            t.add_row(kind, ", ".join(items) or "-")
    con.print(t)
    if mode == "refuse":
        con.print(f"[red]Refusing: {reason}[/red]")
        raise typer.Exit(2)
    if mode == "project":
        con.print("[yellow]Deleting the project removes EVERYTHING in it (data included). It can be restored with "
                  f"`gcloud projects undelete {pid}` for 30 days; the ID can't be reused during that time.[/yellow]")
    con.print("[yellow]Not removed (outside the project): your GitHub fork and the Google Cloud Build app installed "
              "on your GitHub account; remove them on GitHub if no longer needed.[/yellow]")
    if not execute:
        con.print("Dry run only. Re-run with --execute to delete.")
        return
    phrase = gp.confirmation_phrase(cfg)
    typed = confirm or typer.prompt(f'Type "{phrase}" to confirm')
    if typed.strip() != phrase:
        con.print("[red]Confirmation text did not match; nothing was deleted.[/red]")
        raise typer.Exit(2)
    if mode == "project":
        gp.delete_project(pid)
        for f in STATE_DIR.glob(f"{cfg.customer.id}-{cfg.environment}.*"):
            f.unlink()
        con.print(f"[green]Project {pid} deleted (pending deletion for 30 days).[/green]")
        return
    _teardown_resources(cfg)


def _teardown_resources(cfg) -> None:
    """Existing project: remove only what this pipeline and Creative Studio created, in reverse order."""
    p, r = cfg.gcp.project_id, cfg.gcp.region
    for job in [j for j, _ in SCHEDULE_JOBS.values()]:
        _run(["gcloud", "scheduler", "jobs", "delete", job, f"--location={r}", f"--project={p}", "--quiet"], check=False)
    for trig in ("cap-creative-studio-finalized", "cap-intermediate-finalized"):
        _run(["gcloud", "eventarc", "triggers", "delete", trig, f"--location={r}", f"--project={p}", "--quiet"],
             check=False)
    for svc in ("cap-prompt-agent", "cap-lineage-router"):
        _run(["gcloud", "run", "services", "delete", svc, f"--region={r}", f"--project={p}", "--quiet"], check=False)
    gcc_env = _gcc_env_dir(cfg)
    if gcc_env and (gcc_env / "backend.tf").exists():
        con.print(f"Destroying Creative Studio infrastructure ({gcc_env}) ...")
        tfvars = next(gcc_env.glob("*.tfvars"), None)
        _run(["terraform", "init", "-input=false"], cwd=gcc_env, check=False)
        _run(["terraform", "destroy", "-input=false", "-auto-approve", *( [f"-var-file={tfvars.name}"] if tfvars else [])],
             cwd=gcc_env, check=False)
    elif cfg.creative_studio.deployment == "cloud_run":
        con.print("[yellow]Creative Studio environment folder not found; destroy it from its clone with "
                  "`terraform destroy` in infra/environments/<env>.[/yellow]")
    _tf(cfg, "destroy", "-input=false", "-auto-approve", bootstrap=False)
    con.print("[green]Pipeline resources removed. The project and its state bucket were kept.[/green]")


def _gcc_env_dir(cfg) -> Optional[Path]:
    src = cfg.creative_studio.fork_url or cfg.creative_studio.repo_url
    clone = ROOT.parent / src.rstrip("/").removesuffix(".git").split("/")[-1]
    envs = [d for d in (clone / "infra" / "environments").glob("*") if d.is_dir() and d.name != "dev-infra-example"] \
        if clone.is_dir() else []
    return envs[0] if len(envs) == 1 else None


# =============================================================================
# deploy
# =============================================================================
def _project_number(cfg) -> str:
    return _run(["gcloud", "projects", "describe", cfg.gcp.project_id, "--format=value(projectNumber)"],
                capture=True).stdout.strip()


@deploy.command("agent")
def deploy_agent(config: Optional[Path] = ConfigOpt):
    """Deploy the prompt agent according to agent_runtime (nothing to deploy for local)."""
    cfg = _load(config)
    rt = cfg.agent_runtime.value
    if rt == "local":
        con.print("[yellow]agent_runtime=local: nothing to deploy. Run `cap agent web` (browser) or `cap agent chat`.[/yellow]")
        return
    if rt == "cloud_run":
        _deploy_agent_cloud_run(cfg, _cfg_path(config))
        return
    from agents import deploy as d
    cfg_path = Path(config or os.environ["CAP_CONFIG"]).resolve()
    con.print("Deploying to Agent Engine (takes a few minutes)...")
    engine = d.deploy_agent_engine(cfg, cfg_path)
    con.print(f"[green]Agent Engine:[/green] {engine}")
    if rt != "gemini_enterprise":
        con.print("agent_runtime=agent_engine: not registering in Gemini Enterprise. Use `cap agent chat`.")
        return
    if not cfg.gemini_enterprise.app_id:
        con.print("[red]gemini_enterprise.app_id is empty; create the app in the Gemini Enterprise console first.[/red]")
        raise typer.Exit(2)
    num = _project_number(cfg)
    _service_identity(cfg, "discoveryengine.googleapis.com")
    _run(["gcloud", "projects", "add-iam-policy-binding", cfg.gcp.project_id, "--condition=None", "--quiet",
          f"--member=serviceAccount:service-{num}@gcp-sa-discoveryengine.iam.gserviceaccount.com",
          "--role=roles/aiplatform.user", "--format=none"])
    agent = d.register_in_gemini_enterprise(cfg, engine)
    con.print(f"[green]Registered in Gemini Enterprise:[/green] {agent.get('name')}")


def _cfg_path(config: Optional[Path]) -> Path:
    return Path(config or os.environ["CAP_CONFIG"]).resolve()


def _build_image(cfg, cfg_path: Path, service: str, image_name: str) -> str:
    """Build services/<service>/Dockerfile with Cloud Build (only this customer's config goes in the image)."""
    p, r = cfg.gcp.project_id, cfg.gcp.region
    img = f"{r}-docker.pkg.dev/{p}/cap/{image_name}:latest"
    build = STATE_DIR / f"cloudbuild.{image_name}.yaml"
    STATE_DIR.mkdir(exist_ok=True)
    build.write_text(yaml.safe_dump({"steps": [{"name": "gcr.io/cloud-builders/docker", "args": [
        "build", "-f", f"services/{service}/Dockerfile", "--build-arg",
        f"CUSTOMER_DIR={cfg_path.relative_to(ROOT).parent}", "-t", img, "."]}], "images": [img]}))
    _run(["gcloud", "builds", "submit", str(ROOT), f"--config={build}", f"--project={p}", f"--region={r}"])
    return img


def _bucket_exists(bucket: str, project: str) -> bool:
    return _run(["gcloud", "storage", "buckets", "describe", f"gs://{bucket}", f"--project={project}",
                 "--format=value(name)"], check=False, capture=True).returncode == 0


def _deploy_agent_cloud_run(cfg, cfg_path: Path) -> None:
    p, r = cfg.gcp.project_id, cfg.gcp.region
    img = _build_image(cfg, cfg_path, "prompt_agent", "prompt-agent")
    # browser origins used by `cap agent open --port ...`; ADK rejects other origins with 403
    origins = ",".join([f"http://{h}:{port}" for port in (8000, 8080, 8001) for h in ("localhost", "127.0.0.1")]
                       # Cloud Shell Web Preview serves the proxy from https://<port>-cs-<id>.cloudshell.dev
                       + [r"regex:^https://[0-9]+-cs-[a-z0-9-]+\.cloudshell\.dev$"])
    # "^|^" makes "|" the separator so the comma-separated origins stay one value (emails contain "@")
    env = "^|^" + "|".join([f"CAP_CONFIG={cfg_path.relative_to(ROOT)}", f"CAP_USER={cfg.gcp.admin_account}",
                            f"GOOGLE_CLOUD_PROJECT={p}", f"GOOGLE_CLOUD_LOCATION={cfg.models.model_location}",
                            "GOOGLE_GENAI_USE_VERTEXAI=TRUE", f"CAP_ALLOW_ORIGINS={origins}"])
    _run(["gcloud", "run", "deploy", "cap-prompt-agent", f"--image={img}", f"--region={r}", f"--project={p}",
          f"--service-account=sa-prompt-agent@{p}.iam.gserviceaccount.com", "--no-allow-unauthenticated",
          "--min-instances=0", "--max-instances=2", "--memory=2Gi", "--cpu=1", "--timeout=900",
          f"--set-env-vars={env}", "--quiet"])
    con.print("[green]Prompt agent deployed to Cloud Run (scales to zero).[/green] Open it with `cap agent open`.")


def _service_identity(cfg, service: str) -> str:
    """Create (idempotently) a Google service agent via the Service Usage API.

    Replaces `gcloud beta services identity create`, which prompts to install the
    gcloud beta component and hangs in non-interactive runs.
    """
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    r = AuthorizedSession(creds).post(
        f"https://serviceusage.googleapis.com/v1beta1/projects/{cfg.gcp.project_id}/services/{service}:generateServiceIdentity",
        headers={"x-goog-user-project": cfg.gcp.project_id}, timeout=60)
    r.raise_for_status()
    email = r.json().get("response", {}).get("email", "")
    con.print(f"[dim]service agent for {service}: {email or '(pending)'}[/dim]")
    return email


def _ensure_eventarc_agent(cfg) -> None:
    """First Eventarc use in a project: provision its service agent and grant its role (idempotent).

    Without this, the first `eventarc triggers create` fails with
    'Permission storage.buckets.get denied ... Eventarc service account'.
    """
    p, num = cfg.gcp.project_id, _project_number(cfg)
    _service_identity(cfg, "eventarc.googleapis.com")
    _run(["gcloud", "projects", "add-iam-policy-binding", p, "--condition=None", "--quiet", "--format=none",
          f"--member=serviceAccount:service-{num}@gcp-sa-eventarc.iam.gserviceaccount.com",
          "--role=roles/eventarc.serviceAgent"])


def _create_trigger_with_retry(cmd: list[str], attempts: int = 6, wait_s: int = 20) -> None:
    """New IAM grants take a minute to propagate; retry permission errors a few times."""
    import time
    for i in range(1, attempts + 1):
        r = _run(cmd, check=False, capture=True)
        if r.returncode == 0:
            return
        err = (r.stderr or "") + (r.stdout or "")
        if "PERMISSION_DENIED" not in err or i == attempts:
            con.print(f"[red]{err.strip()}[/red]")
            raise typer.Exit(1)
        con.print(f"[yellow]Permission not propagated yet; retrying in {wait_s}s ({i}/{attempts})...[/yellow]")
        time.sleep(wait_s)


@deploy.command("router")
def deploy_router(config: Optional[Path] = ConfigOpt):
    """Build the lineage router, deploy it to Cloud Run and wire Eventarc triggers."""
    cfg = _load(config)
    cfg_path = _cfg_path(config)
    p, r = cfg.gcp.project_id, cfg.gcp.region
    img = _build_image(cfg, cfg_path, "lineage_router", "lineage-router")
    sa = f"sa-lineage-router@{p}.iam.gserviceaccount.com"
    _run(["gcloud", "run", "deploy", "cap-lineage-router", f"--image={img}", f"--region={r}", f"--project={p}",
          f"--service-account={sa}", "--no-allow-unauthenticated", "--memory=1Gi", "--timeout=600",
          "--min-instances=0", "--concurrency=4", f"--set-env-vars=CAP_CONFIG={cfg_path.relative_to(ROOT)}", "--quiet"])
    _ensure_eventarc_agent(cfg)
    purposes = ["intermediate"]
    if cfg.creative_studio.deployment != "none":
        cs_bucket = cfg.bucket("creative-studio")
        if _bucket_exists(cs_bucket, p):
            purposes.append("creative-studio")
            if cfg.creative_studio.deployment == "cloud_run":  # GCC's own bucket: not managed by our Terraform
                _run(["gcloud", "storage", "buckets", "add-iam-policy-binding", f"gs://{cs_bucket}",
                      f"--member=serviceAccount:{sa}", "--role=roles/storage.objectViewer", "--format=none"])
        else:
            con.print(f"[yellow]Creative Studio bucket gs://{cs_bucket} not found yet: install Creative Studio, "
                      f"then re-run `cap deploy router` to add its trigger.[/yellow]")
    for purpose in purposes:
        name = f"cap-{purpose}-finalized"
        exists = _run(["gcloud", "eventarc", "triggers", "describe", name, f"--location={r}", f"--project={p}"],
                      check=False, capture=True).returncode == 0
        if exists:
            continue
        _create_trigger_with_retry(["gcloud", "eventarc", "triggers", "create", name, f"--location={r}",
              f"--project={p}", "--destination-run-service=cap-lineage-router", f"--destination-run-region={r}",
              "--event-filters=type=google.cloud.storage.object.v1.finalized",
              f"--event-filters=bucket={cfg.bucket(purpose)}", f"--service-account={sa}"])
    con.print("[green]Router deployed.[/green]")


@deploy.command("all")
def deploy_all(config: Optional[Path] = ConfigOpt):
    """Deploy the router, then the agent."""
    deploy_router(config)
    deploy_agent(config)


# =============================================================================
# agent (talk to the prompt agent without Gemini Enterprise)
# =============================================================================
def _agent_env(cfg, cfg_path: Path) -> dict:
    return {**os.environ, "CAP_CONFIG": str(cfg_path), "CAP_USER": _whoami() or os.environ.get("USER", "dev"),
            "GOOGLE_GENAI_USE_VERTEXAI": "TRUE", "GOOGLE_CLOUD_PROJECT": cfg.gcp.project_id,
            "GOOGLE_CLOUD_LOCATION": cfg.models.model_location}


@agent.command("web")
def agent_web(config: Optional[Path] = ConfigOpt, port: int = 8000):
    """Run the prompt agent locally in the ADK dev UI (http://localhost:<port>). No hosting cost."""
    cfg = _load(config)
    cfg_path = Path(config or os.environ["CAP_CONFIG"]).resolve()
    adk = shutil.which("adk") or str(Path(sys.executable).parent / "adk")
    con.print(f"Open http://localhost:{port} and pick [bold]prompt_enrichment[/bold]. Prompts are recorded as "
              f"created by {_agent_env(cfg, cfg_path)['CAP_USER']}. Ctrl+C to stop.")
    os.chdir(ROOT)
    os.execvpe(adk, [adk, "web", "agents", "--port", str(port)], _agent_env(cfg, cfg_path))


@agent.command("open")
def agent_open(config: Optional[Path] = ConfigOpt, port: int = 8000):
    """Open the Cloud Run prompt agent on http://localhost:<port> through an authenticated proxy."""
    cfg = _load(config)
    con.print(f"Proxying cap-prompt-agent to http://localhost:{port} (uses your gcloud login). Ctrl+C to stop.")
    os.execvp("gcloud", ["gcloud", "run", "services", "proxy", "cap-prompt-agent", f"--region={cfg.gcp.region}",
                         f"--project={cfg.gcp.project_id}", f"--port={port}"])


@agent.command("chat")
def agent_chat(config: Optional[Path] = ConfigOpt,
               remote: bool = typer.Option(None, help="Talk to the deployed Agent Engine agent (default when "
                                                      "agent_runtime is agent_engine or gemini_enterprise)")):
    """Chat with the prompt agent in the terminal (local, or the deployed Agent Engine agent)."""
    cfg = _load(config)
    cfg_path = Path(config or os.environ["CAP_CONFIG"]).resolve()
    env = _agent_env(cfg, cfg_path)
    os.environ.update({k: env[k] for k in ("CAP_CONFIG", "CAP_USER", "GOOGLE_GENAI_USE_VERTEXAI",
                                           "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION")})
    user = env["CAP_USER"]
    remote = cfg.agent_runtime.value in ("agent_engine", "gemini_enterprise") if remote is None else remote
    con.print(f"Chatting as {user} ({'Agent Engine' if remote else 'local'}). Empty line or Ctrl+D to quit.")
    if remote:
        _chat_remote(cfg, user)
    else:
        import asyncio
        asyncio.run(_chat_local(user))


def _read() -> str:
    try:
        return input("\nyou> ").strip()
    except EOFError:
        return ""


async def _chat_local(user: str) -> None:
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from agents.prompt_enrichment.agent import root_agent
    runner = InMemoryRunner(agent=root_agent, app_name="cap")
    session = await runner.session_service.create_session(app_name="cap", user_id=user)
    while text := _read():
        msg = types.Content(role="user", parts=[types.Part.from_text(text=text)])
        async for ev in runner.run_async(user_id=user, session_id=session.id, new_message=msg):
            for part in (ev.content.parts if ev.content else None) or []:
                if part.text:
                    con.print(f"[cyan]agent>[/cyan] {part.text}")
                elif part.function_call:
                    con.print(f"[dim]  tool: {part.function_call.name}[/dim]")


def _chat_remote(cfg, user: str) -> None:
    import vertexai
    from vertexai import agent_engines

    from agents import deploy as d
    name = d.load_state(cfg).get("agent_engine")
    if not name:
        con.print("[red]No deployed agent recorded; run `cap deploy agent` (agent_runtime agent_engine) first.[/red]")
        raise typer.Exit(2)
    vertexai.init(project=cfg.gcp.project_id, location=cfg.agent.agent_engine_location)
    app_ = agent_engines.get(name)
    session = app_.create_session(user_id=user)
    while text := _read():
        for ev in app_.stream_query(user_id=user, session_id=session["id"], message=text):
            for part in (ev.get("content") or {}).get("parts") or []:
                if part.get("text"):
                    con.print(f"[cyan]agent>[/cyan] {part['text']}")
                elif part.get("function_call"):
                    con.print(f"[dim]  tool: {part['function_call'].get('name')}[/dim]")


# =============================================================================
# batch / prompt / image / hitl
# =============================================================================
@batch.command("start")
def batch_start(skus: list[str] = typer.Argument(..., help="SKUs"), config: Optional[Path] = ConfigOpt,
                user: Optional[str] = typer.Option(None), notes: str = ""):
    """Open a batch run and print its Batch Run ID."""
    cfg = _load(config)
    con.print(_pipe(cfg).start_batch(skus, _user(user), "CLI", notes))


@prompt.command("save")
def prompt_save(sku: str, text: str = typer.Option(..., "--text", help="Enriched prompt text (or @file.txt)"),
                shot_type: str = "hero", variant_index: int = 1, aspect_ratio: str = "", resolution: str = "",
                batch_run_id: str = "", config: Optional[Path] = ConfigOpt, user: Optional[str] = typer.Option(None),
                generate: bool = typer.Option(None, help="Generate now (default: only in automatic mode)")):
    """Save one prompt version (the agent normally does this) and print the copy block."""
    cfg = _load(config)
    if text.startswith("@"):
        text = Path(text[1:]).read_text()
    pipe = _pipe(cfg)
    saved = pipe.save_prompts([{"sku": sku, "prompt_text": text, "shot_type": shot_type, "variant_index": variant_index,
                                "aspect_ratio": aspect_ratio or None, "resolution": resolution or None}],
                              user=_user(user), batch_run_id=batch_run_id or None, origin="CLI")[0]
    con.print(saved["copy_text"])
    if generate or (generate is None and cfg.generation.mode.value == "automatic"):
        _print(pipe.generate_for_prompt(saved["prompt_id"], _user(user)))


@prompt.command("history")
def prompt_history(sku: str, config: Optional[Path] = ConfigOpt):
    """Every prompt version for a SKU."""
    rows = _pipe(_load(config)).bq.query(
        "SELECT prompt_lineage_id, prompt_version, prompt_id, parent_prompt_id, origin, shot_type, variant_index, "
        "created_by, created_at FROM `${project}.${dataset}.enriched_prompt` WHERE sku = @s "
        "ORDER BY prompt_lineage_id, prompt_version", s=sku)
    _table(rows)


@image.command("generate")
def image_generate(prompt_id: str, n: Optional[int] = None, config: Optional[Path] = ConfigOpt,
                   user: Optional[str] = typer.Option(None)):
    """Generate images for a prompt with the image model (automatic step), then score and route."""
    _print(_pipe(_load(config)).generate_for_prompt(prompt_id, _user(user), n))


@image.command("register")
def image_register(prompt_id: str, file: Path, config: Optional[Path] = ConfigOpt,
                   user: Optional[str] = typer.Option(None), generation_id: Optional[str] = None):
    """Register an image made outside the pipeline (e.g. downloaded from Creative Studio) against a Prompt ID."""
    mt = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}[file.suffix.lower()[1:]]
    _print(_pipe(_load(config)).register_image(data=file.read_bytes(), mime_type=mt, prompt_id=prompt_id,
                                               user=_user(user), tool="MANUAL_UPLOAD", match_method="MANUAL_LINK",
                                               generation_id=generation_id))


@image.command("link")
def image_link(generation_id: str, prompt_id: str, config: Optional[Path] = ConfigOpt,
               user: Optional[str] = typer.Option(None)):
    """Link an UNMATCHED Creative Studio image to its Prompt ID, then score it."""
    _print(_pipe(_load(config)).link(generation_id, prompt_id, _user(user)))


@image.command("rescore")
def image_rescore(generation_id: str, version: Optional[int] = None, config: Optional[Path] = ConfigOpt):
    """Score an image version again (e.g. after threshold calibration)."""
    _print(_pipe(_load(config)).rescore(generation_id, version).__dict__)


@hitl.command("revise")
def hitl_revise(generation_id: str, text: str = typer.Option(..., "--text", help="Edited prompt (or @file.txt)"),
                reason: str = "", config: Optional[Path] = ConfigOpt, user: Optional[str] = typer.Option(None),
                force: bool = False):
    """Edit the prompt for an image in NEEDS_REVISION/FLAGGED and regenerate (new prompt + image version)."""
    if text.startswith("@"):
        text = Path(text[1:]).read_text()
    r = _pipe(_load(config)).revise(generation_id, text, _user(user), reason, force)
    if "copy_text" in r["prompt"]:
        con.print(r["prompt"]["copy_text"])
    _print({k: v for k, v in r.items() if k != "prompt"} | {"prompt_id": r["prompt"]["prompt_id"]})


@hitl.command("approve")
def hitl_approve(generation_id: str, reason: str = "", config: Optional[Path] = ConfigOpt,
                 user: Optional[str] = typer.Option(None)):
    """Approve an image as-is."""
    _print(_pipe(_load(config)).decide(generation_id, "APPROVE", _user(user), reason))


@hitl.command("reject")
def hitl_reject(generation_id: str, reason: str = typer.Option(..., help="Reason code / text"),
                config: Optional[Path] = ConfigOpt, user: Optional[str] = typer.Option(None)):
    """Reject an image."""
    _print(_pipe(_load(config)).decide(generation_id, "REJECT", _user(user), reason))


# =============================================================================
# status / reports
# =============================================================================
def _table(rows: list[dict]) -> None:
    if not rows:
        con.print("[dim](no rows)[/dim]")
        return
    t = Table(*rows[0].keys())
    for r in rows:
        t.add_row(*[("" if v is None else str(v))[:80] for v in r.values()])
    con.print(t)


@app.command()
def status(sku: str = "", batch_run_id: str = "", generation_id: str = "", config: Optional[Path] = ConfigOpt):
    """Current status of images (filter by SKU, batch or generation)."""
    rows = _pipe(_load(config)).bq.query(
        "SELECT generation_id, version, sku, prompt_version, prompted_by, composite, hallucination_band, "
        "current_status, output_stage, status_uri FROM `${project}.${dataset}.v_asset_lifecycle` "
        "WHERE (@s = '' OR sku = @s) AND (@b = '' OR batch_run_id = @b) AND (@g = '' OR generation_id = @g) "
        "ORDER BY created_at DESC LIMIT 200", s=sku, b=batch_run_id, g=generation_id)
    _table(rows)


@app.command()
def report(name: str = typer.Argument(..., help="Report file name without .sql, e.g. 01_who_prompted_what; 'list' to list"),
           user: str = "", batch_run_id: str = "", parent_asset_id: str = "", config: Optional[Path] = ConfigOpt):
    """Run a report from bigquery/reports/."""
    rdir = ROOT / "bigquery" / "reports"
    if name == "list":
        for f in sorted(rdir.glob("*.sql")):
            con.print(f"{f.stem:32} {f.read_text().splitlines()[0].lstrip('- ')}")
        return
    sql = (rdir / f"{name}.sql").read_text()
    params = {k: v for k, v in {"user": user, "batch_run_id": batch_run_id, "parent_asset_id": parent_asset_id}.items()
              if f"@{k}" in sql}
    _table(_pipe(_load(config)).bq.query(sql, **params))


# =============================================================================
# Creative Studio VM
# =============================================================================
def _vm(cfg) -> list[str]:
    return [f"creative-studio-{cfg.environment}", f"--zone={cfg.gcp.zone}", f"--project={cfg.gcp.project_id}"]


def _gcc_sql_instance(cfg) -> str:
    out = _run(["gcloud", "sql", "instances", "list", f"--project={cfg.gcp.project_id}",
                f"--filter=name~^{cfg.creative_studio.sql_instance_prefix}", "--format=value(name)"],
               capture=True).stdout.split()
    if not out:
        con.print("[red]No Creative Studio Cloud SQL instance found (is GCC installed in this project?).[/red]")
        raise typer.Exit(1)
    return out[0]


def _is_sha(ref: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{7,40}", ref))


def _remote_has_ref(url: str, ref: str) -> bool:
    if _is_sha(ref):
        return True  # can't cheaply check a commit without cloning
    out = _run(["git", "ls-remote", "--heads", "--tags", url, ref], check=False, capture=True).stdout
    return bool(out.strip())


@gcc.command("install")
def gcc_install(config: Optional[Path] = ConfigOpt, dry_run: bool = typer.Option(False, help="Only print the steps")):
    """Install Creative Studio (deployment=cloud_run) with Google's bootstrap.sh, using the configured GCC ref."""
    cfg = _load(config)
    cs, ref = cfg.creative_studio, cfg.gcc_ref()
    if cs.deployment != "cloud_run":
        con.print(f"[yellow]creative_studio.deployment is {cs.deployment}; `cap gcc install` is for cloud_run "
                  f"(vm installs through `cap infra apply`).[/yellow]")
        raise typer.Exit(1)
    source = cs.fork_url or cs.repo_url
    if not cs.fork_url:
        con.print("[yellow]creative_studio.fork_url is empty. GCC's bootstrap deploys from YOUR GitHub fork: fork "
                  f"{cs.repo_url} (untick 'Copy the main branch only') and set fork_url.[/yellow]")
    if not _remote_has_ref(source, ref):
        con.print(f"[red]Ref '{ref}' not found in {source}. If this is your fork, sync it or re-fork with all branches.[/red]")
        raise typer.Exit(2)
    # the ref may exist only in the fork (e.g. developlocal), so take bootstrap.sh from the fork
    raw = source.replace("https://github.com/", "https://raw.githubusercontent.com/").removesuffix(".git")
    url = f"{raw}/{ref if _is_sha(ref) else 'refs/heads/' + ref}/bootstrap.sh"
    t = Table(title="Answers for GCC's bootstrap.sh", show_header=False)
    for k, v in [("Existing project?", f"y -> {cfg.gcp.project_id}"), ("Fork git URL", cs.fork_url or "(your fork)"),
                 ("Branch", ref), ("Environment name", cs.gcc_environment),
                 ("Console steps", "Cloud Build GitHub connection, Firebase terms, OAuth client ID")]:
        t.add_row(k, v)
    con.print(t)
    # bootstrap.sh clones into ./<repo-name>; run it next to this repo so an existing clone
    # (e.g. code/gcc-creative-studio) is reused instead of cluttering the pipeline folder
    workdir = ROOT.parent
    clone = workdir / source.rstrip("/").removesuffix(".git").split("/")[-1]
    con.print(f"Bootstrap script from ref [bold]{ref}[/bold]: {url}")
    con.print(f"Runs in {workdir}" + (f"; when asked whether to use the existing directory '{clone.name}', answer y"
                                      if clone.is_dir() else ""))
    if dry_run:
        return
    os.chdir(workdir)
    os.execvp("bash", ["bash", "-c", f"curl -fsSL {shlex.quote(url)} | bash"])


@gcc.command("status")
def gcc_status(config: Optional[Path] = ConfigOpt):
    """Show Creative Studio (Cloud Run deployment) services, database state and URL."""
    cfg = _load(config)
    p, r, cs = cfg.gcp.project_id, cfg.gcp.region, cfg.creative_studio
    _run(["gcloud", "run", "services", "list", f"--project={p}", f"--region={r}",
          "--format=table(metadata.name,status.url,spec.template.metadata.annotations.'autoscaling.knative.dev/minScale':label=MIN)"],
         check=False)
    _run(["gcloud", "sql", "instances", "list", f"--project={p}",
          "--format=table(name,settings.tier,settings.activationPolicy,state)"], check=False)
    _run(["gcloud", "scheduler", "jobs", "list", f"--location={r}", f"--project={p}", "--filter=ID~cap-gcc-sql",
          "--format=table(ID,schedule,timeZone,state,lastAttemptTime)"], check=False)
    con.print(f"Creative Studio UI (Firebase Hosting): https://{p}.web.app   media bucket: gs://{cfg.bucket('creative-studio')}")


SCHEDULE_JOBS = {"stop": ("cap-gcc-sql-stop", "NEVER"), "start": ("cap-gcc-sql-start", "ALWAYS"),
                 "idle": ("cap-gcc-sql-idle-check", None)}


@gcc.command("schedule")
def gcc_schedule(config: Optional[Path] = ConfigOpt,
                 remove: bool = typer.Option(False, "--remove", help="Delete the schedule jobs"),
                 force: bool = typer.Option(False, help="Allow outside the dev environment")):
    """DEV: Cloud Scheduler jobs: daily stop (default 17:00 region-local), idle stop (default 30 min), optional start."""
    from .schedule import cron
    cfg = _load(config)
    p, r, sch = cfg.gcp.project_id, cfg.gcp.region, cfg.creative_studio.auto_schedule
    existing = set(_run(["gcloud", "scheduler", "jobs", "list", f"--location={r}", f"--project={p}",
                         "--format=value(ID)"], check=False, capture=True).stdout.split())
    for job, _ in SCHEDULE_JOBS.values():  # recreate from config each time (simple and idempotent)
        if job in existing:
            _run(["gcloud", "scheduler", "jobs", "delete", job, f"--location={r}", f"--project={p}", "--quiet"])
    if remove or not sch.enabled:
        con.print("[green]Schedule removed.[/green]" if remove else
                  "[yellow]creative_studio.auto_schedule.enabled is false; no schedule created.[/yellow]")
        return
    if cfg.environment != "dev" and not force:
        con.print(f"[red]Auto-stop is meant for development; environment is {cfg.environment}. Use --force.[/red]")
        raise typer.Exit(2)
    instance, tz = _gcc_sql_instance(cfg), cfg.schedule_timezone()
    uri = f"https://sqladmin.googleapis.com/v1/projects/{p}/instances/{instance}"
    plan = [("stop", sch.stop_time, sch.stop_days)] + ([("start", sch.start_time, sch.start_days)] if sch.start_time else [])
    for action, hhmm, days in plan:
        job, policy = SCHEDULE_JOBS[action]
        _run(["gcloud", "scheduler", "jobs", "create", "http", job, f"--location={r}", f"--project={p}",
              # Cloud Scheduler has no PATCH; Google APIs accept POST + X-HTTP-Method-Override
              f"--schedule={cron(hhmm, days)}", f"--time-zone={tz}", f"--uri={uri}", "--http-method=POST",
              f"--message-body={json.dumps({'settings': {'activationPolicy': policy}})}",
              "--headers=Content-Type=application/json,X-HTTP-Method-Override=PATCH",
              f"--oauth-service-account-email=sa-scheduler@{p}.iam.gserviceaccount.com",
              "--oauth-token-scope=https://www.googleapis.com/auth/cloud-platform",
              f"--description=cap: {action} Creative Studio Cloud SQL ({instance})"])
        con.print(f"[green]{action}[/green] {instance} at {hhmm} {tz} on {', '.join(days)}")
    if cfg.gcc_idle_stop_enabled():
        router_url = _run(["gcloud", "run", "services", "describe", "cap-lineage-router", f"--region={r}",
                           f"--project={p}", "--format=value(status.url)"], check=False, capture=True).stdout.strip()
        if not router_url:
            con.print("[yellow]Idle stop needs the lineage router: run `cap deploy router`, then `cap gcc schedule`.[/yellow]")
        else:
            job, _ = SCHEDULE_JOBS["idle"]
            _run(["gcloud", "scheduler", "jobs", "create", "http", job, f"--location={r}", f"--project={p}",
                  f"--schedule=*/{sch.idle_check_every_minutes} * * * *", f"--time-zone={tz}",
                  f"--uri={router_url}/tasks/gcc-idle-check", "--http-method=POST",
                  f"--oidc-service-account-email=sa-scheduler@{p}.iam.gserviceaccount.com",
                  f"--oidc-token-audience={router_url}", "--attempt-deadline=120s",
                  f"--description=cap: stop Creative Studio Cloud SQL after {sch.idle_stop_minutes} idle min"])
            con.print(f"[green]idle stop[/green] after {sch.idle_stop_minutes} min without backend requests "
                      f"(checked every {sch.idle_check_every_minutes} min)")
    if not sch.start_time:
        con.print("No scheduled start: with GCC developlocal the database starts on the first request "
                  "(otherwise `cap gcc wake`).")


@gcc.command("sleep")
def gcc_sleep(config: Optional[Path] = ConfigOpt):
    """Pause Creative Studio when not in use: stop Cloud SQL and let the backend scale to zero."""
    cfg = _load(config)
    p, r = cfg.gcp.project_id, cfg.gcp.region
    _run(["gcloud", "run", "services", "update", cfg.creative_studio.backend_service, "--min-instances=0",
          f"--region={r}", f"--project={p}", "--quiet"])
    _run(["gcloud", "sql", "instances", "patch", _gcc_sql_instance(cfg), "--activation-policy=NEVER",
          f"--project={p}", "--quiet"])
    con.print("[green]Creative Studio is asleep.[/green] Only storage is billed. `cap gcc wake` to resume.")


@gcc.command("wake")
def gcc_wake(config: Optional[Path] = ConfigOpt):
    """Start Cloud SQL again (the backend starts on the first request)."""
    cfg = _load(config)
    _run(["gcloud", "sql", "instances", "patch", _gcc_sql_instance(cfg), "--activation-policy=ALWAYS",
          f"--project={cfg.gcp.project_id}", "--quiet"])
    con.print(f"[green]Creative Studio database started.[/green] UI: https://{cfg.gcp.project_id}.web.app")


@gcc.command("ssh")
def gcc_ssh(config: Optional[Path] = ConfigOpt):
    """SSH to the Creative Studio VM through IAP (deployment=vm only)."""
    cfg = _load(config)
    os.execvp("gcloud", ["gcloud", "compute", "ssh", *_vm(cfg), "--tunnel-through-iap"])


@gcc.command("tunnel")
def gcc_tunnel(config: Optional[Path] = ConfigOpt, remote_port: int = 8080, local_port: int = 8080):
    """Open the Creative Studio UI on http://localhost:<local_port> through IAP (deployment=vm only)."""
    cfg = _load(config)
    name, *rest = _vm(cfg)
    os.execvp("gcloud", ["gcloud", "compute", "start-iap-tunnel", name, str(remote_port),
                         f"--local-host-port=localhost:{local_port}", *rest])


@gcc.command("logs")
def gcc_logs(config: Optional[Path] = ConfigOpt):
    """Show the VM startup/installation log."""
    cfg = _load(config)
    _run(["gcloud", "compute", "ssh", *_vm(cfg), "--tunnel-through-iap", "--command",
          "sudo tail -n 200 /var/log/creative-studio-startup.log"], check=False)


if __name__ == "__main__":
    sys.exit(app())
