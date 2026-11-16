"""Real native sound/ACK with the existing audio/media daily chain; no model fake.

The shared native fixture requires the pinned sound runtime/assets. Tests use a
synthetic silence WAV for engineering, not sound-event domain quality evidence.
"""
import copy
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from generic_agents import models as m, audio_daily, media_daily_delivery as delivery, sound_daily
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.security import Principal
from generic_agents.sound_models import EnvironmentSoundRun
from generic_agents import native_sound_provenance as origin
from tests.test_workflow_sound_native_pg import sound_scene, original_scene, ack, good


def principal(db, account):
    user=db.scalar(select(m.User).where(m.User.username==account[0]))
    return Principal(user.id,account[2],'admin','sound-daily-real-pg-test')


def legacy_record():
    return {'key':'transcription:legacy','kind':'transcription','status':'succeeded','integrity_verified':True,
        'input_sha256':'a'*64,'origin_key':'transcription:original','task_id':None,'review_state':'unreviewed',
        'saved_asr_segments':0,'whole_asr_performed':True,'vad_intervals':0,'no_speech_candidates':False,'late_processing':False}


def test_sound_summary_does_not_relabel_or_change_legacy_asr_vad_metrics():
    old=legacy_record();before=copy.deepcopy(old);legacy=audio_daily.summary([old])
    machine={'kind':'environment_sound','sound_windows':4,'sound_candidate_classes':2,'review_state':'confirmed'}
    combined=audio_daily.summary([old,machine])
    assert {key:value for key,value in combined.items() if key!='environment_sounds'}==legacy
    assert old==before and 'environment_sounds' not in legacy
    assert combined['environment_sounds']=={'received_runs':1,'window_count':4,'candidate_class_count':2,
        'reviewed_runs':1,'date_basis':'machine_result_registered','recording_time_verified':False,'not_authoritative':True}


def test_old_record_survives_later_valid_review_but_not_changed_original_machine_values():
    review={'request_id':'new-review','receipt_hash':'review-hash','verdict':'uncertain'}
    run=SimpleNamespace(data={'reviews':[review]})
    current={'source_version':3,'task_version':9,'task_status':'workflow_completed',
        'review_state':'confirmed','review_hash':digest([review]),'last_review_verdict':'uncertain',
        'review_receipt_hashes':['review-hash'],'sound_windows':4,'sound_candidate_classes':2}
    old={**current,'source_version':2,'task_version':8,'task_status':'workflow_waiting',
        'review_state':'unreviewed','review_hash':digest([]),'last_review_verdict':None,'review_receipt_hashes':[]}
    assert sound_daily._verified_record(old,current,run)==[]
    for key in ('sound_windows','sound_candidate_classes'):
        bad={**old,key:old[key]+1}
        with pytest.raises(HTTPException):sound_daily._verified_record(bad,current,run)
    with pytest.raises(HTTPException):sound_daily._verified_record({**old,'review_hash':digest([review])},current,run)


def test_real_sound_ack_daily_separate_receiver_retry_dedup_and_human_late_revision(sound_scene,account,monkeypatch):
    s=sound_scene
    with Session(engine()) as db:
        p=principal(db,account);observation=db.get(m.Observation,s.entry['reception']['observation_id'])
        day=observation.business_day
        assert len(sound_daily.pending(db,p,day))==1
        assert sound_daily.pending(db,p,day)[0]['window_count'] is None
        assert sound_daily.reconcile(db,p,day) is None
        assert db.scalar(select(func.count()).select_from(m.AudioDaily))==0
        db.commit()
    received=ack(s);assert received['reception']['state']=='received'
    with Session(engine()) as db:
        p=principal(db,account)
        # Root's real ACK hook must have persisted the original registration-day collection.
        daily=db.scalar(select(m.AudioDaily).where(m.AudioDaily.workspace_id==p.workspace_id,m.AudioDaily.received_day==day))
        assert daily is not None
        assert daily.data['environment_sounds']['received_runs']==1
        assert daily.data['environment_sounds']['window_count']==len(s.run['result']['classification']['windows'])
        assert daily.data['whole_transcriptions']==0 and daily.data['vad_intervals']==0
        assert not sound_daily.pending(db,p,day)
        source=daily.data['records'][0];assert source['registration_day']==day and source['source_time']=='unknown'
        first_version=daily.version;first_memory_id=daily.data['data_memory_id'];first_memory=copy.deepcopy(db.get(m.Memory,first_memory_id).data)
        event=db.scalar(select(m.Event).where(m.Event.workspace_id==p.workspace_id,m.Event.kind=='media.daily'))
        assert event.status=='pending_delivery';event_id=event.id
        assert not db.scalars(select(m.Memory).where(m.Memory.data['type'].as_string()=='media_daily_received')).all()
        sound_daily.reconcile(db,p,day);db.commit()
        assert db.get(m.AudioDaily,daily.id).version==first_version
    assert delivery.consume_once(p.workspace_id)['received']==1
    with Session(engine()) as db:
        event=db.get(m.Event,event_id);proof=delivery.checked(db,p,event)
        assert event.status=='received';delivery.validate_sources(db,p,proof['body'])
        old_event_data=copy.deepcopy(event.data)
        assert db.get(m.Memory,first_memory_id).data==first_memory
    current=good(s.client.get('/api/workspace/tasks/'+s.task['id']+'/sounds'))['records'][0]['run']
    review=good(s.client.post('/api/sounds/runs/'+current['id']+'/reviews',json={
        'schema':'sound-review-v1','request_id':'daily-review-'+uuid4().hex,'version':current['version'],
        'verdict':'uncertain','note':'候选声音无法独立证实现场异常；保留原分数。'}),201)
    with Session(engine()) as db:
        p=principal(db,account);audio_daily.reconcile(db,p.workspace_id);db.commit()
        daily=db.scalar(select(m.AudioDaily).where(m.AudioDaily.workspace_id==p.workspace_id,m.AudioDaily.received_day==day))
        assert daily.version==first_version+1
        assert daily.data['environment_sounds']['reviewed_runs']==1
        assert daily.data['records'][0]['review_receipt_hashes']==[review['receipt_hash']]
        old=db.get(m.Event,event_id);assert old.data==old_event_data
        delivery.validate_sources(db,p,delivery.checked(db,p,old)['body'])
        assert db.get(m.Memory,first_memory_id).data==first_memory
        # A later collector date is a test clock only; source/ACK clocks and bytes are untouched.
        actual_now=m.now();monkeypatch.setattr(audio_daily,'now',lambda:actual_now+timedelta(days=2))
        sound_daily.reconcile(db,p,day);db.commit()
        assert daily.received_day==day
        assert db.scalar(select(func.count()).select_from(m.AudioDaily).where(m.AudioDaily.workspace_id==p.workspace_id))==1
        assert db.scalar(select(func.count()).select_from(m.Invocation))==1
        assert db.scalar(select(func.count()).select_from(m.CloudCall))==0


def test_bad_original_blocks_daily_reception_and_original_revision_can_recover_without_inference(sound_scene,account):
    from generic_agents.config import settings
    s=sound_scene;received=ack(s)
    with Session(engine()) as db:
        p=principal(db,account);daily=sound_daily.reconcile(db,p,received['reception']['receipt']['registration_day']);db.commit()
        event=db.scalar(select(m.Event).where(m.Event.workspace_id==p.workspace_id,m.Event.kind=='media.daily'))
        event_id=event.id;original_payload=event.data['payload_hash']
        stored=db.get(m.Evidence,s.attached['snapshot']['evidence_id']);path=settings().storage_dir/stored.storage_key
        original=path.read_bytes();invocations=db.scalar(select(func.count()).select_from(m.Invocation))
    try:
        path.write_bytes(original[:-1]+bytes([original[-1]^1]))
        assert delivery.consume_once(p.workspace_id)['received']==0
        with Session(engine()) as db:
            assert db.get(m.Event,event_id).status=='blocked_delivery'
            # The original sound ACK remains its own immutable receipt, not a repaired one.
            original_data_event=db.get(m.Event,received['reception']['event_id']);assert original_data_event.status=='received'
        path.write_bytes(original)
        with Session(engine()) as db:
            event=db.get(m.Event,event_id)
            body=delivery.Retry(request_id=uuid4(),expected_version=event.version,payload_hash=original_payload,reason='原件已按原摘要恢复，仅重试该日修订接收')
            delivery.retry(db,p,event,body);db.commit()
        assert delivery.consume_once(p.workspace_id)['received']==1
        with Session(engine()) as db:
            assert db.get(m.Event,event_id).status=='received'
            assert db.scalar(select(func.count()).select_from(m.Invocation))==invocations
    finally:
        path.write_bytes(original)


def test_saved_sound_daily_trace_cannot_be_silently_removed_or_read_from_another_workspace(sound_scene,account):
    s=sound_scene;received=ack(s)
    with Session(engine()) as db:
        p=principal(db,account);day=received['reception']['receipt']['registration_day']
        daily=sound_daily.reconcile(db,p,day);db.commit()
        saved=copy.deepcopy(daily.data);old_count=db.scalar(select(func.count()).select_from(m.Memory))
        foreign=Principal(p.user_id,str(uuid4()),'admin','foreign-sound-daily-read')
        with pytest.raises(HTTPException):sound_daily.source_pin(db,foreign,s.run['id'])
        # Explicit isolated integrity fault: retain the independently saved daily/issued trace.
        issued=db.scalar(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==origin.ISSUED_ACTION,
            m.Audit.object_id==received['reception']['observation_id']))
        db.delete(issued);db.commit()
        with pytest.raises(HTTPException):sound_daily.reconcile(db,p,day)
        db.rollback();db.expire_all()
        assert db.get(m.AudioDaily,daily.id).data==saved
        assert db.scalar(select(func.count()).select_from(m.Memory))==old_count



def test_persisted_legacy_pending_asr_and_real_sound_survive_unscoped_legacy_refresh(sound_scene,account):
    """Historical pending ASR is a persistence compatibility fixture, not ASR inference.

    The sound side uses the normal real CPU/ACK fixture. Legacy original/queue
    anchors use real Evidence/Audit rows; no old ASR model result is invented.
    """
    from generic_agents.evidence import save_immutable
    from generic_agents.local_audio import RECIPE_HASH as ASR_RECIPE
    from generic_agents.services import audit
    s=sound_scene;received=ack(s);day=received['reception']['receipt']['registration_day']
    with Session(engine()) as db:
        p=principal(db,account);stamp=db.get(m.Observation,received['reception']['observation_id']).created_at
        evidence=save_immutable(db,p,s.actual_raw,'audio/wav',{'modality':'audio','purpose':'legacy_pending_compatibility_fixture'})
        legacy=m.AudioRun(workspace_id=p.workspace_id,request_id=str(uuid4()),evidence_id=evidence.id,status='queued',created_at=stamp,
            data={'name':'兼容性：原有待处理语音','input_sha256':evidence.sha256,'size_bytes':evidence.size_bytes,'media_type':'audio/wav',
                'recipe_hash':ASR_RECIPE,'configuration':{'channel':'mono','language':'auto'},'generation':1,'result':None,
                'result_hash':None,'review':None,'error':None,'stage':'queued'})
        db.add(legacy);db.flush()
        job=m.Job(workspace_id=p.workspace_id,kind='transcribe_audio',status='queued',idempotency_key='audio:'+legacy.id+':1',
            data={'run_id':legacy.id,'generation':1,'actor_id':p.user_id})
        db.add(job);db.flush();legacy.data={**legacy.data,'job_id':job.id}
        audit(db,p,'audio.queued',legacy.id,{'input_sha256':evidence.sha256,'recipe_hash':ASR_RECIPE,'cloud_calls':0})
        db.commit();legacy_id=legacy.id
        audio_daily.reconcile(db,p.workspace_id);db.commit()
        daily=db.scalar(select(m.AudioDaily).where(m.AudioDaily.workspace_id==p.workspace_id,m.AudioDaily.received_day==day))
        assert {item['kind'] for item in daily.data['records']}=={'transcription','environment_sound'}
        assert daily.data['pending_ids']==['transcription:'+legacy_id]
        original_memory_id=daily.data['data_memory_id'];original_memory=copy.deepcopy(db.get(m.Memory,original_memory_id).data)
        # The signed old day is a real persisted revision, not a mocked source_groups result.
        original_event=db.scalar(select(m.Event).where(m.Event.workspace_id==p.workspace_id,m.Event.kind=='media.daily',
            m.Event.data['daily_revision'].as_integer()==daily.version));original_event_id=original_event.id
        original_payload=copy.deepcopy(original_event.data)
    cancelled=good(s.client.post('/api/audio/'+legacy_id+'/cancel',json={'version':1}))
    assert cancelled['status']=='cancelled'
    with Session(engine()) as db:
        # This previously routed an environment_sound row into legacy ASR's record().
        audio_daily.reconcile(db);db.commit()
        daily=db.scalar(select(m.AudioDaily).where(m.AudioDaily.workspace_id==p.workspace_id,m.AudioDaily.received_day==day))
        assert {item['kind'] for item in daily.data['records']}=={'transcription','environment_sound'}
        assert daily.data['cancelled_ids']==['transcription:'+legacy_id]
        assert daily.data['environment_sounds']['received_runs']==1
        assert db.get(m.Memory,original_memory_id).data==original_memory
        assert db.get(m.Event,original_event_id).data==original_payload
        delivery.validate_sources(db,p,delivery.checked(db,p,db.get(m.Event,original_event_id))['body'])


@pytest.mark.parametrize('key',['sound_windows','sound_candidate_classes','result_hash'])
def test_daily_source_rejects_changed_summary_machine_values_despite_valid_run(sound_scene,account,key):
    s=sound_scene;received=ack(s);day=received['reception']['receipt']['registration_day']
    with Session(engine()) as db:
        p=principal(db,account);daily=sound_daily.reconcile(db,p,day);db.commit()
        before=copy.deepcopy(daily.data);record=next(item for item in daily.data['records'] if item['kind']=='environment_sound')
        bad=copy.deepcopy(record);bad[key]='0'*64 if key=='result_hash' else bad[key]+1
        with pytest.raises(HTTPException):delivery._source_rows(db,p,'audio',bad)
        db.rollback();assert db.get(m.AudioDaily,daily.id).data==before


def test_daily_collection_fault_is_visible_without_rolling_back_source_ack_and_explicit_refresh_recovers(sound_scene,account,monkeypatch):
    s=sound_scene
    def rejected(*args,**kwargs):raise HTTPException(409,'test: day store temporarily unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(audio_daily,'save_collection',rejected)
        received=ack(s)
    assert received['reception']['state']=='received'
    day=received['reception']['receipt']['registration_day']
    with Session(engine()) as db:
        p=principal(db,account);status=sound_daily.collection_status(db,p,day)
        assert status['state']=='collection_blocked' and status['affected_run_id']==s.run['id'] and status['reason']
        source_event=db.get(m.Event,received['reception']['event_id']);source_before=copy.deepcopy(source_event.data)
        assert source_event.status=='received'
        sound_daily.reconcile(db,p,day);db.commit()
        assert sound_daily.collection_status(db,p,day)['state']=='collected'
        assert db.get(m.Event,source_event.id).data==source_before
        assert db.scalar(select(func.count()).select_from(m.Invocation))==1
