"""Original-request GET with real scopes, graph checks and immutable file reads.

The source inventory/saved_execution and current goal/route decisions are
explicit IO boundaries. The real context, nonce selection, full public graph
projection, DOCX parser and final evidence path/SHA checks run. Session and
Connection are the scope fixture's spies; this is not PostgreSQL/HTTP proof.
"""
import copy
import inspect
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from typing import get_args
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import workflow_graph_api as api, workspace_api
from generic_agents import workflow_graph as runtime
from generic_agents import workflow_graph_goal_view_v133 as goal_view
from generic_agents import signal_graph_routing_v134 as routing
from generic_agents import native_report_reading as reading
from generic_agents import report_full_source_read_scope as full_scope
from generic_agents import report_text_parse_scope as text_scope
from generic_agents.setup_api import DB
from generic_agents.signal_dual_read_session import read_session
from tests.test_business_flow_runtime_pure_dedup import scene as binding_scene
from tests.test_report_full_source_read_scope import scene as source_scene, uid
from tests.test_report_text_parse_scope import doc


@pytest.fixture
def case(source_scene, monkeypatch):
    s = source_scene
    c = binding_scene(monkeypatch, with_ability=False)
    c.snapshot['profile'] = {'domain': '公开合成核验业务'}
    graph = c.binding['graph']
    state = runtime.graph_api.new_run(graph, {'source_input': '已保存原输入'}, 10)
    activation_id = state['ready_human_ids'][0]
    node = runtime.node_for(graph, state['activations'][activation_id])
    s.p.user_id = node['actor_id']
    nonce = UUID(uid('saved-human-draft'))
    state = runtime.graph_api.save_draft(graph, state, activation_id,
        {'note': '尚待补充的原人工草稿', 'number': 1.0}, str(nonce), s.p.user_id,
        state['activations'][activation_id]['version'], state['cancel_epoch'], 10)
    task = NS(id=uid('request-task'), workspace_id=s.p.workspace_id,
        owner_id=s.p.user_id, version=3, status='workflow_waiting',
        title='原人工草稿核对', created_at=datetime(2026, 10, 24, tzinfo=timezone.utc),
        data={'graph_runtime': state, 'graph_jobs': {}})
    expected_command = copy.deepcopy(state['commands'][0])
    body = doc('公开合成原文：此次仅核对已经保存的人工草稿。')
    parse_calls, io = [], []
    flags = {'source_valid': True, 'damage': None}
    actual_parser = reading._read_docx_uncached

    def counted_parser(content):
        parse_calls.append(content)
        return actual_parser(content)

    def source_read():
        s.resolve()
        reading.read_docx(body)

    def saved_execution(db, p, identity, lock=False):
        assert db is s.db and p is s.p and identity == task.id
        assert lock is False, 'request recovery must retain the read-only context'
        io.append(('saved_execution', lock))
        if not flags['source_valid']:
            raise HTTPException(409, 'original source failed its current inventory boundary')
        source_read()
        return task, c.release, c.business

    def project_goal(db, p, selected):
        assert db is s.db and p is s.p and selected is task
        io.append(('goal',))
        source_read()
        return None

    def project_route(db, p, selected):
        assert db is s.db and p is s.p and selected is task
        io.append(('route',))
        source_read()
        if flags['damage']:
            s.paths[uid(flags['damage'])].write_bytes(b'changed before GET returns')
        return None

    monkeypatch.setattr(workspace_api, 'saved_execution', saved_execution)
    monkeypatch.setattr(goal_view, 'project', project_goal)
    monkeypatch.setattr(routing, 'task_state', project_route)
    monkeypatch.setattr(reading, '_read_docx_uncached', counted_parser)
    return NS(source=s, binding=c, task=task, nonce=nonce, command=expected_command,
        activation_id=activation_id, parse_calls=parse_calls, io=io, flags=flags)


def test_only_original_request_get_declares_the_attested_snapshot_dependency():
    signature = inspect.signature(api.request)
    assert signature.parameters['request_id'].annotation is UUID
    assert get_args(signature.parameters['db'].annotation)[1].dependency is read_session
    route = next(row for row in api.router.routes if row.endpoint is api.request)
    assert route.methods == {'GET'}
    assert route.path == '/tasks/{identity}/graph/requests/{request_id}'
    assert {item.name for item in route.dependant.path_params} == {'identity', 'request_id'}
    # In particular the read-only preparation POST and every write keep their
    # original dependency; this change cannot attest a writer as a read GET.
    for endpoint in (api.command_hash, api.draft, api.complete, api.cancel):
        assert inspect.signature(endpoint).parameters['db'].annotation is DB


def test_real_get_preserves_original_command_and_full_projection_then_finishes_files(case):
    c = case; s = c.source
    before = copy.deepcopy((c.task.data, c.binding.release.data))
    with s.permit():
        result = api.request(c.task.id, c.nonce, s.p, s.db)
        assert full_scope._CURRENT.get() is None and text_scope._active() is None
        assert set(s.file_reads) == set(s.rows), 'file checks complete before endpoint returns'
    assert set(result) == {'schema', 'found', 'request', 'task'}
    assert result['schema'] == 'workflow-graph-request-recovery-v1' and result['found'] is True
    assert result['request'] == c.command
    assert result['task']['graph_runtime'] == before[0]['graph_runtime']
    assert result['task']['business'] == c.binding.business
    assert result['task']['roles'] == c.binding.snapshot['roles']
    assert result['task']['graph_pending_humans'][0]['activation_id'] == c.activation_id
    assert result['task']['graph_pending_humans'][0]['draft'] == {'note': '尚待补充的原人工草稿', 'number': 1.0}
    assert c.io == [('saved_execution', False), ('goal',), ('route',)]
    assert len(s.loads) == len(c.parse_calls) == 1
    assert (c.task.data, c.binding.release.data) == before
    assert not s.db.commit.called
    assert all(call.args[0]._for_update_arg is None for call in s.db.scalar.call_args_list)
    # A later GET is a new operation, even while this synthetic external
    # snapshot permit is reused: no DTO or parser result crosses the boundary.
    with s.permit():
        again = api.request(c.task.id, c.nonce, s.p, s.db)
    assert again == result and len(s.loads) == len(c.parse_calls) == 2


def test_unknown_nonce_preserves_found_false_and_still_checks_the_full_task(case):
    c = case; s = c.source
    with s.permit():
        result = api.request(c.task.id, UUID(uid('unknown-original-nonce')), s.p, s.db)
    assert result['schema'] == 'workflow-graph-request-recovery-v1'
    assert result['found'] is False and result['request'] is None
    assert result['task']['graph_runtime'] == c.task.data['graph_runtime']
    assert c.io == [('saved_execution', False), ('goal',), ('route',)]
    assert set(s.file_reads) == set(s.rows) and not s.db.commit.called


def test_original_nonce_owner_still_rejects_before_public_projection(case):
    c = case; s = c.source
    s.p.user_id = uid('different-current-actor')
    with s.permit(), pytest.raises(HTTPException) as error:
        api.request(c.task.id, c.nonce, s.p, s.db)
    assert error.value.status_code == 403
    assert c.io == [('saved_execution', False)]
    assert full_scope._CURRENT.get() is None and text_scope._active() is None
    assert not s.db.commit.called


@pytest.mark.parametrize('family', ['graph_retry_requests', 'graph_wait_retries'])
def test_multiple_nonce_families_still_refuse_ambiguous_saved_commands(case, family):
    c = case; s = c.source
    c.task.data[family] = {str(c.nonce): {'actor_id': s.p.user_id, 'body_hash': 'a' * 64}}
    with s.permit(), pytest.raises(HTTPException) as error:
        api.request(c.task.id, c.nonce, s.p, s.db)
    assert error.value.status_code == 409
    assert c.io == [('saved_execution', False)] and not s.db.commit.called


@pytest.mark.parametrize('evidence_name', ['word', 'png1'])
def test_changed_original_or_page_refuses_result_before_the_get_can_return(case, evidence_name):
    c = case; s = c.source; returned = []
    c.flags['damage'] = evidence_name
    with s.permit(), pytest.raises(HTTPException) as error:
        returned.append(api.request(c.task.id, c.nonce, s.p, s.db))
    assert error.value.status_code == 409 and returned == []
    assert c.io[-1] == ('route',) and len(s.loads) == 1
    assert full_scope._CURRENT.get() is None and text_scope._active() is None
    assert not s.db.commit.called


def test_current_source_failure_is_not_downgraded_to_a_cached_historical_reply(case):
    c = case; s = c.source
    with s.permit():
        api.request(c.task.id, c.nonce, s.p, s.db)
    c.flags['source_valid'] = False
    with s.permit(), pytest.raises(HTTPException) as error:
        api.request(c.task.id, c.nonce, s.p, s.db)
    assert error.value.status_code == 409
    assert error.value.detail == 'original source failed its current inventory boundary'
    assert len(s.loads) == 1 and not s.db.commit.called


def test_without_a_server_permit_full_source_uses_the_original_complete_path(case):
    c = case; s = c.source
    result = api.request(c.task.id, c.nonce, s.p, s.db)
    assert result['found'] is True and result['request'] == c.command
    assert c.io == [('saved_execution', False), ('goal',), ('route',)]
    assert len(s.loads) == 3, 'no permit means every full source call runs its original loader'
    assert s.file_reads == [] and len(c.parse_calls) == 1
    assert full_scope._CURRENT.get() is None and text_scope._active() is None
    assert not s.db.commit.called
