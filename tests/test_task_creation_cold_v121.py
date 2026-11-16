"""Cold retirement integrity, detached reads and actual PG corruption controls.

The PG case first creates a genuine retirement through its real API, then
corrupts only the identified stored field. No fake Task/source authority is
inserted. These are corruption seams, not claims of real storage corruption.
"""
import copy
from types import SimpleNamespace as NS
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents import task_creation_recovery_v120 as recovery
from generic_agents.workspace_api import NewTask, creation_body_hash
from tests.test_graph_wait_journey_v118 import (
    waiting_release, graph_seed, v7_scene, reading_scene, local_runtime, full_source_scene, reply,
)
from tests.test_graph_task_inputs_v120 import _body, _paths, _counts


def _cold():
    p = NS(user_id=str(uuid4()), workspace_id=str(uuid4()))
    original = {'release_id': str(uuid4()), 'business_id': str(uuid4()), 'entity_ids': [str(uuid4())],
        'title': '保留原输入🙂', 'request_id': str(uuid4()), 'graph_inputs': {'threshold': 1.0}}
    typed = NewTask.model_validate(original)
    row = NS(id=str(uuid4()), workspace_id=p.workspace_id, actor_id=p.user_id, instance_id=str(uuid4()),
        request_id=typed.request_id, body_hash=creation_body_hash(typed), version=1,
        data={'reason': '保留本次明确提交的原正文', 'original': copy.deepcopy(original)})
    row.command_hash = digest({'actor_id': row.actor_id, 'instance_id': row.instance_id,
        'original': original, 'reason': row.data['reason']})
    return p, row


def test_pure_cold_view_is_detached_and_preserves_actual_submitted_member_presence():
    p, row = _cold(); before = copy.deepcopy(row.__dict__)
    value = recovery.checked_view(row, p, row.body_hash)
    assert value['original'] == row.data['original']
    assert 'image_ids' not in value['original'] and 'goal_spec' not in value['original']
    assert type(value['original']['graph_inputs']['threshold']) is float
    value['original']['graph_inputs']['threshold'] = 2
    assert row.__dict__ == before


@pytest.mark.parametrize('change', ['reason', 'original', 'nonce', 'body_hash', 'command_hash',
    'extra', 'missing', 'workspace', 'version', 'boolean_version', 'coerced_original'])
def test_pure_unchecked_or_normalized_retirement_data_never_becomes_a_receipt(change):
    p, row = _cold(); signature = row.body_hash
    if change == 'reason': row.data['reason'] = '另一份未签入的理由'
    elif change == 'original': row.data['original']['title'] = '另一份未签入的正文'
    elif change == 'nonce': row.data['original']['request_id'] = str(uuid4())
    elif change in {'body_hash', 'command_hash'}: setattr(row, change, 'f' * 64)
    elif change == 'extra': row.data['extra'] = '不属于封闭记录'
    elif change == 'missing': row.data.pop('original')
    elif change == 'workspace': row.workspace_id = str(uuid4())
    elif change == 'version': row.version = 2
    elif change == 'boolean_version': row.version = True
    else:
        # NewTask would coerce an integer into comparison_confirmed. The stored
        # command itself must already be exactly the typed submitted value.
        row.data['original']['comparison_confirmed'] = 1
        row.body_hash = signature = creation_body_hash(NewTask.model_validate(row.data['original']))
        row.command_hash = digest({'actor_id': row.actor_id, 'instance_id': row.instance_id,
            'original': row.data['original'], 'reason': row.data['reason']})
    before = copy.deepcopy(row.__dict__)
    with pytest.raises(HTTPException) as caught: recovery.checked_view(row, p, signature)
    assert caught.value.status_code == 409 and row.__dict__ == before


@pytest.mark.parametrize('reason', ['', ' ', ' 未裁剪的理由 ', 'x' * 1001, True, [], '\x00', '\ud800'])
def test_pure_reason_shape_and_encoding_are_checked_even_if_hashes_were_resealed(reason):
    p, row = _cold(); row.data['reason'] = reason
    if reason != '\ud800':
        row.command_hash = digest({'actor_id': row.actor_id, 'instance_id': row.instance_id,
            'original': row.data['original'], 'reason': reason})
    with pytest.raises(HTTPException) as caught: recovery.checked_view(row, p, row.body_hash)
    assert caught.value.status_code == 409


def test_pure_foreign_actor_keeps_403_precedence_over_corrupt_private_data():
    p, row = _cold(); row.actor_id = str(uuid4()); row.data = None
    with pytest.raises(HTTPException) as caught: recovery.checked_view(row, p, row.body_hash)
    assert caught.value.status_code == 403


def test_actual_retirement_reason_original_and_hash_corruption_block_lookup_and_replay_without_execution(waiting_release):
    value = waiting_release; client = value['client']; _, _, lookup_path = _paths(value)
    original = _body(value); reason = '真实保存后独立冷核原撤回请求'
    retire_path = lookup_path.rsplit('/', 1)[0] + '/retire'
    command = {'original': original, 'reason': reason}
    accepted = reply(client.post(retire_path, json=command))
    with Session(engine()) as db:
        row = db.scalar(select(recovery.TaskCreationRetirement).where(
            recovery.TaskCreationRetirement.request_id == original['request_id']))
        identity = row.id
        baseline = {'data': copy.deepcopy(row.data), 'body_hash': row.body_hash,
            'command_hash': row.command_hash, 'version': row.version}
    producer = value['producer']
    reply(client.post('/api/setup/graph-event-producers/' + producer['id'] + '/revoke', json={
        'version': producer['version'], 'reason': '冷核保留记录不依赖当前来源执行健康'}))
    before = _counts()
    for change in ('reason', 'original', 'nonce', 'body_hash', 'command_hash', 'extra', 'missing', 'version'):
        with Session(engine()) as db:
            row = db.get(recovery.TaskCreationRetirement, identity)
            for key, content in baseline.items(): setattr(row, key, copy.deepcopy(content))
            data = copy.deepcopy(row.data)
            if change == 'reason': data['reason'] = '另一份未签入的真实字段'
            elif change == 'original': data['original']['title'] = '另一份未签入的原正文'
            elif change == 'nonce': data['original']['request_id'] = str(uuid4())
            elif change in {'body_hash', 'command_hash'}: setattr(row, change, 'f' * 64)
            elif change == 'extra': data['unexpected'] = '不属于封闭数据'
            elif change == 'missing': data.pop('original')
            else: row.version = 2
            row.data = data; db.commit()
            corrupted = {'data': copy.deepcopy(row.data), 'body_hash': row.body_hash,
                'command_hash': row.command_hash, 'version': row.version}
        assert client.post(lookup_path, json=original).status_code == 409, change
        assert client.post(retire_path, json=command).status_code == 409, change
        assert _counts() == before, change
        with Session(engine()) as db:
            row = db.get(recovery.TaskCreationRetirement, identity)
            assert {key: copy.deepcopy(getattr(row, key)) for key in corrupted} == corrupted
    # Restore only this intentional fixture corruption, never repair in the API.
    with Session(engine()) as db:
        row = db.get(recovery.TaskCreationRetirement, identity)
        for key, content in baseline.items(): setattr(row, key, copy.deepcopy(content))
        db.commit()
    restored = reply(client.post(lookup_path, json=original))
    assert restored['found'] is False and restored['retirement'] == accepted
    assert reply(client.post(retire_path, json=command)) == accepted
    assert _counts() == before
