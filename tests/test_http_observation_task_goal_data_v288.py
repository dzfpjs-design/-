"""Fixed consumer-family dispatch only, with actual structural V288 checker.

No worker, PG, IAM, HTTP provider, receipt or sampling execution is proved by
these tests. Root must exercise that path in its durable runtime separately.
"""
import copy

import pytest
from fastapi import HTTPException

from generic_agents import http_observation_task_consumer_v270 as consumer
from generic_agents import authored_goal_data_workflow_store_v288 as family
from generic_agents import business_flow_nested_adoption_v195 as old
from generic_agents.contracts import digest
from tests.test_authored_goal_data_workflow_v288 import cold_binding, command


def test_task_consumer_uses_the_actual_new_frozen_family_without_translation(monkeypatch):
    binding = cold_binding(command())
    monkeypatch.setattr(old, 'service_delegations', lambda *a: pytest.fail('new family became V195 translation'))
    before = copy.deepcopy(binding)
    assert consumer._service_delegations(binding) == family.service_delegations(binding)
    assert consumer._service_delegations(binding)['resense'] == {
        'service_actor': 'execution_service', 'operation': 'resense_from_context', 'agent_role': 'business'}
    assert binding == before


@pytest.mark.parametrize('damage', ['operation', 'actor', 'graph', 'missing_policy'])
def test_a_new_marker_and_rehash_cannot_replace_its_actual_frozen_declarations(damage):
    binding = cold_binding(command())
    if damage == 'operation': binding['service_delegations']['resense']['operation'] = 'in_app_notice'
    elif damage == 'actor': binding['service_delegations']['resense']['service_actor'] = 'reader_service'
    elif damage == 'graph': binding['graph']['entry'] = 'resense'
    else: binding.pop('preparation_policy')
    binding['binding_hash'] = digest({key: value for key, value in binding.items() if key != 'binding_hash'})
    with pytest.raises(HTTPException): consumer._service_delegations(binding)
