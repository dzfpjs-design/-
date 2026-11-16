"""Normal native source and immutable annotations, not model-quality evidence.

Root executes these in its isolated PG/native environment. Only the Annotation
insert timestamp is controlled, before the row and its real Audit are created;
no saved row, origin hash or Audit is rewritten to manufacture timestamp ties.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, func, select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents import models as m
from generic_agents.pixel_annotation_models_v256 import PixelAnnotationV256 as Annotation, PixelAnnotationCommandV256 as Command
from generic_agents.worker import process_once
from tests.test_pixel_annotations_native_pg_v256 import actual, native_scene, body_for, save
from tests.test_pixel_change_native_pg_v249 import pytestmark
from tests.test_pixel_change_pg_v249 import create


def inventory(s):
    with Session(engine()) as db:
        run = db.get(m.PixelChangeRun, s.run['id'])
        classes = [Annotation, Command, m.Audit, m.Evidence, m.Job, m.Invocation,
            m.Observation, m.Event, m.Memory, m.Task, m.CloudCall, m.Entity, m.PixelChangeRun]
        result = {cls.__name__: db.scalar(select(func.count()).select_from(cls).where(cls.workspace_id == run.workspace_id))
            for cls in classes}
        result['source_hash'] = digest({'id': run.id, 'status': run.status, 'version': run.version, 'data': run.data})
        result['entity_hash'] = digest([{'id': row.id, 'version': row.version, 'data': row.data}
            for row in db.scalars(select(m.Entity).where(m.Entity.workspace_id == run.workspace_id).order_by(m.Entity.id))])
        return result


@pytest.fixture
def history_scene(actual):
    s = actual
    clock = {'at': datetime.now(timezone.utc)}
    def annotation_insert_clock(_mapper, _connection, target):
        target.created_at = clock['at']
    event.listen(Annotation, 'before_insert', annotation_insert_clock)
    try:
        s.annotation_clock = clock; s.records = []; s.no_inference_before = inventory(s)
        latest = None
        for index in range(23):
            value = save(s, body_for(s.run, supersedes_annotation_id=latest,
                note='分页工程验证版本' + str(index + 1) + '；不是独立质量标注'))
            latest = value['id']; s.records.append(value)
        yield s
    finally:
        event.remove(Annotation, 'before_insert', annotation_insert_clock)


def page(s, *, before_id=None, limit=10, expected=200, headers=None):
    params = {'limit': limit}
    if before_id is not None:
        params['before_id'] = before_id
    response = s.client.get(s.annotation_base + '/annotations/history', params=params, headers=headers)
    assert response.status_code == expected, response.text
    if expected != 200:
        return response
    value = response.json()
    assert set(value) == {'schema', 'workspace_id', 'instance_id', 'run_id', 'before_id', 'items', 'has_more', 'next_cursor'}
    assert value['schema'] == 'pixel-annotation-history-v257' and value['instance_id'] == s.instance['id']
    assert value['run_id'] == s.run['id'] and value['before_id'] == before_id
    assert type(value['has_more']) is bool and len(value['items']) <= limit
    assert value['next_cursor'] == (value['items'][-1]['id'] if value['has_more'] else None)
    return value


def test_complete_keyset_history_ties_new_insert_and_old_api_are_durable_reads(history_scene):
    s = history_scene; state = inventory(s)
    expected_ids = sorted([value['id'] for value in s.records], reverse=True)
    assert len({value['created_at'] for value in s.records}) == 1
    first = page(s, limit=7)
    assert [value['id'] for value in first['items']] == expected_ids[:7]
    assert first['has_more'] and inventory(s) == state
    old_response = s.client.get(s.annotation_base + '/annotations', params={'limit': 20})
    assert old_response.status_code == 200, old_response.text
    old = old_response.json()
    assert set(old) == {'schema', 'workspace_id', 'instance_id', 'run_id', 'items', 'has_more'}
    assert old['schema'] == 'pixel-annotation-list-v256' and old['has_more'] is True
    assert old['items'] == page(s, limit=20)['items'] and inventory(s) == state
    # Newer insert is a genuine new immutable API revision, not a rewritten row.
    s.annotation_clock['at'] += timedelta(microseconds=1)
    added = save(s, body_for(s.run, supersedes_annotation_id=s.records[-1]['id'], note='第一页之后明确新存的一版'))
    after_write = inventory(s)
    values = list(first['items']); cursor = first['next_cursor']
    while cursor is not None:
        current = page(s, before_id=cursor, limit=7)
        values.extend(current['items']); cursor = current['next_cursor']
    assert [value['id'] for value in values] == expected_ids
    assert len({value['id'] for value in values}) == 23 and added['id'] not in {value['id'] for value in values}
    assert page(s)['items'][0]['id'] == added['id']
    empty = page(s, before_id=values[-1]['id'])
    assert empty['items'] == [] and empty['has_more'] is False and empty['next_cursor'] is None
    assert inventory(s) == after_write
    for key in ['Job', 'Invocation', 'Observation', 'Event', 'Memory', 'Task', 'CloudCall', 'Entity', 'PixelChangeRun', 'source_hash', 'entity_hash']:
        assert after_write[key] == s.no_inference_before[key]


def test_cursor_validation_and_workspace_scope_never_reset_first_page(history_scene):
    s = history_scene; before = inventory(s)
    for cursor in ['', 'not-a-canonical-cursor', uuid4().hex, 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA', 'nul-cursor-\x00']:
        page(s, before_id=cursor, expected=422)
    for limit in [0, 21]:
        page(s, limit=limit, expected=422)
    page(s, before_id=str(uuid4()), expected=404)
    with Session(engine()) as db:
        run = db.get(m.PixelChangeRun, s.run['id'])
        workspace = m.Workspace(name='独立分页权限反例'); db.add(workspace); db.flush()
        db.add(m.Membership(user_id=run.owner_id, workspace_id=workspace.id, role='admin'))
        workspace_id = workspace.id; db.commit()
    page(s, before_id=s.records[0]['id'], expected=404, headers={'X-Workspace-ID': workspace_id})
    assert inventory(s) == before


def test_cursor_from_a_second_real_completed_run_is_rejected(history_scene):
    s = history_scene
    other = create(s, request_id='history-other-run-' + uuid4().hex)
    assert process_once(job_id=other['job_id'], deliver=False)
    response = s.client.get(s.base + '/runs/' + other['id'])
    assert response.status_code == 200, response.text
    other = response.json(); assert other['status'] == 'succeeded'
    s.annotation_clock['at'] = datetime.now(timezone.utc)
    second = SimpleNamespace(**{**vars(s), 'run': other, 'annotation_base': s.base + '/runs/' + other['id']})
    foreign = save(second, body_for(other))
    before = inventory(s)
    page(s, before_id=foreign['id'], expected=404)
    page(second, before_id=s.records[-1]['id'], expected=404)
    assert inventory(s) == before


@pytest.mark.parametrize('damage', ['missing_audited_cursor', 'cursor_artifact_metadata'])
def test_broken_cursor_proof_is_blocked_without_writes_or_fallback(history_scene, damage):
    s = history_scene
    with Session(engine()) as db:
        if damage == 'missing_audited_cursor':
            # Delete only the leaf and its FK command; retain both independent
            # Audits so source inventory must reject instead of paging anew.
            target = db.get(Annotation, s.records[-1]['id'])
            cursor = target.id
            db.execute(delete(Command).where(Command.annotation_id == target.id))
            db.delete(target)
        else:
            target = db.get(Annotation, s.records[10]['id']); cursor = target.id
            evidence = db.get(m.Evidence, target.data['artifacts']['labels']['evidence_id'])
            evidence.size_bytes += 1
        db.commit()
    before = inventory(s)
    page(s, before_id=cursor, expected=409)
    assert inventory(s) == before
