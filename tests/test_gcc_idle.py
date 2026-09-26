from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cap import config as cap_config
from cap import gcc_idle
from cap.cli import _tfvars

SBD = Path(__file__).resolve().parents[1] / "config" / "customers" / "sbd-dewalt" / "customer.yaml"


@pytest.fixture(scope="module")
def cfg():
    return cap_config.load(SBD)


class R:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def session(policy, points):
    s = MagicMock()
    instances = {"items": [{"name": "other-db"},
                           {"name": "creative-studio-db-ab12", "settings": {"activationPolicy": policy}}]}
    series = {"timeSeries": [{"points": [{"value": {"int64Value": str(p)}} for p in points]}]} if points else {}
    s.get.side_effect = lambda url, timeout, params=None: R(series if "timeSeries" in url else instances)
    s.patch.return_value = R({"name": "op"})
    return s


def test_idle_rule():
    assert gcc_idle.idle_decision("ALWAYS", 0, 30)[0]
    assert not gcc_idle.idle_decision("ALWAYS", 3, 30)[0]
    assert not gcc_idle.idle_decision("NEVER", 0, 30)[0]


def test_stops_running_idle_database(cfg):
    s = session("ALWAYS", [])
    with patch.object(gcc_idle, "_session", return_value=s):
        out = gcc_idle.check_and_stop(cfg)
    assert out["action"] == "stopped" and out["instance"] == "creative-studio-db-ab12"
    assert s.patch.call_args.kwargs["json"] == {"settings": {"activationPolicy": "NEVER"}}
    params = next(c.kwargs["params"] for c in s.get.call_args_list if c.kwargs.get("params"))
    assert 'service_name="cstudio-be"' in params["filter"] and params["aggregation.alignmentPeriod"] == "1800s"


def test_keeps_busy_database(cfg):
    s = session("ALWAYS", [2, 5])
    with patch.object(gcc_idle, "_session", return_value=s):
        out = gcc_idle.check_and_stop(cfg)
    assert out["action"] == "none" and "7 backend request" in out["reason"]
    s.patch.assert_not_called()


def test_tfvars_enable_idle_stop_and_cleanup(cfg):
    v = _tfvars(cfg)
    assert v["gcc_idle_stop"] is True and v["container_images_keep"] == 5
    off = cfg.model_copy(deep=True)
    off.creative_studio.auto_schedule.idle_stop_minutes = 0
    assert _tfvars(off)["gcc_idle_stop"] is False
