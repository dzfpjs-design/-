"""Actual V190 preparation/profile/consumer contracts over public sources.

No DB, source ACL, actual publication, spending consent or provider quality is
proved. Budget and admit I/O adapters are explicitly synthetic boundaries;
source selection, preparation, selector/causal projection and DTOs stay real.
"""
import copy
import hashlib
import json
from decimal import Decimal, ROUND_CEILING
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import report_business_flow_preparation_v190 as subject
from generic_agents import report_business_flow_native_v190 as native
from generic_agents import report_business_flow_wire_v190 as wire
from generic_agents import report_business_flow_preparation_v187 as old_preparation
from generic_agents import report_business_flow_revision_policy_v185 as platform
from generic_agents import report_scoped_design_consumer_v181 as consumer
from generic_agents import report_scoped_design_runtime_v181 as runtime
from generic_agents import report_scoped_design_profiles_v182 as profiles
from generic_agents import report_business_flow_allowance_v187 as allowance_adapter
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_protocol_v184 import no_io, prepared as original_prepared, answer as plan_answer
from tests.test_report_business_flow_protocol_v187 import raw, identity
from tests.test_report_business_flow_wire_v190 import envelope


NOTE = ' 保留完整选定范围，逐项核对因果来源＋🙂。 '


def prepared():
    cap, source, plan, previous, _ = original_prepared()
    cap.data['execution'] = {'provider': native.PROVIDER, 'model': native.MODEL,
        'max_tokens': 2048, 'secret_ref': 'DEEPSEEK_API_KEY', 'report_evidence_output': {
            'schema': native.OUTPUT_DECLARATION, 'max_tokens': 65536,
            'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}}
    # This is only a public in-memory fixture, not a registered capability.
    cap.data.setdefault('data_policy', {})['cloud_allowed'] = True
    cap.id = identity(1903)
    cap.workspace_id = source['snapshot']['workspace_id']
    cap.content_hash = digest(cap.data)
    cap.status = 'published'
    value = subject.build_preparation(cap, source, plan, previous['scope_id'], note=NOTE)
    rebuilt = subject.rebuild_request(value, cap, source, plan, value['scope_id'], note=NOTE)
    return NS(cap=cap, source=source, plan=plan, previous=previous, value=value, rebuilt=rebuilt)


def preview(c):
    snapshot = c.source['snapshot']
    p = NS(user_id=snapshot['actor_id'], workspace_id=snapshot['workspace_id'], role='operator')
    body = consumer.PreviewFlowCausalBody(plan_id=identity(1901), expected_plan_version=1,
        plan_hash=c.value['plan_hash'], scope_id=c.value['scope_id'], revision=1,
        model_id=c.cap.id, note=NOTE, activity_id=identity(1902), generation_profile=profiles.FLOW_CAUSAL)
    budget = {'schema': 'report-scoped-design-budget-v181', 'call_limit': 1,
        'token_bound': c.value['input_summary']['token_bound'], 'conservative_cost_usd': 0.5,
        'maximum_cost_usd': 3, 'pricing_basis_hash': digest('public-synthetic-price-only'),
        'grant_hash': digest('public-synthetic-not-a-spending-grant')}
    value = {'schema': consumer.PREVIEW, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'body': body.model_dump(), 'source_context': copy.deepcopy(c.source),
        'scope_plan': copy.deepcopy(c.plan), 'capability': consumer._cap_value(c.cap),
        'preparation': copy.deepcopy(c.value), 'budget': budget,
        'generation_profile': profiles.FLOW_CAUSAL, **consumer.FALSE_FLAGS}
    value['preview_hash'] = consumer.h(value)
    return p, body, value


def reseal(value):
    value['generation_contract_hash'] = digest(value['generation_contract'])
    value['payload_hash'] = digest(value['payload'])
    value['preparation_hash'] = digest({k: v for k, v in value.items() if k != 'preparation_hash'})
    return value


def test_same_scope_keeps_every_original_claim_span_and_unsent_ledger_without_clipping():
    c = prepared()
    old, value = c.previous, c.value
    assert value['scope_binding'] == old['scope_binding']
    assert value['input_claims'] == old['input_claims'] == value['payload']['input_claims']
    assert value['payload']['spans'] == old['payload']['spans']
    assert value['selected_global_positions'] == old['selected_global_positions'] == [0, 2]
    assert value['not_sent_global_positions'] == old['not_sent_global_positions'] == [1, 3]
    assert value['source_context'] == c.source and value['scope_plan'] == c.plan
    assert value['scope_binding']['global_input_count'] == 4 and len(value['input_claims']) == 2
    assert value['source_snapshot_hash'] == c.source['snapshot']['snapshot_hash']
    assert set(value['payload']) == {'schema', 'scope_binding', 'input_claims', 'spans', 'design_note', 'platform_policy'}
    assert value['payload']['schema'] == native.REQUEST_PAYLOAD_SCHEMA
    assert value['payload']['design_note'] == NOTE
    assert not any(k in value['payload'] for k in ('parent_source', 'source_context', 'snapshot', 'findings'))
    sent = c.rebuilt['request_body'].decode('utf-8')
    assert 'GLOBAL_CONTEXT_NOT_FOR_THIS_MODEL' not in sent
    assert 'GLOBAL_USER_INSTRUCTION_NOT_FOR_THIS_MODEL' not in sent
    assert value['partial_scope'] is True and value['call_count'] == 1
    assert all(value[k] is False for k in ('aggregate_verified', 'semantic_quality_verified',
        'consumer_authorized', 'can_apply', 'can_publish'))


def test_exact_task_schema_provider_policy_request_and_hashes_bind_one_total_output_reservation():
    c = prepared()
    value, actual = c.value, c.rebuilt
    spec = value['generation_contract']
    assert value['schema'] == subject.PREPARATION == 'report-business-flow-preparation-v190'
    assert spec['schema'] == subject.GENERATION == 'report-business-flow-generation-v190'
    assert spec['task'] == subject.TASK and spec['task_hash'] == digest(subject.TASK)
    assert spec['evidence_stage'] == native.STAGE and spec['delivery'] == native.DELIVERY
    assert spec['endpoint'] == native.ENDPOINT and spec['model_hash'] == digest(c.cap.data)
    assert spec['output_schema'] == wire.output_schema(value['scope_binding'])
    assert spec['wire_result_schema'] == wire.WIRE and spec['projection_schema'] == wire.PROJECTION
    assert value['platform_policy_hash'] == spec['platform_policy_hash'] == digest(platform.platform_policy())
    assert value['payload']['platform_policy'] == platform.platform_policy()
    assert actual['request']['messages'][0]['content'] == native.system_prefix(native.STAGE) + native.SCHEMA_PROMPT_PREFIX + canonical_json(spec['output_schema'])
    task = json.loads(actual['request']['messages'][1]['content'])
    assert task['schema'] == subject.TASK_ID and task['task'] == subject.TASK
    assert task['platform_policy'] == platform.platform_policy()
    assert json.loads(actual['request']['messages'][-1]['content']) == value['payload']
    assert {k: v for k, v in actual['request'].items() if k != 'messages'} == spec['dispatch_options'] == {
        'model': 'deepseek-v4-pro', 'max_tokens': 65536, 'thinking': {'type': 'enabled'},
        'reasoning_effort': 'high', 'stream': False, 'response_format': {'type': 'json_object'}}
    assert actual['request_body'] == raw(actual['request'])
    assert actual['request_hash'] == value['request_hash'] == hashlib.sha256(actual['request_body']).hexdigest()
    assert value['messages_hash'] == digest(actual['request']['messages'])
    assert value['input_summary']['token_bound'] == len(actual['request_body']) + 65536 + 1024
    assert value['output_capacity']['max_output_tokens'] == 65536
    assert value['output_capacity']['output_reservation'] == native.OUTPUT_RESERVATION
    assert value['output_capacity']['fit_guaranteed'] is False


def test_actual_cold_build_check_rebuild_spec_and_c190_answer_projection_share_the_frozen_scope():
    c = prepared()
    before = canonical_json([c.source, c.plan, c.previous, c.value])
    assert subject.check_preparation(c.value, c.cap, c.source, c.plan, c.value['scope_id'], note=NOTE) == c.value
    assert subject.rebuild_request(c.value, c.cap, c.source, c.plan, c.value['scope_id'], note=NOTE) == c.rebuilt
    spec = subject.stage_spec(c.value)
    assert spec == c.value['generation_contract']
    authored = envelope(plan_answer(c.value), c.value['scope_binding'])
    projection = wire.build_projection(raw(authored), scope_binding=c.value['scope_binding'],
        catalog=c.source['catalog'], allowed_span_refs=c.value['allowed_span_refs'],
        context=c.source['context'], input_claims=c.value['input_claims'])
    assert projection['raw_causal_plan'] == authored
    assert projection['causal_analysis']['availability']['declared_inputs_available'] is True
    assert projection['wire']['schema'] == 'report-business-flow-wire-v182'
    assert projection['can_apply'] is projection['semantic_quality_verified'] is False
    manual = wire.new_manual(projection)
    assert wire.validate_value(manual, scope_binding=c.value['scope_binding'], catalog=c.source['catalog'],
        allowed_span_refs=c.value['allowed_span_refs'], context=c.source['context'], input_claims=c.value['input_claims']) == manual
    spec['task'] = '本地改动不能覆盖冻结任务'
    assert canonical_json([c.source, c.plan, c.previous, c.value]) == before


@pytest.mark.parametrize('fault', ['marker', 'claims', 'spans', 'policy', 'task', 'model_hash',
    'model_option', 'output_schema', 'reservation', 'response_capacity', 'note', 'request_hash', 'unsent', 'parent'])
def test_resealed_preparation_mutation_cannot_create_a_new_quote_or_source(fault):
    c = prepared()
    value = copy.deepcopy(c.value)
    if fault == 'marker': value['schema'] = old_preparation.PREPARATION
    elif fault == 'claims': value['payload']['input_claims'][0]['statement'] += '改写'
    elif fault == 'spans': value['payload']['spans'][0][1] += '改写'
    elif fault == 'policy': value['payload']['platform_policy']['roles']['business_agent'] = '改写'
    elif fault == 'task': value['generation_contract']['task'] = '另一项任务'
    elif fault == 'model_hash': value['generation_contract']['model_hash'] = 'f' * 64
    elif fault == 'model_option': value['generation_contract']['dispatch_options']['model'] = 'deepseek-flash'
    elif fault == 'output_schema': value['generation_contract']['output_schema']['additionalProperties'] = True
    elif fault == 'reservation': value['input_summary']['token_bound'] += 65536
    elif fault == 'response_capacity': value['output_capacity']['max_output_tokens'] = 131072
    elif fault == 'note': value['payload']['design_note'] = '另一条说明'
    elif fault == 'request_hash': value['request_hash'] = 'f' * 64
    elif fault == 'unsent': value['not_sent_global_positions'] = []
    else: value['parent_source'] = {}
    reseal(value)
    with pytest.raises(ValueError):
        subject.check_preparation(value, c.cap, c.source, c.plan, c.value['scope_id'], note=NOTE)


@pytest.mark.parametrize('fault', ['source', 'provider', 'model', 'declaration', 'note', 'capacity'])
def test_changed_source_cap_or_real_request_capacity_requires_a_new_preparation(fault, monkeypatch):
    c = prepared()
    context, cap, note = copy.deepcopy(c.source), copy.deepcopy(c.cap), NOTE
    before = canonical_json([c.source, c.plan, c.value])
    if fault == 'source': context['context']['independent_change'] = '当前来源不同'
    elif fault == 'provider': cap.data['execution']['provider'] = 'qwen'
    elif fault == 'model': cap.data['execution']['model'] = 'deepseek-flash'
    elif fault == 'declaration': cap.data['execution']['report_evidence_output']['schema'] = 'report-business-flow-output-v187'
    elif fault == 'note': note += '新说明'
    else: monkeypatch.setattr(subject, 'MAX_REQUEST_BYTES', len(c.rebuilt['request_body']) - 1)
    with pytest.raises(ValueError):
        subject.check_preparation(c.value, cap, context, c.plan, c.value['scope_id'], note=note)
    assert canonical_json([c.source, c.plan, c.value]) == before


def test_exact_nine_field_preview_start_nonce_consent_provider_and_registered_routes():
    c = prepared()
    p, body, value = preview(c)
    assert set(body.model_dump()) == {'plan_id', 'expected_plan_version', 'plan_hash', 'scope_id', 'revision',
        'model_id', 'note', 'activity_id', 'generation_profile'}
    family = consumer._body_family(body)
    assert family.id == profiles.FLOW_CAUSAL == 'flow-v190'
    assert profiles.selected(c.value).id == family.id and profiles.is_flow(family)
    assert consumer._preview_model(family) is consumer.PreviewFlowCausalBody
    assert consumer._start_model(family) is consumer.StartFlowCausalBody
    assert consumer._start_path(family) == consumer.PREFIX + '/start-flow-causal'
    shown = consumer.preview_view(consumer.check_preview(value, p))
    assert shown['provider'] == 'deepseek' and shown['model'] == native.MODEL
    assert shown['generation_profile'] == 'flow-v190' and shown['sent_payload'] == c.value['payload']
    assert shown['note'] == NOTE and shown['budget'] == value['budget'] and shown['call_count'] == 1
    assert all(shown[k] is False for k in consumer.FALSE_FLAGS)
    assert 'parent_attempt_id' not in shown
    start = {**body.model_dump(), 'request_id': body.activity_id, 'consent': True,
        'source_snapshot_hash': c.value['source_snapshot_hash'], 'preview_hash': value['preview_hash']}
    decoded, exact, sha = consumer.read_wire(raw(start), consumer.StartFlowCausalBody)
    assert decoded.request_id == decoded.activity_id and exact.encode('utf-8') == raw(start)
    assert sha == hashlib.sha256(raw(start)).hexdigest()
    for changed in ({**start, 'consent': 1}, {**start, 'request_id': identity(1909)},
            {**start, 'generation_profile': 'flow-v187'}, {**start, 'parent_attempt_id': identity(1851)}):
        with pytest.raises(HTTPException): consumer.read_wire(raw(changed), consumer.StartFlowCausalBody)
    routes = {r.path for r in consumer.router.routes}
    assert consumer.PREFIX + '/preview-flow-causal' in routes and consumer.PREFIX + '/start-flow-causal' in routes


@pytest.mark.parametrize('fault', ['cap_hash', 'model_id', 'workspace', 'profile', 'budget_tokens', 'call_limit', 'grant_hash'])
def test_resealed_preview_cannot_borrow_other_cap_identity_or_reservation(fault):
    c = prepared()
    p, _, value = preview(c)
    if fault == 'cap_hash': value['capability']['content_hash'] = 'f' * 64
    elif fault == 'model_id': value['body']['model_id'] = identity(1908)
    elif fault == 'workspace': value['capability']['workspace_id'] = identity(1907)
    elif fault == 'profile': value['generation_profile'] = 'flow-v187'
    elif fault == 'budget_tokens': value['budget']['token_bound'] += 65536
    elif fault == 'call_limit': value['budget']['call_limit'] = 2
    else: value['budget']['grant_hash'] = 'not-a-hash'
    value['preview_hash'] = consumer.h({k: v for k, v in value.items() if k != 'preview_hash'})
    with pytest.raises((HTTPException, ValueError)): consumer.check_preview(value, p)


def test_old187_preparation_and_body_remain_independent_without_decoder_relabelling():
    c = prepared()
    p, body, _ = preview(c)
    with pytest.raises(HTTPException): consumer.read_wire(raw(body.model_dump()), consumer.PreviewFlowReasonedBody)
    changed = {**body.model_dump(), 'generation_profile': 'flow-v187'}
    with pytest.raises(HTTPException): consumer.read_wire(raw(changed), consumer.PreviewFlowCausalBody)
    with pytest.raises(ValueError): old_preparation.check_preparation(c.value, c.cap, c.source, c.plan, c.value['scope_id'], note=NOTE)
    assert consumer._start_path(profiles.modules(profiles.FLOW_REASONED)) == consumer.PREFIX + '/start-flow-reasoned'
    assert profiles.modules(profiles.FLOW_CAUSAL).wire is wire


def test_actual_runtime_admit_prices_65536_once_with_explicit_synthetic_io_boundaries(monkeypatch):
    c = prepared()
    p, body, _ = preview(c)
    db = object()
    plan_row = NS(id=body.plan_id, version=1, producer_id=c.source['snapshot']['producer']['id'],
        data={'source_context': copy.deepcopy(c.source), 'plan': copy.deepcopy(c.plan)})
    calls = []

    def load(actual_db, actual_p, producer_id, revision, *, lock, current_attempt_id):
        assert actual_db is db and actual_p is p and producer_id == plan_row.producer_id
        assert revision == body.revision and lock is False and current_attempt_id is None
        calls.append('source'); return copy.deepcopy(c.source)

    def published(actual_db, actual_p, model_id, kind):
        assert actual_db is db and actual_p is p and model_id == c.cap.id and kind == 'foundation_model'
        calls.append('published'); return c.cap

    grant = {'schema': 'report-business-flow-activity-v187', 'activity_id': body.activity_id,
        'input_usd_per_million': 1.32, 'output_usd_per_million': 3.96,
        'maximum_cost_usd': 3, 'pricing_basis_hash': digest('synthetic-peak-no-discount')}

    def allowance(actual_p, producer_id, revision, model_id, activity_id):
        assert actual_p is p and producer_id == plan_row.producer_id and revision == body.revision
        assert model_id == c.cap.id and activity_id == body.activity_id
        calls.append('allowance'); return copy.deepcopy(grant)

    monkeypatch.setattr(runtime, 'load_source', load)
    monkeypatch.setattr(runtime, 'published', published)
    monkeypatch.setattr(allowance_adapter, 'allowance', allowance)
    monkeypatch.setattr(runtime, 'settings', lambda: NS(cloud_daily_token_limit=1000000))
    admitted = runtime.admit(db, p, plan_row, body)
    assert calls == ['source', 'published', 'allowance']
    assert admitted['preparation'] == c.value
    count = len(c.rebuilt['request_body']) + 1024
    expected = (Decimal(count) * Decimal('1.32') + Decimal(65536) * Decimal('3.96')) / Decimal(1000000)
    assert admitted['budget']['conservative_cost_usd'] == float(expected.quantize(Decimal('0.000001'), rounding=ROUND_CEILING))
    assert admitted['budget']['token_bound'] == count + 65536
    assert admitted['budget']['call_limit'] == 1
