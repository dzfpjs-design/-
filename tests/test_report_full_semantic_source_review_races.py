"""Root-run owned PG: private source progress races and historical recovery.

Normal authentication, transactions and complete frozen-source gates are real.
The inherited document/OCR/provider transport alone is synthetic. No formal
source approval, model quality, physical restart or execution grant is proved.
"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Barrier
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import report_full_semantic_source_review as api
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, Membership
from generic_agents.report_full_semantic_source_review_models import FullSemanticSourceReview as Review
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline_v7_postgres import v7_scene, BASE
from tests.test_report_evidence_pipeline import reply
from tests.test_report_evidence_pipeline_v3 import all_rows
from tests.test_report_full_semantic_handoff_security_postgres import authenticated
from tests.test_report_full_semantic_replacement_postgres import (
    command as replacement_command, evidence_snapshot, assert_evidence,
)
from tests.test_report_full_semantic_source_review_postgres import (
    full_source_scene, source_scene, command, ROOT,
)


def rows():
    return {name: {identity: json.loads(raw) for identity, raw in values.items()}
        for name, values in all_rows().items()}


def ref_owner(state):
    return next(owner for owner in state['manifest']['owners']
        if owner['ref_hashes'] and owner['evidence_status'] not in ('unknown', 'context'))


def one_progress_commit(before, after):
    assert {name for name in before if before[name] != after[name]} == {
        Review.__tablename__, Audit.__tablename__}
    for model in (Review, Audit):
        old, new = before[model.__tablename__], after[model.__tablename__]
        assert len(new) == len(old) + 1
        assert all(new[key] == value for key, value in old.items())
    return next(value for key, value in after[Review.__tablename__].items()
        if key not in before[Review.__tablename__])


def test_two_authenticated_sessions_serialize_owner_cas_and_same_nonce_once(source_scene, account):
    fence(); seed = source_scene
    client, path, draft, http = seed['client'], seed['path'], seed['report'], seed['http']
    state = reply(client.get(path)); owner = ref_owner(state)
    with ExitStack() as stack:
        sessions = [stack.enter_context(authenticated(client.app, account, account[2], 'admin')) for _ in range(2)]
        assert sessions[0].cookies.get('ga_session') != sessions[1].cookies.get('ga_session')
        evidence = evidence_snapshot(http, draft)

        def race(bodies):
            barrier = Barrier(2)
            def post(session, body):
                barrier.wait(timeout=10)
                return session.post(path + '/entries', json=body)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(post, session, body)
                    for session, body in zip(sessions, bodies, strict=True)]
                return [future.result(timeout=30) for future in futures]

        # Both clients read the same current owner version. The losing nonce
        # must not overwrite the winner or be recorded as an accepted save.
        first = command(state, owner, version=0, note='合成会话甲的私人来源判断')
        second = command(state, owner, version=0, note='合成会话乙的独立私人判断')
        before = rows(); responses = race((first, second))
        assert sorted(response.status_code for response in responses) == [200, 409]
        index = next(i for i, response in enumerate(responses) if response.status_code == 200)
        winner = reply(responses[index]); accepted = (first, second)[index]
        loser = responses[1-index]
        assert loser.json()['detail']['code'] == 'source_review_conflict'
        inserted = one_progress_commit(before, rows())
        assert inserted['version'] == 1 and inserted['request_id'] == accepted['request_id']
        assert inserted['data']['command'] == accepted and inserted['data']['receipt'] == winner['receipt']
        assert winner['receipt']['version'] == 1
        assert reply(client.get(path + '/requests/' + (first, second)[1-index]['request_id']))['found'] is False
        history = reply(client.get(path + '/history', params={'owner_path': owner['path']}))['items']
        assert len(history) == 1 and history[0]['value'] == accepted['value']
        checkpoint = rows()
        original = reply(sessions[1-index].post(path + '/entries', json=accepted))
        assert original['receipt'] == winner['receipt'] and rows() == checkpoint

        # A genuinely new owner version races with the identical nonce/body.
        # Both responses must recover one immutable receipt, with one audit row.
        same = command(state, owner, version=1, decision='needs_change', note='新发现的问题只保存一个版本🙂')
        before = rows(); responses = race((same, copy.deepcopy(same)))
        assert [response.status_code for response in responses] == [200, 200]
        replies = [reply(response) for response in responses]
        assert replies[0]['receipt'] == replies[1]['receipt']
        inserted = one_progress_commit(before, rows())
        assert inserted['version'] == 2 and inserted['data']['command'] == same
        assert inserted['data']['receipt'] == replies[0]['receipt']
        history = reply(client.get(path + '/history', params={'owner_path': owner['path']}))['items']
        assert [item['version'] for item in history] == [1, 2]
        assert [item['value'] for item in history] == [accepted['value'], same['value']]
        old = reply(client.get(path + '/requests/' + accepted['request_id']))
        assert old['found'] is True and old['current_matches'] is False
        assert old['receipt'] == winner['receipt']
        assert replies[0]['can_confirm'] is replies[0]['can_publish'] is False
        assert_evidence(http, draft, evidence)


def test_revocation_after_auth_before_final_actor_lock_cannot_append_progress(source_scene, monkeypatch, account):
    fence(); seed = source_scene
    client, path, draft, http = seed['client'], seed['path'], seed['report'], seed['http']
    state = reply(client.get(path)); owner = ref_owner(state)
    accepted_body = command(state, owner, decision='pending', note='撤权前已保存的私人核对劳动')
    accepted = reply(client.post(path + '/entries', json=accepted_body))
    original_context = api.context

    # The wrapper calls the real context/source checks, then changes only an
    # independent auth row. No caller DB Session is shared between connections.
    for mode in ('membership', 'session'):
        with authenticated(client.app, account, account[2], 'admin') as concurrent:
            fired, revoked_snapshot = [], []
            body = command(state, owner, version=1, decision='pending', note='认证之后撤权，本次内容不应写入：' + mode)
            def revoke_after_context(db, p, identity, *, lock=False):
                result = original_context(db, p, identity, lock=lock)
                if lock and not fired:
                    assert p.role == 'admin'
                    with Session(engine()) as independent:
                        if mode == 'membership':
                            member = independent.scalar(select(Membership).where(
                                Membership.user_id == p.user_id, Membership.workspace_id == p.workspace_id))
                            assert member.role == 'admin'; member.role = 'viewer'
                        else:
                            session = independent.get(AuthSession, p.session_id)
                            assert session.user_id == p.user_id and session.revoked is False
                            session.revoked = True
                        independent.commit()
                    fired.append((p.user_id, p.workspace_id, p.session_id))
                    revoked_snapshot.append(rows())
                return result
            evidence = evidence_snapshot(http, draft)
            try:
                with monkeypatch.context() as patch:
                    patch.setattr(api, 'context', revoke_after_context)
                    response = concurrent.post(path + '/entries', json=body)
                assert response.status_code == 403 and len(fired) == len(revoked_snapshot) == 1
                assert rows() == revoked_snapshot[0]  # Auth change survives; review/audit/config do not change.
                if mode == 'membership':
                    assert reply(concurrent.get('/api/me'))['role'] == 'viewer'
                    old = reply(concurrent.get(path + '/requests/' + accepted_body['request_id']))
                    assert old['found'] is True and old['receipt'] == accepted['receipt']
                    assert reply(concurrent.get(path))['reviews'][0]['version'] == 1
                else:
                    assert concurrent.get(path + '/requests/' + accepted_body['request_id']).status_code == 401
                assert_evidence(http, draft, evidence)
            finally:
                # Restore only the role deliberately changed by this synthetic
                # race. The revoked test session stays revoked permanently.
                if mode == 'membership' and fired:
                    with Session(engine()) as independent:
                        member = independent.scalar(select(Membership).where(
                            Membership.user_id == fired[0][0], Membership.workspace_id == fired[0][1]))
                        assert member.role == 'viewer'; member.role = 'admin'; independent.commit()
            checkpoint = rows()
            assert reply(client.get(path + '/requests/' + body['request_id']))['found'] is False
            assert rows() == checkpoint


def test_real_replacement_preserves_old_receipt_as_inactive_and_rejects_new_old_head_writes(source_scene):
    fence(); seed = source_scene
    client, path, draft, http = seed['client'], seed['path'], seed['report'], seed['http']
    state = reply(client.get(path)); owner = ref_owner(state)
    body = command(state, owner, note='这份旧共享版本的核对劳动应原样保留')
    saved = reply(client.post(path + '/entries', json=body))
    evidence = evidence_snapshot(http, draft)
    old_rows = copy.deepcopy(rows()[Review.__tablename__])

    # Normal manual save and explicit v110 preview/consent create a real new
    # shared head; no report/model/source value is edited by SQL or a stub.
    value = copy.deepcopy(seed['value'])
    value['result']['questions'].append('人工新增待核事项，仅用于核对共享版本失效恢复。')
    value['note'] += '后来私人说明仍不共享'
    manual = reply(client.put(BASE + '/' + draft['id'] + '/review', json={
        'revision': draft['revision'], 'request_id': str(uuid4()), 'value': value}))
    assert manual['draft']['review_value'] == value
    assert manual['draft']['model_result'] == draft['model_result']
    config = reply(client.get('/api/setup/draft'))
    replacement_body = replacement_command(manual, config, seed['record'])
    report_path = BASE + '/' + draft['id']
    preview = reply(client.post(report_path + '/full-configuration-replacement-preview', json=replacement_body))
    assert preview['changes']['changed_sections'] == ['questions']
    replacement = reply(client.post(report_path + '/full-configuration-replacement', json={
        **replacement_body, 'preview_hash': preview['preview_hash'], 'consent': True}))
    assert replacement['is_active'] is replacement['current_matches'] is True
    new_path = ROOT + replacement['record']['id']
    checkpoint = rows()
    old = reply(client.get(path)); current = reply(client.get(new_path))
    assert old['active'] is False and current['active'] is True
    assert old['manifest'] == state['manifest'] and old['reviews'] == saved['reviews']
    assert current['reviews'] == []
    assert current['manifest']['handoff_id'] == replacement['record']['id']
    assert current['manifest']['manifest_hash'] != old['manifest']['manifest_hash']
    for key in ('source_hash', 'catalog_hash'):
        assert current['manifest'][key] == old['manifest'][key]
    recovered = reply(client.get(path + '/requests/' + body['request_id']))
    assert recovered['found'] is True and recovered['current_matches'] is False
    assert recovered['receipt'] == saved['receipt'] and recovered['entry']['value'] == body['value']
    replay = reply(client.post(path + '/entries', json=body))
    assert replay['active'] is False and replay['receipt'] == saved['receipt']
    stale = command(state, owner, version=1, decision='needs_change', note='旧head不能用新编号继续写')
    response = client.post(path + '/entries', json=stale)
    assert response.status_code == 409 and response.json()['detail']['code'] == 'source_review_stale'
    assert reply(client.get(path + '/requests/' + stale['request_id']))['found'] is False
    response = client.post(new_path + '/entries', json=stale)
    assert response.status_code == 409 and response.json()['detail']['code'] == 'source_review_stale'
    changed = copy.deepcopy(body); changed['value']['note'] += '不得借旧编号重签'
    response = client.post(path + '/entries', json=changed)
    assert response.status_code == 409 and response.json()['detail']['code'] == 'source_review_nonce_conflict'
    assert reply(client.get(path + '/history', params={'owner_path': owner['path']}))['items'] == [
        {**recovered['entry'], 'receipt': recovered['receipt']}]
    assert old['can_confirm'] is old['can_publish'] is current['can_confirm'] is current['can_publish'] is False
    assert rows() == checkpoint and rows()[Review.__tablename__] == old_rows
    assert_evidence(http, draft, evidence)
