"""Local service/producer checks over explicit SQL and current-authority spies.

Actual V196 DTO/manifest/receipt checks, the fixed V288 family checker, graph
Invocation identity, Audit comparisons, Q0 inventory, pure route and local
record code run here. Rows, current IAM/lease/approval/live-policy boundaries
are synthetic spies: this is not PG locking, source authenticity or E2E proof.
No model, query execution, device, Observation, ACK or Goal is manufactured.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator
from sqlalchemy import select

from generic_agents import workflow_goal_data_route_runtime_v288 as subject
from generic_agents import workflow_service_v195 as shared
from generic_agents import leases, native_goals
from generic_agents.contracts import Manifest, digest
from generic_agents.models import Audit, Capability, DataGap, DataRun, Invocation, Job, ObservationPlan, Task
from tests.test_workflow_goal_data_policy_v288 import scene as pure_scene, reseal
from tests.test_authored_goal_data_workflow_v288 import command, cold_binding, ident


def sql(query): return str(query.compile(compile_kwargs={'literal_binds': True}))


def manifest():
    return Manifest(kind='tool', namespace='local_route_v288', name='local_prepare', version='1.0.0',
        owner='synthetic unit boundary', description='Local route fixture; no external execution',
        allowed_agent_roles=['business'], input_schema=subject.input_schema(), output_schema=subject.output_schema(),
        execution={'adapter': 'workflow_service', 'service_actor': 'execution_service',
            'operation': subject.OP, 'side_effect': True}, specification={'schema': subject.CONTRACT},
        data_policy={'cloud_allowed': False}, dependencies=[], budget={'max_attempts': 1}).model_dump(mode='json')


def scene(monkeypatch, *, ready=False):
    pure = pure_scene(); result = pure.result; goal = pure.goal
    original_gap_id = result['resense_context']['gap']['gap_id']
    cap_data = manifest()
    cap = NS(id=ident(201), workspace_id=ident(1), data=cap_data, kind='tool', status='published',
        release_version='1.0.0', content_hash=digest(cap_data), validated_hash=digest(cap_data))
    reader_data = Manifest(kind='tool', namespace='local_route_v288', name='context_reader', version='1.0.0',
        owner='synthetic unit boundary', description='Original V196 reader fixture', allowed_agent_roles=['data'],
        execution={'adapter': 'builtin', 'handler': subject.reader.HANDLER, 'side_effect': False},
        input_schema=subject.reader.input_schema(), output_schema=subject.reader.output_schema(),
        data_policy={'cloud_allowed': False}, budget={'max_attempts': 1}).model_dump(mode='json')
    source_cap = NS(id=ident(202), workspace_id=ident(1), data=reader_data, kind='tool', status='published',
        release_version='1.0.0', content_hash=digest(reader_data), validated_hash=digest(reader_data))
    body = command(); body['preparation_policy'] = copy.deepcopy(pure.policy)
    body['tool_bindings']['local_prepare'].update(capability_id=cap.id, capability_hash=cap.content_hash)
    body['tool_bindings']['context_reader'].update(capability_id=source_cap.id,
        capability_hash=source_cap.content_hash, actor_id=ident(80))
    binding = cold_binding(body); graph = binding['graph']; nodes = {node['id']: node for node in graph['nodes']}
    p = NS(workspace_id=binding['workspace_id'], user_id=nodes['local_prepare']['actor_id'], role='operator')
    original_scope = result['evidence_package']['scope']; task_id = original_scope['task_id']
    release = NS(id=original_scope['release_id'], instance_id=original_scope['instance_id'], package_id=original_scope['package_id'])
    business = {'id': original_scope['business_id'], 'name': binding['business_name']}
    snapshot = copy.deepcopy(result['candidates'][0]['gap']['observation_request']['goal_delegation']['goal_snapshot'])
    context_pin = {'instance_id': release.instance_id, 'release_id': release.id, 'business_id': business['id'],
        'entity_ids': goal['entity_ids'], 'actor_id': p.user_id, 'goal_snapshot': snapshot}
    state = {'graph_hash': digest(graph), 'cancel_epoch': 0, 'status': 'waiting', 'activations': {},
        'eligible_ability_ids': ['prepare_activation']}
    task = NS(id=task_id, workspace_id=p.workspace_id, package_id=release.package_id, data={
        'entity_ids': goal['entity_ids'], 'instance_id': release.instance_id, 'instance_release_id': release.id,
        'business_id': business['id'], 'goal_spec': goal, 'goal_hash': digest(goal), 'goal_version': 1,
        'goal_window': snapshot['time'], 'goal_confirmed_by': p.user_id, 'native_goal_context': context_pin,
        'native_goal_context_hash': digest(context_pin), 'graph_runtime': state})
    source_inputs = {'policy_pin': {key: result['candidates'][0]['policy'][key] for key in ('policy_id', 'approved_hash')}}
    source_key = 'graph:' + task.id + ':reader_activation:1'
    source_job = NS(id=ident(203), workspace_id=p.workspace_id, status='succeeded', kind=subject.scope.KIND,
        idempotency_key=source_key, data={'task_id': task.id, 'activation_id': 'reader_activation',
            'activation_version': 0, 'actor_id': nodes['context_reader']['actor_id'], 'graph_hash': state['graph_hash'],
            'cancel_epoch': 0, 'inputs': source_inputs, 'input_hash': digest(source_inputs), 'invocation_key': source_key})
    source_call = NS(id=ident(204), workspace_id=p.workspace_id, capability_id=source_cap.id,
        idempotency_key=source_key, status='succeeded', data={'job_id': source_job.id, 'lease_token': 'reader-original-lease',
            'input_hash': digest(source_inputs), 'capability_hash': source_cap.content_hash,
            'role': 'data', 'approval': None, 'side_effect': False})
    request = copy.deepcopy(result['candidates'][0]['gap']['observation_request']['request'])
    query_id = result['evidence_package']['query']['query_id']
    report = {'status': 'insufficient', 'result': {'evidence': [], 'observation_evidence': []}}
    run = NS(id=query_id, workspace_id=p.workspace_id, version=1, status='insufficient', data={
        'request': request, 'result': report, 'native_goal_task_id': task.id,
        'native_goal_context_hash': digest(context_pin), 'native_goal_request_hash': digest(request),
        'native_goal_result_hash': digest(report), 'goal_delegation': {'task_id': task.id, 'goal_snapshot': snapshot}})
    task.data['native_goal_query_id'] = run.id
    scope_value = {**copy.deepcopy(original_scope), 'workspace_id': p.workspace_id, 'graph_hash': state['graph_hash'],
        'source_binding_hash': binding['binding_hash'], 'native_goal_context_hash': digest(context_pin)}
    producer = {'capability_id': source_cap.id, 'capability_hash': source_cap.content_hash,
        'invocation_id': source_call.id, 'job_id': source_job.id, 'actor_id': source_job.data['actor_id'],
        'activation_id': 'reader_activation', 'node_id': 'context_reader', 'cancel_epoch': 0,
        'input_hash': digest(source_inputs), 'request_key_hash': digest(source_key)}
    package = result['evidence_package']; package['scope'] = copy.deepcopy(scope_value)
    package['query']['result_hash_at_read'] = digest(report); package['producer'] = copy.deepcopy(producer)
    for context in result['candidates']:
        context['scope'] = copy.deepcopy(scope_value); context['query'] = copy.deepcopy(package['query'])
        context['producer'] = copy.deepcopy(producer)
    if ready:
        original_row = {'entity_id': goal['entity_ids'][0], 'observation_id': ident(240), 'version': 1,
            'observed_at': '2026-11-01T11:59:00+00:00', 'values': {'temperature_c': 25, 'humidity_pct': 60},
            'simulation': True, 'test_only': True, 'field_verified': False, 'production_activation': False}
        report = {'status': 'sufficient', 'result': {'evidence': [], 'observation_evidence': [original_row]}}
        run.status = 'sufficient'; run.data.update(result=report, native_goal_result_hash=digest(report))
        package.update(evidence_status='sufficient', gap_refs=[], evidence_refs=[{
            'kind': 'observation', 'entity_id': original_row['entity_id'], 'observation_id': original_row['observation_id'],
            'version': original_row['version'], 'observed_at': original_row['observed_at'], 'evidence_hash': digest(original_row)}])
        package['query']['result_hash_at_read'] = digest(report)
        result.update(status='no_gap', resense_context=None, candidates=[])
    reseal(pure); result = pure.result
    source_call.data['output'] = copy.deepcopy(result)
    reader_activation = {'id': 'reader_activation', 'node_id': 'context_reader', 'status': 'completed',
        'epoch': 0, 'output': copy.deepcopy(result), 'inputs_snapshot': {'arguments': source_inputs}}
    inputs = {'context_result': copy.deepcopy(result)}
    activation = {'id': 'prepare_activation', 'node_id': 'local_prepare', 'status': 'ready', 'epoch': 0,
        'inputs_snapshot': {'arguments': copy.deepcopy(inputs), 'bindings': {'context_result': {
            'missing': False, 'producer_activation_id': reader_activation['id'], 'value_hash': digest(result)}}}}
    state['activations'] = {reader_activation['id']: reader_activation, activation['id']: activation}
    key = 'graph:' + task.id + ':' + activation['id'] + ':1'
    job = NS(id=ident(205), workspace_id=p.workspace_id, status='running', kind=subject.scope.KIND,
        idempotency_key=key, data={'task_id': task.id, 'activation_id': activation['id'], 'activation_version': 0,
            'actor_id': p.user_id, 'graph_hash': state['graph_hash'], 'cancel_epoch': 0,
            'inputs': copy.deepcopy(inputs), 'input_hash': digest(inputs), 'invocation_key': key})
    def transport(actual_job, actual_cap, token, output=None):
        owner = {'job_id': actual_job.id, 'lease_token': token}
        return {'job_id': actual_job.id, 'invocation_key': actual_job.idempotency_key, 'cancel_epoch': 0,
            'input_hash': actual_job.data['input_hash'], 'phase': 'succeeded' if output is not None else 'running',
            'dispatch_committed': True, 'dispatch_ownership': owner, 'dispatch_authorization': {**owner,
                'actor_id': actual_job.data['actor_id'], 'epoch': 0, 'input_hash': actual_job.data['input_hash'],
                'capability_hash': actual_cap.content_hash}, **({'output_hash': digest(output)} if output is not None else {})}
    source_transport = transport(source_job, source_cap, 'reader-original-lease', result)
    source_transport['invocation_id'] = source_call.id
    current_transport = transport(job, cap, 'prepare-original-lease')
    task.data['graph_jobs'] = {'reader_activation': source_transport, 'prepare_activation': current_transport}
    approval = shared.make_approval(task, job, nodes['local_prepare'], inputs)
    call = NS(id=ident(206), workspace_id=p.workspace_id, capability_id=cap.id, idempotency_key=key,
        status='running', data={'job_id': job.id, 'lease_token': 'prepare-original-lease', 'role': 'business',
            'side_effect': True, 'capability_hash': cap.content_hash, 'input_hash': digest(inputs),
            'inputs': copy.deepcopy(inputs), 'approval': approval, 'capability_snapshot': copy.deepcopy(cap.data)})
    gap = NS(id=original_gap_id, workspace_id=p.workspace_id, status='received')
    rows = {(type_, row.id): row for type_, row in ((Task, task), (Job, job), (Job, source_job),
        (Invocation, call), (Invocation, source_call), (Capability, cap), (Capability, source_cap), (DataRun, run), (DataGap, gap))}
    events = []; checks = []; plan = {'id': None}
    def add_event(action, object_id, actor_id, data):
        events.append(NS(action=action, object_id=object_id, actor_id=actor_id, data=copy.deepcopy(data)))
    def native_events(actual_job, actual_activation, actual_call):
        add_event('workflow.graph.dispatch_authorized', task.id, actual_job.data['actor_id'], {
            'activation_id': actual_activation['id'], 'job_id': actual_job.id, 'epoch': 0,
            'input_hash': actual_job.data['input_hash']})
        add_event('workflow.graph.node_completed', task.id, actual_job.data['actor_id'], {
            'activation_id': actual_activation['id'], 'invocation_id': actual_call.id})
    native_events(source_job, reader_activation, source_call)
    add_event('capability.invoke', source_call.id, source_job.data['actor_id'], {'status': 'succeeded'})
    add_event(subject.reader.PRODUCED, source_call.id, source_job.data['actor_id'],
        subject.reader.receipt_audit_data(source_call, source_job, result))
    scalar_queries = {sql(select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity)
        .execution_options(populate_existing=True)): row for (cls, identity), row in rows.items()}
    scalar_queries[sql(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.idempotency_key == key).with_for_update().execution_options(populate_existing=True))] = call
    plan_query = sql(select(ObservationPlan.id).where(ObservationPlan.workspace_id == p.workspace_id,
        ObservationPlan.data['observation_request']['gap_id'].as_string() == gap.id).limit(1))
    audit_queries = {}
    for object_id, actions in ((source_call.id, ['capability.invoke', subject.reader.PRODUCED]),
            (call.id, [subject.CREATED, 'capability.invoke', 'workflow.service.receipt_verified'])):
        for action in actions:
            audit_queries[sql(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == action,
                Audit.object_id == object_id).limit(2))] = lambda row, a=action, oid=object_id: row.action == a and row.object_id == oid
    for actual_job, actual_activation in ((source_job, reader_activation), (job, activation)):
        for action, field, identity in [('workflow.graph.dispatch_authorized', 'job_id', actual_job.id),
                ('workflow.graph.node_completed', 'activation_id', actual_activation['id'])]:
            audit_queries[sql(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == action,
                Audit.object_id == task.id, Audit.data[field].as_string() == identity).limit(2))] = (
                    lambda row, a=action, f=field, selected=identity: row.action == a
                    and row.object_id == task.id and row.data.get(f) == selected)
    class IO:
        info = {'worker_lease': {'job_id': job.id, 'lease_token': 'prepare-original-lease'}}
        flushes = 0
        def scalar(self, query):
            if sql(query) == plan_query: return plan['id']
            assert sql(query) in scalar_queries, 'unknown SQL is rejected'
            return scalar_queries[sql(query)]
        def scalars(self, query):
            assert sql(query) in audit_queries, 'unknown Audit SQL is rejected'
            return [row for row in events if audit_queries[sql(query)](row)]
        def flush(self): self.flushes += 1
        def commit(self): pytest.fail('local service changed transaction ownership')
        def add(self, value): pytest.fail('local service created another row')
    db = IO()
    def frozen(actual_db, actual_p, identity, activation_id):
        assert actual_db is db and actual_p.workspace_id == task.workspace_id and identity == task.id
        actual_activation = state['activations'][activation_id]
        return task, release, business, graph, state, actual_activation, nodes[actual_activation['node_id']]
    monkeypatch.setattr(subject.scope, '_frozen', frozen)
    monkeypatch.setattr(subject.graph_runtime, 'business_binding', lambda *args: binding)
    def shared_authority(*args, **kwargs):
        checks.append('original-service-authority')
        assert kwargs['_variant'] is subject and args[3] == 'business'
        return {'task': task, 'job': job, 'node': nodes['local_prepare'], 'release': release, 'binding': binding,
            'transport': current_transport, 'delegation': {'agent_role': 'business'}, 'capability': cap}
    monkeypatch.setattr(shared, 'validate_call_approval', shared_authority)  # explicit current IAM/SDK boundary
    monkeypatch.setattr(native_goals, 'guard', lambda *args, **kwargs: checks.append('original-goal-authority'))
    monkeypatch.setattr(leases, 'fenced', lambda actual_db: actual_db is db)
    monkeypatch.setattr(subject.reader, 'checked_context', lambda actual_db, actual_p, value, **kwargs:
        checks.append('current-policy-context') or copy.deepcopy(value))
    monkeypatch.setattr(subject, 'audit', lambda actual_db, actual_p, action, object_id, data:
        add_event(action, object_id, actual_p.user_id, data))
    return NS(p=p, pure=pure, result=result, goal=goal, inputs=inputs, cap=cap, source_cap=source_cap,
        source_job=source_job, source_call=source_call, task=task, release=release, binding=binding,
        state=state, reader_activation=reader_activation, activation=activation, job=job, call=call,
        key=key, approval=approval, events=events, db=db, checks=checks, plan=plan,
        source_transport=source_transport, transport=current_transport, add_event=add_event, native_events=native_events)


def producer(s): return subject._producer(s.db, s.p, s.task, s.job, s.inputs['context_result'])


def execute(s): return subject.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)


def close(s, output):
    s.call.status = s.job.status = 'succeeded'; s.call.data['output'] = copy.deepcopy(output)
    s.activation.update(status='completed', output=copy.deepcopy(output))
    s.transport.update(phase='succeeded', invocation_id=s.call.id, output_hash=digest(output))
    s.native_events(s.job, s.activation, s.call)
    s.add_event('capability.invoke', s.call.id, s.job.data['actor_id'], {'status': 'succeeded'})


def test_manifest_and_graph_ports_are_fixed_new_local_contracts():
    value = manifest()
    assert subject.validate_manifest(value) == value
    for schema in (subject.input_schema(), subject.output_schema()): Draft202012Validator.check_schema(schema)
    schema = subject.output_schema()
    assert schema['type'] == 'object' and 'allOf' not in schema
    assert 'allOf' in schema['properties']['decision']
    assert subject.OP == 'plan_approved_goal_data_v288'
    bad = copy.deepcopy(value); bad['execution']['operation'] = 'plan_business_decision_review_v285'
    with pytest.raises(HTTPException): subject.validate_manifest(bad)


def test_explicit_output_checker_preserves_route_context_and_frozen_input_consistency(monkeypatch):
    s = scene(monkeypatch); value = execute(s)
    before = copy.deepcopy(value)
    assert subject.checked_output(value) == value and subject.checked_output(value) is not value
    for damage in ('route', 'missing_context', 'context_ref', 'producer_hash'):
        altered = copy.deepcopy(value)
        if damage == 'route': altered['route'] = 'data_ready'
        elif damage == 'missing_context': altered['resense_context'] = None
        elif damage == 'context_ref': altered['decision']['context_ref']['context_hash'] = '0' * 64
        else: altered['producer']['output_hash'] = '0' * 64
        with pytest.raises(HTTPException): subject.checked_output(altered)
    assert value == before
    s.call.status = 'unknown'
    record = s.call.data[subject.KEY]; record['output']['route'] = 'data_ready'
    record['record_hash'] = digest({key: item for key, item in record.items() if key != 'record_hash'})
    assert subject.retained_marker_shape(s.call) is False
    ready = execute(scene(monkeypatch, ready=True)); ready['resense_context'] = copy.deepcopy(value['resense_context'])
    with pytest.raises(HTTPException): subject.checked_output(ready)


def test_actual_completed_reader_sdk_audits_and_q0_prove_producer_under_current_different_actor(monkeypatch):
    s = scene(monkeypatch); before = copy.deepcopy(s.result)
    pin, goal_snapshot, preparation = producer(s)
    assert pin['actor_id'] == s.source_job.data['actor_id'] != s.p.user_id
    assert pin['invocation_id'] == s.source_call.id and pin['output_hash'] == digest(s.result)
    assert goal_snapshot['spec'] == s.goal and preparation == s.pure.policy
    assert s.result == before and 'inputs' not in s.source_call.data and subject.KEY not in s.call.data


def test_reader_success_output_with_running_original_job_is_pending_not_authority(monkeypatch):
    s = scene(monkeypatch); s.source_job.status = 'running'
    with pytest.raises(subject.reader.ProducerPending) as caught: producer(s)
    assert caught.value.job_id == s.source_job.id and caught.value.invocation_id == s.source_call.id


def test_missing_reader_produced_audit_rejects_otherwise_self_consistent_hashes(monkeypatch):
    s = scene(monkeypatch); s.events[:] = [row for row in s.events if row.action != subject.reader.PRODUCED]
    with pytest.raises(HTTPException): producer(s)


def test_sdk_success_audit_cannot_be_borrowed_from_current_local_actor(monkeypatch):
    s = scene(monkeypatch)
    next(row for row in s.events if row.action == 'capability.invoke').actor_id = s.p.user_id
    with pytest.raises(HTTPException): producer(s)


def test_real_q0_version_change_is_not_hidden_by_original_output_hash(monkeypatch):
    s = scene(monkeypatch)
    # The actual SELECT returns this row, not a replacement query or new result.
    run_id = s.result['evidence_package']['query']['query_id']
    run = subject._fresh(s.db, s.p, DataRun, run_id); run.version += 1
    with pytest.raises(HTTPException): producer(s)


def test_tampered_reader_output_does_not_become_true_by_rehashing_graph_and_invocation(monkeypatch):
    s = scene(monkeypatch)
    altered = copy.deepcopy(s.result); altered['evidence_package']['evidence_status'] = 'unknown'
    altered['evidence_package'] = {**altered['evidence_package'], 'package_hash': digest({
        key: value for key, value in altered['evidence_package'].items() if key != 'package_hash'})}
    s.source_call.data['output'] = copy.deepcopy(altered); s.reader_activation['output'] = copy.deepcopy(altered)
    s.source_transport['output_hash'] = digest(altered)
    s.inputs['context_result'] = copy.deepcopy(altered); s.job.data['inputs'] = copy.deepcopy(s.inputs)
    s.job.data['input_hash'] = digest(s.inputs); s.transport['input_hash'] = digest(s.inputs)
    s.activation['inputs_snapshot']['arguments'] = copy.deepcopy(s.inputs)
    s.activation['inputs_snapshot']['bindings']['context_result']['value_hash'] = digest(altered)
    with pytest.raises(HTTPException): producer(s)  # Independent original PRODUCED Audit still pins original bytes/hash.


def test_current_kernel_and_original_goal_gates_remain_required(monkeypatch):
    s = scene(monkeypatch)
    assert subject.validate_call_approval(s.db, s.p, s.cap, 'business', s.inputs, s.key, s.approval)['job'] is s.job
    assert s.checks[:2] == ['original-service-authority', 'original-goal-authority']
    monkeypatch.setattr(leases, 'fenced', lambda *args: False)
    with pytest.raises(HTTPException): subject.validate_call_approval(s.db, s.p, s.cap, 'business', s.inputs, s.key, s.approval)


def test_one_original_local_record_execute_and_reconcile_do_not_repeat_policy_or_sensing(monkeypatch):
    s = scene(monkeypatch); value = execute(s)
    assert value['route'] == 'acquire' and value['resense_context'] == s.result['resense_context']
    assert all(value[key] is False for key in subject.policy.FLAGS)
    before = copy.deepcopy(s.call.data[subject.KEY]); count = len(s.events)
    monkeypatch.setattr(subject, '_first_output', lambda *args: pytest.fail('replay rebuilt local plan'))
    assert execute(s) == value and len(s.events) == count and s.db.flushes == 1
    s.call.status = 'unknown'
    assert subject.retained_marker_shape(s.call)
    assert subject.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call) == value
    assert s.call.data[subject.KEY] == before and len(s.events) == count


def test_reader_sufficient_route_has_no_acquisition_context_or_live_monitor_lookup(monkeypatch):
    s = scene(monkeypatch, ready=True)
    monkeypatch.setattr(subject.reader, 'checked_context', lambda *args, **kwargs: pytest.fail('ready route borrowed sensing permission'))
    value = execute(s)
    assert value['route'] == 'data_ready' and value['resense_context'] is None
    assert value['decision']['reason_code'] == 'reader_reports_sufficient'
    assert all(value[key] is False for key in subject.policy.FLAGS)


def test_missing_original_local_record_never_rebuilds_through_reconcile(monkeypatch):
    s = scene(monkeypatch); execute(s); s.call.status = 'unknown'; del s.call.data[subject.KEY]
    with pytest.raises(HTTPException): subject.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    assert subject.KEY not in s.call.data


def test_existing_observation_plan_refuses_another_automatic_cycle_before_record_write(monkeypatch):
    s = scene(monkeypatch); s.plan['id'] = ident(250)
    with pytest.raises(HTTPException): execute(s)
    assert subject.KEY not in s.call.data and not any(row.action == subject.CREATED for row in s.events)


def test_saved_result_is_read_only_under_current_reader_and_checks_original_sdk_or_reconcile_audits(monkeypatch):
    s = scene(monkeypatch); value = execute(s); close(s, value)
    current = NS(workspace_id=s.p.workspace_id, user_id=ident(70), role='operator')
    before = copy.deepcopy(s.call.data)
    monkeypatch.setattr(subject.reader, 'checked_context', lambda *args, **kwargs: pytest.fail('cold proof attempted live policy'))
    assert subject.saved_result(s.db, current, s.task, s.job, s.call, s.cap, s.inputs) == value
    assert s.call.data == before
    original_success = next(row for row in s.events if row.action == 'capability.invoke' and row.object_id == s.call.id)
    original_success.data = {'status': 'unknown'}
    s.call.data.update(service_previous_status='unknown', service_receipt_verified_at='2026-11-01T12:01:00+00:00')
    s.add_event('workflow.service.receipt_verified', s.call.id, s.job.data['actor_id'], {
        'job_id': s.job.id, 'activation_id': s.activation['id']})
    assert subject.saved_result(s.db, current, s.task, s.job, s.call, s.cap, s.inputs) == value
    next(row for row in s.events if row.action == 'workflow.service.receipt_verified').actor_id = current.user_id
    with pytest.raises(HTTPException): subject.saved_result(s.db, current, s.task, s.job, s.call, s.cap, s.inputs)


def test_a_resealed_foreign_workspace_binding_is_not_the_original_task_scope(monkeypatch):
    s = scene(monkeypatch); s.binding['workspace_id'] = ident(260)
    s.binding['binding_hash'] = digest({key: value for key, value in s.binding.items() if key != 'binding_hash'})
    with pytest.raises(HTTPException): producer(s)
