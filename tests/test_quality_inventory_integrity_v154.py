"""Quality inventory structure proofs over real mapped rows, without a DB.

Only the three read-only SQL result sets are supplied below. The production
inventory, shape, canonical comparison and transition functions all run. These
synthetic declarations, manifests and hashes prove neither a real evaluation
nor permission to approve quality or deploy a policy.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from generic_agents import quality_review_store_v152 as store
from generic_agents.models import Audit
from generic_agents.quality_review_models_v152 import (
    ThresholdQualityCommand as Command,
    ThresholdQualityReview as Review,
)
from generic_agents.quality_review_protocol_v152 import ROOT
from generic_agents.threshold_evolution_protocol_v147 import command_fingerprint, wire_sha256
from generic_agents.typed_payload_codec_v131 import canonical_json


def identity(number):
    return f'154abc00-0000-4000-8000-{number:012x}'


WORKSPACE = identity(1)
CREATOR = identity(2)
APPROVER = identity(3)
REVOKER = identity(4)
REVIEW = identity(5)
EVALUATION = identity(6)
SHADOW = identity(7)
STAMP = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


class ReadOnlyInventorySQL:
    """Return mapped objects for exactly the inventory's public SELECT seams.

    The full join includes missing and duplicate counterparts. No inventory
    result, shape decision, transition or typed comparison is replaced here.
    """
    def __init__(self, reviews, commands, states, command_audits):
        self.reviews = reviews
        self.commands = commands
        self.states = states
        self.command_audits = command_audits
        self.reads = []

    def scalars(self, statement):
        descriptions = statement.column_descriptions
        assert len(descriptions) == 1
        assert statement.get_execution_options().get('populate_existing') is True
        params = tuple(statement.compile().params.values())
        assert WORKSPACE in params
        entity = descriptions[0]['entity']
        if entity is Review:
            self.reads.append('reviews')
            return iter(row for row in self.reviews if row.workspace_id == WORKSPACE)
        assert entity is Audit and store.STATE_ACTION in params
        self.reads.append('states')
        return iter(row for row in self.states
                    if row.workspace_id == WORKSPACE and row.action == store.STATE_ACTION)

    def execute(self, statement):
        assert [item['entity'] for item in statement.column_descriptions] == [Command, Audit]
        assert statement.get_execution_options().get('populate_existing') is True
        joins = statement.get_final_froms()
        assert len(joins) == 1 and joins[0].full is True
        params = tuple(statement.compile().params.values())
        assert WORKSPACE in params and store.COMMAND_ACTION in params
        self.reads.append('command_audit_full_join')
        commands = [row for row in self.commands if row.workspace_id == WORKSPACE]
        audits = [row for row in self.command_audits
                  if row.workspace_id == WORKSPACE and row.action == store.COMMAND_ACTION]
        pairs = []
        for command in commands:
            matches = [issued for issued in audits
                       if issued.workspace_id == command.workspace_id and issued.object_id == command.id]
            pairs.extend((command, issued) for issued in matches)
            if not matches:
                pairs.append((command, None))
        for issued in audits:
            if not any(command.id == issued.object_id and command.workspace_id == issued.workspace_id
                       for command in commands):
                pairs.append((None, issued))
        return pairs


def draft():
    return {
        'evaluation_id': EVALUATION, 'expected_evaluation_version': 7,
        'shadow_id': SHADOW, 'expected_shadow_version': 4,
        'title': '合成库存审核', 'scope_statement': '只检查原固定对象及来源',
        'sampling_statement': '合成测试，不代表真实样本质量',
        'limitations': '没有实际原件、标签或审批来源证明',
        'unresolved_items': [], 'representative_confirmation': True,
        'label_sources': [{'feedback_id': identity(8), 'expected_version': 2, 'material_refs': []}],
    }


def context():
    scope = {
        'workspace_id': WORKSPACE, 'instance_id': identity(9), 'release_id': identity(10),
        'entity_id': identity(11), 'source_id': identity(12), 'baseline_id': identity(13),
        'candidate_capability_id': identity(14), 'source_hash': 'a' * 64,
        'baseline_hash': 'b' * 64, 'candidate_hash': 'c' * 64,
        'rule_id': 'count-threshold', 'field': 'count', 'field_label': '数量',
        'unit': '棵', 'operator': 'gt', 'baseline_threshold': 20.0,
        'candidate_threshold': 18.0, 'instance_name': '合成实例', 'release_name': '合成发布',
        'entity_name': '合成对象', 'source_name': '合成来源',
        'baseline_name': '合成基线', 'candidate_name': '合成候选',
    }
    return {
        'purpose': 'mechanism_check', 'scope': scope,
        'baseline_manifest': {'synthetic_only': True, 'threshold': 20.0},
        'candidate_manifest': {'synthetic_only': True, 'threshold': 18.0},
        'evaluation': {'id': EVALUATION, 'version': 7, 'record_hash': 'd' * 64},
        'shadow': {'id': SHADOW, 'version': 4, 'record_hash': 'e' * 64},
        'samples': [{'group': 'evaluation', 'feedback_id': identity(8), 'value': -0.0, 'count': 1,
                     'label': 'should_not_alert'}],
    }


def frozen(declarations, original):
    return {
        'schema': 'threshold-quality-freeze-v152', 'purpose': original['purpose'],
        'scope': deepcopy(original['scope']), 'criteria': {'max_additional_reviews': 0},
        'evaluation': deepcopy(original['evaluation']), 'shadow': deepcopy(original['shadow']),
        'baseline_manifest': deepcopy(original['baseline_manifest']),
        'candidate_manifest': deepcopy(original['candidate_manifest']),
        'declarations': deepcopy(declarations), 'evaluation_samples': [], 'shadow_samples': [],
        'label_sources': deepcopy(declarations['label_sources']),
        'statistics': {'synthetic_only': True}, 'approved_by': APPROVER,
    }


def apply_snapshot(row, value):
    for key in store.SNAPSHOT_KEYS:
        setattr(row, key, deepcopy(value[key]))
    row.record_hash = store.hash_value(value)


def refresh_command(scene, index, body=None):
    command = scene.commands[index]
    if body is not None:
        command.data = {**deepcopy(command.data),
                        'wire_text': json.dumps(body, ensure_ascii=False, indent=2)}
        command.request_id = body['request_id']
    raw = command.data['wire_text'].encode('utf-8')
    command.wire_sha256 = wire_sha256(raw)
    command.fingerprint = command_fingerprint(
        command.method, command.path, command.workspace_id, command.actor_id, command.wire_sha256)
    command.command_hash = store.hash_value(store.command_payload(command))
    issued = scene.command_audits[index]
    issued.actor_id = command.actor_id
    issued.data = store.command_pin(command)


def reseal_snapshots(scene, snapshots):
    """Make synthetic hashes self-consistent, leaving semantic checks real.

    State version is its strict sequence integer, not a potentially corrupted
    Command snapshot version. Quality State intentionally has no snapshot.
    """
    assert len(snapshots) == len(scene.commands) == len(scene.states)
    previous_hash = None
    for index, (saved, command, issued) in enumerate(zip(snapshots, scene.commands, scene.states)):
        command.data = {**deepcopy(command.data), 'result_snapshot': deepcopy(saved)}
        refresh_command(scene, index)
        record_hash = store.hash_value(saved)
        issued.actor_id = command.actor_id
        issued.data = {
            'schema': 'threshold-quality-state-v152', 'review_id': REVIEW,
            'workspace_id': WORKSPACE, 'actor_id': command.actor_id,
            'version': index + 1, 'status': saved['status'], 'record_hash': record_hash,
            'previous_hash': previous_hash, 'operation': command.operation, 'command_id': command.id,
        }
        previous_hash = record_hash
    apply_snapshot(scene.row, snapshots[-1])


def synthetic_chain(length=3):
    declarations = draft()
    original = context()
    first = {
        'id': REVIEW, 'workspace_id': WORKSPACE, 'actor_id': CREATOR,
        'evaluation_id': EVALUATION, 'shadow_id': SHADOW, 'status': 'draft', 'version': 1,
        'approved_by': None, 'approval_hash': None,
        'data': {'schema': 'threshold-quality-review-record-v152', 'draft': declarations,
                 'context': original, 'frozen': None},
    }
    second = deepcopy(first)
    second.update(version=2, status='approved', approved_by=APPROVER)
    second['data']['frozen'] = frozen(declarations, original)
    second['approval_hash'] = store.hash_value(second['data']['frozen'])
    third = deepcopy(second)
    third.update(version=3, status='revoked')
    snapshots = [first, second, third][:length]
    row = Review(id=REVIEW, created_at=STAMP)
    commands = []
    states = []
    command_audits = []
    operations = [('create', CREATOR), ('approve', APPROVER), ('revoke', REVOKER)]
    for index, (operation, actor) in enumerate(operations[:length]):
        request_id = identity(30 + index)
        body = ({'schema': 'threshold-quality-draft-v152', 'request_id': request_id,
                 **deepcopy(declarations)} if operation == 'create' else {
                    'schema': 'threshold-quality-control-v152', 'request_id': request_id,
                    'expected_version': index, 'reason': '保留合成人类原说明 ' + operation, 'confirm': True})
        commands.append(Command(
            id=identity(40 + index), created_at=STAMP, workspace_id=WORKSPACE,
            actor_id=actor, request_id=request_id, version=1, method='POST',
            path=ROOT if operation == 'create' else ROOT + '/' + REVIEW + '/' + operation,
            operation=operation, target_id=REVIEW, outcome='saved',
            data={'schema': 'threshold-quality-command-record-v152',
                  'wire_text': json.dumps(body, ensure_ascii=False, indent=2), 'result_snapshot': {}},
        ))
        states.append(Audit(id=identity(50 + index), created_at=STAMP, workspace_id=WORKSPACE,
                            actor_id=actor, action=store.STATE_ACTION, object_id=REVIEW, version=1))
        command_audits.append(Audit(id=identity(60 + index), created_at=STAMP, workspace_id=WORKSPACE,
                                    actor_id=actor, action=store.COMMAND_ACTION,
                                    object_id=commands[-1].id, version=1))
    scene = SimpleNamespace(row=row, reviews=[row], commands=commands, states=states,
                            command_audits=command_audits,
                            principal=SimpleNamespace(user_id=REVOKER, workspace_id=WORKSPACE, role='admin'))
    reseal_snapshots(scene, snapshots)
    scene.db = ReadOnlyInventorySQL(scene.reviews, commands, states, command_audits)
    return scene


def saved_snapshots(scene):
    return [deepcopy(command.data['result_snapshot']) for command in scene.commands]


def original_payloads(scene):
    return canonical_json({
        'reviews': [store.snapshot(row) | {'record_hash': row.record_hash} for row in scene.reviews],
        'commands': [store.command_payload(row) | {'command_hash': row.command_hash} for row in scene.commands],
        'states': [{'id': row.id, 'actor_id': row.actor_id, 'data': row.data} for row in scene.states],
        'command_audits': [{'id': row.id, 'actor_id': row.actor_id, 'data': row.data}
                           for row in scene.command_audits],
    })


def rejected(scene):
    before = original_payloads(scene)
    with pytest.raises(store.QualityInventoryError) as error:
        store.inventory(scene.db, scene.principal)
    assert error.value.status_code == 409 and error.value.detail == store.ERROR
    assert original_payloads(scene) == before


def test_complete_mapped_chain_keeps_creator_and_distinct_control_actors_without_writes():
    scene = synthetic_chain()
    before = original_payloads(scene)
    reviews, commands = store.inventory(scene.db, scene.principal)
    assert len(reviews) == 1 and reviews[0] is scene.row
    assert commands == scene.commands and scene.row.actor_id == CREATOR
    assert scene.row.approved_by == APPROVER and scene.commands[-1].actor_id == REVOKER
    assert scene.db.reads == ['reviews', 'states', 'command_audit_full_join']
    assert all('snapshot' not in issued.data for issued in scene.states)
    assert original_payloads(scene) == before


@pytest.mark.parametrize('length', [1, 2])
def test_valid_draft_and_approved_heads_do_not_require_a_later_state(length):
    scene = synthetic_chain(length)
    reviews, commands = store.inventory(scene.db, scene.principal)
    assert reviews[0] is scene.row and len(commands) == length


@pytest.mark.parametrize('fault', [
    'evaluation_float_version', 'shadow_float_version', 'shadow_bool_version', 'extra_context_key',
])
def test_command_result_must_keep_its_own_full_shape_even_with_self_consistent_hashes(fault):
    scene = synthetic_chain(1)
    snapshots = saved_snapshots(scene)
    current = snapshots[0]['data']['context']
    if fault == 'evaluation_float_version':
        current['evaluation']['version'] = 7.0
    elif fault == 'shadow_float_version':
        current['shadow']['version'] = 4.0
    elif fault == 'shadow_bool_version':
        snapshots[0]['data']['draft']['expected_shadow_version'] = 1
        current['shadow']['version'] = True
        body = json.loads(scene.commands[0].data['wire_text'])
        body['expected_shadow_version'] = 1
        refresh_command(scene, 0, body)
    else:
        current['server_approved'] = True
    reseal_snapshots(scene, snapshots)
    rejected(scene)


@pytest.mark.parametrize('value', [True, 3.0, 0, None, [3], {'version': 3}])
def test_current_row_version_never_inherits_python_numeric_aliases_or_bad_shape(value):
    scene = synthetic_chain(1 if value is True else 3)
    scene.row.version = deepcopy(value)
    scene.row.record_hash = store.hash_value(store.snapshot(scene.row))
    rejected(scene)


@pytest.mark.parametrize('key,replacement', [
    ('value', 0), ('value', False), ('value', 0.0), ('count', True), ('count', 1.0),
])
def test_current_row_must_match_final_snapshot_with_exact_numeric_type_and_zero_sign(key, replacement):
    scene = synthetic_chain()
    value = deepcopy(scene.row.data)
    value['context']['samples'][0][key] = replacement
    scene.row.data = value
    scene.row.record_hash = store.hash_value(store.snapshot(scene.row))
    rejected(scene)


def test_current_row_hash_is_checked_even_when_the_whole_snapshot_matches():
    scene = synthetic_chain()
    scene.row.record_hash = 'f' * 64
    rejected(scene)


@pytest.mark.parametrize('value', [True, 1.0, [1], {'version': 1}])
def test_saved_command_version_fails_closed_before_bad_dictionary_key_lookup(value):
    scene = synthetic_chain()
    command = scene.commands[0]
    data = deepcopy(command.data)
    data['result_snapshot']['version'] = deepcopy(value)
    command.data = data
    refresh_command(scene, 0)
    rejected(scene)


@pytest.mark.parametrize('value', [True, 1.0, [1], {'version': 1}])
def test_state_version_is_strict_before_indexing_or_sorting(value):
    scene = synthetic_chain()
    scene.states[0].data = {**scene.states[0].data, 'version': deepcopy(value)}
    rejected(scene)


@pytest.mark.parametrize('missing', ['state', 'command', 'command_audit', 'all_states', 'review'])
def test_missing_inventory_edges_never_turn_a_saved_review_into_an_older_head(missing):
    scene = synthetic_chain()
    if missing == 'state':
        scene.states.pop()
    elif missing == 'command':
        scene.commands.pop()
    elif missing == 'command_audit':
        scene.command_audits.pop()
    elif missing == 'all_states':
        scene.states.clear()
    else:
        scene.reviews.clear()
    rejected(scene)


@pytest.mark.parametrize('duplicate', ['state', 'command_audit'])
def test_duplicate_inventory_edges_are_rejected(duplicate):
    scene = synthetic_chain()
    originals = scene.states if duplicate == 'state' else scene.command_audits
    row = originals[-1]
    originals.append(Audit(id=identity(90), created_at=STAMP, workspace_id=row.workspace_id,
                           actor_id=row.actor_id, action=row.action, object_id=row.object_id,
                           version=1, data=deepcopy(row.data)))
    rejected(scene)


@pytest.mark.parametrize('fault', ['wire_bytes', 'fingerprint', 'command_hash', 'audit_actor', 'state_actor', 'path'])
def test_wire_and_actor_bindings_are_not_replaced_by_snapshot_equality(fault):
    scene = synthetic_chain()
    command = scene.commands[1]
    if fault == 'wire_bytes':
        command.data = {**deepcopy(command.data), 'wire_text': command.data['wire_text'] + '\n'}
    elif fault == 'fingerprint':
        command.fingerprint = 'f' * 64
    elif fault == 'command_hash':
        command.command_hash = 'f' * 64
    elif fault == 'audit_actor':
        scene.command_audits[1].actor_id = CREATOR
    elif fault == 'state_actor':
        scene.states[1].actor_id = CREATOR
        scene.states[1].data = {**scene.states[1].data, 'actor_id': CREATOR}
    else:
        command.path = ROOT + '/' + identity(99) + '/approve'
        refresh_command(scene, 1)
    rejected(scene)


def test_same_actor_nonce_collision_is_rejected_even_with_individually_valid_commands():
    scene = synthetic_chain()
    command = scene.commands[2]
    command.actor_id = APPROVER
    body = json.loads(command.data['wire_text'])
    body['request_id'] = scene.commands[1].request_id
    refresh_command(scene, 2, body)
    scene.states[2].actor_id = APPROVER
    scene.states[2].data = {**scene.states[2].data, 'actor_id': APPROVER}
    rejected(scene)


def test_same_nonce_for_different_actors_keeps_the_original_actor_fingerprints():
    scene = synthetic_chain()
    body = json.loads(scene.commands[1].data['wire_text'])
    body['request_id'] = scene.commands[0].request_id
    refresh_command(scene, 1, body)
    reviews, commands = store.inventory(scene.db, scene.principal)
    assert reviews[0] is scene.row and len(commands) == 3
    assert commands[0].request_id == commands[1].request_id
    assert commands[0].actor_id != commands[1].actor_id
    assert commands[0].fingerprint != commands[1].fingerprint


@pytest.mark.parametrize('fault', ['approval_changes_scope', 'revoke_stays_approved', 'revoke_wrong_cas'])
def test_self_consistent_hashes_do_not_make_an_illegal_transition_valid(fault):
    scene = synthetic_chain()
    snapshots = saved_snapshots(scene)
    if fault == 'approval_changes_scope':
        for saved in snapshots[1:]:
            saved['data']['context']['scope']['baseline_threshold'] = 21.0
            saved['data']['frozen']['scope']['baseline_threshold'] = 21.0
            saved['approval_hash'] = store.hash_value(saved['data']['frozen'])
    elif fault == 'revoke_stays_approved':
        snapshots[2]['status'] = 'approved'
    else:
        body = json.loads(scene.commands[2].data['wire_text'])
        body['expected_version'] = 1
        refresh_command(scene, 2, body)
    reseal_snapshots(scene, snapshots)
    rejected(scene)


def test_no_state_does_not_validate_a_zero_version_row():
    scene = synthetic_chain(1)
    scene.states.clear()
    scene.commands.clear()
    scene.command_audits.clear()
    scene.row.version = 0
    scene.row.record_hash = store.hash_value(store.snapshot(scene.row))
    rejected(scene)
