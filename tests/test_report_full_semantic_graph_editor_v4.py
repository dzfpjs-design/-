"""Source-choice unit boundaries plus an actual issued v4 context read.

Unit rows below are explicit database doubles, not new source or graph grants.
The final journey reads real synthetic registrations and v4 acceptance records.
"""
import copy
import json
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest

from generic_agents.api import app
from generic_agents import report_full_semantic_graph_editor_v4 as editor
from generic_agents import graph_event_api
from generic_agents.contracts import digest
from generic_agents.graph_event_admission import producer_command_hash
from generic_agents.graph_event_models import GraphEventProducer
from generic_agents.models import Capability, User
from tests.test_graph_event_observation_sources import contract_fixture


FLOAT_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['reading'],
    'properties': {'reading': {'type': 'number', 'minimum': -0.0, 'maximum': 1.0, 'multipleOf': 1e-7}}}


def _base_context(identity, active=True):
    return {'schema': editor.old.SCHEMA, 'handoff_id': identity, 'active': active,
        'design': {'handoff_id': identity}, 'manifest': {}, 'configuration': {'revision': 4, 'hash': 'a' * 64},
        'owners': [], 'proofs': [], 'schema_origins': {'objects': [], 'relationships': []},
        'adoptions': [], 'event_wait': {'available': False, 'reason': editor.old.UNAVAILABLE}}


def _producer(p, label):
    source_id, extractor_id = str(uuid4()), str(uuid4())
    source_data = {'kind': 'source', 'version': '1.2.0', 'credentials_ref': 'NEVER_SOURCE_SECRET'}
    extractor_data = {'kind': 'tool', 'version': '3.0.1', 'allowed_agent_roles': ['perception'],
        'output_schema': copy.deepcopy(FLOAT_SCHEMA), 'secret_ref': 'NEVER_EXTRACTOR_SECRET'}
    caps = {}
    for identity, data in ((source_id, source_data), (extractor_id, extractor_data)):
        caps[identity] = NS(id=identity, workspace_id=p.workspace_id, data=data, kind=data['kind'],
            release_version=data['version'], status='published', content_hash=digest(data), validated_hash=digest(data))
    contract = contract_fixture(result_schema=FLOAT_SCHEMA)
    contract.update(source_id=source_id, extractor_id=extractor_id,
        source_hash=caps[source_id].content_hash, extractor_hash=caps[extractor_id].content_hash)
    contract['contract_hash'] = digest({key: value for key, value in contract.items() if key != 'contract_hash'})
    request_id = str(uuid4())
    command = {'schema': 'graph-event-producer-command-v1', 'request_id': request_id, 'label': label,
        **editor.observation_sources.registration_fields(contract)}
    signature = producer_command_hash(p, command)
    row = NS(id=str(uuid4()), workspace_id=p.workspace_id, registered_by=p.user_id, request_id=request_id,
        command_hash=signature, contract_hash=digest(contract), status='active', version=1,
        data={'schema': 'graph-event-producer-record-v1', 'label': label, 'contract': contract,
            'contract_hash': digest(contract), 'registration': {'request_id': request_id,
                'actor_id': p.user_id, 'command_hash': signature, 'command': command}})
    return row, caps


class ChoiceDB:
    def __init__(self, p, rows, caps):
        self.p, self.rows, self.caps = p, rows, caps
        self.actor_allowed = True; self.calls = []

    def scalars(self, query):
        assert query.column_descriptions[0]['entity'] is GraphEventProducer
        assert self.p.workspace_id in query.compile().params.values()
        self.calls.append('producers')
        return self.rows[:query._limit_clause.value]

    def scalar(self, query):
        model = query.column_descriptions[0]['entity']
        params = query.compile().params
        assert self.p.workspace_id in params.values()
        if model is User:
            self.calls.append('actor')
            assert self.p.user_id in params.values() and self.p.role in params.values()
            return self.p.user_id if self.actor_allowed else None
        assert model is Capability
        self.calls.append('capability')
        return next((row for identity, row in self.caps.items() if identity in params.values()
            and row.workspace_id == self.p.workspace_id), None)


@pytest.fixture
def choices(monkeypatch):
    p = NS(user_id=str(uuid4()), workspace_id=str(uuid4()), role='admin', session_id='unit-session')
    first, caps = _producer(p, '原登记处理结果')
    second, more = _producer(p, '独立的另一来源'); caps.update(more)
    identity = str(uuid4()); original = _base_context(identity)
    db = ChoiceDB(p, [first, second], caps)
    monkeypatch.setattr(editor.old, 'get_context', lambda selected, actor, store:
        copy.deepcopy(original) if str(selected) == identity and actor is p and store is db else None)
    return NS(p=p, first=first, second=second, caps=caps, identity=identity, original=original, db=db)


def _get(s):
    return editor.get_context(UUID(s.identity), s.p, s.db)


def test_context_v2_keeps_closed_history_and_complete_float_contracts_without_capability_secrets(choices):
    s = choices; before = copy.deepcopy(s.original)
    value = _get(s)
    assert set(value) == set(before)
    assert value['schema'] == editor.SCHEMA
    assert value['event_wait']['available'] is True and value['event_wait']['reason'] is None
    assert set(value['event_wait']) == {'available', 'reason', 'policy', 'sources'}
    assert value['event_wait']['policy'] == editor.sources.POLICY
    for row, actual in zip(value['event_wait']['sources'], [s.first, s.second]):
        assert set(row) == {'producer_id', 'label', 'status', 'contract_hash', 'contract', 'available', 'reason'}
        assert row['available'] is True and row['reason'] is None
        assert row['contract'] == actual.data['contract'] and row['contract_hash'] == actual.contract_hash
    encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    assert '"minimum":-0.0' in encoded and '"maximum":1.0' in encoded and '1e-07' in encoded
    assert 'NEVER_' not in encoded and 'registration' not in value['event_wait']['sources'][0]
    value['event_wait']['sources'][0]['contract']['result_schema'].clear()
    value['event_wait']['policy']['sequence']['open'] = 'changed'
    assert s.first.data['contract']['result_schema'] == FLOAT_SCHEMA
    assert s.original == before and editor.sources.POLICY['sequence']['open'] == 'exclusive'


@pytest.mark.parametrize('change', ['revoked', 'retired_source', 'retired_extractor', 'changed_hash', 'changed_release', 'changed_role'])
def test_current_source_failure_disables_only_that_choice_and_preserves_registered_contract(choices, change):
    s = choices; row = s.first; contract = copy.deepcopy(row.data['contract'])
    source, extractor = (s.caps[contract['source_id']], s.caps[contract['extractor_id']])
    if change == 'revoked': row.status = 'revoked'
    elif change == 'retired_source': source.status = 'retired'
    elif change == 'retired_extractor': extractor.status = 'retired'
    elif change == 'changed_hash': extractor.content_hash = 'b' * 64
    elif change == 'changed_release': extractor.release_version = '3.0.2'
    else:
        extractor.data['allowed_agent_roles'] = ['business']
        extractor.content_hash = extractor.validated_hash = digest(extractor.data)
    value = _get(s); failed, healthy = value['event_wait']['sources']
    assert failed['available'] is False and failed['reason']
    assert failed['contract'] == contract and healthy['available'] is True
    assert value['event_wait']['available'] is True


@pytest.mark.parametrize('change', ['viewer', 'actor_revoked', 'inactive', 'empty'])
def test_read_history_does_not_grant_new_source_selection(choices, change):
    s = choices
    if change == 'viewer': s.p.role = 'viewer'
    elif change == 'actor_revoked': s.db.actor_allowed = False
    elif change == 'inactive': s.original['active'] = False
    else: s.db.rows = []
    value = _get(s)
    assert value['event_wait']['available'] is False and value['event_wait']['reason']
    assert value['design'] == s.original['design'] and value['configuration'] == s.original['configuration']
    if change in {'viewer', 'actor_revoked'}:
        assert all(row['available'] is False for row in value['event_wait']['sources'])
        assert 'capability' not in s.db.calls


def test_corrupt_cold_producer_is_neither_trusted_nor_silently_omitted(choices):
    choices.first.command_hash = 'f' * 64
    with pytest.raises(HTTPException) as caught: _get(choices)
    assert caught.value.status_code == 409


def test_source_catalog_is_bounded_and_foreign_workspace_row_is_not_displayed(choices, monkeypatch):
    s = choices; monkeypatch.setattr(editor, 'MAX_SOURCES', 1)
    with pytest.raises(HTTPException) as caught: _get(s)
    assert caught.value.status_code == 413
    monkeypatch.setattr(editor, 'MAX_SOURCES', 200)
    s.first.workspace_id = str(uuid4())
    with pytest.raises(HTTPException) as caught: _get(s)
    assert caught.value.status_code == 409


def test_source_contract_expected_value_error_becomes_safe_conflict_without_changing_register_dto(monkeypatch):
    def unavailable(*args): raise ValueError('NEVER_PRIVATE_INTERNAL_DIAGNOSTIC')
    monkeypatch.setattr(graph_event_api, 'build_contract', unavailable)
    body = NS(source_id=str(uuid4()), extractor_id=str(uuid4()), adapter_id='processed-observation-v1')
    with pytest.raises(HTTPException) as caught: graph_event_api.proposed(None, None, body)
    assert caught.value.status_code == 409 and 'NEVER_' not in caught.value.detail
    assert set(graph_event_api.Register.model_fields) == {'schema', 'request_id', 'label',
        'adapter_id', 'source_id', 'source_hash', 'extractor_id', 'extractor_hash'}
