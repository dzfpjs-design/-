"""Original-number receipt GET and atomic parent/child finalization.

There is no device POST, dispatch authorization, new request number or business
gap settlement here. Parent locking/live business guards remain in the central
worker. An ended business parent may still retain its already-dispatched receipt.
"""
import copy
from uuid import UUID, uuid5

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from . import connectors
from . import http_observation_exchange_v177 as ledger
from . import http_observation_mapping_v177 as protocol
from . import http_observation_transport_v177 as transport
from .contracts import ObservationCreate
from .http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from .leases import checkpoint
from .models import Acquisition, Audit, Capability, Entity, Job, SensorAsset, now
from .relations import lock_current_actor
from .services import audit, require, scoped, validate

ACQ_EXCHANGE_KEY = 'http_observation_exchange_id_v177'
QUEUED_MESSAGE = '核对原回执已排队'
UNKNOWN_MESSAGE = '尚不能核对原回执；原请求号和设备占用保留，未重新派发'
RESULT = 'http_observation_receipt_query_v177'
DERIVATION_FAILED = 'http.observation.derivation_failed_v292'


def _actual_acquisition(db, p, acq, *, lock=False):
    return scoped(db, Acquisition, acq.id, p, lock)


@ledger._safe
def find_exchange(db, p, acq):
    """None only for an actual legacy Acq with no new-family traces at all."""
    acq = _actual_acquisition(db, p, acq)
    rows = list(db.scalars(select(Exchange).where(Exchange.workspace_id == p.workspace_id,
        Exchange.acquisition_id == acq.id)))
    ledger.need(len(rows) <= 1)
    remnants = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ledger.ACTION, Audit.data['acquisition_id'].as_string() == acq.id)))
    source = scoped(db, Capability, acq.capability_id, p)
    ledger.need(source.kind == 'source'
        and source.content_hash == source.validated_hash == protocol.digest(source.data))
    spec = source.data.get('specification')
    ledger.need(type(spec) is dict)
    marked = ACQ_EXCHANGE_KEY in acq.data
    if not rows:
        ledger.need(not remnants and not marked and transport.MAPPING_KEY not in spec,
            '原映射交换主记录缺失或仍有独立回执；禁止改走旧通道或重建派发')
        return None
    row = ledger.checked_store(db, p, rows[0].id)
    ledger.need(row.acquisition_id == acq.id and row.source_id == acq.capability_id
        and row.actor_id == acq.data.get('actor_id')
        and row.data['freeze']['original_request_key'] == acq.idempotency_key
        and all(item.object_id == row.id for item in remnants)
        and (not marked or acq.data[ACQ_EXCHANGE_KEY] == row.id))
    return row


def _read_job(db, p, row, job):
    ledger.need(job.workspace_id == p.workspace_id and job.kind == ledger.READ_KIND
        and job.data.get('actor_id') == row.actor_id == p.user_id
        and job.data.get('exchange_id') == row.id
        and job.data.get('original_request_key') == row.data['freeze']['original_request_key']
        and job.data.get('wire_request_hash') == row.data['freeze']['wire_frozen']['request_hash']
        and type(job.data.get('exchange_version')) is int)
    protocol._uuid(job.data['request_id'])
    ledger.need(job.idempotency_key == 'http-observation-receipt:' + row.id + ':' + job.data['request_id'])
    saved = [item for item in row.data['read_jobs'] if item['job_id'] == job.id]
    ledger.need(len(saved) == 1 and saved[0]['request_id'] == job.data['request_id']
        and saved[0]['instruction_hash'] == protocol.digest(ledger._read_instruction(job)))
    anchors = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ledger.READ_AUDIT, Audit.object_id == job.id)))
    ledger.need(len(anchors) == 1 and anchors[0].actor_id == row.actor_id
        and ledger.same(anchors[0].data, {'exchange_id': row.id, 'request_id': job.data['request_id'],
            'instruction_hash': saved[0]['instruction_hash'], 'job_key': job.idempotency_key}))


@ledger._safe
def queue_read(db, p, acq):
    """Enqueue one explicit GET; queued means neither observed nor completed."""
    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    acq = _lock_acquisition(db, p, acq.id)
    row = find_exchange(db, p, acq)
    ledger.need(row is not None, '此入口只安排已有映射交换的原号查询')
    ledger.need(row.actor_id == p.user_id, '首版仅原派发账号可以核对原回执', 403)
    row = ledger.checked_store(db, p, row.id, lock=True)
    resume = ledger.can_resume_observation(row)
    if row.status in {'received', 'received_no_data'} and not resume:
        return row, None
    ledger.need(row.status == 'awaiting_receipt' or resume)
    active_id = row.data['active_read_job_id']
    if active_id:
        active = scoped(db, Job, active_id, p)
        _read_job(db, p, row, active)
        if active.status in {'queued', 'running'}:
            return row, active
    request_id = str(uuid5(UUID(row.id), 'receipt:' + str(row.version) + ':' + str(acq.version)))
    return ledger.enqueue_receipt_read(db, p, row.id, row.version, request_id)


def _context(db, p, row, acq):
    """Historical bindings only; not pre_dispatch or permission for a new action."""
    frozen = row.data['freeze']; mapping = frozen['mapping']; request = frozen['domain_frozen']['request']
    source = scoped(db, Capability, row.source_id, p)
    entity = scoped(db, Entity, acq.entity_id, p)
    asset = scoped(db, SensorAsset, frozen['parent_pins']['sensor_asset_id'], p)
    ledger.need(source.kind == 'source' and row.source_id == acq.capability_id
        and source.content_hash == source.validated_hash == protocol.digest(source.data)
            == mapping['source_manifest_hash'] == acq.data['source_hash']
        and entity.id == frozen['parent_pins']['entity_id']
        and entity.external_id == request['target_external_id']
        and entity.schema_capability_id == mapping['mapping']['scope']['package_id']
        and entity.entity_type == mapping['mapping']['scope']['entity_type']
        and asset.source_id == source.id and asset.external_id == request['asset_external_id']
        and acq.data.get('actor_id') == row.actor_id == p.user_id
        and acq.data['sensor_asset_id'] == asset.id
        and ledger.same(acq.data['selected_fields'], request['fields'])
        and acq.data['selected_action'] == request['action']
        and ledger.same(acq.data['reserved_cost_minor'], request['max_cost_minor'])
        and acq.data['currency'] == request['currency']
        and acq.idempotency_key == request['request_key'])
    execution = source.data['execution']
    ledger.need(execution.get('adapter') == 'http'
        and ledger.same(acq.data['dispatch_execution'], execution),
        '原已登记查询接口与实际Source摘要不同，不能换端点核对')
    return source, execution


def _get(execution, key, *, db=None, p=None, source=None, acq=None, exchange=None):
    from .device_gateway_source_http_v270 import managed_execution, get_original_receipt
    if managed_execution(execution):
        ledger.need(all(value is not None for value in (db,p,source,acq,exchange)),
            '设备原号回执缺少实际任务上下文，未读取凭据或联网')
        return get_original_receipt(db,p,source,acq,exchange)
    from .gateway import validate_endpoint
    reference = execution.get('secret_ref')
    ledger.need(type(reference) is str and reference.startswith('GA_CONNECTOR_SECRET_'),
        '原回执服务须使用已登记的服务端凭据引用')
    endpoint = validate_endpoint({'endpoint': execution.get('receipt_endpoint', '')})
    headers = {**connectors.headers(execution), 'Idempotency-Key': key, 'Accept': 'application/json'}
    ledger.need('Authorization' in headers, '原回执服务凭据未配置')
    with httpx.Client(timeout=httpx.Timeout(15, connect=5), follow_redirects=False, trust_env=False) as client:
        value, _ = connectors.bounded_response(client, 'GET', endpoint,
            params={'request_key': key}, headers=headers)
    return value


def _lock_acquisition(db, p, identity):
    from .observation_requests import lock_acquisition_origin
    acq = scoped(db, Acquisition, identity, p)
    lock_acquisition_origin(db, p, acq)
    return scoped(db, Acquisition, identity, p, True)


def _after_receipt(db, p, acq, raw):
    from .sensing_selection import after_receipt
    return after_receipt(db, p, acq, raw)


def _outcome(db, p, acq, job, row, status, message, *, retained=False, observation_id=None):
    value = {'schema': 'http-observation-read-result-v177', 'exchange_id': row.id,
        'job_id': job.id, 'status': status, 'message': message,
        'retain_receipt': retained, 'observation_id': observation_id, 'goal_completed': False}
    job.data = {**job.data, RESULT: value}
    acq.data = {**acq.data, RESULT: copy.deepcopy(value), 'reason': message}
    acq.version += 1
    audit(db, p, 'http.observation.receipt_read.result_v177', job.id, value)


@ledger._safe
def run_read(db, p, job):
    """Bounded GET or same-parent saved receipt; never retry a device POST."""
    lock_current_actor(db, p)
    actual, _ = ledger._leased(db, p)
    ledger.need(actual.id == job.id and actual.kind == ledger.READ_KIND)
    job = actual
    candidate = scoped(db, Exchange, job.data['exchange_id'], p)
    acq = _lock_acquisition(db, p, candidate.acquisition_id)
    row = find_exchange(db, p, acq)
    ledger.need(row is not None and row.id == candidate.id and row.data['active_read_job_id'] == job.id)
    _read_job(db, p, row, job)
    source, execution = _context(db, p, row, acq)
    origin = ledger.receipt_origin(db, p, row.id)
    history = row.data['receipt_history']
    saved = history and ledger.same(history[-1]['receipt_origin'], origin)
    if saved:
        raw = copy.deepcopy(history[-1]['raw'])
    else:
        checkpoint(db)
        try:
            from .device_gateway_sources_v270 import managed as managed_source
            raw = (_get(execution, row.data['freeze']['original_request_key'],db=db,p=p,
                source=source,acq=acq,exchange=row) if managed_source(db,p,source)
                else _get(execution, row.data['freeze']['original_request_key']))
            validate(source.data['output_schema'], raw)
            protocol.from_supplier_observation(raw, row.data['freeze']['mapping'],
                row.data['freeze']['domain_frozen'], row.data['freeze']['wire_frozen'])
        except HTTPException as error:
            if error.status_code in {401, 403}: raise
            checkpoint(db)
            acq = _lock_acquisition(db, p, row.acquisition_id)
            lock_current_actor(db, p); ledger._leased(db, p)
            acq.status = 'unknown'
            _outcome(db, p, acq, job, row, 'unknown', UNKNOWN_MESSAGE)
            return row
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError):
            checkpoint(db)
            acq = _lock_acquisition(db, p, row.acquisition_id)
            lock_current_actor(db, p); ledger._leased(db, p)
            acq.status = 'unknown'
            _outcome(db, p, acq, job, row, 'unknown', UNKNOWN_MESSAGE)
            return row
    return settle_receipt(db, p, row.id, raw)


def _derivation_failure_diagnostic(db, p, job, exchange, acquisition, failure):
    """Bounded code locations on the actual receipt branch, never a retry proof."""
    from .worker_failure_diagnostics_v289 import capture_failure
    diagnostic = capture_failure(failure)
    audit(db, p, DERIVATION_FAILED, exchange.id, {
        'schema': 'http-observation-derivation-failure-v292',
        'workspace_id': p.workspace_id, 'job_id': job.id,
        'exchange_id': exchange.id, 'acquisition_id': acquisition.id,
        'original_dispatch_job_id': exchange.dispatch_job_id,
        'diagnostic': diagnostic, 'diagnostic_hash': protocol.digest(diagnostic)})


@ledger._safe
def settle_receipt(db, p, exchange_id, raw_receipt):
    """Shared POST/GET settlement: prove first, retain, then derive atomically.

    The central dispatcher MUST NOT call after_receipt before this function:
    the first checkpoint must not carry any unverified terminal/resource writes.
    The caller remains responsible for the original dispatch/live parent guards.
    """
    lock_current_actor(db, p)
    job, _ = ledger._leased(db, p)
    ledger.need(job.kind in {'dispatch_acquisition', ledger.READ_KIND})
    candidate = scoped(db, Exchange, exchange_id, p)
    acq = _lock_acquisition(db, p, candidate.acquisition_id)
    row = find_exchange(db, p, acq)
    ledger.need(row is not None and row.id == exchange_id)
    if job.kind == ledger.READ_KIND:
        _read_job(db, p, row, job)
    source, _ = _context(db, p, row, acq)
    raw = copy.deepcopy(raw_receipt)
    # Validate the WHOLE mapped original packet before the ledger's first
    # checkpoint, not merely the generic key/asset/terminal subset. Invalid SIM
    # labels, hashes, units or changed clocks may never release device holdings.
    validate(source.data['output_schema'], raw)
    protocol.from_supplier_observation(raw, row.data['freeze']['mapping'],
        row.data['freeze']['domain_frozen'], row.data['freeze']['wire_frozen'])
    # Receipt first, independently audited and durable. No business-derived rows
    # or terminal-resource mutation exists before this checkpoint.
    origin = ledger.receipt_origin(db, p, row.id)
    row = ledger.checked_store(db, p, row.id)
    row = ledger.record_receipt(db, p, row.id, row.version, raw, receipt_origin=origin)
    checkpoint(db)
    acq = _lock_acquisition(db, p, row.acquisition_id)
    source, _ = _context(db, p, row, acq)
    if acq.status != 'succeeded':
        _after_receipt(db, p, acq, raw)
    acq.data = {**acq.data, 'reconciled_receipt': copy.deepcopy(raw), 'reconciled_at': now().isoformat()}
    acq.version += 1
    audit(db, p, 'acquisition.reconcile_v177', acq.id,
        {'exchange_id': row.id, 'receipt_hash': protocol.digest(raw), 'status': acq.status, 'receipt_only': True})
    checkpoint(db)  # Preserve a proven terminal receipt even if derivation stops.
    projection = row.data['projection']
    if projection['status'] != 'mapped' or projection['temporal_usable'] is not True:
        acq = _lock_acquisition(db, p, row.acquisition_id)
        _outcome(db, p, acq, job, row, 'receipt_saved',
            '原回执已保存；尚无可接入的完整观测资料', retained=True)
        return row
    try:
        # All Observation, child and attach writes roll back together. No
        # checkpoint is permitted inside this saved-receipt derivation branch.
        with db.begin_nested():
            _lock_acquisition(db, p, row.acquisition_id)
            row = transport.receive(db, p, row.id, raw, saved_receipt=True)
    except HTTPException as error:
        if error.status_code in {401, 403}: raise
        row = ledger.checked_store(db, p, row.id)
        acq = _lock_acquisition(db, p, row.acquisition_id)
        _derivation_failure_diagnostic(db, p, job, row, acq, error)
        _outcome(db, p, acq, job, row, 'derived_blocked',
            '原回执已保存，后续观测接入被阻止：' + str(error.detail)[:700], retained=True)
        return row
    acq = _lock_acquisition(db, p, row.acquisition_id)
    _outcome(db, p, acq, job, row, 'observation_saved',
        '观测已保存，等待原任务成功提交后处理；尚未完成业务补证',
        retained=True, observation_id=row.observation_id)
    return row


@ledger._safe
def finalize_parent(db, job):
    """Only the real running parent can queue its own still-unstarted child.

    Root sets parent.status=succeeded and commits in this SAME transaction. This
    function never checkpoints or commits; the normal lease hook fences commit.
    """
    if job.kind not in {'dispatch_acquisition', ledger.READ_KIND}: return
    p = db.info.get('worker_actor')
    ledger.need(p is not None and p.workspace_id == job.workspace_id)
    lock_current_actor(db, p)
    actual, lease = ledger._leased(db, p)
    ledger.need(actual.id == job.id and actual.kind == job.kind and actual.status == 'running')
    if job.kind == ledger.READ_KIND:
        candidate = scoped(db, Exchange, actual.data['exchange_id'], p)
        acq = _lock_acquisition(db, p, candidate.acquisition_id)
    else:
        acq = _lock_acquisition(db, p, actual.data['acquisition_id'])
    row = find_exchange(db, p, acq)
    children = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id,
        Job.data['http_mapped_parent_job_id'].as_string() == job.id).with_for_update()
        .execution_options(populate_existing=True)))
    ledger.need(len(children) <= 1)
    if row is None:
        ledger.need(not children); return
    if not row.observation_id:
        ledger.need(not children); return
    row = ledger.checked_store(db, p, row.id, lock=True)
    origin = ledger.receipt_origin(db, p, row.id)
    ledger.need(ledger.same(row.data['receipt_history'][-1]['receipt_origin'], origin)
        and origin['job_id'] == actual.id and origin['lease_token'] == lease['lease_token'])
    if actual.kind == ledger.READ_KIND: _read_job(db, p, row, actual)
    pin = row.data['observation_pin']
    body = ObservationCreate.model_validate(pin['original_request'])
    ledger.need(ledger.same(transport._request(body),
        transport._request(transport._body(row, body.extractor_id))))
    observation = transport._observation(db, p, row, row.observation_id, body)
    ledger.need(pin['input_hash'] == protocol.digest(transport._request(body))
        and all(ledger.same(observation.data.get(key), value)
            for key, value in transport._labels(row).items()))
    child = transport._process(db, p, observation)
    ledger.need(len(children) == 1 and children[0].id == child.id and child.kind == transport.PROCESS
        and child.status == 'waiting_parent' and type(child.attempts) is int and child.attempts == 0
        and child.data.get('http_mapped_exchange_id') == row.id
        and child.data.get('http_mapped_parent_job_id') == actual.id)
    child.status = 'queued'
