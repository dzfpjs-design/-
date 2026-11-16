"""New-request context projection only; no SQL, transport, model or child."""
import ast
import copy
import hashlib
import json
import re
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import setup_understanding as understanding
from generic_agents.contracts import digest


ROOT = Path(__file__).resolve().parents[1]
NONCE = UUID('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('pure context tests forbid databases, services, models and children')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(understanding, 'cloud_json', forbidden)
    import sqlalchemy
    monkeypatch.setattr(sqlalchemy, 'create_engine', forbidden)


def ref(identity, quote=None):
    return {'material_id': identity, 'block_id': 'p1', 'quote': quote or '原文' + identity}


def configuration(*, alias=False):
    old, current = ('旧业务', '新业务') if alias else ('业务', '业务')
    value = {'version': 1, 'instanceProfile': {'name': '合成实例', 'domain': '资料核查', 'goal': '保留人稿'},
             'businesses': [current], 'businessAliases': {current: old} if alias else {},
             'resources': [], 'connections': [], 'materials': [], 'private_extra': '不得发送',
             'flows': {old: {'trigger': '人工发起', 'output': '人工核查结果',
                'citations': [ref('B')], 'inferenceReason': '保留业务级说明',
                'private_extra': '业务秘密不得发送', 'confirmedSignature': 'not a cloud field',
                'steps': [
                    {'id': 'internal-1', 'name': '先核查', 'purpose': '原有人工目的', 'agent': '人工确认',
                     'role': '核查员', 'output': '记录一', 'sourceId': 'A',
                     'citations': [ref('A'), ref('C')], 'inferenceReason': '保留步骤说明',
                     'citation': '原文A；未选C隐藏摘录', 'condition': '需要继续',
                     'nextId': 'internal-2', 'elseId': 'end', 'password': 'synthetic-hidden'},
                    {'id': 'internal-2', 'name': '后记录', 'purpose': '原有记录目的', 'agent': '人工确认',
                     'role': '记录员', 'output': '记录二', 'citation': '用户手写的职责说明',
                     'nextId': 'end', 'elseId': 'end'},
                ]}}}
    return value


def selected_blocks(*identities):
    return [{'material_id': identity, 'block_id': 'p1', 'text': '完整原文' + identity + '。',
             'location': {'body_index': 0}} for identity in identities]


def prepare(monkeypatch, config, *, version=4, selected=('A', 'B'), model='qwen-plus'):
    row = SimpleNamespace(data=config, version=7)
    cap = SimpleNamespace(id='model-id', content_hash='model-hash', data={
        'execution': {'provider': 'qwen', 'model': model, 'max_tokens': 2048},
        'data_policy': {'redact_fields': []}})
    materials = {identity: SimpleNamespace(id=identity, name=identity + '.txt', sha256=identity * 64,
        version=1, archived=False, status='parsed', data={'blocks': [
            {'id': 'p1', 'text': '完整原文' + identity + '。', 'location': {'body_index': 0}}]})
        for identity in selected}
    monkeypatch.setattr(understanding, 'draft', lambda db, p, lock: row)
    monkeypatch.setattr(understanding, 'check_version', lambda record, version: None)
    monkeypatch.setattr(understanding, 'model', lambda db, p, identity: cap)
    monkeypatch.setattr(understanding, 'scoped', lambda db, kind, identity, p, lock: materials[identity])
    monkeypatch.setattr(understanding, 'frozen_material', lambda db, p, m: {
        'id': m.id, 'blocks': copy.deepcopy(m.data['blocks'])})
    monkeypatch.setattr(understanding, 'preview_budget', lambda *args, **kwargs: None)
    from generic_agents import material_ocr
    monkeypatch.setattr(material_ocr, 'ensure_publishable', lambda *args: None)
    body = understanding.PreviewRequest(revision=7, model_id=cap.id, material_ids=list(selected),
        instruction='只修改一个角色，其余保留', contract_version=version,
        request_id=NONCE if version >= 3 else None)
    return understanding.prepare(object(), SimpleNamespace(workspace_id='workspace'), body)


@pytest.mark.parametrize('version', [4, 5])
def test_new_preview_keeps_aliased_human_flow_and_authorized_structured_basis(monkeypatch, version):
    config = configuration(alias=True); before = copy.deepcopy(config)
    request = prepare(monkeypatch, config, version=version)
    flow = request['context']['flows']['新业务']
    assert flow['trigger'] == '人工发起'
    assert flow['citations'] == [ref('B')]
    assert flow['inference_reason'] == '保留业务级说明'
    assert flow['omitted_citation_count'] == 0
    assert [s['name'] for s in flow['steps']] == ['先核查', '后记录']
    first, last = flow['steps']
    assert first['next_label'] == '后记录' and first['else_label'] == '结束流程'
    assert last['next_label'] == '结束流程'
    assert first['citations'] == [ref('A')] and first['omitted_citation_count'] == 1
    assert first['inference_reason'] == '保留步骤说明' and 'citation' not in first
    assert last['citation'] == '用户手写的职责说明'
    encoded = json.dumps(request['context'], ensure_ascii=False)
    for hidden in ('未选C隐藏摘录', '原文C', '不得发送', 'internal-1', 'sourceId', 'confirmedSignature', 'password'):
        assert hidden not in encoded
    assert config == before


@pytest.mark.parametrize('version', [4, 5])
def test_new_preview_accepts_complete_context_over_old_18kb_limit(monkeypatch, version):
    config = configuration(); flow = config['flows']['业务']
    flow['steps'][0]['purpose'] = '完整保留人工核查说明' * 1500
    before = copy.deepcopy(config)
    request = prepare(monkeypatch, config, version=version)
    assert len(json.dumps(request['context'], ensure_ascii=False).encode()) > 18000
    assert request['context']['flows']['业务']['steps'][0]['purpose'] == flow['steps'][0]['purpose']
    assert len(request['chunks']) == 1
    assert request['input_summary']['prepared_messages_bytes'] < request['input_summary']['max_input_bytes']
    assert request['first_dispatch_messages_hash']
    assert config == before


@pytest.mark.parametrize('version', [1, 2, 3])
def test_legacy_preview_retains_18kb_guard(monkeypatch, version):
    config = configuration(); config['flows']['业务']['steps'][0]['purpose'] = '完整人工说明' * 2000
    with pytest.raises(HTTPException) as caught: prepare(monkeypatch, config, version=version)
    assert caught.value.status_code == 413 and '18000' not in caught.value.detail
    assert '现有业务流程' in caught.value.detail


@pytest.mark.parametrize('version', [1, 2, 3])
def test_legacy_context_projection_remains_original_shape(monkeypatch, version):
    request = prepare(monkeypatch, configuration(alias=True), version=version)
    assert request['context']['flows']['新业务'] == {'trigger': '', 'output': '', 'steps': []}


def project(config, blocks):
    from generic_agents.workflow_context import workflow_context
    return workflow_context(config, blocks, scrub_text=understanding.scrub)


def test_expired_or_wrong_block_quotes_are_counted_without_leaking_text():
    config = configuration(); f = config['flows']['业务']
    f['citations'] = [ref('A', '不在原文的秘密摘录'), ref('A') | {'block_id': 'wrong'}, ref('B')]
    f['steps'][0]['citations'] = [ref('A'), ref('C')]
    before = copy.deepcopy(config)
    result = project(config, selected_blocks('A'))
    assert result['业务']['citations'] == [] and result['业务']['omitted_citation_count'] == 3
    assert result['业务']['steps'][0]['citations'] == [ref('A')]
    assert '秘密摘录' not in json.dumps(result, ensure_ascii=False)
    assert config == before


@pytest.mark.parametrize('selected,retained', [(('A',), True), (('B',), False)])
def test_legacy_source_quote_requires_selected_original(selected, retained):
    config = configuration(); step = config['flows']['业务']['steps'][0]
    step.pop('citations'); step['citation'] = '原文A的旧手写说明'
    value = project(config, selected_blocks(*selected))['业务']['steps'][0]
    assert ('citation' in value) == retained
    assert value['omitted_citation_count'] == (0 if retained else 1)
    if retained: assert value['citation'] == step['citation']


def test_no_selected_materials_keep_manual_notes_but_no_original_quotes():
    result = project(configuration(), [])['业务']
    assert result['omitted_citation_count'] == 1
    assert result['steps'][0]['omitted_citation_count'] == 2
    assert result['steps'][1]['citation'] == '用户手写的职责说明'
    assert all(not s['citations'] for s in result['steps'])


def test_project_scrubs_allowed_text_and_never_copies_private_extra_fields():
    config = configuration(); step = config['flows']['业务']['steps'][1]
    step['citation'] = '联系 person@example.com；password=synthetic-value'
    step['inferenceReason'] = '电话13800138000仅作合成隐私测试'
    result = project(config, selected_blocks('A', 'B'))
    text = json.dumps(result, ensure_ascii=False)
    assert 'person@example.com' not in text and 'synthetic-value' not in text and '13800138000' not in text
    assert '已移除' in text and 'private_extra' not in text and 'confirmedSignature' not in text


@pytest.mark.parametrize('model,repeat', [('qwen-plus', 60000), ('unverified-small-model', 2500)])
def test_full_prepared_message_policy_still_rejects_atomic_oversize(monkeypatch, model, repeat):
    config = configuration(); config['flows']['业务']['steps'][0]['purpose'] = '完整原有人稿' * repeat
    original = copy.deepcopy(config)
    with pytest.raises(HTTPException) as caught: prepare(monkeypatch, config, model=model)
    assert caught.value.status_code == 413
    assert '完整消息' in caught.value.detail or '上下文上限' in caught.value.detail
    assert config == original


def test_m50_frozen_messages_remain_exact_without_repreparing_old_context():
    path = ROOT / 'docs/evidence/m50-long-report/runtime/actual-analysis.json'
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '5e60c46953f961e78beade6ffe39a1fd6c0caf883ab5a33f955dde6fbf79d8e6'
    request = json.loads(raw)['data']['request']; before = copy.deepcopy(request)
    messages = understanding.messages_for(request, None, 0)
    execution = request['generation_contract']['dispatch_options']
    cap = SimpleNamespace(data={'execution': execution, 'data_policy': {'redact_fields': []}})
    prepared, _, images = understanding.prepare_cloud_json(cap, messages, understanding.ResultV2.model_json_schema())
    assert images == 0 and digest(prepared) == request['first_dispatch_messages_hash']
    assert request == before


def test_no_at_fastpath_preserves_historical_scrub_bytes_on_small_corpus():
    archive = ROOT / '.agentic-engineering/history/2026-10-16-before-understanding-context/src/generic_agents/setup_understanding.py'
    original = next(node for node in ast.parse(archive.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == 'scrub')
    scope = {'re': re}
    exec(compile(ast.Module(body=[original], type_ignores=[]), str(archive), 'exec'), scope)
    corpus = ['', '普通人工说明，保持原文。', '电话13800138000仅为合成输入',
              'password=synthetic-value；token:synthetic-value', 'sk-syntheticabcdefghij',
              'Bearer syntheticabcdefghij', '邮箱person@example.com，保留句号。',
              'a@invalid', 'a+b.c-d@example.org / other@sub.example.net',
              '上行\n下行无邮箱且不应改变\n']
    for text in corpus:
        assert understanding.scrub(text).encode() == scope['scrub'](text).encode()
