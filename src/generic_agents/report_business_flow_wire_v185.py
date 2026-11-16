"""Independent critique/revision wire over a frozen V184 parent plan.

All checks here are pure shape, pointer, hash and source-literal checks.  The
caller must cold-load the real parent capture, current source and permissions.
No parent self-hash, finding or compilation establishes semantic correctness.
"""
import copy
import hashlib
import json
import re
from typing import Literal
from uuid import UUID

from pydantic import Field

from . import report_business_flow_wire_v182 as base
from . import report_business_flow_wire_v184 as plans
from .contracts import digest
from .report_business_flow_revision_policy_v185 import platform_policy
from .report_full_reading_semantics import Closed


WIRE = 'report-business-flow-revision-v185'
PROJECTION = 'report-business-flow-projection-v185'
PARENT = 'business-flow-revision-parent-v185'
PARENT_BINDING = 'business-flow-revision-parent-source-v185'
BINDING = plans.BINDING
MANUAL = plans.MANUAL
MAX_WIRE_BYTES = plans.MAX_WIRE_BYTES
MAX_PROJECTION_BYTES = plans.MAX_PROJECTION_BYTES
ERROR = '业务流程修订、父计划或冻结来源无法核对；原答和人工劳动保留，未修写或授权'
_PARENT_KEYS = {'schema', 'binding', 'raw_plan', 'parent_hash'}
_BINDING_KEYS = set(('schema attempt_id producer_id revision model_id request_hash reserved_call_id '
    'capture_hash response_body_sha256 arguments_sha256 source_snapshot_hash generation_profile '
    'preparation_hash scope_id plan_id plan_hash raw_plan_hash scope_binding_hash').split())
_ID_KEYS = ('attempt_id', 'producer_id', 'model_id', 'reserved_call_id', 'plan_id')
_HASH_KEYS = ('request_hash', 'capture_hash', 'response_body_sha256', 'arguments_sha256',
    'source_snapshot_hash', 'preparation_hash', 'plan_hash', 'raw_plan_hash', 'scope_binding_hash')
_FALSE_FLAGS = {key: False for key in (
    'all_input_semantics_verified', 'semantic_quality_verified', 'branch_completeness_verified',
    'whole_source_coverage_verified', 'aggregate_verified', 'provider_delivery_verified',
    'source_authority_verified', 'parent_source_authority_verified', 'platform_policy_compliance_verified',
    'can_apply', 'can_publish', 'can_execute')}

check_binding = plans.check_binding
manual_value_schema = plans.manual_value_schema
validate_value = plans.validate_value
confirm_value = plans.confirm_value


class BusinessFlowRevisionCapacityError(ValueError):
    """Complete revision cannot fit; no partial result or truncation allowed."""


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


def _bytes(value):
    return base._bytes(value)


def _same(left, right):
    return _bytes(left) == _bytes(right)


def _identity(value):
    _need(type(value) is str and value == str(UUID(value)))


def check_parent_source(parent_source, scope_binding):
    """Internal binding only; it does not read the parent row or grant access."""
    try:
        binding = check_binding(scope_binding)
        parent = parent_source
        _need(type(parent) is dict and set(parent) == _PARENT_KEYS and parent['schema'] == PARENT)
        _bytes(parent)
        origin = parent['binding']
        _need(type(origin) is dict and set(origin) == _BINDING_KEYS
            and origin['schema'] == PARENT_BINDING and origin['generation_profile'] == 'flow-v184')
        for key in _ID_KEYS:
            _identity(origin[key])
        for key in _HASH_KEYS:
            base._hash(origin[key])
        _need(type(origin['revision']) is int and origin['revision'] >= 1
            and origin['scope_id'] == binding['scope_id']
            and origin['plan_hash'] == binding['plan_hash']
            and origin['source_snapshot_hash'] == binding['source_snapshot_hash']
            and origin['scope_binding_hash'] == digest(binding))
        plan = plans.parse_plan(_bytes(parent['raw_plan']), scope_binding=binding)
        _need(origin['raw_plan_hash'] == digest(plan))
        base._hash(parent['parent_hash'])
        _need(parent['parent_hash'] == digest({key: value for key, value in parent.items() if key != 'parent_hash'}))
        return copy.deepcopy(parent)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


class FindingV185(Closed):
    kind: Literal['roles', 'time', 'ordering', 'parallel', 'source', 'condition', 'termination', 'scope', 'other']
    severity: Literal['critical', 'major', 'minor']
    basis: Literal['platform_policy', 'source', 'both']
    base_paths: list[str] = Field(min_length=1, max_length=16)
    revised_paths: list[str] = Field(max_length=16)
    resolution: Literal['changed', 'removed', 'unresolved']
    citations: list[base.SpanRef] = Field(max_length=24)
    explanation: base.Text
    resolution_explanation: base.Text


class BusinessFlowRevisionV185(Closed):
    schema: Literal['report-business-flow-revision-v185']
    binding_hash: base.Hash
    parent_source_hash: base.Hash
    base_plan_hash: base.Hash
    platform_policy_hash: base.Hash
    findings: list[FindingV185] = Field(max_length=64)
    revised_plan: plans.BusinessFlowPlanV184


def _schema_for_shape(name, binding_hash, parent_source_hash, base_plan_hash, platform_policy_hash):
    """Source-free native shape reconstruction; no arbitrary nested schema."""
    _need(type(name) is str and 1 <= len(name) <= 200 and bool(name.strip()))
    for value in (binding_hash, parent_source_hash, base_plan_hash, platform_policy_hash):
        base._hash(value)
    _need(platform_policy_hash == digest(platform_policy()))
    schema = BusinessFlowRevisionV185.model_json_schema()
    for key, value in (('binding_hash', binding_hash), ('parent_source_hash', parent_source_hash),
            ('base_plan_hash', base_plan_hash), ('platform_policy_hash', platform_policy_hash)):
        schema['properties'][key]['const'] = value
    # Pydantic merges the actual nested model definitions and their local refs.
    schema['$defs']['BusinessFlowPlanV184']['properties']['binding_hash']['const'] = binding_hash
    schema['$defs']['BusinessDefinitionV184']['properties']['name']['const'] = name
    return schema


def output_schema(scope_binding, *, parent_source):
    binding = check_binding(scope_binding)
    parent = check_parent_source(parent_source, binding)
    return _schema_for_shape(binding['name'], digest(binding), digest(parent),
        digest(parent['raw_plan']), digest(platform_policy()))


def _resolve_pointer(value, pointer):
    """Resolve RFC6901, rejecting noncanonical array tokens and bad escapes."""
    _need(type(pointer) is str and len(pointer) <= 2048)
    if pointer == '':
        return value
    _need(pointer.startswith('/'))
    for encoded in pointer.split('/')[1:]:
        _need(re.search(r'~(?:[^01]|$)', encoded) is None)
        token = encoded.replace('~1', '/').replace('~0', '~')
        if type(value) is dict:
            _need(token in value)
            value = value[token]
        elif type(value) is list:
            _need(re.fullmatch(r'0|[1-9][0-9]*', token) is not None)
            index = int(token)
            _need(index < len(value))
            value = value[index]
        else:
            _need(False)
    return value


def parse_review(raw, *, scope_binding, parent_source):
    try:
        binding = check_binding(scope_binding)
        parent = check_parent_source(parent_source, binding)
        _need(type(raw) is bytes and 0 < len(raw) <= MAX_WIRE_BYTES)
        value = json.loads(raw.decode('utf-8', errors='strict'),
            object_pairs_hook=base._pairs, parse_constant=base._constant)
        checked = BusinessFlowRevisionV185.model_validate(value).model_dump()
        _need(_same(value, checked) and checked['binding_hash'] == digest(binding)
            and checked['parent_source_hash'] == digest(parent)
            and checked['base_plan_hash'] == digest(parent['raw_plan'])
            and checked['platform_policy_hash'] == digest(platform_policy()))
        revised = plans.parse_plan(_bytes(checked['revised_plan']), scope_binding=binding)
        for finding in checked['findings']:
            for pointer in finding['base_paths']:
                _resolve_pointer(parent['raw_plan'], pointer)
            for pointer in finding['revised_paths']:
                _resolve_pointer(revised, pointer)
            if finding['resolution'] == 'changed':
                _need(bool(finding['revised_paths']))
            elif finding['resolution'] == 'removed':
                _need(not finding['revised_paths'])
        return copy.deepcopy(checked)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def build_projection(raw, *, scope_binding, parent_source, catalog, allowed_span_refs, context, input_claims):
    parent = check_parent_source(parent_source, scope_binding)
    review = parse_review(raw, scope_binding=scope_binding, parent_source=parent)
    # Findings are not necessarily used by the revised graph.  Check them all,
    # rather than losing an unknown citation when compiling the new plan.
    originals = {item['span_ref'] for item in base.resolve_evidence(catalog, allowed_span_refs, allowed_span_refs)}
    for finding in review['findings']:
        _need(set(finding['citations']) <= set(allowed_span_refs)
            and set(finding['citations']) <= originals)
    revised = plans.build_projection(_bytes(review['revised_plan']), scope_binding=scope_binding,
        catalog=catalog, allowed_span_refs=allowed_span_refs, context=context, input_claims=input_claims)
    policy = platform_policy()
    value = {
        'schema': PROJECTION, 'wire_schema': base.WIRE, 'wire': copy.deepcopy(revised['wire']),
        'wire_value_hash': digest(revised['wire']), 'scope_binding': copy.deepcopy(revised['scope_binding']),
        'scope_binding_hash': revised['scope_binding_hash'],
        'raw_review_schema': WIRE, 'raw_review': review, 'raw_review_value_hash': digest(review),
        'raw_review_bytes_sha256': hashlib.sha256(raw).hexdigest(), 'raw_review_bytes': len(raw),
        'parent_source': parent, 'parent_source_hash': digest(parent),
        'base_plan_hash': digest(parent['raw_plan']), 'platform_policy': policy,
        'platform_policy_hash': digest(policy), 'revised_plan': copy.deepcopy(review['revised_plan']),
        'revised_plan_hash': digest(review['revised_plan']), 'revised_projection': revised,
        'revised_projection_hash': revised['projection_hash'],
        'compiled_wire_bytes_sha256': revised['compiled_wire_bytes_sha256'],
        'compiled_wire_bytes': revised['compiled_wire_bytes'],
        'compilation_manifest': copy.deepcopy(revised['compilation_manifest']),
        'compilation_manifest_hash': revised['compilation_manifest_hash'],
        'selected_input_claims': copy.deepcopy(revised['selected_input_claims']),
        'selected_input_count': revised['selected_input_count'], 'selected_claims_hash': revised['selected_claims_hash'],
        'used_refs': copy.deepcopy(revised['used_refs']), 'used_refs_hash': revised['used_refs_hash'],
        'partial_scope': True, **_FALSE_FLAGS,
    }
    value['projection_hash'] = digest(value)
    if len(_bytes(value)) > MAX_PROJECTION_BYTES:
        raise BusinessFlowRevisionCapacityError('完整父计划、修订和编译结果超过8MiB；原答保留，未截断')
    return value


def check_projection(value, raw, **expected_inputs):
    expected = build_projection(raw, **expected_inputs)
    _need(type(value) is dict and _same(value, expected))
    return expected


def new_manual(projection):
    """Unconfirmed V182 draft after checked revision, never a source grant.

    Callers must have checked the actual raw response and frozen inputs with
    build/check_projection first; this source-free helper cannot replace that.
    """
    _need(type(projection) is dict and projection.get('schema') == PROJECTION
        and projection.get('projection_hash') == digest({key: value for key, value in projection.items() if key != 'projection_hash'}))
    _need(all(projection.get(key) is False for key in _FALSE_FLAGS))
    binding = check_binding(projection['scope_binding'])
    parent = check_parent_source(projection['parent_source'], binding)
    review = parse_review(_bytes(projection['raw_review']), scope_binding=binding, parent_source=parent)
    revised = projection['revised_projection']
    _need(projection['parent_source_hash'] == digest(parent)
        and projection['base_plan_hash'] == digest(parent['raw_plan'])
        and projection['raw_review_value_hash'] == digest(review)
        and _same(projection['platform_policy'], platform_policy())
        and projection['platform_policy_hash'] == digest(platform_policy())
        and _same(projection['revised_plan'], review['revised_plan'])
        and projection['revised_plan_hash'] == digest(review['revised_plan'])
        and projection['revised_projection_hash'] == revised['projection_hash']
        and _same(revised['raw_plan'], review['revised_plan'])
        and _same(revised['scope_binding'], binding)
        and _same(projection['wire'], revised['wire'])
        and projection['wire_value_hash'] == digest(revised['wire'])
        and _same(projection['compilation_manifest'], revised['compilation_manifest'])
        and projection['compilation_manifest_hash'] == revised['compilation_manifest_hash'])
    return plans.new_manual(revised)
