"""Actual preview seal permits only deterministic UUID masking of instruction."""
import copy
import pytest
from generic_agents import same_goal_advice_contracts_v302 as c
from generic_agents.business_uuid_projection_v307 import project_text
from generic_agents.contracts import digest
from tests.test_same_goal_advice_contracts_v302 import scene


def prepared_scene():
    v=scene();original=copy.deepcopy(v.source);old=copy.deepcopy(v.preparation)
    identifier=old['scope']['task_id']
    instruction='仅核对当前目标 '+identifier.upper()+' 的资料，保留单位和原引用。'
    original['specification']['instruction']=instruction
    source_ref={**v.original,'content_hash':digest(original)}
    manifest=c.build_manifest(original,source_ref)
    unsigned={key:value for key,value in old.items() if key!='record_hash'}
    unsigned.update(instruction=instruction,source_business_capability_ref=source_ref,
        advice_capability_ref={**old['advice_capability_ref'],'content_hash':digest(manifest)})
    prep=c.seal_preparation(unsigned,manifest=manifest)
    source=c.check_source(v.sources,preparation=prep,manifest=manifest)
    preview={key:copy.deepcopy(value) for key,value in v.preview.items() if key!='preview_hash'}
    preview.update(preparation_snapshot_hash=prep['record_hash'],advice_capability_ref=prep['advice_capability_ref'])
    preview['cloud_request']['registered_instruction']=project_text(instruction,
        namespace_id=prep['scope']['analysis_plan_id'],salt='same-goal-advice:'+prep['record_hash'])
    preview['cloud_request_hash']=digest(preview['cloud_request'])
    return prep,source,manifest,preview


def test_current_instruction_uuid_is_masked_without_changing_registered_words_or_sealed_preparation():
    prep,source,manifest,preview=prepared_scene();before=copy.deepcopy(prep)
    result=c.seal_preview(preview,preparation=prep,source=source,manifest=manifest)
    assert prep==before and prep['scope']['task_id'].lower() not in result['cloud_request']['registered_instruction'].lower()
    assert result['cloud_request']['registered_instruction'].startswith('仅核对当前目标 ')
    assert result['cloud_request']['registered_instruction'].endswith(' 的资料，保留单位和原引用。')


@pytest.mark.parametrize('fault',['original_uuid','changed_instruction','different_namespace'])
def test_self_consistent_preview_hash_cannot_authorize_private_or_rewritten_instruction(fault):
    prep,source,manifest,preview=prepared_scene()
    if fault=='original_uuid':preview['cloud_request']['registered_instruction']=prep['instruction']
    elif fault=='changed_instruction':preview['cloud_request']['registered_instruction']+=' 自动执行设备动作。'
    else:preview['cloud_request']['registered_instruction']=project_text(prep['instruction'],
        namespace_id=prep['scope']['task_id'],salt='same-goal-advice:'+prep['record_hash'])
    preview['cloud_request_hash']=digest(preview['cloud_request'])
    with pytest.raises(ValueError):c.seal_preview(preview,preparation=prep,source=source,manifest=manifest)
