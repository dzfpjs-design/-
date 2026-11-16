"""Root-owned real installer and two actual local neural model executions.

No ready Installation rows, probe stubs, model stubs or network downloads.
The tiny synthetic imagery proves engineering execution, not field quality.
"""
import json
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, GeoPair, Invocation, Job, SpecialistInstallation
from generic_agents.instance_compiler import signature_value
from generic_agents.worker import process_once, deliver_events
from tests.test_geo_codec import pytestmark
from tests.test_geo_pairs import ready
from tests.test_learned_geo_pairs import rasters
from tests.test_instance_workspace import configuration, prepared, object_row
from tests.test_workflow_scenes import attach
from tests.test_workflow_abilities import enable, finish_job


def good(response, status=200):
    assert response.status_code == status, response.text[:800]
    return response.json()


def actual_learned_journey(client, rasters):
    requested = good(client.post('/api/specialists/installations', json={
        'pack_id': 'changer-r18-binary-v1', 'source': 'prepared_local', 'backend': 'cpu'}), 201)
    assert requested['status'] == 'queued'
    assert process_once(requested['job_id'], deliver=False)
    installed = next(row for row in good(client.get('/api/specialists/installations')) if row['id'] == requested['id'])
    assert installed['status'] == 'ready', installed.get('error')
    assert installed['smoke']['inference_performed'] is True
    assert installed['smoke']['cloud_calls'] == 0 and installed['smoke']['backend'] == 'cpu'
    assert installed['smoke']['domain_quality_verified'] is False
    with Session(engine()) as db:
        audits = list(db.scalars(select(Audit).where(Audit.object_id == installed['id'], Audit.action == 'changer.install.ready')))
        assert len(audits) == 1 and audits[0].data['generation'] == installed['generation']
        assert db.get(Job, requested['job_id']).status == 'succeeded'
        assert db.get(SpecialistInstallation, installed['id']).status == 'ready'
    a = ready(client, rasters / 'a.tif', '专业模型工程A.tif')
    b = ready(client, rasters / 'b.tif', '专业模型工程B.tif')
    resource = good(client.post('/api/geo/learned-pairs', json={
        'request_id': str(uuid4()), 'model_installation_id': installed['id'],
        'model_generation': installed['generation'], 'model_recipe_hash': installed['recipe_hash'],
        'before_scene_id': a['id'], 'after_scene_id': b['id'],
        'before_version': a['version'], 'after_version': b['version'], 'roi': [0, 0, 80, 64],
        'alignment': 'same_grid', 'probability_threshold': .5, 'rgb_comparability_confirmed': True}), 201)
    assert process_once(resource['job_id'], deliver=False)
    sample = good(client.get('/api/geo/pairs/' + resource['id']))
    assert sample['status'] == 'succeeded', sample.get('error')
    assert sample['result']['learned_model'] is True and sample['result']['cloud_calls'] == 0
    assert sample['result']['backend'] == 'cpu'
    assert len(sample['result']['artifacts']) == 8
    sample_export = good(client.get('/api/geo/pairs/' + sample['id'] + '/export'))
    assert sample_export['result_hash'] == sample['result_hash']
    registered = good(client.post('/api/geo/pairs/' + sample['id'] + '/register-business',
        json={'version': sample['version']}))
    assert registered['kind'] == 'specialist_model' and registered['learned_model'] is True
    config = configuration(); flow = config['flows']['资料核验']
    flow['steps'][0].update(name='本地变化模型候选核查', agent='多模感知智能体', role='',
        execution={'capability_id': registered['capability_id'], 'automation_action': 'observe', 'arguments': {}, 'bindings': {}})
    config['automation']['policies']['资料核验']['rules']['observe'] = {'mode': '自动运行'}
    flow['confirmedSignature'] = json.dumps(signature_value(config, flow), ensure_ascii=False)
    release, _, check = prepared(client, config)
    assert release['available_business_ids'], check
    enable(client, release)
    sources = [good(attach(client, release, a, roi=[0, 0, 80, 64]), 201),
        good(attach(client, release, b, roi=[10, 8, 72, 56]), 201)]
    obj = object_row(client, release); base = '/api/workspace/instances/' + release['instance_id']
    creation = {'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': [obj['id']], 'title': '真实本地学习模型候选核查',
        'comparison_scene_ids': [row['id'] for row in sources], 'comparison_confirmed': True, 'request_id': str(uuid4())}
    task = finish_job(client, good(client.post(base + '/tasks', json=creation), 201))
    assert task['status'] == 'workflow_waiting', task.get('workflow_runtime', {}).get('automatic')
    receipt = task['workflow_runtime']['receipts'][0]
    result = receipt['output']['spatial_comparison']; run_id = receipt['output']['geo_pair_id']
    assert run_id != sample['id'] and result['learned_model'] is True and result['cloud_calls'] == 0
    assert result['after_read_window'] == [10, 8, 72, 56]
    assert len(result['artifacts']) == 8 and receipt['candidate_only'] is True
    with Session(engine()) as db:
        actual = db.get(GeoPair, run_id)
        assert actual.data['request_kind'] == 'learned_binary_change'
        assert len(list(db.scalars(select(Invocation)))) == 1
        assert not list(db.scalars(select(CloudCall)))
        queued = db.scalar(select(Job).where(Job.kind == 'sync_native_receipts',
            Job.data['original_task_id'].as_string() == task['id']))
        assert queued is not None and queued.status == 'queued'
        queue_id = queued.id
    assert process_once(queue_id, deliver=False)
    deliver_events()
    listing = good(client.get(base + '/native-receipts'))
    item = next(row for row in listing['records'] if row.get('record', {}).get('source_key') == 'GeoPair:' + run_id)
    assert item['data_received'] and not item['integrity_error'], (item['data_received'], item['integrity_error'])
    return {'installation': installed, 'sample': sample, 'release': release, 'creation': creation,
        'task': task, 'base': base, 'pair_id': run_id, 'observation': item, 'queue_id': queue_id}


def test_actual_install_resource_model_business_model_and_modern_data_reception(client, rasters):
    actual_learned_journey(client, rasters)


def test_actual_learned_reception_human_work_and_restore_checkpoint(client, rasters, account):
    import os
    from pathlib import Path
    from generic_agents.models import Entity, Event, Memory, Observation
    from tests.test_native_receipts import bridge
    from tests.test_instance_workspace import complete
    value = actual_learned_journey(client, rasters)
    task, base, item = value['task'], value['base'], value['observation']
    reception = item['data_reception']
    assert reception['schema'] == 'instance-native-learned-geo-pair-data-reception-v1'
    assert reception['learned_model'] is True and reception['candidate_only'] is True
    assert reception['declaration_status'] == 'not_declared' and reception['declared_outputs'] == []
    assert reception['original_declared_outputs'] and len(reception['artifacts']) == 8
    for key in ['source_time_verified', 'physical_state_verified', 'entity_state_updated', 'can_execute', 'can_delete']:
        assert reception[key] is False
    with Session(engine()) as db:
        observation = db.get(Observation, item['id'])
        packet = json.dumps(observation.data['native_packet'], ensure_ascii=False, sort_keys=True)
        event = db.scalar(select(Event).where(Event.idempotency_key == 'data:' + observation.id))
        memories = list(db.scalars(select(Memory).where(Memory.data['event_id'].as_string() == event.id,
            Memory.data['type'].as_string() == 'observation_evidence')))
        assert observation.observed_at is None and event.status == 'received' and event.data['invocation_id'] is None
        assert len(memories) == 1 and memories[0].status == 'candidate'
        assert memories[0].data['data_reception'] == reception
        assert reception['model_trace'] == db.get(GeoPair, value['pair_id']).data['model_trace']
        assert reception['model_ready_audit_id'] and db.get(Audit, reception['model_ready_audit_id']).action == 'changer.install.ready'
        memory_id, event_id = memories[0].id, event.id
        original_hash = db.get(GeoPair, value['pair_id']).data['result_hash']
    draft = {'version': task['version'], 'step_id': task['workflow_runtime']['current_step_id'],
        'note': '已核对模型、A/B允许范围及概率产物，仅为机器候选；现场变化尚未证实。'}
    task = good(client.put('/api/workspace/tasks/' + task['id'] + '/note', json=draft))
    saved = good(client.get('/api/workspace/tasks/' + task['id']))
    assert saved['workflow_runtime']['drafts'][draft['step_id']]['note'] == draft['note']
    repeated, listing = bridge(client, base)
    assert repeated['created'] == 0 and repeated['already_saved'] == 1
    assert listing['records'][0]['record']['review_state'] == 'draft_saved'
    task = good(complete(client, task, note=draft['note']))
    assert task['status'] == 'workflow_completed'
    repeated, listing = bridge(client, base)
    assert repeated['created'] == 0 and repeated['already_saved'] == 1
    assert listing['records'][0]['data_received'] and listing['records'][0]['record']['review_state'] == 'confirmed'
    with Session(engine()) as db:
        assert db.get(GeoPair, value['pair_id']).data['result_hash'] == original_hash
        assert json.dumps(db.get(Observation, item['id']).data['native_packet'], ensure_ascii=False, sort_keys=True) == packet
        assert db.get(Memory, memory_id).data['data_reception'] == reception
        assert db.get(Entity, value['creation']['entity_ids'][0]).version == 1
        assert len(list(db.scalars(select(Invocation)))) == 1 and not list(db.scalars(select(CloudCall)))
    destination = os.environ.get('GA_V138_NATIVE_PROOF_OUT')
    if destination:
        from generic_agents.config import settings
        paths = ['/api/workspace/tasks/' + task['id'], base + '/native-receipts',
            base + '/native-receipts/' + item['id'] + '/export',
            '/api/workspace/tasks/' + task['id'] + '/comparisons/' + value['pair_id'] + '/export',
            '/api/geo/pairs/' + value['sample']['id'] + '/export', '/api/geo/learned-pairs/catalog']
        readbacks = []
        for url in paths:
            response = client.get(url)
            assert response.status_code == 200, response.text[:600]
            readbacks.append({'path': url, 'text': response.text})
        proof = {'schema': 'native-learned-geo-durable-checkpoint-v138', 'synthetic_only': True,
            'account': {'username': account[0], 'password': account[1], 'workspace_id': account[2]},
            'instance_id': value['release']['instance_id'], 'release_id': value['release']['id'], 'task_id': task['id'],
            'pair_id': value['pair_id'], 'sample_id': value['sample']['id'], 'installation_id': value['installation']['id'],
            'observation_id': item['id'], 'event_id': event_id, 'memory_id': memory_id, 'creation': value['creation'],
            'saved_draft': draft, 'automatic_native_sync_job_id': value['queue_id'], 'automatic_native_sync_verified': True,
            'result_hash': original_hash, 'model_trace': reception['model_trace'], 'readbacks': readbacks,
            'storage_dir': str(settings().storage_dir), 'specialist_dir': str(settings().specialist_dir),
            'prepared_model_dir': str(Path(__file__).resolve().parents[1] / '.runtime/changer-candidate'),
            'geo_python': str(settings().geo_python), 'specialist_python': str(settings().specialist_python),
            'real_provider_calls_delta': 0, 'learned_model': True, 'model_domain_quality_verified': False,
            'actual_browser_verified': False, 'production_ready': False}
        path = Path(destination)
        assert path.name.endswith('.native-checkpoint.private.json') and not path.exists()
        path.write_text(json.dumps(proof, ensure_ascii=False, sort_keys=True)); path.chmod(0o600)
