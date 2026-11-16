"""Recover saved normal needs without data execution or current activation.

New records use normal public creation and independent typed-body publication.
The named legacy producer seam disables only new V248 metadata publication in
this test; the original save/query/Job/Audit paths remain real. It proves old
format compatibility, not provenance from an actually deployed old service.
Root executes on disposable PostgreSQL; this file starts no global consumer.
"""
import copy
from contextlib import ExitStack
from uuid import uuid4

import pytest
from sqlalchemy import func,select,text
from sqlalchemy.orm import Session

from generic_agents import models as m,worker,workspace_need_request_recovery_v248 as recovery
from generic_agents.contracts import digest
from generic_agents.workspace_needs import NeedCreate,KIND
from tests.test_scoped_data_gap_intake_pg_v245 import scene as published_scene,good
from tests.test_authored_workflow_document_packet_dispatch_pg_v208 import operator
from tests.test_instance_workspace import prepared


def test_public_lookup_is_get_only_with_query_nonce_and_preserves_known_query_detail(client):
    paths=client.app.openapi()['paths']
    path='/api/workspace/instances/{identity}/data-need-requests'
    assert set(paths[path])=={'get'}
    parameters={(row['name'],row['in']) for row in paths[path]['get']['parameters']}
    assert ('request_id','query') in parameters and ('expected_body_hash','query') in parameters
    assert '/api/workspace/instances/{identity}/data-needs/{query_id}' in paths


def body(s,**changes):
    return NeedCreate.model_validate({'request_id':'need_v248_'+uuid4().hex,'release_id':s.release['id'],
        'business_id':s.release['available_business_ids'][0],'entity_id':s.entity['id'],
        'purpose':'只核对本对象已登记数量，不调用云或设备','fields':[s.field],
        'source':'observations','confirmation':'unverified_extraction','max_age_hours':1}|changes).model_dump(mode='json')


def create(s,value):return good(s.client.post('/api/workspace/instances/'+s.release['instance_id']+'/data-needs',json=value),201)


@pytest.fixture
def scene(published_scene):
    s=published_scene;s.workspace=s.workspace_id
    s.base='/api/workspace/instances/'+s.release['instance_id']+'/data-need-requests'
    s.body=body(s);s.body_hash=digest(s.body);s.need=create(s,s.body)
    s.instance_id=s.release['instance_id'];s.query_id=s.need['id'];s.request_id=s.body['request_id']
    return s


def lookup(s,*,request_id=None,expected_hash=None,client=None,instance_id=None):
    path='/api/workspace/instances/'+(instance_id or s.release['instance_id'])+'/data-need-requests'
    return (client or s.client).get(path,params={'request_id':request_id or s.body['request_id'],
        'expected_body_hash':expected_hash or s.body_hash})


def inventory(s):
    with Session(s.bound) as db:
        return {model.__tablename__:{row.id:digest({'version':getattr(row,'version',None),
            'status':getattr(row,'status',None),'data':getattr(row,'data',None)}) for row in db.scalars(
                select(model).where(model.workspace_id==s.workspace_id))}
            for model in (m.DataRun,m.DataGap,m.Job,m.Audit,m.Event,m.Memory,m.Task,m.Acquisition,m.Invocation,m.CloudCall)}


def legacy_create(s,monkeypatch,**changes):
    original=body(s,**changes)
    # Explicit pre-V248 writer format seam. No success, request, context,
    # initial Job or original creation Audit is fabricated or rewritten.
    def prior_writer(db,p,row,parsed):
        assert 'creation_protocol' not in row.data['workspace_need_context'] and recovery.MARKER not in row.data
    with monkeypatch.context() as patch:
        patch.setattr(recovery,'creation_metadata',lambda:{})
        patch.setattr(recovery,'save_original',prior_writer)
        view=create(s,original)
    return original,view


def test_typed_original_is_saved_once_and_lookup_returns_no_view_evidence_or_new_execution(scene):
    s=scene;before=inventory(s);result=good(lookup(s));proof=result['request']
    assert result['schema']==recovery.SCHEMA and result['found'] and result['read_only']
    assert result['device_actions'] is False and result['cloud_calls']==0
    assert proof['query_id']==s.need['id'] and proof['request_id']==s.body['request_id']
    assert proof['body_hash']==s.body_hash and proof['typed_body']==s.body and proof['evidence_level']=='typed_body_receipt'
    assert proof['actor_id']==s.p.user_id and proof['instance_id']==s.release['instance_id']
    assert result['query_path']=='/api/workspace/instances/'+s.release['instance_id']+'/data-needs/'+s.need['id']
    assert not {'view','records','attributes','result','observations'}&set(result)
    assert inventory(s)==before
    assert create(s,s.body)['id']==s.need['id'] and inventory(s)==before
    with Session(s.bound) as db:
        records=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==s.workspace_id,
            m.Audit.action==recovery.ACTION,m.Audit.object_id==s.need['id'])))
        assert len(records)==1 and records[0].id==proof['typed_receipt_audit_id']
        assert records[0].data['receipt_hash']==proof['receipt_hash']


def test_original_beyond_latest_hundred_and_paused_instance_recovers_by_nonce_without_resubmit(scene):
    s=scene
    name=s.release['snapshot']['object_types'][1]['fields'][0]['key']
    for index in range(101):
        create(s,body(s,purpose='核对已有的对象名称登记',source='archive',fields=[name],
            request_id='newer_need_'+str(index)+'_'+uuid4().hex))
    base='/api/workspace/instances/'+s.release['instance_id']+'/data-needs'
    listed=good(s.client.get(base));assert listed['truncated'] and s.need['id'] not in {row['id'] for row in listed['items']}
    instance=next(row for row in good(s.client.get('/api/instances')) if row['id']==s.release['instance_id'])
    good(s.client.post('/api/instances/'+instance['id']+'/pause',json={'version':instance['version']}))
    before=inventory(s);result=good(lookup(s))
    assert result['found'] and result['request']['query_id']==s.need['id']
    assert result['request']['typed_body']==s.body
    detail=good(s.client.get(result['query_path']));assert not detail['scope_valid'] and detail['purpose']==s.body['purpose']
    assert s.client.get(base+'/options').status_code==409
    assert s.client.post(base,json=s.body).status_code==409
    assert inventory(s)==before


def test_slug_and_unknown_unicode_query_parameters_keep_unknown_and_never_issue_actions(scene):
    s=scene;before=inventory(s)
    assert good(lookup(s))['request_id']==s.body['request_id']  # legal non-UUID slug
    for nonce in ['unknown_need_'+uuid4().hex,'未知/请求?原始参数_123456']:
        result=good(lookup(s,request_id=nonce))
        assert result['found'] is False and result['request'] is None and result['query_path'] is None
        assert result['request_id']==nonce and result['body_hash']==s.body_hash and result['read_only']
    assert lookup(s,expected_hash='0'*64).status_code==409
    assert inventory(s)==before


def test_another_actor_or_instance_cannot_recover_original_scope(scene):
    s=scene
    with ExitStack() as stack:
        _,other=operator(s,stack)
        before=inventory(s)
        assert lookup(s,client=other).status_code==404
        assert inventory(s)==before
    _,creation,checked=prepared(s.client)
    different=good(s.client.post('/api/instances',json={**creation,
        'confirmed_configuration_hash':checked['configuration_hash'],'confirm_configuration':True,
        'generation_intent':'new_instance','request_id':str(uuid4())}),201)
    assert different['instance_id']!=s.release['instance_id']
    before=inventory(s)
    assert lookup(s,instance_id=different['instance_id']).status_code==404
    assert inventory(s)==before


def test_original_actor_downgraded_to_viewer_can_read_history_without_new_submit_permission(scene):
    s=scene
    with Session(s.bound) as db:
        member=db.scalar(select(m.Membership).where(m.Membership.workspace_id==s.workspace_id,m.Membership.user_id==s.p.user_id))
        member.role='viewer';db.commit()
    before=inventory(s);result=good(lookup(s))
    assert result['found'] and result['request']['query_id']==s.need['id']
    assert s.client.post('/api/workspace/instances/'+s.release['instance_id']+'/data-needs',json=body(s)).status_code==403
    assert inventory(s)==before


def test_real_read_only_transaction_and_initial_failed_job_do_not_prevent_original_request_recovery(scene):
    s=scene
    instance=next(row for row in good(s.client.get('/api/instances')) if row['id']==s.release['instance_id'])
    good(s.client.post('/api/instances/'+instance['id']+'/pause',json={'version':instance['version']}))
    with Session(s.bound) as db:
        job_id=db.scalar(select(m.Job.id).where(m.Job.workspace_id==s.workspace_id,
            m.Job.idempotency_key=='workspace-need:'+s.need['id']+':initial'))
    assert worker.process_once(job_id,kinds=[KIND],workspace_id=s.workspace_id,deliver=False)
    with Session(s.bound) as db:assert db.get(m.Job,job_id).status=='failed'
    before=inventory(s)
    with Session(s.bound) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        result=recovery.lookup(db,s.p,s.release['instance_id'],s.body['request_id'],s.body_hash)
        assert result['found'] and result['request']['initial_job_id']==job_id
        db.rollback()
    assert inventory(s)==before


@pytest.mark.parametrize('source',['observations','archive'])
def test_legacy_job_context_is_explicit_and_does_not_backfill_or_reconstruct_folded_body(scene,monkeypatch,source):
    s=scene;original,view=legacy_create(s,monkeypatch,source=source,confirmation='unverified_extraction',max_age_hours=3)
    before=inventory(s);result=good(lookup(s,request_id=original['request_id'],expected_hash=digest(original)))
    proof=result['request']
    assert proof['query_id']==view['id'] and proof['evidence_level']=='legacy_job_context'
    assert proof['typed_body'] is None and proof['receipt_hash'] is None and proof['typed_receipt_audit_id'] is None
    with Session(s.bound) as db:
        row=db.get(m.DataRun,view['id'])
        assert 'creation_protocol' not in row.data['workspace_need_context'] and recovery.MARKER not in row.data
        assert not list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==s.workspace_id,
            m.Audit.action==recovery.ACTION,m.Audit.object_id==view['id'])))
    assert inventory(s)==before


def test_new_archive_receipt_preserves_full_ignored_form_values_without_querying_them(scene):
    s=scene;original=body(s,source='archive',confirmation='unverified_extraction',max_age_hours=3)
    view=create(s,original);before=inventory(s)
    result=good(lookup(s,request_id=original['request_id'],expected_hash=digest(original)))
    assert result['request']['typed_body']==original and result['request']['evidence_level']=='typed_body_receipt'
    assert view['requirement']['minimum_verification']=='recorded' and view['requirement']['time']['mode']=='archive'
    assert inventory(s)==before


def test_new_request_and_its_receipt_rollback_together_if_publication_cannot_finish(scene,monkeypatch):
    s=scene;before=inventory(s);original=body(s);publish_original=recovery.save_original
    def interrupt_after_real_publication(*args,**kwargs):
        publish_original(*args,**kwargs)
        raise RuntimeError('isolated interruption after normal receipt staging')
    with monkeypatch.context() as patch:
        patch.setattr(recovery,'save_original',interrupt_after_real_publication)
        with pytest.raises(RuntimeError,match='isolated interruption'):
            create(s,original)
    assert inventory(s)==before
    result=good(lookup(s,request_id=original['request_id'],expected_hash=digest(original)))
    assert result['found'] is False and result['request'] is None
    assert inventory(s)==before
