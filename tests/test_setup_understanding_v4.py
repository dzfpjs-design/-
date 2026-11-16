"""Complete long-input transport with explicit HTTP doubles, never live cloud."""
import copy
import json
from uuid import uuid4
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, SetupAnalysis, Job
from tests.test_setup_understanding import prepared, start, process, proposal
from tests.test_setup_understanding_v3 import get, process_latest
from tests.test_vertical_slice import publish


@pytest.fixture(autouse=True)
def synthetic_transport_call_budget(monkeypatch):
    # The root-owned driver keeps real keys and cloud environment budgets zero.
    # These cases explicitly mock all provider HTTP; give only their temporary
    # synthetic reserve/settle ledger a call allowance. Token tests stay intact.
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 100)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 50000)


def long_request(client):
    # First and last restrictions are separated by more than the old byte cap.
    text = '\n\n'.join(['设备编号：文本，必填。仅人工确认后归档。'] +
                       [f'记录{n}：' + '完整保留正文，不得据此新增业务。' * 60 for n in range(35)] +
                       ['结尾约束：不得自动启动无人机，仍须人工确认。'])
    body, material = prepared(client, text)
    return {**body, 'contract_version': 4, 'request_id': str(uuid4())}, material


def wire(monkeypatch, *, fail=None, invalid=False, abbreviate=False):
    sent = []; options = []; base = httpx.Client
    def handler(request):
        body = json.loads(request.content); sent.append(body)
        if fail: raise fail('synthetic interrupted response', request=request)
        payload = json.loads(body['messages'][1]['content'])
        value = proposal(payload); value['objects'] = []
        if invalid: value['businesses'][0]['citations'][0]['quote'] = '原文不存在的引用'
        if abbreviate:
            block=payload['source_blocks'][-1]
            value['businesses'][0]['citations']=[{'material_id':block['material_id'],'block_id':block['block_id'],
                                                'quote':'结尾约束：不得自动启动无人机……仍须人工确认。'}]
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(value, ensure_ascii=False)}}],
                                        'usage': {'total_tokens': 1234}}, headers={'x-request-id': 'test-only-v4'})
    def factory(**kwargs):
        options.append(kwargs)
        return base(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr('generic_agents.gateway.httpx.Client', factory)
    monkeypatch.delenv('DASHSCOPE_API_KEY', raising=False)
    monkeypatch.setattr(settings(), 'dashscope_api_key', SecretStr('synthetic-nonlive'))
    return sent, options


def test_v4_complete_preview_actual_gateway_ledger_and_nonce_replay(client, monkeypatch):
    body, _ = long_request(client)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 1000000)
    sent, options = wire(monkeypatch)
    row, preview = start(client, body)
    assert preview['contract_version'] == 4 and len(preview['chunks']) == 1
    assert len(preview['chunks'][0]) == 37
    assert 50000 < preview['input_summary']['prepared_messages_bytes'] < 900000
    assert preview['input_summary']['max_input_bytes'] == 900000
    assert preview['generation_contract']['dispatch_options']['enable_thinking'] is False
    process(row); result = get(client, row)
    assert result['status'] == 'review', result['error']
    assert len(sent) == 1 and options[0]['timeout'] == 180
    assert digest(sent[0]['messages']) == preview['first_dispatch_messages_hash']
    payload = json.loads(sent[0]['messages'][1]['content'])
    assert len(payload['source_blocks']) == 37
    assert payload['source_blocks'][-1]['text'] == '结尾约束：不得自动启动无人机，仍须人工确认。'
    with Session(engine()) as db:
        call = db.scalar(select(CloudCall))
        assert call.reserved_tokens == preview['input_summary']['token_bound']
        assert call.charged_tokens == 1234 and call.status == 'succeeded'
        assert call.data['dispatch_options_hash'] == digest(preview['generation_contract']['dispatch_options'])
        assert call.data['input_policy_hash'] == digest(preview['generation_contract']['input_policy'])
    replay = client.post('/api/setup/understanding', json={**body, 'preview_hash': preview['preview_hash'], 'consent': True})
    assert replay.status_code == 202 and replay.json()['id'] == row['id']
    assert client.get('/api/setup/understanding/by-preview/' + preview['preview_hash']).json()['id'] == row['id']
    assert len(sent) == 1
    applied = client.post('/api/setup/understanding/' + row['id'] + '/apply', json={
        'version': result['version'], 'revision': body['revision'], 'names': ['异常复核']})
    assert applied.status_code == 200, applied.text
    saved = applied.json()
    assert client.get('/api/setup/draft').json()['configuration'] == saved['configuration']
    undone = client.post('/api/setup/understanding/' + row['id'] + '/undo', json={
        'version': saved['analysis']['version'], 'revision': saved['revision'], 'names': ['异常复核']})
    assert undone.status_code == 200, undone.text
    reuse = client.post('/api/setup/understanding/' + row['id'] + '/reuse', json={
        'version': undone.json()['analysis']['version'], 'revision': undone.json()['revision']})
    assert reuse.status_code == 201, reuse.text
    assert reuse.json()['contract_version'] == 4 and reuse.json()['receipts'] == []
    assert len(sent) == 1


def test_v4_budget_rejects_before_job_or_paid_call_and_requires_nonce(client):
    body, _ = long_request(client)
    assert client.post('/api/setup/understanding/preview', json={k:v for k,v in body.items() if k != 'request_id'}).status_code == 422
    response = client.post('/api/setup/understanding/preview', json=body)
    assert response.status_code == 409 and '预算' in response.json()['detail']
    assert '50,000' in response.json()['detail'] and '未发送' in response.json()['detail']
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
        assert db.scalar(select(func.count()).select_from(SetupAnalysis)) == 0
        assert not db.scalar(select(Job).where(Job.kind == 'cloud_budget'))


@pytest.mark.parametrize('damage', ['omit', 'policy', 'thinking'])
def test_v4_self_signed_damage_fails_before_wire(client, monkeypatch, damage):
    body, _ = long_request(client)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 1000000)
    sent, _ = wire(monkeypatch); row, _ = start(client, body)
    with Session(engine()) as db:
        record = db.get(SetupAnalysis, row['id']); req = copy.deepcopy(record.data['request'])
        if damage == 'omit': req['chunks'][0].pop()
        elif damage == 'policy': req['generation_contract']['input_policy']['max_input_bytes'] = 999999999
        else: req['generation_contract']['dispatch_options']['enable_thinking'] = True
        req['projection_hash'] = digest(req['chunks'][0])
        req['generation_contract_hash'] = digest(req['generation_contract'])
        req['preview_hash'] = digest({k:v for k,v in req.items() if k != 'preview_hash'})
        record.data = {**record.data, 'request': req, 'consent': {**record.data['consent'], 'preview_hash': req['preview_hash']}}
        db.commit()
    process(row)
    assert get(client, row)['status'] == 'failed' and not sent
    with Session(engine()) as db: assert not db.scalar(select(CloudCall))


def test_v4_timeout_retains_charge_and_forbids_retry_old_request(client, monkeypatch):
    body, _ = long_request(client)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 1000000)
    sent, _ = wire(monkeypatch, fail=httpx.ReadTimeout)
    row, preview = start(client, body); process(row); failed = get(client, row)
    assert failed['status'] == 'failed' and failed['recovery']['mode'] == 'new_preview_required'
    retry = client.post('/api/setup/understanding/' + row['id'] + '/retry', json={'version': failed['version']})
    assert retry.status_code == 409 and len(sent) == 1
    with Session(engine()) as db:
        call = db.scalar(select(CloudCall))
        assert call.charged_tokens == call.reserved_tokens == preview['input_summary']['token_bound']


def test_v4_paid_invalid_answer_local_recheck_never_resends(client, monkeypatch):
    body, _ = long_request(client)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 1000000)
    sent, _ = wire(monkeypatch, invalid=True)
    row, _ = start(client, body); process(row); failed = get(client, row)
    assert failed['status'] == 'failed' and failed['recovery']['mode'] == 'local_only'
    with Session(engine()) as db: raw = copy.deepcopy(db.get(SetupAnalysis, row['id']).data['raw_response'])
    retry = client.post('/api/setup/understanding/' + row['id'] + '/retry', json={'version': failed['version']})
    assert retry.status_code == 200
    process_latest(retry.json()); assert len(sent) == 1
    with Session(engine()) as db:
        assert db.get(SetupAnalysis, row['id']).data['raw_response'] == raw
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 1


@pytest.mark.parametrize('thinking', ['disabled', 'enabled', None])
def test_v4_deepseek_requires_explicit_verified_text_profile(client, monkeypatch, thinking):
    body, _ = long_request(client)
    value = json.loads(Path('examples/qwen.json').read_text())
    value['name'] = 'deepseek_full_report_check'
    value['execution'].update(provider='deepseek', model='deepseek-flash', secret_ref='DEEPSEEK_API_KEY')
    if thinking is not None: value['execution']['thinking_mode'] = thinking
    cap = publish(client, value); body['model_id'] = cap['id']
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 1000000)
    sent, options = wire(monkeypatch)
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setattr(settings(), 'deepseek_api_key', SecretStr('synthetic-nonlive-deepseek'))
    if thinking != 'disabled':
        blocked = client.post('/api/setup/understanding/preview', json=body)
        assert blocked.status_code == 413 and '50000' in blocked.json()['detail']
        assert not sent
        with Session(engine()) as db: assert not db.scalar(select(CloudCall))
        return
    row, preview = start(client, body); process(row); result = get(client, row)
    assert result['status'] == 'review', result['error']
    assert len(sent) == 1 and options[0]['timeout'] == 180
    assert sent[0]['thinking'] == {'type': 'disabled'} and 'enable_thinking' not in sent[0]
    assert preview['generation_contract']['dispatch_options']['model'] == 'deepseek-flash'
    with Session(engine()) as db:
        call = db.scalar(select(CloudCall))
        assert call.reserved_tokens == preview['input_summary']['token_bound']
        assert call.data['dispatch_options_hash'] == digest(preview['generation_contract']['dispatch_options'])


def test_v4_saved_paid_answer_can_expand_exact_source_locally_and_reuse_visible_audit(client, monkeypatch):
    import generic_agents.citation_expansion as expansion
    body, _ = long_request(client)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 1000000)
    sent, _ = wire(monkeypatch, abbreviate=True)
    real_expansion=expansion.expand_citations
    monkeypatch.setattr(expansion,'expand_citations',lambda value,blocks:(copy.deepcopy(value),[]))
    row, _ = start(client,body);process(row);failed=get(client,row)
    assert failed['status']=='failed' and failed['recovery']['mode']=='local_only'
    with Session(engine()) as db:
        raw=copy.deepcopy(db.get(SetupAnalysis,row['id']).data['raw_response'])
        call=db.scalar(select(CloudCall));ledger_before=(call.status,call.reserved_tokens,call.charged_tokens,copy.deepcopy(call.data))
    monkeypatch.setattr(expansion,'expand_citations',real_expansion)
    retry=client.post('/api/setup/understanding/'+row['id']+'/retry',json={'version':failed['version']})
    assert retry.status_code==200
    process_latest(retry.json());reviewed=get(client,row)
    assert reviewed['status']=='review',reviewed['error']
    assert len(reviewed['citation_normalizations'])==1 and len(sent)==1
    assert reviewed['citation_normalizations'][0]['original_quote']=='结尾约束：不得自动启动无人机……仍须人工确认。'
    assert reviewed['result']['businesses'][0]['citations'][0]['quote']=='结尾约束：不得自动启动无人机，仍须人工确认。'
    with Session(engine()) as db:
        assert db.get(SetupAnalysis,row['id']).data['raw_response']==raw
        call=db.scalar(select(CloudCall))
        assert (call.status,call.reserved_tokens,call.charged_tokens,call.data)==ledger_before
        assert db.scalar(select(func.count()).select_from(CloudCall))==1
    applied=client.post('/api/setup/understanding/'+row['id']+'/apply',json={
        'version':reviewed['version'],'revision':body['revision'],'names':['异常复核']})
    assert applied.status_code==200,applied.text
    saved=applied.json()
    undone=client.post('/api/setup/understanding/'+row['id']+'/undo',json={
        'version':saved['analysis']['version'],'revision':saved['revision'],'names':['异常复核']})
    assert undone.status_code==200
    reused=client.post('/api/setup/understanding/'+row['id']+'/reuse',json={
        'version':undone.json()['analysis']['version'],'revision':undone.json()['revision']})
    assert reused.status_code==201,reused.text
    assert reused.json()['citation_normalizations']==reviewed['citation_normalizations'] and len(sent)==1
