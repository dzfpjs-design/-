"""Real local CPU candidate -> actual DataManager reception -> adopted graph.

The source-design fixture is explicitly synthetic. Installation/inference,
registration, reception, admission, window, branch and human writes are real.
"""
import copy
import json
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.instance_compiler import signature_value
from generic_agents.models import CloudCall, Entity, Event, GeoPair, Invocation, Job, Memory, Observation, Task
from generic_agents.graph_event_models import GraphEventAdmission, GraphEventIntent
from generic_agents.worker import process_once, deliver_events
from tests.test_native_audio_wait_journey_v136 import native_wait_seed, v7_scene, reading_scene, local_runtime, full_source_scene
from tests.test_graph_wait_journey_v118 import build_waiting_release, start, poll
from tests.test_full_semantic_graph_journey_v114 import human
from tests.test_instance_workspace import configuration as manual_configuration, save
from tests.test_geo_pairs import ready
from tests.test_learned_geo_pairs import rasters
from tests.test_workflow_scenes import attach
from tests.test_workflow_abilities import finish_job
from tests.test_native_graph_pipeline_v136 import wire


def ok(response, status=200):
    assert response.status_code == status, response.text[:1000]
    return response.json()


def test_real_received_learned_candidate_drives_same_object_wait_and_coverage_review(native_wait_seed, account, rasters):
    seed = native_wait_seed; client = seed['client']
    value = build_waiting_release(seed, 3600)
    identity = value['release']['instance_id']; base = '/api/workspace/instances/' + identity
    with Session(engine()) as db: source_phase_calls = len(list(db.scalars(select(CloudCall.id))))
    install = ok(client.post('/api/specialists/installations', json={
        'pack_id': 'changer-r18-binary-v1', 'source': 'prepared_local', 'backend': 'cpu'}), 201)
    assert process_once(install['job_id'], deliver=False)
    installed = next(row for row in ok(client.get('/api/specialists/installations')) if row['id'] == install['id'])
    assert installed['status'] == 'ready' and installed['smoke']['inference_performed'] is True
    assert installed['smoke']['domain_quality_verified'] is False
    a = ready(client, rasters / 'a.tif', '真实本地流程A.tif')
    b = ready(client, rasters / 'b.tif', '真实本地流程B.tif')
    sample = ok(client.post('/api/geo/learned-pairs', json={
        'request_id': str(uuid4()), 'model_installation_id': installed['id'],
        'model_generation': installed['generation'], 'model_recipe_hash': installed['recipe_hash'],
        'before_scene_id': a['id'], 'after_scene_id': b['id'], 'before_version': a['version'], 'after_version': b['version'],
        'roi': [0, 0, 80, 64], 'alignment': 'same_grid', 'probability_threshold': .5,
        'rgb_comparability_confirmed': True}), 201)
    assert process_once(sample['job_id'], deliver=False)
    sample = ok(client.get('/api/geo/pairs/' + sample['id'])); assert sample['status'] == 'succeeded'
    registered = ok(client.post('/api/geo/pairs/' + sample['id'] + '/register-business', json={'version': sample['version']}))
    current = ok(client.get('/api/setup/draft')); configuration = copy.deepcopy(current['configuration'])
    business_name = seed['record']['design']['result']['businesses'][0]['name']
    extra = manual_configuration(); flow = copy.deepcopy(extra['flows']['资料核验'])
    flow['steps'][0].update(name='固定A/B范围的本地变化候选', agent='多模感知智能体', role='',
        execution={'capability_id': registered['capability_id'], 'automation_action': 'observe', 'arguments': {}, 'bindings': {}})
    configuration['businesses'].append('资料核验'); configuration['flows']['资料核验'] = flow
    configuration['automation']['policies']['资料核验'] = copy.deepcopy(extra['automation']['policies']['资料核验'])
    configuration['automation']['policies']['资料核验']['rules']['observe'] = {'mode': '自动运行'}
    flow['confirmedSignature'] = json.dumps(signature_value(configuration, flow), ensure_ascii=False)
    save(client, configuration, review_automation=True)
    preview = ok(client.get(base + '/native-receipts'))
    assert ok(client.post(base + '/native-receipts/sync', json={'preview_hash': preview['preview_hash']}))['created'] == 0
    native = base + '/graph-event-native-learned-geo-producers'
    options = wire(client.get(native + '/options'))
    assert options['can_register'] and options['sources'] and options['capabilities']
    source = options['sources'][0]
    capability = next(row for row in options['capabilities'] if row['id'] == registered['capability_id'])
    command = {'schema': 'graph-event-native-learned-geo-producer-command-v1', 'request_id': str(uuid4()),
        'label': '已授权本地变化候选', 'adapter_id': 'native-learned-binary-change-v1', 'instance_id': identity,
        'source_id': source['id'], 'source_hash': source['content_hash'],
        'capability_id': capability['id'], 'capability_hash': capability['content_hash']}
    proposed = wire(client.post(native + '/preview', json=command))
    producer = wire(client.post(native, json=command), 201)['producer']
    assert producer['contract_hash'] == proposed['contract_hash']
    assert wire(client.post(native, json=command), 201)['producer'] == producer
    with Session(engine()) as db: assert not list(db.scalars(select(GraphEventIntent)))
    current = ok(client.get('/api/setup/draft')); path = value['adopt_path']
    parent = ok(client.get(path + '/requests/' + value['adopt_body']['request_id']))['receipt']
    graph_body = copy.deepcopy({key: item for key, item in value['adopt_body'].items()
        if key not in {'request_id', 'preview_hash', 'consent', 'semantic_mode'}})
    graph_body.update(schema='report-full-semantic-graph-revision-preview-command-v4',
        parent={'id': parent['id'], 'receipt_hash': parent['receipt_hash']},
        configuration_revision=current['revision'], configuration_hash=digest(current['configuration']))
    graph = graph_body['graph']
    graph['subscriptions'][0].update(event_type=producer['contract']['event_kind'], output_schema=copy.deepcopy(producer['contract']['output_schema']))
    graph_body['event_source_bindings']['observed'].update(producer_id=producer['id'],
        contract_hash=producer['contract_hash'], contract=copy.deepcopy(producer['contract']))
    # This is an actual observed coverage metric, not a constant true branch.
    graph['references']['unknown_pixels'] = {'kind': 'integer', 'nullable': False,
        'source': 'output', 'field': 'unknown_pixels', 'node_id': 'wait_source'}
    graph['predicates']['p_after'] = {'op': 'gt', 'left': {'op': 'ref', 'key': 'unknown_pixels'},
        'right': {'op': 'value', 'kind': 'integer', 'value': 0}}
    claim = next(row for row in graph_body['owner_bindings'] if row['owner_path'] == '/global_claims/0')
    next(row for row in claim['clauses'] if row['source_field'] == 'statement')['targets'].append({'kind': 'value_ref', 'id': 'unknown_pixels'})
    graph_preview = ok(client.post(path + '/revisions/preview', json=graph_body))
    adopt = {**graph_body, 'schema': 'report-full-semantic-graph-revision-command-v4', 'request_id': str(uuid4()),
        'preview_hash': graph_preview['preview_hash'], 'consent': True, 'semantic_mode': 'workspace_workflow_design'}
    ok(client.post(path + '/revisions/adopt', json=adopt))
    compilation = copy.deepcopy(value['compilation'])
    member = next(row for row in ok(client.get('/api/instances/members')) if row['id'] == seed['scene']['p'].user_id)
    current = ok(client.get('/api/setup/draft'))
    compilation.update(revision=current['revision'], instance_id=identity,
        role_bindings={'职责甲': member['id'], '职责乙': member['id'], '核验负责人': member['id']})
    checked = ok(client.post('/api/instances/check', json=compilation))
    assert checked['general_issues'] == [] and {row['name'] for row in checked['businesses'] if row['available']} == {business_name, '资料核验'}, checked
    release = ok(client.post('/api/instances', json={**compilation,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True}), 201)
    instance = next(row for row in ok(client.get('/api/instances')) if row['id'] == identity)
    ok(client.post('/api/instances/' + identity + '/activate', json={'version': instance['version'],
        'release_id': release['id'], 'business_ids': release['available_business_ids'], 'confirm_workflow_scope': True}))
    inputs = [ok(attach(client, release, a, roi=[0, 0, 80, 64]), 201), ok(attach(client, release, b, roi=[10, 8, 72, 56]), 201)]
    obj = next(row for row in ok(client.get('/api/instances/' + identity + '/objects')) if row['id'] == value['object']['id'])
    graph_business = next(row for row in release['snapshot']['businesses'] if row['name'] == business_name)
    model_business = next(row for row in release['snapshot']['businesses'] if row['name'] == '资料核验')
    with Session(engine()) as db: entity_before = copy.deepcopy(db.get(Entity, obj['id']).data)
    waiting = start({'client': client, 'release': {**release, 'available_business_ids': [graph_business['id']]}, 'object': obj})
    opened = copy.deepcopy(waiting['graph_wait_authority'])
    task = finish_job(client, ok(client.post(base + '/tasks', json={'release_id': release['id'], 'business_id': model_business['id'],
        'entity_ids': [obj['id']], 'title': '为已打开流程提供实际本地变化候选',
        'comparison_scene_ids': [row['id'] for row in inputs], 'comparison_confirmed': True, 'request_id': str(uuid4())}), 201))
    output = task['workflow_runtime']['receipts'][0]['output']; pair_id = output['geo_pair_id']
    assert pair_id != sample['id'] and output['spatial_comparison']['learned_model'] is True
    assert output['spatial_comparison']['unknown_pixels'] > 0
    with Session(engine()) as db:
        queue = db.scalar(select(Job).where(Job.kind == 'sync_native_receipts', Job.data['original_task_id'].as_string() == task['id']))
        assert queue is not None; queue_id = queue.id
    assert process_once(queue_id, deliver=False)
    with Session(engine()) as db:
        observation = db.scalar(select(Observation).where(Observation.source_record_id == 'GeoPair:' + pair_id + ':' + obj['id']))
        assert observation is not None and observation.observed_at is None
        observation_id = observation.id
        assert not list(db.scalars(select(GraphEventIntent).where(GraphEventIntent.observation_id == observation_id))), 'No typed intent before actual DataManager ACK'
    deliver_events()
    with Session(engine()) as db:
        event = db.scalar(select(Event).where(Event.idempotency_key == 'data:' + observation_id))
        assert event.status == 'received'
        memories = list(db.scalars(select(Memory).where(Memory.data['event_id'].as_string() == event.id,
            Memory.data['type'].as_string() == 'observation_evidence')))
        assert len(memories) == 1 and memories[0].data['data_reception']['learned_model'] is True
        intents = list(db.scalars(select(GraphEventIntent).where(GraphEventIntent.observation_id == observation_id)))
        assert len(intents) == 1 and intents[0].producer_id == producer['id']; intent_id = intents[0].id
        projection = copy.deepcopy(intents[0].data['projection'])
        assert projection['output']['data_received'] is True and projection['output']['observed_at'] is None
        assert projection['output']['unknown_pixels'] > 0 and projection['output']['candidate_only'] is True
        dispatch = db.scalar(select(Job).where(Job.idempotency_key == 'typed-graph-source:' + intent_id)); assert dispatch is not None
        dispatch_id = dispatch.id
    assert process_once(dispatch_id, deliver=False)
    with Session(engine()) as db:
        admission = db.scalar(select(GraphEventAdmission).where(GraphEventAdmission.intent_id == intent_id)); assert admission is not None
        admission_id = admission.id
        assert len(list(db.scalars(select(CloudCall.id)))) == source_phase_calls
        assert db.get(Entity, obj['id']).data == entity_before
    resolved = poll(client, waiting['id']); window = next(iter(resolved['graph_wait_authority']['windows'].values()))
    assert window['status'] == 'consumed' and window['event_id'] == admission_id
    assert resolved['graph_wait_authority']['initial_cut'] == opened['initial_cut']
    assert [row['node_id'] for row in resolved['graph_pending_humans']] == ['c']
    resolved, human_draft = human(client, resolved, 'c', draft=True)
    saved = ok(client.get('/api/workspace/tasks/' + resolved['id'])); assert saved['graph_runtime'] == resolved['graph_runtime']
    preview = ok(client.get(base + '/native-receipts'))
    assert ok(client.post(base + '/native-receipts/sync', json={'preview_hash': preview['preview_hash']}))['created'] == 0
    deliver_events()
    with Session(engine()) as db:
        assert len(list(db.scalars(select(GraphEventIntent).where(GraphEventIntent.observation_id == observation_id)))) == 1
        assert len(list(db.scalars(select(GraphEventAdmission).where(GraphEventAdmission.intent_id == intent_id)))) == 1
        assert db.get(GraphEventIntent, intent_id).data['projection'] == projection
        assert db.get(Entity, obj['id']).data == entity_before
    resolved, human_complete = human(client, resolved, 'c'); assert resolved['status'] == 'workflow_completed'
    with Session(engine()) as db:
        assert db.get(Memory, resolved['workflow_runtime']['data_receipt_id']).status == 'candidate'
        assert len(list(db.scalars(select(CloudCall.id)))) == source_phase_calls
    destination = os.environ.get('GA_V141_NATIVE_PROOF_OUT')
    if destination:
        from generic_agents.config import settings
        target = Path(destination); assert target.name.endswith('.native-checkpoint.private.json') and not target.exists()
        paths = ['/api/workspace/tasks/' + task['id'], '/api/workspace/tasks/' + resolved['id'],
            native + '/requests/' + command['request_id'], native + '/registrations/' + producer['id'],
            native + '/observations/' + observation_id + '/stages', base + '/native-receipts']
        readbacks = []
        for url in paths:
            response = client.get(url); ok(response); readbacks.append({'path': url, 'text': response.text})
        proof = {'schema': 'native-learned-graph-durable-checkpoint-v141', 'synthetic_only': True, 'learned_model': True,
            'account': {'username': account[0], 'password': account[1], 'workspace_id': account[2]},
            'instance_id': identity, 'release_id': release['id'], 'entity_id': obj['id'], 'producer_id': producer['id'],
            'registration': command, 'observation_id': observation_id, 'intent_id': intent_id, 'admission_id': admission_id,
            'source_run_id': sample['id'], 'business_run_id': pair_id, 'processing_task_id': task['id'],
            'waiting_task_id': resolved['id'], 'opened': opened, 'human_draft_command': human_draft,
            'storage_dir': str(settings().storage_dir), 'specialist_dir': str(settings().specialist_dir),
            'prepared_model_dir': str(Path.cwd() / '.runtime/changer-candidate'),
            'geo_python': os.environ['GA_GEO_PYTHON'], 'specialist_python': os.environ['GA_SPECIALIST_PYTHON'],
            'local_runtime_root': str(settings().local_runtime_root), 'saved_human_draft': saved,
            'human_complete_command': human_complete, 'readbacks': readbacks,
            'source_phase_cloud_calls': source_phase_calls, 'real_provider_calls_delta': 0,
            'actual_browser_verified': False, 'production_ready': False}
        target.write_text(json.dumps(proof, ensure_ascii=False, sort_keys=True)); target.chmod(0o600)
