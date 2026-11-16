"""Actual Task entry function through declared IO boundaries, no PG/IAM proof."""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import workspace_api as api
from generic_agents import business_flow_execution_runtime as runtime
from generic_agents import instance_semantic_runtime_dispatch as dispatch
from generic_agents import relations, graph_task_inputs_v120, task_creation_recovery_v120
from tests.test_business_flow_nested_inputs_runtime_v195 import entry


class ReachedMaterialCheck(Exception):
    pass


@pytest.mark.parametrize('mode,kind,simulation,allowed', [
    ('engineering_manual', 'event', True, True),
    ('declared_manual', 'manual', False, True),
    ('declared_manual', 'event', False, False),
    ('authorized_anomaly_handoff', 'event', False, False),
    ('engineering_manual', 'event', False, False),
])
def test_real_task_entry_enforces_nested_policy_and_preserves_all_original_materials(
        monkeypatch, mode, kind, simulation, allowed):
    binding = entry(mode, kind, simulation=simulation)
    binding['source_materials'] = [{'id': 'source_one'}, {'id': 'source_two'}]
    flow = {'steps': [], 'source_flow': binding['flow']['source_flow']}
    business = {'id': 'business_one', 'flow': flow, 'execution_mode': 'confirmed_graph_workflow'}
    materials = [{'id': 'source_one', 'sha256': '1' * 64}, {'id': 'source_two', 'sha256': '2' * 64}]
    release = NS(id='release_one', data={'snapshot': {'businesses': [business], 'materials': materials}})
    instance = NS(data={'enabled_business_ids': ['business_one']})
    principal = NS(user_id='actor_one', workspace_id='workspace_one', role='operator')
    body = api.NewTask(release_id=release.id, business_id=business['id'], entity_ids=['entity_one'],
        title='公开合成任务入口核对', request_id='original_entry_195')
    before = copy.deepcopy(binding); selected = []
    monkeypatch.setattr(api, 'scoped', lambda *a, **k: instance)
    monkeypatch.setattr(api, 'active', lambda *a, **k: (instance, release))
    monkeypatch.setattr(relations, 'lock_current_actor', lambda *a, **k: None)
    monkeypatch.setattr(task_creation_recovery_v120, 'find', lambda *a, **k: None)
    monkeypatch.setattr(graph_task_inputs_v120, 'validate_associations', lambda *a, **k: None)
    monkeypatch.setattr(dispatch, 'modern', lambda *a, **k: NS(business_binding=lambda *a, **k: binding))
    def verified(db, p, sources):
        selected.extend(sources)
        raise ReachedMaterialCheck()
    monkeypatch.setattr(api, 'verify_material_snapshots', verified)
    class DB:
        def scalar(self, query): return None
        def add(self, value): raise AssertionError('no Task may be written before entry/material checks')
    if allowed:
        with pytest.raises(ReachedMaterialCheck): api.create_task('instance_one', body, principal, DB())
        assert selected == materials
    else:
        with pytest.raises(HTTPException) as error: api.create_task('instance_one', body, principal, DB())
        assert error.value.status_code == 409 and not selected
    assert binding == before
