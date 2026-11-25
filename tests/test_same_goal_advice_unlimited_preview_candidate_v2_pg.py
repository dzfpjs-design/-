"""Protected v2 preview chain using disposable PG and local Evidence.

The Goal/frame/source selection and evaluation packet are substitutes; policy,
Plan, receipt, model registration, Evidence, model-specific grants, nonce and
preview Audits use actual PG/files. No SDK or cloud call is made. This is not a
real mature Goal, source approval, worker or paid end-to-end acceptance.
"""
import copy
import hashlib
from datetime import timedelta
from threading import Event, Thread
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from generic_agents import evidence
from generic_agents import same_goal_advice_contracts_v302 as old
from generic_agents import same_goal_advice_sources_v302 as sources
from generic_agents import same_goal_advice_unlimited_contracts_v2 as contracts
from generic_agents import same_goal_advice_unlimited_policy_v2 as policy
from generic_agents import same_goal_advice_unlimited_store_v2 as custody
from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as writer
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as document
from generic_agents import same_goal_advice_file_intent_candidate_v2 as intents
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as preview
from generic_agents import same_goal_advice_start_safety_candidate_v2 as start_safety
from generic_agents import same_goal_advice_common_inventory_v2 as common
from generic_agents.contracts import Manifest, digest
from generic_agents.db import engine
from generic_agents.models import Audit, Capability, CloudCall, DomainInstance, Evidence, InstanceRelease, Invocation, Job, Plan, Task, uid
from tests.test_same_goal_advice_contracts_v302 import scene as contract_scene
from tests.test_same_goal_advice_projection_v307 import scene as projection_scene
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


@pytest.fixture
def prepared_preview(synthetic_custody,monkeypatch,request):
    # The integrated file-intent gate requires its unique index in the disposable PG.
    from generic_agents.same_goal_advice_intent_index_migration_candidate_v2 import rehearse
    assert rehearse()['status'] in {'installed','already_installed'}
    s=synthetic_custody;p=s['principal'];prep=copy.deepcopy(s['prep']);sample=contract_scene()
    deadline_seconds=getattr(request,'param',300)
    with monkeypatch.context() as m:
        frame=copy.deepcopy(projection_scene.__wrapped__(m)['frame'])
    data_text=frame['decision_input']['sources'][0]['text'];doc_text=frame['decision_input']['sources'][1]['text']
    model_data=Manifest(kind='foundation_model',namespace='preview_fixture',name='fixed_model',version='1.0.0',
        owner='synthetic',description='synthetic model',input_schema={'type':'object'},output_schema={'type':'object'},
        allowed_agent_roles=['business'],execution={'provider':'deepseek','model':'deepseek-v4-pro',
        'thinking_mode':'disabled','max_tokens':4096,'secret_ref':'DEEPSEEK_API_KEY'},data_policy={'cloud_allowed':True},
        budget={'max_attempts':1,'max_concurrency':1},dependencies=[],specification={}).model_dump(mode='json')
    source_manifest=copy.deepcopy(sample.source);source_manifest['execution']['model_profile_hash']=digest(model_data)
    source_pin={**prep['source_business_capability_ref'],'content_hash':digest(source_manifest)}
    manifest=old.build_manifest(source_manifest,source_pin)
    prep.update(source_business_capability_ref=source_pin,model_pin={**prep['model_pin'],'content_hash':digest(model_data)},
        advice_capability_ref={**prep['advice_capability_ref'],'content_hash':digest(manifest)})
    prep.pop('record_hash')
    with Session(engine(),expire_on_commit=False) as db:
        task=db.get(Task,s['task_id']);cap=db.get(Capability,prep['advice_capability_ref']['id'])
        instance=DomainInstance(id=prep['scope']['instance_id'],workspace_id=p.workspace_id,
            namespace='synthetic_preview',status='partially_active',active_release_id=None,data={})
        db.add(instance);db.flush()
        release=InstanceRelease(id=prep['scope']['release_id'],workspace_id=p.workspace_id,instance_id=instance.id,
            release_number=1,configuration_hash='c'*64,package_id=prep['scope']['package_id'],
            actor_id=p.user_id,status='published',data={})
        db.add(release);db.flush();instance.active_release_id=release.id
        now=db.scalar(select(func.clock_timestamp()))
        goal_spec={'deadline':(now+timedelta(seconds=deadline_seconds)).isoformat()}
        goal_hash=digest(goal_spec)
        goal={'version':1,'hash':goal_hash,'spec':goal_spec,'time':{}}
        task.data={**task.data,'goal_confirmed_by':p.user_id,'goal_version':1,'goal_hash':goal_hash,
            'goal_spec':goal_spec,'goal_window':{}}
        prep['scope'].update(goal_version=1,goal_hash=goal_hash,goal_snapshot_hash=digest(goal))
        prep['request']['expected_goal_hash']=goal_hash;prep['request_hash']=digest(prep['request'])
        cap.data=manifest;cap.content_hash=cap.validated_hash=digest(manifest)
        model=Capability(id=prep['model_pin']['id'],workspace_id=p.workspace_id,namespace='preview_fixture',
            name='fixed_model',release_version='1.0.0',kind='foundation_model',status='published',
            content_hash=digest(model_data),validated_hash=digest(model_data),data=model_data)
        db.add(model)
        db.add(Capability(id=source_pin['id'],workspace_id=p.workspace_id,namespace='preview_source_fixture',
            name='source',release_version=source_pin['release_version'],kind='tool',status='published',
            content_hash=digest(source_manifest),validated_hash=digest(source_manifest),data=source_manifest))
        pol=db.get(Audit,s['policy_id']);value=copy.deepcopy(pol.data)
        value['scope']={key:copy.deepcopy(item) for key,item in prep['scope'].items() if key!='analysis_plan_id'}
        for name in ('source_business_capability_ref','advice_capability_ref','model_pin'):value[name]=copy.deepcopy(prep[name])
        value['record_hash']=old.record_hash(value);pol.data=value
        auth=db.scalar(select(Audit).where(Audit.object_id==task.id,Audit.action==policy.AUTHORIZATION))
        auth_body={**auth.data['body'],'expected_goal_hash':goal_hash}
        auth.data=policy._auth_body(task,p,auth_body,pol)
        prep['budget']['policy_ref']={'id':pol.id,'record_hash':digest(value)}
        prep=contracts.seal_preparation(prep,manifest=manifest);s['prep']=prep
        db.delete(db.get(Job,s['job_id']));db.delete(db.get(Audit,s['accepted_id']));db.delete(db.get(Plan,s['plan_id']))
        db.commit()
    source_ref=prep['request']['document_source']['source_set_ref'];raw=doc_text.encode('utf-8')
    resolved={'selection':copy.deepcopy(prep['request']['document_source']),
        'approved_selection':{**source_ref,'submission_id':'00000000-0000-0000-0000-000000000001',
            'submission_hash':'a'*64,'input_key':'runtime_document_sources'},
        'source_set':{'source_snapshot_hash':'b'*64},'content':raw,'sha256':hashlib.sha256(raw).hexdigest(),
        'size_bytes':len(raw),'packet_receipt':None,'evidence_ref':None}
    monkeypatch.setattr(writer.candidate,'derive_candidate',lambda *_:{'preparation_candidate':copy.deepcopy(prep)})
    monkeypatch.setattr(document.sources,'resolve_documents',lambda *_:copy.deepcopy(resolved))
    with Session(engine()) as db:created=writer.prepare(db,p,s['task_id'],prep['request'],prep['budget']['policy_ref']);db.commit()
    with Session(engine()) as db:intents.register(db,p,s['task_id'],created['job_id']);db.commit()
    with Session(engine()) as db:document.process(db,p,s['task_id'],created['job_id']);db.commit()
    with Session(engine(),expire_on_commit=False) as db:
        doc=document.cold_read(db,p,db.get(Task,s['task_id']),db.get(Plan,created['plan_id']))
        data=evidence.save_evidence(db,p,data_text.encode(),'text/plain');db.flush()
        data_ref=sources._text(data,data_text.encode());doc_ref=doc['document']['evidence_ref']
        for identity in (data.id,doc_ref['evidence_id']):
            item=db.get(Evidence,identity);evidence.authorize_cloud(db,p,identity,prep['model_pin']['id'],item.version,'synthetic explicit consent')
        db.commit()
    source=contracts.seal_source({'schema':old.SOURCE,'plan_id':created['plan_id'],
        'mature_head':copy.deepcopy(prep['mature_head']),'original_query_ref':prep['original_query_ref'],
        'evaluation_query_ref':prep['evaluation_query_ref'],'evaluation_cut':prep['evaluation_cut'],
        'evaluation_generation':2,'data_evidence_ref':data_ref,'document':doc['document']},preparation=prep,manifest=manifest)
    frame['source_record']=source;frame['model_pin']=prep['model_pin'];frame['instruction']=prep['instruction']
    frame['decision_input']['scope']['goal_snapshot_hash']=prep['scope']['goal_snapshot_hash']
    frame['capability_pins']['goal_snapshot_hash']=prep['scope']['goal_snapshot_hash']
    frame['capability_pins']['pins_hash']=digest({k:v for k,v in frame['capability_pins'].items() if k!='pins_hash'})
    frame['evidence_ids']=[data_ref['evidence_id'],doc_ref['evidence_id']]
    rows=frame['decision_input']['sources'];rows[0].update(source_id=prep['mature_head']['evaluation_ref']['id'],content_hash=data_ref['sha256'],text=data_text)
    rows[1].update(source_id=doc_ref['evidence_id'],content_hash=doc_ref['sha256'],text=doc_text)
    monkeypatch.setattr(preview.frame_builder,'build_current',lambda *_,**__:copy.deepcopy(frame))
    monkeypatch.setattr(preview,'_verify_evaluation',lambda *_:None)
    # This local PG fixture has no genuine Goal evaluation or approval rows.
    monkeypatch.setattr(start_safety,'_lock_approved_sources',lambda *_:None)
    monkeypatch.setattr(start_safety,'check_freshness',lambda *_:None)
    request={'request_id':uid(),'expected_plan_version':2,'expected_goal_hash':prep['scope']['goal_hash'],
        'expected_mature_head_hash':prep['mature_head']['head_hash'],'reason':'合成预览'}
    return s,created,request,frame


def test_durable_original_preview_and_replay_without_send(prepared_preview):
    s,created,request,frame=prepared_preview;p=s['principal']
    with Session(engine()) as db:first=preview.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    assert first['plan_version']==3 and first['price_state']=='unknown' and first['cloud_send_authorized'] is False
    engine().dispose()
    with Session(engine()) as db:
        second=preview.prepare(db,p,s['task_id'],created['plan_id'],request)
        assert second==first
        assert common.read(db,p,db.get(Task,s['task_id']))['queued_v2']==1
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==preview.ACTION))==1
        assert not list(db.scalars(select(CloudCall))) and not list(db.scalars(select(Invocation)))


def test_altered_preview_or_different_request_cannot_create_replacement(prepared_preview):
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:preview.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    with Session(engine()) as db,pytest.raises(HTTPException):
        preview.prepare(db,p,s['task_id'],created['plan_id'],{**request,'reason':'different'})
    with Session(engine()) as db:
        row=db.scalar(select(Audit).where(Audit.action==preview.ACTION));row.data={**row.data,'record_hash':'0'*64};db.commit()
    with Session(engine()) as db,pytest.raises(HTTPException):
        custody.read_prepared(db,p,db.get(Task,s['task_id']),db.get(Plan,created['plan_id']))


def test_concurrent_same_nonce_returns_single_original(prepared_preview):
    from queue import Queue
    from time import monotonic, sleep
    from sqlalchemy import text
    s,created,request,_=prepared_preview;p=s['principal'];second_pid=Queue();out=[];errors=[]
    def contender():
        try:
            with Session(engine()) as db:
                second_pid.put(db.scalar(select(func.pg_backend_pid())))
                out.append(preview.prepare(db,p,s['task_id'],created['plan_id'],request));db.commit()
        except Exception as e:errors.append(e)
    with Session(engine()) as first:
        first_pid=first.scalar(select(func.pg_backend_pid()))
        original=preview.prepare(first,p,s['task_id'],created['plan_id'],request)
        thread=Thread(target=contender,daemon=True);thread.start();pid=second_pid.get(timeout=5)
        try:
            deadline=monotonic()+5;blocked=False
            while monotonic()<deadline:
                with engine().connect() as observer:
                    blocked=first_pid in observer.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':pid})
                if blocked:break
                sleep(.05)
            assert blocked,'second original did not wait on the first transaction'
        finally:first.commit()
        thread.join(timeout=10);assert not thread.is_alive() and not errors
    assert out==[original]
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==preview.ACTION))==1
        assert db.get(Plan,created['plan_id']).version==3 and not list(db.scalars(select(CloudCall)))


def test_saved_preview_stays_historical_but_new_preview_needs_current_consent(prepared_preview):
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:original=preview.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    with Session(engine()) as db:
        record=db.scalar(select(Audit).where(Audit.action==preview.ACTION));identity=record.data['preview_record']['evidence_pins'][0]['evidence_id']
        item=db.get(Evidence,identity);item.data={**item.data,'cloud_model_ids':[]};item.version+=1;db.commit()
    with Session(engine()) as db:
        assert preview.prepare(db,p,s['task_id'],created['plan_id'],request)==original
        with pytest.raises(HTTPException):preview.prepare(db,p,s['task_id'],created['plan_id'],
            {**request,'request_id':uid(),'expected_plan_version':3})
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==preview.ACTION))==1
        assert db.get(Plan,created['plan_id']).version==3 and not list(db.scalars(select(CloudCall)))


def test_preview_session_expires_naturally_before_commit(prepared_preview,monkeypatch):
    import time
    from generic_agents.models import AuthSession
    s,created,request,frame=prepared_preview;p=s['principal']
    with Session(engine()) as db:
        session=db.get(AuthSession,p.session_id);session.expires_at=db.scalar(select(func.clock_timestamp()))+timedelta(seconds=2);db.commit()
    def slow(*_):time.sleep(2.5);return copy.deepcopy(frame)
    monkeypatch.setattr(preview.frame_builder,'build_current',slow)
    with Session(engine()) as db,pytest.raises(HTTPException):preview.prepare(db,p,s['task_id'],created['plan_id'],request)
    with Session(engine()) as db:
        assert db.get(Plan,created['plan_id']).version==2
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==preview.ACTION))==0
        assert not list(db.scalars(select(CloudCall)))


def test_new_generation_preserves_exact_original_receipt(prepared_preview):
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:first=preview.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    next_request={**request,'request_id':uid(),'expected_plan_version':3}
    with Session(engine()) as db:second=preview.prepare(db,p,s['task_id'],created['plan_id'],next_request);db.commit()
    assert first['plan_version']==3 and second['plan_version']==4
    with Session(engine()) as db:
        assert preview.prepare(db,p,s['task_id'],created['plan_id'],request)==first
        assert preview.prepare(db,p,s['task_id'],created['plan_id'],next_request)==second
        assert common.read(db,p,db.get(Task,s['task_id']))['queued_v2']==1
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==preview.ACTION))==2
        assert db.get(Plan,created['plan_id']).version==4 and not list(db.scalars(select(CloudCall)))


def test_preview_expiring_before_outer_commit_rolls_back(prepared_preview):
    import time
    from generic_agents.models import AuthSession
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:
        session=db.get(AuthSession,p.session_id);session.expires_at=db.scalar(select(func.clock_timestamp()))+timedelta(seconds=5);db.commit()
    with Session(engine()) as db:
        preview.prepare(db,p,s['task_id'],created['plan_id'],request)
        delay=(db.get(AuthSession,p.session_id).expires_at-db.scalar(select(func.clock_timestamp()))).total_seconds()
        time.sleep(max(0,delay)+.1)
        with pytest.raises(HTTPException):db.commit()
        db.rollback()
    with Session(engine()) as db:
        assert db.get(Plan,created['plan_id']).version==2
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==preview.ACTION))==0
        assert not list(db.scalars(select(CloudCall)))


def test_changed_original_file_blocks_preview_history(prepared_preview):
    from generic_agents.config import settings
    s,created,request,_=prepared_preview;p=s['principal']
    with Session(engine()) as db:preview.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    with Session(engine()) as db:
        record=db.scalar(select(Audit).where(Audit.action==preview.ACTION))
        identity=record.data['preview_record']['evidence_pins'][0]['evidence_id']
        path=settings().storage_dir / db.get(Evidence,identity).storage_key
        path.write_bytes(b'changed synthetic evidence')
    with Session(engine()) as db,pytest.raises(HTTPException):
        custody.read_prepared(db,p,db.get(Task,s['task_id']),db.get(Plan,created['plan_id']))
    with Session(engine()) as db:
        assert db.get(Plan,created['plan_id']).version==3
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action==preview.ACTION))==1
        assert not list(db.scalars(select(CloudCall)))


def test_v1_http_replay_survives_v2_preview_but_new_v1_remains_blocked(prepared_preview,client,monkeypatch):
    from tests.test_same_goal_advice_v1_history_compat_pg import add_cancelled
    from tests.test_same_goal_advice_v1_replay_compat_pg import use_synthetic_task
    from tests import test_same_goal_advice_common_inventory_v2_pg as factory
    s,created,request,_=prepared_preview;p=s['principal'];use_synthetic_task(monkeypatch)
    sample=contract_scene();legacy_cap=uid()
    sample.preparation['advice_capability_ref']['id']=legacy_cap
    with Session(engine()) as db:
        db.add(Capability(id=legacy_cap,workspace_id=p.workspace_id,namespace='legacy_preview_fixture',
            name='historical_advice',release_version='1.0.0',kind='tool',status='published',
            content_hash=digest(sample.manifest),validated_hash=digest(sample.manifest),data=sample.manifest))
        db.flush()
        with monkeypatch.context() as m:
            m.setattr(factory,'contract_scene',lambda:sample)
            old_id=add_cancelled(db,s)
        old=db.get(Plan,old_id)
        old_prepare=copy.deepcopy(old.data['operations'][0]['body'])
        old_cancel=copy.deepcopy(old.data['operations'][1]['body']);db.commit()
    with Session(engine()) as db:preview.prepare(db,p,s['task_id'],created['plan_id'],request);db.commit()
    with Session(engine()) as db:
        counts={model.__name__:db.scalar(select(func.count()).select_from(model)) for model in (Plan,Job,Audit,CloudCall,Invocation)}
    from generic_agents import same_goal_advice_store_v302 as legacy
    with Session(engine()) as db:legacy._inventory(db,p,db.get(Task,s['task_id']),history_compatible=True)
    base='/api/workspace/tasks/'+s['task_id']+'/business-advice/preparations'
    response=client.post(base,json=old_prepare)
    assert response.status_code==201,response.text
    assert client.post(base+'/'+old_id+'/cancel',json=old_cancel).status_code==200
    assert client.post(base,json={**old_prepare,'request_id':uid()}).status_code==409
    with Session(engine()) as db:
        assert {model.__name__:db.scalar(select(func.count()).select_from(model)) for model in (Plan,Job,Audit,CloudCall,Invocation)}==counts
