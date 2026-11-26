import copy
from types import SimpleNamespace

from fastapi.testclient import TestClient

from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.instance_compiler import flow_issues
from generic_agents.api import app
from generic_agents.models import SetupMaterial, Membership, User, Workspace
from generic_agents.security import password_hash
from generic_agents.worker import process_once
from generic_agents.setup_business_candidates import (
    _clear_stale_material_confirmations, _resolve_unchanged_flows, validate_bundle,
)


def material(client, text='第一段：进行日常检查。\n第二段：记录处理结果。\n'):
    reply = client.post('/api/setup/materials', files={'file': ('来源.txt', text.encode())})
    assert reply.status_code == 201
    row = reply.json()
    assert process_once(row['job_id'])
    return client.get('/api/setup/materials/' + row['id']).json()


def bundle(row, *, source_text='第一段：进行日常检查。', quote='进行日常检查'):
    return {'schema': 'setup-business-candidates-v1', 'origin': 'local_summary',
        'material': {key: row[key] for key in ('id', 'version', 'hash', 'engine', 'status')},
        'candidates': [{'key': 'patrol', 'name': '日常检查', 'trigger': '按计划', 'output': '检查记录',
            'inference_reason': '根据资料整理', 'sources': [{'source_text': source_text, 'quote': quote}],
            'steps': [{'key': 'observe', 'name': '收集观测', 'purpose': '检查对象', 'agent': '多模感知智能体',
                'role': '', 'output': '观测记录', 'condition': '', 'next_key': 'auto', 'else_key': 'end',
                'inference_reason': '根据资料提出建议', 'sources': []}]}]}


def add_flow(configuration, preview):
    value = copy.deepcopy(configuration)
    candidate = preview['candidates'][0]
    step = candidate['steps'][0]
    value['businesses'] = [*value.get('businesses', []), candidate['name']]
    value.setdefault('flows', {})[candidate['name']] = {
        'candidateKey': candidate['key'], 'trigger': candidate['trigger'], 'output': candidate['output'],
        'inferenceReason': candidate['inference_reason'], 'citations': candidate['citations'],
        'candidateUnresolvedSources': candidate['unresolved_sources'],
        'confirmedAt': None, 'confirmedSignature': None,
        'steps': [{'candidateKey': step['key'], 'id': 'step-1', 'name': step['name'],
            'purpose': step['purpose'], 'agent': step['agent'], 'role': step['role'],
            'output': step['output'], 'condition': step['condition'], 'nextId': step['next_key'],
            'elseId': step['else_key'], 'inferenceReason': step['inference_reason'],
            'citations': step['citations'], 'candidateUnresolvedSources': step['unresolved_sources']}]}
    return value


def test_preview_and_append_are_workspace_scoped_and_versioned(client):
    row = material(client)
    proposal = bundle(row)
    before = client.get('/api/setup/draft').json()
    preview = client.post('/api/setup/candidates/preview', json=proposal)
    assert preview.status_code == 200, preview.text
    item = preview.json()['candidates'][0]
    assert item['citations'] == [{'material_id': row['id'], 'block_id': row['blocks'][0]['id'], 'quote': '进行日常检查'}]
    assert item['unresolved_sources'] == []
    assert client.get('/api/setup/draft').json()['revision'] == before['revision']
    appended = client.post('/api/setup/candidates', json={'revision': before['revision'], 'bundle': proposal})
    assert appended.status_code == 200
    saved = appended.json()
    assert saved['configuration']['businessCandidates']['candidates'] == proposal['candidates']
    assert saved['configuration']['flows'] == {}
    assert client.get('/api/setup/draft').json() == saved
    assert client.post('/api/setup/candidates', json={'revision': before['revision'], 'bundle': proposal}).status_code == 409
    assert client.post('/api/setup/candidates', json={'revision': saved['revision'], 'bundle': proposal}).status_code == 409
    stale = copy.deepcopy(proposal); stale['material']['version'] += 1
    assert client.post('/api/setup/candidates/preview', json=stale).status_code == 409
    edited = copy.deepcopy(saved['configuration'])
    edited['businessCandidates']['candidates'][0]['sources'] = []
    assert client.put('/api/setup/draft', json={'revision': saved['revision'], 'configuration': edited}).status_code == 409


def test_pending_sources_cannot_be_silently_dropped_and_can_be_rechecked(client, account):
    row = material(client)
    # Initial extraction misses a genuine second line; the original remains intact.
    with Session(engine()) as db:
        source = db.get(SetupMaterial, row['id'])
        full = copy.deepcopy(source.data['blocks'])
        source.data = {**source.data, 'blocks': full[:1]}; source.status = 'needs_input'; source.version += 1
        db.commit()
    row = client.get('/api/setup/materials/' + row['id']).json()
    proposal = bundle(row, source_text='第二段：记录处理结果。', quote='记录处理结果')
    initial = client.get('/api/setup/draft').json()
    saved = client.post('/api/setup/candidates', json={'revision': initial['revision'], 'bundle': proposal}).json()
    preview_reply = client.post('/api/setup/candidates/preview', json=proposal)
    assert preview_reply.status_code == 200, preview_reply.text
    preview = preview_reply.json()
    assert preview['candidates'][0]['unresolved_sources'][0]['reason'] == 'not_found'
    config = add_flow(saved['configuration'], preview)
    tampered = copy.deepcopy(config); tampered['flows']['日常检查']['candidateUnresolvedSources'] = []
    assert client.put('/api/setup/draft', json={'revision': saved['revision'], 'configuration': tampered}).status_code == 409
    reply = client.put('/api/setup/draft', json={'revision': saved['revision'], 'configuration': config})
    assert reply.status_code == 200, reply.text
    saved = reply.json()
    with Session(engine()) as db:
        issues = flow_issues(db, SimpleNamespace(workspace_id=account[2]), saved['configuration'],
                             saved['configuration']['flows']['日常检查'], require_signature=False)
        assert any('待核原文' in issue for issue in issues)
    dropped = copy.deepcopy(saved['configuration']); dropped['flows']['日常检查']['candidateUnresolvedSources'] = []
    assert client.put('/api/setup/draft', json={'revision': saved['revision'], 'configuration': dropped}).status_code == 409
    changed = copy.deepcopy(saved['configuration']); changed['instanceProfile']['name'] = '人工编辑保留'
    edited = client.put('/api/setup/draft', json={'revision': saved['revision'], 'configuration': changed})
    assert edited.status_code == 200, edited.text
    assert client.post('/api/setup/candidates/revalidate', json={'revision': saved['revision']}).status_code == 409
    saved = edited.json()
    # Reparsing the same file makes the text available; rebind, then explicitly
    # replace the pending source with a server-verified current citation.
    with Session(engine()) as db:
        source = db.get(SetupMaterial, row['id'])
        source.data = {**source.data, 'blocks': full}; source.version += 1
        db.commit()
    unrelated_quote = copy.deepcopy(saved['configuration'])
    flow = unrelated_quote['flows']['日常检查']
    flow['candidateUnresolvedSources'] = []
    flow['citations'] = [{'material_id': row['id'], 'block_id': full[1]['id'], 'quote': '处理'}]
    assert client.put('/api/setup/draft', json={'revision': saved['revision'], 'configuration': unrelated_quote}).status_code == 409
    rechecked = client.post('/api/setup/candidates/revalidate', json={'revision': saved['revision']})
    assert rechecked.status_code == 200, rechecked.text
    draft, preview = rechecked.json()['draft'], rechecked.json()['preview']
    assert draft['configuration']['instanceProfile']['name'] == '人工编辑保留'
    assert preview['candidates'][0]['unresolved_sources'] == []
    assert draft['configuration']['flows']['日常检查']['candidateUnresolvedSources'] == []
    assert draft['configuration']['flows']['日常检查']['citations'] == preview['candidates'][0]['citations']
    assert any(history['configuration'].get('flows', {}).get('日常检查', {}).get('candidateUnresolvedSources')
               for history in client.get('/api/setup/draft/history').json())
    update = copy.deepcopy(draft['configuration'])
    flow = update['flows']['日常检查']
    flow['citations'] = preview['candidates'][0]['citations']
    flow['candidateUnresolvedSources'] = []
    assert client.put('/api/setup/draft', json={'revision': draft['revision'], 'configuration': update}).status_code == 200


def test_stale_material_metadata_is_never_silently_overwritten(client):
    row = material(client)
    draft = client.get('/api/setup/draft').json()
    config = {'version': 1, 'resources': [], 'connections': [], 'materials': [row],
              'instanceProfile': {}, 'businesses': [], 'flows': {}}
    saved = client.put('/api/setup/draft', json={'revision': draft['revision'], 'configuration': config}).json()
    with Session(engine()) as db:
        source = db.get(SetupMaterial, row['id'])
        source.data = {**source.data, 'note': '另一人已更新'}; source.version += 1
        db.commit()
    stale = copy.deepcopy(saved['configuration'])
    stale['instanceProfile']['name'] = '新名称'
    assert client.put('/api/setup/draft', json={'revision': saved['revision'], 'configuration': stale}).status_code == 409
    with Session(engine()) as db:
        assert db.get(SetupMaterial, row['id']).data['note'] == '另一人已更新'
    assert client.get('/api/setup/draft').json()['revision'] == saved['revision']


def test_material_version_change_with_unchanged_metadata_keeps_draft_edit(client):
    row = material(client)
    draft = client.get('/api/setup/draft').json()
    config = {'version': 1, 'resources': [], 'connections': [], 'materials': [row],
              'instanceProfile': {}, 'businesses': [], 'flows': {}}
    saved = client.put('/api/setup/draft', json={'revision': draft['revision'], 'configuration': config}).json()
    with Session(engine()) as db:
        source = db.get(SetupMaterial, row['id'])
        source.version += 1
        current_version = source.version
        db.commit()
    update = copy.deepcopy(saved['configuration']); update['instanceProfile']['name'] = '新名称'
    reply = client.put('/api/setup/draft', json={'revision': saved['revision'], 'configuration': update})
    assert reply.status_code == 200, reply.text
    assert reply.json()['configuration']['materials'][0]['version'] == current_version


def test_candidate_material_changed_after_preview_cannot_be_appended(client):
    row = material(client)
    proposal = bundle(row)
    draft = client.get('/api/setup/draft').json()
    assert client.post('/api/setup/candidates/preview', json=proposal).status_code == 200
    with Session(engine()) as db:
        source = db.get(SetupMaterial, row['id']); source.version += 1; db.commit()
    response = client.post('/api/setup/candidates', json={'revision': draft['revision'], 'bundle': proposal})
    assert response.status_code == 409
    assert client.get('/api/setup/draft').json() == draft


def test_problematic_business_key_and_candidate_origin_are_not_silently_accepted(client):
    row = material(client)
    proposal = bundle(row); proposal['candidates'][0]['name'] = '__proto__'
    assert client.post('/api/setup/candidates/preview', json=proposal).status_code == 422
    proposal = bundle(row); duplicate = copy.deepcopy(proposal['candidates'][0])
    proposal['candidates'][0]['name'] = 'ＡＢＣ'; duplicate.update(key='other', name='abc')
    proposal['candidates'].append(duplicate)
    assert client.post('/api/setup/candidates/preview', json=proposal).status_code == 422


def test_candidate_never_overwrites_existing_name_without_flow(client):
    row = material(client)
    proposal = bundle(row)
    draft = client.get('/api/setup/draft').json()
    config = {'version': 1, 'resources': [], 'connections': [], 'materials': [],
              'instanceProfile': {}, 'businesses': ['日常检查'], 'flows': {}}
    saved = client.put('/api/setup/draft', json={'revision': draft['revision'], 'configuration': config}).json()
    appended = client.post('/api/setup/candidates', json={'revision': saved['revision'], 'bundle': proposal}).json()
    preview = client.post('/api/setup/candidates/preview', json=proposal).json()
    response = client.put('/api/setup/draft', json={'revision': appended['revision'],
        'configuration': add_flow(appended['configuration'], preview)})
    assert response.status_code == 409
    assert client.get('/api/setup/draft').json()['configuration']['businesses'] == ['日常检查']


def test_user_can_rename_candidate_flow_but_cannot_drop_unresolved_source(client):
    row = material(client)
    proposal = bundle(row, source_text='不存在的原文', quote='不存在的原文')
    draft = client.get('/api/setup/draft').json()
    saved = client.post('/api/setup/candidates', json={'revision': draft['revision'], 'bundle': proposal}).json()
    preview = client.post('/api/setup/candidates/preview', json=proposal).json()
    configured = client.put('/api/setup/draft', json={'revision': saved['revision'],
        'configuration': add_flow(saved['configuration'], preview)}).json()
    renamed = copy.deepcopy(configured['configuration'])
    renamed['flows']['新的业务名称'] = renamed['flows'].pop('日常检查')
    renamed['businesses'] = ['新的业务名称']
    renamed['businessAliases'] = {'新的业务名称': '日常检查'}
    forged = copy.deepcopy(renamed); forged['flows']['新的业务名称']['candidateUnresolvedSources'] = []
    assert client.put('/api/setup/draft', json={'revision': configured['revision'], 'configuration': forged}).status_code == 409
    assert client.put('/api/setup/draft', json={'revision': configured['revision'], 'configuration': renamed}).status_code == 200


def test_rename_cannot_launder_pending_but_explicit_deletion_is_allowed(client):
    row = material(client)
    proposal = bundle(row, source_text='不存在的原文', quote='不存在的原文')
    draft = client.get('/api/setup/draft').json()
    saved = client.post('/api/setup/candidates', json={'revision': draft['revision'], 'bundle': proposal}).json()
    preview = client.post('/api/setup/candidates/preview', json=proposal).json()
    configured = client.put('/api/setup/draft', json={'revision': saved['revision'],
        'configuration': add_flow(saved['configuration'], preview)}).json()
    # A rename combined with removing candidate identity and pending basis is
    # indistinguishable from laundering the same suggestion as a manual flow.
    for keep_alias in (False, True):
        forged = copy.deepcopy(configured['configuration'])
        flow = forged['flows'].pop('日常检查'); flow.pop('candidateKey')
        flow['candidateUnresolvedSources'] = []
        forged['flows']['更名业务'] = flow; forged['businesses'] = ['更名业务']
        if keep_alias: forged['businessAliases'] = {'更名业务': '日常检查'}
        assert client.put('/api/setup/draft', json={'revision': configured['revision'], 'configuration': forged}).status_code == 409
    deleted = copy.deepcopy(configured['configuration']); deleted['flows'] = {}; deleted['businesses'] = []
    assert client.put('/api/setup/draft', json={'revision': configured['revision'], 'configuration': deleted}).status_code == 200


def test_replaced_step_cannot_launder_pending_but_deletion_is_allowed(client):
    row = material(client)
    proposal = bundle(row)
    proposal['candidates'][0]['sources'] = []
    proposal['candidates'][0]['steps'][0]['sources'] = [{'source_text': '不存在的原文', 'quote': '不存在的原文'}]
    draft = client.get('/api/setup/draft').json()
    saved = client.post('/api/setup/candidates', json={'revision': draft['revision'], 'bundle': proposal}).json()
    preview = client.post('/api/setup/candidates/preview', json=proposal).json()
    configured = client.put('/api/setup/draft', json={'revision': saved['revision'],
        'configuration': add_flow(saved['configuration'], preview)}).json()
    replaced = copy.deepcopy(configured['configuration'])
    step = replaced['flows']['日常检查']['steps'][0]
    step['id'] = 'new-step'; step.pop('candidateKey'); step['candidateUnresolvedSources'] = []
    assert client.put('/api/setup/draft', json={'revision': configured['revision'], 'configuration': replaced}).status_code == 409
    deleted = copy.deepcopy(configured['configuration']); deleted['flows']['日常检查']['steps'] = []
    assert client.put('/api/setup/draft', json={'revision': configured['revision'], 'configuration': deleted}).status_code == 200


def test_revalidation_never_modifies_protected_flow_markers():
    source = {'source_text': '合成原句', 'quote': '原句'}
    candidate = {'key': 'patrol', 'name': '日常检查', 'trigger': '按计划', 'output': '记录',
        'inference_reason': '', 'sources': [source], 'steps': []}
    material = {'id': 'm', 'version': 1, 'hash': 'a' * 64, 'engine': 'test',
                'blocks': [{'id': 'b1', 'text': '合成原句'}]}
    pending = {'material_id': 'm', **source, 'reason': 'not_found'}
    for marker in ('reportSource', 'fullSemanticSource'):
        flow = {'candidateKey': 'patrol', 'trigger': '按计划', 'output': '记录',
                'candidateUnresolvedSources': [pending], 'steps': [], marker: {'synthetic': True}}
        value = {'flows': {'日常检查': flow}}
        before = copy.deepcopy(value)
        _resolve_unchanged_flows(value, {'candidates': [candidate]}, material)
        assert value == before


def test_material_change_only_clears_relevant_stale_approvals():
    old = {'materials': [{'id': 'm', 'version': 1}], 'flows': {
        'related': {'steps': [{'sourceId': 'm'}], 'confirmedAt': 'yes', 'confirmedSignature': 'yes'},
        'unrelated': {'steps': [{'sourceId': 'n'}], 'confirmedAt': 'yes', 'confirmedSignature': 'yes'}}}
    b = validate_bundle(bundle({'id': 'm', 'version': 1, 'hash': 'a' * 64, 'engine': 'test', 'status': 'parsed'}))
    current = {'id': 'm', 'version': 2, 'engine': 'test'}
    changed = copy.deepcopy(old); _clear_stale_material_confirmations(old, changed, current, b)
    assert changed['flows']['related']['confirmedAt'] is None
    assert changed['flows']['unrelated'] == old['flows']['unrelated']
    # A flow already confirmed against the new saved material version is not
    # invalidated merely because the separate candidate binding was older.
    old['materials'][0]['version'] = 2
    unchanged = copy.deepcopy(old); _clear_stale_material_confirmations(old, unchanged, current, b)
    assert unchanged == old


def test_revalidation_respects_existing_source_protection(client, monkeypatch):
    row = material(client)
    with Session(engine()) as db:
        source = db.get(SetupMaterial, row['id']); full = copy.deepcopy(source.data['blocks'])
        source.data = {**source.data, 'blocks': full[:1]}; source.status = 'needs_input'; source.version += 1; db.commit()
    row = client.get('/api/setup/materials/' + row['id']).json()
    proposal = bundle(row, source_text='第二段：记录处理结果。', quote='记录处理结果')
    draft = client.get('/api/setup/draft').json()
    saved = client.post('/api/setup/candidates', json={'revision': draft['revision'], 'bundle': proposal}).json()
    preview = client.post('/api/setup/candidates/preview', json=proposal).json()
    configured = client.put('/api/setup/draft', json={'revision': saved['revision'],
        'configuration': add_flow(saved['configuration'], preview)}).json()
    with Session(engine()) as db:
        source = db.get(SetupMaterial, row['id']); source.data = {**source.data, 'blocks': full}
        source.version += 1; db.commit()
    from generic_agents import report_configuration_handoff as guard
    def reject(_db, _p, _old, _new):
        from fastapi import HTTPException
        raise HTTPException(409, 'protected synthetic source')
    monkeypatch.setattr(guard, 'protect_configuration_sources', reject)
    result = client.post('/api/setup/candidates/revalidate', json={'revision': configured['revision']})
    assert result.status_code == 409
    assert client.get('/api/setup/draft').json() == configured


def test_other_workspace_cannot_preview_or_append_material(client):
    row = material(client)
    proposal = bundle(row)
    with Session(engine()) as db:
        user = User(username='separate_candidate_user', password_hash=password_hash('candidate-password'))
        workspace = Workspace(name='另一工作区')
        db.add_all([user, workspace]); db.flush()
        workspace_id = workspace.id
        db.add(Membership(user_id=user.id, workspace_id=workspace.id, role='admin')); db.commit()
    with TestClient(app) as other:
        login = other.post('/api/login', json={'username': 'separate_candidate_user', 'password': 'candidate-password'})
        assert login.status_code == 200
        other.headers.update({'X-CSRF-Token': login.json()['csrf_token'], 'X-Workspace-ID': workspace_id})
        revision = other.get('/api/setup/draft').json()['revision']
        assert other.post('/api/setup/candidates/preview', json=proposal).status_code == 404
        assert other.post('/api/setup/candidates', json={'revision': revision, 'bundle': proposal}).status_code == 404
        assert other.get('/api/setup/draft').json()['configuration'] == {}
