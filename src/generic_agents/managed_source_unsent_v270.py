"""Prove an unsent managed acquisition; never authorize or perform a POST.

An original V177 freeze stays immutable. A normal retry may make its FIRST
send only after every older dispatch Job has a real terminal record and a
separate no-send Audit. Missing proof and crash windows fail closed. Current
Source/Goal/policy/selection/lease gates remain mandatory in the transport.
"""
import copy
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select

from . import leases
from . import http_observation_exchange_v177 as ledger
from . import device_gateway_sources_v270 as sources
from .contracts import digest
from .http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from .models import Acquisition, Audit, Capability, Entity, Job, ObservationPlan, SensorAsset, uid
from .relations import lock_current_actor
from .services import scoped


UNSENT = 'acquisition.managed_source_blocked_v270'
UNSENT_SCHEMA = 'managed-source-unsent-proof-v270'
PRE_UNSENT = 'acquisition.managed_source_pre_exchange_v270'
PRE_SCHEMA = 'managed-source-pre-exchange-unsent-v270'
SENT_SCHEMA = 'managed-source-first-dispatch-v270'
DISPATCH = 'acquisition.dispatch'
TERMINAL = {'succeeded', 'failed', 'cancelled'}
LIMIT = 256
ERROR = '原天气请求不能证明尚未发送；保留原号，不重新派发'
PIN_KEYS = {'acquisition_id', 'exchange_id', 'source_id', 'source_hash', 'actor_id',
    'original_request_key_hash', 'freeze_hash', 'parent_pins_hash', 'original_dispatch_job_id'}
PRE_KEYS = {'acquisition_id', 'source_id', 'source_hash', 'actor_id', 'original_request_key_hash'}


def need(value):
    if not value:
        raise HTTPException(409, ERROR)


def _job_pin(job, lease_token):
    need(job.kind == 'dispatch_acquisition' and type(lease_token) is str and bool(lease_token))
    return {'job_id': job.id, 'job_key': job.idempotency_key, 'lease_token': lease_token}


def _proof_data(pin, job_pin):
    need(type(pin) is dict and set(pin) == PIN_KEYS
        and type(job_pin) is dict and set(job_pin) == {'job_id', 'job_key', 'lease_token'})
    return {'schema': UNSENT_SCHEMA, **copy.deepcopy(pin), **copy.deepcopy(job_pin), 'post_sent': False}


def _pre_data(pin, job_pin):
    need(type(pin) is dict and set(pin) == PRE_KEYS
        and type(job_pin) is dict and set(job_pin) == {'job_id', 'job_key', 'lease_token'})
    return {'schema': PRE_SCHEMA, **copy.deepcopy(pin), **copy.deepcopy(job_pin), 'post_sent': False}


def _time(value):
    if type(value) is str:
        try:
            value = datetime.fromisoformat(value)
        except (TypeError, ValueError, OverflowError):
            need(False)
    need(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None)
    return value


def _sent_data(pin, job_pin, proof_ids):
    need(type(proof_ids) is list and 1 <= len(proof_ids) <= LIMIT
        and proof_ids == sorted(set(proof_ids)))
    result = _proof_data(pin, job_pin)
    result.update(schema=SENT_SCHEMA, post_sent=True, unsent_proof_ids=copy.deepcopy(proof_ids))
    return result


def _leased(db, p):
    need(leases.fenced(db))
    lock_current_actor(db, p)
    owned = leases.ownership(db)
    need(type(owned) is dict and type(owned.get('job_id')) is str
        and type(owned.get('lease_token')) is str and bool(owned['lease_token']))
    job = db.scalar(select(Job).where(Job.id == owned['job_id'], Job.workspace_id == p.workspace_id,
        Job.kind == 'dispatch_acquisition', Job.status == 'running', Job.lease_token == owned['lease_token'],
        Job.lease_until > func.clock_timestamp(), Job.data['actor_id'].as_string() == p.user_id)
        .execution_options(populate_existing=True))
    need(job is not None)
    return job, owned['lease_token']


def _scope(db, p, acq, exchange):
    need(isinstance(acq, Acquisition) and isinstance(exchange, Exchange))
    from .http_observation_worker_v177 import _lock_acquisition
    actual = _lock_acquisition(db, p, acq.id)
    row = ledger.checked_store(db, p, exchange.id, lock=True)
    return _scope_pin(db, p, actual, row)


def _cold_scope(db, p, exchange):
    """Actual immutable chain without recursively validating receipt origins."""
    need(isinstance(exchange, Exchange))
    inventory = select(func.array_agg(Audit.id)).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ledger.ACTION, Audit.object_id == exchange.id).scalar_subquery()
    captured = db.execute(select(Exchange, inventory.label('captured_audit_ids')).where(
        Exchange.workspace_id == p.workspace_id, Exchange.id == exchange.id)
        .execution_options(populate_existing=True)).first()
    need(captured is not None)
    row, identities = captured[0], captured[1]
    need(type(identities) is list and len(identities) == row.version)
    anchors = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ledger.ACTION, Audit.object_id == row.id, Audit.id.in_(identities))
        .execution_options(populate_existing=True)))
    need(len(anchors) == len(identities) and all(item.actor_id == row.actor_id for item in anchors))
    ledger.verify_authority(ledger.snapshot(row), [copy.deepcopy(item.data) for item in anchors])
    ledger._validate_saved(row)
    # The caller's outer checked_store validates each receipt/evidence and
    # invokes this historical-origin check. Re-entering it would recurse.
    actual = scoped(db, Acquisition, row.acquisition_id, p)
    return _scope_pin(db, p, actual, row, live_actor=False)


def _scope_actor(row_actor, acquisition_actor, reader_actor, *, live_actor):
    need(type(live_actor) is bool and acquisition_actor == row_actor
        and (not live_actor or reader_actor == row_actor))
    return row_actor


def _scope_pin(db, p, actual, row, *, live_actor=True):
    need(row.acquisition_id == actual.id and row.source_id == actual.capability_id
        and row.actor_id == actual.data.get('actor_id'))
    origin_actor = _scope_actor(row.actor_id, actual.data.get('actor_id'), p.user_id, live_actor=live_actor)
    source = scoped(db, Capability, row.source_id, p)
    need(sources.managed(db, p, source))
    checked = sources.validate_source(db, p, source, purpose='receipt')
    source = checked['capability']
    frozen = row.data['freeze']
    parent = frozen['parent_pins']
    request = frozen['domain_frozen']['request']
    entity = scoped(db, Entity, actual.entity_id, p)
    asset = scoped(db, SensorAsset, parent['sensor_asset_id'], p)
    need(actual.idempotency_key == frozen['original_request_key']
        == frozen['wire_frozen']['request']['request_key']
        and source.content_hash == actual.data['source_hash'] == frozen['mapping']['source_manifest_hash']
        and source.id == frozen['mapping']['source_id'] and actual.entity_id == parent['entity_id']
        and actual.data['observation_plan_id'] == parent['plan_id']
        and actual.data['policy_id'] == parent['policy_id'] and actual.data['policy_hash'] == parent['policy_hash']
        and row.dispatch_job_id == frozen['dispatch_identity']['job_id']
        and row.data['freeze_hash'] == digest(frozen)
        and ledger.same(actual.data.get('dispatch_execution'), source.data['execution'])
        and entity.external_id == request['target_external_id']
        and entity.schema_capability_id == frozen['mapping']['mapping']['scope']['package_id']
        and entity.entity_type == frozen['mapping']['mapping']['scope']['entity_type']
        and asset.id == actual.data['sensor_asset_id'] and asset.source_id == source.id
        and asset.external_id == request['asset_external_id'])
    for key in ('sensor_asset_id', 'sensor_asset_version', 'sensor_device_id', 'sensor_device_version',
                'device_binding_id', 'device_binding_version', 'selected_action', 'selected_fields',
                'reserved_cost_minor', 'currency'):
        need(ledger.same(actual.data.get(key), parent.get(key)))
    pin = {'acquisition_id': actual.id, 'exchange_id': row.id, 'source_id': source.id,
        'source_hash': source.content_hash, 'actor_id': origin_actor,
        'original_request_key_hash': digest(actual.idempotency_key), 'freeze_hash': digest(frozen),
        'parent_pins_hash': digest(parent), 'original_dispatch_job_id': row.dispatch_job_id}
    return actual, row, pin


def _rows(db, p, acq):
    jobs = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id,
        Job.kind == 'dispatch_acquisition', Job.data['acquisition_id'].as_string() == acq.id)
        .limit(LIMIT + 1).execution_options(populate_existing=True)))
    audits = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == acq.id, Audit.action.in_([UNSENT, PRE_UNSENT, DISPATCH]))
        .limit(2 * LIMIT + 1).execution_options(populate_existing=True)))
    need(1 <= len(jobs) <= LIMIT and len(audits) <= 2 * LIMIT)
    return jobs, audits


def _normal_retry(job, acq):
    prefix = 'dispatch:' + acq.id + ':resume:'
    suffix = job.idempotency_key[len(prefix):] if job.idempotency_key.startswith(prefix) else ''
    need(suffix.isdecimal() and str(int(suffix)) == suffix and int(suffix) >= 1
        and job.data.get('acquisition_id') == acq.id and job.data.get('actor_id') == acq.data['actor_id'])


def _prior_proofs(pin, current_job_id, facts, audits, *, exchange_created_at, allow_empty=False):
    """Pure closed proof check; callers obtain facts only from actual rows."""
    need(type(allow_empty) is bool and type(facts) is list and 1 <= len(facts) <= LIMIT
        and len({fact['id'] for fact in facts}) == len(facts)
        and current_job_id in {fact['id'] for fact in facts}
        and pin['original_dispatch_job_id'] in {fact['id'] for fact in facts})
    previous = [fact for fact in facts if fact['id'] != current_job_id]
    need(bool(previous) or allow_empty)
    frozen_at = _time(exchange_created_at)
    proofs = []
    for fact in previous:
        need(fact['status'] in TERMINAL and fact['lease_until'] is None
            and type(fact['finished_at']) is str and bool(fact['finished_at'])
            and fact['actor_id'] == pin['actor_id'] and fact['acquisition_id'] == pin['acquisition_id'])
        matching = [item for item in audits if item['action'] in {UNSENT, PRE_UNSENT}
            and type(item['data']) is dict and item['data'].get('job_id') == fact['id']]
        need(len(matching) == 1)
        item = matching[0]
        job_pin = {'job_id': fact['id'], 'job_key': fact['key'], 'lease_token': fact['lease_token']}
        need(type(job_pin['lease_token']) is str and bool(job_pin['lease_token']))
        finished_at = _time(fact['finished_at']); recorded_at = _time(item.get('created_at'))
        need(recorded_at <= finished_at)
        if finished_at < frozen_at:
            # Only an independent actual pre-freeze Audit can explain a Job
            # that ended before this Exchange existed. Never synthesize its
            # missing freeze or retrospectively rewrite a normal unsent proof.
            need(item['action'] == PRE_UNSENT and recorded_at < frozen_at
                and fact['id'] != pin['original_dispatch_job_id'])
            expected = _pre_data({key: pin[key] for key in PRE_KEYS}, job_pin)
        else:
            need(item['action'] == UNSENT and recorded_at >= frozen_at
                and current_job_id != pin['original_dispatch_job_id'])
            expected = _proof_data(pin, job_pin)
        need(item['actor_id'] == pin['actor_id'] and item['data'] == expected)
        proofs.append(item['id'])
    # Extra or malformed no-send records are damage, not evidence to ignore.
    all_unsent = [item for item in audits if item['action'] in {UNSENT, PRE_UNSENT}]
    need(len(all_unsent) == len(previous) and {item['id'] for item in all_unsent} == set(proofs))
    return sorted(proofs)


def _facts(jobs):
    return [{'id': job.id, 'key': job.idempotency_key, 'status': job.status,
        'lease_until': job.lease_until, 'lease_token': job.lease_token,
        'finished_at': job.data.get('finished_at'), 'actor_id': job.data.get('actor_id'),
        'acquisition_id': job.data.get('acquisition_id')} for job in jobs]


def _audit_facts(audits):
    return [{'id': item.id, 'action': item.action, 'actor_id': item.actor_id,
        'created_at': item.created_at, 'data': copy.deepcopy(item.data)} for item in audits]


def _unsent(acq, row, audits):
    need(not acq.data.get('dispatch_started_at') and not any(item.action == DISPATCH for item in audits)
        and row.status == 'awaiting_receipt' and not row.data['receipt_history']
        and row.observation_id is None and row.data.get('active_read_job_id') is None
        and not row.data.get('read_jobs'))


def _retry(db, p, acq, row, pin, job, token, stage):
    need(stage in {'preflight', 'send', 'receive'} and job.id != row.dispatch_job_id)
    _normal_retry(job, acq)
    jobs, audits = _rows(db, p, acq)
    proofs = _prior_proofs(pin, job.id, _facts(jobs), _audit_facts(audits),
        exchange_created_at=row.created_at)
    if stage in {'preflight', 'send'}:
        _unsent(acq, row, audits)
        need(acq.status in {'authorized', 'dispatching', 'blocked_connector'}
            and ledger.same(ledger._parent_pins(db, p, acq), row.data['freeze']['parent_pins']))
        plan = scoped(db, ObservationPlan, acq.data['observation_plan_id'], p)
        need(any(item.get('kind') == 'resource_recovered' and item.get('acquisition_id') == acq.id
            for item in plan.data.get('history', [])))
    else:
        sent = [item for item in audits if item.action == DISPATCH]
        need(type(acq.data.get('dispatch_started_at')) is str and bool(acq.data['dispatch_started_at'])
            and len(sent) == 1 and sent[0].actor_id == p.user_id
            and sent[0].data == _sent_data(pin, _job_pin(job, token), proofs))
    return proofs


def mark_unsent(db, p, acq, exchange):
    """Record proven pre-send failure under its real worker transaction."""
    job, token = _leased(db, p)
    acq, row, pin = _scope(db, p, acq, exchange)
    need(job.data.get('acquisition_id') == acq.id and acq.status == 'blocked_connector')
    jobs, audits = _rows(db, p, acq)
    _unsent(acq, row, audits)
    data = _proof_data(pin, _job_pin(job, token))
    previous = [item for item in audits if item.action == UNSENT
        and type(item.data) is dict and item.data.get('job_id') == job.id]
    need(len(previous) <= 1)
    if previous:
        need(previous[0].actor_id == p.user_id and previous[0].data == data)
        return previous[0]
    if job.id == row.dispatch_job_id:
        frozen = row.data['freeze']['dispatch_identity']
        need(frozen == {'job_id': job.id, 'kind': job.kind, 'idempotency_key': job.idempotency_key,
            'actor_id': p.user_id, 'lease_token': token})
        _prior_proofs(pin, job.id, _facts(jobs), _audit_facts(audits),
            exchange_created_at=row.created_at, allow_empty=True)
    else:
        _retry(db, p, acq, row, pin, job, token, 'preflight')
    item = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        action=UNSENT, object_id=acq.id, data=data)
    db.add(item)
    db.flush()
    return item


def mark_pre_exchange_unsent(db, p, acq):
    """Record a real early failure before any immutable Exchange exists.

    This reads historical Source issuance, never plaintext, and confers no
    right to send. Later recovery also requires actual terminal Job/Audit times
    strictly before the eventual Exchange and every normal live transport gate.
    """
    job, token = _leased(db, p)
    need(isinstance(acq, Acquisition))
    from .http_observation_worker_v177 import _lock_acquisition, ACQ_EXCHANGE_KEY
    actual = _lock_acquisition(db, p, acq.id)
    need(actual.status == 'blocked_connector' and actual.data.get('actor_id') == p.user_id
        and job.data.get('acquisition_id') == actual.id
        and not actual.data.get('dispatch_started_at') and ACQ_EXCHANGE_KEY not in actual.data)
    row = db.scalar(select(Exchange.id).where(Exchange.workspace_id == p.workspace_id,
        Exchange.acquisition_id == actual.id).limit(1))
    remnants = db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ledger.ACTION, Audit.data['acquisition_id'].as_string() == actual.id).limit(1))
    need(row is None and remnants is None)
    source = db.scalar(select(Capability).where(Capability.id == actual.capability_id,
        Capability.workspace_id == p.workspace_id).execution_options(populate_existing=True))
    need(source is not None and sources.managed(db, p, source))
    checked = sources.validate_source(db, p, source, purpose='receipt')
    need(checked['capability'].id == actual.capability_id
        and checked['capability'].content_hash == actual.data['source_hash']
        and type(actual.idempotency_key) is str
        and sources.store.KEY.fullmatch(actual.idempotency_key) is not None)
    jobs, audits = _rows(db, p, actual)
    need(not any(item.action in {DISPATCH, UNSENT} for item in audits))
    pin = {'acquisition_id': actual.id, 'source_id': source.id, 'source_hash': source.content_hash,
        'actor_id': p.user_id, 'original_request_key_hash': digest(actual.idempotency_key)}
    data = _pre_data(pin, _job_pin(job, token))
    known = {item.id: item for item in jobs}
    need(len(known) == len(jobs) and job.id in known)
    for item in audits:
        need(item.action == PRE_UNSENT and type(item.data) is dict
            and item.data.get('job_id') in known)
        prior_job = known[item.data['job_id']]
        need(prior_job.data.get('actor_id') == p.user_id
            and prior_job.data.get('acquisition_id') == actual.id
            and item.actor_id == p.user_id
            and item.data == _pre_data(pin, _job_pin(prior_job, prior_job.lease_token)))
        _time(item.created_at)
    prior = [item for item in audits if item.action == PRE_UNSENT
        and type(item.data) is dict and item.data.get('job_id') == job.id]
    need(len(prior) <= 1)
    if prior:
        need(prior[0].actor_id == p.user_id and prior[0].data == data)
        return prior[0]
    # Use actual DB time as immutable Audit metadata, never a caller timestamp.
    recorded_at = _time(db.scalar(select(func.clock_timestamp())))
    item = Audit(id=uid(), created_at=recorded_at, workspace_id=p.workspace_id, actor_id=p.user_id,
        action=PRE_UNSENT, object_id=actual.id, data=data)
    db.add(item); db.flush()
    return item


def require_unsent_retry(db, p, acq, exchange, *, stage='preflight'):
    """Boolean provenance only; success never replaces any live send gate."""
    try:
        job, token = _leased(db, p)
        acq, row, pin = _scope(db, p, acq, exchange)
        _retry(db, p, acq, row, pin, job, token, stage)
        return True
    except (HTTPException, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        return False


def dispatch_audit_data(db, p, acq, exchange):
    """Exact send-intent metadata for the dispatcher's own final marker.

    This performs no write, sends nothing and must follow the transport's full
    live gates. Recording this intent prohibits a second POST even if IO fails.
    """
    job, token = _leased(db, p)
    acq, row, pin = _scope(db, p, acq, exchange)
    proofs = _retry(db, p, acq, row, pin, job, token, 'send')
    return _sent_data(pin, _job_pin(job, token), proofs)


def verify_retry_receipt_origin(db, p, exchange, origin):
    """Historical retry POST provenance only; never a dispatch/read grant."""
    try:
        need(isinstance(exchange, Exchange) and type(origin) is dict
            and origin.get('operation') == 'dispatch_post' and origin.get('read_enqueue_audit_id') is None)
        acq, row, pin = _cold_scope(db, p, exchange)
        need(origin.get('actor_id') == row.actor_id and origin.get('exchange_id') == row.id
            and origin.get('original_request_key') == acq.idempotency_key
            and origin.get('wire_request_hash') == row.data['freeze']['wire_frozen']['request_hash'])
        job = scoped(db, Job, origin['job_id'], p)
        need(job.id != row.dispatch_job_id and job.workspace_id == p.workspace_id
            and job.kind == 'dispatch_acquisition' and job.data.get('actor_id') == row.actor_id
            and type(origin.get('lease_token')) is str and bool(origin['lease_token']))
        _normal_retry(job, acq)
        jobs, audits = _rows(db, p, acq)
        proofs = _prior_proofs(pin, job.id, _facts(jobs), _audit_facts(audits),
            exchange_created_at=row.created_at)
        sent = [item for item in audits if item.action == DISPATCH]
        need(len(sent) == 1 and sent[0].actor_id == row.actor_id
            and sent[0].data == _sent_data(pin, _job_pin(job, origin['lease_token']), proofs))
        return True
    except (HTTPException, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        return False
