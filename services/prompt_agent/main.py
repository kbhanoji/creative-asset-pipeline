"""Prompt agent on Cloud Run (agent_runtime = cloud_run).

Serves the ADK web UI + API for the agents/ directory. Scales to zero when idle.
The service is private (--no-allow-unauthenticated); developers reach it with
`cap agent open`, which runs an authenticated `gcloud run services proxy`.
Sessions are in memory (lost on scale-down); all lineage is in BigQuery.

ADK rejects browser requests whose Origin differs from the Host it sees
("403 origin not allowed"). Through the proxy the browser is on
http://localhost:<port> while the service sees its run.app host, so the local
proxy origins are allowed explicitly (CAP_ALLOW_ORIGINS, comma-separated).
That is safe because the service itself only accepts authenticated callers.
"""
import os

from google.adk.cli.fast_api import get_fast_api_app

app = get_fast_api_app(
    agents_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents"),
    web=True,
    allow_origins=[o.strip() for o in os.environ.get(
        "CAP_ALLOW_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if o.strip()],
    use_local_storage=False,
    auto_create_session=True,
    trace_to_cloud=os.environ.get("CAP_TRACE", "false").lower() == "true",
)
