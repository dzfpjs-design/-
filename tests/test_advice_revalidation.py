"""Local recovery never regenerates a paid response or weakens evidence matching."""
import copy,json
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents import models as m
from generic_agents.worker import process_once
from tests.test_signal_advice import setup,start,cloud,get

def failed_structured(client,monkeypatch,invalid=False):
    r,obj,s,cap=setup(client)
    rows=[{'integrity_verified':True,'source_sha256':[sha],'name':'公开原件','status':'succeeded','kind':'segmentation','metrics':{'candidate_masks':count},'source_time_verified':False} for sha,count in [('A',3),('B',4)]]
    monkeypatch.setattr('generic_agents.signal_advice.collect',lambda *args:(rows,[],False))
    row,_,_=start(client,r,s,cap);calls=[]
    def audit_quote(value,context):
        pair=copy.deepcopy(context['basis_audit']['comparisons'][0])
        if invalid:pair['relation']='fabricated_conflict'
        value['findings']=[{'text':'不同原件的候选数量不能直接比较','basis':[{'ref_id':'basis_audit','quote':json.dumps(dict(reversed(list(pair.items()))),ensure_ascii=False)}]}]
    cloud(monkeypatch,calls,audit_quote)
    from generic_agents.advice_basis import matches_frozen_quote
    monkeypatch.setattr('generic_agents.advice_basis.matches_frozen_quote',lambda *args:False)
    assert process_once(row['job_id'],deliver=False)
    monkeypatch.setattr('generic_agents.advice_basis.matches_frozen_quote',matches_frozen_quote)
    row=get(client,row);assert row['status']=='failed' and row['has_saved_response']
    return row,calls

def test_saved_paid_response_recovers_locally_with_original_error_and_zero_new_calls(client,monkeypatch):
    row,calls=failed_structured(client,monkeypatch)
    with Session(engine()) as db:
        stored=db.get(m.SignalAdvice,row['id']);raw=copy.deepcopy(stored.data['raw_response']);jobs=len(list(db.scalars(select(m.Job).where(m.Job.kind=='advise_signal'))))
    body={'version':row['version'],'request_id':str(uuid4())};url='/api/signal-advice/'+row['id']+'/revalidate'
    response=client.post(url,json=body);assert response.status_code==200,response.text;saved=response.json()
    assert saved['status']=='needs_confirmation' and saved['local_revalidation']['status']=='passed'
    assert saved['local_revalidation']['cloud_calls']==0 and saved['local_revalidation']['previous_error']==row['error']
    assert saved['proposal']['findings'][0]['basis'][0]['verification']=='frozen_audit_fields'
    assert client.post(url,json=body).json()==saved and len(calls)==1
    assert client.post(url,json={**body,'request_id':str(uuid4()),'version':saved['version']}).status_code==409
    with Session(engine()) as db:
        assert db.get(m.SignalAdvice,row['id']).data['raw_response']==raw
        assert db.get(m.Job,row['job_id']).status=='failed'
        assert len(list(db.scalars(select(m.Job).where(m.Job.kind=='advise_signal'))))==jobs
        assert not list(db.scalars(select(m.Task)))

def test_invented_audit_fields_remain_failed_and_idempotent_without_new_cloud(client,monkeypatch):
    row,calls=failed_structured(client,monkeypatch,invalid=True)
    body={'version':row['version'],'request_id':str(uuid4())};url='/api/signal-advice/'+row['id']+'/revalidate'
    response=client.post(url,json=body);assert response.status_code==200,response.text;saved=response.json()
    assert saved['status']=='failed' and saved['local_revalidation']['status']=='failed' and '字段或值' in saved['error']
    assert client.post(url,json=body).json()==saved and len(calls)==1

def test_no_saved_response_or_changed_source_cannot_become_new_cloud_call(client,monkeypatch):
    row,calls=failed_structured(client,monkeypatch)
    with Session(engine()) as db:
        signal=db.get(m.PerceptionSignal,row['signal_id']);signal.version+=1;signal.data={**signal.data,'note':'依据变化'};db.commit()
    body={'version':row['version'],'request_id':str(uuid4())};url='/api/signal-advice/'+row['id']+'/revalidate'
    response=client.post(url,json=body);assert response.status_code==409 and '已变化' in response.text
    assert len(calls)==1 and get(client,row)['model_receipt']==row['model_receipt']
    with Session(engine()) as db:
        stored=db.get(m.SignalAdvice,row['id']);stored.data={k:v for k,v in stored.data.items() if k!='raw_response'};db.commit()
    assert client.post(url,json=body).status_code==409 and len(calls)==1
