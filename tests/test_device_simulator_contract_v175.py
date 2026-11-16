"""Isolated simulator contract tests; no platform DB, model, or hardware.

These use the actual standalone app, SQLite ledger and injected simulation clock.
They do not replace the state machine, transport validators, or business gates.
Root executes this file in its isolated harness without repository conftest.
Writing these tests is not evidence that an actual device was operated.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import sqlite3
from threading import Barrier

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from generic_agents.device_simulator_v175 import Command, DeviceLedger, create_app


TOKEN = "synthetic-simulator-contract-token-v175"
DOCK = "sim-dock-01"
CAMERA = "sim-camera-01"
WEATHER = "sim-weather-01"


class Clock:
    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def headers(key=None):
    result = {"Authorization": "Bearer " + TOKEN}
    if key is not None:
        result["Idempotency-Key"] = key
    return result


@contextmanager
def opened(path, clock):
    with TestClient(create_app(path, TOKEN, clock=clock), raise_server_exceptions=False) as client:
        yield client


def device(client, device_id=DOCK):
    response = client.get("/devices/" + device_id, headers=headers())
    assert response.status_code == 200, response.text
    return response.json()


def invoke(client, key, device_id, action, arguments=None, revision=None):
    body = {"device_id": device_id, "action": action, "arguments": arguments or {}}
    if revision is not None:
        body["expected_revision"] = revision
    return client.post("/invoke", json=body, headers=headers(key))


def start(client, key, duration=10):
    current = device(client)
    return invoke(client, key, DOCK, "start_mission", {
        "duration_seconds": duration, "speed_m_s": 5, "mission_name": "bounded laboratory mission",
    }, current["revision"])


def lab(client, key, *, device_id=DOCK, fault=None, telemetry=None, confirm=False):
    current = device(client, device_id)
    return client.post("/lab/state", json={
        "device_id": device_id, "expected_revision": current["revision"],
        "fault": fault, "telemetry": telemetry or {}, "confirm_safe_idle": confirm,
    }, headers=headers(key))


def receipt(client, key):
    response = client.get("/receipts", headers=headers(key))
    assert response.status_code == 200, response.text
    return response.json()


def stored_receipts(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT request_id,request_hash,device_id,body,status,data,due_at FROM receipts ORDER BY request_id").fetchall()


def assert_simulated(value):
    assert value["simulation"] is True
    assert value["origin_kind"] == "simulated_equipment"
    assert value["field_verified"] is False


def test_status_and_procurement_requirements_do_not_claim_field_capability(tmp_path):
    clock = Clock()
    with opened(tmp_path / "ledger.sqlite3", clock) as client:
        response = client.get("/devices", headers=headers())
        assert response.status_code == 200
        value = response.json()
        assert_simulated(value)
        assert {row["id"] for row in value["devices"]} == {DOCK, CAMERA, WEATHER}
        for row in value["devices"]:
            assert_simulated(row)
            assert row["physical_device_connected"] is False
            assert row["time_semantics"] == "simulator_sample_time"
        dock = next(row for row in value["devices"] if row["id"] == DOCK)
        requirements = dock["profile"]["procurement_requirements"]
        for name in ("flight_time_minutes", "maximum_horizontal_speed_m_s", "wind_resistance_m_s", "landing_wind_m_s"):
            assert requirements[name]["operator"] == ">="
        assert dock["profile"]["simulation_assumptions"]["speed_m_s_max"] < requirements["maximum_horizontal_speed_m_s"]["value"]
        assert device(client, CAMERA)["profile"]["simulation_assumptions"]["image_generation"] is False


@pytest.mark.parametrize("authorization", [None, "Bearer incorrect", "Bearer \u00e9"])
def test_mutation_requires_the_independent_simulator_secret(tmp_path, authorization):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    with opened(path, clock) as client:
        before = device(client)
        request_headers = {"Idempotency-Key": "unauthorized"}
        if authorization is not None:
            request_headers["Authorization"] = authorization.encode("utf-8")
        response = client.post("/invoke", headers=request_headers, json={
            "device_id": DOCK, "action": "start_mission", "expected_revision": 1,
            "arguments": {"duration_seconds": 10, "speed_m_s": 5, "mission_name": "not authorized"},
        })
        assert response.status_code == 401
        assert device(client) == before
        assert stored_receipts(path) == []


def test_lost_reply_restart_and_same_key_replay_do_not_execute_twice(tmp_path):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    body = {"device_id": CAMERA, "action": "set_ptz", "expected_revision": 1,
            "arguments": {"pan_degrees": 12, "tilt_degrees": -7, "zoom": 3}}
    with opened(path, clock) as client:
        response = client.post("/invoke", headers=headers("ptz-once"), json=body)
        assert response.status_code == 200
        accepted = response.json()
        assert accepted["status"] == "accepted"
        assert accepted["completion_requires_receipt"] is True
    # Discard the response as a caller might, then reopen the same durable file.
    with opened(path, clock) as client:
        current = receipt(client, "ptz-once")
        assert current["status"] in {"accepted", "running"}
        assert current["accepted_at"] == accepted["accepted_at"]
        assert device(client, CAMERA)["state"]["phase"] == "moving"
        clock.advance(2)
        completed = receipt(client, "ptz-once")
        assert completed["status"] == "succeeded"
        assert completed["result"]["artifacts"] == []
        assert completed["result"]["field_observation_created"] is False
        state = device(client, CAMERA)
        assert (state["state"]["pan_degrees"], state["state"]["tilt_degrees"], state["state"]["zoom"]) == (12, -7, 3)
        revision = state["revision"]
        replay = client.post("/invoke", headers=headers("ptz-once"), json=body)
        assert replay.status_code == 200
        assert replay.json() == completed
        assert device(client, CAMERA)["revision"] == revision
    with opened(path, clock) as client:
        assert receipt(client, "ptz-once") == completed
        assert device(client, CAMERA)["revision"] == revision
    assert len(stored_receipts(path)) == 1


@pytest.mark.parametrize("changed", ["other_device", "float_alias", "zero_sign"])
def test_same_key_with_different_device_or_typed_arguments_is_conflict(tmp_path, changed):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    arguments = {"pan_degrees": -0.0, "tilt_degrees": 1, "zoom": 2}
    body = {"device_id": CAMERA, "action": "set_ptz", "expected_revision": 1, "arguments": arguments}
    with opened(path, clock) as client:
        assert client.post("/invoke", headers=headers("fixed-content"), json=body).status_code == 200
        before = stored_receipts(path)
        replacement = json.loads(json.dumps(body))
        if changed == "other_device":
            replacement["device_id"] = DOCK
        elif changed == "float_alias":
            replacement["arguments"]["tilt_degrees"] = 1.0
        else:
            replacement["arguments"]["pan_degrees"] = 0.0
        assert client.post("/invoke", headers=headers("fixed-content"), json=replacement).status_code == 409
        assert stored_receipts(path) == before


def test_two_connections_cannot_reserve_one_device_twice(tmp_path):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    ledgers = [DeviceLedger(path, clock), DeviceLedger(path, clock)]
    rendezvous = Barrier(2)

    def contender(index):
        rendezvous.wait(timeout=5)
        try:
            result = ledgers[index].invoke("parallel-" + str(index), Command(
                device_id=DOCK, action="start_mission", expected_revision=1,
                arguments={"duration_seconds": 10, "speed_m_s": 5, "mission_name": "reservation"},
            ))
            return 200, result
        except HTTPException as error:
            return error.status_code, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(contender, range(2)))
    assert sorted(status for status, _ in outcomes) == [200, 409]
    with opened(path, clock) as client:
        state = device(client)
        assert state["state"]["phase"] == "flying"
        winner = next(value for status, value in outcomes if status == 200)
        assert state["state"]["active_request_id"] == winner["request_id"]
    assert len(stored_receipts(path)) == 1


@pytest.mark.parametrize("action", ["return_to_dock", "cancel_mission"])
def test_return_and_cancel_keep_occupancy_until_the_followup_receipt(tmp_path, action):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    with opened(path, clock) as client:
        assert start(client, "flight", 100).status_code == 200
        current = device(client)
        response = invoke(client, "return", DOCK, action, revision=current["revision"])
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"
        assert response.json()["recovery_of_request_id"] == "flight"
        original = receipt(client, "flight")
        assert original["status"] == "cancelled"
        assert device(client)["state"]["phase"] == "returning"
        assert start(client, "too-early").status_code == 409
        clock.advance(3)
        assert receipt(client, "return")["status"] == "succeeded"
        assert device(client)["state"]["phase"] == "idle"
        assert receipt(client, "flight") == original


def test_overdue_offline_reconnect_keeps_recovery_occupancy_across_restart(tmp_path):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    with opened(path, clock) as client:
        assert start(client, "disconnected-flight", 10).status_code == 200
        clock.advance(1)
        assert lab(client, "disconnect", fault="offline").status_code == 200
        clock.advance(20)
        assert receipt(client, "disconnected-flight")["status"] != "succeeded"
        assert device(client)["state"]["active_request_id"] == "disconnected-flight"
    with opened(path, clock) as client:
        assert device(client)["state"]["online"] is False
        assert lab(client, "reconnect").status_code == 200
        held = device(client)
        assert held["state"]["phase"] == "recovery_required"
        assert held["state"]["active_request_id"] == "disconnected-flight"
        failed = receipt(client, "disconnected-flight")
        assert failed["status"] == "failed"
        assert start(client, "must-not-reuse").status_code == 409
    with opened(path, clock) as client:
        current = device(client)
        recovery = invoke(client, "explicit-return", DOCK, "return_to_dock", revision=current["revision"])
        assert recovery.status_code == 200
        assert recovery.json()["recovery_of_request_id"] == "disconnected-flight"
        assert start(client, "still-returning").status_code == 409
        clock.advance(3)
        assert receipt(client, "explicit-return")["status"] == "succeeded"
        assert device(client)["state"]["active_request_id"] is None
        assert receipt(client, "disconnected-flight") == failed


@pytest.mark.parametrize("fault", ["motor_fault", "sensor_fault"])
def test_clearing_fault_is_not_a_safe_return_and_does_not_rewrite_failure(tmp_path, fault):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    with opened(path, clock) as client:
        assert start(client, "faulted-flight", 100).status_code == 200
        assert lab(client, "inject-fault", fault=fault).status_code == 200
        failed = receipt(client, "faulted-flight")
        assert failed["status"] == "failed"
        assert lab(client, "repair-only").status_code == 200
        state = device(client)
        assert state["state"]["fault"] is None
        assert state["state"]["phase"] == "recovery_required"
        assert state["state"]["active_request_id"] == "faulted-flight"
        assert start(client, "no-implicit-return").status_code == 409
        assert receipt(client, "faulted-flight") == failed


def test_explicit_lab_safe_idle_is_separate_durable_simulated_evidence(tmp_path):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    with opened(path, clock) as client:
        assert start(client, "safe-recovery-flight", 100).status_code == 200
        assert lab(client, "fault", fault="motor_fault").status_code == 200
        failed = receipt(client, "safe-recovery-flight")
        current = device(client)
        body = {"device_id": DOCK, "expected_revision": current["revision"], "fault": None,
                "telemetry": {}, "confirm_safe_idle": True}
        response = client.post("/lab/state", headers=headers("safe-confirmation"), json=body)
        assert response.status_code == 200
        confirmation = response.json()
        assert_simulated(confirmation)
        assert confirmation["simulation_safe_idle_confirmed_for"] == "safe-recovery-flight"
        assert device(client)["state"]["active_request_id"] is None
        assert receipt(client, "safe-recovery-flight") == failed
    with opened(path, clock) as client:
        before = device(client)
        replay = client.post("/lab/state", headers=headers("safe-confirmation"), json=body)
        assert replay.status_code == 200
        assert replay.json() == confirmation
        assert device(client) == before
        assert receipt(client, "safe-recovery-flight") == failed


def test_safe_idle_cannot_be_used_on_an_ordinary_inflight_action(tmp_path):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    with opened(path, clock) as client:
        assert start(client, "still-flying", 100).status_code == 200
        before = device(client)
        stored = stored_receipts(path)
        assert lab(client, "not-a-recovery", confirm=True).status_code == 409
        assert device(client) == before
        assert stored_receipts(path) == stored


@pytest.mark.parametrize("replacement", [True, 1.0, 0, -1])
def test_action_revision_is_a_strict_positive_integer(tmp_path, replacement):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    with opened(path, clock) as client:
        response = invoke(client, "bad-revision", CAMERA, "set_ptz", {
            "pan_degrees": 0, "tilt_degrees": 0, "zoom": 1,
        }, replacement)
        assert response.status_code == 422
        assert stored_receipts(path) == []
        assert device(client, CAMERA)["state"]["phase"] == "idle"


@pytest.mark.parametrize("field,value", [
    ("duration_seconds", True), ("duration_seconds", 1.0), ("duration_seconds", 0),
    ("duration_seconds", 3241), ("duration_seconds", 2 ** 4096),
    ("speed_m_s", True), ("speed_m_s", 15.01), ("speed_m_s", float("nan")),
    ("speed_m_s", float("inf")), ("mission_name", "   "),
])
def test_invalid_inputs_return_422_without_allocating_action_or_changing_device(tmp_path, field, value):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    with opened(path, clock) as client:
        before = device(client)
        arguments = {"duration_seconds": 10, "speed_m_s": 5, "mission_name": "invalid input"}
        arguments[field] = value
        body = {"device_id": DOCK, "action": "start_mission", "expected_revision": before["revision"], "arguments": arguments}
        # Send the actual malformed/nonfinite wire; do not let the test client
        # reject it before the service's finite-JSON boundary is exercised.
        response = client.post("/invoke", headers={**headers("invalid-input"), "Content-Type": "application/json"},
                               content=json.dumps(body, allow_nan=True))
        assert response.status_code == 422, response.text
        assert stored_receipts(path) == []
        assert device(client) == before


def test_missing_receipt_does_not_invent_success_or_a_replacement_request(tmp_path):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    with opened(path, clock) as client:
        before = device(client)
        response = client.get("/receipts", headers=headers("never-sent"))
        assert response.status_code == 404
        assert device(client) == before
        assert stored_receipts(path) == []


def test_weather_values_are_explicit_lab_inputs_not_field_observations(tmp_path):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    with opened(path, clock) as client:
        changed = lab(client, "weather-fixture", device_id=WEATHER,
                      telemetry={"temperature_c": 30, "humidity_percent": 63, "wind_m_s": 4, "pressure_hpa": 1001})
        assert changed.status_code == 200
        response = invoke(client, "weather-read", WEATHER, "read_telemetry")
        assert response.status_code == 200
        value = response.json()
        assert_simulated(value)
        assert_simulated(value["result"])
        assert value["result"]["state"]["temperature_c"] == 30
        assert value["result"]["physical_device_connected"] is False
        assert value["result"]["profile"]["simulation_assumptions"]["values_are_lab_inputs"] is True
