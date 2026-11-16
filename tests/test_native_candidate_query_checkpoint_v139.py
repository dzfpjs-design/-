"""Root-owned actual model, human work and registered query recovery checkpoint."""
import json,os
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents import models as m
from generic_agents.config import settings
from generic_agents.contracts import digest
from tests.test_native_learned_geo_journey_v138 import actual_learned_journey,rasters,pytestmark
from tests.test_native_candidate_queries_v139 import good,query_body,query_root,finish_query,assert_actual_record,original_state
from tests.test_instance_workspace import complete


def test_actual_received_candidate_human_query_and_durable_checkpoint(client,rasters,account):
 value=actual_learned_journey(client,rasters)
 task=good(client.get('/api/workspace/tasks/'+value['task']['id']))
 note='已核对原件、模型候选及A/B允许范围；保留人工核查意见，尚未证实现场变化。'
 draft=good(client.put('/api/workspace/tasks/'+task['id']+'/note',json={'version':task['version'],'step_id':task['workflow_runtime']['current_step_id'],'note':note}))
 task=good(complete(client,draft,note=note));assert task['status']=='workflow_completed'
 original=original_state(value);body=query_body(value,field='review_status',text='已核查');base=query_root(value)
 queued=good(client.post(base,json=body),201)
 saved=finish_query(client,value,body,queued,account[2]);assert_actual_record(saved,value,'confirmed')
 assert original_state(value)==original
 with Session(engine()) as db:
  assert len(list(db.scalars(select(m.Invocation))))==2
  assert not list(db.scalars(select(m.CloudCall)))
  data=db.get(m.DataRun,saved['id']);assert data.status=='sufficient'
  result_hash=digest(data.data['result'])
 destination=os.environ.get('GA_V139_NATIVE_PROOF_OUT')
 if destination:
  paths=['/api/workspace/tasks/'+task['id'],value['base']+'/native-receipts',
   value['base']+'/native-receipts/'+value['observation']['id']+'/export',
   base,base+'/'+saved['id'],base+'/requests/'+body['request_id']+'?expected_hash='+digest(body),
   '/api/geo/learned-pairs/catalog']
  readbacks=[]
  for path in paths:
   response=client.get(path);assert response.status_code==200,response.text[:600]
   readbacks.append({'path':path,'text':response.text})
  proof={'schema':'native-candidate-query-durable-checkpoint-v139','synthetic_only':True,
   'account':{'username':account[0],'password':account[1],'workspace_id':account[2]},
   'instance_id':value['release']['instance_id'],'release_id':value['release']['id'],'task_id':task['id'],
   'pair_id':value['pair_id'],'observation_id':value['observation']['id'],'query_id':saved['id'],
   'query_request':body,'query_result_hash':result_hash,'saved_note':note,'readbacks':readbacks,
   'entity_id':body['entity_id'],'storage_dir':str(settings().storage_dir),'specialist_dir':str(settings().specialist_dir),
   'prepared_model_dir':str(Path(__file__).resolve().parents[1]/'.runtime/changer-candidate'),
   'geo_python':str(settings().geo_python),'specialist_python':str(settings().specialist_python),
   'learned_model':True,'actual_registered_query_invocations':1,'real_provider_calls_delta':0,
   'actual_browser_verified':False,'model_domain_quality_verified':False,'production_ready':False}
  out=Path(destination);assert not out.exists() and out.name.endswith('.native-checkpoint.private.json')
  with out.open('x') as stream:json.dump(proof,stream,ensure_ascii=False,sort_keys=True)
  out.chmod(0o600)
