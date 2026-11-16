"""Manual presentation/readback contracts; no DB, network or process calls."""
import copy
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from fastapi import HTTPException
import pytest

from generic_agents.contracts import ObservationCreate, digest
from generic_agents import observation_integrity, reported_form as forms, workspace_api


@pytest.fixture(autouse=True)
def no_runtime(monkeypatch):
    import socket
    import subprocess
    from generic_agents import db
    def denied(*args, **kwargs):
        raise AssertionError('Real DB/network/process forbidden in presentation tests')
    monkeypatch.setattr(db, 'engine', denied)
    monkeypatch.setattr(socket, 'socket', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)


def manifest(properties=None, required=None, rules=None):
    properties = properties or {'mark': {'type': 'string', 'title': '交接标记', 'minLength': 1},
                                'received': {'type': 'boolean', 'title': '是否收到'}}
    return {'execution': {'adapter': 'builtin', 'handler': 'numeric_rules'},
            'data_policy': {'cloud_allowed': False}, 'specification': {'rules': rules or []},
            'input_schema': {'type': 'object', 'required': ['attributes'], 'properties': {
                'attributes': {'type': 'object', 'properties': properties, 'required': required or ['mark']}}}}


def test_original_string_boolean_and_optional_contract_is_not_coerced_or_mutated():
    source = manifest(); before = copy.deepcopy(source)
    value = forms.registration_form(source)
    assert value['registration_mode'] == 'passthrough'
    assert value['registration_fields'] == [
        {'key': 'mark', 'label': '交接标记', 'type': 'string', 'required': True, 'minLength': 1},
        {'key': 'received', 'label': '是否收到', 'type': 'boolean', 'required': False}]
    assert source == before


def test_legacy_numeric_rules_keep_numeric_fields_and_unit():
    source = manifest(rules=[{'field': 'temperature', 'operator': 'gt', 'threshold': 30, 'unit': '℃'}])
    source['input_schema'] = {'type': 'object'}
    result = forms.registration_form(source)
    assert result['registration_mode'] == 'threshold'
    assert result['registration_fields'] == [
        {'key': 'temperature', 'label': 'temperature', 'type': 'number', 'required': True, 'unit': '℃'}]


@pytest.mark.parametrize('mutate', [
    lambda x: x['execution'].update(side_effect=True),
    lambda x: x['data_policy'].update(cloud_allowed=True),
    lambda x: x.update(input_schema={'properties': []}),
    lambda x: x['input_schema'].update(required=['attributes', 'secret']),
    lambda x: x['input_schema']['properties']['attributes'].update(type='array'),
    lambda x: x['input_schema']['properties']['attributes'].update(anyOf=[{'type': 'object'}]),
    lambda x: x['input_schema']['properties']['attributes'].update(required=['missing']),
    lambda x: x['input_schema']['properties']['attributes']['properties']['mark'].update(type=['string', 'null']),
    lambda x: x['input_schema']['properties']['attributes']['properties']['mark'].update(type='object'),
])
def test_unsupported_or_corrupt_form_is_explicitly_blocked(mutate):
    source = manifest(); mutate(source)
    value = forms.registration_form(source)
    assert value['registration_mode'] == 'unsupported' and value['registration_fields'] == []
    assert value['registration_issue']


def history_data():
    first = {'request_id': 'original-request-001', 'reviewed_by': 'actor', 'decision': 'confirmed',
             'input_hash': 'a' * 64, 'body_hash': 'b' * 64, 'reason': '与原登记一致'}
    second = {**first, 'request_id': 'later-request-002', 'decision': 'rejected'}
    history = [first, second]
    return {'verification_history': history, 'verification_history_hash': digest(history),
            'human_verification': second}


def test_unknown_original_review_is_resolved_from_history_even_after_later_review():
    data = history_data(); before = copy.deepcopy(data)
    result = forms.review_request(data, 'original-request-001')
    assert result['found'] is True and result['review']['decision'] == 'confirmed'
    assert forms.review_request(data, 'not-present-request')['found'] is False
    result['review']['reason'] = 'external change'
    assert data == before


@pytest.mark.parametrize('mutate', [
    lambda x: x['verification_history'][0].update(reason='changed'),
    lambda x: x.update(human_verification={'request_id': 'forged-latest'}),
    lambda x: x.update(verification_history=[None]),
])
def test_corrupt_review_history_cannot_be_reported_as_saved(mutate):
    data = history_data(); mutate(data)
    with pytest.raises(HTTPException) as failure:
        forms.review_request(data, 'original-request-001')
    assert failure.value.status_code == 409


@pytest.mark.parametrize('seal', [None, '', 'not-a-seal', 42])
def test_recoverable_request_requires_a_valid_history_seal(seal):
    data = history_data(); data['verification_history_hash'] = seal
    with pytest.raises(HTTPException) as failure:
        forms.review_request(data, 'original-request-001')
    assert failure.value.status_code == 409


@pytest.mark.parametrize('mutate', [
    lambda x: x.pop('verification_history_hash'),
    lambda x: x.update(human_verification=[]),
    lambda x: x['verification_history'][0].update(request_id={}),
    lambda x: x['verification_history'][0].update(body_hash=None),
    lambda x: x['verification_history'][0].update(decision={}),
])
def test_missing_seal_or_malformed_receipts_fail_explicitly(mutate):
    data = history_data(); mutate(data)
    with pytest.raises(HTTPException) as failure:
        forms.review_request(data, 'original-request-001')
    assert failure.value.status_code == 409


def row_fixture():
    body = ObservationCreate(source_id='source', source_record_id='reported-original-key',
        entity_id='entity', business_day='2026-10-15', observed_at='2026-10-15T12:00:00+00:00',
        attributes={'mark': '  甲 原文  ', 'received': False}, extractor_id='extractor', startup_mode='manual')
    original = body.model_dump(exclude={'evidence_ids'})
    row = NS(id='observation', version=3, entity_id='entity', source_id='source',
        source_record_id=body.source_record_id, processing_version='1.0.0', status='processed',
        observed_at=datetime(2026, 10, 15, 12, tzinfo=timezone.utc), created_at=datetime.now(timezone.utc),
        data={'original_request': original, 'input_hash': digest(original), 'attributes': original['attributes'],
              'extractor_id': 'extractor', 'extracted': {'attributes': original['attributes'], 'anomalies': []},
              'reported_rule_contract': {'instance_id': 'instance'}, 'invocation_id': 'call'})
    return body, row


@pytest.mark.parametrize(('received', 'role', 'allowed'), [(True, 'operator', True),
    (False, 'operator', False), (True, 'viewer', False)])
def test_review_requires_actual_receipt_and_write_role(monkeypatch, received, role, allowed):
    _, row = row_fixture()
    monkeypatch.setattr(observation_integrity, 'checked_report', lambda *args: {'review': None})
    result = forms.observation_review_basis(None, NS(role=role), row, received=received,
        source=NS(data={'description': '人工来源'}), extractor=NS(data=manifest()))
    assert result['can_verify'] is allowed
    assert result['review_basis']['original_attributes']['mark'] == '  甲 原文  '
    assert result['review_basis']['original_attributes']['received'] is False
    assert result['review_basis']['physical_state_verified'] is False


def test_valid_extraction_with_corrupt_history_still_blocks_confirmation(monkeypatch):
    _, row = row_fixture(); row.data.update(history_data()); row.data['verification_history_hash'] = '0' * 64
    monkeypatch.setattr(observation_integrity, 'checked_report', lambda *args: {'review': None})
    result = forms.observation_review_basis(None, NS(role='operator'), row, received=True,
        source=NS(data={'description': '来源'}), extractor=NS(data=manifest()))
    assert result['can_verify'] is False and result['review_basis']['verification_input_hash'] is None
    assert result['review_basis']['integrity_issue']
    assert result['review_basis']['fields'] == []
    assert result['review_basis']['source_name'] == '原来源暂不可核对'
    assert result['review_basis']['extractor_name'] == '原登记方式暂不可核对'


def test_sealed_historical_review_is_not_current_confirmation(monkeypatch):
    _, row = row_fixture(); row.data.update(history_data())
    monkeypatch.setattr(observation_integrity, 'checked_report', lambda *args: {'review': None})
    result = forms.observation_review_basis(None, NS(role='operator'), row, received=True,
        source=NS(data={'description': '来源'}), extractor=NS(data=manifest()))
    assert result['review_basis']['verification'] is None
    assert result['review_basis']['historical_verification'] == row.data['human_verification']
    assert result['can_verify'] is True
    current_hash = result['review_basis']['verification_input_hash']
    row.data['human_verification']['input_hash'] = current_hash
    assert forms.current_verification(row.data, {'review': 'rejected'}) == row.data['human_verification']
    assert forms.current_verification(row.data, {'review': {}}) is None


class ReadOnlySession:
    def __init__(self, result): self.result = result; self.queries = []
    def scalar(self, query): self.queries.append(query); return self.result
    def __getattr__(self, name): raise AssertionError('Unexpected DB method: ' + name)


def test_registration_resolve_is_read_only_and_checks_original_full_body(monkeypatch):
    body, row = row_fixture(); db = ReadOnlySession(row); principal = NS(workspace_id='workspace')
    monkeypatch.setattr(workspace_api, 'scoped', lambda *args: NS(id='instance'))
    monkeypatch.setattr(forms, 'reported_record_view', lambda *args: {'id': row.id})
    result = workspace_api.resolve_reported_observation('instance', body, principal, db)
    assert result == {'found': True, 'request_matches': True, 'record': {'id': row.id}}
    changed = body.model_copy(update={'attributes': {'mark': 'changed'}})
    assert workspace_api.resolve_reported_observation('instance', changed, principal, db)['request_matches'] is False
    params = db.queries[0].compile().params
    assert set(params.values()) >= {'workspace', 'source', 'reported-original-key', '1.0.0'}


def test_registration_resolve_absence_is_unknown_and_other_instance_is_hidden(monkeypatch):
    body, row = row_fixture(); principal = NS(workspace_id='workspace')
    monkeypatch.setattr(workspace_api, 'scoped', lambda *args: NS(id='instance'))
    assert workspace_api.resolve_reported_observation('instance', body, principal, ReadOnlySession(None)) == {
        'found': False, 'request_matches': None, 'record': None}
    with pytest.raises(HTTPException) as failure:
        workspace_api.resolve_reported_observation('other-instance', body, principal, ReadOnlySession(row))
    assert failure.value.status_code == 404


def test_review_probe_checks_instance_and_actor_without_writing(monkeypatch):
    _, row = row_fixture(); row.data.update(history_data())
    monkeypatch.setattr(workspace_api, 'scoped', lambda db, model, *args: row if model.__name__ == 'Observation' else NS())
    monkeypatch.setattr(observation_integrity, 'checked_report', lambda *args: {'review': None})
    result = workspace_api.reported_review_request('instance', row.id, 'original-request-001',
        NS(user_id='other-reader'), ReadOnlySession(None))
    assert result['found'] is True and result['actor_matches'] is False
    with pytest.raises(HTTPException) as failure:
        workspace_api.reported_review_request('wrong-instance', row.id, 'original-request-001',
            NS(user_id='actor'), ReadOnlySession(None))
    assert failure.value.status_code == 404
