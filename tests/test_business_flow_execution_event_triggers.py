"""Public synthetic source fixtures; real converter and native validation only.

An event declaration never proves the separate signal route, graph handoff,
membership, automation or source permission.  These tests create no Task,
subscription, Job, device action or provider call.
"""
import copy

import pytest

from generic_agents import business_flow_execution_graph as subject
from generic_agents import full_semantic_graph_v1 as kernel
from tests.test_business_flow_execution_graph import bindings, codes, convert, no_io
from tests.test_report_business_flow_wire_v182 import scene


def event_scene():
    value, context = scene()
    value['business']['flows'][0]['trigger']['kind'] = 'event'
    supplied = bindings(value)
    supplied.update(schema=subject.BINDINGS_V2,
        trigger={'mode': subject.EVENT_TRIGGER_MODE})
    return value, context, supplied


def test_explicit_event_v2_lowers_without_creating_a_subscription_or_authority():
    value, context, supplied = event_scene()
    before = copy.deepcopy((value, context, supplied))
    result = convert(value, context, supplied)
    assert result['issues'] == [] and result['graph'] is not None
    assert result['graph'] == kernel.validate_graph(result['graph'])
    assert result['graph']['subscriptions'] == []
    assert set(supplied) == subject.BINDING_KEYS | {'trigger'}
    assert result['execution_bindings'] == supplied
    assert result['source_value'] == value
    assert all(result[key] is False for key in subject.FLAGS)
    assert (value, context, supplied) == before
    assert subject.check_execution_graph(result, value, result['flow_id'], supplied,
        scope_binding=context['scope_binding']) == result


def test_selected_event_does_not_discard_an_unselected_manual_flow_or_quotes():
    value, context, supplied = event_scene()
    other = copy.deepcopy(value['business']['flows'][0])
    other['flow_id'] = 'another_manual_flow'
    other['name'] = '另一个人工核对流程'
    other['trigger']['kind'] = 'manual'
    value['business']['flows'].append(other)
    result = convert(value, context, supplied)
    assert result['issues'] == [] and result['graph'] is not None
    assert result['source_value'] == value
    assert result['unselected_flow_ids'] == ['another_manual_flow']
    assert len(result['source_mapping']['flows']) == 2
    assert len(result['source_mapping']['evidence']) == len(value['evidence'])


def test_event_v1_remains_blocked_even_when_description_names_the_real_route():
    value, context, _ = event_scene()
    value['business']['flows'][0]['trigger']['description'] = (
        'signal_graph_anomaly_v134；异常事件独立路由，文字不代表已接通。')
    result = convert(value, context, bindings(value))
    assert result['graph'] is None
    assert 'trigger_not_connected' in codes(result)
    assert result['execution_bindings']['schema'] == subject.BINDINGS
    assert 'trigger' not in result['execution_bindings']


@pytest.mark.parametrize('kind', ['manual', 'schedule', 'data_arrival'])
def test_v2_cannot_relabel_other_source_trigger_kinds_as_anomaly_event(kind):
    value, context, supplied = event_scene()
    value['business']['flows'][0]['trigger']['kind'] = kind
    result = convert(value, context, supplied)
    assert result['graph'] is None and 'trigger_not_connected' in codes(result)
    assert result['source_value']['business']['flows'][0]['trigger']['kind'] == kind


@pytest.mark.parametrize('trigger', [
    None, {}, [], 'signal_graph_anomaly_v134',
    {'mode': None}, {'mode': True}, {'mode': []},
    {'mode': 'unknown'}, {'mode': 'signal_graph_anomaly_v134 '},
    {'mode': 'signal_graph_anomaly_v134', 'subscription_id': 'not_authority'},
])
def test_trigger_is_an_exact_closed_declaration_without_default_or_fallback(trigger):
    value, context, supplied = event_scene()
    supplied['trigger'] = copy.deepcopy(trigger)
    result = convert(value, context, supplied)
    assert result['graph'] is None
    assert codes(result).intersection({'binding_shape', 'trigger_mode_unknown'})
    assert result['execution_bindings']['trigger'] == trigger
    assert all(result[key] is False for key in subject.FLAGS)


@pytest.mark.parametrize('fault', ['missing_trigger', 'extra_field', 'v1_plus_trigger',
    'unknown_family'])
def test_new_and_old_binding_families_do_not_accept_each_others_fields(fault):
    value, context, supplied = event_scene()
    if fault == 'missing_trigger': del supplied['trigger']
    elif fault == 'extra_field': supplied['event_authorized'] = True
    elif fault == 'v1_plus_trigger': supplied['schema'] = subject.BINDINGS
    else: supplied['schema'] = 'business-flow-execution-bindings-unknown'
    result = convert(value, context, supplied)
    assert result['graph'] is None and 'binding_shape' in codes(result)


@pytest.mark.parametrize('tamper', ['trigger', 'source', 'subscriptions', 'flags'])
def test_resealing_event_translation_cannot_replace_deterministic_rebuild(tamper):
    value, context, supplied = event_scene()
    result = convert(value, context, supplied)
    assert result['issues'] == []
    if tamper == 'trigger': result['execution_bindings']['trigger']['mode'] = 'unknown'
    elif tamper == 'source': result['source_value']['business']['flows'][0]['trigger']['kind'] = 'manual'
    elif tamper == 'subscriptions': result['graph']['subscriptions'] = [{'authority': 'self_reported'}]
    else: result['can_execute'] = True
    result['translation_hash'] = subject._hash({key: child for key, child in result.items()
        if key != 'translation_hash'})
    with pytest.raises(ValueError, match='execution_graph_translation_mismatch'):
        subject.check_execution_graph(result, value, result['flow_id'], supplied,
            scope_binding=context['scope_binding'])


def test_v2_result_and_inputs_are_detached_in_both_directions():
    value, context, supplied = event_scene()
    result = convert(value, context, supplied)
    before = copy.deepcopy((value, context, supplied, result))
    result['execution_bindings']['trigger']['mode'] = 'local_unsaved_edit'
    result['source_value']['evidence'][0]['quote'] = 'local_unsaved_quote'
    assert (value, context, supplied) == before[:3]
    result = convert(value, context, supplied)
    supplied['trigger']['mode'] = 'changed_after_translation'
    value['business']['flows'][0]['trigger']['description'] = '另一个输入版本'
    assert result == before[3]


def test_v1_manual_translation_stays_ten_keys_and_is_unchanged_by_v2_translation():
    value, context = scene()
    supplied = bindings(value)
    before = copy.deepcopy((value, context, supplied))
    original = convert(value, context, supplied)
    event_value, event_context, event_bindings = event_scene()
    assert convert(event_value, event_context, event_bindings)['issues'] == []
    assert convert(value, context, supplied) == original
    assert (value, context, supplied) == before
    assert original['issues'] == [] and original['graph']['subscriptions'] == []
    assert set(original['execution_bindings']) == subject.BINDING_KEYS
    assert 'trigger' not in original['execution_bindings']
    assert subject.check_execution_graph(original, value, original['flow_id'], supplied,
        scope_binding=context['scope_binding']) == original
