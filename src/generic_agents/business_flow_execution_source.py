"""An explicitly shared author-reviewed design, with current material checks.

Fresh adoption uses the real author's private inventory. Historical shared
verification uses immutable rows/audits and a DATA owner identity, never an
impersonated Principal or the private API. It acquires no private review mutex
after a configuration/task lock. The caller must first verify the actual
workspace adoption sharing grant; hashes alone do not establish that grant.
"""
import copy
from types import SimpleNamespace
from sqlalchemy import select

from . import business_flow_qualification_store as qualified
from . import business_flow_review_store as reviews
from . import business_flow_review_source as source
from .business_flow_qualification_models import BusinessFlowQualification
from .business_flow_review_models import BusinessFlowReviewCommand, BusinessFlowReviewDraft
from .contracts import digest
from .models import Audit
from .report_full_reading_source import freeze_full_source, check_full_source


def fresh(db, p, qualification_id):
    """Current author's version only. Call before taking configuration locks."""
    qualified._inventory(db, p, write=True)
    value = qualified.get(db, p, qualification_id)
    saved = reviews.get(db, p, value['attempt_id'])
    source.need(saved is not None and saved['version'] == value['draft_version']
        and saved['history'][-1]['request_id'] == value['save_request_id']
        and digest(saved['value']) == value['value_hash'],
        '作者核对后稿件已修订；请核对当前保存版本，旧记录仍保留')
    current = source.current_review_source(db, p, value['attempt_id'], saved)
    source.need(current == value['source_snapshot'], '原来源已变化，请重新核对当前版本')
    loaded = source.load(db, p, value['attempt_id'])
    from .report_evidence_pipeline import owned
    producer = owned(db, p, value['source_binding']['producer_id'], lock=True)
    original = copy.deepcopy(producer.data['preview']['source_snapshot'])
    check_full_source(original)
    material_snapshot = {'schema': 'business-flow-execution-materials-v1',
        'selection': copy.deepcopy(original['selection']), 'snapshot_hash': original['snapshot_hash'],
        'materials': [{**copy.deepcopy(original[k]['ref']), 'name': original[k]['name'],
            'evidence_id': original[k]['evidence_id']} for k in ('original', 'copy')]}
    return {'schema': 'business-flow-execution-source-v1', 'qualification': copy.deepcopy(value),
        'scope_binding': copy.deepcopy(loaded['diagnostic_context']['scope_binding']),
        'material_snapshot': material_snapshot}


def fresh_nested(db, p, qualification_id):
    """Exact saved recursive review with its real review mode and current source.

    An engineering record remains engineering-only. Its presence cannot turn
    the failed original causal protocol into a valid proof or a human review.
    """
    qualified._inventory(db, p, write=True)
    value = qualified.get(db, p, qualification_id)
    source.need(value.get('schema') == 'business-flow-nested-review-result-v195'
        and value.get('value_schema') == 'report-business-flow-nested-plan-v192'
        and value.get('review_mode') in {'author_self_review', 'engineering_simulation'},
        '请选择当前递归流程的明确核对记录', 422)
    engineering = value['review_mode'] == 'engineering_simulation'
    source.need(value.get('simulation_only') is engineering
        and value.get('human_review') == ('not_human_review' if engineering else 'author_self_reviewed')
        and value.get('status') == ('engineering_reviewed' if engineering else 'qualified'))
    saved = reviews.get(db, p, value['attempt_id'])
    source.need(saved is not None and saved['version'] == value['draft_version']
        and saved['history'][-1]['request_id'] == value['save_request_id']
        and digest(saved['value']) == value['value_hash'],
        '核对后稿件已修订，请核对当前保存版本；旧记录保留')
    actual, loaded = source.nested_review_source_bundle(db, p, value['attempt_id'], saved,
        review_mode=value['review_mode'])
    source.need(actual == value['source_snapshot'], '原件或当前来源已变化，请重新核对当前版本')
    from .report_evidence_pipeline import owned
    producer = owned(db, p, value['source_binding']['producer_id'], lock=True)
    original = copy.deepcopy(producer.data['preview']['source_snapshot'])
    check_full_source(original)
    material_snapshot = {'schema': 'business-flow-execution-materials-v1',
        'selection': copy.deepcopy(original['selection']), 'snapshot_hash': original['snapshot_hash'],
        'materials': [{**copy.deepcopy(original[k]['ref']), 'name': original[k]['name'],
            'evidence_id': original[k]['evidence_id']} for k in ('original', 'copy')]}
    return {'schema': 'business-flow-nested-execution-source-v195', 'qualification': copy.deepcopy(value),
        'scope_binding': copy.deepcopy(loaded['diagnostic_context']['scope_binding']),
        'material_snapshot': material_snapshot}


def check_shared_qualification(db, p, expected):
    """Immutable specific version, authorized by an already checked adoption.

    Later author edits do not rewrite an issued release. Reconstruct only its
    immutable save prefix; never claim the current private draft is unchanged.
    Current material eligibility is a separate check below.
    """
    q = db.scalar(select(BusinessFlowQualification).where(
        BusinessFlowQualification.id == expected['id'], BusinessFlowQualification.workspace_id == p.workspace_id))
    source.need(q is not None and q.record_hash == expected['qualification_hash'])
    draft = db.scalar(select(BusinessFlowReviewDraft).where(BusinessFlowReviewDraft.id == q.draft_id,
        BusinessFlowReviewDraft.workspace_id == p.workspace_id, BusinessFlowReviewDraft.actor_id == q.actor_id))
    source.need(draft is not None)
    version = q.data['record']['draft_version']
    commands = list(db.scalars(select(BusinessFlowReviewCommand).where(
        BusinessFlowReviewCommand.draft_id == q.draft_id, BusinessFlowReviewCommand.workspace_id == p.workspace_id,
        BusinessFlowReviewCommand.actor_id == q.actor_id,
        BusinessFlowReviewCommand.draft_version <= version).order_by(BusinessFlowReviewCommand.draft_version)))
    source.need(len(commands) == version and commands[-1].id == q.save_command_id)
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.actor_id == q.actor_id, Audit.action.in_([reviews.COMMAND_AUDIT, reviews.STATE_AUDIT]),
        Audit.data['draft_id'].as_string() == q.draft_id)))
    ids = {c.id for c in commands}
    events = [e for e in events if e.action == reviews.COMMAND_AUDIT and e.object_id in ids
        or e.action == reviews.STATE_AUDIT and type(e.data.get('version')) is int and e.data['version'] <= version]
    latest = commands[-1].data['result']
    frozen = SimpleNamespace(**{key: getattr(draft, key) for key in ('id', 'workspace_id', 'actor_id',
        'attempt_id', 'request_id', 'source_binding_hash', 'baseline_hash', 'status')}, version=version,
        record_hash=digest(commands[-1].data['record_snapshot']), data={**copy.deepcopy(draft.data),
            'command_refs': copy.deepcopy(draft.data['command_refs'][:version]),
            **{key: copy.deepcopy(latest[key]) for key in ('value', 'note', 'diagnostics')}})
    owner = SimpleNamespace(user_id=q.actor_id, workspace_id=p.workspace_id)
    reviews._history(frozen, commands, events, owner)
    qevents = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == qualified.AUDIT, Audit.object_id == q.id)))
    actual = qualified._check(q, qevents, owner, {draft.id: frozen}, {c.id: c for c in commands})
    source.need(reviews._same(actual, expected), '共享流程的作者核对记录或原保存版本无法核对')
    return actual


def current_materials(db, p, package, *, lock=False):
    frozen = package['material_snapshot']
    source.need(type(frozen) is dict and set(frozen) == {'schema', 'selection', 'snapshot_hash', 'materials'}
        and frozen['schema'] == 'business-flow-execution-materials-v1')
    actual = freeze_full_source(db, p, frozen['selection'], lock=lock)
    refs = [{**copy.deepcopy(actual[k]['ref']), 'name': actual[k]['name'],
        'evidence_id': actual[k]['evidence_id']} for k in ('original', 'copy')]
    source.need(actual['snapshot_hash'] == frozen['snapshot_hash'] and reviews._same(refs, frozen['materials']),
        '原件或解析代次已变化，当前执行不能使用旧来源')
    return refs


def verify_material_snapshot(db, p, material):
    """A full-source grant, never the mere needs_input status, permits this view."""
    from .business_flow_execution_adoption import checked_origin
    origins = material['business_flow_execution_sources']
    source.need(type(origins) is list and 1 <= len(origins) <= 20
        and len({digest(x) for x in origins}) == len(origins))
    for origin in origins:
        binding = checked_origin(db, p, origin)
        actual = [x for x in current_materials(db, p, binding['source']) if x['id'] == material['id']]
        source.need(len(actual) == 1 and all(actual[0][key] == material[key]
            for key in ('id', 'version', 'hash', 'evidence_id')),
            '任务的完整原文来源与所采用流程不一致；原记录保留')
