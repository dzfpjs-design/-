"""Real fixed-version Search creation; Root supplies disposable PostgreSQL.

Uses the normal instance/upload/parse/independent-receiver/search worker APIs.
No successful parse/search Job or reception row is fabricated. Post-success
ledger changes below are explicit fault injection, not recovery acceptance.
"""
import copy
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, runtime_documents as d, runtime_document_receipts as receipts
from generic_agents.db import engine
from generic_agents.contracts import digest
from generic_agents.config import settings
from generic_agents.runtime_document_models import RuntimeDocumentSearch as Search
from generic_agents.runtime_document_models import RuntimeDocumentVersion as Version
from generic_agents.runtime_document_models import RuntimeDocumentParseAttempt as Attempt
from tests.test_runtime_documents_pg_v205 import scene, response, instance, uploaded, run, version

BASE = '/api/runtime-documents'


def count(db, p):
    return {cls.__tablename__: db.scalar(select(func.count()).select_from(cls).where(
        cls.workspace_id == p.workspace_id)) for cls in (Search, m.Job, m.Audit, m.Event, m.Memory)}


def body(case, **changes):
    return {'request_id': str(uuid4()), 'release_id': case.release['id'], 'keyword': '摄像', **changes}


def selected(db, case, value=None, **changes):
    arguments = {**case.arguments, **changes}
    return d.start_selected_search(db, case.p, case.release['instance_id'], value or body(case), **arguments)


@pytest.fixture
def fixed_document(scene):
    s = scene; bound = engine()
    assert bound.dialect.name == 'postgresql' and bound.url.database.startswith('ga_test_')
    release = instance(s)
    raw = '固定原报告：摄像资料应逐项核对独立接收回执。\n这是技术条款，不证明现场时间。'.encode('utf8')
    document = response(uploaded(s, release, raw)[0], 201)
    identity = document['latest_version_id']
    assert run(s, identity) == 'succeeded'
    receipts.consume_once(s.workspace)
    view = version(s, identity)
    assert view['status'] == 'parsed' and view['delivery']['verified']
    with Session(bound) as db:
        row = db.get(Version, identity)
        attempt = db.get(Attempt, row.data['latest_attempt_id'])
        arguments = {'selected_version_id': row.id, 'selected_attempt_id': attempt.id,
            'expected_parse_hash': attempt.data['parse_hash'], 'expected_raw_sha256': row.data['origin']['raw_sha256']}
        case = NS(s=s, bound=bound, p=s.p, release=release, document=document,
            version_id=row.id, attempt_id=attempt.id, evidence_id=row.evidence_id,
            parse_job_id=attempt.job_id, event_id=attempt.data['event_id'], arguments=arguments, raw=raw)
        before = count(db, s.p)
        # Every negative reaches a genuine successful fixed-source baseline.
        created = selected(db, case)
        assert created['status'] == 'queued' and created['coverage']['selected'] == 1
        search = db.get(Search, created['id'])
        assert search.data['inventory'] == [{'document_id': row.document_id, 'version_id': row.id,
            'attempt_id': attempt.id, 'parse_hash': arguments['expected_parse_hash'], 'eligible': True, 'reason': None}]
        assert db.get(m.Job, search.data['job_id']).kind == 'search_runtime_documents'
        db.rollback()
    with Session(bound) as db:
        assert count(db, s.p) == before  # helper never commits for its caller
    return case


def test_fixed_old_version_is_searched_after_normal_upload_of_a_new_latest(fixed_document):
    case = fixed_document
    current = response(case.s.client.get(BASE+'/documents/'+case.document['id']))
    newer = response(uploaded(case.s, case.release, '新版本只有森林条款，不包含原检索词。'.encode(),
                              previous=current)[0], 201)
    assert run(case.s, newer['latest_version_id']) == 'succeeded'
    receipts.consume_once(case.s.workspace)
    request = body(case)
    with Session(case.bound) as db:
        created = selected(db, case, request)
        search = db.get(Search, created['id'])
        assert len(search.data['inventory']) == 1
        assert search.data['inventory'][0]['version_id'] == case.version_id
        assert created['coverage'] == {'selected': 1, 'eligible': 1, 'unavailable': 0, 'limited': False,
            'document_limit': 1, 'hit_limit': d.HIT_LIMIT,
            'notice': '仅本次明确选择的一份固定文档版本；未包括其他版本或资料。'}
        db.commit()
    assert run(case.s, created['id'], 'search_runtime_documents') == 'succeeded'
    result = response(case.s.client.get(BASE+'/searches/'+created['id']))
    assert result['status'] == 'completed' and len(result['hits']) == 1
    hit = result['hits'][0]
    assert hit['version_id'] == case.version_id and hit['attempt_id'] == case.attempt_id
    assert '摄像资料应逐项核对' in hit['text']
    assert response(case.s.client.get(hit['citation_url']))['raw_sha256'] == case.arguments['expected_raw_sha256']
    ordinary = response(case.s.client.post(BASE+'/instances/'+case.release['instance_id']+'/searches',
                                         json=body(case)), 201)
    with Session(case.bound) as db:
        inventory = db.get(Search, ordinary['id']).data['inventory']
        assert len(inventory) == 1 and inventory[0]['version_id'] == newer['latest_version_id']
        assert inventory[0]['eligible'] is True


def test_helper_leaves_child_and_command_in_the_callers_uncommitted_transaction(fixed_document):
    case = fixed_document
    with Session(case.bound) as db:
        before = count(db, case.p)
        request = body(case)
        created = selected(db, case, request)
        search = db.get(Search, created['id'])
        origin = d.anchored(db, case.p, d.SEARCH_ORIGIN, search.id, d.search_origin(search))
        assert origin.actor_id == case.p.user_id
        command = db.scalar(select(m.Audit).where(m.Audit.action == d.COMMAND,
                                                 m.Audit.object_id == request['request_id']))
        assert command.data['body'] == request and command.data['request_hash'] == digest(request)
        db.rollback()
    with Session(case.bound) as db:
        assert db.get(Search, created['id']) is None
        assert count(db, case.p) == before


def test_missing_independent_archive_ack_does_not_create_a_search(fixed_document):
    case = fixed_document
    with Session(case.bound) as db:
        ack = db.scalar(select(m.Audit).where(m.Audit.workspace_id == case.p.workspace_id,
            m.Audit.object_id == case.event_id, m.Audit.action == receipts.ACK))
        assert ack is not None
        db.delete(ack); db.commit()
    with Session(case.bound) as db:
        before = count(db, case.p)
        with pytest.raises(HTTPException) as error:
            selected(db, case)
        assert error.value.status_code == 409
        assert count(db, case.p) == before


@pytest.mark.parametrize('state', ['running', 'failed', 'blocked_integrity', 'cancelled'])
def test_parsed_row_with_unsettled_or_failed_real_parse_job_cannot_supply_new_child(fixed_document, state):
    case = fixed_document
    with Session(case.bound) as db:
        assert db.get(Version, case.version_id).status == 'parsed'
        # The baseline normal worker really succeeded; now inject only a
        # ledger fault to prove a persisted result is not job completion.
        db.get(m.Job, case.parse_job_id).status = state
        db.commit()
    with Session(case.bound) as db:
        before = count(db, case.p)
        with pytest.raises(HTTPException) as error:
            selected(db, case)
        assert error.value.status_code == 409
        assert count(db, case.p) == before


@pytest.mark.parametrize('field', ['expected_raw_sha256', 'expected_parse_hash'])
def test_wrong_fixed_fingerprint_never_uses_the_current_document_instead(fixed_document, field):
    case = fixed_document
    with Session(case.bound) as db:
        before = count(db, case.p)
        with pytest.raises(HTTPException) as error:
            selected(db, case, **{field: '0'*64})
        assert error.value.status_code == 409
        assert count(db, case.p) == before


def test_changed_original_bytes_are_not_replaced_by_saved_parsed_text(fixed_document):
    case = fixed_document
    with Session(case.bound) as db:
        evidence = db.get(m.Evidence, case.evidence_id)
        path = settings().storage_dir/evidence.storage_key
        assert path.read_bytes() == case.raw
        before = count(db, case.p)
        try:
            path.write_bytes(b'controlled original-byte corruption')
            with pytest.raises(HTTPException) as error:
                selected(db, case)
            assert error.value.status_code in {404, 409}
            assert count(db, case.p) == before
        finally:
            path.write_bytes(case.raw)


@pytest.mark.parametrize('predecessor', ['ordinary', 'selected'])
def test_an_occupied_nonce_is_never_adopted_by_same_body_alone(fixed_document, predecessor):
    case = fixed_document; request = body(case)
    if predecessor == 'ordinary':
        old = response(case.s.client.post(BASE+'/instances/'+case.release['instance_id']+'/searches',
                                         json=request), 201)
    else:
        with Session(case.bound) as db:
            old = selected(db, case, request)
            db.commit()
    with Session(case.bound) as db:
        before = count(db, case.p)
        with pytest.raises(HTTPException) as error:
            selected(db, case, request)
        assert error.value.status_code == 409
        assert db.get(Search, old['id']) is not None
        assert count(db, case.p) == before


@pytest.mark.parametrize('remove_search', [False, True])
def test_missing_command_with_a_retained_search_or_origin_is_an_orphan_conflict(fixed_document, remove_search):
    case = fixed_document; request = body(case)
    created = response(case.s.client.post(BASE+'/instances/'+case.release['instance_id']+'/searches',
                                        json=request), 201)
    with Session(case.bound) as db:
        command = db.scalar(select(m.Audit).where(m.Audit.workspace_id == case.p.workspace_id,
            m.Audit.action == d.COMMAND, m.Audit.object_id == request['request_id']))
        assert command is not None
        db.delete(command)
        if remove_search:
            db.delete(db.get(Search, created['id']))
        db.commit()
    with Session(case.bound) as db:
        before = count(db, case.p)
        with pytest.raises(HTTPException) as error:
            selected(db, case, request)
        assert error.value.status_code == 409
        assert count(db, case.p) == before


def test_normal_default_inventory_coverage_command_and_hash_stay_unchanged(fixed_document):
    case = fixed_document; request = body(case)
    path = BASE+'/instances/'+case.release['instance_id']+'/searches'
    ordinary = response(case.s.client.post(path, json=request), 201)
    with Session(case.bound) as db:
        row = db.get(Search, ordinary['id']); original = copy.deepcopy(row.data)
        original_origin = d.search_origin(row)
        job_id = row.data['job_id']; job_data = copy.deepcopy(db.get(m.Job, job_id).data)
        command = db.scalar(select(m.Audit).where(m.Audit.action == d.COMMAND,
                                                 m.Audit.object_id == request['request_id']))
        old_command = copy.deepcopy(command.data)
        assert original['request_hash'] == digest(request)
        assert ordinary['coverage']['document_limit'] == d.DOCUMENT_LIMIT == 100
        assert ordinary['coverage']['notice'] is None
        selected(db, case)
        db.commit()
    assert response(case.s.client.post(path, json=request), 201) == ordinary
    with Session(case.bound) as db:
        row = db.get(Search, ordinary['id'])
        assert row.data == original and d.search_origin(row) == original_origin
        assert db.get(m.Job, job_id).data == job_data
        command = db.scalar(select(m.Audit).where(m.Audit.action == d.COMMAND,
                                                 m.Audit.object_id == request['request_id']))
        assert command.data == old_command
        assert set(command.data['body']) == {'request_id', 'release_id', 'keyword'}


def test_internal_helper_does_not_widen_the_standard_search_body(fixed_document):
    case = fixed_document
    with Session(case.bound) as db:
        before = count(db, case.p)
        with pytest.raises(HTTPException) as error:
            selected(db, case, {**body(case), 'parent_query_id': str(uuid4())})
        assert error.value.status_code == 422
        assert count(db, case.p) == before
