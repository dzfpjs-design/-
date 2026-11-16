"""Actual V193 preparation and source preservation, public synthetic inputs.

The in-memory capability is not a real registration or spending grant. No DB,
provider, private report, semantic quality or current-source authority is proved.
"""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from generic_agents import report_business_flow_preparation_v193 as subject
from generic_agents import report_business_flow_native_v193 as native
from generic_agents import report_business_flow_wire_v193 as wire
from generic_agents import report_business_flow_preparation_v190 as previous
from generic_agents import report_business_flow_native_v190 as previous_native
from generic_agents import report_business_flow_revision_policy_v185 as platform
from generic_agents import report_scoped_design_profiles_v182 as profiles
from generic_agents import business_flow_nested_plan_v192 as plans
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_protocol_v184 import prepared as original_prepared, answer as plan_answer
from tests.test_report_business_flow_protocol_v187 import identity
from tests.test_report_business_flow_native_v193 import answer
from tests.test_report_business_flow_wire_v184 import no_external


NOTE = ' 保留完整选定范围；递归表达条件与并行＋🙂。 '


def raw(value):
    return canonical_json(value).encode('utf-8')


def prepared():
    """Public fixture consumed by source/API tests; no normal registration claim."""
    cap, source, plan, prior, _ = original_prepared()
    cap.data['execution'] = {'provider': native.PROVIDER, 'model': native.MODEL,
        'max_tokens': 2048, 'secret_ref': 'DEEPSEEK_API_KEY', 'report_evidence_output': {
            'schema': native.OUTPUT_DECLARATION, 'max_tokens': 65536,
            'reduce_transport': native.DELIVERY, 'endpoint': native.ENDPOINT}}
    cap.data.setdefault('data_policy', {})['cloud_allowed'] = True
    cap.id = identity(1933); cap.workspace_id = source['snapshot']['workspace_id']
    cap.content_hash = digest(cap.data); cap.status = 'published'
    value = subject.build_preparation(cap, source, plan, prior['scope_id'], note=NOTE)
    rebuilt = subject.rebuild_request(value, cap, source, plan, value['scope_id'], note=NOTE)
    return NS(cap=cap, source=source, plan=plan, previous=prior, value=value, rebuilt=rebuilt)


def authored(c):
    plan = plan_answer(c.value)
    plan['schema'] = plans.PLAN
    return answer(plan, c.value['scope_binding'])


def reseal(value):
    value['generation_contract_hash'] = digest(value['generation_contract'])
    value['payload_hash'] = digest(value['payload'])
    value['preparation_hash'] = digest({key: item for key, item in value.items() if key != 'preparation_hash'})
    return value


def test_same_source_scope_preserves_all_claims_full_spans_and_unsent_positions_without_growth():
    c = prepared(); old, value = c.previous, c.value
    assert value['scope_binding'] == old['scope_binding']
    assert value['input_claims'] == old['input_claims'] == value['payload']['input_claims']
    assert value['payload']['spans'] == old['payload']['spans']
    assert value['selected_global_positions'] == old['selected_global_positions'] == [0, 2]
    assert value['not_sent_global_positions'] == old['not_sent_global_positions'] == [1, 3]
    assert value['source_context'] == c.source and value['scope_plan'] == c.plan
    assert value['scope_binding']['global_input_count'] == 4 and len(value['input_claims']) == 2
    assert value['source_snapshot_hash'] == c.source['snapshot']['snapshot_hash']
    assert set(value['payload']) == {'schema', 'scope_binding', 'input_claims', 'spans', 'design_note', 'platform_policy'}
    assert value['payload']['schema'] == native.REQUEST_PAYLOAD_SCHEMA and value['payload']['design_note'] == NOTE
    assert not any(key in value['payload'] for key in ('parent_source', 'source_context', 'snapshot', 'findings'))
    sent = c.rebuilt['request_body'].decode('utf-8')
    assert 'GLOBAL_CONTEXT_NOT_FOR_THIS_MODEL' not in sent and 'GLOBAL_USER_INSTRUCTION_NOT_FOR_THIS_MODEL' not in sent
    assert value['partial_scope'] is True and value['call_count'] == 1
    assert all(value[key] is False for key in ('aggregate_verified', 'semantic_quality_verified',
        'consumer_authorized', 'can_apply', 'can_publish'))


def test_new_task_full_schema_policy_and_total_generation_are_bound_to_exact_request_bytes():
    c = prepared(); value, actual = c.value, c.rebuilt; spec = value['generation_contract']
    assert value['schema'] == subject.PREPARATION == 'report-business-flow-preparation-v193'
    assert spec['schema'] == subject.GENERATION == 'report-business-flow-generation-v193'
    assert spec['task'] == subject.TASK and spec['task_hash'] == digest(subject.TASK)
    assert spec['evidence_stage'] == native.STAGE and spec['delivery'] == native.DELIVERY and spec['endpoint'] == native.ENDPOINT
    assert spec['model_hash'] == digest(c.cap.data) and spec['output_schema'] == wire.output_schema(value['scope_binding'])
    assert spec['wire_result_schema'] == wire.WIRE and spec['projection_schema'] == wire.PROJECTION
    assert value['platform_policy_hash'] == spec['platform_policy_hash'] == digest(platform.platform_policy())
    assert value['payload']['platform_policy'] == platform.platform_policy()
    assert actual['request']['messages'][0]['content'] == native.system_prefix(native.STAGE) + native.SCHEMA_PROMPT_PREFIX + canonical_json(spec['output_schema'])
    task = json.loads(actual['request']['messages'][1]['content'])
    assert task['schema'] == subject.TASK_ID and task['task'] == subject.TASK
    assert task['platform_policy'] == platform.platform_policy()
    assert json.loads(actual['request']['messages'][-1]['content']) == value['payload']
    assert {key: item for key, item in actual['request'].items() if key != 'messages'} == spec['dispatch_options'] == {
        'model': 'deepseek-v4-pro', 'max_tokens': 65536, 'thinking': {'type': 'enabled'},
        'reasoning_effort': 'high', 'stream': False, 'response_format': {'type': 'json_object'}}
    assert actual['request_body'] == raw(actual['request'])
    assert actual['request_hash'] == value['request_hash'] == hashlib.sha256(actual['request_body']).hexdigest()
    assert value['messages_hash'] == digest(actual['request']['messages'])
    assert value['input_summary']['token_bound'] == len(actual['request_body']) + 65536 + 1024
    assert value['output_capacity']['max_output_tokens'] == 65536 and value['output_capacity']['fit_guaranteed'] is False
    assert value['output_capacity']['output_reservation'] == native.OUTPUT_RESERVATION
    for instruction in ('address', 'branch_index', 'parallel', '退回', '拒绝', '复核', '关闭', '3层控制', '专业设备'):
        assert instruction in subject.TASK
    assert subject.TASK != previous.TASK


def test_cold_check_rebuild_and_detached_spec_keep_current_source_and_exact_nested_schema():
    c = prepared(); before = canonical_json([c.source, c.plan, c.previous, c.value])
    assert subject.check_preparation(c.value, c.cap, c.source, c.plan, c.value['scope_id'], note=NOTE) == c.value
    assert subject.rebuild_request(c.value, c.cap, c.source, c.plan, c.value['scope_id'], note=NOTE) == c.rebuilt
    spec = subject.stage_spec(c.value)
    assert spec == c.value['generation_contract']
    assert profiles.selected(c.value).id == profiles.FLOW_NESTED
    authored_value = authored(c)
    assert wire.parse_plan(raw(authored_value), scope_binding=c.value['scope_binding']) == authored_value
    source_inputs = {'scope_binding': c.value['scope_binding'], 'catalog': c.source['catalog'],
        'allowed_span_refs': c.value['allowed_span_refs'], 'context': c.source['context'],
        'input_claims': c.value['input_claims']}
    projection = wire.build_projection(raw(authored_value), **source_inputs)
    assert projection['wire'] == authored_value['plan'] and projection['wire']['schema'] == plans.PLAN
    assert projection['raw_causal_plan'] == authored_value
    assert projection['causal_analysis']['declared_inputs_available'] is True
    assert projection['can_apply'] is projection['semantic_quality_verified'] is False
    manual = wire.new_manual(projection)
    assert manual['schema'] == wire.MANUAL and manual['result'] == authored_value['plan']
    assert manual['acknowledge_partial_scope'] is manual['acknowledge_inferences'] is False
    assert wire.validate_value(manual, **source_inputs) == manual
    spec['task'] = '返回副本不能改动已冻结任务'
    assert canonical_json([c.source, c.plan, c.previous, c.value]) == before


@pytest.mark.parametrize('fault', ['marker', 'claims', 'spans', 'policy', 'task', 'model_hash', 'model_option',
    'schema', 'reservation', 'response_capacity', 'note', 'request_hash', 'unsent', 'parent'])
def test_resealed_mutations_never_change_a_frozen_source_or_quote(fault):
    c = prepared(); value = copy.deepcopy(c.value)
    if fault == 'marker': value['schema'] = previous.PREPARATION
    elif fault == 'claims': value['payload']['input_claims'][0]['statement'] += '改写'
    elif fault == 'spans': value['payload']['spans'][0][1] += '改写'
    elif fault == 'policy': value['payload']['platform_policy']['roles']['business_agent'] = '改写'
    elif fault == 'task': value['generation_contract']['task'] = '另一项任务'
    elif fault == 'model_hash': value['generation_contract']['model_hash'] = 'f' * 64
    elif fault == 'model_option': value['generation_contract']['dispatch_options']['model'] = 'deepseek-flash'
    elif fault == 'schema': value['generation_contract']['output_schema']['additionalProperties'] = True
    elif fault == 'reservation': value['input_summary']['token_bound'] += 65536
    elif fault == 'response_capacity': value['output_capacity']['max_output_tokens'] = 131072
    elif fault == 'note': value['payload']['design_note'] = '另一条说明'
    elif fault == 'request_hash': value['request_hash'] = 'f' * 64
    elif fault == 'unsent': value['not_sent_global_positions'] = []
    else: value['parent_source'] = {}
    reseal(value)
    with pytest.raises(ValueError): subject.check_preparation(value, c.cap, c.source, c.plan, value['scope_id'], note=NOTE)


@pytest.mark.parametrize('fault', ['source', 'provider', 'model', 'declaration', 'note', 'capacity'])
def test_live_changes_need_a_new_preparation_and_never_clip_the_existing_request(fault, monkeypatch):
    c = prepared(); source, cap, note = copy.deepcopy(c.source), copy.deepcopy(c.cap), NOTE
    before = canonical_json([c.source, c.plan, c.value])
    if fault == 'source': source['context']['independent_change'] = '当前来源不同'
    elif fault == 'provider': cap.data['execution']['provider'] = 'qwen'
    elif fault == 'model': cap.data['execution']['model'] = 'deepseek-flash'
    elif fault == 'declaration': cap.data['execution']['report_evidence_output']['schema'] = previous_native.OUTPUT_DECLARATION
    elif fault == 'note': note += '新说明'
    else: monkeypatch.setattr(subject, 'MAX_REQUEST_BYTES', len(c.rebuilt['request_body']) - 1)
    with pytest.raises(ValueError): subject.check_preparation(c.value, cap, source, c.plan, c.value['scope_id'], note=note)
    assert canonical_json([c.source, c.plan, c.value]) == before


def test_190_preparation_and_registration_stay_independent_after_new_generation():
    c = prepared(); cap = copy.deepcopy(c.cap)
    cap.data['execution']['report_evidence_output'] = {'schema': previous_native.OUTPUT_DECLARATION,
        'max_tokens': 65536, 'reduce_transport': previous_native.DELIVERY, 'endpoint': previous_native.ENDPOINT}
    cap.content_hash = digest(cap.data)
    old = previous.build_preparation(cap, c.source, c.plan, c.value['scope_id'], note=NOTE)
    before = canonical_json(old)
    assert profiles.selected(old).id == profiles.FLOW_CAUSAL
    with pytest.raises(ValueError): subject.check_preparation(old, c.cap, c.source, c.plan, c.value['scope_id'], note=NOTE)
    with pytest.raises(ValueError): previous.check_preparation(c.value, cap, c.source, c.plan, c.value['scope_id'], note=NOTE)
    assert previous.check_preparation(old, cap, c.source, c.plan, c.value['scope_id'], note=NOTE) == old
    assert canonical_json(old) == before
