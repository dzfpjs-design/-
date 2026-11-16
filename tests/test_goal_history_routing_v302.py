"""Finite routing; SQL/IAM/graph authentication/publication are explicit IO spies.

Actual Eval contracts, Command _saved/_operation_progress, hash Audits, snapshot,
Memory and new routing control flow run. No PG, broker, SDK, source authority,
worker or business completion is claimed by these synthetic in-memory fixtures.
"""
import copy
from contextlib import nullcontext
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import goal_history_routing_v302 as r
from generic_agents import goal_evaluation_append_store_v297 as cmd
from generic_agents import goal_evaluation_maturity_v299 as maturity
from generic_agents import workflow_goal_evaluation_append_v299 as append
from generic_agents import workflow_goal_evaluation_contracts_v299 as newer
from generic_agents import workflow_goal_evaluation_recovery_v294 as recovery
from generic_agents import workflow_goal_evaluation_store_v288 as old
from generic_agents import workflow_goal_evaluation_contracts_v288 as contract
from generic_agents.contracts import digest
from generic_agents.models import Audit, Capability, DataRun, Invocation, Job, Memory, Task
from generic_agents.security import Principal
from generic_agents.temporal import instant
from tests.test_goal_evaluation_append_command_v297 import predicate
from tests.test_workflow_goal_evaluation_contracts_v288 import scene as contract_scene, INITIAL, CUT


def uid(): return str(uuid4())


def build(monkeypatch, *, family='legacy'):
    _, value, _, original = contract_scene()
    value = copy.deepcopy(value); scope = value['scope']; goal = value['goal_snapshot']
    p = Principal(value['producer']['actor_id'], scope['workspace_id'], 'admin', 'worker')
    graph = {'nodes': [{'id': 'evaluation_packet', 'fixture': 'original graph authority IO'}]}
    scope['graph_hash'] = digest(graph)
    release = NS(id=scope['release_id'], instance_id=scope['instance_id'], package_id=scope['package_id'])
    selected = {'release': release, 'binding': {'schema': 'authored-goal-data-workflow-binding-v288',
        'workspace_id': p.workspace_id, 'binding_hash': scope['source_binding_hash'], 'graph': graph}}
    task = Task(id=scope['task_id'], workspace_id=p.workspace_id, package_id=scope['package_id'],
        owner_id=p.user_id, title='有限沿革纯验收', status='workflow_active', version=1,
        data={'instance_id': scope['instance_id'], 'instance_release_id': scope['release_id'],
            'business_id': scope['business_id'], 'goal_spec': goal['spec'], 'goal_hash': goal['hash'],
            'goal_version': goal['version'], 'goal_window': goal['time'], 'goal_confirmed_by': p.user_id,
            'native_goal_context_hash': scope['native_goal_context_hash'],
            'native_goal_query_id': value['original_query_ref']['query_id']})
    original.update(native_goal_context_hash=scope['native_goal_context_hash'],
        goal_delegation={'task_id': task.id, 'goal_snapshot': goal})
    original['native_goal_request_hash'] = digest(original['request'])
    original['native_goal_result_hash'] = digest(original['result'])
    q0 = DataRun(id=task.data['native_goal_query_id'], workspace_id=p.workspace_id, owner_id=p.user_id,
        question='original Q0', status='insufficient', version=2, data=original)
    value['original_query_ref'] = r.query.query_ref(q0)
    inputs = {'local_plan': {'fixture': 'source authenticated at explicit IO boundary'}}
    cap = Capability(id=value['producer']['capability_id'], workspace_id=p.workspace_id,
        namespace='routing_fixture', name='evaluation_packet', kind='tool', status='published',
        release_version='1.0.0', version=1, data={'version': '1.0.0', 'execution': {
            'adapter': 'workflow_service', 'service_actor': 'reader_service',
            'operation': old.OP, 'side_effect': True}})
    cap.content_hash = cap.validated_hash = digest(cap.data)
    producer = value['producer']; producer.update(scope_hash=digest(scope),
        capability_hash=cap.content_hash, input_hash=digest(inputs))
    job = Job(id=producer['job_id'], workspace_id=p.workspace_id,
        kind='execute_full_semantic_graph_node', status='succeeded', attempts=1, version=1,
        lease_until=None, lease_token=uid(), idempotency_key=value['request_key'],
        data={'task_id': task.id, 'actor_id': p.user_id, 'inputs': inputs, 'input_hash': digest(inputs),
            'activation_id': producer['activation_id'], 'invocation_key': value['request_key']})
    call = Invocation(id=value['evaluation_id'], workspace_id=p.workspace_id, capability_id=cap.id,
        idempotency_key=job.idempotency_key, status='succeeded', version=1,
        data={'job_id': job.id, 'capability_hash': cap.content_hash, 'capability_snapshot': cap.data,
            'input_hash': digest(inputs), 'inputs': inputs, 'role': 'data', 'side_effect': True,
            'lease_token': producer['lease_token'], 'approval': {'fixture': 'original graph approval IO'}})
    s = NS(p=p, task=task, selected=selected, cap=cap, job=job, call=call, q0=q0, family=family,
        rows={Task: [task], Capability: [cap], Job: [job], Invocation: [call], DataRun: [q0],
            Audit: [], Memory: [], r.Command: [], r.Evaluation: []},
        reads=[], source_reads=[], publication_reads=[], allowed=True)
    def event(action, object_id, data, actor=None, identity=None):
        row = Audit(id=identity or uid(), workspace_id=p.workspace_id, actor_id=actor or p.user_id,
            action=action, object_id=object_id, data=copy.deepcopy(data), created_at=instant(INITIAL))
        s.rows[Audit].append(row); return row
    s.event = event
    def command_for(base_ref, base_head, goal_id, memory_id, report_hash, *, existing=None):
        identity, request_id, audit_id = uid(), uid(), uid()
        body = {'request_id': request_id, 'expected_goal_hash': goal['hash'],
            'expected_mature_head_hash': base_head, 'reason': '合成已接纳命令'}
        limited = {key: scope[key] for key in ('workspace_id', 'task_id', 'instance_id',
            'release_id', 'package_id', 'business_id', 'graph_hash')}
        j = existing or Job(id=uid(), workspace_id=p.workspace_id, kind=cmd.c.KIND,
            status='queued', attempts=0, version=1)
        record = {'schema': cmd.c.SCHEMA, 'command_id': identity, 'workspace_id': p.workspace_id,
            'task_id': task.id, 'actor_id': p.user_id, 'request_id': request_id, 'body': body,
            'body_hash': cmd.c.body_hash(task.id, body), 'goal_version': goal['version'],
            'goal_hash': goal['hash'], 'goal_snapshot_hash': digest(goal),
            'native_goal_context_hash': scope['native_goal_context_hash'], 'scope': limited,
            'scope_hash': digest(limited), 'source_binding_hash': scope['source_binding_hash'],
            'capability_id': cap.id, 'capability_hash': cap.content_hash, 'release_version': cap.release_version,
            'inputs': inputs, 'input_hash': digest(inputs), 'original_query_ref': value['original_query_ref'],
            'base_evaluation_ref': base_ref, 'base_mature_head_hash': base_head,
            'base_goal_check_job_id': goal_id, 'base_goal_check_memory_id': memory_id,
            'base_goal_check_report_hash': report_hash, 'job_id': j.id,
            'invocation_key': cmd.c.invocation_key(task.id, request_id),
            'accepted_audit_id': audit_id, 'accepted_at': INITIAL}
        record['record_hash'] = digest(record)
        record = cmd.c.AppendRecord.model_validate(record).model_dump(mode='json')
        row = r.Command(id=identity, workspace_id=p.workspace_id, task_id=task.id,
            version=1, created_at=instant(INITIAL), data=record,
            **{key: record[key] for key in ('actor_id', 'goal_version', 'goal_hash', 'request_id',
                'body_hash', 'capability_id', 'capability_hash', 'input_hash', 'job_id', 'invocation_key', 'record_hash')})
        j.kind = cmd.c.KIND; j.idempotency_key = record['invocation_key']; j.data = cmd.job_data_for(record)
        if existing is None: s.rows[Job].append(j)
        s.rows[r.Command].append(row)
        event(cmd.c.ACTION, identity, cmd._anchor(record), identity=audit_id)
        return row, record, j
    s.command_for = command_for
    if family == 'append':
        prior = {'id': uid(), 'record_hash': 'e' * 64}
        s.base_command, s.base_record, _ = command_for(prior, 'f' * 64, uid(), uid(), '1' * 64, existing=job)
        value.update(schema=newer.RECORD, producer_family=newer.PRODUCER_FAMILY, generation=2,
            command_ref={'id': s.base_command.id, 'record_hash': s.base_record['record_hash']})
        value['producer'] = {key: producer[key] for key in newer.ProducerPin.model_fields}
        producer = value['producer']; value['request_key'] = job.idempotency_key
        producer['request_key_hash'] = digest(job.idempotency_key)
        value['trigger'] = {'kind': 'append_requested', 'command_ref': value['command_ref'],
            'base_evaluation_ref': prior, 'base_mature_head_hash': s.base_record['base_mature_head_hash'],
            'request_hash': s.base_record['body_hash']}
        call.idempotency_key = job.idempotency_key
        call.data = {**call.data, 'approval': cmd.approval_for(s.base_record)}
    marker = {'schema': 'goal-evaluation-query-provenance-v299' if family == 'append' else r.query.SCHEMA,
        'evaluation_id': call.id, 'request_id': job.id, 'request_key': call.idempotency_key,
        'scope': scope, 'creator': {**producer, 'request_key': call.idempotency_key},
        'goal_snapshot': goal, 'preparation_policy': value['preparation_policy'],
        'derived_window': value['derived_window'], 'cut_at': value['cut_at'],
        'cut_checked_at': value['cut_checked_at'], 'controls': r.query.CONTROLS,
        'original_query_ref': value['original_query_ref'], 'original_query_snapshot': original}
    if family == 'append':
        marker.update(producer_family=newer.PRODUCER_FAMILY, command_ref=value['command_ref'])
        marker['creator']['command_ref'] = value['command_ref']
    marker['marker_hash'] = r.query.hashed(marker)
    qe = DataRun(id=value['evaluation_query_ref']['query_id'], workspace_id=p.workspace_id,
        owner_id=p.user_id, version=1, question=goal['spec']['purpose'], status='sufficient',
        data={'request': value['evaluation_request'], 'result': value['evaluation_result'], r.query.KEY: marker})
    value['evaluation_query_ref'] = r.query.query_ref(qe)
    value['record_hash'] = contract.record_hash(value)
    row = r.Evaluation(**r.row_values(value)); s.rows[r.Evaluation].append(row)
    s.row, s.value, s.qe = row, value, qe; s.rows[DataRun].append(qe)
    s.saved = event('workflow.goal_evaluation.query_saved_v299' if family == 'append' else r.query.SAVED,
        qe.id, {'schema': marker['schema'], 'evaluation_id': call.id, 'job_id': job.id,
            'marker_hash': marker['marker_hash'], 'query_ref': value['evaluation_query_ref'],
            **({'producer_family': newer.PRODUCER_FAMILY, 'command_ref': value['command_ref']}
                if family == 'append' else {})})
    output = old._output(row)
    journal = {'schema': append.STATE if family == 'append' else old.STATE, 'task_id': task.id,
        'job_id': job.id, 'invocation_id': call.id, 'capability_id': cap.id,
        'capability_hash': cap.content_hash, 'input_hash': digest(inputs),
        'approval_hash': digest(call.data['approval']), 'evaluation_record_hash': row.record_hash, 'output': output}
    if family == 'append': journal['command_ref'] = value['command_ref']
    journal['journal_hash'] = digest(journal)
    call.data = {**call.data, 'output': output, (append.KEY if family == 'append' else old.KEY): journal}
    s.journal = journal
    s.created = event(append.CREATED if family == 'append' else old.CREATED, call.id,
        append._anchor(journal) if family == 'append' else old._anchor(journal))
    s.invoke = event('capability.invoke', call.id, {'status': 'succeeded'})
    goal_job = Job(id=uid(), workspace_id=p.workspace_id, kind=r.provider.KIND, status='succeeded',
        version=1, lease_until=None, idempotency_key='native-goal:' + task.id + ':'
        + task.data['native_goal_context_hash'] + ':original', data={'task_id': task.id,
            'context_hash': task.data['native_goal_context_hash'], 'actor_id': p.user_id,
            r.provider.JOB_REF: {'id': row.id, 'record_hash': row.record_hash}, r.provider.JOB_PRODUCER: job.id})
    head = {'schema': r.provider.HEAD, 'task_id': task.id,
        'evaluation_ref': {'id': row.id, 'record_hash': row.record_hash}, 'generation': row.generation,
        'query_id': qe.id, 'goal_snapshot_hash': row.goal_snapshot_hash, 'source_binding_hash': row.source_binding_hash,
        'goal_check_job_id': goal_job.id, 'actor_id': p.user_id,
        'previous_hash': s.base_record['base_mature_head_hash'] if family == 'append' else None}
    head['head_hash'] = digest(head)
    report = {'goal_snapshot': goal, r.provider.JOB_REF: head['evaluation_ref'],
        'evidence': {r.provider.VIEW: {'evaluation_ref': head['evaluation_ref']}},
        'goal_evaluation_head_hash_v288': head['head_hash']}
    memory = Memory(id=uid(), workspace_id=p.workspace_id, status='candidate', text='历史成果',
        data={'type': 'native_goal_check', 'task_id': task.id, 'report': report, 'report_hash': digest(report)})
    goal_job.data = {**goal_job.data, 'goal_check_id': memory.id}
    s.goal_job, s.memory, s.head = goal_job, memory, head
    s.rows[Job].append(goal_job); s.rows[Memory].append(memory)
    s.selected_event = event(r.provider.SELECTED, task.id, head)
    if family == 'append':
        s.matured = event(maturity.MATURED, task.id,
            {'producer_job_id': job.id, 'evaluation_ref': head['evaluation_ref'], 'goal_check_job_id': goal_job.id})
    s.command, s.record, s.request_job = command_for(head['evaluation_ref'], head['head_hash'],
        goal_job.id, memory.id, digest(report))
    s.source = {'task': task, 'job': job, 'call': call, 'cap': cap, 'graph': graph,
        'activation': {'id': value['producer'].get('activation_id'), 'status': 'completed'},
        'approval': call.data['approval']}
    class IO:
        @property
        def no_autoflush(self): return nullcontext()
        def scalar(self, statement):
            model = statement.column_descriptions[0]['entity']
            s.reads.append(('fresh', model)); assert p.workspace_id in statement.compile().params.values()
            items = [item for item in s.rows[model] if predicate(statement.whereclause, item)]
            assert len(items) <= 1
            return items[0] if items else None
        def scalars(self, statement):
            model = statement.column_descriptions[0]['entity']
            s.reads.append(('inventory', model)); assert p.workspace_id in statement.compile().params.values()
            assert cmd.MAX_COMMANDS + 1 in statement.compile().params.values()
            return [item for item in s.rows[model] if predicate(statement.whereclause, item)]
        def flush(self): pytest.fail('routing cannot flush')
        def commit(self): pytest.fail('routing cannot commit')
        def add(self, item): pytest.fail('routing cannot invent closure')
    s.db = IO()
    def reader(db, principal, **options):
        assert db is s.db and principal is p
        if not s.allowed: raise HTTPException(403, 'explicit current reader IAM denial')
    monkeypatch.setattr(cmd, '_reader', reader)
    def graph_origin(db, principal, actual, activation, node):
        assert db is s.db and principal is p and actual is task
        assert activation == value['producer']['activation_id'] and node == 'evaluation_packet'
        s.source_reads.append('explicit original graph/SDK/dispatch/completion IO')
        return s.source
    monkeypatch.setattr(old, '_cold_node', graph_origin)
    def publication(db, principal, actual, actual_head):
        assert db is s.db and principal is p and actual is task
        s.publication_reads.append('explicit full independent publication receipt IO')
        r.need(s.matured in s.rows[Audit] and actual_head == head)
        return {'row': row, 'record': copy.deepcopy(value), 'head': copy.deepcopy(head),
            'candidate': {'base_head': {'head_hash': s.base_record['base_mature_head_hash'],
                'evaluation_ref': s.base_record['base_evaluation_ref']}}}
    monkeypatch.setattr(maturity, 'publication_receipt', publication)
    def forbidden(*args, **kwargs): pytest.fail('routing must not recurse or authenticate old facts')
    for module, names in ((r.provider, ('_record', '_head')),
            (cmd, ('_mature_value', '_base_history', 'runner_authority')),
            (old, ('saved_result', 'saved_receipt')), (append, ('runner_authority', 'checked_record'))):
        for name in names: monkeypatch.setattr(module, name, forbidden)
    return s


@pytest.fixture
def legacy_scene(monkeypatch): return build(monkeypatch)


@pytest.fixture
def append_scene(monkeypatch): return build(monkeypatch, family='append')


def checked(s): return r.checked_base(s.db, s.p, s.task, s.selected, s.record)


@pytest.mark.parametrize('fixture', ['legacy_scene', 'append_scene'])
def test_returns_detached_routing_only_without_opening_ancestors(request, fixture):
    s = request.getfixturevalue(fixture); before = copy.deepcopy(s.row.data)
    proof = checked(s)
    assert proof['purpose'] == 'routing-not-facts' and proof['schema'] == r.SCHEMA
    assert proof['head'] == s.head and proof['base_evaluation_ref'] == s.head['evaluation_ref']
    assert proof['memory_id'] == s.memory.id and proof['report_hash'] == s.record['base_goal_check_report_hash']
    assert proof['sdk_input_hash'] == s.call.data['input_hash']
    assert all(proof[key] is False for key in contract.FLAGS)
    assert not {'record', 'report', 'ack_refs'} & proof.keys()
    proof['head']['generation'] = 999
    assert s.head['generation'] != 999 and s.row.data == before
    if s.family == 'append':
        assert s.publication_reads and not s.source_reads
        assert not any(item.id == s.base_record['base_evaluation_ref']['id'] for item in s.rows[r.Evaluation])
    else: assert len(s.source_reads) == 1


@pytest.mark.parametrize('fault', ['accepted-missing', 'selected-orphan', 'wrong-scope', 'wrong-task',
    'report-tamper', 'goal-running', 'producer-running', 'sdk-unknown', 'created-missing',
    'saved-missing', 'saved-duplicate', 'query-tamper', 'inputs-tamper', 'unknown-schema', 'mixed-origin'])
def test_custody_failures_never_trigger_replay(legacy_scene, fault):
    s = legacy_scene
    if fault == 'accepted-missing': s.rows[Audit] = [a for a in s.rows[Audit] if a.id != s.record['accepted_audit_id']]
    elif fault == 'selected-orphan': s.rows[Audit].remove(s.selected_event)
    elif fault == 'wrong-scope': s.selected['release'].id = uid()
    elif fault == 'wrong-task': s.row.task_id = uid()
    elif fault == 'report-tamper': s.memory.data['report']['goal_evaluation_head_hash_v288'] = '0' * 64
    elif fault == 'goal-running': s.goal_job.status = 'running'
    elif fault == 'producer-running': s.job.status = 'running'
    elif fault == 'sdk-unknown': s.call.status = 'unknown'
    elif fault == 'created-missing': s.rows[Audit].remove(s.created)
    elif fault == 'saved-missing': s.rows[Audit].remove(s.saved)
    elif fault == 'saved-duplicate': s.event(s.saved.action, s.saved.object_id, s.saved.data)
    elif fault == 'query-tamper': s.qe.data = {**s.qe.data, 'result': {'status': 'sufficient'}}
    elif fault == 'inputs-tamper': s.call.data = {**s.call.data, 'inputs': {'other': True}}
    elif fault == 'unknown-schema': s.row.data = {**s.row.data, 'schema': 'other-history'}
    else: s.event(append.CREATED, s.call.id, {'orphan': True})
    with pytest.raises(HTTPException): checked(s)


def unknown_legacy(s):
    s.invoke.data = {'status': 'unknown'}
    s.call.data = {**s.call.data, 'service_previous_status': 'unknown', 'service_receipt_verified_at': CUT}
    payload = {'schema': recovery.SCHEMA, 'workspace_id': s.p.workspace_id, 'task_id': s.task.id,
        'job_id': s.job.id, 'invocation_id': s.call.id, 'actor_id': s.p.user_id,
        'activation_id': s.source['activation']['id'], 'capability_id': s.cap.id,
        'capability_hash': s.cap.content_hash, 'input_hash': s.call.data['input_hash'],
        'approval_hash': digest(s.call.data['approval']), 'source_binding_hash': s.selected['binding']['binding_hash'],
        'initial_unknown_audit_id': s.invoke.id, 'query_ref': s.value['evaluation_query_ref'],
        'evaluation_record_hash': s.row.record_hash, 'journal_hash': s.journal['journal_hash'],
        'output_hash': digest(s.call.data['output'])}
    payload['record_hash'] = digest(payload)
    event = s.event(recovery.TERMINAL, s.call.id, payload)
    s.event('workflow.service.receipt_verified', s.call.id,
        {'job_id': s.job.id, 'activation_id': s.source['activation']['id']})
    return event


def test_unknown_retains_original_invoke_and_requires_exact_terminal(legacy_scene):
    s = legacy_scene; event = unknown_legacy(s)
    assert checked(s)['historical_publication_verified'] is True
    assert s.invoke.data == {'status': 'unknown'} and event.data['initial_unknown_audit_id'] == s.invoke.id


@pytest.mark.parametrize('fault', ['terminal-missing', 'terminal-rehashed', 'initial-changed',
    'invoke-duplicate', 'marker-terminal-double-delete'])
def test_unknown_cannot_downgrade_to_ordinary_success(legacy_scene, fault):
    s = legacy_scene; event = unknown_legacy(s)
    if fault == 'terminal-missing': s.rows[Audit].remove(event)
    elif fault == 'terminal-rehashed':
        event.data['output_hash'] = '0' * 64
        event.data['record_hash'] = digest({k: v for k, v in event.data.items() if k != 'record_hash'})
    elif fault == 'initial-changed': s.invoke.data = {'status': 'succeeded'}
    elif fault == 'invoke-duplicate': s.event('capability.invoke', s.call.id, {'status': 'succeeded'})
    else:
        s.call.data.pop('service_previous_status'); s.call.data.pop('service_receipt_verified_at')
        s.rows[Audit].remove(event)
    with pytest.raises(HTTPException): checked(s)


@pytest.mark.parametrize('fault', ['publication-missing', 'marker-publication-double-delete',
    'command-ref-drift', 'base-accepted-missing', 'sdk-unknown'])
def test_append_never_falls_back_to_legacy_when_proofs_disappear(append_scene, fault):
    s = append_scene
    if fault in {'publication-missing', 'marker-publication-double-delete'}:
        s.rows[Audit].remove(s.matured)
        if fault == 'marker-publication-double-delete': s.call.data.pop(append.KEY)
    elif fault == 'command-ref-drift': s.call.data[append.KEY]['command_ref']['id'] = uid()
    elif fault == 'base-accepted-missing':
        s.rows[Audit] = [a for a in s.rows[Audit] if a.id != s.base_record['accepted_audit_id']]
    else: s.call.status = 'unknown'
    with pytest.raises(HTTPException): checked(s)
    assert not s.source_reads


def test_missing_legacy_admission_anchor_explicitly_needs_full_proof(legacy_scene):
    s = legacy_scene; supplied = copy.deepcopy(s.record); supplied.pop('base_goal_check_report_hash')
    with pytest.raises(HTTPException) as error: r.checked_base(s.db, s.p, s.task, s.selected, supplied)
    assert error.value.detail['reason_code'] == 'legacy_full_needed'


def test_current_reader_denial_is_not_cached(legacy_scene):
    s = legacy_scene; checked(s); s.allowed = False
    with pytest.raises(HTTPException) as error: checked(s)
    assert error.value.status_code == 403

