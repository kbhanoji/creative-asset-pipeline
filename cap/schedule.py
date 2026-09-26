"""Development auto-stop/start schedule for the Creative Studio Cloud SQL database.

Cloud Scheduler calls the Cloud SQL Admin API directly (PATCH activationPolicy),
so nothing extra has to run. Times are in the timezone of the GCP region that
hosts Creative Studio unless creative_studio.auto_schedule.timezone is set.
"""
from __future__ import annotations

import re

# Timezone of the city each GCP region is in ("local on the server where it is hosted").
REGION_TIMEZONES = {
    "us-central1": "America/Chicago", "us-south1": "America/Chicago", "us-east1": "America/New_York",
    "us-east4": "America/New_York", "us-east5": "America/New_York", "us-west1": "America/Los_Angeles",
    "us-west2": "America/Los_Angeles", "us-west3": "America/Denver", "us-west4": "America/Los_Angeles",
    "northamerica-northeast1": "America/Toronto", "northamerica-northeast2": "America/Toronto",
    "southamerica-east1": "America/Sao_Paulo", "europe-west1": "Europe/Brussels", "europe-west2": "Europe/London",
    "europe-west3": "Europe/Berlin", "europe-west4": "Europe/Amsterdam", "europe-west6": "Europe/Zurich",
    "europe-west9": "Europe/Paris", "europe-north1": "Europe/Helsinki", "europe-southwest1": "Europe/Madrid",
    "asia-south1": "Asia/Kolkata", "asia-south2": "Asia/Kolkata", "asia-southeast1": "Asia/Singapore",
    "asia-northeast1": "Asia/Tokyo", "asia-east1": "Asia/Taipei", "asia-east2": "Asia/Hong_Kong",
    "australia-southeast1": "Australia/Sydney", "me-west1": "Asia/Jerusalem",
}

DAYS = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 0}


def region_timezone(region: str) -> str:
    return REGION_TIMEZONES.get(region, "Etc/UTC")


def cron(hhmm: str, days: list[str]) -> str:
    """'17:00', ['mon',...,'fri'] -> '0 17 * * 1,2,3,4,5'. All seven days -> '*'."""
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", hhmm.strip())
    if not m:
        raise ValueError(f"time must be HH:MM (24h), got {hhmm!r}")
    try:
        nums = sorted({DAYS[d.lower()[:3]] for d in days})
    except KeyError as e:
        raise ValueError(f"unknown day {e.args[0]!r}; use mon..sun") from None
    dow = "*" if len(nums) == 7 else ",".join(str(n) for n in nums)
    return f"{int(m.group(2))} {int(m.group(1))} * * {dow}"
