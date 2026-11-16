"""Read-only guard bodies over labelled creator/Task/receipt/SQL boundaries.

The prior query fixture runs the actual frozen query and snapshot checker.
These are synthetic membership/registry/Task/model projections, not real PG,
IAM, SDK completion, adopted family, ACK completeness or target-runtime proof.
"""
import copy
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import or_, select

from generic_agents import models as m, workspace_api
from generic_agents import goal_evaluation_query_v288 as queries
from generic_agents import goal_evaluation_query_guard_v288 as subject
from generic_agents.contracts import Manifest, digest
from tests.test_goal_evaluation_query_v288 import scene as query_scene, same_sql, uid, Rows


@pytest.fixture
def scene(monkeypatch):
    s = query_scene.__wrapped__(monkeypatch)
    s.task.package_id = uid(5)
    s.task.data.update(instance_id=uid(3), instance_release_id=uid(4), business_id='read_data')
    s.release = NS(id=uid(4), instance_id=uid(3), package_id=uid(5))
    s.business = {'id': 'read_data'}
    data = Manifest.model_validate({'kind': 'tool', 'namespace': 'public_test', 'name': 'evaluation_reader',
        'version': '1.0.0', 'owner': 'synthetic registry boundary', 'description': 'Local synthetic query guard fixture.',
        'allowed_agent_roles': ['data'], 'execution': {'adapter': 'workflow_service',
            'service_actor': 'reader_service', 'operation': queries.OP, 'side_effect': True}}).model_dump(mode='json')
    s.cap = NS(id=uid(70), workspace_id=s.p.workspace_id, kind='tool', data=data,
        content_hash=digest(data), validated_hash=digest(data), status='published')
    s.job.workspace_id = s.p.workspace_id; s.job.kind = 'execute_full_semantic_graph_node'
    s.job.idempotency_key = 'graph:' + s.task.id + ':evaluation_activation:1'
    s.job.data = {'task_id': s.task.id, 'actor_id': s.p.user_id, 'activation_id': 'evaluation_activation',
        'graph_hash': 'a' * 64, 'input_hash': digest({'synthetic': 'creator input'}),
        'invocation_key': s.job.idempotency_key}
    s.call = NS(id=s.evaluation_id, workspace_id=s.p.workspace_id, capability_id=s.cap.id,
        idempotency_key=s.job.idempotency_key, status='running', data={'job_id': s.job.id,
            'role': 'data', 'side_effect': True, 'input_hash': s.job.data['input_hash']})
    s.scope = {'workspace_id': s.p.workspace_id, 'task_id': s.task.id, 'instance_id': s.release.instance_id,
        'release_id': s.release.id, 'package_id': s.release.package_id, 'business_id': s.business['id'],
        'graph_hash': s.job.data['graph_hash'], 'source_binding_hash': 'b' * 64,
        'goal_snapshot_hash': queries.hashed(queries.snapshot(s.task)),
        'native_goal_context_hash': s.task.data['native_goal_context_hash']}

    def creator(db, p, task, job, evaluation_id, policy):
        # Actual leased creator/publication/adopted binding is an explicit IO
        # boundary; never assert that this fixture installed or dispatched one.
        assert db is s.db and p is s.p and task is s.task and job.id == s.job.id
        assert evaluation_id == s.call.id
        identity = {'invocation_id': s.call.id, 'job_id': s.job.id, 'actor_id': s.p.user_id,
            'lease_token': s.token, 'activation_id': s.job.data['activation_id'], 'node_id': 'evaluation_packet',
            'capability_id': s.cap.id, 'capability_hash': s.cap.content_hash,
            'input_hash': s.job.data['input_hash'], 'request_key': s.job.idempotency_key}
        return s.task, copy.deepcopy(s.scope), identity, queries.contract.check_preparation_policy(s.goal, policy)
    monkeypatch.setattr(queries, '_assert_creator', creator)
    s.row = s.create(); s.row.created_at = s.trusted
    for model, row in ((m.Job, s.job), (m.Invocation, s.call), (m.Capability, s.cap)):
        s.db.rows[model, row.id] = row
    s.evaluations = []; s.task_reads = []
    original_scalars = s.db.scalars

    def scalars(statement):
        assert 'FOR UPDATE' not in str(statement), 'cold read acquired a write lock'
        query = select(subject.GoalEvaluationV288).where(
            subject.GoalEvaluationV288.workspace_id == s.p.workspace_id,
            subject.GoalEvaluationV288.evaluation_query_id == s.row.id).limit(2)
        if same_sql(statement, query): return Rows(s.evaluations)
        query = select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
            m.Audit.action.in_([queries.SAVED, 'workflow.goal_evaluation.query_saved_v299']),
            m.Audit.object_id == s.row.id).limit(2)
        if same_sql(statement, query): return Rows(s.db.events)
        query = select(m.GapCheck).where(m.GapCheck.workspace_id == s.p.workspace_id,
            m.GapCheck.data['query_id'].as_string().in_([s.row.id])).limit(subject.MAX_LINEAGE_ROWS + 1)
        if same_sql(statement, query): return Rows()
        query = select(m.DataGap).where(m.DataGap.workspace_id == s.p.workspace_id,
            or_(m.DataGap.query_id.in_([s.row.id]),
                m.DataGap.data['pending_recheck_query_id'].as_string().in_([s.row.id]),
                m.DataGap.data['latest_recheck_query_id'].as_string().in_([s.row.id]),
                m.DataGap.id.in_([]))).limit(subject.MAX_LINEAGE_ROWS + 1)
        if same_sql(statement, query): return Rows()
        return original_scalars(statement)
    monkeypatch.setattr(s.db, 'scalars', scalars)

    def saved(db, p, task_id, lock=False):
        assert db is s.db and p in (s.p, s.reader) and lock is False
        s.task_reads.append(task_id)
        if task_id != s.task.id: raise HTTPException(404, 'synthetic task denied')
        return s.task, s.release, s.business
    monkeypatch.setattr(workspace_api, 'saved_execution', saved)
    return s


def test_current_independent_reader_gets_only_cold_reference_without_claiming_completion(scene):
    s = scene; before = copy.deepcopy((s.row.data, s.task.data)); additions = list(s.db.additions)
    view = subject.visible(s.db, s.reader, s.row)
    assert s.reader.user_id != s.row.owner_id and s.task_reads == [s.job.data['task_id']]
    assert view['schema'] == 'data-query-reference-v144' and view['family'] == subject.FAMILY
    assert view['reference_only'] is True and view['context']['task_id'] == s.task.id
    assert view['context']['detail_path'] == '/api/workspace/tasks/' + s.task.id
    assert '生产任务已完成' in view['notice'] and '不表示' in view['notice']
    assert not {'request', 'result', 'provenance', 'creator', 'lease_token'} & set(view)
    assert s.call.status == 'running' and (s.row.data, s.task.data) == before and s.db.additions == additions


@pytest.mark.parametrize('anchor', ['audit', 'evaluation'])
def test_independent_anchor_restricts_generic_write_when_marker_is_removed(scene, anchor):
    s = scene; s.row.data = {key: value for key, value in s.row.data.items() if key != queries.KEY}
    if anchor == 'evaluation':
        s.db.events.clear(); s.evaluations.append(NS(evaluation_query_id=s.row.id))
    assert subject.managed(s.row) is False and subject.has_trace(s.db, s.p, s.row) is True
    with pytest.raises(HTTPException) as error: subject.reject_generic_write(s.db, s.p, s.row)
    assert error.value.status_code == 409 and error.value.detail == subject.WRITE_ERROR
    with pytest.raises(HTTPException): subject.visible(s.db, s.reader, s.row)


def test_null_or_unrecognized_marker_never_grants_read_or_generic_execution(scene):
    s = scene; s.row.data[queries.KEY] = None; s.db.events.clear()
    assert subject.managed(s.row) is True
    with pytest.raises(HTTPException): subject.visible(s.db, s.reader, s.row)
    with pytest.raises(HTTPException) as error: subject.reject_generic_write(s.db, s.p, s.row)
    assert error.value.status_code == 409


@pytest.mark.parametrize('damage', ['reader', 'audit', 'job', 'invocation', 'actor', 'capability', 'task_marker'])
def test_marker_and_self_hash_cannot_replace_current_iam_or_real_creator_scope(scene, damage):
    s = scene
    if damage == 'reader': s.read_allowed = False
    elif damage == 'audit': s.db.events.clear()
    elif damage == 'job': s.db.rows[m.Job, s.job.id] = None
    elif damage == 'invocation': s.db.rows[m.Invocation, s.call.id] = None
    elif damage == 'actor': s.job.data['actor_id'] = s.reader.user_id
    elif damage == 'capability': s.cap.data['execution']['operation'] = 'resense_from_context'
    else: s.row.data[queries.KEY]['scope']['task_id'] = uid(999)
    with pytest.raises(HTTPException): subject.visible(s.db, s.reader, s.row)
    with pytest.raises(HTTPException) as error: subject.reject_generic_write(s.db, s.p, s.row)
    assert error.value.status_code == 409 and error.value.detail == subject.WRITE_ERROR
    assert all(task_id == s.job.data['task_id'] for task_id in s.task_reads)


def test_optional_immutable_record_projection_must_match_same_query_and_creator(scene):
    s = scene; ref = queries.query_ref(s.row)
    record = NS(id=s.call.id, invocation_id=s.call.id, evaluation_query_id=s.row.id,
        task_id=s.task.id, job_id=s.job.id, actor_id=s.p.user_id, workspace_id=s.p.workspace_id,
        release_id=s.release.id, instance_id=s.release.instance_id, package_id=s.release.package_id,
        data={'evaluation_id': s.call.id, 'evaluation_query_ref': ref, 'scope': copy.deepcopy(s.scope)})
    s.evaluations.append(record)
    assert subject.visible(s.db, s.reader, s.row)['id'] == s.row.id
    record.actor_id = s.reader.user_id
    with pytest.raises(HTTPException): subject.visible(s.db, s.reader, s.row)


def test_unmanaged_generic_query_is_not_restricted_and_has_no_new_read_grant(scene):
    s = scene; s.row.data = {'request': {}, 'result': {}}; s.db.events.clear()
    assert subject.managed(s.row) is False and subject.has_trace(s.db, s.p, s.row) is False
    assert subject.reject_generic_write(s.db, s.p, s.row) is None
    with pytest.raises(HTTPException): subject.visible(s.db, s.reader, s.row)


def test_cross_workspace_row_never_becomes_a_reference(scene):
    s = scene; foreign = NS(id=s.row.id, workspace_id=uid(999), data=s.row.data)
    with pytest.raises(HTTPException) as error: subject.visible(s.db, s.reader, foreign)
    assert error.value.status_code == 404 and not s.task_reads


class LineageDB:
    """Exact scoped SQL shapes for a single-parent or independent Gap chain.

    This is a synthetic ancestry inventory, not target-runtime custody proof.
    It rejects write locks, unknown SELECTs and every query without workspace.
    """
    def __init__(self, p, rows, *, events=None, evaluations=None, checks=(), gaps=()):
        self.p = p; self.rows = {row.id: row for row in rows}
        self.events = events or {}; self.evaluations = evaluations or {}
        self.checks, self.gaps = list(checks), list(gaps); self.lookups = []
        self.identities = set(self.rows)
        for row in rows:
            if type(row.data) is dict and type(row.data.get('parent_query_id')) is str:
                self.identities.add(row.data['parent_query_id'])
        self.identities.update(gap.query_id for gap in gaps)

    @property
    def no_autoflush(self): return nullcontext()

    def scalars(self, statement):
        assert 'FOR UPDATE' not in str(statement)
        p = self.p
        for identity in sorted(self.identities):
            query = select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
                m.Audit.action.in_([queries.SAVED, 'workflow.goal_evaluation.query_saved_v299']),
                m.Audit.object_id == identity).limit(2)
            # Only the two fixed evaluation actions are in this SQL whitelist.
            # Ordinary lineage has no old or append witness and returns empty.
            if same_sql(statement, query): return Rows(self.events.get(identity, ()))
            query = select(subject.GoalEvaluationV288).where(
                subject.GoalEvaluationV288.workspace_id == p.workspace_id,
                subject.GoalEvaluationV288.evaluation_query_id == identity).limit(2)
            if same_sql(statement, query): return Rows(self.evaluations.get(identity, ()))
            query = select(m.DataRun).where(m.DataRun.workspace_id == p.workspace_id,
                m.DataRun.id.in_([identity])).limit(subject.MAX_LINEAGE_ROWS + 1)
            if same_sql(statement, query):
                self.lookups.append(identity)
                row = self.rows.get(identity)
                return Rows([row] if row is not None and row.workspace_id == p.workspace_id else [])
            checks = [row for row in self.checks if row.workspace_id == p.workspace_id
                and row.data['query_id'] == identity]
            query = select(m.GapCheck).where(m.GapCheck.workspace_id == p.workspace_id,
                m.GapCheck.data['query_id'].as_string().in_([identity])).limit(subject.MAX_LINEAGE_ROWS + 1)
            if same_sql(statement, query): return Rows(checks)
            row = self.rows.get(identity); explicit = []
            if row is not None and type(row.data.get('gap_recheck')) is dict:
                explicit = [row.data['gap_recheck']['gap_id']]
            gap_ids = sorted(set(explicit) | {check.gap_id for check in checks})
            query = select(m.DataGap).where(m.DataGap.workspace_id == p.workspace_id,
                or_(m.DataGap.query_id.in_([identity]),
                    m.DataGap.data['pending_recheck_query_id'].as_string().in_([identity]),
                    m.DataGap.data['latest_recheck_query_id'].as_string().in_([identity]),
                    m.DataGap.id.in_(gap_ids))).limit(subject.MAX_LINEAGE_ROWS + 1)
            if same_sql(statement, query):
                return Rows(gap for gap in self.gaps if gap.workspace_id == p.workspace_id
                    and (gap.query_id == identity or gap.id in gap_ids
                        or gap.data.get('pending_recheck_query_id') == identity
                        or gap.data.get('latest_recheck_query_id') == identity))
        raise AssertionError('Unknown or unscoped lineage SELECT')


def lineage(*, managed=True):
    p = NS(workspace_id=uid(1), user_id=uid(7), role='operator')
    root = NS(id=uid(201), workspace_id=p.workspace_id, data={queries.KEY: None} if managed else {})
    child = NS(id=uid(202), workspace_id=p.workspace_id, data={'parent_query_id': root.id},
        status='running', version=1)
    grandchild = NS(id=uid(203), workspace_id=p.workspace_id, data={'parent_query_id': child.id},
        status='running', version=1)
    return p, root, child, grandchild


@pytest.mark.parametrize('generation', [0, 1, 2])
@pytest.mark.parametrize('anchor', ['marker', 'audit', 'evaluation'])
def test_all_generations_inherit_the_real_evaluation_restriction(generation, anchor):
    p, root, child, grandchild = lineage(managed=anchor == 'marker')
    events = {root.id: [NS(id=uid(210))]} if anchor == 'audit' else {}
    evaluations = {root.id: [NS(id=uid(211))]} if anchor == 'evaluation' else {}
    db = LineageDB(p, [root, child, grandchild], events=events, evaluations=evaluations)
    selected = (root, child, grandchild)[generation]
    assert subject.has_trace(db, p, selected) is True
    with pytest.raises(HTTPException) as failure: subject.reject_generic_write(db, p, selected)
    assert failure.value.status_code == 409


def test_a_valid_ordinary_lineage_does_not_become_the_new_family():
    p, root, child, grandchild = lineage(managed=False)
    db = LineageDB(p, [root, child, grandchild])
    assert subject.has_trace(db, p, grandchild) is False
    assert subject.reject_generic_write(db, p, grandchild) is None


@pytest.mark.parametrize('damage', ['cycle', 'missing_parent', 'foreign_parent', 'row_limit', 'depth_limit'])
def test_unresolved_or_over_budget_lineage_is_never_a_generic_write_grant(monkeypatch, damage):
    p, root, child, grandchild = lineage(managed=False)
    rows = [root, child, grandchild]
    if damage == 'cycle': root.data = {'parent_query_id': grandchild.id}
    elif damage == 'missing_parent': rows.remove(root)
    elif damage == 'foreign_parent': root.workspace_id = uid(999)
    elif damage == 'row_limit': monkeypatch.setattr(subject, 'MAX_LINEAGE_ROWS', 2)
    else: monkeypatch.setattr(subject, 'MAX_LINEAGE_DEPTH', 2)
    db = LineageDB(p, rows)
    assert subject.has_trace(db, p, grandchild) is True
    with pytest.raises(HTTPException): subject.reject_generic_write(db, p, grandchild)
    assert len(db.lookups) <= 2 * subject.MAX_LINEAGE_DEPTH


@pytest.mark.parametrize('independent', ['pointer', 'check'])
def test_gap_reverse_inventory_restricts_a_child_even_after_its_parent_marker_is_lost(independent):
    p, root, child, _grandchild = lineage()
    child.data = {}
    gap = NS(id=uid(220), workspace_id=p.workspace_id, query_id=root.id,
        data={'latest_recheck_query_id': child.id} if independent == 'pointer' else {})
    checks = [NS(gap_id=gap.id, workspace_id=p.workspace_id, data={'query_id': child.id})] if independent == 'check' else []
    db = LineageDB(p, [root, child], checks=checks, gaps=[gap])
    assert subject.has_trace(db, p, child) is True
    with pytest.raises(HTTPException): subject.reject_generic_write(db, p, child)


def test_valid_ordinary_gap_lineage_remains_ordinary():
    p, root, child, _grandchild = lineage(managed=False)
    gap = NS(id=uid(221), workspace_id=p.workspace_id, query_id=root.id,
        data={'latest_recheck_query_id': child.id})
    child.data = {'gap_recheck': {'gap_id': gap.id}}
    db = LineageDB(p, [root, child], gaps=[gap])
    assert subject.has_trace(db, p, child) is False
    assert subject.reject_generic_write(db, p, child) is None


def test_unanchored_gap_declaration_cannot_downgrade_or_authorize_a_query():
    p, root, child, _grandchild = lineage(managed=False)
    child.data = {'gap_recheck': {'gap_id': uid(230)}}
    db = LineageDB(p, [root, child])
    assert subject.has_trace(db, p, child) is True
    with pytest.raises(HTTPException): subject.reject_generic_write(db, p, child)


def test_data_agent_and_failure_recovery_do_not_mutate_a_derived_evaluation(monkeypatch):
    from generic_agents import agents, job_recovery
    from generic_agents import threshold_policy_execution_v153
    p, root, child, _grandchild = lineage(); db = LineageDB(p, [root, child])
    before = copy.deepcopy((child.data, child.status, child.version))
    monkeypatch.setattr(agents, 'scoped', lambda actual_db, model, identity, actual_p: child)
    with pytest.raises(HTTPException): agents.data_agent(db, p, {}, child.id)
    # Unrelated policy recovery is an explicit unchanged service boundary.
    monkeypatch.setattr(threshold_policy_execution_v153, 'fail', lambda *_args: False)
    monkeypatch.setattr(job_recovery, 'scoped', lambda actual_db, model, identity, actual_p, lock: child)
    job_recovery.fail_target(db, p, NS(kind='data_query', data={'query_id': child.id}), 'synthetic failure')
    assert (child.data, child.status, child.version) == before
