"""Closed snapshot tests; source authorization and dispatch remain separate."""
import copy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from generic_agents import same_goal_advice_contracts_v302 as old
from generic_agents import same_goal_advice_unlimited_contracts_v2 as v2
from generic_agents.business_uuid_projection_v307 import project_text
from generic_agents.contracts import digest
from tests.test_same_goal_advice_contracts_v302 import scene as old_scene


def scene():
    original = old_scene()
    prep = copy.deepcopy(original.preparation); prep.pop('record_hash')
    prep['schema'] = v2.PREPARATION
    budget = prep['budget']; budget['policy_ref'] = budget.pop('quota_policy_ref')
    budget.pop('max_cost_minor_units'); budget.pop('currency')
    budget.update(cost_policy='unlimited', allocation_basis='per_plan')
    prep = v2.seal_preparation(prep, manifest=original.manifest)
    source = v2.check_source(original.sources, preparation=prep, manifest=original.manifest)
    preview = copy.deepcopy(original.preview); preview.pop('preview_hash')
    preview.update(schema=v2.PREVIEW, preparation_snapshot_hash=prep['record_hash'], budget=copy.deepcopy(budget))
    preview['cloud_request']['registered_instruction'] = project_text(prep['instruction'],
        namespace_id=prep['scope']['analysis_plan_id'], salt='same-goal-advice:' + prep['record_hash'])
    preview['cloud_request_hash'] = digest(preview['cloud_request'])
    preview = v2.seal_preview(preview, preparation=prep, source=source, manifest=original.manifest)
    execution = copy.deepcopy(original.execution); execution.pop('record_hash')
    execution.update(schema=v2.EXECUTION, preparation_snapshot_hash=prep['record_hash'], preview=preview)
    execution['start_request']['preview_hash'] = preview['preview_hash']
    execution['start_request_hash'] = digest(execution['start_request'])
    execution = v2.seal_plan(execution, preparation=prep, manifest=original.manifest)
    return SimpleNamespace(old=original, prep=prep, source=source, preview=preview, execution=execution)


def test_v2_snapshot_keeps_source_consent_and_single_call_without_amount():
    s = scene()
    assert s.prep['budget']['cost_policy'] == 'unlimited'
    assert s.prep['budget']['allocation_basis'] == 'per_plan'
    assert s.prep['budget']['max_actual_calls'] == 1
    assert 'max_cost_minor_units' not in s.prep['budget']
    assert v2.check_plan(s.execution, preparation=s.prep, manifest=s.old.manifest) == s.execution
    with pytest.raises((ValidationError, ValueError)):
        old.check_preparation(s.prep, manifest=s.old.manifest)
    with pytest.raises((ValidationError, ValueError)):
        v2.check_preparation(s.old.preparation, manifest=s.old.manifest)


@pytest.mark.parametrize('fault', ['head', 'source', 'consent', 'private_id', 'budget', 'start'])
def test_rehashed_snapshots_cannot_change_evidence_consent_or_original_scope(fault):
    s = scene()
    value = copy.deepcopy(s.execution)
    if fault == 'head':
        value['source_record']['mature_head']['head_hash'] = 'e' * 64
        value['source_record']['record_hash'] = old.record_hash(value['source_record'])
    elif fault == 'source':
        value['source_record']['document']['approved_selection']['source_set_hash'] = 'd' * 64
        value['source_record']['record_hash'] = old.record_hash(value['source_record'])
    elif fault == 'consent':
        value['preview']['consent_pins'].pop()
    elif fault == 'private_id':
        value['preview']['cloud_request']['question'] += s.prep['scope']['task_id']
        value['preview']['cloud_request_hash'] = digest(value['preview']['cloud_request'])
    elif fault == 'budget':
        value['preview']['budget']['max_actual_calls'] = 2
    else:
        value['start_request']['expected_goal_hash'] = 'a' * 64
        value['start_request_hash'] = digest(value['start_request'])
    value['preview']['preview_hash'] = digest({k: v for k, v in value['preview'].items() if k != 'preview_hash'})
    value['start_request']['preview_hash'] = value['preview']['preview_hash']
    value['start_request_hash'] = digest(value['start_request'])
    value['record_hash'] = old.record_hash(value)
    with pytest.raises((ValidationError, ValueError)):
        v2.check_plan(value, preparation=s.prep, manifest=s.old.manifest)


def test_result_dispatch_is_versioned_and_never_implies_acceptance_or_verified_facts():
    s = scene()
    result = v2.derive_result(s.execution, s.old.opinion, preparation=s.prep, manifest=s.old.manifest)
    assert result['schema'] == v2.RESULT and result['review_state'] == 'ready_for_review'
    assert all(result[key] is False for key in old.FLAGS)
    assert v2.check_result(result, snapshot=s.execution, preparation=s.prep, manifest=s.old.manifest) == result
    with pytest.raises((ValidationError, ValueError)):
        old.check_result(result, snapshot=s.old.execution, preparation=s.old.preparation, manifest=s.old.manifest)
    old_result = old.derive_result(s.old.execution, s.old.opinion,
        preparation=s.old.preparation, manifest=s.old.manifest)
    with pytest.raises((ValidationError, ValueError)):
        v2.check_result(old_result, snapshot=s.execution, preparation=s.prep, manifest=s.old.manifest)
    bad = copy.deepcopy(s.old.opinion)
    bad['recommendation']['supporting_fact_refs'] = ['fact_invented']
    with pytest.raises(ValueError):
        v2.derive_result(s.execution, bad, preparation=s.prep, manifest=s.old.manifest)
    for key in old.FLAGS:
        with pytest.raises(ValueError):
            v2.check_result({**result, key: 0}, snapshot=s.execution, preparation=s.prep, manifest=s.old.manifest)
