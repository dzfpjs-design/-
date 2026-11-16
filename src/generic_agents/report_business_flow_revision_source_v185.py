"""Current server-owned parent for a new, independent revision activity.

Only a terminal, cold-verified V184 original is admitted. Human drafts,
mutable attempt versions and client supplied plans never become model input.
Historical reads do not invoke this fresh source/spending admission.
"""
import copy

from .contracts import digest


def load_parent(db, principal, plan, body, source_context):
    from . import report_scoped_design_consumer_v181 as attempts
    from . import business_flow_review_source as reviews
    from . import report_scoped_design_profiles_v182 as profiles
    from . import report_business_flow_wire_v185 as wire
    from .report_scoped_design_runtime_v181 import need

    row = attempts.checked_attempt(db, principal, body.parent_attempt_id)
    prepared = row.data['preview']['preparation']
    need(profiles.selected(prepared).id == profiles.FLOW_PLAN
        and row.status in {'review', 'needs_clarification', 'reviewed'}
        and row.reserved_call_id is not None and row.data['projection'] is not None,
        '请选择已保存且完整可核对的原始流程计划；失败、未结束或修订候选不能充当原稿')
    need(row.plan_id == plan.id and row.plan_version == body.expected_plan_version
        and row.plan_hash == body.plan_hash and row.scope_id == body.scope_id
        and row.producer_id == plan.producer_id and row.revision == body.revision
        and digest(row.data['preview']['source_context']) == digest(source_context),
        '原计划与当前已确认业务范围或完整来源不一致；原稿和人工工作保留')
    loaded = reviews.load(db, principal, row.id)
    raw_plan = copy.deepcopy(row.data['projection']['raw_plan'])
    binding = {**copy.deepcopy(loaded['source_binding']),
        'schema': 'business-flow-revision-parent-source-v185',
        'preparation_hash': prepared['preparation_hash'],
        'scope_id': row.scope_id, 'plan_id': row.plan_id, 'plan_hash': row.plan_hash,
        'raw_plan_hash': digest(raw_plan), 'scope_binding_hash': digest(prepared['scope_binding'])}
    value = {'schema': 'business-flow-revision-parent-v185', 'binding': binding, 'raw_plan': raw_plan}
    return wire.check_parent_source({**value, 'parent_hash': digest(value)}, prepared['scope_binding'])
