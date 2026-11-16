"""Pure recovery protocol and cached-consumer seams; no DB/network/storage I/O."""
import asyncio
import copy
import hashlib
import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BindParameter, BooleanClauseList

from generic_agents import simulation_sample_recovery_v176 as recovery
from generic_agents import connectors, evidence, leases, relations
from generic_agents.models import Audit, Invocation, Job
from generic_agents.simulation_models_v176 import SimulationSampleV176 as Sample


def uid(value):
    return str(UUID(int=value))


P = SimpleNamespace(workspace_id=uid(1), user_id=uid(2), role='operator', session_id='pure-recovery')


class Rows:
    def __init__(self, rows):
        self.rows = rows; self.commits = 0

    def add(self, row):
        self.rows.append(row)

    def scalars(self, statement):
        model = statement.column_descriptions[0]['entity']
        def value(row, node):
            if isinstance(node, BindParameter): return node.value
            return getattr(row, node.key)
        def matches(row, node):
            if isinstance(node, BooleanClauseList):
                assert node.operator is operators.and_
                return all(matches(row, clause) for clause in node.clauses)
            assert node.operator is operators.eq
            return value(row, node.left) == value(row, node.right)
        return [row for row in self.rows if type(row) is model and matches(row, statement.whereclause)]

    def scalar(self, statement):
        rows = self.scalars(statement)
        return rows[0] if rows else None

    def commit(self):
        self.commits += 1


def original_output():
    return {**recovery.store.MARK, 'device_id': 'sim-weather-01', 'action': 'read_telemetry',
        'status': 'succeeded', 'request_id': 'simulation-sample:' + uid(10),
        'result': {**recovery.store.MARK, 'id': 'sim-weather-01',
            'sampled_at': '2026-10-23T02:03:04.125+00:00',
            'time_semantics': 'simulator_sample_time', 'physical_device_connected': False,
            'state': {'temperature_c': 38}}}


@pytest.fixture
def case(monkeypatch):
    env = SimpleNamespace(id=uid(3), data={'namespace': 'sim', **recovery.store.MARK})
    cap = SimpleNamespace(id=uid(4), kind='tool', namespace='equipment_simulation', name='read_equipment',
        data={'execution': {'adapter': 'http', 'side_effect': False, 'endpoint': 'http://fixture/invoke',
                'receipt_endpoint': 'http://fixture/platform-receipts', 'secret_ref': 'GA_CONNECTOR_SECRET_SIM'},
            'data_policy': {'simulation': True, 'test_only': True, 'cloud_allowed': False},
            'allowed_agent_roles': ['perception'],
            'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object'}})
    cap.content_hash = recovery.digest(cap.data)
    definition = SimpleNamespace(id=uid(5), status='enabled', data={
        'device_id': 'sim-weather-01', 'fields': ['temperature_c'], 'gateway_id': cap.id,
        'pins': {cap.id: cap.content_hash}})
    inputs = {'device_id': 'sim-weather-01', 'action': 'read_telemetry',
        'expected_revision': None, 'arguments': {}}
    sampler = Job(id=uid(6), workspace_id=P.workspace_id, kind=recovery.store.KIND,
        status='succeeded', version=1, lease_token=uid(7),
        idempotency_key='simulation-sample:' + uid(10), data={'sample_id': uid(10), 'actor_id': P.user_id})
    sample = Sample(id=uid(10), workspace_id=P.workspace_id, definition_id=definition.id,
        actor_id=P.user_id, request_id=uid(11), status='awaiting_receipt', version=1,
        created_at=datetime(2026, 10, 23, 2, 3, 4, tzinfo=timezone.utc),
        invocation_id=uid(12), observation_id=None,
        data={'schema': 'simulation-sample-v176', 'job_id': sampler.id,
            'definition_hash': recovery.digest(definition.data), 'environment_hash': recovery.digest(env.data),
            'inputs': inputs, 'input_hash': recovery.digest(inputs), 'issue': '原调用结果未知'})
    call = Invocation(id=uid(12), workspace_id=P.workspace_id, capability_id=cap.id,
        idempotency_key=sampler.idempotency_key, status='unknown', version=3,
        data={'input_hash': recovery.digest(inputs), 'capability_hash': cap.content_hash,
            'role': 'perception', 'side_effect': False, 'approval': None,
            'job_id': sampler.id, 'lease_token': uid(8),
            'financial': {'reserved_minor': 0, 'settled': False}, 'error': '原传输结果未知'})
    anchor = Audit(id=uid(13), workspace_id=P.workspace_id, actor_id=P.user_id,
        action='simulation.sample.enqueued_v176', object_id=sample.id,
        data={'definition_id': definition.id, 'request_id': sample.request_id})
    db = Rows([sample, sampler, call, anchor])
    api = SimpleNamespace(db=db, sample=sample, sampler=sampler, call=call, definition=definition,
        cap=cap, env=env, query_calls=[], consumed=[], evidence={})
    monkeypatch.setattr(recovery.store, 'checked_definition', lambda db, p, identity: (
        definition, env, SimpleNamespace(id=uid(14)), SimpleNamespace(id=uid(15))))
    monkeypatch.setattr(recovery, 'published', lambda db, p, identity, kind: cap)
    monkeypatch.setattr(leases, 'checkpoint', lambda db: db.commit())
    monkeypatch.setattr(relations, 'lock_current_actor', lambda db, p: None)
    monkeypatch.setattr(leases, 'ownership', lambda db: {'job_id': api.job.id, 'lease_token': api.job.lease_token})
    def save(db, p, raw, media_type, metadata):
        identity = uid(1000 + len(api.evidence))
        row = SimpleNamespace(id=identity, workspace_id=p.workspace_id,
            sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw), media_type=media_type,
            data=copy.deepcopy(metadata))
        api.evidence[identity] = (row, bytes(raw))
        return row
    monkeypatch.setattr(evidence, 'save_immutable', save)
    monkeypatch.setattr(evidence, 'evidence_bytes', lambda db, p, identity: api.evidence[identity])
    def query(execution, key):
        api.query_calls.append((copy.deepcopy(execution), key))
        return {'idempotency_key': key, 'input_hash': sample.data['input_hash'],
            'status': 'succeeded', 'output': original_output()}
    monkeypatch.setattr(connectors, 'query_receipt', query)
    def consume(db, p, original_job, *, cached_only):
        assert cached_only is True and original_job.id == sampler.id
        assert call.status == 'succeeded' and sample.invocation_id == call.id
        assert 'issue' not in sample.data
        assert sample.data['recovery']['status'] == 'verified_succeeded'
        api.consumed.append(copy.deepcopy(sample.data))
        # This seam stands in for Root's existing cached-only observation intake.
        sample.observation_id = uid(16); sample.status = 'received'
    monkeypatch.setattr(recovery.store, 'run_sample', consume)
    body = recovery.RecoverBody(request_id=uid(20), expected_version=1)
    response = recovery.enqueue(db, P, sample.id, body)
    api.job = next(row for row in db.rows if type(row) is Job and row.kind == recovery.KIND)
    api.job.status = 'running'; api.job.lease_token = uid(21)
    api.response = response
    return api


@pytest.mark.parametrize('body', [
    {'request_id': uid(20), 'expected_version': True},
    {'request_id': uid(20), 'expected_version': '1'},
    {'request_id': uid(20), 'expected_version': 0},
    {'request_id': uid(20), 'expected_version': 1, 'gateway_id': uid(99)},
    {'request_id': '00000000-0000-0000-0000-0000000000AB', 'expected_version': 1}])
def test_recovery_has_only_strict_original_nonce_and_version(body):
    with pytest.raises(ValidationError):
        recovery.RecoverBody.model_validate(body)


def test_route_delegates_original_request_recovery_to_root_journal_without_network(case, monkeypatch):
    seen = []
    async def journal(request, body, operation, p, db, apply):
        seen.append((body.request_id, body.expected_version, operation, p.user_id))
        return {'from_original_journal': True}
    monkeypatch.setattr(recovery, 'command', journal)
    result = asyncio.run(recovery.recover(case.sample.id,
        recovery.RecoverBody(request_id=uid(22), expected_version=2), None, P, case.db))
    assert result == {'from_original_journal': True}
    assert seen == [(uid(22), 2, 'sample.' + case.sample.id + '.recover', P.user_id)]
    assert case.query_calls == []


def test_successful_original_receipt_preserves_financial_and_lease_fields_and_caches_only(case):
    before = recovery._snapshot(case.call)
    result = recovery.run(case.db, P, case.job)
    assert result['status'] == 'received' and result['post_replayed'] is False
    assert len(case.query_calls) == 1 and case.query_calls[0][1] == case.sampler.idempotency_key
    assert case.query_calls[0][0]['receipt_endpoint'].endswith('/platform-receipts')
    assert len(case.consumed) == 1 and case.call.status == 'succeeded'
    proof = case.call.data['simulation_receipt_recovery_v176']
    assert proof['original_state'] == before and proof['original_state_hash'] == recovery.digest(before)
    assert case.call.data['financial'] == before['data']['financial']
    assert case.call.data['job_id'] == before['data']['job_id']
    assert case.call.data['lease_token'] == before['data']['lease_token']
    assert case.call.version == before['version'] + 1
    assert case.call.data['output']['result']['sampled_at'] == '2026-10-23T02:03:04.125+00:00'
    assert case.sample.data['recovery']['original_sample_state']['issue'] == '原调用结果未知'
    assert case.sample.data == case.consumed[0]  # no late mutation after received provenance
    assert any(type(row) is Audit and row.action == recovery.READ for row in case.db.rows)
    recovery._restored_proof(case.db, P, case.call)
    receipt_evidence, raw = case.evidence[proof['receipt_evidence_id']]
    assert raw == recovery.store.json_bytes(proof['receipt'])
    assert receipt_evidence.data['native_http_bytes'] is False


@pytest.mark.parametrize('status', ['pending', 'not_executed', 'failed'])
def test_non_success_receipt_stays_unknown_and_never_becomes_nonexecution(case, monkeypatch, status):
    def query(execution, key):
        case.query_calls.append((execution, key))
        return {'idempotency_key': key, 'input_hash': case.sample.data['input_hash'],
            'status': status, **({'terminal': True} if status == 'not_executed' else {})}
    monkeypatch.setattr(connectors, 'query_receipt', query)
    before = recovery._snapshot(case.call)
    result = recovery.run(case.db, P, case.job)
    assert result['status'] == 'unknown' and case.sample.status == 'awaiting_receipt'
    assert case.sample.observation_id is None and case.consumed == []
    assert recovery._snapshot(case.call) == before
    assert case.sample.data['recovery']['post_replayed'] is False
    if status != 'failed':
        assert case.sample.data['recovery']['receipt_evidence_id'] in case.evidence


def test_missing_receipt_is_unknown_and_preserves_original_call(case, monkeypatch):
    def absent(execution, key):
        raise HTTPException(404, 'not found')
    monkeypatch.setattr(connectors, 'query_receipt', absent)
    before = recovery._snapshot(case.call)
    result = recovery.run(case.db, P, case.job)
    assert result['status'] == 'unknown' and case.sample.observation_id is None
    assert recovery._snapshot(case.call) == before and not case.consumed


def test_no_original_call_never_invokes_gateway_or_receipt_transport(case):
    case.db.rows.remove(case.call); case.sample.invocation_id = None
    result = recovery.run(case.db, P, case.job)
    assert result['status'] == 'unknown'
    assert case.query_calls == case.consumed == []
    assert not any(type(row) is Invocation for row in case.db.rows)


def test_existing_success_without_proof_uses_one_get_and_preserves_original_success(case):
    case.call.status = 'succeeded'; case.call.data = {**case.call.data, 'output': original_output()}
    before = recovery._snapshot(case.call)
    result = recovery.run(case.db, P, case.job)
    assert result['status'] == 'received' and len(case.query_calls) == 1
    proof = case.call.data['simulation_receipt_recovery_v176']
    assert proof['original_state'] == before and proof['original_state_hash'] == recovery.digest(before)
    assert proof['query_only'] is True and proof['post_replayed'] is False
    assert case.call.version == before['version'] + 1 and len(case.consumed) == 1
    assert recovery.same(before['data']['output'], case.call.data['output'])
    assert {k: v for k, v in case.call.data.items() if k != 'simulation_receipt_recovery_v176'} == before['data']


@pytest.mark.parametrize('changed', [37, 38.0])
def test_existing_success_get_must_match_the_saved_typed_output(case, monkeypatch, changed):
    case.call.status = 'succeeded'; case.call.data = {**case.call.data, 'output': original_output()}
    before = recovery._snapshot(case.call)
    def query(execution, key):
        case.query_calls.append((copy.deepcopy(execution), key))
        value = original_output(); value['result']['state']['temperature_c'] = changed
        return {'idempotency_key': key, 'input_hash': case.sample.data['input_hash'],
            'status': 'succeeded', 'output': value}
    monkeypatch.setattr(connectors, 'query_receipt', query)
    with pytest.raises(HTTPException): recovery.run(case.db, P, case.job)
    assert len(case.query_calls) == 1 and recovery._snapshot(case.call) == before
    assert not case.consumed and not case.evidence


def test_real_supplier_failure_is_not_reclassified_even_if_a_success_receipt_exists(case):
    case.call.status = 'failed'; case.call.data = {**case.call.data, 'error': 'supplier_fault'}
    before = recovery._snapshot(case.call)
    result = recovery.run(case.db, P, case.job)
    assert result['status'] == 'unknown' and recovery._snapshot(case.call) == before
    assert case.query_calls == case.consumed == []


@pytest.mark.parametrize('anchor', [True, False])
def test_only_independently_anchored_worker_interruption_can_recover_a_failed_call(case, anchor):
    case.call.status = 'failed'
    case.call.data = {**case.call.data, 'recovery': {'reason': 'expired_worker_lease',
        'recovering_job_id': case.sampler.id, 'previous_lease_token': case.call.data['lease_token'],
        'recovered_at': '2026-10-23T00:00:00+00:00'}}
    if anchor:
        case.db.add(Audit(id=uid(30), workspace_id=P.workspace_id, actor_id=P.user_id,
            action='worker.invocation_recovered', object_id=case.call.id,
            data={'status': 'failed', 'job_id': case.sampler.id}))
    result = recovery.run(case.db, P, case.job)
    assert result['status'] == ('received' if anchor else 'unknown')
    assert len(case.query_calls) == int(anchor)
    assert case.call.status == ('succeeded' if anchor else 'failed')


@pytest.mark.parametrize('change', ['key', 'hash', 'physical', 'wrong_device', 'missing_field', 'bad_clock', 'bad_number'])
def test_mismatched_or_fabricated_receipt_never_creates_observation(case, monkeypatch, change):
    def query(execution, key):
        result = {'idempotency_key': key, 'input_hash': case.sample.data['input_hash'],
            'status': 'succeeded', 'output': original_output()}
        if change == 'key': result['idempotency_key'] = 'other-request'
        if change == 'hash': result['input_hash'] = 'a' * 64
        if change == 'physical': result['output']['result']['physical_device_connected'] = True
        if change == 'wrong_device': result['output']['device_id'] = 'sim-dock-01'
        if change == 'missing_field': result['output']['result']['state'] = {}
        if change == 'bad_clock': result['output']['result']['sampled_at'] = '2026-10-23T00:00:00'
        if change == 'bad_number': result['output']['result']['state']['temperature_c'] = True
        return result
    monkeypatch.setattr(connectors, 'query_receipt', query)
    before = recovery._snapshot(case.call)
    assert recovery.run(case.db, P, case.job)['status'] == 'unknown'
    assert recovery._snapshot(case.call) == before and case.consumed == []


@pytest.mark.parametrize('field,value', [('input_hash', 'wrong'), ('job_id', uid(99)),
    ('side_effect', True), ('approval', {'any': 'approval'}), ('role', 'business'), ('capability_hash', 'wrong')])
def test_original_call_identity_and_authority_are_cold_checked_before_get(case, field, value):
    case.call.data = {**case.call.data, field: value}
    with pytest.raises(HTTPException) as caught:
        recovery.run(case.db, P, case.job)
    assert caught.value.status_code == 409
    assert case.query_calls == case.consumed == []


def test_live_authority_is_rechecked_after_receipt_read_without_mutating_call(case, monkeypatch):
    original = recovery.store.checked_definition; checks = []
    def changed(*args):
        checks.append(True)
        if len(checks) == 2:
            raise HTTPException(409, '接入或审批已停止')
        return original(*args)
    monkeypatch.setattr(recovery.store, 'checked_definition', changed)
    before = recovery._snapshot(case.call)
    with pytest.raises(HTTPException):
        recovery.run(case.db, P, case.job)
    assert len(checks) == 2 and len(case.query_calls) == 1
    assert recovery._snapshot(case.call) == before and not case.consumed


def test_foreign_actor_original_running_job_or_missing_enqueued_anchor_cannot_recover(case):
    stranger = SimpleNamespace(**{**vars(P), 'user_id': uid(99), 'role': 'admin'})
    with pytest.raises(HTTPException) as caught:
        recovery._source(case.db, stranger, case.sample)
    assert caught.value.status_code == 403
    case.sampler.status = 'running'
    with pytest.raises(HTTPException):
        recovery.run(case.db, P, case.job)
    case.sampler.status = 'succeeded'
    case.db.rows = [row for row in case.db.rows if not (type(row) is Audit and row.action == recovery.ENQUEUED)]
    with pytest.raises(HTTPException):
        recovery.run(case.db, P, case.job)
    assert not case.query_calls and not case.consumed


def test_concurrent_second_recovery_is_blocked_and_received_sample_is_read_only(case):
    with pytest.raises(HTTPException) as caught:
        recovery.enqueue(case.db, P, case.sample.id,
            recovery.RecoverBody(request_id=uid(40), expected_version=2))
    assert caught.value.status_code == 409
    recovery.run(case.db, P, case.job)
    before = copy.deepcopy(case.sample.data); calls = len(case.query_calls)
    again = recovery.run(case.db, P, case.job)
    assert again['status'] == 'already_received' and case.sample.data == before
    assert len(case.query_calls) == calls


def test_success_proof_cannot_be_resealed_after_mutating_original_state(case):
    recovery.run(case.db, P, case.job)
    proof = copy.deepcopy(case.call.data['simulation_receipt_recovery_v176'])
    proof['original_state']['data']['financial']['reserved_minor'] = 99
    proof['original_state_hash'] = recovery.digest(proof['original_state'])
    case.call.data = {**case.call.data, 'simulation_receipt_recovery_v176': proof}
    with pytest.raises(HTTPException):
        recovery._restored_proof(case.db, P, case.call)


def test_consumer_seam_has_no_post_or_invoke_and_all_metadata_precedes_source_freeze():
    source = inspect.getsource(recovery)
    assert 'invoke(' not in source and 'httpx' not in source and '.post(' not in source.replace('@router.post(', '')
    assert 'cached_only=True' in inspect.getsource(recovery.run)
    assert 'query_receipt' not in inspect.getsource(recovery.recover)
    assert recovery.router.prefix == '/api/workspace/simulation-v176'
