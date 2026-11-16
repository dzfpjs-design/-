"""Local catalog reuse over the unchanged source core; no real DB/IAM claim.

The source fixture uses real temporary native captures/journals and original
decoders, with explicit synthetic row/authority spies. Lifetime-only tests swap
the inner computation and test the existing owner-bound cache adapter itself.
No source grant, current permission, file or receipt result is cached.
"""
import copy
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import report_design_saved_source_v174 as subject
from generic_agents import report_evidence_pipeline as pipeline
from generic_agents import report_evidence_plan as evidence
from generic_agents import evidence_request_scope_v191 as reuse
from generic_agents.contracts import digest
from tests.test_report_design_saved_source_v174 import source_case, preserved, identity
from tests.test_report_evidence_enum_pipeline_v172 import kernel, no_external_effects


P = NS(user_id='public-synthetic-user', workspace_id='public-synthetic-workspace',
    role='operator', session_id='public-synthetic-session')


@pytest.fixture
def complete_catalog_checks(monkeypatch):
    original, calls = evidence._check_evidence_source_uncached, []
    def counted(catalog):
        calls.append(digest(catalog))
        return original(catalog)
    monkeypatch.setattr(evidence, '_check_evidence_source_uncached', counted)
    return calls


def test_real_original_core_returns_identical_snapshot_but_checks_equal_catalog_only_once(
        source_case, complete_catalog_checks):
    scene = source_case
    scene.p.session_id = identity(941)
    before = preserved(scene)
    expected = subject._cold_source_context_uncached(scene.db, scene.p, scene.case.row.id)
    assert len(complete_catalog_checks) > 1
    complete_catalog_checks.clear()
    scene.authority.clear()
    first = subject._cold_source_context(scene.db, scene.p, scene.case.row.id)
    assert first == expected and len(complete_catalog_checks) == 1
    assert ('owned', scene.case.row.id, False) in scene.authority
    assert ('material_refs', scene.case.row.id, False) in scene.authority
    assert ('check_row', scene.case.row.id) in scene.authority
    assert reuse._CURRENT.get() is None
    second = subject._cold_source_context(scene.db, scene.p, scene.case.row.id)
    assert second == expected and len(complete_catalog_checks) == 2
    assert scene.authority.count(('material_refs', scene.case.row.id, False)) == 2
    assert preserved(scene) == before


def test_original_capture_files_are_read_again_each_call_and_changed_bytes_still_reject(source_case, monkeypatch):
    scene = source_case
    scene.p.session_id = identity(942)
    reads, adapters, stages_by_binding = [], set(), {}
    for stage in [*scene.case.row.data['maps'], scene.case.row.data['reduction']]:
        key = stage.get('chunk_id', 'reduce')
        adapters.add(pipeline._journal_for(scene.case.row, key))
        binding = pipeline.journal_binding(scene.case.row, key, stage['reserved_call_id'])
        stages_by_binding[digest(binding)] = key
    # journal_binding selects v1/v2 by MODULE IDENTITY. Instrument that module's
    # real function instead of returning a proxy adapter that changes identity.
    for adapter in adapters:
        original = adapter.read_response
        def counted(*args, _original=original, **kwargs):
            binding = args[1] if len(args) > 1 else kwargs['binding']
            reads.append(stages_by_binding[digest(binding)])
            return _original(*args, **kwargs)
        monkeypatch.setattr(adapter, 'read_response', counted)
    first = subject._cold_source_context(scene.db, scene.p, scene.case.row.id)
    count = len(reads)
    assert count > 0
    assert subject._cold_source_context(scene.db, scene.p, scene.case.row.id) == first
    assert len(reads) == count * 2
    stage = scene.case.row.data['maps'][0]['chunk_id']
    path = scene.case.captures / scene.p.workspace_id / scene.case.row.id / stage / 'body.bin'
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises((ValueError, HTTPException)):
        subject._cold_source_context(scene.db, scene.p, scene.case.row.id)
    assert len(reads) > count * 2 and reuse._CURRENT.get() is None


@pytest.mark.parametrize('fault', ['cap_model', 'original_ledger_receipt', 'materials'])
def test_current_non_catalog_boundaries_cannot_reuse_a_prior_source_success(source_case, monkeypatch, fault):
    scene = source_case
    scene.p.session_id = identity(943)
    subject._cold_source_context(scene.db, scene.p, scene.case.row.id)
    if fault == 'cap_model':
        original = pipeline._cap
        def altered(preview):
            cap = original(preview)
            cap.data['execution']['model'] = 'different-unapproved-model'
            return cap
        monkeypatch.setattr(pipeline, '_cap', altered)
    elif fault == 'original_ledger_receipt':
        call = scene.db.calls[scene.case.row.data['maps'][0]['reserved_call_id']]
        call.data['receipt']['capture_hash'] = 'f' * 64
    else:
        def unavailable(*args, **kwargs):
            raise HTTPException(409, 'Public synthetic current-material rejection')
        monkeypatch.setattr(subject, 'material_refs', unavailable)
    before = preserved(scene)
    with pytest.raises((ValueError, HTTPException)):
        subject._cold_source_context(scene.db, scene.p, scene.case.row.id)
    assert preserved(scene) == before and reuse._CURRENT.get() is None


def test_same_computation_changed_catalog_cannot_hide_under_old_or_resealed_descriptor(
        monkeypatch, complete_catalog_checks):
    catalog = evidence.build_evidence_source('a' * 64, [['w:p1', '逐字原文😀\n保留']])
    def core(db, p, producer_id, *, lock=False):
        evidence.check_evidence_source(catalog)
        evidence.check_evidence_source(copy.deepcopy(catalog))
        assert len(complete_catalog_checks) == 1
        changed = copy.deepcopy(catalog)
        changed['index']['w:p1/s1']['text'] += '新增篡改'
        changed['descriptor']['catalog_hash'] = digest({key: value for key, value in changed['descriptor'].items()
            if key != 'catalog_hash'})
        evidence.check_evidence_source(changed)
    monkeypatch.setattr(subject, '_cold_source_context_uncached', core)
    with pytest.raises(ValueError):
        subject._cold_source_context(object(), P, 'producer')
    assert len(complete_catalog_checks) == 2 and reuse._CURRENT.get() is None


def test_thread_owner_and_exception_lifetime_never_retain_a_directory_authority(
        monkeypatch, complete_catalog_checks):
    catalog = evidence.build_evidence_source('a' * 64, [['w:p1', '只供生命周期测试的公开原文']])
    captured = []
    def core(db, p, producer_id, *, lock=False):
        evidence.check_evidence_source(catalog)
        copied = copy_context()
        captured.append(copied)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(copied.run, evidence.check_evidence_source, catalog).result()
        assert len(complete_catalog_checks) == 2, 'another thread must fully validate'
        evidence.check_evidence_source(catalog)
        assert len(complete_catalog_checks) == 2, 'only the still-live owner may reuse'
        raise RuntimeError('Public synthetic mid-core failure')
    monkeypatch.setattr(subject, '_cold_source_context_uncached', core)
    with pytest.raises(RuntimeError):
        subject._cold_source_context(object(), P, 'producer')
    assert reuse._CURRENT.get() is None
    captured[0].run(evidence.check_evidence_source, catalog)
    assert len(complete_catalog_checks) == 3, 'copied context is closed after failure'


def test_incomplete_identity_keeps_original_validation_without_adding_authority(
        monkeypatch, complete_catalog_checks):
    catalog = evidence.build_evidence_source('a' * 64, [['w:p1', '无session时保持原校验']])
    def core(db, p, producer_id, *, lock=False):
        first = evidence.check_evidence_source(catalog)
        second = evidence.check_evidence_source(catalog)
        assert first is second is catalog
        return 'lifetime-test-only'
    monkeypatch.setattr(subject, '_cold_source_context_uncached', core)
    assert subject._cold_source_context(object(), NS(user_id='u', workspace_id='w', role='operator'),
        'producer') == 'lifetime-test-only'
    assert len(complete_catalog_checks) == 2 and reuse._CURRENT.get() is None
