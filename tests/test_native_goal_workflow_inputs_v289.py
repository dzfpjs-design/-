"""Assessment source proof and final acceptance are separate, mandatory gates.

Source rows/IAM are explicit spies. These pure tests do not establish a real
V288 evaluation, successful Goal Job, approval or durable Task workflow.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import native_goals as subject, native_goal_tools
from generic_agents import goal_evaluation_provider_v288 as provider
from generic_agents import native_pixel_receipts_v250, workflow_sound
from generic_agents import native_pixel_provider_v250, instance_compiler
from generic_agents import workflow_images, workflow_scenes, workflow_audio
from generic_agents import workflow_geo_pairs, workspace_api
from generic_agents import workflow_graph_goal_tools_v126 as graph_tools
from generic_agents.contracts import digest
from generic_agents.models import Capability, Invocation
from tests.test_workflow_graph_goal_tools_v126 import fixture as graph_fixture


PROOFS = [
    (native_pixel_receipts_v250, 'require_task_received'),
    (workflow_sound, 'require_task_received'),
    (native_pixel_provider_v250, 'verify_task_humans'),
    (instance_compiler, 'verify_material_snapshots'),
    (workflow_images, 'verify'), (workflow_images, 'verify_masks'),
    (workflow_scenes, 'verify_masks'), (workflow_audio, 'verify_receipts'),
    (workflow_geo_pairs, 'verify_receipts'), (workspace_api, 'verify_perception_evidence'),
]


@pytest.fixture
def sources(monkeypatch):
    db, p = object(), object()
    cap = NS(id='original-capability', data={'frozen': 'original-contract'})
    cap.content_hash = digest(cap.data)
    inputs, output = {'original': 'frozen-input'}, {'value': 1}
    receipt = {'execution_mode': 'registered_ability', 'capability_id': cap.id,
        'capability_hash': cap.content_hash, 'invocation_id': 'original-invocation',
        'input_snapshot': inputs, 'input_hash': digest(inputs), 'output': output}
    call = NS(id=receipt['invocation_id'], status='succeeded', capability_id=cap.id,
        data={'capability_hash': cap.content_hash, 'input_hash': digest(inputs),
            'output': copy.deepcopy(output)})
    task = NS(data={'workflow_runtime': {'receipts': [receipt]}})
    checks, lookups = [], []

    def scoped(actual_db, model, identity, actor):
        assert actual_db is db and actor is p
        lookups.append((model, identity))
        result = call if model is Invocation else cap
        assert model in {Invocation, Capability} and result.id == identity
        return result

    monkeypatch.setattr(subject, 'scoped', scoped)
    monkeypatch.setattr(provider, 'require_current', lambda *a, **kw:
        pytest.fail('internal source proof demanded its own completed Goal evaluation'))
    for module, name in PROOFS:
        label = module.__name__ + '.' + name

        def proof(*args, _label=label, **kwargs):
            assert args[0] is db and args[1] is p
            checks.append((_label, kwargs))

        monkeypatch.setattr(module, name, proof)
    return NS(db=db, p=p, task=task, cap=cap, call=call, receipt=receipt,
        checks=checks, lookups=lookups)


@pytest.mark.parametrize('lock_sources', [True, False])
def test_internal_source_proof_keeps_all_original_checks_without_requiring_evaluation(sources, lock_sources):
    s = sources; before = copy.deepcopy(s.task.data)
    subject.verify_workflow_inputs(s.db, s.p, s.task, lock_sources=lock_sources)
    assert [label for label, _ in s.checks] == [m.__name__ + '.' + n for m, n in PROOFS]
    assert s.checks[-1][1] == {'lock_sources': lock_sources}
    assert s.lookups == [(Invocation, s.call.id), (Capability, s.cap.id)]
    assert s.task.data == before


@pytest.mark.parametrize('proof_index', range(len(PROOFS)))
def test_each_original_source_proof_remains_mandatory(sources, monkeypatch, proof_index):
    s = sources

    def reject(*args, **kwargs): raise HTTPException(409, 'actual source proof rejected')

    module, name = PROOFS[proof_index]
    monkeypatch.setattr(module, name, reject)
    with pytest.raises(HTTPException): subject.verify_workflow_inputs(s.db, s.p, s.task)
    assert len(s.checks) == proof_index and s.lookups == []


@pytest.mark.parametrize('damage', ['call_status', 'capability', 'contract', 'call_hash',
    'input_snapshot', 'input_hash', 'call_input_hash', 'output', 'material_snapshot'])
def test_internal_proof_still_rejects_changed_frozen_inputs_or_call_receipts(sources, damage):
    s = sources
    if damage == 'call_status': s.call.status = 'running'
    elif damage == 'capability': s.call.capability_id = 'another-capability'
    elif damage == 'contract': s.cap.data['frozen'] = 'changed-contract'
    elif damage == 'call_hash': s.call.data['capability_hash'] = 'another-hash'
    elif damage == 'input_snapshot': s.receipt['input_snapshot']['original'] = 'changed-input'
    elif damage == 'input_hash': s.receipt['input_hash'] = 'another-hash'
    elif damage == 'call_input_hash': s.call.data['input_hash'] = 'another-hash'
    elif damage == 'output': s.call.data['output'] = {'value': 2}
    elif damage == 'material_snapshot': s.receipt['material_snapshots'] = [{'changed': True}]
    before = copy.deepcopy(s.task.data)
    with pytest.raises(HTTPException): subject.verify_workflow_inputs(s.db, s.p, s.task)
    assert s.task.data == before


@pytest.mark.parametrize('lock_sources', [True, False])
def test_final_gate_always_requires_current_evaluation_then_same_source_proof(monkeypatch, lock_sources):
    db, p, task = object(), object(), object(); calls = []

    def evaluation(actual_db, actor, original, **kwargs):
        assert (actual_db, actor, original) == (db, p, task)
        calls.append(('current evaluation', kwargs))

    def inputs(actual_db, actor, original, **kwargs):
        assert (actual_db, actor, original) == (db, p, task)
        calls.append(('original source proof', kwargs))

    monkeypatch.setattr(provider, 'require_current', evaluation)
    monkeypatch.setattr(subject, 'verify_workflow_inputs', inputs)
    subject.verify_acceptance_inputs(db, p, task, lock_sources=lock_sources)
    assert calls == [('current evaluation', {'lock_sources': lock_sources}),
        ('original source proof', {'lock_sources': lock_sources})]


@pytest.mark.parametrize('state', ['bootstrap has no evaluation', 'own Goal Job still running', 'stale evaluation'])
def test_final_gate_rejects_unmature_or_stale_evaluation_without_source_bypass(monkeypatch, state):
    def reject(*args, **kwargs): raise HTTPException(409, state)
    monkeypatch.setattr(provider, 'require_current', reject)
    monkeypatch.setattr(subject, 'verify_workflow_inputs', lambda *a, **kw:
        pytest.fail('failed current evaluation was ignored'))
    with pytest.raises(HTTPException) as error: subject.verify_acceptance_inputs(None, None, None)
    assert error.value.detail == state


def test_final_gate_also_rejects_failed_source_proof_after_mature_evaluation(monkeypatch):
    calls = []
    monkeypatch.setattr(provider, 'require_current', lambda *a, **kw: calls.append('current evaluation'))

    def reject(*args, **kwargs): raise HTTPException(409, 'changed original source')

    monkeypatch.setattr(subject, 'verify_workflow_inputs', reject)
    with pytest.raises(HTTPException): subject.verify_acceptance_inputs(None, None, None)
    assert calls == ['current evaluation']


@pytest.mark.parametrize('phase', ['bootstrap', 'evaluation Job running'])
def test_real_graph_receipt_enumerator_uses_source_gate_without_final_acceptance_cycle(monkeypatch, phase):
    s = graph_fixture(monkeypatch)
    s.task.data['native_goal_check_phase_fixture'] = phase
    seen = []
    monkeypatch.setattr(subject, 'verify_workflow_inputs', lambda *a, **kw: seen.append(kw))
    monkeypatch.setattr(subject, 'verify_acceptance_inputs', lambda *a, **kw:
        pytest.fail('receipt enumeration recursively demanded final acceptance'))
    monkeypatch.setattr(provider, 'require_current', lambda *a, **kw:
        pytest.fail('receipt enumeration demanded its own completed Goal evaluation'))
    before = copy.deepcopy(s.task.data)
    assert len(graph_tools.receipts(s.db, s.p, s.task, lock_sources=False)) == 2
    assert seen == [{'lock_sources': False}] and s.task.data == before


def test_legacy_receipt_enumerator_reaches_independent_source_gate(monkeypatch):
    class SourceBoundary(Exception): pass
    task = NS(id='original-task', data={'goal_spec': {'criteria': [
        {'kind': 'capability_success', 'capability_id': 'capability'}]},
        'workflow_runtime': {'receipts': [{'execution_mode': 'registered_ability',
            'capability_id': 'capability'}]}})
    monkeypatch.setattr(workspace_api, 'saved_execution', lambda *a: (task, object(), {}))
    monkeypatch.setattr(native_goal_tools, 'catalog', lambda *a: {})
    seen = []

    def source(*args, **kwargs):
        seen.append(kwargs); raise SourceBoundary

    monkeypatch.setattr(subject, 'verify_workflow_inputs', source)
    monkeypatch.setattr(subject, 'verify_acceptance_inputs', lambda *a, **kw:
        pytest.fail('legacy receipt enumeration demanded final acceptance'))
    with pytest.raises(SourceBoundary):
        native_goal_tools.workflow_goal_receipts(None, None, task, lock_sources=False)
    assert seen == [{'lock_sources': False}]
