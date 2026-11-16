"""Pure selector boundaries using synthetic public row shapes, never DB proof.

The selector must preserve exact duplicate matches for enqueue's independent
conflict guard. It neither grants authority nor validates an actual receipt.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from generic_agents import threshold_shadow_execution_v151 as execution
from generic_agents.threshold_shadow_store_v151 import ShadowInventoryError


WORKSPACE = '15130000-0000-4000-8000-000000000001'
INSTANCE = '15130000-0000-4000-8000-000000000002'
RELEASE = '15130000-0000-4000-8000-000000000003'
OTHER_INSTANCE = '15130000-0000-4000-8000-000000000004'
OTHER_RELEASE = '15130000-0000-4000-8000-000000000005'
ENTITY = '15130000-0000-4000-8000-000000000006'
SOURCE = '15130000-0000-4000-8000-000000000007'
BASELINE = '15130000-0000-4000-8000-000000000008'
CANDIDATE = '15130000-0000-4000-8000-000000000009'
OTHER = '15130000-0000-4000-8000-00000000000a'


def grant(identity, **changes):
    scope = {'workspace_id': WORKSPACE, 'instance_id': INSTANCE, 'release_id': RELEASE,
        'entity_id': ENTITY, 'source_id': SOURCE, 'baseline_id': BASELINE,
        'baseline_hash': 'a' * 64, 'candidate_capability_id': CANDIDATE,
        'candidate_hash': 'b' * 64, 'source_hash': 'c' * 64,
        'rule_id': 'original_count', 'field': 'count', 'field_label': '数量',
        'unit': '个', 'operator': 'gt', 'baseline_threshold': 20, 'candidate_threshold': 18,
        'instance_name': '合成实例甲', 'release_name': '第1版', 'entity_name': '合成对象',
        'source_name': '合成人工来源', 'baseline_name': '原规则', 'candidate_name': '候选规则'}
    scope.update(changes)
    return SimpleNamespace(id=identity, workspace_id=WORKSPACE, status='active', data={'scope': scope})


def observation(**changes):
    values = {'instance_id': INSTANCE, 'release_id': RELEASE, 'entity_id': ENTITY,
              'source_id': SOURCE, 'extractor_id': BASELINE}
    values.update(changes)
    return SimpleNamespace(id='15130000-0000-4000-8000-00000000000b', workspace_id=WORKSPACE,
        entity_id=values['entity_id'], source_id=values['source_id'], data={
            'extractor_id': values['extractor_id'], 'reported_rule_contract': {
                'schema': 'local-reported-rule-v1', 'instance_id': values['instance_id'],
                'release_id': values['release_id'], 'entity_id': values['entity_id'],
                'entity_version': 1, 'source_hash': 'c' * 64, 'extractor_hash': 'a' * 64}})


@pytest.mark.parametrize('reverse', [False, True])
def test_shared_source_entity_and_baseline_match_only_the_original_instance_release(reverse):
    exact = grant('15130000-0000-4000-8000-00000000000c')
    elsewhere = grant('15130000-0000-4000-8000-00000000000d',
        instance_id=OTHER_INSTANCE, release_id=OTHER_RELEASE)
    candidates = [exact, elsewhere] if not reverse else [elsewhere, exact]
    original = deepcopy([item.__dict__ for item in candidates])
    row = observation(); before = deepcopy(row.__dict__)
    selected = execution._matching_grants(candidates, row)
    assert len(selected) == 1 and selected[0] is exact
    assert [item.__dict__ for item in candidates] == original and row.__dict__ == before


@pytest.mark.parametrize('changes', [
    {'instance_id': OTHER_INSTANCE}, {'release_id': OTHER_RELEASE},
    {'instance_id': OTHER_INSTANCE, 'release_id': OTHER_RELEASE},
])
def test_other_instance_or_release_is_a_legal_nonmatch_not_a_conflict(changes):
    elsewhere = grant('15130000-0000-4000-8000-00000000000c', **changes)
    assert execution._matching_grants([elsewhere], observation()) == []


@pytest.mark.parametrize('changes', [
    {'entity_id': OTHER}, {'source_id': OTHER}, {'extractor_id': OTHER},
])
def test_other_original_entity_source_or_extractor_never_borrows_this_grant(changes):
    exact = grant('15130000-0000-4000-8000-00000000000c')
    assert execution._matching_grants([exact], observation(**changes)) == []


@pytest.mark.parametrize('contract', [None, [], False, 'local-reported-rule-v1', {},
    {'instance_id': INSTANCE}, {'release_id': RELEASE}])
def test_missing_or_non_dictionary_reported_identity_is_hard_inventory_failure(contract):
    row = observation(); row.data['reported_rule_contract'] = deepcopy(contract)
    before = deepcopy(row.__dict__)
    with pytest.raises(ShadowInventoryError) as error:
        execution._matching_grants([grant('15130000-0000-4000-8000-00000000000c')], row)
    assert error.value.status_code == 409 and row.__dict__ == before


def test_absent_reported_contract_is_not_treated_as_no_matching_grant():
    row = observation(); del row.data['reported_rule_contract']
    with pytest.raises(ShadowInventoryError) as error:
        execution._matching_grants([], row)
    assert error.value.status_code == 409


@pytest.mark.parametrize('scope', [None, [], False, 'another-scope', {}])
def test_malformed_candidate_scope_is_never_silently_skipped(scope):
    broken = grant('15130000-0000-4000-8000-00000000000d', instance_id=OTHER_INSTANCE)
    broken.data['scope'] = deepcopy(scope)
    exact = grant('15130000-0000-4000-8000-00000000000c')
    with pytest.raises(ShadowInventoryError) as error:
        execution._matching_grants([exact, broken], observation())
    assert error.value.status_code == 409


def test_missing_scope_identity_is_hard_even_when_another_instance_would_not_match():
    broken = grant('15130000-0000-4000-8000-00000000000d', instance_id=OTHER_INSTANCE)
    del broken.data['scope']['release_id']
    with pytest.raises(ShadowInventoryError) as error:
        execution._matching_grants([broken], observation())
    assert error.value.status_code == 409


def test_duplicate_exact_scopes_remain_visible_to_enqueues_conflict_guard():
    first = grant('15130000-0000-4000-8000-00000000000c')
    second = grant('15130000-0000-4000-8000-00000000000d')
    selected = execution._matching_grants([first, second], observation())
    assert len(selected) == 2 and selected[0] is first and selected[1] is second
