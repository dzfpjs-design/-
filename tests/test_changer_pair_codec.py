"""Candidate subprocess policy and truthful output boundaries in isolated tests."""
import json
import os
import sys
from pathlib import Path

import pytest

from generic_agents import changer_pair_codec as codec
from generic_agents.config import settings


def test_candidate_is_local_only_and_not_published_for_business():
    assert codec.RECIPE['default_deployment'] == 'local'
    assert codec.RECIPE['cloud_fallback'] is False
    assert codec.RECIPE['network'] is False
    assert codec.RECIPE['enabled_for_business'] is False
    assert codec.RECIPE['upstream_numerical_equivalence_verified'] is False


def test_invalid_probability_or_old_directory_fails_before_loading_or_alignment(monkeypatch, tmp_path):
    def forbidden():
        raise AssertionError('must reject before preparing native dependencies')
    monkeypatch.setattr(codec, 'ready', forbidden)
    for threshold in (None, True, 0, 1, 30, float('nan'), float('inf')):
        with pytest.raises(ValueError):
            codec._call('a', 'b', {'probability_threshold': threshold}, tmp_path, lambda: None)
    (tmp_path / 'old').write_bytes(b'old result')
    with pytest.raises(ValueError):
        codec._call('a', 'b', {'probability_threshold': .5}, tmp_path, lambda: None)
    assert (tmp_path / 'old').read_bytes() == b'old result'


def test_subprocess_receipt_is_bounded_json_not_raw_error_output(tmp_path):
    env = {'PATH': os.environ['PATH']}
    result = codec._run([sys.executable, '-c', 'print("{\\"cloud_calls\\":0}")'], tmp_path, env, lambda: None, 5, 'ok')
    assert result == {'cloud_calls': 0}
    for text in ('print("x"*70000)', 'raise RuntimeError("private token must not be returned")'):
        with pytest.raises(ValueError) as error:
            codec._run([sys.executable, '-c', text], tmp_path, env, lambda: None, 5, 'bad')
        assert 'private token' not in str(error.value)


@pytest.mark.parametrize('termination', ['cancel', 'timeout'])
def test_live_process_is_reaped_after_cancellation_or_timeout(monkeypatch, tmp_path, termination):
    children = []
    original = codec.subprocess.Popen
    def record(*args, **kwargs):
        child = original(*args, **kwargs); children.append(child); return child
    monkeypatch.setattr(codec.subprocess, 'Popen', record)
    def guard():
        if termination == 'cancel':
            raise ValueError('cancelled')
    with pytest.raises(ValueError):
        codec._run([sys.executable, '-c', 'import time;time.sleep(10)'], tmp_path,
                   {'PATH': os.environ['PATH']}, guard, 0 if termination == 'timeout' else 5, 'waiting')
    assert len(children) == 1 and children[0].poll() is not None


def test_changed_policy_is_rejected_before_native_execution(monkeypatch):
    monkeypatch.setitem(codec.RECIPE, 'cloud_fallback', True)
    with pytest.raises(ValueError, match='local deployment policy'):
        codec.ready()


def test_changed_pinned_code_or_missing_weights_are_not_silently_accepted(monkeypatch, tmp_path):
    monkeypatch.setattr(codec, 'align_ready', lambda identity: None)
    monkeypatch.setattr(settings(), 'specialist_python', Path(sys.executable))
    original = codec.file_digest
    monkeypatch.setattr(codec, 'file_digest', lambda path: 'changed')
    with pytest.raises(ValueError):
        codec.ready()
    monkeypatch.setattr(codec, 'file_digest', original)
    monkeypatch.setattr(codec, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='不转云'):
        codec.ready()
