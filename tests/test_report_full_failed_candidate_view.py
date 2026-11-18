"""Isolated, synthetic HTTP/DB checks; never contacts a model provider."""
import copy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from generic_agents import report_full_reading_pipeline as full
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, SetupReportDraft
from generic_agents.report_full_failed_candidate_view import failed_candidate_view
from generic_agents.security import Principal
from generic_agents.worker import process_once
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, read_db
from tests.test_report_full_reading_pipeline import (reading_scene, SyntheticProvider, start, get,
    narrow_extract_capacity)


@pytest.fixture(autouse=True)
def require_owned_disposable_database(monkeypatch):
    import tests.test_report_full_reading_source as source_tests
    import tests.test_report_full_reading_pipeline as pipeline_tests
    def fenced():
        url = engine().url
        assert url.host == '127.0.0.1' and url.database.startswith('ga_test_')
    fenced()
    monkeypatch.setattr(source_tests, 'fence', fenced)
    monkeypatch.setattr(pipeline_tests, 'fence', fenced)


def fail_after_paid_checkpoint(scene, monkeypatch):
    provider = SyntheticProvider(scene, monkeypatch)
    _, _, accepted = start(scene)
    original = full.validate_full_result
    def reject(*args, **kwargs):
        raise ValueError('synthetic validation failure')
    monkeypatch.setattr(full, 'validate_full_result', reject)
    assert process_once(accepted['draft']['job_id'])
    monkeypatch.setattr(full, 'validate_full_result', original)
    row = get(scene, accepted['draft']['id'])
    assert row['status'] == 'failed' and row['error_code'] == 'full_candidate_invalid'
    return provider, row


def route(row, stage='extract-1'):
    return '/api/setup/report-full-readings/' + row['id'] + '/stages/' + stage + '/failed-candidate'


def test_preview_is_scoped_read_only_and_preserves_ledger(reading_scene, monkeypatch):
    scene = reading_scene
    provider, row = fail_after_paid_checkpoint(scene, monkeypatch)
    with Session(engine()) as db:
        draft = db.get(SetupReportDraft, row['id']); before = (draft.version, digest(draft.data))
        call = db.get(CloudCall, provider.calls[0]); before_call = copy.deepcopy(call.data)
    response = scene['client'].get(route(row), params={'revision': row['revision']})
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'private, no-store'
    value = response.json()
    assert value['scope']['actor_id'] == scene['p'].user_id
    assert value['scope']['workspace_id'] == scene['p'].workspace_id
    assert value['scope']['stage'] == 'extract-1'
    assert value['status'] == 'needs_review' and value['confirmed'] is False
    assert value['algorithm_version'] == 'report-failed-candidate-quarantine-v1'
    assert value['can_apply'] is False and value['can_publish'] is False
    assert 'content' not in value and 'provider_wire_base64' not in str(value)
    assert scene['client'].get(route(row), params={'revision': row['revision'] + 1}).status_code == 409
    with read_db() as db:
        for principal in (Principal(str(uuid4()), scene['p'].workspace_id, 'admin', ''),
                          Principal(scene['p'].user_id, str(uuid4()), 'admin', '')):
            with pytest.raises(HTTPException) as denied:
                failed_candidate_view(db, principal, row['id'], 'extract-1', row['revision'])
            assert denied.value.status_code == 404
    with Session(engine()) as db:
        draft = db.get(SetupReportDraft, row['id'])
        assert (draft.version, digest(draft.data)) == before
        assert db.get(CloudCall, provider.calls[0]).data == before_call
    assert len(provider.calls) == 1


@pytest.mark.parametrize('mode', ['truncated', 'unknown', 'multiple_choices', 'legacy_missing'])
def test_incomplete_or_missing_paid_answers_are_not_previewed(reading_scene, monkeypatch, mode):
    scene = reading_scene
    provider = SyntheticProvider(scene, monkeypatch, mode=mode)
    _, _, accepted = start(scene)
    assert process_once(accepted['draft']['job_id'])
    row = get(scene, accepted['draft']['id'])
    response = scene['client'].get(route(row), params={'revision': row['revision']})
    assert response.status_code == 409
    assert response.headers['cache-control'] == 'private, no-store'
    assert len(provider.calls) == 1


def test_succeeded_or_unknown_stages_are_not_previewed(reading_scene, monkeypatch):
    scene = reading_scene; provider = SyntheticProvider(scene, monkeypatch)
    _, _, accepted = start(scene)
    assert process_once(accepted['draft']['job_id'])
    row = get(scene, accepted['draft']['id'])
    assert scene['client'].get(route(row), params={'revision': row['revision']}).status_code == 409
    assert scene['client'].get(route(row, 'extract-9'), params={'revision': row['revision']}).status_code == 404
    assert len(provider.calls) >= 1


def test_preview_budget_is_explicit_and_preserves_original(reading_scene, monkeypatch):
    from generic_agents import report_full_reading_review_candidate as candidate_module
    scene = reading_scene; provider, row = fail_after_paid_checkpoint(scene, monkeypatch)
    with Session(engine()) as db:
        draft = db.get(SetupReportDraft, row['id'])
        original = copy.deepcopy(draft.data['extractions'][0]['raw_response'])
        before = (draft.version, digest(draft.data))
        before_call = copy.deepcopy(db.get(CloudCall, provider.calls[0]).data)
    # Deterministically exercise the work-limit branch with the ordinary
    # synthetic source; no real report or provider is involved.
    monkeypatch.setattr(candidate_module, '_MAX_CITATION_SCAN_CHARS', 1)
    response = scene['client'].get(route(row), params={'revision': row['revision']})
    assert response.status_code == 409
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.json()['detail']['code'] == 'full_candidate_preview_budget'
    with Session(engine()) as db:
        draft = db.get(SetupReportDraft, row['id'])
        assert (draft.version, digest(draft.data)) == before
        assert draft.data['extractions'][0]['raw_response'] == original
        assert db.get(CloudCall, provider.calls[0]).data == before_call
    assert len(provider.calls) == 1


def test_two_batches_only_uses_the_failed_batch_sources(reading_scene, monkeypatch):
    scene = reading_scene
    narrow_extract_capacity(scene, monkeypatch)
    provider, row = fail_after_paid_checkpoint(scene, monkeypatch)
    assert len(row['extractions']) == 2
    from generic_agents import report_full_reading_review_candidate as candidate_module
    original = candidate_module.build_candidate
    observed = {}
    def record(value, sources, context):
        observed['sources'] = set(sources)
        observed['context'] = copy.deepcopy(context)
        return original(value, sources, context)
    monkeypatch.setattr(candidate_module, 'build_candidate', record)
    response = scene['client'].get(route(row), params={'revision': row['revision']})
    assert response.status_code == 200, response.text
    with Session(engine()) as db:
        draft = db.get(SetupReportDraft, row['id'])
        batch = draft.data['preview']['batches'][0]
        assert observed['sources'] == set(batch['source_refs'])
        assert observed['context'] == batch['payload']['context']
        assert draft.data['extractions'][1].get('raw_response') is None
    assert len(provider.calls) == 1
    assert scene['client'].get(route(row, 'extract-2'), params={'revision': row['revision']}).status_code == 409


@pytest.mark.parametrize('tamper', ['ledger', 'wire'])
def test_corrupt_ledger_or_wire_never_previews(reading_scene, monkeypatch, tamper):
    scene = reading_scene; provider, row = fail_after_paid_checkpoint(scene, monkeypatch)
    with Session(engine()) as db:
        if tamper == 'ledger':
            call = db.get(CloudCall, provider.calls[0])
            call.data = {**call.data, 'actor_id': str(uuid4())}
        else:
            draft = db.get(SetupReportDraft, row['id']); data = copy.deepcopy(draft.data)
            raw = data['extractions'][0]['raw_response']
            raw['provider_wire_base64'] = 'e30='
            raw['hash'] = digest({k: v for k, v in raw.items() if k != 'hash'})
            draft.data = data
        db.commit()
    response = scene['client'].get(route(row), params={'revision': row['revision']})
    assert response.status_code == 409
    assert response.headers['cache-control'] == 'private, no-store'
    assert len(provider.calls) == 1
