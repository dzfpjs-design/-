"""Real HTTP study commands; Root owns the pre-existing numeric evidence.

``study_cases`` supplies baseline_id/candidate_id and submitted feedback refs
training_refs/evaluation_refs/overlap_refs, plus no_error_refs (valid TP/TN)
and unknown_refs (independent labelled samples including one uncertain).
Every ref is {feedback_id, expected_version}. The fixture's original Jobs,
Invocations and observations must not be rerun here. This file only queues the
one comparison Job; it does not import future product modules or mock a Worker.
"""
from copy import deepcopy
from uuid import UUID, uuid4


ROOT = '/api/local-rules/evolution'
CANDIDATES = ROOT + '/threshold-candidates'
EVALUATIONS = ROOT + '/threshold-evaluations'


def _nonce():
    return str(uuid4())


def _refs(cases, key):
    refs = deepcopy(cases[key])
    assert isinstance(refs, list) and refs, key
    assert len({item['feedback_id'] for item in refs}) == len(refs)
    for item in refs:
        assert set(item) == {'feedback_id', 'expected_version'}
        assert str(UUID(item['feedback_id'])) == item['feedback_id']
        assert type(item['expected_version']) is int and item['expected_version'] >= 1
    return refs


def _command(client, method, path, body, expected):
    response = client.request(method, path, json=body)
    assert response.status_code == expected, response.text
    if expected >= 400:
        return response
    value = response.json()
    assert isinstance(value, dict)
    return value


def _get(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    value = response.json()
    assert isinstance(value, dict)
    return value


def _record(value, status):
    assert str(UUID(value['id'])) == value['id']
    assert type(value['version']) is int and value['version'] >= 1
    assert value['status'] == status
    assert value['eligible_for_trial'] is False
    return value


def _hash(value):
    assert type(value) is str and len(value) == 64
    assert set(value) <= set('0123456789abcdef')
    return value


def _candidate_body(cases, refs_key='training_refs'):
    return {
        'schema': 'threshold-candidate-draft-v149',
        'request_id': _nonce(),
        'title': '合成阈值调整机制候选',
        'rationale': '原已提交判断提示核查边界；只比较机制，不声称林业质量改善',
        'baseline_capability_id': cases['baseline_id'],
        'candidate_capability_id': cases['candidate_id'],
        'feedback_refs': _refs(cases, refs_key),
    }


def _candidate(client, cases, refs_key='training_refs'):
    body = _candidate_body(cases, refs_key)
    value = _record(_command(client, 'POST', CANDIDATES, body, 201), 'draft')
    assert value['freeze_hash'] is None
    return value, body


def _confirm(value):
    return {'schema': 'threshold-study-confirm-v149', 'request_id': _nonce(),
            'expected_version': value['version'], 'confirm': True}


def _freeze(client, collection, value):
    body = _confirm(value)
    frozen = _record(_command(client, 'POST', collection + '/' + value['id'] + '/freeze', body, 200), 'frozen')
    assert frozen['id'] == value['id'] and frozen['version'] > value['version']
    _hash(frozen['freeze_hash'])
    return frozen, body


def _frozen_candidate(client, cases):
    value, body = _candidate(client, cases)
    frozen, confirmation = _freeze(client, CANDIDATES, value)
    return frozen, body, confirmation


def _evaluation_body(cases, candidate, refs_key='evaluation_refs'):
    return {
        'schema': 'threshold-evaluation-draft-v149',
        'request_id': _nonce(), 'candidate_id': candidate['id'],
        'expected_candidate_version': candidate['version'],
        'title': '独立合成输入的阈值比较',
        'feedback_refs': _refs(cases, refs_key),
        'criteria': {'max_additional_reviews': 1},
        'purpose': 'mechanism_check',
        'limitations': '这些独立合成数值只核保存、比较和失败机制，不代表现场分布或质量收益',
        'confirm_compatible_objects': True,
    }


def _evaluation(client, cases, candidate, refs_key='evaluation_refs'):
    body = _evaluation_body(cases, candidate, refs_key)
    value = _record(_command(client, 'POST', EVALUATIONS, body, 201), 'draft')
    assert value['freeze_hash'] is None and value['job'] is None
    return value, body


def _assert_samples(value, refs):
    # The contract exposes feedback IDs; no proof/snapshot layout is assumed.
    assert isinstance(value['samples'], list)
    assert len(value['samples']) == len(refs)
    assert {item['id'] for item in value['samples']} == {item['feedback_id'] for item in refs}


def test_real_candidate_and_independent_evaluation_freeze_then_queue_once(client, study_cases):
    candidate, candidate_body, _ = _frozen_candidate(client, study_cases)
    evaluation, evaluation_body = _evaluation(client, study_cases, candidate)
    _assert_samples(evaluation, evaluation_body['feedback_refs'])
    frozen, _ = _freeze(client, EVALUATIONS, evaluation)
    _assert_samples(frozen, evaluation_body['feedback_refs'])

    # Freeze preserves original scope and blocks further draft writes.
    for path, current, original in (
        (CANDIDATES, candidate, candidate_body),
        (EVALUATIONS, frozen, evaluation_body),
    ):
        edit = {**deepcopy(original), 'request_id': _nonce(),
                'expected_version': current['version'], 'title': '冻结后不得覆盖原方案'}
        _command(client, 'PUT', path + '/' + current['id'], edit, 409)
        saved = _get(client, path + '/' + current['id'])
        assert saved['version'] == current['version']
        assert saved['freeze_hash'] == current['freeze_hash']

    run_body = {**_confirm(frozen), 'expected_freeze_hash': frozen['freeze_hash']}
    path = EVALUATIONS + '/' + frozen['id'] + '/run'
    queued = _record(_command(client, 'POST', path, run_body, 202), 'queued')
    assert queued['id'] == frozen['id'] and queued['freeze_hash'] == frozen['freeze_hash']
    assert queued['job'] is not None
    repeated = _command(client, 'POST', path, run_body, 202)
    assert repeated['id'] == queued['id'] and repeated['job'] == queued['job']

    altered = {**run_body, 'expected_freeze_hash': '0' * 64}
    assert altered != run_body
    _command(client, 'POST', path, altered, 409)
    _command(client, 'POST', path, {**run_body, 'request_id': _nonce(),
                                  'expected_version': queued['version']}, 409)
    cold = _get(client, EVALUATIONS + '/' + queued['id'])
    assert cold['job'] == queued['job'] and cold['freeze_hash'] == frozen['freeze_hash']
    assert cold['eligible_for_trial'] is False
    # Root separately executes this actual job with the real scoped Worker.


def test_training_without_any_actual_fp_or_fn_stays_draft(client, study_cases):
    value, body = _candidate(client, study_cases, 'no_error_refs')
    for ref in body['feedback_refs']:
        feedback = _get(client, ROOT + '/feedback/' + ref['feedback_id'])
        assert feedback['status'] == 'submitted'
        assert feedback['version'] == ref['expected_version']
        assert feedback['classification'] in {'TP', 'TN'}
    _command(client, 'POST', CANDIDATES + '/' + value['id'] + '/freeze', _confirm(value), 409)
    saved = _record(_get(client, CANDIDATES + '/' + value['id']), 'draft')
    assert saved['version'] == value['version'] and saved['freeze_hash'] is None


def test_overlapping_evaluation_is_retained_but_cannot_freeze(client, study_cases):
    candidate, _, _ = _frozen_candidate(client, study_cases)
    value, body = _evaluation(client, study_cases, candidate, 'overlap_refs')
    _assert_samples(value, body['feedback_refs'])
    _command(client, 'POST', EVALUATIONS + '/' + value['id'] + '/freeze', _confirm(value), 409)
    saved = _record(_get(client, EVALUATIONS + '/' + value['id']), 'draft')
    assert saved['version'] == value['version'] and saved['freeze_hash'] is None
    assert saved['job'] is None
    _assert_samples(saved, body['feedback_refs'])


def test_unknown_independent_sample_survives_freeze_without_claiming_pass(client, study_cases):
    candidate, _, _ = _frozen_candidate(client, study_cases)
    value, body = _evaluation(client, study_cases, candidate, 'unknown_refs')
    unknown_ids = set()
    for ref in body['feedback_refs']:
        feedback = _get(client, ROOT + '/feedback/' + ref['feedback_id'])
        assert feedback['status'] == 'submitted' and feedback['version'] == ref['expected_version']
        if feedback['label'] == 'uncertain':
            assert feedback['classification'] == 'unknown'
            unknown_ids.add(ref['feedback_id'])
    assert unknown_ids, 'Root fixture must contain an actually submitted uncertain evaluation label'
    frozen, _ = _freeze(client, EVALUATIONS, value)
    _assert_samples(frozen, body['feedback_refs'])
    assert unknown_ids <= {sample['id'] for sample in frozen['samples']}
    assert frozen['job'] is None and frozen['eligible_for_trial'] is False
    cold = _record(_get(client, EVALUATIONS + '/' + frozen['id']), 'frozen')
    _assert_samples(cold, body['feedback_refs'])
    assert cold['freeze_hash'] == frozen['freeze_hash']


def test_candidate_draft_nonce_and_cas_do_not_overwrite_original(client, study_cases):
    value, body = _candidate(client, study_cases)
    replay = _command(client, 'POST', CANDIDATES, body, 201)
    assert replay['id'] == value['id'] and replay['version'] == value['version']
    _command(client, 'POST', CANDIDATES, {**body, 'rationale': '异正文不得借用已有原编号'}, 409)

    path = CANDIDATES + '/' + value['id']
    edit = {**deepcopy(body), 'request_id': _nonce(), 'expected_version': value['version'],
            'rationale': '追加明确限制，保存人稿而非自动声称改善'}
    updated = _record(_command(client, 'PUT', path, edit, 200), 'draft')
    assert updated['id'] == value['id'] and updated['version'] > value['version']
    repeated = _command(client, 'PUT', path, edit, 200)
    assert repeated['id'] == updated['id'] and repeated['version'] == updated['version']
    stale = {**edit, 'request_id': _nonce(), 'rationale': '旧版本不得覆盖更新后的人稿'}
    _command(client, 'PUT', path, stale, 409)
    _command(client, 'PUT', path, {**edit, 'rationale': '同编号改正文也不得覆盖'}, 409)
    cold = _record(_get(client, path), 'draft')
    assert cold['version'] == updated['version'] and cold['rationale'] == edit['rationale']
    assert cold['freeze_hash'] is None
