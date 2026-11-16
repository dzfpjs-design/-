"""Validate real disposable inputs before spending provider calls or human time."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import CloudCall, Entity, Task
from tests.browser_native_suggestion_harness import seed


def test_seed_has_three_independent_archives_and_one_accepted_source(client):
    release, tasks, objects, model = seed(client)
    assert set(tasks) == {'A', 'B', 'C'}
    assert len({obj['id'] for obj in objects.values()}) == 3
    assert len({tuple(obj['attributes'].values()) for obj in objects.values()}) == 3
    with Session(engine()) as db:
        assert db.get(Task, tasks['A']['id']).data.get('native_goal_acceptance_hash')
        assert all(not db.get(Task, tasks[x]['id']).data.get('native_goal_acceptance_hash') for x in 'BC')
        assert all(db.get(Entity, row['entity_id']) for row in tasks.values())
        assert not db.scalar(select(CloudCall.id))
    assert model['data']['execution']['provider'] == 'qwen'
