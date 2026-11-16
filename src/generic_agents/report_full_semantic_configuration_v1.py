"""Lossless, non-executable configuration designs from caller-verified manual5.

The caller authenticates the current manual receipt, complete source semantics
and sharing consent. This pure module cannot establish any of those facts.
manual_value_hash is opaque here: it includes a private note which is NEVER
accepted or copied. Self-consistent hashes are not external ledger authority.
"""
import copy
import json
import re
from uuid import UUID, uuid5

from .contracts import digest
from .report_evidence_wire_v3 import CandidateWireV5, candidate_with_ids
from .report_full_reading_semantics import _safe


SCHEMA = 'report-full-semantic-design-v1'
FLOW_REF = 'report-full-semantic-flow-ref-v1'
SIGNATURE = 'report-full-semantic-signature-context-v1'
POLICY = 'requires_full_semantic_runtime_v1'
BLOCKED_ISSUE = '完整语义设计需新版运行时；条件、互动和协同均已保留，当前业务不可执行。'
MAX_RESULT_BYTES = 512 * 1024
CONTEXT_KEYS = ('handoff_id', 'report_id', 'report_revision', 'review_receipt_hash',
    'manual_value_hash', 'model_result_hash', 'source_hash', 'catalog_hash', 'context_hash')
DESIGN_KEYS = {'schema', *CONTEXT_KEYS, 'result', 'business_bindings', 'execution_policy', 'design_hash'}
_HASH = re.compile(r'^[a-f0-9]{64}$')
_UNSET = object()


def _need(condition, code):
    if not condition:
        raise ValueError(code)


def _bytes(value):
    _safe(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError('full_semantic_unsafe_json') from None


def _context(value):
    _need(type(value) is dict and set(value) == set(CONTEXT_KEYS), 'full_semantic_context_keys')
    for key in ('handoff_id', 'report_id'):
        try:
            _need(type(value[key]) is str and str(UUID(value[key])) == value[key], 'full_semantic_uuid')
        except (ValueError, TypeError, AttributeError):
            raise ValueError('full_semantic_uuid') from None
    _need(type(value['report_revision']) is int and value['report_revision'] >= 2, 'full_semantic_revision')
    _need(all(type(value[key]) is str and _HASH.fullmatch(value[key]) for key in CONTEXT_KEYS[3:]),
        'full_semantic_context_hash')
    return copy.deepcopy(value)


def _result(value):
    _need(type(value) is dict, 'full_semantic_result_object')
    _need(len(_bytes(value)) <= MAX_RESULT_BYTES, 'full_semantic_result_capacity')
    _need(type(value.get('businesses')) is list and all(type(business) is dict
        and business.get('operation') == 'upsert' and business.get('original_name') == ''
        for business in value['businesses']), 'full_semantic_append_only')
    result = CandidateWireV5.model_validate(value).model_dump()
    _need(result == value, 'full_semantic_result_changed')
    names = [business['name'] for business in result['businesses']]
    _need(len(names) == len(set(names)) and all(name.strip() and name == name.strip() for name in names),
        'full_semantic_business_names')
    # Check actual local references without fabricating a catalog, provider
    # response or source-semantic validation pass. The resulting view is not saved.
    candidate_with_ids(result, lambda prefix, path: prefix + digest(path)[:32])
    uncertain = any(claim['status'] == 'unknown' for claim in result['global_claims'])
    for business in result['businesses']:
        steps = business['steps']
        if not steps:
            uncertain = True
            _need(bool(business['inference_reason'].strip()), 'full_semantic_partial_business_reason')
        else:
            _need(bool(business['trigger'].strip()) and bool(business['output'].strip()),
                'full_semantic_business_trigger_output')
        for index, step in enumerate(steps):
            _need(step['name'].strip() and step['output'].strip(), 'full_semantic_step_text')
            _need(step['agent'] != '人工确认' or bool(step['role'].strip()), 'full_semantic_human_role')
            _need(step['next_index'] != index and step['else_index'] != index, 'full_semantic_self_edge')
            _need(bool(step['branch_condition'].strip()) or step['else_index'] is None,
                'full_semantic_branch_target')
        for person in business['participants']:
            _need(person['agent'].strip() and person['responsibility'].strip(), 'full_semantic_participant_text')
        for item in business['interactions']:
            _need(item['from_participant_index'] != item['to_participant_index'] and item['purpose'].strip(),
                'full_semantic_interaction')
        for item in business['conditions']:
            _need(bool(item['statement'].strip()), 'full_semantic_condition_text')
        for item in business['coordination']:
            _need(item['kind'] == 'unspecified' or len(item['step_indices']) >= 2,
                'full_semantic_coordination_targets')
            uncertain = uncertain or item['kind'] == 'unspecified'
        uncertain = uncertain or any(item['evidence']['status'] == 'unknown'
            for name in ('participants', 'interactions', 'conditions', 'coordination') for item in business[name])
    _need(all(question.strip() for question in result['questions']), 'full_semantic_question_text')
    _need(not uncertain or bool(result['questions']), 'full_semantic_questions_required')
    return copy.deepcopy(result)


def _bindings(result, handoff_id):
    namespace = UUID(handoff_id)
    bindings = []
    for bi, business in enumerate(result['businesses']):
        binding = {'candidate_index': bi, 'name': business['name']}
        for kind, collection in (('step', 'steps'), ('participant', 'participants'),
                ('interaction', 'interactions'), ('condition', 'conditions'), ('coordination', 'coordination')):
            binding[kind + '_ids'] = [str(uuid5(namespace, f'{SCHEMA}/{kind}/{bi}/{index}'))
                for index in range(len(business[collection]))]
        bindings.append(binding)
    return bindings


def build_design(result, *, handoff_id, report_id, report_revision, review_receipt_hash,
                 manual_value_hash, model_result_hash, source_hash, catalog_hash, context_hash):
    context = _context({'handoff_id': handoff_id, 'report_id': report_id, 'report_revision': report_revision,
        'review_receipt_hash': review_receipt_hash, 'manual_value_hash': manual_value_hash,
        'model_result_hash': model_result_hash, 'source_hash': source_hash,
        'catalog_hash': catalog_hash, 'context_hash': context_hash})
    value = _result(result)
    _need(value['source_hash'] == source_hash and value['catalog_hash'] == catalog_hash,
        'full_semantic_result_source')
    design = {'schema': SCHEMA, **context, 'result': value,
        'business_bindings': _bindings(value, handoff_id), 'execution_policy': POLICY}
    return {**design, 'design_hash': digest(design)}


def check_design(value, *, expected_result=_UNSET, expected_design_hash=None, **expected_context):
    if type(value) is dict and value.get('schema') == 'report-object-semantic-design-v169':
        from . import report_object_configuration_v169 as objects
        kwargs = {'expected_design_hash': expected_design_hash, **expected_context}
        if expected_result is not _UNSET:
            kwargs['expected_result'] = expected_result
        return objects.check_design(value, **kwargs)
    _need(type(value) is dict and set(value) == DESIGN_KEYS, 'full_semantic_design_keys')
    _bytes(value)
    context = _context(expected_context if expected_context else {key: value[key] for key in CONTEXT_KEYS})
    expected = build_design(value['result'], **context)
    _need(value == expected, 'full_semantic_design_does_not_cold_rebuild')
    if expected_result is not _UNSET:
        _need(value['result'] == _result(expected_result), 'full_semantic_expected_result_changed')
    if expected_design_hash is not None:
        _need(type(expected_design_hash) is str and _HASH.fullmatch(expected_design_hash)
            and expected['design_hash'] == expected_design_hash, 'full_semantic_expected_design_hash')
    return expected


def derive_flows(design):
    if type(design) is dict and design.get('schema') == 'report-object-semantic-design-v169':
        from .report_object_configuration_v169 import derive_flows as object_flows
        return object_flows(design)
    design = check_design(design)
    flows = {}
    for binding, business in zip(design['business_bindings'], design['result']['businesses']):
        ids = binding['step_ids']
        steps = [{'id': ids[index], **{key: copy.deepcopy(step[key])
            for key in ('name', 'purpose', 'agent', 'role', 'output')},
            'condition': step['branch_condition'],
            'nextId': 'end' if step['next_index'] is None else ids[step['next_index']],
            'elseId': 'end' if step['else_index'] is None else ids[step['else_index']]}
            for index, step in enumerate(business['steps'])]
        flows[binding['name']] = {'trigger': business['trigger'], 'output': business['output'], 'steps': steps,
            'confirmedAt': None, 'confirmedSignature': None, 'fullSemanticSource': {
                'schema': FLOW_REF, 'handoff_id': design['handoff_id'], 'design_hash': design['design_hash'],
                'candidate_index': binding['candidate_index'], 'business_hash': digest(business)}}
    return flows


def signature_context(config, flow):
    """Validate the design reference; operational bindings remain outer checks.

    Legacy unmarked flows return None. A damaged present design/marker never
    becomes legacy. A missing marker on a named derived flow is also rejected.
    Confirmed timestamps/signatures are deliberately not an execution grant.
    """
    _need(type(config) is dict and type(flow) is dict, 'full_semantic_configuration_shape')
    if 'fullSemanticDesigns' not in config:
        _need('fullSemanticSource' not in flow, 'full_semantic_missing_designs')
        return None
    designs = config['fullSemanticDesigns']
    _need(type(designs) is list, 'full_semantic_designs_shape')
    designs = [check_design(design) for design in designs]
    _need(len({design['handoff_id'] for design in designs}) == len(designs), 'full_semantic_duplicate_handoff')
    names = [binding['name'] for design in designs for binding in design['business_bindings']]
    _need(len(names) == len(set(names)), 'full_semantic_duplicate_configuration_business')
    flows = config.get('flows', {})
    _need(type(flows) is dict, 'full_semantic_flows_shape')
    for design in designs:
        for binding in design['business_bindings']:
            named = flows.get(binding['name'])
            _need(type(named) is dict and named.get('fullSemanticSource') == {
                'schema': ('report-object-semantic-flow-ref-v169' if design['schema'] == 'report-object-semantic-design-v169' else FLOW_REF), 'handoff_id': design['handoff_id'], 'design_hash': design['design_hash'],
                'candidate_index': binding['candidate_index'],
                'business_hash': digest(design['result']['businesses'][binding['candidate_index']])},
                'full_semantic_missing_bound_flow')
    if 'fullSemanticSource' not in flow:
        _need(not any(flows.get(name) == flow for name in names), 'full_semantic_missing_flow_marker')
        return None
    marker = flow['fullSemanticSource']
    _need(type(marker) is dict and set(marker) == {'schema', 'handoff_id', 'design_hash',
        'candidate_index', 'business_hash'}, 'full_semantic_flow_marker_shape')
    selected = [design for design in designs if design['handoff_id'] == marker['handoff_id']]
    _need(len(selected) == 1, 'full_semantic_flow_design_missing')
    design = selected[0]
    index = marker['candidate_index']
    _need(type(index) is int and 0 <= index < len(design['business_bindings']), 'full_semantic_business_index')
    binding = design['business_bindings'][index]
    expected = derive_flows(design)[binding['name']]
    named_flow = flows.get(binding['name'])
    _need(type(named_flow) is dict and marker == expected['fullSemanticSource'] and named_flow.get('fullSemanticSource') == marker,
        'full_semantic_flow_reference_changed')
    _need(flow.get('trigger') == expected['trigger'] and flow.get('output') == expected['output']
        and type(flow.get('steps')) is list and len(flow['steps']) == len(expected['steps']),
        'full_semantic_flow_projection_changed')
    for actual, projected in zip(flow['steps'], expected['steps']):
        _need(type(actual) is dict and all(key in actual and actual[key] == value for key, value in projected.items()),
            'full_semantic_step_projection_changed')
    return {'schema': ('report-object-semantic-signature-v169' if design['schema'] == 'report-object-semantic-design-v169' else SIGNATURE), 'handoff_id': design['handoff_id'], 'design_hash': design['design_hash'],
        'candidate_index': index, 'business_hash': marker['business_hash']}


def execution_issues(design, business_index):
    design = check_design(design)
    _need(type(business_index) is int and 0 <= business_index < len(design['business_bindings']),
        'full_semantic_business_index')
    return [BLOCKED_ISSUE]
