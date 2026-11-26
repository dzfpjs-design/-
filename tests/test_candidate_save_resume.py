"""Reproduce the stale local payload that the browser used to submit."""
import copy
from tests.test_setup_business_candidates import add_flow, bundle, material


def _stage(client, *, second=False, missing=False):
    source = material(client)
    proposal = bundle(source, source_text='未找到的原文', quote='未找到') if missing else bundle(source)
    if second:
        extra = copy.deepcopy(proposal['candidates'][0]['steps'][0])
        extra.update(key='verify', name='现场核实', output='核实结果')
        proposal['candidates'][0]['steps'].append(extra)
    initial = client.get('/api/setup/draft').json()
    staged = client.post('/api/setup/candidates', json={'revision': initial['revision'], 'bundle': proposal}).json()
    preview = client.post('/api/setup/candidates/preview', json=proposal).json()
    local = add_flow(staged['configuration'], preview)
    if second:
        extra = copy.deepcopy(local['flows']['日常检查']['steps'][0])
        extra.update(candidateKey='verify', id='step-2', name='现场核实', output='核实结果')
        local['flows']['日常检查']['steps'].append(extra)
    first = client.put('/api/setup/draft', json={'revision': staged['revision'], 'configuration': local})
    assert first.status_code == 200, first.text
    return local, first.json()


def test_join_then_edit_with_local_snapshot_preserves_verified_server_basis(client):
    local, saved = _stage(client)
    assert saved['configuration']['flows']['日常检查'].get('candidateBasisHash')
    changed = copy.deepcopy(local)
    changed['flows']['日常检查']['steps'][0]['purpose'] = '用户继续编辑的内容'
    second = client.put('/api/setup/draft', json={'revision': saved['revision'], 'configuration': changed})
    assert second.status_code == 200, second.text
    result = second.json()['configuration']['flows']['日常检查']
    assert result['steps'][0]['purpose'] == '用户继续编辑的内容'
    assert result['candidateBasisHash'] == saved['configuration']['flows']['日常检查']['candidateBasisHash']
    assert result['steps'][0]['candidateBasisHash'] == saved['configuration']['flows']['日常检查']['steps'][0]['candidateBasisHash']
    # A later edit originating from the same old in-memory snapshot is also
    # saved; user input remains authoritative, the server restores only proof.
    later = copy.deepcopy(changed)
    later['flows']['日常检查']['steps'][0]['output'] = '用户更新的产出'
    third = client.put('/api/setup/draft', json={'revision': second.json()['revision'], 'configuration': later})
    assert third.status_code == 200, third.text
    assert third.json()['configuration']['flows']['日常检查']['steps'][0]['output'] == '用户更新的产出'


def test_reorder_rename_and_explicit_delete_preserve_identity(client):
    local, saved = _stage(client, second=True)
    local['flows']['日常检查']['steps'].reverse()
    result = client.put('/api/setup/draft', json={'revision': saved['revision'], 'configuration': local})
    assert result.status_code == 200, result.text
    assert [step['id'] for step in result.json()['configuration']['flows']['日常检查']['steps']] == ['step-2', 'step-1']
    local['flows']['新业务'] = local['flows'].pop('日常检查')
    local['businesses'] = ['新业务']; local['businessAliases'] = {'新业务': '日常检查'}
    renamed = client.put('/api/setup/draft', json={'revision': result.json()['revision'], 'configuration': local})
    assert renamed.status_code == 200, renamed.text
    local['flows']['新业务']['steps'].pop()
    removed = client.put('/api/setup/draft', json={'revision': renamed.json()['revision'], 'configuration': local})
    assert removed.status_code == 200, removed.text
    assert len(removed.json()['configuration']['flows']['新业务']['steps']) == 1


def test_forged_identity_duplicate_ids_and_pending_source_removal_are_rejected(client):
    local, saved = _stage(client, second=True, missing=True)
    attempts=[]
    different=copy.deepcopy(local); different['flows']['日常检查']['candidateKey']='other'; attempts.append(different)
    different=copy.deepcopy(local); different['flows']['日常检查']['steps'][0]['candidateKey']='other'; attempts.append(different)
    different=copy.deepcopy(local); different['flows']['日常检查']['steps'][0]['candidateBasisHash']='forged'; attempts.append(different)
    different=copy.deepcopy(local); different['flows']['日常检查']['steps'][1]['id']='step-1'; attempts.append(different)
    different=copy.deepcopy(local); different['flows']['日常检查']['candidateUnresolvedSources']=[]; attempts.append(different)
    for changed in attempts:
        assert client.put('/api/setup/draft', json={'revision': saved['revision'], 'configuration': changed}).status_code in (409, 422)
    assert client.get('/api/setup/draft').json()['configuration'] == saved['configuration']


def test_stale_second_page_cannot_overwrite_concurrent_change(client):
    local, saved = _stage(client)
    current = copy.deepcopy(saved['configuration'])
    current.setdefault('instanceProfile', {})['goal'] = '另一页面的更改'
    other = client.put('/api/setup/draft', json={'revision': saved['revision'], 'configuration': current})
    assert other.status_code == 200, other.text
    local['flows']['日常检查']['steps'][0]['purpose'] = '本页未提交的更改'
    rejected = client.put('/api/setup/draft', json={'revision': saved['revision'], 'configuration': local})
    assert rejected.status_code == 409
    assert client.get('/api/setup/draft').json()['configuration']['instanceProfile']['goal'] == '另一页面的更改'
