"""Original ACK protocol for queued diagnostics. No background runner stubbed."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Event,Job,Issue,Memory,now
from generic_agents.mqtt_data import receive_packet
from generic_agents.mqtt_unit_quality_dispatch_v357 import enqueue,verify,KIND
from tests.test_mqtt_inbox import gateway,save,packet


def test_original_packet_queue_is_idempotent_and_not_authoritative_before_ack(gateway):
    p=gateway[0];receipt=save(gateway,packet(units={'t':'F'}))
    with Session(engine()) as db:
        row=db.get(type(receipt),receipt.id)
        evt=db.scalar(select(Event).where(Event.kind=='perception.sensor_packet',Event.data['receipt_id'].as_string()==receipt.id))
        receive_packet(db,evt);db.flush()
        job=enqueue(db,evt,row);identity=job.id
        assert enqueue(db,evt,row).id==identity
        with pytest.raises(HTTPException):verify(db,p,job)
        assert db.scalar(select(func.count()).select_from(Issue))==0
        evt.status='received';evt.received_at=now();evt.attempts+=1;db.commit()
    with Session(engine()) as db:
        job=db.get(Job,identity);verified,event=verify(db,p,job)
        assert job.kind==KIND and verified.id==receipt.id and event.status=='received'
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind==KIND))==1
        assert db.scalar(select(func.count()).select_from(Memory).where(Memory.data['type'].as_string()=='sensor_original'))==1
        assert db.scalar(select(func.count()).select_from(Issue))==0


def test_no_unit_conflict_does_not_queue_diagnostic(gateway):
    receipt=save(gateway,packet())
    with Session(engine()) as db:
        row=db.get(type(receipt),receipt.id)
        evt=db.scalar(select(Event).where(Event.kind=='perception.sensor_packet',Event.data['receipt_id'].as_string()==receipt.id))
        receive_packet(db,evt);db.flush();assert enqueue(db,evt,row) is None
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind==KIND))==0


def test_damaged_reception_proof_does_not_create_job(gateway):
    receipt=save(gateway,packet(units={'t':'F'}))
    with Session(engine()) as db:
        row=db.get(type(receipt),receipt.id)
        evt=db.scalar(select(Event).where(Event.kind=='perception.sensor_packet',Event.data['receipt_id'].as_string()==receipt.id))
        receive_packet(db,evt);db.flush()
        before=db.scalar(select(func.count()).select_from(Job).where(Job.kind==KIND))
        assert before==1
        evt.data={**evt.data,'report_hash':'not-the-original'};db.flush()
        with pytest.raises(HTTPException):enqueue(db,evt,row)
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind==KIND))==before


def test_status_shows_revoked_actor_and_recovers_original_queue_when_restored(gateway):
    from uuid import uuid4
    from generic_agents.models import User,Membership
    from generic_agents.security import Principal
    from generic_agents.mqtt_unit_quality_worker_v357 import gateway_status
    p=gateway[0];receipt=save(gateway,packet(units={'t':'F'}))
    with Session(engine()) as db:
        row=db.get(type(receipt),receipt.id)
        evt=db.scalar(select(Event).where(Event.kind=='perception.sensor_packet',Event.data['receipt_id'].as_string()==receipt.id))
        receive_packet(db,evt);db.flush();job=enqueue(db,evt,row);identity=job.id
        evt.status='received';evt.received_at=now();evt.attempts+=1
        admin=User(username='other_quality_admin_'+uuid4().hex,password_hash='unused');db.add(admin);db.flush()
        db.add(Membership(workspace_id=p.workspace_id,user_id=admin.id,role='admin'))
        old=db.scalar(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id));old.role='viewer'
        admin_id=admin.id;db.commit()
    observer=Principal(admin_id,p.workspace_id,'admin','test')
    with Session(engine()) as db:
        result=gateway_status(db,observer,gateway[1]);assert result['records'][0]['state']=='actor_unavailable'
        assert result['records'][0]['reference']==identity and not result['worker_online_verified']
        old=db.scalar(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id));old.role='admin';db.commit()
    with Session(engine()) as db:
        result=gateway_status(db,observer,gateway[1]);assert result['records'][0]['state']=='waiting_worker'
        assert result['records'][0]['reference']==identity
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind==KIND))==1


def test_status_does_not_call_damaged_original_waiting_worker(gateway):
    from generic_agents.mqtt_unit_quality_worker_v357 import gateway_status
    p=gateway[0];receipt=save(gateway,packet(units={'t':'F'}))
    with Session(engine()) as db:
        row=db.get(type(receipt),receipt.id)
        evt=db.scalar(select(Event).where(Event.kind=='perception.sensor_packet',Event.data['receipt_id'].as_string()==receipt.id))
        receive_packet(db,evt);db.flush();enqueue(db,evt,row)
        evt.status='received';evt.received_at=now();evt.attempts+=1;db.commit()
    with Session(engine()) as db:
        evt=db.scalar(select(Event).where(Event.kind=='perception.sensor_packet',Event.data['receipt_id'].as_string()==receipt.id))
        evt.data={**evt.data,'report_hash':'damaged'};db.commit()
    with Session(engine()) as db:
        result=gateway_status(db,p,gateway[1]);assert result['records'][0]['state']=='evidence_unverifiable'
        assert not result['worker_online_verified']


def test_actual_receive_and_gateway_worker_store_issue_without_changing_original(client,gateway):
    from generic_agents.models import Issue,Observation
    from generic_agents.evidence import evidence_bytes
    from generic_agents.mqtt_unit_quality_worker_v357 import tick
    from generic_agents.mqtt_unit_quality_v357 import KIND as ISSUE_KIND
    from generic_agents.worker import deliver_events
    p=gateway[0]
    raw=b'{"record_id":"raw-100","device_id":"sensor-1","observed_at":"2026-10-10T23:00:00Z","values":{"t":100000000000000.01},"units":{"t":"F"}}'
    receipt=save(gateway,raw)
    deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=receipt.id)
    with Session(engine()) as db:
        evt=db.scalar(select(Event).where(Event.kind=='perception.sensor_packet',Event.data['receipt_id'].as_string()==receipt.id))
        original_job=db.scalar(select(Job).where(Job.kind==KIND,Job.data['unit_request']['receipt_id'].as_string()==receipt.id))
        assert evt.status=='received' and evt.data['unit_quality_enqueue']['state']=='queued'
        identity=original_job.id;assert original_job.status=='queued'
    result=tick(p.workspace_id,gateway[1]);assert result['attempted']==1 and result['blocked']==0
    with Session(engine()) as db:
        job=db.get(Job,identity);assert job.status=='succeeded',job.data
        issue=db.scalar(select(Issue).where(Issue.kind==ISSUE_KIND))
        assert issue and issue.status=='open' and issue.entity_id is None
        assert issue.data['proof']['original_number_lexeme']=='100000000000000.01'
        assert issue.data['proof']['reported_unit']=='F' and issue.data['proof']['expected_unit']=='C'
        assert db.scalar(select(func.count()).select_from(Observation))==0
        assert evidence_bytes(db,p,receipt.evidence_id)[1]==raw
    response=client.get('/api/mqtt/gateways/'+gateway[1]+'/unit-quality')
    assert response.status_code==200,response.text
    saved=response.json();assert saved['issues'][0]['original_value_text']=='100000000000000.01'
    assert saved['issues'][0]['can_convert'] is False and saved['issues'][0]['can_close'] is False
    assert saved['records'][0]['state']=='job_finished_needs_evidence_check'
    assert not saved['worker_online_verified']


def test_enqueue_failure_does_not_block_original_ack_and_is_visible(client,gateway,monkeypatch):
    from generic_agents.worker import deliver_events
    from generic_agents.mqtt_unit_quality_worker_v357 import gateway_status
    from generic_agents import mqtt_unit_quality_dispatch_v357 as dispatch
    p=gateway[0];receipt=save(gateway,packet(units={'t':'F'}))
    monkeypatch.setattr(dispatch,'enqueue',lambda *_args:(_ for _ in ()).throw(HTTPException(409,'diagnostic blocked')))
    deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=receipt.id)
    with Session(engine()) as db:
        evt=db.scalar(select(Event).where(Event.kind=='perception.sensor_packet',Event.data['receipt_id'].as_string()==receipt.id))
        assert evt.status=='received' and evt.data['unit_quality_enqueue']['state']=='blocked'
        assert db.scalar(select(func.count()).select_from(Memory).where(Memory.data['type'].as_string()=='sensor_original'))==1
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind==KIND))==0
        assert gateway_status(db,p,gateway[1])['records'][0]['state']=='enqueue_blocked'


def test_worker_never_claims_another_gateway(gateway):
    from generic_agents.mqtt_inbox import create_gateway,set_status
    from generic_agents.mqtt_unit_quality_worker_v357 import tick
    from generic_agents.worker import deliver_events
    from tests.test_mqtt_inbox import config
    p=gateway[0]
    with Session(engine(),expire_on_commit=False) as db:
        other=create_gateway(db,p,'另一传感网关',config());set_status(db,p,other.id,other.version,'active')
        db.commit();other_gateway=(p,other.id,other.version)
    first=save(gateway,packet(record_id='first',units={'t':'F'}))
    second=save(other_gateway,packet(record_id='second',units={'t':'F'}))
    for item in (first,second):
        deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=item.id)
    with Session(engine()) as db:
        jobs={j.data['unit_request']['receipt_id']:j.id for j in db.scalars(select(Job).where(Job.kind==KIND)).all()}
        assert set(jobs)=={first.id,second.id}
    assert tick(p.workspace_id,gateway[1])['attempted']==1
    with Session(engine()) as db:
        assert db.get(Job,jobs[first.id]).status=='succeeded'
        other_job=db.get(Job,jobs[second.id]);assert other_job.status=='queued' and other_job.attempts==0


def test_general_workers_cannot_claim_gateway_diagnostics(gateway):
    from uuid import uuid4
    from generic_agents.mqtt_inbox import create_gateway,set_status
    from generic_agents.worker import claim,process_once,deliver_events,SETUP_KINDS
    from tests.test_mqtt_inbox import config
    p=gateway[0]
    with Session(engine(),expire_on_commit=False) as db:
        other=create_gateway(db,p,'另一网关',config());set_status(db,p,other.id,other.version,'active')
        db.commit();other_gateway=(p,other.id,other.version)
    items=[save(gateway,packet(record_id='scoped-a',units={'t':'F'})),
        save(other_gateway,packet(record_id='scoped-b',units={'t':'F'}))]
    for item in items:
        deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=item.id)
    with Session(engine()) as db:
        jobs={j.data['gateway_id']:j.id for j in db.scalars(select(Job).where(Job.kind==KIND))}
    assert KIND not in SETUP_KINDS
    assert claim() is None
    assert claim(workspace_id=p.workspace_id) is None
    assert not process_once(kinds=SETUP_KINDS,deliver=False,workspace_id=p.workspace_id)
    assert claim(job_id=jobs[gateway[1]],kinds=[KIND],workspace_id=p.workspace_id) is None
    assert claim(job_id=jobs[gateway[1]],kinds=[KIND],workspace_id=p.workspace_id,
        _mqtt_gateway_id=other_gateway[1]) is None
    for kwargs in ({'_mqtt_gateway_id':gateway[1]},
        {'_mqtt_gateway_id':gateway[1],'workspace_id':p.workspace_id,'job_id':jobs[gateway[1]],'kinds':[KIND,'diagnose_data']},
        {'_mqtt_gateway_id':'not-a-uuid','workspace_id':p.workspace_id,'job_id':jobs[gateway[1]],'kinds':[KIND]}):
        with pytest.raises(ValueError):claim(**kwargs)
    with pytest.raises(ValueError):
        process_once(job_id=jobs[gateway[1]],kinds=[KIND],workspace_id=p.workspace_id,
            _mqtt_gateway_id=gateway[1],deliver=True)
    with Session(engine(),expire_on_commit=False) as db:
        normal=Job(workspace_id=p.workspace_id,kind='ordinary_test_job_v357',
            idempotency_key='ordinary:'+str(uuid4()),data={'actor_id':p.user_id,
                'unit_request':{'schema':'ordinary-request'}})
        db.add(normal);db.commit();normal_id=normal.id
    lease=claim(workspace_id=p.workspace_id)
    assert lease and lease[0]==normal_id
    with Session(engine()) as db:
        assert all(db.get(Job,j).status=='queued' for j in jobs.values())


def test_expired_diagnostic_can_only_be_reclaimed_by_original_gateway(gateway):
    from datetime import timedelta
    from generic_agents.worker import claim,deliver_events
    from generic_agents.mqtt_unit_quality_worker_v357 import tick
    p=gateway[0];receipt=save(gateway,packet(units={'t':'F'}))
    deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=receipt.id)
    with Session(engine()) as db:
        job=db.scalar(select(Job).where(Job.kind==KIND));identity=job.id
        job.status='running';job.lease_until=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1)
        db.commit()
    assert claim() is None
    assert claim(job_id=identity,kinds=[KIND],workspace_id=p.workspace_id) is None
    assert tick(p.workspace_id,gateway[1])['attempted']==1
    with Session(engine()) as db:assert db.get(Job,identity).status=='succeeded'


@pytest.mark.parametrize('changed',['gateway','kind'])
def test_gateway_scope_is_rechecked_after_claim_before_execution(gateway,monkeypatch,changed):
    from uuid import uuid4
    from generic_agents import worker,job_recovery
    p=gateway[0];receipt=save(gateway,packet(units={'t':'F'}))
    worker.deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=receipt.id)
    with Session(engine()) as db:identity=db.scalar(select(Job.id).where(Job.kind==KIND))
    original=worker.claim
    def change_after_claim(*args,**kwargs):
        lease=original(*args,**kwargs)
        if lease:
            with Session(engine()) as db:
                row=db.get(Job,identity)
                if changed=='gateway':row.data={**row.data,'gateway_id':str(uuid4())}
                else:row.kind='ordinary_test_job_v357'
                db.commit()
        return lease
    recovered=[]
    monkeypatch.setattr(job_recovery,'recover_calls',lambda *args:recovered.append(True))
    monkeypatch.setattr(worker,'claim',change_after_claim)
    assert worker.process_once(job_id=identity,kinds=[KIND],deliver=False,
        workspace_id=p.workspace_id,_mqtt_gateway_id=gateway[1])
    with Session(engine()) as db:
        job=db.get(Job,identity)
        assert job.status=='running' and job.lease_until is not None
        assert db.scalar(select(func.count()).select_from(Issue))==0
    assert recovered==[]


@pytest.mark.parametrize('preserved_marker',['audit','key','schema'])
def test_generic_worker_never_reclaims_damaged_diagnostic_by_kind(gateway,preserved_marker):
    from datetime import timedelta
    from generic_agents.models import Audit
    from generic_agents.worker import claim,deliver_events
    p=gateway[0];receipt=save(gateway,packet(units={'t':'F'}))
    deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=receipt.id)
    with Session(engine()) as db:
        job=db.scalar(select(Job).where(Job.kind==KIND));identity=job.id
        job.kind='ordinary_test_job_v357';job.status='running'
        job.lease_until=db.scalar(select(func.clock_timestamp()))-timedelta(seconds=1)
        if preserved_marker!='key':job.idempotency_key='damaged-other-key'
        if preserved_marker!='schema':job.data={**job.data,'unit_request':{'schema':'damaged'}}
        if preserved_marker!='audit':
            audit=db.scalar(select(Audit).where(Audit.object_id==identity,
                Audit.action=='quality.mqtt_unit_diagnostic_queued_v357'))
            assert audit is not None;db.delete(audit)
        db.commit()
    assert claim(job_id=identity,workspace_id=p.workspace_id) is None
    assert claim() is None
    with Session(engine()) as db:
        assert db.get(Job,identity).status=='running'
