"""Pure protocol/calculator/record seams; no providers, DB, devices or old lane."""
import asyncio
import copy
import inspect
import json
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BindParameter, BooleanClauseList

from generic_agents import simulation_evolution_v176 as app
from generic_agents.models import Audit
from generic_agents.simulation_models_v176 import SimulationFeedbackV176 as Feedback
from generic_agents.simulation_evolution_models_v176 import (
    SimulationEvolutionCommandV176 as Command, SimulationStudyV176 as Study)


def identity(number):
    return str(UUID(int=number))


WORKSPACE = identity(1)
ACTOR = identity(2)
ENV = identity(3)
P = SimpleNamespace(workspace_id=WORKSPACE, user_id=ACTOR, role='operator', session_id='pure-seam')


def manifest(threshold=35, operator='gt'):
    return {'execution': {'adapter': 'builtin', 'handler': 'numeric_rules'},
        'data_policy': {'cloud_allowed': False, 'deployment': 'local'},
        'allowed_agent_roles': ['perception'],
        'specification': {'rules': [{'id': 'temperature-high', 'field': 'temperature_c',
            'unit': '°C', 'operator': operator, 'threshold': threshold, 'severity': 'warning'}]}}


def sample(index, value, label, *, instant=None):
    observed_at = instant or (datetime(2026, 10, 23, tzinfo=timezone.utc) + timedelta(seconds=index)).isoformat()
    return {'observation_id': identity(100 + index), 'sample_id': identity(200 + index),
        'invocation_id': identity(300 + index), 'feedback_id': identity(400 + index),
        'feedback_version': 1, 'feedback_hash': str(index % 10) * 64,
        'feedback_actor_id': ACTOR, 'observed_at': observed_at,
        'label': label, 'reason': '模拟读数核对，保留原意见',
        'field': 'temperature_c', 'value': value, 'unit': '°C'}


def freeze(samples=None, *, baseline=None, threshold=30):
    baseline = copy.deepcopy(baseline or manifest())
    rows = samples or [sample(1, 20, 'should_not_alert'), sample(2, 34, 'should_alert'),
        sample(3, 20, 'should_not_alert'), sample(4, 34, 'should_alert')]
    return {'schema': 'simulation-study-freeze-v176', **app.SCOPE,
        'environment_id': ENV, 'entity_id': identity(5), 'extractor_id': identity(6),
        'extractor_hash': app.hash_value(baseline), 'title': '本地气象规则比较', 'reason': '只验证机制',
        'baseline_manifest': baseline, 'candidate_manifest': app.candidate_manifest(baseline, threshold),
        'samples': copy.deepcopy(rows), 'sample_inventory_hash': app.hash_value(rows),
        'candidate_selection': 'human_proposed_before_temporal_split'}


def feedback_body(**changes):
    return {'request_id': identity(10), 'expected_version': 0,
        'observation_id': identity(101), 'label': 'should_alert', 'reason': '需报警，模拟取样'} | changes


def study_body(**changes):
    return {'request_id': identity(11), 'environment_id': ENV, 'title': '温度阈值',
        'feedback_refs': [{'feedback_id': identity(401), 'expected_version': 1}],
        'candidate_threshold': 30, 'reason': '待验证候选'} | changes


def test_exact_wire_preserves_bytes_and_distinguishes_numeric_types():
    a = b'{ "request_id":"00000000-0000-0000-0000-00000000000a", "x":1 }'
    b = a.replace(b'"x":1', b'"x":1.0')
    va, exact, sha = app.read_wire(a)
    vb, _, other = app.read_wire(b)
    assert exact.encode() == a and sha != other
    assert type(va['x']) is int and type(vb['x']) is float
    assert app.hash_value(1) != app.hash_value(1.0) != app.hash_value(True)


@pytest.mark.parametrize('wire', [
    b'{"request_id":"00000000-0000-0000-0000-00000000000a","x":1,"x":2}',
    b'{"request_id":"00000000-0000-0000-0000-00000000000a","x":NaN}',
    b'{"request_id":"00000000-0000-0000-0000-00000000000a","x":Infinity}',
    b'{"request_id":"00000000-0000-0000-0000-00000000000A"}', b'[]', b'{}', b'\xff'])
def test_invalid_original_json_never_enters_the_command_lane(wire):
    with pytest.raises(HTTPException) as caught:
        app.read_wire(wire)
    assert caught.value.status_code == 422


@pytest.mark.parametrize('change', [{'expected_version': True}, {'expected_version': '0'},
    {'expected_version': -1}, {'label': 'yes'}, {'reason': '  '}, {'extra': 'discard me'},
    {'observation_id': 1}, {'request_id': identity(10).upper().replace('a', 'A')}])
def test_feedback_dto_is_closed_and_typed(change):
    with pytest.raises(ValidationError):
        app.FeedbackBody.model_validate(feedback_body(**change))


@pytest.mark.parametrize('value', [True, '30', float('nan'), float('inf'), 10**100])
def test_candidate_threshold_rejects_coercion_and_nonfinite(value):
    with pytest.raises(ValidationError):
        app.StudyBody.model_validate(study_body(candidate_threshold=value))


def test_candidate_threshold_retains_integer_or_float_spelling():
    assert type(app.StudyBody.model_validate(study_body(candidate_threshold=30)).candidate_threshold) is int
    assert type(app.StudyBody.model_validate(study_body(candidate_threshold=30.0)).candidate_threshold) is float
    with pytest.raises(ValidationError):
        app.StudyBody.model_validate(study_body(feedback_refs=[{'feedback_id': identity(401), 'expected_version': 1}] * 2))


def test_real_local_comparison_and_temporal_split_are_not_a_production_grant():
    original = freeze(); saved = copy.deepcopy(original)
    result = app.evaluate_frozen(original)
    assert original == saved
    assert result['status'] == 'completed'
    assert result['training']['baseline']['accuracy'] == .5
    assert result['validation']['baseline']['fn'] == 1
    assert result['validation']['candidate']['accuracy'] == 1.0
    assert result['recommendation'] == 'consider_simulation_shadow_only'
    assert result['split']['training_observation_ids'] == [identity(101), identity(102)]
    assert result['split']['validation_observation_ids'] == [identity(103), identity(104)]
    assert result['split_hash'] == app.hash_value(result['split'])
    assert result['cloud_calls'] == result['tool_calls'] == result['observation_writes'] == 0
    assert result['production_rule_changes'] == 0
    assert result['production_activation'] is result['original_extractor_replaced'] is False
    assert result['generalization_verified'] is result['shadow_started'] is False


def test_reverse_input_is_sorted_by_original_sample_clock_without_relabeling():
    frozen = freeze(); frozen['samples'].reverse()
    frozen['sample_inventory_hash'] = app.hash_value(frozen['samples'])
    result = app.evaluate_frozen(frozen)
    assert [row['observation_id'] for row in result['per_sample']] == [identity(101 + i) for i in range(4)]
    assert [row['label'] for row in result['per_sample']] == [
        'should_not_alert', 'should_alert', 'should_not_alert', 'should_alert']
    assert not set(result['split']['training_observation_ids']) & set(result['split']['validation_observation_ids'])


def test_gt_and_lt_use_strict_numeric_rules_boundary():
    rows = [sample(1, 30, 'should_not_alert'), sample(2, 29, 'should_alert'),
        sample(3, 30, 'should_not_alert'), sample(4, 29, 'should_alert')]
    result = app.evaluate_frozen(freeze(rows, baseline=manifest(20, 'lt'), threshold=30))
    assert [row['candidate_matched'] for row in result['per_sample']] == [False, True, False, True]
    assert result['validation']['candidate']['accuracy'] == 1.0


def test_uncertain_is_visible_but_not_in_accuracy_or_minimum_labeled_count():
    rows = [sample(1, 20, 'should_not_alert'), sample(2, 34, 'should_alert'),
        sample(3, 99, 'uncertain'), sample(4, 20, 'should_not_alert'), sample(5, 34, 'should_alert')]
    result = app.evaluate_frozen(freeze(rows))
    assert len(result['per_sample']) == 5
    counts = [result[part]['candidate'] for part in ('training', 'validation')]
    assert sum(row['uncertain'] for row in counts) == 1
    assert sum(row['labeled_count'] for row in counts) == 4
    assert all(row['accuracy'] == 1.0 for row in counts)
    blocked = app.evaluate_frozen(freeze([sample(i, 34, 'uncertain') for i in range(1, 5)]))
    assert blocked['status'] == 'blocked' and blocked['accuracy'] is None
    assert blocked['training'] is blocked['validation'] is None


def test_insufficient_samples_are_persistable_blocked_results_with_no_fake_accuracy():
    result = app.evaluate_frozen(freeze([sample(1, 38, 'should_alert')]))
    assert result['status'] == 'blocked' and result['accuracy'] is None
    assert result['split'] is result['split_hash'] is None
    assert len(result['per_sample']) == 1 and result['per_sample'][0]['split'] is None
    assert result['per_sample'][0]['baseline_matched'] is True


def test_equal_clock_groups_cannot_leak_across_the_temporal_boundary():
    stamp = '2026-10-23T12:00:00+08:00'
    rows = [sample(i, 34, 'should_alert', instant=stamp) for i in range(1, 5)]
    assert app.evaluate_frozen(freeze(rows))['status'] == 'blocked'
    rows[2]['observed_at'] = rows[3]['observed_at'] = '2026-10-23T12:00:01+08:00'
    result = app.evaluate_frozen(freeze(rows))
    assert result['status'] == 'completed'
    assert result['split']['training_latest'] == stamp
    assert result['split']['validation_earliest'] == '2026-10-23T12:00:01+08:00'
    assert result['recommendation'] == 'inconclusive'  # no negative class


@pytest.mark.parametrize('key', ['observation_id', 'sample_id', 'invocation_id', 'feedback_id'])
def test_duplicate_origin_is_not_an_independent_sample(key):
    frozen = freeze(); frozen['samples'][1][key] = frozen['samples'][0][key]
    frozen['sample_inventory_hash'] = app.hash_value(frozen['samples'])
    with pytest.raises(HTTPException):
        app.evaluate_frozen(frozen)


@pytest.mark.parametrize('mutation', ['operator', 'unit', 'field', 'extra_manifest_change', 'inventory'])
def test_only_threshold_can_change_and_frozen_inventory_is_bound(mutation):
    frozen = freeze()
    if mutation == 'inventory':
        frozen['samples'][0]['value'] = 1.0
    elif mutation == 'extra_manifest_change':
        frozen['candidate_manifest']['data_policy']['deployment'] = 'cloud'
    else:
        frozen['candidate_manifest']['specification']['rules'][0][mutation] = {
            'operator': 'lt', 'unit': 'F', 'field': 'humidity_percent'}[mutation]
    with pytest.raises(HTTPException):
        app.evaluate_frozen(frozen)


@pytest.mark.parametrize('key,value', [('simulation', False), ('test_only', False),
    ('field_verified', True), ('production_activation', True), ('simulation', 1)])
def test_sim_scope_is_strict_and_cannot_be_relabelled_as_field_authority(key, value):
    frozen = freeze(); frozen[key] = value
    with pytest.raises(HTTPException):
        app.evaluate_frozen(frozen)


@pytest.mark.parametrize('bad', [True, '34', None, float('nan'), float('inf')])
def test_invalid_measurement_is_never_converted_to_a_no_alert(bad):
    with pytest.raises(HTTPException):
        app.matched(manifest()['specification']['rules'][0], bad)


class FakeDB:
    """Just statement-selected row storage for transactional API seams, not SQL."""
    def __init__(self):
        self.rows = []; self.commits = 0; self.locks = []

    def add(self, row):
        self.rows.append(row)

    def scalars(self, statement):
        model = statement.column_descriptions[0]['entity']
        rows = [row for row in self.rows if type(row) is model]
        # Interpret only actual WHERE expressions. TypedPayloadORM's SELECT-list
        # json_build_object binds are output projections, never row filters.
        def value(row, node):
            if isinstance(node, BindParameter):
                return node.value
            if getattr(node, 'operator', None) is operators.json_getitem_op:
                original = value(row, node.left)
                return original.get(value(row, node.right)) if isinstance(original, dict) else None
            return getattr(row, node.key)
        def matches(row, node):
            if isinstance(node, BooleanClauseList):
                assert node.operator is operators.and_
                return all(matches(row, clause) for clause in node.clauses)
            left, right = value(row, node.left), value(row, node.right)
            if node.operator is operators.eq:
                return left == right
            assert getattr(node.operator, 'opstring', None) == '@>'
            return isinstance(left, list) and all(any(isinstance(actual, dict)
                and all(actual.get(key) == item for key, item in expected.items())
                for actual in left) for expected in right)
        if statement.whereclause is not None:
            rows = [row for row in rows if matches(row, statement.whereclause)]
        if model is Command:
            rows.sort(key=lambda row: row.target_version)
        return rows

    def scalar(self, statement):
        rows = self.scalars(statement)
        return rows[0] if rows else None

    def execute(self, *args, **kwargs):
        if len(args) > 1:
            self.locks.append(copy.deepcopy(args[1]))
        return None

    def commit(self):
        self.commits += 1


def request(raw, path='/api/workspace/simulation-v176/feedback'):
    async def receive():
        return {'type': 'http.request', 'body': raw, 'more_body': False}
    return Request({'type': 'http', 'method': 'POST', 'scheme': 'http',
        'server': ('test', 80), 'path': path, 'query_string': b'', 'headers': []}, receive)


def fake_basis():
    return {'schema': 'simulation-feedback-basis-v176', **app.SCOPE,
        'environment_id': ENV, 'entity_id': identity(5), 'observation_id': identity(101),
        'sample_id': identity(201), 'invocation_id': identity(301), 'source_id': identity(7),
        'extractor_id': identity(6), 'extractor_hash': app.hash_value(manifest()),
        'origin': {'independent_sample': identity(201)}, 'origin_hash': 'b' * 64,
        'observed_at': '2026-10-23T00:00:01+00:00', 'time_semantics': 'simulator_sample_time',
        'source_time_verified': False, 'field': 'temperature_c', 'value': 38,
        'unit': '°C', 'baseline_manifest': manifest(), 'extracted_hash': 'c' * 64}


def save(db, wire, *, principal=P, path=app.PREFIX + '/feedback', operation='feedback.save', apply=None):
    return asyncio.run(app.command(request(wire, path), principal, db, operation,
        app.FeedbackBody, apply or (lambda body: app.save_feedback(db, principal, body))))


@pytest.fixture
def seam(monkeypatch):
    live_calls = []
    def basis(db, p, identity, *, live):
        live_calls.append((identity, live))
        return copy.deepcopy(fake_basis())
    monkeypatch.setattr(app, 'lock_current_actor', lambda db, p: None)
    monkeypatch.setattr(app, 'observation_basis', basis)
    return FakeDB(), live_calls


def test_saved_label_replays_original_body_and_get_recovers_without_second_write(seam):
    db, calls = seam
    raw = json.dumps(feedback_body(), ensure_ascii=False).encode()
    first = save(db, raw)
    replay = save(db, raw)
    assert first == replay and first['version'] == 1
    assert db.commits == 1 and len(calls) == 1
    assert len([row for row in db.rows if type(row) is Feedback]) == 1
    recovered = app.original_request(identity(10), P, db, app.read_wire(raw)[2])
    assert recovered['found'] is True and recovered['result'] == first
    assert db.commits == 1
    absent = app.original_request(identity(99), P, db, 'a' * 64)
    assert absent['found'] is False


@pytest.mark.parametrize('change', ['whitespace', 'label', 'extra', 'path'])
def test_saved_nonce_rejects_any_changed_wire_or_path_before_live_sources(seam, change):
    db, calls = seam
    raw = json.dumps(feedback_body(), ensure_ascii=False).encode()
    save(db, raw)
    wire = feedback_body()
    if change == 'label': wire['label'] = 'should_not_alert'
    if change == 'extra': wire['extra'] = 'not silently discarded'
    altered = b' ' + raw if change == 'whitespace' else json.dumps(wire, ensure_ascii=False).encode()
    with pytest.raises(HTTPException) as caught:
        save(db, altered, path=app.PREFIX + ('/studies' if change == 'path' else '/feedback'))
    assert caught.value.status_code == 409
    assert db.commits == 1 and len(calls) == 1


def test_feedback_cas_keeps_independent_older_command_receipt_and_reason(seam):
    db, _ = seam
    original = json.dumps(feedback_body(), ensure_ascii=False).encode(); first = save(db, original)
    updated = feedback_body(request_id=identity(12), expected_version=1,
        label='should_not_alert', reason='修订原因仍是模拟数据')
    second = save(db, json.dumps(updated, ensure_ascii=False).encode())
    assert second['id'] == first['id'] and second['version'] == 2
    assert second['data']['reason'] == updated['reason']
    assert app.checked_record(db, P, 'feedback', first['id']).version == 2
    restored = app.original_request(identity(10), P, db, app.read_wire(original)[2])
    assert restored['result']['version'] == 1
    assert restored['result']['data']['label'] == 'should_alert'
    with pytest.raises(HTTPException) as caught:
        save(db, json.dumps(feedback_body(request_id=identity(13), expected_version=1), ensure_ascii=False).encode())
    assert caught.value.status_code == 409
    assert db.commits == 2


def test_mutating_current_record_and_resealing_its_view_cannot_forge_saved_label(seam):
    db, _ = seam
    result = save(db, json.dumps(feedback_body()).encode())
    row = next(row for row in db.rows if type(row) is Feedback)
    row.data = {**row.data, 'label': 'should_not_alert'}
    assert app._view(row, 'feedback')['record_hash'] != result['record_hash']
    with pytest.raises(HTTPException) as caught:
        app.checked_record(db, P, 'feedback', row.id)
    assert caught.value.status_code == 409


@pytest.mark.parametrize('damage', ['audit', 'command', 'command_and_own_hash'])
def test_independent_receipt_is_required_even_if_row_self_hash_is_recomputed(seam, damage):
    db, _ = seam
    result = save(db, json.dumps(feedback_body()).encode())
    receipt = next(row for row in db.rows if type(row) is Command)
    if damage == 'audit':
        db.rows = [row for row in db.rows if type(row) is not Audit]
    else:
        receipt.data = copy.deepcopy(receipt.data)
        receipt.data['result']['data']['reason'] = '伪造原因'
        if damage == 'command_and_own_hash':
            receipt.command_hash = app.hash_value(app._command_value(receipt))
    with pytest.raises(HTTPException) as caught:
        app.checked_record(db, P, 'feedback', result['id'])
    assert caught.value.status_code == 409


def test_original_request_is_private_to_real_submitter_and_body_sha(seam):
    db, _ = seam
    raw = json.dumps(feedback_body()).encode(); save(db, raw)
    other = SimpleNamespace(**{**vars(P), 'user_id': identity(9), 'role': 'admin'})
    with pytest.raises(HTTPException) as caught:
        app.original_request(identity(10), other, db, app.read_wire(raw)[2])
    assert caught.value.status_code == 403
    with pytest.raises(HTTPException) as caught:
        app.original_request(identity(10), P, db, '0' * 64)
    assert caught.value.status_code == 409


def test_live_lineage_failure_cannot_create_label_or_command(seam, monkeypatch):
    db, _ = seam
    def unavailable(*args, **kwargs):
        raise HTTPException(409, '来源或当前能力已撤销')
    monkeypatch.setattr(app, 'observation_basis', unavailable)
    with pytest.raises(HTTPException):
        save(db, json.dumps(feedback_body()).encode())
    assert db.rows == [] and db.commits == 0


def test_viewer_cannot_submit_even_with_correct_sim_markers(seam):
    db, calls = seam
    viewer = SimpleNamespace(**{**vars(P), 'role': 'viewer'})
    with pytest.raises(HTTPException) as caught:
        save(db, json.dumps(feedback_body()).encode(), principal=viewer)
    assert caught.value.status_code == 403
    assert not calls and db.rows == []


def prepare_study(monkeypatch, *, count=4, altered_basis=None):
    db = FakeDB()
    samples = [sample(1, 20, 'should_not_alert'), sample(2, 34, 'should_alert'),
        sample(3, 20, 'should_not_alert'), sample(4, 34, 'should_alert')][:count]
    bases = {}
    for item in samples:
        basis = fake_basis()
        basis.update({key: copy.deepcopy(item[key]) for key in (
            'observation_id', 'sample_id', 'invocation_id', 'observed_at', 'field', 'value', 'unit')})
        if altered_basis:
            altered_basis(basis, item)
        bases[item['observation_id']] = basis
    monkeypatch.setattr(app, 'lock_current_actor', lambda db, p: None)
    monkeypatch.setattr(app, 'observation_basis', lambda db, p, identity, *, live: copy.deepcopy(bases[identity]))
    # Scope and provenance have their own cold guard tests. This pure seam
    # supplies only environment admission; it cannot stand in for PG evidence.
    def environment(db, p, identity, *, live):
        app.need(identity == ENV)
        return None
    monkeypatch.setitem(sys.modules, 'generic_agents.simulation_loop_v176', SimpleNamespace(environment=environment))
    refs = []
    for index, item in enumerate(samples, 1):
        body = feedback_body(request_id=identity(10 + index), observation_id=item['observation_id'],
            label=item['label'], reason=item['reason'])
        result = save(db, json.dumps(body, ensure_ascii=False).encode())
        refs.append({'feedback_id': result['id'], 'expected_version': result['version']})
    return db, refs


def create_comparison(db, refs, **changes):
    raw = json.dumps(study_body(request_id=identity(30), feedback_refs=refs, **changes), ensure_ascii=False).encode()
    result = asyncio.run(app.command(request(raw, app.PREFIX + '/studies'), P, db,
        'study.create', app.StudyBody, lambda body: app.create_study(db, P, body)))
    return result, raw


def run_comparison(db, study, *, request_id=None):
    body = {'request_id': request_id or identity(31), 'expected_version': study['version'],
        'expected_study_hash': study['frozen_hash']}
    raw = json.dumps(body).encode()
    path = app.PREFIX + '/studies/' + study['id'] + '/evaluate'
    result = asyncio.run(app.command(request(raw, path), P, db, 'study.evaluate', app.EvaluateBody,
        lambda command: app.evaluate_study(db, P, study['id'], command)))
    return result, raw


def test_study_freezes_original_feedback_then_persists_actual_local_result_and_recovers(monkeypatch):
    db, refs = prepare_study(monkeypatch)
    created, _ = create_comparison(db, refs)
    assert created['status'] == 'frozen' and created['version'] == 1
    frozen = created['data']['freeze']
    assert created['frozen_hash'] == app.hash_value(frozen)
    assert frozen['samples'][0]['reason'] == '模拟读数核对，保留原意见'
    assert all(row['feedback_version'] == 1 for row in frozen['samples'])
    assert frozen['baseline_manifest']['specification']['rules'][0]['threshold'] == 35
    assert frozen['candidate_manifest']['specification']['rules'][0]['threshold'] == 30
    completed, raw = run_comparison(db, created)
    assert completed['status'] == 'completed' and completed['version'] == 2
    assert completed['data']['freeze'] == frozen
    assert completed['data']['result']['validation']['candidate']['accuracy'] == 1.0
    assert completed['data']['result_hash'] == app.hash_value(completed['data']['result'])
    assert app.checked_record(db, P, 'study', completed['id']).version == 2
    assert db.commits == 6  # four labels, freeze, comparison
    monkeypatch.setattr(app, 'evaluate_frozen', lambda value: pytest.fail('原请求重放不得重新计算'))
    path = app.PREFIX + '/studies/' + created['id'] + '/evaluate'
    replay = asyncio.run(app.command(request(raw, path), P, db, 'study.evaluate', app.EvaluateBody,
        lambda command: app.evaluate_study(db, P, created['id'], command)))
    assert replay == completed and db.commits == 6
    recovery = app.original_request(identity(31), P, db, app.read_wire(raw)[2])
    assert recovery['result'] == completed
    assert app.study_detail(created['id'], P, db) == completed


def test_not_enough_real_samples_saves_a_blocked_study_not_perfect_metrics(monkeypatch):
    db, refs = prepare_study(monkeypatch, count=1)
    created, _ = create_comparison(db, refs)
    result, _ = run_comparison(db, created)
    assert result['status'] == 'blocked' and result['version'] == 2
    assert result['data']['result']['accuracy'] is None
    assert result['data']['result']['training'] is result['data']['result']['validation'] is None
    assert app.checked_record(db, P, 'study', result['id']).status == 'blocked'


def test_label_revision_preserves_frozen_study_but_requires_new_refs_for_new_evaluation(monkeypatch):
    db, refs = prepare_study(monkeypatch)
    study, _ = create_comparison(db, refs)
    edited = feedback_body(request_id=identity(40), expected_version=1,
        observation_id=identity(101), label='uncertain', reason='后续核对仍不确定')
    save(db, json.dumps(edited, ensure_ascii=False).encode())
    with pytest.raises(HTTPException) as caught:
        run_comparison(db, study)
    assert caught.value.status_code == 409
    saved = app.study_detail(study['id'], P, db)
    assert saved['status'] == 'frozen' and saved['version'] == 1
    original = next(row for row in saved['data']['freeze']['samples'] if row['observation_id'] == identity(101))
    assert original['label'] == 'should_not_alert' and original['feedback_version'] == 1
    assert saved['data']['result'] is None


@pytest.mark.parametrize('kind', ['environment', 'entity', 'extractor', 'duplicate_sample'])
def test_study_cannot_mix_scope_objects_models_or_duplicate_device_samples(monkeypatch, kind):
    def alter(basis, item):
        if item['observation_id'] != identity(104):
            return
        if kind == 'environment': basis['environment_id'] = identity(99)
        if kind == 'entity': basis['entity_id'] = identity(99)
        if kind == 'extractor': basis['extractor_id'] = identity(99)
        if kind == 'duplicate_sample': basis['sample_id'] = identity(201)
    db, refs = prepare_study(monkeypatch, altered_basis=alter)
    with pytest.raises(HTTPException) as caught:
        create_comparison(db, refs)
    assert caught.value.status_code == 409
    assert not any(type(row) is Study for row in db.rows)
    assert db.commits == 4


def test_forged_completed_study_state_cannot_bypass_real_frozen_command(monkeypatch):
    db, refs = prepare_study(monkeypatch)
    created, _ = create_comparison(db, refs)
    row = next(row for row in db.rows if type(row) is Study)
    row.status = 'completed'; row.data = {**row.data, 'result': {'accuracy': 1.0}}
    with pytest.raises(HTTPException) as caught:
        app.study_detail(created['id'], P, db)
    assert caught.value.status_code == 409


@pytest.mark.parametrize('kind', ['feedback', 'study'])
@pytest.mark.parametrize('retain_audit', [True, False])
def test_missing_command_with_audit_or_target_residue_never_becomes_fresh_or_missing(monkeypatch, kind, retain_audit):
    if kind == 'feedback':
        db, _ = prepare_study(monkeypatch, count=1)
        request_id = identity(11)
        old = next(row for row in db.rows if type(row) is Command and row.request_id == request_id)
        raw = old.exact_body.encode('utf-8')
        operation, model = 'feedback.save', app.FeedbackBody
        apply = lambda body: app.save_feedback(db, P, body)
    else:
        db, refs = prepare_study(monkeypatch)
        created, raw = create_comparison(db, refs)
        request_id = identity(30)
        old = next(row for row in db.rows if type(row) is Command and row.request_id == request_id)
        assert created['data']['request_journal'][0]['request_id'] == request_id
        operation, model = 'study.create', app.StudyBody
        apply = lambda body: app.create_study(db, P, body)
    old_count, commits = len(db.rows), db.commits
    db.rows.remove(old)
    if not retain_audit:
        db.rows = [row for row in db.rows if not (type(row) is Audit and row.object_id == old.id)]
    kept_count = len(db.rows)
    with pytest.raises(HTTPException) as caught:
        app.original_request(request_id, P, db, app.read_wire(raw)[2])
    assert caught.value.status_code == 409
    with pytest.raises(HTTPException) as caught:
        asyncio.run(app.command(request(raw, old.path), P, db, operation, model, apply))
    assert caught.value.status_code == 409
    assert db.commits == commits and len(db.rows) == kept_count < old_count
    assert db.locks[-1] == db.locks[-2]  # GET/POST serialize on the original nonce


def test_new_audit_and_target_keep_exact_request_reverse_references(seam):
    db, _ = seam
    raw = json.dumps(feedback_body(), ensure_ascii=False).encode()
    result = save(db, raw)
    row = next(row for row in db.rows if type(row) is Command)
    anchor = next(row for row in db.rows if type(row) is Audit)
    ref = result['data']['request_journal'][0]
    assert ref == {'request_id': row.request_id, 'body_sha256': app.read_wire(raw)[2],
        'path': row.path, 'operation': row.operation, 'command_id': row.id,
        'target_kind': 'feedback', 'target_id': result['id'], 'target_version': 1}
    assert all(anchor.data[key] == ref[key] for key in (
        'request_id', 'body_sha256', 'path', 'operation', 'target_kind', 'target_id', 'target_version'))


def test_record_reader_refreshes_and_shared_locks_before_loading_command_inventory(seam, monkeypatch):
    db, _ = seam
    result = save(db, json.dumps(feedback_body()).encode())
    captured = []; original = db.scalar
    def scalar(statement):
        captured.append(statement)
        return original(statement)
    monkeypatch.setattr(db, 'scalar', scalar)
    app.checked_record(db, P, 'feedback', result['id'])
    first = captured[0]
    assert first._for_update_arg is not None and first._for_update_arg.read is True
    assert first.get_execution_options()['populate_existing'] is True


def test_router_and_write_seams_do_not_import_the_manual_lane_or_execute_tools():
    source = inspect.getsource(app)
    assert app.router.prefix == '/api/workspace/simulation-v176'
    assert '/evolution/requests/{request_id}' in {route.path.removeprefix(app.PREFIX) for route in app.router.routes}
    assert 'threshold_evolution_basis_v147' not in source
    assert 'threshold_evolution_gate_v147' not in source
    assert 'from .gateway' not in source and 'invoke(' not in source
    assert 'enqueue_event(' not in source and 'Memory(' not in source
    assert 'execution=False' in inspect.getsource(app.observation_basis)
    assert 'live=False' in inspect.getsource(app.feedback_list)
    assert 'live=False' in inspect.getsource(app.study_detail)
    assert not any('shadow' in route.path or 'publish' in route.path for route in app.router.routes)
