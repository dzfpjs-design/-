"""Pure original Map-plan reuse only; no real IAM, DB or paid-call assertion."""
import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import evidence_request_scope_v191 as reuse
from generic_agents import report_evidence_enum_map_v172 as maps
from generic_agents import report_design_saved_source_v174 as source
from generic_agents import report_evidence_pipeline as pipeline
from generic_agents.contracts import digest
from tests.test_report_evidence_enum_map_v172 import catalog, cap, build
from tests.test_report_design_saved_source_v174 import source_case, preserved, identity
from tests.test_report_evidence_enum_pipeline_v172 import kernel, no_external_effects


def principal():
    return NS(user_id='public-user', workspace_id='public-workspace', role='operator', session_id='public-session')


@pytest.fixture
def triplet():
    directory, capability = catalog(), cap()
    value = build(directory, capability)
    return value, directory, capability


@pytest.fixture
def complete_builds(monkeypatch, triplet):
    original, calls = maps.build_map_preparation, []
    def counted(*args, **kwargs):
        calls.append(None)
        return original(*args, **kwargs)
    monkeypatch.setattr(maps, 'build_map_preparation', counted)
    return calls


@pytest.mark.parametrize('scoped', [False, True])
def test_outside_opt_in_preserves_all_original_checks_and_return_identity(triplet, complete_builds, scoped):
    from contextlib import nullcontext
    value, directory, capability = triplet
    with reuse.catalog_read_scope(principal(), 'producer') if scoped else nullcontext():
        assert maps.check_map_preparation(value, directory, capability) is value
        assert maps.check_map_preparation(value, directory, capability) is value
    assert len(complete_builds) == 2


def test_five_equal_full_checks_rebuild_once_with_detached_results_and_a_fresh_next_scope(triplet, complete_builds):
    value, directory, capability = triplet
    before = copy.deepcopy(value)
    with reuse.catalog_read_scope(principal(), 'producer', map_preparation=True):
        for _ in range(5):
            checked = maps.check_map_preparation(copy.deepcopy(value), copy.deepcopy(directory), copy.deepcopy(capability))
            assert checked == before and checked is not value
        assert len(complete_builds) == 1
        checked['context']['mutated_return'] = True
        assert maps.check_map_preparation(value, directory, capability) == before
        assert len(complete_builds) == 1
    with reuse.catalog_read_scope(principal(), 'producer', map_preparation=True):
        assert maps.check_map_preparation(value, directory, capability) == before
    assert len(complete_builds) == 2 and value == before and reuse._CURRENT.get() is None


@pytest.mark.parametrize('fault', ['plan_context', 'catalog_text', 'cap_model'])
def test_changed_actual_value_catalog_or_cap_is_rechecked_not_accepted_by_its_old_hash(
        triplet, complete_builds, fault):
    value, directory, capability = triplet
    with reuse.catalog_read_scope(principal(), 'producer', map_preparation=True):
        maps.check_map_preparation(value, directory, capability)
        if fault == 'plan_context':
            value['context']['new_real_value'] = True
            value['preparation_hash'] = digest({key: item for key, item in value.items() if key != 'preparation_hash'})
        elif fault == 'catalog_text':
            ref = next(iter(directory['index']))
            directory['index'][ref]['text'] += ' changed actual bytes'
            directory['descriptor']['catalog_hash'] = digest({key: item for key, item in directory['descriptor'].items()
                if key != 'catalog_hash'})
        else:
            capability.data['execution']['model'] = 'different-unapproved-model'
        for _ in range(2):
            with pytest.raises(ValueError):
                maps.check_map_preparation(value, directory, capability)
        assert len(complete_builds) == 3, 'invalid new content is never cached, sorted or repaired'


def test_data_policy_changes_and_validator_identity_cannot_alias_an_old_pure_success(triplet):
    value, directory, capability = triplet
    calls = []
    def permissive_for_lifetime_only(value, directory, capability):
        calls.append(copy.deepcopy(capability.data))
        return value
    with reuse.catalog_read_scope(principal(), 'producer', map_preparation=True):
        reuse._map_preparation(value, directory, capability, permissive_for_lifetime_only)
        capability.data['data_policy']['redact_fields'] = ['public-new-redaction-field']
        reuse._map_preparation(value, directory, capability, permissive_for_lifetime_only)
        assert len(calls) == 2
        forged = copy.deepcopy(value)
        forged['preparation_hash'] = 'f' * 64
        reuse._map_preparation(forged, directory, capability, permissive_for_lifetime_only)
        with pytest.raises(ValueError):
            maps.check_map_preparation(forged, directory, capability)


@pytest.mark.parametrize('entry_limit,byte_limit', [(0, 16 * 1024 * 1024), (8, 0)])
def test_capacity_disables_reuse_but_keeps_original_validation_and_does_not_reject_valid_input(
        monkeypatch, triplet, complete_builds, entry_limit, byte_limit):
    value, directory, capability = triplet
    monkeypatch.setattr(reuse, 'MAX_ENTRIES', entry_limit)
    monkeypatch.setattr(reuse, 'MAX_RETAINED_BYTES', byte_limit)
    with reuse.catalog_read_scope(principal(), 'producer', map_preparation=True):
        assert maps.check_map_preparation(value, directory, capability) is value
        assert maps.check_map_preparation(value, directory, capability) is value
    assert len(complete_builds) == 2


def test_threads_and_copied_context_cannot_reuse_or_outlive_the_original_owner(triplet, complete_builds):
    value, directory, capability = triplet
    with pytest.raises(RuntimeError):
        with reuse.catalog_read_scope(principal(), 'producer', map_preparation=True):
            maps.check_map_preparation(value, directory, capability)
            captured = copy_context()
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(captured.run, maps.check_map_preparation, value, directory, capability).result()
            assert result is value and len(complete_builds) == 2
            maps.check_map_preparation(value, directory, capability)
            assert len(complete_builds) == 2
            raise RuntimeError('public synthetic failure after validation')
    assert reuse._CURRENT.get() is None
    assert captured.run(maps.check_map_preparation, value, directory, capability) is value
    assert len(complete_builds) == 3


def test_async_child_inherited_context_cannot_share_a_parent_map_preparation(triplet, complete_builds):
    value, directory, capability = triplet
    async def child():
        return maps.check_map_preparation(value, directory, capability)
    async def run():
        with reuse.catalog_read_scope(principal(), 'producer', map_preparation=True):
            maps.check_map_preparation(value, directory, capability)
            result = await asyncio.create_task(child())
            assert result is value and len(complete_builds) == 2
            maps.check_map_preparation(value, directory, capability)
            assert len(complete_builds) == 2
    asyncio.run(run())
    assert reuse._CURRENT.get() is None


def test_mutated_principal_and_incomplete_identity_disable_reuse_without_authorizing_anything(triplet, complete_builds):
    value, directory, capability = triplet
    actor = principal()
    with reuse.catalog_read_scope(actor, 'producer', map_preparation=True):
        maps.check_map_preparation(value, directory, capability)
        actor.role = 'viewer'
        assert maps.check_map_preparation(value, directory, capability) is value
    with reuse.catalog_read_scope(NS(user_id='u'), 'producer', map_preparation=True):
        assert maps.check_map_preparation(value, directory, capability) is value
    assert len(complete_builds) == 3


def test_real_cold_source_keeps_original_snapshot_and_all_original_file_and_ledger_paths(
        source_case, complete_builds, monkeypatch):
    scene = source_case
    scene.p.session_id = identity(971)
    before = preserved(scene)
    expected = source._cold_source_context_uncached(scene.db, scene.p, scene.case.row.id)
    complete_builds.clear()
    actual = source._cold_source_context(scene.db, scene.p, scene.case.row.id)
    assert actual == expected and len(complete_builds) == 1
    assert preserved(scene) == before and reuse._CURRENT.get() is None
    assert source._cold_source_context(scene.db, scene.p, scene.case.row.id) == expected
    assert len(complete_builds) == 2, 'a second source invocation fully rebuilds once again'
    # The previously checked ledger cannot be retained through the new pure memo.
    call = scene.db.calls[scene.case.row.data['maps'][0]['reserved_call_id']]
    call.data['receipt']['capture_hash'] = 'f' * 64
    with pytest.raises((ValueError, HTTPException)):
        source._cold_source_context(scene.db, scene.p, scene.case.row.id)
    assert reuse._CURRENT.get() is None
