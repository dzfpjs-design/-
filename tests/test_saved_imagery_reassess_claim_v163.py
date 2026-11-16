"""Assessment claim and lock-order boundaries, not real source/PG approval.

The prior-result cases call the actual public creator and actual bridge claim,
using mapped rows and only two explicit SQL IO seams. The new-result carrier
case proves only the created flag contract, not INSERT/source/Worker success.
The ordering case deliberately fails the first source gate: it never supplies
fake successful selection, reception or human-review validators.
"""
from copy import deepcopy
from operator import eq
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from sqlalchemy.sql.elements import BinaryExpression, BindParameter

from generic_agents.contracts import digest
from generic_agents.models import EvidenceAssessment
from generic_agents.saved_imagery_contracts_v163 import Reassess
from generic_agents.saved_imagery_models_v163 import SavedImageryReassessment
from generic_agents import evidence_assessment_api as assessments
from generic_agents import saved_imagery_store_v163 as store


def identity(number):
    return '163abc00-0000-4000-8000-' + f'{number:012x}'


def case():
    principal = SimpleNamespace(workspace_id=identity(1), user_id=identity(2), role='admin')
    plan = SavedImageryReassessment(id=identity(3), workspace_id=principal.workspace_id,
        instance_id=identity(4), assessment_id=identity(5), entity_id=identity(6), owner_id=principal.user_id,
        task_id=identity(7), reassessment_id=None, status='task_created', version=2,
        record_hash='a' * 64, data={'sentinel': '原计划保持', 'assessment': {'request': {}}})
    body = assessments.Request(instance_id=plan.instance_id, release_id=identity(8),
        entity_ids=[plan.entity_id], purpose='仅为纯边界测试的原条件', required_modalities=['imagery'],
        lookback_hours=24, parent_id=plan.assessment_id,
        request_id='saved-imagery-reassess-' + plan.id)
    content = body.model_dump(exclude={'question_id'})
    prior = EvidenceAssessment(id=identity(9), workspace_id=principal.workspace_id,
        instance_id=plan.instance_id, owner_id=principal.user_id, request_id=body.request_id,
        status='queued', version=1, data={'creation_hash': digest(content),
            'request': content, 'request_hash': digest(content), 'sentinel': '普通入口原核查'})
    return principal, plan, body, prior


class PriorOnlyIO:
    """No arbitrary SQL simulator and no add/flush/commit success fallback."""
    def __init__(self, principal, request_id, prior):
        self.principal = principal; self.request_id = request_id; self.prior = prior
        self.calls = []

    def execute(self, statement, parameters):
        assert str(statement) == 'SELECT pg_advisory_xact_lock(:key)'
        assert parameters == {'key': int(digest({'workspace': self.principal.workspace_id,
            'request': self.request_id})[:16], 16) - (1 << 63)}
        self.calls.append('lock')

    def scalar(self, statement):
        assert self.prior is not None
        assert statement.column_descriptions[0]['entity'] is EvidenceAssessment
        assert self.calls == ['lock']
        predicates = tuple(statement._where_criteria)
        expected = ((EvidenceAssessment.__table__.c.workspace_id, self.principal.workspace_id),
            (EvidenceAssessment.__table__.c.request_id, self.request_id))
        assert len(predicates) == len(expected)
        for predicate, (column, value) in zip(predicates, expected):
            assert isinstance(predicate, BinaryExpression) and predicate.operator is eq
            assert predicate.left.compare(column)
            assert isinstance(predicate.right, BindParameter)
            assert type(predicate.right.value) is str and predicate.right.value == value
        self.calls.append('prior')
        return self.prior


def test_public_prior_result_keeps_legacy_return_and_explicitly_denies_new_creation():
    p, plan, body, prior = case(); before = deepcopy(prior.data)
    outcome = {'created': True, 'caller_note': '保留'}
    db = PriorOnlyIO(p, body.request_id, prior)
    assert assessments.create_saved(body, p, db, outcome=outcome) is prior
    assert outcome == {'created': False, 'caller_note': '保留'}
    assert prior.data == before and store.MARKER not in prior.data
    legacy = PriorOnlyIO(p, body.request_id, prior)
    assert assessments.create_saved(body, p, legacy) is prior
    assert legacy.calls == ['lock', 'prior']
    assert plan.reassessment_id is None


def test_real_public_prior_return_cannot_be_adopted_by_the_actual_claim_helper():
    p, plan, body, prior = case()
    db = PriorOnlyIO(p, body.request_id, prior)
    before_plan = deepcopy(plan.data); before_prior = deepcopy(prior.data)
    with pytest.raises(HTTPException) as error:
        store._claim_follow(db, p, plan, body)
    assert error.value.status_code == 409
    assert plan.status == 'task_created' and plan.reassessment_id is None
    assert plan.data == before_plan and prior.data == before_prior
    assert store.MARKER not in prior.data and db.calls == ['lock', 'prior']


class Outcome(dict):
    pass


@pytest.mark.parametrize('outcome', [True, 1, [], Outcome()], ids=['bool', 'int', 'list', 'dict-subclass'])
def test_public_outcome_container_must_be_an_actual_dict_before_any_io(outcome):
    p, _, body, prior = case(); db = PriorOnlyIO(p, body.request_id, prior)
    with pytest.raises(HTTPException) as error:
        assessments.create_saved(body, p, db, outcome=outcome)
    assert error.value.status_code == 422 and db.calls == []


@pytest.mark.parametrize('flag', [None, False, 0, 1, 'true'], ids=['missing', 'false', 'zero', 'one', 'string'])
def test_claim_requires_actual_true_before_touching_any_plan_or_returned_row(monkeypatch, flag):
    p, plan, body, returned = case()
    original = deepcopy(plan.data); returned_data = deepcopy(returned.data)

    def carrier(actual_body, actual_p, actual_db, *, outcome):
        assert actual_body is body and actual_p is p and actual_db is db
        if flag is not None: outcome['created'] = flag
        return returned

    db = object(); monkeypatch.setattr(assessments, 'create_saved', carrier)
    with pytest.raises(HTTPException) as error:
        store._claim_follow(db, p, plan, body)
    assert error.value.status_code == 409
    assert plan.reassessment_id is None and plan.status == 'task_created'
    assert plan.data == original and returned.data == returned_data


def test_explicit_new_result_carrier_binds_only_the_returned_scope_and_original_body(monkeypatch):
    p, plan, body, returned = case(); db = object()

    def carrier(actual_body, actual_p, actual_db, *, outcome):
        assert actual_body is body and actual_p is p and actual_db is db
        outcome['created'] = True
        return returned

    monkeypatch.setattr(assessments, 'create_saved', carrier)
    store._claim_follow(db, p, plan, body)
    assert plan.reassessment_id == returned.id and plan.status == 'reassessment_requested'
    assert plan.data['reassessment_request'] == body.model_dump()
    assert plan.data['reassessment_creation_hash'] == returned.data['creation_hash']
    assert returned.data[store.MARKER] == {'plan_id': plan.id, 'task_id': plan.task_id}


def test_shared_public_request_lock_precedes_the_first_source_resource_gate(monkeypatch):
    p, plan, body, _ = case(); db = PriorOnlyIO(p, body.request_id, None)
    original = deepcopy(plan.data)
    failure = HTTPException(409, '原来源刻意拒绝；此测试不建立来源资格')

    def reject_selection(actual_db, actual_p, actual_plan, *, no_processing):
        assert actual_db is db and actual_p is p and actual_plan is plan and no_processing is False
        assert db.calls == ['lock'], 'actual ordinary assessment advisory must precede source locks'
        raise failure

    monkeypatch.setattr(store, '_selection', reject_selection)
    with pytest.raises(HTTPException) as error:
        store._reassess(db, p, plan)
    assert error.value is failure and db.calls == ['lock']
    assert plan.reassessment_id is None and plan.status == 'task_created' and plan.data == original


def test_actual_mutate_takes_assessment_advisory_before_instance_for_update(monkeypatch):
    """Reject the first row IO; no synthetic successful source/actor gates.

    The ordinary creator's Assessment INSERT needs Instance FK KEY SHARE, so
    taking its advisory only inside _reassess is already too late.
    """
    p, plan, assessment_body, _ = case()
    db = PriorOnlyIO(p, assessment_body.request_id, None)
    original = deepcopy(plan.data)
    command = Reassess(request_id='bridge-reassess-163-lock-order', version=plan.version,
        confirm_reassess=True)
    failure = HTTPException(409, '实例读取刻意拒绝；不授予任何后续操作资格')

    def reject_instance(actual_db, actual_p, model, identity, lock=False):
        assert actual_db is db and actual_p is p
        assert model is store.DomainInstance and identity == plan.instance_id and lock is True
        assert db.calls == ['lock'], 'assessment advisory must precede Instance FOR UPDATE and its FK lock cycle'
        raise failure

    monkeypatch.setattr(store, '_row', reject_instance)
    path = '/api/workspace/instances/' + plan.instance_id + '/saved-imagery-reassessments/' + plan.id + '/reassess'
    with pytest.raises(HTTPException) as error:
        store.mutate(db, p, plan.instance_id, 'reassess', plan.id, command,
            command.model_dump_json(), 'POST', path)
    assert error.value is failure and db.calls == ['lock']
    assert plan.reassessment_id is None and plan.status == 'task_created' and plan.data == original
