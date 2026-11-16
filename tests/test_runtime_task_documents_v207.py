"""Pure contract/adversarial tests; no successful business runtime is fabricated."""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import runtime_task_documents as sources
from generic_agents import workflow_document_packet_v207 as packet
from generic_agents import workflow_document_packet_wait_v207 as waiting
from generic_agents import workflow_document_task_integrity_v207 as integrity
from generic_agents.contracts import Manifest, digest
from generic_agents.typed_payload_codec_v131 import canonical_json

ID = '11111111-1111-1111-1111-111111111111'
OTHER = '22222222-2222-2222-2222-222222222222'
HASH = 'a' * 64


def selector():
    return {'kind': 'native', 'version_id': ID, 'attempt_id': OTHER, 'parse_hash': HASH,
        'block_id': 'b1', 'char_span': {'start': 1, 'end': 3}}


def body():
    return {'request_id': ID, 'expected_task_version': 1, 'expected_approval_version': 1,
        'prior_set_id': None, 'prior_set_hash': None, 'input_key': 'document_sources', 'selectors': [selector()]}


def manifest():
    return Manifest(kind='tool', namespace='runtime_documents_v207', name='selected_document_packet',
        version='1.0.0', owner='公开测试', description='只从明确批准的原摘录制作本地资料包',
        allowed_agent_roles=['data'], input_schema=packet.input_schema(), output_schema=packet.output_schema(),
        execution={'adapter': packet.ADAPTER, 'side_effect': True}, dependencies=[],
        data_policy={'cloud_allowed': False}, budget={'max_attempts': 1, 'max_concurrency': 1},
        specification={'schema': 'workflow-document-packet-contract-v207',
            'operation': 'build_approved_document_packet'}).model_dump(mode='json')


def graph_and_cap():
    data = manifest(); cap = NS(id=ID, kind='tool', data=data, content_hash=digest(data),
        validated_hash=digest(data), release_version='1.0.0', status='published')
    graph = {'input_schema': {'type': 'object', 'properties': {'document_sources': sources.ref_schema(),
        'question': {'type': 'string'}}, 'required': ['question', 'document_sources'], 'additionalProperties': False},
        'references': {'selected': {'kind': 'object', 'nullable': False, 'source': 'input',
            'field': 'document_sources', 'node_id': None}},
        'nodes': [{'id': 'producer', 'kind': 'ability', 'actor_id': OTHER, 'execution': {
            'adapter': packet.ADAPTER, 'capability_id': ID, 'capability_hash': cap.content_hash,
            'release_version': cap.release_version, 'agent_role': 'data', 'max_attempts': 1, 'input_schema': packet.input_schema(),
            'output_schema': packet.output_schema(), 'input_bindings': {'source_set_ref': 'selected'}, 'arguments': {}}}]}
    return graph, cap


def test_source_schema_defer_is_explicit_absence_only_and_does_not_mutate_published_schema():
    graph, _ = graph_and_cap(); before = copy.deepcopy(graph)
    deferred = sources.creation_schema(graph['input_schema'])
    assert deferred['required'] == ['question'] and graph == before
    assert deferred['properties'] == before['input_schema']['properties']
    plain = {'type': 'object', 'properties': {'question': {'type': 'string'}}, 'required': ['question']}
    assert sources.creation_schema(plain) == plain
    graph['input_schema']['properties']['document_sources']['additionalProperties'] = True
    with pytest.raises(HTTPException): sources.creation_schema(graph['input_schema'])


@pytest.mark.parametrize('change', ['bool_cas', 'string_cas', 'empty', 'too_many', 'extra_text', 'wrong_span', 'large_span', 'half_parent'])
def test_selection_request_rejects_ambiguous_authority_and_caller_text(change):
    value = body()
    if change == 'bool_cas': value['expected_task_version'] = True
    elif change == 'string_cas': value['expected_approval_version'] = '1'
    elif change == 'empty': value['selectors'] = []
    elif change == 'too_many': value['selectors'] *= 21
    elif change == 'extra_text': value['selectors'][0]['text'] = '调用者自填正文'
    elif change == 'wrong_span': value['selectors'][0]['char_span'] = {'start': 2, 'end': 1}
    elif change == 'large_span': value['selectors'][0]['char_span'] = {'start': 0, 'end': 1201}
    elif change == 'half_parent': value['prior_set_id'] = OTHER
    with pytest.raises(ValidationError): sources.CreateSourceSet.model_validate(value)


def test_typed_original_canonical_body_preserves_selector_order_and_unicode():
    value = body(); value['selectors'].append({**selector(), 'block_id': 'b2'})
    typed = sources.CreateSourceSet.model_validate(value).model_dump(mode='json')
    assert typed == value and 'document_sources' in canonical_json(typed)
    changed = copy.deepcopy(typed); changed['selectors'].reverse()
    assert digest(typed) != digest(changed)
    assert sources.Span(start=1, end=3).model_dump() == {'start': 1, 'end': 3}


@pytest.mark.parametrize('fault', ['unwired', 'literal', 'different_input', 'node_output', 'nullable', 'wrong_kind',
    'unpublished', 'cap_hash', 'schema_drift', 'wrong_role', 'unannotated_producer'])
def test_declared_port_requires_actual_registered_direct_typed_producer(monkeypatch, fault):
    graph, cap = graph_and_cap(); monkeypatch.setattr(sources, 'scoped', lambda *a, **k: cap)
    node = graph['nodes'][0]; ref = graph['references']['selected']
    if fault == 'unwired': graph['nodes'] = []
    elif fault == 'literal': node['execution']['arguments']['source_set_ref'] = {'source_set_id': ID, 'source_set_hash': HASH}
    elif fault == 'different_input': ref['field'] = 'question'
    elif fault == 'node_output': ref.update(source='output', node_id='other')
    elif fault == 'nullable': ref['nullable'] = True
    elif fault == 'wrong_kind': ref['kind'] = 'string'
    elif fault == 'unpublished': cap.status = 'draft'
    elif fault == 'cap_hash': cap.validated_hash = '0' * 64
    elif fault == 'schema_drift': node['execution']['output_schema'] = {'type': 'object'}
    elif fault == 'wrong_role': node['execution']['agent_role'] = 'business'
    elif fault == 'unannotated_producer': graph['input_schema']['properties'].pop('document_sources')
    with pytest.raises(HTTPException): sources.verify_graph_sources(None, None, graph)


def test_historical_graph_checks_keep_original_pin_without_granting_current_publication(monkeypatch):
    graph, cap = graph_and_cap(); monkeypatch.setattr(sources, 'scoped', lambda *a, **k: cap)
    actual = sources.verify_graph_sources(None, None, graph)
    assert actual['document_sources'][0]['capability_hash'] == digest(cap.data)
    cap.status = 'retired'
    assert sources.verify_graph_sources(None, None, graph, current=False) == actual
    with pytest.raises(HTTPException): sources.verify_graph_sources(None, None, graph)


def entry(text='甲😀乙'):
    return {'source_name': '公开资料', 'version_sequence': 2, 'location_label': '第3页·识别区域1',
        'partial': True, 'warnings': ['仅所选识别区域，未经人工核对'], 'text': text}


def test_packet_text_keeps_original_unicode_and_uncertainty_and_never_silently_truncates():
    raw = sources.render([entry()]); text = raw.decode('utf-8')
    assert '甲😀乙' in text and '第2版' in text and '第3页' in text and '未经' in text
    assert '不是事实确认' in text and '云端授权' in text
    with pytest.raises(HTTPException): sources.render([entry('中' * 10000)])


@pytest.mark.parametrize('fault', ['cloud', 'wrong_role', 'many_attempts', 'bool_attempts', 'float_attempts', 'dependency', 'new_output', 'side_effect'])
def test_new_packet_capability_is_local_closed_contract_not_old199_or_cloud_consent(fault):
    value = manifest()
    if fault == 'cloud': value['data_policy']['cloud_allowed'] = True
    elif fault == 'wrong_role': value['allowed_agent_roles'] = ['business']
    elif fault == 'many_attempts': value['budget']['max_attempts'] = 2
    elif fault == 'bool_attempts': value['budget']['max_attempts'] = True
    elif fault == 'float_attempts': value['budget']['max_attempts'] = 1.0
    elif fault == 'dependency': value['dependencies'] = [OTHER]
    elif fault == 'new_output': value['output_schema']['properties']['cloud_authorized']['const'] = True
    elif fault == 'side_effect': value['execution']['side_effect'] = False
    with pytest.raises(HTTPException): packet.validate_manifest(value)


def test_mixed_evidence_requires_at_least_one_packet_and_never_qualifies_generic_file_as_packet(monkeypatch):
    task = NS(id=ID); p = NS(workspace_id=OTHER)
    row = NS(task_id=ID, source_set_id=ID, data={'selected': {'source_set_hash': HASH}})
    selected = row.data['selected']
    class DB:
        def scalars(self, _): return [row]
    monkeypatch.setattr(packet, 'declared_sources', lambda *a: True)
    monkeypatch.setattr(packet, 'is_family_evidence', lambda *a: a[-1] == 'packet')
    monkeypatch.setattr(packet, 'approved_set', lambda *a: (None, selected))
    assert packet.selected_packets(DB(), p, task, ['ordinary', 'packet']) == [row]
    with pytest.raises(HTTPException): packet.selected_packets(DB(), p, task, ['ordinary'])
    row.task_id = OTHER
    with pytest.raises(HTTPException): packet.selected_packets(DB(), p, task, ['packet'])


def test_new_wait_does_not_touch_legacy_ledger_when_no_family(monkeypatch):
    monkeypatch.setattr(waiting.reasoning, '_inputs', lambda value: value)
    monkeypatch.setattr(packet, 'uses_family', lambda *a: False)
    monkeypatch.setattr(waiting.original, '_locked_preflight', lambda *a: pytest.fail('legacy must be delegated untouched'))
    cap = NS(data={'execution': {'adapter': 'business_reasoning'}})
    assert waiting.preflight(None, None, None, None, None, cap, {'evidence_ids': [ID]}) is None


def test_outcome_integrity_old_graph_is_exact_noop_and_completed_family_never_uses_status_only(monkeypatch):
    integrity.verify_task_results(None, None, None, {'input_schema': {'properties': {}}, 'nodes': []})
    graph, _ = graph_and_cap(); state = {'activations': {'a': {'id': 'a', 'node_id': 'producer',
        'status': 'completed', 'output': {'evidence_ids': [ID]}}}}
    task = NS(id=ID, data={'graph_runtime': state, 'graph_jobs': {}})
    monkeypatch.setattr(sources, 'task_context', lambda *a: (None, graph, {}))
    from generic_agents import full_semantic_graph_v1 as pure, workflow_graph_scope as scope
    monkeypatch.setattr(pure, 'check_run', lambda *a: state)
    monkeypatch.setattr(scope, '_frozen', lambda *a: (task, None, None, graph, state,
        state['activations']['a'], graph['nodes'][0]))
    with pytest.raises(HTTPException): integrity.verify_task_results(None, None, task, graph)
