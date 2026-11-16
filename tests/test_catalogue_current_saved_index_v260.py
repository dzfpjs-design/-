"""Real PG/API saved pointers, with explicit synthetic source/HTTP boundaries.

The existing M1/M2 fixture creates actual owner, catalogue, fork, Start, Job,
Call and independent Audits. Only its immutable-source admission and provider
HTTP are synthetic. These tests do not prove original-report or model quality.
An empty filtered page never establishes no activity or new-call authority.
Root executes this file; authoring it does not constitute a passing test run.
"""
import copy
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import catalogue_current_saved_index_v260 as index
from generic_agents import catalogue_current_saved_record_v260 as saved
from generic_agents import catalogue_current_allocation_v260 as current
from generic_agents import report_saved_map_design_consumer_v176 as consumer
from tests.test_catalogue_current_saved_record_v260 import saved_scene, poison_full_paths
from tests.test_catalogue_current_allocation_v260 import (
    allocation_scene, completed, quote, start, owned_inventory, Consumer, Command,
    BASE, ok, h, advance, other_client)
from tests.test_catalogue_current_context_v260 import reuse_scene, fork, binding

TOP = {'schema', 'workspace_id', 'actor_id', 'producer_id', 'binding_id', 'offset',
    'limit', 'has_more', 'records', 'verification'}
RECORD = {'consumer_id', 'version', 'status', 'note', 'request_sha256', 'start_receipt', 'state_hash'}
VERIFICATION = {'command_state_anchors_checked': True,
    'complete_operational_inventory_checked': False, 'source_revalidated': False,
    'files_revalidated': False, 'current_configuration_revalidated': False,
    'can_request_new_call': False, 'can_apply': False, 'can_publish': False}


def path(s):
    return BASE + '/saved-records/by-producer/' + s.origin['metadata']['producer_id']


def get_index(s, *, client=None, binding_id=None, offset=0, limit=20, status=200):
    before = owned_inventory(s); posts = copy.deepcopy(s.posts)
    response = (client or s.client).get(path(s), params={
        'binding_id': binding_id or s.binding['id'], 'offset': offset, 'limit': limit})
    assert response.status_code == status, response.text[:500]
    assert owned_inventory(s) == before and s.posts == posts
    return response


def test_one_saved_pointer_is_exact_readonly_rr_without_full_or_answer_paths(saved_scene, monkeypatch):
    s = saved_scene; flow = s.saved_flow
    advance(s, '索引只定位已保存原请求，不重授当前配置')
    s.source_unavailable = True
    reads = []; real_index = index.saved_index
    def observed(db, principal, producer_id, binding_id, **kwargs):
        reads.append((db.scalar(text('SHOW transaction_read_only')),
            db.scalar(text('SHOW transaction_isolation'))))
        return real_index(db, principal, producer_id, binding_id, **kwargs)
    monkeypatch.setattr(index, 'saved_index', observed)
    poison_full_paths(monkeypatch)
    def no_answer(*_args, **_kwargs):
        raise AssertionError('index is not a complete paid-answer or Call validator')
    monkeypatch.setattr(saved, '_call', no_answer)
    monkeypatch.setattr(saved, '_answer', no_answer)
    value = get_index(s).json()
    assert reads and all(v == ('on', 'repeatable read') for v in reads)
    assert set(value) == TOP and value['schema'] == index.SCHEMA
    assert value['workspace_id'] == s.p.workspace_id and value['actor_id'] == s.p.user_id
    assert value['producer_id'] == s.origin['metadata']['producer_id'] and value['binding_id'] == s.binding['id']
    assert value['offset'] == 0 and value['limit'] == 20 and value['has_more'] is False
    assert value['verification'] == VERIFICATION and len(value['records']) == 1
    record = value['records'][0]
    assert set(record) == RECORD and record['consumer_id'] == flow.created['id']
    assert record['start_receipt'] == flow.created and record['request_sha256'] == h(flow.original)
    assert record['version'] == flow.view['version'] and record['status'] == flow.view['status']
    assert record['note'] == flow.body['note']
    with Session(s.bound) as db:
        audit = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
            m.Audit.action == consumer.STATE_AUDIT, m.Audit.object_id == flow.created['id'],
            m.Audit.data['version'].as_integer() == flow.view['version']))
        assert record['state_hash'] == audit.data['state_hash']


def test_latest_page_then_earlier_page_retains_queued_history_and_original_receipts(saved_scene):
    s = saved_scene; first = s.saved_flow
    second = completed(s)
    body, preview = quote(s); _, original, third = start(s, body, preview)
    latest = get_index(s, limit=1).json()
    assert latest['has_more'] is True and latest['records'][0]['consumer_id'] == third['id']
    assert latest['records'][0]['status'] == 'queued'
    assert latest['records'][0]['start_receipt'] == third
    assert latest['records'][0]['request_sha256'] == h(original)
    earlier = get_index(s, offset=1, limit=1).json()
    assert earlier['has_more'] is True and earlier['records'][0]['consumer_id'] == second.created['id']
    oldest = get_index(s, offset=2, limit=1).json()
    assert oldest['has_more'] is False and oldest['records'][0]['consumer_id'] == first.created['id']
    empty = get_index(s, offset=3, limit=1).json()
    assert empty['records'] == [] and empty['has_more'] is False
    assert empty['verification'] == VERIFICATION  # Never means no paid/in-flight activity.
    assert get_index(s, limit=1).json()['records'] == latest['records']


def test_binding_filter_keeps_real_separate_working_copy_history_distinct(saved_scene):
    s = saved_scene; old_binding = copy.deepcopy(s.binding); old = s.saved_flow
    advance(s, '另一配置的新工作副本')
    _, _, receipt = fork(s)
    s.binding = binding(s, receipt); s.working_id = receipt['working_draft_ref']['id']
    assert s.binding['id'] != old_binding['id']
    newer = completed(s)
    assert [v['consumer_id'] for v in get_index(s).json()['records']] == [newer.created['id']]
    assert [v['consumer_id'] for v in get_index(s, binding_id=old_binding['id']).json()['records']] == [old.created['id']]
    unrelated = get_index(s, binding_id=str(uuid4())).json()
    assert unrelated['records'] == [] and unrelated['verification']['can_request_new_call'] is False


def test_noncurrent_declared_record_is_not_read_as_a_current_saved_pointer(saved_scene):
    s = saved_scene; body, preview = quote(s); _, _, other = start(s, body, preview)
    with Session(s.bound) as db:
        row = db.get(Consumer, other['id']); value = copy.deepcopy(row.data)
        # A filtering fault, not a fixture claiming a valid legacy conversion.
        # The index is intentionally not a whole-actor/legacy inventory reader.
        value['preview']['generation_profile'] = consumer.ALLOCATION
        row.data = value; db.commit()
    result = get_index(s).json()
    assert [r['consumer_id'] for r in result['records']] == [s.saved_flow.created['id']]
    assert result['verification']['complete_operational_inventory_checked'] is False


def test_viewer_reads_own_page_while_foreign_and_inactive_accounts_get_no_owned_content(saved_scene):
    s = saved_scene
    with Session(s.bound) as db:
        member = db.scalar(select(m.Membership).where(m.Membership.workspace_id == s.p.workspace_id,
            m.Membership.user_id == s.p.user_id)); member.role = 'viewer'; db.commit()
    with other_client(s, same_actor=True) as viewer:
        assert ok(viewer.get('/api/me'))['role'] == 'viewer'
        assert len(get_index(s, client=viewer).json()['records']) == 1
    with other_client(s) as foreign:
        response = get_index(s, client=foreign)
        assert response.json()['records'] == []
        assert s.saved_flow.original.decode() not in response.text
        assert s.saved_flow.created['id'] not in response.text
    with Session(s.bound) as db:
        db.get(m.User, s.p.user_id).active = False; db.commit()
    before = owned_inventory(s)
    response = s.client.get(path(s), params={'binding_id': s.binding['id']})
    assert response.status_code in {401, 403}
    assert s.saved_flow.created['id'] not in response.text and owned_inventory(s) == before


@pytest.mark.parametrize('damage', ['missing-state', 'missing-command-audit', 'bad-job-owner'])
def test_one_bad_selected_anchor_blocks_the_whole_page_instead_of_disappearing(saved_scene, damage):
    s = saved_scene; cid = s.saved_flow.created['id']
    with Session(s.bound) as db:
        row = db.get(Consumer, cid)
        if damage == 'missing-state':
            event = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
                m.Audit.action == consumer.STATE_AUDIT, m.Audit.object_id == cid,
                m.Audit.data['version'].as_integer() == row.version)); db.delete(event)
        elif damage == 'missing-command-audit':
            command = db.scalar(select(Command).where(Command.workspace_id == s.p.workspace_id,
                Command.consumer_id == cid))
            event = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
                m.Audit.action == consumer.COMMAND_AUDIT, m.Audit.object_id == command.id)); db.delete(event)
        else:
            job = db.get(m.Job, row.job_id); job.data = {**job.data, 'actor_id': str(uuid4())}
        db.commit()
    response = get_index(s, status=409)
    assert 'records' not in response.json() and response.json().get('found') is not False


@pytest.mark.parametrize('params', [{'offset': -1}, {'limit': 0}, {'limit': 21}])
def test_invalid_paging_does_not_turn_into_an_unbounded_inventory_query(saved_scene, params):
    s = saved_scene; before = owned_inventory(s)
    response = s.client.get(path(s), params={'binding_id': s.binding['id'], **params})
    assert response.status_code == 422 and owned_inventory(s) == before
