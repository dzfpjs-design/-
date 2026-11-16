"""Managed weather acquisition IO under actual V177 scope and fenced leases.

Only the exact frozen POST and an explicitly queued original-number GET are
supported. No ENV credentials, general URL policy exception, Tool Invocation,
V201 parent or newly manufactured observation authority is used here.
"""
import hashlib
import time
from datetime import datetime

import httpx
from fastapi import HTTPException
from sqlalchemy import select, func

from . import leases
from . import device_gateway_sources_v270 as sources
from . import http_observation_exchange_v177 as ledger
from . import http_observation_mapping_v177 as protocol
from .device_gateway_http_v269 import _strict
from .device_gateway_store_v268 import contains_secret
from .gateway_credentials_v268 import open_secret
from .http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from .models import (Acquisition, AssetDeviceBinding, Capability, DataGap, DataRun,
    Entity, Job, MonitorPolicy, ObservationPlan, SensorAsset, Task)
from .relations import lock_current_actor
from .services import scoped, validate

LIMIT = 1024 * 1024
INPUT_LIMIT = 64 * 1024
TIMEOUT = 15
ERROR = '天气设备原回执尚不能核对，原号与设备占用保留；未更换凭据或自动重发'


def need(value, message=ERROR):
    if not value:
        raise HTTPException(409, message)


def managed_execution(execution):
    return type(execution) is dict and sources.EXECUTION_KEY in execution


def reject_legacy(execution):
    need(not managed_execution(execution), '托管天气来源只能通过原补采计划和工作区授权调用')


def _fresh(db, p, model, identity):
    value = db.scalar(select(model).where(model.id == identity,
        model.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    need(value is not None)
    return value


@sources.safe
def authorize_acquisition(db, p, source, acq, exchange, *, operation, stage='preflight'):
    """Server-only context, including current real worker ownership.

    POST preflight/send renew no authority: the original Monitor/Goal/selection
    gates must still admit the actual acquisition. Receipt capture/GET checks
    historical pins and current reader/lease, allowing a paused or rotated
    gateway to retain its already sent answer using only the issued snapshot.
    """
    need(operation in {'post', 'get'} and stage in {'preflight', 'send', 'receive'})
    need(isinstance(source, Capability) and isinstance(acq, Acquisition)
        and isinstance(exchange, Exchange) and leases.fenced(db),
        '天气设备调用缺少真实补采记录或实际事务租约保护')
    lock_current_actor(db, p)
    owned = leases.ownership(db)
    need(type(owned.get('job_id')) is str and type(owned.get('lease_token')) is str)
    job = db.scalar(select(Job).where(Job.id == owned['job_id'],
        Job.workspace_id == p.workspace_id, Job.status == 'running',
        Job.lease_token == owned['lease_token'], Job.lease_until > func.clock_timestamp(),
        Job.data['actor_id'].as_string() == p.user_id).execution_options(populate_existing=True))
    expected_kind = 'dispatch_acquisition' if operation == 'post' else ledger.READ_KIND
    need(job is not None and job.kind == expected_kind)
    from . import http_observation_worker_v177 as worker
    if operation == 'post' and stage != 'receive':
        from .goal_conflict_observation_service_v259 import lock_requester
        lock_requester(db, p, _fresh(db, p, Acquisition, acq.id))
    actual_acq = worker._lock_acquisition(db, p, acq.id)
    row = ledger.checked_store(db, p, exchange.id)
    need(row.acquisition_id == actual_acq.id and row.source_id == source.id == actual_acq.capability_id
        and row.actor_id == actual_acq.data.get('actor_id') == p.user_id
        and row.data['freeze']['original_request_key'] == actual_acq.idempotency_key)
    receipt = operation == 'get' or stage == 'receive'
    context = sources.validate_source(db, p, source, 'receipt' if receipt else 'dispatch')
    actual_source = context['capability']; binding = context['binding']
    frozen = row.data['freeze']; configured = binding['mapping']
    need(ledger.same(configured, frozen['mapping']['mapping']))
    need(frozen['mapping']['source_id'] == actual_source.id
        and frozen['mapping']['source_manifest_hash'] == actual_source.content_hash
        and actual_acq.data['source_hash'] == actual_source.content_hash
        and row.environment_id == context['environment'].id
        and row.instance_id == context['instance'].id and row.release_id == context['release'].id)
    # Reuse the actual historical entity/asset/request checks, not a synthetic
    # Source wrapper or a caller's request/manifest hash.
    _fresh(db, p, Entity, actual_acq.entity_id)
    checked_source, execution = worker._context(db, p, row, actual_acq)
    need(checked_source.id == actual_source.id and execution == actual_source.data['execution'])
    asset = _fresh(db, p, SensorAsset, frozen['parent_pins']['sensor_asset_id'])
    request = frozen['domain_frozen']['request']; wire = frozen['wire_frozen']
    need(asset.id == actual_acq.data['sensor_asset_id'] and asset.source_id == actual_source.id
        and asset.external_id == binding['device_external_id'] == sources.DEVICE
        and request['asset_external_id'] == sources.DEVICE and request['action'] == 'observe'
        and actual_acq.data['selected_action'] == 'observe'
        and actual_acq.data.get('sensor_device_id') == binding['sensor_device_id']
        and request['currency'] == 'CNY' and wire['request']['asset_external_id'] == sources.DEVICE
        and wire['request']['action'] == 'observe')
    protocol._exchange(frozen['mapping'], frozen['domain_frozen'], wire)
    unsent_retry = False
    if operation == 'post' and job.id != row.dispatch_job_id:
        # A normal explicit retry may make the first POST only when an
        # independently anchored original attempt proves no request was sent.
        # This boolean alone is not dispatch authority: every current gate
        # above/below and the ledger's actual receipt provenance still apply.
        from .managed_source_unsent_v270 import require_unsent_retry
        unsent_retry = require_unsent_retry(db, p, actual_acq, row, stage=stage)
        need(unsent_retry is True)
    # A retry's receipt provenance exists only after its independently anchored
    # send marker. Preflight/send must not manufacture a receipt origin.
    origin = ledger.receipt_origin(db, p, row.id) if receipt else None
    if operation == 'post':
        need(job.data.get('acquisition_id') == actual_acq.id and (unsent_retry
            or job.id == row.dispatch_job_id
            and owned['lease_token'] == frozen['dispatch_identity']['lease_token']))
        if not receipt:
            need(actual_acq.status in {'authorized', 'dispatching'}
                and not actual_acq.data.get('dispatch_started_at')
                and row.status == 'awaiting_receipt' and not row.data['receipt_history'],
                '此原请求已开始或已经存在回执，禁止重新发送')
            policy = scoped(db, MonitorPolicy, actual_acq.data['policy_id'], p, True)
            from .perception import authorized_monitor
            from .sensing_selection import pre_dispatch, reservation
            from .device_identity import checked, occupied
            authorized_monitor(db, p, policy)
            need(policy.approved_by == p.user_id and policy.data['approved_hash'] == actual_acq.data['policy_hash'])
            db_clock = db.scalar(select(func.clock_timestamp()))
            from .temporal import instant
            plan = _fresh(db, p, ObservationPlan, actual_acq.data['observation_plan_id'])
            need(isinstance(db_clock, datetime) and db_clock.tzinfo is not None
                and db_clock < instant(policy.data['valid_until'])
                and db_clock < instant(plan.data['deadline']))
            for model, key in ((Task, 'task_id'), (DataGap, 'gap_id'), (DataRun, 'query_id')):
                _fresh(db, p, model, frozen['parent_pins'][key])
            selected = pre_dispatch(db, p, policy, actual_acq)
            need(ledger.same(ledger._parent_pins(db, p, actual_acq), frozen['parent_pins']))
            _fresh(db, p, AssetDeviceBinding, actual_acq.data['device_binding_id'])
            link, device = checked(db, p, selected.id)
            hold = reservation(db, p, actual_acq)
            device_hold = occupied(db, p, device.id)
            need(selected.id == asset.id and selected.version == actual_acq.data['sensor_asset_version']
                and device.id == binding['sensor_device_id'] == actual_acq.data['sensor_device_id']
                and link.id == actual_acq.data['device_binding_id']
                and link.version == actual_acq.data['device_binding_version']
                and device.version == actual_acq.data['sensor_device_version']
                and hold is not None and hold.acquisition_id == actual_acq.id
                and device_hold is not None and device_hold.acquisition_id == actual_acq.id)
    else:
        worker._read_job(db, p, row, job)
        need(origin['operation'] == 'original_receipt_get'
            and row.data['active_read_job_id'] == job.id
            and (row.status == 'awaiting_receipt' or ledger.can_resume_observation(row)))
    return {**context, 'acquisition': actual_acq, 'exchange': row, 'job': job,
        'origin': origin, 'key': actual_acq.idempotency_key, 'unsent_retry': unsent_retry}


def _secret(context):
    row = context['snapshot']
    return open_secret(row.workspace_id, row.connection_id, row.credential_revision, row.token_cipher)


def _exchange(method, endpoint, secret, key, *, wire=None, before_capture):
    """Only server-derived exact URLs and an already authorized secret enter."""
    started = time.monotonic()
    headers = {'Authorization': 'Bearer ' + secret, 'Idempotency-Key': key,
        'Accept': 'application/json', 'Accept-Encoding': 'identity'}
    if wire is not None:
        headers['Content-Type'] = 'application/json'
    params = {'request_key': key} if method == 'GET' else None
    try:
        with httpx.Client(timeout=httpx.Timeout(TIMEOUT, connect=5),
                follow_redirects=False, trust_env=False) as client:
            with client.stream(method, endpoint, headers=headers, content=wire, params=params) as response:
                need(response.status_code == 200)
                content_type = response.headers.get('content-type', '').split(';')[0]
                need(content_type == 'application/json'
                    and response.headers.get('content-encoding', 'identity').lower() == 'identity')
                raw = bytearray()
                for chunk in response.iter_raw():
                    need(len(raw) + len(chunk) <= LIMIT and time.monotonic() - started <= TIMEOUT)
                    raw.extend(chunk)
        raw = bytes(raw)
        need(raw and time.monotonic() - started <= TIMEOUT)
        value = _strict(raw)
        need(not contains_secret(value, secret) and secret.encode('ascii') not in raw,
            '天气设备响应包含访问凭据，已阻止保存和展示；原请求号与占用保留')
        before_capture()
        return value
    except httpx.HTTPError:
        # Neither exception text, URLs, remote bodies nor plaintext enter logs.
        raise HTTPException(409, ERROR) from None


def _accepted(value, context):
    try:
        validate(context['capability'].data['output_schema'], value)
        frozen = context['exchange'].data['freeze']
        protocol.from_supplier_observation(value, frozen['mapping'],
            frozen['domain_frozen'], frozen['wire_frozen'])
        return value
    except (HTTPException, KeyError, TypeError, ValueError, AttributeError,
            UnicodeError, OverflowError, RecursionError):
        # Schema errors may quote the rejected remote value. Do not let that
        # value become a saved job/acquisition error or an API response.
        raise HTTPException(409, ERROR) from None


def post_acquisition(db, p, source, acq, exchange, *, before_authorize, on_send):
    """Send exact frozen bytes once. on_send records/commits original intent."""
    need(callable(before_authorize) and callable(on_send))
    context = authorize_acquisition(db, p, source, acq, exchange, operation='post')
    secret = _secret(context)
    try:
        wire = context['exchange'].data['freeze']['wire_frozen']
        body = wire['body'].encode('utf-8')
        need(0 < len(body) <= INPUT_LIMIT
            and hashlib.sha256(body).hexdigest() == wire['body_sha256']
            and not contains_secret(wire['request'], secret) and secret.encode('ascii') not in body)
        before_authorize()
        current = authorize_acquisition(db, p, source, acq, exchange, operation='post', stage='send')
        need(current['snapshot'].id == context['snapshot'].id and current['endpoint'] == context['endpoint'])
        leases.checkpoint(db)
        on_send()  # The original dispatcher owns the final sent marker/commit.
        def before_capture():
            actual = authorize_acquisition(db, p, source, acq, exchange,
                operation='post', stage='receive')
            need(actual['snapshot'].id == context['snapshot'].id and actual['endpoint'] == context['endpoint'])
        value = _exchange('POST', current['endpoint'], secret, current['key'],
            wire=body, before_capture=before_capture)
        return _accepted(value, current)
    finally:
        secret = None


def get_original_receipt(db, p, source, acq, exchange):
    context = authorize_acquisition(db, p, source, acq, exchange, operation='get')
    secret = _secret(context)
    try:
        leases.checkpoint(db)
        def before_capture():
            actual = authorize_acquisition(db, p, source, acq, exchange, operation='get', stage='receive')
            need(actual['snapshot'].id == context['snapshot'].id
                and actual['receipt_endpoint'] == context['receipt_endpoint'])
        return _accepted(_exchange('GET', context['receipt_endpoint'], secret,
            context['key'], before_capture=before_capture), context)
    finally:
        secret = None
