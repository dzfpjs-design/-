"""Independent receiver, event recovery and no silent degradation."""
import copy
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Audit,Event,Job,Memory
from generic_agents.worker import process_once,deliver_events
from tests.test_geo_quality_v351 import source_files,prepared
from generic_agents.geo_quality_reception_v352 import KIND,RECEIVED,TYPE


def assessment(client,source_files):
    body,_=prepared(client,source_files);r=client.post('/api/geo/quality/assessments',json=body);assert r.status_code==201,r.text
    identity=r.json()['id'];assert process_once(identity,deliver=False)
    return identity


def test_receipt_is_separate_unique_and_exposes_integrity_failure(client,source_files):
    identity=assessment(client,source_files);url='/api/geo/quality/assessments/'+identity
    report=client.get(url).json();assert report['data_reception']['state']=='pending'
    scenes={}
    for item in report['result']['derived_scenes']:
        row=client.get('/api/geo/scenes/'+item['scene_id']).json();assert process_once(row['job_id'],deliver=False)
        scenes[item['side']]=client.get('/api/geo/scenes/'+row['id']).json()
    before,after=scenes['before'],scenes['after']
    request={'request_id':'quality_gate_request_001','before_scene_id':before['id'],'after_scene_id':after['id'],
        'before_version':before['version'],'after_version':after['version'],'roi':[0,0,64,64],
        'alignment':'same_grid','threshold':10,'rgb_comparability_confirmed':True}
    assert client.post('/api/geo/pairs',json=request).status_code==409
    with Session(engine()) as db:
        event=db.scalar(select(Event).where(Event.kind==KIND));assert event and event.status=='pending_delivery';eid=event.id
        assert not list(db.scalars(select(Memory).where(Memory.data['type'].as_string()==TYPE)))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(deliver_events,event_kind=KIND) for _ in range(2)]
        for future in futures:future.result()
    saved=client.get(url);assert saved.status_code==200,saved.text
    assert saved.json()['data_reception']['state']=='received'
    pair=client.post('/api/geo/pairs',json=request);assert pair.status_code==201,pair.text
    assert all(x['quality_origin_v351']['data_reception_hash']==saved.json()['data_reception']['receipt_hash'] for x in pair.json()['sources'])
    deliver_events(event_kind=KIND)
    with Session(engine()) as db:
        event=db.get(Event,eid);assert event.status=='received' and event.attempts==1
        memories=list(db.scalars(select(Memory).where(Memory.data['type'].as_string()==TYPE)))
        audits=list(db.scalars(select(Audit).where(Audit.action==RECEIVED,Audit.object_id==eid)))
        assert len(memories)==len(audits)==1 and memories[0].data['receipt']['model_result_receipt']=='not_asserted'
        memory=memories[0];memory.data={**memory.data,'receipt_hash':'0'*64};db.commit()
    assert client.get(url).status_code==409
    rows=client.get('/api/geo/quality/assessments');assert rows.status_code==200
    assert next(x for x in rows.json() if x['id']==identity)['data_reception']['state']=='integrity_error'


def test_tampered_original_blocks_same_event_then_restoration_allows_retry(client,source_files):
    identity=assessment(client,source_files);url='/api/geo/quality/assessments/'+identity
    with Session(engine()) as db:
        job=db.get(Job,identity);audit=db.scalar(select(Audit).where(Audit.action=='geo.quality_result_v351',Audit.object_id==identity))
        old=audit.data;audit.data={**old,'result_hash':'0'*64};db.commit()
    deliver_events(event_kind=KIND)
    with Session(engine()) as db:
        event=db.scalar(select(Event).where(Event.kind==KIND));assert event.status=='blocked_delivery';eid=event.id
        assert not list(db.scalars(select(Memory).where(Memory.data['type'].as_string()==TYPE)))
        audit=db.scalar(select(Audit).where(Audit.action=='geo.quality_result_v351',Audit.object_id==identity));audit.data=old;db.commit()
    report=client.get(url);assert report.status_code==200 and report.json()['data_reception']['state']=='blocked'
    retry=client.post(url+'/retry-reception');assert retry.status_code==200,retry.text
    assert retry.json()['data_reception']['state']=='pending';deliver_events(event_kind=KIND)
    assert client.get(url).json()['data_reception']['state']=='received'
    with Session(engine()) as db:
        rows=list(db.scalars(select(Event).where(Event.kind==KIND)));assert len(rows)==1 and rows[0].id==eid
        assert len(list(db.scalars(select(Audit).where(Audit.action==RECEIVED,Audit.object_id==eid))))==1


def test_receiver_recomputes_statistics_from_pixels_not_just_result_hash(client,source_files):
    from fastapi import HTTPException
    from generic_agents.geo_quality_reception_v352 import _verify
    from generic_agents.security import Principal
    import pytest
    identity=assessment(client,source_files)
    with Session(engine()) as db:
        job=db.get(Job,identity);p=Principal(job.data['actor_id'],job.workspace_id,'viewer','test')
        altered=copy.deepcopy(job.data);altered['result']['native']['sides'][0]['raw_eligible_pixels']-=1
        job.data=altered
        with pytest.raises(HTTPException):_verify(db,p,job)
        db.rollback()


def test_historical_omitted_schema_replay_is_not_silently_upgraded(client,source_files):
    from generic_agents.contracts import digest
    body,_=prepared(client,source_files);url='/api/geo/quality/assessments'
    response=client.post(url,json=body);assert response.status_code==201
    identity=response.json()['id']
    # Reconstruct a V351 queued record as it existed before the upgrade.
    with Session(engine()) as db:
        job=db.get(Job,identity);data=copy.deepcopy(job.data);data['request']['schema']='geo-quality-request-v351'
        data['body_hash']=digest(data['request']);job.data=data;job.kind='prepare_geo_quality_v351'
        audit=db.scalar(select(Audit).where(Audit.action=='geo.quality_requested_v351',Audit.object_id==identity))
        audit.data={**audit.data,'body_hash':data['body_hash']};db.commit()
    repeated=client.post(url,json=body);assert repeated.status_code==201,repeated.text
    assert repeated.json()['id']==identity and repeated.json()['data_reception']['state']=='legacy_not_issued'
    assert client.post(url,json={**body,'schema':'geo-quality-request-v352'}).status_code==409
    assert client.post(url,json={**body,'request_id':'new_legacy_request_123','schema':'geo-quality-request-v351'}).status_code==422
    with Session(engine()) as db:assert not list(db.scalars(select(Event).where(Event.kind==KIND)))


def test_setup_receiver_requires_exact_workspace_and_never_calls_global(monkeypatch,account):
    import pytest
    from generic_agents import worker
    from generic_agents.geo_quality_reception_v352 import deliver_pending
    calls=[];monkeypatch.setattr(worker,'deliver_events',lambda **kwargs:calls.append(kwargs))
    assert ('geo_quality_reception_v352','deliver_pending') in worker.SETUP_POLL_ROOTS
    for value in (None,'','all','{'+account[2]+'}'):
        with pytest.raises((TypeError,ValueError)):deliver_pending(workspace_id=value)
    deliver_pending(workspace_id=account[2]);assert calls==[{'workspace_id':account[2],'event_kind':KIND}]


def test_failure_after_memory_write_rolls_back_receipt_and_recovers_original_event(client,source_files,monkeypatch):
    identity=assessment(client,source_files);url='/api/geo/quality/assessments/'+identity
    original=Session.flush
    def fail_before_audit(session,*args,**kwargs):
        if any(isinstance(row,Audit) and row.action==RECEIVED for row in session.new):
            raise ValueError('simulated failure before receipt commit')
        return original(session,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Session,'flush',fail_before_audit);deliver_events(event_kind=KIND)
    with Session(engine()) as db:
        event=db.scalar(select(Event).where(Event.kind==KIND));assert event.status=='blocked_delivery';event_id=event.id
        assert not list(db.scalars(select(Memory).where(Memory.data['type'].as_string()==TYPE)))
        assert not list(db.scalars(select(Audit).where(Audit.action==RECEIVED,Audit.object_id==event_id)))
    assert client.post(url+'/retry-reception').status_code==200;deliver_events(event_kind=KIND)
    assert client.get(url).json()['data_reception']['state']=='received'
    with Session(engine()) as db:
        event=db.get(Event,event_id);assert event.status=='received'
        assert len(list(db.scalars(select(Memory).where(Memory.data['type'].as_string()==TYPE))))==1


def test_reader_started_before_atomic_completion_refreshes_original_job(client,source_files,account):
    from generic_agents.geo_quality_v351 import view
    from generic_agents.security import Principal
    body,_=prepared(client,source_files);response=client.post('/api/geo/quality/assessments',json=body)
    assert response.status_code==201;identity=response.json()['id']
    with Session(engine()) as reader:
        stale=reader.get(Job,identity);assert stale.status=='queued'
        actor=Principal(stale.data['actor_id'],stale.workspace_id,'viewer','test')
        assert process_once(identity,deliver=False)
        assert stale.status=='queued'  # ORM object really predates producer commit.
        current=view(reader,actor,stale)
        assert current['status']=='succeeded' and current['result'] is not None
        assert current['data_reception']['state']=='pending'


def test_reader_started_before_receiver_commit_refreshes_same_event(client,source_files):
    from generic_agents.geo_quality_v351 import view
    from generic_agents.security import Principal
    identity=assessment(client,source_files)
    with Session(engine()) as reader:
        job=reader.get(Job,identity);event=reader.scalar(select(Event).where(Event.kind==KIND))
        assert event.status=='pending_delivery';actor=Principal(job.data['actor_id'],job.workspace_id,'viewer','test')
        deliver_events(event_kind=KIND)
        assert event.status=='pending_delivery'  # Loaded before the receiver committed.
        current=view(reader,actor,job)
        assert current['data_reception']['state']=='received'


def test_receiver_commit_between_memory_and_audit_reads_is_consistent(client,source_files,monkeypatch):
    from generic_agents.geo_quality_v351 import view
    from generic_agents.security import Principal
    from generic_agents import geo_quality_reception_v352 as reception
    identity=assessment(client,source_files);original=reception.memories;fired=[]
    def interleaved(db,p,event):
        rows=original(db,p,event)
        if not fired:
            fired.append(True);deliver_events(event_kind=KIND)
        return rows
    with Session(engine()) as reader:
        job=reader.get(Job,identity);actor=Principal(job.data['actor_id'],job.workspace_id,'viewer','test')
        monkeypatch.setattr(reception,'memories',interleaved)
        current=view(reader,actor,job)
        assert fired and current['data_reception']['state']=='received'
