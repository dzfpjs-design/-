"""Real learned-source registration and original-request recovery on owned PG.

Root executes the real public install/resource/business model journey. Neither
installation, source contracts, model results nor authority checks are mocked.
"""
import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.graph_event_models import GraphEventProducer, GraphEventIntent, GraphEventAdmission
from generic_agents.worker import deliver_events
from tests.test_native_learned_geo_journey_v138 import actual_learned_journey, rasters, pytestmark


def good(response, status=200):
    assert response.status_code == status, response.text[:900]
    return response.json()


def wire(response, status=200):
    envelope = good(response, status)
    assert set(envelope) == {'schema', 'canonical', 'value'}
    assert envelope['schema'] == 'report-full-semantic-graph-canonical-wire-v1'
    value = json.loads(envelope['canonical'])
    assert digest(value) == digest(envelope['value'])
    return value


def inventory():
    with Session(engine()) as db:
        return {model.__name__: set(db.scalars(select(model.id))) for model in
            (m.GeoPair, m.Invocation, m.CloudCall, m.Task, m.Entity, GraphEventProducer, GraphEventIntent, GraphEventAdmission)}


def registered_learned_scene(client, rasters):
    value = actual_learned_journey(client, rasters)
    base = value['base'] + '/graph-event-native-learned-geo-producers'
    with Session(engine()) as db:
        original = db.get(m.Observation, value['observation']['id'])
        source = db.get(m.Capability, original.source_id)
        pair = db.get(m.GeoPair, value['pair_id'])
        original_source = (source.name, source.content_hash, digest(source.data))
        source_id, capability_id = source.id, pair.data['workflow_capability_id']
    options = wire(client.get(base + '/options'))
    assert options['schema'] == 'graph-event-native-learned-geo-producer-options-v1'
    assert options['instance_id'] == value['release']['instance_id'] and options['can_register']
    source = next(row for row in options['sources'] if row['id'] == source_id)
    cap = next(row for row in options['capabilities'] if row['id'] == capability_id)
    assert source['name'] != original_source[0] and 'instance_' not in source['name']
    body = {'schema': 'graph-event-native-learned-geo-producer-command-v1', 'request_id': str(uuid4()),
        'label': '本地学习型变化检测的已接收候选', 'adapter_id': 'native-learned-binary-change-v1',
        'instance_id': value['release']['instance_id'], 'source_id': source['id'], 'source_hash': source['content_hash'],
        'capability_id': cap['id'], 'capability_hash': cap['content_hash']}
    before = inventory()
    preview = wire(client.post(base + '/preview', json=body))
    assert preview['command'] == body and preview['execution_granted'] is False
    assert preview['contract']['capability_id'] == capability_id
    assert 'extractor_id' not in preview['contract'] and inventory() == before
    registered = wire(client.post(base, json=body), 201)
    producer = registered['producer']
    assert registered['created'] and producer['contract_hash'] == preview['contract_hash'] == digest(preview['contract'])
    assert producer['registration']['command_hash'] == preview['command_hash']
    with Session(engine()) as db:
        actual_source = db.get(m.Capability, source_id)
        assert (actual_source.name, actual_source.content_hash, digest(actual_source.data)) == original_source
    return {**value, 'producer_base': base, 'producer': producer, 'registration': body, 'options': options}


def test_real_learned_registration_recovery_retirement_and_no_historical_backfill(client, rasters):
    value = registered_learned_scene(client, rasters)
    base, command, producer = value['producer_base'], value['registration'], value['producer']
    stable = inventory()
    assert wire(client.post(base, json=command), 201) == {'producer': producer, 'created': False}
    assert client.post(base, json={**command, 'label': '另一份正文不能借原编号'}).status_code == 409
    assert wire(client.get(base + '/requests/' + command['request_id'])) == {'found': True, 'producer': producer}
    assert wire(client.get(base + '/registrations/' + producer['id']))['capability_name'] == '本地学习型变化检测'
    assert wire(client.get(base))['items'][0]['id'] == producer['id']
    audio = value['base'] + '/graph-event-native-producers'
    # Valid Audio command syntax, deliberately never submitted as a new source.
    # Retiring an unsent request checks its exact DTO, not a model installation;
    # all POST attempts below must stop at the shared original nonce inventory.
    def audio_original(nonce):
        return {'schema': 'graph-event-native-audio-producer-command-v1', 'request_id': nonce,
            'label': '未提交的本地语音来源申请', 'adapter_id': 'native-audio-transcription-v1',
            'instance_id': command['instance_id'], 'source_id': command['source_id'],
            'source_hash': command['source_hash'], 'installation_id': str(uuid4()),
            'installation_hash': digest('synthetic unsent Audio installation pin; no installed model claimed')}

    def request_inventory():
        from generic_agents.graph_event_native_producer_requests_v136 import GraphEventNativeProducerCommand
        with Session(engine()) as db:
            commands = {row.id: (row.version, row.command_hash, row.original_hash, digest(row.data))
                for row in db.scalars(select(GraphEventNativeProducerCommand))}
            return commands, set(db.scalars(select(m.Audit.id)))

    before_conflict = request_inventory()
    assert client.post(audio, json=audio_original(command['request_id'])).status_code == 409
    assert request_inventory() == before_conflict and inventory() == stable
    assert wire(client.get(audio))['items'] == []  # A learned row cannot break the Audio page.
    assert client.get(audio + '/requests/' + command['request_id']).status_code == 409
    assert client.get('/api/setup/graph-event-producers/requests/' + command['request_id']).status_code == 409
    saved = good(client.get(value['base'] + '/native-receipts'))
    repeated = good(client.post(value['base'] + '/native-receipts/sync', json={'preview_hash': saved['preview_hash']}))
    assert repeated['created'] == 0 and repeated['already_saved'] == 1
    deliver_events()
    assert inventory() == stable
    assert not stable['GraphEventIntent'] and not stable['GraphEventAdmission']
    stages = wire(client.get(base + '/observations/' + value['observation']['id'] + '/stages'))
    assert stages['data_received'] and stages['deliveries'] == [] and stages['waiting_match'] == 'not_checked'

    never_sent = {**command, 'request_id': str(uuid4()), 'label': '明确不再提交的原登记'}
    retirement = {'schema': 'graph-event-native-learned-geo-source-retirement-v1',
        'request_id': str(uuid4()), 'instance_id': command['instance_id'], 'kind': 'register',
        'original': never_sent, 'reason': '原操作未提交，明确安全结束并保留原内容'}
    retired = wire(client.post(base + '/command-retirements', json=retirement))
    assert retired['retirement']['command'] == retirement
    assert wire(client.post(base + '/command-retirements', json=retirement)) == {
        'retirement': retired['retirement'], 'replayed': True}
    assert wire(client.get(base + '/command-retirements/requests/' + retirement['request_id'])) == {
        'found': True, 'retirement': retired['retirement']}
    state = wire(client.get(base + '/command-requests/register/' + never_sent['request_id']))
    assert state['outcome'] == 'retired' and state['saved'] is None
    assert client.post(base, json=never_sent).status_code == 409
    assert client.get(audio + '/command-requests/register/' + never_sent['request_id']).status_code == 409
    before_conflict = request_inventory()
    audio_borrowed = audio_original(never_sent['request_id'])
    assert client.post(audio, json=audio_borrowed).status_code == 409
    audio_borrowed_retirement = {'schema': 'graph-event-native-audio-source-retirement-v1',
        'request_id': str(uuid4()), 'instance_id': command['instance_id'], 'kind': 'register',
        'original': audio_borrowed, 'reason': '不能借用另一协议已结束的原操作编号'}
    assert client.post(audio + '/command-retirements', json=audio_borrowed_retirement).status_code == 409
    assert request_inventory() == before_conflict and inventory() == stable

    # A real Audio retirement of a valid, never-sent command requires no fake
    # Audio Producer or installation. The stored SQL receipt excludes learned
    # mutations of that same original nonce in the opposite direction as well.
    audio_unsent = audio_original(str(uuid4()))
    audio_retirement = {**audio_borrowed_retirement, 'request_id': str(uuid4()), 'original': audio_unsent,
        'reason': '原语音登记未提交，明确结束这份原申请'}
    audio_ended = wire(client.post(audio + '/command-retirements', json=audio_retirement))
    assert audio_ended['retirement']['command'] == audio_retirement
    assert wire(client.get(audio + '/command-retirements/requests/' + audio_retirement['request_id'])) == {
        'found': True, 'retirement': audio_ended['retirement']}
    before_conflict = request_inventory()
    learned_borrowed = {**never_sent, 'request_id': audio_unsent['request_id']}
    assert client.post(base, json=learned_borrowed).status_code == 409
    assert client.post(base + '/command-retirements', json={**retirement,
        'request_id': str(uuid4()), 'original': learned_borrowed}).status_code == 409
    assert request_inventory() == before_conflict and inventory() == stable
    assert wire(client.get(base + '/requests/' + command['request_id'])) == {'found': True, 'producer': producer}
    assert wire(client.get(base + '/command-retirements/requests/' + retirement['request_id'])) == {
        'found': True, 'retirement': retired['retirement']}
    assert wire(client.get(audio))['items'] == []
    assert inventory() == stable
