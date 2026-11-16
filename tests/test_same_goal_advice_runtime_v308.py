"""Actual pure projection/limited transport/parser and protocol journal checks.

The original Start fixture declares source, permission, official price and DB
IO spies. These tests do not claim real PG leases, money holds, provider HTTP,
worker atomicity or restart durability. Root validates those actual surfaces.
"""
import base64
import copy
import hashlib
import json
import sys
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_runtime_v308 as s
from generic_agents import business_reasoning_capture_v275 as capture_protocol
from generic_agents import gateway
from generic_agents.contracts import digest
from generic_agents.security import Principal
from tests.test_goal_evaluation_append_command_v297 import predicate
from tests.test_same_goal_advice_contracts_v302 import ref
from tests.test_same_goal_advice_execution_v308 import scene as execution_scene, _sdk


@pytest.fixture
def scene(monkeypatch):
    t = execution_scene.__wrapped__(monkeypatch)
    t.start(); call = _sdk(t); call.version = 1
    value = t.checked(); job = value['job']
    job.status = 'running'; job.lease_token = ref('current_actual_owner_token')['id']
    job.lease_until = t.clock + timedelta(minutes=3)
    t.rows.update({s.Task: [t.task], s.CloudCall: []})
    t.call = call; t.send_job = job; t.execution = value
    t.inputs = s.execution.input_for(t.plan, value['snapshot'])
    t.key = job.idempotency_key; t.commits = 0; t.readers = []; t.live_checks = []; t.history_checks = []
    t.owner = {'job_id': job.id, 'lease_token': job.lease_token}; t.fenced = True
    t.allow_reader = True

    class DB:
        no_autoflush = nullcontext()
        def scalar(self, statement):
            if 'clock_timestamp' in str(statement):
                # Explicit installed-lease SQL IO boundary, not a fake fence:
                # assert actual bound token/Job/scope and return current DB time result.
                assert statement.column_descriptions[0]['entity'] is s.Job
                params = statement.compile().params
                assert t.p.workspace_id in params.values() and job.id in params.values()
                assert t.owner['lease_token'] in params.values() and 'running' in params.values()
                return job.id if job.status == 'running' and job.lease_until > t.clock and job.lease_token == t.owner['lease_token'] else None
            cls = statement.column_descriptions[0]['entity']
            assert cls in t.rows
            rows = [row for row in t.rows[cls] if predicate(statement.whereclause, row)]
            assert len(rows) <= 1
            if statement._for_update_arg is not None: t.locks.append((cls, True))
            return rows[0] if rows else None
        def scalars(self, statement):
            cls = statement.column_descriptions[0]['entity']; assert cls in t.rows
            return [row for row in t.rows[cls] if predicate(statement.whereclause, row)]
        def add(self, row):
            assert type(row) in {s.Audit, s.CloudCall}
            t.rows[type(row)].append(row)
        def flush(self): t.flushes += 1
        def commit(self): t.commits += 1

    t.db = DB()
    def reader(db, p, *, write=False):
        t.readers.append((p.user_id, write))
        if not t.allow_reader: raise HTTPException(403, 'current reader IO refused')
    monkeypatch.setattr(s.store, '_reader', reader)
    monkeypatch.setattr(s.leases, 'ownership', lambda db: dict(t.owner))
    monkeypatch.setattr(s.leases, 'fenced', lambda db: t.fenced)
    monkeypatch.setattr(s, 'lock_current_actor', lambda *args: t.events.append('current_business_writer_IO'))

    def current(db, p, cap, inputs, key):
        t.live_checks.append((p.user_id, key))
        assert cap.id == t.advice.id and inputs == t.inputs and key == t.key
        if not t.allowed: raise HTTPException(409, 'actual current head or source IO refused')
        return t.advice, t.model, t.task, job, t.plan, copy.deepcopy(value['snapshot'])
    monkeypatch.setattr(s, '_current', current)
    def historical(db, p, cap, inputs, key, invocation):
        t.history_checks.append(invocation.id)
        assert cap.id == t.advice.id and invocation.id == call.id and invocation.workspace_id == p.workspace_id
        assert inputs == t.inputs and key == t.key
        # Only original custody IO is spied. Pure frozen request, projection,
        # actual transport.prepare and independent local Audit checks run.
        return {'task': t.task, 'plan': t.plan, 'prep': copy.deepcopy(t.prep),
            'snapshot': copy.deepcopy(value['snapshot']), 'start': value['start'],
            'preview': value['preview'], 'job': job}
    monkeypatch.setattr(s, '_history', historical)
    # Financial custody is an explicit IO boundary in this protocol-only
    # fixture. The spending suite checks actual HOLD/INTENT payloads separately.
    from generic_agents import same_goal_advice_spending_v308 as spending
    def money_original(db, p, context, ledger, *, require_intent=True):
        assert context['invocation'].id == call.id and require_intent is True
        state = call.data[s.STATE_KEY]
        assert state['capture'] is not None and ledger.id == state['call_id']
        assert context['frozen_request_hash'] == state['request']['request_hash']
        assert context['prepared'] == state['request']['prepared']
        assert context['price_ref'] == context['preview'].data['price_basis_ref']
        return {'purpose': 'explicit-financial-custody-IO-spy-not-authority'}
    monkeypatch.setattr(spending, 'checked_original_hold', money_original)
    t.prepare = lambda: s.prepare_request(t.db, t.p, t.advice, t.inputs, t.key)
    t.state = lambda actor=None: s._checked_state_for_actor(t.db, t.p, t.advice, t.inputs,
        t.key, call, actor_id=actor or t.prep['business_actor_id'])
    t.event = lambda action, body, object_id=None: _event(t, action, body, object_id=object_id)
    return t


def _event(t, action, body, *, object_id=None):
    event = s.Audit(id=ref(action + ':' + str(len(t.rows[s.Audit])))['id'], workspace_id=t.p.workspace_id,
        actor_id=t.prep['business_actor_id'], action=action, object_id=object_id or t.call.id,
        created_at=t.clock, data=copy.deepcopy(body))
    t.rows[s.Audit].append(event); return event


def _prepared(t):
    _model, frozen = t.prepare()
    t.call.data = {**t.call.data, s.STATE_KEY: {'schema': s.STATE, 'request': frozen, 'call_id': None, 'capture': None}}
    t.event(s.PREPARED, s._anchor(frozen)); return frozen


def _opinion(cloud):
    facts = [fact['fact_ref'] for fact in cloud['facts']]
    return {'schema': 'business-decision-review-value-v284', 'candidate_reviews': [
        {'candidate_ref': row['candidate_ref'], 'suitability': 'uncertain', 'reasoning': 'Compare the actual supplied facts.',
            'supporting_fact_refs': facts, 'blocking_conditions': []} for row in cloud['candidates']],
        'source_reviews': [{'source_ref': row['source_ref'],
            'assessment': 'reviewed' if row['readability'] == 'readable' else 'unreadable',
            'reason': 'Only the actual supplied excerpt was reviewed.'} for row in cloud['sources']],
        'conflicts': copy.deepcopy(cloud['known_conflicts']),
        'recommendation': {'candidate_ref': cloud['candidates'][0]['candidate_ref'],
            'reason': 'Proposed for human review; no execution permission is conferred.', 'supporting_fact_refs': facts}}


def _captured(t, *, ledger_status='succeeded', sdk_status='running', model_fault=None):
    frozen = _prepared(t); model = frozen['model_snapshot']['execution']
    call_id = ref('real_cloud_ledger')['id']
    receipt = {'call_id': call_id, 'http_status': 200, 'provider': model['provider'], 'model': model['model'],
        'request_id': 'original-provider-request', 'usage': {'prompt_tokens': 40, 'completion_tokens': 60, 'total_tokens': 100}}
    opinion = _opinion(frozen['execution_snapshot']['preview']['cloud_request']['decision_input'])
    if model_fault == 'wrong_ref': opinion['recommendation']['candidate_ref'] = 'not_an_original_candidate'
    raw = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(opinion)}}],
        'usage': receipt['usage']}, ensure_ascii=False).encode('utf-8')
    capture = {'raw_base64': base64.b64encode(raw).decode('ascii'), 'raw_sha256': hashlib.sha256(raw).hexdigest(), 'receipt': receipt}
    state = t.call.data[s.STATE_KEY]; state.update(call_id=call_id, capture=capture)
    ledger = s.CloudCall(id=call_id, workspace_id=t.p.workspace_id, capability_id=t.model.id,
        status=ledger_status, reserved_tokens=frozen['prepared']['token_bound'], charged_tokens=100,
        data={'actor_id': t.prep['business_actor_id'], 'request_hash': frozen['prepared']['request_hash'],
            'dispatch_options_hash': frozen['prepared']['dispatch_options_hash'], 'receipt': copy.deepcopy(receipt),
            'gateway_dispatch': {'identity': {'suggestion_id': t.call.id, 'job_id': t.send_job.id, 'task_id': t.task.id,
                'request_hash': frozen['request_hash'], 'workspace_id': t.p.workspace_id,
                'actor_id': t.prep['business_actor_id'], 'capability_id': t.model.id}}})
    t.rows[s.CloudCall].append(ledger)
    t.event(s.RESERVED, {**s._anchor(frozen), 'call_id': call_id})
    t.event(s.CAPTURED, {**s._anchor(frozen), 'call_id': call_id, 'raw_sha256': capture['raw_sha256'], 'receipt_hash': digest(receipt)})
    t.call.status = sdk_status
    t.opinion = opinion; t.ledger = ledger; t.original_raw = raw
    return state


def _succeeded(t):
    state = _captured(t, sdk_status='succeeded')
    result, _ = s._parsed(state)
    t.call.data = {**t.call.data, 'output': result}
    t.event('capability.invoke', {'status': 'succeeded'})
    return result


def test_true_limited_transport_and_every_frozen_pin_are_rederived_on_cold_read(scene, monkeypatch):
    t = scene; actual_prepare = s.transport.prepare; calls = []
    def preparing(*args, **kwargs):
        calls.append(kwargs); return actual_prepare(*args, **kwargs)
    monkeypatch.setattr(s.transport, 'prepare', preparing)
    frozen = _prepared(t); state = t.state()
    assert calls == [{'output_token_limit': 1024}, {'output_token_limit': 1024}]
    assert frozen['prepared']['options']['max_tokens'] == 1024 < t.model.data['execution']['max_tokens']
    assert frozen['messages'] != frozen['prepared']['messages']
    assert frozen['prepared']['request_hash'] == digest(frozen['prepared']['messages'])
    assert frozen['request_hash'] != frozen['prepared']['request_hash']
    assert state['request'] == frozen and s.evidence_ids_from_request(frozen) == t.frame['evidence_ids']
    assert not {'activation_id', 'node_id', 'scope_hash'} & set(frozen['pin'])
    state['request']['pin'].clear(); assert t.state()['request']['pin']
    assert t.commits == 0


@pytest.mark.parametrize('fault', ['projection', 'messages', 'wire_options', 'limit', 'model', 'prep',
    'preview', 'input', 'creator_token', 'prepared_audit', 'foreign_state', 'actor', 'evidence_order'])
def test_rehashed_frozen_request_cannot_replace_actual_originals_or_prepared_journal(scene, fault):
    t = scene; _prepared(t); state = t.call.data[s.STATE_KEY]; frozen = state['request']
    if fault == 'projection': frozen['cloud_projection']['aliases'].clear()
    elif fault == 'messages': frozen['messages'][0]['content'] += ' tampered'
    elif fault == 'wire_options': frozen['prepared']['options']['max_tokens'] = 2048
    elif fault == 'limit': frozen['output_token_limit'] = 2048
    elif fault == 'model': frozen['model_snapshot']['execution']['max_tokens'] = 8192
    elif fault == 'prep': frozen['preparation']['instruction'] += ' tampered'
    elif fault == 'preview': frozen['preview_record']['record_hash'] = '0' * 64
    elif fault == 'input': frozen['inputs']['preview_hash'] = '0' * 64
    elif fault == 'creator_token': t.call.data['lease_token'] = ref('not_original_creator')['id']
    elif fault == 'prepared_audit':
        t.rows[s.Audit] = [row for row in t.rows[s.Audit] if row.action != s.PREPARED]
    elif fault == 'foreign_state': t.call.data['business_goal_ready_v288'] = {}
    elif fault == 'actor': frozen['pin']['actor_id'] = ref('different_actor')['id']
    else: frozen['evidence_pins'].reverse()
    frozen['request_hash'] = digest({key: value for key, value in frozen.items() if key != 'request_hash'})
    # Self-consistent private hash cannot substitute for the independent
    # original Preview, original SDK identity and protected PREPARED Audit.
    with pytest.raises(HTTPException): t.state()
    assert t.commits == 0


@pytest.mark.parametrize('fault', ['missing_reserved', 'missing_capture', 'ledger_identity', 'ledger_model',
    'raw_bytes', 'receipt', 'call_id', 'duplicate_capture'])
def test_actual_raw_ledger_and_independent_capture_audits_are_all_required(scene, fault):
    t = scene; state = _captured(t)
    if fault in {'missing_reserved', 'missing_capture'}:
        action = s.RESERVED if fault == 'missing_reserved' else s.CAPTURED
        t.rows[s.Audit] = [row for row in t.rows[s.Audit] if row.action != action]
    elif fault == 'ledger_identity': t.ledger.data['gateway_dispatch']['identity']['request_hash'] = '0' * 64
    elif fault == 'ledger_model': t.ledger.capability_id = ref('wrong_model')['id']
    elif fault == 'raw_bytes': state['capture']['raw_base64'] = base64.b64encode(b'changed').decode('ascii')
    elif fault == 'receipt': state['capture']['receipt']['provider'] = 'qwen'
    elif fault == 'call_id': state['call_id'] = ref('wrong_ledger')['id']
    else:
        event = next(row for row in t.rows[s.Audit] if row.action == s.CAPTURED)
        t.event(s.CAPTURED, event.data)
    with pytest.raises(HTTPException): t.state()


def test_historical_first_answer_survives_current_head_price_permission_and_creator_lease_expiry(scene, monkeypatch):
    t = scene; state = _captured(t)
    creator = t.call.data['lease_token']; t.send_job.lease_token = ref('reclaimed_actual_owner')['id']
    t.owner['lease_token'] = t.send_job.lease_token; t.clock += timedelta(days=2)
    t.plan.status = 'cancelled'; t.allowed = False; t.allow_reader = False
    def no_current(*args, **kwargs): pytest.fail('raw custody must not ask current live/reader/price authorization')
    monkeypatch.setattr(s, '_current', no_current)
    monkeypatch.setattr(s.store, '_reader', no_current)
    before = copy.deepcopy(t.call.data[s.STATE_KEY]['capture'])
    assert t.state() == state and t.call.data['lease_token'] == creator != t.send_job.lease_token
    result, receipt = s._parsed(t.state())
    assert result['model_value'] == t.opinion and all(result[key] is False for key in s.c.FLAGS)
    assert receipt['usage']['total_tokens'] == 100 and t.call.data[s.STATE_KEY]['capture'] == before
    assert t.commits == 0


def test_capture_without_first_answer_cannot_reenter_transport_or_reserve(scene, monkeypatch):
    t = scene; _prepared(t)
    monkeypatch.setattr(s.transport, 'call', lambda *args, **kwargs: pytest.fail('no second provider call'))
    monkeypatch.setattr(s, '_settle_capture', lambda *args: pytest.fail('no captured usage to settle'))
    with pytest.raises(gateway.ModelBlocked): s.recover(t.db, t.p, t.advice, t.inputs, t.key)
    assert t.commits == 0 and not t.rows[s.CloudCall]


def test_actual_first_answer_recovery_reparses_same_bytes_and_only_settles_existing_ledger(scene, monkeypatch):
    t = scene; _captured(t, sdk_status='failed'); settlements = []
    monkeypatch.setattr(s, '_settle_capture', lambda p, cap, inputs, key, receipt, status:
        settlements.append((key, copy.deepcopy(receipt), status)))
    monkeypatch.setattr(s.transport, 'call', lambda *args, **kwargs: pytest.fail('never HTTP on recovery'))
    before = copy.deepcopy(t.call.data); result = s.recover(t.db, t.p, t.advice, t.inputs, t.key)
    assert result['model_value'] == t.opinion and len(settlements) == 1
    assert settlements[0][0] == t.key and settlements[0][2] == 'succeeded'
    assert settlements[0][1]['call_id'] == t.ledger.id and t.call.data == before
    assert t.call.status == 'failed'  # worker's fenced reconcile is a separate act


def test_invalid_paid_model_value_is_retained_and_settled_failed_without_repair_or_send(scene, monkeypatch):
    t = scene; _captured(t, sdk_status='failed', model_fault='wrong_ref'); settlements = []
    monkeypatch.setattr(s, '_settle_capture', lambda *args: settlements.append(args[-1]))
    monkeypatch.setattr(s.transport, 'call', lambda *args, **kwargs: pytest.fail('no repair HTTP'))
    before = copy.deepcopy(t.call.data)
    with pytest.raises(HTTPException): s.recover(t.db, t.p, t.advice, t.inputs, t.key)
    assert settlements == ['failed'] and t.call.data == before and t.call.status == 'failed'


def test_settlement_reuses_exact_original_ledger_and_two_hash_meanings_without_new_reservation(scene, monkeypatch):
    from generic_agents import db as dbmodule, cloud_budget
    t = scene; state = _captured(t, ledger_status='reserved'); _result, receipt = s._parsed(state)
    settlements = []; sessions = []
    @contextmanager
    def session(bind):
        sessions.append(bind); yield t.db
    sentinel = object()
    monkeypatch.setattr(dbmodule, 'engine', lambda: sentinel)
    monkeypatch.setattr(s, 'Session', session)
    def settle(db, call_id, captured_receipt, expected, status):
        assert db is t.db and call_id == t.ledger.id and t.ledger.status == 'reserved'
        settlements.append((copy.deepcopy(captured_receipt), copy.deepcopy(expected), status))
        t.ledger.status = status; t.ledger.data['receipt'] = copy.deepcopy(captured_receipt)
    monkeypatch.setattr(cloud_budget, 'settle_paid_response', settle)
    before = copy.deepcopy(t.call.data); frozen = state['request']
    s._settle_capture(t.p, t.advice, t.inputs, t.key, receipt, 'succeeded')
    assert len(settlements) == 1 and settlements[0][2] == 'succeeded'
    assert settlements[0][1] == {'workspace_id': t.p.workspace_id, 'capability_id': t.model.id,
        'actor_id': t.p.user_id, 'request_hash': frozen['prepared']['request_hash'],
        'dispatch_options_hash': frozen['prepared']['dispatch_options_hash'],
        'provider': t.model.data['execution']['provider'], 'model': t.model.data['execution']['model']}
    assert settlements[0][1]['request_hash'] != frozen['request_hash']
    assert t.call.data == before and t.commits == 1 and sessions == [sentinel]
    s._settle_capture(t.p, t.advice, t.inputs, t.key, receipt, 'succeeded')
    assert len(settlements) == 1 and t.commits == 1 and len(t.rows[s.CloudCall]) == 1


def test_fresh_execution_uses_only_installed_owner_token_and_actual_invocation_for_money_scope(scene, monkeypatch):
    t = scene; entries = []; captures = []
    @contextmanager
    def scope(**refs):
        entries.append(copy.deepcopy(refs)); yield
    monkeypatch.setitem(sys.modules, 'generic_agents.same_goal_advice_spending_v308', NS(scope=scope))
    monkeypatch.setattr(capture_protocol, 'execute', lambda *args, **kwargs: captures.append(kwargs['protocol']) or {'explicit_capture_IO': True})
    assert s.execute(t.db, t.p, t.advice, t.inputs, t.key) == {'explicit_capture_IO': True}
    assert entries == [{'workspace_id': t.p.workspace_id, 'actor_id': t.p.user_id, 'task_id': t.task.id,
        'plan_id': t.plan.id, 'job_id': t.send_job.id, 'invocation_id': t.call.id,
        'execution_hash': t.execution['snapshot']['record_hash'], 'lease_token': t.owner['lease_token']}]
    assert entries[0]['lease_token'] != t.call.data['lease_token'] and captures == [s]


@pytest.mark.parametrize('fault', ['no_fence', 'wrong_owner', 'missing_token'])
def test_money_scope_cannot_be_borrowed_from_job_json_or_sdk_creator_token(scene, monkeypatch, fault):
    t = scene
    if fault == 'no_fence': t.fenced = False
    elif fault == 'wrong_owner': t.owner['job_id'] = ref('not_current_job')['id']
    else: t.owner.pop('lease_token')
    monkeypatch.setattr(capture_protocol, 'execute', lambda *args, **kwargs: pytest.fail('no capture without actual installed owner'))
    with pytest.raises(HTTPException): s.execute(t.db, t.p, t.advice, t.inputs, t.key)


def test_retained_capture_does_not_create_a_fresh_money_scope_or_live_grant(scene, monkeypatch):
    t = scene; _prepared(t)
    monkeypatch.setattr(s, '_current', lambda *args: pytest.fail('retained capture uses original custody'))
    monkeypatch.setitem(sys.modules, 'generic_agents.same_goal_advice_spending_v308', NS(scope=lambda **kwargs: pytest.fail('no new money scope')))
    monkeypatch.setattr(capture_protocol, 'execute', lambda *args, **kwargs: kwargs['protocol'])
    assert s.execute(t.db, t.p, t.advice, t.inputs, t.key) is s


def test_normal_worker_only_marks_plan_ready_after_sdk_and_independent_result_audit(scene, monkeypatch):
    t = scene; expected = _succeeded(t); before_prep = copy.deepcopy(t.prep)
    monkeypatch.setattr(gateway, 'invoke', lambda *args, **kwargs: pytest.fail('existing succeeded SDK is not sent again'))
    assert s.process(t.db, t.p, t.send_job) == 'succeeded'
    marker = t.plan.data[s.RESULT_KEY]; ready = [row for row in t.rows[s.Audit] if row.action == s.RESULT_READY]
    assert len(ready) == 1 and t.plan.status == 'ready_for_review'
    assert marker == s._marker(ready[0], ready[0].data)
    assert marker['result_ref'] == {'id': t.call.id, 'record_hash': digest(expected)}
    assert t.send_job.status == 'running' and t.prep == before_prep
    assert s.process(t.db, t.p, t.send_job) == 'succeeded' and len(ready) == 1
    with pytest.raises(HTTPException): s.cold_result(t.db, t.p, t.task, t.plan)
    t.send_job.status = 'succeeded'; t.send_job.lease_until = None
    assert s.cold_result(t.db, t.p, t.task, t.plan) == expected
    assert all(expected[key] is False for key in s.c.FLAGS)


def test_worker_reconciles_only_same_captured_answer_after_fenced_lock_release(scene, monkeypatch):
    t = scene; state = _captured(t, sdk_status='failed')
    original_failure = t.event('worker.invocation_recovered', {'status': 'failed', 'job_id': t.send_job.id})
    creator = t.call.data['lease_token']; raw_before = copy.deepcopy(state['capture']); settlements = []
    monkeypatch.setattr(s, '_settle_capture', lambda *args: settlements.append((t.commits, args[-1])))
    monkeypatch.setattr(gateway, 'invoke', lambda *args, **kwargs: pytest.fail('same-key recovery never sends again'))
    assert s.process(t.db, t.p, t.send_job) == 'succeeded'
    terminals = [row for row in t.rows[s.Audit] if row.action == s.TERMINAL]
    assert settlements == [(1, 'succeeded')] and t.call.status == 'succeeded' and len(terminals) == 1
    assert original_failure.data == {'status': 'failed', 'job_id': t.send_job.id}
    assert t.call.data['lease_token'] == creator != t.owner['lease_token']
    assert t.call.data[s.STATE_KEY]['capture'] == raw_before
    assert terminals[0].data['origin_audits'] == [s._ref(original_failure)]
    assert s._sdk_result(t.db, t.p, t.task, t.plan, t.execution, t.call, t.advice)[0]['model_value'] == t.opinion
    assert (s.Task, True) in t.locks and (s.Plan, True) in t.locks and (s.Job, True) in t.locks


@pytest.mark.parametrize('fault', ['missing_ready', 'bad_marker', 'output', 'missing_sdk_audit', 'ledger_charge', 'reader'])
def test_public_cold_result_requires_current_reader_real_terminal_job_and_all_original_positive_proofs(scene, monkeypatch, fault):
    t = scene; _succeeded(t); s.process(t.db, t.p, t.send_job)
    t.send_job.status = 'succeeded'; t.send_job.lease_until = None
    if fault == 'missing_ready': t.rows[s.Audit] = [row for row in t.rows[s.Audit] if row.action != s.RESULT_READY]
    elif fault == 'bad_marker': t.plan.data[s.RESULT_KEY]['result_ref']['record_hash'] = '0' * 64
    elif fault == 'output': t.call.data['output']['model_value']['recommendation']['reason'] += ' changed'
    elif fault == 'missing_sdk_audit': t.rows[s.Audit] = [row for row in t.rows[s.Audit] if row.action != 'capability.invoke']
    elif fault == 'ledger_charge': t.ledger.charged_tokens = 0
    else: t.allow_reader = False
    before = copy.deepcopy(t.call.data)
    with pytest.raises(HTTPException): s.cold_result(t.db, t.p, t.task, t.plan)
    assert t.call.data == before and t.commits == 0


def test_cancel_and_failure_keep_original_snapshot_paid_capture_and_diagnostics(scene):
    t = scene; _captured(t, sdk_status='failed')
    t.call.data['error'] = 'original diagnostic remains'; before = copy.deepcopy(t.call.data)
    t.plan.status = 'cancelled'
    assert s.process(t.db, t.p, t.send_job) == 'cancelled'
    assert s.fail_job(t.db, t.p, t.send_job, 'not stored') == 'cancelled'
    t.plan.status = 'generating'
    recovery = Principal(t.p.user_id, t.p.workspace_id, 'viewer', 'worker-recovery')
    assert s.fail_job(t.db, recovery, t.send_job, 'not stored') == 'failed'
    assert t.plan.status == 'needs_review' and t.call.data == before and s.RESULT_KEY not in t.plan.data


def test_recovery_only_without_an_original_sdk_cannot_create_a_first_call(scene, monkeypatch):
    t = scene; t.rows[s.Invocation].clear(); t.send_job.data['recovery_only'] = True
    monkeypatch.setattr(gateway, 'invoke', lambda *args, **kwargs: pytest.fail('recovery-only is never a fresh send grant'))
    before = copy.deepcopy(t.plan.data)
    with pytest.raises(HTTPException): s.process(t.db, t.p, t.send_job)
    assert t.plan.data == before and not t.rows[s.Invocation] and not t.rows[s.CloudCall]


@pytest.mark.parametrize('fault', ['expired', 'other_token', 'not_installed', 'other_actor'])
def test_worker_result_and_failure_mutations_require_current_installed_exact_fence(scene, fault):
    t = scene; _succeeded(t)
    if fault == 'expired': t.clock = t.send_job.lease_until + timedelta(seconds=1)
    elif fault == 'other_token': t.owner['lease_token'] = ref('foreign_worker_token')['id']
    elif fault == 'not_installed': t.fenced = False
    else: t.p = Principal(ref('other_business_actor')['id'], t.p.workspace_id, 'operator', 'different_actor')
    before = copy.deepcopy(t.plan.data)
    with pytest.raises(HTTPException): s.process(t.db, t.p, t.send_job)
    assert t.plan.data == before and not [row for row in t.rows[s.Audit] if row.action == s.RESULT_READY]
