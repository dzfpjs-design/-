"""Pure V187 preparation/native/consumer contracts over public V184 sources.

Selection, request/schema/hash, local plan projection and consumer body checks
are actual functions. The synthetic preview budget is not a real spending
grant. No DB, source ACL, publication, provider, capture or UX proof is claimed.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import report_business_flow_native_v187 as native
from generic_agents import report_business_flow_preparation_v187 as subject
from generic_agents import report_business_flow_preparation_v184 as old_preparation
from generic_agents import report_business_flow_preparation_v185 as revision_preparation
from generic_agents import report_business_flow_native_v184 as old_native
from generic_agents import report_business_flow_wire_v184 as wire
from generic_agents import report_business_flow_revision_policy_v185 as platform
from generic_agents import report_scoped_design_consumer_v181 as consumer
from generic_agents import report_scoped_design_profiles_v182 as profiles
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_protocol_v184 import no_io, prepared as original_prepared, answer
from tests.test_report_business_flow_protocol_v182 import response


NOTE = ' 依冻结原文进行独立规划＋🙂，保留未发送范围。 '


def raw(value):
    return canonical_json(value).encode('utf-8')


def identity(number):
    return str(UUID(int=number))


def prepared():
    cap, source, plan, previous, _ = original_prepared()
    cap.data['execution'] = {'provider': native.PROVIDER, 'model': native.MODEL, 'max_tokens': 2048,
        'report_evidence_output': {'schema': native.OUTPUT_DECLARATION, 'max_tokens': 65536,
            'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}}
    cap.id = identity(1873)
    cap.workspace_id = source['snapshot']['workspace_id']
    cap.content_hash = digest(cap.data)
    value = subject.build_preparation(cap, source, plan, previous['scope_id'], note=NOTE)
    rebuilt = subject.rebuild_request(value, cap, source, plan, previous['scope_id'], note=NOTE)
    return NS(cap=cap, source=source, plan=plan, previous=previous, value=value, rebuilt=rebuilt)


def preview(c):
    snapshot = c.source['snapshot']
    p = NS(user_id=snapshot['actor_id'], workspace_id=snapshot['workspace_id'])
    body = consumer.PreviewFlowReasonedBody(plan_id=identity(1871), expected_plan_version=1,
        plan_hash=c.value['plan_hash'], scope_id=c.value['scope_id'], revision=1,
        model_id=c.cap.id, note=NOTE, activity_id=identity(1872), generation_profile=profiles.FLOW_REASONED)
    budget = {'schema': 'report-scoped-design-budget-v181', 'call_limit': 1,
        'token_bound': c.value['input_summary']['token_bound'], 'conservative_cost_usd': 0.5,
        'maximum_cost_usd': 3, 'pricing_basis_hash': digest('public-synthetic-price'),
        'grant_hash': digest('public-synthetic-not-an-actual-spending-grant')}
    value = {'schema': consumer.PREVIEW, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'body': body.model_dump(), 'source_context': copy.deepcopy(c.source),
        'scope_plan': copy.deepcopy(c.plan), 'capability': consumer._cap_value(c.cap),
        'preparation': copy.deepcopy(c.value), 'budget': budget,
        'generation_profile': profiles.FLOW_REASONED, **consumer.FALSE_FLAGS}
    value['preview_hash'] = consumer.h(value)
    return p, body, value


def test_selection_is_exactly_the_original_scope_and_keeps_full_unsent_source_out_of_payload():
    c = prepared()
    previous, value = c.previous, c.value
    assert value['schema'] == subject.PREPARATION == 'report-business-flow-preparation-v187'
    assert value['scope_binding'] == previous['scope_binding']
    assert value['input_claims'] == previous['input_claims']
    assert value['payload']['spans'] == previous['payload']['spans']
    assert value['selected_global_positions'] == previous['selected_global_positions'] == [0, 2]
    assert value['not_sent_global_positions'] == previous['not_sent_global_positions'] == [1, 3]
    assert value['source_context'] == c.source and value['scope_plan'] == c.plan
    assert value['scope_binding']['global_input_count'] == 4 and len(value['input_claims']) == 2
    assert set(value['payload']) == {'schema', 'scope_binding', 'input_claims', 'spans', 'design_note', 'platform_policy'}
    assert value['payload']['schema'] == native.REQUEST_PAYLOAD_SCHEMA
    assert value['payload']['design_note'] == NOTE
    assert not any(key in value['payload'] for key in ('parent_source', 'source_context', 'context', 'findings', 'snapshot'))


def test_actual_request_contains_original_v184_schema_new_task_and_exact_policy_without_parent():
    c = prepared()
    spec, actual = c.value['generation_contract'], c.rebuilt
    assert spec['schema'] == subject.GENERATION == 'report-business-flow-generation-v187'
    assert spec['task'] == subject.TASK and spec['task_hash'] == digest(subject.TASK)
    assert spec['evidence_stage'] == native.STAGE and spec['delivery'] == native.DELIVERY
    assert spec['endpoint'] == native.ENDPOINT
    assert spec['output_schema'] == wire.output_schema(c.value['scope_binding'])
    assert spec['wire_result_schema'] == wire.WIRE == 'report-business-flow-plan-v184'
    assert spec['projection_schema'] == wire.PROJECTION
    assert spec['platform_policy_hash'] == c.value['platform_policy_hash'] == digest(platform.platform_policy())
    assert c.value['payload']['platform_policy'] == platform.platform_policy()
    first = actual['request']['messages'][0]['content']
    assert first == native.system_prefix(native.STAGE) + native.SCHEMA_PROMPT_PREFIX + canonical_json(spec['output_schema'])
    task = json.loads(actual['request']['messages'][1]['content'])
    assert task['schema'] == subject.TASK_ID and task['task'] == subject.TASK
    assert task['platform_policy'] == platform.platform_policy()
    assert json.loads(actual['request']['messages'][-1]['content']) == c.value['payload']
    options = {key: value for key, value in actual['request'].items() if key != 'messages'}
    assert options == spec['dispatch_options'] == {'model': native.MODEL, 'max_tokens': 65536,
        'thinking': {'type': 'enabled'}, 'reasoning_effort': 'high', 'stream': False,
        'response_format': {'type': 'json_object'}}
    assert actual['request_body'] == raw(actual['request'])
    assert actual['request_hash'] == c.value['request_hash'] == hashlib.sha256(actual['request_body']).hexdigest()
    assert c.value['messages_hash'] == digest(actual['request']['messages'])
    assert c.value['input_summary']['token_bound'] == len(actual['request_body']) + 65536 + 1024


def test_check_rebuild_stage_spec_and_real_v184_projection_use_the_same_frozen_inputs():
    c = prepared()
    original = copy.deepcopy([c.source, c.plan, c.previous, c.value])
    assert subject.check_preparation(c.value, c.cap, c.source, c.plan, c.value['scope_id'], note=NOTE) == c.value
    assert subject.rebuild_request(c.value, c.cap, c.source, c.plan, c.value['scope_id'], note=NOTE) == c.rebuilt
    spec = subject.stage_spec(c.value)
    assert spec == c.value['generation_contract']
    authored = answer(c.value)
    envelope = response(json.dumps(authored, ensure_ascii=False))
    parsed = native.parse_arguments(envelope, 200, native.STAGE, response_complete=True,
        http_content_encoding=None, scope_binding=c.value['scope_binding'])
    assert parsed == authored
    projection = wire.build_projection(raw(parsed), scope_binding=c.value['scope_binding'],
        catalog=c.source['catalog'], allowed_span_refs=c.value['allowed_span_refs'],
        context=c.source['context'], input_claims=c.value['input_claims'])
    assert projection['raw_plan'] == authored and projection['wire']['schema'] == 'report-business-flow-wire-v182'
    assert projection['can_apply'] is projection['can_execute'] is False
    spec['task'] = 'local mutation'
    assert [c.source, c.plan, c.previous, c.value] == original


@pytest.mark.parametrize('fault', ['claim', 'span', 'policy', 'task', 'options', 'schema',
    'note', 'positions', 'extra_parent'])
def test_resealed_preparation_mutation_is_not_a_new_authority_or_silent_input_repair(fault):
    c = prepared()
    value = copy.deepcopy(c.value)
    if fault == 'claim': value['payload']['input_claims'][0]['statement'] += '改写'
    elif fault == 'span': value['payload']['spans'][0][1] += '改写'
    elif fault == 'policy': value['payload']['platform_policy']['roles']['data_agent'] = '改写政策'
    elif fault == 'task': value['generation_contract']['task'] = '另一个任务'
    elif fault == 'options': value['generation_contract']['dispatch_options']['thinking']['type'] = 'disabled'
    elif fault == 'schema': value['generation_contract']['output_schema']['additionalProperties'] = True
    elif fault == 'note': value['payload']['design_note'] = '另一个说明'
    elif fault == 'positions': value['selected_global_positions'].reverse()
    else: value['parent_source'] = {}
    value['generation_contract_hash'] = digest(value['generation_contract'])
    value['payload_hash'] = digest(value['payload'])
    value['preparation_hash'] = digest({key: item for key, item in value.items() if key != 'preparation_hash'})
    with pytest.raises(ValueError):
        subject.check_preparation(value, c.cap, c.source, c.plan, c.value['scope_id'], note=NOTE)


def test_changed_independent_source_or_capacity_refuses_without_changing_claims_or_clipping_request(monkeypatch):
    c = prepared()
    before = copy.deepcopy([c.source, c.plan, c.previous])
    changed_source = copy.deepcopy(c.source)
    changed_source['context']['unexpected'] = '独立当前来源已变化'
    with pytest.raises(ValueError):
        subject.check_preparation(c.value, c.cap, changed_source, c.plan, c.value['scope_id'], note=NOTE)
    with pytest.raises(ValueError):
        subject.check_preparation(c.value, c.cap, c.source, c.plan, c.value['scope_id'], note=NOTE + '新说明')
    monkeypatch.setattr(subject, 'MAX_REQUEST_BYTES', len(c.rebuilt['request_body']) - 1)
    with pytest.raises(ValueError):
        subject.build_preparation(c.cap, c.source, c.plan, c.value['scope_id'], note=NOTE)
    assert [c.source, c.plan, c.previous] == before


def test_reasoned_consumer_preview_and_start_body_keep_exact_activity_consent_and_new_path():
    c = prepared()
    p, body, value = preview(c)
    checked = consumer.check_preview(value, p)
    shown = consumer.preview_view(checked)
    family = consumer._body_family(body)
    assert family.id == profiles.FLOW_REASONED == 'flow-v187'
    assert profiles.selected(c.value).id == family.id
    assert consumer._preview_model(family) is consumer.PreviewFlowReasonedBody
    assert consumer._start_model(family) is consumer.StartFlowReasonedBody
    assert consumer._start_path(family) == consumer.PREFIX + '/start-flow-reasoned'
    assert shown['provider'] == native.PROVIDER == 'deepseek' and shown['model'] == native.MODEL
    assert shown['generation_profile'] == family.id and 'parent_attempt_id' not in shown
    assert shown['sent_payload'] == c.value['payload'] and shown['claim_count'] == 4
    assert shown['budget'] == value['budget'] and shown['call_count'] == 1
    assert all(shown[key] is False for key in consumer.FALSE_FLAGS)
    start = {**body.model_dump(), 'request_id': body.activity_id, 'consent': True,
        'source_snapshot_hash': c.value['source_snapshot_hash'], 'preview_hash': value['preview_hash']}
    decoded, exact, sha = consumer.read_wire(raw(start), consumer.StartFlowReasonedBody)
    assert decoded.request_id == decoded.activity_id
    assert exact.encode('utf-8') == raw(start) and sha == hashlib.sha256(raw(start)).hexdigest()
    for changed in ({**start, 'consent': 1}, {**start, 'request_id': identity(1879)},
            {**start, 'parent_attempt_id': identity(1851)}):
        with pytest.raises(HTTPException):
            consumer.read_wire(raw(changed), consumer.StartFlowReasonedBody)


@pytest.mark.parametrize('profile,model', [('flow-v184', consumer.PreviewFlowPlanBody),
    ('flow-v185', consumer.PreviewFlowRevisionBody)])
def test_reasoned_profile_cannot_borrow_old_preparation_or_revision_parent_protocol(profile, model):
    c = prepared()
    _, body, value = preview(c)
    with pytest.raises(HTTPException):
        consumer.read_wire(raw(body.model_dump()), model)
    changed = {**body.model_dump(), 'generation_profile': profile}
    with pytest.raises(HTTPException):
        consumer.read_wire(raw(changed), consumer.PreviewFlowReasonedBody)
    with pytest.raises(ValueError):
        old_preparation.check_preparation(c.value, c.cap, c.source, c.plan, c.value['scope_id'], note=NOTE)
    with pytest.raises(ValueError):
        revision_preparation.check_preparation(c.value, c.cap, c.source, c.plan, c.value['scope_id'], note=NOTE)
    with pytest.raises(ValueError):
        subject.check_preparation(c.previous, c.cap, c.source, c.plan, c.value['scope_id'], note=NOTE)
    old_family = profiles.modules(profiles.FLOW_PLAN)
    revision_family = profiles.modules(profiles.FLOW_REVISION)
    assert consumer._start_path(old_family) == consumer.PREFIX + '/start-flow-plan'
    assert consumer._start_path(revision_family) == consumer.PREFIX + '/start-flow-revision'
    assert old_family.native.MODEL == old_native.MODEL and old_family.native.STAGE == old_native.STAGE
    assert value['preparation']['schema'] == subject.PREPARATION
