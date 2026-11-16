"""Read-only threshold-study projections; cached history grants no authority.

The complete study/feedback inventories are checked outside current-source
fallbacks. Human text is retained when source eligibility fails; no reader here
writes, repairs, commits, invokes a capability or grants trial permission.
"""
import copy
from uuid import UUID

from fastapi import HTTPException

from .typed_payload_codec_v131 import canonical_json


ERROR = '候选、评估或人工判断库存无法完整核对；原记录保留'
_UNAVAILABLE = '原处理依据目前不可用于此研究，人工判断与历史保留'
_LIMIT = 200


def _need(value, message=ERROR, status=409):
    if not value:
        raise HTTPException(status, message)


def _same(left, right):
    return canonical_json(left) == canonical_json(right)


def _business_issue(error):
    from . import threshold_evolution_api_v147 as feedback
    from . import threshold_study_store_v149 as store
    if isinstance(error, store.StudyInventoryError):
        raise error
    if error.status_code not in {404, 409, 422}:
        raise error
    if error.detail in (feedback.ERROR, feedback.LEGACY_ERROR, store.ERROR):
        raise error
    return error.detail if type(error.detail) is str and error.detail.strip() else _UNAVAILABLE


def _can_manage(p, row):
    return p.role in {'admin', 'operator'} and (p.role == 'admin' or row.actor_id == p.user_id)


def rule_summary(cap_id, manifest, field_label=None):
    from .threshold_study_basis_v149 import _managed
    try:
        _need(type(cap_id) is str and str(UUID(cap_id)) == cap_id)
        saved = _managed(manifest)
    except ValueError:
        raise HTTPException(409, ERROR) from None
    rule = saved['specification']['rules'][0]
    metadata = saved['specification']['managed_numeric_rule']
    schema = saved['input_schema']['properties']['attributes']['properties'][rule['field']]
    label = field_label if type(field_label) is str and field_label.strip() else schema.get('title')
    if type(label) is not str or not label.strip():
        label = rule['field']
    return {'id': cap_id, 'title': metadata['title'], 'field': rule['field'], 'field_label': label,
        'operator': rule['operator'], 'threshold': copy.deepcopy(rule['threshold']), 'unit': rule['unit']}


def _entity_label(db, p, identity, package_id):
    from .instance_domain import object_display
    from .models import Capability, Entity
    from .services import scoped
    entity = scoped(db, Entity, identity, p)
    package = scoped(db, Capability, package_id, p)
    label = object_display(entity, package)['label']
    _need(type(label) is str and bool(label.strip()))
    try:
        is_internal = str(UUID(label)) == label
    except ValueError:
        is_internal = False
    return '未命名对象' if is_internal else label[:300]


def sample_summary(db, p, sample, eligible=True, issue=None):
    _need(type(sample) is dict and set(sample) == {
        'id', 'feedback_snapshot', 'basis', 'producer_manifest', 'fingerprints'})
    saved, basis = sample['feedback_snapshot'], sample['basis']
    _need(saved['workspace_id'] == p.workspace_id and saved['id'] == sample['id']
        and saved['status'] == 'submitted' and saved['observation_id'] == basis['observation_id']
        and saved['baseline_capability_id'] == basis['baseline_capability_id'])
    label = saved['data']['label']
    _need(label in {'should_alert', 'should_not_alert', 'uncertain'} and type(eligible) is bool)
    reason = saved['data']['reason']
    _need(type(reason) is str and bool(reason.strip()))
    package_id = sample['producer_manifest']['specification']['managed_numeric_rule']['package_id']
    try:
        entity_label = _entity_label(db, p, basis['entity_id'], package_id)
    except HTTPException as error:
        if error.status_code != 404:
            raise
        entity_label, eligible, issue = '原对象资料暂不可读取', False, _UNAVAILABLE
    if not eligible:
        issue = issue if type(issue) is str and issue.strip() else _UNAVAILABLE
    else:
        _need(issue is None)
    classification = 'unknown'
    if eligible and label != 'uncertain':
        classification = ('TP' if basis['actual_hit'] else 'FN') if label == 'should_alert' else (
            'FP' if basis['actual_hit'] else 'TN')
    return {'id': sample['id'], 'version': saved['version'], 'root_id': saved['root_id'],
        'observation_id': basis['observation_id'], 'entity_id': basis['entity_id'],
        'entity_label': entity_label, 'label': label, 'reason': reason,
        'classification': classification, 'field_label': basis['field_label'],
        'value': copy.deepcopy(basis['actual_value']) if eligible else None,
        'unit': basis['unit'], 'eligible': eligible, 'issue': issue}


def _checked_feedback_rows(db, p, refs):
    """Inventory failures must not become a harmless current-source issue."""
    from . import threshold_evolution_api_v147 as feedback
    from .threshold_feedback_history_v148 import inventory_chain
    rows = []
    for ref in refs:
        row = feedback._feedback_row(db, p, ref['feedback_id'])
        chain = inventory_chain(db, p, row.root_id)
        actual = next((item for item in chain if item.id == row.id), None)
        _need(actual is not None)
        rows.append(actual)
    return rows


def _saved_samples(db, p, refs):
    from . import threshold_evolution_api_v147 as feedback
    rows = _checked_feedback_rows(db, p, refs)
    return [{'id': row.id, 'feedback_snapshot': feedback._snapshot(row),
        'basis': copy.deepcopy(row.data['basis']),
        'producer_manifest': copy.deepcopy(row.data['basis']['proof']['extractor']['manifest']),
        'fingerprints': {}} for row in rows]


def _history_manifests(db, p, row):
    from .local_rules_api import _saved_manifest
    from .models import Capability
    from .services import scoped
    frozen = row.data['freeze']
    if frozen is not None:
        return copy.deepcopy(frozen['baseline_manifest']), copy.deepcopy(frozen['candidate_manifest'])
    caps = [scoped(db, Capability, identity, p) for identity in (
        row.baseline_capability_id, row.candidate_capability_id)]
    for cap in caps:
        _saved_manifest(db, p, cap)
    return tuple(copy.deepcopy(cap.data) for cap in caps)


def _field_label(samples):
    return samples[0]['basis']['field_label'] if samples else None


def candidate_view(db, p, row, *, snapshot_only=False, inventory_checked=False):
    from . import threshold_study_store_v149 as store
    _need(type(snapshot_only) is bool and type(inventory_checked) is bool
          and not (snapshot_only and inventory_checked) and row.workspace_id == p.workspace_id)
    if not snapshot_only and not inventory_checked:
        row = store.get_record(db, p, 'candidate', row.id)
    refs = row.data['draft']['feedback_refs']
    frozen = row.data['freeze']
    current_issue, current = None, None
    try:
        current = store.candidate_freeze_data(db, p, row, require_error=frozen is not None)
        if frozen is not None:
            store.need(row.status=='frozen' and store.hash_value(current)==row.data['freeze_hash'],
                       '原训练判断、规则或依据已变化，请另建候选保留原方案')
    except store.StudyInventoryError:
        raise
    except HTTPException as error:
        _checked_feedback_rows(db, p, refs)
        current_issue = _business_issue(error)
    if frozen is not None:
        display=copy.deepcopy(frozen['training'])
        baseline,candidate=copy.deepcopy(frozen['baseline_manifest']),copy.deepcopy(frozen['candidate_manifest'])
    elif current is not None:
        display=current['training'];baseline,candidate=current['baseline_manifest'],current['candidate_manifest']
    else:
        display=_saved_samples(db,p,refs);baseline,candidate=_history_manifests(db,p,row)
    manage = not snapshot_only and _can_manage(p, row)
    source_ok = current is not None and current_issue is None
    has_error = source_ok and any(
        (sample['feedback_snapshot']['data']['label'] == 'should_alert' and not sample['basis']['actual_hit'])
        or (sample['feedback_snapshot']['data']['label'] == 'should_not_alert' and sample['basis']['actual_hit'])
        for sample in current['training'])
    can_freeze = manage and row.status == 'draft' and has_error
    if source_ok and row.status == 'draft' and not has_error:
        current_issue = '至少选择一条最新已提交的误报或漏报判断后再冻结'
    if frozen is None and current is not None:
        display = current['training']
    # A lack of FP/FN blocks candidate freeze but does not erase a currently
    # checked sample's own value. Source failures leave current unset.
    if snapshot_only:
        current_issue = current_issue or '这是原请求保存时的快照；请另行读回当前版本后再操作'
    return {'schema': 'threshold-study-candidate-view-v149', 'workspace_id': p.workspace_id,
        'id': row.id, 'version': row.version, 'status': row.status, 'actor_id': row.actor_id,
        'title': row.data['draft']['title'], 'rationale': row.data['draft']['rationale'],
        'baseline': rule_summary(row.baseline_capability_id, baseline, _field_label(display)),
        'candidate': rule_summary(row.candidate_capability_id, candidate, _field_label(display)),
        'training': [sample_summary(db, p, sample, source_ok,
            None if source_ok else current_issue) for sample in display],
        'freeze_hash': row.data['freeze_hash'], 'current_issue': current_issue,
        'can_edit': manage and row.status == 'draft', 'can_freeze': can_freeze,
        'eligible_for_trial': False}


def evaluation_view(db, p, row, *, snapshot_only=False, inventory_checked=False):
    from . import threshold_study_store_v149 as store
    from .models import Job
    from .services import scoped
    _need(type(snapshot_only) is bool and type(inventory_checked) is bool
          and not (snapshot_only and inventory_checked) and row.workspace_id == p.workspace_id)
    if not snapshot_only and not inventory_checked:
        row = store.get_record(db, p, 'evaluation', row.id)
    candidate = store.get_record(db, p, 'candidate', row.candidate_id)
    _checked_feedback_rows(db, p, candidate.data['draft']['feedback_refs'])
    original = _saved_samples(db, p, row.data['draft']['feedback_refs'])
    frozen = row.data['freeze']
    candidate_frozen = candidate.data['freeze']
    _need(candidate_frozen is not None)
    body = frozen['candidate'] if frozen is not None else {
        'baseline_manifest': candidate_frozen['baseline_manifest'],
        'candidate_manifest': candidate_frozen['candidate_manifest'],
        'training': candidate_frozen['training'], 'baseline_id': candidate.baseline_capability_id,
        'candidate_capability_id': candidate.candidate_capability_id}
    samples = copy.deepcopy(frozen['samples']) if frozen is not None else original
    current_issue, current = None, None
    try:
        current = store.evaluation_freeze_data(db, p, row)
        if frozen is not None:
            store.need(store.hash_value(current) == row.data['freeze_hash'],
                '原标签、规则或处理依据已变化；原比较保留，不能继续执行')
    except store.StudyInventoryError:
        raise
    except HTTPException as error:
        _checked_feedback_rows(db, p, candidate.data['draft']['feedback_refs'])
        _checked_feedback_rows(db, p, row.data['draft']['feedback_refs'])
        current_issue = _business_issue(error)
    if frozen is None and current is not None:
        samples = current['samples']
    if snapshot_only:
        current_issue = current_issue or '这是原请求保存时的快照；请另行读回当前版本后再操作'
    if current_issue is not None and row.data['result'] is not None:
        current_issue = '原比较结果保留；当前依据不可用于此结果，暂不展示历史机器数值。' + current_issue[:1700]
    source_ok = current_issue is None and current is not None
    training = copy.deepcopy(body['training'])
    manage = not snapshot_only and _can_manage(p, row)
    job = None
    if row.data['job_id'] is not None:
        actual = scoped(db, Job, row.data['job_id'], p)
        job = {'id': actual.id, 'status': actual.status, 'attempts': actual.attempts,
            'error': actual.data.get('error')}
    settings = frozen if frozen is not None else row.data['draft']
    return {'schema': 'threshold-study-evaluation-view-v149', 'workspace_id': p.workspace_id,
        'id': row.id, 'version': row.version, 'status': row.status, 'actor_id': row.actor_id,
        'title': row.data['draft']['title'], 'candidate_id': row.candidate_id,
        'baseline': rule_summary(body['baseline_id'], body['baseline_manifest'], _field_label(training)),
        'candidate': rule_summary(body['candidate_capability_id'], body['candidate_manifest'], _field_label(training)),
        'training': [sample_summary(db, p, sample, source_ok,
            None if source_ok else current_issue) for sample in training],
        'samples': [sample_summary(db, p, sample, source_ok,
            None if source_ok else current_issue) for sample in samples],
        'criteria': copy.deepcopy(settings['criteria']), 'purpose': settings['purpose'],
        'limitations': settings['limitations'], 'freeze_hash': row.data['freeze_hash'],
        'job': job, 'progress': {'completed': len(row.data['receipts']), 'total': 2 * len(samples),
            'reserved': len(row.data['reservations'])},
        'result': copy.deepcopy(row.data['result']) if source_ok else None,
        'error': row.data['error'], 'current_issue': current_issue,
        'can_edit': manage and row.status == 'draft',
        'can_freeze': manage and row.status == 'draft' and source_ok,
        'can_run': manage and row.status == 'frozen' and source_ok,
        'can_cancel': manage and row.status in {'frozen', 'queued', 'running'},
        'eligible_for_trial': False}


def _all_feedback_chains(db, p):
    from sqlalchemy import select
    from . import threshold_evolution_api_v147 as feedback
    from .models import Audit
    from .threshold_evolution_models_v147 import FeedbackRevision, ThresholdCommand
    from .threshold_feedback_history_v148 import inventory_chain
    feedback._legacy_audits(db, p)
    records = list(db.scalars(select(FeedbackRevision).where(FeedbackRevision.workspace_id == p.workspace_id)
        .execution_options(populate_existing=True)))
    commands = list(db.scalars(select(ThresholdCommand).where(ThresholdCommand.workspace_id == p.workspace_id)))
    issued = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == feedback.COMMAND_AUDIT)))
    roots = {row.root_id for row in records}
    roots.update(feedback._checked_command(db, p, row)['root_id'] for row in commands)
    roots.update(feedback._checked_audit(p, row)['root_id'] for row in issued)
    _need(len(roots) <= _LIMIT, '人工反馈范围超过本页可核对上限，请先按业务范围整理', 413)
    return [inventory_chain(db, p, identity) for identity in sorted(roots)]


def options_view(db, p, baseline_id):
    from sqlalchemy import select
    from . import threshold_study_store_v149 as store
    from .local_rules_api import NAMESPACE, _saved_manifest, require_current_check
    from .models import Capability
    from .services import published
    from .threshold_study_basis_v149 import _managed, _producer_shape
    baseline_cap = published(db, p, baseline_id, 'tool')
    _saved_manifest(db, p, baseline_cap); require_current_check(db, p, baseline_cap)
    baseline = _managed(baseline_cap.data)
    candidates = []
    choices = list(db.scalars(select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.namespace == NAMESPACE, Capability.kind == 'tool', Capability.status == 'published',
        Capability.data['specification']['managed_numeric_rule']['package_id'].as_string()
            == baseline['specification']['managed_numeric_rule']['package_id'],
        Capability.data['specification']['managed_numeric_rule']['entity_id'].as_string()
            == baseline['specification']['managed_numeric_rule']['entity_id'],
        Capability.data['specification']['managed_numeric_rule']['entity_type'].as_string()
            == baseline['specification']['managed_numeric_rule']['entity_type'],
        Capability.id != baseline_id).order_by(Capability.id)))
    _need(len(choices) <= _LIMIT, '已发布规则超过本页可核对上限，请先按业务范围整理', 413)
    for cap in choices:
        try:
            _, changed = store.current_manifests(db, p, baseline_id, cap.id)
        except store.StudyInventoryError:
            raise
        except HTTPException as error:
            _business_issue(error)
            continue
        candidates.append(rule_summary(cap.id, changed))
    feedbacks = {'training': [], 'evaluation': []}
    chains = _all_feedback_chains(db, p)
    baseline_label = None
    for rows in chains:
        submitted = [row for row in rows if row.status == 'submitted']
        if not submitted:
            continue
        row = submitted[-1]
        original = _saved_samples(db, p, [{'feedback_id': row.id, 'expected_version': row.version}])[0]
        producer = _managed(original['producer_manifest'])
        if _same(producer, baseline):
            partition = 'training'
            baseline_label = baseline_label or original['basis']['field_label']
        elif _producer_shape(producer) == _producer_shape(baseline):
            partition = 'evaluation'
        else:
            continue
        issue, eligible = None, True
        try:
            actual = store.current_samples(db, p, [{'feedback_id': row.id, 'expected_version': row.version}],
                baseline, partition)[0]
        except store.StudyInventoryError:
            raise
        except HTTPException as error:
            _checked_feedback_rows(db, p, [{'feedback_id': row.id, 'expected_version': row.version}])
            issue, eligible, actual = _business_issue(error), False, original
        feedbacks[partition].append(sample_summary(db, p, actual, eligible, issue))
    if baseline_label is not None:
        for candidate in candidates:
            candidate['field_label'] = baseline_label
    return {'schema': 'threshold-study-options-v149', 'workspace_id': p.workspace_id,
        'baseline': rule_summary(baseline_id, baseline, baseline_label),
        'candidates': candidates, 'feedbacks': feedbacks}
