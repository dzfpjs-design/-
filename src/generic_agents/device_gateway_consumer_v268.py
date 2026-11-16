"""Explicitly scoped discovery consumer; no device operation is dispatched.

Run with --workspace-id UUID --connection-id UUID [--once]. An unsuccessful
attempt ends this original Job; a new discovery always needs a new human request.
"""
from __future__ import annotations
import argparse
import json
import time
from datetime import datetime, timezone
from threading import Event
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session
import httpx
from .db import engine
from .models import Audit, Job, User
from .security import Principal
from . import leases
from . import device_gateway_store_v268 as store
from .device_gateway_models_v268 import DeviceGatewayConnectionV268 as Connection


class DiscoveryFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def context(db, workspace_id, connection_id, job_id):
    job = db.scalar(select(Job).where(Job.id == job_id, Job.workspace_id == workspace_id,
        Job.kind == store.KIND, Job.data['connection_id'].as_string() == connection_id)
        .execution_options(populate_existing=True))
    if not job or not store.admitted(db, job):
        raise DiscoveryFailure('discovery_admission_invalid')
    p = Principal(job.data['actor_id'], workspace_id, 'admin', 'device-gateway-consumer-v268')
    try:
        store.member(db, p, admin=True, locked=True)
    except Exception:
        raise DiscoveryFailure('discovery_actor_revoked') from None
    connection = db.scalar(select(Connection).where(Connection.id == connection_id,
        Connection.workspace_id == workspace_id).with_for_update().execution_options(populate_existing=True))
    d = job.data
    if (not connection or connection.status != 'enabled' or connection.protocol != store.PROTOCOL
            or connection.version != d.get('connection_version')
            or connection.credential_revision != d.get('credential_revision')
            or connection.origin != d.get('origin') or connection.protocol != d.get('protocol')):
        raise DiscoveryFailure('discovery_connection_changed')
    return p, connection, job


def _json_unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate catalogue key')
        result[key] = value
    return result


def _invalid_number(value):
    raise ValueError('nonfinite catalogue number')


def fetch_catalogue(origin, token):
    """At most 1 MiB, ten seconds, exact numeric origin, no redirect or proxy."""
    started = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False,
                          trust_env=False) as client:
            with client.stream('GET', origin + '/devices', headers={
                    'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                    'Accept-Encoding': 'identity'}) as response:
                if response.status_code in {401, 403}:
                    raise DiscoveryFailure('discovery_authentication_failed')
                if response.status_code != 200 or response.headers.get('content-type', '').split(';')[0] != 'application/json':
                    raise DiscoveryFailure('discovery_protocol_failed')
                if response.headers.get('content-encoding', 'identity').strip().lower() != 'identity':
                    raise DiscoveryFailure('discovery_protocol_failed')
                content = bytearray()
                for chunk in response.iter_raw():
                    if len(content) + len(chunk) > 1024 * 1024 or time.monotonic() - started > 10:
                        raise DiscoveryFailure('discovery_response_bounds')
                    content.extend(chunk)
                if time.monotonic() - started > 10:
                    raise DiscoveryFailure('discovery_response_bounds')
        value = json.loads(bytes(content), object_pairs_hook=_json_unique, parse_constant=_invalid_number)
        devices = store.normalize_catalogue(value)
        # A remote echo must never turn the submitted bearer into public metadata.
        if store.contains_secret(devices, token):
            raise DiscoveryFailure('discovery_catalogue_invalid')
        return devices
    except DiscoveryFailure:
        raise
    except httpx.TimeoutException:
        raise DiscoveryFailure('discovery_timeout') from None
    except httpx.HTTPError:
        raise DiscoveryFailure('discovery_network_failed') from None
    except Exception:
        raise DiscoveryFailure('discovery_catalogue_invalid') from None


def terminal(workspace_id, connection_id, job_id, token, *, devices=None, error_code=None):
    """No attach hooks: original lease CAS and terminal Audit commit together."""
    with Session(engine(), expire_on_commit=False) as db:
        store.lock(db, workspace_id)
        job = db.scalar(select(Job).where(Job.id == job_id, Job.workspace_id == workspace_id,
            Job.kind == store.KIND, Job.data['connection_id'].as_string() == connection_id,
            Job.lease_token == token, Job.status == 'running', Job.lease_until > func.clock_timestamp())
            .with_for_update())
        if not job:
            return False
        if devices is not None:
            try:
                context(db, workspace_id, connection_id, job_id)
            except DiscoveryFailure as failure:
                devices = None; error_code = failure.code
        status = 'succeeded' if devices is not None else 'failed'
        data = {**job.data, 'completed_at': datetime.now(timezone.utc).isoformat(),
            'devices': devices if devices is not None else [],
            'catalogue_digest': store.digest(devices) if devices is not None else None,
            'error_code': None if devices is not None else (error_code or 'discovery_failed')}
        actor_id = data.get('actor_id')
        if not db.scalar(select(User.id).where(User.id == actor_id)):
            return False
        result = db.execute(update(Job).where(Job.id == job_id, Job.workspace_id == workspace_id,
            Job.status == 'running', Job.lease_token == token, Job.lease_until > func.clock_timestamp())
            .values(status=status, lease_token=None, lease_until=None,
                    version=Job.version + 1).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            db.rollback(); return False
        # The Core CAS changes only the fence. Payload assignment must pass
        # the existing ORM signing hook; hand-writing private pairing fields
        # or updating JSONB alone would break the durable payload contract.
        db.refresh(job)
        job.data = data
        db.flush()
        proof = {**store.admission(job), 'schema': 'device-gateway-discovery-completion-v268',
            'status': status, 'catalogue_digest': data['catalogue_digest'], 'error_code': data['error_code']}
        db.add(Audit(workspace_id=workspace_id, actor_id=actor_id, action=store.COMPLETED,
                     object_id=job_id, data=proof))
        db.commit()
        return True


def execute(workspace_id, connection_id, job_id, token):
    failure_code = None; devices = None
    try:
        with leases.heartbeat(engine(), job_id, token):
            with Session(engine(), expire_on_commit=False) as db:
                leases.attach(db, job_id, token)
                store.lock(db, workspace_id)
                p, connection, job = context(db, workspace_id, connection_id, job_id)
                db.info['worker_actor'] = p
                if job.data.get('recovery_only') is True:
                    raise DiscoveryFailure('discovery_attempts_exhausted')
                from .gateway_credentials_v268 import validate_origin, open_secret
                origin = validate_origin(connection.origin)
                secret = open_secret(workspace_id, connection_id, connection.credential_revision,
                                     connection.token_cipher)
                leases.checkpoint(db)
                # There is no DB transaction or advisory lock across HTTP.
                devices = fetch_catalogue(origin, secret)
                secret = None
                store.lock(db, workspace_id)
                context(db, workspace_id, connection_id, job_id)
                leases.checkpoint(db)
            return terminal(workspace_id, connection_id, job_id, token, devices=devices)
    except leases.LeaseLost:
        return False
    except DiscoveryFailure as failure:
        failure_code = failure.code
    except leases.ExecutionUnauthorized:
        failure_code = 'discovery_actor_revoked'
    except Exception:
        # Authentication, crypto, configuration and transport errors never expose str(exc).
        failure_code = 'discovery_failed'
    return terminal(workspace_id, connection_id, job_id, token, error_code=failure_code)


def consume_once(workspace_id, connection_id):
    store.canonical_uuid(workspace_id); store.canonical_uuid(connection_id)
    with Session(engine()) as db:
        job_id = db.scalar(select(Job.id).where(Job.workspace_id == workspace_id,
            Job.kind == store.KIND, Job.data['connection_id'].as_string() == connection_id,
            Job.available_at <= func.clock_timestamp(), or_(Job.status == 'queued',
                (Job.status == 'running') & or_(Job.lease_until.is_(None), Job.lease_until < func.clock_timestamp())))
            .order_by(Job.available_at, Job.id).limit(1))
    if not job_id:
        return False
    from .worker import claim
    claimed = claim(job_id, kinds=(store.KIND,), workspace_id=workspace_id)
    if not claimed:
        return False
    execute(workspace_id, connection_id, *claimed)
    return True


def main():
    parser = argparse.ArgumentParser(description='只消费明确工作区和连接的模拟设备目录发现')
    parser.add_argument('--workspace-id', required=True)
    parser.add_argument('--connection-id', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    store.canonical_uuid(args.workspace_id); store.canonical_uuid(args.connection_id)
    if args.once:
        consume_once(args.workspace_id, args.connection_id)
        return
    stopped = Event()
    try:
        while not stopped.is_set():
            if not consume_once(args.workspace_id, args.connection_id):
                stopped.wait(2)
    except KeyboardInterrupt:
        stopped.set()


if __name__ == '__main__':
    main()
