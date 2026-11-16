"""Feedback discovery seam only; these tests are not PG or source-authority proof.

The actual monitor lookup is exercised. Its existing feedback pin validators and
complete-chain reader are separate seams, supplied here without a database.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import threshold_evolution_api_v147 as feedback
from generic_agents import threshold_feedback_history_v148 as history
from generic_agents import threshold_policy_monitor_v153 as monitor
from generic_agents.models import Audit
from generic_agents.threshold_evolution_models_v147 import FeedbackRevision, ThresholdCommand


def discovery_case(monkeypatch, *, records=True, audits=True, commands=True,
                   audit_root='root', command_root='root'):
    principal = SimpleNamespace(workspace_id='workspace', user_id='reviewer', role='admin')
    scope = {
        'candidate_capability_id': 'candidate', 'rule_id': 'temperature-rule',
        'field': 'temperature', 'instance_id': 'instance', 'release_id': 'release',
        'entity_id': 'entity', 'source_id': 'source', 'operator': 'gt',
        'baseline_threshold': 20, 'candidate_threshold': 18,
    }
    grant = SimpleNamespace(data={'scope': deepcopy(scope)})
    selection = SimpleNamespace(
        id='selection', observation_id='observation', version=3, record_hash='a' * 64,
        data={'original_request': {'attributes': {'temperature': 19}},
              'result': {'actual_matched': True}},
    )
    saved = FeedbackRevision(
        id='feedback', workspace_id=principal.workspace_id, actor_id=principal.user_id,
        root_id='root', revision=1, previous_id=None, observation_id='observation',
        baseline_capability_id=scope['candidate_capability_id'], rule_id=scope['rule_id'],
        status='submitted', version=2,
        data={'record_hash': 'b' * 64, 'label': 'should_not_alert', 'basis': {
            'observation_id': 'observation', 'baseline_capability_id': 'candidate',
            'rule_id': scope['rule_id'], 'field': scope['field'],
            'instance_id': scope['instance_id'], 'release_id': scope['release_id'],
            'entity_id': scope['entity_id'], 'source_id': scope['source_id'],
            'actual_value': 19, 'actual_hit': True,
        }},
    )
    audit = SimpleNamespace(root_id=audit_root)
    command = SimpleNamespace(root_id=command_root)
    contents = {
        FeedbackRevision: [saved] if records else [],
        Audit: [audit] if audits else [],
        ThresholdCommand: [command] if commands else [],
    }
    reads = []

    class ReadOnlyInventory:
        def scalars(self, statement):
            entity = statement.column_descriptions[0]['entity']
            reads.append(entity)
            assert entity in contents, 'discovery must only read its three original inventories'
            return iter(contents[entity])

    # Only isolate the already-established pin/whole-chain validators. The
    # discovery root comparison and early-return behaviour remain real code.
    monkeypatch.setattr(feedback, '_checked_audit', lambda p, item: {'root_id': item.root_id})
    monkeypatch.setattr(feedback, '_checked_command', lambda db, p, item: {'root_id': item.root_id})
    chains = []

    def complete_chain(db, p, root_id, lock=False):
        chains.append((root_id, lock))
        assert p is principal and root_id == 'root' and lock is False
        return [saved]

    monkeypatch.setattr(history, 'inventory_chain', complete_chain)

    def no_current_basis(*args, **kwargs):
        pytest.fail('stop monitoring must not require current machine/source eligibility')

    monkeypatch.setattr(history, 'require_current_basis', no_current_basis)
    return ReadOnlyInventory(), principal, selection, grant, saved, reads, chains


@pytest.mark.parametrize('records,audits,commands', [
    (False, True, True), (False, True, False), (False, False, True),
    (True, False, False), (True, False, True), (True, True, False),
], ids=['lost-rows', 'audit-only', 'command-only', 'rows-only', 'lost-audit', 'lost-command'])
def test_partial_feedback_inventory_cannot_become_unlabelled(monkeypatch, records, audits, commands):
    db, p, selection, grant, _, reads, chains = discovery_case(
        monkeypatch, records=records, audits=audits, commands=commands,
    )
    with pytest.raises(HTTPException) as error:
        monitor._feedback(db, p, selection, grant)
    assert error.value.status_code == 409
    assert reads == [FeedbackRevision, Audit, ThresholdCommand]
    assert chains == []


@pytest.mark.parametrize('changed', ['audit', 'command'])
def test_independent_feedback_root_mismatch_is_not_a_label(monkeypatch, changed):
    db, p, selection, grant, _, _, chains = discovery_case(
        monkeypatch, audit_root='other-root' if changed == 'audit' else 'root',
        command_root='other-root' if changed == 'command' else 'root',
    )
    with pytest.raises(HTTPException) as error:
        monitor._feedback(db, p, selection, grant)
    assert error.value.status_code == 409
    assert chains == []


def test_genuinely_absent_feedback_remains_unlabelled_after_all_three_reads(monkeypatch):
    db, p, selection, grant, _, reads, chains = discovery_case(
        monkeypatch, records=False, audits=False, commands=False,
    )
    assert monitor._feedback(db, p, selection, grant) is None
    assert reads == [FeedbackRevision, Audit, ThresholdCommand]
    assert chains == []


def test_existing_submitted_label_still_requires_complete_history_without_current_basis(monkeypatch):
    db, p, selection, grant, saved, _, chains = discovery_case(monkeypatch)
    original = deepcopy(saved.data)
    assert not hasattr(saved, 'record_hash'), 'FeedbackRevision keeps its hash in typed data only'
    assert monitor._feedback(db, p, selection, grant) == {
        'id': saved.id, 'version': saved.version, 'record_hash': saved.data['record_hash'],
        'observation_id': selection.observation_id, 'label': 'should_not_alert',
        'selection': {'id': selection.id, 'version': selection.version, 'record_hash': selection.record_hash},
    }
    assert chains == [('root', False)]
    assert saved.data == original


def test_found_root_cannot_downgrade_a_broken_complete_chain_to_unlabelled(monkeypatch):
    db, p, selection, grant, _, _, _ = discovery_case(monkeypatch)

    def lost_tail(*args, **kwargs):
        raise HTTPException(409, 'original submitted revision is missing')

    monkeypatch.setattr(history, 'inventory_chain', lost_tail)
    with pytest.raises(HTTPException) as error:
        monitor._feedback(db, p, selection, grant)
    assert error.value.status_code == 409


def test_enqueue_submitted_feedback_pins_actual_model_data_hash(monkeypatch):
    # The real ORM class deliberately has no independent record_hash property.
    # Only the read and queue boundaries are isolated; enqueue_feedback is real.
    _, p, _, _, saved, _, _ = discovery_case(monkeypatch)
    original = deepcopy(saved.data)
    selected = monitor.Selection(
        id='selection', workspace_id=p.workspace_id, policy_id='policy',
        observation_id=saved.observation_id, choice='candidate',
    )
    grant = monitor.Grant(id='policy', workspace_id=p.workspace_id)
    reads, queued = [], []

    class SelectionInventory:
        def scalars(self, statement):
            entity = statement.column_descriptions[0]['entity']
            reads.append(entity)
            assert entity is monitor.Selection
            return iter([selected])

    db = SelectionInventory()

    def read_grant(actual_db, model, identity, principal):
        assert actual_db is db and model is monitor.Grant
        assert identity == selected.policy_id and principal is p
        return grant

    def queue(actual_db, principal, actual_grant, trigger):
        assert actual_db is db and principal is p and actual_grant is grant
        queued.append(deepcopy(trigger))

    monkeypatch.setattr(monitor, 'scoped', read_grant)
    monkeypatch.setattr(monitor, 'queue_monitor', queue)
    assert not hasattr(saved, 'record_hash')
    monitor.enqueue_feedback(db, p, saved)
    assert reads == [monitor.Selection]
    assert queued == [{
        'kind': 'feedback', 'feedback_id': saved.id, 'version': saved.version,
        'record_hash': saved.data['record_hash'],
    }]
    assert saved.data == original
