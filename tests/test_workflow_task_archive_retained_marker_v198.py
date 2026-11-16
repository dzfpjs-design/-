"""Archive marker/compound-loss regression, using explicit public IO seams.

Actual archive collection, persistence and inventory code runs. The inherited
fixture is neither PostgreSQL nor current V175/lease/adoption authority proof.
"""
import copy

import pytest
from fastapi import HTTPException

from generic_agents import workflow_task_archive_v198 as archive
from generic_agents.models import Audit
from tests.test_workflow_task_archive_v198 import scene, Archive, Version, Reference, Command
from tests.test_report_business_flow_wire_v184 import no_external


def no_collection(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('An original marker must never recollect sources or create a replacement archive')
    monkeypatch.setattr(archive, 'collect_sources', forbidden)


def test_retained_marker_after_compound_archive_loss_never_creates_a_replacement(monkeypatch):
    s = scene(monkeypatch)
    original = archive.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    marker = copy.deepcopy(s.call.data[archive.STATE_KEY])
    assert marker['archive_id'] == original['archive_id'] and s.call.status == 'running'
    for model in (Archive, Version, Reference, Command): s.db.rows[model] = []
    s.db.rows[Audit] = [row for row in s.db.rows[Audit] if row.action != archive.SAVED]
    before = len(s.db.added); no_collection(monkeypatch)
    with pytest.raises(HTTPException) as error:
        archive.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    assert error.value.status_code == 409
    assert s.call.data[archive.STATE_KEY] == marker and s.call.status == 'running'
    assert len(s.db.added) == before and s.db.commits == 0
    assert all(s.db.rows[model] == [] for model in (Archive, Version, Reference, Command))


@pytest.mark.parametrize('marker', [None, {}, False, [], {'command_id': 'damaged-original-pointer'}])
def test_present_json_null_or_damaged_marker_cannot_enter_fresh_creation(monkeypatch, marker):
    s = scene(monkeypatch)
    s.call.data = {**s.call.data, archive.STATE_KEY: copy.deepcopy(marker)}
    before = len(s.db.added); no_collection(monkeypatch)
    with pytest.raises(HTTPException) as error:
        archive.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    assert error.value.status_code == 409 and len(s.db.added) == before and s.db.commits == 0
    assert archive.STATE_KEY in s.call.data and s.call.data[archive.STATE_KEY] == marker
    assert all(not s.db.rows.get(model) for model in (Archive, Version, Reference, Command))


def test_saved_success_can_be_read_only_reconciled_without_recollection_or_new_rows(monkeypatch):
    s = scene(monkeypatch)
    result = archive.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    s.call.status = 'succeeded'; s.call.data = {**s.call.data, 'output': copy.deepcopy(result)}
    before = len(s.db.added); original_data = copy.deepcopy(s.call.data)
    no_collection(monkeypatch)
    assert archive.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call) == result
    assert len(s.db.added) == before and s.db.commits == 0 and s.call.data == original_data


@pytest.mark.parametrize('fault', ['saved_audit', 'marker_missing', 'marker_null', 'marker_wrong_archive'])
def test_read_only_saved_success_still_requires_the_original_marker_and_independent_inventory(monkeypatch, fault):
    s = scene(monkeypatch)
    result = archive.execute(s.db, s.p, s.cap, s.inputs, s.key, s.approval)
    s.call.status = 'succeeded'; s.call.data = {**s.call.data, 'output': copy.deepcopy(result)}
    if fault == 'saved_audit': s.db.rows[Audit] = [row for row in s.db.rows[Audit] if row.action != archive.SAVED]
    elif fault == 'marker_missing': s.call.data = {k: v for k, v in s.call.data.items() if k != archive.STATE_KEY}
    elif fault == 'marker_null': s.call.data = {**s.call.data, archive.STATE_KEY: None}
    else: s.call.data[archive.STATE_KEY]['archive_id'] = 'different-original-archive'
    before = len(s.db.added); no_collection(monkeypatch)
    with pytest.raises(HTTPException) as error:
        archive.reconcile(s.db, s.p, s.cap, s.inputs, s.key, s.approval, s.call)
    assert error.value.status_code == 409 and len(s.db.added) == before and s.db.commits == 0
