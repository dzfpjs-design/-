"""Cold contracts and synthetic ledgers; never DB/source/commit authority.

Actual transactions, source Job/Invocation proof, cursor concurrency and restart
recovery require Root's separate PostgreSQL integration. No checker is mocked
into accepting a contract: fixtures use B's real pure validators.
"""
import copy
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents.contracts import digest
from generic_agents import graph_event_admission as admission
from generic_agents import graph_event_observation_sources as sources
from generic_agents.graph_event_models import (
    GraphEventProducer, GraphEventIntent, GraphEventAdmission,
)
from tests.test_graph_event_observation_sources import (
    contract_fixture, projection_fixture, identity,
)


def rehash(value, key):
    value[key] = digest({name: child for name, child in value.items() if name != key})


def ledger(adapter='processed-observation-v1'):
    contract = contract_fixture(adapter)
    projection = projection_fixture(contract)
    scope = projection['proof']['workspace_id']
    p = NS(user_id=projection['proof']['processing_actor_id'], workspace_id=scope,
        role='operator', session_id='synthetic-admission-test')
    registrar = NS(user_id=identity(1001), workspace_id=scope)
    command = {'schema': 'graph-event-producer-command-v1', 'request_id': identity(1003),
        'label': '真实登记形状的合成来源𠀀\ue000', **sources.registration_fields(contract)}
    command_hash = admission.producer_command_hash(registrar, command)
    producer = NS(id=identity(1002), workspace_id=scope, registered_by=registrar.user_id,
        request_id=command['request_id'], command_hash=command_hash,
        contract_hash=digest(contract), status='active', version=1,
        data={'schema': 'graph-event-producer-record-v1', 'label': command['label'],
            'contract': copy.deepcopy(contract), 'contract_hash': digest(contract),
            'registration': {'request_id': command['request_id'], 'actor_id': registrar.user_id,
                'command_hash': command_hash, 'command': copy.deepcopy(command)}})
    intent = NS(id=identity(1004), workspace_id=scope, producer_id=producer.id,
        source_event_id=projection['source_event_id'], observation_id=projection['observation_id'],
        source_key=projection['source_key'], actor_id=p.user_id, status='admitted',
        receipt_id=identity(1005), version=2,
        data={'schema': 'graph-event-source-intent-v1', 'producer_id': producer.id,
            'contract_hash': producer.contract_hash, 'source_event_id': projection['source_event_id'],
            'observation_id': projection['observation_id'], 'source_key': projection['source_key'],
            'actor_id': p.user_id, 'projection': projection,
            'projection_hash': projection['projection_hash']})
    rehash(intent.data, 'intent_hash')
    stamp = datetime(2026, 10, 19, 18, 1, 2, 123456, tzinfo=timezone.utc)
    receipt = NS(id=intent.receipt_id, workspace_id=scope, producer_id=producer.id,
        intent_id=intent.id, source_key=intent.source_key, sequence=7, admitted_at=stamp,
        version=1, data={'schema': 'graph-event-admission-v1', 'id': intent.receipt_id,
            'workspace_id': scope, 'producer_id': producer.id, 'intent_id': intent.id,
            'source_key': intent.source_key, 'contract_hash': producer.contract_hash,
            'source_intent_hash': intent.data['intent_hash'], 'projection': copy.deepcopy(projection),
            'sequence': 7, 'admitted_at': stamp.isoformat(timespec='microseconds')})
    rehash(receipt.data, 'admission_hash')
    return p, producer, intent, receipt


def denied(operation):
    with pytest.raises(HTTPException) as failure:
        operation()
    assert failure.value.status_code in {403, 409}


@pytest.mark.parametrize('adapter', ['processed-observation-v1', 'anomaly-candidate-v1'])
def test_registered_contract_and_frozen_receipts_are_closed_detached_and_not_fact_certification(adapter):
    p, producer, intent, receipt = ledger(adapter)
    before = copy.deepcopy((producer, intent, receipt))
    contract = admission.check_producer(producer)
    assert producer.registered_by != p.user_id  # Registration and processing actors are distinct.
    assert producer.contract_hash == digest(contract) != contract['contract_hash']
    frozen = admission.check_intent(intent, producer)
    checked = admission.check_admission(receipt, intent, producer)
    contract['result_schema']['properties']['attributes']['properties']['count']['type'] = 'string'
    frozen['projection']['output']['result']['attributes']['count'] = 8
    checked['projection']['proof']['evidence'].append({'id': identity(2000), 'sha256': 'f' * 64})
    assert (producer, intent, receipt) == before
    assert receipt.data['projection']['output']['physical_state_verified'] is False
    assert receipt.data['projection']['output']['source_time_verified'] is False


@pytest.mark.parametrize('mutate', [
    lambda row: row.data.update(extra=True),
    lambda row: row.data['registration'].update(extra=True),
    lambda row: setattr(row, 'registered_by', identity(2101)),
    lambda row: setattr(row, 'request_id', identity(2102)),
    lambda row: setattr(row, 'command_hash', '0' * 64),
    lambda row: setattr(row, 'contract_hash', row.data['contract']['contract_hash']),
    lambda row: setattr(row, 'version', True),
    lambda row: setattr(row, 'status', 'enabled_by_test'),
    lambda row: row.data.update(label='另一个登记名称'),
    lambda row: row.data['registration']['command'].update(source_hash='f' * 64),
])
def test_producer_columns_command_scope_and_whole_contract_pin_cannot_diverge(mutate):
    _, producer, _, _ = ledger(); mutate(producer)
    denied(lambda: admission.check_producer(producer))


def test_resealed_registration_command_still_cannot_add_fields_or_change_frozen_source_pin():
    _, producer, _, _ = ledger()
    registration = producer.data['registration']; command = registration['command']
    command['source_hash'] = 'f' * 64
    actor = NS(user_id=producer.registered_by, workspace_id=producer.workspace_id)
    registration['command_hash'] = producer.command_hash = admission.producer_command_hash(actor, command)
    denied(lambda: admission.check_producer(producer))
    command['debug_extra'] = True
    denied(lambda: admission.producer_command_hash(actor, command))


@pytest.mark.parametrize('field,value', [
    ('request_id', 'not-a-uuid'), ('source_hash', 'A' * 64), ('label', ''),
    ('label', '   '), ('label', '\ud800'), ('source_id', True), ('extractor_hash', None),
])
def test_command_hash_rejects_invalid_closed_registration_values(field, value):
    _, producer, _, _ = ledger(); command = copy.deepcopy(producer.data['registration']['command'])
    command[field] = value
    denied(lambda: admission.producer_command_hash(
        NS(user_id=producer.registered_by, workspace_id=producer.workspace_id), command))


@pytest.mark.parametrize('target', ['workspace', 'actor', 'event'])
def test_resealed_projection_cannot_change_intent_workspace_processing_actor_or_original_event(target):
    _, producer, intent, _ = ledger(); projection = intent.data['projection']
    if target == 'workspace': projection['proof']['workspace_id'] = identity(2201)
    elif target == 'actor': projection['proof']['processing_actor_id'] = identity(2202)
    else: projection['source_event_id'] = identity(2203)
    rehash(projection, 'projection_hash'); intent.data['projection_hash'] = projection['projection_hash']
    rehash(intent.data, 'intent_hash')
    # B's valid nested hash is insufficient to replace A's row identity.
    assert sources.check_projection(projection, producer.data['contract']) == projection
    denied(lambda: admission.check_intent(intent, producer))


@pytest.mark.parametrize('mutate', [
    lambda row: setattr(row, 'source_key', 'observation:' + 'f' * 64),
    lambda row: setattr(row, 'producer_id', identity(2301)),
    lambda row: setattr(row, 'receipt_id', None),
    lambda row: setattr(row, 'status', 'pending'),
    lambda row: row.data.update(private_note='不应写入来源消息'),
    lambda row: row.data.update(projection_hash='0' * 64),
    lambda row: row.data.update(contract_hash='1' * 64),
])
def test_intent_identity_status_and_closed_payload_cannot_be_resealed_away(mutate):
    _, producer, intent, _ = ledger(); mutate(intent); rehash(intent.data, 'intent_hash')
    denied(lambda: admission.check_intent(intent, producer))


@pytest.mark.parametrize('field,value', [
    ('id', identity(2401)), ('workspace_id', identity(2402)), ('producer_id', identity(2403)),
    ('intent_id', identity(2404)), ('source_key', 'observation:' + 'a' * 64),
    ('sequence', True), ('sequence', 0), ('sequence', (1 << 63)),
    ('admitted_at', datetime(2026, 10, 19, 18, 1, 2)),
])
def test_receipt_actual_columns_and_timestamp_bind_the_frozen_receipt(field, value):
    _, producer, intent, receipt = ledger(); setattr(receipt, field, value)
    denied(lambda: admission.check_admission(receipt, intent, producer))


@pytest.mark.parametrize('field,value', [
    ('sequence', True), ('source_intent_hash', 'f' * 64), ('contract_hash', 'e' * 64),
    ('admitted_at', '2026-10-19T18:01:02.123456Z'), ('committed_at', 'not-admission'),
])
def test_resealed_receipt_body_cannot_replace_columns_intent_or_admission_time(field, value):
    _, producer, intent, receipt = ledger(); receipt.data[field] = value
    rehash(receipt.data, 'admission_hash')
    denied(lambda: admission.check_admission(receipt, intent, producer))


def test_resealed_new_source_proof_does_not_rewrite_an_existing_receipt():
    _, producer, intent, receipt = ledger()
    intent.data['projection']['proof']['original_request_hash'] = 'f' * 64
    rehash(intent.data['projection'], 'projection_hash')
    intent.data['projection_hash'] = intent.data['projection']['projection_hash']; rehash(intent.data, 'intent_hash')
    admission.check_intent(intent, producer)
    denied(lambda: admission.check_admission(receipt, intent, producer))


def test_database_timestamp_is_canonical_utc_microseconds_without_claiming_commit_time():
    _, producer, intent, receipt = ledger()
    receipt.admitted_at = receipt.admitted_at.astimezone(timezone(timedelta(hours=8)))
    assert admission.check_admission(receipt, intent, producer)['admitted_at'] == '2026-10-19T18:01:02.123456+00:00'
    assert 'committed_at' not in receipt.data


class ColdRows:
    """A row lookup double, deliberately incapable of source IO or any writes."""
    def __init__(self, producer, intent, receipt):
        self.rows = {GraphEventProducer: producer, GraphEventIntent: intent,
            GraphEventAdmission: receipt}
        self.reads = []

    def scalar(self, statement):
        model = statement.column_descriptions[0]['entity']
        assert model in self.rows, 'history must not access source/cursor/task/job'
        self.reads.append(model)
        return self.rows[model]

    def __getattr__(self, name):
        raise AssertionError('cold history must not write, commit, flush or call ' + name)


def test_revoked_producer_history_and_exact_admit_recovery_never_reproject_or_take_a_new_sequence(monkeypatch):
    p, producer, intent, receipt = ledger(); producer.status = 'revoked'; producer.version = 4; p.role = 'viewer'
    before = copy.deepcopy((producer, intent, receipt)); db = ColdRows(producer, intent, receipt)
    def forbidden(*args, **kwargs):
        raise AssertionError('historical recovery cannot require live source/cursor authorization')
    monkeypatch.setattr(sources, 'project_source', forbidden)
    monkeypatch.setattr(admission, '_stream', forbidden)
    frozen = admission.read_intent(db, p, intent.id)
    saved = admission.read_admission(db, p, receipt.id)
    row, created = admission.admit(db, p, intent.id)
    assert row is receipt and created is False
    frozen['projection']['output']['result']['attributes']['count'] = 55
    saved['sequence'] = 99
    assert (producer, intent, receipt) == before
    assert set(db.reads) == {GraphEventProducer, GraphEventIntent, GraphEventAdmission}


@pytest.mark.parametrize('change', ['actor', 'workspace'])
def test_historical_row_double_does_not_erase_caller_scope_checks(change):
    p, producer, intent, receipt = ledger(); setattr(p, 'user_id' if change == 'actor' else 'workspace_id', identity(2501))
    db = ColdRows(producer, intent, receipt)
    denied(lambda: admission.read_intent(db, p, intent.id))
