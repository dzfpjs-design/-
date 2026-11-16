"""Durable, same-Job evidence-consent wait before any paid/Invocation intent.

This module never grants consent, creates a Job/call, reserves a Goal or calls a
model. Callers commit preflight/scheduler changes under their existing fences.
All scheduling reuses actual current IAM/source/Goal and immutable file bytes.
"""
import copy

from fastapi import HTTPException
from sqlalchemy import func, select

from . import business_flow_review_store as safe
from . import business_reasoning_v197 as reasoning
from . import business_reasoning_protocols_v276 as protocols
from . import workflow_graph_scope as scope
from . import workflow_provenance_wait_v197 as original
from .contracts import digest
from .evidence import evidence_bytes
from .models import Audit, Capability, Evidence, Job, Membership, Task, User
from .security import Principal
from .services import audit

KEY = 'workflow_evidence_consent_wait_v199'
SCHEMA = 'workflow-evidence-consent-wait-ledger-v199'
RECORD = 'workflow-evidence-consent-wait-v199'
WAKE_RECORD = 'workflow-evidence-consent-wake-v199'
WAIT = 'workflow.graph.evidence_consent_wait_v199'
WAKE = 'workflow.graph.evidence_consent_wake_v199'
PHASE = 'waiting_evidence_consent'
TRANSPORT_HASH = 'evidence_consent_wait_hash'
MAX_WAITS = 64
MAX_BYTES = 2 * 1024 * 1024
ERROR = '原证据、模型授权、任务等待或独立审计无法核对；未创建新调用或费用预留'
IDENTITIES = ('task_id', 'actor_id', 'activation_id', 'graph_hash', 'cancel_epoch', 'input_hash', 'invocation_key')
RECORD_KEYS = {'schema', 'version', 'previous_hash', 'job_id', *IDENTITIES, 'node_id',
    'capability_id', 'capability_hash', 'model_profile_id', 'model_profile_hash',
    'goal_context_hash', 'attempts_at_wait', 'evidence_pins', 'missing_evidence_ids', 'record_hash'}
PIN_KEYS = {'evidence_id', 'sha256', 'media_type', 'size_bytes', 'version_at_wait', 'authorized_at_wait'}


def need(value):
    if not value: raise HTTPException(409, ERROR)


def _same(a, b): return safe._same(a, b)


def _sha(value): need(type(value) is str and safe.SHA.fullmatch(value) is not None)


def _family(cap):
    return (type(cap.data) is dict and type(cap.data.get('execution')) is dict
        and protocols.applies(cap.data['execution'].get('adapter')))


def evidence_ids(cap, inputs):
    return protocols.family(cap.data['execution'].get('adapter')).evidence_ids(inputs)


def _actor(db, workspace_id, user_id):
    user = db.scalar(select(User).where(User.id == user_id).execution_options(populate_existing=True))
    member = db.scalar(select(Membership).where(Membership.workspace_id == workspace_id,
        Membership.user_id == user_id).execution_options(populate_existing=True))
    need(user is not None and user.active is True and member is not None and member.role in {'admin', 'operator'})
    return Principal(user_id, workspace_id, member.role, 'evidence-consent-wait-v199')


def _grant_hash(row):
    return digest({'id': row.id, 'workspace_id': row.workspace_id, 'actor_id': row.actor_id,
        'action': row.action, 'object_id': row.object_id, 'data': row.data})


def _grants(db, evidence, model):
    """A JSON permission label alone is never an authorization receipt."""
    if model.id not in evidence.data.get('cloud_model_ids', []): return []
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == evidence.workspace_id,
        Audit.object_id == evidence.id, Audit.action.in_(['evidence.upload', 'evidence.cloud_authorize']))))
    result = []
    for row in rows:
        data = row.data
        if type(data) is not dict or data.get('sha256') != evidence.sha256: continue
        upload = (row.action == 'evidence.upload' and data.get('consent') is True
            and data.get('cloud_model_id') == model.id and data.get('media_type') == evidence.media_type)
        granted = (row.action == 'evidence.cloud_authorize' and data.get('model_id') == model.id
            and type(data.get('version')) is int and 1 <= data['version'] <= evidence.version)
        if upload or granted:
            safe.identity(row.id); safe.identity(row.actor_id)
            result.append({'evidence_id': evidence.id, 'audit_id': row.id, 'audit_hash': _grant_hash(row)})
    return sorted(result, key=lambda value: value['audit_id'])


def _model(db, p, cap):
    protocol = protocols.family(cap.data['execution'].get('adapter'))
    actual = protocol._fresh_published(db, p, cap.id)
    need(_family(actual) and actual.content_hash == cap.content_hash)
    need(actual.data['execution'].get('adapter') == cap.data['execution'].get('adapter'))
    model = protocol.validate_manifest(db, p, actual.data)
    need(model.id == actual.data['execution']['model_profile_id']
        and model.content_hash == actual.data['execution']['model_profile_hash'])
    return actual, model


def _files(db, p, ids, model):
    need(type(ids) is list and 1 <= len(ids) <= 3 and len(ids) == len(set(ids)))
    pins, grants = [], {}
    for identifier in ids:
        safe.identity(identifier)
        # Refresh before evidence_bytes/scoped can reuse an existing ORM row.
        evidence = db.scalar(select(Evidence).where(Evidence.workspace_id == p.workspace_id,
            Evidence.id == identifier).execution_options(populate_existing=True))
        need(evidence is not None and evidence.media_type in {'text/plain', 'image/png', 'image/jpeg'}
            and type(evidence.size_bytes) is int and evidence.size_bytes > 0)
        if evidence.media_type != 'text/plain':
            need(model.data['execution'].get('provider') == 'qwen'
                and model.data['execution'].get('supports_images') is True)
        current, content = evidence_bytes(db, p, identifier)
        need(current.id == identifier and current.sha256 == evidence.sha256
            and len(content) == evidence.size_bytes and type(evidence.version) is int and evidence.version >= 1)
        _sha(evidence.sha256)
        matches = _grants(db, evidence, model); grants[identifier] = matches
        pins.append({'evidence_id': identifier, 'sha256': evidence.sha256, 'media_type': evidence.media_type,
            'size_bytes': evidence.size_bytes, 'version_at_wait': evidence.version, 'authorized_at_wait': bool(matches)})
    return pins, grants


def _check_pins(record, pins, grants):
    need(len(record['evidence_pins']) == len(pins))
    for frozen, current in zip(record['evidence_pins'], pins):
        need(all(_same(frozen[key], current[key]) for key in ('evidence_id', 'sha256', 'media_type', 'size_bytes'))
            and current['version_at_wait'] >= frozen['version_at_wait'])
        # A file that was explicitly authorized when this wait began cannot
        # silently lose that consent while another file is still being checked.
        if frozen['authorized_at_wait']: need(bool(grants[frozen['evidence_id']]))


def _check_wake(proof, grants):
    need(all(any(_same(frozen, value) for value in grants[frozen['evidence_id']]) for frozen in proof['grants']))


def _wait_audit(job, record):
    return {'schema': RECORD, 'job_id': job.id, 'task_id': record['task_id'],
        'record_hash': record['record_hash'], 'record': copy.deepcopy(record)}


def _wake_audit(job, proof):
    return {'schema': WAKE_RECORD, 'job_id': job.id, 'task_id': job.data['task_id'], 'proof': copy.deepcopy(proof)}


def _inventory(db, task, job):
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == job.workspace_id,
        Audit.object_id == job.id, Audit.action.in_([WAIT, WAKE]))))
    ledger = job.data.get(KEY); transport = task.data.get('graph_jobs', {}).get(job.data.get('activation_id'), {})
    if ledger is None:
        need(not events and TRANSPORT_HASH not in transport)
        return {'schema': SCHEMA, 'history': [], 'active_hash': None, 'wake_proofs': []}
    safe._bytes(ledger, MAX_BYTES)
    need(type(ledger) is dict and set(ledger) == {'schema', 'history', 'active_hash', 'wake_proofs'}
        and ledger['schema'] == SCHEMA and type(ledger['history']) is list and 0 < len(ledger['history']) <= MAX_WAITS
        and type(ledger['wake_proofs']) is list)
    hashes = []; used = set(); previous = None
    for number, record in enumerate(ledger['history'], 1):
        need(type(record) is dict and set(record) == RECORD_KEYS and record['schema'] == RECORD
            and type(record['version']) is int and record['version'] == number and record['previous_hash'] == previous
            and record['job_id'] == job.id and record['task_id'] == task.id
            and all(_same(record[k], job.data[k]) for k in IDENTITIES)
            and record['node_id'] == task.data['graph_runtime']['activations'][job.data['activation_id']]['node_id']
            and type(record['attempts_at_wait']) is int and 0 <= record['attempts_at_wait'] <= job.attempts
            and record['record_hash'] == digest({k: v for k, v in record.items() if k != 'record_hash'}))
        for key in ('capability_id', 'model_profile_id'): safe.identity(record[key])
        for key in ('capability_hash', 'model_profile_hash'): _sha(record[key])
        pins = record['evidence_pins']
        need(type(pins) is list and 1 <= len(pins) <= 3
            and [v['evidence_id'] for v in pins] == job.data['inputs']['evidence_ids'])
        for pin in pins:
            need(type(pin) is dict and set(pin) == PIN_KEYS and type(pin['size_bytes']) is int and pin['size_bytes'] > 0
                and type(pin['version_at_wait']) is int and pin['version_at_wait'] >= 1
                and type(pin['authorized_at_wait']) is bool and pin['media_type'] in {'text/plain', 'image/png', 'image/jpeg'})
            safe.identity(pin['evidence_id']); _sha(pin['sha256'])
        need(record['missing_evidence_ids'] == [pin['evidence_id'] for pin in pins if not pin['authorized_at_wait']]
            and record['missing_evidence_ids'])
        matches = [row for row in events if row.action == WAIT and row.data.get('record_hash') == record['record_hash']]
        need(len(matches) == 1 and matches[0].actor_id == record['actor_id'] and _same(matches[0].data, _wait_audit(job, record)))
        used.add(matches[0].id); hashes.append(record['record_hash']); previous = record['record_hash']
    woken = []
    for proof in ledger['wake_proofs']:
        need(type(proof) is dict and set(proof) == {'schema', 'record_hash', 'grants', 'wake_hash'}
            and proof['schema'] == WAKE_RECORD and proof['record_hash'] in hashes
            and proof['record_hash'] not in woken and proof['wake_hash'] == digest({k: v for k, v in proof.items() if k != 'wake_hash'}))
        record = next(value for value in ledger['history'] if value['record_hash'] == proof['record_hash'])
        need(type(proof['grants']) is list and [value['evidence_id'] for value in proof['grants']] == [value['evidence_id'] for value in record['evidence_pins']])
        for grant in proof['grants']:
            need(type(grant) is dict and set(grant) == {'evidence_id', 'audit_id', 'audit_hash'})
            safe.identity(grant['audit_id']); _sha(grant['audit_hash'])
        matches = [row for row in events if row.action == WAKE and row.data.get('proof', {}).get('record_hash') == proof['record_hash']]
        need(len(matches) == 1 and matches[0].actor_id == record['actor_id'] and _same(matches[0].data, _wake_audit(job, proof)))
        used.add(matches[0].id); woken.append(proof['record_hash'])
    need(len(used) == len(events) and used == {row.id for row in events}
        and ledger['active_hash'] in {None, hashes[-1]} and ledger['active_hash'] not in woken
        and set(hashes) == set(woken) | ({ledger['active_hash']} if ledger['active_hash'] else set())
        and transport.get(TRANSPORT_HASH) == hashes[-1])
    return copy.deepcopy(ledger)


def _no_intent(db, task, job):
    from .workflow_graph_goal_guard_v133 import reservations
    need(original._original_call(db, job) is None
        and not any(value['invocation_key'] == job.data['invocation_key'] for value in reservations(task)))
    transport = task.data['graph_jobs'][job.data['activation_id']]
    need(transport.get('job_id') == job.id
        and not set(transport) & {'dispatch_committed', 'dispatch_ownership', 'dispatch_input_hash', 'dispatch_authorization'})
    return transport


def _current(db, p, task, job, record=None):
    from . import workflow_graph
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    actual, release, business, graph, state, activation, node = scope._frozen(db, p, task.id, job.data['activation_id'])
    scope._job_matches(actual, job, state, activation, node)
    need(actual.id == task.id and node['actor_id'] == p.user_id and activation['status'] == 'ready'
        and activation['id'] in state['eligible_ability_ids']
        and activation['epoch'] == state['cancel_epoch'] == job.data['cancel_epoch']
        and _same(original._scheduler_state(db, actual, job), node))
    workflow_graph.live(db, p, actual, release, business)
    if actual.data.get('native_goal_context') is not None:
        from .native_goals import guard
        guard(db, p, actual, job.data['invocation_key'])
    capability = db.scalar(select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.id == node['execution']['capability_id']).execution_options(populate_existing=True))
    need(capability is not None and _family(capability)
        and capability.content_hash == node['execution']['capability_hash'])
    capability, model = _model(db, p, capability)
    if record is not None:
        need(capability.id == record['capability_id'] and capability.content_hash == record['capability_hash']
            and model.id == record['model_profile_id'] and model.content_hash == record['model_profile_hash']
            and record['goal_context_hash'] == (digest(actual.data['native_goal_context']) if actual.data.get('native_goal_context') is not None else None))
    return actual, node, capability, model


def preflight(db, p, task, job, node, cap, inputs):
    """True: persist this same Job waiting; stop before every paid intent."""
    if not _family(cap): return False
    value = protocols.family(cap.data['execution'].get('adapter'))._inputs(inputs)
    task, job, actual_node = original._locked_preflight(db, p, task, job, cap, value)
    need(_same(actual_node, node)); _no_intent(db, task, job)
    ledger = _inventory(db, task, job); need(ledger['active_hash'] is None)
    actual_cap, model = _model(db, p, cap)
    pins, grants = _files(db, p, evidence_ids(actual_cap, value), model)
    if ledger['history']:
        prior = ledger['history'][-1]
        need(prior['capability_id'] == actual_cap.id and prior['capability_hash'] == actual_cap.content_hash
            and prior['model_profile_id'] == model.id and prior['model_profile_hash'] == model.content_hash
            and prior['goal_context_hash'] == (digest(task.data['native_goal_context'])
                if task.data.get('native_goal_context') is not None else None))
        _check_pins(prior, pins, grants)
        proof = next((value for value in ledger['wake_proofs'] if value['record_hash'] == prior['record_hash']), None)
        need(proof is not None); _check_wake(proof, grants)
    missing = [pin['evidence_id'] for pin in pins if not grants[pin['evidence_id']]]
    if not missing: return False
    need(len(ledger['history']) < MAX_WAITS)
    record = {'schema': RECORD, 'version': len(ledger['history']) + 1,
        'previous_hash': ledger['history'][-1]['record_hash'] if ledger['history'] else None,
        'job_id': job.id, **{k: copy.deepcopy(job.data[k]) for k in IDENTITIES}, 'node_id': actual_node['id'],
        'capability_id': actual_cap.id, 'capability_hash': actual_cap.content_hash,
        'model_profile_id': model.id, 'model_profile_hash': model.content_hash,
        'goal_context_hash': digest(task.data['native_goal_context']) if task.data.get('native_goal_context') is not None else None,
        'attempts_at_wait': job.attempts, 'evidence_pins': pins, 'missing_evidence_ids': missing}
    record['record_hash'] = digest(record); ledger['history'].append(record); ledger['active_hash'] = record['record_hash']
    safe._bytes(ledger, MAX_BYTES); job.data = {**job.data, KEY: ledger}
    transports = copy.deepcopy(task.data['graph_jobs']); transport = transports[job.data['activation_id']]
    transports[job.data['activation_id']] = {**transport, 'phase': PHASE, TRANSPORT_HASH: record['record_hash'],
        'reason': '本次业务分析需要先授权具体证据给已选模型；尚未调用模型或预留费用'}
    task.data = {**task.data, 'graph_jobs': transports}; task.version += 1
    audit(db, p, WAIT, job.id, _wait_audit(job, record))
    # No SQL after changing status: worker keeps its original final lease fence.
    job.status = PHASE
    return True


def _cancelled(task, job):
    state = task.data.get('graph_runtime', {})
    return (task.status in {'workflow_cancelled', 'workflow_stopped'} or state.get('status') in {'cancelled', 'stopped'}
        or task.data.get('native_goal_cancelled') or task.data.get('native_goal_timed_out')
        or state.get('cancel_epoch') != job.data.get('cancel_epoch'))


def scheduler_once(db, *, workspace_id=None, limit=64):
    need(type(limit) is int and 1 <= limit <= 256)
    if workspace_id is not None: safe.identity(workspace_id)
    query = select(Job).where(Job.kind == scope.KIND, Job.status == PHASE,
        Job.available_at <= func.clock_timestamp()).order_by(Job.available_at, Job.id).limit(limit)
    if workspace_id is not None: query = query.where(Job.workspace_id == workspace_id)
    count = 0
    for candidate in list(db.scalars(query)):
        task = db.scalar(select(Task).where(Task.workspace_id == candidate.workspace_id,
            Task.id == candidate.data.get('task_id')).with_for_update().execution_options(populate_existing=True))
        job = db.scalar(select(Job).where(Job.workspace_id == candidate.workspace_id, Job.id == candidate.id,
            Job.kind == scope.KIND, Job.status == PHASE).with_for_update().execution_options(populate_existing=True))
        if job is None: continue
        try:
            need(task is not None)
            if _cancelled(task, job):
                original._reject(task, job, 'cancelled', '原任务、目标或节点代次已停止，原授权等待保留且未调用模型'); continue
            ledger = _inventory(db, task, job); need(ledger['active_hash'] is not None)
            record = ledger['history'][-1]; transport = _no_intent(db, task, job)
            need(transport.get('phase') == PHASE and job.attempts == record['attempts_at_wait'])
            p = _actor(db, job.workspace_id, record['actor_id'])
            task, node, capability, model = _current(db, p, task, job, record)
            pins, grants = _files(db, p, evidence_ids(capability, job.data['inputs']), model); _check_pins(record, pins, grants)
            if any(not grants[pin['evidence_id']] for pin in pins): continue
        except (HTTPException, ValueError, KeyError, TypeError, AttributeError, IndexError):
            original._reject(task, job, 'blocked', ERROR); continue
        proof = {'schema': WAKE_RECORD, 'record_hash': record['record_hash'],
            'grants': [grants[pin['evidence_id']][0] for pin in pins]}
        proof['wake_hash'] = digest(proof)
        ledger['wake_proofs'].append(proof); ledger['active_hash'] = None; safe._bytes(ledger, MAX_BYTES)
        job.data = {**job.data, KEY: ledger}; job.status = 'queued'
        transports = copy.deepcopy(task.data['graph_jobs']); transports[job.data['activation_id']] = {**transport,
            'phase': 'queued', 'reason': '原证据已明确授权；继续原任务，执行前仍重新核对权限和来源'}
        task.data = {**task.data, 'graph_jobs': transports}; task.version += 1
        audit(db, p, WAKE, job.id, _wake_audit(job, proof)); count += 1
    return count


def can_start_after_wait(db, task, job):
    """Read-only no-new-attempt exception; not dispatch/source/budget authority."""
    actual_task = db.scalar(select(Task).where(Task.workspace_id == task.workspace_id,
        Task.id == task.id).execution_options(populate_existing=True))
    actual = db.scalar(select(Job).where(Job.workspace_id == task.workspace_id,
        Job.id == job.id, Job.kind == scope.KIND).execution_options(populate_existing=True))
    try:
        need(actual_task is not None and actual is not None and actual.status in {'queued', 'running'}
            and not _cancelled(actual_task, actual))
        ledger = _inventory(db, actual_task, actual); need(ledger['history'] and ledger['active_hash'] is None)
        record = ledger['history'][-1]; proof = next(value for value in ledger['wake_proofs'] if value['record_hash'] == record['record_hash'])
        transport = _no_intent(db, actual_task, actual); need(transport.get('phase') in {'queued', 'running'})
        p = _actor(db, actual.workspace_id, record['actor_id'])
        actual_task, node, capability, model = _current(db, p, actual_task, actual, record)
        pins, grants = _files(db, p, evidence_ids(capability, actual.data['inputs']), model); _check_pins(record, pins, grants)
        _check_wake(proof, grants)
        return True
    except (HTTPException, ValueError, KeyError, TypeError, AttributeError, IndexError, StopIteration):
        return False


def poll_once(workspace_id=None):
    from sqlalchemy.orm import Session
    from .db import engine
    with Session(engine()) as db:
        count = scheduler_once(db, workspace_id=workspace_id)
        db.commit()
        return count
