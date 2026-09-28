"""Development idle stop for the Creative Studio Cloud SQL database.

Cloud Scheduler calls the lineage router's /tasks/gcc-idle-check every
auto_schedule.idle_check_every_minutes. If the database is running and the GCC
backend served no requests in the last idle_stop_minutes, the database is stopped
(activation policy NEVER). GCC's developlocal branch starts it again on the next
request, so nothing has to be started by hand.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import Config

SQLADMIN = "https://sqladmin.googleapis.com/v1"
MONITORING = "https://monitoring.googleapis.com/v3"
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def idle_decision(policy: str | None, request_count: int, idle_minutes: int,
                  minutes_since_change: float | None = None) -> tuple[bool, str]:
    """Pure rule: stop only a running database whose backend had no requests in the window,
    and that wasn't just started/changed (e.g. `cap gcc wake`) within the same window."""
    if policy != "ALWAYS":
        return False, f"database already stopped (activationPolicy={policy})"
    if minutes_since_change is not None and minutes_since_change < idle_minutes:
        return False, f"database was started/changed {minutes_since_change:.0f} min ago (< {idle_minutes} min)"
    if request_count > 0:
        return False, f"{request_count} backend request(s) in the last {idle_minutes} min"
    return True, f"no backend requests in the last {idle_minutes} min"


def _session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    creds, _ = google.auth.default(scopes=SCOPES)
    return AuthorizedSession(creds)


def find_instance(cfg: Config, session) -> dict | None:
    r = session.get(f"{SQLADMIN}/projects/{cfg.gcp.project_id}/instances", timeout=30)
    r.raise_for_status()
    prefix = cfg.creative_studio.sql_instance_prefix
    return next((i for i in r.json().get("items", []) if i["name"].startswith(prefix)), None)


def backend_request_count(cfg: Config, session, minutes: int, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(minutes=minutes)
    params = {
        "filter": ('metric.type="run.googleapis.com/request_count" AND resource.type="cloud_run_revision" '
                   f'AND resource.labels.service_name="{cfg.creative_studio.backend_service}"'),
        "interval.startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interval.endTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aggregation.alignmentPeriod": f"{minutes * 60}s",
        "aggregation.perSeriesAligner": "ALIGN_SUM",
        "aggregation.crossSeriesReducer": "REDUCE_SUM",
    }
    r = session.get(f"{MONITORING}/projects/{cfg.gcp.project_id}/timeSeries", params=params, timeout=30)
    r.raise_for_status()
    return sum(int(p["value"].get("int64Value", 0))
               for ts in r.json().get("timeSeries", []) for p in ts.get("points", []))


def minutes_since_last_operation(cfg: Config, session, instance: str, now: datetime | None = None) -> float | None:
    """Minutes since the instance's most recent admin operation started (a running one counts as 0)."""
    now = now or datetime.now(timezone.utc)
    r = session.get(f"{SQLADMIN}/projects/{cfg.gcp.project_id}/operations",
                    params={"instance": instance, "maxResults": 5}, timeout=30)
    if r.status_code != 200:
        return None
    ops = r.json().get("items", [])
    if not ops:
        return None
    if any(o.get("status") != "DONE" for o in ops):
        return 0.0
    latest = max(datetime.fromisoformat(o["startTime"].replace("Z", "+00:00")) for o in ops if o.get("startTime"))
    return (now - latest).total_seconds() / 60


def check_and_stop(cfg: Config) -> dict:
    minutes = cfg.creative_studio.auto_schedule.idle_stop_minutes
    if minutes <= 0:
        return {"action": "none", "reason": "idle stop disabled (idle_stop_minutes = 0)"}
    session = _session()
    inst = find_instance(cfg, session)
    if not inst:
        return {"action": "none", "reason": "no Creative Studio Cloud SQL instance found"}
    policy = inst.get("settings", {}).get("activationPolicy")
    count = backend_request_count(cfg, session, minutes) if policy == "ALWAYS" else 0
    since = minutes_since_last_operation(cfg, session, inst["name"]) if policy == "ALWAYS" else None
    stop, reason = idle_decision(policy, count, minutes, since)
    if stop:
        url = f"{SQLADMIN}/projects/{cfg.gcp.project_id}/instances/{inst['name']}"
        r = session.patch(url, json={"settings": {"activationPolicy": "NEVER"}}, timeout=30)
        if r.status_code == 409:  # another operation (e.g. a start) is running: try again next round
            return {"action": "none", "instance": inst["name"], "reason": "instance busy (operation in progress)"}
        r.raise_for_status()
    return {"action": "stopped" if stop else "none", "instance": inst["name"], "reason": reason}
