"""Real PG provenance and handoff; video inference in these tests is mocked."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Task, Membership, Event, VideoFrame, Audit
from generic_agents.security import Principal
from generic_agents import video_workflow_handoff_v347 as h
from tests.test_media import media_runtime, capture, finish
from tests.test_local_specialists import mocked_runtime
from tests.test_instance_perception import linked


def saved_candidate(client,account):
    installation,ch,release,obj,_,_=linked(client)
    _,video=finish(client,capture(client,ch,installation).json())
    assert video['alerts'] and video['alerts'][0]['task_id']
    return video['alerts'][0]['task_id'],release,obj


def principal(db,account):
    membership=db.scalar(select(Membership).where(Membership.workspace_id==account[2]))
    return Principal(membership.user_id,account[2],membership.role,'test')


def body(release,obj):
    return SimpleNamespace(release_id=release['id'],business_id=release['available_business_ids'][0],entity_ids=[obj['id']])


def test_source_persists_handoff_and_detects_missing_audit(client,media_runtime,account):
    source_id,release,obj=saved_candidate(client,account);business={'execution_mode':'confirmed_graph_workflow'}
    with Session(engine()) as db:
        p=principal(db,account);source=db.get(Task,source_id);ctx=h.context(db,p,source,business)
        assert ctx['source_clock_verified'] is False and ctx['physical_state_verified'] is False and ctx['data_reception_verified'] is False
        selected=h.Selection(source_task_id=source.id,source_task_version=source.version,basis_hash=ctx['basis_hash'])
        parent,origin=h.prepare(db,p,selected,ctx['instance_id'],body(release,obj),business)
        child=Task(workspace_id=p.workspace_id,owner_id=p.user_id,package_id=source.package_id,title='合成视频流程',status='workflow_waiting',data={'creation_hash':'c'*64,'instance_id':ctx['instance_id'],'instance_release_id':ctx['release_id'],'business_id':ctx['business_id'],'entity_ids':[ctx['entity_id']]})
        db.add(child);db.flush();h.attach(db,p,child,parent,origin,'c'*64);child_id=child.id;db.commit()
    with Session(engine()) as db:
        p=principal(db,account);child=db.get(Task,child_id);source=db.get(Task,source_id)
        h.verify_task(db,p,child);ctx=h.context(db,p,source,business);assert ctx['linked_task_id']==child.id
        with pytest.raises(HTTPException):h.prepare(db,p,h.Selection(source_task_id=source.id,source_task_version=source.version,basis_hash=ctx['basis_hash']),ctx['instance_id'],body(release,obj),business)
        entry=db.scalar(select(Audit).where(Audit.object_id==source.id,Audit.action=='video.workflow_handoff_v347'));db.delete(entry);db.flush()
        with pytest.raises(HTTPException):h.verify_task(db,p,child)
        db.rollback()
    with Session(engine()) as db:h.verify_task(db,principal(db,account),db.get(Task,child_id))


def test_changed_event_frame_and_wrong_scope_are_rejected(client,media_runtime,account):
    source_id,release,obj=saved_candidate(client,account);business={'execution_mode':'confirmed_graph_workflow'}
    with Session(engine()) as db:
        p=principal(db,account);source=db.get(Task,source_id);ctx=h.context(db,p,source,business)
        selection=h.Selection(source_task_id=source.id,source_task_version=source.version,basis_hash=ctx['basis_hash'])
        with pytest.raises(HTTPException):h.prepare(db,p,selection,ctx['instance_id'],SimpleNamespace(**{**vars(body(release,obj)),'entity_ids':['different']}),business)
        event=db.get(Event,source.data['event_id']);event.data={**event.data,'candidate':{**event.data['candidate'],'label':'tampered'}};db.flush()
        with pytest.raises(HTTPException):h.context(db,p,source,business)
        db.rollback()
    with Session(engine()) as db:
        p=principal(db,account);source=db.get(Task,source_id);frame=db.get(VideoFrame,source.data['perception_event']['frame_id'])
        frame.data={**frame.data,'seconds':frame.data['seconds']+1};db.flush()
        with pytest.raises(HTTPException):h.context(db,p,source,business)
        db.rollback()


def test_original_task_payloads_keep_their_previous_wire_shape():
    from generic_agents.workspace_api import NewTask,creation_body_hash
    from generic_agents.contracts import digest
    old={'release_id':'release','business_id':'business','entity_ids':['entity'],'title':'任务','request_id':'original_request_123'}
    value=NewTask(**old);full=value.model_dump(mode='json')
    assert 'video_handoff' not in full
    excluded={'goal_spec','image_ids','scene_ids','comparison_scene_ids','comparison_confirmed','audio_ids','sound_ids','assessment_id','signal_id','signal_version','signal_basis_hash','advice_id','graph_inputs','pixel_pair'}
    assert creation_body_hash(value)==digest(value.model_dump(exclude={key for key in excluded if not getattr(value,key)}))
    selected=NewTask(**old,video_handoff={'source_task_id':'parent','source_task_version':1,'basis_hash':'a'*64})
    assert selected.model_dump(mode='json')['video_handoff']['source_task_id']=='parent'
    assert creation_body_hash(selected)!=creation_body_hash(value)


def test_two_operators_cannot_create_two_children_from_one_saved_candidate(client,media_runtime,account):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    source_id,release,obj=saved_candidate(client,account);business={'execution_mode':'confirmed_graph_workflow'}
    with Session(engine()) as db:
        p=principal(db,account);source=db.get(Task,source_id);ctx=h.context(db,p,source,business);package=source.package_id
    selection=h.Selection(source_task_id=source_id,source_task_version=ctx['source_task_version'],basis_hash=ctx['basis_hash']);barrier=threading.Barrier(2)
    def competing(_):
        with Session(engine()) as db:
            p=principal(db,account);barrier.wait(timeout=10)
            try:
                source,origin=h.prepare(db,p,selection,ctx['instance_id'],body(release,obj),business)
                child=Task(workspace_id=p.workspace_id,owner_id=p.user_id,package_id=package,title='模拟并发交接',status='workflow_waiting',data={'creation_hash':'d'*64,'instance_id':ctx['instance_id'],'instance_release_id':ctx['release_id'],'business_id':ctx['business_id'],'entity_ids':[ctx['entity_id']]})
                db.add(child);db.flush();h.attach(db,p,child,source,origin,'d'*64);db.commit();return child.id
            except HTTPException as error:
                db.rollback();assert error.status_code==409;return None
    with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(competing,range(2)))
    assert sum(value is not None for value in outcomes)==1
    with Session(engine()) as db:
        child=db.get(Task,next(value for value in outcomes if value));h.verify_task(db,principal(db,account),child)


def test_stripping_generated_instance_from_bound_video_never_becomes_legacy(client,media_runtime,account):
    from generic_agents import native_receipts
    from generic_agents.models import VideoRun
    source_id,_release,_obj=saved_candidate(client,account)
    with Session(engine()) as db:
        p=principal(db,account);source=db.get(Task,source_id);run=db.get(VideoRun,source.data['run_id'])
        frame=db.scalar(select(VideoFrame).where(VideoFrame.run_id==run.id).order_by(VideoFrame.sample_index))
        context=dict(run.data['source_context']);binding=dict(context['domain_binding'])
        for key in ('instance_id','instance_release_id','instance_release_number','business_id','business_name'):binding.pop(key,None)
        context['domain_binding']=binding;run.data={**run.data,'source_context':context};frame.data={**frame.data,'source_context':context};db.flush()
        with pytest.raises(HTTPException):native_receipts.enqueue_video(db,p,SimpleNamespace(id='tampered-parent',data={'run_id':run.id}))
        db.rollback()


def test_old_video_inbox_has_readable_context_instead_of_missing_graph_error(client,media_runtime,account,monkeypatch):
    from generic_agents import workspace_api
    source_id,release,_obj=saved_candidate(client,account)
    with Session(engine()) as db:
        p=principal(db,account);source=db.get(Task,source_id)
        business={'execution_mode':'confirmed_graph_workflow','flow':{'steps':[]}}
        frozen=SimpleNamespace(data={'snapshot':{'roles':{},'profile':{}}})
        monkeypatch.setattr(workspace_api,'saved_execution',lambda *_args,**_kwargs:(source,frozen,business))
        detail=workspace_api._detail_report_text_operation(source_id,p,db)
        assert detail['video_workflow_inbox'] is True and not detail['completed'] and not detail['permissions']['can_complete_step']
        ctx=workspace_api.video_handoff_context(source_id,p,db)
        assert ctx['source_task_id']==source_id and ctx['linked_task_id'] is None and not ctx['physical_state_verified']
        assert db.get(Task,source_id).status=='workflow_pending'


@pytest.mark.parametrize('status',['workflow_pending','workflow_waiting','workflow_cancelled','workflow_completed'])
def test_orphaned_video_candidates_remain_readable_in_every_historical_state(status):
    business={'execution_mode':'confirmed_graph_workflow'}
    source=SimpleNamespace(status=status,data={'trigger':'video.presence','perception_event':{'run_id':'saved'}})
    assert h.is_candidate(source,business)
    source.data['graph_runtime']={'actual':'present'};assert not h.is_candidate(source,business)


def test_graph_video_candidate_cannot_use_legacy_start_or_cancel_a_linked_origin(monkeypatch):
    from generic_agents import workspace_api,workflow_cancellation,relations
    business={'execution_mode':'confirmed_graph_workflow'};p=SimpleNamespace(user_id='operator',role='admin')
    source=SimpleNamespace(status='workflow_pending',data={'trigger':'video.presence','perception_event':{'run_id':'saved'},h.KEY:{'task_id':'linked'}})
    monkeypatch.setattr(workspace_api,'execution',lambda *_args,**_kwargs:(source,None,business))
    with pytest.raises(HTTPException,match='协同业务流程'):workspace_api.start_review('saved',SimpleNamespace(version=1),p,None)
    monkeypatch.setattr(workspace_api,'saved_execution',lambda *_args,**_kwargs:(source,None,business))
    monkeypatch.setattr(workflow_cancellation,'authorize',lambda *_args:None)
    monkeypatch.setattr(workflow_cancellation,'prior',lambda *_args:None)
    monkeypatch.setattr(relations,'lock_current_actor',lambda *_args:None)
    with pytest.raises(HTTPException,match='已交接业务任务'):workspace_api.cancel('saved',SimpleNamespace(),p,None)


def test_layered_approval_rechecks_original_video_before_its_decision(client,media_runtime,account):
    from generic_agents import layered_approval_store_v175 as approval
    source_id,release,obj=saved_candidate(client,account);business={'execution_mode':'confirmed_graph_workflow'}
    with Session(engine()) as db:
        p=principal(db,account);source=db.get(Task,source_id);ctx=h.context(db,p,source,business)
        selected=h.Selection(source_task_id=source.id,source_task_version=source.version,basis_hash=ctx['basis_hash'])
        source,origin=h.prepare(db,p,selected,ctx['instance_id'],body(release,obj),business)
        child=Task(workspace_id=p.workspace_id,owner_id=p.user_id,package_id=source.package_id,title='模拟审批核对',status='approval_draft',data={'creation_hash':'e'*64,'instance_id':ctx['instance_id'],'instance_release_id':ctx['release_id'],'business_id':ctx['business_id'],'entity_ids':[ctx['entity_id']]})
        db.add(child);db.flush();h.attach(db,p,child,source,origin,'e'*64);db.commit();child_id=child.id
    with Session(engine()) as db:
        p=principal(db,account);child=db.get(Task,child_id);source=db.get(Task,source_id);event=db.get(Event,source.data['event_id'])
        event.data={**event.data,'candidate':{**event.data['candidate'],'label':'tampered'}};db.flush()
        with pytest.raises(HTTPException):approval.validate_document_sources(db,p,child,{'graph_inputs':{}})
        db.rollback()


def test_dismissed_video_blocks_fresh_action_but_preserves_history(client,media_runtime,account):
    from generic_agents import layered_approval_store_v175 as approval
    from generic_agents.models import VideoAlert
    source_id,release,obj=saved_candidate(client,account);business={'execution_mode':'confirmed_graph_workflow'}
    with Session(engine()) as db:
        p=principal(db,account);source=db.get(Task,source_id);ctx=h.context(db,p,source,business)
        choice=h.Selection(source_task_id=source.id,source_task_version=source.version,basis_hash=ctx['basis_hash'])
        source,origin=h.prepare(db,p,choice,ctx['instance_id'],body(release,obj),business)
        child=Task(workspace_id=p.workspace_id,owner_id=p.user_id,package_id=source.package_id,title='模拟复核',status='approval_draft',data={'creation_hash':'f'*64,'instance_id':ctx['instance_id'],'instance_release_id':ctx['release_id'],'business_id':ctx['business_id'],'entity_ids':[ctx['entity_id']]})
        db.add(child);db.flush();h.attach(db,p,child,source,origin,'f'*64);alert=db.get(VideoAlert,source.data['video_alert_id']);alert.status='dismissed';db.commit();child_id=child.id
    with Session(engine()) as db:
        p=principal(db,account);child=db.get(Task,child_id);h.verify_task(db,p,child)
        assert h.context(db,p,db.get(Task,source_id),business)['linked_task_id']==child_id
        with pytest.raises(HTTPException,match='已被拒绝'):h.verify_task(db,p,child,require_active=True)
        with pytest.raises(HTTPException,match='已被拒绝'):approval.validate_document_sources(db,p,child,{'graph_inputs':{}})


def test_manual_video_context_cannot_impersonate_an_automatic_event_authorization(monkeypatch):
    from generic_agents import workspace_api as api,instance_semantic_runtime_dispatch as dispatch,relations,graph_task_inputs_v120,task_creation_recovery_v120
    from tests.test_business_flow_nested_inputs_runtime_v195 import entry
    binding=entry('authorized_anomaly_handoff','event',simulation=False);binding['source_materials']=[]
    business={'id':'business_one','name':'业务','flow':{'steps':[]},'execution_mode':'confirmed_graph_workflow'}
    release=SimpleNamespace(id='release_one',data={'snapshot':{'businesses':[business],'materials':[]}})
    instance=SimpleNamespace(data={'enabled_business_ids':['business_one']});p=SimpleNamespace(user_id='actor_one',workspace_id='workspace_one',role='operator')
    request=api.NewTask(release_id=release.id,business_id=business['id'],entity_ids=['entity_one'],title='模拟视频人工交接',request_id='original_video_347',video_handoff={'source_task_id':'video_parent','source_task_version':1,'basis_hash':'a'*64})
    monkeypatch.setattr(api,'scoped',lambda *args,**kwargs:instance);monkeypatch.setattr(api,'active',lambda *args,**kwargs:(instance,release))
    monkeypatch.setattr(relations,'lock_current_actor',lambda *args,**kwargs:None)
    monkeypatch.setattr(task_creation_recovery_v120,'find',lambda *args,**kwargs:None)
    monkeypatch.setattr(graph_task_inputs_v120,'validate_associations',lambda *args,**kwargs:None)
    monkeypatch.setattr(dispatch,'modern',lambda *args,**kwargs:SimpleNamespace(business_binding=lambda *a,**k:binding))
    monkeypatch.setattr(h,'prepare',lambda *args,**kwargs:('source','origin'))
    class DB:
        def scalar(self,query):return None
        def add(self,value):raise AssertionError('event-only workflow must reject before task creation')
    with pytest.raises(HTTPException,match='异常') as error:api.create_task('instance_one',request,p,DB())
    assert error.value.status_code==409
