"""Cold source and native transport for independent, partial business designs.

The plan and a scope answer never replace the original report. Read recovery
uses the same reserved call and durable capture; it cannot authorize a retry.
"""
import copy
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

from fastapi import HTTPException

from .config import settings
from .contracts import digest
from .relations import lock_current_actor
from .services import published, require
from . import report_design_saved_source_v174 as source
from . import report_scoped_design_profiles_v182 as profiles


def need(ok, message='业务范围的真实来源或当前资格无法核对'):
    if not ok:
        raise HTTPException(409, message)


def load_source(db, p, producer_id, revision, *, lock=False, current_attempt_id=None):
    require(p, 'admin', 'operator')
    lock_current_actor(db, p)
    draft = source.pipeline.legacy.workspace_draft(db, p, lock=lock)
    need(type(revision) is int and draft.version == revision,
        '配置版本已变化，请保留草稿并核对当前报告版本')
    return source.load_source_context(db, p, producer_id, lock=lock,
        current_scoped_attempt_id=current_attempt_id)


def admit(db, p, plan, body, *, lock=False, attempt=None):
    family=profiles.modules(getattr(body,'generation_profile',profiles.SCOPED))
    prep,native=family.preparation,family.native
    from .report_saved_map_design_runtime_v176 import _indexed_allowance
    context = load_source(db, p, plan.producer_id, body.revision, lock=lock,
        current_attempt_id=attempt.id if attempt is not None and attempt.reserved_call_id else None)
    need(digest(context) == digest(plan.data['source_context']),
        '原报告、声明或来源已变化；保留原分派记录，本次未发送')
    cap = published(db, p, body.model_id, 'foundation_model')
    expected_secret = 'DEEPSEEK_API_KEY' if family.id in {profiles.FLOW_REASONED,profiles.FLOW_CAUSAL,profiles.FLOW_NESTED} else 'DASHSCOPE_API_KEY'
    need(cap.data['execution'].get('secret_ref') == expected_secret
        and cap.data['data_policy'].get('cloud_allowed') is True)
    native.input_policy(cap.data['execution'])
    extra = {}
    if family.id == profiles.FLOW_REVISION:
        from .report_business_flow_revision_source_v185 import load_parent
        extra['parent_source'] = load_parent(db, p, plan, body, context)
    prepared = prep.build_preparation(cap, context, plan.data['plan'], body.scope_id, note=body.note, **extra)
    if family.id in {profiles.FLOW_REASONED,profiles.FLOW_CAUSAL,profiles.FLOW_NESTED}:
        from .report_business_flow_allowance_v187 import allowance as reasoned_allowance
        allowance = reasoned_allowance(p, plan.producer_id, body.revision, body.model_id, body.activity_id)
    else:
        allowance = _indexed_allowance(p, plan.producer_id, body.revision, body.model_id, body.activity_id)
    allowance = {**allowance, 'schema': {profiles.FLOW:'report-business-flow-activity-v182',profiles.FLOW_COMPAT:'report-business-flow-activity-v183',profiles.FLOW_PLAN:'report-business-flow-activity-v184',profiles.FLOW_REVISION:'report-business-flow-activity-v185',
        profiles.FLOW_REASONED:'report-business-flow-activity-v187',profiles.FLOW_CAUSAL:'report-business-flow-activity-v190',profiles.FLOW_NESTED:'report-business-flow-activity-v193',
        profiles.SCOPED:'report-scoped-design-activity-v181'}[family.id],
        'plan_id': plan.id, 'plan_version': plan.version, 'plan_hash': plan.data['plan']['plan_hash'],
        'scope_id': body.scope_id, 'source_snapshot_hash': context['snapshot']['snapshot_hash'],
        'request_hash': prepared['request_hash'], 'note_hash': digest(body.note)}
    count = prepared['input_summary']['prepared_request_bytes'] + 1024
    output = prepared['generation_contract']['dispatch_options']['max_tokens']
    if family.id == profiles.FLOW_REVISION: output = native.output_token_bound(output)
    cost = (Decimal(count) * Decimal(str(allowance['input_usd_per_million']))
        + Decimal(output) * Decimal(str(allowance['output_usd_per_million']))) / Decimal(1000000)
    bound = float(cost.quantize(Decimal('0.000001'), rounding=ROUND_CEILING))
    need(bound <= allowance['maximum_cost_usd'], '单业务完整请求超过费用上限；未截断或发送')
    need(prepared['input_summary']['token_bound'] <= settings().cloud_daily_token_limit,
        '单业务请求超过当前云模型日额度；未发送')
    return {'source_context': context, 'capability': cap, 'preparation': prepared,
        'budget': {'schema': 'report-scoped-design-budget-v181', 'call_limit': 1,
            'token_bound': prepared['input_summary']['token_bound'], 'conservative_cost_usd': bound,
            'maximum_cost_usd': allowance['maximum_cost_usd'],
            'pricing_basis_hash': allowance['pricing_basis_hash'], 'grant_hash': digest(allowance)}}


def _context(row, binding=None):
    from .report_scoped_design_consumer_v181 import journal_binding
    prepared = row.data['preview']['preparation']; spec = prepared['generation_contract']
    policy = copy.deepcopy(spec['input_policy']); need(policy.pop('single_batch') is True)
    return {'binding': binding or journal_binding(row), 'request_hash': prepared['request_hash'],
        'endpoint': spec['endpoint'], 'delivery': spec['delivery'],
        'schema_proof_hash': digest(spec['schema_proof']), 'profile_hash': digest(policy),
        'token_bound': prepared['input_summary']['token_bound'], 'max_bytes': spec['max_response_bytes']}


def _journal_root():
    return Path(settings().storage_dir).absolute() / 'report-scoped-design-journal-v181'


def dispatch(db, p, job, detached, *, request, spec, on_reserved):
    from .gateway import cloud_json
    family=profiles.selected(detached.data['preview']['preparation']);native=family.native
    provider = native.PROVIDER if family.id in {profiles.FLOW_REASONED,profiles.FLOW_CAUSAL,profiles.FLOW_NESTED} else 'qwen'
    from .report_scoped_design_consumer_v181 import gateway_dispatch_identity
    from . import report_evidence_http_capture_v1 as capture
    from . import report_evidence_native_bridge_v1 as bridge
    from . import report_evidence_journal_v2 as journal
    cap = published(db, p, detached.model_id, 'foundation_model')
    need(cap.content_hash == detached.data['preview']['capability']['content_hash'])
    ctx = None
    def reserved(call_id):
        nonlocal ctx
        ctx = _context(detached, on_reserved(call_id))
        return capture.prepare(bridge.root(), ctx)
    def received(content, receipt):
        need(ctx is not None)
        policy = copy.deepcopy(spec['input_policy']); need(policy.pop('single_batch') is True)
        wire, original, _ = bridge.response_context(ctx, provider, native.MODEL, policy, native.STAGE)
        need(content == wire and receipt == original)
        bridge.write_missing_journal(journal, _journal_root(), ctx['binding'], wire, original)
    cloud_json(cap, request['messages'][1:], spec['output_schema'], p, full_report=True,
        evidence_stage=native.STAGE, on_reserved=reserved, on_response=received,
        dispatch_identity=gateway_dispatch_identity(detached))


def read_response(db, p, row, *, recover_saved):
    family=profiles.selected(row.data['preview']['preparation']);native=family.native
    provider = native.PROVIDER if family.id in {profiles.FLOW_REASONED,profiles.FLOW_CAUSAL,profiles.FLOW_NESTED} else 'qwen'
    from . import report_evidence_native_bridge_v1 as bridge
    from . import report_evidence_journal_v2 as journal
    need(row.workspace_id == p.workspace_id and row.actor_id == p.user_id)
    ctx = _context(row)
    policy = copy.deepcopy(row.data['preview']['preparation']['generation_contract']['input_policy'])
    need(policy.pop('single_batch') is True)
    try:
        wire, receipt, state = bridge.response_context(ctx, provider, native.MODEL, policy, native.STAGE)
    except (OSError, ValueError):
        return None
    descriptor = (bridge.write_missing_journal(journal, _journal_root(), ctx['binding'], wire, receipt)
        if recover_saved else journal.read_descriptor(_journal_root(), ctx['binding']))
    loaded = journal.read_response(_journal_root(), ctx['binding'], descriptor)
    need(loaded['content'] == wire and loaded['receipt'] == receipt)
    return {'body': wire.encode('utf-8', errors='surrogateescape'), 'receipt': receipt,
        'capture': state, 'descriptor': descriptor, 'binding': ctx['binding']}
