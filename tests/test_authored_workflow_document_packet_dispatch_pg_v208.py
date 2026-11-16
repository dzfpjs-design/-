"""Normal V208/175/source APIs must reach a real local packet Invocation.

Root runs this only on its disposable ga_test PostgreSQL. Accounts beyond the
existing login fixture, graphs, Tasks, sources, role grants and approvals all
use normal authenticated APIs. The native parser and worker process_once are
real; no gateway, lease, producer or successful receipt is mocked. Explicit
test-process scheduling is not proof of an autonomous default worker process.
No LLM/provider call or approval/Task database fabrication is used.
"""
import copy
import hashlib
from contextlib import ExitStack
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, runtime_task_documents as sources, worker
from generic_agents import runtime_document_receipts as delivery
from generic_agents import workflow_document_packet_v207 as packet
from generic_agents import workflow_graph
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.evidence import evidence_bytes
from tests.test_authored_workflow_pg_v208 import adopted, good
from tests.test_business_data_gateway_pg import scene
from tests.test_instance_workspace import OBJECTS, object_row
from tests.test_runtime_documents_pg_v205 import uploaded, run, version

BASE = '/api/runtime-task-documents'
SETTINGS = '/api/workspace/approval-settings-v175'


def publish_and_activate(s):
    cap, preview, _, _ = adopted(s.client)
    draft = good(s.client.get('/api/setup/draft'))
    request = {'revision': draft['revision'], 'objects': OBJECTS,
        'role_bindings': {role: value['user_id'] for role, value in preview['binding']['role_bindings'].items()}}
    checked = good(s.client.post('/api/instances/check', json=request))
    assert not checked['general_issues']
    release = good(s.client.post('/api/instances', json={**request,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True}), 201)
    current = next(item for item in good(s.client.get('/api/instances')) if item['id'] == release['instance_id'])
    good(s.client.post('/api/instances/' + current['id'] + '/activate', json={'version': current['version'],
        'release_id': release['id'], 'business_ids': release['available_business_ids'], 'confirm_workflow_scope': True}))
    return cap, preview, release


def operator(s, stack, *, login=True):
    username, password = 'packet_v208_' + uuid4().hex[:12], uuid4().hex
    account = good(s.client.post('/api/users', json={'username': username, 'password': password, 'role': 'operator'}), 201)
    if not login: return account, None
    client = stack.enter_context(TestClient(s.client.app))
    session = good(client.post('/api/login', json={'username': username, 'password': password}))
    client.headers.update({'X-CSRF-Token': session['csrf_token'], 'X-Workspace-ID': s.workspace})
    assert good(client.get('/api/me'))['user_id'] == account['id']
    return account, client


def configure_two_stage_policy(s, release, first, second, acceptor):
    unit = str(uuid4())
    good(s.client.post(SETTINGS + '/units', json={'request_id': str(uuid4()), 'unit_id': unit,
        'expected_version': 0, 'name': '公开资料逐级核对范围', 'parent_id': None, 'enabled': True}))
    owner = good(s.client.get('/api/me'))['user_id']
    for actor, role in [(first['id'], 'review_one'), (second['id'], 'review_two'),
            (acceptor['id'], 'acceptor'), (owner, 'executor')]:
        good(s.client.post(SETTINGS + '/roles', json={'request_id': str(uuid4()), 'grant_id': str(uuid4()),
            'expected_version': 0, 'unit_id': unit, 'user_id': actor, 'role_key': role, 'enabled': True}))
    stage = lambda name, role: {'name': name, 'role_key': role, 'scope_id': unit}
    good(s.client.post(SETTINGS + '/policies', json={'request_id': str(uuid4()),
        'instance_id': release['instance_id'], 'business_id': release['available_business_ids'][0],
        'expected_version': 0, 'name': '独立两级核对后明确启动', 'scope_id': unit,
        'stages': [stage('一级核对', 'review_one'), stage('二级核对', 'review_two')],
        'executor': stage('明确启动', 'executor'), 'acceptance': stage('独立人工验收', 'acceptor'), 'enabled': True}))


def task_jobs(s, task_id):
    with Session(engine()) as db:
        return [(row.id, row.status, copy.deepcopy(row.data)) for row in db.scalars(select(m.Job).where(
            m.Job.workspace_id == s.workspace, m.Job.kind == workflow_graph.KIND,
            m.Job.data['task_id'].as_string() == task_id))]


def command(client, path, action, **fields):
    current = good(client.get(path))
    return good(client.post(path + '/' + action, json={'request_id': str(uuid4()),
        'expected_version': current['version'], 'reason': '按原资料片段及当前提交逐级核对', **fields}))


def test_normal_approved_native_sources_dispatch_true_packet_and_reach_first_human(scene):
    s = scene
    with ExitStack() as stack:
        cap, preview, release = publish_and_activate(s)
        first, first_client = operator(s, stack)
        second, second_client = operator(s, stack)
        acceptor, _ = operator(s, stack, login=False)
        configure_two_stage_policy(s, release, first, second, acceptor)
        entity = object_row(s.client, release)
        task = good(s.client.post('/api/workspace/instances/' + release['instance_id'] + '/tasks', json={
            'request_id': str(uuid4()), 'release_id': release['id'],
            'business_id': release['available_business_ids'][0], 'entity_ids': [entity['id']],
            'title': '原文资料明确审批后保存本地资料包', 'graph_inputs': {}}), 201)
        task_id = task['id']; approval_path = '/api/workspace/tasks/' + task_id + '/approval-v175'
        assert good(s.client.get(approval_path))['status'] == 'draft'

        raw = '明确选取的原始😀业务资料；未核对的文字不能当作事实。'.encode()
        document = good(uploaded(s, release, raw, 'approved-native.txt')[0], 201)
        native_id = document['latest_version_id']
        assert run(s, native_id) == 'succeeded'
        delivery.consume_once(s.workspace)
        native = version(s, native_id)
        assert native['status'] == 'parsed' and native['delivery']['verified']
        selected_text = '明确选取的原始😀业务资料'
        selector = {'kind': 'native', 'version_id': native_id, 'attempt_id': native['latest_attempt_id'],
            'parse_hash': native['parse_hash'], 'block_id': 'b1', 'char_span': {'start': 0, 'end': len(selected_text)}}
        context = good(s.client.get(BASE + '/tasks/' + task_id + '/context'))
        assert context['can_select'] and len(context['input_ports']) == 1
        source = good(s.client.post(BASE + '/tasks/' + task_id + '/source-sets', json={
            'request_id': str(uuid4()), 'expected_task_version': context['task_version'],
            'expected_approval_version': context['approval_version'], 'prior_set_id': None, 'prior_set_hash': None,
            'input_key': context['input_ports'][0]['key'], 'selectors': [selector]}), 201)
        assert source['entries'][0]['text'] == selected_text and source['entries'][0]['unreviewed']
        assert source['entries'][0]['selector'] == selector
        approval = good(s.client.get(approval_path))
        assert context['input_ports'][0]['key'] not in approval['value']['graph_inputs']
        value = copy.deepcopy(approval['value']); value['graph_inputs'][source['input_key']] = source['ref']
        command(s.client, approval_path, 'revise', value=value)
        assert good(s.client.get(approval_path))['value']['graph_inputs'][source['input_key']] == source['ref']
        command(s.client, approval_path, 'submit')
        submitted = good(s.client.get(approval_path)); submission_hash = submitted['submission_hash']
        for stage, reviewer in enumerate([first_client, second_client]):
            current = good(reviewer.get(approval_path))
            assert current['current_stage'] == stage and current['submission_hash'] == submission_hash
            assert current['permissions']['decide']
            command(reviewer, approval_path, 'decide', submission_hash=submission_hash, decision='approve')
        approved = good(s.client.get(approval_path))
        assert approved['status'] == 'approved' and len(approved['decisions']) == 2
        assert len({item['actor_id'] for item in approved['decisions']}) == 2
        assert good(s.client.get(BASE + '/tasks/' + task_id + '/packets'))['records'] == []
        assert task_jobs(s, task_id) == []  # Approval alone must not dispatch.
        with Session(engine()) as db:
            assert db.scalar(select(func.count()).select_from(m.Invocation).where(m.Invocation.workspace_id == s.workspace)) == 0
            original_entity = copy.deepcopy((db.get(m.Entity, entity['id']).version, db.get(m.Entity, entity['id']).data))

        command(s.client, approval_path, 'start')
        queued = task_jobs(s, task_id)
        assert len(queued) == 1 and queued[0][1] == 'queued'
        job_id, _, job_data = queued[0]
        assert job_data['inputs'] == {'source_set_ref': source['ref']}
        assert worker.process_once(job_id, kinds=[workflow_graph.KIND], deliver=False, workspace_id=s.workspace)

        with Session(engine()) as db:
            job = db.get(m.Job, job_id)
            assert job.status == 'succeeded', job.data
            assert job.attempts == 1
            calls = list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == s.workspace)))
            assert len(calls) == 1
            call = calls[0]
            assert call.status == 'succeeded' and call.capability_id == cap['id']
            assert call.data['capability_snapshot']['execution']['adapter'] == packet.ADAPTER
            assert call.data['capability_hash'] == cap['content_hash'] and call.data['role'] == 'data'
            assert call.data['inputs'] == job_data['inputs'] and call.data['input_hash'] == digest(job_data['inputs'])
            assert call.data['job_id'] == job_id and call.data['side_effect'] is True
            assert call.data['approval'] == {'schema': packet.APPROVAL, 'task_id': task_id, 'job_id': job_id,
                'activation_id': job_data['activation_id'], 'graph_hash': job_data['graph_hash'],
                'cancel_epoch': job_data['cancel_epoch'], 'input_hash': digest(job_data['inputs']),
                'capability_id': cap['id'], 'capability_hash': cap['content_hash']}
            saved_task = db.get(m.Task, task_id)
            transport = saved_task.data[workflow_graph.TRANSPORT][job_data['activation_id']]
            assert transport['phase'] == 'succeeded' and transport['invocation_id'] == call.id
            assert call.data['lease_token'] == transport['dispatch_ownership']['lease_token']
            assert transport['dispatch_authorization']['lease_token'] == call.data['lease_token']
            rows = list(db.scalars(select(packet.Packet).where(packet.Packet.workspace_id == s.workspace,
                packet.Packet.task_id == task_id)))
            assert len(rows) == 1
            produced = rows[0]; proof = packet.checked_packet(db, s.p, produced)
            assert proof['invocation_id'] == call.id and proof['producer_job_id'] == job_id
            assert produced.source_set_id == source['id'] and produced.invocation_id == call.id
            commands = list(db.scalars(select(packet.Command).where(packet.Command.workspace_id == s.workspace)))
            assert len(commands) == 1 and commands[0].packet_id == produced.id
            assert commands[0].data['output'] == call.data['output']
            original_source = db.get(sources.SourceSet, source['id'])
            actual_evidence, content = evidence_bytes(db, s.p, produced.evidence_id)
            assert content == sources.render(original_source.data['entries'])
            assert selected_text in content.decode() and '未核对的文字不能当作事实' not in content.decode()
            assert produced.sha256 == actual_evidence.sha256 == hashlib.sha256(content).hexdigest()
            audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace,
                m.Audit.action == packet.PRODUCED, m.Audit.object_id == call.id)))
            assert len(audits) == 1 and audits[0].data == packet.receipt_audit_data(call, job, call.data['output'])
            assert call.data['output']['proposal_only'] and not call.data['output']['cloud_authorized']
            assert not call.data['output']['physical_state_verified']
            assert db.scalar(select(func.count()).select_from(m.CloudCall).where(m.CloudCall.workspace_id == s.workspace)) == 0
            assert (db.get(m.Entity, entity['id']).version, db.get(m.Entity, entity['id']).data) == original_entity
            packet_id = produced.id

        packet_view = good(s.client.get(BASE + '/packets/' + packet_id))
        assert packet_view['source_set']['ref'] == source['ref'] and packet_view['text'] == content.decode()
        current_task = good(s.client.get('/api/workspace/tasks/' + task_id))
        humans = current_task['graph_pending_humans']
        assert len(humans) == 1 and humans[0]['source_step_id'] == preview['binding']['flow']['steps'][0]['id']
        assert humans[0]['actor_id'] == s.p.user_id and humans[0]['can_edit'] and not current_task['completed']
        assert good(s.client.get(approval_path))['status'] == 'executing'
        assert good(s.client.get(BASE + '/source-sets/' + source['id'])) == source
        assert s.client.get('/api/runtime-documents/versions/' + native_id + '/original').content == raw
        assert not worker.process_once(job_id, kinds=[workflow_graph.KIND], deliver=False, workspace_id=s.workspace)
        assert good(s.client.get(BASE + '/tasks/' + task_id + '/packets'))['records'] == [packet_view]
