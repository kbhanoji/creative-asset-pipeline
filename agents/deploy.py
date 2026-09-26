"""Deploy the prompt agent to Vertex AI Agent Engine and register it in Gemini Enterprise.

Called by `cap deploy agent`. Re-running updates the existing Agent Engine
resource (its name is kept in .state/<customer>-<env>.json) instead of
creating duplicates.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from cap.config import Config

ROOT = Path(__file__).resolve().parents[1]

REQUIREMENTS = [
    "google-adk>=1.10",
    "google-cloud-aiplatform[agent_engines,adk]>=1.110",
    "google-cloud-bigquery>=3.25",
    "google-cloud-storage>=2.18",
    "google-genai>=1.30",
    "pydantic>=2.8",
    "pyyaml>=6.0",
    "requests>=2.32",
]


def state_file(cfg: Config) -> Path:
    return ROOT / ".state" / f"{cfg.customer.id}-{cfg.environment}.json"


def load_state(cfg: Config) -> dict:
    f = state_file(cfg)
    return json.loads(f.read_text()) if f.exists() else {}


def save_state(cfg: Config, **kv) -> None:
    f = state_file(cfg)
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps({**load_state(cfg), **kv}, indent=2))


def deploy_agent_engine(cfg: Config, config_path: Path) -> str:
    import vertexai
    from vertexai import agent_engines

    rel_cfg = config_path.resolve().relative_to(ROOT)
    os.environ["CAP_CONFIG"] = str(config_path.resolve())
    os.chdir(ROOT)
    from agents.prompt_enrichment.agent import root_agent  # imported after CAP_CONFIG is set

    vertexai.init(project=cfg.gcp.project_id, location=cfg.agent.agent_engine_location,
                  staging_bucket=f"gs://{cfg.bucket(cfg.agent.staging_bucket_purpose)}")
    app = agent_engines.AdkApp(agent=root_agent, enable_tracing=True)
    kwargs = dict(
        agent_engine=app,
        requirements=REQUIREMENTS,
        extra_packages=["cap", "agents", str(rel_cfg.parent)],
        display_name=cfg.agent.display_name,
        description=cfg.agent.description,
        env_vars={"CAP_CONFIG": str(rel_cfg)},
        service_account=f"sa-prompt-agent@{cfg.gcp.project_id}.iam.gserviceaccount.com",
    )
    existing = load_state(cfg).get("agent_engine")
    try:
        remote = agent_engines.update(resource_name=existing, **kwargs) if existing else agent_engines.create(**kwargs)
    except TypeError:
        # older SDKs don't accept service_account; the Reasoning Engine service agent is used instead
        kwargs.pop("service_account")
        remote = agent_engines.update(resource_name=existing, **kwargs) if existing else agent_engines.create(**kwargs)
    save_state(cfg, agent_engine=remote.resource_name)
    return remote.resource_name


def register_in_gemini_enterprise(cfg: Config, reasoning_engine: str) -> dict:
    """Register the Agent Engine agent in an existing Gemini Enterprise app (Discovery Engine API).

    The endpoint and body follow the documented 'register an ADK agent' flow; confirm against the
    current Gemini Enterprise docs if Google changes the API version.
    """
    import google.auth
    import google.auth.transport.requests
    import requests

    ge = cfg.gemini_enterprise
    if not ge.app_id:
        raise RuntimeError("gemini_enterprise.app_id is empty: create the Gemini Enterprise app in the console first")
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    host = "discoveryengine.googleapis.com" if ge.location == "global" else f"{ge.location}-discoveryengine.googleapis.com"
    parent = (f"projects/{cfg.gcp.project_id}/locations/{ge.location}/collections/default_collection/"
              f"engines/{ge.app_id}/assistants/default_assistant")
    headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json",
               "X-Goog-User-Project": cfg.gcp.project_id}
    body = {
        "displayName": cfg.agent.display_name,
        "description": cfg.agent.description,
        "adkAgentDefinition": {
            "toolSettings": {"toolDescription": cfg.agent.description},
            "provisionedReasoningEngine": {"reasoningEngine": reasoning_engine},
        },
    }
    existing = load_state(cfg).get("ge_agent")
    if existing:
        r = requests.patch(f"https://{host}/v1alpha/{existing}", headers=headers, json=body, timeout=60)
    else:
        r = requests.post(f"https://{host}/v1alpha/{parent}/agents", headers=headers, json=body, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"Gemini Enterprise registration failed ({r.status_code}): {r.text}")
    agent = r.json()
    save_state(cfg, ge_agent=agent.get("name", existing))
    return agent
