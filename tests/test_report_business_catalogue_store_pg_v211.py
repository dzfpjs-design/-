"""Real PostgreSQL/API negative controls, deliberately without a paid-source fake.

The successful Draft/Plan journey requires a genuine completed V211 consumer;
Root verifies it separately on the actual target runtime. These tests prove
format/auth/unknown-source failures leave real tables, Jobs and CloudCalls alone.
"""
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.db import engine
from generic_agents.report_business_catalogue_models_v211 import (
    ReportBusinessCatalogueDraftV211 as Draft, ReportBusinessCatalogueCommandV211 as Command)
from generic_agents.report_scoped_design_models_v181 import (
    ReportScopedDesignPlanV181 as Plan, ReportScopedDesignPlanCommandV181 as PlanCommand)
from generic_agents.report_business_catalogue_store_v211 import PREFIX
from tests.test_business_data_gateway_pg import scene


def inventory(s):
    bound = engine()
    assert bound.dialect.name == 'postgresql' and bound.url.database.startswith('ga_test_')
    with Session(bound) as db:
        return tuple(db.scalar(select(func.count()).select_from(model).where(model.workspace_id == s.workspace))
            for model in (Draft, Command, Plan, PlanCommand, m.Job, m.CloudCall, m.Audit))


def test_missing_paid_catalogue_cannot_create_a_draft_or_reconstruct_a_consumer(scene):
    s = scene; before = inventory(s)
    response = s.client.post(PREFIX + '/drafts', json={'request_id': str(uuid4()),
        'consumer_id': str(uuid4()), 'expected_projection_hash': 'a' * 64})
    assert response.status_code == 404, response.text
    assert response.json() == {'detail': '新方案不存在或不属于当前账号'}, response.text
    assert inventory(s) == before


def test_missing_failed_partial_source_cannot_be_promoted_to_a_draft_or_an_approval(scene):
    s = scene; before = inventory(s)
    response = s.client.post(PREFIX + '/drafts', json={'request_id': str(uuid4()),
        'consumer_id': str(uuid4()), 'expected_projection_hash': 'a' * 64,
        'origin_mode': 'failed_catalogue_partial_v212'})
    assert response.status_code == 404, response.text
    assert response.json() == {'detail': '新方案不存在或不属于当前账号'}, response.text
    assert inventory(s) == before


@pytest.mark.parametrize('mode', ['partial', 'failed', 'complete ', True, None, {}, []])
def test_ambiguous_partial_origin_modes_are_rejected_before_any_source_or_history_write(scene, mode):
    s = scene; before = inventory(s)
    response = s.client.post(PREFIX + '/drafts', json={'request_id': str(uuid4()),
        'consumer_id': str(uuid4()), 'expected_projection_hash': 'a' * 64, 'origin_mode': mode})
    assert response.status_code == 422, response.text
    assert response.json() == {'detail': '目录原请求、严格版本或完整字段无法核对；原输入保留'}, response.text
    assert inventory(s) == before


def test_unknown_draft_read_save_confirm_do_not_create_plan_or_clear_a_request(scene):
    s = scene; before = inventory(s); identity = str(uuid4())
    response = s.client.get(PREFIX + '/drafts/' + identity)
    assert response.status_code == 404 and response.json() == {'detail': '目录草稿不存在或不属于当前账号'}, response.text
    save = {'request_id': str(uuid4()), 'expected_version': 1, 'expected_record_hash': 'a' * 64,
        'document': {}, 'actions': []}
    response = s.client.post(PREFIX + '/drafts/' + identity + '/save', json=save)
    assert response.status_code == 404, response.text
    assert response.json() == {'detail': '目录草稿不存在或不属于当前账号'}, response.text
    confirm = {'request_id': str(uuid4()), 'expected_version': 1, 'expected_record_hash': 'a' * 64,
        'document_hash': 'b' * 64, 'confirm': True}
    response = s.client.post(PREFIX + '/drafts/' + identity + '/confirm', json=confirm)
    assert response.status_code == 404, response.text
    assert response.json() == {'detail': '目录草稿不存在或不属于当前账号'}, response.text
    assert inventory(s) == before


def test_history_never_recovers_an_unknown_draft_or_guesses_a_numeric_version(scene):
    s = scene; before = inventory(s); identity = str(uuid4())
    response = s.client.get(PREFIX + '/drafts/' + identity + '/history/1')
    assert response.status_code == 404, response.text
    assert response.json() == {'detail': '目录草稿不存在或不属于当前账号'}, response.text
    for version in ('0', '01', '1.0', 'true', '-1', '301', '1000'):
        response = s.client.get(PREFIX + '/drafts/' + identity + '/history/' + version)
        assert response.status_code == 422, response.text
    assert inventory(s) == before


def test_unknown_original_is_explicitly_unknown_and_bad_wire_is_not_saved(scene):
    s = scene; before = inventory(s); nonce = str(uuid4())
    digest = hashlib.sha256(b'public synthetic unsubmitted original').hexdigest()
    response = s.client.get(PREFIX + '/requests/' + nonce, params={'expected_sha256': digest})
    assert response.status_code == 200, response.text
    assert response.json() == {'schema': 'report-business-catalogue-draft-request-v211',
        'request_id': nonce, 'wire_sha256': digest, 'outcome': 'unknown', 'result': None}
    raw = ('{"request_id":"' + nonce + '","request_id":"' + nonce + '"}').encode()
    assert s.client.post(PREFIX + '/drafts', content=raw,
        headers={'Content-Type': 'application/json'}).status_code == 422
    body = {'request_id': str(uuid4()), 'expected_version': True, 'expected_record_hash': 'a' * 64,
        'document_hash': 'b' * 64, 'confirm': True}
    assert s.client.post(PREFIX + '/drafts/' + str(uuid4()) + '/confirm', json=body).status_code == 422
    assert inventory(s) == before


def test_server_draft_discovery_is_exact_consumer_scoped_and_rejects_ambiguous_pagination(scene):
    s = scene; before = inventory(s); consumer_id = str(uuid4())
    response = s.client.get(PREFIX + '/drafts', params={'consumer_id': consumer_id})
    assert response.status_code == 200, response.text
    assert response.json() == {'schema': 'report-business-catalogue-draft-list-v211',
        'consumer_id': consumer_id, 'offset': 0, 'limit': 50, 'has_more': False, 'records': []}
    for suffix in ('&offset=0.0', '&limit=true', '&offset=0&offset=0'):
        response = s.client.get(PREFIX + '/drafts?consumer_id=' + consumer_id + suffix)
        assert response.status_code == 422, response.text
    assert inventory(s) == before


def test_actual_viewer_role_cannot_save_or_confirm_even_an_unknown_draft(scene):
    s = scene
    # Permission precondition only: no report/consumer/paid success is inserted.
    with Session(engine()) as db:
        membership = db.scalar(select(m.Membership).where(m.Membership.workspace_id == s.workspace,
            m.Membership.user_id == s.p.user_id))
        membership.role = 'viewer'; db.commit()
    before = inventory(s); identity = str(uuid4())
    save = {'request_id': str(uuid4()), 'expected_version': 1, 'expected_record_hash': 'a' * 64,
        'document': {}, 'actions': []}
    response = s.client.post(PREFIX + '/drafts/' + identity + '/save', json=save)
    assert response.status_code == 403, response.text
    confirm = {'request_id': str(uuid4()), 'expected_version': 1, 'expected_record_hash': 'a' * 64,
        'document_hash': 'b' * 64, 'confirm': True}
    response = s.client.post(PREFIX + '/drafts/' + identity + '/confirm', json=confirm)
    assert response.status_code == 403, response.text
    assert inventory(s) == before


def test_signed_out_requests_cannot_recover_or_create_catalogue_work(scene):
    s = scene; before = inventory(s)
    original = list(s.client.cookies.jar)
    s.client.cookies.clear()
    try:
        response = s.client.get(PREFIX + '/requests/' + str(uuid4()), params={'expected_sha256': 'a' * 64})
        assert response.status_code in {401, 403}, response.text
        response = s.client.post(PREFIX + '/drafts', json={'request_id': str(uuid4()),
            'consumer_id': str(uuid4()), 'expected_projection_hash': 'a' * 64})
        assert response.status_code in {401, 403}, response.text
    finally:
        for cookie in original: s.client.cookies.jar.set_cookie(cookie)
    assert inventory(s) == before
