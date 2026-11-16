"""Evaluation carrier boundaries and real Quality assembly; no DB authority.

Reuse V155's explicit structural dependency fixture, then restore the real
_current_grant/fresh_grant. Their approver, resource, cold-evaluation and frontier
callbacks are recorded seams, not proof of those dependencies or actual locks.
Quality sample projection, basis comparison, exclusions, pins and statistics
remain real. Root separately verifies complete persisted cold chains and DTOs.
"""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from generic_agents.models import Job
from generic_agents import quality_review_store_v152 as quality
from generic_agents import threshold_shadow_store_v151 as shadow
from generic_agents import threshold_study_store_v149 as study
from tests.test_quality_feedback_read_once_v155 import (
    ACTOR, EVALUATION, SHADOW, WORKSPACE, EVAL_OBS, SHADOW_OBS, SOURCE,
    evidence_case, identity, original_inputs,
)


REAL_CURRENT = shadow._current_grant
REAL_FRESH = shadow.fresh_grant


def carrier_case(monkeypatch, *, lock=False):
    case = evidence_case(monkeypatch, lock=lock)
    monkeypatch.setattr(shadow, '_current_grant', REAL_CURRENT)
    monkeypatch.setattr(shadow, 'fresh_grant', REAL_FRESH)
    case.grant.workspace_id = WORKSPACE
    case.grant.actor_id = ACTOR
    case.grant.data['scope']['source_id'] = SOURCE
    case.current_scope = deepcopy(case.grant.data['scope'])
    case.failure_stage = None
    case.failure = HTTPException(409, 'original full dependency rejected')
    case.on_frontier = None
    case.job = Job(id=case.evaluation.data['job_id'], workspace_id=WORKSPACE, status='succeeded')
    original_scoped = quality.scoped

    def approver(db, p, actor_id, actual_lock=True):
        assert db is case.db and p is case.p and actor_id == ACTOR
        case.gates.append(('approver', actual_lock))
        if case.failure_stage == 'approver': raise case.failure

    def resources(db, p, scope):
        assert db is case.db and p is case.p and scope is case.grant.data['scope']
        case.gates.append('resources')

    def get_evaluation(db, p, kind, key, actual_lock=False):
        assert db is case.db and p is case.p and kind == 'evaluation' and key == EVALUATION
        assert type(actual_lock) is bool
        case.gates.append(('evaluation', actual_lock))
        return case.evaluation

    def evaluation_context(db, p, key):
        assert db is case.db and p is case.p and key == EVALUATION
        case.gates.append('evaluation-context')
        if case.failure_stage == 'evaluation': raise case.failure
        evaluation = study.get_record(db, p, 'evaluation', key)
        return evaluation, deepcopy(case.current_scope), shadow._pin(evaluation)

    def frontier(db, p, scope):
        assert db is case.db and p is case.p and scope == case.current_scope
        case.gates.append('frontier')
        if case.failure_stage == 'frontier': raise case.failure
        if case.on_frontier is not None: case.on_frontier()
        return {'observation_ids': [], 'source_record_ids': []}

    def scoped(db, model, key, p):
        if model is Job:
            assert db is case.db and p is case.p and key == case.job.id
            case.gates.append('job')
            return case.job
        return original_scoped(db, model, key, p)

    monkeypatch.setattr(shadow, '_approver', approver)
    monkeypatch.setattr(shadow, 'lock_resources', resources)
    monkeypatch.setattr(shadow, 'evaluation_context', evaluation_context)
    monkeypatch.setattr(shadow, 'frontier', frontier)
    monkeypatch.setattr(study, 'get_record', get_evaluation)
    monkeypatch.setattr(quality, 'scoped', scoped)
    return case


def denied(action):
    with pytest.raises(HTTPException) as error:
        action()
    assert error.value.status_code == 409


@pytest.mark.parametrize('lock', [False, True])
def test_default_and_locked_authority_keep_original_grant_return_and_all_callbacks(monkeypatch, lock):
    case = carrier_case(monkeypatch)
    result = (shadow.fresh_grant(case.db, case.p, case.grant) if lock else
        shadow._current_grant(case.db, case.p, case.grant, False))
    assert result is case.grant
    assert case.gates == [('approver', lock), *(['resources'] if lock else []),
        'evaluation-context', ('evaluation', False), 'frontier']


def test_readonly_carrier_returns_the_actual_cold_objects_after_full_callbacks(monkeypatch):
    case = carrier_case(monkeypatch)
    before = original_inputs(case)
    result = shadow._current_grant(case.db, case.p, case.grant, False, _return_evaluation=True)
    assert type(result) is tuple and len(result) == 2
    assert result[0] is case.grant and result[1] is case.evaluation
    assert case.gates == [('approver', False), 'evaluation-context', ('evaluation', False), 'frontier']
    assert original_inputs(case) == before


@pytest.mark.parametrize('value', [None, 0, 1, 'true'], ids=['none', 'zero', 'one', 'string'])
def test_carrier_flag_is_an_exact_boolean_before_any_dependency(monkeypatch, value):
    case = carrier_case(monkeypatch)
    denied(lambda: shadow._current_grant(case.db, case.p, case.grant, False, _return_evaluation=value))
    assert case.gates == []


@pytest.mark.parametrize('lock,context', [(True, None), (0, None), (False, {}), (False, [])],
    ids=['locked', 'integer-lock', 'list-cache', 'invalid-cache'])
def test_carrier_never_accepts_locked_or_cached_read_contexts(monkeypatch, lock, context):
    case = carrier_case(monkeypatch)
    denied(lambda: shadow._current_grant(case.db, case.p, case.grant, lock,
        context, _return_evaluation=True))
    assert case.gates == []


@pytest.mark.parametrize('stage', ['approver', 'evaluation', 'frontier'])
def test_return_mode_does_not_skip_or_swallow_any_complete_dependency(monkeypatch, stage):
    case = carrier_case(monkeypatch)
    case.failure_stage = stage
    with pytest.raises(HTTPException) as error:
        shadow._current_grant(case.db, case.p, case.grant, False, _return_evaluation=True)
    assert error.value is case.failure
    expected = [('approver', False), 'evaluation-context', ('evaluation', False), 'frontier']
    stop = {'approver': 1, 'evaluation': 2, 'frontier': 4}[stage]
    assert case.gates == expected[:stop]


@pytest.mark.parametrize('fault', ['saved-result-pin', 'saved-source-scope'])
def test_carrier_keeps_actual_saved_pin_and_scope_comparisons(monkeypatch, fault):
    case = carrier_case(monkeypatch)
    if fault == 'saved-result-pin': case.grant.data['evaluation']['result_hash'] = 'f' * 64
    else: case.grant.data['scope']['source_id'] = identity(90)
    denied(lambda: shadow._current_grant(case.db, case.p, case.grant, False, _return_evaluation=True))
    assert 'frontier' not in case.gates


@pytest.mark.parametrize('lock', [False, True])
def test_quality_assembles_real_samples_pins_and_statistics_with_original_lock_order(monkeypatch, lock):
    case = carrier_case(monkeypatch, lock=lock)
    before = original_inputs(case)
    result = quality.evidence_context(case.db, case.p, EVALUATION, SHADOW, lock=lock)
    assert result['evaluation'] is case.evaluation and result['shadow'] is case.grant
    assert result['context']['evaluation'] == shadow._pin(case.evaluation)
    expected = ([('evaluation', True), 'job', 'shadow', ('approver', True), 'resources',
        'evaluation-context', ('evaluation', False), 'frontier', 'consumption-inventory'] if lock else
        ['shadow', ('approver', False), 'evaluation-context', ('evaluation', False),
         'frontier', 'job', 'consumption-inventory'])
    assert case.gates == expected
    assert len([item for item in case.gates if type(item) is tuple and item[0] == 'evaluation']) == (2 if lock else 1)
    assert [item['observation_id'] for item in result['context']['samples']] == [EVAL_OBS, SHADOW_OBS]
    chosen = [case.chains[EVAL_OBS][0], case.chains[SHADOW_OBS][1]]
    assert [item['reason'] for item in result['context']['samples']] == [item.data['reason'] for item in chosen]
    assert len(case.checks) == 2 and result['issues'] == []
    assert result['statistics']['evaluation']['baseline']['total'] == 1
    assert result['statistics']['shadow']['candidate']['total'] == 1
    assert original_inputs(case) == before


@pytest.mark.parametrize('fault', ['evaluation-id', 'evaluation-workspace', 'evaluation-status',
    'evaluation-result', 'job-status', 'shadow-status'])
def test_quality_still_checks_its_own_returned_identity_completion_and_job(monkeypatch, fault):
    case = carrier_case(monkeypatch)
    if fault == 'evaluation-id':
        case.evaluation.id = identity(91)
        case.grant.data['evaluation'] = shadow._pin(case.evaluation)
    elif fault == 'evaluation-workspace': case.evaluation.workspace_id = identity(92)
    elif fault == 'evaluation-status': case.evaluation.status = 'queued'
    elif fault == 'evaluation-result':
        case.evaluation.data['result'] = None
        case.grant.data['evaluation'] = shadow._pin(case.evaluation)
    elif fault == 'job-status': case.job.status = 'failed'
    else: case.grant.status = 'active'
    denied(lambda: quality.evidence_context(case.db, case.p, EVALUATION, SHADOW))
    assert 'consumption-inventory' not in case.gates and case.checks == []


def test_quality_recomputes_the_pin_after_a_frontier_read_refreshes_the_actual_evaluation(monkeypatch):
    case = carrier_case(monkeypatch)
    saved_pin = deepcopy(case.grant.data['evaluation'])

    def refreshed():
        case.evaluation.data = deepcopy(case.evaluation.data)
        case.evaluation.data['result']['per_sample'][0]['candidate_matched'] = False

    case.on_frontier = refreshed
    denied(lambda: quality.evidence_context(case.db, case.p, EVALUATION, SHADOW))
    assert case.grant.data['evaluation'] == saved_pin
    assert shadow._pin(case.evaluation) != saved_pin
    assert 'frontier' in case.gates and 'consumption-inventory' not in case.gates
    assert case.checks == []
