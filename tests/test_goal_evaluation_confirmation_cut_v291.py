"""Real normal-review fields and audit-at-cut checks with explicit DB IO seams.

These tests do not simulate human judgment, a real review POST, PG authority,
ACK authenticity or producer completion. Root verifies the normal target flow.
"""
import copy
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import select

from generic_agents import goal_evaluation_query_v288 as subject
from generic_agents.contracts import digest
from generic_agents.models import Audit, DataRun
from generic_agents.temporal import instant
from tests.test_goal_evaluation_query_v288 import scene as query_scene, parent_data, Rows, same_sql
from tests.test_workflow_goal_evaluation_contracts_v288 import uid, CUT


def review(observation, at, *, legacy=False):
    fingerprint = digest({'extracted': observation.data['extracted'],
        'evidence_ids': observation.data.get('evidence_ids', []),
        'invocation_id': observation.data.get('invocation_id')})
    value = {'decision': 'confirmed', 'reason': '核对原输入及提取回执，仅限技术质量，不证明现场事实',
        'reviewed_by': uid(7), 'reviewed_at': at.isoformat(), 'input_hash': fingerprint}
    if not legacy:
        value['request_id'] = uid(91)
        value['body_hash'] = digest({'expected_version': observation.version - 1,
            'reason': value['reason'], 'decision': value['decision'],
            'request_id': value['request_id'], 'expected_input_hash': fingerprint})
    return value


def save_review(observation, value):
    history = [copy.deepcopy(value)]
    observation.data = {**observation.data, 'human_verification': copy.deepcopy(value),
        'verification_history': history, 'verification_history_hash': digest(history)}


def audit(observation, value, at):
    return NS(id=uid(92), workspace_id=observation.workspace_id, object_id=observation.id,
        actor_id=value['reviewed_by'], action='observation.verified', created_at=at,
        data={'decision': value['decision'], 'reason': value['reason']})


@pytest.fixture
def scene():
    cut = instant(CUT); at = cut - timedelta(seconds=1)
    observation = NS(id=uid(40), workspace_id=uid(1), version=2,
        created_at=cut - timedelta(minutes=1), data={'extracted': {'attributes': {'temperature': 21}}})
    value = review(observation, at); save_review(observation, value)
    proofs = [audit(observation, value, at)]

    class AuditIO:
        def __init__(self): self.reads = []
        def scalars(self, statement):
            expected = select(Audit).where(Audit.workspace_id == observation.workspace_id,
                Audit.object_id == observation.id, Audit.action == 'observation.verified').limit(1001)
            assert same_sql(statement, expected)
            self.reads.append(statement); return proofs
        def add(self, row): pytest.fail('cut proof cannot create a review or audit')
        def commit(self): pytest.fail('cut proof cannot change a saved evaluation')

    db = AuditIO()
    clock = subject._Clock(subject._WITNESS, db, observation.workspace_id, uid(7), uid(8), uid(20),
        uid(10), uid(2), 'a' * 64, (), (), 'b' * 64, '{}', cut)
    return NS(observation=observation, proofs=proofs, db=db, clock=clock, cut=cut)


def checked(s):
    return subject._confirmation_at_cut(s.observation, s.observation.data['human_verification'], s.clock)


@pytest.mark.parametrize('at_cut', [False, True])
def test_normal_reviewed_at_with_complete_independent_history_is_known_at_cut(scene, at_cut):
    s = scene
    if at_cut:
        value = review(s.observation, s.cut); save_review(s.observation, value)
        s.proofs[:] = [audit(s.observation, value, s.cut)]
    before = copy.deepcopy((s.observation.data, s.proofs))
    assert checked(s) is True and len(s.db.reads) == 1
    assert (s.observation.data, s.proofs) == before


@pytest.mark.parametrize('fault', ['later_review', 'later_audit', 'missing_audit', 'duplicate_audit',
    'history_hash', 'history_latest', 'input_drift', 'audit_actor', 'audit_reason',
    'audit_workspace', 'audit_extra', 'body_hash', 'only_at_alias', 'missing_history'])
def test_later_missing_or_self_resealed_review_evidence_never_backdates_confirmation(scene, fault):
    s = scene; obs = s.observation; current = obs.data['human_verification']
    if fault == 'later_review':
        value = review(obs, s.cut + timedelta(seconds=1)); save_review(obs, value)
    elif fault == 'later_audit': s.proofs[0].created_at = s.cut + timedelta(seconds=1)
    elif fault == 'missing_audit': s.proofs.clear()
    elif fault == 'duplicate_audit': s.proofs.append(copy.deepcopy(s.proofs[0]))
    elif fault == 'history_hash': obs.data['verification_history_hash'] = '0' * 64
    elif fault == 'history_latest': obs.data['verification_history'][-1]['decision'] = 'rejected'
    elif fault == 'input_drift': obs.data['extracted']['attributes']['temperature'] = 99
    elif fault == 'audit_actor': s.proofs[0].actor_id = uid(99)
    elif fault == 'audit_reason': s.proofs[0].data['reason'] = 'another review'
    elif fault == 'audit_workspace': s.proofs[0].workspace_id = uid(99)
    elif fault == 'audit_extra': s.proofs[0].data['trusted'] = True
    elif fault == 'body_hash':
        value = {**current, 'body_hash': '0' * 64}; save_review(obs, value)
    elif fault == 'only_at_alias':
        value = {**current, 'at': current['reviewed_at']}; value.pop('reviewed_at'); save_review(obs, value)
    elif fault == 'missing_history': obs.data.pop('verification_history')
    before = copy.deepcopy((obs.data, s.proofs))
    assert checked(s) is False
    assert (obs.data, s.proofs) == before


def test_legacy_without_request_id_still_requires_reviewed_at_complete_history_and_audit(scene):
    s = scene; value = review(s.observation, s.cut - timedelta(seconds=1), legacy=True)
    save_review(s.observation, value); s.proofs[:] = [audit(s.observation, value, s.cut)]
    assert checked(s) is True
    s.proofs.clear()
    assert checked(s) is False


def test_same_actor_reason_repeated_reviews_need_complete_audit_multiplicity(scene):
    s = scene; latest = s.observation.data['human_verification']
    older = {key: value for key, value in latest.items() if key not in {'request_id', 'body_hash'}}
    history = [older, copy.deepcopy(latest)]
    s.observation.data.update(verification_history=history, verification_history_hash=digest(history))
    assert checked(s) is False
    extra = copy.deepcopy(s.proofs[0]); extra.id = uid(93)
    s.proofs.append(extra)
    assert checked(s) is True


def test_duplicate_primary_identity_is_not_two_independent_review_audits(scene):
    s = scene; latest = s.observation.data['human_verification']
    older = {key: value for key, value in latest.items() if key not in {'request_id', 'body_hash'}}
    history = [older, copy.deepcopy(latest)]
    s.observation.data.update(verification_history=history, verification_history_hash=digest(history))
    s.proofs.append(copy.deepcopy(s.proofs[0]))
    assert checked(s) is False


@pytest.mark.parametrize('rewrite_timestamps', [False, True])
def test_reordered_and_rehashed_history_cannot_revive_confirmation_before_real_rejection(scene, rewrite_timestamps):
    s = scene; obs = s.observation
    t1, t2 = s.cut - timedelta(seconds=20), s.cut - timedelta(seconds=10)
    confirmed = review(obs, t1)
    obs.version = 3
    rejected = {**review(obs, t2), 'decision': 'rejected', 'request_id': uid(94)}
    rejected['body_hash'] = digest({'expected_version': 2, 'reason': rejected['reason'],
        'decision': rejected['decision'], 'request_id': rejected['request_id'],
        'expected_input_hash': rejected['input_hash']})
    s.proofs[:] = [audit(obs, confirmed, t1 + timedelta(seconds=1)),
        audit(obs, rejected, t2 + timedelta(seconds=1))]
    s.proofs[1].id = uid(93)
    # The attacker rewrites only the mutable history/latest/self hashes. Both
    # real independent audits still say confirmed first and rejected later.
    forged = [copy.deepcopy(rejected), copy.deepcopy(confirmed)]
    if rewrite_timestamps:
        forged[0]['reviewed_at'], forged[1]['reviewed_at'] = t1.isoformat(), t2.isoformat()
    forged[-1]['body_hash'] = digest({'expected_version': 2, 'reason': forged[-1]['reason'],
        'decision': forged[-1]['decision'], 'request_id': forged[-1]['request_id'],
        'expected_input_hash': forged[-1]['input_hash']})
    obs.data.update(human_verification=copy.deepcopy(forged[-1]), verification_history=forged,
        verification_history_hash=digest(forged))
    before = copy.deepcopy((obs.data, s.proofs))
    assert checked(s) is False
    assert (obs.data, s.proofs) == before


def test_real_rejection_then_new_confirmation_keeps_locked_review_and_audit_order(scene):
    s = scene; obs = s.observation
    t1, t2 = s.cut - timedelta(seconds=20), s.cut - timedelta(seconds=10)
    rejected = {**review(obs, t1, legacy=True), 'decision': 'rejected'}
    confirmed = review(obs, t2)
    history = [rejected, confirmed]
    obs.data.update(human_verification=copy.deepcopy(confirmed), verification_history=history,
        verification_history_hash=digest(history))
    # Inventory order is arbitrary SQL order, so the proof uses its actual
    # created_at order while retaining the original row-locked semantics.
    s.proofs[:] = [audit(obs, confirmed, t2 + timedelta(seconds=1)),
        audit(obs, rejected, t1 + timedelta(seconds=1))]
    s.proofs[1].id = uid(93)
    assert checked(s) is True
    # Keeping semantic order but backdating confirmation before the prior
    # locked rejection Audit also cannot manufacture historical knowledge.
    confirmed['reviewed_at'] = (t1 + timedelta(microseconds=1)).isoformat()
    obs.data.update(human_verification=copy.deepcopy(confirmed), verification_history=history,
        verification_history_hash=digest(history))
    assert checked(s) is False


def test_equal_audit_clock_with_different_review_statements_is_ambiguous(scene):
    s = scene; obs = s.observation; at = s.cut - timedelta(seconds=1)
    rejected = {**review(obs, at, legacy=True), 'decision': 'rejected'}
    confirmed = review(obs, at)
    history = [rejected, confirmed]
    obs.data.update(human_verification=copy.deepcopy(confirmed), verification_history=history,
        verification_history_hash=digest(history))
    s.proofs[:] = [audit(obs, rejected, at), audit(obs, confirmed, at)]
    s.proofs[1].id = uid(93)
    assert checked(s) is False


@pytest.mark.parametrize('late', [False, True])
def test_real_confirmed_query_uses_normal_review_spelling_without_changing_original_q0(monkeypatch, late):
    s = query_scene.__wrapped__(monkeypatch)
    # An independently constructed synthetic confirmed Goal before the tested
    # call, not a migration or weakening of a production Goal/Q0 snapshot.
    s.goal['evidence']['minimum_verification'] = 'confirmed'
    s.task.data['goal_hash'] = digest(s.goal); s.parent.data = parent_data(s)
    s.policy = subject.contract.make_preparation_policy(s.goal)
    observation = s.observations[0]
    at = instant(CUT) + timedelta(seconds=1) if late else instant(CUT) - timedelta(seconds=1)
    value = review(observation, at); save_review(observation, value)
    proof = audit(observation, value, at)
    original_scalars = s.db.scalars

    def scalars(statement):
        expected = select(Audit).where(Audit.workspace_id == s.p.workspace_id,
            Audit.object_id == observation.id, Audit.action == 'observation.verified').limit(1001)
        if same_sql(statement, expected): return Rows([proof])
        return original_scalars(statement)

    monkeypatch.setattr(s.db, 'scalars', scalars)
    original = copy.deepcopy(s.parent.data)
    report = s.create().data['result']['result']
    assert (observation.id in [row['observation_id'] for row in report['observation_evidence']]) is (not late)
    if late:
        assert any(row.get('status') == 'confirmation_not_proven_at_evaluation_cut'
            for row in report['excluded_evidence'])
    assert s.parent.data == original and s.task.data['native_goal_query_id'] == s.parent.id
    assert s.db.additions == [DataRun, Audit]
