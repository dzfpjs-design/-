"""Closed union/pin checks only; no synthetic record is origin authority."""
import copy
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import workflow_goal_evaluation_contracts_v288 as subject
from generic_agents import goal_ack_origin_mqtt_v297 as origin
from generic_agents.models import Acquisition, Audit, MqttReceipt, MqttReceiptDelivery, MqttBindingRevision, Evidence
from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from tests.test_workflow_goal_evaluation_contracts_v288 import scene, uid, CUT


def mqtt_record():
    _policy, record, kwargs, _original = scene(acquisition=False)
    ack = record['ack_refs'][0]
    del ack['acquisition_id']; del ack['acquisition_hash']
    ack.update(origin_kind=origin.KIND, origin_task_id=None, simulation_only=False, test_only=False)
    scope = record['scope']
    ack['mqtt_origin'] = {'schema': origin.SCHEMA, 'receipt_id': uid(100), 'gateway_id': uid(101),
        'packet_key': 'a' * 64, 'source_record_id': 'mqtt:' + 'b' * 64, 'input_sha256': 'c' * 64,
        'packet_evidence_id': uid(102), 'packet_evidence_sha256': 'c' * 64, 'packet_size_bytes': 120,
        'config_hash': 'd' * 64, 'gateway_version': 2, 'topic': 'weather/station_one',
        'received_at': ack['received_at'], 'receipt_created_at': ack['received_at'], 'decoded_hash': 'e' * 64,
        'binding_id': uid(103), 'binding_revision': 1, 'binding_hash': 'f' * 64,
        'instance_id': scope['instance_id'], 'release_id': scope['release_id'], 'package_id': scope['package_id'],
        'entity_id': ack['entity_id'], 'field_links_hash': '1' * 64, 'field_units_hash': '2' * 64,
        'delivery_id': uid(104), 'delivery_target_hash': '3' * 64, 'route_id': uid(105), 'route_version': 1,
        'authorization_id': uid(106), 'authorization_hash': '4' * 64,
        'saved_audit_id': uid(107), 'saved_audit_hash': '5' * 64,
        'submitted_audit_id': uid(108), 'submitted_audit_hash': '6' * 64, 'test_origin': None}
    return record, kwargs


def check(record, kwargs):
    record['record_hash'] = subject.record_hash(record)
    return subject.check_record(record, **kwargs)


def test_old_acquisition_ack_roundtrips_exact_bytes_and_hash_without_new_defaults():
    _policy, record, kwargs, _original = scene()
    original = copy.deepcopy(record)
    assert subject.check_record(record, **kwargs) == original
    assert record['record_hash'] == subject.record_hash(original)
    assert 'origin_kind' not in record['ack_refs'][0] and 'mqtt_origin' not in record['ack_refs'][0]


def test_passive_mqtt_ack_has_no_acquisition_or_fabricated_origin_task():
    record, kwargs = mqtt_record(); value = check(record, kwargs)
    ack = value['ack_refs'][0]
    assert ack['origin_task_id'] is None and ack['query_admitted'] is True
    assert not {'acquisition_id', 'acquisition_hash'} & set(ack)
    assert ack['simulation_only'] is False and ack['test_only'] is False
    pins = origin.immutable_pins(ack); pins['topic'] = 'local unsaved mutation'
    assert ack['mqtt_origin']['topic'] == 'weather/station_one'
    locks = set(origin.lock_rows(ack))
    assert {(MqttReceipt, uid(100)), (MqttReceiptDelivery, uid(104)), (MqttBindingRevision, uid(103)),
        (Evidence, uid(102)), (Audit, uid(106)), (Audit, uid(107)), (Audit, uid(108))} <= locks


@pytest.mark.parametrize('fault', ['mixed_acquisition', 'unknown_family', 'invented_task', 'excluded',
    'wrong_release', 'wrong_packet_hash', 'unproven_test', 'unlabelled_test'])
def test_mqtt_ack_cannot_borrow_acquisition_or_scope_or_undeclared_test_authority(fault):
    record, kwargs = mqtt_record(); ack = record['ack_refs'][0]; pins = ack['mqtt_origin']
    if fault == 'mixed_acquisition': ack.update(acquisition_id=uid(39), acquisition_hash='a' * 64)
    if fault == 'unknown_family': ack['origin_kind'] = 'mqtt_callback_from_client'
    if fault == 'invented_task': ack['origin_task_id'] = record['scope']['task_id']
    if fault == 'excluded': ack['query_admitted'] = False
    if fault == 'wrong_release': pins['release_id'] = uid(999)
    if fault == 'wrong_packet_hash': pins['packet_evidence_sha256'] = '0' * 64
    if fault == 'unproven_test': ack['simulation_only'] = ack['test_only'] = True
    if fault == 'unlabelled_test': pins['test_origin'] = {'audit_id': uid(109), 'declaration_hash': '7' * 64}
    with pytest.raises(subject.GoalEvaluationContractError): check(record, kwargs)


def test_declared_test_origin_is_pinned_and_locked_without_granting_physical_truth():
    record, kwargs = mqtt_record(); ack = record['ack_refs'][0]
    ack['mqtt_origin']['test_origin'] = {'audit_id': uid(109), 'declaration_hash': '7' * 64}
    ack['simulation_only'] = ack['test_only'] = True
    value = check(record, kwargs)
    assert (Audit, uid(109)) in origin.lock_rows(value['ack_refs'][0])
    assert all(value[key] is False for key in subject.FLAGS)


def test_original_cut_accepts_actual_aware_datetime_but_never_naive_time():
    assert origin._time(datetime.fromisoformat(CUT)) == origin._time(CUT)
    with pytest.raises(HTTPException): origin._time(datetime(2026, 11, 1, 12, 0))
    assert origin._time(CUT).tzinfo == timezone.utc


@pytest.mark.parametrize('witness', [Acquisition, Exchange])
def test_actual_reverse_inventory_rejects_acquisition_even_after_forward_marker_removal(monkeypatch, witness):
    # Only this early rejection boundary runs. IAM and row storage are explicit
    # spies; the real reverse SELECT/model/workspace/Obs/limit are inspected.
    from generic_agents import goal_evaluation_query_v288
    p = SimpleNamespace(workspace_id=uid(1))
    obs = SimpleNamespace(id=uid(40), workspace_id=p.workspace_id, entity_id=uid(6), status='processed', data={})
    task = SimpleNamespace(workspace_id=p.workspace_id, data={'entity_ids': [obs.entity_id]})
    queries = []
    class DB:
        no_autoflush = nullcontext()
        def scalars(self, statement):
            model = statement.column_descriptions[0]['entity']
            parameters = statement.compile().params
            assert model in {Acquisition, Exchange}
            assert p.workspace_id in parameters.values() and obs.id in parameters.values()
            assert 2 in parameters.values()
            queries.append(model)
            return [SimpleNamespace(id=uid(999))] if model is witness else []
    monkeypatch.setattr(goal_evaluation_query_v288, '_reader', lambda *_args: None)
    monkeypatch.setattr(origin, '_row', lambda *_args: obs)
    with pytest.raises(HTTPException): origin.checked_origin(DB(), p, obs, task=task, cut=CUT)
    assert queries == ([Acquisition] if witness is Acquisition else [Acquisition, Exchange])


def test_fixed_reverse_delivery_detection_does_not_turn_missing_marker_into_legacy(monkeypatch):
    p = SimpleNamespace(workspace_id=uid(1))
    obs = SimpleNamespace(id=uid(40), workspace_id=p.workspace_id, source_id=uid(41), data={})
    source = SimpleNamespace(data={'execution': {'adapter': 'http'}, 'specification': {}})
    monkeypatch.setattr(origin, '_row', lambda *_args: source)
    class DB:
        def scalars(self, statement):
            assert statement.column_descriptions[0]['entity'] is MqttReceiptDelivery
            assert obs.id in statement.compile().params.values()
            return [SimpleNamespace(id=uid(104))]
    assert origin.handles(DB(), p, obs) is True  # A routing witness, never a grant.
