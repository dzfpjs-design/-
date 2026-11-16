"""Real HTTP reads of retained feedback and an original saved-command reference.

Root supplies feedback_recovery_case from actual retained Command/feedback rows.
These tests only GET; they do not register observations, replay the original
command, retire a request, dispatch a worker or manufacture machine evidence.
"""
import hashlib
import json
from uuid import UUID


ROOT = '/api/local-rules/evolution'
ENTRY_KEYS = {'id', 'version', 'root_id', 'revision', 'previous_id', 'status',
              'actor_id', 'label', 'reason', 'cancel_reason'}
REFERENCE_KEYS = {'observation_id', 'instance_id', 'release_id',
                  'baseline_capability_id', 'rule_id', 'basis_hash'}
FLAGS = {'eligible_for_candidate', 'can_edit', 'can_cancel', 'can_submit', 'can_revise'}
HISTORY_KEYS = {'schema', 'entry', 'history', 'latest_submitted', 'basis_reference',
                'basis_available', 'issue'} | FLAGS
SAVED_KEYS = {'command_id', 'feedback_id', 'root_id', 'revision', 'version', 'status', 'result_hash'}
VIEW_KEYS = {'schema', 'classification', 'basis', 'history', 'latest_submitted',
             'can_edit', 'can_cancel', 'can_submit', 'can_revise'} | ENTRY_KEYS


def get(client, path, *, params=None):
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    value = response.json()
    assert type(value) is dict
    return value


def typed_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fixture_original(case, threshold_cases):
    assert set(case) == {'command_id', 'request_id', 'method', 'path', 'sha',
                         'feedback_id', 'retirement_body', 'saved'}
    for key in ('command_id', 'request_id', 'feedback_id'):
        assert str(UUID(case[key])) == case[key]
    body = case['retirement_body']
    raw = body['original_wire_text'].encode('utf-8')
    original = json.loads(raw)
    assert original['request_id'] == case['request_id']
    assert body['original_method'] == case['method'] == 'POST'
    assert body['original_path'] == case['path'] == ROOT + '/feedback'
    assert body['original_sha256'] == case['sha'] == hashlib.sha256(raw).hexdigest()
    assert original['rule_id'] == threshold_cases['rule_id']
    assert original['observation_id'] in {
        item['record']['id'] for item in threshold_cases['observations'].values()}
    assert set(case['saved']) == SAVED_KEYS
    assert case['saved']['command_id'] == case['command_id']
    assert case['saved']['feedback_id'] == case['feedback_id']
    return original


def old_view(client, identity):
    value = get(client, ROOT + '/feedback/' + identity)
    assert set(value) == VIEW_KEYS, 'the V147 actionable view must keep its original closed shape'
    assert value['schema'] == 'threshold-evolution-feedback-view-v147'
    assert value['id'] == identity
    return value


def human_entry(value):
    assert set(value) == ENTRY_KEYS | {'classification'}
    return {key: value[key] for key in ENTRY_KEYS}


def expected_history(view):
    entry = human_entry({key: view[key] for key in ENTRY_KEYS | {'classification'}})
    return {
        'schema': 'threshold-evolution-feedback-history-v148',
        'entry': entry,
        'history': [human_entry(item) for item in view['history']],
        'latest_submitted': human_entry(view['latest_submitted']) if view['latest_submitted'] else None,
        'basis_reference': {key: view['basis'][key] for key in REFERENCE_KEYS},
        'basis_available': True, 'issue': None,
        **{key: False for key in FLAGS},
    }


def check_history(value, view):
    assert set(value) == HISTORY_KEYS
    assert set(value['entry']) == ENTRY_KEYS
    assert type(value['history']) is list and value['history']
    assert all(set(entry) == ENTRY_KEYS for entry in value['history'])
    if value['latest_submitted'] is not None:
        assert set(value['latest_submitted']) == ENTRY_KEYS
    assert set(value['basis_reference']) == REFERENCE_KEYS
    assert value['basis_available'] is True
    assert value['issue'] is None
    assert all(value[key] is False for key in FLAGS), 'history never restores write or candidate authority'
    assert typed_json(value) == typed_json(expected_history(view))
    assert 'classification' not in value['entry']
    assert not {'actual_value', 'actual_hit', 'threshold', 'proof', 'wire_text'} & set(value['basis_reference'])


def test_feedback_history_is_a_closed_readonly_manual_projection_and_keeps_v147_view(
        client, feedback_recovery_case, threshold_cases):
    case = feedback_recovery_case
    original = fixture_original(case, threshold_cases)
    before = old_view(client, case['feedback_id'])
    assert before['basis']['observation_id'] == original['observation_id']
    assert before['basis']['rule_id'] == original['rule_id']
    assert before['status'] == 'submitted' and before['latest_submitted'] is not None
    assert before['can_revise'] is True, 'the ordinary view and readonly history have different authority'

    history = get(client, ROOT + '/feedback/' + case['feedback_id'] + '/history')

    check_history(history, before)
    assert history['latest_submitted']['reason'] == before['latest_submitted']['reason']
    assert typed_json(old_view(client, case['feedback_id'])) == typed_json(before)


def test_observation_history_lookup_finds_the_original_feedback_head_without_a_new_request(
        client, feedback_recovery_case, threshold_cases):
    case = feedback_recovery_case
    original = fixture_original(case, threshold_cases)
    initial = old_view(client, case['feedback_id'])
    head_id = initial['history'][-1]['id']
    head = old_view(client, head_id)

    found = get(client, ROOT + '/observations/' + original['observation_id'] + '/feedback-history',
        params={'rule_id': original['rule_id']})

    assert set(found) == {'schema', 'found', 'history'}
    assert found['schema'] == 'threshold-feedback-history-lookup-v148'
    assert found['found'] is True
    check_history(found['history'], head)
    direct = get(client, ROOT + '/feedback/' + head_id + '/history')
    assert typed_json(found['history']) == typed_json(direct)
    assert typed_json(old_view(client, case['feedback_id'])) == typed_json(initial)


def test_saved_status_uses_the_actual_original_snapshot_not_the_current_feedback_version(
        client, feedback_recovery_case, threshold_cases):
    case = feedback_recovery_case
    fixture_original(case, threshold_cases)
    current = old_view(client, case['feedback_id'])
    saved = case['saved']  # Root's actual Command.result_snapshot projection + canonical hash.
    assert saved['version'] == 1 and saved['status'] == 'draft'
    assert current['version'] > saved['version'] and current['status'] == 'submitted'
    params = {'expected_sha256': case['sha'], 'original_method': case['method'],
              'original_path': case['path']}

    status = get(client, ROOT + '/requests/' + case['request_id'] + '/status', params=params)

    assert set(status) == {'schema', 'original_request_id', 'original_method', 'original_path',
                           'original_sha256', 'outcome', 'saved', 'retirement'}
    assert status['schema'] == 'threshold-request-state-v148'
    assert status['original_request_id'] == case['request_id']
    assert status['original_method'] == case['method']
    assert status['original_path'] == case['path']
    assert status['original_sha256'] == case['sha']
    assert status['outcome'] == 'saved' and status['retirement'] is None
    assert set(status['saved']) == SAVED_KEYS
    assert typed_json(status['saved']) == typed_json(saved)
    assert status['saved']['version'] != current['version']
    assert status['saved']['status'] != current['status']
    assert len(status['saved']['result_hash']) == 64
    assert all(char in '0123456789abcdef' for char in status['saved']['result_hash'])

    legacy = get(client, ROOT + '/requests/' + case['request_id'], params=params)
    assert set(legacy) == {'schema', 'found', 'feedback'}
    assert legacy['schema'] == 'threshold-evolution-request-recovery-v147'
    assert legacy['found'] is True
    assert typed_json(legacy['feedback']) == typed_json(current)
    assert typed_json(old_view(client, case['feedback_id'])) == typed_json(current)
