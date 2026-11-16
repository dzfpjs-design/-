"""M2 real disposable PG/API/ledger, with TWO EXPLICIT external boundaries.

1. reuse_scene supplies synthetic immutable-source admission, not the original
   report, its paid Maps/captures, provider acceptance or domain quality.
   Its nonexecuted FK-only Consumer is isolated under another real fixture User
   below so the new actor's complete Consumer inventory contains ONLY genuine
   M2 Commands/States. That skeleton is not source ownership authority. The
   fixed-owner admission stub remains the only synthetic source boundary; this
   file does NOT prove cross-actor source ownership or old paid acceptance.
2. Only httpx.Client.stream returns a synthetic provider envelope. Real current
   IAM, M1 create/save/Binding, published Capability, Preview/Start, Consumer and
   Command/State Audits, budget reservation, CloudCall, first-answer capture,
   journal, selected Catalogue save and dual links remain production paths.

No accepted Binding, Consumer, Call or child Command is inserted by this file.
The nonzero test budget is bounded to the fixture; no real network or payment
occurs. Root executes tests and separately verifies the real customer's source.
"""
import copy
import json
import traceback
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents import catalogue_current_allocation_v260 as allocation
from generic_agents import catalogue_current_context_store_v260 as current
from generic_agents import report_business_catalogue_allocation_native_v213 as native
from generic_agents import report_business_catalogue_allocation_wire_v213 as wire
from generic_agents import report_business_catalogue_store_v211 as cat
from generic_agents import report_saved_map_design_consumer_v176 as consumer
from generic_agents import report_saved_map_design_runtime_v176 as runtime
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_journal_v2 as journal
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.report_saved_map_design_models_v176 import (
    ReportSavedMapDesignV176 as Consumer, ReportSavedMapDesignCommandV176 as Command)
from generic_agents.security import password_hash
from generic_agents.worker import process_once
from tests.test_catalogue_current_context_v260 import (
    reuse_scene, fork, binding, catalogue, save_document, advance, ids, parent_state,
    other_client, ok, raw, h)
from tests.test_vertical_slice import publish


BASE = '/api/setup/report-saved-map-designs'
ROOT = Path(__file__).resolve().parents[1]
KEY = 'V260-SYNTHETIC-TRANSPORT-NONCREDENTIAL'


def rows(s, *classes):
    """Exact owned DB inventory for no-side-effect and rollback assertions."""
    answer = {}
    with Session(s.bound) as db:
        for model in classes:
            answer[model.__tablename__] = {row.id: {
                column.key: copy.deepcopy(getattr(row, column.key))
                for column in inspect(model).columns}
                for row in db.scalars(select(model).where(model.workspace_id == s.p.workspace_id))}
    return answer


def owned_inventory(s):
    return rows(s, Consumer, Command, cat.Draft, cat.Command, m.Job, m.CloudCall, m.Audit)


@pytest.fixture
def allocation_scene(reuse_scene, monkeypatch):
    s = reuse_scene
    # Root-approved fixture isolation ONLY. Do not weaken production inventory,
    # manufacture an accepted old Consumer, or modify the shared M1 fixture.
    # This cancelled/failed skeleton exists solely to satisfy catalogue FKs;
    # all original-source admission is explicitly synthetic and fixed to s.p.
    with Session(s.bound) as db:
        isolated = m.User(username='m2_fk_' + uuid4().hex[:16],
            password_hash=password_hash(uuid4().hex))
        db.add(isolated); db.flush()
        db.add(m.Membership(user_id=isolated.id, workspace_id=s.p.workspace_id, role='viewer'))
        skeleton = db.get(Consumer, s.consumer_id)
        assert skeleton.status == 'failed' and skeleton.reserved_call_id is None
        assert skeleton.data == {'synthetic_source_boundary_only': True}
        skeleton.actor_id = isolated.id
        db.commit()
    _, _, s.fork_receipt = fork(s)
    s.binding = binding(s, s.fork_receipt)
    s.working_id = s.fork_receipt['working_draft_ref']['id']
    profile = json.loads((ROOT / 'examples/qwen.json').read_text())
    profile.update(namespace='v260_m2_synthetic', name='native_' + uuid4().hex[:8])
    profile['execution'] = {'provider': 'qwen', 'model': native.MODEL, 'max_tokens': 8192,
        'secret_ref': 'DASHSCOPE_API_KEY', 'report_evidence_output': {
            'schema': native.OUTPUT_DECLARATION, 'max_tokens': 32768,
            'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}}
    s.model = publish(s.client, profile)
    s.posts, s.response, s.interrupted, s.run_errors = [], None, False, []
    real_run = consumer.run
    def diagnostic_run(*args, **kwargs):
        try:
            return real_run(*args, **kwargs)
        except BaseException as error:
            # Observe/re-raise the real production exception before worker's
            # safe generic message. No substitute result, state or inventory.
            def detail(failure):
                return {'type': type(failure).__name__, 'message': str(failure)[:200],
                    'stack': [(Path(frame.filename).name, frame.lineno, frame.name)
                        for frame in traceback.extract_tb(failure.__traceback__)[-8:]]}
            record = detail(error); record['causes'] = []
            cause = error.__cause__
            for _ in range(2):
                if cause is None:
                    break
                record['causes'].append(detail(cause)); cause = cause.__cause__
            s.run_errors.append(record)
            raise
    monkeypatch.setattr(consumer, 'run', diagnostic_run)
    monkeypatch.setenv('DASHSCOPE_API_KEY', KEY)
    monkeypatch.setattr(settings(), 'dashscope_api_key', SecretStr(KEY))
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 8)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 2_000_000)
    monkeypatch.setattr(settings(), 'report_design_maximum_cost_usd', 0.5)

    class Response:
        status_code = 200
        headers = {'x-request-id': 'v260-synthetic-native-response'}
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def iter_raw(self):
            assert type(s.response) is bytes and len(s.response) > 17
            yield s.response[:17]
            if s.interrupted:
                raise httpx.ReadError('explicit synthetic interrupted native body')
            yield s.response[17:]

    class HTTP:
        def __init__(self, **options):
            assert options == {'timeout': 600, 'follow_redirects': False, 'trust_env': False}
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def stream(self, method, endpoint, **options):
            assert method == 'POST' and endpoint == native.ENDPOINT
            assert options['headers'] == {'Authorization': 'Bearer ' + KEY,
                'Content-Type': 'application/json', 'Accept-Encoding': 'identity'}
            assert type(options['content']) is bytes
            s.posts.append(copy.deepcopy(options))
            return Response()

    # HTTP transport is the ONLY model/ledger substitution. No gateway,
    # cloud_budget, reserve/finish, DB, capture or journal methods are patched.
    from generic_agents import gateway
    monkeypatch.setattr(gateway.httpx, 'Client', HTTP)
    return s


def allocation_context(s):
    return ok(s.client.get(current.PREFIX + '/bindings/' + s.binding['id'] + '/allocation-context'))


def quote(s, *, positions=None):
    context = allocation_context(s)
    assert context['permissions']['can_request_preview'], context['reason']
    reference = context['draft_ref']
    body = {'generation_profile': allocation.PROFILE, 'activity_id': str(uuid4()),
        'draft_id': reference['id'], 'expected_draft_version': reference['version'],
        'expected_document_hash': reference['document_hash'], 'model_id': s.model['id'],
        'selection': {'mode': 'explicit', 'positions': [0, 1] if positions is None else positions},
        'batch_ordinal': 0, 'max_batch_items': 2,
        'catalogue_review': {'accepted': True,
            'expected_catalogue_review_hash': context['catalogue_review_hash']},
        'note': '仅测试本批明确引用的归属建议，非原报告或模型业务质量证明。😀',
        'binding_id': s.binding['id'], 'binding_record_hash': s.binding['record_hash'],
        'expected_configuration_hash': context['target_configuration']['configuration_hash']}
    preview = ok(s.client.post(BASE + '/preview-allocation', content=raw(body),
        headers={'Content-Type': 'application/json'}))
    return body, preview


def start_body(body, preview):
    return {**body, 'request_id': body['activity_id'], 'preview_hash': preview['preview_hash'],
        'request_hash': preview['current_scope']['native_request_hash'],
        'plan_hash': preview['plan_hash'], 'batch_hash': preview['batch_hash'],
        'current_scope_hash': preview['current_scope']['current_scope_hash'], 'consent': True}


def start(s, body, preview):
    request = start_body(body, preview); original = raw(request)
    result = ok(s.client.post(BASE + '/start-allocation', content=original,
        headers={'Content-Type': 'application/json'}), 202)
    return request, original, result


def finish(s, created, preview):
    # Move both synthetic claims to background; applying only the first must
    # preserve the second claim's pre-existing human assignment byte-for-byte.
    candidate = {'schema': wire.WIRE, 'decisions': {key: {'disposition': 'background',
        'business_keys': [], 'reason': '合成传输建议；由人明确选择，不代表真实报告解释。',
        'questions': []} for key in preview['decision_keys']}}
    envelope = {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant',
        'content': json.dumps(candidate, ensure_ascii=False)}}],
        'usage': {'prompt_tokens': 9, 'completion_tokens': 12, 'total_tokens': 21}}
    s.response = json.dumps(envelope, ensure_ascii=False, separators=(',', ':')).encode()
    assert process_once(job_id=created['job_id'], workspace_id=s.p.workspace_id,
        kinds=[consumer.JOB], deliver=False)
    with Session(s.bound) as db:
        job = db.get(m.Job, created['job_id'])
        if not s.interrupted:
            if job.status != 'succeeded':
                print('V260_M2_RUN_DIAGNOSTIC ' + json.dumps(s.run_errors,
                    ensure_ascii=False, separators=(',', ':')))
            assert job.status == 'succeeded', 'Real Job must succeed; see V260_M2_RUN_DIAGNOSTIC'
    return ok(s.client.get(BASE + '/' + created['id']))


def completed(s):
    body, preview = quote(s); request, original, created = start(s, body, preview)
    view = finish(s, created, preview)
    assert view['status'] in {'review', 'needs_clarification'}
    assert len(view['allocation']['decisions']) == len(preview['decision_keys']) == 2
    return NS(body=body, preview=preview, request=request, original=original,
        created=created, view=view)


def selection(s, view):
    keys = [view['allocation']['decisions'][0]['decision_key']]
    body = {'expected_version': view['version'], 'draft_ref': view['draft_ref'],
        'expected_projection_hash': view['allocation']['projection_hash'],
        'selected_decision_keys': keys, 'binding_id': s.binding['id'],
        'binding_record_hash': s.binding['record_hash'],
        'current_scope_hash': view['current_scope']['current_scope_hash']}
    preview = ok(s.client.post(BASE + '/' + view['id'] + '/preview-current-allocation-apply', json=body))
    return body, preview


def apply(s, view, body=None, preview=None):
    if body is None:
        body, preview = selection(s, view)
    request = {**body, 'request_id': str(uuid4()),
        'expected_apply_proposal_hash': preview['apply_proposal_hash'], 'confirm': True}
    original = raw(request)
    result = ok(s.client.post(BASE + '/' + view['id'] + '/apply-current-allocation',
        content=original, headers={'Content-Type': 'application/json'}))
    return request, original, result


def test_quote_start_real_call_capture_and_frozen_recovery_survive_later_authoring(allocation_scene):
    s = allocation_scene; before = owned_inventory(s)
    body, preview = quote(s)
    assert owned_inventory(s) == before and not s.posts
    assert preview['generation_profile'] == allocation.PROFILE
    assert preview['revision'] == s.binding['target_configuration']['revision']
    assert preview['admission_basis']['source_pins']['source_revision'] == s.origin['metadata']['revision']
    assert preview['revision'] > s.origin['metadata']['revision']
    assert preview['sent_payload']['schema'] == native.REQUEST_PAYLOAD_SCHEMA
    assert all(preview[key] is False for key in ('semantic_quality_verified', 'can_apply', 'can_publish'))
    request, original, created = start(s, body, preview)
    assert created['operation'] == 'start' and created['status'] == 'queued'
    assert set(created) == {'schema', 'id', 'version', 'operation', 'status', 'job_id'}
    with Session(s.bound) as db:
        row = db.get(Consumer, created['id']); frozen = copy.deepcopy(row.data['preview'])
        assert row.grant_hash == preview['budget']['grant_hash'] and row.revision == preview['revision']
        assert row.reserved_call_id is None
        assert db.get(m.Job, row.job_id).data['generation_profile'] == allocation.PROFILE
        assert len(list(db.scalars(select(Command).where(Command.consumer_id == row.id)))) == 1
    view = finish(s, created, preview)
    assert len(s.posts) == 1 and h(s.posts[0]['content']) == request['request_hash']
    # Original prepare_cloud_json may serialize inner content with different
    # whitespace. Its V213 contract compares the parsed object; the complete
    # ACTUAL outgoing body still has the exact frozen SHA asserted above.
    assert json.loads(json.loads(s.posts[0]['content'])['messages'][-1]['content']) == preview['sent_payload']
    with Session(s.bound) as db:
        row = db.get(Consumer, created['id']); call = db.get(m.CloudCall, row.reserved_call_id)
        # Gateway's existing _strict_evidence_usage retains token totals in
        # receipt.usage; cloud_budget charges that nested total, not a new
        # top-level token field. Journal and Call must retain the same receipt.
        usage = {'prompt_tokens': 9, 'completion_tokens': 12, 'total_tokens': 21}
        paid = call.data['receipt']
        assert call.status == 'succeeded' and call.charged_tokens == 21
        assert paid['usage'] == usage and all(type(value) is int for value in paid['usage'].values())
        assert paid['usage_unavailable'] is False and paid['usage_available'] is True
        assert paid['call_id'] == call.id and paid['provider'] == 'qwen' and paid['model'] == native.MODEL
        identity = consumer.dispatch_identity(row)
        assert identity['task_id'] == allocation.TASK_PREFIX + request['current_scope_hash']
        assert call.data['gateway_dispatch']['identity'] == identity
        assert call.data['gateway_dispatch']['reservation_request_hash'] == request['request_hash']
        assert identity['request_hash'] == request['request_hash'] != request['current_scope_hash']
        context = runtime._context(row); state = capture.load(bridge.root(), context)
        assert state['ready'] is True and state['response_complete'] is True
        assert capture.read_body(bridge.root(), context, state['capture_hash']) == s.response
        descriptor = journal.read_descriptor(runtime._journal_root(), context['binding'])
        assert journal.read_response(runtime._journal_root(), context['binding'], descriptor)['receipt'] == call.data['receipt']
        assert context['binding']['dispatch_hash'] == digest(identity)
        assert row.data['preview'] == frozen
    captured = ok(s.client.get(BASE + '/' + created['id'] + '/capture-summary'))
    assert captured['available'] and captured['response_complete'] and captured['usage'] == usage
    working = catalogue(s, s.working_id); document = copy.deepcopy(working['document'])
    document['businesses'][0]['purpose'] += '；之后正常保存的人稿'
    save_document(s, working, document); advance(s, '付费答复之后配置正常前进')
    before = owned_inventory(s)
    replay = ok(s.client.post(BASE + '/start-allocation', content=original,
        headers={'Content-Type': 'application/json'}), 202)
    recovered = ok(s.client.get(BASE + '/requests/' + request['request_id'], params={'expected_sha256': h(original)}))
    assert replay == created and recovered['found'] and recovered['result'] == created
    historical = ok(s.client.get(BASE + '/' + created['id']))
    assert historical['draft_ref'] == view['draft_ref']
    assert historical['allocation'] == view['allocation']
    assert owned_inventory(s) == before and len(s.posts) == 1
    # A genuinely unreadable frozen source is different from a later head:
    # preserve the original receipt, but hide the now-unverifiable suggestion.
    s.source_unavailable = True
    assert s.client.get(BASE + '/' + created['id']).status_code == 409
    assert ok(s.client.get(BASE + '/requests/' + request['request_id'],
        params={'expected_sha256': h(original)}))['result'] == created
    assert owned_inventory(s) == before and len(s.posts) == 1
    # This source failure/current-config advance must still not lose human work.
    working = catalogue(s, s.working_id); document = copy.deepcopy(working['document'])
    document['businesses'][0]['purpose'] += '；来源不可读期间另存的明确人稿'
    save_document(s, working, document)
    assert catalogue(s, s.working_id)['document'] == document
    assert parent_state(s) == s.original_parent


@pytest.mark.parametrize('fault', ['scope-hash', 'native-request-hash', 'binding-rebind'])
def test_start_cannot_rebind_an_old_quote_or_substitute_a_current_hash_for_native_bytes(allocation_scene, fault):
    s = allocation_scene; body, preview = quote(s); request = start_body(body, preview)
    if fault == 'scope-hash': request['current_scope_hash'] = 'a' * 64
    elif fault == 'native-request-hash': request['request_hash'] = request['current_scope_hash']
    else: request['binding_id'] = str(uuid4())
    before = owned_inventory(s)
    response = s.client.post(BASE + '/start-allocation', content=raw(request),
        headers={'Content-Type': 'application/json'})
    assert response.status_code in {404, 409}
    assert owned_inventory(s) == before and not s.posts


def test_config_advance_after_queue_blocks_before_reservation_and_keeps_original_human_copy(allocation_scene):
    s = allocation_scene; body, preview = quote(s); _, _, created = start(s, body, preview)
    original_working = catalogue(s, s.working_id); calls = ids(s, m.CloudCall)
    advance(s, '排队后当前配置真实前进')
    assert process_once(job_id=created['job_id'], workspace_id=s.p.workspace_id,
        kinds=[consumer.JOB], deliver=False)
    with Session(s.bound) as db:
        assert db.get(m.Job, created['job_id']).status == 'failed'
        row = db.get(Consumer, created['id'])
        assert row.reserved_call_id is None and row.data['dispatch_started'] is False
    assert ids(s, m.CloudCall) == calls and not s.posts
    assert catalogue(s, s.working_id)['document'] == original_working['document']
    assert allocation_context(s)['permissions']['can_request_preview'] is False


def test_selected_apply_is_one_real_child_save_and_historical_links_survive_later_head(allocation_scene):
    s = allocation_scene; flow = completed(s); before = catalogue(s, s.working_id)
    before_db = owned_inventory(s); body, proposal = selection(s, flow.view)
    assert owned_inventory(s) == before_db
    assert len(proposal['changes']) == 1 and proposal['requires_explicit_adoption']
    request, original, result = apply(s, flow.view, body, proposal)
    marker = result['application']; after = catalogue(s, s.working_id)
    assert UUID(after['id']).version == 5 and after['version'] == before['version'] + 1
    assert marker['saved_version'] == after['version'] and marker['saved_document_hash'] == after['document_hash']
    assert result['operation'] == 'apply-current-allocation'
    positions = {row['input_position']: row['decision_key'] for row in flow.view['allocation']['decisions']}
    selected_position = next(pos for pos, key in positions.items() if key in request['selected_decision_keys'])
    other_position = next(pos for pos in positions if pos != selected_position)
    selected_groups = [g for g in after['document']['allocation_groups'] if selected_position in g['input_positions']]
    other_groups = [g for g in after['document']['allocation_groups'] if other_position in g['input_positions']]
    assert len(selected_groups) == 1 and selected_groups[0]['kind'] == 'background'
    assert len(other_groups) == 1 and other_groups[0]['kind'] == 'business'
    assert other_groups[0]['business_keys'] == ['human_scope']
    assert after['document']['businesses'] == before['document']['businesses']
    assert after['origin'] == before['origin']
    from generic_agents import catalogue_current_allocation_apply_v260 as application
    with Session(s.bound) as db:
        parent = db.get(Command, marker['command_id']); child = db.get(cat.Command, marker['child_command_id'])
        assert parent.operation == 'apply-current-allocation' and parent.exact_body.encode() == original
        assert child.operation == 'save' and child.draft_id == s.working_id
        anchors = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id,
            m.Audit.action.in_([application.LINK_AUDIT, application.CHILD_LINK_AUDIT]))))
        assert len(anchors) == 2 and all(event.data['link_hash'] == marker['link_hash'] for event in anchors)
    document = copy.deepcopy(after['document']); document['businesses'][0]['purpose'] += '；采纳后另存人稿'
    save_document(s, after, document)
    replay = ok(s.client.post(BASE + '/' + flow.view['id'] + '/apply-current-allocation', content=original,
        headers={'Content-Type': 'application/json'}))
    assert replay == result and len(s.posts) == 1
    assert ok(s.client.get(BASE + '/' + flow.view['id']))['allocation_apply'] == marker
    assert parent_state(s) == s.original_parent


@pytest.mark.parametrize('side', ['parent', 'child'])
def test_losing_either_application_link_blocks_both_histories_without_repair(allocation_scene, side):
    s = allocation_scene; flow = completed(s); _, _, result = apply(s, flow.view)
    from generic_agents import catalogue_current_allocation_apply_v260 as application
    action = application.LINK_AUDIT if side == 'parent' else application.CHILD_LINK_AUDIT
    with Session(s.bound) as db:
        anchor = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.p.workspace_id, m.Audit.action == action))
        assert anchor is not None; db.delete(anchor); db.commit()
    before = owned_inventory(s)
    assert s.client.get(BASE + '/' + flow.view['id']).status_code == 409
    assert s.client.get(cat.PREFIX + '/drafts/' + s.working_id).status_code == 409
    assert owned_inventory(s) == before and len(s.posts) == 1


def test_apply_requires_fresh_source_and_failed_real_child_rolls_back_the_whole_adoption(allocation_scene, monkeypatch):
    s = allocation_scene; flow = completed(s); body, proposal = selection(s, flow.view)
    request = {**body, 'request_id': str(uuid4()), 'expected_apply_proposal_hash': proposal['apply_proposal_hash'], 'confirm': True}
    before = owned_inventory(s); s.source_unavailable = True
    response = s.client.post(BASE + '/' + flow.view['id'] + '/apply-current-allocation', json=request)
    assert response.status_code == 409 and owned_inventory(s) == before
    s.source_unavailable = False
    real_mutate = cat.mutate
    def fail_after_real_child(*args, **kwargs):
        real_mutate(*args, **kwargs)
        raise HTTPException(409, 'explicit synthetic failure after real child save')
    monkeypatch.setattr(cat, 'mutate', fail_after_real_child)
    response = s.client.post(BASE + '/' + flow.view['id'] + '/apply-current-allocation', json=request)
    assert response.status_code == 409 and owned_inventory(s) == before
    assert parent_state(s) == s.original_parent and len(s.posts) == 1


def test_partial_unknown_keeps_one_call_and_can_only_end_with_its_reserved_budget_retained(allocation_scene):
    s = allocation_scene; body, preview = quote(s); request, original, created = start(s, body, preview)
    s.interrupted = True; view = finish(s, created, preview)
    assert view['status'] == 'response_unknown' and view['allocation'] is None and len(s.posts) == 1
    before = rows(s, m.CloudCall); calls = ids(s, m.CloudCall)
    with Session(s.bound) as db:
        budgets = {job.id: copy.deepcopy(job.data) for job in db.scalars(select(m.Job).where(
            m.Job.workspace_id == s.p.workspace_id, m.Job.kind == 'cloud_budget'))}
    end_context = ok(s.client.get(BASE + '/' + created['id'] + '/current-allocation-end-context'))
    assert end_context['can_end'] is True and end_context['usage_unknown'] is True
    end_body = {'request_id': str(uuid4()), 'expected_version': view['version'],
        'confirm': True, 'retain_reserved_budget': True}
    end_raw = raw(end_body)
    result = ok(s.client.post(BASE + '/' + created['id'] + '/end-current-interrupted',
        content=end_raw, headers={'Content-Type': 'application/json'}))
    assert result['operation'] == 'end-current-interrupted'
    assert ids(s, m.CloudCall) == calls and len(s.posts) == 1
    after_calls = rows(s, m.CloudCall)[m.CloudCall.__tablename__]
    for identity, prior in before[m.CloudCall.__tablename__].items():
        after = after_calls[identity]
        assert after['reserved_tokens'] == prior['reserved_tokens']
        assert after['charged_tokens'] == prior['charged_tokens'] == prior['reserved_tokens']
        assert after['data'].get('receipt') in (None, {})
    with Session(s.bound) as db:
        assert {job.id: copy.deepcopy(job.data) for job in db.scalars(select(m.Job).where(
            m.Job.workspace_id == s.p.workspace_id, m.Job.kind == 'cloud_budget'))} == budgets
    found = ok(s.client.get(BASE + '/requests/' + end_body['request_id'], params={'expected_sha256': h(end_raw)}))
    assert found['found'] and found['result'] == result
    assert ok(s.client.post(BASE + '/' + created['id'] + '/end-current-interrupted', content=end_raw,
        headers={'Content-Type': 'application/json'})) == result
    assert ok(s.client.post(BASE + '/start-allocation', content=original,
        headers={'Content-Type': 'application/json'}), 202) == created
    assert len(s.posts) == 1 and parent_state(s) == s.original_parent


def test_foreign_actor_cannot_read_or_reuse_the_original_current_activity(allocation_scene):
    s = allocation_scene; flow = completed(s)
    with other_client(s) as foreign:
        # Login creates its own legitimate rate-limit Job. The baseline starts
        # AFTER login so the following requests must add no other side effects.
        before = owned_inventory(s)
        for response in (foreign.get(BASE + '/' + flow.view['id']),
                foreign.get(BASE + '/requests/' + flow.request['request_id'], params={'expected_sha256': h(flow.original)}),
                foreign.post(BASE + '/start-allocation', content=flow.original,
                    headers={'Content-Type': 'application/json'})):
            assert response.status_code in {403, 404, 409}
            assert flow.body['note'] not in response.text
        assert owned_inventory(s) == before and len(s.posts) == 1
