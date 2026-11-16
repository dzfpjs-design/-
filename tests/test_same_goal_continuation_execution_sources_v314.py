"""Actual proposal validation and source comparison with labelled IO boundaries.

V313 basis construction runs. Saved grant/draft custody, DB, IAM, current Goal
authentication and raw source proof use the V313 IO fixture. No Job/SDK runs.
"""
import copy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_continuation_execution_sources_v314 as s
from generic_agents.contracts import digest
from generic_agents.models import Plan
from generic_agents.security import Principal
from tests.test_same_goal_continuation_grant_sources_v313 import scene, uid, issue


@pytest.fixture
def case(scene, monkeypatch):
    t = scene
    t.source.update(schema=s.c.drafts.SOURCE, workspace_id=t.p.workspace_id,
        origin_kind='independent_advice', bridge_state='not_bound', goal_hash=t.task.data['goal_hash'],
        goal_version=t.task.data['goal_version'], goal_snapshot_hash=digest(s.snapshot(t.task)),
        release_id=t.release.id)
    source_hash = digest(t.source)
    t.continuation.data = {'source_hash': source_hash, 'business_actor_id': uid('business-actor')}
    fields = s.c.drafts.draft({'route': 'business_conclusion', 'purpose': '核对原观测',
        'work_description': '汇总同单位气温观测', 'expected_result': '带时点和缺测数量的均值'})
    revision = {'schema': s.c.drafts.REVISION, 'continuation_id': t.continuation.id,
        'version': t.continuation.version, 'source_hash': source_hash,
        'draft': fields, 'actor_id': t.continuation.data['business_actor_id'],
        'previous_hash': 'c' * 64, 'request_id': uid('revision-command'),
        'reason': '人工修订', 'saved_at': t.at.isoformat()}
    revision['record_hash'] = s.c.record_hash(revision)
    t.revision.clear()
    t.revision.update(revision)
    t.body.update(source_hash=source_hash, revision_hash=revision['record_hash'])
    basis = issue(t)['basis']
    t.grant_plan = Plan(id=uid('saved-grant'), workspace_id=t.p.workspace_id, task_id=t.task.id,
        status='active', version=1)
    grant = {'schema': s.c.grants.GRANT, 'grant_id': t.grant_plan.id, 'workspace_id': t.p.workspace_id,
        'task_id': t.task.id, 'advice_plan_id': t.advice.id, 'continuation_id': t.continuation.id,
        'issuer_id': t.p.user_id, 'issuer_role': 'admin', 'request': copy.deepcopy(t.body),
        'request_hash': digest(t.body), 'source': copy.deepcopy(t.source), 'source_hash': source_hash,
        'basis': basis, 'issued_at': basis['issued_at'], 'expires_at': basis['expires_at'], **s.c.FLAGS}
    grant['record_hash'] = s.c.record_hash(grant)
    t.saved = {'plan': t.grant_plan, 'grant': s.grants._grant(grant, t.p), 'revocation': None}
    monkeypatch.setattr(s.grants, '_inventory', lambda *args: [t.saved])
    def lock_actor(db, principal):
        t.events.append(('current-actor-lock-IO', principal.user_id))
        if not t.permitted:
            raise HTTPException(403, '实际岗位已失效')
    monkeypatch.setattr(s, 'lock_current_actor', lock_actor)
    t.executions = []
    monkeypatch.setattr(s, '_execution_inventory', lambda *args: t.executions)
    monkeypatch.setattr(s, '_budget', lambda db, p, task, owner: t.events.append(('budget-IO', owner)))
    t.command = {'request_id': uid('execution-command'), 'expected_task_version': t.task.version,
        'expected_continuation_version': t.continuation.version, 'revision_hash': revision['record_hash'],
        'source_hash': source_hash, 'grant_ref': s._grant_ref(t.saved),
        'supersedes_plan_id': None,
        'plan_origin': 'human_revision', 'rationale': '人工采用一项新增只读工具'}
    t.events.clear()
    return t


def build(t, body=None, *, lock=False):
    return s.build_proposal(t.db, t.p, t.task.id, t.advice.id, t.continuation.id,
        t.command if body is None else body, lock=lock)


def test_preview_and_submit_reproduce_one_exact_immutable_proposal(case):
    t = case
    initial = copy.deepcopy(t.task.data)
    preview = build(t)
    submitted = build(t, {**t.command, 'preview_hash': preview['preview_hash']}, lock=True)
    assert submitted['proposal'] == preview['proposal']
    assert preview['preview_hash'] == s.c.record_hash(preview['proposal'])
    assert preview['proposal']['grant']['basis']['inputs'] == t.saved['grant']['basis']['inputs']
    assert preview['proposal']['draft'] == t.revision
    assert preview['proposal']['supersedes_plan_id'] is None
    assert t.task.data == initial
    public = s.proposal_view(preview['proposal'])
    assert public['field']['unit'] == '℃' and public['record_count'] == 1 and public['test_only'] is True
    assert not {'inputs', 'grant', 'records', 'source'} & set(public)
    with pytest.raises(HTTPException):
        build(t, {**t.command, 'preview_hash': '0' * 64}, lock=True)


def test_live_is_not_tied_to_submitter_and_passes_own_budget_reservation(case):
    t = case
    proposal = build(t)['proposal']
    t.p = Principal(uid('policy-stage-operator'), t.p.workspace_id, 'operator', 'IO-current-role')
    actual = s.live(t.db, t.p, t.task, proposal, execution_plan_id=uid('execution-plan'))
    assert actual['policy']['spec'] == t.policy_spec
    assert actual['capability'].id == t.cap.id
    assert ('budget-IO', uid('execution-plan')) in t.events
    with pytest.raises(HTTPException): build(t)


def test_live_denies_actor_whose_membership_changes_before_lock(case, monkeypatch):
    t = case
    proposal = build(t)['proposal']
    def denied(db, principal):
        raise HTTPException(403, '岗位已在锁定前撤销')
    monkeypatch.setattr(s, 'lock_current_actor', denied)
    t.events.clear()
    with pytest.raises(HTTPException) as error:
        s.live(t.db, t.p, t.task, proposal, execution_plan_id=uid('execution-plan'))
    assert error.value.status_code == 403
    assert ('budget-IO', uid('execution-plan')) not in t.events


@pytest.mark.parametrize('fault', ['revoked', 'expired', 'revision', 'evaluation', 'policy',
    'records', 'issuer_content', 'goal', 'capability'])
def test_live_rejects_drift_in_the_saved_approval_basis(case, fault):
    t = case
    proposal = build(t)['proposal']
    if fault == 'revoked':
        t.grant_plan.status = 'revoked'
        t.grant_plan.version = 2
    elif fault == 'expired':
        t.at += timedelta(hours=3)
    elif fault == 'revision':
        t.revision['record_hash'] = '1' * 64
    elif fault == 'evaluation':
        t.task.data[s.source.provider.KEY]['head_hash'] = '9' * 64
    elif fault == 'policy':
        t.version.revision += 1
    elif fault == 'records':
        t.record['evaluation_result']['result']['observation_evidence'][0]['attributes']['temperature'] = 26
    elif fault == 'issuer_content':
        t.saved['grant']['issuer_id'] = uid('another-issuer')
    elif fault == 'goal':
        t.active = False
    else:
        t.cap.status = 'draft'
    with pytest.raises(HTTPException): s.live(t.db, t.p, t.task, proposal)


def test_context_preserves_historical_grant_options_with_no_eligible_tool(case):
    t = case
    original = s.context(t.db, t.p, t.task.id, t.advice.id, t.continuation.id)
    assert original['permissions']['can_submit'] is True
    assert original['grants'][0]['eligible'] is True
    t.active = False
    historical = s.context(t.db, t.p, t.task.id, t.advice.id, t.continuation.id)
    assert historical['permissions']['can_submit'] is False and historical['blocked_reasons']
    assert historical['grants'][0]['eligible'] is False


def old_execution(t):
    proposal = build(t)['proposal']
    return {'plan': Plan(id=uid('old-execution'), task_id=t.task.id, status='returned'),
        'proposal': proposal, 'job': None, 'start': None}


def test_explicit_correction_is_sealed_and_displayed_without_rewriting_original(case):
    t = case
    old = old_execution(t)
    t.executions.append(old)
    current = s.context(t.db, t.p, t.task.id, t.advice.id, t.continuation.id)
    assert current['returned_candidates'] == [
        {'plan_id': old['plan'].id, 'phase': 'returned', 'label': '审批退回'}]
    original = copy.deepcopy(old['proposal'])
    t.command['supersedes_plan_id'] = old['plan'].id
    result = build(t)
    assert result['proposal']['supersedes_plan_id'] == old['plan'].id
    assert s.proposal_view(result['proposal'])['supersedes_plan_id'] == old['plan'].id
    assert old['proposal'] == original and old['plan'].status == 'returned'


@pytest.mark.parametrize('fault', ['other_task', 'other_advice', 'other_draft', 'changed_goal',
    'changed_source', 'still_active', 'cycle', 'existing_child', 'lease_live', 'spent_grant', 'missing'])
def test_correction_requires_one_terminal_in_scope_predecessor(case, fault):
    t = case
    old = old_execution(t)
    t.executions.append(old)
    t.command['supersedes_plan_id'] = old['plan'].id
    if fault == 'other_task':
        old['plan'].task_id = uid('different-task')
    elif fault == 'other_advice':
        old['proposal']['advice_plan_id'] = uid('different-advice')
    elif fault == 'other_draft':
        old['proposal']['continuation_ref']['id'] = uid('different-draft')
    elif fault == 'changed_goal':
        old['proposal']['grant']['basis']['goal']['hash'] = '9' * 64
    elif fault == 'changed_source':
        old['proposal']['source_hash'] = '9' * 64
    elif fault == 'still_active':
        old['plan'].status = 'in_review'
    elif fault == 'cycle':
        old['proposal']['supersedes_plan_id'] = old['plan'].id
    elif fault == 'existing_child':
        child = old_execution(t)
        child['plan'].id = uid('existing-child')
        child['proposal']['supersedes_plan_id'] = old['plan'].id
        t.executions.append(child)
    elif fault == 'lease_live':
        old['job'] = SimpleNamespace(lease_until=t.at + timedelta(seconds=1))
    elif fault == 'spent_grant':
        old['start'] = SimpleNamespace(id=uid('actual-start'))
        old['plan'].status = 'result_returned'
    else:
        t.executions.clear()
    with pytest.raises(HTTPException):
        build(t)
