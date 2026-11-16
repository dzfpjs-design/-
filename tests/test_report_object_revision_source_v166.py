"""Source adapter unit seams; never proof of a real report or database flow."""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents.contracts import digest
from generic_agents import report_object_revision_source_v166 as source
from generic_agents import report_saved_map_review_source_v178 as selected_source
from generic_agents import report_full_semantic_handoff as handoff
from generic_agents.report_evidence_wire_v3 import candidate_with_ids
from tests.test_report_evidence_semantics_pure import catalog, CONTEXT
from tests.test_report_evidence_wire_v3_pure import candidate_wire


def scene(monkeypatch, manual=True):
    sources = catalog()
    old = {'schema': 'report-evidence-manual-review-v5', 'result': candidate_wire(sources), 'note': '原私稿不改'}
    projection = candidate_with_ids(old['result'], lambda prefix, path: prefix + digest(path)[:32])
    row = NS(id='16600000-0000-4000-8000-000000000001', version=7, status='review', data={
        'preview': {'source_hash': sources['descriptor']['source_hash'], 'map_preparation': {'context': copy.deepcopy(CONTEXT)}},
        'model_result_hash': digest(projection), 'review_value': copy.deepcopy(old) if manual else None})
    calls = []
    class DB:
        def scalar(self, query):
            calls.append(query)
            return row
    db = DB()
    p = NS(user_id='actor', workspace_id='workspace')
    def no_selected_source(actual_db, actual_p, report_id, lock=False):
        # This fixture is the original V166 source path, with no independently
        # selected saved-Map source. New-family integrity is tested separately.
        assert actual_db is db and actual_p is p and report_id == row.id and type(lock) is bool
        return None
    monkeypatch.setattr(selected_source, 'load_selected_source', no_selected_source)
    monkeypatch.setattr(source.pipeline, 'check_row', lambda actual, actor: actual)
    monkeypatch.setattr(source.pipeline, '_catalog', lambda preview: sources)
    monkeypatch.setattr(source.pipeline, '_reduction_result', lambda *args: {'result': copy.deepcopy(projection)})
    monkeypatch.setattr(handoff, 'material_refs', lambda *args, **kwargs: calls.append(('materials', kwargs['lock'])))
    return db, p, row, old, calls


def test_unit_seam_uses_original_saved_manual_and_share_locks_without_changing_it(monkeypatch):
    db, p, row, old, calls = scene(monkeypatch)
    before = copy.deepcopy(row.data)
    result = source.load_source(db, p, row.id, lock=True)
    assert row.data == before and result['value']['note'] == old['note']
    assert result['basis']['base_value_hash'] == digest(old)
    assert result['basis_hash'] == digest(result['basis'])
    assert calls[0]._for_update_arg.read is True and calls[1] == ('materials', True)
    assert all(not item['acknowledge_platform_identity'] for item in result['value']['object_identity_confirmations'])


def test_unit_seam_without_manual_uses_exact_verified_model_projection(monkeypatch):
    db, p, row, old, calls = scene(monkeypatch, manual=False)
    result = source.load_source(db, p, row.id)
    assert result['basis']['base_value_hash'] == digest({**old, 'note': ''})
    assert row.data['review_value'] is None


def test_missing_original_complete_candidate_is_blocked_without_fabrication_or_replay(monkeypatch):
    db, p, row, old, calls = scene(monkeypatch)
    row.status = 'needs_clarification'; row.data['model_result_hash'] = None
    monkeypatch.setattr(source.pipeline, '_reduction_result', lambda *args: pytest.fail('must not fabricate or retry reduction'))
    with pytest.raises(HTTPException) as error:
        source.load_source(db, p, row.id)
    assert error.value.status_code == 409 and '未自动重跑' in str(error.value.detail)
    assert len(calls) == 1


@pytest.mark.parametrize('fault', ['missing_owner', 'changed_material', 'unsupported_manual', 'conversion_failure', 'projection_hash'])
def test_unit_seams_fail_closed_and_never_replace_base(monkeypatch, fault):
    db, p, row, old, calls = scene(monkeypatch, manual=fault != 'projection_hash')
    before = copy.deepcopy(row.data)
    if fault == 'missing_owner':
        db.scalar = lambda query: None
    elif fault == 'changed_material':
        def changed(*args, **kwargs): raise HTTPException(409, 'source changed')
        monkeypatch.setattr(handoff, 'material_refs', changed)
    elif fault == 'unsupported_manual':
        row.data['review_value']['schema'] = 'unsupported'
        before = copy.deepcopy(row.data)
    elif fault == 'conversion_failure':
        def invalid(*args, **kwargs): raise ValueError('cannot losslessly fork')
        monkeypatch.setattr(source, 'new_from_value', invalid)
    elif fault == 'projection_hash':
        row.data['model_result_hash'] = '0' * 64
        before = copy.deepcopy(row.data)
    with pytest.raises(HTTPException) as error:
        source.load_source(db, p, row.id)
    assert error.value.status_code == (404 if fault == 'missing_owner' else 409)
    assert row.data == before
