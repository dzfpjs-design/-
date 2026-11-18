"""Owned-PG private full reading: real source chain and paid-stage protocol.

Only new synthetic data is used. No provider HTTP or old paid record is read.
The shared two-page fixture uses explicit fake local conversion/OCR IO; this
file publishes a real capability and uses reserve/on_reserved/on_response plus
production settlement. Budgets are opened only inside each test fixture.
Root alone executes; the author has not run this file.
"""
import copy
import base64
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents import cloud_budget
from generic_agents import report_full_reading_pipeline as pipeline
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.leases import LeaseLost
from generic_agents.models import Audit, CloudCall, Job, SetupMaterial, SetupReportDraft
from generic_agents.security import Principal
from generic_agents.worker import process_once
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_setup import config
from tests.test_vertical_slice import publish


BASE = '/api/setup/report-full-readings'


def reply(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def example_candidate():
    """Literal synthetic scene evidence, not a real-report/F11 quality gold."""
    cite = {'source_ref': 'o:p1', 'quote': '先响应🙂；核验并行。'}
    evidence = {'status': 'explicit', 'citations': [copy.deepcopy(cite)], 'explanation': ''}
    def step(key, name, role):
        return {'key': key, 'name': name, 'purpose': name, 'agent': '人工确认', 'role': role,
            'output': name + '记录', 'next_key': 'end', 'branch_condition': '', 'else_key': 'end',
            'citations': [copy.deepcopy(cite)], 'inference_reason': ''}
    return {'schema': 'report-business-candidate-v3', 'domain': '合成完整来源核对',
        'objects': [], 'relationships': [],
        'businesses': [{'operation': 'upsert', 'original_name': '', 'name': '合成响应与核验',
            'trigger': '人工登记合成记录', 'output': '响应和核验待核记录',
            'steps': [step('respond', '先响应', '合成响应职责'), step('verify', '并行核验', '合成核验职责')],
            'citations': [copy.deepcopy(cite)], 'inference_reason': '',
            'participants': [{'key': 'responder', 'agent': '合成响应方', 'responsibility': '先响应', 'evidence': copy.deepcopy(evidence)},
                             {'key': 'verifier', 'agent': '合成核验方', 'responsibility': '核验', 'evidence': copy.deepcopy(evidence)}],
            'interactions': [], 'conditions': [],
            'coordination': [{'key': 'parallel_check', 'kind': 'parallel', 'step_keys': ['respond', 'verify'],
                'start_rule': '先响应；核验并行', 'wait_rule': '', 'join_rule': '', 'evidence': copy.deepcopy(evidence)}]}],
        'questions': ['合成协议用例不证明真实模型质量或实际账号授权。']}


@pytest.fixture
def reading_scene(full_source_scene, monkeypatch):
    scene = full_source_scene; fence()
    client = scene['client']; me = reply(client.get('/api/me'))
    client.headers['X-Workspace-ID'] = me['workspace_id']
    material = reply(client.get('/api/setup/materials/' + scene['copy']['id']))
    before = reply(client.get('/api/setup/draft'))
    reply(client.put('/api/setup/draft', json={'revision': before['revision'],
        'configuration': config([scene['original'], material])}))
    capability = json.loads((Path(__file__).parents[1] / 'examples/qwen.json').read_text())
    capability.update(name='synthetic_full_reading_' + uuid4().hex[:10],
                      description='Synthetic callback only; no HTTP or model-quality claim')
    cap = publish(client, capability)
    # Environment/driver budgets stay zero. These temporary settings apply only
    # to real *synthetic* reserve/settle calls in this isolated empty database.
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 3)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 4000000)
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
    options = reply(client.get('/api/setup/report-drafts/full-source-options/' + scene['original']['id'],
        params={'version': scene['original']['version'], 'hash': scene['original']['hash']}))
    draft = reply(client.get('/api/setup/draft'))
    body = {'request_id': 'fr_' + str(uuid4()), 'revision': draft['revision'], 'model_id': cap['id'],
        'material_refs': [{key: scene['original'][key] for key in ('id', 'version', 'hash')}],
        'selection': options['copies'][0]['proposal'], 'note': '仅合成测试；不代表模型质量。'}
    return {**scene, 'options': options, 'body': body, 'cap': cap,
            'p': Principal(me['user_id'], me['workspace_id'], me['role'], '')}


class SyntheticProvider:
    """Actual new CloudCalls and callback checkpoints; zero provider network."""
    def __init__(self, scene, monkeypatch, *, mode='success'):
        self.scene = scene; self.mode = mode; self.calls = []; self.inputs = []
        monkeypatch.setattr(pipeline, 'cloud_json', self)

    def __call__(self, cap, messages, schema, principal, **callbacks):
        assert callbacks['full_report'] is True
        payload = json.loads(messages[-1]['content'])
        merge = payload['schema'] == 'report-full-reading-merge-evidence-v1'
        spec = pipeline.generation(cap)
        prepared, summary = pipeline.prepared_stage(cap, spec, payload, merge=merge)
        assert principal.user_id == self.scene['p'].user_id
        call_id = cloud_budget.reserve(principal, cap, summary['token_bound'], digest(prepared))
        with Session(engine()) as db:
            call = db.get(CloudCall, call_id)
            # Exactly the real gateway's authorization additions, including its
            # single_batch policy wrapper frozen by generation(). No validation
            # or settlement callback is mocked.
            call.data = {**call.data, 'dispatch_options_hash': digest(spec['dispatch_options']),
                         'input_policy_hash': digest({'single_batch': True, **spec['input_policy']}),
                         'synthetic_test_only': True}
            db.commit()
        self.calls.append(call_id); self.inputs.append(copy.deepcopy(payload))
        callbacks['on_reserved'](call_id)
        if self.mode == 'unknown':
            raise TimeoutError('synthetic reserved response is deliberately unavailable')
        value = example_candidate()
        if not merge and self.mode != 'cross_batch' and not any(block[0] == 'o:p1' for block in payload['blocks']):
            # The Word-only extraction must not cite an OCR page it was never
            # given. Keep the generic synthetic uncertainty; the OCR batch has
            # the literal evidence for its candidate. This is not a quality gold.
            value['businesses'] = []
        if merge:
            results = {stage['batch_id']: stage['result'] for stage in payload['extraction_results']}
            reconciliation, seen = [], set()
            for item in payload['input_items']:
                batch, kind, index = item.split('/'); original = results[batch][kind][int(index)]
                positions = [index for index, target in enumerate(value[kind]) if target == original]
                assert len(positions) == 1, 'synthetic merge must map actual extracted item, not invent a destination'
                destination = (kind, positions[0])
                reconciliation.append({'input_id': item, 'disposition': 'duplicate' if destination in seen else 'kept',
                    'output_kind': kind, 'output_index': positions[0], 'reason': '合成相同抽取条目明确归并，不丢弃输入。'})
                seen.add(destination)
            value = {'result': value, 'reconciliation': reconciliation}
            if self.mode == 'bad_merge': value['reconciliation'] = value['reconciliation'][1:]
        receipt = {'call_id': call_id, 'provider': cap.data['execution']['provider'], 'model': cap.data['execution']['model'],
                   'usage': {'total_tokens': 20}, 'request_id': 'synthetic-only-' + str(uuid4()),
                   'completion_guard': 'full-report-completion-v1', 'response_choice_count': 1,
                   'finish_reason': 'stop', 'response_body_status': 'complete'}
        if self.mode == 'truncated': receipt['finish_reason'] = 'length'
        if self.mode == 'missing_finish': receipt.pop('finish_reason')
        if self.mode == 'multiple_choices': receipt['response_choice_count'] = 2
        unavailable_mode=self.mode.removesuffix('_after_raw')
        if unavailable_mode in ('oversize','unsupported_encoding'):
            receipt.update(response_body_status=unavailable_mode,usage={},finish_reason=None,response_choice_count=None)
            callbacks['on_response'](None,receipt)
            if self.mode.endswith('_after_raw'):raise LeaseLost('synthetic interruption after unavailable response checkpoint')
            return copy.deepcopy(value),receipt
        if self.mode == 'legacy_missing':
            for key in ('completion_guard','finish_reason','response_choice_count','response_body_status'): receipt.pop(key)
        content=json.dumps(value,ensure_ascii=False)
        first={'message':{'content':content}}
        if 'finish_reason' in receipt:first['finish_reason']=receipt['finish_reason']
        choices=[first]+([{'finish_reason':'stop','message':{'content':'{}'}}] if self.mode=='multiple_choices' else [])
        wire=json.dumps({'usage':receipt['usage'],'choices':choices},ensure_ascii=False).encode()
        if self.mode != 'legacy_missing': receipt['_full_report_wire_base64']=base64.b64encode(wire).decode()
        callbacks['on_response'](content, receipt)
        if self.mode == 'after_raw': raise LeaseLost('synthetic lease interruption after durable response checkpoint')
        return copy.deepcopy(value), receipt


def start(scene):
    preview = reply(scene['client'].post(BASE + '/preview', json=scene['body']))
    assert preview['can_start'] is True and preview['can_apply'] is preview['can_publish'] is False
    command = {**scene['body'], 'preview_hash': preview['preview_hash'], 'consent': True}
    return preview, command, reply(scene['client'].post(BASE, json=command), 202)


def get(scene, identity):
    return reply(scene['client'].get(BASE + '/' + identity))


def finish_extract(scene, accepted):
    assert process_once(accepted['draft']['job_id'])
    view = get(scene, accepted['draft']['id'])
    assert view['status'] == view['phase'] == 'awaiting_merge', view.get('error')
    assert all(stage['raw_response_saved'] and stage['status'] == 'succeeded' for stage in view['extractions'])
    assert view['merge']['status'] == 'not_started' and view['model_result'] is None
    return view


def merge(scene, extracted):
    identity = extracted['id']; body = {'revision': extracted['revision'], 'request_id': 'fm_' + str(uuid4())}
    preview = reply(scene['client'].post(BASE + '/' + identity + '/merge-preview', json=body))
    command = {**body, 'preview_hash': preview['preview_hash'], 'consent': True}
    accepted = reply(scene['client'].post(BASE + '/' + identity + '/merge', json=command), 202)
    assert process_once(accepted['draft']['job_id'])
    return preview, command, accepted, get(scene, identity)


def counts():
    with Session(engine()) as db:
        return {model.__tablename__: db.scalar(select(func.count()).select_from(model))
                for model in (CloudCall, SetupReportDraft, Job, Audit)}


def test_actual_options_extract_merge_review_receipt_cas_nonce_and_readback(reading_scene, monkeypatch):
    scene = reading_scene; provider = SyntheticProvider(scene, monkeypatch)
    preview, command, accepted = start(scene)
    assert scene['options']['actor_id'] == accepted['draft']['actor_id']
    assert all(row['id'] != accepted['draft']['id'] for row in reply(scene['client'].get('/api/setup/report-drafts')))
    assert any(row['id'] == accepted['draft']['id'] for row in reply(scene['client'].get(BASE))['items'])
    assert accepted['receipt']['source_hash'] == digest(preview['source_snapshot'])
    projection = pipeline.legacy.old().scrub_tree(pipeline.project_full_source(preview['source_snapshot'])[0])
    assert 'projection' not in preview
    original_refs = [row[0] for row in projection['blocks']]
    assert [ref for batch in preview['batches'] for ref in batch['source_refs']] == original_refs
    assert [row for batch in preview['batches'] for row in batch['payload']['blocks']] == projection['blocks']
    extracted = finish_extract(scene, accepted)
    assert len(provider.calls) == len(preview['batches'])
    merge_preview, merge_body, merged_start, generated = merge(scene, extracted)
    assert generated['status'] == 'review' and generated['model_result'] == example_candidate(), generated.get('error')
    expected_inputs = [stage['batch_id'] + '/' + kind + '/' + str(index)
        for stage in merge_preview['payload']['extraction_results']
        for kind in ('businesses', 'objects', 'relationships', 'questions')
        for index in range(len(stage['result'][kind]))]
    assert merge_preview['payload']['input_items'] == expected_inputs
    assert [entry['input_id'] for entry in generated['merge']['reconciliation']] == expected_inputs
    assert len(provider.calls) == len(preview['batches']) + 1
    saved_body = {'revision': generated['revision'], 'request_id': str(uuid4()),
        'value': {'result': copy.deepcopy(generated['model_result']), 'note': '人工核对保存末字🙂'}}
    saved = reply(scene['client'].put(BASE + '/' + generated['id'] + '/review', json=saved_body))
    assert saved['current_matches'] is True and saved['draft']['review_value']['note'].endswith('🙂')
    assert get(scene, generated['id']) == saved['draft']
    assert reply(scene['client'].get(BASE + '/' + generated['id'] + '/review-receipts/' + saved_body['request_id'])) == saved
    replay_before = counts()
    assert reply(scene['client'].post(BASE, json=command), 202)['receipt'] == accepted['receipt']
    assert reply(scene['client'].post(BASE + '/' + generated['id'] + '/merge', json=merge_body), 202)['receipt'] == merged_start['receipt']
    assert reply(scene['client'].put(BASE + '/' + generated['id'] + '/review', json=saved_body)) == saved
    assert counts() == replay_before
    stale = {**saved_body, 'request_id': str(uuid4())}
    assert scene['client'].put(BASE + '/' + generated['id'] + '/review', json=stale).status_code == 409
    changed = copy.deepcopy(saved_body); changed['value']['note'] += 'different'
    assert scene['client'].put(BASE + '/' + generated['id'] + '/review', json=changed).status_code == 409
    later = {**saved_body, 'revision': saved['draft']['revision'], 'request_id': str(uuid4()),
             'value': {**saved_body['value'], 'note': '后来人稿保留终字。'}}
    latest = reply(scene['client'].put(BASE + '/' + generated['id'] + '/review', json=later))
    old_receipt = reply(scene['client'].get(BASE + '/' + generated['id'] + '/review-receipts/' + saved_body['request_id']))
    assert old_receipt['receipt'] == saved['receipt'] and old_receipt['current_matches'] is False
    assert old_receipt['draft']['review_value'] == latest['draft']['review_value']
    assert old_receipt['draft']['model_result'] == example_candidate()
    with Session(engine()) as db:
        calls = list(db.scalars(select(CloudCall)))
        assert len(calls) == len(provider.calls) and all(call.status == 'succeeded' and call.charged_tokens == 20 for call in calls)
    assert len(provider.calls) == len(preview['batches']) + 1
    # Actual HTTP serializers, synthetic new data only. Root may copy this
    # credential-free bridge into the dedicated frontend contract fixture.
    bridge = {'verification_scope': 'synthetic-engineering-not-model-quality',
        'options': scene['options'], 'preview': preview, 'start': accepted,
        'extracted': extracted, 'merge_preview': merge_preview, 'merge_start': merged_start,
        'generated': generated, 'review': saved, 'latest_review': latest, 'prior_review_readback': old_receipt,
        'list': reply(scene['client'].get(BASE)),
        'status': reply(scene['client'].get(BASE + '/' + generated['id'] + '/status'))}
    (Path.cwd() / 'full-reading-api-bridge.json').write_text(json.dumps(bridge, ensure_ascii=False))


def test_zero_budget_preview_and_rejected_start_are_read_only(reading_scene, monkeypatch):
    scene = reading_scene; monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 0)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 0)
    def forbidden(*args, **kwargs): raise AssertionError('zero budget must not invoke a provider')
    monkeypatch.setattr(pipeline, 'cloud_json', forbidden)
    before = counts(); value = reply(scene['client'].post(BASE + '/preview', json=scene['body']))
    assert value['can_start'] is False and value['start_blocker'] and counts() == before
    response = scene['client'].post(BASE, json={**scene['body'], 'preview_hash': value['preview_hash'], 'consent': True})
    assert response.status_code == 409 and counts() == before


def test_source_changed_after_authorization_is_blocked_before_reservation(reading_scene, monkeypatch):
    scene = reading_scene; provider = SyntheticProvider(scene, monkeypatch)
    preview, command, accepted = start(scene)
    with Session(engine()) as db:
        row = db.get(SetupMaterial, scene['original']['id']); row.archived = True; db.commit()
    assert process_once(accepted['draft']['job_id'])
    stopped = get(scene, accepted['draft']['id'])
    assert stopped['status'] == 'failed' and stopped['error_code'] == 'full_source_changed'
    assert not any(stage['raw_response_saved'] for stage in stopped['extractions'])
    assert not provider.calls and counts()[CloudCall.__tablename__] == 0
    assert reply(scene['client'].post(BASE, json=command), 202)['receipt'] == accepted['receipt']
    assert get(scene, stopped['id'])['preview']['source_snapshot'] == preview['source_snapshot']


def test_reserved_unknown_is_get_first_and_never_automatically_repaid(reading_scene, monkeypatch):
    scene = reading_scene; provider = SyntheticProvider(scene, monkeypatch, mode='unknown')
    _, command, accepted = start(scene)
    assert process_once(accepted['draft']['job_id'])
    identity = accepted['draft']['id']; unknown = get(scene, identity)
    assert unknown['status'] == 'response_unknown' and unknown['error_code'] == 'full_response_unknown'
    assert not unknown['extractions'][0]['raw_response_saved'] and len(provider.calls) == 1
    before = counts()
    restored = reply(scene['client'].get(BASE + '/by-request/' + scene['body']['request_id']))
    assert restored['receipt'] == accepted['receipt']
    assert reply(scene['client'].post(BASE, json=command), 202)['draft']['id'] == identity
    with Session(engine()) as db:
        assert pipeline.run(db, scene['p'], identity) == 'response_unknown'
        db.rollback()
    fresh = {**command, 'request_id': 'fr_' + str(uuid4())}
    assert scene['client'].post(BASE, json=fresh).status_code == 409
    assert counts() == before and len(provider.calls) == 1


def test_paid_raw_checkpoint_is_consumed_on_new_session_without_another_provider_call(reading_scene, monkeypatch):
    scene = reading_scene; provider = SyntheticProvider(scene, monkeypatch, mode='after_raw')
    _, _, accepted = start(scene); assert process_once(accepted['draft']['job_id'])
    identity = accepted['draft']['id']; stopped = get(scene, identity)
    assert stopped['status'] == 'running' and stopped['extractions'][0]['raw_response_saved'] and len(provider.calls) == 1
    key = stopped['extractions'][0]['batch_id']
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], identity, True)
        original_raw = copy.deepcopy(pipeline._stage(row, key)['raw_response'])
        original_call = db.get(CloudCall, provider.calls[0]); fee_before = (original_call.status, original_call.charged_tokens, copy.deepcopy(original_call.data))
        assert pipeline.run(db, scene['p'], identity) == 'awaiting_merge'
        db.commit()
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], identity)
        assert pipeline._stage(row, key)['status'] == 'succeeded'
        assert pipeline._stage(row, key)['raw_response'] == original_raw
        call = db.get(CloudCall, provider.calls[0])
        assert (call.status, call.charged_tokens, call.data) == fee_before
    assert get(scene, identity)['status'] == 'awaiting_merge'
    assert len(provider.calls) == 1 and counts()[CloudCall.__tablename__] == 1


@pytest.mark.parametrize('mode',['truncated','missing_finish','multiple_choices','legacy_missing'])
def test_incomplete_schema_valid_paid_answer_never_recovers_or_replays(reading_scene, monkeypatch,mode):
    scene=reading_scene;provider=SyntheticProvider(scene,monkeypatch,mode=mode)
    _,_,accepted=start(scene);assert process_once(accepted['draft']['job_id'])
    identity=accepted['draft']['id'];failed=get(scene,identity)
    assert failed['status']=='failed' and failed['extractions'][0]['raw_response_saved']
    assert failed['extractions'][0]['status']=='failed' and failed['model_result'] is None
    with Session(engine()) as db:
        row=pipeline.owned(db,scene['p'],identity);raw=copy.deepcopy(row.data['extractions'][0]['raw_response'])
        call=db.get(CloudCall,provider.calls[0]);fee=(call.status,call.charged_tokens,copy.deepcopy(call.data))
    assert 'value' in raw and raw['receipt']['usage']['total_tokens']==20
    assert fee[0]=='failed' and fee[1]==20
    response=scene['client'].post(BASE+'/'+identity+'/recover-saved',json={'revision':failed['revision'],'request_id':str(uuid4())})
    assert response.status_code==422 and response.json()['detail']['code']=='full_completion_unverified'
    again=get(scene,identity)
    assert again['status']=='failed' and again['extractions'][0]['status']=='failed' and again['model_result'] is None
    with Session(engine()) as db:
        row=pipeline.owned(db,scene['p'],identity);assert row.data['extractions'][0]['raw_response']==raw
        call=db.get(CloudCall,provider.calls[0]);assert (call.status,call.charged_tokens,call.data)==fee
    assert len(provider.calls)==1 and counts()[CloudCall.__tablename__]==1


@pytest.mark.parametrize('mode',['oversize','unsupported_encoding'])
def test_unavailable_wire_truthfully_reports_unknown_original_and_usage(reading_scene,monkeypatch,mode):
    scene=reading_scene;provider=SyntheticProvider(scene,monkeypatch,mode=mode)
    _,_,accepted=start(scene);assert process_once(accepted['draft']['job_id'])
    identity=accepted['draft']['id'];failed=get(scene,identity)
    assert failed['status']=='failed' and failed['error_code']=='full_response_body_unavailable'
    assert '完整原答未保存' in failed['error'] and '真实用量无法核对' in failed['error']
    assert failed['extractions'][0]['raw_response_saved'] is False
    with Session(engine()) as db:
        row=pipeline.owned(db,scene['p'],identity);stage=row.data['extractions'][0];raw=stage['raw_response']
        assert raw['receipt']['response_body_status']==mode and 'provider_wire_base64' not in raw
        call=db.get(CloudCall,provider.calls[0]);assert call.status=='failed' and call.charged_tokens==call.reserved_tokens
    response=scene['client'].post(BASE+'/'+identity+'/recover-saved',json={'revision':failed['revision'],'request_id':str(uuid4())})
    assert response.status_code==422 and response.json()['detail']['code']=='full_completion_unverified'
    assert len(provider.calls)==1


@pytest.mark.parametrize('mode',['oversize','unsupported_encoding'])
def test_unavailable_wire_remains_truthful_if_worker_loses_lease_after_checkpoint(reading_scene,monkeypatch,mode):
    scene=reading_scene;provider=SyntheticProvider(scene,monkeypatch,mode=mode+'_after_raw')
    _,_,accepted=start(scene);job=accepted['draft']['job_id'];assert process_once(job)
    identity=accepted['draft']['id'];stopped=get(scene,identity)
    assert stopped['extractions'][0]['raw_response_saved'] is False
    with Session(engine()) as db:
        pipeline.record_job_failure(db,scene['p'],db.get(Job,job),'synthetic worker interruption');db.commit()
    failed=get(scene,identity)
    assert failed['status']=='failed' and failed['error_code']=='full_response_body_unavailable'
    assert '完整原答未保存' in failed['error'] and '真实用量无法核对' in failed['error']
    assert len(provider.calls)==1 and counts()[CloudCall.__tablename__]==1


def test_legacy_success_remains_readable_but_cannot_launch_unverified_merge(reading_scene,monkeypatch):
    scene=reading_scene;provider=SyntheticProvider(scene,monkeypatch)
    _,_,accepted=start(scene);done=finish_extract(scene,accepted);identity=done['id']
    with Session(engine()) as db:
        row=pipeline.owned(db,scene['p'],identity,True);stage=copy.deepcopy(row.data['extractions'][0]);raw=stage['raw_response']
        raw.pop('provider_wire_base64',None)
        for key in ('completion_guard','finish_reason','response_choice_count','response_body_status'):raw['receipt'].pop(key,None)
        raw['hash']=digest({k:v for k,v in raw.items() if k!='hash'})
        pipeline._replace_stage(row,stage['batch_id'],stage);db.commit()
    old=get(scene,identity)
    assert old['status']=='awaiting_merge' and old['extractions'][0]['status']=='succeeded'
    result=scene['client'].post(BASE+'/'+identity+'/merge-preview',json={'revision':old['revision'],'request_id':'fm_'+str(uuid4())})
    assert result.status_code==409 and result.json()['detail']['code']=='full_completion_unverified'
    assert len(provider.calls)==1 and counts()[CloudCall.__tablename__]==1


def test_missing_merge_input_is_visible_failure_with_saved_raw_and_fee(reading_scene, monkeypatch):
    scene = reading_scene; provider = SyntheticProvider(scene, monkeypatch, mode='bad_merge')
    preview, _, accepted = start(scene); extracted = finish_extract(scene, accepted)
    _, _, _, failed = merge(scene, extracted)
    assert failed['status'] == 'failed' and failed['merge']['raw_response_saved']
    assert failed['model_result'] is None
    assert all(stage['raw_response_saved'] and stage['status'] == 'succeeded' for stage in failed['extractions'])
    assert len(provider.calls) == len(preview['batches']) + 1
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, failed['id']); raw = row.data['merge']['raw_response']
        assert raw['value']['reconciliation'] != [] or len(provider.inputs[-1]['input_items']) == 1
        assert len(raw['value']['reconciliation']) == len(provider.inputs[-1]['input_items']) - 1
        assert db.get(CloudCall, provider.calls[-1]).status == 'succeeded'


@pytest.mark.parametrize('role', ['operator', 'admin'])
def test_other_actor_including_admin_cannot_read_or_edit_private_full_draft(reading_scene, monkeypatch, role):
    scene = reading_scene; provider = SyntheticProvider(scene, monkeypatch)
    _, _, accepted = start(scene); extracted = finish_extract(scene, accepted)
    _, _, _, generated = merge(scene, extracted)
    username, password = 'other_full_' + uuid4().hex[:12], 'Synthetic-full-' + uuid4().hex
    user = reply(scene['client'].post('/api/users', json={'username': username, 'password': password, 'role': role}), 201)
    with TestClient(app) as other:
        login = reply(other.post('/api/login', json={'username': username, 'password': password}))
        other.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': scene['p'].workspace_id})
        assert reply(other.get('/api/me'))['user_id'] == user['id']
        assert other.get(BASE + '/' + generated['id']).status_code == 404
        assert other.get(BASE + '/by-request/' + scene['body']['request_id']).status_code == 404
        assert other.put(BASE + '/' + generated['id'] + '/review', json={'revision': generated['revision'],
            'request_id': str(uuid4()), 'value': {'result': generated['model_result'], 'note': 'must not overwrite'}}).status_code == 404
        assert reply(other.get(BASE))['items'] == []
    assert get(scene, generated['id']) == generated and len(provider.calls) == 2


def test_new_semantics_cannot_be_silently_handed_off_through_legacy_projection(reading_scene, monkeypatch):
    scene = reading_scene; SyntheticProvider(scene, monkeypatch)
    preview, _, accepted = start(scene); extracted = finish_extract(scene, accepted)
    _, _, _, generated = merge(scene, extracted)
    saved = reply(scene['client'].put(BASE + '/' + generated['id'] + '/review', json={'revision': generated['revision'],
        'request_id': str(uuid4()), 'value': {'result': generated['model_result'], 'note': '保留协同语义'}}))
    configuration = reply(scene['client'].get('/api/setup/draft'))
    body = {'report_revision': saved['draft']['revision'], 'review_receipt_hash': saved['receipt']['receipt_hash'],
        'candidate_hash': digest(saved['draft']['review_value']['result']), 'source_hash': preview['source_hash'],
        'validation_report_hash': digest(None), 'configuration_revision': configuration['revision'],
        'configuration_hash': digest(configuration['configuration']), 'business_indexes': [0]}
    before = counts()
    response = scene['client'].post('/api/setup/report-drafts/' + generated['id'] + '/configuration-preview', json=body)
    assert response.status_code == 409 and response.json()['detail']['code'] == 'full_semantics_mapping_required'
    assert counts() == before and get(scene, generated['id']) == saved['draft']


def narrow_extract_capacity(scene, monkeypatch):
    # Only this case tightens the byte budget to exercise the real splitter.
    # Schema, gateway preparation, provenance, source blocks, raw and ledger
    # checks all remain production paths; the policy is never made more lenient.
    natural = reply(scene['client'].post(BASE + '/preview', json=scene['body']))
    projection = pipeline.legacy.old().scrub_tree(pipeline.project_full_source(natural['source_snapshot'])[0])
    cap = scene['cap']; full_blocks = projection['blocks']; spec = natural['generation_contract']
    with Session(engine()) as db:
        from generic_agents.models import Capability
        model = db.get(Capability, cap['id'])
        sizes = []
        for prefix in ('w:', 'o:'):
            payload = pipeline._payload(projection, natural['batches'][0]['payload']['context'],
                natural['batches'][0]['payload']['user_instruction'], [row for row in full_blocks if row[0].startswith(prefix)])
            _, summary = pipeline.prepared_stage(model, spec, payload); sizes.append(summary['prepared_messages_bytes'])
    bound = max(sizes)
    assert bound < natural['batches'][0]['input_summary']['prepared_messages_bytes']
    actual_measure = pipeline.measure_input
    def stricter(messages, policy, max_tokens):
        payload = json.loads(messages[-1]['content'])
        selected = ({**policy, 'max_input_bytes': min(policy['max_input_bytes'], bound)}
                    if payload.get('schema') == 'report-full-reading-extract-evidence-v1' else policy)
        return actual_measure(messages, selected, max_tokens)
    monkeypatch.setattr(pipeline, 'measure_input', stricter)
    return full_blocks


def test_two_exact_extract_batches_have_no_truncation_and_first_saved_stage_never_repeats(reading_scene, monkeypatch):
    scene = reading_scene; full_blocks = narrow_extract_capacity(scene, monkeypatch)
    provider = SyntheticProvider(scene, monkeypatch)
    preview, _, accepted = start(scene)
    assert len(preview['batches']) == 2
    assert [row for batch in preview['batches'] for row in batch['payload']['blocks']] == full_blocks
    view = finish_extract(scene, accepted)
    assert len(provider.calls) == 2
    assert view['extractions'][0]['status'] == 'succeeded' and view['extractions'][0]['raw_response_saved']
    assert view['extractions'][1]['status'] == 'succeeded' and view['extractions'][1]['raw_response_saved']
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], view['id'], True)
        first = copy.deepcopy(row.data['extractions'][0])
        pipeline._run_stage(db, scene['p'], row, first['batch_id'])
        assert row.data['extractions'][0] == first
        db.rollback()
    assert len(provider.calls) == 2 and counts()[CloudCall.__tablename__] == 2
    merge_preview, _, _, final = merge(scene, view)
    assert final['status'] == 'review' and len(provider.calls) == 3 and counts()[CloudCall.__tablename__] == 3
    assert len(merge_preview['payload']['input_items']) == 3
    assert {entry['input_id'] for entry in final['merge']['reconciliation']} == set(merge_preview['payload']['input_items'])
    assert {entry['disposition'] for entry in final['merge']['reconciliation']} == {'kept', 'duplicate'}
    assert final['model_result'] == example_candidate()


def test_extract_cannot_borrow_literal_citation_from_a_different_unsent_batch(reading_scene, monkeypatch):
    scene = reading_scene; narrow_extract_capacity(scene, monkeypatch)
    provider = SyntheticProvider(scene, monkeypatch, mode='cross_batch')
    preview, _, accepted = start(scene)
    assert len(preview['batches']) == 2
    assert 'o:p1' not in preview['batches'][0]['source_refs'] and 'o:p1' in preview['batches'][1]['source_refs']
    assert process_once(accepted['draft']['job_id'])
    failed = get(scene, accepted['draft']['id'])
    assert failed['status'] == 'failed' and failed['extractions'][0]['raw_response_saved']
    assert failed['extractions'][1]['status'] == 'pending' and not failed['extractions'][1]['raw_response_saved']
    assert len(provider.calls) == 1 and counts()[CloudCall.__tablename__] == 1
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, failed['id'])
        raw = row.data['extractions'][0]['raw_response']
        assert raw['value']['businesses'][0]['citations'][0]['source_ref'] == 'o:p1'
        call = db.get(CloudCall, provider.calls[0])
        assert call.status == 'succeeded' and call.charged_tokens == 20


def test_last_extract_checkpoint_lease_loss_recovers_only_phase_without_mutating_saved_results(reading_scene, monkeypatch):
    scene = reading_scene; narrow_extract_capacity(scene, monkeypatch)
    provider = SyntheticProvider(scene, monkeypatch)
    preview, _, accepted = start(scene)
    assert len(preview['batches']) == 2
    identity, job_id = accepted['draft']['id'], accepted['draft']['job_id']
    actual_checkpoint = pipeline.checkpoint; interrupted = {'once': False}

    def checkpoint_then_lose_tail_lease(db):
        # This invokes the real worker-owned commit FIRST. No row/status is
        # edited by the injector. It observes the real completed final _consume
        # while phase is still extract, before run's awaiting_merge transition.
        actual_checkpoint(db)
        if interrupted['once']: return
        row = db.get(SetupReportDraft, identity)
        if (row is not None and row.data['phase'] == 'extract'
                and all(stage['status'] == 'succeeded' for stage in row.data['extractions'])):
            assert db.info.get('worker_lease'), 'interruption must follow an actual leased-worker checkpoint'
            interrupted['once'] = True
            raise LeaseLost('synthetic tail window after all paid extraction results committed')

    monkeypatch.setattr(pipeline, 'checkpoint', checkpoint_then_lose_tail_lease)
    assert process_once(job_id) and interrupted['once'] is True
    before_view = get(scene, identity)
    assert before_view['status'] == 'running' and before_view['phase'] == 'extract'
    assert len(provider.calls) == 2
    assert all(stage['status'] == 'succeeded' and stage['raw_response_saved'] for stage in before_view['extractions'])
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], identity)
        saved_stages = copy.deepcopy(row.data['extractions'])
        fee_before = {call_id: (call.status, call.reserved_tokens, call.charged_tokens, copy.deepcopy(call.data))
            for call_id in provider.calls for call in [db.get(CloudCall, call_id)]}
    before_counts = counts()
    # The real crash-recovery hook, in a fresh transaction. Its only authorized
    # change is the local phase transition plus its explicit recovery Audit.
    with Session(engine()) as db:
        pipeline.record_job_failure(db, scene['p'], db.get(Job, job_id), 'synthetic original worker lease stopped')
        db.commit()
    recovered = get(scene, identity)
    assert recovered['status'] == recovered['phase'] == 'awaiting_merge'
    assert recovered['error'] is recovered['error_code'] is None
    assert recovered['extractions'] == before_view['extractions']
    assert recovered['model_result'] is None and recovered['merge']['status'] == 'not_started'
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], identity)
        assert row.data['extractions'] == saved_stages
        for call_id, frozen in fee_before.items():
            call = db.get(CloudCall, call_id)
            assert (call.status, call.reserved_tokens, call.charged_tokens, call.data) == frozen
        # Repeated recovery is also a no-op; it neither demotes the saved last
        # stage nor adds another audit/receipt or paid request.
        pipeline.record_job_failure(db, scene['p'], db.get(Job, job_id), 'same already recovered tail')
        db.commit()
    after_counts = counts()
    assert {key: value for key, value in after_counts.items() if key != Audit.__tablename__} == {
        key: value for key, value in before_counts.items() if key != Audit.__tablename__}
    assert after_counts[Audit.__tablename__] == before_counts[Audit.__tablename__] + 1
    assert get(scene, identity) == recovered and len(provider.calls) == 2


@pytest.mark.parametrize('phase', ['extract', 'merge'])
def test_real_saved_raw_failure_recovers_via_owner_api_without_provider_job_or_fee_change(reading_scene, monkeypatch, phase):
    scene = reading_scene; provider = SyntheticProvider(scene, monkeypatch)
    _, _, accepted = start(scene); identity = accepted['draft']['id']
    if phase == 'extract':
        provider.mode = 'after_raw'
        assert process_once(accepted['draft']['job_id'])
        interrupted = get(scene, identity); job_id = accepted['draft']['job_id']; key = 'extract-1'
    else:
        extracted = finish_extract(scene, accepted)
        provider.mode = 'after_raw'
        _, _, merge_start, interrupted = merge(scene, extracted)
        job_id = merge_start['draft']['job_id']; key = 'merge'
    assert interrupted['status'] == 'running' and interrupted['phase'] == phase
    assert (interrupted['merge']['raw_response_saved'] if phase == 'merge'
            else interrupted['extractions'][0]['raw_response_saved'])
    # This is the real failure hook after a committed callback and lost worker,
    # not a test-written failed row. It must retain the received original.
    with Session(engine()) as db:
        pipeline.record_job_failure(db, scene['p'], db.get(Job, job_id), 'synthetic stopped worker after saved raw')
        db.commit()
    failed = get(scene, identity)
    assert failed['status'] == 'failed'
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], identity)
        original_raw = copy.deepcopy(pipeline._stage(row, key)['raw_response'])
        fees = {call_id: (call.status, call.reserved_tokens, call.charged_tokens, copy.deepcopy(call.data))
                for call_id in provider.calls for call in [db.get(CloudCall, call_id)]}
    request_id = str(uuid4()); path = BASE + '/' + identity
    body = {'revision': failed['revision'], 'request_id': request_id}
    assert scene['client'].get(path + '/recovery-receipts/' + request_id).status_code == 404
    before_counts, before_invocations = counts(), list(provider.calls)
    recovered = reply(scene['client'].post(path + '/recover-saved', json=body))
    receipt = recovered['receipt']; current = recovered['draft']
    assert receipt['schema'] == 'report-full-reading-recovery-receipt-v1' and receipt['kind'] == 'report_full_recovery'
    assert receipt['actor_id'] == scene['p'].user_id and receipt['workspace_id'] == scene['p'].workspace_id
    assert receipt['draft_id'] == identity and receipt['request_id'] == request_id
    assert receipt['accepted_revision'] == current['revision'] and receipt['accepted_revision'] > failed['revision']
    assert receipt['receipt_hash'] == digest({key: value for key, value in receipt.items() if key != 'receipt_hash'})
    assert current['status'] == ('review' if phase == 'merge' else 'awaiting_merge')
    assert current['can_apply'] is current['can_publish'] is False
    if phase == 'merge': assert current['model_result'] == example_candidate()
    else: assert current['model_result'] is None
    assert get(scene, identity) == current
    assert reply(scene['client'].get(path + '/recovery-receipts/' + request_id)) == recovered
    # A separate HTTP app/session reads the same owner receipt, using the same
    # real synthetic cookie/CSRF; this is an engineering reload, not browser proof.
    with TestClient(app) as reopened:
        reopened.cookies.update(scene['client'].cookies)
        reopened.headers.update({'X-Workspace-ID': scene['p'].workspace_id})
        assert reply(reopened.get(path)) == current
        assert reply(reopened.get(path + '/recovery-receipts/' + request_id)) == recovered
    assert counts()[CloudCall.__tablename__] == before_counts[CloudCall.__tablename__]
    assert counts()['jobs'] == before_counts['jobs'] and provider.calls == before_invocations
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], identity)
        assert pipeline._stage(row, key)['raw_response'] == original_raw
        for call_id, original_fee in fees.items():
            call = db.get(CloudCall, call_id)
            assert (call.status, call.reserved_tokens, call.charged_tokens, call.data) == original_fee
    replay_counts = counts()
    assert reply(scene['client'].post(path + '/recover-saved', json=body)) == recovered
    assert scene['client'].post(path + '/recover-saved', json={**body, 'revision': body['revision'] + 1}).status_code == 409
    assert scene['client'].post(path + '/recover-saved', json={**body, 'request_id': str(uuid4())}).status_code == 409
    assert counts() == replay_counts and provider.calls == before_invocations
    (Path.cwd() / ('full-reading-recovery-' + phase + '-bridge.json')).write_text(json.dumps({
        'verification_scope': 'synthetic-engineering-not-model-quality', 'failed': failed,
        'body': body, 'recovered': recovered}, ensure_ascii=False))


def test_partial_saved_raw_free_recovery_never_resends_or_consumes_missing_batch(reading_scene, monkeypatch):
    scene = reading_scene; narrow_extract_capacity(scene, monkeypatch)
    provider = SyntheticProvider(scene, monkeypatch, mode='after_raw')
    preview, _, accepted = start(scene); identity = accepted['draft']['id']
    assert len(preview['batches']) == 2 and process_once(accepted['draft']['job_id'])
    interrupted = get(scene, identity)
    assert interrupted['extractions'][0]['raw_response_saved'] is True
    assert interrupted['extractions'][1]['status'] == 'pending' and not interrupted['extractions'][1]['raw_response_saved']
    with Session(engine()) as db:
        pipeline.record_job_failure(db, scene['p'], db.get(Job, accepted['draft']['job_id']), 'synthetic partial saved source')
        db.commit()
    failed = get(scene, identity)
    assert failed['status'] == 'failed'
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], identity); data_before = copy.deepcopy(row.data)
        call = db.get(CloudCall, provider.calls[0]); fee_before = (call.status, call.charged_tokens, copy.deepcopy(call.data))
    before_counts = counts(); request_id = str(uuid4()); path = BASE + '/' + identity
    response = scene['client'].post(path + '/recover-saved', json={'revision': failed['revision'], 'request_id': request_id})
    assert response.status_code == 409 and response.json()['detail']['code'] == 'full_saved_response_incomplete'
    assert scene['client'].get(path + '/recovery-receipts/' + request_id).status_code == 404
    assert counts() == before_counts and len(provider.calls) == 1
    assert get(scene, identity) == failed
    with Session(engine()) as db:
        row = pipeline.owned(db, scene['p'], identity)
        assert row.data == data_before
        call = db.get(CloudCall, provider.calls[0])
        assert (call.status, call.charged_tokens, call.data) == fee_before
