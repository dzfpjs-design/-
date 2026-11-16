"""CLI scopes cannot accidentally select a global or different worker."""
import sys
from uuid import uuid4
import pytest
from generic_agents import cli, http_observation_task_consumer_v270 as consumer


@pytest.mark.parametrize('arguments', [[], ['--workspace-id', str(uuid4())],
    ['--workspace-id', str(uuid4()), '--instance-id', str(uuid4()), '--task-id', str(uuid4()),
     '--family', 'graph', '--consumer-mode', 'jobs']])
def test_missing_or_wrong_scope_never_starts_a_consumer(monkeypatch, arguments):
    monkeypatch.setattr(sys, 'argv', ['ga', 'http-observation-task-worker', *arguments])
    monkeypatch.setattr(consumer, 'run', lambda *a, **kw: pytest.fail('invalid scope started a worker'))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize('mode', ['jobs', 'receipts'])
def test_only_explicit_task_scope_is_passed_to_the_selected_independent_role(monkeypatch, mode):
    workspace, instance, task = (str(uuid4()) for _ in range(3))
    captured = []
    monkeypatch.setattr(consumer, 'run', lambda *a, **kw: captured.append((a, kw)) or 0)
    monkeypatch.setattr(sys, 'argv', ['ga', 'http-observation-task-worker', '--workspace-id', workspace,
        '--instance-id', instance, '--task-id', task, '--family', 'http_mapped_v177', '--consumer-mode', mode])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 0
    assert captured == [((workspace, instance, task), {'family': 'http_mapped_v177', 'mode': mode})]
