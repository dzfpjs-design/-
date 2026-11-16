"""Read-only simulator receipt adaptation, not a real gateway/PG journey.

Root runs this standalone test source in an isolated harness without repository
conftest. The actual app/SQLite state machine is used. RECEIPT_SCHEMA is read
from its real public module as a literal, avoiding tool_recovery's unrelated DB
imports; no validator, receipt handler, state transition or response is mocked.
"""
import ast
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
import pytest

from generic_agents import device_simulator_v175 as simulator
from generic_agents.contracts import digest


TOKEN = "synthetic-platform-receipt-contract-v175"
DOCK = "sim-dock-01"
CAMERA = "sim-camera-01"
WEATHER = "sim-weather-01"


class Clock:
    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        return self.now


def auth(key=None):
    value = {"Authorization": "Bearer " + TOKEN}
    if key is not None:
        value["Idempotency-Key"] = key
    return value


@contextmanager
def opened(path, clock):
    with TestClient(simulator.create_app(path, TOKEN, clock=clock), raise_server_exceptions=False) as client:
        yield client


def public_receipt_schema():
    source = Path(simulator.__file__).with_name("tool_recovery.py")
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "RECEIPT_SCHEMA" for target in node.targets)]
    assert len(assignments) == 1, "The actual recovery contract must remain explicit"
    return ast.literal_eval(assignments[0].value)


def native(client, key):
    response = client.get("/receipts", headers=auth(key))
    assert response.status_code == 200, response.text
    return response.json()


def state(client, device_id=DOCK):
    response = client.get("/devices/" + device_id, headers=auth())
    assert response.status_code == 200, response.text
    return response.json()


def command(device_id, action, revision, arguments):
    # All four gateway input fields are deliberately present, including null on
    # reads. The expected hash is never calculated from Pydantic-filled defaults.
    return {"device_id": device_id, "action": action,
            "expected_revision": revision, "arguments": arguments}


def receipt_count(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT count(*) FROM receipts").fetchone()[0]


def check_envelope(value, body, key, status):
    Draft202012Validator(public_receipt_schema()).validate(value)
    assert value["idempotency_key"] == key
    assert value["input_hash"] == digest(body)
    assert value["status"] == status
    assert set(value) <= {"idempotency_key", "input_hash", "status", "output"}
    if "output" in value:
        output = value["output"]
        assert output["request_id"] == key
        assert output["device_id"] == body["device_id"]
        assert output["action"] == body["action"]
        assert output["simulation"] is True
        assert output["origin_kind"] == "simulated_equipment"
        assert output["field_verified"] is False


def minimum_terminal_output_schema():
    """Consumer safety oracle, not a claim about a registered platform tool.

    Root separately validates the actual registered manifest in the PG journey.
    These mandatory fields ensure an HTTP-200 acknowledgement cannot itself
    satisfy the terminal device-tool output contract.
    """
    return {"type": "object", "required": ["status", "simulation", "origin_kind", "field_verified",
                                               "request_id", "device_id", "action"],
            "properties": {"status": {"const": "succeeded"}, "simulation": {"const": True},
                           "origin_kind": {"const": "simulated_equipment"}, "field_verified": {"const": False},
                           "request_id": {"type": "string"}, "device_id": {"type": "string"},
                           "action": {"type": "string"}}}


def test_accepted_and_running_are_pending_not_terminal_tool_success(tmp_path):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    body = command(DOCK, "start_mission", 1, {
        "duration_seconds": 60, "speed_m_s": 5, "mission_name": "receipt wait",
    })
    terminal = Draft202012Validator(minimum_terminal_output_schema())
    with opened(path, clock) as client:
        response = client.post("/invoke", headers=auth("pending-flight"), json=body)
        assert response.status_code == 200
        accepted = response.json()
        assert accepted["status"] == "accepted"
        assert not terminal.is_valid(accepted)
        for _ in range(2):
            lookup = client.get("/platform-receipts", headers=auth("pending-flight"))
            assert lookup.status_code == 200, lookup.text
            check_envelope(lookup.json(), body, "pending-flight", "pending")
            original = native(client, "pending-flight")
            assert original["status"] in {"accepted", "running"}
            assert not terminal.is_valid(original)
        assert state(client)["state"]["active_request_id"] == "pending-flight"
        assert receipt_count(path) == 1


def test_terminal_lookup_after_lost_reply_and_restart_returns_original_marked_output(tmp_path):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    body = command(CAMERA, "set_ptz", 1, {"pan_degrees": -0.0, "tilt_degrees": 1.0, "zoom": 2})
    with opened(path, clock) as client:
        response = client.post("/invoke", headers=auth("ptz-original"), json=body)
        assert response.status_code == 200
        accepted_at = response.json()["accepted_at"]
    clock.now += 2
    with opened(path, clock) as client:
        # Recovery is exclusively GET: the original POST is never replayed.
        response = client.get("/platform-receipts", headers=auth("ptz-original"))
        assert response.status_code == 200, response.text
        value = response.json()
        check_envelope(value, body, "ptz-original", "succeeded")
        output = value["output"]
        assert output == native(client, "ptz-original")
        assert output["accepted_at"] == accepted_at
        assert output["result"]["field_observation_created"] is False
        assert output["result"]["artifacts"] == []
        Draft202012Validator(minimum_terminal_output_schema()).validate(output)
        revision = state(client, CAMERA)["revision"]
        for _ in range(2):
            repeated = client.get("/platform-receipts", headers=auth("ptz-original"))
            assert repeated.status_code == 200
            assert repeated.json() == value
            assert state(client, CAMERA)["revision"] == revision
    assert receipt_count(path) == 1


def test_control_command_with_all_four_fields_has_the_actual_gateway_hash(tmp_path):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    body = command(CAMERA, "set_ptz", 1, {"pan_degrees": -0.0, "tilt_degrees": 1.0, "zoom": 2})
    with opened(path, clock) as client:
        assert client.post("/invoke", headers=auth("four-fields"), json=body).status_code == 200
        clock.now += 2
        response = client.get("/platform-receipts", headers=auth("four-fields"),
                              params={"input_hash": "0" * 64, "idempotency_key": "unrelated"})
        assert response.status_code == 200, response.text
        check_envelope(response.json(), body, "four-fields", "succeeded")
        # No endpoint parameter may override the frozen input hash.
        assert response.json()["input_hash"] != "0" * 64
        assert response.json()["output"] == native(client, "four-fields")


@pytest.mark.parametrize("action", ["return_to_dock", "cancel_mission"])
def test_cancelled_original_is_never_reported_as_not_executed(tmp_path, action):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    original = command(DOCK, "start_mission", 1, {
        "duration_seconds": 60, "speed_m_s": 5, "mission_name": "cancel outcome",
    })
    with opened(path, clock) as client:
        assert client.post("/invoke", headers=auth("cancelled-original"), json=original).status_code == 200
        current = state(client)
        followup = command(DOCK, action, current["revision"], {})
        assert client.post("/invoke", headers=auth("return-original"), json=followup).status_code == 200
        old = native(client, "cancelled-original")
        assert old["status"] == "cancelled"
        before = receipt_count(path)
        denied = client.get("/platform-receipts", headers=auth("cancelled-original"))
        assert denied.status_code == 409, denied.text
        pending = client.get("/platform-receipts", headers=auth("return-original"))
        assert pending.status_code == 200
        check_envelope(pending.json(), followup, "return-original", "pending")
        clock.now += 3
        completed = client.get("/platform-receipts", headers=auth("return-original"))
        assert completed.status_code == 200
        check_envelope(completed.json(), followup, "return-original", "succeeded")
        assert completed.json()["output"]["recovery_of_request_id"] == "cancelled-original"
        assert native(client, "cancelled-original") == old
        assert client.get("/platform-receipts", headers=auth("cancelled-original")).status_code == 409
        assert receipt_count(path) == before


def test_failed_original_remains_visible_but_cannot_claim_no_execution(tmp_path):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    body = command(DOCK, "start_mission", 1, {
        "duration_seconds": 60, "speed_m_s": 5, "mission_name": "fault outcome",
    })
    with opened(path, clock) as client:
        assert client.post("/invoke", headers=auth("failed-original"), json=body).status_code == 200
        current = state(client)
        assert client.post("/lab/state", headers=auth("fault-input"), json={
            "device_id": DOCK, "expected_revision": current["revision"],
            "fault": "motor_fault", "telemetry": {}, "confirm_safe_idle": False,
        }).status_code == 200
        old = native(client, "failed-original")
        assert old["status"] == "failed"
        before = receipt_count(path)
        response = client.get("/platform-receipts", headers=auth("failed-original"))
        assert response.status_code == 409, response.text
        assert native(client, "failed-original") == old
        assert state(client)["state"]["active_request_id"] == "failed-original"
        assert receipt_count(path) == before


def test_lab_injection_is_not_packaged_as_a_device_tool_success(tmp_path):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    with opened(path, clock) as client:
        current = state(client)
        response = client.post("/lab/state", headers=auth("laboratory-only"), json={
            "device_id": DOCK, "expected_revision": current["revision"], "fault": None,
            "telemetry": {"battery_percent": 80}, "confirm_safe_idle": False,
        })
        assert response.status_code == 200
        old = native(client, "laboratory-only")
        assert old["action"] == "lab_change"
        assert client.get("/platform-receipts", headers=auth("laboratory-only")).status_code == 409
        assert native(client, "laboratory-only") == old
        assert receipt_count(path) == 1


def test_missing_key_cannot_be_promoted_to_permanent_non_execution(tmp_path):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    with opened(path, clock) as client:
        before = state(client)
        response = client.get("/platform-receipts", headers=auth("never-issued"))
        assert response.status_code == 404, response.text
        assert state(client) == before
        assert receipt_count(path) == 0


@pytest.mark.parametrize("headers_value,expected", [({}, 401), (auth(), 422), (auth("bad key"), 422)])
def test_recovery_requires_authentication_and_a_valid_original_header(tmp_path, headers_value, expected):
    path, clock = tmp_path / "ledger.sqlite3", Clock()
    with opened(path, clock) as client:
        response = client.get("/platform-receipts", headers=headers_value)
        assert response.status_code == expected
        assert receipt_count(path) == 0


@pytest.mark.parametrize('field,value', [
    ('simulation', 1), ('simulation', False), ('field_verified', 0),
    ('field_verified', True), ('origin_kind', 'actual_equipment'),
    ('request_id', 'different-original'), ('device_id', DOCK), ('action', 'start_mission'),
])
def test_platform_receipt_rejects_tampered_binding_and_bool_integer_aliases(tmp_path, field, value):
    """Corrupt only the disposable SQLite evidence; never forge a successful run."""
    path, clock = tmp_path / 'ledger.sqlite3', Clock()
    body = command(CAMERA, 'set_ptz', 1, {'pan_degrees': 0, 'tilt_degrees': 1, 'zoom': 2})
    with opened(path, clock) as client:
        assert client.post('/invoke', headers=auth('checked-original'), json=body).status_code == 200
        clock.now += 2
        assert client.get('/platform-receipts', headers=auth('checked-original')).status_code == 200
        with sqlite3.connect(path) as db:
            original = db.execute('SELECT data FROM receipts WHERE request_id=?', ('checked-original',)).fetchone()[0]
            changed = json.loads(original)
            changed[field] = value
            db.execute('UPDATE receipts SET data=? WHERE request_id=?',
                       (json.dumps(changed, ensure_ascii=False), 'checked-original'))
        rejected = client.get('/platform-receipts', headers=auth('checked-original'))
        assert rejected.status_code == 409, rejected.text
        assert 'output' not in rejected.json()
        with sqlite3.connect(path) as db:
            db.execute('UPDATE receipts SET data=? WHERE request_id=?', (original, 'checked-original'))
        restored = client.get('/platform-receipts', headers=auth('checked-original'))
        assert restored.status_code == 200, restored.text
        check_envelope(restored.json(), body, 'checked-original', 'succeeded')
        assert receipt_count(path) == 1


def test_platform_receipt_checks_original_body_hash_before_exposing_success(tmp_path):
    path, clock = tmp_path / 'ledger.sqlite3', Clock()
    body = command(CAMERA, 'set_ptz', 1, {'pan_degrees': -0.0, 'tilt_degrees': 1.0, 'zoom': 2})
    with opened(path, clock) as client:
        assert client.post('/invoke', headers=auth('hash-original'), json=body).status_code == 200
        clock.now += 2
        assert client.get('/platform-receipts', headers=auth('hash-original')).status_code == 200
        with sqlite3.connect(path) as db:
            original_body, original_hash = db.execute(
                'SELECT body, request_hash FROM receipts WHERE request_id=?', ('hash-original',)).fetchone()
            changed = json.loads(original_body)
            # Equal Python numeric values do not preserve the gateway's raw
            # float/negative-zero JSON input digest.
            changed['arguments']['pan_degrees'] = 0
            db.execute('UPDATE receipts SET body=? WHERE request_id=?',
                       (json.dumps(changed, ensure_ascii=False), 'hash-original'))
        assert client.get('/platform-receipts', headers=auth('hash-original')).status_code == 409
        with sqlite3.connect(path) as db:
            db.execute('UPDATE receipts SET body=?,request_hash=? WHERE request_id=?',
                       (original_body, '0' * 64, 'hash-original'))
        assert client.get('/platform-receipts', headers=auth('hash-original')).status_code == 409
        with sqlite3.connect(path) as db:
            db.execute('UPDATE receipts SET request_hash=? WHERE request_id=?', (original_hash, 'hash-original'))
        restored = client.get('/platform-receipts', headers=auth('hash-original'))
        assert restored.status_code == 200, restored.text
        check_envelope(restored.json(), body, 'hash-original', 'succeeded')
        assert receipt_count(path) == 1


@pytest.mark.parametrize('device_id', [DOCK, CAMERA, WEATHER])
@pytest.mark.parametrize('action', ['get_status', 'read_telemetry'])
def test_saved_read_only_success_is_recovered_verbatim_without_resampling_or_posting(tmp_path, monkeypatch, device_id, action):
    path, clock = tmp_path / 'ledger.sqlite3', Clock()
    key = 'original-read-' + device_id + '-' + action
    body = command(device_id, action, None, {})
    with opened(path, clock) as client:
        response = client.post('/invoke', headers=auth(key), json=body)
        assert response.status_code == 200, response.text
        original = response.json()
        assert original['status'] == 'succeeded'
        with sqlite3.connect(path) as db:
            before = db.execute('SELECT request_id,request_hash,device_id,body,status,data,due_at FROM receipts').fetchall()
        def no_new_command(*args, **kwargs):
            pytest.fail('Read-only receipt recovery must not invoke or resample the device')
        monkeypatch.setattr(simulator.DeviceLedger, 'invoke', no_new_command)
        for _ in range(2):
            clock.now += 60
            recovered = client.get('/platform-receipts', headers=auth(key))
            assert recovered.status_code == 200, recovered.text
            value = recovered.json()
            check_envelope(value, body, key, 'succeeded')
            assert value['output'] == original
            assert value['output']['result']['sampled_at'] == original['result']['sampled_at']
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT request_id,request_hash,device_id,body,status,data,due_at FROM receipts').fetchall() == before
    # A fresh server/SQLite connection recovers the same original record. The
    # patched invoke remains forbidden, so restart cannot hide a new command.
    clock.now += 600
    with opened(path, clock) as client:
        recovered = client.get('/platform-receipts', headers=auth(key))
        assert recovered.status_code == 200, recovered.text
        check_envelope(recovered.json(), body, key, 'succeeded')
        assert recovered.json()['output'] == original
        assert receipt_count(path) == 1


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'unknown'])
def test_read_only_unsuccessful_or_unknown_saved_receipt_does_not_become_not_executed(tmp_path, status):
    path, clock = tmp_path / 'ledger.sqlite3', Clock()
    body = command(WEATHER, 'read_telemetry', None, {})
    with opened(path, clock) as client:
        assert client.post('/invoke', headers=auth('read-status'), json=body).status_code == 200
        with sqlite3.connect(path) as db:
            original = json.loads(db.execute('SELECT data FROM receipts WHERE request_id=?', ('read-status',)).fetchone()[0])
            original['status'] = status
            stored = json.dumps(original, ensure_ascii=False)
            db.execute('UPDATE receipts SET status=?,data=? WHERE request_id=?', (status, stored, 'read-status'))
        response = client.get('/platform-receipts', headers=auth('read-status'))
        assert response.status_code == 409
        with sqlite3.connect(path) as db:
            row = db.execute('SELECT status,data FROM receipts WHERE request_id=?', ('read-status',)).fetchone()
        assert row == (status, stored)
        assert receipt_count(path) == 1


@pytest.mark.parametrize('field,value', [('simulation', 1), ('field_verified', 0),
    ('device_id', DOCK), ('action', 'get_status'), ('request_id', 'another-key'), ('status', 'running')])
def test_read_only_recovery_keeps_original_identity_typed_marks_and_status_checks(tmp_path, field, value):
    path, clock = tmp_path / 'ledger.sqlite3', Clock()
    body = command(WEATHER, 'read_telemetry', None, {})
    with opened(path, clock) as client:
        assert client.post('/invoke', headers=auth('bound-read'), json=body).status_code == 200
        with sqlite3.connect(path) as db:
            data = json.loads(db.execute('SELECT data FROM receipts WHERE request_id=?', ('bound-read',)).fetchone()[0])
            data[field] = value
            db.execute('UPDATE receipts SET data=? WHERE request_id=?', (json.dumps(data), 'bound-read'))
        assert client.get('/platform-receipts', headers=auth('bound-read')).status_code == 409
        assert receipt_count(path) == 1


def test_read_only_recovery_checks_original_four_key_body_hash_and_saved_device(tmp_path):
    path, clock = tmp_path / 'ledger.sqlite3', Clock()
    body = command(WEATHER, 'read_telemetry', None, {})
    with opened(path, clock) as client:
        assert client.post('/invoke', headers=auth('body-read'), json=body).status_code == 200
        with sqlite3.connect(path) as db:
            original_body, original_hash = db.execute(
                'SELECT body,request_hash FROM receipts WHERE request_id=?', ('body-read',)).fetchone()
            changed = json.loads(original_body); changed['expected_revision'] = False
            db.execute('UPDATE receipts SET body=? WHERE request_id=?', (json.dumps(changed), 'body-read'))
        assert client.get('/platform-receipts', headers=auth('body-read')).status_code == 409
        with sqlite3.connect(path) as db:
            db.execute('UPDATE receipts SET body=?,device_id=? WHERE request_id=?', (original_body, DOCK, 'body-read'))
        assert client.get('/platform-receipts', headers=auth('body-read')).status_code == 409
        with sqlite3.connect(path) as db:
            db.execute('UPDATE receipts SET device_id=? WHERE request_id=?', (WEATHER, 'body-read'))
        recovered = client.get('/platform-receipts', headers=auth('body-read'))
        assert recovered.status_code == 200
        assert recovered.json()['input_hash'] == original_hash == digest(body)
        assert receipt_count(path) == 1
