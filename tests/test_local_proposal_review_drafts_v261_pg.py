"""Private-draft durability on real PG, not cloud/model-quality acceptance.

Only provider HTTP is an explicit synthetic transport. The saved failed
SetupAnalysis, actual users/memberships, original paid ledger, draft rows,
locks, audits and receipts use real services/database persistence. Root must
register the new model in migration before running this file. No service DB,
permission check, or receipt success is mocked.
"""
import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.security import Principal, password_hash
from generic_agents.local_proposal_review_models_v261 import LocalProposalReviewDraftV261 as Draft
from generic_agents import local_proposal_review_drafts_v261 as subject
from tests.test_setup_understanding import prepared, start, process
from tests.test_setup_understanding_v3 import process_latest
from tests.test_setup_understanding_v4 import wire


@pytest.fixture()
def review_scene(client, monkeypatch):
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 100)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 1000000)
    body, _ = prepared(client, '只在值班人员明确核对后归档原音候选，禁止自动控制设备。')
    body = {**body, 'contract_version': 4, 'request_id': str(uuid4())}
    sent, _ = wire(monkeypatch, invalid=True)
    opened, _ = start(client, body); process(opened)
    response = client.get('/api/setup/understanding/' + opened['id'])
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'failed' and len(sent) == 1
    me = client.get('/api/me').json()
    p = Principal(me['user_id'], me['workspace_id'], me['role'], 'private-review-test')
    with Session(engine()) as db:
        source = db.get(m.SetupAnalysis, opened['id'])
        raw = copy.deepcopy(source.data['raw_response'])
        assert type(raw['value']) is dict
        assert digest({key: value for key, value in raw.items() if key != 'hash'}) == raw['hash']
        version = source.version
    return SimpleNamespace(client=client, p=p, source_id=opened['id'],
        source_version=version, raw_hash=raw['hash'], raw=raw, sent=sent)


def open_body(s, **changes):
    return {'request_id': str(uuid4()), 'source_version': s.source_version,
        'raw_hash': s.raw_hash, **changes}


def open_saved(s, body=None, *, p=None):
    body = body or open_body(s)
    with Session(engine()) as db:
        value = subject.open_draft(db, p or s.p, s.source_id, body)
        assert db.in_transaction()
        db.commit()
    return value, body


def save_body(current, *, reason='私人理由不能进入共享审计', **changes):
    value = copy.deepcopy(current['value'])
    value['edits'] = [{'op': 'replace_citation', 'path': '/businesses/0/citations/0',
        'reason': reason, 'material_id': None, 'block_id': None, 'start_text': '', 'end_text': ''}]
    return {'version': current['version'], 'request_id': str(uuid4()), 'value': value, **changes}


def save(s, draft_id, body, *, p=None):
    with Session(engine()) as db:
        value = subject.save_draft(db, p or s.p, draft_id, body)
        assert db.in_transaction()
        db.commit()
    return value


def read(s, draft_id, *, p=None):
    with Session(engine()) as db:
        return subject.get_draft(db, p or s.p, draft_id)


def public_state(s):
    with Session(engine()) as db:
        result = {}
        for cls in (m.SetupAnalysis, m.SetupDraft, m.CloudCall, m.Job, m.Memory, m.DataRun):
            result[cls.__name__] = sorted((row.id, getattr(row, 'version', None),
                getattr(row, 'status', None), digest(getattr(row, 'data', {})),
                getattr(row, 'reserved_tokens', None), getattr(row, 'charged_tokens', None))
                for row in db.scalars(select(cls).where(cls.workspace_id == s.p.workspace_id)))
        return result


def another_principal(s, *, role='admin', foreign=False, active=True):
    with Session(engine()) as db:
        user = m.User(username='review_' + uuid4().hex[:20],
            password_hash=password_hash(uuid4().hex), active=active)
        db.add(user)
        workspace_id = s.p.workspace_id
        if foreign:
            workspace = m.Workspace(name='独立私人稿隔离工作区'); db.add(workspace); db.flush()
            workspace_id = workspace.id
        db.flush()
        db.add(m.Membership(user_id=user.id, workspace_id=workspace_id, role=role))
        db.commit()
        return Principal(user.id, workspace_id, role, 'actual-membership-private-review')


def test_open_save_fresh_session_recovery_private_contents_and_public_inventories_unchanged(review_scene):
    s = review_scene; before = public_state(s)
    opened, original_open = open_saved(s)
    assert opened['version'] == 1 and opened['status'] == 'editing'
    assert opened['value'] == {'source': {'analysis_id': s.source_id,
        'source_version': s.source_version, 'raw_hash': s.raw_hash, 'contract_version': 4}, 'edits': []}
    body = save_body(opened, reason='私人标记🌲须保存但不可进入共享审计')
    saved = save(s, opened['id'], body)
    assert saved['current_matches'] is True and saved['save_receipt']['version_after'] == 2
    current = read(s, opened['id'])  # A separate, cold ORM Session.
    assert current == saved['current_draft'] and current['value'] == body['value']
    assert current['value_hash'] == digest(body['value'])
    assert current['source_state'] == 'not_revalidated' and current['can_submit'] is False
    recovered, _ = open_saved(s, original_open)
    assert recovered == current and recovered['id'] == opened['id']
    with Session(engine()) as db:
        row = db.get(Draft, opened['id'])
        assert len(row.data['commands']) == 1
        assert set(row.data['commands'][0]) == {'request_id', 'body_hash', 'receipt'}
        assert '私人标记' not in json.dumps(row.data['commands'], ensure_ascii=False)
        audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
            m.Audit.object_id == row.id, m.Audit.action.in_([subject.OPEN_ACTION, subject.SAVE_ACTION]))))
        assert len(audits) == 2
        assert all(set(record.data) == {'draft_id', 'version', 'hash'} for record in audits)
        assert '私人标记' not in json.dumps([record.data for record in audits], ensure_ascii=False)
    assert public_state(s) == before and len(s.sent) == 1


def test_same_nonce_restore_precedes_cas_but_changed_body_and_stale_new_command_cannot_overwrite(review_scene):
    s = review_scene; opened, _ = open_saved(s)
    first_body = save_body(opened); first = save(s, opened['id'], first_body)
    replay = save(s, opened['id'], first_body)
    assert replay == first
    second = save(s, opened['id'], save_body(first['current_draft'], reason='第二次私人修改'))
    old = save(s, opened['id'], first_body)
    assert old['save_receipt'] == first['save_receipt'] and old['current_matches'] is False
    assert old['current_draft'] == second['current_draft']
    altered = copy.deepcopy(first_body); altered['value']['edits'][0]['reason'] = '原号不能换正文'
    for bad in (altered, {**first_body, 'request_id': str(uuid4())}):
        with pytest.raises(HTTPException) as failure:
            save(s, opened['id'], bad)
        assert failure.value.status_code == 409
    wrong_source = save_body(second['current_draft'])
    wrong_source['value']['source']['analysis_id'] = str(uuid4())
    with pytest.raises(HTTPException) as failure:
        save(s, opened['id'], wrong_source)
    assert failure.value.status_code == 409
    assert read(s, opened['id']) == second['current_draft']


def test_open_nonce_is_exact_but_explicit_distinct_nonce_can_create_another_private_draft(review_scene):
    s = review_scene; first, body = open_saved(s)
    again, _ = open_saved(s, body)
    assert again == first
    for change in ({'raw_hash': '0' * 64}, {'source_version': s.source_version + 1}):
        with pytest.raises(HTTPException) as failure:
            open_saved(s, {**body, **change})
        assert failure.value.status_code == 409
    second, _ = open_saved(s)
    assert second['id'] != first['id']
    with Session(engine()) as db:
        listed = subject.list_drafts(db, s.p, s.source_id)
        assert {item['id'] for item in listed} == {first['id'], second['id']}
        assert subject.list_drafts(db, s.p, s.source_id, limit=1)[0]['id'] == second['id']


@pytest.mark.parametrize('foreign', [False, True])
def test_another_real_admin_or_workspace_cannot_read_or_save_owner_private_work(review_scene, foreign):
    s = review_scene; opened, _ = open_saved(s)
    other = another_principal(s, foreign=foreign)
    for action in (lambda: read(s, opened['id'], p=other),
            lambda: save(s, opened['id'], save_body(opened), p=other)):
        with pytest.raises(HTTPException) as failure:
            action()
        assert failure.value.status_code == 404
    with Session(engine()) as db:
        if foreign:
            with pytest.raises(HTTPException) as failure:
                subject.list_drafts(db, other, s.source_id)
            assert failure.value.status_code == 404
        else:
            assert subject.list_drafts(db, other, s.source_id) == []
    assert read(s, opened['id']) == opened


def test_actual_viewer_inactive_and_stale_roles_are_rejected_while_operator_can_author(review_scene):
    s = review_scene; opened, _ = open_saved(s)
    viewer = another_principal(s, role='viewer')
    inactive = another_principal(s, active=False)
    stale = Principal(s.p.user_id, s.p.workspace_id, 'operator', s.p.session_id)
    for p in (viewer, inactive, stale):
        with pytest.raises(HTTPException) as failure:
            read(s, opened['id'], p=p)
        assert failure.value.status_code == (404 if p is viewer else 403)
        with pytest.raises(HTTPException) as failure:
            open_saved(s, p=p)
        assert failure.value.status_code == 403
    operator = another_principal(s, role='operator')
    theirs, _ = open_saved(s, p=operator)
    result = save(s, theirs['id'], save_body(theirs), p=operator)
    assert result['current_draft']['owner_id'] == operator.user_id
    assert read(s, theirs['id'], p=operator) == result['current_draft']


def test_owner_downgraded_to_viewer_can_recover_private_work_but_cannot_write(review_scene):
    s=review_scene;opened,_=open_saved(s)
    saved=save(s,opened['id'],save_body(opened))['current_draft']
    with Session(engine()) as db:
        member=db.scalar(select(m.Membership).where(m.Membership.workspace_id==s.p.workspace_id,
            m.Membership.user_id==s.p.user_id));member.role='viewer';db.commit()
    viewer=Principal(s.p.user_id,s.p.workspace_id,'viewer',s.p.session_id)
    assert read(s,opened['id'],p=viewer)==saved
    for operation in (lambda:save(s,opened['id'],save_body(saved),p=viewer),lambda:open_saved(s,p=viewer)):
        with pytest.raises(HTTPException) as failure:operation()
        assert failure.value.status_code==403


@pytest.mark.parametrize('damage', ['missing_raw', 'bad_raw_hash'])
def test_bad_source_blocks_new_writes_but_never_swallows_existing_private_input_or_original_nonce(review_scene, damage):
    s = review_scene; opened, original_open = open_saved(s)
    saved = save(s, opened['id'], save_body(opened))['current_draft']
    with Session(engine()) as db:
        source = db.get(m.SetupAnalysis, s.source_id); data = copy.deepcopy(source.data)
        if damage == 'missing_raw':
            del data['raw_response']
        else:
            data['raw_response']['value']['domain'] = '显式损坏反例，未重新签hash'
        source.data = data; db.commit()
    assert read(s, opened['id']) == saved
    restored, _ = open_saved(s, original_open)
    assert restored == saved
    with Session(engine()) as db:
        assert subject.list_drafts(db, s.p, s.source_id) == [saved]
    for action in (lambda: open_saved(s), lambda: save(s, opened['id'], save_body(saved))):
        with pytest.raises(HTTPException) as failure:
            action()
        assert failure.value.status_code == 409
    assert read(s, opened['id']) == saved and len(s.sent) == 1


def test_normal_local_recheck_and_new_configuration_revision_do_not_block_private_saving(review_scene):
    s = review_scene; opened, _ = open_saved(s)
    retry = s.client.post('/api/setup/understanding/' + s.source_id + '/retry',
        json={'version': s.source_version})
    assert retry.status_code == 200, retry.text
    process_latest(retry.json())  # Existing local revalidation of the same failed answer, not another HTTP call.
    with Session(engine()) as db:
        source = db.get(m.SetupAnalysis, s.source_id)
        assert source.version > s.source_version and source.data['raw_response'] == s.raw
    current = s.client.get('/api/setup/draft').json()
    put = s.client.put('/api/setup/draft', json={'revision': current['revision'],
        'configuration': current['configuration']})
    assert put.status_code == 200, put.text
    before = public_state(s)
    saved = save(s, opened['id'], save_body(opened))
    assert saved['current_draft']['value']['source']['source_version'] == s.source_version
    assert public_state(s) == before and len(s.sent) == 1


def test_latest_sixty_four_receipts_keep_recent_replay_and_old_stale_nonce_cannot_rewrite(review_scene):
    s = review_scene; opened, _ = open_saved(s); current = opened; oldest = None
    with Session(engine()) as db:
        for index in range(65):
            body = save_body(current, reason='最近回执工程对照' + str(index))
            if oldest is None: oldest = copy.deepcopy(body)
            current = subject.save_draft(db, s.p, opened['id'], body)['current_draft']
        db.commit()
    assert current['version'] == 66
    with Session(engine()) as db:
        assert len(db.get(Draft, opened['id']).data['commands']) == 64
    assert save(s, opened['id'], body)['current_matches'] is True
    with pytest.raises(HTTPException) as failure:
        save(s, opened['id'], oldest)
    assert failure.value.status_code == 409
    assert read(s, opened['id']) == current


def test_old_audit_clock_never_hides_the_latest_logical_save(review_scene,monkeypatch):
    s=review_scene;opened,_=open_saved(s);current=opened
    with Session(engine()) as db:
        for index in range(65):
            current=subject.save_draft(db,s.p,opened['id'],save_body(current,reason='正常历史保存'+str(index)))['current_draft']
        db.commit()
    actual=subject.audit
    def clock_rollback(db,p,action,object_id,data):
        result=actual(db,p,action,object_id,data)
        if action==subject.SAVE_ACTION:
            db.flush()
            record=db.scalar(select(m.Audit).where(m.Audit.object_id==object_id,
                m.Audit.action==action,m.Audit.data['version'].as_integer()==data['version']))
            record.created_at=m.now()-timedelta(days=365)
        return result
    monkeypatch.setattr(subject,'audit',clock_rollback)
    body=save_body(current,reason='时钟回退仍保存真实人稿')
    saved=save(s,opened['id'],body)
    assert saved['current_draft']['version']==67 and saved['current_matches']
    assert read(s,opened['id'])==saved['current_draft']
    assert save(s,opened['id'],body)==saved


def test_original_open_nonce_cannot_recreate_an_empty_draft_when_original_row_is_missing(review_scene):
    s=review_scene;opened,body=open_saved(s)
    save(s,opened['id'],save_body(opened,reason='必须保留的私人审稿劳动'))
    with Session(engine()) as db:
        db.delete(db.get(Draft,opened['id']));db.commit()
    with pytest.raises(HTTPException) as failure:open_saved(s,body)
    assert failure.value.status_code==409 and '未重新创建空稿' in failure.value.detail
    with Session(engine()) as db:
        assert db.scalar(select(Draft).where(Draft.id==opened['id'])) is None
        assert db.scalar(select(m.Audit).where(m.Audit.object_id==opened['id'])) is not None


def test_caller_rollback_discards_uncommitted_draft_and_no_service_implicitly_commits(review_scene):
    s = review_scene; body = open_body(s)
    with Session(engine()) as db:
        transient = subject.open_draft(db, s.p, s.source_id, body)
        assert db.in_transaction()
        db.rollback()
    with Session(engine()) as db:
        assert db.get(Draft, transient['id']) is None
        assert db.scalar(select(m.Audit.id).where(m.Audit.object_id == transient['id'],
            m.Audit.action == subject.OPEN_ACTION)) is None


@pytest.mark.parametrize('damage', ['bool_version', 'extra_value', 'source_extra', 'reason_oversize',
    'start_oversize', 'unknown_op', 'unknown_edit_field', 'wrong_path', 'relationships_v4', 'too_many', 'too_big'])
def test_private_input_is_bounded_closed_and_never_stores_whole_candidate_or_source(review_scene, damage):
    s = review_scene; opened, _ = open_saved(s); body = save_body(opened)
    if damage == 'bool_version': body['version'] = True
    elif damage == 'extra_value': body['value']['raw_candidate'] = s.raw['value']
    elif damage == 'source_extra': body['value']['source']['raw'] = s.raw
    elif damage == 'reason_oversize': body['value']['edits'][0]['reason'] = '理' * 1001
    elif damage == 'start_oversize': body['value']['edits'][0]['start_text'] = '字' * 31
    elif damage == 'unknown_op': body['value']['edits'][0]['op'] = 'replace_agent'
    elif damage == 'unknown_edit_field': body['value']['edits'][0]['role'] = 'admin'
    elif damage == 'wrong_path': body['value']['edits'][0]['path'] = '/businesses/0/steps/0/role'
    elif damage == 'relationships_v4': body['value']['edits'][0]['path'] = '/relationships/0/citations/0'
    elif damage == 'too_many': body['value']['edits'] *= 101
    else: body['value']['edits'] = [{'op': 'discard_item', 'path': '/objects/0', 'reason': '理' * 1000} for _ in range(100)]
    with pytest.raises((HTTPException, ValidationError)):
        save(s, opened['id'], body)
    assert read(s, opened['id']) == opened
