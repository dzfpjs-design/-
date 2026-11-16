"""Public pure/strict-IO origin boundaries, not PG, a device or a data ACK.

No full successful source/Goal/parent is fabricated. The real mapping/body,
numeric result, scoped SQL, independent ledger missing-row and lease boundary
functions are exercised. Root owns actual API/worker/PG acceptance separately.
"""
import copy
import socket
import subprocess
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from generic_agents import mapped_acquisition_origin_v177 as subject
from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from generic_agents.models import Audit, Capability, Job, Observation
from generic_agents.leases import LeaseLost, ExecutionUnauthorized
from tests.test_http_observation_exchange_v177 import fixture_row, packet, uid
from tests.test_report_saved_map_design_consumer_v176 import matches_statement, equal_statement


P = NS(user_id=uid(1), workspace_id=uid(2), role='operator')


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Origin pure tests cannot contact a device, DB or process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def rules_cap():
    return NS(namespace='synthetic_numeric', data={
        'execution': {'adapter': 'builtin', 'handler': 'numeric_rules', 'side_effect': False},
        'allowed_agent_roles': ['perception'],
        'data_policy': {'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False},
        'specification': {'rules': [
            {'id': 'hot', 'field': 'temperature', 'operator': 'gt', 'threshold': 30, 'unit': '°C'},
            {'id': 'cold', 'field': 'temperature', 'operator': 'lt', 'threshold': 10, 'unit': '°C'}]}})


def rule_shape(cap):
    return subject._rule_shape(cap, {'temperature'}, {'temperature': '°C'},
        {'package_id': uid(6), 'entity_type': 'synthetic_object'})


def test_closed_origin_is_a_detached_pointer_not_sample_reported_or_authority():
    origin = {'schema': subject.SCHEMA, 'exchange_id': uid(14)}
    result = subject._origin(origin)
    assert result == origin and result is not origin
    assert subject.PROCESS == 'process_http_mapped_observation_v177'
    assert subject.CHECKED_SCHEMA != subject.SCHEMA


@pytest.mark.parametrize('origin', [None, {}, {'schema': 'simulation-origin-v176', 'exchange_id': uid(14)},
    {'schema': subject.SCHEMA, 'exchange_id': True},
    {'schema': subject.SCHEMA, 'exchange_id': uid(14), 'sample_id': uid(15)}])
def test_thin_origin_never_coerces_or_borrows_another_family(origin):
    with pytest.raises((HTTPException, ValueError)): subject._origin(origin)


@pytest.mark.parametrize('fault', ['side_effect_int', 'extra_execution', 'cloud', 'remote', 'unit',
    'threshold_bool', 'threshold_large', 'field', 'operator', 'role', 'managed_scope'])
def test_numeric_extractor_shape_keeps_registered_local_unit_and_managed_scope_gates(fault):
    cap = rules_cap(); e = cap.data['execution']; rule = cap.data['specification']['rules'][0]
    if fault == 'side_effect_int': e['side_effect'] = 0
    elif fault == 'extra_execution': e['endpoint'] = 'https://example.invalid'
    elif fault == 'cloud': cap.data['data_policy']['cloud_allowed'] = True
    elif fault == 'remote': cap.data['data_policy']['deployment'] = 'remote'
    elif fault == 'unit': rule['unit'] = 'F'
    elif fault == 'threshold_bool': rule['threshold'] = True
    elif fault == 'threshold_large': rule['threshold'] = 10 ** 1000
    elif fault == 'field': rule['field'] = 'unmapped'
    elif fault == 'operator': rule['operator'] = 'gte'
    elif fault == 'role': cap.data['allowed_agent_roles'] = ['business']
    else: cap.data['specification']['managed_numeric_rule'] = {'package_id': uid(999), 'entity_type': 'synthetic_object'}
    with pytest.raises(HTTPException): rule_shape(cap)


def test_numeric_result_preserves_exact_attributes_threshold_equality_and_order():
    cap = rules_cap(); rules = rule_shape(cap)
    for value, matches in [(30, []), (10, []), (31, [{'field': 'temperature', 'value': 31,
            'rule_id': 'hot', 'severity': 'warning'}])]:
        attrs = {'temperature': value, 'retained_unruled': -0.0}
        result = {'attributes': copy.deepcopy(attrs), 'anomalies': matches, 'method': 'registered_numeric_rules'}
        before = copy.deepcopy((attrs, rules, result))
        assert subject._result(result, attrs, rules) is None
        assert (attrs, rules, result) == before
    duplicated = [rules[0], {**rules[0], 'id': 'second', 'severity': 'critical'}]
    attrs = {'temperature': 31}
    result = {'attributes': attrs, 'anomalies': [
        {'field': 'temperature', 'value': 31, 'rule_id': 'hot', 'severity': 'warning'},
        {'field': 'temperature', 'value': 31, 'rule_id': 'second', 'severity': 'critical'}],
        'method': 'registered_numeric_rules'}
    subject._result(result, attrs, duplicated)
    result['anomalies'].reverse()
    with pytest.raises(HTTPException): subject._result(result, attrs, duplicated)


def test_request_subset_does_not_invent_unrequested_fields_or_alerts():
    cap = rules_cap()
    cap.data['specification']['rules'].append({'id': 'humid', 'field': 'humidity',
        'operator': 'lt', 'threshold': 20, 'unit': '%'})
    rules = subject._rule_shape(cap, {'temperature', 'humidity'}, {'temperature': '°C', 'humidity': '%'},
        {'package_id': uid(6), 'entity_type': 'synthetic_object'})
    result = {'attributes': {'temperature': 30}, 'anomalies': [], 'method': 'registered_numeric_rules'}
    subject._result(result, {'temperature': 30}, rules)


@pytest.mark.parametrize('fault', ['attribute_type', 'anomaly_type', 'missing_alert', 'extra_fact', 'method', 'bad_value'])
def test_numeric_result_cannot_replace_raw_numbers_or_claim_physical_facts(fault):
    attrs = {'temperature': 31}; rules = rule_shape(rules_cap())
    result = {'attributes': copy.deepcopy(attrs), 'anomalies': [
        {'field': 'temperature', 'value': 31, 'rule_id': 'hot', 'severity': 'warning'}],
        'method': 'registered_numeric_rules'}
    if fault == 'attribute_type': result['attributes']['temperature'] = 31.0
    elif fault == 'anomaly_type': result['anomalies'][0]['value'] = 31.0
    elif fault == 'missing_alert': result['anomalies'] = []
    elif fault == 'extra_fact': result['field_verified'] = True
    elif fault == 'method': result['method'] = 'physical_measurement'
    else: attrs['temperature'] = True
    with pytest.raises(HTTPException): subject._result(result, attrs, rules)


def body_case():
    """A supplied protocol/DTO scene only; no independent saved source claim."""
    row = fixture_row(); frozen = row.data['freeze']
    raw = packet(row)
    projection = subject.mapping.from_supplier_observation(raw, frozen['mapping'],
        frozen['domain_frozen'], frozen['wire_frozen'])
    row.status = 'received'; row.observation_id = uid(40)
    row.data = {**row.data, 'projection': projection, 'receipt_history': [
        {'raw': raw, 'evidence_id': uid(41), 'sha256': subject.mapping.digest(raw)}]}
    body = subject.transport._body(row, uid(42)).model_dump(mode='json')
    assert body['evidence_ids'] == []
    original = {key: value for key, value in body.items() if key != 'evidence_ids'}
    row.data['observation_pin'] = {'observation_id': row.observation_id,
        'input_hash': subject.mapping.digest(original), 'original_request': copy.deepcopy(original),
        'projection_hash': subject.mapping.digest(projection)}
    obs = Observation(id=row.observation_id, workspace_id=row.workspace_id, source_id=body['source_id'],
        source_record_id=body['source_record_id'], processing_version=body['processing_version'],
        entity_id=body['entity_id'], observed_at=datetime.fromisoformat(body['observed_at']),
        business_day=body['business_day'], status='received', data={**copy.deepcopy(body),
            'original_request': original, 'input_hash': subject.mapping.digest(original),
            'submitted_by': row.actor_id, **subject.transport._labels(row)})
    return row, obs, body


def test_body_uses_empty_file_inputs_original_time_and_independent_parsed_receipt_label():
    row, obs, body = body_case(); original = copy.deepcopy(obs.data)
    assert subject._body(row, body['extractor_id'], obs) == body
    assert obs.data == original and obs.data['evidence_ids'] == []
    assert 'evidence_ids' not in obs.data['original_request']
    assert obs.data['http_mapped_receipt_evidence_id_v177'] == row.data['receipt_history'][-1]['evidence_id']
    assert obs.data['source_time_verified'] is False and obs.data['field_verified'] is False
    obs.observed_at = obs.observed_at.astimezone(timezone.utc)
    assert subject._body(row, body['extractor_id'], obs) == body


@pytest.mark.parametrize('fault', ['raw_type', 'clock', 'hash', 'file_input', 'receipt_pointer',
    'body_clock', 'sample', 'mqtt', 'reported', 'actor', 'unit', 'physical_flag'])
def test_body_refuses_cross_family_aliases_changed_clock_unit_actor_or_file_model_input(fault):
    row, obs, body = body_case()
    if fault == 'raw_type':
        key = next(iter(obs.data['attributes'])); obs.data['attributes'][key] = float(obs.data['attributes'][key])
    elif fault == 'clock': obs.observed_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    elif fault == 'hash': obs.data['input_hash'] = 'c' * 64
    elif fault == 'file_input': obs.data['evidence_ids'] = [row.data['receipt_history'][-1]['evidence_id']]
    elif fault == 'receipt_pointer': obs.data['http_mapped_receipt_evidence_id_v177'] = uid(999)
    elif fault == 'body_clock': obs.data['original_request']['observed_at'] = '2020-01-01T00:00:00+08:00'
    elif fault == 'sample': obs.data['simulation_origin'] = {'sample_id': uid(999)}
    elif fault == 'mqtt': obs.data['mqtt_receipt_id'] = uid(999)
    elif fault == 'reported': obs.data['reported_rule_contract'] = {}
    elif fault == 'actor': obs.data['submitted_by'] = uid(999)
    elif fault == 'unit': obs.data['field_units'] = {key: 'F' for key in obs.data['field_units']}
    else: obs.data['field_verified'] = True
    with pytest.raises(HTTPException): subject._body(row, body['extractor_id'], obs)


class OriginIO:
    """Exact query contracts; no permissive expression evaluator or live DB."""
    def __init__(self):
        self.p = P; self.no_autoflush = nullcontext(); self.log = []
        self.obs = Observation(id=uid(50), workspace_id=P.workspace_id, source_id=uid(51),
            source_record_id='ordinary-public', processing_version='ordinary-v1', data={})
        self.source = Capability(id=uid(51), workspace_id=P.workspace_id, kind='source',
            data={'specification': {}}, status='draft')
        self.source.content_hash = self.source.validated_hash = subject.mapping.digest(self.source.data)
        self.processes = []; self.reverse = []; self.anchors = []
    def scalars(self, statement):
        choices = [(select(Observation).where(Observation.workspace_id == P.workspace_id,
                Observation.id == self.obs.id), [self.obs]),
            (select(Capability).where(Capability.workspace_id == P.workspace_id, Capability.id == self.source.id), [self.source]),
            (select(Job).where(Job.workspace_id == P.workspace_id, Job.idempotency_key == 'extract:' + self.obs.id), self.processes),
            (select(Exchange).where(Exchange.workspace_id == P.workspace_id, Exchange.observation_id == self.obs.id), self.reverse),
            (select(Audit).where(Audit.workspace_id == P.workspace_id, Audit.action == subject.ledger.ACTION,
                Audit.data['state']['observation_id'].as_string() == self.obs.id), self.anchors)]
        for expected, values in choices:
            if matches_statement(statement, expected):
                equal_statement(statement, expected); self.log.append('scoped-read'); return list(values)
        raise AssertionError('Unexpected origin query')
    def execute(self, statement):
        exchange_id = self.obs.data['http_mapped_origin_v177']['exchange_id']
        inventory = select(func.array_agg(Audit.id)).where(Audit.workspace_id == P.workspace_id,
            Audit.action == subject.ledger.ACTION, Audit.object_id == exchange_id).scalar_subquery()
        expected = select(Exchange, inventory.label('captured_audit_ids')).where(
            Exchange.workspace_id == P.workspace_id, Exchange.id == exchange_id).execution_options(populate_existing=True)
        equal_statement(statement, expected); self.log.append('actual-ledger-missing-row')
        return NS(first=lambda: None)
    def add(self, *_): raise AssertionError('Origin checking cannot manufacture domain records')
    def commit(self): raise AssertionError('Read-only origin checking cannot commit')


def test_truly_unmarked_ordinary_observation_is_none_without_writes_or_author_requirement():
    db = OriginIO(); viewer = NS(user_id=uid(999), workspace_id=P.workspace_id, role='viewer')
    assert subject.guard(db, viewer, db.obs) is None and len(db.log) == 5


@pytest.mark.parametrize('trace', ['marker', 'receipt_label', 'source_record', 'processing_version',
    'source_mapping', 'source_extractor', 'process_kind', 'process_data', 'reverse_row', 'independent_audit'])
def test_any_new_family_trace_with_missing_marker_fails_closed_instead_of_falling_through(trace):
    db = OriginIO()
    if trace == 'marker': db.obs.data['http_mapped_origin_v177'] = None
    elif trace == 'receipt_label': db.obs.data['http_mapped_receipt_evidence_id_v177'] = uid(41)
    elif trace == 'source_record': db.obs.source_record_id = subject.PREFIX + uid(14)
    elif trace == 'processing_version': db.obs.processing_version = subject.PROCESSING_VERSION
    elif trace == 'source_mapping': db.source.data['specification'][subject.transport.MAPPING_KEY] = {}
    elif trace == 'source_extractor': db.source.data['specification'][subject.transport.EXTRACTOR_HASH] = 'a' * 64
    elif trace == 'process_kind': db.processes = [NS(kind=subject.PROCESS, data={})]
    elif trace == 'process_data': db.processes = [NS(kind='process_observation', data={'http_mapped_parent_job_id': uid(60)})]
    elif trace == 'reverse_row': db.reverse = [NS(id=uid(14), actor_id=P.user_id)]
    else: db.anchors = [NS(object_id=uid(14), actor_id=P.user_id)]
    with pytest.raises(HTTPException) as error: subject.guard(db, P, db.obs)
    assert error.value.status_code == 409 and db.log == ['scoped-read'] * 5


def test_marker_and_reverse_hints_cannot_substitute_for_actual_independent_ledger():
    db = OriginIO(); db.obs.data['http_mapped_origin_v177'] = {'schema': subject.SCHEMA, 'exchange_id': uid(14)}
    db.reverse = [NS(id=uid(14), actor_id=P.user_id)]
    db.anchors = [NS(object_id=uid(14), actor_id=P.user_id)]
    with pytest.raises(HTTPException) as error: subject.guard(db, P, db.obs)
    assert error.value.status_code == 409 and db.log[-1] == 'actual-ledger-missing-row'


def test_historical_cap_pin_allows_withdrawn_but_execution_requires_current_publication():
    db = OriginIO(); viewer = NS(user_id=uid(999), workspace_id=P.workspace_id, role='viewer')
    cap = subject._cap(db, viewer, db.source.id, db.source.content_hash, 'source', execution=False)
    assert cap is db.source
    with pytest.raises(HTTPException):
        subject._cap(db, P, db.source.id, db.source.content_hash, 'source', execution=True)
    db.source.status = 'published'
    assert subject._cap(db, P, db.source.id, db.source.content_hash, 'source', execution=True) is db.source
    db.source.data['changed'] = True
    with pytest.raises(HTTPException):
        subject._cap(db, viewer, db.source.id, db.source.content_hash, 'source', execution=False)


def lease_case():
    process = Job(id=uid(70), workspace_id=P.workspace_id, status='running',
        lease_token=uid(71), data={'actor_id': P.user_id})
    db = NS(info={'worker_lease': {'job_id': process.id, 'lease_token': process.lease_token}}, commits=0)
    def commit(): db.commits += 1
    db.commit = commit
    return db, process


@pytest.mark.parametrize('fault', ['actor', 'job', 'lease', 'empty_token', 'status'])
def test_execution_requires_exact_child_and_original_actual_actor_before_checkpoint(fault):
    db, process = lease_case(); p = P
    if fault == 'actor': p = NS(user_id=uid(999), workspace_id=P.workspace_id)
    elif fault == 'job': db.info['worker_lease']['job_id'] = uid(999)
    elif fault == 'lease': db.info['worker_lease']['lease_token'] = uid(999)
    elif fault == 'empty_token': process.lease_token = ''
    else: process.status = 'queued'
    with pytest.raises(HTTPException): subject._execution(db, p, process, P.user_id)
    assert db.commits == 0


@pytest.mark.parametrize('error', [LeaseLost('lost'), ExecutionUnauthorized('revoked'),
    OperationalError('SYNTHETIC SQL', {}, RuntimeError('synthetic DB error'))])
def test_execution_checkpoint_does_not_swallow_lease_iam_or_database_failure(error):
    db, process = lease_case()
    def commit(): raise error
    db.commit = commit
    with pytest.raises(type(error)) as caught: subject._execution(db, P, process, P.user_id)
    assert caught.value is error
