"""Scope/dispatch contracts only; real source, authority and cache are PG tests."""
import copy
import sys
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import cli
from generic_agents import state_projection_consumer_v266 as subject
from generic_agents.contracts import digest

W = '1a0bc001-a462-42da-b822-a89e541bdc8d'
I = '2a0bc001-a462-42da-b822-a89e541bdc8d'
E = '3a0bc001-a462-42da-b822-a89e541bdc8d'
S = '4a0bc001-a462-42da-b822-a89e541bdc8d'
O = '5a0bc001-a462-42da-b822-a89e541bdc8d'
J = '6a0bc001-a462-42da-b822-a89e541bdc8d'
A = '7a0bc001-a462-42da-b822-a89e541bdc8d'
R = '8a0bc001-a462-42da-b822-a89e541bdc8d'


@pytest.fixture(autouse=True)
def no_real_io(monkeypatch):
    def denied(*a, **k): pytest.fail('pure scope contract attempted a real database or dispatcher')
    for module in (cli, subject):
        monkeypatch.setattr(module, 'engine', denied)
        monkeypatch.setattr(module, 'Session', denied)


@pytest.mark.parametrize('args', [[], ['--workspace-id', W], ['--instance-id', I]])
def test_cli_requires_both_instance_and_workspace_before_any_action(monkeypatch, args):
    monkeypatch.setattr(sys, 'argv', ['ga', 'state-projection-worker', *args])
    monkeypatch.setattr(subject, 'run', lambda *a, **k: pytest.fail('unscoped projection dispatch'))
    with pytest.raises(SystemExit) as error: cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize('filters', [False, True])
def test_cli_instance_default_and_optional_filters_reach_only_projection_entry(monkeypatch, filters):
    args = ['ga', 'state-projection-worker', '--workspace-id', W, '--instance-id', I]
    expected = {'entity_id': None, 'source_id': None, 'observation_id': None, 'job_id': None}
    if filters:
        args += ['--entity-id', E, '--source-id', S, '--observation-id', O, '--projection-job-id', J]
        expected = {'entity_id': E, 'source_id': S, 'observation_id': O, 'job_id': J}
    calls = []
    monkeypatch.setattr(sys, 'argv', args)
    monkeypatch.setattr(subject, 'run', lambda *a, **k: calls.append((a, k)) or 0)
    with pytest.raises(SystemExit) as error: cli.main()
    assert error.value.code == 0 and calls == [((W, I), expected)]


@pytest.mark.parametrize('command,args', [
    ('worker', ['--source-id', S]),
    ('setup-worker', ['--workspace-id', W, '--projection-job-id', J]),
    ('workflow-task-worker', ['--workspace-id', W, '--instance-id', I, '--family', 'compact', '--entity-id', E]),
    ('state-projection-worker', ['--workspace-id', W, '--instance-id', I, '--family', 'graph']),
    ('state-projection-worker', ['--workspace-id', W, '--instance-id', I, '--task-id', O]),
])
def test_projection_flags_cannot_expand_or_replace_other_worker_contracts(monkeypatch, command, args):
    monkeypatch.setattr(sys, 'argv', ['ga', command, *args])
    with pytest.raises(SystemExit) as error: cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize('change', [
    {'workspace_id': None}, {'instance_id': I.upper()}, {'entity_id': True},
    {'source_id': S.replace('-', '')}, {'observation_id': '../' + O},
    {'job_id': 'not-a-job'}, {'limit': True}, {'limit': 0}, {'limit': 51},
])
def test_direct_scope_is_canonical_and_never_global_or_coerced(change):
    args = {'workspace_id': W, 'instance_id': I}; args.update(change)
    with pytest.raises(HTTPException): subject.step(**args)


def test_preexisting_stop_does_not_read_claim_receive_or_install_thread_signals(monkeypatch):
    stop = Event(); stop.set()
    monkeypatch.setattr(subject.signal, 'signal', lambda *a: pytest.fail('background signal installation'))
    assert subject.step(W, I, stop_event=stop) == {'processed': False, 'after': None,
        'waiting': [], 'errors': [], 'job_id': None}
    results = []; errors = []
    def background():
        try: results.append(subject.run(W, I, stop_event=stop))
        except BaseException as error: errors.append(error)
    thread = Thread(target=background); thread.start(); thread.join(timeout=5)
    assert not thread.is_alive() and not errors and results == [0]


@pytest.mark.parametrize('nonce', [True, '', 'short', ' leading_nonce_123', 'nonce_with:colon'])
def test_manual_original_key_has_no_fuzzy_or_ambiguous_nonce(nonce):
    with pytest.raises(HTTPException): subject._manual_key(A, nonce)


def request_job():
    request = {'schema': 'local-state-refresh-v1', 'entity_id': E, 'entity_version': 1,
        'instance_id': I, 'release_id': R, 'object_definition_hash': 'a' * 64,
        'authorized_by': A, 'reason': '新登记处理结果已保存', 'trigger_observation_id': O,
        'trigger_observation_version': 1, 'due_state_hash': None}
    return SimpleNamespace(id=J, kind=subject.KIND, workspace_id=W,
        data={'actor_id': A, 'state_request': request, 'state_request_hash': digest(request)})


@pytest.mark.parametrize('fault', ['bool_version', 'unknown_field', 'actor', 'hash', 'wrong_kind'])
def test_corrupted_request_cannot_become_a_valid_projection_by_its_own_hash(fault):
    job = request_job(); original = copy.deepcopy(job.data)
    if fault == 'bool_version': job.data['state_request']['entity_version'] = True
    elif fault == 'unknown_field': job.data['state_request']['send_device_command'] = True
    elif fault == 'actor': job.data['actor_id'] = W
    elif fault == 'hash': job.data['state_request_hash'] = '0' * 64
    else: job.kind = 'dispatch_acquisition'
    if fault not in {'hash', 'actor'}:
        job.data['state_request_hash'] = digest(job.data['state_request'])
    with pytest.raises(HTTPException): subject._request(job)
    # This is a synthetic negative shape test, never independent authority or
    # a successful Job/Source/ACK. The consumer still owes all real cold proof.
    assert original['state_request']['entity_version'] == 1
