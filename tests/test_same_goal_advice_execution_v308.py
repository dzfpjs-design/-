"""Actual Start/seals/custody/SQL predicates with declared source/IAM IO spies.

No PostgreSQL permissions, worker leases, HTTP/provider sending, actual quota
accounting, API commit or restart durability is claimed by this unit fixture.
"""
import copy
from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_execution_v308 as s
from generic_agents.security import Principal
from tests.test_goal_evaluation_append_command_v297 import predicate
from tests.test_same_goal_advice_contracts_v302 import ref
from tests.test_same_goal_advice_preview_v307 import scene as preview_scene


@pytest.fixture
def scene(monkeypatch):
    t=preview_scene.__wrapped__(monkeypatch)
    t.job.data.update(plan_id=t.plan.id)
    t.rows.update({s.Job:[t.job],s.Invocation:[],s.Plan:[t.plan],s.CloudCall:[]})
    t.no_autoflush_depth=0
    class DB:
        @property
        @contextmanager
        def no_autoflush(self):
            t.no_autoflush_depth+=1
            try:yield
            finally:t.no_autoflush_depth-=1
        def scalar(self,statement):
            if 'clock_timestamp' in str(statement):return t.clock
            cls=statement.column_descriptions[0]['entity']
            assert cls in {s.store.Task,s.Plan}
            row=t.task if cls is s.store.Task else t.plan
            t.locks.append((cls,statement._for_update_arg is not None))
            return row if predicate(statement.whereclause,row) else None
        def scalars(self,statement):
            cls=statement.column_descriptions[0]['entity']
            assert cls in {s.Audit,s.Job,s.Invocation,s.Plan,s.CloudCall}
            return [row for row in t.rows[cls] if predicate(statement.whereclause,row)]
        def add(self,row):
            assert type(row) in {s.Audit,s.Job};t.rows[type(row)].append(row)
        def flush(self):t.flushes+=1
        @contextmanager
        def begin_nested(self):
            before={cls:list(rows) for cls,rows in t.rows.items()}
            plan=(t.plan.version,t.plan.status,copy.deepcopy(t.plan.data))
            try:yield
            except BaseException:
                t.rows=before;t.plan.version,t.plan.status,t.plan.data=plan;raise
        def commit(self):pytest.fail('caller must own commit')
    t.db=DB()
    monkeypatch.setattr(s,'lock_current_actor',lambda *args:t.events.append('current_business_IAM_IO'))
    def price(db,p,pin,*,basis_ref=None):
        t.current_prices+=1
        if basis_ref is not None and basis_ref!=t.price['basis_ref']:
            raise HTTPException(409,'explicit actual price IO rejects replaced basis')
        return copy.deepcopy(t.price)
    monkeypatch.setattr(s.previews.prices,'current',price)
    t.preview_view=t.prepare()
    t.clock+=timedelta(seconds=10)
    t.body={'request_id':ref('formal_start_nonce')['id'],'expected_plan_version':t.plan.version,
        'expected_goal_hash':t.prep['scope']['goal_hash'],
        'expected_mature_head_hash':t.prep['mature_head']['head_hash'],
        'preview_hash':t.preview_view['preview_hash'],'cloud_authorized':True,
        'reason':'已核对匿名全文、资料同意和本次费用上限，正式启动业务建议'}
    t.start=lambda body=None:s.start(t.db,t.p,t.task.id,t.plan.id,body or t.body)
    t.checked=lambda:s.checked(t.db,t.p,t.task,t.plan,t.prep,t.job)
    t.budget=lambda:s.budget_calls(t.db,t.p,t.task)
    return t


def _sdk(t):
    value=t.checked();job=value['job'];snapshot=value['snapshot']
    call=s.Invocation(id=ref('actual_original_business_sdk')['id'],workspace_id=t.p.workspace_id,
        capability_id=t.advice.id,idempotency_key=job.idempotency_key,status='running',created_at=t.clock,
        data={'job_id':job.id,'lease_token':ref('original_sdk_creator_token')['id'],'role':'business',
            'capability_hash':t.advice.content_hash,'capability_snapshot':copy.deepcopy(t.advice.data),
            'inputs':s.input_for(t.plan,snapshot),'input_hash':s.digest(s.input_for(t.plan,snapshot)),
            'approval':s.approval_for(t.plan,snapshot,job),
            'side_effect':bool(t.advice.data['execution'].get('side_effect'))})
    t.rows[s.Invocation].append(call);return call


def test_formal_start_seals_separate_snapshot_and_one_real_job_without_mutating_original_materials(scene):
    t=scene;before=copy.deepcopy(t.plan.data);preview_events=[row for row in t.rows[s.Audit] if row.action==s.previews.ACTION]
    assert t.checked() is None and t.budget()==0
    result=t.start();saved=t.checked();job=saved['job'];event=saved['start']
    assert t.plan.status=='generating' and t.plan.version==t.body['expected_plan_version']+1
    assert {key:value for key,value in t.plan.data.items() if key!=s.KEY}==before
    assert len(t.plan.data)==7 and saved['snapshot']['preparation_snapshot_hash']==t.prep['record_hash']
    assert saved['snapshot']['record_hash']!=t.prep['record_hash'] and saved['snapshot']['start_request']==t.body
    assert job.kind==s.c.SEND_KIND and job.status=='queued' and job.attempts==0
    assert job.data==s._job_data(t.plan,t.prep,saved['snapshot'])
    assert job.idempotency_key==s.job_key(t.task.id,t.plan.id)
    assert event.data['record_hash']==s.c.record_hash(event.data)
    assert t.plan.data[s.KEY]['start_ref']=={'id':event.id,'record_hash':s.digest(event.data)}
    assert len(preview_events)==1 and saved['preview'] is preview_events[0] and saved['calls']==[]
    assert result['invocation_created'] is False and t.budget()==1
    assert not {'snapshot','projection','aliases','frame_snapshot','model_snapshot','secret_ref'}&set(result)
    assert (s.store.Task,True) in t.locks and (s.Plan,True) in t.locks


def test_same_nonce_lost_response_is_original_without_live_sources_price_or_second_job(scene):
    t=scene;first=t.start();counts=(t.builds,t.clouds,t.current_prices,len(t.rows[s.Audit]),len(t.rows[s.Job]))
    t.clock+=timedelta(hours=4);t.allowed=False
    assert t.start()==first and t.budget()==1
    assert counts==(t.builds,t.clouds,t.current_prices,len(t.rows[s.Audit]),len(t.rows[s.Job]))
    with pytest.raises(HTTPException):t.start({**t.body,'reason':'changed old request'})
    with pytest.raises(HTTPException):t.start({**t.body,'request_id':ref('replacement_nonce')['id']})


def test_later_valid_consent_and_a_new_minimum_grant_do_not_rewrite_original_preview(scene):
    t=scene
    for index,pin in enumerate(t.documents['evidence_pins']):
        file,_content=t.files[pin['evidence_id']];file.version+=1;pin['version']=file.version
        audit=s.Audit(id=ref('later_grant_'+str(index))['id'],workspace_id=t.p.workspace_id,actor_id=t.p.user_id,
            action='evidence.cloud_authorize',object_id=file.id,created_at=t.clock,
            data={'model_id':t.model.id,'sha256':file.sha256,'version':file.version,'reason':'new genuine grant IO'})
        t.rows[s.Audit].append(audit)
        t.documents['consent_pins'][index]={
            'evidence_id':file.id,'model_profile_id':t.model.id,'audit_id':audit.id,'audit_hash':s.consent._grant_hash(audit)}
    before=copy.deepcopy(next(row for row in t.rows[s.Audit] if row.action==s.previews.ACTION).data)
    t.start()
    assert t.checked()['snapshot']['preview']==before['preview_record']
    assert next(row for row in t.rows[s.Audit] if row.action==s.previews.ACTION).data==before


@pytest.mark.parametrize('fault',['version','goal','head','preview','consent_false','cancelled','prep_running',
    'actor','source','changed_frame','replaced_price','expired_price','removed_grant','withdrawn_consent'])
def test_start_requires_current_actual_preview_sources_authority_price_and_explicit_consent(scene,fault):
    t=scene;body=copy.deepcopy(t.body)
    if fault=='version':body['expected_plan_version']-=1
    if fault=='goal':body['expected_goal_hash']='0'*64
    if fault=='head':body['expected_mature_head_hash']='0'*64
    if fault=='preview':body['preview_hash']='0'*64
    if fault=='consent_false':body['cloud_authorized']=False
    if fault=='cancelled':t.plan.status='cancelled'
    if fault=='prep_running':t.job.status='running'
    if fault=='actor':t.p=Principal(t.prep['data_actor_id'],t.p.workspace_id,'operator','actual_data_post_IO')
    if fault=='source':t.allowed=False
    if fault=='changed_frame':t.frame['question']+=' input drift'
    if fault=='replaced_price':t.price['basis_ref']=ref('newest_replaces_original_price')
    if fault=='expired_price':t.clock+=timedelta(hours=4)
    if fault=='removed_grant':t.rows[s.Audit]=[row for row in t.rows[s.Audit] if row.action!='evidence.cloud_authorize']
    if fault=='withdrawn_consent':
        for file,_ in t.files.values():file.data['cloud_model_ids']=[]
    before=copy.deepcopy(t.plan.data);version=t.plan.version
    with pytest.raises(HTTPException):t.start(body)
    assert t.plan.data==before and t.plan.version==version and not [row for row in t.rows[s.Audit] if row.action==s.STARTED]
    assert len(t.rows[s.Job])==1 and not t.rows[s.Invocation]


def test_sdk_pin_preserves_original_token_under_reclaim_and_counts_only_one_reservation(scene,monkeypatch):
    t=scene;t.start();call=_sdk(t);job=t.checked()['job']
    assert t.budget()==1
    job.status='running';job.lease_token=ref('new_reclaimed_token')['id']
    job.data.update(lease_recoveries=1,recovery_only=True,claimed_at=t.clock.isoformat())
    for name in ('_saved','_inventory'):
        monkeypatch.setattr(s.store,name,lambda *args:pytest.fail('cold checked must not call store custody'))
    monkeypatch.setattr(s.previews,'_history',lambda *args,**kwargs:pytest.fail('no live/history source recursion'))
    value=t.checked()
    assert value['calls']==[call] and call.data['lease_token']!=job.lease_token
    assert value['job'] is job and value['start'].data['snapshot']==value['snapshot']
    call.status='unknown';assert t.checked()['calls'][0].status=='unknown'


@pytest.mark.parametrize('fault',['missing_pointer','missing_start','missing_send_job','extra_start','extra_job',
    'wrong_job_key','job_payload','unknown_job_field','wrong_sdk_key','wrong_sdk_role','wrong_sdk_approval',
    'wrong_sdk_cap_snapshot','wrong_sdk_inputs','extra_sdk','preview_hash'])
def test_positive_custody_and_reverse_inventory_reject_orphans_and_sdk_drift(scene,fault):
    t=scene;t.start();value=t.checked();job=value['job'];call=_sdk(t)
    if fault=='missing_pointer':t.plan.data.pop(s.KEY)
    if fault=='missing_start':t.rows[s.Audit].remove(value['start'])
    if fault=='missing_send_job':t.rows[s.Job].remove(job)
    if fault=='extra_start':
        t.rows[s.Audit].append(s.Audit(id=ref('orphan_start')['id'],workspace_id=t.p.workspace_id,
            actor_id=t.p.user_id,action=s.STARTED,object_id=t.plan.id,created_at=t.clock,data=copy.deepcopy(value['start'].data)))
    if fault=='extra_job':
        t.rows[s.Job].append(s.Job(id=ref('orphan_job')['id'],workspace_id=t.p.workspace_id,
            kind=s.c.SEND_KIND,idempotency_key='foreign:'+job.idempotency_key,data=copy.deepcopy(job.data)))
    if fault=='wrong_job_key':job.idempotency_key+=':other'
    if fault=='job_payload':job.data['actor_id']=t.prep['data_actor_id']
    if fault=='unknown_job_field':job.data['arbitrary_adapter']='python'
    if fault=='wrong_sdk_key':call.idempotency_key+=':other'
    if fault=='wrong_sdk_role':call.data['role']='data'
    if fault=='wrong_sdk_approval':call.data['approval']['job_id']=ref('foreign_job')['id']
    if fault=='wrong_sdk_cap_snapshot':call.data['capability_snapshot']['description']+=' changed'
    if fault=='wrong_sdk_inputs':call.data['inputs']['preview_hash']='0'*64
    if fault=='extra_sdk':
        t.rows[s.Invocation].append(s.Invocation(id=ref('orphan_sdk')['id'],workspace_id=t.p.workspace_id,
            capability_id=call.capability_id,idempotency_key=call.idempotency_key+':other',data=copy.deepcopy(call.data)))
    if fault=='preview_hash':value['preview'].data['record_hash']='0'*64
    with pytest.raises(HTTPException):t.checked()


@pytest.mark.parametrize('orphan',['start','job','sdk'])
def test_task_budget_does_not_treat_missing_plan_as_available_capacity(scene,orphan):
    t=scene;t.start();value=t.checked()
    if orphan=='sdk':_sdk(t)
    t.rows[s.Plan]=[]
    if orphan=='start':t.rows[s.Job]=[t.job]
    if orphan=='job':t.rows[s.Audit]=[row for row in t.rows[s.Audit] if row.action!=s.STARTED]
    if orphan=='sdk':
        t.rows[s.Job]=[t.job];t.rows[s.Audit]=[row for row in t.rows[s.Audit] if row.action!=s.STARTED]
    with pytest.raises(HTTPException):t.budget()
    assert value['start'].data['snapshot']['record_hash']==t.plan.data[s.KEY]['snapshot_hash']


def _paid_route(t,call,kind):
    """Routing-only originals, not complete journal/spending receipt fixtures."""
    value=t.checked();job=value['job']
    if kind=='cloud':
        row=s.CloudCall(id=ref('actual_cloud_ledger_routing_IO')['id'],workspace_id=t.p.workspace_id,
            capability_id=t.model.id,status='reserved',data={'gateway_dispatch':{'identity':{
                'workspace_id':t.p.workspace_id,'actor_id':t.prep['business_actor_id'],
                'capability_id':t.model.id,'task_id':t.task.id,'job_id':job.id,'suggestion_id':call.id}}})
        t.rows[s.CloudCall].append(row);return row
    field='send_job_id' if kind in {'same_goal_advice.sdk_reconciled_v308','same_goal_advice.result_ready_v308'} else 'job_id'
    hash_field='execution_hash' if kind in {'same_goal_advice.money_reserved_v308',
        'same_goal_advice.money_transport_entered_v308'} else 'execution_snapshot_hash'
    body={'workspace_id':t.p.workspace_id,'actor_id':t.prep['business_actor_id'],'task_id':t.task.id,
        'plan_id':t.plan.id,'invocation_id':call.id,field:job.id,hash_field:value['snapshot']['record_hash']}
    row=s.Audit(id=ref('routing_'+kind)['id'],workspace_id=t.p.workspace_id,
        actor_id=t.prep['business_actor_id'],action=kind,
        object_id=call.id if kind in s._SDK_ACTIONS else t.plan.id,created_at=t.clock,data=body)
    t.rows[s.Audit].append(row);return row


@pytest.mark.parametrize('kind',sorted(s._SDK_ACTIONS|s._PLAN_ACTIONS)+['cloud'])
def test_deleted_sdk_with_any_paid_original_blocks_first_send_and_lost_response_reentry(scene,kind):
    t=scene;t.start();call=_sdk(t);_paid_route(t,call,kind);t.rows[s.Invocation].remove(call)
    counts=(len(t.rows[s.Audit]),len(t.rows[s.Job]),len(t.rows[s.CloudCall]))
    with pytest.raises(HTTPException):t.checked()
    with pytest.raises(HTTPException):t.start()
    assert not t.rows[s.Invocation] and counts==(len(t.rows[s.Audit]),len(t.rows[s.Job]),len(t.rows[s.CloudCall]))


@pytest.mark.parametrize('fault',['audit_foreign_sdk','audit_foreign_job','cloud_foreign_sdk','cloud_foreign_job'])
def test_existing_sdk_does_not_admit_foreign_paid_routes(scene,fault):
    t=scene;t.start();call=_sdk(t)
    if fault.startswith('audit'):
        row=_paid_route(t,call,'same_goal_advice.prepared_v308')
        row.data['invocation_id' if fault.endswith('sdk') else 'job_id']=ref('foreign_paid_owner')['id']
    else:
        row=_paid_route(t,call,'cloud')
        row.data['gateway_dispatch']['identity']['suggestion_id' if fault.endswith('sdk') else 'job_id']=ref('foreign_paid_owner')['id']
        # Positive current SDK's saved raw call link independently finds a
        # ledger even if its dispatch job/suggestion identity was corrupted.
        call.data['same_goal_advice_v308']={'call_id':row.id}
    with pytest.raises(HTTPException):t.checked()


def test_paid_routes_remain_historical_custody_only_and_budget_queries_never_autoflush(scene,monkeypatch):
    t=scene;t.start();call=_sdk(t);_paid_route(t,call,'cloud')
    for action in sorted(s._SDK_ACTIONS|s._PLAN_ACTIONS):_paid_route(t,call,action)
    # No full capture/result proof is claimed here; the protected store must
    # still allow its separate result sibling without recursively reading it.
    t.plan.data[s._RESULT]={'explicit_result_marker_IO':True}
    actual=s.store._rows
    def rows(*args,**kwargs):
        assert t.no_autoflush_depth>0
        return actual(*args,**kwargs)
    monkeypatch.setattr(s.store,'_rows',rows)
    assert t.budget()==1 and t.no_autoflush_depth==0
    assert call.status=='running'


def test_no_execution_pointer_does_not_erase_orphan_paid_plan_audit(scene):
    t=scene
    t.rows[s.Audit].append(s.Audit(id=ref('orphan_paid_before_start')['id'],workspace_id=t.p.workspace_id,
        actor_id=t.p.user_id,action='same_goal_advice.prepared_v308',object_id=ref('missing_sdk')['id'],
        data={'plan_id':t.plan.id}))
    with pytest.raises(HTTPException):t.checked()
    assert s.KEY not in t.plan.data and not t.rows[s.Invocation]
