"""Saved confirmation follows every source, without changing human edits."""
import copy
import json

import pytest

from generic_agents.flow_confirmation import synchronize_flow_confirmations
from generic_agents.instance_compiler import signature_value


def citation(identity):
    return {'material_id': identity, 'block_id': 'line-1', 'quote': '已保存的原文'}


def configuration():
    step = {'id': 'step-1', 'name': '核对记录', 'purpose': '保留人工修改',
            'agent': '人工确认', 'role': '业务负责人', 'output': '核对结论',
            'sourceId': 'a', 'citations': [citation('a')], 'condition': '',
            'nextId': 'end', 'elseId': 'end'}
    flow = {'trigger': '人工发起', 'output': '已核对记录', 'steps': [step]}
    value = {'version': 1, 'resources': [], 'connections': [],
             'instanceProfile': {'domain': '设备运维', 'goal': '核对资料'},
             'materials': [{'id': key, 'name': key + '.txt', 'hash': key * 64,
                            'version': 2, 'archived': False} for key in 'abc'],
             'businesses': ['复核', '记录', '业务依据'],
             'flows': {name: copy.deepcopy(flow) for name in ('复核', '记录', '业务依据')}}
    value['flows']['复核']['steps'][0]['citations'].append(citation('b'))
    value['flows']['业务依据']['citations'] = [citation('b')]
    value['flows']['业务依据']['inferenceReason'] = '人工核对原有规则'
    for current in value['flows'].values():
        current['confirmedSignature'] = json.dumps(signature_value(value, current), ensure_ascii=False)
        current['confirmedAt'] = '2026-10-16T08:00:00+00:00'
    return value


@pytest.mark.parametrize('change', ['archive', 'version', 'hash', 'remove'])
def test_only_secondary_and_business_level_dependents_lose_saved_confirmation(change):
    before = configuration()
    proposed = copy.deepcopy(before)
    b = next(row for row in proposed['materials'] if row['id'] == 'b')
    if change == 'archive': b['archived'] = True
    if change == 'version': b['version'] += 1
    if change == 'hash': b['hash'] = 'd' * 64
    if change == 'remove': proposed['materials'].remove(b)
    # A caller repeating the old stamps cannot preserve approval of the old basis.
    saved = synchronize_flow_confirmations(before, proposed)
    for name in ('复核', '业务依据'):
        assert saved['flows'][name]['confirmedAt'] is None
        assert saved['flows'][name]['confirmedSignature'] is None
        for key in proposed['flows'][name].keys() - {'confirmedAt', 'confirmedSignature'}:
            assert saved['flows'][name][key] == proposed['flows'][name][key]
    assert saved['flows']['记录'] == before['flows']['记录']
    assert before == configuration()  # no mutation of the saved previous version


def test_unrelated_source_does_not_invalidate_any_approval():
    before = configuration(); proposed = copy.deepcopy(before)
    proposed['materials'][2]['version'] += 1
    saved = synchronize_flow_confirmations(before, proposed)
    assert saved['flows'] == before['flows']


def test_restoring_a_source_does_not_resurrect_earlier_confirmation():
    before = configuration(); archived = copy.deepcopy(before)
    archived['materials'][1]['archived'] = True
    archived = synchronize_flow_confirmations(before, archived)
    restored = copy.deepcopy(archived)
    restored['materials'][1]['archived'] = False
    restored = synchronize_flow_confirmations(archived, restored)
    assert restored['flows']['复核']['confirmedSignature'] is None
    assert restored['flows']['业务依据']['confirmedSignature'] is None
    # A later, explicit review can confirm that saved version normally.
    reviewed = copy.deepcopy(restored)
    for name in ('复核', '业务依据'):
        flow = reviewed['flows'][name]
        flow['confirmedSignature'] = json.dumps(signature_value(reviewed, flow), ensure_ascii=False)
        flow['confirmedAt'] = '2026-10-16T08:05:00+00:00'
    assert synchronize_flow_confirmations(restored, reviewed)['flows'] == reviewed['flows']


def test_invalid_or_old_incomplete_stamp_is_cleared_without_discarding_edits():
    before = configuration(); proposed = copy.deepcopy(before)
    proposed['flows']['复核']['confirmedSignature'] = 'not valid json'
    proposed['flows']['复核']['steps'][0]['role'] = '用户新指定角色'
    saved = synchronize_flow_confirmations(before, proposed)
    assert saved['flows']['复核']['confirmedSignature'] is None
    assert saved['flows']['复核']['steps'][0]['role'] == '用户新指定角色'
    assert saved['flows']['记录'] == before['flows']['记录']


def test_explicit_material_change_signal_is_scoped_to_dependencies():
    before = configuration()
    saved = synchronize_flow_confirmations(before, before, changed_sources={'b'})
    assert saved['flows']['复核']['confirmedAt'] is None
    assert saved['flows']['业务依据']['confirmedAt'] is None
    assert saved['flows']['记录'] == before['flows']['记录']


def test_business_evidence_is_retained_when_a_candidate_is_applied():
    from generic_agents.setup_understanding import apply_configuration
    before = configuration()
    step = {'key': 'check', 'name': '核对记录', 'purpose': '核对资料',
            'agent': '人工确认', 'role': '业务负责人', 'output': '核对结论',
            'next_key': 'end', 'else_key': 'end', 'condition': '',
            'citations': [citation('a')], 'inference_reason': ''}
    business = {'name': '新业务', 'original_name': '', 'operation': 'upsert',
                'trigger': '人工发起', 'output': '核对记录', 'steps': [step],
                'citations': [citation('b')], 'inference_reason': '保留来源解释'}
    result = {'businesses': [business], 'domain': '设备运维', 'objects': []}
    saved = apply_configuration(before, result, ['新业务'])
    flow = saved['flows']['新业务']
    assert flow['citations'] == business['citations']
    assert flow['inferenceReason'] == business['inference_reason']
    assert flow['confirmedSignature'] is None
    business['citations'][0]['quote'] = '不能改动已应用配置'
    assert flow['citations'][0]['quote'] == '已保存的原文'


@pytest.mark.parametrize('domain,invalidated', [('新领域', True), ('设备运维', False)])
def test_domain_only_application_synchronizes_existing_confirmations(domain, invalidated):
    from generic_agents.setup_understanding import apply_configuration
    before = configuration(); original = copy.deepcopy(before)
    result = {'businesses': [], 'domain': domain, 'objects': []}
    saved = apply_configuration(before, result, [], accept_domain=True)
    assert saved['instanceProfile']['domain'] == domain
    for name, previous in before['flows'].items():
        current = saved['flows'][name]
        if invalidated:
            assert current['confirmedSignature'] is None and current['confirmedAt'] is None
        else:
            assert current == previous
        assert {k: v for k, v in current.items() if k not in {'confirmedSignature', 'confirmedAt'}} == {
            k: v for k, v in previous.items() if k not in {'confirmedSignature', 'confirmedAt'}}
    assert before == original
