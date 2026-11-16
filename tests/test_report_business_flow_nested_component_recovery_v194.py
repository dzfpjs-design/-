"""Recover only a source-valid nested plan beside an invalid V193 answer.

Real V192/V193 shape, source and availability gates use public synthetic data.
No model, capture authenticity, IAM, durable recovery or execution is proved.
The original paid decoder is not replaced. Root executes these tests.
"""
import copy
import hashlib
import json

import pytest

from generic_agents import report_business_flow_nested_component_recovery_v194 as subject
from generic_agents import report_business_flow_wire_v193 as original
from generic_agents import business_flow_nested_plan_v192 as plans
from generic_agents import business_flow_nested_causal_v192 as causal
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_wire_v193 import fixture as original_fixture
from tests.test_report_business_flow_wire_v184 import no_external


def raw(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


def valid_plan_invalid_causality(fault='unit'):
    """Public Root fixture: (original V193 value, independent source kwargs).

    Faults affect only original causality. They do not change the V192 plan,
    source scope, claims, attributions or underlying synthetic original text.
    """
    value, source = original_fixture(nested=True)
    target = max(value['causality']['input_bindings'], key=lambda item: len(item['action']['address']))
    if fault == 'unit': target['contract']['unit'] = '°C'
    elif fault == 'type': target['contract']['data_type'] = 'numeric_metric'
    elif fault == 'unknown_selector': target['action']['address'][-1]['phase_index'] = 999
    elif fault == 'unknown_ordinal': target['ordinal'] = 999
    elif fault == 'unknown_entry': target['sources'] = [{'kind': 'entry', 'index': 999}]
    elif fault == 'missing_input': value['causality']['input_bindings'].pop()
    elif fault == 'future':
        completion = next(item for item in value['causality']['output_contracts']
            if item['action']['part'] == 'completion')
        value['causality']['input_bindings'][0]['sources'] = [{'kind': 'output',
            'action': copy.deepcopy(completion['action']), 'ordinal': completion['ordinal']}]
    else: raise ValueError('unknown synthetic fault')
    return value, source


def build(value, source):
    return subject.build_component(raw(value), **source)


def reseal(value):
    value['component_hash'] = digest({key: item for key, item in value.items() if key != 'component_hash'})
    return value


def test_exact_original_bytes_and_plan_are_preserved_as_an_independent_incomplete_draft():
    value, source = valid_plan_invalid_causality()
    before = canonical_json([value, source])
    actual = b'\n' + json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8') + b'\n'
    result = subject.build_component(actual, **source)
    assert result['schema'] == subject.SCHEMA == 'report-business-flow-nested-component-recovery-v194'
    assert result['component_path'] == '/plan' and result['component_value_schema'] == plans.PLAN
    assert result['original_wire_schema'] == original.WIRE and result['wire'] == value['plan']
    assert result['component_value_hash'] == digest(value['plan'])
    assert result['original_raw_sha256'] == hashlib.sha256(actual).hexdigest()
    assert result['original_raw_bytes'] == len(actual) and result['original_value_hash'] == digest(value)
    assert result['scope_binding'] == source['scope_binding'] and result['scope_binding_hash'] == digest(source['scope_binding'])
    assert result['selected_input_count'] == len(source['input_claims'])
    assert result['selected_claims_hash'] == digest(source['input_claims'])
    assert result['source_inputs_hash'] == digest(source)
    assert result['component_hash'] == digest({key: item for key, item in result.items() if key != 'component_hash'})
    assert result['status'] == 'incomplete' and result['original_protocol_valid'] is False
    assert result['component_only'] is result['partial_scope'] is True
    assert all(result[key] is False for key in subject._FALSE_FLAGS)
    assert not any(key in result for key in ('projection', 'causal_analysis', 'compiled_sidecar', 'causality', 'raw_exception'))
    assert subject.check_component(result, actual, **source) == result
    assert canonical_json([value, source]) == before
    result['wire']['business']['name'] = '只修改返回副本'
    result['scope_binding']['name'] = '只修改返回绑定副本'
    assert canonical_json([value, source]) == before


@pytest.mark.parametrize('fault', ['unit', 'type', 'unknown_selector', 'unknown_ordinal',
    'unknown_entry', 'missing_input', 'future'])
def test_only_plan_is_recoverable_after_real_whole_protocol_failure(fault):
    value, source = valid_plan_invalid_causality(fault)
    before = canonical_json([value, source])
    assert plans.check_plan(value['plan'], scope_binding=source['scope_binding']) == value['plan']
    assert original.validate_wire_value(value['plan'], **source) == value['plan']
    checked = original.BusinessFlowNestedCausalPlanV193.model_validate(value).model_dump()
    assert canonical_json(checked) == canonical_json(value)
    with pytest.raises(ValueError): original.build_projection(raw(value), **source)
    result = build(value, source)
    assert result['wire'] == value['plan'] and result['original_protocol_valid'] is False
    assert result['failure_code'] == 'original_protocol_validation_failed'
    assert result['issues'][0] == {'code': result['failure_code'], 'path': '/causality'}
    inputs = plans.walk_action_ports(value['plan'], scope_binding=source['scope_binding'])['input_paths']
    for issue in result['issues'][1:]:
        assert set(issue) == {'code', 'path'} and subject._ISSUE_CODE.fullmatch(issue['code'])
        assert issue['path'] in inputs
    assert canonical_json([value, source]) == before
    with pytest.raises(ValueError): original.build_projection(raw(value), **source)


def test_nested_unit_failure_exposes_only_the_real_input_pointer_for_human_navigation():
    value, source = valid_plan_invalid_causality('unit')
    result = build(value, source)
    issue = result['issues'][1]
    assert issue['code'] == 'source_contract_mismatch'
    assert issue['path'].endswith('/inputs/0') and issue['path'].count('/branches/') == 3
    assert set(issue) == {'code', 'path'}
    assert '°C' not in canonical_json(result['issues'])


def test_future_only_failure_is_not_mistaken_for_a_parse_failure_or_a_causal_pass():
    value, source = valid_plan_invalid_causality('future')
    assert original.parse_plan(raw(value), scope_binding=source['scope_binding']) == value
    with pytest.raises(causal.CausalAvailabilityError):
        original.build_projection(raw(value), **source)
    result = build(value, source)
    assert result['issues'][1]['code'] == 'source_not_available'
    assert result['issues'][1]['path'] == '/business/flows/0/phases/0/steps/0/inputs/0'
    assert result['causal_declaration_consistency_only'] is False


def test_a_whole_valid_original_must_use_its_normal_family_instead_of_component_recovery():
    value, source = original_fixture(nested=True)
    projected = original.build_projection(raw(value), **source)
    assert projected['schema'] == original.PROJECTION
    with pytest.raises(ValueError, match=subject.ERROR): build(value, source)
    assert original.build_projection(raw(value), **source) == projected


@pytest.mark.parametrize('fault', ['unknown_ref', 'original_text', 'claims', 'context', 'allowed_refs'])
def test_source_failures_are_fatal_before_any_whole_failure_can_enable_recovery(fault, monkeypatch):
    value, source = valid_plan_invalid_causality()
    if fault == 'unknown_ref':
        value['plan']['business']['flows'][0]['phase_order']['citations'] = ['w:p999999/s1']
    elif fault == 'original_text':
        ref = source['allowed_span_refs'][0]
        source['catalog']['index'][ref]['text'] += '改写合成原文'
    elif fault == 'claims': source['input_claims'][0]['statement'] += '改写合成声明'
    elif fault == 'context': source['context']['unexpected_source_context'] = '不属于冻结来源'
    else: source['allowed_span_refs'] = source['allowed_span_refs'][1:]
    def forbidden(*args, **kwargs):
        pytest.fail('independent source gate must reject before whole-failure eligibility')
    monkeypatch.setattr(original, 'build_projection', forbidden)
    with pytest.raises(ValueError, match=subject.ERROR): build(value, source)


@pytest.mark.parametrize('fault', ['outer_extra', 'old_outer', 'plan_extra_quote', 'plan_binding',
    'plan_name', 'empty_outputs', 'selector_bool', 'nonstring_unit'])
def test_malformed_original_shape_cannot_be_salvaged_by_discarding_invalid_fields(fault):
    value, source = valid_plan_invalid_causality()
    if fault == 'outer_extra': value['private_note'] = '不属于原输出合同'
    elif fault == 'old_outer': value['schema'] = 'report-business-flow-causal-plan-v190'
    elif fault == 'plan_extra_quote': value['plan']['quote'] = '禁止补造或容忍模型原文quote'
    elif fault == 'plan_binding': value['plan']['binding_hash'] = 'e' * 64
    elif fault == 'plan_name': value['plan']['business']['name'] = '另一业务'
    elif fault == 'empty_outputs': value['plan']['business']['flows'][0]['completion']['outputs'] = []
    elif fault == 'selector_bool': value['causality']['input_bindings'][0]['ordinal'] = True
    else: value['causality']['entry_ports'][0]['contract']['unit'] = 1
    before = canonical_json([value, source])
    with pytest.raises(ValueError): build(value, source)
    assert canonical_json([value, source]) == before


@pytest.mark.parametrize('bad', [b'', b'[]', b'null', b'\xff', b'{"schema":1,"schema":2}',
    b'{"value":NaN}', b'{"value":Infinity}', bytearray(b'{}'), '{}'])
def test_raw_wire_is_strict_bytes_unique_finite_json_with_no_partial_parse(bad):
    _, source = valid_plan_invalid_causality()
    with pytest.raises(ValueError): subject.build_component(bad, **source)


def test_raw_one_mib_limit_includes_original_whitespace_and_never_truncates():
    value, source = valid_plan_invalid_causality()
    content = raw(value)
    maximum = original.MAX_WIRE_BYTES
    exact = content + b' ' * (maximum - len(content))
    result = subject.build_component(exact, **source)
    assert result['original_raw_bytes'] == maximum
    assert result['original_raw_sha256'] == hashlib.sha256(exact).hexdigest()
    with pytest.raises(ValueError): subject.build_component(exact + b' ', **source)


def test_recursive_capacity_is_checked_before_outer_pydantic(monkeypatch):
    value, source = valid_plan_invalid_causality()
    flow = value['plan']['business']['flows'][0]
    wrapper = copy.deepcopy(flow['phases'][1])
    wrapper['branches'][0]['phases'] = copy.deepcopy(flow['phases'])
    wrapper['branches'][1]['phases'] = []
    flow['phases'] = [wrapper]
    def forbidden(*args, **kwargs): pytest.fail('complete V192 preflight must precede outer recursive validation')
    monkeypatch.setattr(original.BusinessFlowNestedCausalPlanV193, 'model_validate', forbidden)
    with pytest.raises(ValueError): build(value, source)


@pytest.mark.parametrize('fault', ['wire', 'raw_hash', 'claims_hash', 'source_hash', 'status', 'authority', 'extra'])
def test_resealed_dto_tampering_never_replaces_cold_original_and_source_reconstruction(fault):
    value, source = valid_plan_invalid_causality(); result = build(value, source)
    if fault == 'wire':
        result['wire']['business']['flows'][0]['name'] = '改写独立稿'
        result['component_value_hash'] = digest(result['wire'])
    elif fault == 'raw_hash': result['original_raw_sha256'] = 'e' * 64
    elif fault == 'claims_hash': result['selected_claims_hash'] = 'e' * 64
    elif fault == 'source_hash': result['source_inputs_hash'] = 'e' * 64
    elif fault == 'status': result['status'] = 'reviewed'
    elif fault == 'authority': result['can_apply'] = True
    else: result['raw_exception'] = '不应输出任何内部异常'
    reseal(result)
    with pytest.raises(ValueError): subject.check_component(result, raw(value), **source)


@pytest.mark.parametrize('code,path_kind', [('unsafe-code:private', 'real'), ('source_contract_mismatch', 'wrong'),
    ('a' * 81, 'real')])
def test_diagnostic_metadata_cannot_expose_exception_values_or_invent_navigation(code, path_kind, monkeypatch):
    value, source = valid_plan_invalid_causality()
    path = plans.walk_action_ports(value['plan'], scope_binding=source['scope_binding'])['input_paths'][0]
    if path_kind == 'wrong': path = '/causality/private_secret/0'
    def rejected(*args, **kwargs): raise causal.CausalAvailabilityError(code, path)
    # This spy only exercises diagnostic redaction after the independent source
    # gate. No test using it claims the source of a real original answer.
    monkeypatch.setattr(original, '_sidecar', rejected)
    result = build(value, source)
    assert result['issues'] == [{'code': subject.FAILURE_CODE, 'path': '/causality'}]
    assert code not in canonical_json(result['issues'])


def test_foreign_runtime_error_cannot_be_silently_promoted_to_recoverable_original_failure(monkeypatch):
    value, source = valid_plan_invalid_causality()
    def broken(*args, **kwargs): raise RuntimeError('synthetic unexpected implementation failure')
    monkeypatch.setattr(original, 'build_projection', broken)
    with pytest.raises(RuntimeError): build(value, source)
