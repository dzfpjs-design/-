"""Real normal APIs create accepted175/V208/V207 source before case review.

Root alone executes on disposable ga_test PostgreSQL. No direct Task, Managed,
Packet, Memory, successful Job or model record fabrication; local parsing and
worker are real. Explicit process_once is not autonomous-worker evidence.
"""
import copy
import hashlib
from contextlib import ExitStack
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from generic_agents import models as m, worker, workflow_graph
from generic_agents.db import engine
from generic_agents import runtime_document_receipts as delivery
from generic_agents import layered_cases_v209 as cases
from tests.test_business_data_gateway_pg import scene
from tests.test_authored_workflow_pg_v208 import good
from tests.test_authored_workflow_document_packet_dispatch_pg_v208 import (
    publish_and_activate, operator, configure_two_stage_policy, task_jobs, command)
from tests.test_instance_workspace import object_row
from tests.test_runtime_documents_pg_v205 import uploaded, run, version
from tests.test_layered_cases_v209 import value

BASE = cases.PREFIX


@pytest.fixture
def accepted_case_source(scene):
    s = scene
    with ExitStack() as stack:
        _, preview, release = publish_and_activate(s)
        first, first_client = operator(s, stack)
        second, second_client = operator(s, stack)
        acceptor, reviewer_client = operator(s, stack)
        configure_two_stage_policy(s, release, first, second, acceptor)
        entity = object_row(s.client, release)
        task = good(s.client.post('/api/workspace/instances/' + release['instance_id'] + '/tasks', json={
            'request_id': str(uuid4()), 'release_id': release['id'], 'business_id': release['available_business_ids'][0],
            'entity_ids': [entity['id']], 'title': '公开工程资料核对后独立验收', 'graph_inputs': {}}), 201)
        task_id = task['id']; approval_path = '/api/workspace/tasks/' + task_id + '/approval-v175'
        raw = '公开工程测试😀文字，仅核对片段，不证明现场对象状态。'.encode()
        document = good(uploaded(s, release, raw, 'engineering-source.txt')[0], 201)
        native_id = document['latest_version_id']; assert run(s, native_id) == 'succeeded'
        delivery.consume_once(s.workspace); native = version(s, native_id)
        assert native['delivery']['verified']
        source_context = good(s.client.get('/api/runtime-task-documents/tasks/' + task_id + '/context'))
        selector = {'kind': 'native', 'version_id': native_id, 'attempt_id': native['latest_attempt_id'],
            'parse_hash': native['parse_hash'], 'block_id': 'b1', 'char_span': {'start': 0, 'end': 15}}
        source = good(s.client.post('/api/runtime-task-documents/tasks/' + task_id + '/source-sets', json={
            'request_id': str(uuid4()), 'expected_task_version': source_context['task_version'],
            'expected_approval_version': source_context['approval_version'], 'prior_set_id': None, 'prior_set_hash': None,
            'input_key': source_context['input_ports'][0]['key'], 'selectors': [selector]}), 201)
        approval = good(s.client.get(approval_path)); draft = copy.deepcopy(approval['value'])
        draft['graph_inputs'][source['input_key']] = source['ref']
        command(s.client, approval_path, 'revise', value=draft)
        command(s.client, approval_path, 'submit')
        submission_hash = good(s.client.get(approval_path))['submission_hash']
        for client in (first_client, second_client):
            command(client, approval_path, 'decide', submission_hash=submission_hash, decision='approve')
        command(s.client, approval_path, 'start')
        queued = task_jobs(s, task_id); assert len(queued) == 1
        assert worker.process_once(queued[0][0], kinds=[workflow_graph.KIND], deliver=False, workspace_id=s.workspace)
        task_path = '/api/workspace/tasks/' + task_id
        for _ in preview['binding']['flow']['steps']:
            current = good(s.client.get(task_path)); human = current['graph_pending_humans'][0]
            good(s.client.post(task_path + '/graph/complete', json={'request_id': str(uuid4()),
                'activation_id': human['activation_id'], 'version': human['version'],
                'cancel_epoch': current['graph_runtime']['cancel_epoch'],
                'output': {'result': '真实人工工程核对已完成，仍未证明现场状态'}}))
        basis = good(reviewer_client.get(approval_path))['acceptance_basis']; assert basis
        command(reviewer_client, approval_path, 'accept', decision='accept', basis_hash=basis['basis_hash'])
        assert good(s.client.get(approval_path))['status'] == 'accepted'
        source_context = good(s.client.get(BASE + '/tasks/' + task_id + '/context'))
        assert source_context['can_create'] and source_context['source']['human_results']
        yield SimpleNamespace(s=s, task_id=task_id, approval_path=approval_path, task_path=task_path,
            author_client=s.client, reviewer_client=reviewer_client, first_client=first_client,
            second_client=second_client, reviewer_id=acceptor['id'], source_context=source_context,
            release=release, source_set_id=source['id'], native_id=native_id)


def create_case(f):
    body = {'request_id': str(uuid4()), 'expected_source_task_version': f.source_context['task_version'],
        'source_fingerprint': f.source_context['source_fingerprint'], 'value': value()}
    response = f.author_client.post(BASE + '/tasks/' + f.task_id + '/cases', json=body)
    return body, response, good(response, 201)


def change(client, view, operation, **fields):
    return client.post(BASE + '/cases/' + view['id'] + '/' + operation, json={
        'request_id': str(uuid4()), 'expected_case_version': view['version'],
        'revision_id': view['revision']['id'], 'revision_hash': view['revision']['revision_hash'],
        'reason': '对照原固定版本与工程边界独立核对', **fields})


def original_inventory(f):
    with Session(engine()) as db:
        task = db.get(m.Task, f.task_id)
        rows = {}
        for cls in (m.Job, m.Invocation, m.CloudCall, m.Memory):
            selected = db.scalars(select(cls).where(cls.workspace_id == f.s.workspace))
            rows[cls.__tablename__] = [(r.id, r.version, copy.deepcopy(r.data), r.status) for r in selected]
        return (task.version, task.status, copy.deepcopy(task.data), rows)


def test_real_source_case_revise_independent_review_exact_requests_and_search(accepted_case_source):
    f = accepted_case_source; before = original_inventory(f)
    body, response, result = create_case(f); original = copy.deepcopy(result)
    view = result['case']; assert view['status'] == 'draft' and view['permissions']['revise']
    assert not view['source']['physical_state_verified'] and not view['source']['model_results']
    revised = value(); revised['experience'] = '先核对原文，再确认工程边界，最后独立审核'
    view = good(change(f.author_client, view, 'revise', value=revised))['case']
    assert view['revision']['ordinal'] == 2
    view = good(change(f.author_client, view, 'submit'))['case']; assert view['status'] == 'in_review'
    assert change(f.author_client, view, 'review', decision='approve').status_code == 403
    review_view = good(f.reviewer_client.get(BASE + '/cases/' + view['id']))
    assert review_view['permissions']['review']
    view = good(change(f.reviewer_client, review_view, 'review', decision='return'))['case']
    view = good(change(f.author_client, view, 'revise', value=revised))['case']
    view = good(change(f.author_client, view, 'submit'))['case']
    view = good(change(f.reviewer_client, view, 'review', decision='approve'))['case']
    assert view['status'] == 'approved' and not any(view['permissions'].values())
    records = good(f.author_client.get(BASE + '/search', params={'q': revised['experience']}))['records']
    assert [row['id'] for row in records] == [view['id']]
    history = good(f.author_client.get(BASE + '/cases/' + view['id'] + '/revisions'))
    assert [row['ordinal'] for row in history['records']] == [3, 2, 1]
    sha = hashlib.sha256(response.request.content).hexdigest()
    recovered = good(f.author_client.get(BASE + '/requests/' + body['request_id'], params={'expected_sha256': sha}))
    assert recovered['found'] and recovered['result'] == original
    assert good(f.author_client.post(BASE + '/tasks/' + f.task_id + '/cases',
        content=response.request.content, headers={'Content-Type': 'application/json'}), 201) == original
    assert f.reviewer_client.get(BASE + '/requests/' + body['request_id'], params={'expected_sha256': sha}).status_code == 403
    assert f.author_client.get(BASE + '/requests/' + body['request_id'], params={'expected_sha256': '0' * 64}).status_code == 409
    assert original_inventory(f) == before


def test_case_cas_and_unknown_original_do_not_mutate_source(accepted_case_source):
    f = accepted_case_source; before = original_inventory(f)
    _, _, result = create_case(f); stale = result['case']
    view = good(change(f.author_client, stale, 'submit'))['case']
    assert change(f.author_client, stale, 'revise', value=value()).status_code == 409
    view = good(change(f.author_client, view, 'withdraw'))['case']; assert view['status'] == 'withdrawn'
    unknown = good(f.author_client.get(BASE + '/requests/' + str(uuid4()), params={'expected_sha256': 'a' * 64}))
    assert unknown['found'] is False and unknown['result'] is None
    assert original_inventory(f) == before
