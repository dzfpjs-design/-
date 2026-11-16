"""Explicit incomplete V192 plan beside a rejected original V193 answer.

Only the original /plan value is exposed after complete independent source
validation. The original causality is never repaired, replaced or promoted.
Callers must cold-check the original capture, failed attempt and current IAM;
self hashes here bind supplied data and are not evidence of that authority.
"""
import copy
import hashlib
import json
import re

from . import business_flow_nested_causal_v192 as causal
from . import business_flow_nested_plan_v192 as plans
from . import report_business_flow_wire_v193 as original
from .contracts import digest


SCHEMA = 'report-business-flow-nested-component-recovery-v194'
COMPONENT = SCHEMA
COMPONENT_PATH = '/plan'
MAX_COMPONENT_BYTES = original.MAX_PROJECTION_BYTES
FAILURE_CODE = 'original_protocol_validation_failed'
ERROR = '原回答不符合独立递归稿恢复条件；原回答、失败记录及人工稿保留，未修写或授权'
_FALSE_FLAGS = {key: False for key in (
    'all_input_semantics_verified', 'semantic_quality_verified', 'branch_completeness_verified',
    'whole_source_coverage_verified', 'aggregate_verified', 'provider_delivery_verified',
    'source_authority_verified', 'entry_facts_verified', 'runtime_entry_binding_verified',
    'permissions_verified', 'external_dispatch_allowed', 'whole_report_verified',
    'causal_declaration_consistency_only', 'can_apply', 'can_publish', 'can_execute')}
_ISSUE_CODE = re.compile(r'[a-z_]{1,80}')


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


def _bytes(value):
    return original._bytes(value)


def _same(left, right):
    return _bytes(left) == _bytes(right)


def _issues(checked, plan, binding):
    """Only a public code and a real input pointer may refine the banner.

    Re-reading the original sidecar for diagnostics never repairs or returns a
    replacement sidecar, and does not mark its consistency as passed.
    """
    issues = [{'code': FAILURE_CODE, 'path': '/causality'}]
    try:
        sidecar = original._sidecar(checked, binding)
        causal.analyze_availability(plan, sidecar, scope_binding=binding)
    except causal.CausalAvailabilityError as error:
        pointers = plans.walk_action_ports(plan, scope_binding=binding)['input_paths']
        if (type(error.code) is str and _ISSUE_CODE.fullmatch(error.code) is not None
                and type(error.path) is str and error.path in pointers):
            issues.append({'code': error.code, 'path': error.path})
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        pass
    return issues


def build_component(raw, *, scope_binding, catalog, allowed_span_refs, context, input_claims):
    """Return an incomplete component, never a successful V193 projection.

    All five source arguments are supplied independently by the caller. Even a
    valid component grants no permission to adopt, publish, execute or dispatch.
    A whole-valid original must use its normal family rather than this route.
    """
    try:
        binding = original.check_binding(scope_binding)
        _need(type(raw) is bytes and 0 < len(raw) <= original.MAX_WIRE_BYTES)
        value = json.loads(raw.decode('utf-8', errors='strict'),
            object_pairs_hook=original.source_contract._pairs,
            parse_constant=original.source_contract._constant)
        _need(type(value) is dict and set(value) == {'schema', 'plan', 'causality'}
            and value['schema'] == original.WIRE)
        # Complete recursive capacity/shape checks precede the outer recursive
        # Pydantic model. Nothing is flattened, clipped or default-filled.
        plan = plans.check_plan(value['plan'], scope_binding=binding)
        checked = original.BusinessFlowNestedCausalPlanV193.model_validate(value).model_dump()
        _need(_same(checked, value) and _same(checked['plan'], plan))
        inputs = {'scope_binding': binding, 'catalog': catalog,
            'allowed_span_refs': allowed_span_refs, 'context': context, 'input_claims': input_claims}
        # This source-only HUMAN-plan gate is independent of the invalid model
        # sidecar. Unknown refs, changed original text or claims remain fatal.
        sourced = original.validate_wire_value(plan, **inputs)
        _need(_same(sourced, plan))
        failed = False
        try:
            original.build_projection(raw, **inputs)
        except ValueError:
            failed = True
        _need(failed)
        result = {'schema': SCHEMA, 'wire': copy.deepcopy(plan),
            'component_path': COMPONENT_PATH, 'component_value_schema': plans.PLAN,
            'component_value_hash': digest(plan), 'original_wire_schema': original.WIRE,
            'original_raw_sha256': hashlib.sha256(raw).hexdigest(), 'original_raw_bytes': len(raw),
            'original_value_hash': digest(checked), 'scope_binding': copy.deepcopy(binding),
            'scope_binding_hash': digest(binding), 'selected_input_count': len(input_claims),
            'selected_claims_hash': digest(input_claims), 'source_inputs_hash': digest(inputs),
            'original_protocol_valid': False, 'component_only': True, 'partial_scope': True,
            'status': 'incomplete', 'failure_code': FAILURE_CODE,
            'issues': _issues(checked, plan, binding), **_FALSE_FLAGS}
        result['component_hash'] = digest(result)
        _need(len(_bytes(result)) <= MAX_COMPONENT_BYTES)
        return result
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def check_component(value, raw, **expected_inputs):
    """Rebuild against original bytes and independent source, not a self seal."""
    try:
        expected = build_component(raw, **expected_inputs)
        _need(type(value) is dict and _same(value, expected))
        return expected
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None
