"""Real indexed admission/registry/request/journal seams, synthetic SQL source.

RuntimeIO retains exact real published/AuthSession/grant SQL. The authenticated
source loader is an explicit in-memory spy, not PG/source-ownership evidence.
Real V178 preparation and actual budget arithmetic remain active. No test sends
a provider request, writes a consumer/Job, or establishes model usefulness.
"""
import copy
import json
from decimal import Decimal, ROUND_CEILING
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import gateway, cloud_budget, db as database
from generic_agents import report_saved_map_design_runtime_v176 as subject
from generic_agents import report_saved_map_design_consumer_v176 as consumer
from generic_agents import report_saved_map_design_preparation_v178 as preparation
from generic_agents import report_design_grouped_native_v178 as native
from generic_agents import report_design_profiles_v178 as profiles
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_journal_v2 as journal
from generic_agents.contracts import digest
from tests.test_report_saved_map_design_runtime_pure_v176 import admission, RuntimeIO
from tests.test_report_saved_map_design_consumer_v176 import no_network_or_process
from tests.test_report_saved_map_design_preparation_v176 import identity, seal


@pytest.fixture
def indexed(admission, monkeypatch):
    a = admission
    a.cap.data['execution']['report_evidence_output'] = {'schema': native.OUTPUT_DECLARATION,
        'max_tokens': 32768, 'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}
    a.cap.content_hash = a.cap.validated_hash = digest(a.cap.data)
    a.activity_id = identity(770)
    a.config = NS(report_design_maximum_cost_usd=3.0,
        cloud_daily_call_limit=10, cloud_daily_token_limit=1000000)
    monkeypatch.setattr(subject, 'settings', lambda: a.config)
    def forbidden(*args, **kwargs): raise AssertionError('A quote cannot read legacy allowance or send/reserve')
    monkeypatch.setattr(subject, '_allowance', forbidden)
    monkeypatch.setattr(gateway, 'cloud_json', forbidden)
    monkeypatch.setattr(cloud_budget, 'reserve', forbidden)
    monkeypatch.setattr(database, 'engine', forbidden)
    return a


def quote_inputs(a, *, note=None, activity_id=None, model_id=None):
    note = a.body.note if note is None else note
    activity_id = a.activity_id if activity_id is None else activity_id
    model_id = a.body.model_id if model_id is None else model_id
    prep = preparation.build_preparation(a.cap, a.context, note=note)
    allowance = subject._indexed_allowance(a.p, a.body.producer_id, a.body.revision,
        model_id, activity_id)
    frozen = {**allowance, 'source_snapshot_hash': a.context['snapshot']['snapshot_hash'],
        'request_hash': prep['request_hash'], 'note_hash': digest(note)}
    return prep, frozen


def quote(a, *, note=None, activity_id=None, model_id=None, consumer_row=None, lock=False):
    note = a.body.note if note is None else note
    activity_id = a.activity_id if activity_id is None else activity_id
    model_id = a.body.model_id if model_id is None else model_id
    _, allowance = quote_inputs(a, note=note, activity_id=activity_id, model_id=model_id)
    a.db.grant_hash = digest(allowance)
    return subject.admit(a.db, a.p, a.body.producer_id, a.body.revision, model_id,
        lock=lock, consumer=consumer_row, note=note, generation_profile=profiles.INDEXED,
        activity_id=activity_id)


def test_actual_quote_uses_indexed_preparation_exact_actor_sql_and_never_consents_or_writes(indexed):
    a = indexed; before = copy.deepcopy(a.context)
    prep, allowance = quote_inputs(a)
    result = quote(a)
    assert set(result) == {'source_context', 'capability', 'revision', 'budget'}
    assert result['source_context'] == a.context and result['capability'] is a.cap
    assert result['budget']['grant_hash'] == digest(allowance)
    assert result['budget']['token_bound'] == prep['input_summary']['token_bound']
    assert result['budget']['call_limit'] == 1 and result['budget']['maximum_cost_usd'] == 3.0
    assert prep['generation_contract']['evidence_stage'] == native.STAGE
    assert prep['generation_contract']['delivery'] == native.DELIVERY
    assert a.db.log == ['actor-session', ('configuration', False), 'published', 'flush', 'source-spy', 'grant']
    assert not {'consent', 'job_id', 'reserved_call_id', 'can_apply', 'can_publish'} & set(result)
    assert a.context == before
    # RuntimeIO.add/commit are hard failures; successful preview cannot reserve,
    # dispatch, create a consumer, Job or pretend that the user has consented.
    assert quote(a)['budget']['grant_hash'] == result['budget']['grant_hash']


@pytest.mark.parametrize('maximum', [0, -1, 3.000001, True, False, None, '3', float('nan'), float('inf')])
def test_server_cost_limit_is_strictly_positive_numeric_and_at_most_three(indexed, maximum):
    a = indexed; a.config.report_design_maximum_cost_usd = maximum
    with pytest.raises(HTTPException) as error:
        subject.admit(a.db, a.p, a.body.producer_id, a.body.revision, a.body.model_id,
            lock=False, note=a.body.note, generation_profile=profiles.INDEXED, activity_id=a.activity_id)
    assert error.value.status_code == 409 and '3美元' in error.value.detail
    assert a.sources == []


@pytest.mark.parametrize('field', ['cloud_daily_call_limit', 'cloud_daily_token_limit'])
@pytest.mark.parametrize('value', [0, -1])
def test_quote_cannot_enable_a_disabled_global_budget(indexed, field, value):
    a = indexed; setattr(a.config, field, value)
    with pytest.raises(HTTPException) as error:
        subject.admit(a.db, a.p, a.body.producer_id, a.body.revision, a.body.model_id,
            lock=False, note=a.body.note, generation_profile=profiles.INDEXED, activity_id=a.activity_id)
    assert error.value.status_code == 409 and '日预算未启用' in error.value.detail
    assert a.sources == []


def test_whole_actual_token_bound_must_fit_global_budget_without_truncation(indexed):
    a = indexed; prep, _ = quote_inputs(a)
    a.config.cloud_daily_token_limit = prep['input_summary']['token_bound']
    assert quote(a)['budget']['token_bound'] == a.config.cloud_daily_token_limit
    a.config.cloud_daily_token_limit -= 1
    with pytest.raises(HTTPException) as error: quote(a)
    assert error.value.status_code == 409 and '云模型日额度' in error.value.detail
    assert prep == preparation.build_preparation(a.cap, a.context, note=a.body.note)


def test_exact_full_http_bytes_and_output_ceiling_determine_the_cost_bound(indexed):
    a = indexed; note = '明确人工待核，不裁剪全部输入。' * 50
    prep, _ = quote_inputs(a, note=note)
    output = prep['generation_contract']['dispatch_options']['max_tokens']
    cost = (Decimal(prep['input_summary']['prepared_request_bytes'] + 1024) * 2
        + Decimal(output) * 6) / Decimal(1000000)
    ceiling = float(cost.quantize(Decimal('0.000001'), rounding=ROUND_CEILING))
    assert 0 < ceiling < 3
    a.config.report_design_maximum_cost_usd = ceiling
    assert quote(a, note=note)['budget']['maximum_cost_usd'] == ceiling
    a.config.report_design_maximum_cost_usd = ceiling - 0.000001
    with pytest.raises(HTTPException) as error: quote(a, note=note)
    assert error.value.status_code == 409 and '费用上限' in error.value.detail


@pytest.mark.parametrize('change', ['activity', 'note', 'source_snapshot', 'model', 'actor', 'workspace'])
def test_frozen_grant_never_reuses_different_activity_source_model_or_principal(indexed, change):
    a = indexed; original = quote(a)['budget']['grant_hash']; options = {}
    if change == 'activity': options['activity_id'] = identity(771)
    elif change == 'note': options['note'] = a.body.note + ' 新独立说明。'
    elif change == 'source_snapshot':
        a.context['snapshot']['producer']['state_hash'] = 'e' * 64
        seal(a.context['snapshot'])
    elif change == 'model':
        a.cap.id = identity(772); options['model_id'] = a.cap.id
    elif change == 'actor': a.p.user_id = identity(773)
    else: a.p.workspace_id = identity(774); a.cap.workspace_id = a.p.workspace_id
    assert quote(a, **options)['budget']['grant_hash'] != original
    # The in-memory source spy is not source authority for another actor/env;
    # this proves budget binding only. Real source-scope rejection is PG/API.


@pytest.mark.parametrize('fault', ['revoked_session', 'configuration', 'cap_kind', 'cap_hash',
    'unpublished', 'cloud_policy', 'secret', 'old_declaration', 'consumed_grant'])
def test_indexed_quote_still_requires_current_iam_published_model_and_unused_grant(indexed, fault):
    a = indexed
    if fault == 'revoked_session': a.db.authorized = False
    elif fault == 'configuration': a.revision += 1
    elif fault == 'cap_kind': a.cap.kind = 'tool'
    elif fault == 'cap_hash': a.cap.validated_hash = 'f' * 64
    elif fault == 'unpublished': a.cap.status = 'draft'
    elif fault == 'cloud_policy':
        a.cap.data['data_policy']['cloud_allowed'] = False
        a.cap.content_hash = a.cap.validated_hash = digest(a.cap.data)
    elif fault == 'secret':
        a.cap.data['execution']['secret_ref'] = 'QWEN_TOKENPLAN_API_KEY'
        a.cap.content_hash = a.cap.validated_hash = digest(a.cap.data)
    elif fault == 'old_declaration':
        a.cap.data['execution']['report_evidence_output'] = {'schema': subject.native.OUTPUT_DECLARATION,
            'max_tokens': 32768, 'reduce_transport': subject.native.DELIVERY, 'endpoint': subject.native.ENDPOINT}
        a.cap.content_hash = a.cap.validated_hash = digest(a.cap.data)
    else: a.db.consumed = NS(id=identity(779))
    # Compute the query fixture before modifying the invalid model when its
    # preparation would correctly fail first. The production call stays real.
    if fault in {'old_declaration'}:
        with pytest.raises(ValueError):
            subject.admit(a.db, a.p, a.body.producer_id, a.body.revision, a.body.model_id,
                lock=False, note=a.body.note, generation_profile=profiles.INDEXED, activity_id=a.activity_id)
    else:
        with pytest.raises(HTTPException) as error: quote(a)
        assert error.value.status_code == (403 if fault == 'revoked_session' else 409)


def test_exact_existing_consumer_may_recheck_its_original_grant_but_not_another(indexed):
    a = indexed; a.db.consumed = NS(id=identity(780))
    with pytest.raises(HTTPException): quote(a)
    original = NS(id=a.db.consumed.id, reserved_call_id=None)
    assert quote(a, consumer_row=original)['budget']['grant_hash'] == a.db.grant_hash
    with pytest.raises(HTTPException): quote(a, consumer_row=NS(id=identity(781), reserved_call_id=None))


@pytest.mark.parametrize('consent', [False, 0, 1, 'true', None])
def test_quote_or_truthy_data_is_not_explicit_new_call_consent(indexed, consent):
    a = indexed; result = quote(a)
    body = {**a.body.model_dump(), 'generation_profile': profiles.INDEXED,
        'activity_id': a.activity_id, 'request_id': a.activity_id,
        'source_snapshot_hash': a.context['snapshot']['snapshot_hash'],
        'preview_hash': digest(result['budget']), 'consent': consent}
    with pytest.raises(ValueError): consumer.StartIndexedBody.model_validate(body)
    body['consent'] = True
    assert consumer.StartIndexedBody.model_validate(body).consent is True
    body['request_id'] = identity(782)
    with pytest.raises(ValueError): consumer.StartIndexedBody.model_validate(body)


def test_profile_registry_selects_original_family_only_and_does_not_relabel_legacy():
    assert profiles.profile_id({'schema': 'report-saved-map-design-preparation-v178'}) == profiles.INDEXED
    assert profiles.profile_id({'schema': 'report-saved-map-design-preparation-v176'}) == profiles.LEGACY
    new = profiles.modules(profiles.INDEXED); old = profiles.modules(profiles.LEGACY)
    assert new.preparation is preparation and new.native is native
    assert new.native.STAGE == 'reduce-v178' and old.native is subject.native
    assert old.preparation is subject.preparation and old.native.STAGE == 'reduce-v176'
    assert new.wire.PROJECTION != old.wire.PROJECTION
    for unknown in ('v178', 'indexed', None):
        with pytest.raises(ValueError): profiles.modules(unknown)
    with pytest.raises(ValueError): profiles.profile_id({'schema': 'report-saved-map-design-preparation-v999'})


def test_legacy_admission_keeps_original_file_permission_path(admission, monkeypatch):
    a = admission
    def forbidden(*args, **kwargs): raise AssertionError('A legacy activity cannot borrow indexed UI allowance')
    monkeypatch.setattr(subject, '_indexed_allowance', forbidden)
    result = subject.admit(a.db, a.p, a.body.producer_id, a.body.revision, a.body.model_id,
        lock=False, note=a.body.note)
    assert result['budget']['grant_hash'] == digest(a.permit)


def indexed_row(a):
    prep, allowance = quote_inputs(a)
    row = a.row
    row.grant_hash = digest(allowance)
    row.data = {**copy.deepcopy(row.data), 'preview': {
        **copy.deepcopy(row.data['preview']), 'preparation': prep,
        'capability': consumer._cap_value(a.cap),
        'body': {**a.body.model_dump(), 'generation_profile': profiles.INDEXED, 'activity_id': a.activity_id}}}
    return row, prep


def test_actual_dispatch_and_cold_journal_recovery_keep_new_profile_and_original_bytes(indexed, tmp_path, monkeypatch):
    a = indexed; row, prep = indexed_row(a); call_id = identity(783)
    request = preparation.rebuild_request(prep, a.cap, a.context, note=a.body.note)
    spec = preparation.stage_spec(prep)
    saved_root = tmp_path / 'journal'; capture_root = tmp_path / 'capture'
    monkeypatch.setattr(bridge, 'root', lambda: capture_root)
    monkeypatch.setattr(subject, '_journal_root', lambda: saved_root)
    events = []; calls = []
    def associate(identity_value):
        assert identity_value == call_id
        row.reserved_call_id = identity_value; a.db.poison = True
        events.append('associated')
        return consumer.journal_binding(row)
    def transport(cap, messages, schema, principal, **kwargs):
        assert cap is a.cap and principal is a.p
        assert kwargs['evidence_stage'] == native.STAGE and kwargs['full_report'] is True
        assert kwargs['dispatch_identity']['task_id'] == 'saved-map-design-v178'
        prepared, _, images = gateway.prepare_cloud_json(cap, messages, schema)
        assert images == 0
        actual = native.prepare_request(prepared, schema,
            native.dispatch_options(cap.data['execution'], gateway.cloud_json_options(cap.data['execution'])),
            native.STAGE, **native.request_inputs(prepared))
        assert actual['body'] == request['request_body'] and actual['body_sha256'] == prep['request_hash']
        # A deliberately invalid content body is still the original response;
        # dispatch/cold recovery must preserve it, not fabricate a new candidate.
        raw = json.dumps({'usage': {'prompt_tokens': 8, 'completion_tokens': 9, 'total_tokens': 17},
            'choices': [{'finish_reason': 'length', 'message': {'role': 'assistant',
                'content': '{"result":'}}]}, ensure_ascii=False).encode('utf-8')
        with kwargs['on_reserved'](call_id) as sink:
            calls.append(sink.context)
            sink.mark_request_started(); sink.begin_response(200, None, 'SYNTHETIC-ONLY')
            sink.append(raw); sink.finish(eof=True)
            text, receipt, _ = bridge.response_context(sink.context, 'qwen', native.MODEL,
                native.input_policy(a.cap.data['execution']), native.STAGE)
            kwargs['on_response'](text, receipt)
            descriptor = journal.read_descriptor(saved_root, sink.context['binding'])
            assert journal.read_response(saved_root, sink.context['binding'], descriptor)['content'] == text
            events.append('original-journal-before-orm')
        return None, receipt
    monkeypatch.setattr(gateway, 'cloud_json', transport)
    detached = NS(**consumer._row_pin(row))
    subject.dispatch(a.db, a.p, None, detached, request=request, spec=spec, on_reserved=associate)
    assert events == ['associated', 'original-journal-before-orm']
    # No provider, reserve, ORM or replay is allowed during original GET read.
    def forbidden(*args, **kwargs): raise AssertionError('Cold read cannot dispatch or reserve')
    monkeypatch.setattr(gateway, 'cloud_json', forbidden)
    first = subject.read_response(a.db, a.p, row, recover_saved=False)
    second = subject.read_response(a.db, a.p, row, recover_saved=True)
    assert first == second and first['receipt']['delivery'] == native.DELIVERY
    assert first['receipt']['finish_reason'] == 'length' and first['receipt']['call_id'] == call_id
    assert first['binding'] == calls[0]['binding'] and len(calls) == 1
    assert first['body'].decode('utf-8') == journal.read_response(saved_root,
        first['binding'], first['descriptor'])['content']
