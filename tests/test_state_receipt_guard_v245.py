"""Synthetic storage; real reception rebuild and state guard, no IO claimed."""
import copy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import state_receipt_guard_v245 as subject
from generic_agents.contracts import digest
from generic_agents.models import Audit
from tests.test_instance_data_receipts_pure import scene, acknowledge, receive, scoped


def test_extraction_and_even_a_prepared_receipt_cannot_replace_independent_ack(scene):
    before = copy.deepcopy((scene.observation.data, scene.event.data, scene.db.memories))
    value = subject.check(scene.db, scene.p, scene.observation)
    assert value == {'status': 'waiting_data', 'delivery_status': 'pending_delivery'}
    assert (scene.observation.data, scene.event.data, scene.db.memories) == before
    receipt = receive(scene)
    # receive_observation prepares one immutable receipt, not an ACK. An
    # incomplete receiver transaction must not be treated as adopted state.
    with pytest.raises(HTTPException) as error:
        subject.check(scene.db, scene.p, scene.observation)
    assert error.value.status_code == 409
    acknowledge(scene, receipt)
    assert subject.check(scene.db, scene.p, scene.observation) == {
        'status': 'received', 'receipt_hash': receipt['receipt_hash']}


@pytest.mark.parametrize('damage', ['status_only', 'missing_memory', 'memory_payload',
                                  'withdraw_event', 'missing_event', 'duplicate_memory'])
def test_a_flag_or_broken_independent_link_never_admits_value(scene, damage):
    if damage == 'status_only':
        scene.event.status = 'received'
    else:
        acknowledge(scene, receive(scene))
        if damage == 'missing_memory': scene.db.memories = []
        elif damage == 'memory_payload': scene.db.memories[0].data['extracted'] = {'attributes': {'temperature': 999}}
        elif damage == 'withdraw_event': scene.event.status = 'pending_delivery'
        elif damage == 'missing_event': scene.db.event = None
        elif damage == 'duplicate_memory': scene.db.memories.append(copy.deepcopy(scene.db.memories[0]))
    with pytest.raises(HTTPException) as error:
        subject.check(scene.db, scene.p, scene.observation)
    assert error.value.status_code == 409


def test_refused_delivery_is_excluded_without_mutating_or_retrying_it(scene):
    scene.event.status = 'blocked_delivery'
    scene.event.data['delivery_error'] = 'synthetic independent receiver refusal'
    before = copy.deepcopy((scene.observation.data, scene.event.data))
    assert subject.check(scene.db, scene.p, scene.observation) == {
        'status': 'waiting_data', 'delivery_status': 'blocked_delivery'}
    assert (scene.observation.data, scene.event.data) == before


def test_pending_event_tamper_is_integrity_failure_not_normal_waiting(scene):
    scene.event.data['extracted'] = {'attributes': {'temperature': 999}}
    with pytest.raises(HTTPException) as error:
        subject.check(scene.db, scene.p, scene.observation)
    assert error.value.status_code == 409


def legacy(scene, *, retain_original_generation_hash=False):
    original_hash = scene.release.configuration_hash
    del scene.release.data['snapshot']['data_requirements']
    del scene.release.data['data_requirements_schema']
    scene.release.configuration_hash = digest(scene.release.data['snapshot'])
    scene.package.data = {'specification': {'configuration_hash': scene.release.configuration_hash}}
    scene.package.content_hash = scene.package.validated_hash = digest(scene.package.data)
    scene.release.actor_id = scene.p.user_id
    scene.db.put(Audit, SimpleNamespace(id='generation', workspace_id=scene.p.workspace_id,
        action='instance.generated', object_id=scene.release.id, actor_id=scene.p.user_id,
        data={'number': scene.release.release_number, 'hash': original_hash
            if retain_original_generation_hash else scene.release.configuration_hash}))


def test_genuinely_legacy_generated_contract_keeps_its_original_behavior(scene, monkeypatch):
    # A synthetic historical publication, not a migrated modern one: its
    # independent original publication already attests the plan-free hash.
    legacy(scene)
    monkeypatch.setattr(subject, 'scoped', scoped)
    before = copy.deepcopy((scene.observation.data, scene.event.data, scene.db.memories))
    assert subject.check(scene.db, scene.p, scene.observation) is None
    assert (scene.observation.data, scene.event.data, scene.db.memories) == before


def test_deleting_all_modern_markers_and_rehashing_cannot_impersonate_legacy(scene, monkeypatch):
    legacy(scene, retain_original_generation_hash=True)
    monkeypatch.setattr(subject, 'scoped', scoped)
    with pytest.raises(HTTPException) as error:
        subject.check(scene.db, scene.p, scene.observation)
    assert error.value.status_code == 409
