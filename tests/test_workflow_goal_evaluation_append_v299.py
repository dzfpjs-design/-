"""Actual producer orchestration/contracts over explicit SQL/source/IAM IO spies.

These tests do not prove PG, publication, leases, SDK or worker persistence.
The SQL spy interprets actual predicates and refuses unsupported statements;
Root separately runs the real same-Task vertical producer and cold readback.
"""
import copy
import hashlib
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BindParameter, BinaryExpression, BooleanClauseList, Grouping

from generic_agents import workflow_goal_evaluation_append_v299 as s
from generic_agents import goal_evaluation_maturity_v299 as maturity
from generic_agents import entity_release_compat, gateway
from generic_agents.auto_evaluation_trigger_v356 import AutoEvaluationTriggerV356
from generic_agents.contracts import digest
from tests.test_goal_evaluation_append_contracts_v299 import record as contract_record
from tests.test_workflow_goal_evaluation_contracts_v288 import uid, CUT, CHECKED
from tests.test_workflow_goal_evaluation_store_v288 import display_task

ACTUAL_VALIDATE = s.validate_call_approval
ACTUAL_AUTHORITY = s.commands.runner_authority


def predicate(expression, row):
    if isinstance(expression, BindParameter): return expression.value
    if isinstance(expression, Grouping): return predicate(expression.element, row)
    if isinstance(expression, BooleanClauseList):
        values = [predicate(item, row) for item in expression.clauses]
        if expression.operator is operators.and_: return all(values)
        if expression.operator is operators.or_: return any(values)
        raise AssertionError('Unsupported boolean SQL')
    if isinstance(expression, BinaryExpression):
        left, right = predicate(expression.left, row), predicate(expression.right, row)
        op = expression.operator
        if op in (operators.json_getitem_op, operators.json_path_getitem_op):
            return left.get(right) if isinstance(left, dict) else None
        if op is operators.eq: return left is not None and right is not None and left == right
        if op is operators.in_op: return left is not None and left in right
        if op is operators.ge: return left is not None and left >= right
        if op is operators.le: return left is not None and left <= right
        raise AssertionError('Unsupported binary SQL')
    if hasattr(expression, 'table') and getattr(expression, 'key', None): return getattr(row, expression.key)
    raise AssertionError('Unsupported SQL expression ' + type(expression).__name__)


@pytest.fixture
def scene(monkeypatch):
    template, kwargs = contract_record(); task = display_task(template)
    p = NS(workspace_id=task.workspace_id, user_id=template['producer']['actor_id'], role='admin')
    inputs = {'local_plan': {'explicit_original_plan_IO': True}}
    cap = NS(id=template['producer']['capability_id'], data={'explicit_released_manifest_IO': True})
    cap.content_hash = digest(cap.data)
    command = NS(id=template['command_ref']['id'])
    command_data = {'record_hash': template['command_ref']['record_hash'], 'actor_id': p.user_id,
        'capability_hash': cap.content_hash, 'input_hash': digest(inputs),
        'base_evaluation_ref': template['trigger']['base_evaluation_ref'],
        'base_mature_head_hash': template['trigger']['base_mature_head_hash'],
        'body_hash': template['trigger']['request_hash'], 'original_query_ref': template['original_query_ref']}
    approval = {'schema': s.commands.c.APPROVAL, 'command_ref': template['command_ref']}
    job = s.Job(id=template['request_id'], workspace_id=p.workspace_id, kind=s.commands.c.KIND,
        status='running', lease_token=template['producer']['lease_token'],
        data={'task_id': task.id}, idempotency_key=template['request_key'])
    call = s.Invocation(id=template['evaluation_id'], workspace_id=p.workspace_id, capability_id=cap.id,
        status='running', idempotency_key=template['request_key'], data={'job_id': job.id,
            'role': 'data', 'side_effect': True, 'capability_snapshot': cap.data,
            'capability_hash': cap.content_hash, 'inputs': inputs, 'input_hash': digest(inputs),
            'approval': approval, 'lease_token': job.lease_token})
    current = {'task': task, 'job': job, 'call': call, 'cap': cap, 'command': command,
        'record': command_data, 'selected': {'binding': {'simulation_only': True}},
        'inputs': inputs, 'key': template['request_key'], 'approval': approval}
    creator = {**template['producer'], 'capability_hash': cap.content_hash, 'input_hash': digest(inputs)}
    frozen = {'query_ref': copy.deepcopy(template['evaluation_query_ref']),
        'request': copy.deepcopy(template['evaluation_request']), 'result': copy.deepcopy(template['evaluation_result']),
        'provenance': {'scope': template['scope'], 'goal_snapshot': template['goal_snapshot'],
            'preparation_policy': template['preparation_policy'], 'original_query_ref': template['original_query_ref'],
            'cut_at': CUT, 'cut_checked_at': CHECKED, 'derived_window': template['derived_window'],
            'creator': {**creator, 'request_key': current['key'], 'command_ref': template['command_ref']}}}
    base = NS(id=command_data['base_evaluation_ref']['id'], workspace_id=p.workspace_id,
        task_id=task.id, goal_version=1, generation=1, record_hash=command_data['base_evaluation_ref']['record_hash'],
        invocation_id=uid(190), job_id=uid(191), request_key='old-original', evaluation_query_id=uid(192))
    state = NS(p=p, task=task, job=job, call=call, cap=cap, current=current, template=template,
        frozen=frozen, kwargs=kwargs, flushes=0, additions=[], reads=[], queues=[], queries=[], authority=[],
        bodies={}, ack_drift=False, candidate_failure=False, clock=s.instant(CUT),
        rows={s.Evaluation:[base], s.Invocation:[call], s.Job:[job], s.Audit:[], s.DataRun:[],
            s.Evidence:[], s.DataGap:[], AutoEvaluationTriggerV356:[]})
    class Results(list):
        def all(self): return list(self)
    class DB:
        def scalars(self, statement):
            model = statement.column_descriptions[0]['entity']; assert model in state.rows
            assert statement._limit_clause is None or statement._limit_clause.value in (1,2,s.MAX_ROWS + 1)
            state.reads.append(model)
            rows = [row for row in state.rows[model] if predicate(statement.whereclause, row)]
            return Results(rows[:statement._limit_clause.value] if statement._limit_clause is not None else rows)
        def scalar(self, statement):
            if 'clock_timestamp' in str(statement): return state.clock
            assert statement.column_descriptions[0]['entity'] is s.Audit
            rows=self.scalars(statement)
            return rows[0].id if rows else None
        def add(self, row):
            assert type(row) in state.rows
            state.rows[type(row)].append(row); state.additions.append(type(row))
        def flush(self): state.flushes += 1
        @contextmanager
        def begin_nested(self):
            before = {model:list(rows) for model,rows in state.rows.items()}
            data = copy.deepcopy(call.data); status = call.status
            try: yield
            except BaseException:
                state.rows = before; call.data = data; call.status = status
                raise
        def commit(self): pytest.fail('caller owns commit')
    state.db = DB()
    def fresh(db, principal, model, identity):
        assert db is state.db and principal is p
        rows = [row for row in state.rows[model] if row.id == identity]
        s.need(len(rows) == 1); return rows[0]
    def audit(db, principal, action, object_id, data):
        assert db is state.db and principal is p
        row = s.Audit(id=uid(400 + len(state.rows[s.Audit])), workspace_id=p.workspace_id,
            actor_id=p.user_id, action=action, object_id=object_id, data=copy.deepcopy(data),
            created_at=s.instant(CHECKED) + timedelta(seconds=1))
        db.add(row); return row
    def save(db, principal, body, media_type, *, cloud_model_id):
        assert media_type == 'text/plain' and cloud_model_id is None
        row = s.Evidence(id=template['evidence_ref']['evidence_id'], workspace_id=p.workspace_id,
            sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body), media_type=media_type,
            created_at=s.instant(CHECKED), data={'cloud_model_ids':[]})
        state.bodies[row.id] = body; db.add(row)
        audit(db, principal, 'evidence.upload', row.id,
            {'media_type':media_type, 'sha256':row.sha256, 'cloud_model_id':None, 'consent':False})
        return row
    def acks(*args, **kwargs):
        assert not kwargs  # No old resense-trigger mandatory ACKFalse in append.
        value = copy.deepcopy(template['ack_refs'])
        if state.ack_drift: value[0]['observation_hash'] = 'f' * 64
        return value
    def candidate(db, principal, actual_task, row, producer_job):
        assert db is state.db and principal is p and actual_task is task and producer_job is job
        assert row in state.rows[s.Evaluation] and call.data[s.KEY]['evaluation_record_hash'] == row.record_hash
        assert any(event.action == s.CREATED for event in state.rows[s.Audit]) and state.flushes > 0
        state.queues.append(row.id)
        if state.candidate_failure: raise HTTPException(409, 'explicit queue IO failure')
        return NS(id=uid(350))
    def authority(db, principal, actual_job, **arguments):
        assert db is state.db and principal is p and actual_job.id == job.id
        state.authority.append(arguments['require_lease']); return current
    def validate(*args): return current
    def checked_query(db, principal, actual_task, identity, *, evaluation_id):
        assert identity == frozen['query_ref']['query_id'] and evaluation_id == call.id
        return copy.deepcopy(frozen)
    def create(db, principal, actual, sdk, *, cut_at):
        assert sdk is call and actual is current and cut_at == CUT
        state.queries.append(cut_at)
        row = s.DataRun(id=frozen['query_ref']['query_id'], workspace_id=p.workspace_id,
            data={s.queries.KEY:{'evaluation_id':call.id}}); db.add(row)
        audit(db, principal, s.queries.SAVED, row.id, {'evaluation_id':call.id})
        return row
    monkeypatch.setattr(s.q, '_fresh', fresh)
    monkeypatch.setattr(s, 'audit', audit)
    monkeypatch.setattr(s.old, 'save_evidence', save)
    monkeypatch.setattr(s.old, 'evidence_bytes', lambda db,p,identity: (fresh(db,p,s.Evidence,identity), state.bodies[identity]))
    monkeypatch.setattr(s.old, '_acks', acks)
    monkeypatch.setattr(entity_release_compat, 'verify_task_binding_snapshot', lambda actual: {'explicit_IO':actual.id})
    monkeypatch.setattr(maturity, 'queue_candidate', candidate)
    monkeypatch.setattr(s.commands, 'runner_authority', authority)
    monkeypatch.setattr(s, 'runner_authority', authority)
    monkeypatch.setattr(s.commands, '_inventory', lambda *args: [(command,command_data,job)])
    monkeypatch.setattr(s, 'validate_call_approval', validate)
    monkeypatch.setattr(s.queries, 'checked_frozen', checked_query)
    monkeypatch.setattr(s.queries, 'create_frozen', create)
    state.audit = audit; state.create_query = lambda: create(state.db,p,current,call,cut_at=CUT)
    state.execute = lambda: s.execute(state.db,p,cap,inputs,current['key'],approval)
    state.reconcile = lambda: s.reconcile(state.db,p,cap,inputs,current['key'],approval,call)
    return state


def succeed(state):
    output = state.execute(); state.call.status = state.job.status = 'succeeded'
    state.call.data = {**state.call.data, 'output':output}
    state.audit(state.db,state.p,'capability.invoke',state.call.id,{'status':'succeeded'})
    return output


def test_new_producer_materializes_real_v299_record_and_flushes_before_candidate_queue(scene):
    t = scene; output = succeed(t)
    row = t.rows[s.Evaluation][-1]
    assert row.generation == 2 and row.data['producer_family'] == 'append_command_v297'
    assert row.data['command_ref'] == t.template['command_ref'] and t.queries == [CUT]
    assert not {'activation_id','node_id','cancel_epoch'} & set(row.data['producer'])
    assert t.queues == [row.id] and output['evaluation_ref']['id'] == t.call.id
    assert s.saved_result(t.db,t.p,t.task,t.job,t.call,t.cap,t.current['inputs']) == output
    assert t.authority == [False]


def test_existing_own_query_keeps_its_original_cut_and_never_requeries(scene):
    t = scene; t.create_query(); t.clock += timedelta(hours=2)
    assert t.execute()['evaluation_ref']['id'] == t.call.id
    assert t.queries == [CUT] and t.rows[s.Evaluation][-1].data['cut_at'] == CUT


def test_candidate_failure_rolls_back_query_record_journal_and_created_as_one_stage(scene):
    t = scene; t.candidate_failure = True
    with pytest.raises(HTTPException, match='explicit queue'): t.execute()
    assert len(t.rows[s.Evaluation]) == 1 and not t.rows[s.DataRun] and not t.rows[s.Evidence]
    assert not t.rows[s.Audit] and s.KEY not in t.call.data


@pytest.mark.parametrize('fault', ['missing_saved','orphan_evaluation','orphan_candidate_audit','evidence_effect'])
def test_unknown_needs_positive_query_and_empty_later_effect_inventory(scene, fault):
    t = scene; t.create_query(); t.call.status = 'unknown'
    t.audit(t.db,t.p,'capability.invoke',t.call.id,{'status':'unknown'})
    if fault == 'missing_saved': t.rows[s.Audit] = [event for event in t.rows[s.Audit] if event.action != s.queries.SAVED]
    if fault == 'orphan_evaluation':
        t.rows[s.Evaluation].append(NS(id=t.call.id, workspace_id=t.p.workspace_id,
            invocation_id=t.call.id, job_id=t.job.id, request_key=t.current['key']))
    if fault == 'orphan_candidate_audit':
        t.audit(t.db,t.p,maturity.QUEUED,uid(800),{'evaluation_ref':{'id':t.call.id}})
    if fault == 'evidence_effect':
        t.rows[s.Evidence].append(NS(id=uid(801),workspace_id=t.p.workspace_id,created_at=s.instant(CHECKED)))
    with pytest.raises(HTTPException): t.reconcile()
    assert t.queries == [CUT] and not t.queues and s.KEY not in t.call.data


def test_unknown_without_saved_query_does_not_treat_absence_as_retry_permission(scene):
    t = scene; t.call.status = 'unknown'
    with pytest.raises(HTTPException, match='不重跑查询'): t.reconcile()
    assert not t.queries and not t.queues


def test_original_unknown_recovers_same_sdk_with_new_lease_and_independent_terminal_only(scene):
    t = scene; t.create_query(); t.call.status = 'unknown'
    t.audit(t.db,t.p,'capability.invoke',t.call.id,{'status':'unknown'})
    original_token = t.call.data['lease_token']; t.job.lease_token = uid(999)
    assert s.run(t.db,t.p,t.job) == 'succeeded'
    assert t.call.status == 'succeeded' and t.job.status == 'running'
    assert t.call.data['lease_token'] == original_token != t.job.lease_token
    assert t.queries == [CUT] and t.rows[s.Evaluation][-1].generation == 2
    assert [event.data for event in t.rows[s.Audit] if event.action == 'capability.invoke'] == [{'status':'unknown'}]
    assert len([event for event in t.rows[s.Audit] if event.action == s.TERMINAL]) == 1
    t.job.status = 'succeeded'
    assert s.saved_result(t.db,t.p,t.task,t.job,t.call,t.cap,t.current['inputs'])['evaluation_ref']['id'] == t.call.id


@pytest.mark.parametrize('fault', ['later_consent','body_drift','ack_drift','missing_upload','missing_terminal'])
def test_cold_original_proof_ignores_current_consent_but_rejects_content_source_and_audit_drift(scene, fault):
    t = scene; t.create_query(); t.call.status = 'unknown'
    t.audit(t.db,t.p,'capability.invoke',t.call.id,{'status':'unknown'})
    s.run(t.db,t.p,t.job); t.job.status = 'succeeded'
    evidence = t.rows[s.Evidence][0]
    if fault == 'later_consent': evidence.data['cloud_model_ids'] = [uid(901)]
    if fault == 'body_drift': t.bodies[evidence.id] += b'changed'
    if fault == 'ack_drift': t.ack_drift = True
    if fault == 'missing_upload': t.rows[s.Audit] = [event for event in t.rows[s.Audit] if event.action != 'evidence.upload']
    if fault == 'missing_terminal': t.rows[s.Audit] = [event for event in t.rows[s.Audit] if event.action != s.TERMINAL]
    if fault == 'later_consent': assert s.checked_record(t.db,t.p,t.task,t.rows[s.Evaluation][-1])['row'].generation == 2
    else:
        with pytest.raises(HTTPException): s.checked_record(t.db,t.p,t.task,t.rows[s.Evaluation][-1])


def test_normal_runner_uses_gateway_once_and_existing_success_never_reenters_it(scene, monkeypatch):
    t = scene; t.rows[s.Invocation].clear(); calls = []
    def invoke(db,p,cap_id,role,inputs,key,*,approval):
        calls.append((cap_id,role,key,approval)); t.rows[s.Invocation].append(t.call)
        output = t.execute(); t.call.status = 'succeeded'; t.call.data = {**t.call.data,'output':output}
        t.audit(db,p,'capability.invoke',t.call.id,{'status':'succeeded'}); return t.call
    monkeypatch.setattr(gateway,'invoke',invoke)
    assert s.run(t.db,t.p,t.job) == 'succeeded'
    assert s.run(t.db,t.p,t.job) == 'succeeded' and len(calls) == 1
    assert calls[0][1:] == ('data',t.current['key'],t.current['approval'])


def test_unfinished_sdk_and_nonadvancing_generation_are_not_cold_completed_producers(scene):
    t = scene; t.execute(); row = t.rows[s.Evaluation][-1]
    with pytest.raises(HTTPException): s.checked_record(t.db,t.p,t.task,row)
    t.authority.clear()
    row.generation = 1
    with pytest.raises(HTTPException): s.checked_record(t.db,t.p,t.task,row,producer_complete=False)
    assert not t.authority  # Generation checked before recursive base-history authority.


def test_typed_approval_resolves_actual_command_job_before_live_authority(scene,monkeypatch):
    t=scene;calls=[]
    def fresh(db,p,model,identity):
        calls.append((model,identity))
        if model is s.commands.Command:
            assert identity==t.current['command'].id
            return NS(job_id=t.job.id)
        assert model is s.Job and identity==t.job.id;return t.job
    monkeypatch.setattr(s.q,'_fresh',fresh)
    assert ACTUAL_VALIDATE(t.db,t.p,t.cap,'data',t.current['inputs'],t.current['key'],t.current['approval']) is t.current
    assert calls==[(s.commands.Command,t.current['command'].id),(s.Job,t.job.id)] and t.authority==[True]
    with pytest.raises(HTTPException):ACTUAL_VALIDATE(t.db,t.p,t.cap,'business',{},'invalid',t.current['approval'])


@pytest.mark.parametrize('live',[False,True])
def test_actual_authority_cold_avoids_current_head_and_live_counts_its_own_reserved_last_slot(scene,monkeypatch,live):
    from contextlib import nullcontext
    from generic_agents import leases,goal_queries,native_step_execution,native_goal_queries
    t=scene;c=s.commands;record=t.current['record'];task=t.task
    task.package_id=t.template['scope']['package_id']
    task.data.update(instance_id=t.template['scope']['instance_id'],business_id='append_authority_IO',
        goal_hash=t.template['goal_snapshot']['hash'],native_goal_context_hash='a'*64,
        workflow_runtime={'receipts':[]})
    # The shared counter below already includes this immutable reservation.
    task.data['goal_spec'].update(max_tool_calls=1,max_steps=1)
    task.data.update(goal_confirmed_by=t.p.user_id,goal_window=t.template['goal_snapshot']['time'],
        goal_hash=digest(task.data['goal_spec']))
    binding={'graph':{'fixed_source_IO':True},'binding_hash':'b'*64,'capability_pins':{
        'evaluation_packet':{'capability_id':t.cap.id,'capability_hash':t.cap.content_hash,'release_version':'1.0.0'}}}
    selected={'binding':binding,'release':NS(id=t.template['scope']['release_id'])}
    scope={'workspace_id':t.p.workspace_id,'task_id':task.id,'instance_id':task.data['instance_id'],
        'release_id':selected['release'].id,'package_id':task.package_id,
        'business_id':task.data['business_id'],'graph_hash':digest(binding['graph'])}
    record.update(scope=scope,scope_hash=digest(scope),source_binding_hash=binding['binding_hash'],
        native_goal_context_hash=task.data['native_goal_context_hash'],goal_hash=task.data['goal_hash'],
        goal_version=task.data['goal_version'],goal_snapshot_hash=digest(c.snapshot(task)),
        capability_id=t.cap.id,release_version='1.0.0',inputs=t.current['inputs'],invocation_key=t.current['key'])
    t.cap.validated_hash=t.cap.content_hash;t.cap.release_version='1.0.0'
    t.job.lease_token=t.call.data['lease_token'];t.current['command'].workspace_id=t.p.workspace_id
    source_calls=[]
    monkeypatch.setattr(c,'_reader',lambda *args,**kwargs:source_calls.append(('reader',kwargs['write'])))
    monkeypatch.setattr(c,'_task',lambda *args,**kwargs:task)
    monkeypatch.setattr(c.provider,'binding',lambda *args:selected)
    monkeypatch.setattr(c.provider,'_q0',lambda *args:NS())
    monkeypatch.setattr(c.queries,'query_ref',lambda *args:record['original_query_ref'])
    monkeypatch.setattr(c,'approval_for',lambda actual:t.current['approval'])
    monkeypatch.setattr(c.packet,'validate_manifest',lambda data:None)
    monkeypatch.setattr(c.provider,'_fresh',lambda db,p,model,identity:t.cap if model is c.Capability else t.call)
    monkeypatch.setattr(c,'_base_history',lambda *args:source_calls.append(('historical_base',True)))
    def context(*args):
        assert live,'cold proof must not require current head, Goal guard or freshness'
        source_calls.append(('current',True));return {'mature':{'head':{'head_hash':record['base_mature_head_hash']}},
            'cap':t.cap,'inputs':t.current['inputs']}
    monkeypatch.setattr(c,'_context',context)
    monkeypatch.setattr(goal_queries,'call_count',lambda *args:1)
    monkeypatch.setattr(native_step_execution,'step_count',lambda *args:0)
    monkeypatch.setattr(native_goal_queries,'step_count',lambda *args:1)
    monkeypatch.setattr(leases,'fenced',lambda db:True)
    monkeypatch.setattr(leases,'ownership',lambda db:{'job_id':t.job.id,'lease_token':t.job.lease_token})
    t.db.no_autoflush=nullcontext()
    def scalar(statement):
        if statement.column_descriptions[0]['entity'] is s.Audit:
            rows=t.db.scalars(statement);return rows[0].id if rows else None
        assert statement.column_descriptions[0]['entity'] is s.Job
        assert statement._where_criteria and 'lease_until' in str(statement)
        return t.job.id
    monkeypatch.setattr(t.db,'scalar',scalar)
    result=ACTUAL_AUTHORITY(t.db,t.p,t.job,call=t.call,require_lease=live)
    assert result['job'] is t.job and source_calls[:2]==[('reader',live),('historical_base',True)]
    assert ('current',True) in source_calls if live else ('current',True) not in source_calls
