"""Pure request validation for the exact continuation execution commands."""
import copy

import pytest

from generic_agents import same_goal_continuation_execution_contracts_v314 as c
from tests.test_same_goal_advice_contracts_v302 import ref


def preview():
    return {'request_id': ref('execution-command')['id'], 'expected_task_version': 8,
        'expected_continuation_version': 2, 'revision_hash': 'a' * 64, 'source_hash': 'b' * 64,
        'grant_ref': {'id': ref('actual-grant')['id'], 'record_hash': 'c' * 64, 'version': 1},
        'supersedes_plan_id': None,
        'plan_origin': 'human_revision', 'rationale': '  人工新增一次原范围只读计算  '}


def test_preview_and_submit_have_distinct_closed_shapes():
    original = preview()
    assert c.dump(c.PreviewRequest, original) == original
    submitted = {**original, 'preview_hash': 'd' * 64}
    assert c.dump(c.SubmitRequest, submitted) == submitted
    with pytest.raises(ValueError): c.dump(c.PreviewRequest, submitted)
    with pytest.raises(ValueError): c.dump(c.SubmitRequest, original)


@pytest.mark.parametrize('key,value', [('records', []), ('policy', {}), ('actor_id', 'x'),
    ('plan_origin', 'model'), ('rationale', ' '), ('expected_task_version', True),
    ('expected_continuation_version', 0), ('supersedes_plan_id', 'latest')])
def test_client_cannot_invent_evidence_actors_or_coerced_versions(key, value):
    body = {**preview(), key: value}
    with pytest.raises(ValueError): c.dump(c.PreviewRequest, body)


@pytest.mark.parametrize('version', [True, '1', 1.0, 2])
def test_only_exact_active_grant_version_can_be_requested(version):
    body = copy.deepcopy(preview())
    body['grant_ref']['version'] = version
    with pytest.raises(ValueError): c.dump(c.PreviewRequest, body)


def test_decision_and_result_review_bind_different_objects():
    common = {'request_id': ref('decision-command')['id'],
        'expected_execution_version': 3, 'reason': '已核对'}
    approve = {**common, 'submission_hash': 'd' * 64, 'decision': 'approve'}
    adopt = {**common, 'output_hash': 'e' * 64, 'decision': 'adopt'}
    assert c.dump(c.DecideRequest, approve) == approve
    assert c.dump(c.ReviewRequest, adopt) == adopt
    for model in (c.StartRequest, c.CancelRequest, c.RecoverRequest):
        assert c.dump(model, common) == common
        with pytest.raises(ValueError): c.dump(model, approve)
    with pytest.raises(ValueError): c.dump(c.ReviewRequest, approve)
    with pytest.raises(ValueError): c.dump(c.DecideRequest, adopt)
