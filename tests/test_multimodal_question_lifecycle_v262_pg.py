"""Real multimodal-question lifecycle; ONLY planner HTTP is synthetic.

The reused fixture produces actual pixel/YAMNet CPU results and independent
ACKs in one instance. Registry, scopes, budgets, CloudCall, raw capture, SDK,
DataRun and source/outcome audits are production paths. The planner transport
double proves neither paid-provider behavior nor natural-language quality.
Root owns execution against a disposable PG database; authors do not run it.
"""
import base64
import copy
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.gateway import invoke
from generic_agents.security import password_hash
from generic_agents.sound_models import EnvironmentSoundRun
from generic_agents.worker import process_once
from generic_agents import multimodal_question_service_v262 as service
from generic_agents import multimodal_question_tool_v262 as tool
from tests.test_multimodal_query_facets_v262_pg import (
    two_sources, flow_scene, native_scene, pytestmark, good, receive,
)
from tests.test_multimodal_query_contracts_v262 import pixel_intent, sound_intent
from tests.test_vertical_slice import publish

ROOT = Path(__file__).resolve().parents[1]
READ_QUESTION = '列出像素成果，并查看声音成果。'


def candidate(*, received=False):
    if received:
        pixel = pixel_intent()
        sound = sound_intent()
        sound['question_quote'] = '合计已接收声音成果的候选窗口数量'
        sound['intent'].update(operation='sum', metric='candidate_window_count')
        sound['intent']['filters']['reception'] = 'received'
        sound['intent']['grounding'].update(operation_quote='合计', metric_quote='候选窗口', filter_quotes=['已接收'])
    else:
        pixel = pixel_intent(operation='read', metric=None, operation_quote='列出', metric_quote=None)
        pixel['question_quote'] = '列出像素成果'
        sound = sound_intent()
        sound['question_quote'] = '查看声音成果'
        sound['intent'].update(operation='read')
        sound['intent']['filters']['reception'] = 'any'
        sound['intent']['grounding'].update(operation_quote='查看', filter_quotes=[])
    return {'status': 'candidate', 'intent': {'schema': 'multimodal-query-intent-v262',
        'subqueries': [pixel, sound], 'unmet_requirements': []},
        'explanation': '合成规划响应，只按原问题连续分句核对登记的机器候选成果。'}


@pytest.fixture()
def question_scene(two_sources, monkeypatch):
    s = two_sources
    with Session(engine()) as db:
        package = db.get(m.Capability, s.current_release['package_id'])
        original_package = (package.version, package.content_hash, digest(package.data))
    model_manifest = json.loads((ROOT / 'examples/qwen.json').read_text())
    model_manifest.update(namespace='public_multimodal_question_v262', name='planner')
    model = publish(s.client, model_manifest)
    registered_tool = publish(s.client, tool.manifest().model_dump(mode='json'))
    f = SimpleNamespace(**vars(s), model=model, registered_tool=registered_tool,
        question_base=s.base + '/multimodal-questions', posts=[], responses=[],
        reply=candidate(), post_error=None, original_package=original_package)
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'public-v262-test-noncredential')
    # Explicit fixture-only synthetic budget, with real reservation/charge rows.
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 4)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 131072)
    import generic_agents.cloud_business_reasoning_v197 as transport

    class HTTP:
        def __init__(self, **options):
            assert options['follow_redirects'] is False and options['trust_env'] is False

        def __enter__(self): return self

        def __exit__(self, *_): return False

        def post(self, url, *, headers, json):
            assert url == transport.ENDPOINTS['qwen']
            f.posts.append(copy.deepcopy(json))
            if f.post_error is not None:
                raise f.post_error
            envelope = {'choices': [{'finish_reason': 'stop', 'message': {
                'content': __import__('json').dumps(f.reply, ensure_ascii=False)}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 100, 'total_tokens': 200}}
            raw = __import__('json').dumps(envelope, ensure_ascii=False).encode()
            f.responses.append(raw)
            return SimpleNamespace(status_code=200, headers={
                'x-request-id': 'public-v262-' + str(len(f.posts))}, content=raw)

    monkeypatch.setattr(transport.httpx, 'Client', HTTP)
    return f


def options(s):
    value = good(s.client.get(s.question_base + '/options', params={
        'task_id': s.combined_task['id'], 'entity_id': s.combined_task['entity_ids'][0]}))
    if value['registry'] is None:
        assert value['registry_hash'] is None
    else:
        assert value['registry_hash'] == value['registry']['registry_hash']
    return value


def ask(s, question=READ_QUESTION, *, request_id=None):
    opts = options(s)
    assert opts['can_ask'], {'reasons': opts.get('reasons'), 'release_id': opts.get('release_id')}
    value = {'schema': 'multimodal-question-preview-input-v262', 'release_id': opts['release_id'],
        'task_id': s.combined_task['id'], 'entity_id': s.combined_task['entity_ids'][0],
        'question': question, 'model_profile_id': s.model['id'],
        'options_hash': opts['options_hash'], 'registry_hash': opts['registry_hash']}
    preview = good(s.client.post(s.question_base + '/preview', json=value))
    assert preview['input'] == value and preview['registry_hash'] == opts['registry_hash']
    body = {**value, 'schema': 'multimodal-question-submit-v262',
        'request_id': request_id or '多模态/原号?262-' + uuid4().hex,
        'preview_hash': preview['preview_hash'], 'confirm_cloud': True}
    return body, good(s.client.post(s.question_base, json=body), 201)


def query_job(s, identity):
    with Session(engine()) as db:
        rows = list(db.scalars(select(m.Job).where(m.Job.workspace_id == s.workspace_id,
            m.Job.kind == 'data_query', m.Job.data['query_id'].as_string() == identity)))
        assert rows
        return max(rows, key=lambda row: (row.created_at, row.id)).id


def finish(s, identity):
    assert process_once(job_id=query_job(s, identity), deliver=False)
    return good(s.client.get(s.question_base + '/' + identity))


def inventory(s, *, workspace_id=None):
    with Session(engine()) as db:
        return {cls.__tablename__: sorted((row.id, row.version, getattr(row, 'status', None),
            digest(row.data), getattr(row, 'charged_tokens', None), getattr(row, 'reserved_tokens', None))
            for row in db.scalars(select(cls).where(cls.workspace_id == (workspace_id or s.workspace_id))))
            for cls in (m.DataRun, m.Job, m.Invocation, m.CloudCall, m.Audit)}


def command(s, body, current, operation, *, request_id=None):
    return {'schema': 'multimodal-question-command-v262', 'request_id': request_id or '原操作/262-' + uuid4().hex,
        'version': current['version'], 'reason': '明确保留原问题与已付费回答，不重新发送模型',
        'operation': operation, 'expected_request_hash': digest(body)}


def lookup(s, body):
    return good(s.client.get(s.question_base + '/requests', params={
        'request_id': body['request_id'], 'expected_body_hash': digest(body)}))


def source_state(s, identity):
    with Session(engine()) as db:
        row = db.get(m.DataRun, identity)
        return row.version, copy.deepcopy(row.data), row.status


def producer_state(s):
    with Session(engine()) as db:
        return {cls.__tablename__: sorted((row.id, row.version, getattr(row, 'status', None), digest(row.data))
            for row in db.scalars(select(cls).where(cls.workspace_id == s.workspace_id)))
            for cls in (m.Task, m.Entity, m.WorkflowPixelRun, EnvironmentSoundRun, m.Observation, m.Event, m.Memory)}


def test_options_and_preview_do_not_register_tools_change_sources_or_call_models(two_sources):
    s = two_sources
    base = s.base + '/multimodal-questions'
    before = inventory(s)
    with Session(engine()) as db:
        fixed = tool.manifest()
        assert db.scalar(select(m.Capability.id).where(m.Capability.workspace_id == s.workspace_id,
            m.Capability.namespace == fixed.namespace, m.Capability.name == fixed.name)) is None
    opts = good(s.client.get(base + '/options'))
    assert opts['can_ask'] is False
    assert inventory(s) == before
    with Session(engine()) as db:
        assert db.scalar(select(m.Capability.id).where(m.Capability.workspace_id == s.workspace_id,
            m.Capability.namespace == fixed.namespace, m.Capability.name == fixed.name)) is None


def test_real_mixed_sources_paid_ledger_raw_capture_and_single_registered_sdk(question_scene):
    s = question_scene
    receive(s, s.pixel_entry); receive(s, s.sound_entry)
    producers = producer_state(s)
    s.reply = candidate(received=True)
    question = '已接收成果的候选像素合计多少？合计已接收声音成果的候选窗口数量'
    body, created = ask(s, question)
    assert UUID(created['id']).version == 5
    completed = finish(s, created['id'])
    assert completed['status'] == completed['persisted_status'] == 'succeeded', {
        'status': completed['status'], 'error': completed.get('error'),
        'integrity_issue': completed.get('integrity_issue'), 'planning': completed['planning']['state']}
    assert completed['answer_usable'] is True and completed['integrity_issue'] is None
    answer = completed['saved_answer']; facets = answer['computation']['facets']
    assert [item['facet'] for item in facets] == ['pixel', 'task_sound']
    assert facets[0]['computation']['unit'] == 'model_result_pixels'
    assert facets[1]['computation']['unit'] == 'summed_candidate_window_entries'
    assert answer['computation']['verification']['source_authority_checked'] is False
    assert len(s.posts) == len(s.responses) == 1
    transmitted = json.dumps(s.posts[0], ensure_ascii=False)
    assert all(identity not in transmitted for identity in (
        created['id'], s.instance_id, s.combined_task['id'], s.sound_input['id'],
        s.combined_task['entity_ids'][0]))
    with Session(engine()) as db:
        row = db.get(m.DataRun, created['id']); state = row.data[service.KEY]
        capture = state['planning']['capture']
        assert base64.b64decode(capture['raw_b64'], validate=True) == s.responses[0]
        assert capture['raw_sha256'] == hashlib.sha256(s.responses[0]).hexdigest()
        ledger = db.get(m.CloudCall, completed['planning']['cloud_call_id'])
        assert ledger.charged_tokens == 200 and ledger.data['receipt']['usage']['total_tokens'] == 200
        identity = ledger.data['gateway_dispatch']['identity']
        assert identity['suggestion_id'] == row.id and identity['workspace_id'] == s.workspace_id
        assert identity['actor_id'] == s.p.user_id and identity['task_id'] != 'pixel-question-v252'
        call = db.get(m.Invocation, completed['tool']['invocation_id'])
        assert call.status == 'succeeded' and call.data['output'] == answer
        assert call.data['inputs'] == {'query_id': row.id, 'plan_hash': completed['plan_hash']}
        assert call.capability_id == s.registered_tool['id']
        assert state['source_snapshot']['facets']['task_sound']['records'][0]['reception']['receipt_hash']
        calls = list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id == s.workspace_id)))
        assert len(calls) == 1
        package = db.get(m.Capability, s.current_release['package_id'])
        assert (package.version, package.content_hash, digest(package.data)) == s.original_package
        assert s.registered_tool['id'] not in package.data['dependencies']
        assert len(package.data['specification']['native_query_catalog']['tools']) == 3
    before = inventory(s); original = source_state(s, created['id'])
    assert lookup(s, body)['query'] == completed
    assert good(s.client.post(s.question_base, json=body), 201) == completed
    assert s.client.post(s.question_base, json={**body, 'question': '同一原号不能换成另一问题'}).status_code == 409
    assert good(s.client.get(s.question_base + '/' + created['id'])) == completed
    assert source_state(s, created['id']) == original and inventory(s) == before and len(s.posts) == 1
    assert producer_state(s) == producers  # No source re-inference, Task creation, ACK, or object write.
    # Even a known successful SDK key must pass the new family's owned-job
    # authorization before the gateway's ordinary idempotent-return branch.
    with Session(engine()) as db:
        sdk = db.get(m.Invocation, completed['tool']['invocation_id'])
        original_sdk = (sdk.status, digest(sdk.data))
        with pytest.raises(HTTPException) as denied:
            invoke(db, s.p, sdk.capability_id, 'data', copy.deepcopy(sdk.data['inputs']), sdk.idempotency_key)
        assert denied.value.status_code in {403, 409}
        db.rollback()
        sdk = db.get(m.Invocation, completed['tool']['invocation_id'])
        assert (sdk.status, digest(sdk.data)) == original_sdk
    assert inventory(s) == before and len(s.posts) == 1


def test_late_ack_does_not_recompute_saved_question_or_its_original_sdk(question_scene):
    s = question_scene; body, created = ask(s)
    before_ack = finish(s, created['id']); old = copy.deepcopy(before_ack['saved_answer'])
    assert before_ack['answer_usable'] and old['computation']['facets'][1]['computation']['references'][0]['values'] is None
    receive(s, s.pixel_entry); receive(s, s.sound_entry)
    before = inventory(s)
    current = good(s.client.get(s.question_base + '/' + created['id']))
    assert current['saved_answer'] == old and current['answer_usable'] is True
    assert lookup(s, body)['query']['saved_answer'] == old
    assert inventory(s) == before and len(s.posts) == 1


def test_unknown_planning_transport_preserves_original_nonce_without_redispatch(question_scene):
    s = question_scene; s.post_error = httpx.ReadTimeout('synthetic timeout after transport entry')
    body, created = ask(s); unknown = finish(s, created['id'])
    assert unknown['status'] == 'unknown_model' and unknown['saved_answer'] is None
    assert unknown['actions']['can_retry'] is False and len(s.posts) == 1
    before = inventory(s)
    assert lookup(s, body)['query']['id'] == created['id']
    assert good(s.client.post(s.question_base, json=body), 201)['id'] == created['id']
    retry = command(s, body, unknown, 'retry')
    assert s.client.post(s.question_base + '/' + created['id'] + '/retry', json=retry).status_code == 409
    assert inventory(s) == before and len(s.posts) == 1


def test_cancel_and_original_command_recovery_are_known_without_model_or_sdk(question_scene):
    s = question_scene; body, created = ask(s); stop = command(s, body, created, 'cancel')
    path = s.question_base + '/' + created['id']
    saved = good(s.client.post(path + '/cancel', json=stop))
    assert saved['query']['status'] == 'cancelled' and not s.posts
    before = inventory(s)
    found = good(s.client.get(path + '/commands/requests', params={
        'request_id': stop['request_id'], 'expected_body_hash': digest(stop)}))
    assert found['found'] and found['result'] == saved
    assert good(s.client.post(path + '/cancel', json=stop)) == saved
    assert s.client.post(path + '/cancel', json={**stop, 'reason': '同原号不能偷偷改变正文'}).status_code == 409
    assert inventory(s) == before and not s.posts
    original_job = query_job(s, created['id'])
    assert process_once(job_id=original_job, deliver=False)
    assert good(s.client.get(path))['status'] == 'cancelled' and not s.posts
    with Session(engine()) as db:
        assert db.get(m.Job, original_job).status == 'cancelled'
        assert db.scalar(select(m.CloudCall.id).where(m.CloudCall.workspace_id == s.workspace_id)) is None
        assert db.scalar(select(m.Invocation.id).where(m.Invocation.workspace_id == s.workspace_id,
            m.Invocation.capability_id == s.registered_tool['id'])) is None


def test_real_source_failure_then_explicit_retry_uses_original_paid_answer(question_scene, monkeypatch):
    s = question_scene; body, created = ask(s)
    from generic_agents import multimodal_query_facets_v262 as sources

    def broken_database_read(db, *args, **kwargs):
        db.execute(text('SELECT multimodal_question_intentional_missing_function_v262()'))
        raise AssertionError('the disposable database must not contain this function')

    with monkeypatch.context() as patch:
        patch.setattr(sources, 'capture', broken_database_read)
        failed = finish(s, created['id'])
    assert failed['status'] in {'failed', 'blocked_evidence', 'blocked_integrity'} and failed['actions']['can_retry'] is True
    assert failed['plan'] is not None and failed['source'] is None and failed['saved_answer'] is None
    assert failed['planning']['response_sha256'] and len(s.posts) == 1
    original_call = failed['planning']['cloud_call_id']; original_raw = failed['planning']['response_sha256']
    assert failed['error'] and 'SELECT' not in failed['error'] and 'missing_function' not in failed['error']
    retry = command(s, body, failed, 'retry')
    good(s.client.post(s.question_base + '/' + created['id'] + '/retry', json=retry))
    completed = finish(s, created['id'])
    assert completed['status'] == 'succeeded' and completed['answer_usable']
    assert completed['planning']['cloud_call_id'] == original_call
    assert completed['planning']['response_sha256'] == original_raw and len(s.posts) == 1
    with Session(engine()) as db:
        assert len(list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id == s.workspace_id)))) == 1


def test_parent_outcome_failure_reuses_real_saved_sdk_source_and_paid_answer(question_scene, monkeypatch):
    s = question_scene; body, created = ask(s)
    actual = service._audit; stopped = []

    def fail_before_outcome(db, p, action, identity, data):
        if action == service.OUTCOME and not stopped:
            stopped.append(True)
            raise ValueError('明确工程故障：真实SDK已成功，父查询终结审计尚未保存')
        return actual(db, p, action, identity, data)

    with monkeypatch.context() as patch:
        patch.setattr(service, '_audit', fail_before_outcome)
        failed = finish(s, created['id'])
    assert stopped and failed['status'] == 'blocked_integrity'
    assert failed['saved_answer'] is None and failed['tool']['status'] == 'succeeded'
    old_job = query_job(s, created['id']); old_sdk = failed['tool']['invocation_id']
    original_source = copy.deepcopy(failed['source'])
    original_planning = copy.deepcopy(failed['planning'])
    retry = command(s, body, failed, 'retry')
    resumed = good(s.client.post(s.question_base + '/' + created['id'] + '/retry', json=retry))
    assert resumed['query']['status'] == 'queued'
    new_job = query_job(s, created['id']); assert new_job != old_job
    completed = finish(s, created['id'])
    assert completed['status'] == 'succeeded' and completed['answer_usable']
    assert completed['tool']['invocation_id'] == old_sdk and completed['source'] == original_source
    assert completed['planning'] == original_planning and len(s.posts) == 1
    with Session(engine()) as db:
        assert db.get(m.Job, old_job).status == 'failed' and db.get(m.Job, new_job).status == 'succeeded'
        assert len(list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == s.workspace_id,
            m.Invocation.capability_id == s.registered_tool['id'])))) == 1
        assert len(list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id == s.workspace_id)))) == 1


def test_bad_received_source_retains_exact_saved_answer_and_never_repairs_or_reexecutes(question_scene):
    s = question_scene; receive(s, s.pixel_entry); receive(s, s.sound_entry)
    body, created = ask(s); completed = finish(s, created['id'])
    saved = copy.deepcopy(completed['saved_answer']); original = source_state(s, created['id'])
    with Session(engine()) as db:
        snapshot = db.get(m.DataRun, created['id']).data[service.KEY]['source_snapshot']
        identity = snapshot['facets']['task_sound']['records'][0]['reception']['memory']['id']
        memory = db.get(m.Memory, identity)
        memory.data = {**memory.data, 'explicit_disposable_corruption': True}; db.commit()
    before = inventory(s)
    blocked = good(s.client.get(s.question_base + '/' + created['id']))
    assert blocked['status'] == 'blocked_integrity' and blocked['persisted_status'] == 'succeeded'
    assert blocked['saved_answer'] == saved and blocked['answer_usable'] is False and blocked['integrity_issue']
    assert blocked['actions']['can_retry'] is False
    assert lookup(s, body)['query']['saved_answer'] == saved
    assert inventory(s) == before and source_state(s, created['id']) == original and len(s.posts) == 1


def test_generic_query_is_reference_only_and_all_generic_mutations_remain_blocked(question_scene):
    s = question_scene; _, created = ask(s); before = inventory(s)
    path = '/api/data/queries/' + created['id']
    public = good(s.client.get(path))
    assert public['reference_only'] and public['family'] == service.KEY
    assert public['context']['detail_path'] == s.question_base + '/' + created['id']
    assert not {'data', 'source_snapshot', 'saved_answer', 'raw_b64'} & set(public)
    assert s.client.put(path + '/draft', json={'expected_version': created['version'],
        'reason': '通用查询不能改写专属范围', 'draft': {'question': '改为普通字段查询'}}).status_code == 409
    assert s.client.post(path + '/cancel', json={'expected_version': created['version'],
        'reason': '不能绕过专属原操作编号'}).status_code == 409
    assert inventory(s) == before and not s.posts


@pytest.mark.parametrize('damage', ['state', 'creation', 'all_ordinary_markers'])
def test_independent_or_intrinsic_family_trace_cannot_downgrade_to_generic(question_scene, damage):
    s = question_scene; body, created = ask(s); finish(s, created['id'])
    with Session(engine()) as db:
        row = db.get(m.DataRun, created['id'])
        if damage == 'state': row.data = {k: v for k, v in row.data.items() if k != service.KEY}
        elif damage == 'creation':
            proof = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
                m.Audit.object_id == row.id, m.Audit.action == service.CREATED))
            assert proof is not None; db.delete(proof)
        else:
            # Only this owned test query's markers/links are removed; its
            # server-derived UUID5 identity must still prevent ordinary use.
            for proof in db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
                    m.Audit.object_id == row.id)):
                db.delete(proof)
            for job in db.scalars(select(m.Job).where(m.Job.workspace_id == s.workspace_id,
                    m.Job.data['query_id'].as_string() == row.id)):
                db.delete(job)
            for call in db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == s.workspace_id,
                    m.Invocation.data['inputs']['query_id'].as_string() == row.id)):
                db.delete(call)
            for ledger in db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id == s.workspace_id,
                    m.CloudCall.data['gateway_dispatch']['identity']['suggestion_id'].as_string() == row.id)):
                db.delete(ledger)
            row.data = {}
        db.commit()
    before = inventory(s)
    assert s.client.get(s.question_base + '/' + created['id']).status_code == 409
    assert s.client.get('/api/data/queries/' + created['id']).status_code == 409
    assert s.client.post('/api/data/queries/' + created['id'] + '/cancel', json={
        'expected_version': source_state(s, created['id'])[0], 'reason': '残余原族标识不得降级'}).status_code == 409
    assert inventory(s) == before and len(s.posts) == 1


@contextmanager
def another_client(s, *, foreign=False):
    username = 'multi262_' + uuid4().hex[:18]; password = uuid4().hex
    with Session(engine()) as db:
        user = m.User(username=username, password_hash=password_hash(password)); db.add(user)
        workspace_id = s.workspace_id
        if foreign:
            workspace = m.Workspace(name='多模态问答私有原号隔离'); db.add(workspace); db.flush()
            workspace_id = workspace.id
        db.flush(); db.add(m.Membership(user_id=user.id, workspace_id=workspace_id, role='admin')); db.commit()
    with TestClient(s.client.app) as client:
        session = good(client.post('/api/login', json={'username': username, 'password': password}))
        client.headers.update({'X-CSRF-Token': session['csrf_token'], 'X-Workspace-ID': workspace_id})
        yield client


@pytest.mark.parametrize('foreign', [False, True])
def test_other_actual_actor_or_workspace_cannot_read_replay_or_claim_original(question_scene, foreign):
    s = question_scene; body, created = ask(s)
    with another_client(s, foreign=foreign) as client:
        # Login legitimately creates its own limiter record. Baseline AFTER
        # login so read-only assertions still catch every query side effect.
        before = inventory(s)
        other_workspace = good(client.get('/api/me'))['workspace_id']
        before_other = inventory(s, workspace_id=other_workspace)
        assert client.get(s.question_base + '/' + created['id']).status_code == 404
        assert client.get(s.question_base + '/requests', params={
            'request_id': body['request_id'], 'expected_body_hash': digest(body)}).status_code == 404
        assert client.post(s.question_base, json=body).status_code in {403, 404, 409}
        public = client.get('/api/data/queries/' + created['id'])
        assert public.status_code == 404
        assert inventory(s) == before and inventory(s, workspace_id=other_workspace) == before_other and not s.posts


def test_direct_sdk_without_original_owned_query_job_never_creates_success(question_scene):
    s = question_scene; _, created = ask(s)
    with Session(engine()) as db:
        with pytest.raises(HTTPException):
            invoke(db, s.p, s.registered_tool['id'], 'data', {'query_id': created['id'],
                'plan_hash': 'a' * 64}, 'unowned-multimodal-tool:' + uuid4().hex)
        db.rollback()
        assert db.scalar(select(m.Invocation.id).where(m.Invocation.workspace_id == s.workspace_id,
            m.Invocation.capability_id == s.registered_tool['id'])) is None
    assert not s.posts


def test_history_and_missing_lookup_are_readonly_after_legitimate_pause(question_scene):
    s = question_scene; body, created = ask(s); completed = finish(s, created['id'])
    instance = next(item for item in good(s.client.get('/api/instances')) if item['id'] == s.instance_id)
    good(s.client.post('/api/instances/' + s.instance_id + '/pause', json={'version': instance['version']}))
    before = inventory(s)
    assert lookup(s, body)['query']['saved_answer'] == completed['saved_answer']
    absent = good(s.client.get(s.question_base + '/requests', params={
        'request_id': '仍未知/262-' + uuid4().hex, 'expected_body_hash': digest(body)}))
    assert absent['found'] is False and absent['state'] == 'unknown' and absent['query'] is None
    assert good(s.client.post(s.question_base, json=body), 201)['id'] == created['id']
    assert options(s)['can_ask'] is False
    listing = good(s.client.get(s.question_base))
    assert any(item['id'] == created['id'] for item in listing['records'])
    assert all('saved_answer' not in item for item in listing['records'])
    assert inventory(s) == before and len(s.posts) == 1


def dictionaries(value):
    if type(value) is dict:
        yield value
        for item in value.values(): yield from dictionaries(item)
    elif type(value) is list:
        for item in value: yield from dictionaries(item)


def strings(value):
    if type(value) is str: yield value
    elif type(value) is dict:
        for item in value.values(): yield from strings(item)
    elif type(value) is list:
        for item in value: yield from strings(item)


def test_actual_generic_reads_mask_private_query_sdk_paid_raw_and_audits_even_with_stripped_markers(question_scene):
    s = question_scene
    private_note = 'PRIVATE_PLAN_RESPONSE_' + uuid4().hex
    s.reply['explanation'] += private_note
    question = READ_QUESTION + '【私人核对编号：' + uuid4().hex + '】'
    _, created = ask(s, question); completed = finish(s, created['id'])
    with Session(engine()) as db:
        row = db.get(m.DataRun, created['id']); state = row.data[service.KEY]
        secrets = (question, private_note, state['raw_b64'], state['planning']['capture']['raw_b64'])
        query_job_id = query_job(s, row.id)
        sdk_id = completed['tool']['invocation_id']; paid_id = completed['planning']['cloud_call_id']
        audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
            m.Audit.object_id == row.id)))
        audit_ids = {entry.id for entry in audits}
        # Serializer-only negative control. This terminal Job claims no Task,
        # query, lease, capability, successful output, or execution authority.
        # Ordinary UUID5 IDs/Task/Entity/namespace values are not family markers.
        control_id = str(uuid5(NAMESPACE_URL, 'public-nonquery-control/' + uuid4().hex))
        control = m.Job(id=control_id, workspace_id=s.workspace_id, status='cancelled',
            kind='fixture_projection_control', idempotency_key='ordinary-control:' + uuid4().hex,
            data={'task_id': str(uuid5(NAMESPACE_URL, 'ordinary-task/' + uuid4().hex)),
                'entity_id': str(uuid5(NAMESPACE_URL, 'ordinary-entity/' + uuid4().hex)),
                'namespace': str(uuid5(NAMESPACE_URL, 'ordinary-namespace/' + uuid4().hex)),
                'public_control': 'ordinary non-query serializer control'})
        db.add(control); db.commit()

    target_ids = {created['id'], query_job_id, sdk_id, paid_id, *audit_ids}

    def assert_private(row):
        assert row['schema'] == 'multimodal-question-private-reference-v262'
        assert row['private_record'] is True and row['reference_only'] is True
        assert not {'data', 'question', 'inputs', 'output', 'raw_b64', 'source_snapshot',
            'saved_answer', 'planning', 'computation'} & set(row)

    def check_public(client, *, owner):
        overview = good(client.get('/api/overview'))
        assert all(not any(secret in value for secret in secrets) for value in strings(overview))
        rows = [row for row in dictionaries(overview) if row.get('id') in target_ids]
        found = {row['id'] for row in rows}
        assert {query_job_id, paid_id} <= found, 'actual overview must contain the safe Job and paid-call inventory'
        for row in rows: assert_private(row)
        controls = [row for row in dictionaries(overview) if row.get('id') == control_id]
        assert controls and all(row.get('private_record') is not True for row in controls)
        assert controls[0]['data']['public_control'] == 'ordinary non-query serializer control'
        sdk = client.get('/api/invocations/' + sdk_id)
        if owner or sdk.status_code == 200:
            # This existing GET has its own five-field status DTO and does
            # not call as_dict. Require its exact safe shape, not a synthetic
            # schema wrapper. Check the real serializer separately below.
            status = good(sdk)
            assert type(status) is dict and set(status) == {'id', 'version', 'status', 'error', 'receipt_checks'}
            assert status['id'] == sdk_id and type(status['version']) is int and status['version'] >= 1
            assert status['status'] == 'succeeded' and status['error'] is None and status['receipt_checks'] == []
        else:
            assert sdk.status_code in {403, 404}
        assert all(not any(secret in value for secret in secrets) for value in strings(sdk.json()))

    from generic_agents.services import as_dict
    with another_client(s) as other:
        before = inventory(s)
        check_public(s.client, owner=True); check_public(other, owner=False)
        with Session(engine()) as db:
            assert_private(as_dict(db.get(m.Invocation, sdk_id)))
            for identity in audit_ids: assert_private(as_dict(db.get(m.Audit, identity)))
        assert inventory(s) == before
        with Session(engine()) as db:
            row = db.get(m.DataRun, created['id']); row.data = {}
            job = db.get(m.Job, query_job_id); data = copy.deepcopy(job.data)
            data.pop('schema', None); data.pop('family', None)
            job.data = data; job.idempotency_key = 'fixture-stripped-query-key:' + uuid4().hex
            sdk = db.get(m.Invocation, sdk_id); data = copy.deepcopy(sdk.data)
            data['capability_snapshot']['execution'].pop('handler', None)
            sdk.data = data; sdk.idempotency_key = 'fixture-stripped-sdk-key:' + uuid4().hex
            paid = db.get(m.CloudCall, paid_id); data = copy.deepcopy(paid.data)
            assert data['query_family'] == service.KEY
            data.pop('query_id', None); data['gateway_dispatch']['identity'].pop('task_id', None)
            # Retain the independently recorded explicit query_family hint.
            # Losing two other hints cannot expose paid raw as a generic row.
            paid.data = data
            for identity in audit_ids: db.get(m.Audit, identity).action = 'fixture_stripped_query_action'
            db.commit()
        before = inventory(s)
        check_public(s.client, owner=True); check_public(other, owner=False)
        with Session(engine()) as db:
            assert_private(as_dict(db.get(m.Invocation, sdk_id)))
            for identity in audit_ids: assert_private(as_dict(db.get(m.Audit, identity)))
        assert inventory(s) == before and len(s.posts) == 1
