"""Legacy dispatch boundaries plus owned-PG compatibility, root-executed only.

The PG fixture performs genuine synthetic upload/copy/OCR-source selection and
publication. Its local converter/OCR IO is explicitly fake; no report model or
provider is invoked and CloudCall must remain empty. The pure dispatch doubles
test routing only, never stand in for new-state integrity validation in PG.
The author has not imported, executed or connected anything while writing.
"""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import report_reading_drafts as rd
from generic_agents import report_configuration_handoff as handoff
from generic_agents import report_draft_partial_review as partial
from generic_agents import report_evidence_pipeline as evidence
from generic_agents import report_full_reading_pipeline as full
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Base, CloudCall, Job, SetupDraft, SetupReportDraft
from tests import test_report_reading_drafts_pure as old_fixture
from tests import test_report_configuration_handoff_pure as handoff_fixture
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_setup import config
from tests.test_vertical_slice import publish


STATE = 'report-evidence-draft-state-v1'
BASE = '/api/setup/report-evidence-readings'
P = old_fixture.P


def new_row(schema=STATE, status='queued'):
    return NS(id=str(uuid4()), workspace_id=P.workspace_id, actor_id=P.user_id,
        request_id='fe_' + str(uuid4()), version=1, status=status,
        data={'schema': schema, 'phase': 'awaiting_reduce',
              'opaque_synthetic_only': '原始合成占位；不是模型回答。'})


def failure(code):
    def forbidden(*args, **kwargs):
        pytest.fail(code)
    return forbidden


def test_pure_old_records_and_generation_registry_keep_their_original_read_shape():
    row = old_fixture.row(); original = copy.deepcopy(row.__dict__)
    registry = copy.deepcopy(rd.GENERATION_REGISTRY)
    assert rd.check_row(row, P) is row
    value = rd.draft_view(row, P)
    assert value['model_result'] == old_fixture.result()
    assert value['preview'] == row.data['preview']
    assert value['can_apply'] is value['can_publish'] is False
    assert row.__dict__ == original and rd.GENERATION_REGISTRY == registry
    assert set(registry) == {'report-reading-generation-v1', 'report-reading-generation-v2'}
    assert registry['report-reading-generation-v1']['task'] == rd.TASK_V1
    assert registry['report-reading-generation-v2']['task'] == rd.TASK_V2


@pytest.mark.parametrize('schema,module', [(STATE, evidence),
    ('report-full-reading-draft-state-v1', full)])
@pytest.mark.parametrize('operation', ['check_row', 'draft_view', 'run', 'record_job_failure'])
def test_pure_dispatch_is_exact_and_does_not_parse_or_mutate_the_other_format(monkeypatch, schema, module, operation):
    row = new_row(schema); before = copy.deepcopy(row.__dict__); calls = []
    expected = object()
    def delegated(*args):
        calls.append(args)
        return expected
    monkeypatch.setattr(module, operation, delegated)
    if operation in {'check_row', 'draft_view'}:
        assert getattr(rd, operation)(row, P) is expected
        assert calls == [(row, P)]
    elif operation == 'run':
        locks = []
        def identify(db, principal, identity, lock=False):
            assert principal == P and identity == row.id
            locks.append(lock); return row
        monkeypatch.setattr(rd, 'owned', identify)
        db = object()
        assert rd.run(db, P, row.id) is expected
        assert locks == [False] and calls == [(db, P, row.id)]
    else:
        queries = []
        def lookup(query):
            assert query._for_update_arg is not None
            queries.append(query); return row
        db = NS(scalar=lookup); job = NS(data={'report_draft_id': row.id})
        assert rd.record_job_failure(db, P, job, 'synthetic stop') is expected
        assert len(queries) == 1 and calls == [(db, P, job, 'synthetic stop')]
    assert row.__dict__ == before


@pytest.mark.parametrize('operation', ['record_review', 'save_review', 'get_review_receipt',
    'record_local_review', 'record_partial_review', 'local_review_receipt', 'checked_raw',
    'saved_value', 'preview_value', 'submit_handoff'])
def test_pure_new_candidate_is_never_sent_through_old_review_or_handoff(monkeypatch, operation):
    row = new_row(status='review'); before = copy.deepcopy(row.__dict__)
    monkeypatch.setattr(evidence, 'check_row', lambda current, p: current)
    monkeypatch.setattr(rd, 'owned', lambda *args, **kwargs: row)
    monkeypatch.setattr(rd, 'validate_candidate', failure('new value reached old V5 validator'))
    monkeypatch.setattr(partial, 'check_local_state', failure('new value reached old local policy'))
    monkeypatch.setattr(partial, 'analyze', failure('new value reached old partial analyzer'))
    db = NS(commit=failure('rejected compatibility request committed'))
    review = rd.ReviewCommand(revision=1, request_id=uuid4(),
        value={'result': old_fixture.result(), 'note': '仅旧接口拒绝测试'})
    local = partial.LocalReviewCommand(revision=1, request_id=uuid4())
    request_id = uuid4()
    if operation == 'record_review': call = lambda: rd.record_review(row, P, review)
    elif operation == 'save_review': call = lambda: rd.save_review(UUID(row.id), review, P, db)
    elif operation == 'get_review_receipt': call = lambda: rd.get_review_receipt(UUID(row.id), request_id, P, db)
    elif operation == 'record_local_review': call = lambda: partial.record_local_review(row, P, local, failure('ledger read'))
    elif operation == 'record_partial_review': call = lambda: partial.record_partial_review(row, P, review, failure('ledger read'))
    elif operation == 'local_review_receipt': call = lambda: partial.local_review_receipt(UUID(row.id), request_id, P, db)
    elif operation == 'checked_raw': call = lambda: partial.checked_raw(row)
    elif operation == 'saved_value': call = lambda: handoff.saved_value(row, P, object())
    elif operation == 'preview_value': call = lambda: handoff.preview_value(row, P, object(), object())
    else:
        monkeypatch.setattr(handoff, 'nonce_lock', lambda *args: None)
        monkeypatch.setattr(handoff, 'request_row', lambda *args: None)
        monkeypatch.setattr(handoff, 'configuration_row', failure('new value reached old configuration mutation'))
        call = lambda: handoff.submit_handoff(UUID(row.id), NS(request_id=request_id), P, db)
    with pytest.raises(HTTPException) as error:
        call()
    assert error.value.status_code == 409
    expected = 'full_semantics_mapping_required' if operation in {'saved_value', 'preview_value', 'submit_handoff'} else 'evidence_reading_endpoint_required'
    assert error.value.detail['code'] == expected
    assert row.__dict__ == before


def test_pure_existing_handoff_nonce_replay_still_precedes_any_new_state_gate(monkeypatch):
    report, configuration, command, record, _, receipt = handoff_fixture.accepted()
    ledger = NS(id=record['id'], workspace_id=P.workspace_id, actor_id=P.user_id,
        report_id=report.id, configuration_id=configuration.id, request_id=str(command.request_id),
        kind='handoff', parent_id=None, data=handoff.ledger_data(record, command, receipt))
    before = copy.deepcopy(ledger.__dict__)
    monkeypatch.setattr(handoff, 'nonce_lock', lambda *args: None)
    monkeypatch.setattr(handoff, 'request_row', lambda *args: ledger)
    monkeypatch.setattr(handoff, 'configuration_row', lambda *args: configuration)
    monkeypatch.setattr(rd, 'owned', failure('exact old nonce touched a fresh private row'))
    value = handoff.submit_handoff(UUID(report.id), command, P, object())
    assert value['receipt'] == receipt and ledger.__dict__ == before
    with pytest.raises(HTTPException):
        handoff.submit_handoff(UUID(report.id), command.model_copy(update={'report_revision': command.report_revision + 1}), P, object())
    assert ledger.__dict__ == before


def test_pure_old_review_same_nonce_does_not_gain_new_schema_or_lose_original_value():
    row = old_fixture.row()
    body = rd.ReviewCommand(revision=row.version, request_id=uuid4(),
        value={'result': old_fixture.result(), 'note': '旧人工稿末字🙂'})
    receipt = rd.record_review(row, P, body); saved = copy.deepcopy(row.__dict__)
    assert rd.record_review(row, P, body) == receipt and row.__dict__ == saved
    changed = body.model_copy(update={'value': body.value.model_copy(update={'note': '同号改体'})})
    with pytest.raises(HTTPException) as error:
        rd.record_review(row, P, changed)
    assert error.value.detail['code'] == 'review_nonce_conflict'
    assert row.__dict__ == saved


@pytest.mark.parametrize('phase', ['map', 'awaiting_reduce'])
def test_pure_active_guard_includes_new_awaiting_merge_without_parsing_its_phase(phase):
    row = new_row(status='awaiting_merge'); row.data['phase'] = phase
    class GuardDB:
        def scalar(self, query):
            if query.get_final_froms()[0].name == SetupDraft.__tablename__:
                assert query._for_update_arg is not None
                return NS()
            assert query._for_update_arg is None
            assert any(isinstance(v, (list, tuple)) and 'awaiting_merge' in v for v in query.compile().params.values())
            return row
    with pytest.raises(HTTPException) as error:
        rd.guard_existing_paid(GuardDB(), P)
    assert error.value.detail['code'] == 'report_draft_pending'
    lists = [[row], []]
    db = NS(scalars=lambda query: NS(all=lambda: lists.pop(0)), commit=failure('active GET wrote'))
    value = rd.get_active(P, db)
    assert value['blocked'] is value['report_draft_active'] is True
    assert value['own'] == [{'id': row.id, 'request_id': row.request_id, 'status': 'awaiting_merge'}]


def reply(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def persisted_pg_state():
    """Fresh synthetic database only; do not decode any CloudCall payload."""
    fence()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
    with engine().connect() as connection:
        rows = {name: tuple(connection.scalars(text('SELECT row_to_json(t)::text FROM "' + name + '" t ORDER BY t.id')))
                for name in sorted(Base.metadata.tables)}
    storage = settings().storage_dir
    files = {str(path.relative_to(storage)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in storage.rglob('*') if path.is_file()}
    return rows, files


@pytest.fixture
def pg_scene(full_source_scene):
    """Actual new queued authorization, no model dispatch or synthetic paid row."""
    scene = full_source_scene; client = scene['client']; fence()
    me = reply(client.get('/api/me')); client.headers['X-Workspace-ID'] = me['workspace_id']
    material = reply(client.get('/api/setup/materials/' + scene['copy']['id']))
    before = reply(client.get('/api/setup/draft'))
    reply(client.put('/api/setup/draft', json={'revision': before['revision'],
        'configuration': config([scene['original'], material])}))
    manifest = json.loads((Path(__file__).parents[1] / 'examples/qwen.json').read_text())
    manifest.update(name='synthetic_legacy_boundary_' + uuid4().hex[:10],
        description='Source and endpoint compatibility only; no model is called')
    capability = publish(client, manifest)
    options = reply(client.get('/api/setup/report-drafts/full-source-options/' + scene['original']['id'],
        params={'version': scene['original']['version'], 'hash': scene['original']['hash']}))
    draft = reply(client.get('/api/setup/draft'))
    body = {'request_id': 'fe_' + str(uuid4()), 'revision': draft['revision'], 'model_id': capability['id'],
        'material_refs': [{key: scene['original'][key] for key in ('id', 'version', 'hash')}],
        'selection': options['copies'][0]['proposal'], 'note': '真实合成来源与旧入口隔离；不代表模型质量。'}
    return {**scene, 'body': body}


def start_pg(scene, monkeypatch):
    # Driver and ambient budget remain zero. This fixture-local allowance only
    # permits storing a new queued authorization; no worker/gateway is invoked.
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 25)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 4000000)
    preview = reply(scene['client'].post(BASE + '/preview', json=scene['body']))
    command = {**scene['body'], 'preview_hash': preview['preview_hash'], 'consent': True}
    return preview, command, reply(scene['client'].post(BASE, json=command), 202)


def test_pg_new_queued_record_is_absent_from_old_lists_but_blocks_fresh_old_payment(pg_scene, monkeypatch):
    scene = pg_scene; client = scene['client']
    _, _, started = start_pg(scene, monkeypatch); row = started['draft']
    before = persisted_pg_state()
    assert all(item['id'] != row['id'] for item in reply(client.get('/api/setup/report-drafts')))
    assert all(item['id'] != row['id'] for item in reply(client.get('/api/setup/report-full-readings'))['items'])
    assert reply(client.get('/api/setup/report-drafts/' + row['id'])) == row
    restored = reply(client.get('/api/setup/report-drafts/by-request/' + scene['body']['request_id']))
    assert restored['draft'] == row and restored['receipt'] == started['receipt']
    active = reply(client.get('/api/setup/report-drafts/active'))
    assert active['blocked'] is True and active['report_draft_active'] is True
    assert any(item['id'] == row['id'] for item in active['own'])
    legacy = {key: copy.deepcopy(value) for key, value in scene['body'].items() if key != 'selection'}
    legacy.update(request_id='rd_' + str(uuid4()), preview_hash='0' * 64, consent=True)
    error = client.post('/api/setup/report-drafts', json=legacy)
    assert error.status_code == 409 and error.json()['detail']['code'] == 'report_draft_pending'
    assert persisted_pg_state() == before


def test_pg_old_human_and_handoff_endpoints_refuse_new_state_without_any_write(pg_scene, monkeypatch):
    scene = pg_scene; client = scene['client']
    _, _, started = start_pg(scene, monkeypatch); row = started['draft']
    before = persisted_pg_state(); path = '/api/setup/report-drafts/' + row['id']
    review = {'revision': row['revision'], 'request_id': str(uuid4()),
        'value': {'result': old_fixture.result(), 'note': '不允许丢弃全局声明。'}}
    local = {'revision': row['revision'], 'request_id': str(uuid4())}
    for response in [client.put(path + '/review', json=review), client.post(path + '/local-review', json=local),
                     client.get(path + '/review-receipts/' + review['request_id']),
                     client.get(path + '/local-review-receipts/' + local['request_id'])]:
        assert response.status_code == 409, response.text
        assert response.json()['detail']['code'] == 'evidence_reading_endpoint_required'
    selection = {'report_revision': row['revision'], 'review_receipt_hash': '0' * 64,
        'candidate_hash': '0' * 64, 'source_hash': row['preview']['source_hash'],
        'validation_report_hash': '0' * 64, 'configuration_revision': scene['body']['revision'],
        'configuration_hash': '0' * 64, 'business_indexes': [0]}
    for suffix, body in [('/configuration-preview', selection), ('/configuration-handoff',
            {**selection, 'request_id': str(uuid4()), 'preview_hash': '0' * 64, 'consent': True})]:
        response = client.post(path + suffix, json=body)
        assert response.status_code == 409, response.text
        assert response.json()['detail']['code'] == 'full_semantics_mapping_required'
    assert reply(client.get(BASE + '/' + row['id'])) == row
    assert persisted_pg_state() == before
