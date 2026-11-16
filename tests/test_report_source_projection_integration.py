"""Typed ledger and compiler integration; OCR IO is an explicit synthetic adapter."""
import copy
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import test_report_source_projection as fixtures
from generic_agents import report_configuration_handoff as h
from generic_agents import report_source_projection as projection
from generic_agents.contracts import digest


def accepted(monkeypatch):
    s = fixtures.state(monkeypatch)
    body = h.ProjectedSourceReviewCommand.model_validate(s.body.model_dump(mode='json'))
    proof, proposed, receipt = projection.projected_review_value(s.db, fixtures.P,
        s.handoff, s.config, body, str(uuid4()))
    row = NS(id=proof['id'], workspace_id=fixtures.P.workspace_id, actor_id=fixtures.P.user_id,
        kind='source_review', parent_id=s.handoff['id'], report_id=s.handoff['report_id'],
        configuration_id=s.config.id, request_id=str(body.request_id),
        data=h.ledger_data(proof, body, receipt))
    return s, body, proof, proposed, receipt, row


def test_actual_typed_ledger_accepts_projected_record_and_rejects_resealed_descriptor_change(monkeypatch):
    s, body, proof, _, _, row = accepted(monkeypatch)
    assert h.checked_ledger(row, fixtures.P)['record'] == proof
    changed = copy.deepcopy(row.data)
    changed['record']['projection']['projection_refs'][0]['provenance_hash'] = 'f' * 64
    changed['record'] = h.public_record({k: v for k, v in changed['record'].items() if k != 'record_hash'})
    changed['receipt']['record_hash'] = changed['record']['record_hash']
    changed['receipt']['receipt_hash'] = digest({k: v for k, v in changed['receipt'].items() if k != 'receipt_hash'})
    changed['data_hash'] = digest({k: v for k, v in changed.items() if k != 'data_hash'})
    row.data = changed
    with pytest.raises(HTTPException) as failure:
        h.checked_ledger(row, fixtures.P)
    assert failure.value.detail['code'] == 'source_record_integrity'


def test_projected_proof_compiles_only_formal_pdf_but_activation_rechecks_origin(monkeypatch):
    s, _, proof, proposed, _, row = accepted(monkeypatch)
    monkeypatch.setattr(h, 'all_records', lambda *args: [s.handoff, proof])
    pins = h.configuration_source_snapshots(s.db, fixtures.P, proposed)
    assert set(pins) == {s.copy.id}
    assert s.origin.status == 'needs_input'
    monkeypatch.setattr(h, 'owned_record', lambda db, p, identity, **kwargs:
        row if str(identity) == proof['id'] else NS(data={'record': s.handoff}))
    snapshot = {'id': s.copy.id, 'report_sources': pins[s.copy.id]}
    h.verify_report_material_snapshot(s.db, fixtures.P, snapshot)
    s.origin.version += 1
    with pytest.raises(HTTPException) as failure:
        h.verify_report_material_snapshot(s.db, fixtures.P, snapshot)
    assert failure.value.detail['code'] == 'source_review_changed'


def test_source_review_v1_body_remains_exact_and_new_projection_never_silently_parses_as_v1(monkeypatch):
    s = fixtures.state(monkeypatch)
    value = s.body.model_dump(mode='json')
    for key in ('review_mode', 'projection_refs', 'final_view_confirmed'):
        value.pop(key)
    old = h.SourceReviewCommand.model_validate(value)
    assert old.model_dump(mode='json') == value
    with pytest.raises(ValidationError):
        h.SourceReviewCommand.model_validate(s.body.model_dump(mode='json'))
    for confirmation in (1, 'true', False):
        changed = s.body.model_dump(mode='json'); changed['final_view_confirmed'] = confirmation
        with pytest.raises(ValidationError):
            h.ProjectedSourceReviewCommand.model_validate(changed)
