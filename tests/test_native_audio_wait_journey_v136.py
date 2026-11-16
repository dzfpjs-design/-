"""Issued synthetic design + real public local ASR + actual native graph wait.

Only the existing document/OCR/provider source-fixture seam is substituted.
Audio inference, native authority, registrations, PG, worker and waits are real.
"""
import copy
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.graph_event_models import GraphEventAdmission, GraphEventIntent
from generic_agents.graph_wait_sources_v118 import POLICY
from generic_agents.instance_compiler import signature_value
from generic_agents.models import CloudCall, Event, Job, Memory, Observation, Task
from generic_agents.worker import deliver_events, process_once
from tests.test_full_semantic_graph_journey_v114 import (
    graph_seed, v7_scene, reading_scene, local_runtime, full_source_scene,
    configured, graph_command, tool, human, create_object,
)
from tests import test_full_semantic_graph_journey_v114 as journey
from tests.test_graph_wait_journey_v118 import start, poll
from tests.test_instance_workspace import save
from tests.test_report_full_semantic_graph_binding_v3 import edge, guards, ref
from tests.test_workflow_audio import configured as audio_configured
from tests.test_workflow_abilities import finish_job
from tests.test_native_graph_pipeline_v136 import wire


def ok(response, status=200):
    assert response.status_code == status, response.text[:800]
    return response.json()


@pytest.fixture
def native_wait_seed(v7_scene,monkeypatch):
    # End the synthetic source provider transport before the actual local
    # installer and model create their own offline-cache verifying clients.
    with monkeypatch.context() as source_phase:
        seed = journey.graph_seed.__wrapped__(v7_scene,source_phase)
    return seed


def test_real_audio_result_satisfies_only_registered_same_instance_open_wait_and_persists(native_wait_seed,account):
    seed = native_wait_seed; client = seed['client']
    with Session(engine()) as db:
        source_phase_calls = len(list(db.scalars(select(CloudCall.id))))
    from tests.test_graph_wait_journey_v118 import build_waiting_release
    from tests.test_instance_workspace import configuration as manual_configuration
    from tests.test_audio import upload
    value = build_waiting_release(seed, 3600)
    first_release = value['release']; identity = first_release['instance_id']
    base = '/api/workspace/instances/' + identity
    original = ok(upload(client),201)
    assert process_once(original['job_id'],deliver=False)
    original = ok(client.get('/api/audio/' + original['id']))
    assert original['status'] == 'succeeded'
    audio_capability = ok(client.post('/api/audio/' + original['id'] + '/register-business',json={'version':original['version']}))
    current = ok(client.get('/api/setup/draft'))
    configuration = copy.deepcopy(current['configuration'])
    business_name = seed['record']['design']['result']['businesses'][0]['name']
    extra = manual_configuration()
    flow = copy.deepcopy(extra['flows']['资料核验'])
    flow['steps'][0].update(name='本地音频转写',agent='多模感知智能体',role='',
        execution={'capability_id':audio_capability['capability_id'],'automation_action':'observe','arguments':{},'bindings':{}})
    configuration['businesses'].append('资料核验');configuration['flows']['资料核验']=flow
    configuration['automation']['policies']['资料核验']=copy.deepcopy(extra['automation']['policies']['资料核验'])
    configuration['automation']['policies']['资料核验']['rules']['observe']={'mode':'自动运行'}
    flow['confirmedSignature']=json.dumps(signature_value(configuration,flow),ensure_ascii=False)
    save(client,configuration,review_automation=True)
    # Keep the existing instance active while explicitly adding a real managed
    # source, then re-consent a successor graph; never delete issued schemas.
    preview = ok(client.get(base + '/native-receipts'))
    assert ok(client.post(base + '/native-receipts/sync',json={'preview_hash':preview['preview_hash']}))['created'] == 0
    native = base + '/graph-event-native-producers'
    options = wire(client.get(native + '/options'))
    assert options['can_register'] and options['sources'] and options['installations']
    source,installation=options['sources'][0],options['installations'][0]
    command={'schema':'graph-event-native-audio-producer-command-v1','request_id':str(uuid4()),
        'label':'已授权本地语音结果','adapter_id':'native-audio-transcription-v1','instance_id':identity,
        'source_id':source['id'],'source_hash':source['content_hash'],
        'installation_id':installation['id'],'installation_hash':installation['installation_hash']}
    proposed=wire(client.post(native + '/preview',json=command))
    producer=wire(client.post(native,json=command),201)['producer']
    assert producer['contract_hash']==proposed['contract_hash'] and 'extractor_id' not in producer['contract']
    assert wire(client.post(native,json=command),201)['producer']==producer
    current=ok(client.get('/api/setup/draft'));path=value['adopt_path']
    parent=ok(client.get(path + '/requests/' + value['adopt_body']['request_id']))['receipt']
    graph_body=copy.deepcopy({key:item for key,item in value['adopt_body'].items()
        if key not in {'request_id','preview_hash','consent','semantic_mode'}})
    graph_body.update(schema='report-full-semantic-graph-revision-preview-command-v4',
        parent={'id':parent['id'],'receipt_hash':parent['receipt_hash']},
        configuration_revision=current['revision'],configuration_hash=digest(current['configuration']))
    graph_body['graph']['subscriptions'][0].update(event_type=producer['contract']['event_kind'],
        output_schema=copy.deepcopy(producer['contract']['output_schema']))
    graph_body['event_source_bindings']['observed'].update(producer_id=producer['id'],
        contract_hash=producer['contract_hash'],contract=copy.deepcopy(producer['contract']))
    graph_preview=ok(client.post(path + '/revisions/preview',json=graph_body))
    adopt={**graph_body,'schema':'report-full-semantic-graph-revision-command-v4','request_id':str(uuid4()),
        'preview_hash':graph_preview['preview_hash'],'consent':True,'semantic_mode':'workspace_workflow_design'}
    ok(client.post(path + '/revisions/adopt',json=adopt))
    compilation=copy.deepcopy(value['compilation'])
    member=next(item for item in ok(client.get('/api/instances/members')) if item['id']==seed['scene']['p'].user_id)
    current = ok(client.get('/api/setup/draft'))
    compilation.update(revision=current['revision'], instance_id=identity,
        role_bindings={'职责甲': member['id'], '职责乙': member['id'], '核验负责人': member['id']})
    checked = ok(client.post('/api/instances/check', json=compilation))
    assert checked['general_issues'] == [] and {item['name'] for item in checked['businesses'] if item['available']} == {business_name,'资料核验'}, {'general':checked['general_issues'],'businesses':{item['name']:item['issues'] for item in checked['businesses']}}
    missing = ok(client.post('/api/instances/check', json={key: value for key, value in compilation.items() if key != 'instance_id'}))
    assert any(not item['available'] for item in missing['businesses'] if item['name'] == business_name)
    assert client.post('/api/instances', json={key: value for key, value in {
        **compilation, 'confirmed_configuration_hash': missing['configuration_hash'], 'confirm_configuration': True}.items() if key != 'instance_id'}).status_code == 409
    release = ok(client.post('/api/instances', json={**compilation, 'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True}), 201)
    instance = next(item for item in ok(client.get('/api/instances')) if item['id'] == identity)
    ok(client.post('/api/instances/' + identity + '/activate', json={'version': instance['version'], 'release_id': release['id'],
        'business_ids': release['available_business_ids'], 'confirm_workflow_scope': True}))
    candidate=next(item for item in ok(client.get(base + '/audio-candidates')) if item['source_run_id']==original['id'])
    audio_input=ok(client.post(base + '/input-audio',json={'source_run_id':original['id'],
        'source_snapshot_hash':candidate['snapshot_hash'],'request_id':str(uuid4())}),201)
    # A successor version keeps its existing business object and identifier.
    obj = next(item for item in ok(client.get('/api/instances/' + identity + '/objects')) if item['id'] == value['object']['id'])
    graph_business = next(item for item in release['snapshot']['businesses'] if item['name'] == business_name)
    graph_release = {**release, 'available_business_ids': [graph_business['id']]}
    waiting = start({'client': client, 'release': graph_release, 'object': obj})
    opened = copy.deepcopy(waiting['graph_wait_authority'])
    audio_business = next(item for item in release['snapshot']['businesses'] if item['name'] == '资料核验')
    task = finish_job(client, ok(client.post(base + '/tasks', json={'release_id': release['id'], 'business_id': audio_business['id'],
        'entity_ids': [obj['id']], 'title': '为已打开的核查流程提供真实本地语音结果', 'audio_ids': [audio_input['id']], 'request_id': str(uuid4())}), 201))
    run_id = task['workflow_runtime']['receipts'][0]['output']['audio_run_id']
    assert run_id != original['id'] and '9点' in task['workflow_runtime']['receipts'][0]['output']['audio_transcription']['transcription']['text']
    with Session(engine()) as db:
        queued = list(db.scalars(select(Job).where(Job.kind == 'sync_native_audio_run_v136', Job.data['run_id'].as_string() == run_id)))
        assert len(queued) == 1 and db.get(Job, queued[0].data['processing_job_id']).status == 'succeeded'
        sync_job = queued[0].id
    assert process_once(sync_job, deliver=False)
    with Session(engine()) as db:
        assert db.get(Job, sync_job).status == 'succeeded', db.get(Job, sync_job).data
        observation = db.scalar(select(Observation).where(Observation.source_record_id == 'AudioRun:' + run_id + ':' + obj['id']))
        assert observation is not None and observation.observed_at is None
        intents = list(db.scalars(select(GraphEventIntent).where(GraphEventIntent.observation_id == observation.id)))
        assert len(intents) == 1 and intents[0].producer_id == producer['id']
        intent_id = intents[0].id
        dispatch = db.scalar(select(Job).where(Job.idempotency_key == 'typed-graph-source:' + intent_id)).id
    deliver_events()
    assert process_once(dispatch, deliver=False)
    with Session(engine()) as db:
        admission = db.scalar(select(GraphEventAdmission).where(GraphEventAdmission.intent_id == intent_id))
        assert admission is not None
        admission_id = admission.id
        assert len(list(db.scalars(select(CloudCall.id)))) == source_phase_calls
        event = db.scalar(select(Event).where(Event.idempotency_key == 'data:' + observation.id))
        assert event is not None and event.status == 'received'
        assert db.scalar(select(Memory.id).where(Memory.data['event_id'].as_string() == event.id)) is not None
    resolved = poll(client, waiting['id'])
    window = next(iter(resolved['graph_wait_authority']['windows'].values()))
    assert window['status'] == 'consumed' and window['event_id'] == admission_id
    assert resolved['graph_wait_authority']['initial_cut'] == opened['initial_cut']
    assert [item['node_id'] for item in resolved['graph_pending_humans']] == ['c']
    resolved, human_draft = human(client, resolved, 'c', draft=True)
    saved = ok(client.get('/api/workspace/tasks/' + resolved['id']))
    assert saved['graph_runtime'] == resolved['graph_runtime']
    with Session(engine()) as db:
        durable = db.get(Task, resolved['id'])
        assert digest(durable.data['graph_wait_authority']) == digest(resolved['graph_wait_authority'])
        assert len(list(db.scalars(select(GraphEventAdmission.id).where(GraphEventAdmission.intent_id == intent_id)))) == 1
    # Repeated reception cannot mint another intent, admission or advance a human.
    preview = ok(client.get(base + '/native-receipts'))
    assert ok(client.post(base + '/native-receipts/sync', json={'preview_hash': preview['preview_hash']}))['created'] == 0
    assert ok(client.get('/api/workspace/tasks/' + resolved['id']))['graph_runtime'] == saved['graph_runtime']
    resolved, human_complete = human(client,resolved,'c')
    assert resolved['status'] == 'workflow_completed'
    with Session(engine()) as db:
        archived = db.get(Memory,resolved['workflow_runtime']['data_receipt_id'])
        assert archived is not None and archived.status == 'candidate'
        assert len(list(db.scalars(select(CloudCall.id)))) == source_phase_calls
    # Root can opt into an isolated durable checkpoint. No production path
    # writes this artifact and no credentials or full records are printed.
    import os
    from pathlib import Path
    destination=os.environ.get('GA_V136_NATIVE_PROOF_OUT')
    if destination:
        from generic_agents.config import settings
        proof_path=Path(destination)
        assert proof_path.name.endswith('.native-checkpoint.private.json') and not proof_path.exists()
        paths=['/api/workspace/tasks/'+task['id'],'/api/workspace/tasks/'+resolved['id'],
            native+'/requests/'+command['request_id'],native+'/registrations/'+producer['id'],
            native+'/observations/'+observation.id+'/stages',base+'/native-receipts']
        readbacks=[]
        for url in paths:
            response=client.get(url);ok(response)
            readbacks.append({'path':url,'text':response.text})
        proof={'schema':'native-audio-graph-durable-checkpoint-v136','synthetic_only':True,
            'account':{'username':account[0],'password':account[1],'workspace_id':account[2]},
            'instance_id':identity,'release_id':release['id'],'producer_id':producer['id'],
            'registration':command,'observation_id':observation.id,'intent_id':intent_id,'admission_id':admission_id,
            'source_run_id':original['id'],'business_run_id':run_id,'processing_task_id':task['id'],
            'waiting_task_id':resolved['id'],'opened':opened,'human_draft_command':human_draft,
            'storage_dir':str(settings().storage_dir),'local_runtime_root':str(settings().local_runtime_root) if settings().local_runtime_root else None,
            'saved_human_draft':saved,'human_complete_command':human_complete,'readbacks':readbacks,
            'source_phase_cloud_calls':source_phase_calls,'real_provider_calls_delta':0,
            'actual_browser_verified':False,'production_ready':False}
        proof_path.write_text(json.dumps(proof,ensure_ascii=False,sort_keys=True));proof_path.chmod(0o600)
