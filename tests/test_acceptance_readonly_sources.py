"""Actual acceptance readers with explicit DB/source IO boundaries.

These tests exercise lock propagation and the real entity-definition and graph
receipt validators. They do not claim a PostgreSQL transaction or live Task.
"""
import copy
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import advice_workflows, entity_release_compat as compat
from generic_agents import layered_approval_store_v175 as approval
from generic_agents import native_goals, native_goal_tools, perception_signals as signals
from generic_agents import workspace_api
from generic_agents.contracts import digest
from generic_agents.models import Capability, DomainInstance, Entity, Event, InstanceRelease, PerceptionSignal
from tests.test_entity_release_compat_pure import World
from tests.test_workflow_graph_goal_business_flow_sources import case as graph_case


@pytest.fixture(autouse=True)
def forbid_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('pure acceptance tests prohibit DB engines, network and processes')
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    monkeypatch.setattr(Engine, 'connect', forbidden)
    monkeypatch.setattr(Engine, 'begin', forbidden)
    monkeypatch.setattr(Session, 'commit', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def acceptance_scene(monkeypatch, *, readonly):
    """Synthetic originals; only unrelated media/route IO is stubbed."""
    from generic_agents import instance_compiler, workflow_images, workflow_scenes
    from generic_agents import workflow_audio, workflow_geo_pairs, signal_routing, signal_dual_handoffs
    from generic_agents import goal_evaluation_provider_v288 as provider
    from generic_agents import native_pixel_receipts_v250, native_pixel_provider_v250, workflow_sound
    from generic_agents import audio_stream_delivery
    world = World(monkeypatch)
    world.db.info = {}
    task = world.task()
    task.status = 'workflow_completed'; task.version = 8
    task.data['workflow_runtime'] = {'receipts': []}
    proof = compat.require_entity(world.db, world.p, world.instance, world.r2, world.entity,
        lock_sources=False)
    event = NS(id='original-event', status='received', data={'original': 'saved event'})
    observation = NS(id='original-observation')
    basis = {'instance_id': world.instance.id, 'entity_id': world.entity.id,
        'release_id': world.r2.id, 'package_id': world.r2.package_id,
        'event_hash': digest(event.data), 'observation_id': observation.id,
        'observation_hash': digest({'original': 'saved observation'}),
        'reported_at': '2026-10-16T00:00:00+00:00',
        'source_entity_package_id': world.entity.schema_capability_id,
        'entity_definition_hash': digest(proof)}
    signal = NS(id='original-signal', event_id=event.id, instance_id=world.instance.id,
        entity_id=world.entity.id, data={'basis': basis, 'basis_hash': digest(basis)})
    task.data['perception_signal'] = {'signal_id': signal.id, 'basis_hash': digest(basis)}
    rows = {Event: event, PerceptionSignal: signal, Entity: world.entity,
        DomainInstance: world.instance, InstanceRelease: world.r2}
    probes = []

    def non_v288_provider_boundary(db, p, actual_task, **kwargs):
        # World has synthetic non-UUID release IDs and no V288 family. This
        # declared provider IO boundary is not proof of evaluation maturity;
        # keep the real final wrapper and every original-source proof below.
        assert db is world.db and p is world.p and actual_task is task
        assert provider.KEY not in task.data and provider.PIN not in task.data
        assert provider.PIN not in (task.data.get('native_goal_context') or {})
        probes.append(('non_v288_provider_io_boundary',))
        return None

    monkeypatch.setattr(provider, 'binding', non_v288_provider_boundary)

    def unrelated_audio_signal_boundary(db, p, actual_signal):
        # is_signal inventories native audio rows even without a marker. This
        # fixture declares no such rows; real generic signal.checked stays live.
        assert db is world.db and p is world.p and actual_signal is signal
        assert signal.data.get('schema') != 'gateway-audio-signal-receipt-v255'
        assert not signal.data['basis'].get('original_model_result')
        assert event.data.get('schema') not in {
            audio_stream_delivery.DAILY_SCHEMA, audio_stream_delivery.CANDIDATE_SCHEMA}
        probes.append(('unrelated_audio_signal_io_boundary',))
        return False

    monkeypatch.setattr(audio_stream_delivery, 'is_signal', unrelated_audio_signal_boundary)

    def scoped(db, model, identity, p, lock=False):
        assert db is world.db and p is world.p
        probes.append(('row', model, identity, lock))
        assert not readonly or lock is False, 'read-only snapshot attempted a row lock'
        row = rows[model]
        assert row.id == identity
        return row

    original_scoped = world.db.scoped
    def source_scoped(db, model, identity, p, lock=False):
        assert not readonly or lock is False, 'read-only proof attempted source FOR UPDATE'
        probes.append(('source', model, identity, lock))
        return original_scoped(db, model, identity, p, lock)

    monkeypatch.setattr(compat, 'scoped', source_scoped)
    monkeypatch.setattr(signals, 'scoped', scoped)
    monkeypatch.setattr(workspace_api, 'scoped', scoped)
    monkeypatch.setattr(signals, 'event_report', lambda *a: (observation, {'review': None}))
    monkeypatch.setattr(signals, '_simulation_projection', lambda *a: None)
    monkeypatch.setattr(signals, 'machine_basis', lambda *a, **kw: {
        'machine_hash': basis['observation_hash'], 'observed_at': basis['reported_at']})
    def unrelated_media_task_boundary(db, p, actual_task, **kwargs):
        # No media inputs, producer/human receipts or graph transports exist in
        # this declared fixture. Their native DB inventory is outside this
        # signal/definition test, not simulated through permissive fake SQL.
        assert db is world.db and p is world.p and actual_task is task
        for key in ('pixel_pair_snapshot', 'material_snapshots', 'image_snapshots',
                'scene_snapshots', 'comparison_scene_snapshots', 'audio_snapshots',
                'sound_snapshots', 'perception_event'):
            assert key not in task.data
        assert task.data['workflow_runtime']['receipts'] == []
        assert not task.data.get('graph_jobs') and 'graph_runtime' not in task.data
        probes.append(('unrelated_media_io_boundary',))

    def unrelated_material_boundary(db, p, materials, **kwargs):
        assert materials == [] and kwargs == {'allow_archived': True}
        unrelated_media_task_boundary(db, p, task)

    def unrelated_handoff_boundary(db, p, actual_task, **kwargs):
        assert db is world.db and p is world.p and actual_task is task
        for key in ('auto_routing', 'dual_handoff', 'dual_response_ack'):
            assert key not in task.data
        probes.append(('unrelated_handoff_io_boundary',))

    monkeypatch.setattr(instance_compiler, 'verify_material_snapshots', unrelated_material_boundary)
    for module, name in [(native_pixel_receipts_v250, 'require_task_received'),
            (workflow_sound, 'require_task_received'), (native_pixel_provider_v250, 'verify_task_humans'),
            (workflow_images, 'verify'), (workflow_images, 'verify_masks'),
            (workflow_scenes, 'verify_masks'), (workflow_audio, 'verify_receipts'),
            (workflow_geo_pairs, 'verify_receipts')]:
        monkeypatch.setattr(module, name, unrelated_media_task_boundary)
    monkeypatch.setattr(signal_routing, 'verify_task', unrelated_handoff_boundary)
    monkeypatch.setattr(signal_dual_handoffs, 'verify_task', unrelated_handoff_boundary)
    monkeypatch.setattr(approval, 'guard', lambda *a, **kw: probes.append(('approval', kw['lock_sources'])))
    monkeypatch.setattr(workspace_api, 'execution', lambda *a, **kw: (
        probes.append(('execution', kw['lock_sources'])) or (task, world.r2, {})))
    monkeypatch.setattr(approval, 'checked', lambda row: {'supplements': []})
    monkeypatch.setattr(approval, 'row_hash', lambda *a: 'f' * 64)
    world.db.reads.clear(); probes.clear()
    return NS(**vars(world), task=task, event=event, signal=signal, probes=probes)


@pytest.mark.parametrize('readonly', [True, False])
def test_actual_outcome_to_signal_definition_preserves_read_and_default_write_mode(monkeypatch, readonly):
    c = acceptance_scene(monkeypatch, readonly=readonly)
    before = copy.deepcopy(c.task.data)
    kwargs = {'lock_sources': False} if readonly else {}
    result = approval.outcome(c.db, c.p, c.task, NS(), **kwargs)
    expected = not readonly
    assert result['schema'] == 'layered-approval-acceptance-basis-v175'
    assert ('approval', expected) in c.probes and ('execution', expected) in c.probes
    source_locks = [entry[3] for entry in c.probes if entry[0] == 'source' and entry[1] is Capability]
    assert source_locks and all(lock is expected for lock in source_locks)
    assert c.task.data == before


@pytest.mark.parametrize('fault', ['basis_hash', 'event_hash', 'source_hash', 'source_publication', 'definition'])
def test_read_mode_retains_actual_original_and_entity_definition_rejections(monkeypatch, fault):
    c = acceptance_scene(monkeypatch, readonly=True)
    if fault == 'basis_hash': c.signal.data['basis_hash'] = '0' * 64
    elif fault == 'event_hash': c.event.data['original'] = 'changed'
    elif fault == 'source_hash':
        c.db.rows[Capability][c.r1.package_id].data['changed'] = True
    elif fault == 'source_publication':
        c.db.rows[Capability][c.r1.package_id].status = 'retired'
    else:
        c.signal.data['basis']['entity_definition_hash'] = '0' * 64
        c.signal.data['basis_hash'] = digest(c.signal.data['basis'])
        c.task.data['perception_signal']['basis_hash'] = c.signal.data['basis_hash']
    before = copy.deepcopy(c.task.data)
    with pytest.raises(HTTPException) as error:
        approval.outcome(c.db, c.p, c.task, NS(), lock_sources=False)
    assert error.value.status_code == 409
    assert c.task.data == before
    assert all(entry[3] is False for entry in c.probes if entry[0] in {'row', 'source'})


class StopAtBoundary(Exception):
    pass


@pytest.mark.parametrize('readonly', [True, False])
def test_advice_branch_forwards_mode_before_checking_saved_advice_fields(monkeypatch, readonly):
    from generic_agents import signal_advice
    value = {'advice_id': 'advice', 'signal_id': 'signal'}
    value['hash'] = digest(value)
    task = NS(data={'business_advice': value})
    probes = []
    monkeypatch.setattr(advice_workflows, 'scoped', lambda *a: NS())
    monkeypatch.setattr(signal_advice, 'checked', lambda row: row)
    def checked(*args, **kwargs):
        probes.append(kwargs)
        raise StopAtBoundary
    monkeypatch.setattr(signals, 'checked', checked)
    with pytest.raises(StopAtBoundary):
        advice_workflows.verify(None, None, task, **({'lock_sources': False} if readonly else {}))
    assert probes == [{'allow_rejected': False, 'lock_sources': not readonly}]


@pytest.mark.parametrize('readonly', [True, False])
@pytest.mark.parametrize('ability', [False, True])
def test_real_graph_receipts_forward_mode_without_skipping_zero_ability_source(monkeypatch, readonly, ability):
    c = graph_case(monkeypatch, with_ability=ability)
    probes = []
    monkeypatch.setattr(native_goals, 'verify_workflow_inputs',
        lambda *a, **kw: probes.append(('inputs', kw['lock_sources'])))
    monkeypatch.setattr(compat, 'verify_task_objects',
        lambda *a, **kw: probes.append(('objects', kw['lock_sources'])))
    before = copy.deepcopy(c.task.data)
    result = native_goal_tools.workflow_goal_receipts(c.db, c.p, c.task,
        **({'lock_sources': False} if readonly else {}))
    assert len(result) == int(ability)
    assert probes == [('inputs', not readonly), ('objects', not readonly)]
    assert ('historical_release', False) in c.probes
    assert c.task.data == before
    c.binding['flow']['source_flow']['nodes'][0]['name'] = 'changed original source'
    c.binding['binding_hash'] = digest({k: v for k, v in c.binding.items() if k != 'binding_hash'})
    with pytest.raises(HTTPException):
        native_goal_tools.workflow_goal_receipts(c.db, c.p, c.task, lock_sources=False)


@pytest.mark.parametrize('readonly', [True, False])
def test_actual_assessment_forwards_mode_to_workflow_receipts(monkeypatch, readonly):
    from generic_agents import native_step_execution, native_cases, data_gaps
    task = NS(id='task', status='workflow_completed', data={'goal_spec': {'criteria': []},
        'workflow_runtime': {'current_step_id': None, 'receipts': [{'original': 'saved'}]}})
    probes = []
    monkeypatch.setattr(native_goals, 'guard', lambda *a, **kw: probes.append(('guard', kw['lock_sources'])))
    monkeypatch.setattr(native_goals, 'query_goal', lambda *a, **kw: (
        probes.append(('query', kw['lock_sources'])) or {'status': 'sufficient'}))
    monkeypatch.setattr(native_goal_tools, 'workflow_goal_receipts', lambda *a, **kw: (
        probes.append(('receipts', kw['lock_sources'])) or []))
    monkeypatch.setattr(native_step_execution, 'goal_gate', lambda *a, **kw: {
        'receipts': [], 'runs': [], 'basis_hash': 'a' * 64, 'all_reviewed': True})
    monkeypatch.setattr(native_goals, 'evaluate', lambda *a, **kw: {'completed': True, 'ready_for_acceptance': True})
    monkeypatch.setattr(native_goals, 'basis', lambda *a: 'b' * 64)
    monkeypatch.setattr(data_gaps, 'receipts_ready', lambda *a: True)
    monkeypatch.setattr(native_cases, 'gate', lambda *a, **kw: {'all_confirmed': True})
    before = copy.deepcopy(task.data)
    native_goals.assessment(None, None, task, accepted=True,
        **({'lock_sources': False} if readonly else {}))
    assert probes == [('guard', not readonly), ('query', not readonly), ('receipts', not readonly)]
    assert task.data == before


@pytest.mark.parametrize('readonly', [True, False])
def test_native_goal_guard_forwards_mode_to_current_graph_route(monkeypatch, readonly):
    from generic_agents import workflow_graph_goal_guard_v133, signal_graph_routing_v134
    context = {'instance_id': 'instance'}
    task = NS(data={'graph_runtime': {}, 'native_goal_context': context,
        'native_goal_context_hash': digest(context)})
    probes = []
    monkeypatch.setattr(approval, 'guard', lambda *a, **kw: probes.append(('approval', kw['lock_sources'])))
    monkeypatch.setattr(workflow_graph_goal_guard_v133, 'verify_reservations', lambda *a: None)
    def route(*args, **kwargs):
        probes.append(('route', kwargs['lock']))
        raise StopAtBoundary
    monkeypatch.setattr(signal_graph_routing_v134, 'guard_current', route)
    with pytest.raises(StopAtBoundary):
        native_goals.guard(None, None, task, **({'lock_sources': False} if readonly else {}))
    assert probes == [('approval', not readonly), ('route', not readonly)]
