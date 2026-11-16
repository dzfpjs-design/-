"""Normal V204 APIs and real scoped jobs against Root's disposable PostgreSQL.

No database selection, service, socket or runtime configuration is created here.
Only HTTP transport/intentional failure seams are replaced; parsers, leases,
object mutations, independent receipts and originals are real.
"""
import copy
import hashlib
import io
import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import business_data as b, business_data_reader as reader
from generic_agents import business_data_receipts as receipts, models as m, worker
from generic_agents.business_data_models import BusinessDataSource as Source, BusinessDataRead as Read
from generic_agents.business_data_models import BusinessDataImport as Import, BusinessDataImportRow as ImportRow
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.security import Principal
from generic_agents.services import update_entity
from tests.test_instance_workspace import prepared, enable

BASE = '/api/business-data'


@pytest.fixture
def scene(client, account):
    bound = engine()
    assert bound.dialect.name == 'postgresql' and bound.url.database.startswith('ga_test_')
    with Session(bound) as db:
        user = db.scalar(select(m.User).where(m.User.username == account[0]))
        p = Principal(user.id, account[2], 'admin', 'business-data-pg')
    return NS(client=client, workspace=account[2], p=p)


def response(value, status=200):
    assert value.status_code == status, value.text
    return value.json()


def source(s, kind='json'):
    return response(s.client.post(BASE + '/sources', json={'request_id': str(uuid4()),
        'name': '公开业务资料', 'kind': kind}), 201)


def upload(s, src, raw, name='dataset.json', *, request=None):
    body = request or {'request_id': str(uuid4()), 'expected_version': src['version'],
        'file_name': name, 'file_size': len(raw), 'file_sha256': hashlib.sha256(raw).hexdigest()}
    result = s.client.post(BASE + '/sources/' + src['id'] + '/uploads',
        data={key: str(value) for key, value in body.items()}, files={'file': (name, raw)})
    return result, body


def run(s, identity, kind, *, model=Read):
    with Session(engine()) as db:
        row = db.get(model, identity)
        job_id = row.data['import_job_id' if kind == 'import_business_data' else 'job_id']
    worker.process_once(job_id, kinds=[kind], deliver=False, workspace_id=s.workspace)
    with Session(engine()) as db:
        job = db.get(m.Job, job_id)
        return job.status, copy.deepcopy(job.data)


def ready(s, src, raw, name='dataset.json'):
    result, body = upload(s, src, raw, name)
    read = response(result, 201)
    assert run(s, read['id'], 'read_business_data')[0] == 'succeeded'
    return response(s.client.get(BASE + '/reads/' + read['id'])), body


def target(s):
    release, _, _ = prepared(s.client); enable(s.client, release)
    choices = response(s.client.get(BASE + '/instances/' + release['instance_id'] + '/options'))
    kind = next(item for item in choices['object_types'] if item['label'] == '档案条目')
    fields = {item['label']: item['key'] for item in kind['fields']}
    assert set(fields) == {'标题', '数量'}  # Never display generated field IDs as human labels.
    return release, kind, fields


def binding(s, src, read, release, kind, fields, record_set=''):
    return response(s.client.post(BASE + '/bindings', json={'request_id': str(uuid4()),
        'source_id': src['id'], 'source_expected_version': src['version'], 'read_id': read['id'],
        'instance_id': release['instance_id'], 'release_id': release['id'], 'object_type': kind['key'],
        'schema_hash': kind['schema_hash'], 'record_set': record_set, 'external_id_path': '/id',
        'fields': [{'target': fields['标题'], 'source': '/title', 'conversion': 'identity'},
            {'target': fields['数量'], 'source': '/quantity', 'conversion': 'integer'}]}), 201)


def preview(s, bind, read):
    value = response(s.client.post(BASE + '/bindings/' + bind['id'] + '/previews',
        json={'request_id': str(uuid4()), 'read_id': read['id']}), 201)
    assert run(s, value['id'], 'preview_business_data', model=Import)[0] == 'succeeded'
    return response(s.client.get(BASE + '/imports/' + value['id']))


def confirm(s, batch, partial=False):
    body = {'request_id': str(uuid4()), 'expected_version': batch['version'],
        'confirm_hash': batch['confirm_hash'], 'accept_partial': partial}
    result = response(s.client.post(BASE + '/imports/' + batch['id'] + '/confirm', json=body))
    run(s, batch['id'], 'import_business_data', model=Import)
    return response(s.client.get(BASE + '/imports/' + result['id']))


def dataset(records):
    return json.dumps(records, ensure_ascii=False, separators=(',', ':')).encode()


@pytest.mark.parametrize('kind,set_key', [('json', ''), ('csv', 'csv'), ('xlsx', 'sheet:业务记录')])
def test_actual_upload_parse_dynamic_mapping_import_and_independent_ack(scene, kind, set_key):
    s = scene; src = source(s, kind)
    if kind == 'json': raw = dataset([{'id': 'record-1', 'title': '原始业务记录', 'quantity': 0}])
    elif kind == 'csv': raw = 'id,title,quantity\r\nrecord-1,原始业务记录,0\r\n'.encode()
    else:
        book = Workbook(); sheet = book.active; sheet.title = '业务记录'
        sheet.append(['id', 'title', 'quantity']); sheet.append(['record-1', '原始业务记录', 0])
        output = io.BytesIO(); book.save(output); raw = output.getvalue()
    read, _ = ready(s, src, raw, 'dataset.' + kind)
    assert s.client.get(BASE + '/reads/' + read['id'] + '/original').content == raw
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(m.Entity).where(m.Entity.workspace_id == s.workspace)) == 0
    release, object_kind, fields = target(s)
    bind = binding(s, src, read, release, object_kind, fields, set_key)
    assert bind['read_id'] == read['id'] and bind['record_set'] == set_key
    batch = preview(s, bind, read)
    assert batch['rows'][0]['name'] == '原始业务记录' and batch['counts']['new'] == 1
    batch = confirm(s, batch)
    assert batch['status'] == 'completed' and batch['counts']['imported'] == 1
    assert batch['counts']['data_received'] == 0
    receipts.consume_once(s.workspace)
    received = response(s.client.get(BASE + '/imports/' + batch['id']))
    assert received['counts']['data_received'] == 1 and received['rows'][0]['data_delivery']['verified']
    receipts.schedule_once(s.workspace); receipts.consume_once(s.workspace)
    days = response(s.client.get(BASE + '/sources/' + src['id'] + '/days'))
    assert days and days[0]['counts']['imported'] == days[0]['counts']['data_received'] == 1
    assert days[0]['data_delivery']['verified']
    with Session(engine()) as db:
        entity = db.get(m.Entity, received['rows'][0]['entity_id'])
        assert entity.data == {fields['标题']: '原始业务记录', fields['数量']: 0}
        assert db.scalar(select(func.count()).select_from(m.Invocation).where(m.Invocation.workspace_id == s.workspace)) == 0


def test_upload_original_nonce_checks_actual_bytes_even_after_source_pause(scene):
    s = scene; src = source(s); raw = b'[{"id":"a","title":"A","quantity":0}]'
    result, body = upload(s, src, raw); original = response(result, 201)
    response(s.client.post(BASE + '/sources/' + src['id'] + '/pause', json={
        'request_id': str(uuid4()), 'expected_version': src['version'], 'reason': '保留历史原件'}))
    replay, _ = upload(s, src, raw, request=body)
    assert response(replay, 201)['id'] == original['id']
    altered = raw.replace(b'"A"', b'"B"'); assert len(altered) == len(raw)
    different, _ = upload(s, src, altered, request=body)
    assert different.status_code == 409
    assert s.client.get(BASE + '/reads/' + original['id'] + '/original').content == raw


def test_three_way_preserves_human_value_and_conflict_is_not_silent_overwrite(scene):
    s = scene; src = source(s); release, kind, fields = target(s)
    read, _ = ready(s, src, dataset([{'id': 'a', 'title': '原名', 'quantity': 1}]))
    bind = binding(s, src, read, release, kind, fields)
    first = confirm(s, preview(s, bind, read)); entity_id = first['rows'][0]['entity_id']
    with Session(engine()) as db:
        entity = db.get(m.Entity, entity_id)
        update_entity(db, s.p, entity.id, {**entity.data, fields['标题']: '人工修正'}, entity.version, '真实人工修改')
        db.commit()
    second, _ = ready(s, src, dataset([{'id': 'a', 'title': '原名', 'quantity': 2}]))
    applied = confirm(s, preview(s, bind, second))
    assert applied['status'] == 'completed'
    with Session(engine()) as db:
        assert db.get(m.Entity, entity_id).data == {fields['标题']: '人工修正', fields['数量']: 2}
    third, _ = ready(s, src, dataset([{'id': 'a', 'title': '来源新名', 'quantity': 3}]))
    conflict = preview(s, bind, third)
    assert conflict['counts']['conflict'] == 1
    rejected = s.client.post(BASE + '/imports/' + conflict['id'] + '/confirm', json={
        'request_id': str(uuid4()), 'expected_version': conflict['version'],
        'confirm_hash': conflict['confirm_hash'], 'accept_partial': False})
    assert rejected.status_code == 409
    receipts.consume_once(s.workspace)  # Historical receipts survive later legitimate Entity heads.
    assert response(s.client.get(BASE + '/imports/' + first['id']))['counts']['data_received'] == 1


def test_status_alone_neither_proves_data_ack_nor_completed_import(scene):
    s = scene; src = source(s); read, _ = ready(s, src, dataset([{'id': 'a', 'title': '记录', 'quantity': 1}]))
    release, kind, fields = target(s); bind = binding(s, src, read, release, kind, fields)
    batch = confirm(s, preview(s, bind, read))
    with Session(engine()) as db:
        row = db.scalar(select(ImportRow).where(ImportRow.import_id == batch['id']))
        event = db.get(m.Event, row.data['event_id']); event.status = 'received'; event.received_at = m.now(); db.commit()
    view = response(s.client.get(BASE + '/imports/' + batch['id']))
    assert view['counts']['data_received'] == 0
    assert view['rows'][0]['data_delivery']['status'] == 'integrity_error'
    other = preview(s, bind, read)
    with Session(engine()) as db:
        value = db.get(Import, other['id']); value.status = 'completed'; db.commit()
    view = response(s.client.get(BASE + '/imports/' + other['id']))
    assert view['status'] == 'failed' and view['counts']['imported'] == 0
    with Session(engine()) as db:
        with pytest.raises(HTTPException): b.verify_committed_rows(db, s.p, db.get(Import, other['id']))


def http_source(s, monkeypatch):
    from generic_agents import gateway
    monkeypatch.setattr(gateway, 'validate_endpoint', lambda *_args, **_kwargs: None)
    return response(s.client.post(BASE + '/sources', json={'request_id': str(uuid4()),
        'name': '受控HTTP资料', 'kind': 'http_json', 'endpoint': 'https://public.example/data'}), 201)


def test_source_pause_resume_aba_during_read_is_blocked_then_explicit_retry_works(scene, monkeypatch):
    s = scene; src = http_source(s, monkeypatch)
    read = response(s.client.post(BASE + '/sources/' + src['id'] + '/read', json={
        'request_id': str(uuid4()), 'expected_version': src['version']}), 201)
    raw = dataset([{'id': 'a', 'title': '记录', 'quantity': 1}]); changed = []
    def fetch(_execution, *, before_request, on_response):
        before_request()
        if not changed:
            paused = response(s.client.post(BASE + '/sources/' + src['id'] + '/pause', json={
                'request_id': str(uuid4()), 'expected_version': src['version'], 'reason': '读取期间暂停'}))
            response(s.client.post(BASE + '/sources/' + src['id'] + '/resume', json={
                'request_id': str(uuid4()), 'expected_version': paused['version'], 'reason': '明确恢复同连接'}))
            changed.append(True)
        on_response(raw, {'content_type': 'application/json', 'status_code': 200})
        return NS(content=raw, media_type='application/json', headers={}, received_at=m.now().isoformat())
    monkeypatch.setattr(reader, 'fetch_json', fetch)
    assert run(s, read['id'], 'read_business_data')[0] == 'failed'
    with Session(engine()) as db:
        old = db.get(Read, read['id']); assert old.evidence_id is None
    failed = response(s.client.get(BASE + '/reads/' + read['id']))
    retry = {'request_id': str(uuid4()), 'expected_version': failed['version'], 'reason': '明确恢复原读取'}
    response(s.client.post(BASE + '/reads/' + read['id'] + '/retry', json=retry))
    assert run(s, read['id'], 'read_business_data')[0] == 'succeeded'
    assert s.client.get(BASE + '/reads/' + read['id'] + '/original').content == raw
    assert response(s.client.post(BASE + '/reads/' + read['id'] + '/retry', json=retry))['id'] == read['id']
    with Session(engine()) as db:
        attempts = list(db.scalars(select(m.Audit).where(m.Audit.action == b.READ_ATTEMPT, m.Audit.object_id == read['id'])))
        assert len(attempts) == 2 and len({item.data['source_version'] for item in attempts}) == 2


def test_original_job_cannot_be_redirected_to_another_read(scene):
    s = scene; src = source(s); raw = dataset([{'id': 'a', 'title': '记录', 'quantity': 1}])
    one = response(upload(s, src, raw)[0], 201); two = response(upload(s, src, raw)[0], 201)
    with Session(engine()) as db:
        first = db.get(Read, one['id']); second = db.get(Read, two['id'])
        job = db.get(m.Job, first.data['job_id']); original_id = job.id
        job.data = {**job.data, 'business_data_id': second.id}; db.commit()
    worker.process_once(original_id, kinds=['read_business_data'], deliver=False, workspace_id=s.workspace)
    with Session(engine()) as db:
        assert db.get(m.Job, original_id).status == 'failed'
        assert db.get(m.Job, db.get(Read, two['id']).data['job_id']).status == 'queued'
        assert 'parsed' not in db.get(Read, two['id']).data


def test_workspace_claim_and_all_history_remain_scoped(scene):
    s = scene; src = source(s); read = response(upload(s, src,
        dataset([{'id': 'a', 'title': '记录', 'quantity': 1}]))[0], 201)
    with Session(engine()) as db:
        foreign = m.Workspace(name='不得触及的B'); db.add(foreign); db.flush(); foreign_id = foreign.id
        job_id = db.get(Read, read['id']).data['job_id']; db.commit()
    worker.process_once(job_id, kinds=['read_business_data'], deliver=False, workspace_id=foreign_id)
    receipts.consume_once(foreign_id); receipts.schedule_once(foreign_id)
    with Session(engine()) as db:
        assert db.get(m.Job, job_id).status == 'queued' and db.get(m.Job, job_id).attempts == 0
    headers = {'X-Workspace-ID': foreign_id}
    assert s.client.get(BASE + '/reads/' + read['id'], headers=headers).status_code in {403, 404}


def test_partial_checkpoint_resume_preserves_original_committed_entity_and_event(scene, monkeypatch):
    s = scene; src = source(s); release, kind, fields = target(s)
    read, _ = ready(s, src, dataset([{'id': 'a', 'title': '第一条', 'quantity': 1},
        {'id': 'b', 'title': '第二条', 'quantity': 2}]))
    bind = binding(s, src, read, release, kind, fields); batch = preview(s, bind, read)
    real_write = b._write_row
    def fail_second(db, p, current, mapping, captured, row, instance, target_release):
        if row.ordinal == 1: raise HTTPException(409, '明确的逐行失败测试')
        return real_write(db, p, current, mapping, captured, row, instance, target_release)
    monkeypatch.setattr(b, '_write_row', fail_second)
    partial = confirm(s, batch)
    assert partial['status'] == 'partial' and partial['counts']['imported'] == partial['counts']['failed'] == 1
    with Session(engine()) as db:
        committed = db.scalar(select(ImportRow).where(ImportRow.import_id == batch['id'], ImportRow.status == 'imported'))
        old_row = copy.deepcopy(committed.data); old_entity = db.get(m.Entity, old_row['entity_id'])
        pin = (old_entity.id, old_entity.version, copy.deepcopy(old_entity.data), old_row['event_id'])
    monkeypatch.setattr(b, '_write_row', real_write)
    request = {'request_id': str(uuid4()), 'expected_version': partial['version'], 'reason': '恢复原批次剩余失败行'}
    response(s.client.post(BASE + '/imports/' + batch['id'] + '/resume', json=request))
    assert run(s, batch['id'], 'import_business_data', model=Import)[0] == 'succeeded'
    completed = response(s.client.get(BASE + '/imports/' + batch['id']))
    assert completed['status'] == 'completed' and completed['counts']['imported'] == 2
    with Session(engine()) as db:
        old_entity = db.get(m.Entity, pin[0]); assert (old_entity.version, old_entity.data) == (pin[1], pin[2])
        assert db.get(ImportRow, committed.id).data == old_row
        assert db.scalar(select(func.count()).select_from(m.Event).where(m.Event.workspace_id == s.workspace,
            m.Event.kind == 'business_data.imported')) == 2
    assert response(s.client.post(BASE + '/imports/' + batch['id'] + '/resume', json=request))['id'] == batch['id']


def test_cancelled_confirmed_batch_resumes_same_job_without_duplicate_object(scene):
    s = scene; src = source(s); release, kind, fields = target(s)
    read, _ = ready(s, src, dataset([{'id': 'a', 'title': '原件', 'quantity': 1}]))
    bind = binding(s, src, read, release, kind, fields); batch = preview(s, bind, read)
    queued = response(s.client.post(BASE + '/imports/' + batch['id'] + '/confirm', json={
        'request_id': str(uuid4()), 'expected_version': batch['version'],
        'confirm_hash': batch['confirm_hash'], 'accept_partial': False}))
    cancelled = response(s.client.post(BASE + '/imports/' + batch['id'] + '/cancel', json={
        'request_id': str(uuid4()), 'expected_version': queued['version'], 'reason': '暂缓此批次'}))
    assert run(s, batch['id'], 'import_business_data', model=Import)[0] == 'cancelled'
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(m.Entity).where(m.Entity.workspace_id == s.workspace)) == 0
        job_id = db.get(Import, batch['id']).data['import_job_id']
    response(s.client.post(BASE + '/imports/' + batch['id'] + '/resume', json={
        'request_id': str(uuid4()), 'expected_version': cancelled['version'], 'reason': '明确恢复同批次'}))
    assert run(s, batch['id'], 'import_business_data', model=Import)[0] == 'succeeded'
    with Session(engine()) as db:
        assert db.get(Import, batch['id']).data['import_job_id'] == job_id
        assert db.scalar(select(func.count()).select_from(m.Entity).where(m.Entity.workspace_id == s.workspace)) == 1


def test_source_revocation_blocks_new_import_but_not_authentic_historical_ack(scene):
    s = scene; src = source(s); release, kind, fields = target(s)
    read, _ = ready(s, src, dataset([{'id': 'a', 'title': '原记录', 'quantity': 1}]))
    bind = binding(s, src, read, release, kind, fields)
    old = confirm(s, preview(s, bind, read))
    later, _ = ready(s, src, dataset([{'id': 'b', 'title': '不得新写', 'quantity': 2}]))
    batch = preview(s, bind, later)
    response(s.client.post(BASE + '/imports/' + batch['id'] + '/confirm', json={
        'request_id': str(uuid4()), 'expected_version': batch['version'],
        'confirm_hash': batch['confirm_hash'], 'accept_partial': False}))
    response(s.client.post(BASE + '/sources/' + src['id'] + '/pause', json={
        'request_id': str(uuid4()), 'expected_version': src['version'], 'reason': '撤销新的来源执行资格'}))
    assert run(s, batch['id'], 'import_business_data', model=Import)[0] == 'failed'
    receipts.consume_once(s.workspace)
    assert response(s.client.get(BASE + '/imports/' + old['id']))['counts']['data_received'] == 1
    assert s.client.get(BASE + '/reads/' + read['id'] + '/original').content
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(m.Entity).where(m.Entity.workspace_id == s.workspace)) == 1


def second_admin(s):
    from generic_agents.security import password_hash
    username = 'business_admin_' + uuid4().hex; password = uuid4().hex
    with Session(engine()) as db:
        user = m.User(username=username, password_hash=password_hash(password)); db.add(user); db.flush()
        db.add(m.Membership(user_id=user.id, workspace_id=s.workspace, role='admin')); db.commit(); actor = user.id
    return actor, username, password


def test_explicit_other_admin_retry_is_audited_with_original_actor_preserved(scene, monkeypatch):
    s = scene; src = http_source(s, monkeypatch)
    read = response(s.client.post(BASE + '/sources/' + src['id'] + '/read', json={
        'request_id': str(uuid4()), 'expected_version': src['version']}), 201)
    def fail(*_args, **_kwargs): raise reader.ReaderError('明确的读取失败测试')
    monkeypatch.setattr(reader, 'fetch_json', fail)
    assert run(s, read['id'], 'read_business_data')[0] == 'failed'
    failed = response(s.client.get(BASE + '/reads/' + read['id']))
    actor, username, password = second_admin(s)
    login = response(s.client.post('/api/login', json={'username': username, 'password': password}))
    s.client.headers.update({'X-CSRF-Token': login['csrf_token']})
    body = {'request_id': str(uuid4()), 'expected_version': failed['version'], 'reason': '接管并恢复原读取'}
    response(s.client.post(BASE + '/reads/' + read['id'] + '/retry', json=body))
    raw = dataset([{'id': 'a', 'title': '恢复', 'quantity': 0}])
    def fetch(_execution, *, before_request, on_response):
        before_request(); on_response(raw, {'content_type': 'application/json', 'status_code': 200})
        return NS(content=raw, media_type='application/json', headers={}, received_at=m.now().isoformat())
    monkeypatch.setattr(reader, 'fetch_json', fetch)
    assert run(s, read['id'], 'read_business_data')[0] == 'succeeded'
    with Session(engine()) as db:
        saved = db.get(Read, read['id']); job = db.get(m.Job, saved.data['job_id'])
        assert saved.actor_id == s.p.user_id and job.data['actor_id'] == actor
        auth = db.get(m.Audit, job.data['business_data_authorization_id'])
        assert auth.actor_id == actor and auth.data['request_hash'] == digest(body)


def test_original_job_actor_cannot_change_without_explicit_recovery_command(scene, monkeypatch):
    s = scene; src = http_source(s, monkeypatch)
    read = response(s.client.post(BASE + '/sources/' + src['id'] + '/read', json={
        'request_id': str(uuid4()), 'expected_version': src['version']}), 201)
    actor, _, _ = second_admin(s)
    with Session(engine()) as db:
        saved = db.get(Read, read['id']); job = db.get(m.Job, saved.data['job_id'])
        job.data = {**job.data, 'actor_id': actor}; db.commit()
    monkeypatch.setattr(reader, 'fetch_json', lambda *_args, **_kwargs: pytest.fail('Unaudited actor must not fetch'))
    assert run(s, read['id'], 'read_business_data')[0] == 'failed'
    with Session(engine()) as db:
        saved = db.get(Read, read['id'])
        assert saved.actor_id == s.p.user_id and saved.evidence_id is None
        assert not list(db.scalars(select(m.Audit).where(m.Audit.action == b.READ_ATTEMPT, m.Audit.object_id == saved.id)))


def test_blocked_ack_needs_explicit_original_event_retry_and_never_reimports(scene):
    s = scene; src = source(s); release, kind, fields = target(s)
    read, _ = ready(s, src, dataset([{'id': 'a', 'title': '已保存', 'quantity': 1}]))
    bind = binding(s, src, read, release, kind, fields); batch = confirm(s, preview(s, bind, read))
    with Session(engine()) as db:
        row = db.scalar(select(ImportRow).where(ImportRow.import_id == batch['id']))
        row_id = row.id; event = db.get(m.Event, row.data['event_id']); original = copy.deepcopy(event.data)
        event_id = event.id; entity = db.get(m.Entity, row.data['entity_id']); entity_pin = (entity.id, entity.version, copy.deepcopy(entity.data))
        event.data = {'schema': 'broken', 'receipt': {**original['receipt'], 'raw_sha256': '0' * 64}}; db.commit()
    receipts.consume_once(s.workspace)
    with Session(engine()) as db:
        event = db.get(m.Event, event_id); assert event.status == 'blocked_delivery'
        version = event.version; event.data = {**original, 'delivery_error': event.data['delivery_error']}; db.commit()
    receipts.consume_once(s.workspace)
    with Session(engine()) as db:
        assert db.get(m.Event, event_id).status == 'blocked_delivery'
    request = {'request_id': str(uuid4()), 'expected_version': version, 'reason': '恢复原接收回执依据后明确重试'}
    response(s.client.post(BASE + '/imports/' + batch['id'] + '/rows/' + row_id + '/retry-data-delivery', json=request))
    receipts.consume_once(s.workspace)
    view = response(s.client.get(BASE + '/imports/' + batch['id']))
    assert view['counts']['data_received'] == 1 and view['rows'][0]['data_delivery']['event_id'] == event_id
    with Session(engine()) as db:
        entity = db.get(m.Entity, entity_pin[0]); assert (entity.version, entity.data) == entity_pin[1:]
        assert db.scalar(select(func.count()).select_from(m.Event).where(m.Event.kind == 'business_data.imported',
            m.Event.workspace_id == s.workspace)) == 1


def test_projection_reuses_original_only_in_one_projection_and_transaction(scene, monkeypatch):
    s = scene; src = source(s); release, kind, fields = target(s)
    read, _ = ready(s, src, dataset([{'id': str(index), 'title': '业务记录', 'quantity': index} for index in range(4)]))
    bind = binding(s, src, read, release, kind, fields); batch = confirm(s, preview(s, bind, read))
    actual = b.evidence_bytes; calls = []
    def counted(*args, **kwargs): calls.append(args[2]); return actual(*args, **kwargs)
    monkeypatch.setattr(b, 'evidence_bytes', counted)
    response(s.client.get(BASE + '/imports/' + batch['id']))
    assert len(calls) == 1  # counts + four original row/ACK projections share the checked Read.
    response(s.client.get(BASE + '/imports/' + batch['id']))
    assert len(calls) == 2  # Never reuse a previous HTTP request's acceptance.
    with Session(engine()) as db:
        captured = db.get(Read, read['id'])
        with b.verification_scope(db):
            b.checked_read(db, s.p, captured); b.checked_read(db, s.p, captured)
            assert len(calls) == 3
            db.commit(); b.checked_read(db, s.p, captured)
            assert len(calls) == 4  # Commit invalidates even an explicitly nested projection.
