"""Real authoring/publication foundation for the future end-to-end test.

Only publication is exercised here. This does NOT create a Task, mature head,
MQTT observation, automatic evaluation, or prove an end-to-end journey.
Every run uses conftest's disposable PostgreSQL and the managed test fixture.
"""
import copy
import json
from types import SimpleNamespace
from uuid import uuid4
import pytest
from generic_agents import workflow_task_context_v196 as reader
from generic_agents import workflow_goal_data_route_runtime_v288 as local
from generic_agents import workflow_goal_evaluation_packet_v288 as packet
from generic_agents import business_goal_ready_runtime_v288 as reasoning
from generic_agents import authored_goal_data_workflow_contracts_v288 as commands
from tests.test_authored_decision_workflow_pg_v286 import prepared_decision
from tests.test_authored_service_workflow_pg_v270 import good,publish
from tests.test_authored_goal_data_workflow_v288 import policy
from tests.test_authored_goal_data_workflow_pg_v288 import tool
from tests.test_device_gateway_v268 import scene
from tests.test_device_gateway_tools_v269 import managed
from tests.test_device_gateway_source_pg_v270 import OBJECTS, WEATHER_FIELDS
from tests.test_instance_workspace import save
from generic_agents.instance_compiler import signature_value
from generic_agents.contracts import digest


@pytest.fixture
def published_goal_data(managed):
    s=managed
    original,old_pins,model,candidate,extractor,material=prepared_decision(s)
    # Publish the *same adopted graph* for a real local simulation scope with
    # numeric observations; no weather sample, cloud call, or device action.
    draft=good(s.client.get('/api/setup/draft'))
    cfg=copy.deepcopy(draft['configuration'])
    cfg['instanceProfile']['simulation_v176']=True
    flow=cfg['flows'][original['business_name']]
    flow['confirmedSignature']=json.dumps(signature_value(cfg,flow),ensure_ascii=False)
    save(s.client,cfg,review_automation=True)
    current=good(s.client.get('/api/setup/draft'))
    original={**original,'configuration_revision':current['revision'],'configuration_hash':digest(current['configuration'])}
    listed=good(s.client.get('/api/capabilities'))
    context=[row for row in listed if row['data']['execution'].get('handler')==reader.HANDLER]
    assert len(context)==1
    plan=publish(s.client,tool(local,'business',{'adapter':'workflow_service',
        'service_actor':'execution_service','operation':local.OP,'side_effect':True}))
    evaluation=publish(s.client,tool(packet,'data',{'adapter':'workflow_service',
        'service_actor':'reader_service','operation':packet.OP,'side_effect':True}))
    reason=publish(s.client,tool(reasoning,'business',{'adapter':reasoning.ADAPTER,
        'model_profile_id':model['id'],'model_profile_hash':model['content_hash'],'side_effect':False},
        dependencies=[model['id']],cloud=True))
    pins={'context_reader':context[0],'local_prepare':plan,'resense':old_pins['resense'],
        'evaluation_packet':evaluation,'packet':old_pins['packet'],'reasoning':reason}
    body={**original,'schema':'authored-goal-data-workflow-preview-command-v288',
        'preparation_policy':policy(),'node_actions':commands.NODE_ACTIONS,
        'external_node_ids':commands.EXTERNAL_NODE_IDS,
        'tool_bindings':{name:{'capability_id':row['id'],'capability_hash':row['content_hash'],
            'actor_id':s.actor_id} for name,row in pins.items()}}
    preview=good(s.client.post(commands.PREFIX+'/preview',json=body))
    adoption={**body,'schema':'authored-goal-data-workflow-adopt-command-v288',
        'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True}
    good(s.client.post(commands.PATH,json=adoption),201)
    draft=good(s.client.get('/api/setup/draft'))
    request={'revision':draft['revision'],'objects':OBJECTS,
        'role_bindings':{key:actor['user_id'] for key,actor in preview['binding']['role_bindings'].items()}}
    checked=good(s.client.post('/api/instances/check',json=request))
    assert not checked['general_issues'] and all(x['available'] for x in checked['businesses'])
    release=good(s.client.post('/api/instances',json={**request,
        'confirmed_configuration_hash':checked['configuration_hash'],'confirm_configuration':True}),201)
    assert release['snapshot']['semantic_runtime_plan']['graph_adoptions'][0]['binding']==preview['binding']
    return SimpleNamespace(scene=s,release=release,pins=pins,preview=preview,extractor=extractor,
        candidate=candidate,material=material,model=model)


def test_real_six_port_publication_foundation(published_goal_data):
    s=published_goal_data
    graph=s.release['snapshot']['semantic_runtime_plan']['graph_adoptions'][0]['binding']['graph']
    assert graph['entry']=='context_reader'
    assert s.pins['evaluation_packet']['data']['execution']['operation']==packet.OP


def test_published_six_port_release_accepts_an_independent_simulation_source(published_goal_data):
    """Real gateway and registration only; no Task, evidence, or model call."""
    s=published_goal_data.scene;release=published_goal_data.release;client=s.client
    instance=next(x for x in good(client.get('/api/instances')) if x['id']==release['instance_id'])
    good(client.post('/api/instances/'+instance['id']+'/activate',json={'version':instance['version'],
        'release_id':release['id'],'business_ids':release['available_business_ids'],'confirm_workflow_scope':True}))
    environment=good(client.post('/api/workspace/simulation-v176/environments',json={'request_id':str(uuid4()),
        'instance_id':instance['id'],'release_id':release['id'],'note':'隔离软件模拟，不代表实际设备或现场情况。',
        'confirm_simulation':True}),201)
    device=good(client.get('/api/sensing/devices/'+s.grant['sensor_device_id']))
    if device['status']=='review':device=good(client.post('/api/sensing/devices/'+device['id']+'/approve',json={
        'expected_version':device['version'],'reason':'仅登记隔离模拟设备身份。'}))
    assert device['status']=='enabled'
    root='/api/workspace/device-gateways/'+s.connection_id
    connection=good(client.get(root));grant=next(x for x in connection['grants'] if x['id']==s.grant['id']);assert grant['effective']
    object_type=next(x for x in release['snapshot']['object_types'] if x['name']==OBJECTS[0]['name'])
    fields={x['label']:x['key'] for x in object_type['fields']}
    links={wire:{'field_key':fields[label],'unit':unit} for wire,(label,unit) in WEATHER_FIELDS.items()}
    body={'request_id':str(uuid4()),'expected_version':connection['version'],'grant_id':grant['id'],
        'grant_expected_version':grant['version'],'environment_id':environment['id'],'entity_type':object_type['key'],
        'links':links,'extractor_id':published_goal_data.extractor['id'],
        'extractor_hash':published_goal_data.extractor['content_hash'],'confirm_simulation':True,
        'reason':'登记隔离模拟来源，不触发采集。'}
    result=good(client.post(root+'/sources',json=body))
    assert result['simulation'] and not result['field_verified'] and not result['observation_created']
    assert result['capability']['status']=='published'
    from sqlalchemy import select,func
    from sqlalchemy.orm import Session
    from generic_agents.db import engine
    from generic_agents import models as m
    with Session(engine()) as db:
        for model in (m.Task,m.Observation,m.CloudCall,m.Acquisition):
            assert db.scalar(select(func.count()).select_from(model).where(model.workspace_id==s.workspace_id))==0


def test_real_published_six_port_task_with_simulated_scope_and_two_independent_approvals(published_goal_data,monkeypatch):
    """Normal APIs create scope, source material and task; no execution is faked."""
    from contextlib import ExitStack
    from generic_agents import runtime_document_receipts as delivery
    from generic_agents.contracts import GoalSpec
    from tests.auto_evaluation_journey_fixture_helpers_v357 import setup_simulated_scope
    from tests.test_authored_workflow_document_packet_dispatch_pg_v208 import operator,configure_two_stage_policy,command
    from tests.test_runtime_documents_pg_v205 import uploaded,run,version
    from sqlalchemy import select,func
    from sqlalchemy.orm import Session
    from generic_agents.db import engine
    from generic_agents import models as m
    scoped=setup_simulated_scope(published_goal_data);s=scoped.scene;s.workspace=s.workspace_id;client=s.client
    release=scoped.release;bid=release['available_business_ids'][0]
    # Authorize only the two endpoints of this test-created loopback supplier;
    # never widen connector hosts, access user services, or contact the internet.
    from urllib.parse import urlparse
    from generic_agents.config import settings
    from generic_agents.gateway import validate_endpoint
    origin=urlparse(s.origin)
    assert origin.scheme=='http' and origin.hostname=='127.0.0.1' and origin.port
    endpoints=[s.origin+'/acquisitions',s.origin+'/acquisitions/receipts']
    registered=next(row for row in good(client.get('/api/capabilities')) if row['id']==scoped.source['capability']['id'])
    assert registered['status']=='published'
    assert registered['data']['execution']['endpoint']==endpoints[0]
    assert registered['data']['execution']['receipt_endpoint']==endpoints[1]
    monkeypatch.setattr(settings(),'development_connector_urls',','.join(endpoints))
    assert all(validate_endpoint({'endpoint':value})==value for value in endpoints)
    with ExitStack() as stack:
        first,first_client=operator(s,stack);second,second_client=operator(s,stack);acceptor,_=operator(s,stack,login=False)
        configure_two_stage_policy(s,release,first,second,acceptor)
        task=good(client.post('/api/workspace/instances/'+release['instance_id']+'/tasks',json={
            'request_id':str(uuid4()),'release_id':release['id'],'business_id':bid,'entity_ids':[scoped.entity['id']],
            'title':'隔离模拟观测与独立业务评价',
            'graph_inputs':{'analysis_question':'先取得软件模拟观测和独立数据回执，再依据本次资料比较候选方案，不认定现场状态。',
                            'policy_pin':scoped.policy_pin},'goal_spec':None}),201)
        task_id=task['id'];raw=('采用本次已登记的模拟数据来源，先执行原观察并取得独立接收回执。'
            '只比较已保存的两个候选结论，资料不足时保留不确定性，不得认定为现场事实。').encode()
        document=good(uploaded(s,release,raw,'isolated-goal.txt')[0],201);version_id=document['latest_version_id']
        assert run(s,version_id)=='succeeded';delivery.consume_once(s.workspace_id);parsed=version(s,version_id)
        assert parsed['status']=='parsed' and parsed['delivery']['verified']
        blocks=good(client.get('/api/runtime-documents/versions/'+version_id+'/blocks',params={
            'attempt_id':parsed['latest_attempt_id'],'expected_parse_hash':parsed['parse_hash']}))
        assert blocks['records'] and not blocks['has_more']
        selectors=[{'kind':'native','version_id':version_id,'attempt_id':parsed['latest_attempt_id'],
            'parse_hash':parsed['parse_hash'],'block_id':block['id'],'char_span':{'start':0,'end':len(block['text'])}}
            for block in blocks['records'] if block['text']]
        base='/api/runtime-task-documents/tasks/'+task_id;context=good(client.get(base+'/context'))
        source=good(client.post(base+'/source-sets',json={'request_id':str(uuid4()),
            'expected_task_version':context['task_version'],'expected_approval_version':context['approval_version'],
            'prior_set_id':None,'prior_set_hash':None,'input_key':'runtime_document_sources','selectors':selectors}),201)
        goal=GoalSpec.model_validate({'purpose':'取得隔离模拟观测和独立收件，并形成业务评价，不证明现场状态。',
            'entity_ids':[scoped.entity['id']],'required_fields':scoped.required,'subgoals':[{
                'id':'received_evidence','description':'原观测、独立收件与不可改写评价',
                'criterion_ids':['observations_ready','evaluation_created']}],
            'criteria':[{'id':'observations_ready','kind':'evidence_sufficient','description':'资料满足来源、质量和时效要求'},
                {'id':'evaluation_created','kind':'capability_success','capability_id':published_goal_data.pins['evaluation_packet']['id'],
                 'description':'形成独立评价资料包'}],
            'evidence':{'source':'observations','time':{'mode':'latest','timezone':'Asia/Shanghai',
                'max_age_seconds':published_goal_data.preview['binding']['preparation_policy']['max_age_seconds']},
                'minimum_verification':'unverified_extraction','minimum_records_per_entity':1},
            'observation_mode':'allow_new','deadline':None,'max_steps':12,'max_tool_calls':12,'stop_on_conflict':True}).model_dump(mode='json')
        approval='/api/workspace/tasks/'+task_id+'/approval-v175';view=good(client.get(approval));value=copy.deepcopy(view['value'])
        value.update(title=task['title'],note='仅隔离软件模拟，不认定现场。',goal_spec=goal)
        value['graph_inputs']['runtime_document_sources']=source['ref']
        command(client,approval,'revise',value=value);command(client,approval,'submit')
        submitted=good(client.get(approval));submission_hash=submitted['submission_hash']
        for stage,reviewer in enumerate((first_client,second_client)):
            current=good(reviewer.get(approval));assert current['current_stage']==stage and current['submission_hash']==submission_hash
            command(reviewer,approval,'decide',submission_hash=submission_hash,decision='approve')
        approved=good(client.get(approval));assert approved['status']=='approved' and len(approved['decisions'])==2
        assert len({row['actor_id'] for row in approved['decisions']})==2 and approved['permissions']['start']
        with Session(engine()) as db:
            for model in (m.Invocation,m.CloudCall,m.Observation,m.Acquisition):
                assert db.scalar(select(func.count()).select_from(model).where(model.workspace_id==s.workspace_id))==0
        # Bound to this test-created task and its loopback supplier. Never
        # claim the reasoning node or send documents to a cloud provider.
        import time,sys,traceback
        from generic_agents import http_observation_exchange_v177 as http_ledger
        underlying_errors=[];original_need=http_ledger.need
        def observe_need(value,*args,**kwargs):
            # Observe the transport wrapper's suppressed Python exception,
            # while preserving every original check and raise unchanged.
            if not value:
                error=sys.exc_info()[1]
                if error is not None:
                    frames=traceback.extract_tb(error.__traceback__)
                    underlying_errors.append({'type':type(error).__name__,
                        'frames':[{'file':f.filename.rsplit('/',1)[-1],'function':f.name,'line':f.lineno} for f in frames[-8:]]})
            return original_need(value,*args,**kwargs)
        monkeypatch.setattr(http_ledger,'need',observe_need)
        from generic_agents.workflow_task_consumer_v265 import step
        from generic_agents.http_observation_task_consumer_v270 import tick,receive,FAMILY
        from generic_agents.workflow_goal_evaluation_models_v288 import GoalEvaluationV288
        from generic_agents.goal_evaluation_provider_v288 import KEY
        from generic_agents.contracts import digest
        command(client,approval,'start')
        assert good(client.get(approval))['status']=='executing'
        after=delivery_after=None;query_pin=None
        for _ in range(40):
            with Session(engine()) as db:
                live=db.get(m.Task,task_id);assert live.workspace_id==s.workspace_id
                jobs=list(db.scalars(select(m.Job).where(m.Job.workspace_id==s.workspace_id,
                    m.Job.data['task_id'].as_string()==task_id)))
                activations=live.data.get('graph_runtime',{}).get('activations',{})
                reason=[j for j in jobs if activations.get(j.data.get('activation_id'),{}).get('node_id')=='reasoning']
                assert all(j.status=='queued' and j.attempts==0 for j in reason)
                assert db.scalar(select(func.count()).select_from(m.CloudCall).where(m.CloudCall.workspace_id==s.workspace_id))==0
                if live.data.get('native_goal_query_id'):
                    runrow=db.get(m.DataRun,live.data['native_goal_query_id'])
                    current=(runrow.id,runrow.version,digest(runrow.data))
                    if query_pin is None:query_pin=current
                    assert current==query_pin
                head=copy.deepcopy(live.data.get(KEY))
            if reason and head:break
            graph=None
            if not reason:
                graph=step(s.workspace_id,release['instance_id'],family='graph',task_id=task_id,
                    after=after,delivery_after=delivery_after,limit=1)
                after,delivery_after=graph['after'],graph['delivery_after']
            observed=tick(s.workspace_id,release['instance_id'],task_id,family=FAMILY,limit=20)
            received=receive(s.workspace_id,release['instance_id'],task_id,family=FAMILY,limit=20)
            if observed['blocked'] or received.get('blocked'):
                # Diagnostic re-read of the same isolated test originals; never
                # mutate or retry a Job and never weaken its proof guards.
                from generic_agents.http_observation_task_consumer_v270 import _reader,_inventory,_receipt,_poll_state
                with _reader() as db:
                    try:
                        principal,original_task,bindings,observations,original_jobs=_inventory(db,s.workspace_id,release['instance_id'],task_id)
                        clock=db.scalar(select(func.clock_timestamp()))
                        for binding in bindings.values():
                            if binding['exchange'] is not None:_poll_state(binding['exchange'],original_jobs,clock)
                        for observation_id,original in observations.items():
                            event=db.scalar(select(m.Event).where(m.Event.workspace_id==s.workspace_id,
                                m.Event.kind=='observation.data',m.Event.idempotency_key=='data:'+observation_id))
                            if event is not None and event.status=='received' and original['process'].status=='succeeded':
                                _original,_event,expected=_receipt(db,s.workspace_id,release['instance_id'],task_id,observation_id)
                                from generic_agents.http_observation_task_consumer_v270 import RECEIVED
                                traces=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==s.workspace_id,
                                    m.Audit.action==RECEIVED,m.Audit.object_id==event.id).limit(2)))
                                if traces:assert len(traces)==1 and traces[0].actor_id==expected['origin_actor_id'] and traces[0].data==expected
                    except Exception as error:
                        calls=list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id==s.workspace_id)))
                        facts=[]
                        for call in calls:
                            cap=db.get(m.Capability,call.capability_id)
                            facts.append({'operation':cap.data.get('execution',{}).get('operation'),
                                'status':call.status,'error':call.data.get('error'),
                                'state_present':call.data.get('service_state') is not None,
                                'job_id':call.data.get('job_id')})
                        jobs=list(db.scalars(select(m.Job).where(m.Job.workspace_id==s.workspace_id,
                            m.Job.data['task_id'].as_string()==task_id)))
                        jobfacts=[{'kind':row.kind,'status':row.status,'error':row.data.get('error'),
                            'failure_diagnostic':row.data.get('failure_diagnostic_v289')} for row in jobs]
                        acquisitions=list(db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id==s.workspace_id)))
                        plans=list(db.scalars(select(m.ObservationPlan).where(m.ObservationPlan.workspace_id==s.workspace_id)))
                        dispatches=list(db.scalars(select(m.Job).where(m.Job.workspace_id==s.workspace_id,m.Job.kind=='dispatch_acquisition')))
                        from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
                        exchanges=list(db.scalars(select(Exchange).where(Exchange.workspace_id==s.workspace_id)))
                        acqfacts=[{'id':row.id,'status':row.status,'version':row.version} for row in acquisitions]
                        planfacts=[{'status':row.status,'current_acquisition_id':row.data.get('current_acquisition_id')} for row in plans]
                        dispatchfacts=[{'acquisition_id':row.data.get('acquisition_id'),'status':row.status,'attempts':row.attempts,
                            'has_lease':bool(row.lease_token or row.lease_until),'error':row.data.get('error'),
                            'failure_diagnostic':row.data.get('failure_diagnostic_v289')} for row in dispatches]
                        exfacts=[{'acquisition_id':row.acquisition_id,'status':row.status} for row in exchanges]
                        raise AssertionError('isolated original inventory failed: '+repr({'calls':facts,'jobs':jobfacts,
                            'acquisitions':acqfacts,'plans':planfacts,'dispatches':dispatchfacts,'exchanges':exfacts,
                            'underlying_errors':underlying_errors})) from error
            assert not (graph and graph['errors']) and not observed['blocked'] and not received.get('blocked')
            if not (graph and graph['processed']) and not observed['processed'] and not received.get('received_count'):
                time.sleep(.25)
        else:raise AssertionError('Bounded simulated original task did not reach its initial evaluation head')
        with Session(engine()) as db:
            live=db.get(m.Task,task_id);head=live.data.get(KEY);assert head and head['task_id']==task_id
            record=db.get(GoalEvaluationV288,head['evaluation_ref']['id'])
            assert record and record.task_id==task_id and record.record_hash==head['evaluation_ref']['record_hash']
            assert query_pin and db.scalar(select(func.count()).select_from(m.CloudCall).where(m.CloudCall.workspace_id==s.workspace_id))==0
            assert db.scalar(select(func.count()).select_from(m.Observation).where(m.Observation.workspace_id==s.workspace_id))>=1

        from tests.auto_evaluation_real_append_helpers_v357 import append_fresh_mqtt_observation
        result=append_fresh_mqtt_observation(scoped,published_goal_data,task_id,monkeypatch)
        assert result["simulation_only"] and result["broker_puback_proven"] is False
