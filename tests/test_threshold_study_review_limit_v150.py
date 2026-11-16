"""Real HTTP dynamic review-limit admission, without scheduling a comparison.

Root's study_cases already owns actual processed and submitted feedback. Only
new Candidate/Evaluation records are created here; no original observation is
processed again, and this test never calls /run or a worker/model/gateway.
"""
from copy import deepcopy

from tests.test_threshold_study_http_v149 import (
    EVALUATIONS,
    _assert_samples,
    _command,
    _confirm,
    _evaluation_body,
    _frozen_candidate,
    _get,
    _nonce,
    _record,
)


def _unstarted(value):
    assert value['job'] is None, 'review-limit admission must not create a comparison Job'
    assert value['result'] is None
    assert value['progress'] == {'completed': 0, 'total': 8, 'reserved': 0}
    assert value['eligible_for_trial'] is False


def _same_selection(value, original):
    _assert_samples(value, original['feedback_refs'])
    assert [(sample['id'], sample['version']) for sample in value['samples']] == [
        (ref['feedback_id'], ref['expected_version']) for ref in original['feedback_refs']]
    assert value['candidate_id'] == original['candidate_id']
    assert value['title'] == original['title']
    assert value['purpose'] == original['purpose']
    assert value['limitations'] == original['limitations']


def test_four_samples_keep_limit_five_as_editable_draft_until_explicit_limit_one_save(client, study_cases):
    candidate, _, _ = _frozen_candidate(client, study_cases)
    body = _evaluation_body(study_cases, candidate)
    assert len(body['feedback_refs']) == 4, 'the real fixture must provide four independent evaluation samples'
    body['criteria'] = {'max_additional_reviews': 5}
    draft = _record(_command(client, 'POST', EVALUATIONS, body, 201), 'draft')
    _same_selection(draft, body)
    _unstarted(draft)
    assert draft['criteria'] == {'max_additional_reviews': 5}
    assert draft['freeze_hash'] is None and draft['can_edit'] is True

    freeze_body = _confirm(draft)
    rejected = client.post(EVALUATIONS + '/' + draft['id'] + '/freeze', json=freeze_body)
    assert rejected.status_code == 422, rejected.text
    assert isinstance(rejected.json().get('detail'), str) and rejected.json()['detail'].strip()
    assert draft['can_freeze'] is False, 'a draft above its sample count must not advertise freeze eligibility'
    assert isinstance(draft['current_issue'], str) and draft['current_issue'].strip()

    saved = _record(_get(client, EVALUATIONS + '/' + draft['id']), 'draft')
    assert saved['id'] == draft['id'] and saved['version'] == draft['version']
    assert saved['criteria'] == body['criteria'], 'a failed freeze must not clamp the original human criterion'
    assert saved['freeze_hash'] is None and saved['can_freeze'] is False and saved['can_edit'] is True
    assert isinstance(saved['current_issue'], str) and saved['current_issue'].strip()
    _same_selection(saved, body)
    _unstarted(saved)

    corrected = deepcopy(body)
    corrected['request_id'] = _nonce()
    corrected['expected_version'] = saved['version']
    corrected['criteria'] = {'max_additional_reviews': 1}
    assert corrected['request_id'] not in {body['request_id'], freeze_body['request_id']}
    revised = _record(_command(client, 'PUT', EVALUATIONS + '/' + saved['id'], corrected, 200), 'draft')
    assert revised['id'] == saved['id'] and revised['version'] == saved['version'] + 1
    assert revised['criteria'] == {'max_additional_reviews': 1}
    assert revised['can_freeze'] is True and revised['current_issue'] is None
    _same_selection(revised, body)
    _unstarted(revised)

    frozen = _record(_command(client, 'POST', EVALUATIONS + '/' + revised['id'] + '/freeze',
        _confirm(revised), 200), 'frozen')
    assert frozen['id'] == saved['id'] and frozen['version'] == revised['version'] + 1
    assert frozen['criteria'] == {'max_additional_reviews': 1}
    assert isinstance(frozen['freeze_hash'], str) and len(frozen['freeze_hash']) == 64
    assert frozen['can_run'] is True and frozen['can_edit'] is False
    assert frozen['current_issue'] is None
    _same_selection(frozen, body)
    _unstarted(frozen)

    cold = _record(_get(client, EVALUATIONS + '/' + frozen['id']), 'frozen')
    assert cold['version'] == frozen['version'] and cold['freeze_hash'] == frozen['freeze_hash']
    assert cold['criteria'] == {'max_additional_reviews': 1}
    _same_selection(cold, body)
    _unstarted(cold)
