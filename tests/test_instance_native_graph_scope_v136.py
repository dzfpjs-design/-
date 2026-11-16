from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents.contracts import digest
from generic_agents.instance_api import Compilation, Generate
from generic_agents.instance_native_graph_scope_v136 import binding_issues, require_snapshot_instance


def snapshot(instance_id, schema='graph-event-native-audio-source-contract-v1'):
    binding = {'event_source_bindings': {'wait-a': {'contract': {'schema': schema, 'instance_id': instance_id}}}}
    return {'semantic_runtime_plan': {'schema': 'instance-full-semantic-runtime-plan-v4',
        'graph_adoptions': [{'binding': binding}]}}


@pytest.mark.parametrize('schema', ['graph-event-native-audio-source-contract-v1',
    'graph-event-native-learned-geo-source-contract-v1'])
def test_native_source_requires_explicit_matching_generation_target_before_save_or_activation(schema):
    value = snapshot('instance-a', schema)
    require_snapshot_instance(value, 'instance-a')
    for target in [None, '', 'instance-b']:
        with pytest.raises(HTTPException) as failure:
            require_snapshot_instance(value, target)
        assert failure.value.status_code == 409


def test_processing_source_and_older_runtime_keep_original_instance_behavior():
    for target in [None, 'instance-b']:
        require_snapshot_instance(snapshot('instance-a', 'graph-event-observation-source-contract-v1'), target)
        require_snapshot_instance({}, target)
    assert binding_issues({}, None) == []


def test_check_accepts_target_but_generation_original_wire_values_and_hash_stay_exact():
    original = {'revision': 3, 'objects': [], 'role_bindings': {}, 'instance_id': str(uuid4()),
        'confirmed_configuration_hash': 'a' * 64, 'confirm_configuration': True,
        'generation_intent': 'new_release', 'request_id': str(uuid4())}
    assert Compilation.model_validate({key: original[key] for key in ['revision', 'objects', 'role_bindings', 'instance_id']}).instance_id == original['instance_id']
    assert Generate.model_validate(original).model_dump(mode='json') == original
    assert digest(Generate.model_validate(original).model_dump(mode='json')) == digest(original)
