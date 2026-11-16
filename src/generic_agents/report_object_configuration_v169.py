"""Pure, lossless designs from a caller-authenticated V166 confirmation.

This module checks shapes, hashes and local business references only. The caller
must authenticate the current revision/command/audit, original wire, source
catalog, complete semantics and sharing consent. A self-consistent origin is
not ledger authority. The private manual note is never accepted or shared.
New objects are never converted to an older object or candidate family.
"""
import copy
import re
from uuid import UUID, uuid5

from .contracts import digest
from .report_full_reading_semantics import _safe
from .report_object_semantics_v166 import CandidateObjects, ManualValueV166, SCHEMA as MANUAL_SCHEMA
from .typed_payload_codec_v131 import canonical_json


SCHEMA = 'report-object-semantic-design-v169'
FLOW_REF = 'report-object-semantic-flow-ref-v169'
SIGNATURE = 'report-object-semantic-signature-v169'
POLICY = 'requires_full_semantic_runtime_v1'
ORIGIN_SCHEMA = 'report-object-confirmed-origin-v169'
MAX_RESULT_BYTES = 512 * 1024
CONTEXT_KEYS = ('handoff_id', 'report_id', 'report_revision', 'review_receipt_hash',
    'manual_value_hash', 'model_result_hash', 'source_hash', 'catalog_hash', 'context_hash')
DESIGN_KEYS = {'schema', *CONTEXT_KEYS, 'result', 'business_bindings', 'execution_policy',
    'review_origin', 'object_identity_confirmations', 'design_hash'}
ORIGIN_KEYS = {'schema', 'report_id', 'actor_id', 'workspace_id', 'revision_id',
    'revision_version', 'revision_record_hash', 'basis_hash', 'confirmed_command_id',
    'confirmed_command_hash', 'confirmed_wire_sha256', 'confirmed_value_hash',
    'confirmed_audit_id', 'confirmed_audit_hash'}
_HASH = re.compile(r'^[a-f0-9]{64}$')
_UNSET = object()


def _need(condition, code):
    if not condition:
        raise ValueError('object_design_v169_' + code)


def _bytes(value):
    try:
        _safe(value)
        return canonical_json(value).encode('utf-8', 'strict')
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise ValueError('object_design_v169_unsafe_json') from None


def _uuid(value):
    try:
        _need(type(value) is str and str(UUID(value)) == value, 'uuid')
    except (ValueError, TypeError, AttributeError):
        raise ValueError('object_design_v169_uuid') from None


def _hash(value):
    _need(type(value) is str and _HASH.fullmatch(value) is not None, 'hash')


def _context(value):
    _need(type(value) is dict and set(value) == set(CONTEXT_KEYS), 'context_keys')
    _bytes(value)
    for key in ('handoff_id', 'report_id'):
        _uuid(value[key])
    _need(type(value['report_revision']) is int and value['report_revision'] >= 2, 'report_revision')
    for key in CONTEXT_KEYS[3:]:
        _hash(value[key])
    return copy.deepcopy(value)


def check_origin(origin):
    """Return a detached closed origin; this does not authenticate its ledger."""
    _need(type(origin) is dict and set(origin) == ORIGIN_KEYS, 'origin_keys')
    _bytes(origin)
    _need(origin['schema'] == ORIGIN_SCHEMA, 'origin_family')
    for key in ('report_id', 'actor_id', 'workspace_id', 'revision_id',
            'confirmed_command_id', 'confirmed_audit_id'):
        _uuid(origin[key])
    _need(type(origin['revision_version']) is int and origin['revision_version'] >= 1,
        'origin_revision_version')
    for key in ('revision_record_hash', 'basis_hash', 'confirmed_command_hash',
            'confirmed_wire_sha256', 'confirmed_value_hash', 'confirmed_audit_hash'):
        _hash(origin[key])
    return copy.deepcopy(origin)


def _index(value, size):
    _need(type(value) is int and 0 <= value < size, 'local_index')


def _indices(values, size):
    _need(type(values) is list and len(values) == len(set(values)), 'duplicate_local_index')
    for value in values:
        _index(value, size)


def _citations(value):
    refs = [item['span_ref'] for item in value['citations']]
    _need(len(refs) == len(set(refs)), 'duplicate_citation')


def _businesses(result):
    """Shared business meaning, checked without retagging any new object."""
    businesses = result['businesses']
    names = [business['name'] for business in businesses]
    _need(len(names) == len(set(names)) and all(name.strip() and name == name.strip() for name in names),
        'business_names')
    uncertain = any(claim['status'] == 'unknown' for claim in result['global_claims'])
    for business in businesses:
        _need(business['operation'] == 'upsert' and business['original_name'] == '', 'append_only')
        _citations(business)
        steps, people = business['steps'], business['participants']
        if not steps:
            uncertain = True
            _need(bool(business['inference_reason'].strip()), 'partial_business_reason')
        else:
            _need(bool(business['trigger'].strip()) and bool(business['output'].strip()), 'business_trigger_output')
        for index, step in enumerate(steps):
            _citations(step)
            _need(step['name'].strip() and step['output'].strip(), 'step_text')
            _need(step['agent'] != '人工确认' or bool(step['role'].strip()), 'human_role')
            for key in ('next_index', 'else_index'):
                if step[key] is not None:
                    _index(step[key], len(steps))
                    _need(step[key] != index, 'self_edge')
            _need(bool(step['branch_condition'].strip()) or step['else_index'] is None, 'branch_target')
        for person in people:
            _need(person['agent'].strip() and person['responsibility'].strip(), 'participant_text')
        for item in business['interactions']:
            _index(item['from_participant_index'], len(people))
            _index(item['to_participant_index'], len(people))
            _need(item['from_participant_index'] != item['to_participant_index'] and item['purpose'].strip(),
                'interaction')
        for item in business['conditions']:
            _need(bool(item['statement'].strip()), 'condition_text')
        for collection in ('interactions', 'conditions', 'coordination'):
            for item in business[collection]:
                _indices(item['step_indices'], len(steps))
        for item in business['coordination']:
            _need(item['kind'] == 'unspecified' or len(item['step_indices']) >= 2, 'coordination_targets')
            uncertain = uncertain or item['kind'] == 'unspecified'
        for collection in ('participants', 'interactions', 'conditions', 'coordination'):
            for item in business[collection]:
                _citations(item['evidence'])
                uncertain = uncertain or item['evidence']['status'] == 'unknown'
    _need(all(question.strip() for question in result['questions']), 'question_text')
    _need(not uncertain or bool(result['questions']), 'questions_required')


def _result(value):
    _need(type(value) is dict, 'result_object')
    raw = _bytes(value)
    _need(len(raw) <= MAX_RESULT_BYTES, 'result_capacity')
    parsed = CandidateObjects.model_validate(value).model_dump()
    _need(_bytes(parsed) == raw, 'result_changed')
    # Names/field labels/identity references are checked by the new models.
    # Catalog scope, quotes, units in original text and existing relationship
    # endpoints remain the caller's full validate_value responsibility.
    for obj in parsed['objects']:
        _citations(obj)
        _citations(obj['business_identity'])
        for field in obj['fields']:
            _citations(field)
    relation_names = [relation['name'] for relation in parsed['relationships']]
    _need(len(relation_names) == len(set(relation_names)), 'relationship_names')
    for relation in parsed['relationships']:
        _citations(relation)
    _businesses(parsed)
    return copy.deepcopy(parsed)


def _confirmations(result, value):
    wrapper = {'schema': MANUAL_SCHEMA, 'result': result, 'note': '',
        'object_identity_confirmations': value}
    parsed = ManualValueV166.model_validate(wrapper).model_dump()
    _need(_bytes(parsed) == _bytes(wrapper), 'confirmations_changed')
    _need(all(item['acknowledge_platform_identity'] is True
        for item in parsed['object_identity_confirmations']), 'platform_identity_unconfirmed')
    return copy.deepcopy(parsed['object_identity_confirmations'])


def _bindings(result, handoff_id):
    namespace = UUID(handoff_id)
    bindings = []
    for index, business in enumerate(result['businesses']):
        binding = {'candidate_index': index, 'name': business['name']}
        for kind, collection in (('step', 'steps'), ('participant', 'participants'),
                ('interaction', 'interactions'), ('condition', 'conditions'), ('coordination', 'coordination')):
            binding[kind + '_ids'] = [str(uuid5(namespace, f'{SCHEMA}/{kind}/{index}/{position}'))
                for position in range(len(business[collection]))]
        bindings.append(binding)
    return bindings


def build_design(result, object_identity_confirmations, review_origin, *, handoff_id,
                 report_id, report_revision, review_receipt_hash, manual_value_hash,
                 model_result_hash, source_hash, catalog_hash, context_hash):
    context = _context({'handoff_id': handoff_id, 'report_id': report_id, 'report_revision': report_revision,
        'review_receipt_hash': review_receipt_hash, 'manual_value_hash': manual_value_hash,
        'model_result_hash': model_result_hash, 'source_hash': source_hash,
        'catalog_hash': catalog_hash, 'context_hash': context_hash})
    origin = check_origin(review_origin)
    _need(origin['report_id'] == report_id
        and origin['confirmed_command_hash'] == review_receipt_hash
        and origin['confirmed_value_hash'] == manual_value_hash, 'origin_context')
    value = _result(result)
    _need(value['source_hash'] == source_hash and value['catalog_hash'] == catalog_hash, 'result_source')
    confirmations = _confirmations(value, object_identity_confirmations)
    design = {'schema': SCHEMA, **context, 'result': value, 'review_origin': origin,
        'object_identity_confirmations': confirmations, 'business_bindings': _bindings(value, handoff_id),
        'execution_policy': POLICY}
    return {**design, 'design_hash': digest(design)}


def check_design(value, *, expected_result=_UNSET, expected_design_hash=None, **expected_context):
    _need(type(value) is dict and set(value) == DESIGN_KEYS, 'design_keys')
    _bytes(value)
    context = _context(expected_context if expected_context else {key: value[key] for key in CONTEXT_KEYS})
    expected = build_design(value['result'], value['object_identity_confirmations'], value['review_origin'], **context)
    _need(_bytes(value) == _bytes(expected), 'design_does_not_cold_rebuild')
    if expected_result is not _UNSET:
        _need(_bytes(value['result']) == _bytes(_result(expected_result)), 'expected_result_changed')
    if expected_design_hash is not None:
        _hash(expected_design_hash)
        _need(expected['design_hash'] == expected_design_hash, 'expected_design_hash')
    return expected


def derive_flows(design):
    """Project unchanged core flow fields; no execution confirmation is granted."""
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
