"""Worker reservations and cold actual-call inventory for threshold studies.

Core study records/commands/state Audits remain in the store. No function here
invokes a tool, creates observation state or issues trial authority. Core is
imported lazily so its read path can verify this execution inventory in turn.
"""
from copy import deepcopy
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_, select


JOB_KIND = 'evaluate_threshold_study_v149'
RESULT_ACTION = 'threshold_study.result_v149'
RESULT_SCHEMA = 'threshold-study-result-v149'
_ERROR = '原比较后台任务、预约或实际调用回执无法完整核对，请保留原记录并核查'
_LIVE = {'queued', 'running'}
_TERMINAL = {'completed', 'failed', 'cancelled'}
_RESERVATION = {'key', 'sample_id', 'side', 'capability_id', 'capability_hash',
    'input_hash', 'job_id', 'lease_token_hash'}
_RECEIPT = {'key', 'sample_id', 'side', 'invocation_id', 'capability_id',
    'capability_hash', 'input_hash', 'output_hash', 'lease_token_hash', 'output'}
_CALL_STATES = {'running', 'unknown', 'failed', 'blocked_evidence',
    'blocked_model', 'succeeded', 'not_executed'}


def _store():
    from . import threshold_study_store_v149
    return threshold_study_store_v149


def _need(condition):
    if not condition:
        raise HTTPException(409, _ERROR)


def _same(left, right):
    from .typed_payload_codec_v131 import canonical_json
    return canonical_json(left) == canonical_json(right)


def _uuid(value):
    try:
        _need(type(value) is str and str(UUID(value)) == value)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(409, _ERROR) from None
    return value


def _job_context(p, row, job):
    """Original Job identity only; no current lease or business eligibility."""
    _need(row.workspace_id == p.workspace_id == job.workspace_id
        and job.kind == JOB_KIND and job.id == row.data['job_id']
        and type(job.data) is dict and job.data.get('eval_id') == row.id
        and job.data.get('candidate_id') == row.candidate_id
        and job.data.get('freeze_hash') == row.data['freeze_hash']
        and job.data.get('actor_id') == row.data['job_actor_id'])
    _uuid(job.id); _uuid(row.data['job_actor_id'])
    _need(row.data['freeze']['candidate']['candidate_id'] == row.candidate_id)


def _descriptors(row):
    """Derive every permitted key/input/output from the original frozen set."""
    from .threshold_evolution_gate_v147 import compare_frozen_thresholds
    s = _store()
    try:
        frozen = row.data['freeze']
        candidate = frozen['candidate']
        samples = frozen['samples']
        _need(type(samples) is list and 1 <= len(samples) <= 30)
        values = [{'id': sample['id'],
            'values': {sample['basis']['field']: deepcopy(sample['basis']['actual_value'])},
            'expected': sample['feedback_snapshot']['data']['label']} for sample in samples]
        comparison = compare_frozen_thresholds(candidate['baseline_manifest'],
            candidate['candidate_manifest'], values, frozen['criteria'])
        _need(len(comparison['per_sample']) == len(samples))
        descriptions = {}
        for sample, original, result in zip(samples, values, comparison['per_sample'], strict=True):
            _uuid(sample['id'])
            _need(result['id'] == sample['id'] and result['issue'] is None)
            for side in ('baseline', 'candidate'):
                matched = result[side + '_matched']
                _need(type(matched) is bool)
                manifest = candidate[side + '_manifest']
                capability_id = candidate['baseline_id' if side == 'baseline' else 'candidate_capability_id']
                _uuid(capability_id)
                key = f'threshold-study:{row.id}:{sample["id"]}:{side}'
                _need(key not in descriptions)
                inputs = {'attributes': deepcopy(original['values'])}
                rule = manifest['specification']['rules'][0]
                anomalies = ([{'field': rule['field'], 'value': deepcopy(inputs['attributes'][rule['field']]),
                    'rule_id': rule['id'], 'severity': rule.get('severity', 'warning')}] if matched else [])
                output = {'attributes': deepcopy(inputs['attributes']), 'anomalies': anomalies,
                    'method': 'registered_numeric_rules'}
                descriptions[key] = {'key': key, 'sample_id': sample['id'], 'side': side,
                    'capability_id': capability_id, 'capability_hash': s.hash_value(manifest),
                    'input_hash': s.hash_value(inputs), 'inputs': inputs, 'output': output,
                    'manifest': manifest, 'sample': sample}
        return descriptions, comparison
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError):
        raise HTTPException(409, _ERROR) from None


def _maps(row, descriptions):
    reservations, receipts = row.data['reservations'], row.data['receipts']
    _need(type(reservations) is dict and type(receipts) is dict
        and set(reservations) <= set(descriptions) and set(receipts) <= set(reservations))
    return reservations, receipts


def _reservation(value, expected, job):
    _need(type(value) is dict and set(value) == _RESERVATION)
    _need(_same({key: value[key] for key in _RESERVATION - {'lease_token_hash'}},
        {**{key: expected[key] for key in ('key', 'sample_id', 'side',
            'capability_id', 'capability_hash', 'input_hash')}, 'job_id': job.id}))
    token_hash = value['lease_token_hash']
    _need(type(token_hash) is str and len(token_hash) == 64
        and set(token_hash) <= set('0123456789abcdef'))


def _call(call, expected, reservation, job):
    s = _store()
    data = call.data
    _uuid(call.id)
    _need(call.workspace_id == job.workspace_id and call.idempotency_key == expected['key']
        and call.capability_id == expected['capability_id'] and call.status in _CALL_STATES
        and type(data) is dict and data.get('job_id') == job.id
        and data.get('role') == 'perception' and 'approval' in data and data['approval'] is None
        and data.get('side_effect') is False and data.get('input_hash') == expected['input_hash']
        and data.get('capability_hash') == expected['capability_hash'])
    _uuid(data.get('lease_token'))
    _need(s.hash_value({'lease_token': data['lease_token']}) == reservation['lease_token_hash'])
    if 'inputs' in data:
        _need(_same(data['inputs'], expected['inputs']))
    if 'capability_snapshot' in data:
        _need(_same(data['capability_snapshot'], expected['manifest']))
    if call.status == 'succeeded':
        _need('output' in data and _same(data['output'], expected['output']))


def _receipt(call, expected, reservation):
    s = _store()
    _need(call.status == 'succeeded')
    return {**{key: expected[key] for key in ('key', 'sample_id', 'side',
        'capability_id', 'capability_hash', 'input_hash')}, 'invocation_id': call.id,
        'output_hash': s.hash_value(call.data['output']),
        'lease_token_hash': reservation['lease_token_hash'], 'output': deepcopy(call.data['output'])}


def _result_audits(db, p, row):
    from .models import Audit
    return list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == RESULT_ACTION, or_(Audit.object_id == row.id,
            Audit.data['evaluation_id'].as_string() == row.id))))


def _public_result(comparison):
    _need(type(comparison) is dict and set(comparison) == {
        'per_sample', 'baseline', 'candidate', 'eligible_for_trial', 'issues'}
        and type(comparison['eligible_for_trial']) is bool)
    return {key: deepcopy(comparison[key]) for key in ('per_sample', 'baseline', 'candidate', 'issues')} | {
        'mechanism_gate_passed': comparison['eligible_for_trial']}


def _inventory(db, p, row, job, require_complete=False):
    from .models import Invocation
    s = _store()
    _job_context(p, row, job)
    descriptions, comparison = _descriptors(row)
    reservations, receipts = _maps(row, descriptions)
    calls = list(db.scalars(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        or_(Invocation.idempotency_key.startswith('threshold-study:' + row.id + ':'),
            Invocation.data['job_id'].as_string() == job.id))
        .execution_options(populate_existing=True)))
    by_key = {}
    for call in calls:
        key = call.idempotency_key
        _need(key in descriptions and key in reservations and key not in by_key)
        by_key[key] = call
    _need(set(by_key) == set(reservations))
    for key, reservation in reservations.items():
        expected, call = descriptions[key], by_key[key]
        _reservation(reservation, expected, job)
        _call(call, expected, reservation, job)
        if key in receipts:
            _need(type(receipts[key]) is dict and set(receipts[key]) == _RECEIPT
                and _same(receipts[key], _receipt(call, expected, reservation)))
    complete = require_complete or row.status == 'completed'
    if complete:
        _need(set(reservations) == set(receipts) == set(descriptions)
            and all(call.status == 'succeeded' for call in calls))
    audits = _result_audits(db, p, row)
    if row.status == 'completed':
        result = _public_result(comparison)
        _need(_same(row.data['result'], result) and len(audits) == 1)
        expected = {'schema': RESULT_SCHEMA, 'evaluation_id': row.id, 'job_id': job.id,
            'freeze_hash': row.data['freeze_hash'], 'result_hash': s.hash_value(result),
            'call_ids': [by_key[key].id for key in descriptions], 'record_hash': row.record_hash}
        _need(audits[0].object_id == row.id and audits[0].actor_id == row.data['job_actor_id']
            and _same(audits[0].data, expected))
    else:
        _need(row.data['result'] is None and not audits)
    return descriptions, comparison, by_key


def checked_execution_inventory(db, p, row, job, require_complete=False):
    """Read original execution evidence; no current gate, locks or write-back."""
    _need(type(require_complete) is bool)
    _inventory(db, p, row, job, require_complete)


def _owned_record(db, p, job, lock=True):
    from .models import Job
    from .leases import LeaseLost
    s = _store()
    actual = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
        Job.id == job.id).execution_options(populate_existing=True))
    _need(actual is not None and type(actual.data) is dict
        and actual.kind == JOB_KIND and actual.data.get('actor_id') == p.user_id)
    lease = db.info.get('worker_lease')
    _need(type(lease) is dict and lease.get('job_id') == actual.id)
    db_now = db.scalar(select(func.clock_timestamp()))
    if (actual.status != 'running' or actual.lease_token != lease.get('lease_token')
        or not actual.lease_until or actual.lease_until <= db_now):
        raise LeaseLost('后台比较租约已失效，旧执行不能保存新结果')
    _uuid(lease.get('lease_token'))
    row = s.get_record(db, p, 'evaluation', actual.data['eval_id'], lock=lock)
    _job_context(p, row, actual)
    return row, actual


def owned_evaluation(db, p, job, lock=True):
    row, actual = _owned_record(db, p, job, lock)
    if row.status not in _TERMINAL:
        _need(row.status in _LIVE)
        from .relations import lock_current_actor
        lock_current_actor(db, p)
        _store().fresh_evaluation(db, p, row)
    return row


def _selected(row, sample, side, descriptions):
    _need(type(side) is str and side in {'baseline', 'candidate'} and type(sample) is dict)
    key = f'threshold-study:{row.id}:{sample["id"]}:{side}'
    _need(key in descriptions and _same(sample, descriptions[key]['sample']))
    return key, descriptions[key]


def reserve_call(db, p, job, evaluation, sample, side):
    from .models import Capability
    from .services import published
    from .relations import lock_current_actor
    s = _store()
    row, actual = _owned_record(db, p, job)
    _need(row.id == evaluation.id and row.status in _LIVE
        and not actual.data.get('recovery_only') and 1 <= actual.attempts <= 3)
    lock_current_actor(db, p)
    # Exclusive locks avoid two Evaluation workers upgrading shared cap locks
    # inside the original Gateway. Both IDs always use the same sorted order.
    frozen = row.data['freeze']['candidate']
    for identity in sorted({frozen['baseline_id'], frozen['candidate_capability_id']}):
        cap = db.scalar(select(Capability).where(Capability.workspace_id == p.workspace_id,
            Capability.id == identity).with_for_update().execution_options(populate_existing=True))
        _need(cap is not None)
        cap = published(db, p, identity, 'tool')
        manifest = frozen['baseline_manifest' if identity == frozen['baseline_id'] else 'candidate_manifest']
        execution, policy = cap.data['execution'], cap.data['data_policy']
        _need(_same(cap.data, manifest) and cap.content_hash == s.hash_value(manifest)
            and execution.get('adapter') == 'builtin' and execution.get('handler') == 'numeric_rules'
            and execution.get('side_effect', False) is False and 'perception' in cap.data['allowed_agent_roles']
            and policy.get('deployment') == 'local' and policy.get('cloud_allowed') is False
            and policy.get('external_allowed') is False)
    s.fresh_evaluation(db, p, row)
    descriptions, _, calls = _inventory(db, p, row, actual)
    key, expected = _selected(row, sample, side, descriptions)
    _need(key not in row.data['reservations'] and key not in calls)
    reservation = {**{name: expected[name] for name in ('key', 'sample_id', 'side',
        'capability_id', 'capability_hash', 'input_hash')}, 'job_id': actual.id,
        'lease_token_hash': s.hash_value({'lease_token': db.info['worker_lease']['lease_token']})}
    row.status = 'running'
    row.data = {**row.data, 'reservations': {**row.data['reservations'], key: reservation}}
    s.change_state(db, p, row, 'call_reserved', job_id=actual.id)
    # Deliberately no commit: original Gateway INSERT/running commit must make
    # this reservation and its independent state Audit visible atomically.
    return {'key': key, 'inputs': deepcopy(expected['inputs']), 'capability_id': expected['capability_id']}


def saved_call(db, p, job, evaluation, sample, side):
    row, actual = _owned_record(db, p, job)
    _need(row.id == evaluation.id)
    descriptions, _, calls = _inventory(db, p, row, actual)
    key, _ = _selected(row, sample, side, descriptions)
    if key not in calls:
        return None
    _need(calls[key].status == 'succeeded')
    return calls[key]


def accept_call(db, p, job, evaluation, sample, side, call):
    s = _store()
    row, actual = _owned_record(db, p, job)
    _need(row.id == evaluation.id)
    if row.status == 'cancelled':
        return False
    _need(row.status in _LIVE)
    descriptions, _, calls = _inventory(db, p, row, actual)
    key, expected = _selected(row, sample, side, descriptions)
    _need(key in calls and calls[key].id == call.id and calls[key].status == 'succeeded')
    receipt = _receipt(calls[key], expected, row.data['reservations'][key])
    if key in row.data['receipts']:
        _need(_same(row.data['receipts'][key], receipt))
        return True
    row.data = {**row.data, 'receipts': {**row.data['receipts'], key: receipt}}
    s.change_state(db, p, row, 'call_accepted', job_id=actual.id)
    db.commit()
    return True


def complete_evaluation(db, p, job, evaluation, comparison):
    from .models import Audit
    s = _store()
    row, actual = _owned_record(db, p, job)
    _need(row.id == evaluation.id)
    if row.status in _TERMINAL:
        return row.status
    _need(row.status in _LIVE)
    descriptions, original, calls = _inventory(db, p, row, actual, require_complete=True)
    _need(_same(comparison, original))
    try:
        s.fresh_evaluation(db, p, row)
    except HTTPException as error:
        if error.status_code not in {404, 409, 422}:
            raise
        return fail_evaluation(db, p, actual, '比较原依据已变化，已保存的实际调用和进度保留，请重新核查后另建评估')
    result = _public_result(original)
    row.status = 'completed'
    row.data = {**row.data, 'result': result, 'error': None}
    s.change_state(db, p, row, 'completed', job_id=actual.id)
    details = {'schema': RESULT_SCHEMA, 'evaluation_id': row.id, 'job_id': actual.id,
        'freeze_hash': row.data['freeze_hash'], 'result_hash': s.hash_value(result),
        'call_ids': [calls[key].id for key in descriptions], 'record_hash': row.record_hash}
    db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=RESULT_ACTION,
        object_id=row.id, data=details))
    db.flush()
    db.commit()
    return 'completed'


def fail_evaluation(db, p, job, reason):
    s = _store()
    row, actual = _owned_record(db, p, job)
    if row.status in _TERMINAL:
        return row.status
    _need(row.status in _LIVE)
    safe_reason = reason if type(reason) is str and reason.strip() else _ERROR
    row.status = 'failed'
    row.data = {**row.data, 'error': safe_reason[:2000]}
    s.change_state(db, p, row, 'failed', job_id=actual.id)
    # Original Worker failure handling owns this final transaction's commit.
    return 'failed'
