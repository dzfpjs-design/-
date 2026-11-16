"""Issued synthetic graph plus actual public local ASR and durable review.

Reuse the document/OCR/provider seams of graph_seed only. The synthetic source
explicitly assigns the audio step to perception before formal issuance. Actual
installation, publication, graph adoption, compiler, worker, gateway, model and
ledger checks are not patched. No assertion claims transcription/domain quality.
Root alone runs this against its disposable database and copied public assets.
"""
import copy
import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import AudioRun, Capability, CloudCall, Entity, Invocation, Job, Memory, Task
from generic_agents.worker import process_once
from tests import test_full_semantic_graph_journey_v114 as journey
from tests.test_full_semantic_graph_journey_v114 import v7_scene, reading_scene, local_runtime, full_source_scene
from tests.test_full_semantic_instance_journey_v113 import configured, generate, create_object
from tests.test_instance_workspace import save
from tests.test_report_evidence_pipeline import reply
from tests.test_report_full_semantic_graph_binding_v3 import ref
from tests.test_audio import upload


@pytest.fixture
def audio_graph_seed(v7_scene, monkeypatch):
    original = journey.three_step_fixture

    def audio_template():
        proposal = original()
        # This is the unissued synthetic proposal consumed by graph_seed's real
        # source-review/issuance protocol, never a mutation of a saved ledger.
        step = proposal['design']['result']['businesses'][0]['steps'][1]
        step.update(agent='多模感知智能体', role='', name='本地原音频转写候选',
            purpose='按任务明确关联的原音频与设置运行本地ASR并保存机器候选',
            output='可追溯原音频的本地转写候选，仍需人工核对')
        node = next(n for n in proposal['graph']['nodes'] if n['id'] == 'b')
        node['execution']['agent_role'] = 'perception'
        return proposal

    # NativeHTTP in graph_seed temporarily replaces httpx.Client with a strict
    # provider-only transport. End that scope before the real audio installer
    # constructs its own cache-verifying HTTP client (and before actual graph IO).
    with monkeypatch.context() as source_phase:
        source_phase.setattr(journey, 'three_step_fixture', audio_template)
        seed = journey.graph_seed.__wrapped__(v7_scene, source_phase)
    return seed


def audio_command(seed, member, configuration, capability_id):
    command = journey.graph_command(seed, member, configuration, {'id': capability_id})
    graph = command['graph']; node = next(n for n in graph['nodes'] if n['id'] == 'b')
    with Session(engine()) as db:
        cap = db.get(Capability, capability_id)
        assert cap.status == 'published' and cap.data['execution']['adapter'] == 'local_audio'
        node['execution'] = {'capability_id': cap.id, 'capability_hash': cap.content_hash,
            'agent_role': 'perception', 'input_bindings': {'audio_ref': 'audio', 'task_id': 'task'},
            'arguments': {}, 'input_schema': copy.deepcopy(cap.data['input_schema']),
            'output_schema': copy.deepcopy(cap.data['output_schema']), 'adapter': 'local_audio',
            'release_version': cap.release_version, 'max_attempts': cap.data['budget']['max_attempts']}
        graph['input_schema'] = {'type': 'object', 'additionalProperties': False,
            'required': ['task_audio_ref', 'task_identifier'], 'properties': {
                'task_audio_ref': copy.deepcopy(cap.data['input_schema']['properties']['audio_ref']),
                'task_identifier': copy.deepcopy(cap.data['input_schema']['properties']['task_id'])}}
    graph['references'] = {
        'audio': {'kind': 'object', 'nullable': False, 'source': 'input', 'field': 'task_audio_ref', 'node_id': None},
        'task': {'kind': 'string', 'nullable': False, 'source': 'input', 'field': 'task_identifier', 'node_id': None},
        'approved': {'kind': 'boolean', 'nullable': False, 'source': 'output', 'field': 'ok', 'node_id': 'a'}}
    # The post-join decision is the parallel human's explicit approval, never a
    # guessed interpretation of an ASR string or the mere count of receipts.
    graph['predicates']['p_after'] = {'op': 'ref', 'key': 'approved'}
    replacements = {'records': [ref('value_ref', 'audio'), ref('value_ref', 'task')],
        'count': [ref('value_ref', 'approved')]}
    for owner in command['owner_bindings']:
        for clause in owner['clauses']:
            updated = []
            for target in clause['targets']:
                if target['kind'] == 'value_ref' and target['id'] in replacements:
                    updated.extend(copy.deepcopy(replacements[target['id']]))
                else:
                    updated.append(copy.deepcopy(target))
            clause['targets'] = updated
    covered = {target['id'] for owner in command['owner_bindings'] for clause in owner['clauses']
        for target in clause['targets'] if target['kind'] == 'value_ref'}
    assert covered == set(graph['references'])
    assert not any(target['kind'] == 'value_ref' and target['id'] in {'records', 'count'}
        for owner in command['owner_bindings'] for clause in owner['clauses'] for target in clause['targets'])
    command['node_actions'] = {'b': 'observe'}
    return command


def test_real_full_graph_public_audio_asr_and_human_review_are_durable(audio_graph_seed):
    seed = audio_graph_seed; client = seed['client']
    source = reply(upload(client), status=201)
    assert process_once(source['job_id'], deliver=False)
    source = reply(client.get('/api/audio/' + source['id']))
    assert source['status'] == 'succeeded', source.get('error')
    assert source['result']['transcription']['inference_performed'] is True
    registration = reply(client.post('/api/audio/' + source['id'] + '/register-business',
        json={'version': source['version']}))
    cap_id = registration['capability_id']

    view, member, compilation = configured(seed)
    name = seed['record']['design']['result']['businesses'][0]['name']
    configuration = copy.deepcopy(view['configuration'])
    configuration['automation']['policies'][name]['rules']['observe'] = {'mode': '自动运行'}
    save(client, configuration, review_automation=True)
    view = reply(client.get('/api/setup/draft'))
    command = audio_command(seed, member, view['configuration'], cap_id)
    path = '/api/setup/full-semantic-graphs/' + seed['record']['id']
    preview = reply(client.post(path + '/preview', json=command))
    adoption = {**command, 'schema': 'report-full-semantic-graph-command-v3',
        'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'], 'consent': True,
        'semantic_mode': 'workspace_workflow_design'}
    accepted = reply(client.post(path + '/adopt', json=adoption))
    assert reply(client.post(path + '/adopt', json=adoption))['receipt'] == accepted['receipt']
    current = reply(client.get('/api/setup/draft'))
    compilation.update(revision=current['revision'], role_bindings={'职责甲': member['id'], '职责乙': member['id']})
    checked = reply(client.post('/api/instances/check', json=compilation))
    assert checked['general_issues'] == [], checked['general_issues']
    release = generate(client, compilation, checked)
    assert len(release['available_business_ids']) == 1
    instance = next(i for i in reply(client.get('/api/instances')) if i['id'] == release['instance_id'])
    reply(client.post('/api/instances/' + instance['id'] + '/activate', json={
        'version': instance['version'], 'release_id': release['id'],
        'business_ids': release['available_business_ids'], 'confirm_workflow_scope': True}))
    base = '/api/workspace/instances/' + release['instance_id']
    candidate = next(c for c in reply(client.get(base + '/audio-candidates')) if c['source_run_id'] == source['id'])
    attach = {'source_run_id': source['id'], 'source_snapshot_hash': candidate['snapshot_hash'], 'request_id': str(uuid4())}
    audio = reply(client.post(base + '/input-audio', json=attach), status=201)
    assert reply(client.post(base + '/input-audio', json=attach), status=201) == audio
    obj, _ = create_object(client, release, release['snapshot']['object_types'][0])
    with Session(engine()) as db:
        original_entity = copy.deepcopy(db.get(Entity, obj['id']).data)
        cloud_before = set(db.scalars(select(CloudCall.id)))
    payload = {'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': [obj['id']], 'audio_ids': [audio['id']], 'title': '真实原音频图协同候选核对',
        'request_id': str(uuid4())}
    task = reply(client.post(base + '/tasks', json=payload), status=201)
    task_path = '/api/workspace/tasks/' + task['id']
    task = reply(client.get(task_path))
    assert [x['node_id'] for x in task['graph_pending_humans']] == ['a']
    assert len(task['graph_jobs']) == 1 and task['audio_snapshots'] == [audio]
    ability_id, transport = next(iter(task['graph_jobs'].items())); job_id = transport['job_id']
    expected = {'audio_ref': {'input_audio_id': audio['id'], 'snapshot_hash': audio['snapshot_hash']}, 'task_id': task['id']}
    assert task['graph_runtime']['activations'][ability_id]['inputs_snapshot']['arguments'] == expected
    with Session(engine()) as db:
        assert db.get(Job, job_id).data['inputs'] == expected
    forged = {'request_id': str(uuid4()), 'activation_id': ability_id, 'version': 0,
        'cancel_epoch': 0, 'output': {'audio_run_id': source['id'], 'audio_transcription': source['result'],
            'attributes': {}, 'anomalies': []}}
    assert client.post(task_path + '/graph/complete', json=forged).status_code == 409
    task, _ = journey.human(client, task, 'a', draft=True)
    assert process_once(job_id, deliver=False)
    task = reply(client.get(task_path))
    assert task['graph_jobs'][ability_id]['phase'] == 'succeeded', task['graph_jobs'][ability_id]
    assert [x['node_id'] for x in task['graph_pending_humans']] == ['a']
    assert task['graph_pending_humans'][0]['draft'] == {'ok': True}
    receipt = next(r for r in task['workflow_runtime']['receipts'] if r.get('activation_id') == ability_id)
    output = receipt['output']; run_id = output['audio_run_id']
    assert run_id != source['id'] and receipt['input_snapshot'] == expected
    assert receipt['candidate_only'] is True and receipt['entity_state_updated'] is False
    assert output['audio_transcription']['transcription']['inference_performed'] is True
    assert output['audio_transcription']['cloud_calls'] == 0
    original = client.get(task_path + '/audio/' + run_id + '/original')
    assert original.status_code == 200
    assert hashlib.sha256(original.content).hexdigest() == audio['snapshot']['input_sha256']
    exported = reply(client.get(task_path + '/audio/' + run_id + '/export'))
    assert exported['result'] == output['audio_transcription'] and exported['candidate_only'] is True
    task, first_body = journey.human(client, task, 'a')
    assert [x['node_id'] for x in task['graph_pending_humans']] == ['c']
    assert reply(client.post(task_path + '/graph/complete', json=first_body))['id'] == task['id']
    task, final_body = journey.human(client, task, 'c')
    assert task['completed'] and task['status'] == 'workflow_completed'
    assert reply(client.post(task_path + '/graph/complete', json=final_body))['completed']
    reopened = reply(client.get(task_path))
    assert reopened['graph_runtime'] == task['graph_runtime']
    assert process_once(job_id, deliver=False) is False
    with Session(engine()) as db:
        saved = db.get(Task, task['id']); job = db.get(Job, job_id)
        calls = db.scalars(select(Invocation).where(Invocation.capability_id == cap_id)).all()
        assert job.status == 'succeeded' and len(calls) == 1 and calls[0].status == 'succeeded'
        call = calls[0]; run = db.get(AudioRun, run_id)
        frozen = run.data['workflow_graph_scope']
        assert frozen['activation_id'] == ability_id and frozen['cancel_epoch'] == 0
        assert frozen['graph_hash'] == reopened['graph_runtime']['graph_hash']
        assert frozen['input_hash'] == digest(expected) == call.data['input_hash']
        assert run.data['workflow_job_id'] == job.id and frozen['invocation_key'] == call.idempotency_key
        assert call.id == receipt['invocation_id'] and call.data['job_id'] == job.id
        assert call.data['output'] == output and run.data['result'] == output['audio_transcription']
        assert output['attributes']['transcription_text_candidate'] == run.data['result']['transcription']['text']
        assert run.data['input_audio_id'] == audio['id'] and run.data['input_snapshot_hash'] == audio['snapshot_hash']
        assert saved.data['graph_runtime'] == reopened['graph_runtime']
        authorization = saved.data['graph_jobs'][ability_id]['dispatch_authorization']
        assert authorization['job_id'] == job.id and authorization['input_hash'] == digest(expected)
        assert authorization['lease_token'] == call.data['lease_token']
        memory = db.get(Memory, saved.data['workflow_runtime']['data_receipt_id'])
        assert memory.status == 'candidate' and memory.data['entity_state_updated'] is False
        assert memory.data['activations'][ability_id]['output'] == output
        assert db.get(Entity, obj['id']).data == original_entity
        assert set(db.scalars(select(CloudCall.id))) == cloud_before
