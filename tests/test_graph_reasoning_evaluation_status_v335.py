from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import graph_reasoning_evaluation_status_v335 as subject


@pytest.fixture
def state(monkeypatch):
    identity = 'reasoning-activation'
    inputs = {'data_packet': {'evidence_id': 'frozen-original'}, 'document_packet': {'evidence_id': 'document'}}
    activation = {'id': identity, 'node_id': 'reasoning', 'inputs_snapshot': {'arguments': inputs}}
    state = {'activations': {identity: activation}}
    task = SimpleNamespace(id='task', data={'graph_runtime': state, 'graph_jobs': {identity: {'job_id': 'job'}}})
    release = SimpleNamespace(id='release')
    node = {'execution': {'adapter': 'business_goal_ready_v288'}}
    job = SimpleNamespace(data={'inputs': inputs})
    original = {'evaluation_id': 'eval-1', 'record_hash': 'hash-1', 'cut_at': '2026-11-03T01:00:00Z'}
    head = {'evaluation_ref': {'id': 'eval-1', 'record_hash': 'hash-1'}}
    value = {'cut_at': '2026-11-03T01:00:00Z'}
    env = SimpleNamespace(task=task, release=release, business={}, graph={}, state=state, node=node,
                          job=job, original=original, head=head, value=value, freshness='current')
    monkeypatch.setattr(subject.sources, '_reader_iam', lambda *_: None)
    monkeypatch.setattr(subject.workflow, 'business_binding', lambda *_: {'schema': subject.BINDING})
    def pure(fn, *args):
        return env.state if fn is subject.workflow.graph_api.check_run else inputs
    monkeypatch.setattr(subject.workflow, 'invoke_pure', pure)
    monkeypatch.setattr(subject.scope, '_frozen', lambda *_: (task, release, env.business, env.graph, env.state, activation, env.node))
    monkeypatch.setattr(subject, 'scoped', lambda *_: env.job)
    monkeypatch.setattr(subject.scope, '_job_matches', lambda *_: None)
    monkeypatch.setattr(subject.sources, '_inputs', lambda value: value)
    monkeypatch.setattr(subject.sources, '_data', lambda *_args, **_kwargs: (release, 'package', {'evaluation_record': env.original}))
    monkeypatch.setattr(subject.provider, '_head', lambda *_: env.head)
    monkeypatch.setattr(subject, '_checked_mature_report', lambda *_: None)
    monkeypatch.setattr(subject.provider, 'binding', lambda *_: {'release': release})
    from datetime import datetime, timezone
    monkeypatch.setattr(subject.provider, '_record', lambda *_args, **_kwargs: ('row', env.value, datetime(2026, 11, 3, tzinfo=timezone.utc)))
    monkeypatch.setattr(subject.provider, '_evidence', lambda *_: {subject.provider.VIEW: {'state': env.freshness}})
    return env


def read(s):
    return subject.project(object(), object(), s.task, s.release, s.business, s.graph)


@pytest.mark.parametrize(('changed', 'freshness', 'expected'), [
    (False, 'current', 'current'), (False, 'stale', 'expired'),
    (True, 'current', 'superseded'), (True, 'stale', 'superseded'),
])
def test_frozen_evaluation_and_verified_head_are_compared_without_mutation(state, changed, freshness, expected):
    if changed:
        state.head['evaluation_ref'] = {'id': 'eval-2', 'record_hash': 'hash-2'}
        state.value['cut_at'] = '2026-11-03T02:00:00Z'
    state.freshness = freshness
    before = deepcopy((state.task.data, state.original, state.head, state.value))
    result = read(state)
    assert result['status'] == expected and result['activation_id'] == 'reasoning-activation'
    assert result['current_freshness'] == freshness
    assert 'id' not in result and 'record_hash' not in result
    assert before == (state.task.data, state.original, state.head, state.value)


def test_unverified_or_unavailable_provenance_is_never_reported_current(state, monkeypatch):
    def broken(*_args, **_kwargs):
        raise HTTPException(409, 'original source cannot be verified')
    monkeypatch.setattr(subject.sources, '_data', broken)
    result = read(state)
    assert result['status'] == 'unknown' and result['evaluated_at'] is None


def test_an_input_mismatch_is_not_mistaken_for_a_newer_evaluation(state):
    state.job.data = {'inputs': {'data_packet': {'evidence_id': 'other'}}}
    assert read(state)['status'] == 'unknown'


def test_unauthorized_actor_is_not_hidden_as_an_unknown_state(state, monkeypatch):
    def denied(*_args):
        raise HTTPException(403, 'not allowed')
    monkeypatch.setattr(subject.sources, '_reader_iam', denied)
    with pytest.raises(HTTPException) as error:
        read(state)
    assert error.value.status_code == 403


def test_unsupported_and_missing_node_do_not_claim_a_running_blocker(state, monkeypatch):
    monkeypatch.setattr(subject.workflow, 'business_binding', lambda *_: {'schema': 'different-family'})
    assert read(state)['status'] == 'not_applicable'
    monkeypatch.setattr(subject.workflow, 'business_binding', lambda *_: {'schema': subject.BINDING})
    state.state['activations'].clear()
    assert read(state)['status'] == 'not_applicable'


def test_multiple_activations_are_unknown_instead_of_picking_one(state):
    state.state['activations']['other'] = {'node_id': 'reasoning'}
    assert read(state)['status'] == 'unknown'


def test_unproven_goal_completion_is_not_reported_as_newer(state, monkeypatch):
    def missing(*_args):
        raise ValueError('goal result not proven')
    state.head['evaluation_ref'] = {'id': 'eval-2', 'record_hash': 'hash-2'}
    monkeypatch.setattr(subject, '_checked_mature_report', missing)
    assert read(state)['status'] == 'unknown'


@pytest.mark.parametrize('damage', [None, 'job_running', 'report_changed', 'wrong_evaluation', 'wrong_head'])
def test_mature_report_checks_the_actual_goal_job_memory_and_hash(monkeypatch, damage):
    ref = {'id': 'evaluation', 'record_hash': 'hash'}
    head = {'goal_check_job_id': 'job', 'evaluation_ref': ref, 'head_hash': 'head'}
    task = SimpleNamespace(id='task')
    report = {'goal_snapshot': {'goal': 'fixed'}, subject.provider.JOB_REF: ref,
              'evidence': {subject.provider.VIEW: {'evaluation_ref': ref}}, 'goal_evaluation_head_hash_v288': 'head'}
    memory = SimpleNamespace(status='candidate', data={'type': 'native_goal_check', 'task_id': 'task',
        'report_hash': subject.digest(report), 'report': report})
    job = SimpleNamespace(status='succeeded', data={'goal_check_id': 'memory'})
    monkeypatch.setattr(subject.provider, '_fresh', lambda _db, _p, model, _id: job if model is subject.Job else memory)
    monkeypatch.setattr(subject.provider, 'snapshot', lambda _task: {'goal': 'fixed'})
    if damage == 'job_running': job.status = 'running'
    if damage == 'report_changed': memory.data['report_hash'] = 'different'
    if damage == 'wrong_evaluation': head['evaluation_ref'] = {'id': 'other', 'record_hash': 'hash'}
    if damage == 'wrong_head': head['head_hash'] = 'other'
    if damage:
        with pytest.raises(ValueError): subject._checked_mature_report(object(), object(), task, head)
    else:
        subject._checked_mature_report(object(), object(), task, head)
