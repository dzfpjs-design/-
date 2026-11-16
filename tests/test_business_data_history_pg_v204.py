"""Read-only operator history and explicit original daily-ACK recovery APIs."""
import copy
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import business_data as b, business_data_receipts as receipts, models as m
from generic_agents.business_data_models import BusinessDataRead as Read, BusinessDataImport as Import
from generic_agents.db import engine
from tests.test_business_data_gateway_pg import (scene, source, upload, ready, run, response, dataset,
    target, binding, preview, confirm, BASE)


def row_counts(workspace):
    with Session(engine()) as db:
        return tuple(db.scalar(select(func.count()).select_from(model).where(model.workspace_id == workspace))
            for model in (m.Audit, m.Job, m.Event, m.Memory, m.Entity, Read, Import))


def test_read_history_keeps_old_failure_after_success_and_get_never_writes_or_reads_files(scene, monkeypatch):
    from generic_agents import business_data_reader as reader
    s = scene; src = source(s); actual_parser = reader.parse_dataset
    captured = response(upload(s, src, dataset([{'id': 'a', 'title': '资料', 'quantity': 1}]))[0], 201)
    def fail(*_args): raise reader.ReaderError('本次读取期间连接或执行批准已变化；原读取保留，须明确恢复')
    monkeypatch.setattr(reader, 'parse_dataset', fail)
    assert run(s, captured['id'], 'read_business_data')[0] == 'failed'
    failed = response(s.client.get(BASE + '/reads/' + captured['id']))
    before = response(s.client.get(BASE + '/reads/' + captured['id'] + '/history'))
    assert any(item['status'] == 'failed' and '读取期间连接版本' in item['detail'] for item in before['entries'])
    monkeypatch.setattr(reader, 'parse_dataset', actual_parser)
    request = {'request_id': str(uuid4()), 'expected_version': failed['version'], 'reason': '明确恢复原读取'}
    response(s.client.post(BASE + '/reads/' + captured['id'] + '/retry', json=request))
    assert run(s, captured['id'], 'read_business_data')[0] == 'succeeded'
    monkeypatch.setattr(b, 'evidence_bytes', lambda *_args: (_ for _ in ()).throw(AssertionError('History must not read files')))
    count = row_counts(s.workspace)
    history = response(s.client.get(BASE + '/reads/' + captured['id'] + '/history'))
    assert row_counts(s.workspace) == count
    assert history['schema'] == 'business-data-history-v204' and history['subject_type'] == 'read'
    assert history['subject_id'] == captured['id'] and history['continuation']['count'] == 0
    assert any(item['label'] == '资料解析完成' for item in history['entries'])
    assert any(item['label'] == '已明确重试原读取' for item in history['entries'])
    assert any(item['status'] == 'failed' and '读取期间连接版本' in item['detail'] for item in history['entries'])
    assert all(set(item) == {'id', 'at', 'actor', 'label', 'detail', 'status'} for item in history['entries'])
    shown = ' '.join(item['detail'] for item in history['entries'])
    assert request['request_id'] not in shown and all(word not in shown for word in ('lease_token', 'job_id', 'secret_ref'))


def test_history_viewer_can_read_inactive_original_actor_but_foreign_subject_is_404(scene):
    from generic_agents.security import password_hash
    s = scene; src = source(s); read, _ = ready(s, src, dataset([{'id': 'a', 'title': '历史', 'quantity': 1}]))
    username, password = 'history_viewer_' + uuid4().hex, uuid4().hex
    with Session(engine()) as db:
        viewer = m.User(username=username, password_hash=password_hash(password)); db.add(viewer); db.flush()
        db.add(m.Membership(user_id=viewer.id, workspace_id=s.workspace, role='viewer'))
        foreign = m.Workspace(name='外域历史'); db.add(foreign); db.flush()
        db.add(m.Membership(user_id=s.p.user_id, workspace_id=foreign.id, role='admin')); foreign_id = foreign.id; db.commit()
    other_src = response(s.client.post(BASE + '/sources', headers={'X-Workspace-ID': foreign_id},
        json={'request_id': str(uuid4()), 'name': '外域资料', 'kind': 'json'}), 201)
    raw = dataset([{'id': 'b', 'title': '外域', 'quantity': 2}])
    import hashlib
    foreign_read = response(s.client.post(BASE + '/sources/' + other_src['id'] + '/uploads',
        headers={'X-Workspace-ID': foreign_id}, data={'request_id': str(uuid4()), 'expected_version': '1',
            'file_name': 'foreign.json', 'file_size': str(len(raw)), 'file_sha256': hashlib.sha256(raw).hexdigest()},
        files={'file': ('foreign.json', raw)}), 201)
    login = response(s.client.post('/api/login', json={'username': username, 'password': password}))
    s.client.headers.update({'X-CSRF-Token': login['csrf_token']})
    with Session(engine()) as db:
        db.get(m.User, s.p.user_id).active = False; db.commit()
    history = response(s.client.get(BASE + '/reads/' + read['id'] + '/history'))
    assert history['entries'] and all(item['actor'] != username for item in history['entries'])
    assert s.client.get(BASE + '/reads/' + foreign_read['id'] + '/history').status_code == 404
    assert s.client.post(BASE + '/reads/' + read['id'] + '/retry', json={'request_id': str(uuid4()),
        'expected_version': read['version'], 'reason': '只读者不得恢复'}).status_code in {403, 409}


def test_history_keyset_pages_real_failures_without_row_noise_and_strict_subject_cursor(scene):
    s = scene; src = source(s)
    read = response(upload(s, src, b'not-json')[0], 201)
    for index in range(8):
        assert run(s, read['id'], 'read_business_data')[0] == 'failed'
        failed = response(s.client.get(BASE + '/reads/' + read['id']))
        if index < 7:
            response(s.client.post(BASE + '/reads/' + read['id'] + '/retry', json={
                'request_id': str(uuid4()), 'expected_version': failed['version'], 'reason': '原件格式待核实的明确重试'}))
    first = response(s.client.get(BASE + '/reads/' + read['id'] + '/history'))
    assert len(first['entries']) == 20 and first['next_cursor']
    second = response(s.client.get(BASE + '/reads/' + read['id'] + '/history', params={'cursor': first['next_cursor']}))
    assert second['entries'] and second['next_cursor'] is None
    all_entries = first['entries'] + second['entries']
    assert len({item['id'] for item in all_entries}) == len(all_entries)
    assert sum(item['label'] == '读取或解析失败' for item in all_entries) == 8
    assert [(item['at'], item['id']) for item in all_entries] == sorted(
        [(item['at'], item['id']) for item in all_entries], reverse=True)
    assert s.client.get(BASE + '/reads/' + read['id'] + '/history', params={'cursor': '!invalid'}).status_code == 422
    other = response(upload(s, src, b'also-not-json')[0], 201)
    assert s.client.get(BASE + '/reads/' + other['id'] + '/history', params={'cursor': first['next_cursor']}).status_code == 422


def test_history_continuation_requires_original_job_closure_and_not_just_counter(scene):
    s = scene; src = source(s); read, _ = ready(s, src, dataset([{'id': 'a', 'title': '资料', 'quantity': 1}]))
    with Session(engine()) as db:
        saved = db.get(Read, read['id']); job = db.get(m.Job, saved.data['job_id'])
        job.data = {**job.data, 'lease_recoveries': 77, 'business_data_id': str(uuid4())}; db.commit()
    history = response(s.client.get(BASE + '/reads/' + read['id'] + '/history'))
    assert history['continuation']['count'] is None and history['continuation']['notice']
    assert any(item['label'] == '资料解析完成' for item in history['entries'])


def test_import_history_is_stage_level_and_completed_recovery_history_is_read_only(scene):
    s = scene; src = source(s); release, kind, fields = target(s)
    read, _ = ready(s, src, dataset([{'id': str(index), 'title': '记录', 'quantity': index} for index in range(21)]))
    bind = binding(s, src, read, release, kind, fields); batch = confirm(s, preview(s, bind, read))
    before = row_counts(s.workspace)
    history = response(s.client.get(BASE + '/imports/' + batch['id'] + '/history'))
    assert row_counts(s.workspace) == before and history['subject_type'] == 'import'
    assert history['continuation']['count'] == 0 and history['next_cursor'] is None
    assert {item['label'] for item in history['entries']} == {'已提交导入预览', '导入预览已保存', '已明确确认导入'}
    assert len(history['entries']) == 3  # 21 real rows must not become 21 audit rows in the human timeline.


def test_daily_blocked_original_event_needs_explicit_retry_with_same_nonce_and_scope(scene):
    s = scene; src = source(s); ready(s, src, dataset([{'id': 'a', 'title': '原资料', 'quantity': 0}]))
    receipts.schedule_once(s.workspace)
    with Session(engine()) as db:
        event = db.scalar(select(m.Event).where(m.Event.workspace_id == s.workspace, m.Event.kind == 'business_data.daily'))
        event_id = event.id
        anchor = db.scalar(select(m.Audit).where(m.Audit.object_id == event.id, m.Audit.action == receipts.DAY_AUDIT))
        audit_id = anchor.id; original = copy.deepcopy(anchor.data)
        anchor.data = {'damaged': True}; db.commit()
    receipts.consume_once(s.workspace)
    day = next(item for item in response(s.client.get(BASE + '/sources/' + src['id'] + '/days')) if item['id'] == event_id)
    assert day['source_id'] == src['id'] and day['data_delivery']['can_retry'] and day['status'] == 'blocked_delivery'
    body = {'request_id': str(uuid4()), 'expected_version': day['version'], 'reason': '恢复原日整理接收'}
    path = BASE + '/sources/' + src['id'] + '/days/' + event_id + '/retry-data-delivery'
    before = row_counts(s.workspace)
    assert s.client.post(path, json=body).status_code == 409
    assert row_counts(s.workspace) == before
    with Session(engine()) as db:
        db.get(m.Audit, audit_id).data = original; db.commit()
    receipts.consume_once(s.workspace)
    unchanged = next(item for item in response(s.client.get(BASE + '/sources/' + src['id'] + '/days')) if item['id'] == event_id)
    assert unchanged['status'] == 'blocked_delivery'  # No automatic reset after evidence repair.
    retried = response(s.client.post(path, json=body))
    assert retried['id'] == event_id and retried['source_id'] == src['id'] and retried['status'] == 'pending_delivery'
    saved_counts = row_counts(s.workspace)
    assert response(s.client.post(path, json=body)) == retried
    assert row_counts(s.workspace) == saved_counts
    receipts.consume_once(s.workspace)
    received = next(item for item in response(s.client.get(BASE + '/sources/' + src['id'] + '/days')) if item['id'] == event_id)
    assert received['status'] == 'received' and received['data_delivery']['verified']
    assert s.client.post(path, json={**body, 'reason': '同nonce换内容必须拒绝'}).status_code == 409
    with Session(engine()) as db:
        foreign = m.Workspace(name='外域日整理'); db.add(foreign); db.flush()
        db.add(m.Membership(user_id=s.p.user_id, workspace_id=foreign.id, role='admin')); foreign_id = foreign.id; db.commit()
    assert s.client.post(path, headers={'X-Workspace-ID': foreign_id}, json={
        **body, 'request_id': str(uuid4())}).status_code == 404
