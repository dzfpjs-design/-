"""Read DTO orchestration with explicitly scoped authority/DB IO spies.

No PG, approved business, source producer, permission, price or cloud call is
claimed. Actual DTOs, Evidence text/hash, grant Audit projection, fixed packet
identity checks and read-only control flow execute without replacement data.
"""
import copy
import hashlib
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import same_goal_advice_context_v310 as s
from generic_agents.contracts import digest
from generic_agents.models import Evidence, Task
from generic_agents.security import Principal
from tests.test_goal_evaluation_append_command_v297 import predicate
from tests.test_same_goal_advice_contracts_v302 import scene as contract_scene, ref

AT = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
ACTUAL_APPROVED_SOURCES = s._approved_sources


@pytest.fixture
def scene(monkeypatch):
    original = contract_scene(); prep = copy.deepcopy(original.preparation); scope = prep['scope']
    p = Principal(prep['business_actor_id'], scope['workspace_id'], 'operator', 'explicit-current-reader-IO')
    spec = {'purpose': '核对原观测与批准资料，再提出建议', 'max_tool_calls': 12, 'max_steps': 12}
    task = Task(id=scope['task_id'], workspace_id=p.workspace_id, package_id=scope['package_id'],
        owner_id=prep['requested_by'], version=3, title='原任务', status='workflow_active',
        data={'goal_spec': spec, 'goal_hash': digest(spec), 'goal_version': 1,
            'goal_confirmed_by': p.user_id, 'goal_window': {'original': 'fixed-test-window'}})
    plan = s.Plan(id=scope['analysis_plan_id'], workspace_id=p.workspace_id, task_id=task.id,
        version=4, status='reviewed', data={})
    job = s.Job(id=ref('actual-prepare-job')['id'], workspace_id=p.workspace_id,
        kind=s.c.PREPARE_KIND, status='succeeded', lease_until=None)
    model_data = {'name': '原固定模型', 'execution': {'provider': 'deepseek', 'model': 'deepseek-v4-pro'}}
    model = s.Capability(id=prep['model_pin']['id'], workspace_id=p.workspace_id, kind='foundation_model',
        status='published', content_hash=digest(model_data), validated_hash=digest(model_data),
        release_version='1.0.0', data=model_data)
    prep['model_pin'] = s.store._pin(model); prep['record_hash'] = s.c.record_hash(prep)
    refs = [copy.deepcopy(original.sources['data_evidence_ref']),
        copy.deepcopy(original.sources['document']['evidence_ref'])]
    contents = ['原数据观测快照：温度与湿度。😀'.encode(), '原批准业务摘录：发现异常应报告。'.encode()]
    files = []; uploads = []; grants = []
    for index, (reference, raw) in enumerate(zip(refs, contents)):
        reference.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
        file = Evidence(id=reference['evidence_id'], workspace_id=p.workspace_id, version=2,
            sha256=reference['sha256'], size_bytes=len(raw), media_type='text/plain',
            data={'cloud_model_ids': [model.id]})
        files.append(file)
        grants.append(s.Audit(id=ref('grant-' + str(index))['id'], workspace_id=p.workspace_id,
            actor_id=p.user_id, action='evidence.cloud_authorize', object_id=file.id, created_at=AT,
            data={'model_id': model.id, 'sha256': file.sha256, 'version': 2, 'reason': '仅本次原模型分析'}))
    receipt = s.Audit(id=ref('prepared-receipt')['id'], workspace_id=p.workspace_id,
        actor_id=prep['data_actor_id'], action=s.preparations.PREPARED, object_id=plan.id,
        created_at=AT - timedelta(minutes=1), data={})
    policy = s.Audit(id=prep['budget']['quota_policy_ref']['id'], workspace_id=p.workspace_id,
        actor_id=p.user_id, action=s.store.QUOTA_POLICY, object_id=task.id, created_at=AT,
        data={'expires_at': (AT + timedelta(hours=1)).isoformat()})
    evaluated = s.Evaluation(id=prep['mature_head']['evaluation_ref']['id'], workspace_id=p.workspace_id,
        task_id=task.id, record_hash=prep['mature_head']['evaluation_ref']['record_hash'], generation=4,
        goal_snapshot_hash=scope['goal_snapshot_hash'], evidence_id=refs[0]['evidence_id'],
        job_id=ref('evaluation-job')['id'], invocation_id=ref('evaluation-call')['id'],
        data={'cut_at': prep['evaluation_cut'], 'original_query_ref': prep['original_query_ref'],
            'evaluation_query_ref': prep['evaluation_query_ref'], 'evidence_ref': refs[0]})
    data_job = s.Job(id=evaluated.job_id, workspace_id=p.workspace_id, kind='append_goal_evaluation_v297',
        status='succeeded', data={'inputs': {'explicit_original_input_IO': True}})
    cap = s.Capability(id=ref('actual-evaluation-cap')['id'], workspace_id=p.workspace_id)
    sdk = s.Invocation(id=evaluated.invocation_id, workspace_id=p.workspace_id, capability_id=cap.id,
        status='succeeded')
    packet = {'actual_original_packet': True, 'evidence_id': refs[0]['evidence_id']}
    selected = {'binding': {'graph': {'actual_original_graph_IO': True}}}
    source = NS(id=prep['source_business_capability_ref']['id'])
    configured = {'scope': scope, 'source': source, 'advice': NS(id=prep['advice_capability_ref']['id']),
        'model': model, 'goal': s.snapshot(task)}
    prepared = {'content': contents[1], 'evidence_ref': refs[1],
        'receipt_ref': {'id': receipt.id, 'record_hash': digest(receipt.data)}}
    t = NS(p=p, task=task, plan=plan, job=job, prep=prep, model=model, refs=refs, contents=contents,
        files=files, grants=grants, receipt=receipt, policy=policy, evaluated=evaluated, data_job=data_job,
        sdk=sdk, cap=cap, packet=packet, selected=selected, configured=configured, prepared=prepared,
        inventory=[], occupied=[], clock=AT, fresh='current', allowed=True, config_ok=True, price_ok=True,
        source_ok=True, maturity_ok=True, review_ok=True, events=[],
        rows={s.Audit: [receipt, policy, *grants], s.Capability: [model, cap], s.Evaluation: [evaluated],
            s.Job: [job, data_job], s.Invocation: [sdk], s.Plan: [plan]})
    class DB:
        no_autoflush = nullcontext()
        def scalars(self, statement):
            kind = statement.column_descriptions[0]['entity']
            assert p.workspace_id in statement.compile().params.values()
            return [row for row in t.rows[kind] if predicate(statement.whereclause, row)]
        def scalar(self, statement):
            assert 'clock_timestamp' in str(statement)
            return t.clock
        def add(self, row): raise AssertionError('GET must not create rows')
        def flush(self): raise AssertionError('GET must not flush')
        def commit(self): raise AssertionError('GET must not commit')
    t.db = DB()
    def reader(db, principal, **kwargs):
        assert principal.workspace_id == p.workspace_id
        if not t.allowed: raise HTTPException(403, 'actual current reader IO refused')
    def task_read(db, principal, identity):
        assert principal.workspace_id == task.workspace_id
        if identity != task.id: raise HTTPException(404, 'scoped Task IO missing')
        return task
    monkeypatch.setattr(s.store, '_reader', reader)
    monkeypatch.setattr(s.store.existing, '_task', task_read)
    monkeypatch.setattr(s.store.provider, 'binding', lambda *args: selected)
    monkeypatch.setattr(s, '_released', lambda *args: {'source': source, 'model': model,
        'model_pin': s.store._pin(model), 'display': s._model(t.db, t.p, s.store._pin(model))[1],
        'business_actor': prep['business_actor_id'], 'data_actor': prep['data_actor_id']})
    def mature(*args):
        if not t.maturity_ok: raise HTTPException(409, 'actual mature publication IO unavailable')
        return {'head': {'head_hash': prep['mature_head']['head_hash']}, 'row': evaluated, 'record': evaluated.data}
    monkeypatch.setattr(s.store.existing, '_mature_head', mature)
    monkeypatch.setattr(s.store.provider, '_evidence', lambda *args: {s.store.provider.VIEW: {'state': t.fresh}})
    def configuration(*args):
        if not t.config_ok: raise HTTPException(409, 'actual live Goal/role/release IO refused')
        return configured
    monkeypatch.setattr(s.quota, '_configuration', configuration)
    monkeypatch.setattr(s.store, '_policy', lambda *args: copy.deepcopy(prep['budget']))
    monkeypatch.setattr(s.store, '_policy_starts', lambda *args: list(t.occupied))
    monkeypatch.setattr(s.store, '_inventory', lambda *args: list(t.inventory))
    monkeypatch.setattr(s.store, '_saved_base', lambda db, principal, actual, saved: (prep, job))
    monkeypatch.setattr(s.preparations, '_read', lambda *args: copy.deepcopy(prepared))
    monkeypatch.setattr(s.quota, '_published', lambda db, principal, identity, kind: model)
    def current_price(*args, **kwargs):
        t.events.append('current-price-read')
        if not t.price_ok: raise HTTPException(409, 'actual latest price expired')
        return {'basis_ref': ref('actual-official-price'), 'price': {'currency': 'CNY',
            'checked_at': AT.isoformat(), 'expires_at': (AT + timedelta(hours=1)).isoformat()}}
    monkeypatch.setattr(s.prices, 'current', current_price)
    approved = {'selection': copy.deepcopy(prep['request']['document_source']), 'title': '原批准摘录',
        'description': '原件来源', 'input_key': 'runtime_document_sources', 'excerpt_count': 1}
    def approved_sources(*args):
        if not t.source_ok: raise HTTPException(409, 'actual submission not approved')
        return [copy.deepcopy(approved)]
    monkeypatch.setattr(s, '_approved_sources', approved_sources)
    monkeypatch.setattr(s.sources.evidence, 'evidence_bytes', lambda db, principal, identity:
        next((file, raw) for file, raw in zip(files, contents) if file.id == identity))
    def saved_result(db, principal, actual, actual_job, actual_sdk, actual_cap, inputs):
        t.events.append('original-packet-read')
        assert actual is task and actual_job is data_job and actual_sdk is sdk and actual_cap is cap
        assert inputs == data_job.data['inputs']
        return {'evaluation_ref': copy.deepcopy(prep['mature_head']['evaluation_ref']), 'packet': packet}
    monkeypatch.setattr(s.packets, 'saved_result', saved_result)
    def actual_data(db, principal, actual, reference, *, live):
        t.events.append('historical-source-read')
        assert actual is task and reference is packet and live is False
        return None, None, {'packet_id': evaluated.id, 'evaluation_record': copy.deepcopy(evaluated.data)}
    monkeypatch.setattr(s.originals, '_data', actual_data)
    return t


def test_context_has_only_closed_human_facing_fields_and_no_writes(scene):
    t = scene; value = s.context(t.db, t.p, t.task.id)
    assert value['schema'] == s.CONTEXT and value['permissions']['can_prepare'] is True
    assert value['mature_head_hash'] == t.prep['mature_head']['head_hash']
    assert value['document_sources'][0]['selection'] == t.prep['request']['document_source']
    assert value['actor']['is_business_actor'] and value['model']['available']
    assert not {'goal_spec', 'frame', 'aliases', 'capability_snapshot', 'source_record'} & set(value)
    assert s.Context.model_validate(value).model_dump(mode='json') == value


@pytest.mark.parametrize('status', ['cancelled', 'reviewed', 'awaiting_consent'])
def test_prepare_availability_uses_actual_shared_open_plan_gate(scene, status):
    t = scene; t.plan.status = status; t.inventory = [(t.plan, t.prep, t.job)]
    t.fresh = 'stale'  # No Preview/Start work, regardless of local Prepare.
    value = s.context(t.db, t.p, t.task.id)
    assert value['permissions']['can_prepare'] is (status in {'cancelled', 'reviewed'})


def test_stale_data_and_expired_price_do_not_block_local_prepare_or_hide_saved_sources(scene):
    t = scene; t.fresh = 'stale'; t.price_ok = False
    value = s.context(t.db, t.p, t.task.id)
    assert value['freshness'] == 'stale' and value['permissions']['can_prepare']
    assert value['document_sources'] and value['price']['status'] == 'unavailable'
    assert {'data_stale', 'price_unavailable'} <= {item['code'] for item in value['blocked_reasons']}
    assert value['permissions']['can_start'] is False


@pytest.mark.parametrize('flag', ['maturity_ok', 'config_ok', 'source_ok'])
def test_missing_configuration_is_section_blocked_without_fake_replacement_or_preparation(scene, flag):
    t = scene; setattr(t, flag, False)
    value = s.context(t.db, t.p, t.task.id)
    assert value['permissions']['can_prepare'] is False and value['blocked_reasons']
    assert value['title'] == t.task.title and value['goal_hash'] == t.task.data['goal_hash']
    if flag == 'source_ok': assert value['document_sources'] == []


def test_viewer_and_unassigned_operator_do_not_gain_prepare_or_authorize(scene):
    t = scene
    viewer = Principal(ref('other-reader')['id'], t.p.workspace_id, 'viewer', 'reader')
    value = s.context(t.db, viewer, t.task.id)
    assert all(value['permissions'][name] is False for name in s.Permissions.model_fields)
    operator = Principal(viewer.user_id, viewer.workspace_id, 'operator', 'reader')
    assert s.context(t.db, operator, t.task.id)['permissions']['can_prepare'] is False


def test_review_remains_available_after_current_config_and_price_expire(scene, monkeypatch):
    t = scene; t.config_ok = False; t.price_ok = False; t.maturity_ok = False
    t.plan.status = 'ready_for_review'; t.inventory = [(t.plan, t.prep, t.job)]
    from generic_agents import same_goal_advice_runtime_v308 as runtime
    calls = []
    monkeypatch.setattr(runtime, 'cold_result', lambda db, principal, task, plan: calls.append(plan.id) or {'actual_paid_answer_IO': True})
    value = s.context(t.db, t.p, t.task.id)
    assert value['permissions']['can_review'] is True and calls == [t.plan.id]
    assert value['permissions']['can_prepare'] is False


@pytest.mark.parametrize('failure', ['financial', 'forbidden', 'unavailable'])
def test_review_result_financial_block_is_visible_without_hiding_auth_or_io_failure(scene, monkeypatch, failure):
    t = scene; t.plan.status = 'ready_for_review'; t.inventory = [(t.plan, t.prep, t.job)]
    from generic_agents import same_goal_advice_runtime_v308 as runtime
    def blocked(*args):
        if failure == 'financial': raise s.ModelBlocked('original HOLD/INTENT differs')
        raise HTTPException(403 if failure == 'forbidden' else 503, 'explicit read boundary refused')
    monkeypatch.setattr(runtime, 'cold_result', blocked)
    if failure != 'financial':
        with pytest.raises(HTTPException) as caught: s.context(t.db, t.p, t.task.id)
        assert caught.value.status_code == (403 if failure == 'forbidden' else 503)
        return
    value = s.context(t.db, t.p, t.task.id)
    assert value['permissions']['can_review'] is False
    assert any(row['code'] == 'review_result_unavailable' and row['section'] == 'review'
        for row in value['blocked_reasons'])
    assert value['task_id'] == t.task.id and value['document_sources']


def test_documents_cold_read_uses_original_packet_and_both_actual_full_texts(scene, monkeypatch):
    t = scene
    def no_current(*args, **kwargs): raise AssertionError('historical texts cannot require current head/price')
    monkeypatch.setattr(s.prices, 'current', no_current)
    monkeypatch.setattr(s.store.provider, 'require_current', no_current)
    value = s.documents(t.db, t.p, t.task.id, t.plan.id)
    assert value['schema'] == s.DOCUMENTS and value['model_pin'] == t.prep['model_pin']
    assert [item['text'] for item in value['documents']] == [raw.decode() for raw in t.contents]
    assert [item['source_scope'] for item in value['documents']] == ['evaluation_text', 'approved_excerpt']
    assert value['documents'][0]['as_of'] == t.prep['evaluation_cut']
    assert value['documents'][1]['as_of'] == t.receipt.created_at.isoformat()
    assert t.events == ['original-packet-read', 'historical-source-read']
    grant = value['documents'][0]['consent']['grants'][0]
    assert grant == {'audit_id': t.grants[0].id, 'audit_hash': s.consent._grant_hash(t.grants[0]),
        'actor_id': t.p.user_id, 'action': 'evidence.cloud_authorize', 'model_profile_id': t.model.id,
        'evidence_sha256': t.files[0].sha256, 'version_after': 2, 'reason': '仅本次原模型分析'}


def test_later_valid_grant_and_version_increment_preserve_original_text_and_prior_receipt(scene):
    t = scene; before = s.documents(t.db, t.p, t.task.id, t.plan.id)
    file = t.files[0]; file.version = 3
    t.rows[s.Audit].append(s.Audit(id=ref('later-real-grant')['id'], workspace_id=t.p.workspace_id,
        actor_id=ref('other-admin')['id'], action='evidence.cloud_authorize', object_id=file.id,
        data={'model_id': t.model.id, 'sha256': file.sha256, 'version': 3, 'reason': '另一次明确授权'}))
    after = s.documents(t.db, t.p, t.task.id, t.plan.id)
    assert after['documents'][0]['text'] == before['documents'][0]['text']
    assert after['documents'][0]['version'] == 3 and len(after['documents'][0]['consent']['grants']) == 2
    assert before['documents'][0]['consent']['grants'][0] in after['documents'][0]['consent']['grants']


@pytest.mark.parametrize('raw', [b'\xef\xbb\xbf' + '原文😀\r\n第二行\n'.encode('utf-8'),
    '原文é😀\r\n第二行\n末尾保留\r\n'.encode('utf-8')])
def test_real_text_blob_boundary_preserves_bom_unicode_and_line_endings_for_original_hash(scene, raw):
    t = scene; file = t.files[0]; t.contents[0] = raw
    file.sha256 = hashlib.sha256(raw).hexdigest(); file.size_bytes = len(raw)
    file.data = {'cloud_model_ids': []}  # No consent is needed for local text custody.
    reference = {'evidence_id': file.id, 'sha256': file.sha256,
        'size_bytes': len(raw), 'media_type': 'text/plain'}
    # Only Blob read/Task scope is the fixture's declared IO. The production
    # sources._text byte/hash validator and DTO text projection run unchanged.
    value = s._text(t.db, t.p, reference, role='data', title='实际原文字节',
        scope='evaluation_text', as_of=t.prep['evaluation_cut'], model=t.model, available=True)
    encoded = value['text'].encode('utf-8')
    assert encoded == raw and len(encoded) == value['evidence_ref']['size_bytes']
    assert hashlib.sha256(encoded).hexdigest() == value['evidence_ref']['sha256']
    assert value['consent'] == {'status': 'not_authorized', 'grants': []}


@pytest.mark.parametrize('state', ['retired', 'changed', 'missing'])
def test_unavailable_or_changed_model_does_not_hide_original_text_or_invent_historical_name(scene, state):
    t = scene
    if state == 'retired': t.model.status = 'retired'
    elif state == 'changed': t.model.content_hash = '9' * 64
    else: t.rows[s.Capability].remove(t.model)
    value = s.documents(t.db, t.p, t.task.id, t.plan.id)
    assert len(value['documents']) == 2 and value['restrictions']
    assert all(item['consent']['status'] == 'model_unavailable' for item in value['documents'])
    if state != 'retired': assert value['model'] is None


def test_consent_label_without_independent_audit_is_not_authorization(scene):
    t = scene; t.rows[s.Audit] = [t.receipt, t.policy]
    value = s.documents(t.db, t.p, t.task.id, t.plan.id)
    assert all(item['consent'] == {'status': 'not_authorized', 'grants': []} for item in value['documents'])


@pytest.mark.parametrize('fault', ['foreign_plan', 'wrong_evaluation_hash', 'wrong_sdk_packet', 'changed_bytes', 'future_grant_version'])
def test_original_mismatch_fails_closed_without_new_sources_or_writes(scene, monkeypatch, fault):
    t = scene
    if fault == 'foreign_plan': t.plan.task_id = ref('other-task')['id']
    elif fault == 'wrong_evaluation_hash': t.evaluated.record_hash = '9' * 64
    elif fault == 'wrong_sdk_packet': t.packet['evidence_id'] = t.refs[1]['evidence_id']
    elif fault == 'changed_bytes': t.contents[0] += b' altered'
    else:
        t.grants[0].data = {**t.grants[0].data, 'version': 3}
        # The actual grant reader excludes this invalid future receipt.
        # Local text custody remains available without granting a send.
        value = s.documents(t.db, t.p, t.task.id, t.plan.id)
        assert value['documents'][0]['consent'] == {'status': 'not_authorized', 'grants': []}
        assert value['documents'][1]['consent']['status'] == 'authorized'
        assert [item['text'] for item in value['documents']] == [raw.decode() for raw in t.contents]
        assert all(grant['audit_id'] != t.grants[0].id for item in value['documents'] for grant in item['consent']['grants'])
        return
    with pytest.raises(HTTPException) as error: s.documents(t.db, t.p, t.task.id, t.plan.id)
    assert error.value.status_code == 409


@pytest.mark.parametrize('reader', ['context', 'documents'])
def test_current_reader_denial_is_not_converted_to_an_empty_success(scene, reader):
    t = scene; t.allowed = False
    with pytest.raises(HTTPException) as error:
        if reader == 'context': s.context(t.db, t.p, t.task.id)
        else: s.documents(t.db, t.p, t.task.id, t.plan.id)
    assert error.value.status_code == 403 and t.events == []


def test_dto_schema_and_private_extra_fields_cannot_be_client_supplied(scene):
    t = scene; value = s.context(t.db, t.p, t.task.id)
    with pytest.raises(ValidationError): s.Context.model_validate({**value, 'schema': 'other'})
    with pytest.raises(ValidationError): s.Context.model_validate({**value, 'frame': {'private': 'not-a-public-field'}})


def test_approved_source_requires_actual_approval_guard_and_uses_submission_not_latest_source(scene, monkeypatch):
    t = scene; calls = []; selection = t.prep['request']['document_source']; reference = selection['source_set_ref']
    approval = s.task_documents.approval
    monkeypatch.setattr(approval, 'managed', lambda *args: NS(id=ref('real-managed')['id']))
    def guard(db, p, task, *, record_actor, lock_sources):
        assert task is t.task and record_actor is lock_sources is False
        calls.append('actual-approval-IO')
    monkeypatch.setattr(approval, 'guard', guard)
    monkeypatch.setattr(approval, 'current_submission', lambda *args: (None,
        {'value': {'graph_inputs': {'original_port': reference}}}, None))
    monkeypatch.setattr(s.task_documents, 'ports', lambda graph: [{'key': 'original_port', 'label': '原批准报告'}])
    def resolve(db, p, task, selected):
        assert calls == ['actual-approval-IO'] and selected == selection
        return {'approved_selection': {'input_key': 'original_port'}, 'entries': [{'text': 'actual approved excerpt'}]}
    monkeypatch.setattr(s.sources, 'resolve_documents', resolve)
    value = ACTUAL_APPROVED_SOURCES(t.db, t.p, t.task, t.selected)
    assert value == [{'selection': selection, 'title': '原批准报告', 'description': '原任务逐级批准的资料摘录',
        'input_key': 'original_port', 'excerpt_count': 1}]
    def denied(*args, **kwargs): raise HTTPException(409, 'submission exists but not actually approved')
    monkeypatch.setattr(approval, 'guard', denied)
    with pytest.raises(HTTPException): ACTUAL_APPROVED_SOURCES(t.db, t.p, t.task, t.selected)


def test_wrong_or_foreign_task_is_not_an_empty_document_collection(scene):
    t = scene
    with pytest.raises(HTTPException) as error:
        s.documents(t.db, t.p, ref('foreign-task')['id'], t.plan.id)
    assert error.value.status_code == 404 and t.events == []


@pytest.mark.parametrize('generation', [1, 2])
def test_local_preparation_discloses_first_evaluation_preview_limit(scene, monkeypatch, generation):
    t = scene
    t.inventory = []
    original = s.store.existing._mature_head

    def mature(*args, **kwargs):
        value = original(*args, **kwargs)
        value['row'].generation = generation
        return value

    monkeypatch.setattr(s.store.existing, '_mature_head', mature)
    value = s.context(t.db, t.p, t.task.id)
    reasons = {item['code']: item for item in value['blocked_reasons']}
    assert ('initial_evaluation_preview_limited' in reasons) is (generation == 1)
    assert value['permissions']['can_prepare'] is True
    assert value['permissions']['can_preview'] is False
    assert value['permissions']['can_start'] is False
    if generation == 1:
        message = reasons['initial_evaluation_preview_limited']['message']
        assert '本地整理' in message and '不能' in message and '重新整理' in message


def test_first_evaluation_notice_does_not_guess_without_mature_head(scene):
    t = scene
    t.maturity_ok = False
    value = s.context(t.db, t.p, t.task.id)
    assert 'initial_evaluation_preview_limited' not in {row['code'] for row in value['blocked_reasons']}


def test_existing_first_evaluation_plan_has_only_one_generation_explanation(scene, monkeypatch):
    t = scene
    t.plan.status = 'awaiting_consent'
    t.inventory = [(t.plan, t.prep, t.job)]
    original = s.store.existing._mature_head

    def mature(*args, **kwargs):
        value = original(*args, **kwargs)
        value['row'].generation = 1
        return value

    def frame(*args):
        raise s.EvaluationGenerationRequired()

    monkeypatch.setattr(s.store.existing, '_mature_head', mature)
    monkeypatch.setattr(s.previews.frames, 'build_current', frame)
    value = s.context(t.db, t.p, t.task.id)
    codes = [row['code'] for row in value['blocked_reasons']]
    assert codes.count('evaluation_generation_required') == 1
    assert 'initial_evaluation_preview_limited' not in codes
    assert value['permissions']['can_preview'] is False
    assert value['permissions']['can_start'] is False


@pytest.mark.parametrize('denied', [None, 'frame', 'generation', 'price', 'preview'])
def test_preview_and_start_hints_require_original_plan_and_current_readonly_source_price_checks(scene, monkeypatch, denied):
    t = scene; t.plan.status = 'awaiting_consent'; t.inventory = [(t.plan, t.prep, t.job)]
    t.price_ok = denied != 'price'; reads = []
    def frame(*args):
        reads.append('current-original-frame-IO')
        if denied == 'frame': raise HTTPException(409, 'original Plan references previous head')
        if denied == 'generation': raise s.EvaluationGenerationRequired()
        return {'actual_original_frame_IO': True}
    monkeypatch.setattr(s.previews.frames, 'build_current', frame)
    event = NS(id=ref('protected-preview')['id'])
    monkeypatch.setattr(s.previews, '_chain', lambda *args: [(event, {'actual_original_preview_IO': True})])
    from generic_agents import same_goal_advice_execution_v308 as execution
    def live(db, principal, task, plan, prep, actual, now):
        assert actual is event and plan is t.plan and now == AT
        reads.append('live-exact-preview-IO')
        if denied == 'preview': raise HTTPException(409, 'exact protected Preview not currently eligible')
        return {'actual_original_frame_IO': True}
    monkeypatch.setattr(execution, '_live_preview', live)
    value = s.context(t.db, t.p, t.task.id)
    assert value['permissions']['can_preview'] is (denied not in {'frame', 'generation', 'price'})
    assert value['permissions']['can_start'] is (denied is None)
    assert value['permissions']['can_prepare'] is False
    reasons = {item['code']: item for item in value['blocked_reasons']}
    assert ('evaluation_generation_required' in reasons) is (denied == 'generation')
    assert ('preview_sources_unavailable' in reasons) is (denied == 'frame')
    if denied == 'generation':
        assert reasons['evaluation_generation_required']['message'] == s.EvaluationGenerationRequired().detail


@pytest.mark.parametrize('calls,status', [([], 'allocated'), ([NS(status='unknown')], 'used'), ([NS(status='succeeded')], 'used')])
def test_original_single_call_policy_allocation_survives_review_and_never_advertises_a_second_call(scene, calls, status):
    t = scene; t.plan.status = 'reviewed'; t.inventory = [(t.plan, t.prep, t.job)]
    t.occupied = [(t.plan, {'calls': calls})]
    value = s.context(t.db, t.p, t.task.id)
    assert value['quota']['status'] == status and value['quota']['reference'] == t.prep['budget']['quota_policy_ref']
    assert value['quota']['expires_at'] == t.policy.data['expires_at']
    assert value['permissions']['can_prepare'] is False
    assert 'quota_' + status in {item['code'] for item in value['blocked_reasons']}
