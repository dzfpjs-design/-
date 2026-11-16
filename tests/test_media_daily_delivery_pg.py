"""Disposable PG/file persistence, not an ASR/model/business-quality proof.

The source row and its actual queue Audit are deliberately synthetic. The
outbox, independent receipt transactions, file hashes and API are real. Root
owns running these checks; never run against a non-ga_test_ database.
"""
import copy
import hashlib
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import media_daily_delivery as s, models as m
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.security import Principal
from generic_agents.services import audit


@pytest.fixture
def media_scene(account, client):
    bound = engine()
    assert bound.dialect.name == 'postgresql' and bound.url.database.startswith('ga_test_')
    raw = b'public synthetic immutable receipt test original; not ASR acceptance'
    sha = hashlib.sha256(raw).hexdigest()
    with Session(bound, expire_on_commit=False) as db:
        user = db.scalar(select(m.User).where(m.User.username == account[0]))
        p = Principal(user.id, account[2], 'admin', 'media-daily-pg-test')
        path = settings().storage_dir / p.workspace_id / (sha + '.bin')
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        ev = m.Evidence(workspace_id=p.workspace_id, sha256=sha, size_bytes=len(raw),
            storage_key=p.workspace_id+'/'+sha+'.bin', media_type='audio/wav', data={})
        db.add(ev); db.flush()
        source = m.AudioRun(workspace_id=p.workspace_id, request_id=str(uuid4()), evidence_id=ev.id,
            status='succeeded', data={'evidence_id':ev.id, 'input_sha256':sha, 'test_only':True})
        db.add(source); db.flush()
        job = m.Job(workspace_id=p.workspace_id, kind='transcribe_audio', status='succeeded',
            idempotency_key='test-original:'+source.id, data={'run_id':source.id,'actor_id':p.user_id})
        db.add(job); db.flush(); source.data={**source.data,'job_id':job.id}
        audit(db,p,'audio.queued',source.id,{'input_sha256':sha,'test_only':True})
        daily = m.AudioDaily(workspace_id=p.workspace_id, received_day='2026-10-01',
            created_at=m.now()-timedelta(days=20), data={})
        db.add(daily); db.flush()
        value={'received_day':daily.received_day,'records':[{'kind':'transcription','id':source.id,
            'source_version':source.version,'input_sha256':sha}],'not_authoritative':True,'cloud_calls':0}
        memory=m.Memory(workspace_id=p.workspace_id,status='candidate',text='old producer candidate, not ACK',
            data={**value,'type':'audio_daily_summary','daily_id':daily.id,'receipt_hash':digest(value)})
        db.add(memory);db.flush()
        daily.data={**value,'receipt_hash':digest(value),'data_memory_id':memory.id}
        db.commit()
        return NS(bound=bound,p=p,path=path,raw=raw,daily_id=daily.id,source_id=source.id,
            producer_id=memory.id,job_id=job.id)


def publish(x):
    with Session(x.bound,expire_on_commit=False) as db:
        event=s.publish_revision(db,db.get(m.AudioDaily,x.daily_id),'audio');db.commit()
        return event.id


def received_memories(db,x):
    return list(db.scalars(select(m.Memory).where(m.Memory.workspace_id==x.p.workspace_id,
        m.Memory.data['type'].as_string()=='media_daily_received')))


def test_producer_and_receiver_are_distinct_cold_transactions_and_rollback_together(media_scene):
    x=media_scene;identity=publish(x)
    with Session(x.bound) as db:
        event=db.get(m.Event,identity)
        assert event.status=='pending_delivery' and not received_memories(db,x)
        assert s.receive(db,x.p,event)
        db.rollback()
    with Session(x.bound) as db:
        event=db.get(m.Event,identity)
        assert event.status=='pending_delivery' and not received_memories(db,x)
        assert s.receive(db,x.p,event);db.commit()
    with Session(x.bound) as db:
        event=db.get(m.Event,identity)
        assert s.checked(db,x.p,event)['state']['status']=='received'
        assert len(received_memories(db,x))==1 and event.attempts==1
        assert not s.receive(db,x.p,event)


def test_corrupt_file_blocks_until_explicit_same_event_cas_retry_and_nonce_recovery(media_scene,client):
    x=media_scene;identity=publish(x);x.path.write_bytes(b'corrupt original')
    s.consume_once(x.p.workspace_id)
    with Session(x.bound) as db:
        event=db.get(m.Event,identity);assert event.status=='blocked_delivery' and not received_memories(db,x)
        version=event.version;payload=copy.deepcopy(event.data)
    x.path.write_bytes(x.raw)
    s.consume_once(x.p.workspace_id)
    with Session(x.bound) as db:assert db.get(m.Event,identity).version==version
    body={'request_id':str(uuid4()),'expected_version':version,'payload_hash':payload['payload_hash'],'reason':'测试原件已恢复，仅接收原摘要'}
    result=client.post('/api/media-daily/events/'+identity+'/retry',json=body)
    assert result.status_code==200,result.text
    again=client.post('/api/media-daily/events/'+identity+'/retry',json=body)
    assert again.status_code==200 and again.json()['version']==result.json()['version']
    recovered=client.get('/api/media-daily/events/'+identity+'/requests/'+body['request_id'])
    assert recovered.status_code==200 and recovered.json()['found'] and recovered.json()['body']==body
    s.consume_once(x.p.workspace_id)
    with Session(x.bound) as db:
        event=db.get(m.Event,identity);assert event.status=='received' and event.data==payload
        assert len(received_memories(db,x))==1 and event.attempts==2


def test_closed_legacy_head_catches_up_without_new_source_or_model_job(media_scene):
    x=media_scene
    with Session(x.bound) as db:
        old_job_ids=list(db.scalars(select(m.Job.id).where(m.Job.workspace_id==x.p.workspace_id)))
        old_data=copy.deepcopy(db.get(m.AudioDaily,x.daily_id).data)
    s.consume_once(x.p.workspace_id)
    with Session(x.bound) as db:
        daily=db.get(m.AudioDaily,x.daily_id)
        events=list(db.scalars(select(m.Event).where(m.Event.workspace_id==x.p.workspace_id,m.Event.kind==s.KIND)))
        assert len(events)==1 and events[0].status=='received'
        assert daily.data==old_data and daily.version==1
        assert list(db.scalars(select(m.Job.id).where(m.Job.workspace_id==x.p.workspace_id)))==old_job_ids
        assert not list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id==x.p.workspace_id)))
    s.consume_once(x.p.workspace_id)
    with Session(x.bound) as db:assert len(received_memories(db,x))==1


def test_late_revision_keeps_both_original_summary_proofs(media_scene):
    x=media_scene;first=publish(x)
    with Session(x.bound) as db:
        source=db.get(m.AudioRun,x.source_id);source.version+=1;source.data={**source.data,'review':{'note':'normal later review'}}
        daily=db.get(m.AudioDaily,x.daily_id);daily.version+=1;value=s.summary_value(daily)
        value['records'][0]['source_version']=source.version
        memory=m.Memory(workspace_id=x.p.workspace_id,status='candidate',text='late revised candidate',
            data={**value,'type':'audio_daily_summary','daily_id':daily.id,'receipt_hash':digest(value)})
        db.add(memory);db.flush();daily.data={**value,'receipt_hash':digest(value),'data_memory_id':memory.id}
        second=s.publish_revision(db,daily,'audio');second_id=second.id;db.commit()
    s.consume_once(x.p.workspace_id)
    with Session(x.bound) as db:
        old,new=db.get(m.Event,first),db.get(m.Event,second_id)
        assert old.status==new.status=='received'
        assert s.checked(db,x.p,old)['body']['daily_revision']==1
        assert s.checked(db,x.p,new)['body']['daily_revision']==2
        assert len(received_memories(db,x))==2


def test_damaged_fifty_event_prefix_cannot_starve_valid_tail_and_cursor_is_durable(media_scene):
    x=media_scene
    with Session(x.bound) as db:
        for index in range(50):
            db.add(m.Event(workspace_id=x.p.workspace_id,kind=s.KIND,idempotency_key='damaged:'+str(index),
                status='pending_delivery',created_at=m.now()-timedelta(days=2),data={'test_only':'missing publication'}))
        db.commit()
    identity=publish(x)
    s.consume_once(x.p.workspace_id)
    with Session(x.bound) as db:assert db.get(m.Event,identity).status=='pending_delivery'
    s.consume_once(x.p.workspace_id)
    with Session(x.bound) as db:
        assert db.get(m.Event,identity).status=='received'
        damaged=list(db.scalars(select(m.Event).where(m.Event.workspace_id==x.p.workspace_id,
            m.Event.idempotency_key.like('damaged:%'))))
        assert len(damaged)==50 and all(event.status=='pending_delivery' for event in damaged)
        assert len(received_memories(db,x))==1


def test_missing_publication_never_becomes_a_forged_ack_and_api_exposes_integrity_failure(media_scene,client):
    x=media_scene;identity=publish(x)
    with Session(x.bound) as db:
        row=db.scalar(select(m.Audit).where(m.Audit.workspace_id==x.p.workspace_id,
            m.Audit.object_id==identity,m.Audit.action==s.PUBLICATION));db.delete(row);db.commit()
    s.consume_once(x.p.workspace_id)
    response=client.get('/api/media-daily/audio/'+x.daily_id)
    assert response.status_code==200 and response.json()['revisions'][0]['status']=='integrity_error'
    with Session(x.bound) as db:assert not received_memories(db,x)
