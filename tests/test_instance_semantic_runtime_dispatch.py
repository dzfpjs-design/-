"""Synthetic dispatch contracts only; Root executes these tests.

Fake engines/database replies check version selection and refusals, not real
source approval, runtime adoption, a published package or successful execution.
"""
import copy
from types import SimpleNamespace as NS

from fastapi import HTTPException
import pytest

from generic_agents import instance_semantic_runtime_dispatch as dispatch


V2_MARKERS = ('semantic_runtime_plan', 'full_semantic_runtime_schema', 'full_semantic_runtime_hash')
BUSINESS_MARKER = 'full_semantic_runtime_origin'


class FakeDB:
    def __init__(self, *values):
        self.values = iter(values)
        self.calls = 0

    def scalar(self, statement):
        self.calls += 1
        return next(self.values)


def scene(monkeypatch, *, modern_result=None):
    calls = []
    old_result = {'synthetic': 'original v1 verifier result'}
    new_result = {'synthetic': 'original v2 verifier result'} if modern_result is None else modern_result
    def old_verify(*args): calls.append(('legacy_verify', args)); return old_result
    def new_verify(*args): calls.append(('modern_verify', args)); return new_result
    def old_pins(value): calls.append(('legacy_pins', value)); return {'synthetic_old': 'pin'}
    def new_pins(value): calls.append(('modern_pins', value)); return {'synthetic_new': 'pin'}
    engine = NS(MARKERS=V2_MARKERS, BUSINESS_MARKER=BUSINESS_MARKER,
        verify_release=new_verify, pins=new_pins)
    monkeypatch.setattr(dispatch, 'modern', lambda value=None: engine)
    monkeypatch.setattr(dispatch.legacy, 'verify_release', old_verify)
    monkeypatch.setattr(dispatch.legacy, 'pins', old_pins)
    package = NS(data={'specification': {'business_definitions': []}})
    monkeypatch.setattr(dispatch, 'scoped', lambda db, model, identity, p: package)
    p = NS(workspace_id='synthetic-workspace')
    release = NS(package_id='synthetic-package', data={'snapshot': {'businesses': []}, 'configuration_revision': 4})
    return engine, package, p, release, calls, old_result, new_result


def container(release, package, side):
    return release.data['snapshot'] if side == 'snapshot' else release.data if side == 'release' else package.data['specification']


def denied(call):
    with pytest.raises(HTTPException) as error:
        call()
    assert error.value.status_code == 409


def test_unmarked_v1_calls_original_verifier_and_original_pins(monkeypatch):
    _, _, p, release, calls, old, _ = scene(monkeypatch)
    db = FakeDB(None)
    assert dispatch.verify_release(db, p, release) is old
    assert calls == [('legacy_verify', (db, p, release))]
    snapshot = release.data['snapshot']
    assert dispatch.pins(snapshot) == {'synthetic_old': 'pin'}
    assert calls[-1] == ('legacy_pins', snapshot)


@pytest.mark.parametrize('side', ['snapshot', 'release', 'spec'])
@pytest.mark.parametrize('marker', V2_MARKERS)
def test_any_single_side_v2_marker_selects_modern_and_never_legacy(monkeypatch, side, marker):
    _, package, p, release, calls, _, modern = scene(monkeypatch)
    container(release, package, side)[marker] = {} if marker == 'semantic_runtime_plan' else 'synthetic marker'
    before = copy.deepcopy((release.data, package.data))
    db = FakeDB(None)
    assert dispatch.verify_release(db, p, release) is modern
    assert calls == [('modern_verify', (db, p, release))] and db.calls == 1
    assert (release.data, package.data) == before


@pytest.mark.parametrize('side', ['snapshot', 'release', 'spec'])
@pytest.mark.parametrize('legacy_marker', dispatch.legacy.MARKERS)
def test_any_legacy_side_marker_conflicts_with_v2_without_running_either_engine(monkeypatch, side, legacy_marker):
    _, package, p, release, calls, _, _ = scene(monkeypatch)
    release.data['snapshot'][V2_MARKERS[0]] = {}
    container(release, package, side)[legacy_marker] = 'legacy marker'
    denied(lambda: dispatch.verify_release(FakeDB(None), p, release))
    assert calls == []


@pytest.mark.parametrize('historical', [False, True])
def test_all_runtime_snapshot_release_and_package_markers_stripped_cannot_downgrade_issued_config(monkeypatch, historical):
    engine, _, p, release, calls, _, _ = scene(monkeypatch)
    issued = {'fullSemanticRuntimeBindings': [{'synthetic': 'issued binding'}]}
    draft = NS(id='synthetic-draft', version=5 if historical else 4,
        data={} if historical else issued)
    db = FakeDB(draft, issued) if historical else FakeDB(draft)
    # The actual issued schema now refuses stripped/mismatched markers before
    # invoking an engine. Its precise historical read must never select v1.
    def no_plan(*args): calls.append(('modern_missing', args)); return None
    engine.verify_release = no_plan
    denied(lambda: dispatch.verify_release(db, p, release))
    assert calls == []
    assert db.calls == (2 if historical else 1)


@pytest.mark.parametrize('side', ['snapshot', 'release', 'spec'])
def test_present_v2_marker_with_none_from_modern_never_selects_legacy(monkeypatch, side):
    engine, package, p, release, calls, _, _ = scene(monkeypatch)
    container(release, package, side)[V2_MARKERS[1]] = 'synthetic marker'
    def no_plan(*args): calls.append(('modern_missing', args)); return None
    engine.verify_release = no_plan
    db = FakeDB(None)
    denied(lambda: dispatch.verify_release(db, p, release))
    assert calls == [('modern_missing', (db, p, release))]


@pytest.mark.parametrize('side', ['snapshot', 'spec'])
def test_business_origin_alone_is_a_v2_signal_even_without_configuration_revision(monkeypatch, side):
    engine, package, p, release, calls, _, _ = scene(monkeypatch)
    release.data.pop('configuration_revision')
    key = 'businesses' if side == 'snapshot' else 'business_definitions'
    container(release, package, side)[key] = [{BUSINESS_MARKER: {'synthetic': 'present'}}]
    def damaged(*args): calls.append(('modern_business', args)); raise HTTPException(409, 'synthetic damaged plan')
    engine.verify_release = damaged
    db = FakeDB(None)
    denied(lambda: dispatch.verify_release(db, p, release))
    assert calls == [('modern_business', (db, p, release))]


def test_pins_honor_v2_and_reject_mixed_snapshot_versions(monkeypatch):
    _, _, _, _, calls, _, _ = scene(monkeypatch)
    value = {V2_MARKERS[0]: {}}
    assert dispatch.pins(value) == {'synthetic_new': 'pin'}
    assert calls == [('modern_pins', value)]
    calls.clear(); value[dispatch.legacy.MARKERS[0]] = {}
    denied(lambda: dispatch.pins(value))
    assert calls == []


def test_pins_business_origin_alone_cannot_fall_back_to_legacy(monkeypatch):
    _, _, _, _, calls, _, _ = scene(monkeypatch)
    value = {'businesses': [{BUSINESS_MARKER: {'synthetic': 'present'}}]}
    assert dispatch.pins(value) == {'synthetic_new': 'pin'}
    assert calls == [('modern_pins', value)]
