"""Prove real registered mixed schemas and independent raw inputs before calls."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import CloudCall, Entity, Task
from tests.browser_native_suggestion_v2_harness import seed


def test_v2_seed_uses_two_real_schemas_and_does_not_seed_B_acceptance(client):
    release,tasks,objects,models=seed(client)
    assert len(objects['A'])==len(objects['B'])==2
    assert set(tasks['A']['entity_ids']).isdisjoint(tasks['B']['entity_ids'])
    assert len(tasks['A']['fields'])==3 and len(tasks['CAP']['fields'])==6
    assert models['deepseek']['data']['execution']['thinking_mode']=='disabled'
    with Session(engine()) as db:
        assert db.get(Entity,objects['A'][0]['id']).entity_type!=db.get(Entity,objects['A'][1]['id']).entity_type
        assert db.get(Task,tasks['A']['id']).data.get('native_goal_acceptance_hash')
        assert db.get(Task,tasks['CAP']['id']).data.get('native_goal_acceptance_hash')
        assert not db.get(Task,tasks['B']['id']).data.get('native_goal_acceptance_hash')
        assert not db.scalar(select(CloudCall.id))
    previews={}
    for provider,model in models.items():
        response=client.post('/api/workspace/tasks/'+tasks['A']['id']+'/experience/suggestions/preview',
                             json={'model_profile_id':model['id']})
        assert response.status_code==200,response.text
        view=response.json()
        assert [field['type'] for field in view['cloud_input']['fields']]==['integer','boolean','unknown']
        assert view['cloud_input']['goal']['required_aliases']==['F1','F2','F3']
        assert view['capacity']['required_count']==4
        previews[provider]={key:view[key] for key in ['cloud_input','generation_contract','dispatch_messages_hash','dispatch_options','capacity','model_hash']}
    assert previews['qwen']['cloud_input']==previews['deepseek']['cloud_input']
    from pathlib import Path
    import json
    path=Path('.runtime/native-suggestion-v2-preflight.json')
    path.write_text(json.dumps({'classification':'API_preflight_only_no_CUA_or_cloud','previews':previews},ensure_ascii=False,indent=2)+'\n')
