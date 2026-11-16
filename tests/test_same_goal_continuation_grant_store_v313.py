"""Actual grant custody/nonce SQL over explicit source/account/persistence IO.

The basis IO fixture is NOT a real published tool, PolicyVersion, Qe/ACK,
current-source authorization, PG transaction or execution proof. Source owner
and Root validate those independently. No Job/Invocation/CloudCall is faked.
"""
import copy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_continuation_grant_store_v313 as s
from generic_agents.contracts import digest
from generic_agents.models import Task, User
from tests.test_same_goal_continuation_store_v312 import predicate


def uid(number): return str(UUID(int=number))


@pytest.fixture
def scene(monkeypatch):
    at = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    p = NS(user_id=uid(1), workspace_id=uid(2), role='admin', session_id='explicit-current-IAM-IO')
    task = Task(id=uid(3), workspace_id=p.workspace_id, owner_id=uid(8), package_id=uid(4),
        title='原任务不改写', version=4, status='workflow_active', data={'goal_hash': 'a'*64, 'goal_version': 1,
            'goal_spec': {'deadline': (at+timedelta(hours=3)).isoformat()},
            'native_goal_context_hash': 'b'*64, 'instance_release_id': uid(5),
            'native_goal_query_id': uid(6), 'graph_runtime': {'graph_hash': 'c'*64, 'unchanged_original_IO': True}})
    source = {'schema': s.drafts.c.SOURCE, 'workspace_id': p.workspace_id, 'task_id': task.id,
        'advice_plan_id': uid(7), 'origin_kind': 'independent_advice', 'bridge_state': 'not_bound',
        'goal_hash': 'a'*64, 'goal_version': 1, 'goal_snapshot_hash': 'd'*64,
        'release_id': uid(5), 'original_graph_hash': 'c'*64, 'review_decision': 'accepted',
        'result_ref': {'id': uid(9), 'record_hash': 'e'*64}, 'review_ref': {'id': uid(10), 'record_hash': 'f'*64}}
    advice = s.Plan(id=uid(7), workspace_id=p.workspace_id, task_id=task.id, status='reviewed', version=9,
        data={'schema': 'same-goal-advice-plan-v302', 'retained_paid_original_IO': True})
    continuation = s.Plan(id=uid(11), workspace_id=p.workspace_id, task_id=task.id, status='draft', version=2,
        data={'schema': s.drafts.c.PLAN, 'source_hash': digest(source), 'revisions': [{'record_hash': 'e'*64}],
            'untouched_human_text_IO': '原人工内容\r\n😀'})
    manifest = s.c.manifest('observation_records')
    pin = {'id': uid(12), 'content_hash': digest(manifest), 'release_version': '1.0.0'}
    policy = {'policy_id': uid(13), 'version_id': uid(14), 'revision': 1, 'record_hash': 'f'*64}
    v = NS(p=p, task=task, advice=advice, continuation=continuation, source=source, pin=pin,
        policy=policy, manifest=manifest, clock=at, live=True, tamper=None, flushes=0, fail_flush=False,
        counter=100, events=[], source_calls=[], rows={s.Plan: [advice, continuation], s.Audit: []},
        members={uid(1): {'role': 'admin', 'active': True}, uid(20): {'role': 'admin', 'active': True},
            uid(21): {'role': 'operator', 'active': True}, uid(22): {'role': 'viewer', 'active': True}})

    class DB:
        @property
        @contextmanager
        def no_autoflush(self): yield
        def scalar(self, statement):
            if 'clock_timestamp' in str(statement): return v.clock
            model = statement.column_descriptions[0]['entity']
            if model is Task:
                v.events.append(('task', statement._for_update_arg is not None))
                return task if predicate(statement.whereclause, task) else None
            assert model is User
            for identity, member in v.members.items():
                row = NS(id=identity, user_id=identity, workspace_id=p.workspace_id, **member)
                if predicate(statement.whereclause, row): return identity
            return None
        def execute(self, statement, parameters):
            assert str(statement) == s.command_nonce.LOCK_SQL
            assert set(parameters) == {'workspace_command_nonce_key'}
            v.events.append(('nonce-lock-IO', parameters['workspace_command_nonce_key']))
        def scalars(self, statement):
            model = statement.column_descriptions[0]['entity'];assert model in v.rows
            assert statement._limit_clause.value == s.MAX_ROWS+1
            assert p.workspace_id in statement.compile().params.values()
            return [row for row in v.rows[model] if predicate(statement.whereclause, row)]
        def add(self, row):
            assert type(row) in v.rows, 'this family writes no Task/Job/Invocation/CloudCall';v.rows[type(row)].append(row)
        def flush(self):
            v.flushes += 1
            if v.fail_flush: raise HTTPException(503, 'explicit persistence IO failed')
        def commit(self): pytest.fail('API owns the transaction')
        @contextmanager
        def begin_nested(self):
            rows = {model: list(values) for model, values in v.rows.items()}
            saved = [(row, row.status, row.version, copy.deepcopy(row.data)) for row in v.rows[s.Plan]]
            try: yield
            except BaseException:
                v.rows = rows
                for row, status, version, data in saved: row.status, row.version, row.data = status, version, data
                raise
    v.db = DB()

    def actor_io(db, actor):
        assert db is v.db and actor is p
        member = v.members.get(p.user_id)
        if not member or not member['active'] or member['role'] != p.role: raise HTTPException(403, 'current IAM IO denied')
        v.events.append(('actor-lock-IO', p.user_id))
    monkeypatch.setattr(s, 'lock_current_actor', actor_io)

    def source_io(db, actor, task_id, advice_id, continuation_id, body, *, lock=True):
        assert db is v.db and actor is p and task_id == task.id and advice_id == advice.id and continuation_id == continuation.id
        v.source_calls.append((p.user_id, lock, copy.deepcopy(body)))
        if not v.live: raise HTTPException(409, 'current Source/Goal/policy IO expired or revoked')
        records = [{'entity_id': uid(30), 'version': 2, 'attributes': {body['field']: 64.5}}]
        provenance = [{'kind': 'observation', 'entity_id': uid(30), 'record_version': 2,
            'record_hash': 'a'*64, 'origin_hash': 'b'*64, 'actual_acked_source_IO': True}]
        basis = {'schema': s.c.BASIS, 'workspace_id': p.workspace_id, 'task_id': task.id, 'advice_plan_id': advice.id,
            'continuation_ref': {'id': continuation.id, 'version': body['expected_continuation_version'],
                'revision_hash': body['revision_hash'], 'source_hash': body['source_hash']}, 'source_hash': digest(v.source),
            'goal': {'hash': task.data['goal_hash'], 'version': task.data['goal_version'],
                'snapshot_hash': v.source['goal_snapshot_hash'], 'native_context_hash': task.data['native_goal_context_hash']},
            'release': {'id': task.data['instance_release_id'], 'configuration_hash': 'a'*64, 'package_id': task.package_id,
                'package_hash': 'b'*64, 'original_graph_hash': v.source['original_graph_hash']},
            'capability_pin': copy.deepcopy(v.pin), 'agent_role': 'data', 'input_schema_hash': digest(s.c.input_schema()),
            'output_schema_hash': digest(s.c.output_schema()), 'records_binding': 'observation_records',
            'scope': {'entity_ids': [uid(30)], 'fields': [body['field']]},
            'field': {'key': body['field'], 'label': '相对湿度', 'unit': '%'}, 'operation': body['operation'],
            'evaluation': {'evaluation_ref': {'id': uid(31), 'record_hash': 'c'*64}, 'query_ref': {'original_query_IO': uid(32)},
                'cut_at': at.isoformat(), 'generation': 3, 'mature_head_hash': 'd'*64,
                'goal_check_job_id': uid(33), 'goal_check_id': uid(34), 'report_hash': 'e'*64},
            'records': records, 'provenance': provenance,
            'inputs': {'field': body['field'], 'operation': body['operation'], 'records': copy.deepcopy(records)},
            'policy': {'ref': copy.deepcopy(v.policy), 'spec': {'original_V175_policy_IO': True},
                'subject_kind': s.c.SUBJECT_KIND, 'application': 'explicit_additive_continuation_grant'},
            'actor_id': p.user_id, 'issued_at': at.isoformat(), 'expires_at': (at+timedelta(hours=2)).isoformat(),
            'max_calls': 1, 'side_effect': False, 'external_allowed': False, 'cloud_allowed': False,
            'simulation_only': True, 'test_only': True}
        basis['input_hash'] = digest(basis['inputs'])
        if v.tamper: v.tamper(basis)
        basis['record_hash'] = s.c.record_hash(basis)
        return {'task': task, 'continuation': continuation, 'source': copy.deepcopy(v.source),
            'source_hash': digest(v.source), 'basis': basis}
    monkeypatch.setattr(s.sources, 'issue_basis', source_io)
    def body():
        v.counter += 1
        return {'request_id': uid(v.counter), 'expected_task_version': task.version,
            'expected_continuation_version': continuation.version, 'revision_hash': continuation.data['revisions'][-1]['record_hash'],
            'source_hash': digest(v.source), 'capability_pin': copy.deepcopy(pin), 'field': 'humidity_percent',
            'operation': 'mean', 'policy_ref': copy.deepcopy(policy), 'validity_seconds': 7200, 'reason': '明确登记本任务只读汇总范围，不批准执行'}
    v.body = body
    v.issue = lambda raw=None: s.issue(v.db,p,task.id,advice.id,continuation.id,body() if raw is None else raw)
    return v


def test_issue_seals_real_plan_and_protected_original_without_changing_originals_or_creating_execution(scene):
    v = scene;before = [(row, copy.deepcopy(row.data), row.status, row.version) for row in [v.task, v.advice, v.continuation]]
    raw = v.body();view = v.issue(raw);plan = v.rows[s.Plan][-1];grant = plan.data['grant']
    assert view['phase'] == 'active' and view['version'] == 1 and view['expired'] is False
    assert view['simulation_only'] is True and view['test_only'] is True and all(view[key] is False for key in s.c.FLAGS)
    assert plan.approved_by is None and plan.approved_hash is None and set(plan.data) == s._PLAN_FIELDS
    assert grant['basis']['inputs']['records'] == grant['basis']['records'] and grant['basis']['provenance']
    assert grant['source']['origin_kind'] == 'independent_advice' and grant['source']['bridge_state'] == 'not_bound'
    assert len(v.rows[s.Audit]) == v.flushes == 1
    assert all((row.data, row.status, row.version) == (data, status, version) for row, data, status, version in before)
    order = [entry[0] for entry in v.events];assert order.index('nonce-lock-IO') < order.index('task') < order.index('actor-lock-IO')


def test_mixed_record_without_selected_field_keeps_missingness_and_original_provenance_not_zero(scene):
    v = scene;original = {}
    def mixed(value):
        value['records'].append({'entity_id': uid(30), 'version': 3, 'attributes': {}})
        value['provenance'].append({'kind': 'observation', 'entity_id': uid(30), 'record_version': 3,
            'record_hash': 'c'*64, 'origin_hash': 'd'*64, 'actual_missing_field_source_IO': True})
        value['inputs']['records'] = copy.deepcopy(value['records']);value['input_hash'] = digest(value['inputs'])
        original.update({'records': copy.deepcopy(value['records']), 'provenance': copy.deepcopy(value['provenance']),
            'input_hash': value['input_hash']})
    v.tamper = mixed;view = v.issue();basis = v.rows[s.Plan][-1].data['grant']['basis']
    assert view['phase'] == 'active' and basis['records'] == original['records'] and basis['provenance'] == original['provenance']
    assert basis['input_hash'] == original['input_hash'] == digest(basis['inputs'])
    assert basis['inputs']['records'][0]['attributes'] == {'humidity_percent': 64.5}
    assert basis['inputs']['records'][1]['attributes'] == {} and 'humidity_percent' not in basis['records'][1]['attributes']


def test_expired_historical_get_list_receipt_are_read_only_and_never_reauthorize_sources_or_nonce(scene):
    v = scene;raw = v.body();first = v.issue(raw);plan = v.rows[s.Plan][-1];before = copy.deepcopy(plan.data)
    v.clock += timedelta(hours=4);v.live = False;v.task.status = 'workflow_cancelled';calls = list(v.source_calls)
    nonces = sum(entry[0] == 'nonce-lock-IO' for entry in v.events)
    view = s.get(v.db,v.p,v.task.id,v.advice.id,v.continuation.id,plan.id)
    assert view['expired'] is True and view['phase'] == 'active' and view['grant_ref'] == first['grant_ref']
    assert s.listing(v.db,v.p,v.task.id,v.advice.id,v.continuation.id)['records'] == [view]
    receipt = s.get_operation(v.db,v.p,v.task.id,v.advice.id,v.continuation.id,raw['request_id'])
    assert receipt['result'] == view and receipt['body_hash'] == digest(s.c.dump(s.c.IssueRequest,raw))
    assert v.source_calls == calls and plan.data == before and sum(entry[0] == 'nonce-lock-IO' for entry in v.events) == nonces


def test_original_issue_replays_current_view_before_live_expiry_and_after_revocation(scene):
    v = scene;raw = v.body();view = v.issue(raw);calls = list(v.source_calls);v.live = False;v.clock += timedelta(hours=4)
    replay = v.issue(raw);assert replay['expired'] is True and replay['grant_ref'] == view['grant_ref'] and v.source_calls == calls
    revoked = s.revoke(v.db,v.p,v.task.id,v.advice.id,v.continuation.id,view['grant_id'],
        {'request_id': uid(500), 'expected_grant_version': 1, 'reason': '停止后续使用，保留原记录'})
    assert v.issue(raw) == revoked and v.source_calls == calls and len(v.rows[s.Audit]) == 2


def test_other_current_admin_can_revoke_after_goal_cancel_without_rewriting_issued_scope(scene):
    v = scene;view = v.issue();plan = v.rows[s.Plan][-1];grant = copy.deepcopy(plan.data['grant']);calls = list(v.source_calls)
    v.clock += timedelta(hours=4);v.task.status = 'workflow_cancelled';v.live = False;v.p.user_id = uid(20)
    raw = {'request_id': uid(501), 'expected_grant_version': 1, 'reason': '目标结束后明确撤销原登记'}
    revoked = s.revoke(v.db,v.p,v.task.id,v.advice.id,v.continuation.id,plan.id,raw)
    assert revoked['phase'] == 'revoked' and revoked['expired'] is True and revoked['revocation']['actor_id'] == uid(20)
    assert s.revoke(v.db,v.p,v.task.id,v.advice.id,v.continuation.id,plan.id,raw) == revoked
    assert plan.data['grant'] == grant and revoked['grant_ref'] == view['grant_ref'] and v.source_calls == calls


@pytest.mark.parametrize('role,identity', [('operator', 21), ('viewer', 22)])
def test_issue_and_revoke_require_admin_before_positive_nonce_lookup(scene, role, identity):
    v = scene;view = v.issue();v.p.role = role;v.p.user_id = uid(identity);nonces = len(v.events)
    with pytest.raises(HTTPException) as issue_error: v.issue()
    with pytest.raises(HTTPException) as revoke_error: s.revoke(v.db,v.p,v.task.id,v.advice.id,v.continuation.id,view['grant_id'],
        {'request_id': uid(502), 'expected_grant_version': 1, 'reason': '不能借其他岗位撤销'})
    assert issue_error.value.status_code == revoke_error.value.status_code == 403 and len(v.events) == nonces
    assert s.get(v.db,v.p,v.task.id,v.advice.id,v.continuation.id,view['grant_id'])['permissions']['can_revoke'] is False


@pytest.mark.parametrize('fault', ['task_version', 'continuation_version', 'revision', 'source_hash', 'capability', 'policy_ref'])
def test_body_binding_mismatch_never_persists_a_registration(scene, fault):
    v = scene;raw = v.body()
    if fault == 'task_version': raw['expected_task_version'] += 1
    elif fault == 'continuation_version': raw['expected_continuation_version'] += 1
    elif fault == 'revision': raw['revision_hash'] = '0'*64
    elif fault == 'source_hash': raw['source_hash'] = '0'*64
    elif fault == 'capability': raw['capability_pin']['content_hash'] = '0'*64
    else: raw['policy_ref']['record_hash'] = '0'*64
    with pytest.raises(HTTPException): v.issue(raw)
    assert not v.rows[s.Audit] and len(v.rows[s.Plan]) == 2


@pytest.mark.parametrize('fault', ['role', 'native_context', 'external', 'cloud', 'side_effect', 'max_calls',
    'policy_kind', 'input_hash', 'provenance', 'wrong_field', 'expired', 'goal_deadline'])
def test_source_basis_consistency_is_checked_even_when_returned_self_hash_is_recomputed(scene, fault):
    v = scene
    def tamper(value):
        if fault == 'role': value['agent_role'] = 'business'
        elif fault == 'native_context': value['goal']['native_context_hash'] = '0'*64
        elif fault in {'external', 'cloud'}: value[fault+'_allowed'] = True
        elif fault == 'side_effect': value['side_effect'] = True
        elif fault == 'max_calls': value['max_calls'] = True
        elif fault == 'policy_kind': value['policy']['subject_kind'] = 'goal_continuation_v313'
        elif fault == 'input_hash': value['input_hash'] = '0'*64
        elif fault == 'provenance': value['provenance'] = []
        elif fault == 'wrong_field':
            value['records'][0]['attributes'] = {'temperature_c': 64.5}
            value['inputs']['records'] = copy.deepcopy(value['records']);value['input_hash'] = digest(value['inputs'])
        elif fault == 'expired': value['expires_at'] = value['issued_at']
        else: v.task.data['goal_spec']['deadline'] = (v.clock+timedelta(hours=1)).isoformat()
    v.tamper = tamper
    with pytest.raises(HTTPException): v.issue()
    assert not v.rows[s.Audit] and len(v.rows[s.Plan]) == 2 and not v.flushes


@pytest.mark.parametrize('fault', ['missing_audit', 'missing_plan', 'pointer', 'snapshot', 'foreign_task'])
def test_positive_inventory_blocks_different_nonce_after_missing_or_corrupt_original(scene, fault):
    v = scene;v.issue();plan = v.rows[s.Plan][-1]
    if fault == 'missing_audit': v.rows[s.Audit].clear()
    elif fault == 'missing_plan': v.rows[s.Plan].remove(plan)
    elif fault == 'pointer': plan.data = {**plan.data, 'operations': []}
    elif fault == 'snapshot':
        damaged = copy.deepcopy(plan.data);damaged['grant']['basis']['inputs']['records'][0]['attributes']['humidity_percent'] = 99;plan.data = damaged
    else: plan.task_id = uid(999)
    calls = list(v.source_calls)
    with pytest.raises(HTTPException): v.issue()
    assert v.source_calls == calls


def test_protected_issue_receipt_survives_missing_plan_as_needs_check(scene):
    v = scene;raw = v.body();view = v.issue(raw);v.rows[s.Plan].pop()
    receipt = s.get_operation(v.db,v.p,v.task.id,v.advice.id,v.continuation.id,raw['request_id'])
    assert receipt['found'] is True and receipt['result_status'] == 'needs_check' and receipt['result'] is None
    assert receipt['grant_id'] == view['grant_id'] and receipt['body_hash'] == digest(s.c.dump(s.c.IssueRequest,raw))
    with pytest.raises(HTTPException): v.issue(raw)


@pytest.mark.parametrize('status', [401, 403, 500, 503])
def test_nonce_result_reader_does_not_swallow_real_auth_or_internal_errors(scene, monkeypatch, status):
    v = scene;raw = v.body();v.issue(raw)
    def fail(*args, **kwargs): raise HTTPException(status, 'explicit result IO failure')
    monkeypatch.setattr(s, '_inventory', fail)
    with pytest.raises(HTTPException) as caught: s.get_operation(v.db,v.p,v.task.id,v.advice.id,v.continuation.id,raw['request_id'])
    assert caught.value.status_code == status


def test_nonce_cannot_change_full_body_actor_or_continuation_scope(scene):
    v = scene;raw = v.body();v.issue(raw)
    with pytest.raises(HTTPException): v.issue({**raw, 'reason': '同号修改人工说明'})
    with pytest.raises(HTTPException): s.issue(v.db,v.p,v.task.id,v.advice.id,uid(990),raw)
    v.p.user_id = uid(20)
    with pytest.raises(HTTPException): v.issue(raw)
    assert len(v.rows[s.Audit]) == 1 and len(v.rows[s.Plan]) == 3


def test_foreign_workspace_orphan_does_not_poison_this_scoped_ledger(scene):
    v = scene;v.rows[s.Audit].append(s.Audit(id=uid(980),workspace_id=uid(981),actor_id=v.p.user_id,
        action=s.c.ACTION_ISSUE,object_id=uid(982),data={'task_id':v.task.id,'malformed_foreign_IO':True}))
    assert v.issue()['phase'] == 'active'


def test_failed_local_persistence_rolls_back_without_changing_originals_or_issuing_success(scene):
    v = scene;before = [(row,copy.deepcopy(row.data),row.status,row.version) for row in [v.task,v.advice,v.continuation]]
    v.fail_flush = True
    with pytest.raises(HTTPException) as caught: v.issue()
    assert caught.value.status_code == 503 and not v.rows[s.Audit] and len(v.rows[s.Plan]) == 2
    assert all((row.data,row.status,row.version) == (data,status,version) for row,data,status,version in before)


def test_revoke_cas_and_terminal_rejection_preserve_the_single_original_stop(scene):
    v = scene;view = v.issue();plan = v.rows[s.Plan][-1];before = copy.deepcopy(plan.data);calls = list(v.source_calls)
    wrong = {'request_id': uid(503), 'expected_grant_version': 2, 'reason': '不能用错误版本撤销'}
    with pytest.raises(HTTPException): s.revoke(v.db,v.p,v.task.id,v.advice.id,v.continuation.id,plan.id,wrong)
    assert plan.data == before and plan.version == view['version'] and len(v.rows[s.Audit]) == 1
    raw = {'request_id': uid(504), 'expected_grant_version': 1, 'reason': '明确停止原登记'}
    s.revoke(v.db,v.p,v.task.id,v.advice.id,v.continuation.id,plan.id,raw)
    with pytest.raises(HTTPException): s.revoke(v.db,v.p,v.task.id,v.advice.id,v.continuation.id,plan.id,
        {'request_id': uid(505), 'expected_grant_version': 2, 'reason': '不能重复产生另一个停止'})
    assert len(v.rows[s.Audit]) == 2 and v.source_calls == calls


def test_historical_receipt_still_requires_the_actual_current_account_and_membership(scene):
    v = scene;raw = v.body();v.issue(raw);before = copy.deepcopy(v.rows[s.Audit][0].data)
    v.members[v.p.user_id]['active'] = False;calls = list(v.source_calls);events = len(v.events)
    with pytest.raises(HTTPException): s.get_operation(v.db,v.p,v.task.id,v.advice.id,v.continuation.id,raw['request_id'])
    assert v.rows[s.Audit][0].data == before and v.source_calls == calls and len(v.events) == events
