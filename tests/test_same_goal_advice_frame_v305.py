"""Thin frame with explicit resolver/field/capability/fact IO boundaries.

The real pure composer, new closed CloudDecisionInput and reference/quote/
mapping/conflict checks run. This is not source, IAM, PG, worker or cloud proof.
"""
import copy
import hashlib
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_frame_v305 as f
from generic_agents.contracts import digest
from generic_agents.models import Task, Plan
from tests.test_business_goal_content_composition_v305 import scene as content_scene, uid
from tests.test_same_goal_advice_contracts_v302 import scene as contract_scene


@pytest.fixture
def scene(monkeypatch):
    pure = content_scene(); contract = contract_scene(); prep = copy.deepcopy(contract.preparation); scope = prep['scope']
    source = copy.deepcopy(contract.sources); raw = '真实已批准摘录😀，未声明整份文档完整。'.encode('utf-8')
    source['document']['evidence_ref'].update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    source['data_evidence_ref'].update(sha256=hashlib.sha256(pure.data_source['text'].encode('utf-8')).hexdigest(),
        size_bytes=len(pure.data_source['text'].encode('utf-8')))
    source = f.c.seal_source({key: value for key, value in source.items() if key != 'record_hash'},
        preparation=prep, manifest=contract.manifest)
    actual = Task(id=scope['task_id'], workspace_id=scope['workspace_id'], package_id=scope['package_id'],
        data={'authenticated_current_task': True})
    caller_task = Task(id=actual.id, workspace_id=scope['workspace_id'], package_id=uid(999),
        data={'stale_caller_data_must_not_drive_the_frame': True})
    caller_plan = Plan(id=scope['analysis_plan_id'], workspace_id=scope['workspace_id'], task_id=actual.id,
        data={'instruction': 'Untrusted caller fallback', 'model_pin': {'id': uid(999)}})
    release = NS(id=scope['release_id'])
    package = {'id': scope['package_id'], 'hash': 'b' * 64, 'data': {'actual_package_IO': True}}
    pins = {'package': package, 'release': {'id': release.id}, 'fields': copy.deepcopy(pure.fields),
        'entities': copy.deepcopy(pure.entities), 'object_snapshots_hash': 'e' * 64,
        'goal_snapshot_hash': scope['goal_snapshot_hash']}
    original = {'schema': f.analysis.ORIGINALS, 'task_id': actual.id, 'release_id': release.id,
        'package_id': actual.package_id, 'package_hash': package['hash'], 'data': {
            'packet_id': source['mature_head']['evaluation_ref']['id'], 'evidence_id': source['data_evidence_ref']['evidence_id'],
            'sha256': source['data_evidence_ref']['sha256'], 'text': pure.data_source['text'],
            'evaluation_record': {'evaluation_id': source['mature_head']['evaluation_ref']['id'],
                'record_hash': source['mature_head']['evaluation_ref']['record_hash'], 'evaluation_result': copy.deepcopy(pure.report)}}}
    resolved = {'schema': f.analysis.SCHEMA, 'task': actual, 'preparation': prep, 'source_record': source,
        'original_sources': original, 'document': {'record': copy.deepcopy(source['document']), 'content': raw,
            'preparation_marker': {'explicit_original_receipt_IO': True}},
        'frame_inputs': {'question': '已批准的业务问题', 'decision_intent': 'information_acquisition',
            'candidate_specs': copy.deepcopy(pure.candidate_specs)}, 'release': release}
    state = NS(task=caller_task, plan=caller_plan, actual=actual, release=release, resolved=resolved,
        pins=pins, pure=pure, calls=[], allowed=True, p=NS(workspace_id=scope['workspace_id'],
            user_id=prep['business_actor_id'], role='operator'))
    class DB:
        @property
        def no_autoflush(self): return nullcontext()
        def __getattr__(self, name): pytest.fail('Thin frame performed undeclared IO: ' + name)
    state.db = DB()
    def resolve(db, p, task, plan):
        assert db is state.db and p is state.p and task is caller_task and plan is caller_plan
        state.calls.append('actual_current_resolver'); f.sources.need(state.allowed)
        return resolved  # All nested mutation must be prevented by the thin frame.
    def fields(db, p, task, actual_release, originals, *, live):
        assert db is state.db and p is state.p and task is actual and actual_release is release and originals is original and live is True
        state.calls.append('actual_field_IO'); return pins
    def capabilities(db, p, task, originals, specs, actual_fields, actual_package, *, live):
        assert db is state.db and p is state.p and task is actual and originals is original and live is True
        assert specs == pure.candidate_specs and actual_fields is pins['fields'] and actual_package is pins['package']
        state.calls.append('actual_capability_IO'); return pure.capability_pins['capabilities'], pure.capability_pins['policies']
    def facts(db, p, originals, actual_fields):
        assert db is state.db and p is state.p and originals is original and actual_fields is pins['fields']
        state.calls.append('actual_facts_IO'); return pure.facts, pure.observation_sources, pure.conflicts
    monkeypatch.setattr(f.analysis, 'resolve_current', resolve)
    monkeypatch.setattr(f.original, '_field_pins', fields)
    monkeypatch.setattr(f.original, '_capability_pins', capabilities)
    monkeypatch.setattr(f.original, '_facts', facts)
    monkeypatch.setattr(f.original, '_identity', lambda *args, **kwargs: pytest.fail('New Plan impersonated an old activation'))
    monkeypatch.setattr(f.original, 'build_frame', lambda *args, **kwargs: pytest.fail('New Plan invoked the old runtime frame entry'))
    return state


def build(value): return f.build_current(value.db, value.p, value.task, value.plan)


def test_current_business_resolver_precedes_all_proofs_and_new_scope_uses_real_eval_and_evidence(scene):
    x = scene; result = build(x)
    assert x.calls == ['actual_current_resolver', 'actual_field_IO', 'actual_capability_IO', 'actual_facts_IO']
    assert result['source_record'] == x.resolved['source_record']
    decision = result['decision_input']
    assert decision['scope'] == {'goal_snapshot_hash': x.pins['goal_snapshot_hash'], 'entity_ids': [uid(1)]}
    assert decision['sources'][0]['kind'] == 'packet'
    assert decision['sources'][0]['source_id'] == x.resolved['source_record']['mature_head']['evaluation_ref']['id']
    document = decision['sources'][1]; original = x.resolved['document']
    assert document == {'source_ref': 'document_evidence', 'kind': 'evidence',
        'source_id': original['record']['evidence_ref']['evidence_id'], 'content_hash': original['record']['evidence_ref']['sha256'],
        'text': original['content'].decode('utf-8'), 'readability': 'partial'}
    assert result['question'] == x.resolved['frame_inputs']['question']
    assert result['instruction'] == x.resolved['preparation']['instruction']
    assert result['model_pin'] == x.resolved['preparation']['model_pin']
    assert result['evidence_ids'] == [x.resolved['source_record']['data_evidence_ref']['evidence_id'], document['source_id']]
    assert set(result['capability_pins']) == {'schema', 'package', 'release', 'entities', 'fields',
        'object_snapshots_hash', 'goal_snapshot_hash', 'capabilities', 'policies', 'pins_hash'}
    assert not {'scope', 'activation_id', 'task_id', 'job_id'} & result['capability_pins'].keys()
    assert result['capability_pins']['pins_hash'] == digest({key: value for key, value in result['capability_pins'].items() if key != 'pins_hash'})
    f.c._check_decision(decision)


def test_business_actor_authority_is_never_replaced_by_a_data_role_or_a_local_fallback(scene):
    x = scene; x.allowed = False
    with pytest.raises(HTTPException): build(x)
    assert x.calls == ['actual_current_resolver']


def test_all_returned_values_and_composed_facts_are_detached_from_resolver_and_proof_inputs(scene):
    x = scene; before = copy.deepcopy((x.resolved['source_record'], x.resolved['preparation'], x.pins,
        x.pure.facts, x.pure.candidate_specs, x.pure.capability_pins))
    result = build(x)
    result['source_record']['document']['evidence_ref']['sha256'] = 'f' * 64
    result['decision_input']['facts'][0]['source_refs'][0]['quote'] = 'changed result'
    result['capability_pins']['capabilities'][0]['data']['execution']['side_effect'] = False
    result['model_pin']['id'] = uid(999)
    assert before == (x.resolved['source_record'], x.resolved['preparation'], x.pins,
        x.pure.facts, x.pure.candidate_specs, x.pure.capability_pins)


@pytest.mark.parametrize('fault', ['invalid_quote', 'conflict_unknown_ref', 'invalid_port_action', 'duplicate_candidate', 'bad_field_hash', 'extra_old_scope'])
def test_thin_frame_preserves_real_pure_quote_mapping_reference_and_pin_contracts(scene, fault):
    x = scene
    if fault == 'invalid_quote': x.pure.facts[0]['source_refs'][0]['quote'] = 'not original text'
    elif fault == 'conflict_unknown_ref': x.pure.conflicts.append({'conflict_ref': 'conflict_1', 'fact_refs': ['fact_1', 'unknown'], 'reason': 'invalid'})
    elif fault == 'invalid_port_action': x.pure.capability_pins['capabilities'][0]['action_kind'] = 'operational_action'
    elif fault == 'duplicate_candidate':
        x.resolved['frame_inputs']['candidate_specs'][1]['candidate_ref'] = x.resolved['frame_inputs']['candidate_specs'][0]['candidate_ref']
    elif fault == 'bad_field_hash': x.pins['goal_snapshot_hash'] = 'f' * 64
    else: x.pins['scope'] = {'activation_id': 'must-not-be-borrowed'}
    with pytest.raises(HTTPException): build(x)


@pytest.mark.parametrize('fault', ['wrong_eval', 'wrong_task', 'wrong_release', 'wrong_document', 'bad_bytes', 'bad_data_text', 'non_utf8'])
def test_resolver_dto_must_keep_real_source_and_byte_identity_even_after_the_authentication_boundary(scene, fault):
    x = scene
    if fault == 'wrong_eval': x.resolved['original_sources']['data']['packet_id'] = uid(999)
    elif fault == 'wrong_task': x.resolved['original_sources']['task_id'] = uid(999)
    elif fault == 'wrong_release': x.resolved['original_sources']['release_id'] = uid(999)
    elif fault == 'wrong_document': x.resolved['document']['record']['evidence_ref']['evidence_id'] = uid(999)
    elif fault == 'bad_bytes': x.resolved['document']['content'] += b'changed'
    elif fault == 'bad_data_text': x.resolved['original_sources']['data']['text'] += 'changed'
    else:
        x.resolved['document']['content'] = b'\xff'
        ref = x.resolved['source_record']['document']['evidence_ref']
        ref.update(sha256=hashlib.sha256(b'\xff').hexdigest(), size_bytes=1)
        x.resolved['source_record']['record_hash'] = f.c.record_hash(x.resolved['source_record'])
        x.resolved['document']['record'] = copy.deepcopy(x.resolved['source_record']['document'])
    with pytest.raises(HTTPException): build(x)


def test_missing_original_fact_unit_is_not_filled_from_the_requirement_or_turned_into_a_grant(scene):
    x = scene; x.pure.facts[0]['unit'] = None
    decision = build(x)['decision_input']
    assert decision['requirements'][0]['unit'] == 'Cel' and decision['facts'][0]['unit'] is None
    assert decision['known_conflicts'] == []
    assert not {'permissions_verified', 'can_dispatch', 'goal_completion_verified'} & decision.keys()


@pytest.mark.parametrize('unit', ['Cel', None])
def test_conflicts_require_comparable_original_units_and_never_become_a_new_scope_grant(scene, unit):
    x = scene; x.pure.facts[0]['unit'] = unit
    second = copy.deepcopy(x.pure.facts[0]); second.update(fact_ref='fact_2', value=28)
    x.pure.facts.append(second)
    x.pure.conflicts.append({'conflict_ref': 'conflict_1', 'fact_refs': ['fact_1', 'fact_2'],
        'reason': 'Unresolved original scalar conflict, not a new conclusion.'})
    if unit is None:
        # The real numeric conflict contract rejects unknown units even when
        # the two fixture values share the same timestamp and field.
        with pytest.raises(HTTPException): build(x)
        return
    decision = build(x)['decision_input']
    assert decision['requirements'][0]['unit'] == decision['facts'][0]['unit'] == 'Cel'
    assert decision['known_conflicts'] == x.pure.conflicts
    assert not {'permissions_verified', 'can_dispatch', 'goal_completion_verified'} & decision.keys()
