"""Actual registered archive setup, independent current inputs, no providers."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import CloudCall, Capability, Entity, Task, Job, Memory
from tests.browser_local_method_harness import seed


def test_local_seed_has_independent_current_records_and_truthful_acceptance(client):
    release, tasks, objects = seed(client)
    assert set(tasks) == {'A', 'B', 'CAP', 'A2', 'B2'}
    all_ids = [row['id'] for rows in objects.values() for row in rows]
    assert len(all_ids) == len(set(all_ids)) == 9
    assert len(tasks['A']['fields']) == 3 and len(tasks['CAP']['fields']) == 6
    assert tasks['A2']['fields'] == tasks['A']['fields'][1:]
    assert tasks['B2']['fields'] == tasks['A2']['fields']
    with Session(engine()) as db:
        assert not db.scalar(select(CloudCall.id))
        assert not db.scalar(select(Capability.id).where(Capability.kind == 'foundation_model'))
        for key, task in tasks.items():
            accepted = db.get(Task, task['id']).data.get('native_goal_acceptance_hash')
            assert bool(accepted) == (key in {'A', 'A2', 'CAP'})
        assert db.get(Entity, objects['A'][0]['id']).data[tasks['A']['fields'][0]] == 17
        assert db.get(Entity, objects['B'][0]['id']).data[tasks['B']['fields'][0]] == 51


def test_actual_seed_local_preview_is_complete_without_cloud_registration(client):
    _, tasks, _ = seed(client)
    for key, expected in [('A', 4), ('A2', 2)]:
        result = client.post('/api/workspace/tasks/' + tasks[key]['id'] +
                             '/experience/suggestions/local/preview', json={})
        assert result.status_code == 200, result.text
        view = result.json()
        assert view['source_kind'] == 'local_rules'
        assert view['capacity']['required_count'] == expected
        assert len(view['steps']) == expected
        assert view['local_rules_contract']['version'] == 'native-method-local-rules-v1'
        assert view.get('cloud_input') is None and view.get('call') is None
    result = client.post('/api/workspace/tasks/' + tasks['CAP']['id'] +
                         '/experience/suggestions/local/preview', json={})
    assert result.status_code == 409 and '最多5项' in result.text
    with Session(engine()) as db:
        assert not db.scalar(select(CloudCall.id))
        assert not db.scalar(select(Job.id).where(Job.kind == 'generate_native_method_suggestion'))
        assert not db.scalar(select(Memory.id).where(Memory.data['type'].as_string() == 'native_procedure_suggestion'))
