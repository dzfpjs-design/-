"""Selected saved-answer PG/API checks; NOT original-report/paid acceptance.

Reuse the real M1/M2 API fixture: its immutable-source admission is explicitly
synthetic and only HTTP transport is substituted. Real Auth, fork, published
Capability, Start, worker, reservation, Call, capture, Commands and Audits create
the positive record. No successful new saved-record check is mocked here.

Negative cases deliberately corrupt existing DB witnesses. A few re-anchor the
LAST existing STATE hash to reach the inner receipt/raw/identity checks instead
of obtaining twenty identical failures at the outer row hash. This is adversarial
storage damage, never a fixture claiming a newly accepted result or source.
Root alone runs this file in a disposable PostgreSQL database.
"""
import base64
import copy
import json
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import catalogue_current_saved_record_v260 as saved
from generic_agents import catalogue_current_allocation_v260 as allocation
from generic_agents import catalogue_current_context_store_v260 as current
from generic_agents import cloud_budget
from generic_agents import report_business_catalogue_allocation_plan_v213 as planning
from generic_agents import report_business_catalogue_allocation_preparation_v213 as preparation
from generic_agents import report_business_catalogue_allocation_native_v213 as native
from generic_agents import report_business_catalogue_allocation_wire_v213 as wire
from generic_agents import report_saved_map_design_consumer_v176 as consumer
from generic_agents import report_saved_map_design_runtime_v176 as runtime
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_journal_v2 as journal
from generic_agents import report_business_catalogue_store_v211 as cat
from generic_agents.contracts import digest
from tests.test_catalogue_current_allocation_v260 import (
    allocation_scene, completed, quote, start, owned_inventory, Consumer, Command,
    BASE, ok, raw, h, advance, other_client)
from tests.test_catalogue_current_context_v260 import reuse_scene


TOP = {'schema', 'workspace_id', 'actor_id', 'consumer_id', 'version', 'status',
    'state_hash', 'start_receipt', 'request_sha256', 'call', 'answer',
    'verification', 'full_read_path'}
VERIFICATION = {'saved_record_checked': True, 'source_revalidated': False,
    'files_revalidated': False, 'current_configuration_revalidated': False,
    'semantic_quality_verified': False, 'can_apply': False, 'can_publish': False}


def path(flow):
    return BASE + '/' + flow.created['id'] + '/saved-record'


@pytest.fixture
def saved_scene(allocation_scene):
    s = allocation_scene
    s.saved_flow = completed(s)
    assert len(s.posts) == 1
    return s


def get_without_effects(s, *, client=None, target=None, status=200):
    before = owned_inventory(s)
    posts = copy.deepcopy(s.posts)
    response = (client or s.client).get(target or path(s.saved_flow))
    assert response.status_code == status, response.text[:500]
    assert owned_inventory(s) == before
    assert s.posts == posts
    return response


def state_for(db, row):
    states = list(db.scalars(select(m.Audit).where(
        m.Audit.workspace_id == row.workspace_id,
        m.Audit.action == consumer.STATE_AUDIT, m.Audit.object_id == row.id)))
    assert len(states) == row.version
    return next(event for event in states if event.data['version'] == row.version)


def reanchor_last_state(db, row):
    # Fault injection only: keep the independent existing version/ref chain,
    # so the intended deeper damaged field must actually be checked.
    state = state_for(db, row)
    state.data = {**state.data, 'state_hash': consumer.h(consumer._row_pin(row))}


def poison_full_paths(monkeypatch):
    def forbidden(name):
        def reject(*_args, **_kwargs):
            raise AssertionError('saved-record crossed forbidden full/write boundary: ' + name)
        return reject
    boundaries = {
        consumer: ('_inventory', '_inventory_value', '_native_preparation', 'view',
            '_capture_summary', '_check_call', '_check_allocation_answer'),
        allocation: ('native_preparation', 'check_preview', 'verify_history'),
        planning: ('check_embedded_plan',),
        preparation: ('stage_spec',),
        native: ('check_allocation_evidence',),
        wire: ('check_projection',),
        current: ('_inventory',), cat: ('_load_origin',),
        current.source: ('load_source_context',),
        runtime: ('read_response', '_context', '_journal_root'),
        bridge: ('root', 'response_context', 'write_missing_journal'),
        capture: ('load', 'read_body'), journal: ('read_descriptor', 'read_response'),
        cloud_budget: ('reserve', 'finish', 'finish_tx', 'settle_paid_response'),
    }
    for module, names in boundaries.items():
        for name in names:
            monkeypatch.setattr(module, name, forbidden(module.__name__ + '.' + name))


def test_saved_answer_is_exact_readonly_record_when_current_source_is_unavailable(saved_scene, monkeypatch):
    s = saved_scene; flow = s.saved_flow
    advance(s, '原机答保存后真实配置前进；只读记录不重新授予当前权限')
    s.source_unavailable = True
    # The original full gate must still reject the missing source. This call is
    # before throw-spies; a fake new "success" path cannot make it green.
    get_without_effects(s, target=BASE + '/' + flow.created['id'], status=409)
    reads = []
    real_saved_record = saved.saved_record
    def observed(db, principal, consumer_id):
        reads.append((db.scalar(text('SHOW transaction_read_only')),
            db.scalar(text('SHOW transaction_isolation'))))
        return real_saved_record(db, principal, consumer_id)
    monkeypatch.setattr(saved, 'saved_record', observed)
    poison_full_paths(monkeypatch)
    result = get_without_effects(s).json()
    assert reads and all(value == ('on', 'repeatable read') for value in reads)
    assert set(result) == TOP
    assert result['schema'] == 'catalogue-current-allocation-saved-record-v260'
    assert result['workspace_id'] == s.p.workspace_id and result['actor_id'] == s.p.user_id
    assert result['consumer_id'] == flow.created['id'] and result['version'] == flow.view['version']
    assert result['status'] == flow.view['status'] and result['start_receipt'] == flow.created
    assert result['request_sha256'] == h(flow.original)
    assert result['full_read_path'] == BASE + '/' + flow.created['id']
    assert result['verification'] == VERIFICATION
    assert all(type(value) is bool for value in result['verification'].values())
    with Session(s.bound) as db:
        row = db.get(Consumer, flow.created['id']); call = db.get(m.CloudCall, row.reserved_call_id)
        assert result['state_hash'] == state_for(db, row).data['state_hash']
        assert result['answer'] == {'raw_arguments_sha256': row.data['response']['arguments_sha256'],
            'projection_hash': flow.view['allocation']['projection_hash'],
            'projection': flow.view['allocation']}
        assert result['call'] == {'id': call.id, 'status': 'succeeded', 'charged_tokens': 21,
            'usage': {'prompt_tokens': 9, 'completion_tokens': 12, 'total_tokens': 21},
            'usage_unavailable': False}
        assert row.data['projection'] == flow.view['allocation']


def test_unrelated_broken_owned_attempt_does_not_become_a_full_actor_inventory_gate(saved_scene, monkeypatch):
    s = saved_scene
    body, preview = quote(s); _, _, second = start(s, body, preview)
    with Session(s.bound) as db:
        row = db.get(Consumer, second['id'])
        row.data = {**row.data, 'preview': {'deliberately_broken_other_attempt': True}}
        db.commit()
    poison_full_paths(monkeypatch)
    assert get_without_effects(s).json()['answer']['projection'] == s.saved_flow.view['allocation']
    damaged = get_without_effects(s, target=BASE + '/' + second['id'] + '/saved-record', status=409)
    assert damaged.json().get('found') is not False
    assert len(s.posts) == 1


def test_owner_viewer_reads_but_foreign_and_inactive_accounts_cannot_read_saved_answer(saved_scene):
    s = saved_scene
    with Session(s.bound) as db:
        member = db.scalar(select(m.Membership).where(m.Membership.workspace_id == s.p.workspace_id,
            m.Membership.user_id == s.p.user_id))
        member.role = 'viewer'; db.commit()
    with other_client(s, same_actor=True) as viewer:
        assert ok(viewer.get('/api/me'))['role'] == 'viewer'
        assert get_without_effects(s, client=viewer).json()['answer']['projection'] == s.saved_flow.view['allocation']
    with other_client(s) as foreign:
        before = owned_inventory(s)  # This baseline includes legitimate login_limit Job creation.
        response = foreign.get(path(s.saved_flow))
        assert response.status_code in {403, 404}
        assert 'projection_hash' not in response.text and s.saved_flow.original.decode() not in response.text
        assert owned_inventory(s) == before and len(s.posts) == 1
    with Session(s.bound) as db:
        db.get(m.User, s.p.user_id).active = False; db.commit()
    before = owned_inventory(s)
    denied = s.client.get(path(s.saved_flow))
    assert denied.status_code in {401, 403}
    assert 'projection_hash' not in denied.text
    assert owned_inventory(s) == before and len(s.posts) == 1


def test_unknown_and_queued_records_never_assert_found_false_or_create_another_call(allocation_scene):
    s = allocation_scene
    body, preview = quote(s); _, _, created = start(s, body, preview)
    s.saved_flow = type('Pending', (), {'created': created})()
    before = owned_inventory(s)
    for identity in (created['id'], str(uuid4())):
        response = s.client.get(BASE + '/' + identity + '/saved-record')
        assert response.status_code in {404, 409}
        assert response.json().get('found') is not False
    with Session(s.bound) as db:
        row = db.get(Consumer, created['id'])
        assert row.status == 'queued' and row.reserved_call_id is None
    assert owned_inventory(s) == before and not s.posts


def test_saved_record_rejects_a_noncurrent_family_even_when_the_outer_state_is_rehashed(saved_scene):
    s = saved_scene
    with Session(s.bound) as db:
        row = db.get(Consumer, s.saved_flow.created['id']); data = copy.deepcopy(row.data)
        data['preview']['preparation'] = data['preview']['preparation']['native_preparation']
        row.data = data; reanchor_last_state(db, row); db.commit()
    get_without_effects(s, status=409)


@pytest.mark.parametrize('damage', ['changed-row', 'missing-state'])
def test_row_and_independent_state_are_both_required(saved_scene, damage):
    s = saved_scene
    with Session(s.bound) as db:
        row = db.get(Consumer, s.saved_flow.created['id'])
        if damage == 'changed-row': row.data = {**row.data, 'error': 'unanchored damage'}
        else: db.delete(state_for(db, row))
        db.commit()
    get_without_effects(s, status=409)


@pytest.mark.parametrize('damage', ['missing', 'foreign-actor', 'extra-foreign-actor'])
def test_command_audit_reverse_inventory_cannot_hide_changed_or_extra_actors(saved_scene, damage):
    s = saved_scene
    with Session(s.bound) as db:
        row = db.get(Consumer, s.saved_flow.created['id'])
        command = db.scalar(select(Command).where(Command.consumer_id == row.id))
        anchor = db.scalar(select(m.Audit).where(m.Audit.action == consumer.COMMAND_AUDIT,
            m.Audit.object_id == command.id, m.Audit.workspace_id == s.p.workspace_id))
        foreign_id = db.get(Consumer, s.consumer_id).actor_id
        assert foreign_id != s.p.user_id
        if damage == 'missing': db.delete(anchor)
        elif damage == 'foreign-actor': anchor.actor_id = foreign_id
        else:
            db.add(m.Audit(workspace_id=s.p.workspace_id, actor_id=foreign_id,
                action=consumer.COMMAND_AUDIT, object_id=command.id, data=copy.deepcopy(anchor.data)))
        db.commit()
    get_without_effects(s, status=409)


@pytest.mark.parametrize('damage', ['job-owner', 'missing-call', 'extra-call'])
def test_true_original_job_and_one_actual_call_are_required(saved_scene, damage):
    s = saved_scene
    with Session(s.bound) as db:
        row = db.get(Consumer, s.saved_flow.created['id'])
        call = db.get(m.CloudCall, row.reserved_call_id)
        if damage == 'job-owner':
            job = db.get(m.Job, row.job_id); job.data = {**job.data, 'actor_id': str(uuid4())}
        elif damage == 'missing-call':
            # Keep the referenced row present (real FK); remove its independent
            # dispatch identity so it cannot count as the original actual Call.
            data = copy.deepcopy(call.data); data.pop('gateway_dispatch'); call.data = data
        else:
            db.add(m.CloudCall(workspace_id=call.workspace_id, capability_id=call.capability_id,
                status=call.status, reserved_tokens=call.reserved_tokens,
                charged_tokens=call.charged_tokens, data=copy.deepcopy(call.data)))
        db.commit()
    get_without_effects(s, status=409)


def test_each_of_the_seven_dispatch_identity_fields_is_derived_not_trusted_from_call_pin(saved_scene):
    s = saved_scene; cid = s.saved_flow.created['id']
    with Session(s.bound) as db:
        row = db.get(Consumer, cid); call = db.get(m.CloudCall, row.reserved_call_id)
        originals = (copy.deepcopy(row.data), copy.deepcopy(call.data), copy.deepcopy(state_for(db, row).data))
        keys = set(call.data['gateway_dispatch']['identity'])
    assert keys == {'suggestion_id', 'job_id', 'task_id', 'request_hash',
        'workspace_id', 'actor_id', 'capability_id'}
    for key in sorted(keys):
        try:
            with Session(s.bound) as db:
                row = db.get(Consumer, cid); call = db.get(m.CloudCall, row.reserved_call_id)
                changed = copy.deepcopy(originals[1]); identity = changed['gateway_dispatch']['identity']
                identity[key] = ('a' * 64 if key == 'request_hash' else
                    allocation.TASK_PREFIX + 'b' * 64 if key == 'task_id' else str(uuid4()))
                call.data = changed
                row.data = {**row.data, 'call_pin': consumer._call_pin(call)}
                reanchor_last_state(db, row); db.commit()
            get_without_effects(s, status=409)
        finally:
            with Session(s.bound) as db:
                row = db.get(Consumer, cid); call = db.get(m.CloudCall, row.reserved_call_id)
                row.data, call.data = copy.deepcopy(originals[0]), copy.deepcopy(originals[1])
                state_for(db, row).data = copy.deepcopy(originals[2]); db.commit()


@pytest.mark.parametrize('damage', ['base64', 'duplicate-json-key', 'raw-sha', 'projection-sha'])
def test_raw_bytes_and_saved_projection_are_checked_beyond_the_outer_state_hash(saved_scene, damage):
    s = saved_scene
    with Session(s.bound) as db:
        row = db.get(Consumer, s.saved_flow.created['id']); data = copy.deepcopy(row.data)
        call = db.get(m.CloudCall, row.reserved_call_id)
        original = base64.b64decode(data['allocation_raw_arguments_b64'], validate=True)
        if damage == 'base64': data['allocation_raw_arguments_b64'] = 'not-valid-base64!'
        elif damage == 'duplicate-json-key':
            # Duplicate with the SAME value: ordinary json.loads would erase
            # this damage. Synchronize the stored SHA/length witnesses so the
            # closed JSON reader, not merely a stale checksum, must reject it.
            duplicate = b'{"schema":' + json.dumps(wire.WIRE).encode() + b',' + original[1:]
            assert json.loads(duplicate) == json.loads(original)
            sha = h(duplicate); data['allocation_raw_arguments_b64'] = base64.b64encode(duplicate).decode()
            data['response']['arguments_sha256'] = sha
            data['response']['receipt'].update(arguments_sha256=sha, arguments_bytes=len(duplicate))
            paid = copy.deepcopy(call.data)
            paid['receipt'].update(arguments_sha256=sha, arguments_bytes=len(duplicate))
            call.data = paid
            projection = data['projection']; projection['raw_arguments_sha256'] = sha
            projection['projection_hash'] = digest({key: value for key, value in projection.items()
                if key != 'projection_hash'})
        elif damage == 'raw-sha':
            parsed = json.loads(original)
            first = next(iter(parsed['decisions']))
            parsed['decisions'][first]['reason'] += '；替换原参数而不改其签存摘要'
            data['allocation_raw_arguments_b64'] = base64.b64encode(raw(parsed)).decode()
        else:
            data['projection']['decisions'][0]['raw_decision']['reason'] += '；投影损坏'
        row.data = data; reanchor_last_state(db, row); db.commit()
    get_without_effects(s, status=409)


def test_extra_foreign_command_pointing_to_selected_consumer_is_not_hidden_by_owner_filter(saved_scene):
    s = saved_scene
    with Session(s.bound) as db:
        row = db.get(Consumer, s.saved_flow.created['id'])
        original = db.scalar(select(Command).where(Command.consumer_id == row.id))
        foreign_id = db.get(Consumer, s.consumer_id).actor_id
        extra = Command(id=str(uuid4()), workspace_id=s.p.workspace_id, actor_id=foreign_id,
            request_id=str(uuid4()), consumer_id=row.id, operation=original.operation,
            method=original.method, path=original.path, exact_body=original.exact_body,
            body_sha256=original.body_sha256, data=copy.deepcopy(original.data), command_hash='a' * 64)
        extra.command_hash = consumer.h(consumer._command_pin(extra)); db.add(extra)
        db.add(m.Audit(workspace_id=s.p.workspace_id, actor_id=foreign_id,
            action=consumer.COMMAND_AUDIT, object_id=extra.id, data=consumer._command_anchor(extra)))
        db.commit()
    get_without_effects(s, status=409)


def test_missing_selected_root_with_real_audit_and_job_traces_is_not_a_negative_execution_proof(saved_scene):
    s = saved_scene
    with Session(s.bound) as db:
        # Command FK must be removed before its root, while original independent
        # Audit/Job/Call witnesses remain. No repair/new command is permitted.
        db.execute(delete(Command).where(Command.consumer_id == s.saved_flow.created['id']))
        db.execute(delete(Consumer).where(Consumer.id == s.saved_flow.created['id']))
        db.commit()
    result = get_without_effects(s, status=409)
    assert result.json().get('found') is not False
