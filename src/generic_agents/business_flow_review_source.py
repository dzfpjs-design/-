"""Historical paid flow bytes for a separate, non-authoritative human draft.

No generation, journal recovery, budget mutation, model fallback or adoption.
Current ownership and original inventory are checked before reading saved bytes.
Historical source access is not a current source/operation/spending grant.
"""
import copy
import hashlib

from fastapi import HTTPException

from . import report_scoped_design_consumer_v181 as attempts
from . import report_scoped_design_profiles_v182 as profiles
from . import report_scoped_design_runtime_v181 as runtime
from .report_evidence_strict_map import _loads
from .security import require

FALSE_FLAGS = {'can_apply': False, 'can_publish': False, 'can_execute': False,
    'semantic_quality_verified': False}


def need(value, message='原回答和独立人工稿来源暂时无法核对；保留已有稿件', code=409):
    if not value:
        raise HTTPException(code, message)


def load(db, principal, attempt_id):
    """Read a complete original capture without approving its graph or quotes."""
    try:
        return _load(db, principal, attempt_id)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, '原回答或冻结来源暂时无法核对；原记录和已有稿件保留') from None


def load_checked(db, principal, attempt_id):
    """Return this very read's checked row, without a second inventory replay.

    This is not a caller-provided validation flag or cached authority. The
    ordinary complete loader owns the row and releases it only after success.
    """
    rows = []
    try:
        detail = _load(db, principal, attempt_id, _checked_rows=rows)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, '原回答或冻结来源暂时无法核对；原记录和已有稿件保留') from None
    need(len(rows) == 1)
    return detail, rows[0]


def _load(db, principal, attempt_id, *, _checked_rows=None):
    require(principal, 'admin', 'operator')
    attempts.route_identity(attempt_id)
    row = attempts.checked_attempt(db, principal, attempt_id)
    prepared = row.data['preview']['preparation']
    family = profiles.selected(prepared)
    need(profiles.is_flow(family), '此回答不属于可核对的业务流程类型', 422)
    need(row.status in {'failed', 'review', 'needs_clarification', 'reviewed'},
        '原生成活动尚未结束或未能确定结果，请先恢复原活动；不会重新生成', 409)
    need(row.reserved_call_id is not None)
    response = runtime.read_response(db, principal, row, recover_saved=False)
    need(response is not None, '原回答文件暂时不可读；已有人工稿仍可恢复', 409)
    raw = response['body']
    capture = response['capture']
    inspected = family.native.inspect_response(raw, capture['http_status'], family.native.STAGE,
        response_complete=capture['response_complete'],
        http_content_encoding=capture['http_content_encoding'])
    metadata = inspected['metadata']
    need(metadata['envelope_valid'] is True and type(inspected['arguments']) is str,
        '原回答没有完整可核对的文字；原记录和已有稿件保留', 409)
    arguments = inspected['arguments'].encode('utf-8', errors='strict')
    need(0 < len(arguments) <= 1024 * 1024)
    need(response['receipt']['call_id'] == row.reserved_call_id
        and capture['ready'] is True and capture['response_complete'] is True
        and metadata['delivery'] == family.native.DELIVERY
        and metadata['response_body_sha256'] == hashlib.sha256(raw).hexdigest()
        and metadata['arguments_sha256'] == hashlib.sha256(arguments).hexdigest())
    try:
        value = _loads(arguments.decode('utf-8'))
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise HTTPException(409, '原回答不是可编辑的完整数据；原文字与已有稿件保留') from None
    source = row.data['preview']['source_context']
    component = None
    nested_component = None
    if family.id == profiles.FLOW_NESTED and row.status == 'failed' and row.data['projection'] is None:
        from .report_business_flow_nested_component_recovery_v194 import build_component
        nested_component = build_component(arguments, scope_binding=prepared['scope_binding'],
            catalog=source['catalog'], allowed_span_refs=prepared['allowed_span_refs'],
            context=source['context'], input_claims=prepared['input_claims'])
        value = nested_component['wire']
    elif family.id == profiles.FLOW_REVISION and row.status == 'failed' and row.data['projection'] is None:
        from .report_business_flow_component_recovery_v186 import build_component
        component = build_component(arguments, scope_binding=prepared['scope_binding'],
            parent_source=prepared['parent_source'], catalog=source['catalog'],
            allowed_span_refs=prepared['allowed_span_refs'], context=source['context'],
            input_claims=prepared['input_claims'])
        value = component['wire']
    elif family.id in {profiles.FLOW_PLAN, profiles.FLOW_REVISION, profiles.FLOW_REASONED, profiles.FLOW_CAUSAL, profiles.FLOW_NESTED}:
        # New structured plans have an explicit, independently hashed compiled
        # baseline. Never reinterpret an older answer with this compiler.
        extra = {'parent_source': prepared['parent_source']} if family.id == profiles.FLOW_REVISION else {}
        projection = family.wire.build_projection(arguments,
            scope_binding=prepared['scope_binding'],catalog=source['catalog'],
            allowed_span_refs=prepared['allowed_span_refs'],context=source['context'],
            input_claims=prepared['input_claims'], **extra)
        need(row.data['projection'] is not None
            and store_same(row.data['projection'], projection),
            '原流程计划、编译版本或冻结来源已变化；原稿保留')
        value = projection['wire']
    binding = {'schema': 'business-flow-review-source-v1', 'attempt_id': row.id,
        'producer_id': row.producer_id, 'revision': row.revision, 'model_id': row.model_id,
        'request_hash': prepared['request_hash'], 'reserved_call_id': row.reserved_call_id,
        'capture_hash': capture['capture_hash'],
        'response_body_sha256': hashlib.sha256(raw).hexdigest(),
        'arguments_sha256': hashlib.sha256(arguments).hexdigest(),
        'source_snapshot_hash': prepared['source_snapshot_hash'], 'generation_profile': family.id}
    extra = {}
    if family.id == profiles.FLOW_NESTED:
        binding.update(schema='business-flow-nested-review-source-v193',
            value_schema='report-business-flow-nested-plan-v192')
        if nested_component is None:
            extra['_nested_causal_projection'] = copy.deepcopy(projection)
        else:
            binding.update(schema='business-flow-nested-component-review-source-v194',
                component_path=nested_component['component_path'],
                component_value_hash=nested_component['component_value_hash'],
                component_validation_hash=nested_component['component_hash'], original_protocol_valid=False)
            extra['generation_recovery'] = {'schema': 'business-flow-nested-component-view-v194',
                'component_path': nested_component['component_path'],
                'component_value_schema': nested_component['component_value_schema'],
                'component_value_hash': nested_component['component_value_hash'],
                'component_validation_hash': nested_component['component_hash'],
                'original_protocol_valid': False, 'component_only': True,
                'failure_code': nested_component['failure_code'],
                'issues': copy.deepcopy(nested_component['issues']), **FALSE_FLAGS}
    if family.id == profiles.FLOW_CAUSAL:
        # Internal cold-validated original only. The API builds a natural-name
        # view against the currently saved draft; this does not persist a new
        # causal proof for edited human graph values.
        extra['_causal_projection'] = copy.deepcopy(projection)
    if component is not None:
        binding.update(schema='business-flow-component-review-source-v186',
            component_path=component['component_path'], component_value_hash=component['component_value_hash'],
            component_validation_hash=component['component_hash'], original_protocol_valid=False)
    if family.id == profiles.FLOW_REVISION:
        if component is not None:
            projection = {'parent_source': component['parent_source'],
                'parent_source_hash': component['parent_source_hash'],
                'base_plan_hash': prepared['parent_source']['binding']['raw_plan_hash'],
                'platform_policy_hash': prepared['platform_policy_hash'],
                'platform_policy': prepared['payload']['platform_policy'],
                'raw_review': component['raw_review'], 'revised_plan_hash': component['component_value_hash']}
        extra['generation_review'] = {'schema': 'business-flow-source-review-v185',
            'parent_attempt_id': projection['parent_source']['binding']['attempt_id'],
            'parent_source_hash': projection['parent_source_hash'], 'base_plan_hash': projection['base_plan_hash'],
            'platform_policy_hash': projection['platform_policy_hash'],
            'platform_policy': copy.deepcopy(projection['platform_policy']),
            'findings': copy.deepcopy(projection['raw_review']['findings']),
            'revised_plan_hash': projection['revised_plan_hash'],
            'machine_review_only': True, **FALSE_FLAGS}
        if component is not None:
            extra['generation_review'].update(schema='business-flow-source-component-review-v186',
                original_protocol_valid=False, component_only=True,
                location_issues=copy.deepcopy(component['location_issues']),
                component_path=component['component_path'], component_validation_hash=component['component_hash'])
    detail = {'source_binding': binding, 'baseline_value': copy.deepcopy(value), **extra,
        'name': prepared['scope_binding']['name'],
        'diagnostic_context': {'scope_binding': copy.deepcopy(prepared['scope_binding']),
            'catalog': copy.deepcopy(source['catalog']),
            'allowed_span_refs': copy.deepcopy(prepared['allowed_span_refs']),
            'context': copy.deepcopy(source['context']),
            'input_claims': copy.deepcopy(prepared['input_claims'])}}
    if _checked_rows is not None:
        _checked_rows.append(row)
    return detail


def diagnose(loaded, value):
    if loaded['source_binding'].get('schema') in {'business-flow-nested-review-source-v193',
            'business-flow-nested-component-review-source-v194'}:
        from .business_flow_nested_review_diagnostics_v193 import review
        return review(value, **loaded['diagnostic_context'])
    from .business_flow_review_diagnostics import review
    return review(value, **loaded['diagnostic_context'])


def current_review_source(db, principal, attempt_id, saved):
    """Fresh author review, separate from historical draft access.

    Re-read original materials, parsing generation, map receipts and ACL under
    the existing source authority. Configuration edits do not invalidate a
    source merely because the setup revision has advanced. Conversely a saved
    draft or a previous successful diagnostic never renews source authority.
    The caller first holds the exclusive human-review actor inventory lock.
    No model, journal recovery, capability publication or task is invoked.
    """
    from .contracts import digest
    from . import report_design_saved_source_v174 as current
    loaded = load(db, principal, attempt_id)
    need(loaded['source_binding'].get('value_schema') != 'report-business-flow-nested-plan-v192',
        '递归流程可以保存和继续修订；实际能力绑定与采用尚未接入，当前不能签发作者核对或执行记录', 422)
    need(saved is not None and saved['attempt_id'] == attempt_id
        and saved['source_binding'] == loaded['source_binding']
        and store_same(saved['baseline_value'], loaded['baseline_value']),
        '请先保存当前修订稿；原模型来源与独立稿件必须一致')
    diagnostics = diagnose(loaded, saved['value'])
    need(diagnostics['available'] is True and diagnostics['structural_valid'] is True
        and diagnostics['source_valid'] is True and not diagnostics['issues'],
        '流程连线或原文引用仍有待处理项；请先完成修订并保存', 422)
    need(not saved['value']['business']['questions'],
        '还有待核问题，请核对原文并处理后再记录作者核对', 422)
    row = attempts.checked_attempt(db, principal, attempt_id)
    original = copy.deepcopy(row.data['preview']['source_context'])
    fresh = current.load_source_context(db, principal, row.producer_id, lock=True)
    need(store_same(original, fresh)
        and loaded['source_binding']['source_snapshot_hash'] == fresh['snapshot']['snapshot_hash'],
        '当前原件、解析结果或来源记录已变化；已有稿件保留，请重新核对来源')
    snapshot = {'schema': 'business-flow-author-review-source-v1', 'attempt_id': attempt_id,
        'source_binding_hash': digest(loaded['source_binding']),
        'value_hash': digest(saved['value']),
        'diagnostic_context_hash': digest(loaded['diagnostic_context']),
        'original_source_hash': digest(original), 'current_source_hash': digest(fresh),
        'current_materials_hash': digest(fresh['snapshot']['materials'])}
    return snapshot


def store_same(left, right):
    from .typed_payload_codec_v131 import canonical_json
    return canonical_json(left) == canonical_json(right)


def current_nested_review_source(db, principal, attempt_id, saved, *, review_mode):
    return nested_review_source_bundle(db, principal, attempt_id, saved, review_mode=review_mode)[0]


def nested_review_source_bundle(db, principal, attempt_id, saved, *, review_mode):
    """Fresh recursive source review, with an explicit engineering-only lane.

    This records neither a repaired original protocol nor an execution grant.
    Engineering review preserves questions and cannot stand in for human review.
    The caller holds the same exclusive saved-review inventory as legacy review.
    """
    from .contracts import digest
    from . import report_design_saved_source_v174 as current
    need(review_mode in {'author_self_review', 'engineering_simulation'}, '请选择明确的核对方式', 422)
    loaded, row = load_checked(db, principal, attempt_id)
    need(loaded['source_binding'].get('value_schema') == 'report-business-flow-nested-plan-v192',
        '此核对入口仅接收已保存的递归流程', 422)
    need(saved is not None and saved['attempt_id'] == attempt_id
        and saved['source_binding'] == loaded['source_binding']
        and store_same(saved['baseline_value'], loaded['baseline_value']),
        '请先保存当前修订稿，再核对这一保存版本')
    diagnostics = diagnose(loaded, saved['value'])
    need(diagnostics['available'] is True and diagnostics['structural_valid'] is True
        and diagnostics['source_valid'] is True and not diagnostics['issues'],
        '阶段或原文依据仍有待处理项，请先修订并保存', 422)
    if review_mode == 'author_self_review':
        need(not saved['value']['business']['questions'],
            '还有待核问题，须处理后才能记录作者核对；工程模拟不会代替人审', 422)
    original = copy.deepcopy(row.data['preview']['source_context'])
    fresh = current.load_source_context(db, principal, row.producer_id, lock=True)
    need(store_same(original, fresh)
        and loaded['source_binding']['source_snapshot_hash'] == fresh['snapshot']['snapshot_hash'],
        '当前原件、解析或来源已变化，已有稿件保留，请重新核对来源')
    snapshot = {'schema': 'business-flow-nested-review-source-v195', 'attempt_id': attempt_id,
        'source_binding_hash': digest(loaded['source_binding']), 'value_hash': digest(saved['value']),
        'diagnostic_context_hash': digest(loaded['diagnostic_context']),
        'original_source_hash': digest(original), 'current_source_hash': digest(fresh),
        'current_materials_hash': digest(fresh['snapshot']['materials']), 'review_mode': review_mode}
    return snapshot, loaded
