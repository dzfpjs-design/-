"""Public packet/command inventories and real local Evidence byte persistence.

IAM, adopted graph and original source-reception IO seams are explicit. This
does not prove PostgreSQL, normal HTTP adoption, provider quality or a device.
The ordinary observation's real extraction and the new Evidence bytes are read
by their product checkers. No external call is permitted.
"""
import copy
import hashlib
from contextlib import nullcontext
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from generic_agents import workflow_task_packet_v199 as subject
from generic_agents import workflow_task_packet_api_v199 as api
from generic_agents import workflow_task_context_v196 as reader
from generic_agents import evidence,instance_data_receipts,native_goals,workflow_graph
from generic_agents import models as m
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents.contracts import digest
from generic_agents.workflow_task_packet_models_v199 import (
    WorkflowTaskEvidencePacket as Packet,WorkflowTaskEvidencePacketCommand as Command)
from tests.test_workflow_task_context_v196 import scene as reader_scene,identity,same_sql
from tests.test_business_reasoning_v197 import manifest
from tests.test_full_semantic_graph_v1 import base,control,edge
from tests.test_report_business_flow_wire_v184 import no_external

AT=datetime(2026,1,1,tzinfo=timezone.utc)


class IO:
    """SQL selection boundary, not a transaction, lease or PostgreSQL emulator."""
    def __init__(self,scene):
        self.scene=scene;self.rows={};self.added=[];self.commits=0;self.selects=[];self.info={}
        # Real origin guards enter Session.no_autoflush before checking every
        # reverse inventory. This boundary has no implicit flush to suppress.
        self.no_autoflush=nullcontext()
    def add(self,row):self.added.append(row)
    def flush(self):
        for index,row in enumerate(self.added):
            if row.id is None:row.id=identity('new-row-'+str(index))
            if row.created_at is None:row.created_at=AT
            if getattr(row,'version',1) is None:row.version=1
            self.rows[type(row),row.id]=row
    def commit(self):self.flush();self.commits+=1
    def _find(self,statement):
        model=statement.column_descriptions[0]['entity'];params=statement.compile().params;self.selects.append((model,params))
        rows=[row for (kind,_),row in self.rows.items() if kind is model]
        for name in ('workspace_id','id','task_id','request_key','idempotency_key','action','object_id','kind','status','lease_token'):
            if name+'_1' in params:
                expected=params[name+'_1']
                rows=[row for row in rows if getattr(row,name,None) in expected] if type(expected) is list else [row for row in rows if getattr(row,name,None)==expected]
        values=list(params.values());s=self.scene
        if model is m.Audit and 'request_key' in values:
            wanted=next(value for value in values if type(value) is str and (value.startswith('graph:') or value=='unseen-public-original'))
            rows=[row for row in rows if row.data.get('request_key')==wanted]
        if model is m.Audit and 'request_key_hash' in values:
            rows=[row for row in rows if row.data.get('request_key_hash')==digest(s.key) or row.object_id==s.call.id]
        if model is m.Evidence and subject.STATE_KEY in values:
            rows=[row for row in rows if row.data.get(subject.STATE_KEY,{}).get('request_key_hash')==digest(s.key)]
        return rows
    def scalar(self,statement):
        if statement.column_descriptions[0]['entity'] is m.Capability:
            s=self.scene
            audio_source=select(m.Capability.id).where(m.Capability.workspace_id == s.p.workspace_id,
                m.Capability.id == s.obs.source_id,
                m.Capability.data['execution']['adapter'].as_string() == 'audio_stream_source').limit(1)
            if same_sql(statement,audio_source):
                # The reverse audio-family lookup projects an ID and filters
                # its registered adapter. Returning our ordinary Source row
                # merely because its ID matches would invent an audio origin.
                self.selects.append((m.Capability,statement.compile().params))
                source=self.rows.get((m.Capability,s.obs.source_id))
                return source.id if (source is not None and source.workspace_id == s.p.workspace_id
                    and source.data.get('execution',{}).get('adapter') == 'audio_stream_source') else None
        if statement.column_descriptions[0]['entity'] is m.User:
            s=self.scene
            expected=select(m.User.id).join(m.Membership,m.Membership.user_id == m.User.id).where(
                m.User.id == s.producer_actor,m.User.active.is_(True),m.Membership.workspace_id == s.p.workspace_id,
                m.Membership.role.in_(['admin','operator']))
            assert same_sql(statement,expected),'exact original-producer IAM query only'
            user=self.rows.get((m.User,s.producer_actor))
            members=[row for (model,_),row in self.rows.items() if model is m.Membership
                and row.user_id == s.producer_actor and row.workspace_id == s.p.workspace_id]
            return user.id if user is not None and user.active is True and len(members)==1 and members[0].role in {'admin','operator'} else None
        rows=self._find(statement);assert len(rows)<=1;return rows[0] if rows else None
    def scalars(self,statement):return self._find(statement)


@pytest.fixture
def scene(monkeypatch,tmp_path,reader_scene,no_external):
    s=reader_scene;old=s.db;s.db=IO(s);s.db.rows=old.rows
    s.task.title='公开通用对象资料核对';s.task.owner_id=s.p.user_id
    s.cap.data={**manifest(),'kind':'tool','name':'public_task_packet','allowed_agent_roles':['data'],
        'input_schema':subject.input_schema(),'output_schema':subject.output_schema(),
        'execution':{'adapter':subject.ADAPTER,'side_effect':True},'budget':{'max_attempts':1},
        'dependencies':[],'data_policy':{'cloud_allowed':False},
        'specification':{'schema':'workflow-task-evidence-packet-contract-v199','operation':subject.OP,'candidate_only':True}}
    s.cap.workspace_id=s.p.workspace_id;s.cap.content_hash=digest(s.cap.data);s.cap.validated_hash=s.cap.content_hash;s.cap.kind='tool';s.cap.status='published';s.cap.release_version='1.0.0'
    s.node['execution'].update(adapter=subject.ADAPTER,input_schema=subject.input_schema(),output_schema=subject.output_schema(),
        capability_hash=s.cap.content_hash,release_version='1.0.0',max_attempts=1)
    s.graph=kernel.validate_graph(base([s.node,control('context-end','end')],[edge(s.node['id'],'context-end')],s.node['id'],[s.node['id']]))
    s.state=kernel.new_run(s.graph,{},10);s.node=next(row for row in s.graph['nodes'] if row['kind']=='ability')
    activation=s.state['eligible_ability_ids'][0];s.activation=s.state['activations'][activation]
    s.frozen.update(activation_id=activation,node_id=s.node['id'],graph_hash=s.state['graph_hash'])
    s.job.data.update(activation_id=activation,graph_hash=s.state['graph_hash'])
    s.task.data['graph_runtime']=copy.deepcopy(s.state)
    s.binding={'schema':'business-flow-nested-execution-binding-v195','review_mode':'engineering_simulation',
        'simulation_only':True,'graph':s.graph};s.binding['binding_hash']=digest(s.binding)
    s.business['name']='通用核对'
    s.release.data['snapshot'].update(businesses=[s.business],object_types=[{'key':'object','fields':[
        {'key':'temperature_c','label':'温度','unit':'°C'}]}])
    s.entity=m.Entity(id=s.entity_id,workspace_id=s.p.workspace_id,namespace='public',entity_type='object',
        external_id='public-object',schema_capability_id=s.release.package_id,version=1,observed_at=AT,
        data={'name':'公开对象','temperature_c':-0.0})
    package_data={**manifest(),'kind':'domain_package','specification':{'entity_schemas':{'object':{'type':'object',
        'properties':{'temperature_c':{'type':'number','unit':'°C'}}}}}}
    s.package=NS(id=s.release.package_id,workspace_id=s.p.workspace_id,data=package_data,kind='domain_package',
        status='published',content_hash=digest(package_data),validated_hash=digest(package_data))
    source_data={**manifest(),'kind':'source','specification':{}}
    s.source=NS(id=s.source.id,workspace_id=s.p.workspace_id,name='public_sensor',data=source_data,
        kind='source',status='published',content_hash=digest(source_data),validated_hash=digest(source_data))
    extractor_data={**manifest(),'kind':'tool','execution':{'adapter':'builtin','handler':'numeric_rules','side_effect':False}}
    s.extractor=NS(id=identity('extractor'),workspace_id=s.p.workspace_id,name='public_rule',data=extractor_data,
        kind='tool',content_hash=digest(extractor_data),validated_hash=digest(extractor_data),status='published')
    extracted={'attributes':{'temperature_c':1.0},'anomalies':[],'method':'registered_numeric_rules'}
    s.extraction=m.Invocation(id=identity('extraction'),workspace_id=s.p.workspace_id,capability_id=s.extractor.id,
        idempotency_key='extract-public',status='succeeded',data={'capability_hash':s.extractor.content_hash,
            'input_hash':digest({'attributes':{'temperature_c':1.0}}),'output':copy.deepcopy(extracted)})
    original={'source_id':s.source.id,'source_record_id':'public-source-record','processing_version':'public-1',
        'entity_id':s.entity.id,'business_day':None,'observed_at':AT.isoformat(),'attributes':{'temperature_c':1.0},
        'extractor_id':s.extractor.id,'evidence_ids':[]}
    s.obs=m.Observation(id=identity('observation'),workspace_id=s.p.workspace_id,source_id=s.source.id,
        source_record_id=original['source_record_id'],processing_version=original['processing_version'],entity_id=s.entity.id,
        observed_at=AT,status='processed',version=2,business_day=None,data={'original_request':original,
            'input_hash':digest(original),'attributes':{'temperature_c':1.0},'extractor_id':s.extractor.id,
            'invocation_id':s.extraction.id,'evidence_ids':[],'extracted':extracted})
    row={'observation_id':s.obs.id,'entity_id':s.entity.id,'version':s.obs.version,'source_id':s.source.id,
        'observed_at':AT.isoformat(),'attributes':{'temperature_c':1.0},'anomalies':[],'evidence_ids':[],
        'invocation_id':s.extraction.id,'verification':'unverified_extraction','source':'processed_observation'}
    raw={'status':'insufficient','evidence':[],'observation_evidence':[row],'gaps':[],
        'conflicts':[],'excluded_evidence':[]}
    s.run.data['result']={'status':'insufficient','result':raw};s.run.data['native_goal_result_hash']=digest(s.run.data['result'])
    s.context={'task':s.task,'job':s.job,'node':s.node,'scope':s.frozen,'release':s.release,'business':s.business,
        'binding':s.binding,'graph':s.graph,'state':s.state,'capability':s.cap}
    s.approval=subject.make_approval(s.task,s.job,s.node,s.inputs)
    s.call.data.update(side_effect=True,capability_hash=s.cap.content_hash,approval=s.approval)
    for model,row in ((m.Capability,s.cap),(m.Capability,s.package),(m.Capability,s.source),(m.Capability,s.extractor),
            (m.Entity,s.entity),(m.Observation,s.obs),(m.Invocation,s.extraction)):
        s.db.rows[model,row.id]=row
    s.db.rows[m.User,s.p.user_id]=m.User(id=s.p.user_id,username='public-packet-data-actor',
        password_hash='synthetic-not-a-login',active=True)
    member=m.Membership(id=identity('packet-data-membership'),workspace_id=s.p.workspace_id,
        user_id=s.p.user_id,role='operator');s.db.rows[m.Membership,member.id]=member
    def scoped(db,model,identifier,p,*args):
        assert db is s.db
        row=db.rows.get((model,identifier))
        if row is None or row.workspace_id!=p.workspace_id:raise HTTPException(404,'public scoped row missing')
        return row
    monkeypatch.setattr(subject,'scoped',scoped);monkeypatch.setattr(evidence,'scoped',scoped)
    monkeypatch.setattr(evidence,'settings',lambda:NS(storage_dir=tmp_path,max_upload_bytes=20*1024*1024))
    monkeypatch.setattr(instance_data_receipts,'observation_receipt',lambda *args:{})
    s.actual_approval=subject.validate_call_approval
    def approval(db,p,cap,role,inputs,key,value):
        assert (db,p,cap,role,key)==(s.db,s.p,s.cap,'data',s.key)
        assert inputs==s.inputs and value==s.approval;return s.context
    monkeypatch.setattr(subject,'validate_call_approval',approval)
    return s


def finish(s,output):
    s.call.status='succeeded';s.call.data={**s.call.data,'output':copy.deepcopy(output)}
    s.db.add(m.Audit(workspace_id=s.p.workspace_id,actor_id=s.p.user_id,action=subject.PRODUCED,
        object_id=s.call.id,data=subject.receipt_audit_data(s.call,s.job,output)));s.db.flush()


def run(s):return subject.execute(s.db,s.p,s.cap,s.inputs,s.key,s.approval)


def complete_packet_activation(s,output):
    activation_id=s.activation['id']
    s.state=kernel.complete(s.graph,s.state,activation_id,output,s.job.id,s.p.user_id,
        s.activation['version'],s.state['cancel_epoch'],11)
    assert kernel.check_run(s.graph,copy.deepcopy(s.state))==s.state
    s.activation=s.state['activations'][activation_id];s.task.data['graph_runtime']=copy.deepcopy(s.state)
    s.job.status='succeeded'
    s.db.add(m.Audit(workspace_id=s.p.workspace_id,actor_id=s.p.user_id,
        action='workflow.graph.node_completed',object_id=s.task.id,
        data={'activation_id':activation_id,'invocation_id':s.call.id}));s.db.flush()


def test_new_adopted_family_retains_its_identity_without_a_fabricated_legacy_review_mode(scene,monkeypatch):
    # The adopted graph/approval boundary is the fixture's explicit spy. The
    # real collection, packet bytes and provenance builder run unchanged.
    s=scene;s.binding['schema']='authored-decision-workflow-binding-v278';s.binding.pop('review_mode')
    s.binding['binding_hash']=digest({key:value for key,value in s.binding.items() if key!='binding_hash'})
    s.context['binding']=s.binding
    def checked_family(value):
        assert value==s.binding and value['schema']=='authored-decision-workflow-binding-v278'
        assert 'review_mode' not in value
        return value
    monkeypatch.setattr(reader,'_checked_source_binding',checked_family)
    result=run(s);packet=s.db.rows[Packet,result['packet']['id']]
    provenance=packet.data['provenance']
    assert provenance['binding_schema']=='authored-decision-workflow-binding-v278'
    assert 'review_mode' not in provenance and packet.simulation_only is True
    assert result['context_result']['evidence_package'] is not None


def test_actual_query_rows_and_local_bytes_become_one_unconsented_immutable_packet(scene):
    s=scene;result=run(s);assert s.db.commits==0;subject.checked_result(result)
    queried={model.__name__ for model,_ in s.db.selects}
    assert {'HTTPObservationExchangeV177','SimulationSampleV176','SimulationEnvironmentV176'} <= queried
    assert 'simulation_origin' not in s.obs.data and 'http_mapped_origin_v177' not in s.obs.data
    assert result['packet']['proposal_only'] is True and result['packet']['cloud_authorized'] is False
    packet=s.db.rows[Packet,result['packet']['id']];ev=s.db.rows[m.Evidence,result['evidence_ids'][0]]
    assert ev.data['cloud_model_ids']==[] and packet.version==1
    assert packet.data['provenance']['sources'][0]['record']['observation_id']==s.obs.id
    assert packet.data['snapshot']['objects'][0]['observations'][0]['verification']=='candidate'
    finish(s,result);view=subject.get(s.db,s.p,packet.id)
    assert '1.0' in view['text'] and '°C' in view['text'] and '未经人工确认' in view['text']
    assert all(identity not in view['text'] for identity in (s.obs.id,s.task.id,s.call.id,s.p.workspace_id,s.entity.id))
    assert subject.read_original_context(s.db,s.p,s.call)==result['context_result']
    command=next(row for (model,_),row in s.db.rows.items() if model is Command)
    recovered=subject.recover(s.db,s.p,s.key,command.body_sha256)
    assert recovered['found'] is True and recovered['result']==result
    assert subject.reconcile(s.db,s.p,s.cap,s.inputs,s.key,s.approval,s.call)==result
    assert len([row for (model,_),row in s.db.rows.items() if model is Packet])==1
    assert subject.list_packets(s.db,s.p,s.task.id)['items'][0]['evidence']['id']==ev.id


@pytest.mark.parametrize('offset_minutes',[-420,480,330])
def test_cold_packet_inventory_hash_is_same_instant_across_database_timezones(scene,offset_minutes):
    s=scene;output=run(s);finish(s,output)
    packet=s.db.rows[Packet,output['packet']['id']]
    command=next(row for (model,_),row in s.db.rows.items() if model is Command)
    original_time=packet.created_at
    original_proof=subject._packet_proof(packet)
    original_hash=command.data['packet_hash']
    command_before=copy.deepcopy((command.data,command.exact_body,command.command_hash))
    call_before=copy.deepcopy(s.call.data);saved_count=len(s.db.added);commits=s.db.commits
    assert original_hash==digest(original_proof)
    # This is the same datetime value returned by a TIMESTAMPTZ connection with
    # another TimeZone, not an UPDATE or a repair of the saved command digest.
    packet.created_at=original_time.astimezone(timezone(timedelta(minutes=offset_minutes)))
    assert packet.created_at==original_time and packet.created_at.isoformat()!=original_time.isoformat()
    assert subject._packet_proof(packet)==original_proof
    assert subject.read_original_context(s.db,s.p,s.call)==output['context_result']
    assert subject.reconcile(s.db,s.p,s.cap,s.inputs,s.key,s.approval,s.call)==output
    assert command.data['packet_hash']==original_hash
    assert (command.data,command.exact_body,command.command_hash)==command_before
    assert s.call.data==call_before and len(s.db.added)==saved_count and s.db.commits==commits


@pytest.mark.parametrize('fault',['changed_instant','naive'])
def test_packet_timezone_canonicalization_never_accepts_changed_or_unknown_instant(scene,fault):
    s=scene;output=run(s);finish(s,output)
    packet=s.db.rows[Packet,output['packet']['id']]
    command=next(row for (model,_),row in s.db.rows.items() if model is Command)
    original_hash=command.data['packet_hash'];before=len(s.db.added);commits=s.db.commits
    converted=packet.created_at.astimezone(timezone(timedelta(hours=-7)))
    packet.created_at=converted+timedelta(microseconds=1) if fault=='changed_instant' else converted.replace(tzinfo=None)
    with pytest.raises(HTTPException):subject.read_original_context(s.db,s.p,s.call)
    assert command.data['packet_hash']==original_hash and len(s.db.added)==before and s.db.commits==commits


def test_reader_checks_v199_full_packet_producer_inventory_and_completed_activation_without_flattening(scene):
    s=scene;output=run(s);finish(s,output);complete_packet_activation(s,output)
    assert len(output['context_result']['candidates'])==1
    context=copy.deepcopy(output['context_result']['candidates'][0]);before=len(s.db.added)
    assert s.activation['output']==output and s.activation['output']!=output['context_result']
    assert reader.checked_context(s.db,s.p,context,task=s.task,dispatch=False)==context
    assert len(s.db.added)==before and s.db.commits==0
    events=[row for (model,_),row in s.db.rows.items() if model is m.Audit]
    assert len([row for row in events if row.action==subject.PRODUCED])==1
    assert not any(row.action==reader.PRODUCED for row in events),'no counterfeit old-reader produced anchor'


def test_reader_rejects_damaged_full_activation_even_when_original_v199_packet_and_audits_are_intact(scene):
    s=scene;output=run(s);finish(s,output);context=copy.deepcopy(output['context_result']['candidates'][0])
    with pytest.raises(ValueError):
        kernel.complete(s.graph,s.state,s.activation['id'],output['context_result'],s.job.id,s.p.user_id,
            s.activation['version'],s.state['cancel_epoch'],11)
    damaged=copy.deepcopy(output);damaged['packet']['sha256']='0'*64
    assert damaged['packet']['sha256']!=output['packet']['sha256']
    complete_packet_activation(s,damaged)
    # The native graph accepts this output's shape, but it is not the actual
    # saved Invocation product. Neither packet/audit lookup is patched here.
    assert subject.read_original_context(s.db,s.p,s.call)==output['context_result']
    before=len(s.db.added)
    with pytest.raises(HTTPException):reader.checked_context(s.db,s.p,context,task=s.task,dispatch=False)
    assert len(s.db.added)==before and s.call.data['output']==output


@pytest.mark.parametrize('fault',['version','attributes','time','entity','verification','file_output'])
def test_frozen_query_cannot_substitute_other_or_changed_observation_facts(scene,fault):
    s=scene;row=s.run.data['result']['result']['observation_evidence'][0]
    if fault=='version':row['version']+=1
    elif fault=='attributes':row['attributes']['temperature_c']=False
    elif fault=='time':row['observed_at']='2026-01-02T00:00:00+00:00'
    elif fault=='entity':row['entity_id']=identity('other-entity')
    elif fault=='verification':row['verification']='confirmed'
    else:s.extraction.data['output']['attributes']['temperature_c']=2.0
    s.run.data['native_goal_result_hash']=digest(s.run.data['result'])
    with pytest.raises((HTTPException,ValueError)):run(s)
    assert not any(model in {Packet,Command,m.Evidence} for model,_ in s.db.rows)


@pytest.mark.parametrize('fault',['property_unit','label_unit','reported_unit'])
def test_real_property_label_and_reported_units_must_agree_before_any_evidence(scene,fault):
    s=scene
    if fault=='property_unit':s.package.data['specification']['entity_schemas']['object']['properties']['temperature_c']['unit']='°F';s.package.content_hash=digest(s.package.data);s.package.validated_hash=s.package.content_hash
    elif fault=='label_unit':s.release.data['snapshot']['object_types'][0]['fields'][0]['unit']='°F'
    else:s.obs.data['field_units']={'temperature_c':'°F'}
    with pytest.raises(HTTPException):run(s)
    assert not any(model in {Packet,Command,m.Evidence} for model,_ in s.db.rows)


def test_http_origin_requires_actual_saved_exchange_and_its_field_unit_not_only_a_marker(scene,monkeypatch):
    from generic_agents import simulation_observation_origin_v177 as origins,http_observation_exchange_v177 as ledger
    s=scene;checks=[];exchange_id=identity('checked-http-exchange')
    monkeypatch.setattr(origins,'guard',lambda *args,**kwargs:{'simulation':True,'exchange_id':exchange_id})
    def checked(db,p,identifier,*,lock=False):
        assert (db,p,identifier,lock)==(s.db,s.p,exchange_id,False);checks.append(identifier)
        return NS(data={'projection':{'field_units':{'temperature_c':'°F'}}})
    monkeypatch.setattr(ledger,'checked_store',checked)
    with pytest.raises(HTTPException):run(s)
    assert checks==[exchange_id] and not any(model is m.Evidence for model,_ in s.db.rows)
    def missing(*args,**kwargs):raise HTTPException(409,'independent HTTP inventory missing')
    monkeypatch.setattr(ledger,'checked_store',missing)
    with pytest.raises(HTTPException):run(s)
    assert not any(model is m.Evidence for model,_ in s.db.rows)


def test_waiting_goal_and_oversize_body_never_create_a_partial_evidence(scene):
    s=scene;old=s.task.data.pop('native_goal_context')
    with pytest.raises(HTTPException):run(s)
    assert not any(model in {Packet,Command,m.Evidence} for model,_ in s.db.rows)
    s.task.data['native_goal_context']=old;s.task.title='超长任务'+('界'*7500)
    with pytest.raises(HTTPException):run(s)
    assert not any(model in {Packet,Command,m.Evidence} for model,_ in s.db.rows)


@pytest.mark.parametrize('retained',['packet','marker','saved_audit','produced_audit','evidence'])
def test_missing_original_command_with_any_reverse_inventory_cannot_report_not_found_or_rebuild(scene,retained):
    s=scene;output=run(s);finish(s,output);command=next(row for (model,_),row in s.db.rows.items() if model is Command)
    sha=command.body_sha256;packet=s.db.rows[Packet,output['packet']['id']]
    original_rows=dict(s.db.rows);s.db.rows={key:row for key,row in original_rows.items() if key[0] not in {Packet,Command,m.Evidence,m.Audit}}
    s.call.data.pop(subject.STATE_KEY,None);s.call.data.pop('output',None);s.call.status='running'
    if retained=='packet':s.db.rows[Packet,packet.id]=packet
    elif retained=='marker':s.call.data[subject.STATE_KEY]=None
    elif retained=='evidence':
        ev=original_rows[m.Evidence,output['evidence_ids'][0]];s.db.rows[m.Evidence,ev.id]=ev
    else:
        action=subject.SAVED if retained=='saved_audit' else subject.PRODUCED
        audit_row=next(row for (model,_),row in original_rows.items() if model is m.Audit and row.action==action)
        s.db.rows[m.Audit,audit_row.id]=audit_row
    before=len(s.db.added)
    with pytest.raises(HTTPException):subject.recover(s.db,s.p,s.key,sha)
    with pytest.raises(HTTPException):run(s)
    assert len(s.db.added)==before


def test_only_a_truly_empty_original_key_may_be_not_found_and_receipt_keeps_creation_consent(scene):
    s=scene;unknown=subject.recover(s.db,s.p,'unseen-public-original','a'*64);assert unknown['found'] is False
    output=run(s);finish(s,output);ev=s.db.rows[m.Evidence,output['evidence_ids'][0]]
    ev.version+=1;ev.data={**ev.data,'cloud_model_ids':[identity('independent-generic-model')]}
    assert subject.read_original_context(s.db,s.p,s.call)==output['context_result']
    view=subject.get(s.db,s.p,output['packet']['id']);assert view['evidence']['version']==ev.version
    assert view['evidence']['cloud_model_ids']==ev.data['cloud_model_ids'] and output['packet']['cloud_authorized'] is False


@pytest.mark.parametrize('fault',['bytes','saved_audit','produced_audit','marker','packet_data'])
def test_original_context_needs_actual_bytes_inventory_marker_and_independent_audits(scene,fault):
    s=scene;output=run(s);finish(s,output);packet=s.db.rows[Packet,output['packet']['id']]
    if fault=='bytes':
        ev=s.db.rows[m.Evidence,output['evidence_ids'][0]];(evidence.settings().storage_dir/ev.storage_key).write_bytes(b'changed original')
    elif fault in {'saved_audit','produced_audit'}:
        action=subject.SAVED if fault=='saved_audit' else subject.PRODUCED
        s.db.rows={key:row for key,row in s.db.rows.items() if not (key[0] is m.Audit and row.action==action)}
    elif fault=='marker':s.call.data[subject.STATE_KEY]={}
    else:packet.data['snapshot']['task_title']='rewritten title'
    with pytest.raises(HTTPException):subject.read_original_context(s.db,s.p,s.call)


def test_known_internal_identifiers_are_rejected_but_distinct_business_uuid_and_typed_values_are_preserved():
    internal=identity('actual-task');business=identity('public-business-serial')
    value={'attributes':{'业务编号':business,'zero':0,'false':False,'null':None,'integer':1,'float':1.0,'negative_zero':-0.0}}
    assert subject._public(copy.deepcopy(value),{internal})==value
    with pytest.raises(HTTPException):subject._public({'业务编号':internal},{internal})
    with pytest.raises(HTTPException):subject._public({'Authorization':'not copied'},set())


def test_model_choices_are_exact_task_pins_and_a_marker_alone_is_not_cloud_consent(scene):
    from tests.test_business_reasoning_v197 import scene as reasoning_scene
    from tests.test_full_semantic_graph_v1 import ability
    s=scene;r=reasoning_scene();r.model.workspace_id=s.p.workspace_id;r.model.name=r.model.data['name'];r.cap.workspace_id=s.p.workspace_id
    for row in (r.model,r.cap):s.db.rows[m.Capability,row.id]=row
    node=ability('public-reasoner',r.inputs);node['actor_id']=s.p.user_id
    node['execution'].update(adapter='business_reasoning',agent_role='business',capability_id=r.cap.id,
        capability_hash=r.cap.content_hash,release_version=r.cap.release_version,max_attempts=1,
        input_schema=r.cap.data['input_schema'],output_schema=r.cap.data['output_schema'],arguments=r.inputs,input_bindings={})
    graph=kernel.validate_graph(base([s.node,node,control('context-end','end')],
        [edge(s.node['id'],node['id']),edge(node['id'],'context-end')],s.node['id'],[s.node['id'],node['id']]))
    # Explicit frozen graph IO boundary; this test is not an adoption receipt.
    s.binding['graph']=graph;s.task.data['graph_runtime']['graph_hash']=digest(graph)
    ev=NS(id=identity('model-choice-evidence'),workspace_id=s.p.workspace_id,sha256='a'*64,
        media_type='text/plain',version=2,data={'cloud_model_ids':[r.model.id]})
    choices,available=subject.model_options(s.db,s.p,s.task,ev)
    assert available is True and len(choices)==1 and choices[0]['id']==r.model.id and choices[0]['authorized'] is False
    grant=m.Audit(workspace_id=s.p.workspace_id,actor_id=s.p.user_id,action='evidence.cloud_authorize',object_id=ev.id,
        data={'model_id':r.model.id,'sha256':ev.sha256,'version':2,'reason':'explicit fixture consent'})
    s.db.add(grant);s.db.flush();choices,available=subject.model_options(s.db,s.p,s.task,ev)
    assert available is True and choices[0]['authorized'] is True
    r.model.status='draft';assert subject.model_options(s.db,s.p,s.task,ev)==([],False)


@pytest.mark.parametrize('field,value',[('execution',{'adapter':subject.ADAPTER,'side_effect':False}),
    ('allowed_agent_roles',['business']),('budget',{'max_attempts':2}),('dependencies',[identity('model')]),
    ('data_policy',{'cloud_allowed':True})])
def test_manifest_cannot_disguise_persistent_local_effect_or_authorize_cloud(scene,field,value):
    data=copy.deepcopy(scene.cap.data);data[field]=value
    with pytest.raises((HTTPException,ValueError)):subject.validate_manifest(data)


@pytest.mark.parametrize('fault',[None,'missing_lease','wrong_actor'])
def test_actual_packet_guard_is_preinvocation_and_keeps_current_lease_175_goal_release_and_actor_gates(scene,monkeypatch,fault):
    from generic_agents import leases,layered_approval_store_v175,workflow_graph_goal_guard_v133,relations
    s=scene;checks=[];token=identity('current-lease');s.job.lease_token=token
    transport={'job_id':s.job.id,'invocation_key':s.key,'cancel_epoch':0,'input_hash':digest(s.inputs),'phase':'running'}
    s.task.data['graph_jobs']={s.job.data['activation_id']:transport}
    # This is the real pre-Invocation validator, at explicit IO/authority seams.
    s.db.rows.pop((m.Invocation,s.call.id))
    monkeypatch.setattr(leases,'ownership',lambda db:{'job_id':s.job.id,'lease_token':token if fault!='missing_lease' else None})
    monkeypatch.setattr(layered_approval_store_v175,'guard',lambda *args,**kwargs:checks.append('175'))
    monkeypatch.setattr(workflow_graph,'live',lambda *args:checks.append('live'))
    monkeypatch.setattr(workflow_graph_goal_guard_v133,'before_dispatch',lambda *args:checks.append('goal'))
    monkeypatch.setattr(relations,'lock_current_actor',lambda *args:checks.append('actor'))
    if fault=='wrong_actor':s.job.data['actor_id']=identity('other-actor')
    if fault:
        with pytest.raises(HTTPException):s.actual_approval(s.db,s.p,s.cap,'data',s.inputs,s.key,s.approval)
        assert checks==[]
    else:
        result=s.actual_approval(s.db,s.p,s.cap,'data',s.inputs,s.key,s.approval)
        assert result['capability'] is s.cap and checks==['175','live','goal','actor']
    assert not any(model in {Packet,Command,m.Evidence} for model,_ in s.db.rows)


def test_query_routes_are_get_only_and_reject_duplicate_scope_or_mutation(scene):
    s=scene;output=run(s);finish(s,output);app=FastAPI();app.include_router(api.router)
    app.dependency_overrides[api.authenticated]=lambda:s.p;app.dependency_overrides[api.session]=lambda:s.db
    with TestClient(app) as client:
        assert client.get('/api/task-evidence-packets',params={'task_id':s.task.id}).status_code==200
        assert client.get('/api/task-evidence-packets/'+output['packet']['id']).status_code==200
        assert client.get('/api/task-evidence-packets?task_id='+s.task.id+'&task_id='+s.task.id).status_code==422
        assert client.post('/api/task-evidence-packets/'+output['packet']['id'],json={'authorize':True}).status_code==405
