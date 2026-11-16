"""A durable, loopback-only equipment simulator, consumed by the HTTP gateway.

This is an external test dependency. It never controls hardware, makes images,
or asserts a field observation. Procurement requirements and chosen simulation
limits remain separate. Run with an explicit state file and bearer secret.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Annotated, Callable, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt


SOURCE_SHA256 = "1b9d0d4b38cda7aac4c822ca8691a2b369eb05b60265deccc6084747e91a7c37"
MARK = {"simulation": True, "origin_kind": "simulated_equipment", "field_verified": False}
KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
READ_ACTIONS = {"get_status", "read_telemetry"}
TERMINAL = {"succeeded", "failed", "cancelled"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _request_hash(body):
    try:
        return hashlib.sha256(_json(body).encode()).hexdigest()
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(422, "参数必须是有限数值与有效JSON结构") from None


def _utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _profiles():
    source = {"document_sha256": SOURCE_SHA256, "reference_system": "OOXML table/row, one-based"}
    return [
        {"id": "sim-dock-01", "kind": "drone_dock", "name": "模拟固定机库与无人机",
         "source": {**source, "table": 10, "row": 5},
         "procurement_requirements": {
             "flight_time_minutes": {"operator": ">=", "value": 54},
             "maximum_horizontal_speed_m_s": {"operator": ">=", "value": 21},
             "wind_resistance_m_s": {"operator": ">=", "value": 12},
             "landing_wind_m_s": {"operator": ">=", "value": 12},
             "drone_operating_temperature_c": [-20, 50],
             "dock_protection": "IP56 or better", "drone_protection": "IP55 or better"},
         "simulation_assumptions": {"mission_duration_seconds_max": 3240, "speed_m_s_max": 15,
                                    "dispatch_wind_m_s_max": 10, "return_seconds": 2,
                                    "initial_battery_percent": 100, "minimum_dispatch_battery_percent": 25},
         "actions": ["get_status", "read_telemetry", "start_mission", "return_to_dock", "cancel_mission"]},
        {"id": "sim-camera-01", "kind": "ptz_camera", "name": "模拟高清云台摄像头",
         "source": {**source, "table": 14, "row": 4},
         "procurement_requirements": {"maximum_resolution_pixels": [2688, 1520],
                                      "focal_length_mm": [4.8, 160], "optical_zoom": 33,
                                      "operating_temperature_c": [-30, 60], "protection": "IP66"},
         "simulation_assumptions": {"pan_degrees": [-180, 180], "tilt_degrees": [-90, 90],
                                    "zoom_range": [1, 33], "movement_seconds": 1,
                                    "image_generation": False, "live_video_source_is_separate": True},
         "actions": ["get_status", "read_telemetry", "set_ptz"]},
        {"id": "sim-weather-01", "kind": "weather_station", "name": "模拟环境监测站",
         "source": {**source, "table": 14, "row": 9},
         "procurement_requirements": {"temperature_c": {"range": [-50, 100], "resolution": 0.1, "accuracy": 0.5},
                                      "humidity_percent": {"range": [0, 100], "resolution": 0.1, "accuracy": 3},
                                      "wind_m_s": {"range": [0, 75], "resolution": 0.1},
                                      "pressure_hpa": {"range": [500, 1100], "resolution": 0.1}},
         "simulation_assumptions": {"initial_temperature_c": 25, "initial_humidity_percent": 70,
                                    "initial_wind_m_s": 2, "initial_pressure_hpa": 1005,
                                    "values_are_lab_inputs": True},
         "actions": ["get_status", "read_telemetry"]},
    ]


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    device_id: str = Field(min_length=1, max_length=64)
    action: Literal["get_status", "read_telemetry", "start_mission", "return_to_dock", "cancel_mission", "set_ptz"]
    expected_revision: Annotated[StrictInt, Field(ge=1)] | None = None
    arguments: dict = Field(default_factory=dict, max_length=8)


class LabChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    device_id: str = Field(min_length=1, max_length=64)
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    fault: Literal["offline", "motor_fault", "sensor_fault"] | None = None
    telemetry: dict = Field(default_factory=dict, max_length=5)
    confirm_safe_idle: bool = False


class DeviceLedger:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock
        if self.path.is_symlink():
            raise ValueError("模拟器状态文件不能为符号链接")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS simulator_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO simulator_meta VALUES ('schema_version', '1')")
            if db.execute("SELECT value FROM simulator_meta WHERE key='schema_version'").fetchone()[0] != '1':
                raise ValueError("不支持的模拟器状态版本")
            db.execute("CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, profile TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS receipts (request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, device_id TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL, due_at REAL)")
            for profile in _profiles():
                state = {"phase": "idle", "online": True, "fault": None, "active_request_id": None,
                         "battery_percent": 100, "pan_degrees": 0, "tilt_degrees": 0, "zoom": 1,
                         "temperature_c": 25, "humidity_percent": 70, "wind_m_s": 2, "pressure_hpa": 1005}
                db.execute("INSERT OR IGNORE INTO devices VALUES (?,?,?,1)", (profile["id"], _json(profile), _json(state)))
        os.chmod(self.path, 0o600)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _device(self, db, device_id):
        row = db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "模拟设备不存在")
        return dict(row), json.loads(row["profile"]), json.loads(row["state"])

    def _save_device(self, db, row, state):
        row["revision"] += 1
        row["state"] = _json(state)
        db.execute("UPDATE devices SET state=?,revision=? WHERE id=?", (row["state"], row["revision"], row["id"]))

    def _view(self, row, profile, state):
        return {**MARK, "namespace": "lab-public-data", "id": row["id"], "revision": row["revision"],
                "profile": profile, "state": state, "sampled_at": _utc(self.clock()),
                "time_semantics": "simulator_sample_time", "physical_device_connected": False}

    def _store_receipt(self, db, key, body, data, status, due_at=None):
        db.execute("INSERT INTO receipts VALUES (?,?,?,?,?,?,?)",
                   (key, hashlib.sha256(_json(body).encode()).hexdigest(), body["device_id"], _json(body), status, _json(data), due_at))

    def _update_receipt(self, db, key, status, result):
        row = db.execute("SELECT * FROM receipts WHERE request_id=?", (key,)).fetchone()
        data = json.loads(row["data"])
        data.update(status=status, updated_at=_utc(self.clock()), result=result)
        db.execute("UPDATE receipts SET status=?,data=?,due_at=NULL WHERE request_id=?", (status, _json(data), key))

    def _advance(self, db, device_id):
        row, profile, state = self._device(db, device_id)
        key = state["active_request_id"]
        if not key:
            return row, profile, state
        receipt = db.execute("SELECT * FROM receipts WHERE request_id=?", (key,)).fetchone()
        if receipt is not None and receipt["status"] in TERMINAL and state["phase"] == "recovery_required":
            return row, profile, state
        if receipt is None or receipt["status"] in TERMINAL:
            raise HTTPException(409, "模拟设备占用与回执不一致，请保留现场并检查台账")
        if not state["online"]:
            # Offline does not manufacture successful completion or free occupancy.
            return row, profile, state
        if state["fault"]:
            self._update_receipt(db, key, "failed", {"reason": state["fault"], "simulated": True})
            state.update(phase="recovery_required")
            self._save_device(db, row, state)
        elif receipt["due_at"] is not None and self.clock() >= receipt["due_at"]:
            body = json.loads(receipt["body"])
            if body.get("action") == "set_ptz":
                state.update(body["arguments"])
            if body.get("action") == "start_mission":
                duration = body["arguments"]["duration_seconds"]
                state["battery_percent"] = max(0, round(state["battery_percent"] - duration / 3240 * 80, 2))
            state.update(phase="idle", active_request_id=None)
            self._save_device(db, row, state)
            self._update_receipt(db, key, "succeeded", {"simulated": True, "device_revision": row["revision"],
                                                       "artifacts": [], "field_observation_created": False})
        elif receipt["status"] == "accepted":
            data = json.loads(receipt["data"])
            data.update(status="running", updated_at=_utc(self.clock()))
            db.execute("UPDATE receipts SET status='running',data=? WHERE request_id=?", (_json(data), key))
        return row, profile, state

    def list_devices(self):
        with self.transaction() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM devices ORDER BY id")]
            return [self._view(*self._advance(db, device_id)) for device_id in ids]

    def device(self, device_id):
        with self.transaction() as db:
            return self._view(*self._advance(db, device_id))

    @staticmethod
    def _number(arguments, name, low, high, *, integer=False):
        value = arguments.get(name)
        if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)) or (integer and type(value) is not int) or not low <= value <= high:
            raise HTTPException(422, f"{name}必须在{low}至{high}之间，且符合数值类型")
        return value

    def invoke(self, key, command: Command):
        body = command.model_dump()
        request_hash = _request_hash(body)
        with self.transaction() as db:
            old = db.execute("SELECT * FROM receipts WHERE request_id=?", (key,)).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise HTTPException(409, "原请求号已绑定其他内容，请先核对原回执")
                self._advance(db, old["device_id"])
                return json.loads(db.execute("SELECT data FROM receipts WHERE request_id=?", (key,)).fetchone()[0])
            row, profile, state = self._advance(db, command.device_id)
            if command.action not in profile["actions"]:
                raise HTTPException(422, "该模拟设备不支持此动作")
            if command.action not in READ_ACTIONS and command.expected_revision != row["revision"]:
                raise HTTPException(409, "设备状态已变化，请读取最新状态后重新确认")
            if not state["online"]:
                raise HTTPException(503, "模拟设备离线；既有动作保持占用，不能凭超时重发")
            if state["fault"] and command.action not in READ_ACTIONS:
                raise HTTPException(409, "模拟设备故障，需先处理故障")
            arguments = command.arguments
            status, due_at, result = "succeeded", None, None
            if command.action in READ_ACTIONS:
                if arguments:
                    raise HTTPException(422, "读取状态不接受额外动作参数")
                result = self._view(row, profile, state)
            elif command.action == "start_mission":
                if set(arguments) != {"duration_seconds", "speed_m_s", "mission_name"}:
                    raise HTTPException(422, "需提供飞行时长、速度和任务名称；航线与区域由专用规划器处理")
                duration = self._number(arguments, "duration_seconds", 1, 3240, integer=True)
                self._number(arguments, "speed_m_s", 0.1, 15)
                if not isinstance(arguments["mission_name"], str) or not 1 <= len(arguments["mission_name"].strip()) <= 120:
                    raise HTTPException(422, "任务名称无效")
                if state["active_request_id"]:
                    raise HTTPException(409, "设备已有在途动作，请查询原回执")
                if state["battery_percent"] < 25 or state["wind_m_s"] > 10 or not -20 <= state["temperature_c"] <= 50:
                    raise HTTPException(409, "模拟电量、风速或温度不满足当前模拟起飞边界")
                state.update(phase="flying", active_request_id=key)
                status, due_at = "accepted", self.clock() + duration
                self._save_device(db, row, state)
            elif command.action in {"return_to_dock", "cancel_mission"}:
                if arguments:
                    raise HTTPException(422, "返航或取消不接受额外参数")
                previous = state["active_request_id"]
                if not previous:
                    raise HTTPException(409, "没有可返航或取消的模拟任务")
                if state["phase"] == "returning":
                    raise HTTPException(409, "返航已在途，请核对原回执")
                previous_receipt = db.execute("SELECT status FROM receipts WHERE request_id=?", (previous,)).fetchone()
                if previous_receipt["status"] not in TERMINAL:
                    self._update_receipt(db, previous, "cancelled", {"reason": command.action, "followup_request_id": key, "simulated": True})
                state.update(phase="returning", active_request_id=key)
                status, due_at = "accepted", self.clock() + 2
                self._save_device(db, row, state)
            elif command.action == "set_ptz":
                if set(arguments) != {"pan_degrees", "tilt_degrees", "zoom"}:
                    raise HTTPException(422, "需同时提供水平角、俯仰角与倍率")
                self._number(arguments, "pan_degrees", -180, 180)
                self._number(arguments, "tilt_degrees", -90, 90)
                self._number(arguments, "zoom", 1, 33)
                if state["active_request_id"]:
                    raise HTTPException(409, "云台正在移动，请查询原回执")
                state.update(phase="moving", active_request_id=key)
                status, due_at = "accepted", self.clock() + 1
                self._save_device(db, row, state)
            data = {**MARK, "request_id": key, "device_id": command.device_id, "action": command.action,
                    "status": status, "device_revision": row["revision"], "accepted_at": _utc(self.clock()),
                    "updated_at": _utc(self.clock()), "result": result,
                    "completion_requires_receipt": status == "accepted"}
            if command.action == 'read_telemetry':
                # Registered workflows bind this explicit projection; the
                # complete supplier view above remains independently available.
                units = {'temperature_c': '°C', 'humidity_percent': '%', 'wind_m_s': 'm/s',
                         'pressure_hpa': 'hPa', 'battery_percent': '%',
                         'pan_degrees': '°', 'tilt_degrees': '°', 'zoom': '倍'}
                selected = sorted(set(state) & set(units))
                data['attributes'] = {field: state[field] for field in selected}
                data['field_units'] = {field: units[field] for field in selected}
            if command.action in {"return_to_dock", "cancel_mission"}:
                data["recovery_of_request_id"] = previous
            self._store_receipt(db, key, body, data, status, due_at)
            return data

    def receipt(self, key):
        with self.transaction() as db:
            receipt = db.execute("SELECT * FROM receipts WHERE request_id=?", (key,)).fetchone()
            if receipt is None:
                raise HTTPException(404, "原请求未找到；不能将缺失回执视为已执行或自动换号重发")
            self._advance(db, receipt["device_id"])
            return json.loads(db.execute("SELECT data FROM receipts WHERE request_id=?", (key,)).fetchone()[0])

    def platform_receipt(self, key):
        """Existing platform's read-only recovery envelope; never re-post a command.

        A failed action may already have moved equipment. It is not proof of
        non-execution. The current platform envelope has no failed-action
        settlement state, so retain its unknown hold and expose the raw receipt.
        """
        with self.transaction() as db:
            row = db.execute("SELECT * FROM receipts WHERE request_id=?", (key,)).fetchone()
            if row is None:
                raise HTTPException(404, "原请求回执不存在，不能证明未执行")
            body = json.loads(row["body"])
            if type(body) is not dict or set(body) != {"device_id", "action", "expected_revision", "arguments"}:
                raise HTTPException(409, "此回执不是可核对的原设备请求")
            if _request_hash(body) != row["request_hash"]:
                raise HTTPException(409, "原设备命令正文与保存摘要不一致")
            if row["device_id"] != body["device_id"]:
                raise HTTPException(409, "原设备请求与保存设备身份不一致")
            self._advance(db, row["device_id"])
            row = db.execute("SELECT * FROM receipts WHERE request_id=?", (key,)).fetchone()
            output = json.loads(row["data"])
            if (output.get("request_id") != key or output.get("device_id") != body["device_id"]
                    or output.get("action") != body["action"]
                    or any(type(output.get(k)) is not type(v) or output.get(k) != v for k, v in MARK.items())):
                raise HTTPException(409, "原设备回执绑定或模拟来源标记不一致")
            if row["status"] not in {"accepted", "running", "succeeded"} or output.get("status") != row["status"]:
                raise HTTPException(409, "原设备动作未成功；请查看原回执并处理恢复，不能证明未执行")
            result = {"idempotency_key": key, "input_hash": row["request_hash"],
                      "status": "succeeded" if row["status"] == "succeeded" else "pending"}
            if result["status"] == "succeeded":
                result["output"] = output
            return result

    def lab_change(self, key, change: LabChange):
        body = {"action": "lab_change", **change.model_dump()}
        request_hash = _request_hash(body)
        with self.transaction() as db:
            old = db.execute("SELECT * FROM receipts WHERE request_id=?", (key,)).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise HTTPException(409, "原请求号已绑定其他内容")
                return json.loads(old["data"])
            row, profile, state = self._advance(db, change.device_id)
            if row["revision"] != change.expected_revision:
                raise HTTPException(409, "设备状态版本已变化")
            ranges = {"temperature_c": (-50, 100), "humidity_percent": (0, 100), "wind_m_s": (0, 75),
                      "pressure_hpa": (500, 1100), "battery_percent": (0, 100)}
            if not set(change.telemetry) <= ranges.keys():
                raise HTTPException(422, "只支持已列明的模拟遥测参数")
            for name, value in change.telemetry.items():
                self._number({name: value}, name, *ranges[name])
            state.update(change.telemetry)
            state.update(fault=None if change.fault == "offline" else change.fault, online=change.fault != "offline")
            if not state["active_request_id"]:
                state["phase"] = "faulted" if change.fault else "idle"
            self._save_device(db, row, state)
            # A reconnect alone cannot turn the disconnected period into a success:
            # retain occupancy and require an explicit recovery choice on the old action.
            active = state["active_request_id"]
            if active and change.fault != "offline":
                previous = db.execute("SELECT * FROM receipts WHERE request_id=?", (active,)).fetchone()
                if previous["due_at"] is not None and previous["due_at"] <= self.clock():
                    self._update_receipt(db, active, "failed", {"reason": "completion_not_observed_during_disconnection_or_lab_change", "simulated": True})
                    state.update(phase="recovery_required")
                    self._save_device(db, row, state)
            if change.confirm_safe_idle:
                if not state["online"] or state["fault"] or state["phase"] != "recovery_required" or not active:
                    raise HTTPException(409, "只有已解除故障、在线且待安全恢复的模拟设备可确认归位")
                state.update(active_request_id=None, phase="idle")
                self._save_device(db, row, state)
            result = {**MARK, "request_id": key, "device_id": change.device_id, "action": "lab_change", "status": "succeeded",
                      "updated_at": _utc(self.clock()), "result": self._view(row, profile, state)}
            if change.confirm_safe_idle:
                result["simulation_safe_idle_confirmed_for"] = active
            self._store_receipt(db, key, body, result, "succeeded")
            return result


def create_app(state_path: Path, token: str, *, clock: Callable[[], float] = time.time) -> FastAPI:
    if len(token) < 24:
        raise ValueError("需通过 GA_SIMULATOR_TOKEN 提供至少24字符的独立模拟器密钥")
    ledger = DeviceLedger(state_path, clock)
    from .device_acquisition_adapter_v176 import AcquisitionAdapterV176
    acquisitions = AcquisitionAdapterV176(ledger)
    app = FastAPI(title="设备模拟服务", docs_url=None, redoc_url=None, openapi_url=None)

    def authorize(authorization):
        expected = "Bearer " + token
        if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(401, "模拟器认证失败")

    def request_key(key):
        if not key or not KEY.fullmatch(key):
            raise HTTPException(422, "需要有效的 Idempotency-Key 原请求号")
        return key

    @app.get("/health")
    def health():
        return {**MARK, "status": "ok", "service": "durable-equipment-simulator", "schema_version": 1}

    @app.get("/devices")
    def devices(authorization: str | None = Header(default=None)):
        authorize(authorization)
        return {**MARK, "devices": ledger.list_devices()}

    @app.get("/devices/{device_id}")
    def device(device_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        return ledger.device(device_id)

    @app.post("/invoke")
    def invoke(command: Command, authorization: str | None = Header(default=None), key: str | None = Header(default=None, alias="Idempotency-Key")):
        authorize(authorization)
        return ledger.invoke(request_key(key), command)

    @app.get("/receipts")
    def receipt(authorization: str | None = Header(default=None), key: str | None = Header(default=None, alias="Idempotency-Key")):
        authorize(authorization)
        return ledger.receipt(request_key(key))

    @app.get("/platform-receipts")
    def platform_receipt(authorization: str | None = Header(default=None), key: str | None = Header(default=None, alias="Idempotency-Key")):
        authorize(authorization)
        return ledger.platform_receipt(request_key(key))

    @app.post('/acquisitions')
    async def acquire(request: Request, authorization: str | None = Header(default=None),
            key: str | None = Header(default=None, alias='Idempotency-Key')):
        authorize(authorization)
        return acquisitions.submit(await request.body(), request_key(key))

    @app.get('/acquisitions/receipts')
    def acquisition_receipt(request_key: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        return acquisitions.receipt(request_key)

    @app.post("/lab/state")
    def lab(change: LabChange, authorization: str | None = Header(default=None), key: str | None = Header(default=None, alias="Idempotency-Key")):
        authorize(authorization)
        return ledger.lab_change(request_key(key), change)

    return app


def main():
    parser = argparse.ArgumentParser(description="启动仅监听127.0.0.1的持久设备模拟服务")
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口需在1024至65535之间")
    token = os.environ.get("GA_SIMULATOR_TOKEN", "")
    app = create_app(args.state, token)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
