"""Policy inventory equivalence at the real shape/transition boundary; no DB.

Mapped Grant/Command/Audit rows and original wire pins are real model objects.
The approved Quality snapshot is synthetic and only satisfies its actual shape;
it proves no review provenance, representative evidence or deployment authority.
Only Session IO is doubled. Inventory, shape, transitions and typed equality are
never patched, so an optimization cannot inherit validation from unequal rows.
"""
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest

from generic_agents.contracts import digest
from generic_agents.local_rules_api import CreateRule, _assemble_manifest
from generic_agents.models import Audit
from generic_agents import quality_review_store_v152 as quality
from generic_agents.quality_review_models_v152 import ThresholdQualityReview
from generic_agents import threshold_policy_store_v153 as store
from generic_agents.threshold_policy_models_v152 import (
    ThresholdPolicyGrant, ThresholdPolicyCommand, ThresholdPolicySelection,
)
from generic_agents.threshold_policy_protocol_v152 import ROOT
from generic_agents.threshold_evolution_protocol_v147 import command_fingerprint, wire_sha256
from generic_agents.typed_payload_codec_v131 import canonical_json


def identity(number):
    return f'00000000-0000-4000-8000-{number:012x}'


def managed_manifest(threshold, request_id):
    body = CreateRule.model_validate({
        'request_id': UUID(request_id), 'title': '仅供库存结构测试的温度规则',
        'entity_id': identity(32), 'rationale': '合成结构夹具，不证明现场效果或真实批准',
        'rules': [{'id': 'warm', 'field': 'temperature', 'unit': 'C',
                   'operator': 'gt', 'threshold': threshold}],
    })
    fields = {'temperature': {'type': 'number', 'unit': 'C', 'minimum': -100, 'maximum': 100}}
    return _assemble_manifest(body, UUID(request_id), digest(body.model_dump(mode='json')),
        identity(1), identity(36), 'site', identity(32), fields, {'temperature': 'C'}).model_dump(mode='json')


def synthetic_approved_review(scope, baseline, candidate):
    draft = {
        'evaluation_id': identity(21), 'expected_evaluation_version': 3,
        'shadow_id': identity(22), 'expected_shadow_version': 2,
        'title': '库存形状专用合成审核', 'scope_statement': '不授予实际来源权限',
        'sampling_statement': '无实际样本，仅测试已保存结构',
        'limitations': '不代表实际审批、代表性质量或试用资格',
        'unresolved_items': [], 'representative_confirmation': True, 'label_sources': [],
    }
    context = {
        'purpose': 'mechanism_check', 'scope': deepcopy(scope),
        'baseline_manifest': deepcopy(baseline), 'candidate_manifest': deepcopy(candidate),
        'evaluation': {'id': draft['evaluation_id'], 'version': 3},
        'shadow': {'id': draft['shadow_id'], 'version': 2}, 'samples': [],
    }
    frozen = {
        'schema': 'threshold-quality-freeze-v152', 'purpose': context['purpose'],
        'scope': deepcopy(scope), 'criteria': {'max_additional_reviews': 0},
        'evaluation': deepcopy(context['evaluation']), 'shadow': deepcopy(context['shadow']),
        'baseline_manifest': deepcopy(baseline), 'candidate_manifest': deepcopy(candidate),
        'declarations': deepcopy(draft), 'evaluation_samples': [], 'shadow_samples': [],
        'label_sources': [], 'statistics': {}, 'approved_by': identity(1),
    }
    row = ThresholdQualityReview(
        id=identity(20), workspace_id=identity(2), actor_id=identity(1),
        evaluation_id=identity(21), shadow_id=identity(22), status='approved', version=2,
        approved_by=identity(1), approval_hash=quality.hash_value(frozen),
        data={'schema': 'threshold-quality-review-record-v152',
              'draft': draft, 'context': context, 'frozen': frozen},
    )
    row.record_hash = quality.hash_value(quality.snapshot(row))
    return row


def policy_case():
    baseline = managed_manifest(20.0, identity(40))
    candidate = managed_manifest(18.0, identity(41))
    scope = {
        'workspace_id': identity(2), 'instance_id': identity(30), 'release_id': identity(31),
        'entity_id': identity(32), 'source_id': identity(33), 'baseline_id': identity(34),
        'baseline_hash': store.hash_value(baseline), 'candidate_capability_id': identity(35),
        'candidate_hash': store.hash_value(candidate), 'source_hash': 'c' * 64,
        'rule_id': 'warm', 'field': 'temperature', 'field_label': '登记温度', 'unit': 'C',
        'operator': 'gt', 'baseline_threshold': 20.0, 'candidate_threshold': 18.0,
        'instance_name': '结构测试实例', 'release_name': '结构测试发布',
        'entity_name': '结构测试对象', 'source_name': '结构测试来源',
        'baseline_name': '合成原基线', 'candidate_name': '合成候选',
    }
    review = synthetic_approved_review(scope, baseline, candidate)
    data = {
        'schema': 'threshold-policy-grant-record-v153', 'scope': scope,
        'review': {'pin': {'id': review.id, 'version': review.version,
                  'record_hash': review.record_hash, 'approval_hash': review.approval_hash},
                  'approved_snapshot': quality.snapshot(review)},
        'baseline_manifest': baseline, 'candidate_manifest': candidate,
        'purpose': 'mechanism_check', 'criteria': {'max_additional_reviews': 0},
        'frontier': {'schema': 'threshold-shadow-frontier-v151',
                     'observation_ids': [], 'source_record_ids': []},
        'epoch': 1, 'max_candidate_inputs': 4,
        'expires_at': '2030-01-02T03:04:05+08:00',
        'title': '合成有限策略', 'reason': '合成创建理由', 'rollback': None,
        'environment_at_approval': 'verification',
    }
    first = {
        'id': identity(10), 'workspace_id': identity(2), 'actor_id': identity(1),
        'review_id': review.id, 'scope_key': store.scope_key(scope),
        'status': 'active', 'version': 1, 'data': data,
    }
    paused = deepcopy(first)
    paused.update(status='paused', version=2)
    paused['data']['reason'] = '合成暂停理由'
    rolled_back = deepcopy(paused)
    rolled_back.update(status='rolled_back', version=3)
    rolled_back['data']['reason'] = '合成回滚理由'
    rolled_back['data']['rollback'] = {
        'reason': '合成回滚理由', 'origin': 'human', 'selection_id': None, 'basis': None,
    }
    case = SimpleNamespace(
        principal=SimpleNamespace(workspace_id=identity(2), user_id=identity(1),
                                  role='admin', session_id=identity(3)),
        review=review, snapshots=[first, paused, rolled_back], grants=[],
        commands=[], command_audits=[], states=[], reads=[],
    )
    rebuild_pins(case)
    return case


def repin_command(case, index):
    command = case.commands[index]
    raw = command.data['wire_text'].encode('utf-8')
    command.wire_sha256 = wire_sha256(raw)
    command.fingerprint = command_fingerprint(command.method, command.path,
        command.workspace_id, command.actor_id, command.wire_sha256)
    command.command_hash = store.hash_value(store.command_payload(command))
    case.command_audits[index].data = store.command_pin(command)


def rebuild_pins(case):
    """Use valid hashes around the supplied snapshots, not a fake validation gate."""
    case.commands, case.command_audits, case.states = [], [], []
    previous_hash = None
    for index, (operation, saved) in enumerate(zip(('create', 'pause', 'rollback'), case.snapshots)):
        nonce, command_id = identity(200 + index), identity(100 + index)
        if operation == 'create':
            body = {
                'schema': 'threshold-policy-create-v152', 'request_id': nonce,
                'review_id': saved['review_id'], 'expected_review_version': 2,
                'title': '合成有限策略', 'reason': '合成创建理由', 'max_candidate_inputs': 4,
                'expires_at': '2030-01-02T03:04:05+08:00', 'confirm_use': True,
            }
            path = ROOT
        else:
            body = {
                'schema': 'threshold-policy-control-v152', 'request_id': nonce,
                'expected_version': index, 'reason': saved['data']['reason'], 'confirm': True,
            }
            path = ROOT + '/' + identity(10) + '/' + operation
        command = ThresholdPolicyCommand(
            id=command_id, workspace_id=identity(2), actor_id=identity(1), version=1,
            request_id=nonce, method='POST', path=path, operation=operation,
            target_id=identity(10), policy_id=identity(10), outcome='saved',
            data={'schema': 'threshold-policy-command-record-v153',
                  'wire_text': canonical_json(body), 'result_snapshot': deepcopy(saved)},
        )
        case.commands.append(command)
        case.command_audits.append(Audit(
            id=identity(400 + index), workspace_id=identity(2), actor_id=identity(1),
            action=store.COMMAND_ACTION, object_id=command_id, version=1, data={},
        ))
        repin_command(case, index)
        record_hash = store.hash_value(saved)
        case.states.append(Audit(
            id=identity(300 + index), workspace_id=identity(2), actor_id=identity(1),
            action=store.STATE_ACTION, object_id=identity(10), version=1,
            data={'schema': 'threshold-policy-state-v153', 'policy_id': identity(10),
                  'workspace_id': identity(2), 'actor_id': identity(1),
                  'version': saved['version'], 'status': saved['status'], 'record_hash': record_hash,
                  'previous_hash': previous_hash, 'operation': operation, 'command_id': command_id,
                  'monitor_basis': None, 'snapshot': deepcopy(saved)},
        ))
        previous_hash = record_hash
    case.grants = [ThresholdPolicyGrant(**deepcopy(case.snapshots[-1]), record_hash=previous_hash)]


class ReadOnlyInventoryIO:
    """Only collection reads and full-outer Command/Audit pairing, never checks."""
    def __init__(self, case):
        self.case = case

    def scalars(self, statement):
        entity = statement.column_descriptions[0]['entity']
        self.case.reads.append(('scalars', entity))
        collections = {ThresholdPolicyGrant: self.case.grants,
                       ThresholdPolicySelection: [], Audit: self.case.states}
        assert entity in collections, 'unexpected inventory read; extend this IO seam explicitly'
        return iter(collections[entity])

    def execute(self, statement):
        entities = tuple(item['entity'] for item in statement.column_descriptions)
        assert entities == (ThresholdPolicyCommand, Audit), 'only the original full-outer inventory join is allowed'
        self.case.reads.append(('execute', entities))
        pairs, seen = [], set()
        for command in self.case.commands:
            matches = [issued for issued in self.case.command_audits
                       if issued.workspace_id == command.workspace_id
                       and issued.action == store.COMMAND_ACTION and issued.object_id == command.id]
            if not matches:
                pairs.append((command, None))
            for issued in matches:
                pairs.append((command, issued))
                seen.add(id(issued))
        pairs.extend((None, issued) for issued in self.case.command_audits if id(issued) not in seen)
        return iter(pairs)


def assert_inventory_rejected(case):
    with pytest.raises(store.PolicyInventoryError) as error:
        store.inventory(ReadOnlyInventoryIO(case), case.principal)
    assert error.value.status_code == 409
    assert error.value.detail == store.ERROR


def test_real_mapped_human_create_pause_rollback_chain_passes_without_mutation():
    case = policy_case()
    before = canonical_json([store.snapshot(case.grants[0]),
        [item.data for item in case.states], [store.command_payload(item) for item in case.commands]])
    grants, commands = store.inventory(ReadOnlyInventoryIO(case), case.principal)
    assert grants == case.grants and commands == case.commands
    assert grants[0].status == 'rolled_back' and grants[0].version == 3
    assert [item.operation for item in commands] == ['create', 'pause', 'rollback']
    unchanged = before == canonical_json([store.snapshot(case.grants[0]),
        [item.data for item in case.states], [store.command_payload(item) for item in case.commands]])
    assert unchanged, 'inventory must not repair or rewrite a historical row'


@pytest.mark.parametrize('fault', [
    'current-hash', 'current-version-zero', 'current-extra-field',
    'command-hash', 'command-version-bool', 'command-actor', 'command-nonce', 'command-wire',
    'command-result', 'command-audit-hash', 'command-audit-actor', 'command-audit-object',
    'state-hash', 'state-previous-hash', 'state-actor', 'state-pin-actor', 'state-version-bool',
    'state-snapshot-version', 'state-operation', 'state-command', 'state-extra-field',
    'missing-create-state', 'missing-middle-state', 'missing-tail-state',
    'missing-command', 'missing-command-audit', 'audit-only', 'missing-grant', 'duplicate-state',
])
def test_single_sided_inventory_damage_never_inherits_a_valid_neighbour(fault):
    case = policy_case()
    current, command, issued, state = case.grants[0], case.commands[1], case.command_audits[1], case.states[1]
    if fault == 'current-hash': current.record_hash = '0' * 64
    elif fault == 'current-version-zero': current.version = 0
    elif fault == 'current-extra-field': current.data = {**current.data, 'extra': True}
    elif fault == 'command-hash': command.command_hash = '0' * 64
    elif fault == 'command-version-bool': command.version = True
    elif fault == 'command-actor': command.actor_id = identity(99)
    elif fault == 'command-nonce': command.request_id = identity(99)
    elif fault == 'command-wire': command.data = {**command.data, 'wire_text': command.data['wire_text'] + ' '}
    elif fault == 'command-result':
        command.data = deepcopy(command.data)
        command.data['result_snapshot']['data']['reason'] = '不同的独立命令结果'
    elif fault == 'command-audit-hash': issued.data = {**issued.data, 'command_hash': '0' * 64}
    elif fault == 'command-audit-actor': issued.actor_id = identity(99)
    elif fault == 'command-audit-object': issued.object_id = identity(99)
    elif fault == 'state-hash': state.data = {**state.data, 'record_hash': '0' * 64}
    elif fault == 'state-previous-hash': state.data = {**state.data, 'previous_hash': '0' * 64}
    elif fault == 'state-actor': state.actor_id = identity(99)
    elif fault == 'state-pin-actor': state.data = {**state.data, 'actor_id': identity(99)}
    elif fault == 'state-version-bool': state.data = {**state.data, 'version': True}
    elif fault == 'state-snapshot-version':
        state.data = deepcopy(state.data); state.data['snapshot']['version'] = 99
    elif fault == 'state-operation': state.data = {**state.data, 'operation': 'resume'}
    elif fault == 'state-command': state.data = {**state.data, 'command_id': identity(99)}
    elif fault == 'state-extra-field': state.data = {**state.data, 'extra': True}
    elif fault == 'missing-create-state': del case.states[0]
    elif fault == 'missing-middle-state': del case.states[1]
    elif fault == 'missing-tail-state': del case.states[-1]
    elif fault == 'missing-command': del case.commands[1]
    elif fault == 'missing-command-audit': del case.command_audits[1]
    elif fault == 'audit-only':
        case.command_audits.append(Audit(id=identity(999), workspace_id=identity(2), actor_id=identity(1),
            action=store.COMMAND_ACTION, object_id=identity(998), version=1, data=deepcopy(issued.data)))
    elif fault == 'missing-grant': case.grants = []
    elif fault == 'duplicate-state':
        case.states.append(Audit(id=identity(999), workspace_id=state.workspace_id, actor_id=state.actor_id,
            action=state.action, object_id=state.object_id, version=1, data=deepcopy(state.data)))
    else: pytest.fail('the requested damage case must be explicit')
    assert_inventory_rejected(case)


@pytest.mark.parametrize('side', ['current', 'command', 'state'])
@pytest.mark.parametrize('kind', ['bool-for-one', 'one-for-bool', 'integer-for-float', 'float-for-integer'])
def test_typed_single_sided_changes_reject_even_with_self_consistent_hashes(side, kind):
    case = policy_case()
    if side == 'current':
        changed = store.snapshot(case.grants[0])
    elif side == 'command':
        changed = deepcopy(case.commands[1].data['result_snapshot'])
    else:
        changed = deepcopy(case.states[1].data['snapshot'])
    if kind == 'bool-for-one': changed['data']['epoch'] = True
    elif kind == 'one-for-bool':
        changed['data']['review']['approved_snapshot']['data']['draft']['representative_confirmation'] = 1
    elif kind == 'integer-for-float': changed['data']['scope']['baseline_threshold'] = 20
    else: changed['data']['max_candidate_inputs'] = 4.0
    if side == 'current':
        case.grants = [ThresholdPolicyGrant(**changed, record_hash=store.hash_value(changed))]
    elif side == 'command':
        case.commands[1].data = {**case.commands[1].data, 'result_snapshot': changed}
        repin_command(case, 1)
    else:
        case.states[1].data = {**case.states[1].data, 'snapshot': changed, 'record_hash': store.hash_value(changed)}
    assert_inventory_rejected(case)


@pytest.mark.parametrize('fault', ['pause-changes-epoch', 'pause-changes-frontier', 'rollback-changes-budget'])
def test_shape_valid_rehashed_chains_still_require_original_transition_semantics(fault):
    case = policy_case()
    if fault == 'pause-changes-epoch':
        for saved in case.snapshots[1:]: saved['data']['epoch'] = 2
    elif fault == 'pause-changes-frontier':
        for saved in case.snapshots[1:]: saved['data']['frontier']['source_record_ids'] = ['not-admitted-by-pause']
    else:
        case.snapshots[-1]['data']['max_candidate_inputs'] = 5
    # Current row, every Command result, CommandAudit, State hash and hash link
    # all agree: only the actual transition rules should reject the change.
    rebuild_pins(case)
    assert_inventory_rejected(case)


def test_duplicate_original_nonce_is_rejected_after_wire_and_audit_pins_are_recomputed():
    case = policy_case()
    command = case.commands[1]
    original = store.read_command(command.data['wire_text'].encode('utf-8'))
    original['request_id'] = case.commands[0].request_id
    command.request_id = original['request_id']
    command.data = {**command.data, 'wire_text': canonical_json(original)}
    repin_command(case, 1)
    assert_inventory_rejected(case)


def test_state_cannot_inherit_quality_shape_from_another_valid_snapshot():
    case = policy_case()
    changed = case.snapshots[1]['data']['review']['approved_snapshot']
    changed['data']['draft']['representative_confirmation'] = 1
    # Rebuild only storage/transport hashes; do not replace the real strict
    # QualityDraft or Quality._shape checks with a fixture success response.
    rebuild_pins(case)
    assert_inventory_rejected(case)
