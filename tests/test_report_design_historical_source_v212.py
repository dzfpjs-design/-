"""Historical seam over real temporary native artifacts and synthetic SQL IO.

Reuses V174's public source fixture. Producer/material ownership remain its
explicit spies; IAM query results below are synthetic, never authenticated PG
authority. The actual current-reader guard, original Map native decoders,
captures/journals and frozen call checks run; no model or successful DB record
is manufactured. Root owns execution and real normal-API verification.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import report_design_saved_source_v174 as subject
from generic_agents import report_evidence_pipeline as pipeline
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents.models import User, Membership, AuthSession
from tests.test_report_design_saved_source_v174 import source_case, preserved, identity
from tests.test_report_evidence_enum_pipeline_v172 import kernel, no_external_effects


def reader_boundary(scene, monkeypatch):
    """Exercise the real SELECT-only IAM guard without claiming real SQL IAM."""
    state = NS(allowed=True, queries=[])
    def execute(statement, *args, **kwargs):
        assert statement.is_select and statement._for_update_arg is None
        entities = [entry['entity'] for entry in statement.column_descriptions]
        assert entities[:2] == [User, Membership]
        values = list(statement.compile().params.values())
        assert scene.p.user_id in values and scene.p.workspace_id in values and scene.p.role in values
        if getattr(scene.p, 'session_id', None) is not None:
            assert AuthSession in entities and scene.p.session_id in values
        state.queries.append((entities, values))
        return NS(first=lambda: (NS(id=scene.p.user_id), NS(role=scene.p.role)) if state.allowed else None)
    monkeypatch.setattr(scene.db, 'execute', execute)
    return state


def forbidden(*args, **kwargs):
    raise AssertionError('Historical verification entered fresh admission, a lease or a write')


def test_historical_viewer_rebuilds_exact_original_context_without_fresh_unknown_call_admission(source_case, monkeypatch):
    scene = source_case
    original = subject.load_source_context(scene.db, scene.p, scene.case.row.id)
    scene.p.role = 'viewer'; scene.p.session_id = identity(890)
    reader = reader_boundary(scene, monkeypatch)
    monkeypatch.setattr(subject, '_current_role', forbidden)
    monkeypatch.setattr(pipeline.legacy, 'guard_existing_paid', forbidden)
    scene.db.calls[identity(891)] = NS(id=identity(891), status='reserved', data={'unrelated': True})
    before = preserved(scene); scene.authority.clear()
    historical = subject.load_historical_source_context(scene.db, scene.p, scene.case.row.id)
    assert historical == original and reader.queries
    assert preserved(scene) == before
    assert ('owned', scene.case.row.id, False) in scene.authority
    assert ('material_refs', scene.case.row.id, False) in scene.authority
    assert all(not event[2] for event in scene.authority if event[0] in {'owned', 'material_refs'})
    historical['snapshot']['input_items'][historical['snapshot']['input_order'][0]]['statement'] = '修改返回副本'
    assert subject.load_historical_source_context(scene.db, scene.p, scene.case.row.id) == original


def test_current_reader_denial_precedes_every_producer_or_file_read(source_case, monkeypatch):
    scene = source_case; scene.p.session_id = identity(892)
    reader = reader_boundary(scene, monkeypatch); reader.allowed = False
    monkeypatch.setattr(pipeline, 'owned', forbidden)
    before = preserved(scene)
    with pytest.raises(HTTPException) as error:
        subject.load_historical_source_context(scene.db, scene.p, scene.case.row.id)
    assert error.value.status_code == 403 and reader.queries
    assert preserved(scene) == before


@pytest.mark.parametrize('fault', ['map_call_receipt', 'map_call_missing', 'map_job_missing', 'map_capture_bytes', 'materials'])
def test_history_still_cold_checks_independent_original_authorities_without_repair(source_case, monkeypatch, fault):
    scene = source_case; reader_boundary(scene, monkeypatch)
    stage = scene.case.row.data['maps'][0]
    if fault == 'map_call_receipt':
        scene.db.calls[stage['reserved_call_id']].data['receipt']['capture_hash'] = 'f' * 64
    elif fault == 'map_call_missing':
        del scene.db.calls[stage['reserved_call_id']]
    elif fault == 'map_job_missing':
        del scene.db.jobs[identity(603)]
    elif fault == 'map_capture_bytes':
        path = scene.case.captures / scene.p.workspace_id / scene.case.row.id / stage['chunk_id'] / 'body.bin'
        path.write_bytes(path.read_bytes() + b' ')
    else:
        def changed_material(*args, **kwargs):
            raise HTTPException(409, 'Public original material authority rejected')
        monkeypatch.setattr(subject, 'material_refs', changed_material)
    for name in ('_settle', '_consume', '_recover_native', '_run_stage', 'cloud_json', 'audit', 'checkpoint'):
        monkeypatch.setattr(pipeline, name, forbidden)
    monkeypatch.setattr(bridge, 'write_missing_journal', forbidden)
    before = preserved(scene)
    with pytest.raises(HTTPException):
        subject.load_historical_source_context(scene.db, scene.p, scene.case.row.id)
    assert preserved(scene) == before


def test_fresh_entry_retains_original_role_and_unknown_scope_gates(source_case, monkeypatch):
    scene = source_case; scene.p.role = 'viewer'; reader_boundary(scene, monkeypatch)
    with pytest.raises(HTTPException) as error:
        subject.load_source_context(scene.db, scene.p, scene.case.row.id)
    assert error.value.status_code == 403
    scene.p.role = 'operator'
    scene.db.calls[identity(893)] = NS(id=identity(893), status='reserved', data={'unrelated': True})
    with pytest.raises(HTTPException):
        subject.load_source_context(scene.db, scene.p, scene.case.row.id)
    # The exact same unrelated reservation is not historical source authority.
    historical = subject.load_historical_source_context(scene.db, scene.p, scene.case.row.id)
    assert historical['snapshot']['claim_count'] == 4
