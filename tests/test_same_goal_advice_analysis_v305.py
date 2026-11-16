"""Actual read-only orchestration over explicitly declared custody/source IO.

Not PG, real IAM, SDK, ACK, query or provider proof. Actual source authority
has separate runtime suites; these spies check exact identity, data pins,
business-vs-data roles and no mutation/replacement in this resolver.
"""
import copy
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_analysis_v305 as s
from generic_agents.contracts import digest
from generic_agents.security import Principal
from generic_agents.workflow_goal_evaluation_store_v288 import _output
from tests.test_same_goal_advice_contracts_v302 import scene as contract_scene,ref


@pytest.fixture
def scene(monkeypatch):
    original=contract_scene();prep=copy.deepcopy(original.preparation);scope=prep['scope'];events=[]
    p=Principal(prep['business_actor_id'],scope['workspace_id'],'operator','explicit_reader_IO')
    task=s.Task(id=scope['task_id'],workspace_id=p.workspace_id,package_id=scope['package_id'],
        owner_id=prep['requested_by'],status='workflow_active',data={'native_goal_query_id':prep['original_query_ref']['query_id'],
            'graph_runtime':{'explicit_original_graph_IO':True}})
    marker={'schema':'same-goal-advice-document-preparation-v304','receipt_ref':ref('prep_receipt'),
        'evidence_ref':original.sources['document']['evidence_ref'],'prepare_job_id':ref('prep_job')['id'],
        'preparation_snapshot_hash':prep['record_hash']}
    plan=s.Plan(id=scope['analysis_plan_id'],workspace_id=p.workspace_id,task_id=task.id,status='awaiting_consent',
        data={'schema':s.c.PLAN,'preparation_snapshot':prep,'document_preparation':marker})
    prep_job=s.Job(id=marker['prepare_job_id'],workspace_id=p.workspace_id,kind=s.c.PREPARE_KIND,
        status='succeeded',lease_until=None,data={'task_id':task.id})
    release=NS(id=scope['release_id'],instance_id=scope['instance_id'],package_id=scope['package_id'])
    cap=s.Capability(id=prep['advice_capability_ref']['id'],workspace_id=p.workspace_id,data=original.manifest,
        content_hash=digest(original.manifest),validated_hash=digest(original.manifest),release_version='1.0.0')
    source_cap=NS(**prep['source_business_capability_ref'],data={'specification':{'instruction':prep['instruction']}})
    model=NS(**prep['model_pin'])
    specs=[{'candidate_ref':'candidate_1','proposal':'Compare the approved actual facts for review.',
        'claims':[],'capability_pins':[{'capability_ref':'cap_temperature','capability_id':ref('original_declared_cap')['id'],
            'capability_hash':'a'*64,'release_version':'1.0.0','delivery':'requested_observation','ports':[]}]}]
    selected={'release':release,'binding':{'graph':{'frozen_graph_IO':True,'nodes':[
        {'id':'reasoning','actor_id':prep['business_actor_id'],'execution':{'capability_id':source_cap.id,
            'arguments':{'candidate_specs':specs,'decision_intent':'business_conclusion'},
            'input_bindings':{'question':'analysis_question'}}},
        {'id':'packet','actor_id':prep['data_actor_id'],'execution':{}}]}}}
    configuration={'scope':{key:value for key,value in scope.items() if key!='analysis_plan_id'},
        'source':source_cap,'advice':cap,'model':model}
    head={**prep['mature_head'],'goal_check_job_id':prep['mature_head']['goal_check_job_id']}
    task.data[s.provider.KEY]=head
    query_result={'status':'sufficient','result':{'status':'sufficient','conflicts':[]}}
    record={'schema':'workflow-goal-evaluation-record-v299','producer_family':'append_command_v297',
        'record_hash':prep['mature_head']['evaluation_ref']['record_hash'],'generation':3,
        'original_query_ref':prep['original_query_ref'],'evaluation_query_ref':prep['evaluation_query_ref'],
        'cut_at':prep['evaluation_cut'],'evaluation_result':query_result,
        'evidence_ref':original.sources['data_evidence_ref'],'simulation_only':True}
    eval_job=s.Job(id=ref('actual_eval_producer_job')['id'],workspace_id=p.workspace_id,status='succeeded',
        data={'inputs':{'local_plan':{'explicit_original_IO':True}}})
    call=s.Invocation(id=prep['mature_head']['evaluation_ref']['id'],workspace_id=p.workspace_id,status='succeeded',
        capability_id=ref('actual_eval_cap')['id'],data={'job_id':eval_job.id})
    eval_cap=s.Capability(id=call.capability_id,workspace_id=p.workspace_id,data={'actual_registry_IO':True})
    row=s.Evaluation(id=call.id,workspace_id=p.workspace_id,task_id=task.id,job_id=eval_job.id,invocation_id=call.id,
        record_hash=record['record_hash'],generation=3,goal_hash=scope['goal_hash'],
        goal_snapshot_hash=scope['goal_snapshot_hash'],data=record)
    output=_output(row)
    data={'packet_id':row.id,'packet':output['packet'],'evidence_id':record['evidence_ref']['evidence_id'],
        'sha256':record['evidence_ref']['sha256'],'producer_job_id':eval_job.id,'invocation_id':call.id,
        'evaluation_record':record,'fact_origins':[{'explicit_actual_ACK_originals_IO':True}],
        'text':'Original saved evaluation text.'}
    current={'evaluation_ref':head['evaluation_ref'],'goal_check_job_id':head['goal_check_job_id'],
        'goal_check_id':head['memory_id'],'report_hash':head['report_hash'],'evidence':{'status':'sufficient','conflicts':[]}}
    document={'marker':marker,'prepare_job_id':prep_job.id,'preparation_snapshot_hash':prep['record_hash'],
        'document':copy.deepcopy(original.sources['document']),'content':b'Original approved document excerpts.'}
    runtime={'graph_hash':digest(selected['binding']['graph']),'inputs':{'analysis_question':'Original approved business question.'}}
    runtime['input_hash']=digest(runtime['inputs'])
    state=NS(p=p,task=task,plan=plan,prep=prep,prep_job=prep_job,configuration=configuration,selected=selected,
        release=release,cap=cap,row=row,record=record,output=output,data=data,current=current,document=document,
        runtime=runtime,events=events,package_hash='b'*64)
    rows={(s.Task,task.id):task,(s.Plan,plan.id):plan,(s.Evaluation,row.id):row,(s.Job,eval_job.id):eval_job,
        (s.Invocation,call.id):call,(s.Capability,eval_cap.id):eval_cap,(s.Capability,cap.id):cap}
    class DB:
        no_autoflush=nullcontext()
        def add(self,*args):pytest.fail('resolver cannot create any row')
        def flush(self):pytest.fail('resolver cannot flush')
        def commit(self):pytest.fail('resolver cannot commit')
        def execute(self,*args):pytest.fail('all DB source inventories are explicit IO spies')
    state.db=DB()
    def fresh(db,p,model,identity):
        assert db is state.db;events.append(('fresh',model,identity));return rows[model,identity]
    monkeypatch.setattr(s.store,'_fresh',fresh)
    monkeypatch.setattr(s.store,'_reader',lambda db,p,**kwargs:events.append(('reader',p.role,kwargs['write'])))
    def inventory(db,p,actual):
        assert db is state.db and actual is task;events.append('actual_command_inventory_IO')
        return [(plan,prep,prep_job)]
    monkeypatch.setattr(s.store,'_inventory',inventory)
    monkeypatch.setattr(s.quota,'_configuration',lambda db,p,actual,body: (events.append('actual_role_configuration_IO') or configuration))
    monkeypatch.setattr(s.provider,'binding',lambda *args:selected)
    monkeypatch.setattr(s.store,'_policy',lambda *args: (events.append('actual_policy_IO') or prep['budget']))
    monkeypatch.setattr(s.provider,'_q0',lambda *args:NS())
    monkeypatch.setattr(s.queries,'query_ref',lambda *args:prep['original_query_ref'])
    monkeypatch.setattr(s.documents,'cold_read',lambda *args: (events.append('actual_document_receipt_IO') or document))
    def require_current(db,p,actual,*,expected_ref,lock_sources):
        assert actual is task and expected_ref==prep['mature_head']['evaluation_ref'] and lock_sources is False
        events.append('actual_current_goal_receipt_IO');return current
    monkeypatch.setattr(s.provider,'require_current',require_current)
    def saved(db,p,actual,job,sdk,actual_cap,inputs):
        assert actual is task and job is eval_job and sdk is call and actual_cap is eval_cap
        assert inputs==eval_job.data['inputs'];events.append('actual_eval_packet_saved_result_IO');return output
    monkeypatch.setattr(s.packet,'saved_result',saved)
    def original_data(db,p,actual,packet,*,live):
        assert actual is task and packet==output['packet'] and live is True
        events.append('actual_V288_data_live_IO');return release,state.package_hash,data
    monkeypatch.setattr(s.originals,'_data',original_data)
    monkeypatch.setattr(s.graph,'check_run',lambda declared,original:runtime)
    monkeypatch.setattr(s.store,'_scope',lambda *args:pytest.fail('business Preview cannot impersonate the data Prepare post'))
    state.resolve=lambda:s.resolve_current(state.db,state.p,task,plan)
    return state


def test_real_business_operator_can_resolve_current_sources_without_impersonating_data_or_writing(scene):
    t=scene;before=copy.deepcopy((t.task.data,t.plan.data,t.record));result=t.resolve()
    assert t.p.user_id==t.prep['business_actor_id']!=t.prep['data_actor_id']
    assert set(result)=={'schema','task','preparation','source_record','original_sources','document','frame_inputs','release'}
    assert result['schema']==s.SCHEMA and result['release'] is t.release and result['task'] is t.task
    assert result['source_record']['evaluation_generation']==3
    assert result['source_record']['evaluation_query_ref']==t.prep['evaluation_query_ref']
    assert result['document']['record']==t.document['document'] and result['document']['content']==t.document['content']
    assert 'documents' not in result['original_sources'] and 'activation_id' not in result['frame_inputs']
    assert result['frame_inputs']['question']==t.runtime['inputs']['analysis_question']
    assert t.events.index('actual_eval_packet_saved_result_IO')<t.events.index('actual_V288_data_live_IO')
    assert before==(t.task.data,t.plan.data,t.record)


def test_data_operator_is_not_automatically_authorized_as_the_business_post(scene):
    t=scene;t.p=Principal(t.prep['data_actor_id'],t.p.workspace_id,'operator','actual_data_post_IO')
    with pytest.raises(HTTPException):t.resolve()
    assert 'actual_role_configuration_IO' not in t.events


@pytest.mark.parametrize('fault',['cancelled','job_running','job_lease','role_drift','model_drift','scope_drift',
    'policy_drift','original_q0','missing_document','head_advanced','goal_memory','report_hash','stale_or_insufficient',
    'record_insufficient','record_conflict','query_changed','cut_changed','wrong_packet','source_record_changed','release_changed'])
def test_source_replacement_or_incomplete_current_authority_does_not_become_a_new_fact(scene,monkeypatch,fault):
    t=scene
    if fault=='cancelled':t.plan.status='cancelled'
    if fault=='job_running':t.prep_job.status='running'
    if fault=='job_lease':t.prep_job.lease_until='unexpected live lease'
    if fault=='role_drift':t.selected['binding']['graph']['nodes'][0]['actor_id']=ref('other_business_actor')['id']
    if fault=='model_drift':t.configuration['model'].content_hash='0'*64
    if fault=='scope_drift':t.configuration['scope']['source_binding_hash']='0'*64
    if fault=='policy_drift':monkeypatch.setattr(s.store,'_policy',lambda *args:{**t.prep['budget'],'max_tokens':512})
    if fault=='original_q0':monkeypatch.setattr(s.queries,'query_ref',lambda *args:{**t.prep['original_query_ref'],'data_hash':'0'*64})
    if fault=='missing_document':t.document['marker']={}
    if fault=='head_advanced':t.task.data[s.provider.KEY]={**t.task.data[s.provider.KEY],'head_hash':'0'*64}
    if fault=='goal_memory':t.current['goal_check_id']=ref('other_memory')['id']
    if fault=='report_hash':t.current['report_hash']='0'*64
    if fault=='stale_or_insufficient':t.current['evidence']['status']='insufficient'
    if fault=='record_insufficient':t.record['evaluation_result']['status']='insufficient'
    if fault=='record_conflict':t.record['evaluation_result']['result']['conflicts']=[{'actual':'conflict'}]
    if fault=='query_changed':t.record['evaluation_query_ref']={**t.record['evaluation_query_ref'],'data_hash':'0'*64}
    if fault=='cut_changed':t.record['cut_at']='2026-11-02T12:01:00+00:00'
    if fault=='wrong_packet':t.output['route']='review_required'
    if fault=='source_record_changed':t.data['evaluation_record']={**t.record,'generation':99}
    if fault=='release_changed':t.release.id=ref('other_release')['id']
    with pytest.raises(HTTPException):t.resolve()


@pytest.mark.parametrize('fault', [None, 'generation_mismatch', 'wrong_packet', 'wrong_release'])
def test_first_evaluation_diagnostic_requires_checked_original_sources(scene, fault):
    t = scene
    t.row.generation = t.record['generation'] = 1
    if fault == 'generation_mismatch':
        t.record['generation'] = 2
    if fault == 'wrong_packet':
        t.output['route'] = 'review_required'
    if fault == 'wrong_release':
        t.release.id = ref('other_release')['id']

    with pytest.raises(HTTPException) as failure:
        t.resolve()

    assert failure.value.status_code == 409
    assert failure.value.detail == (s.GENERATION_REQUIRED if fault is None else s.ERROR)
    if fault is None:
        assert 'actual_V288_data_live_IO' in t.events
