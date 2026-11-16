"""Explicit, source-checked component recovery beside a rejected V185 answer.

Only an otherwise valid V185 answer with invalid finding locations is eligible.
The original answer remains invalid, its locations are never rewritten, and the
V184 component must pass its real compiler and V182 source/graph validation.
Capture authenticity, current permissions and attempt state belong to callers.
"""
import copy
import hashlib
import json
import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError

from . import report_business_flow_wire_v185 as revision
from . import report_business_flow_wire_v184 as plans
from .contracts import digest


SCHEMA = 'report-business-flow-component-recovery-v186'
COMPONENT_PATH = '/revised_plan'
MAX_COMPONENT_BYTES = plans.MAX_PROJECTION_BYTES
ERROR = '原回答不符合独立分层恢复条件；原失败、原文与人工稿保留，未修写或授权'
_FALSE_FLAGS = {key: False for key in (
    'all_input_semantics_verified', 'semantic_quality_verified', 'branch_completeness_verified',
    'whole_source_coverage_verified', 'aggregate_verified', 'provider_delivery_verified',
    'source_authority_verified', 'parent_source_authority_verified', 'platform_policy_compliance_verified',
    'can_apply', 'can_publish', 'can_execute')}


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


def _bytes(value):
    return revision._bytes(value)


def _same(left, right):
    return _bytes(left) == _bytes(right)


def _check_schema(value, binding, parent):
    try:
        Draft202012Validator(revision.output_schema(binding, parent_source=parent)).validate(value)
    except JSONSchemaValidationError:
        raise ValueError(ERROR) from None


def _location_reason(value, pointer):
    """Describe a failed original location without guessing a replacement."""
    if type(pointer) is not str or len(pointer) > 2048:
        return 'invalid_pointer'
    if pointer == '':
        return None
    if not pointer.startswith('/'):
        return 'invalid_pointer'
    for encoded in pointer.split('/')[1:]:
        if re.search(r'~(?:[^01]|$)', encoded) is not None:
            return 'invalid_pointer'
        token = encoded.replace('~1', '/').replace('~0', '~')
        if type(value) is dict:
            if token not in value:
                return 'path_not_found'
            value = value[token]
        elif type(value) is list:
            if re.fullmatch(r'0|[1-9][0-9]*', token) is None:
                return 'invalid_pointer'
            index = int(token)
            if index >= len(value):
                return 'path_not_found'
            value = value[index]
        else:
            return 'path_not_found'
    return None


def _locations(review, parent):
    issues = []
    for index, finding in enumerate(review['findings']):
        # These are resolution/structure errors, not a recoverable spelling or
        # missing-target problem. Preserve the old invariant without repair.
        if finding['resolution'] == 'changed':
            _need(bool(finding['revised_paths']))
        elif finding['resolution'] == 'removed':
            _need(not finding['revised_paths'])
        for field, target in (('base_paths', parent['raw_plan']),
                ('revised_paths', review['revised_plan'])):
            for pointer in finding[field]:
                reason = _location_reason(target, pointer)
                if reason is None:
                    # Keep the original resolver authoritative for every
                    # supposedly valid path; a future rule cannot be bypassed.
                    revision._resolve_pointer(target, pointer)
                else:
                    try:
                        revision._resolve_pointer(target, pointer)
                    except ValueError:
                        pass
                    else:
                        _need(False)
                    issues.append({'finding_index': index, 'field': field,
                        'path': pointer, 'reason': reason})
    return issues


def build_component(raw, *, scope_binding, parent_source, catalog, allowed_span_refs, context, input_claims):
    """Recover one independent, unconfirmed component; never accept V185.

    The caller supplies independent cold source/capture inputs. Self hashes in
    this pure DTO are integrity bindings, not proof of their authenticity.
    """
    try:
        binding = revision.check_binding(scope_binding)
        parent = revision.check_parent_source(parent_source, binding)
        _need(type(raw) is bytes and 0 < len(raw) <= revision.MAX_WIRE_BYTES)
        original = json.loads(raw.decode('utf-8', errors='strict'),
            object_pairs_hook=revision.base._pairs, parse_constant=revision.base._constant)
        review = revision.BusinessFlowRevisionV185.model_validate(original).model_dump()
        _need(_same(original, review))
        # Validate the actual frozen output schema as well as its Pydantic
        # shape. In particular all four source/policy consts remain mandatory.
        _check_schema(review, binding, parent)
        _need(review['binding_hash'] == digest(binding)
            and review['parent_source_hash'] == digest(parent)
            and review['base_plan_hash'] == digest(parent['raw_plan'])
            and review['platform_policy_hash'] == digest(revision.platform_policy()))
        revised_plan = plans.parse_plan(_bytes(review['revised_plan']), scope_binding=binding)
        _need(_same(revised_plan, review['revised_plan']))
        issues = _locations(review, parent)
        _need(bool(issues))
        try:
            revision.parse_review(raw, scope_binding=binding, parent_source=parent)
        except ValueError:
            pass
        else:
            _need(False)
        # Even findings not used by the compiled component retain their exact
        # allowed-ref and actual catalog gate. Never discard unknown evidence.
        originals = {item['span_ref'] for item in revision.base.resolve_evidence(
            catalog, allowed_span_refs, allowed_span_refs)}
        for finding in review['findings']:
            _need(set(finding['citations']) <= set(allowed_span_refs)
                and set(finding['citations']) <= originals)
        projected = plans.build_projection(_bytes(revised_plan), scope_binding=binding,
            catalog=catalog, allowed_span_refs=allowed_span_refs, context=context,
            input_claims=input_claims)
        value = {'schema': SCHEMA, 'component_path': COMPONENT_PATH,
            'original_protocol': revision.WIRE, 'original_protocol_valid': False,
            'raw_review': copy.deepcopy(original), 'raw_review_value_hash': digest(original),
            'raw_review_bytes_sha256': hashlib.sha256(raw).hexdigest(), 'raw_review_bytes': len(raw),
            'component_value_hash': digest(original['revised_plan']),
            'scope_binding': copy.deepcopy(binding), 'scope_binding_hash': digest(binding),
            'parent_source': parent, 'parent_source_hash': digest(parent),
            'location_issues': issues, 'revised_projection': projected,
            'revised_projection_hash': projected['projection_hash'],
            'wire': copy.deepcopy(projected['wire']), 'wire_value_hash': digest(projected['wire']),
            'machine_review_only': True, 'partial_scope': True, **_FALSE_FLAGS}
        value['component_hash'] = digest(value)
        _need(len(_bytes(value)) <= MAX_COMPONENT_BYTES)
        return value
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def check_component(value, raw, **expected_inputs):
    expected = build_component(raw, **expected_inputs)
    _need(type(value) is dict and _same(value, expected))
    return expected


def new_manual(component):
    """Unconfirmed V182 value, after the caller cold-checks this component.

    This detached helper additionally checks internal projection consistency;
    it cannot authenticate a capture, grant authority or mark V185 successful.
    """
    _need(type(component) is dict and component.get('schema') == SCHEMA
        and component.get('component_hash') == digest({key: value for key, value in component.items()
            if key != 'component_hash'}))
    _need(component.get('component_path') == COMPONENT_PATH
        and component.get('original_protocol') == revision.WIRE
        and component.get('original_protocol_valid') is False
        and component.get('machine_review_only') is True
        and component.get('partial_scope') is True
        and all(component.get(key) is False for key in _FALSE_FLAGS))
    binding = revision.check_binding(component['scope_binding'])
    parent = revision.check_parent_source(component['parent_source'], binding)
    original = component['raw_review']
    checked = revision.BusinessFlowRevisionV185.model_validate(original).model_dump()
    _need(_same(original, checked))
    _check_schema(checked, binding, parent)
    projected = component['revised_projection']
    _need(component['scope_binding_hash'] == digest(binding)
        and component['parent_source_hash'] == digest(parent)
        and component['raw_review_value_hash'] == digest(original)
        and component['component_value_hash'] == digest(original['revised_plan'])
        and _same(component['location_issues'], _locations(checked, parent))
        and bool(component['location_issues'])
        and component['revised_projection_hash'] == projected['projection_hash']
        and _same(projected['raw_plan'], original['revised_plan'])
        and _same(projected['scope_binding'], binding)
        and _same(component['wire'], projected['wire'])
        and component['wire_value_hash'] == digest(projected['wire']))
    return plans.new_manual(projected)
