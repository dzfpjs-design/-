"""v3 完整紧凑输入/真实付费账本链；云与 OCR 都为明确测试替身。"""
import copy
import hashlib
import json
from uuid import uuid4

import pytest
import httpx
from pydantic import SecretStr
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import SetupAnalysis, SetupMaterial, Evidence, CloudCall, Job, now, Membership, Workspace
from generic_agents.worker import process_once
import generic_agents.setup_understanding as understanding
from tests.test_setup_understanding import prepared, start, process
from tests.test_setup_objects_runtime import TEXT, fake_v2, apply
from tests.test_material_ocr import local_runtime, upload as upload_scan, start as start_ocr
from tests.test_material_ocr import finish_jobs, detail, confirm_page, confirm_document
from tests.test_material_ocr import scan_pdf


def v3(client, text=TEXT):
    body, material = prepared(client, text)
    return {**body, 'contract_version': 3, 'request_id': str(uuid4())}, material


def get(client, row):
    return client.get('/api/setup/understanding/' + row['id']).json()


def process_latest(row):
    with Session(engine()) as db:
        job = db.scalar(select(Job).where(Job.kind == 'understand_setup', Job.data['analysis_id'].astext == row['id']).order_by(Job.created_at.desc()))
    assert process_once(job.id, deliver=False)


def test_full_31_blocks_one_dispatch_and_server_snapshot_not_sent(client, monkeypatch):
    body, material = v3(client, '\n\n'.join([TEXT] + [f'第{n}项辅助说明：缺失资料必须提问，不扩展流程。' for n in range(1, 31)]))
    calls = fake_v2(monkeypatch)
    row, preview = start(client, body)
    assert preview['contract_version'] == 3 and len(preview['chunks']) == 1
    assert len(preview['chunks'][0]) == 31
    assert preview['source_snapshot_hash'] == digest(preview['source_snapshot'])
    assert all(set(b) == {'material_id', 'block_id', 'text', 'location'} for b in preview['chunks'][0])
    assert all(set(b['location']) <= {'page', 'body_index'} for b in preview['chunks'][0])
    process(row); current = get(client, row)
    assert current['status'] == 'review', current['error']
    expected = [{k: v for k, v in b.items() if k != 'location'} | {'position': b['location']} for b in preview['chunks'][0]]
    assert len(calls) == 1 and calls[0]['source_blocks'] == expected
    assert 'source_snapshot' not in json.dumps(calls) and 'blocks_hash' not in json.dumps(calls)
    with Session(engine()) as db:
        cap = db.get(__import__('generic_agents.models', fromlist=['Capability']).Capability, body['model_id'])
        messages, text_bytes, _ = understanding.prepare_cloud_json(cap, understanding.messages_for(preview, None, 0), understanding.ResultV2.model_json_schema())
        call = db.scalar(select(CloudCall))
        assert call.data['request_hash'] == preview['first_dispatch_messages_hash'] == digest(messages)
        assert preview['input_summary']['prepared_messages_bytes'] == len(json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
        assert preview['input_summary']['token_bound'] == text_bytes + len(json.dumps(understanding.ResultV2.model_json_schema()).encode()) + cap.data['execution'].get('max_tokens', 2048) + 512
        assert call.status == 'succeeded'
    result = apply(client, current, body)
    assert result.status_code == 200, result.text
    undone = client.post('/api/setup/understanding/' + row['id'] + '/undo', json={'version': result.json()['analysis']['version'], 'revision': result.json()['revision'], 'object_names': ['设备']})
    assert undone.status_code == 200
    reused = client.post('/api/setup/understanding/' + row['id'] + '/reuse', json={'version': undone.json()['analysis']['version'], 'revision': undone.json()['revision']})
    assert reused.status_code == 201, reused.text
    assert reused.json()['contract_version'] == 3 and reused.json()['review_selection']['object_names'] == ['设备']
    assert len(calls) == 1


def test_v3_requires_nonce_and_blocks_whole_over_capacity_without_rows(client):
    body, _ = v3(client, '完整资料' * 15000)
    missing = {k: v for k, v in body.items() if k != 'request_id'}
    assert client.post('/api/setup/understanding/preview', json=missing).status_code == 422
    blocked = client.post('/api/setup/understanding/preview', json=body)
    assert blocked.status_code == 413 and '50000' in blocked.json()['detail']
    assert '完整' in blocked.json()['detail']
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
        assert db.scalar(select(func.count()).select_from(SetupAnalysis)) == 0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == 'understand_setup')) == 0


def test_v3_preview_prechecks_daily_budget_without_reserving(client, monkeypatch):
    body, _ = v3(client)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 1)
    response = client.post('/api/setup/understanding/preview', json=body)
    assert response.status_code == 409 and '预算' in response.json()['detail']
    with Session(engine()) as db:
        assert not db.scalar(select(CloudCall)) and not db.scalar(select(Job).where(Job.kind == 'cloud_budget'))


@pytest.mark.parametrize('damage', ['omit', 'text', 'location', 'metadata', 'second_batch', 'native_block', 'provider'])
def test_v3_self_signed_projection_mapping_tamper_never_dispatches(client, monkeypatch, damage):
    body, material = v3(client)
    calls = fake_v2(monkeypatch); row, preview = start(client, body)
    with Session(engine()) as db:
        record = db.get(SetupAnalysis, row['id']); req = copy.deepcopy(record.data['request'])
        if damage == 'omit': req['chunks'][0] = []
        elif damage == 'text': req['chunks'][0][0]['text'] = '替换完整正文'
        elif damage == 'location': req['chunks'][0][0]['location'] = {'page': 999}
        elif damage == 'metadata': req['chunks'][0][0]['raw_image_sha256'] = 'a' * 64
        elif damage == 'second_batch': req['chunks'].append(copy.deepcopy(req['chunks'][0]))
        elif damage == 'provider': req['provider'] = 'deepseek'
        else:
            m = db.get(SetupMaterial, material['id']); blocks = copy.deepcopy(m.data['blocks']); blocks[0]['text'] = '数据库自洽伪原文'
            m.data = {**m.data, 'blocks': blocks}
            req['source_snapshot']['materials'][0]['blocks'] = blocks
            req['source_snapshot']['materials'][0]['blocks_hash'] = digest(blocks)
            req['materials'][0]['blocks_hash'] = digest(blocks)
            req['source_snapshot_hash'] = digest(req['source_snapshot'])
            req['chunks'][0][0]['text'] = blocks[0]['text']
        req['projection_hash'] = digest(req['chunks'][0]); req['preview_hash'] = digest({k: v for k, v in req.items() if k != 'preview_hash'})
        record.data = {**record.data, 'request': req, 'consent': {**record.data['consent'], 'preview_hash': req['preview_hash']}}; db.commit()
    process(row); result = get(client, row)
    assert result['status'] == 'failed' and not calls
    with Session(engine()) as db: assert not db.scalar(select(CloudCall))


def test_v3_paid_raw_local_recovery_no_http_and_nonce_replay_survives_changed_source(client, monkeypatch):
    body, material = v3(client)
    calls = fake_v2(monkeypatch, lambda value, _: value['objects'][0]['fields'][2].update(unit='℃'))
    row, preview = start(client, body); process(row); failed = get(client, row)
    assert failed['status'] == 'failed' and failed['recovery']['mode'] == 'local_only'
    with Session(engine()) as db: frozen = copy.deepcopy(db.get(SetupAnalysis, row['id']).data['raw_response'])
    retry = client.post('/api/setup/understanding/' + row['id'] + '/retry', json={'version': failed['version']})
    assert retry.status_code == 200, retry.text
    process_latest(retry.json()); assert get(client, row)['status'] == 'failed' and len(calls) == 1
    with Session(engine()) as db:
        record = db.get(SetupAnalysis, row['id']); assert record.data['raw_response'] == frozen
        m = db.get(SetupMaterial, material['id']); m.version += 1; db.commit()
    original = {**body, 'preview_hash': preview['preview_hash'], 'consent': True}
    replay = client.post('/api/setup/understanding', json=original)
    assert replay.status_code == 202 and replay.json()['id'] == row['id']
    assert client.post('/api/setup/understanding', json={**original, 'instruction': '修改正文'}).status_code == 409
    assert client.post('/api/setup/understanding', json={**original, 'contract_version': 2}).status_code == 409
    assert client.get('/api/setup/understanding/by-preview/' + preview['preview_hash']).json()['id'] == row['id']
    assert len(calls) == 1


def test_v3_no_response_cannot_retry_old_grant_or_reserve(client, monkeypatch):
    body, _ = v3(client); row, _ = start(client, body)
    with Session(engine()) as db:
        record = db.get(SetupAnalysis, row['id']); record.status = 'failed'; record.data = {**record.data, 'inflight': 0, 'error': '响应未知'}; db.commit()
    current = get(client, row)
    assert current['recovery']['mode'] == 'new_preview_required'
    response = client.post('/api/setup/understanding/' + row['id'] + '/retry', json={'version': current['version']})
    assert response.status_code == 409 and '新' in response.json()['detail'] and '计费' in response.json()['detail']
    assert get(client, row)['version'] == current['version']
    with Session(engine()) as db: assert not db.scalar(select(CloudCall))


def test_confirmed_ocr_full_snapshot_and_image_tamper_blocks_dispatch(client, monkeypatch, local_runtime):
    material = upload_scan(client); _, first = start_ocr(client, material)
    root, units = finish_jobs(client, material, first)
    for unit in units: confirm_page(client, material, detail(client, material, root['run']['id'], unit['id']))
    confirm_document(client, material)
    body, _ = v3(client)
    body['material_ids'] = [material['id']]
    calls = fake_v2(monkeypatch); row, preview = start(client, body)
    snapshot = preview['source_snapshot']['materials'][0]
    assert snapshot['confirmation']['record_hash'] and snapshot['blocks'][0]['location']['ocr']
    assert preview['chunks'][0][0]['location'] == {'page': 1}
    from generic_agents.models import MaterialOcrUnit
    with Session(engine()) as db:
        unit = db.scalar(select(MaterialOcrUnit)); evidence = db.get(Evidence, unit.evidence_id)
        (settings().storage_dir / evidence.storage_key).write_bytes(b'changed-original-page')
    process(row); assert get(client, row)['status'] == 'failed' and not calls
    assert client.get('/api/setup/understanding/' + row['id']).status_code == 200


def test_persisted_31_ocr_regions_shrink_to_full_text_one_batch(client, monkeypatch, local_runtime):
    from generic_agents import material_ocr_runtime as runtime
    original = runtime.recognize
    def recognize(content, expected_manifest_hash, guard=None):
        value = original(content, expected_manifest_hash, guard)
        regions = [{'id': f'r{n}', 'text': TEXT if n == 1 else f'辅助说明{n}：依据不足必须提问。',
                    'box': [1, 2, 80, 20], 'score': .7} for n in range(1, 32)]
        return {**value, 'regions': regions, 'receipt': {**value['receipt'], 'result_sha256': digest(regions)}}
    monkeypatch.setattr(runtime, 'recognize', recognize)
    material = upload_scan(client, scan_pdf(1)); _, first = start_ocr(client, material)
    root, units = finish_jobs(client, material, first)
    confirm_page(client, material, detail(client, material, root['run']['id'], units[0]['id']))
    confirm_document(client, material)
    body, _ = v3(client); body['material_ids'] = [material['id']]
    legacy = client.post('/api/setup/understanding/preview', json={**body, 'contract_version': 2}).json()
    assert len(legacy['chunks']) > 1
    calls = fake_v2(monkeypatch); row, preview = start(client, body)
    assert len(preview['chunks']) == 1 and len(preview['chunks'][0]) == 31
    assert all(b['location'] == {'page': 1} for b in preview['chunks'][0])
    assert all(b['location']['ocr']['image_sha256'] for b in preview['source_snapshot']['materials'][0]['blocks'])
    process(row); assert get(client, row)['status'] == 'review' and len(calls) == 1
    assert len(calls[0]['source_blocks']) == 31
    assert preview['projection_contract']['cloud_location_key'] == 'position'
    assert all(set(b)=={'material_id','block_id','text','position'} and b['position']=={'page':1}
               and type(b['position']['page']) is int for b in calls[0]['source_blocks'])
    sent=json.dumps(calls[0])
    assert not any(key in sent for key in ('"location"','"contact"','"box"','image_sha256','confirmation_id','source_snapshot'))


@pytest.mark.parametrize('content', ['{"domain":"missing-objects"}', 'not-json', None])
def test_actual_http_paid_invalid_answer_is_saved_and_local_retry_never_resends(client, monkeypatch, content):
    body, _ = v3(client); row, preview = start(client, body)
    base = httpx.Client; sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}], 'usage': {'total_tokens': 29}})
    monkeypatch.setattr('generic_agents.gateway.httpx.Client', lambda **kw: base(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(settings(), 'dashscope_api_key', SecretStr('test-only-key'))
    monkeypatch.delenv('DASHSCOPE_API_KEY', raising=False)
    process(row); failed = get(client, row)
    assert failed['status'] == 'failed' and failed['recovery']['mode'] == 'local_only'
    assert len(sent) == 1 and digest(sent[0]['messages']) == preview['first_dispatch_messages_hash']
    assert {k: sent[0][k] for k in ('model', 'max_tokens', 'response_format')} == preview['generation_contract']['dispatch_options']
    assert 'ocr' not in sent[0]['messages'][1]['content'] and 'source_snapshot' not in sent[0]['messages'][1]['content']
    with Session(engine()) as db:
        raw = copy.deepcopy(db.get(SetupAnalysis, row['id']).data['raw_response']); call = db.scalar(select(CloudCall))
        assert raw['content'] == content and raw['receipt']['call_id'] == call.id
        assert call.status == 'failed' and call.charged_tokens == 29
        assert call.data['dispatch_options_hash'] == digest(preview['generation_contract']['dispatch_options'])
    retry = client.post('/api/setup/understanding/' + row['id'] + '/retry', json={'version': failed['version']})
    assert retry.status_code == 200, retry.text
    process_latest(retry.json()); assert get(client, row)['status'] == 'failed' and len(sent) == 1
    with Session(engine()) as db: assert db.get(SetupAnalysis, row['id']).data['raw_response'] == raw


def test_real_worker_revocation_after_http_keeps_paid_body_but_blocks_apply(client, monkeypatch):
    body, _ = v3(client); row, _ = start(client, body)
    base = httpx.Client; sent = []
    def handler(request):
        sent.append(True)
        with Session(engine()) as db:
            member = db.scalar(select(Membership)); member.role = 'viewer'; db.commit()
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"domain":"retained-paid"}'}}], 'usage': {'total_tokens': 19}})
    monkeypatch.setattr('generic_agents.gateway.httpx.Client', lambda **kw: base(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(settings(), 'dashscope_api_key', SecretStr('test-only-key'))
    monkeypatch.delenv('DASHSCOPE_API_KEY', raising=False)
    process(row)
    with Session(engine()) as db:
        record = db.get(SetupAnalysis, row['id']); call = db.scalar(select(CloudCall))
        assert record.data['raw_response']['content'] == '{"domain":"retained-paid"}'
        assert call.charged_tokens == 19 and call.status == 'failed' and len(sent) == 1
        assert not record.data.get('result')
    failed = get(client, row)
    assert client.post('/api/setup/understanding/' + row['id'] + '/retry', json={'version': failed['version']}).status_code == 403
    assert client.post('/api/setup/understanding/preview', json=body).status_code == 403


def test_real_http_timeout_marks_new_preview_only_and_no_automatic_dispatch(client, monkeypatch):
    body, _ = v3(client); row, _ = start(client, body)
    base = httpx.Client; attempts = []
    def handler(request):
        attempts.append(True); raise httpx.ReadTimeout('test transport timeout', request=request)
    monkeypatch.setattr('generic_agents.gateway.httpx.Client', lambda **kw: base(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(settings(), 'dashscope_api_key', SecretStr('test-only-key'))
    monkeypatch.delenv('DASHSCOPE_API_KEY', raising=False)
    process(row); failed = get(client, row)
    assert failed['status'] == 'failed' and failed['recovery']['mode'] == 'new_preview_required' and len(attempts) == 1
    assert client.post('/api/setup/understanding/' + row['id'] + '/retry', json={'version': failed['version']}).status_code == 409
    with Session(engine()) as db:
        assert not db.get(SetupAnalysis, row['id']).data.get('raw_response')
        call = db.scalar(select(CloudCall)); assert call.status == 'failed' and call.charged_tokens == call.reserved_tokens
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 1


def test_v3_cross_workspace_scope_and_original_nonce_actor_are_enforced(client, account, monkeypatch):
    body, _ = v3(client); fake_v2(monkeypatch); row, preview = start(client, body); process(row)
    with Session(engine()) as db:
        member = db.scalar(select(Membership).where(Membership.workspace_id == account[2]))
        another = Workspace(name='另一个验证工作区'); db.add(another); db.flush()
        db.add(Membership(workspace_id=another.id, user_id=member.user_id, role='admin')); db.commit(); another_id = another.id
    client.headers['X-Workspace-ID'] = another_id
    assert client.get('/api/setup/understanding/' + row['id']).status_code == 404
    assert client.get('/api/setup/understanding/by-preview/' + preview['preview_hash']).status_code == 404
    assert client.post('/api/setup/understanding/preview', json=body).status_code in {404, 409}
    from generic_agents.api import app
    from generic_agents.models import User
    from generic_agents.security import password_hash
    username='nonce_other_'+uuid4().hex[:10]; password=uuid4().hex
    with Session(engine()) as db:
        user=User(username=username,password_hash=password_hash(password));db.add(user);db.flush()
        db.add(Membership(workspace_id=account[2],user_id=user.id,role='admin'));db.commit()
    with TestClient(app) as other:
        login=other.post('/api/login',json={'username':username,'password':password}).json()
        other.headers.update({'X-CSRF-Token':login['csrf_token'],'X-Workspace-ID':account[2]})
        assert other.get('/api/setup/understanding/'+row['id']).status_code==200
        replay=other.post('/api/setup/understanding',json={**body,'preview_hash':preview['preview_hash'],'consent':True})
        assert replay.status_code==403
    with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(SetupAnalysis))==1


def test_resigned_valid_request_cannot_expand_original_consent(client, monkeypatch):
    body, _ = v3(client); calls = fake_v2(monkeypatch); row, _ = start(client, body)
    with Session(engine()) as db:
        from generic_agents.models import Capability
        record = db.get(SetupAnalysis, row['id']); req = copy.deepcopy(record.data['request']); req['instruction'] = '增加未经原授权的新目标'
        req['input_summary'], req['first_dispatch_messages_hash'] = understanding.input_summary(req, db.get(Capability, body['model_id']))
        req['preview_hash'] = digest({k: v for k, v in req.items() if k != 'preview_hash'})
        record.data = {**record.data, 'request': req}; db.commit()
    process(row); failed = get(client, row)
    assert failed['status'] == 'failed' and '原授权' in failed['error'] and not calls


def test_gateway_additional_redaction_cannot_silently_change_preview_source(client):
    body, _ = v3(client)
    with Session(engine()) as db:
        from generic_agents.models import Capability
        cap = db.get(Capability, body['model_id'])
        cap.data = {**cap.data, 'data_policy': {**cap.data['data_policy'], 'redact_fields': ['text']}}
        cap.content_hash = digest(cap.data); cap.validated_hash = cap.content_hash; db.commit()
    blocked = client.post('/api/setup/understanding/preview', json=body)
    assert blocked.status_code == 409 and '额外脱敏' in blocked.json()['detail']
    with Session(engine()) as db: assert not db.scalar(select(CloudCall))


def test_paid_model_response_uses_original_profile_after_mid_http_change(client, monkeypatch):
    body, _ = v3(client); row, preview = start(client, body)
    base = httpx.Client
    def handler(request):
        from generic_agents.models import Capability
        with Session(engine()) as db:
            cap = db.get(Capability, body['model_id']); cap.data = {**cap.data, 'execution': {**cap.data['execution'], 'model': 'new-unapproved-model'}}
            cap.content_hash = digest(cap.data); cap.validated_hash = cap.content_hash; db.commit()
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"domain":"paid-old-profile"}'}}], 'usage': {'total_tokens': 21}})
    monkeypatch.setattr('generic_agents.gateway.httpx.Client', lambda **kw: base(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(settings(), 'dashscope_api_key', SecretStr('test-only-key'))
    monkeypatch.delenv('DASHSCOPE_API_KEY', raising=False)
    process(row)
    with Session(engine()) as db:
        record = db.get(SetupAnalysis, row['id']); call = db.scalar(select(CloudCall))
        assert record.data['raw_response']['content'] == '{"domain":"paid-old-profile"}'
        assert record.data['raw_response']['receipt']['model'] == preview['model']
        assert call.charged_tokens == 21 and call.status == 'failed' and not record.data.get('result')


def test_actual_schema_system_messages_exactly_50000_allowed_50001_blocked(client, monkeypatch):
    from tests.test_setup import upload
    from generic_agents.models import Capability
    monkeypatch.setattr(settings(),'cloud_daily_token_limit',100000)
    body, _ = v3(client,'a'*100)
    baseline=client.post('/api/setup/understanding/preview',json=body)
    assert baseline.status_code==200,baseline.text
    padding=50000-baseline.json()['input_summary']['prepared_messages_bytes']
    assert padding>0
    material=upload(client,('a'*(100+padding)).encode());assert process_once(material['job_id'],deliver=False)
    exact_body={**body,'material_ids':[material['id']],'request_id':str(uuid4())}
    response=client.post('/api/setup/understanding/preview',json=exact_body)
    assert response.status_code==200,response.text
    preview=response.json();assert preview['input_summary']['prepared_messages_bytes']==50000
    with Session(engine()) as db:
        cap=db.get(Capability,body['model_id'])
        messages,_,_=understanding.prepare_cloud_json(cap,understanding.messages_for(preview,None,0),understanding.ResultV2.model_json_schema())
        assert messages[0]['role']=='system' and 'objects' in messages[0]['content']
        assert len(json.dumps(messages,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())==50000
        assert digest(messages)==preview['first_dispatch_messages_hash']
    larger=upload(client,('a'*(101+padding)).encode());assert process_once(larger['job_id'],deliver=False)
    blocked=client.post('/api/setup/understanding/preview',json={**body,'material_ids':[larger['id']],'request_id':str(uuid4())})
    assert blocked.status_code==413 and '50001' in blocked.json()['detail']
    with Session(engine()) as db:
        assert not db.scalar(select(CloudCall)) and not db.scalar(select(SetupAnalysis))
        assert not db.scalar(select(Job).where(Job.kind.in_(['cloud_budget','understand_setup'])))


def test_small_projection_extra_context_and_instruction_exceed_actual_messages_limit(client,monkeypatch):
    monkeypatch.setattr(settings(),'cloud_daily_token_limit',100000)
    body, _ = v3(client,'合法的短资料')
    draft=client.get('/api/setup/draft').json();config=draft['configuration'];config['instanceProfile']['goal']='\\'*8500
    saved=client.put('/api/setup/draft',json={'revision':draft['revision'],'configuration':config})
    assert saved.status_code==200,saved.text
    body['revision']=saved.json()['revision']
    response=client.post('/api/setup/understanding/preview',json=body)
    assert response.status_code==200,response.text
    preview=response.json();assert preview['input_summary']['projection_bytes']<500
    assert len(json.dumps(preview['context'],ensure_ascii=False).encode())<18000
    assert preview['input_summary']['prepared_messages_bytes']<50000
    blocked=client.post('/api/setup/understanding/preview',json={**body,'instruction':'\\'*4000,'request_id':str(uuid4())})
    assert blocked.status_code==413 and '实际发送消息' in blocked.json()['detail'] and '50000' in blocked.json()['detail']
    with Session(engine()) as db:
        assert not db.scalar(select(CloudCall)) and not db.scalar(select(SetupAnalysis))
        assert not db.scalar(select(Job).where(Job.kind.in_(['cloud_budget','understand_setup'])))
