"""Owned empty-PG, synthetic callback answers; root alone executes.

Saved-facts GET is compared across every table and immutable journal file.
No actual provider, private report, historical raw answer or paid row is read.
"""
import copy
import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from generic_agents import report_evidence_pipeline as pipeline
from generic_agents.db import engine
from generic_agents.models import Base, SetupReportDraft
from tests.test_report_evidence_pipeline import (evidence_scene, SyntheticProvider, start, get, reply, capture)
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene
from tests.test_report_full_reading_pipeline import reading_scene


BASE = '/api/setup/report-evidence-readings'


def all_rows():
    with Session(engine()) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        return {model.__name__: dict(db.execute(text('SELECT id,row_to_json(t)::text FROM "'
            + model.__tablename__ + '" t ORDER BY id')).all())
            for model in {mapper.class_ for mapper in Base.registry.mappers}}


def journal_files():
    root = pipeline._journal_root()
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob('*') if path.is_file()} if root.exists() else {}


def failed_answer(scene, monkeypatch, case):
    from generic_agents.worker import process_once
    real_provider = SyntheticProvider(scene, monkeypatch)
    original = {}
    def synthetic(cap, messages, schema, principal, **callbacks):
        received = callbacks['on_response']
        def save_original(content, receipt):
            value = json.loads(content)
            if case == 'long_key': value['claims'][0]['key'] = 'condition_unclear_machinery_library_location'
            elif case == 'kind': value['claims'][0]['kind'] = '流程'
            original['value'] = copy.deepcopy(value)
            wire = '{ invalid JSON' if case == 'not_json' else '\ud800' if case == 'quarantined' else json.dumps(value, ensure_ascii=False)
            original['wire'] = wire
            received(wire, receipt)
        value, receipt = real_provider(cap, messages, schema, principal, **{**callbacks, 'on_response': save_original})
        return original.get('value', value), receipt
    monkeypatch.setattr(pipeline, 'cloud_json', synthetic)
    _, command, accepted = start(scene)
    assert process_once(accepted['draft']['job_id'])
    failed = get(scene, accepted['draft']['id'])
    assert failed['status'] == 'failed' and len(real_provider.calls) == 1
    return failed, command, original


@pytest.mark.parametrize('case', ['long_key', 'kind'])
def test_failed_protocol_fields_are_readable_but_all_original_rows_files_dto_and_receipt_stay_exact(evidence_scene, monkeypatch, case):
    scene = evidence_scene; failed, command, original = failed_answer(scene, monkeypatch, case)
    before, files = all_rows(), journal_files(); identity = failed['id']
    original_receipt = reply(scene['client'].post(BASE, json=command), 202)['receipt']
    url = BASE + '/' + identity + '/saved-facts?stage=map-1'
    view = reply(scene['client'].get(url)); assert reply(scene['client'].get(url)) == view
    assert view['original_protocol_valid'] is False and view['stage_status'] == 'failed'
    fact = view['facts'][0]
    assert fact['fields'] == {key: original['value']['claims'][0][key]
        for key in ('kind', 'statement', 'span_refs', 'status', 'explanation')}
    assert fact['field_constraints_valid'] is (case == 'long_key')
    assert fact['protocol_key'] == original['value']['claims'][0]['key']
    assert all(ref['state'] == 'resolved' and ref['text'] for ref in fact['references'])
    assert view['eligible_for_reduce'] is view['can_apply'] is view['can_publish'] is False
    assert view['meaning_review_status'] == 'unreviewed'
    assert get(scene, identity) == failed
    assert reply(scene['client'].post(BASE, json=command), 202)['receipt'] == original_receipt
    assert all_rows() == before and journal_files() == files
    capture(scene, 'saved-facts-' + case.replace('_', '-'), view)


@pytest.mark.parametrize('case', ['not_json', 'quarantined'])
def test_non_json_and_quarantine_are_visible_without_repair_writes_or_arbitrary_raw(evidence_scene, monkeypatch, case):
    scene = evidence_scene; failed, _, original = failed_answer(scene, monkeypatch, case)
    before, files = all_rows(), journal_files()
    view = reply(scene['client'].get(BASE + '/' + failed['id'] + '/saved-facts?stage=map-1'))
    assert view['availability'] == case and view['facts'] == [] and view['original_protocol_valid'] is None
    assert 'wire' not in view and 'content' not in view and 'receipt' not in view
    assert all_rows() == before and journal_files() == files
    capture(scene, 'saved-facts-' + case.replace('_', '-'), view)


def test_queued_missing_reply_and_stage_bounds_are_readonly(evidence_scene):
    scene = evidence_scene; _, _, accepted = start(scene); identity = accepted['draft']['id']
    before = all_rows()
    view = reply(scene['client'].get(BASE + '/' + identity + '/saved-facts?stage=map-1'))
    assert view['availability'] == 'missing' and view['journal'] is None and view['facts'] == []
    assert scene['client'].get(BASE + '/' + identity + '/saved-facts?stage=map-24').status_code == 404
    assert scene['client'].get(BASE + '/' + identity + '/saved-facts?stage=reduce').status_code == 422
    assert all_rows() == before
    capture(scene, 'saved-facts-missing', view)


def test_storage_replacement_is_409_and_not_a_recoverable_or_adopted_fact_view(evidence_scene, monkeypatch):
    scene = evidence_scene; failed, _, _ = failed_answer(scene, monkeypatch, 'long_key')
    with Session(engine()) as db:
        row = db.get(SetupReportDraft, failed['id']); descriptor = pipeline._descriptor(row, 'map-1')
    bound = descriptor['binding']; path = pipeline._journal_root() / bound['workspace_id'] / bound['draft_id'] / 'map-1' / bound['reserved_call_id'] / 'wire.bin'
    path.write_bytes(b'{"changed":true}')
    before = all_rows(); files = journal_files()
    response = reply(scene['client'].get(BASE + '/' + failed['id'] + '/saved-facts?stage=map-1'), 409)
    assert response['detail']['code'] == 'evidence_saved_facts_integrity'
    assert all_rows() == before and journal_files() == files


@pytest.mark.parametrize('role', ['admin', 'operator'])
def test_saved_fact_endpoint_is_private_to_original_actor_even_for_another_admin(evidence_scene, role):
    from fastapi.testclient import TestClient
    from generic_agents.api import app
    scene = evidence_scene; _, _, accepted = start(scene)
    username = 'saved_facts_' + uuid4().hex[:12]; password = 'Synthetic-saved-facts-' + uuid4().hex
    reply(scene['client'].post('/api/users', json={'username': username, 'password': password, 'role': role}), 201)
    with TestClient(app) as other:
        login = reply(other.post('/api/login', json={'username': username, 'password': password}))
        other.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': scene['p'].workspace_id})
        before = all_rows()
        assert other.get(BASE + '/' + accepted['draft']['id'] + '/saved-facts?stage=map-1').status_code == 404
        assert all_rows() == before
