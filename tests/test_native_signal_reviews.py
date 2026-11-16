"""Supplementary human reviews must not mutate or rerun the original native task."""
import copy
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents.db import engine
from generic_agents.worker import process_once,deliver_events
from generic_agents.config import settings
from tests.test_candidate_watches import watch
from tests.test_native_receipts import setup_geo,bridge,pairs,scenes,pytestmark
from tests.test_instance_workspace import complete,task_row,object_row


def prepare(client,pairs,*,completed=False):
 a,b,release,body,base,task=setup_geo(client,pairs)
 if completed:
  r=complete(client,task,note='原流程结论保留');assert r.status_code==200,r.text;task=r.json()
 w,_=watch(client,base,{'id':body['entity_ids'][0]},'native:GeoPair',threshold=5);bridge(client,base)
 with Session(engine()) as db:ids=db.scalars(select(m.Job.id).where(m.Job.kind=='check_candidate_watch')).all()
 for j in ids:assert process_once(j,deliver=False)
 deliver_events();signal=client.get(base+'/signals').json()['records'][0]
 note={'version':signal['version'],'request_id':uuid4().hex,'note':'原工程候选需人工核查'}
 signal=client.put(base+'/signals/'+signal['id']+'/note',json=note).json()
 return a,release,base,task,signal


def link_body(t,s):return {'version':s['version'],'request_id':uuid4().hex,'task_id':t['id'],'task_version':t['version'],'basis_hash':s['basis_hash']}
def review_body(s,**kw):return {'version':s['version'],'request_id':uuid4().hex,'note':'仅工程像素对照，时间与语义未证实','decision':'uncertain',**kw}


def test_link_draft_review_and_revision_preserve_original_workflow_and_inference(client,pairs):
 a,r,base,t,s=prepare(client,pairs)
 with Session(engine()) as db:original=copy.deepcopy(db.get(m.Task,t['id']).data);version=db.get(m.Task,t['id']).version
 payload=link_body(t,s);url=base+'/signals/'+s['id'];response=client.post(url+'/original-task',json=payload);assert response.status_code==200,response.text;s=response.json()
 assert client.post(url+'/original-task',json=payload).json()['version']==s['version']
 assert client.post(url+'/original-task',json={**payload,'task_version':1}).status_code==409
 assert client.get('/api/workspace/tasks/'+t['id']+'/linked-signals').json()['records'][0]['source_task_link']['note']=='原工程候选需人工核查'
 body=review_body(s);draft=client.put(url+'/original-task-draft',json=body);assert draft.status_code==200,draft.text;s=draft.json()
 assert client.put(url+'/original-task-draft',json=body).json()['version']==s['version']
 assert client.get(base+'/signals').json()['records'][0]['linked_review_draft']['note']==body['note']
 decision=review_body(s);done=client.post(url+'/original-task-review',json=decision);assert done.status_code==200,done.text;s=done.json()
 assert s['business_review']['decision']=='uncertain' and s['business_review']['supplementary_review']
 assert client.post(url+'/original-task-review',json=decision).json()['business_review']==s['business_review']
 assert client.post(url+'/original-task-review',json=review_body(s,decision='not_abnormal')).status_code==422
 revised=client.post(url+'/original-task-review',json=review_body(s,decision='not_abnormal',revision_reason='重新对照工程图，非现场异常'));assert revised.status_code==200,revised.text
 assert revised.json()['business_review_history'][0]['decision']=='uncertain'
 with Session(engine()) as db:
  row=db.get(m.Task,t['id']);assert row.data==original and row.version==version
  assert len(db.scalars(select(m.Invocation)).all())==1 and not db.scalars(select(m.CloudCall)).all()
  mem=db.get(m.Memory,revised.json()['business_review']['data_receipt_id']);assert mem.status=='candidate' and not mem.data['auto_publish']
  assert db.get(m.Entity,t['entity_ids'][0]).version==1


def test_late_signal_can_review_completed_original_without_overwriting_original_result(client,pairs):
 a,r,base,t,s=prepare(client,pairs,completed=True);before=client.get('/api/workspace/tasks/'+t['id']).json()
 linked=client.post(base+'/signals/'+s['id']+'/original-task',json=link_body(t,s));assert linked.status_code==200,linked.text
 response=client.post(base+'/signals/'+s['id']+'/original-task-review',json=review_body(linked.json()));assert response.status_code==200,response.text
 assert client.get('/api/workspace/tasks/'+t['id']).json()==before
 export=client.get(base+'/signals/'+s['id']+'/export').json();assert export['source_task_link']['task_id']==t['id'] and export['business_review']['supplementary_review']


def test_wrong_task_version_scope_cancellation_and_missing_judgment_rejected(client,pairs):
 a,r,base,t,s=prepare(client,pairs);url=base+'/signals/'+s['id'];payload=link_body(t,s)
 assert client.post(url+'/original-task',json={**payload,'task_id':'unknown'}).status_code==409
 assert client.post(url+'/original-task',json={**payload,'task_version':t['version']+1}).status_code==409
 assert client.post(url+'/original-task',json={**payload,'basis_hash':'0'*64}).status_code==409
 s=client.post(url+'/original-task',json=payload).json()
 assert client.post(url+'/original-task-review',json=review_body(s,decision=None)).status_code==422
 with Session(engine()) as db:row=db.get(m.Task,t['id']);row.status='workflow_cancelled';db.commit()
 assert client.post(url+'/original-task-review',json=review_body(s)).status_code==409
 assert client.get(base+'/signals').json()['records'][0]['source_task_link'] is not None


def test_bad_original_preserves_draft_and_recovery_does_not_run_model(client,pairs):
 a,r,base,t,s=prepare(client,pairs);url=base+'/signals/'+s['id'];s=client.post(url+'/original-task',json=link_body(t,s)).json()
 s=client.put(url+'/original-task-draft',json=review_body(s,note='人工作业必须保存')).json()
 with Session(engine()) as db:ev=db.get(m.Evidence,a['evidence_id']);path=settings().storage_dir/ev.storage_key;original=path.read_bytes()
 path.write_bytes(b'damaged-disposable-test')
 try:
  assert client.post(url+'/original-task-review',json=review_body(s)).status_code==409
  listed=client.get('/api/workspace/tasks/'+t['id']+'/linked-signals').json()['records'][0]
  assert listed['basis'] is None and listed['linked_review_draft']['note']=='人工作业必须保存'
 finally:path.write_bytes(original)
 assert client.post(url+'/original-task-review',json=review_body(s)).status_code==200
 with Session(engine()) as db:assert len(db.scalars(select(m.Invocation)).all())==1


def test_revoked_review_role_and_cas_do_not_erase_draft(client,pairs,account):
 a,r,base,t,s=prepare(client,pairs);url=base+'/signals/'+s['id'];s=client.post(url+'/original-task',json=link_body(t,s)).json()
 draft=review_body(s);s=client.put(url+'/original-task-draft',json=draft).json()
 assert client.post(url+'/original-task-review',json=review_body({**s,'version':1})).status_code==409
 with Session(engine()) as db:member=db.scalar(select(m.Membership).where(m.Membership.workspace_id==account[2]));member.role='viewer';db.commit()
 assert client.post(url+'/original-task-review',json=review_body(s)).status_code==403
 assert client.get('/api/workspace/tasks/'+t['id']+'/linked-signals').json()['records'][0]['linked_review_draft']['note']==draft['note']


def test_multiple_native_rules_have_independent_reviews_on_same_original_task(client,pairs):
 from tests.test_candidate_watches import jobs
 a,r,base,t,s=prepare(client,pairs)
 second,_=watch(client,base,{'id':t['entity_ids'][0]},'native:GeoPair',threshold=1);jobs(client,base,second)
 records=client.get(base+'/signals').json()['records'];assert len(records)==2
 for row in records:
  linked=client.post(base+'/signals/'+row['id']+'/original-task',json=link_body(t,row));assert linked.status_code==200,linked.text
  result=client.post(base+'/signals/'+row['id']+'/original-task-review',json=review_body(linked.json()));assert result.status_code==200,result.text
 listed=client.get('/api/workspace/tasks/'+t['id']+'/linked-signals').json()['records'];assert len(listed)==2
 assert len({r['business_review']['data_receipt_id'] for r in listed})==2
 assert client.get('/api/workspace/tasks/'+t['id']).json()['workflow_runtime']==t['workflow_runtime']


def test_corrupt_link_hides_machine_values_but_preserves_review_draft(client,pairs):
 a,r,base,t,s=prepare(client,pairs);url=base+'/signals/'+s['id'];s=client.post(url+'/original-task',json=link_body(t,s)).json()
 s=client.put(url+'/original-task-draft',json=review_body(s,note='不可丢失的人工审核')).json()
 with Session(engine()) as db:
  row=db.get(m.PerceptionSignal,s['id']);original=copy.deepcopy(row.data);row.data={**row.data,'source_task_link':{**row.data['source_task_link'],'note':'tampered'}};db.commit()
 listing=client.get('/api/workspace/tasks/'+t['id']+'/linked-signals').json()['records'][0]
 assert listing['basis'] is None and listing['integrity_issue'] and listing['linked_review_draft']['note']=='不可丢失的人工审核'
 assert client.post(url+'/original-task-review',json=review_body(s)).status_code==409
 with Session(engine()) as db:db.get(m.PerceptionSignal,s['id']).data=original;db.commit()
 assert client.post(url+'/original-task-review',json=review_body(s)).status_code==200


def test_historical_duplicate_keeps_notes_and_cannot_start_second_review(client,pairs):
 a,r,base,t,s=prepare(client,pairs)
 with Session(engine()) as db:
  original=db.get(m.PerceptionSignal,s['id']);event=db.get(m.Event,original.event_id)
  old=m.Event(workspace_id=event.workspace_id,kind=event.kind,idempotency_key='historical-duplicate-'+uuid4().hex,data=copy.deepcopy(event.data));db.add(old);db.commit()
 deliver_events();rows=client.get(base+'/signals').json()['records'];assert len(rows)==2
 primary=next(x for x in rows if x['id']==s['id']);other=next(x for x in rows if x['id']!=s['id'])
 linked=client.post(base+'/signals/'+s['id']+'/original-task',json=link_body(t,primary));assert linked.status_code==200,linked.text
 assert client.post(base+'/signals/'+other['id']+'/original-task',json=link_body(t,other)).status_code==409
 listed=next(x for x in client.get(base+'/signals').json()['records'] if x['id']==other['id']);assert listed['related_native_receipts'][0]['tasks'][0]['id']==t['id']
 assert listed['note']==other['note'] and listed['basis'] is not None


def test_historical_duplicate_without_verified_basis_does_not_hide_valid_human_work(client,pairs):
 a,r,base,t,s=prepare(client,pairs)
 with Session(engine()) as db:
  original=db.get(m.PerceptionSignal,s['id']);event=db.get(m.Event,original.event_id)
  dup=m.Event(workspace_id=event.workspace_id,kind=event.kind,idempotency_key='historical-broken-'+uuid4().hex,status='received',data=copy.deepcopy(event.data));db.add(dup);db.flush()
  broken=m.PerceptionSignal(workspace_id=original.workspace_id,event_id=dup.id,instance_id=original.instance_id,entity_id=original.entity_id,status='blocked_evidence',data={**copy.deepcopy(original.data),'basis':None,'basis_hash':None,'receipt_issue':'原依据需要恢复'});db.add(broken);db.commit()
 listed=client.get(base+'/signals');assert listed.status_code==200,listed.text
 good=next(x for x in listed.json()['records'] if x['id']==s['id']);assert good['basis'] and good['note']==s['note']
 assert client.post(base+'/signals/'+s['id']+'/original-task',json=link_body(t,good)).status_code==200
