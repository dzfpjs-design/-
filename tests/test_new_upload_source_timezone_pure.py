"""New upload registration defaults only; no existing source is reinterpreted.

These tests use the real registration/serialization path with an in-memory
recording session. They do not establish HTTP, worker or device execution.
MonitorCreate lives in the protected API module and remains outside this fix.
"""
from copy import deepcopy
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from generic_agents import services
from generic_agents.contracts import Manifest, digest
from generic_agents.models import Capability


class RecordingSession:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    def flush(self):
        for row in self.added:
            if isinstance(row, Capability) and row.id is None:
                row.id = "new-upload-source"


def request(**changes):
    return Manifest(**{
        "kind": "source", "namespace": "ordinary_upload", "name": "manual_records",
        "version": "1.0.0", "owner": "Synthetic contract test",
        "description": "Manually submitted records with an explicit business day",
        "allowed_agent_roles": ["perception", "data"],
        "execution": {"adapter": "upload", "side_effect": False},
        "data_policy": {"cloud_allowed": False, "external_allowed": False},
        **changes,
    })


def register(manifest):
    db = RecordingSession()
    principal = SimpleNamespace(user_id="actor", workspace_id="workspace", role="admin")
    return services.register(db, principal, manifest), db


def test_new_ordinary_upload_serializes_china_before_hash_without_mutating_request():
    manifest = request()
    original = manifest.model_dump(mode="json")
    row, db = register(manifest)

    assert row.data == {**original, "specification": {"timezone": "Asia/Shanghai"}}
    assert row.content_hash == digest(row.data)
    assert manifest.model_dump(mode="json") == original
    assert "timezone" not in manifest.specification
    assert db.added[-1].data == {"hash": row.content_hash}


@pytest.mark.parametrize("zone", ["UTC", "America/Los_Angeles", "Asia/Shanghai", None, ""])
def test_explicit_value_is_preserved_including_invalid_values_for_later_validation(zone):
    manifest = request(specification={"timezone": zone})
    original = manifest.model_dump(mode="json")
    row, _ = register(manifest)

    assert row.data == original
    assert row.data["specification"]["timezone"] == zone
    assert row.content_hash == digest(original)


@pytest.mark.parametrize("changes", [
    {"execution": {"adapter": "http", "endpoint": "https://example.invalid/intake"}},
    {"execution": {"adapter": "builtin", "handler": "numeric_rules"}},
    {"execution": {"adapter": "mqtt_source", "binding_id": "binding"}},
    {"specification": {"managed_instance": "instance"}},
    {"specification": {"simulation_environment_id": "environment"}},
    {"specification": {"source_manifest_hash": "a" * 64}},
    {"specification": {"timezone": "UTC", "mqtt_binding_id": "binding"}},
    {"kind": "specialist_model"},
    {"kind": "domain_package"},
])
def test_specialized_or_other_adapter_manifest_and_hash_are_unchanged(changes):
    manifest = request(**changes)
    original = manifest.model_dump(mode="json")
    row, _ = register(manifest)

    assert row.data == original
    assert row.content_hash == digest(original)


def test_registering_a_new_source_does_not_mutate_a_missing_zone_historical_value():
    historical = request().model_dump(mode="json")
    saved = deepcopy(historical)
    original_hash = digest(historical)

    manifest = Manifest.model_validate(historical)
    row, _ = register(manifest)

    assert row.data["specification"]["timezone"] == "Asia/Shanghai"
    assert historical == saved and digest(historical) == original_hash
    assert Manifest.model_validate(historical).model_dump(mode="json") == saved
    assert "timezone" not in historical["specification"]


@pytest.mark.parametrize("instant,china_day,utc_day", [
    ("2026-10-24T23:59:00Z", "2026-10-25", "2026-10-24"),
    ("2026-10-25T00:01:00Z", "2026-10-25", "2026-10-25"),
    ("2026-10-24T16:00:00Z", "2026-10-25", "2026-10-24"),
])
def test_new_default_and_explicit_utc_use_their_own_business_date(instant, china_day, utc_day):
    china, _ = register(request())
    utc, _ = register(request(specification={"timezone": "UTC"}))
    clock = services.timestamp(instant)

    assert str(clock.astimezone(ZoneInfo(china.data["specification"]["timezone"])).date()) == china_day
    assert str(clock.astimezone(ZoneInfo(utc.data["specification"]["timezone"])).date()) == utc_day
    assert clock.utcoffset().total_seconds() == 0
