"""Synthetic release-pinned perception checks; no DB, service, model or media run."""
import copy
from datetime import datetime,timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import perception_signals as signals,candidate_watches as watches
from generic_agents import local_observations as local,observation_integrity as integrity
from generic_agents.contracts import digest
from generic_agents import models as m


@pytest.fixture(autouse=True)
def no_runtime(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('DB/network/process forbidden in perception pure tests')
    import socket,subprocess,sqlalchemy
    from generic_agents import db
    monkeypatch.setattr(db,'engine',forbidden)
    monkeypatch.setattr(sqlalchemy,'create_engine',forbidden)
    monkeypatch.setattr(socket,'socket',forbidden)
    monkeypatch.setattr(socket,'create_connection',forbidden)
    monkeypatch.setattr(subprocess,'Popen',forbidden)


def context(monkeypatch):
    from generic_agents import entity_release_compat as compat,instance_api
    from generic_agents.signal_dual_models import SignalDualHandoff
    p=NS(user_id='actor',workspace_id='workspace',role='operator')
    instance=NS(id='instance',workspace_id=p.workspace_id,namespace='same-instance',active_release_id='R2',status='partially_active')
    releases={key:NS(id=key,workspace_id=p.workspace_id,instance_id=instance.id,package_id='P'+key[1:]) for key in ['R1','R2','R3']}
    entity=NS(id='original-E',workspace_id=p.workspace_id,namespace=instance.namespace,schema_capability_id='P1',entity_type='sample',external_id='original',version=7,data={'name':'原对象'})
    source=NS(id='source',name='source',kind='source',data={'execution':{'adapter':'upload'},'data_policy':{'cloud_allowed':False}})
    extractor=NS(id='rules',kind='tool',data={'execution':{'adapter':'builtin','handler':'numeric_rules'},'data_policy':{'cloud_allowed':False},
        'specification':{'rules':[{'id':'high','field':'n','operator':'gt','threshold':10,'severity':'warning'}]}})
    for cap in [source,extractor]:cap.content_hash=digest(cap.data)
    db=NS(active='R2',compat_error=None,proof_checks=[],refresh=lambda row:None,rows={},query_rows={})
    def scalar(query):
        columns=query.column_descriptions
        assert len(columns)==1,'unexpected scalar projection in perception fixture'
        model,name=columns[0]['entity'],columns[0]['name']
        if model is m.Membership and name=='role':return 'operator'
        if model is SignalDualHandoff and name=='id':
            matches=db.query_rows.get(SignalDualHandoff,[])
            assert len(matches)<=1,'fixture must specify at most one matching dual handoff'
            return matches[0].id if matches else None
        raise AssertionError('unexpected scalar query in perception fixture')
    db.scalar=scalar
    def require_entity(db,p,instance,release,row):
        db.proof_checks.append((release.id,row.id))
        if db.compat_error:raise HTTPException(409,db.compat_error)
        if row.namespace!=instance.namespace or release.instance_id!=instance.id:raise HTTPException(409,'foreign instance')
        return {'entity_id':row.id,'source_package_id':row.schema_capability_id,'target_release_id':release.id,'contract_hash':'full-contract'}
    def scoped(db,model,identity,p,lock=False):
        if model is m.DomainInstance:return instance
        if model is m.InstanceRelease:return releases[identity]
        if model is m.Entity:return entity
        if model is m.Capability:return {'source':source,'rules':extractor}[identity]
        return db.rows[(model,identity)]
    def active(db,p,identity):return instance,releases[db.active]
    for module in [signals,watches,local,integrity]:monkeypatch.setattr(module,'scoped',scoped)
    monkeypatch.setattr(compat,'require_entity',require_entity)
    monkeypatch.setattr(instance_api,'active',active);monkeypatch.setattr(watches,'active',active)
    monkeypatch.setattr(signals,'published',lambda db,p,identity,kind=None:{'source':source,'rules':extractor}[identity])
    return NS(p=p,db=db,instance=instance,releases=releases,entity=entity,source=source,extractor=extractor,proof=require_entity,scoped=scoped)


def reported(c,release_id='R2'):
    clock=datetime(2026,10,16,tzinfo=timezone.utc);attrs={'n':20}
    original={'source_id':'source','source_record_id':'record','processing_version':'1','entity_id':c.entity.id,
        'business_day':'2026-10-16','observed_at':clock.isoformat(),'extractor_id':'rules','attributes':attrs,'evidence_ids':[]}
    extracted={'attributes':attrs,'anomalies':[{'field':'n','value':20,'rule_id':'high','severity':'warning'}],'method':'registered_numeric_rules'}
    row=NS(id='observation',source_id='source',source_record_id='record',processing_version='1',entity_id=c.entity.id,
        observed_at=clock,business_day='2026-10-16',status='processed',data={'attributes':attrs,'original_request':original,
        'input_hash':digest(original),'extractor_id':'rules','invocation_id':'call','evidence_ids':[],'extracted':extracted})
    row.data['reported_rule_contract']=signals.rule_contract(c.db,c.p,c.instance,c.releases[release_id],c.entity,c.source,c.extractor)
    call=NS(id='call',status='succeeded',capability_id='rules',data={'capability_hash':c.extractor.content_hash,
        'input_hash':digest({'attributes':attrs}),'output':extracted})
    event=NS(id='event',kind='perception.anomaly',status='received',data={'observation_id':row.id,'entity_id':row.entity_id,
        'source_id':row.source_id,'invocation_id':'call','extracted':extracted,'observed_at':clock.isoformat()})
    c.db.rows.update({(m.Observation,row.id):row,(m.Invocation,call.id):call,(m.Event,event.id):event})
    return row,event


def watch_body(release_id='R2'):
    return NS(entity_id='original-E',profile='native:GeoRun',name='明确候选核查',operator='gt',threshold=0,needle='',
        min_score=.5,label=None,confirm_candidate_only=True,release_id=release_id)


def watch(c,monkeypatch,release_id='R2'):
    c.db.active=release_id
    monkeypatch.setattr(watches,'options',lambda *a:[{'key':'native:GeoRun','name':'区域候选','value_kind':'number','family':'native','native_kind':'GeoRun'}])
    value=watches.definition(c.db,c.p,c.instance.id,watch_body(release_id))
    row=NS(id='watch',workspace_id=c.p.workspace_id,instance_id=c.instance.id,status='enabled',version=1,
        data={'definition':value,'definition_hash':digest(value)})
    c.db.rows[(m.CandidateWatch,row.id)]=row
    return row


def signal(c,event,basis):
    row=NS(id='signal',event_id=event.id,instance_id=c.instance.id,entity_id=c.entity.id,version=1,status='unassigned',
        data={'basis':basis,'basis_hash':digest(basis),'note':'已保存核查说明','task_ids':[]})
    c.db.rows[(m.PerceptionSignal,row.id)]=row
    return row


def test_r2_rule_on_original_entity_is_explicit_and_does_not_rewrite_source(monkeypatch):
    c=context(monkeypatch);before=copy.deepcopy(c.entity.__dict__)
    row,_=reported(c)
    contract=row.data['reported_rule_contract']
    assert contract['release_id']=='R2' and contract['entity_source_package_id']=='P1'
    assert contract['entity_definition_hash']==digest(c.proof(c.db,c.p,c.instance,c.releases['R2'],c.entity))
    signals.guard_rule(c.db,c.p,row,result=row.data['extracted'])
    assert c.entity.__dict__==before


def test_same_package_rule_contract_keeps_original_exact_shape(monkeypatch):
    c=context(monkeypatch)
    value=signals.rule_contract(c.db,c.p,c.instance,c.releases['R1'],c.entity,c.source,c.extractor)
    assert value=={'schema':'local-reported-rule-v1','instance_id':c.instance.id,'release_id':'R1',
        'entity_id':c.entity.id,'entity_version':7,'source_hash':digest(c.source.data),'extractor_hash':digest(c.extractor.data)}


def test_r1_queued_rule_cannot_execute_or_resign_after_r2(monkeypatch):
    c=context(monkeypatch);row,event=reported(c,'R1');before=copy.deepcopy(row.data)
    with pytest.raises(HTTPException,match='旧登记规则不迁移'):signals.guard_rule(c.db,c.p,row)
    with pytest.raises(ValueError,match='旧通知不能改签'):signals.freeze(c.db,c.p,event,c.instance,c.entity)
    assert row.data==before


@pytest.mark.parametrize('change',['side_effect','cloud_allowed','unknown_source','unknown_extractor'])
def test_object_compatibility_does_not_grant_unsafe_local_actions(monkeypatch,change):
    c=context(monkeypatch)
    if change=='side_effect':c.extractor.data['execution']['side_effect']=True
    elif change=='cloud_allowed':c.extractor.data['data_policy']['cloud_allowed']=True
    elif change=='unknown_source':c.source.data['execution']['adapter']='mqtt'
    else:c.extractor.data['execution']['handler']='unknown_action'
    with pytest.raises(HTTPException):signals.rule_contract(c.db,c.p,c.instance,c.releases['R2'],c.entity,c.source,c.extractor)


@pytest.mark.parametrize('failure',['source publication revoked','full schema unit changed','foreign instance'])
def test_fresh_rule_and_watch_keep_full_compatibility_gate(monkeypatch,failure):
    c=context(monkeypatch);c.db.compat_error=failure
    with pytest.raises(HTTPException,match=failure):signals.rule_contract(c.db,c.p,c.instance,c.releases['R2'],c.entity,c.source,c.extractor)
    with pytest.raises(HTTPException,match=failure):watch(c,monkeypatch)


def test_rule_input_and_result_equality_and_actor_permission_are_preserved(monkeypatch):
    c=context(monkeypatch);row,_=reported(c)
    bad=copy.deepcopy(row.data['extracted']);bad['anomalies']=[]
    with pytest.raises(HTTPException,match='回执'):signals.guard_rule(c.db,c.p,row,result=bad)
    c.db.scalar=lambda query:'viewer'
    with pytest.raises(HTTPException) as denied:signals.guard_rule(c.db,c.p,row)
    assert denied.value.status_code==403


def test_r2_signal_uses_target_release_and_original_entity_source(monkeypatch):
    c=context(monkeypatch);row,event=reported(c)
    basis=signals.freeze(c.db,c.p,event,c.instance,c.entity)
    assert basis['release_id']=='R2' and basis['package_id']=='P2' and basis['source_entity_package_id']=='P1'
    c.db.active='R3';c.instance.active_release_id='R3'
    saved=signal(c,event,basis);before=copy.deepcopy(saved.data)
    assert signals.checked(c.db,c.p,saved)==basis
    assert c.db.proof_checks[-1]==('R2',c.entity.id)
    assert saved.data==before
    with pytest.raises(HTTPException,match='当前业务版本'):
        signals.snapshot(c.db,c.p,saved.id,c.instance,c.releases['R3'],[c.entity.id],saved.version,saved.data['basis_hash'])


def test_r1_historical_signal_remains_r1_after_r2_without_rebase(monkeypatch):
    c=context(monkeypatch);c.instance.active_release_id='R1';c.db.active='R1';_,event=reported(c,'R1')
    basis=signals.freeze(c.db,c.p,event,c.instance,c.entity)
    assert 'source_entity_package_id' not in basis and basis['package_id']=='P1'
    saved=signal(c,event,basis);c.instance.active_release_id='R2';c.db.active='R2'
    assert signals.checked(c.db,c.p,saved)['release_id']=='R1'
    with pytest.raises(HTTPException,match='当前业务版本'):signals.snapshot(c.db,c.p,saved.id,c.instance,c.releases['R2'],[c.entity.id],1,saved.data['basis_hash'])


def test_old_unversioned_notification_cannot_adopt_r2_compatible_entity(monkeypatch):
    c=context(monkeypatch);row,event=reported(c);row.data.pop('reported_rule_contract')
    with pytest.raises(ValueError,match='缺少当前接收版本'):signals.freeze(c.db,c.p,event,c.instance,c.entity)


def test_watch_confirmation_allows_r2_and_keeps_r1_history_but_blocks_old_execution(monkeypatch):
    c=context(monkeypatch);r2=watch(c,monkeypatch)
    assert watches.verify_definition(c.db,c.p,r2,enabled=True)['entity_source_package_id']=='P1'
    r1=watch(c,monkeypatch,'R1');old=copy.deepcopy(r1.data);c.db.active='R2'
    assert watches.verify_definition(c.db,c.p,r1)==old['definition']
    with pytest.raises(ValueError,match='重新确认规则'):watches.verify_definition(c.db,c.p,r1,enabled=True)
    assert r1.data==old


def test_tampered_watch_source_proof_cannot_execute(monkeypatch):
    c=context(monkeypatch);row=watch(c,monkeypatch)
    row.data['definition'].pop('entity_definition_hash');row.data['definition_hash']=digest(row.data['definition'])
    with pytest.raises(ValueError,match='完整定义'):watches.verify_definition(c.db,c.p,row,enabled=True)


@pytest.mark.parametrize('requested',[None,'R1'])
def test_unknown_watch_request_cannot_adopt_r2_without_explicit_current_release(monkeypatch,requested):
    c=context(monkeypatch);watch(c,monkeypatch)
    with pytest.raises(HTTPException,match='明确确认候选规则'):
        watches.definition(c.db,c.p,c.instance.id,watch_body(requested))


def test_old_watch_nonce_keeps_exact_legacy_hash_and_existing_result(monkeypatch):
    c=context(monkeypatch);body=watch_body(None);body.request_id='original-request';raw=dict(vars(body))
    body.model_dump=lambda *,exclude:{key:value for key,value in raw.items() if key not in exclude}
    legacy={key:value for key,value in raw.items() if key not in {'request_id','release_id'}}
    prior=NS(id='original-watch',data={'request_hash':digest(legacy),'definition':{'release_id':'R1'}})
    c.db.scalar=lambda query:prior
    monkeypatch.setattr(watches,'definition',lambda *a:pytest.fail('old nonce must not be rebound to current release'))
    before=copy.deepcopy(prior.data)
    assert watches.create(c.db,c.p,c.instance.id,body) is prior and prior.data==before


def test_watch_nonce_cannot_change_its_explicit_release(monkeypatch):
    c=context(monkeypatch);body=watch_body('R2');body.request_id='original-request';raw=dict(vars(body))
    body.model_dump=lambda *,exclude:{key:value for key,value in raw.items() if key not in exclude}
    prior=NS(id='original-watch',data={'request_hash':digest({key:('R1' if key=='release_id' else value)
        for key,value in raw.items() if key!='request_id'})})
    c.db.scalar=lambda query:prior
    with pytest.raises(HTTPException,match='原规则请求内容不一致'):watches.create(c.db,c.p,c.instance.id,body)


def candidate_chain(monkeypatch):
    from generic_agents import native_receipts,native_signal_reviews as reviews,workspace_api
    c=context(monkeypatch);confirmed=watch(c,monkeypatch)
    packet={'schema':'native-observation-v2','source_key':'GeoRun:original-result','source_machine_hash':'original-machine-hash',
        'entity_id':c.entity.id,'task_id':'source-task-R1','attributes':{'区域候选数量':2},'candidate_only':True,'evidence_ids':[]}
    row=NS(id='native-observation',entity_id=c.entity.id,source_id='source',
        source_record_id=packet['source_key']+':'+packet['entity_id'],observed_at=None,business_day='2026-10-16',
        data={'native_instance_id':c.instance.id,'candidate_only':True,'native_packet':copy.deepcopy(packet),
            'native_packet_hash':digest(packet),'evidence_ids':[],'extracted':{'attributes':packet['attributes']}})
    c.db.rows[(m.Observation,row.id)]=row
    record={'task_id':packet['task_id'],'key':packet['source_key'],'integrity_verified':True,'name':'原已保存媒体结果','review_state':None}
    def checked_native(db,p,observation):
        if observation.data['native_packet']!=packet or digest(packet)!=observation.data['native_packet_hash']:
            raise HTTPException(409,'original media packet changed')
        return record
    monkeypatch.setattr(native_receipts,'checked',checked_native)
    source_task=NS(id=packet['task_id'],package_id='P1',status='workflow_completed',version=3,
        data={'instance_id':c.instance.id,'instance_release_id':'R1','entity_ids':[c.entity.id]})
    c.db.rows[(m.Task,source_task.id)]=source_task
    locks=[]
    def execution(db,p,identity,lock=False):
        assert identity==source_task.id;locks.append(lock);return source_task,c.releases['R1'],{}
    monkeypatch.setattr(workspace_api,'execution',execution)
    monkeypatch.setattr(reviews,'scoped',c.scoped)
    monkeypatch.setattr(reviews,'published',lambda db,p,identity,kind: NS(id=identity))
    original_scalar=c.db.scalar
    def scalar(query):
        columns=query.column_descriptions
        assert len(columns)==1,'unexpected scalar projection in candidate fixture'
        if columns[0]['entity'] is m.Event and columns[0]['name']=='Event':
            matches=c.db.query_rows.get(m.Event,[])
            assert len(matches)<=1,'fixture must specify at most one matching candidate event'
            return matches[0] if matches else None
        return original_scalar(query)
    def scalars(query):
        columns=query.column_descriptions
        assert len(columns)==1,'unexpected rows projection in candidate fixture'
        if columns[0]['entity'] is not m.PerceptionSignal or columns[0]['name']!='PerceptionSignal':
            raise AssertionError('unexpected rows query in candidate fixture')
        # These are the matching historical rows; the query excludes this Signal.
        return list(c.db.query_rows.get(m.PerceptionSignal,[]))
    c.db.scalar=scalar;c.db.scalars=scalars
    events=[]
    def enqueue_event(db,p,kind,key,payload):
        event=NS(id='new-candidate-event',kind=kind,status='received',data=payload)
        events.append(event);c.db.rows[(m.Event,event.id)]=event
        c.db.query_rows[m.Event]=[event];return event
    monkeypatch.setattr(watches,'enqueue_event',enqueue_event)
    job=NS(data={'watch_id':confirmed.id,'watch_version':confirmed.version,
        'definition_hash':confirmed.data['definition_hash'],'observation_id':row.id})
    result=watches.process(c.db,c.p,job)
    assert result['matched'] is True and len(events)==1
    basis=signals.freeze(c.db,c.p,events[0],c.instance,c.entity)
    saved=signal(c,events[0],basis)
    return c,saved,source_task,locks


def test_actual_candidate_to_signal_handoff_chain_uses_r2_but_keeps_r1_source_task(monkeypatch):
    from generic_agents import native_signal_reviews as reviews
    c,saved,source_task,locks=candidate_chain(monkeypatch);before=copy.deepcopy(source_task.__dict__)
    snapshot=signals.snapshot(c.db,c.p,saved.id,c.instance,c.releases['R2'],[c.entity.id],1,saved.data['basis_hash'])
    assert snapshot['basis']['release_id']=='R2' and snapshot['basis']['package_id']=='P2'
    assert snapshot['basis']['original_model_result']['task_id']==source_task.id
    assert locks and all(locks) and source_task.__dict__==before
    # The duplicate lock is not permission to use R2 as an original R1 linked review.
    with pytest.raises(HTTPException,match='版本或对象不一致'):reviews.origin(c.db,c.p,saved,lock=True)


def test_candidate_handoff_keeps_duplicate_work_guard(monkeypatch):
    c,saved,_,_=candidate_chain(monkeypatch)
    other=NS(id='prior-signal',status='handed_off',data={'basis':copy.deepcopy(saved.data['basis']),'task_ids':['prior-human-task']})
    c.db.rows[(m.Task,'prior-human-task')]=NS(id='prior-human-task',title='已保存审核',status='workflow_completed')
    c.db.query_rows[m.PerceptionSignal]=[other]
    with pytest.raises(HTTPException,match='不重复安排审核'):
        signals.snapshot(c.db,c.p,saved.id,c.instance,c.releases['R2'],[c.entity.id],1,saved.data['basis_hash'])


def test_candidate_handoff_rejects_forged_native_source_pointer(monkeypatch):
    c,saved,_,_=candidate_chain(monkeypatch)
    saved.data['basis']['original_model_result']['source_key']='GeoRun:different-result'
    saved.data['basis_hash']=digest(saved.data['basis'])
    with pytest.raises(HTTPException,match='出处不一致'):
        signals.snapshot(c.db,c.p,saved.id,c.instance,c.releases['R2'],[c.entity.id],1,saved.data['basis_hash'])


def test_candidate_handoff_source_package_revocation_cannot_grant_new_review(monkeypatch):
    from generic_agents import native_signal_reviews as reviews
    c,saved,task,_=candidate_chain(monkeypatch);before=copy.deepcopy(task.__dict__)
    def withdrawn(*args):raise HTTPException(409,'source package withdrawn')
    monkeypatch.setattr(reviews,'published',withdrawn)
    with pytest.raises(HTTPException,match='source package withdrawn'):
        signals.snapshot(c.db,c.p,saved.id,c.instance,c.releases['R2'],[c.entity.id],1,saved.data['basis_hash'])
    assert task.__dict__==before


def local_receipt(c,monkeypatch,release_id,*,with_proof=True):
    cap=NS(id='image-model',data={'model':'frozen-local'});original=NS(id='image',sha256='image-hash')
    value={'source_id':'source','source_record_id':'image-record','processing_version':'1','entity_id':c.entity.id,
        'business_day':'2026-10-16','observed_at':None,'extractor_id':cap.id,'attributes':{},'evidence_ids':[original.id]}
    scope={'id':c.instance.id,'release_id':release_id}
    if with_proof and c.releases[release_id].package_id!=c.entity.schema_capability_id:
        scope.update(entity_source_package_id='P1',entity_definition_hash=digest(c.proof(c.db,c.p,c.instance,c.releases[release_id],c.entity)))
    row=NS(id='local-observation',source_id='source',source_record_id='image-record',processing_version='1',entity_id=c.entity.id,
        business_day='2026-10-16',observed_at=None,data={'original_request':value,'input_hash':digest(value),'attributes':{},
        'evidence_ids':[original.id],'local_capability_hash':digest(cap.data),'local_original_sha256':original.sha256,
        'local_source_hash':digest(c.source.data),'local_instance':scope})
    monkeypatch.setattr(local,'checked_cap',lambda *a:cap)
    monkeypatch.setattr(local,'file_inputs',lambda *a:original)
    monkeypatch.setattr(local,'published',lambda *a:c.source)
    return row,cap,original


def test_new_r2_local_receipt_executes_with_original_e_and_frozen_proof(monkeypatch):
    c=context(monkeypatch);row,cap,original=local_receipt(c,monkeypatch,'R2')
    assert local.verify(c.db,c.p,row,cap.id) is original


def test_old_local_receipt_history_does_not_use_new_active_release_or_execution_permission(monkeypatch):
    c=context(monkeypatch);row,cap,original=local_receipt(c,monkeypatch,'R1');before=copy.deepcopy(row.data)
    assert local.verify(c.db,c.p,row,cap.id,execution=False) is original
    assert c.db.proof_checks[-1]==('R1',c.entity.id)
    with pytest.raises(HTTPException,match='旧处理授权不迁移'):local.verify(c.db,c.p,row,cap.id)
    assert row.data==before


def test_cross_package_local_receipt_without_new_confirmation_proof_is_blocked(monkeypatch):
    c=context(monkeypatch);row,cap,_=local_receipt(c,monkeypatch,'R2',with_proof=False)
    with pytest.raises(HTTPException):local.verify(c.db,c.p,row,cap.id)


def test_checked_report_selects_historical_verification_without_weakening_call_integrity(monkeypatch):
    c=context(monkeypatch);row,_=reported(c);row.data['candidate_only']=True;seen=[]
    monkeypatch.setattr(local,'verify',lambda db,p,row,extractor,*,execution=True:seen.append(execution))
    report=integrity.checked_report(c.db,c.p,row,allow_candidates=True)
    assert seen==[False] and report['attributes']=={'n':20}
    c.db.rows[(m.Invocation,'call')].data['output']={'attributes':{'n':999}}
    with pytest.raises(ValueError,match='输出不一致'):integrity.checked_report(c.db,c.p,row,allow_candidates=True)
