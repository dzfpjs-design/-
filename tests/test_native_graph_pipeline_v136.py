"""Actual public source registration + local ASR + native admission on owned PG.

Root alone runs this. No model/worker/gateway/permission/receipt is patched. The
public audio is the existing isolated workflow_audio fixture, not ASR quality or
physical coverage evidence. A graph's real open-window journey is separate.
"""
import copy
import json
from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.graph_event_models import GraphEventAdmission, GraphEventIntent, GraphEventProducer
from generic_agents.worker import deliver_events, process_once
from tests.test_native_audio_graph_v136 import configured, good
from tests.test_workflow_abilities import finish_job


def wire(response, status=200):
    envelope = good(response, status)
    assert set(envelope) == {'schema', 'canonical', 'value'}
    assert envelope['schema'] == 'report-full-semantic-graph-canonical-wire-v1'
    value = json.loads(envelope['canonical'])
    assert digest(value) == digest(envelope['value'])
    return value


def inventory(workspace):
    with Session(engine()) as db:
        return {model.__name__: list(db.scalars(select(model.id).where(model.workspace_id == workspace).order_by(model.id)))
            for model in (m.AudioRun, m.Invocation, m.CloudCall, GraphEventProducer, GraphEventIntent, GraphEventAdmission)}


def collect(client, base):
    preview = good(client.get(base + '/native-receipts'))
    saved = good(client.post(base + '/native-receipts/sync', json={'preview_hash': preview['preview_hash']}))
    assert saved['cloud_calls'] == 0
    deliver_events()
    return good(client.get(base + '/native-receipts'))


def prepared_native_source(client):
    original, capability, release, attached, creation = configured(client)
    base = '/api/workspace/instances/' + release['instance_id']
    native_base = base + '/graph-event-native-producers'
    first = finish_job(client, good(client.post(base + '/tasks', json=creation), 201))
    assert first['status'] == 'workflow_waiting'
    output = first['workflow_runtime']['receipts'][0]['output']
    assert output['audio_transcription']['transcription']['inference_performed'] is True
    assert output['audio_transcription']['cloud_calls'] == 0
    listing = collect(client, base)
    item = next(row for row in listing['records'] if row.get('record', {}).get('source_key') == 'AudioRun:' + output['audio_run_id'])
    assert item['data_received'] is True
    with Session(engine()) as db:
        observation = db.get(m.Observation, item['id'])
        source_id = observation.source_id; workspace = observation.workspace_id
        managed_source = db.get(m.Capability, source_id)
        original_source = (managed_source.name, managed_source.content_hash, digest(managed_source.data))
        instance_name = db.get(m.DomainInstance, release['instance_id']).data['name'].strip()
        run = db.get(m.AudioRun, output['audio_run_id'])
        assert observation.observed_at is None and not observation.data.get('invocation_id')
        assert 'extractor_id' not in observation.data
        assert not list(db.scalars(select(GraphEventIntent.id).where(GraphEventIntent.observation_id == observation.id)))
        # The installation pin comes from the actual frozen run, not a fake
        # extractor Capability or a client-constructed schema.
        from generic_agents.native_audio_provenance_v136 import checked_run
        from generic_agents.security import Principal
        me = good(client.get('/api/me'))
        context = checked_run(db, Principal(me['user_id'], workspace, me['role'], 'native-pipeline-history'), run.id)
        installation_id = context['installation']['installation_id']
    options = wire(client.get(native_base + '/options'))
    source = next(value for value in options['sources'] if value['id'] == source_id)
    installation = next(value for value in options['installations'] if value['id'] == installation_id)
    assert options['can_register'] and options['reason'] is None
    assert set(source) == {'id', 'name', 'content_hash', 'release_version'}
    assert source['name'] == instance_name[:80] + '的本地语音结果'
    assert source['name'] != original_source[0]
    assert all(value not in source['name'] for value in
        (release['instance_id'], release['instance_id'].replace('-', ''), 'instance_'))
    with Session(engine()) as db:
        unchanged = db.get(m.Capability, source_id)
        assert (unchanged.name, unchanged.content_hash, digest(unchanged.data)) == original_source
        assert source['content_hash'] == unchanged.content_hash
    assert '机器转写文字' not in json.dumps(options, ensure_ascii=False)
    command = {'schema': 'graph-event-native-audio-producer-command-v1', 'request_id': str(uuid4()),
        'label': '实际本地语音已保存候选', 'adapter_id': 'native-audio-transcription-v1',
        'instance_id': release['instance_id'], 'source_id': source['id'], 'source_hash': source['content_hash'],
        'installation_id': installation['id'], 'installation_hash': installation['installation_hash']}
    before = inventory(workspace)
    preview = wire(client.post(native_base + '/preview', json=command))
    assert preview['command'] == command and preview['execution_granted'] is False
    assert 'extractor_id' not in preview['contract']
    assert preview['contract']['output_schema']['properties']['observed_at'] == {'type': 'null'}
    assert inventory(workspace) == before  # A preview does not create source, Job or model work.
    saved = wire(client.post(native_base, json=command), 201)
    producer = saved['producer']; assert saved['created']
    assert producer['contract_hash'] == preview['contract_hash'] == digest(preview['contract'])
    assert producer['registration']['command_hash'] == preview['command_hash']
    replay = wire(client.post(native_base, json=command), 201)
    assert replay == {'producer': producer, 'created': False}
    assert wire(client.get(native_base + '/requests/' + command['request_id'])) == {'found': True, 'producer': producer}
    detail = wire(client.get(native_base + '/registrations/' + producer['id']))
    assert detail['current_availability']['available']
    listed = next(value for value in wire(client.get(native_base))['items'] if value['id'] == producer['id'])
    assert detail['source_name'] == listed['source_name'] == source['name']
    for value in (detail, listed):
        assert all(token not in value['source_name'] for token in
            (release['instance_id'], release['instance_id'].replace('-', ''), 'instance_'))
        assert value['contract'] == producer['contract']
        assert value['contract_hash'] == producer['contract_hash']
        assert value['registration'] == producer['registration']
    with Session(engine()) as db:
        unchanged = db.get(m.Capability, source_id)
        assert (unchanged.name, unchanged.content_hash, digest(unchanged.data)) == original_source
    # Registering now must not turn already-collected data into a new message.
    collect(client, base)
    with Session(engine()) as db:
        assert not list(db.scalars(select(GraphEventIntent.id).where(GraphEventIntent.observation_id == item['id'])))
    return {'base': base, 'native_base': native_base, 'release': release, 'workspace': workspace,
        'source': original, 'capability': capability, 'attached': attached, 'creation': creation,
        'first_task': first, 'old_observation_id': item['id'], 'command': command, 'producer': producer}


def run_native_producer_pipeline(client):
    scene = prepared_native_source(client)
    base, native_base = scene['base'], scene['native_base']; workspace = scene['workspace']
    creation = {**scene['creation'], 'request_id': str(uuid4()), 'title': '登记后真实本地音频业务'}
    task = finish_job(client, good(client.post(base + '/tasks', json=creation), 201))
    output = task['workflow_runtime']['receipts'][0]['output']; run_id = output['audio_run_id']
    assert output['audio_transcription']['transcription']['inference_performed'] is True
    assert run_id != scene['first_task']['workflow_runtime']['receipts'][0]['output']['audio_run_id']
    _mixed_native_record_cannot_skip_capture(client, scene, run_id)
    listing = collect(client, base)
    record = next(row for row in listing['records'] if row.get('record', {}).get('source_key') == 'AudioRun:' + run_id)
    assert record['data_received'] and record['record']['attributes']['已执行整片转写'] is True
    with Session(engine()) as db:
        observation = db.get(m.Observation, record['id'])
        intents = list(db.scalars(select(GraphEventIntent).where(GraphEventIntent.observation_id == observation.id)))
        assert len(intents) == 1
        intent_id = intents[0].id
        assert intents[0].producer_id == scene['producer']['id'] and intents[0].actor_id == observation.data['submitted_by']
        assert intents[0].data['projection']['schema'] == 'graph-event-native-audio-projection-v1'
        assert intents[0].data['projection']['proof']['receipt_actor_id'] == observation.data['submitted_by']
        assert intents[0].data['projection']['output']['observed_at'] is None
        job = db.scalar(select(m.Job).where(m.Job.idempotency_key == 'typed-graph-source:' + intent_id))
        assert job is not None; job_id = job.id
    before_admit = inventory(workspace)
    assert process_once(job_id)
    with Session(engine()) as db:
        assert db.get(m.Job, job_id).status == 'succeeded'
        row = db.get(GraphEventIntent, intent_id)
        assert row.status == 'admitted' and row.receipt_id is not None
        admission_id = row.receipt_id
    frozen = good(client.get('/api/setup/graph-event-producers/admissions/' + admission_id))
    assert frozen['projection']['output']['candidate_only'] is True
    assert frozen['projection']['output']['result'] == {'attributes': record['record']['attributes']}
    after_admit = inventory(workspace)
    for kind in ('AudioRun', 'Invocation', 'CloudCall', 'GraphEventIntent'):
        assert after_admit[kind] == before_admit[kind]
    assert not after_admit['CloudCall']
    stages_path = native_base + '/observations/' + record['id'] + '/stages'
    stages = wire(client.get(stages_path))
    assert stages['collected'] and stages['data_received'] and stages['data_status'] == 'received'
    assert stages['waiting_match'] == 'not_checked'  # Admission is not a wait-match assertion.
    assert stages['deliveries'][0]['admission']['id'] == admission_id
    collect(client, base)
    assert inventory(workspace) == after_admit
    revocation = {'schema': 'graph-event-native-audio-producer-revocation-v1', 'request_id': str(uuid4()),
        'instance_id': scene['release']['instance_id'], 'producer_id': scene['producer']['id'],
        'producer_version': scene['producer']['version'], 'contract_hash': scene['producer']['contract_hash'],
        'reason': '明确停止后续结果供应，保留原已保存回执'}
    stopped = wire(client.post(native_base + '/revocations', json=revocation))
    assert stopped['receipt']['result']['status'] == 'revoked'
    assert wire(client.post(native_base + '/revocations', json=revocation)) == {'receipt': stopped['receipt'], 'replayed': True}
    assert wire(client.get(native_base + '/revocations/requests/' + revocation['request_id'])) == {'found': True, 'receipt': stopped['receipt']}
    restored = wire(client.get(native_base + '/requests/' + scene['command']['request_id']))
    assert restored['producer']['status'] == 'revoked'
    assert restored['producer']['registration'] == scene['producer']['registration']
    assert good(client.get('/api/setup/graph-event-producers/admissions/' + admission_id)) == frozen
    _unknown_and_family_boundaries(client, scene)
    return {'schema': 'actual-native-source-pipeline-proof-v136', 'task_id': task['id'],
        'instance_id': scene['release']['instance_id'], 'run_id': run_id, 'observation_id': record['id'],
        'producer_id': scene['producer']['id'], 'intent_id': intent_id, 'admission_id': admission_id,
        'registration': {'path': native_base, 'body': scene['command'], 'producer': scene['producer']},
        'revocation': {'path': native_base + '/revocations', 'body': revocation, 'receipt': stopped['receipt']},
        'readbacks': [{'path': '/api/setup/graph-event-producers/admissions/' + admission_id, 'response': frozen},
            {'path': stages_path, 'response': wire(client.get(stages_path))}],
        'inventory': inventory(workspace), 'physical_state_verified': False, 'model_quality_verified': False}


def _mixed_native_record_cannot_skip_capture(client, scene, run_id):
    """Corrupt only the real record tag; original source pins still select it."""
    with Session(engine()) as db:
        original = copy.deepcopy(db.get(GraphEventProducer, scene['producer']['id']).data)
    before = inventory(scene['workspace'])
    try:
        for schema in ('graph-event-producer-record-v1', 'unknown-native-record-v999'):
            with Session(engine()) as db:
                producer = db.get(GraphEventProducer, scene['producer']['id'])
                producer.data = {**copy.deepcopy(original), 'schema': schema}; db.commit()
            preview = good(client.get(scene['base'] + '/native-receipts'))
            response = client.post(scene['base'] + '/native-receipts/sync', json={'preview_hash': preview['preview_hash']})
            assert response.status_code == 409, response.text[:400]
            assert inventory(scene['workspace']) == before
            with Session(engine()) as db:
                assert db.get(m.AudioRun, run_id).status == 'succeeded'
                assert not list(db.scalars(select(m.Observation.id).where(
                    m.Observation.data['native_packet']['source_key'].as_string() == 'AudioRun:' + run_id)))
    finally:
        # Restore the original issued row exactly, without changing its command,
        # source pins, nonce, audit, version or any machine/result authority.
        with Session(engine()) as db:
            producer = db.get(GraphEventProducer, scene['producer']['id'])
            producer.data = original; db.commit()


def _unknown_and_family_boundaries(client, scene):
    native_base = scene['native_base']; instance_id = scene['release']['instance_id']
    unknown = {**scene['command'], 'request_id': str(uuid4()), 'label': '未提交原登记的结束边界'}
    state_path = native_base + '/command-requests/register/' + unknown['request_id']
    assert wire(client.get(state_path))['outcome'] == 'not_found'
    retirement = {'schema': 'graph-event-native-audio-source-retirement-v1', 'request_id': str(uuid4()),
        'instance_id': instance_id, 'kind': 'register', 'original': unknown, 'reason': '原登记明确未保存，结束原申请'}
    ended = wire(client.post(native_base + '/command-retirements', json=retirement))
    assert ended['retirement']['result']['outcome'] == 'retired'
    assert wire(client.get(state_path))['retirement'] == ended['retirement']
    assert wire(client.get(native_base + '/command-retirements/requests/' + retirement['request_id'])) == {
        'found': True, 'retirement': ended['retirement']}
    assert client.post(native_base, json=unknown).status_code == 409
    assert wire(client.post(native_base + '/command-retirements', json=retirement)) == {'retirement': ended['retirement'], 'replayed': True}
    changed = copy.deepcopy(retirement); changed['original']['label'] = '迟到正文变化不能复用结束回执'
    assert client.post(native_base + '/command-retirements', json=changed).status_code == 409
    # Actual processing sources/capabilities/producer, not a fake old record.
    from tests.test_graph_event_pipeline import scene as processing_scene
    old = processing_scene(client); old_base = '/api/setup/graph-event-producers'
    old_command = old['commands'][0]
    assert client.post(native_base, json={**scene['command'], 'request_id': old_command['request_id']}).status_code == 409
    assert client.post(old_base, json={**old_command, 'request_id': scene['command']['request_id']}).status_code == 409
    assert client.get(old_base + '/requests/' + scene['command']['request_id']).status_code == 409
    assert client.get(native_base + '/requests/' + old_command['request_id']).status_code == 409
    assert client.post(old_base, json={**old_command, 'request_id': unknown['request_id']}).status_code == 409
    # A retirement in either protocol excludes a delayed mutation from the
    # other family, even before any Producer row exists for the original key.
    old_unknown = {**old_command, 'request_id': str(uuid4())}
    old_ended = {'schema': 'graph-event-source-retirement-v1', 'request_id': str(uuid4()),
        'kind': 'register', 'original': old_unknown, 'reason': '旧处理族原申请明确结束'}
    good(client.post(old_base + '/command-retirements', json=old_ended))
    assert client.post(native_base, json={**scene['command'], 'request_id': old_unknown['request_id']}).status_code == 409
    assert client.post(old_base + '/command-retirements', json={**old_ended, 'request_id': str(uuid4()), 'original': unknown}).status_code == 422
    assert client.post(native_base + '/command-retirements', json={**retirement,
        'request_id': str(uuid4()), 'original': old_command}).status_code == 422


def test_actual_native_registration_capture_admission_and_original_request_boundaries(client):
    run_native_producer_pipeline(client)


def test_processing_capture_does_not_silently_skip_a_mixed_record_tag(client):
    from tests.test_graph_event_pipeline import scene
    value = scene(client)
    with Session(engine()) as db:
        producer = db.get(GraphEventProducer, value['producers'][0]['id'])
        producer.data = {**producer.data, 'schema': 'graph-event-native-audio-producer-record-v1'}
        source_timezone = db.get(m.Capability, value['source']['id']).data['specification'].get('timezone', 'UTC')
        db.commit()
    at = m.now() - timedelta(seconds=1)
    original = {'source_id': value['source']['id'], 'source_record_id': uuid4().hex,
        'entity_id': value['object']['id'], 'observed_at': at.isoformat(),
        'business_day': str(at.astimezone(ZoneInfo(source_timezone)).date()),
        'attributes': {'temperature': 28}, 'extractor_id': value['extractor']['id'], 'startup_mode': 'manual'}
    observation = good(client.post('/api/observations', json=original), 201)
    with Session(engine()) as db:
        before = db.get(m.Observation, observation['id'])
        original_hash = before.data['input_hash']
        frozen_original = copy.deepcopy(before.data['original_request'])
        assert original_hash == digest(frozen_original)
        job_id = db.scalar(select(m.Job.id).where(m.Job.idempotency_key == 'extract:' + before.id))
        assert job_id is not None
    assert process_once(job_id)
    with Session(engine()) as db:
        assert db.get(m.Job, job_id).status == 'failed'
        row = db.get(m.Observation, observation['id'])
        assert row.status != 'processed'
        assert row.data['input_hash'] == original_hash == digest(row.data['original_request'])
        assert row.data['original_request'] == frozen_original
        assert row.data['original_request']['attributes'] == original['attributes']
        assert all(row.data['original_request'][key] == original[key]
            for key in ('source_id', 'source_record_id', 'entity_id', 'extractor_id'))
        assert not list(db.scalars(select(GraphEventIntent.id).where(GraphEventIntent.observation_id == row.id)))
