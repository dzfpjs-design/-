"""Cold source admission and pure identity checks for threshold studies.

Saved history is not current sample authority. This module neither creates
records nor calls a gateway; a caller owns its action/transaction/locking and
must retain the returned original snapshots when freezing a study.
"""
import copy
import re
from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import HTTPException

from .typed_payload_codec_v131 import canonical_json


ERROR = '候选规则或独立样本无法完整核对；原记录保留，请核对原处理与已提交判断'
_SAMPLE = {'id', 'feedback_snapshot', 'basis', 'producer_manifest', 'fingerprints'}
_FINGERPRINTS = {'root_id', 'observation_id', 'entity_id', 'source_key', 'input_sha',
    'attributes_sha', 'job_id', 'invocation_id', 'data_event_id', 'observed_at',
    'business_day', 'lineage_scope'}
_SCOPE = {'kind', 'workspace_id', 'package_id', 'entity_type'}
_DUPLICATES = ('root_id', 'observation_id', 'source_key', 'input_sha', 'attributes_sha',
    'job_id', 'invocation_id', 'data_event_id')
_IDENTITIES = ('root_id', 'observation_id', 'entity_id', 'job_id', 'invocation_id', 'data_event_id')
_LABELS = {'root_id': '同一反馈根', 'observation_id': '同一原登记', 'source_key': '同一来源记录',
    'input_sha': '同一原输入', 'attributes_sha': '同一原数值输入', 'job_id': '同一处理任务',
    'invocation_id': '同一调用回执', 'data_event_id': '同一数据事件', 'entity_id': '同一对象'}
_SHA = re.compile(r'[0-9a-f]{64}')


def _need(value):
    if not value:
        raise ValueError(ERROR)


def _uuid(value):
    _need(type(value) is str and str(UUID(value)) == value)
    return value


def _same(left, right):
    return canonical_json(left) == canonical_json(right)


def _managed(value):
    # local_rules composes routers before defining its constants. Keep the
    # saved-manifest implementation import at call time, not router import.
    from .threshold_evolution_gate_v147 import _manifest
    return _manifest(value)[0]


def check_candidate_manifests(baseline, candidate):
    """Admit one real managed shape differing only in a numerical threshold.

    This pure check proves no publication or authoring Audit. Those remain the
    responsibility of the real capability/source reader and study action.
    """
    from .threshold_evolution_gate_v147 import _fixed_manifest
    baseline, candidate = _managed(baseline), _managed(candidate)
    _need(_fixed_manifest(baseline) == _fixed_manifest(candidate))
    original, changed = baseline['specification']['rules'][0], candidate['specification']['rules'][0]
    _need(original['threshold'] != changed['threshold'])
    return {'field': original['field'], 'unit': original['unit'], 'operator': original['operator'],
        'baseline_threshold': copy.deepcopy(original['threshold']),
        'candidate_threshold': copy.deepcopy(changed['threshold']), 'rule_id': original['id']}


def _instant(value):
    _need(type(value) is str)
    parsed = datetime.fromisoformat(value)
    _need(parsed.tzinfo is not None and parsed.utcoffset() is not None)
    return parsed.astimezone(timezone.utc)


def _fingerprints(sample):
    _need(type(sample) is dict and set(sample) == _SAMPLE)
    _uuid(sample['id'])
    _need(all(type(sample[key]) is dict for key in ('feedback_snapshot', 'basis', 'producer_manifest')))
    value = sample['fingerprints']
    _need(type(value) is dict and set(value) == _FINGERPRINTS)
    for key in _IDENTITIES:
        _uuid(value[key])
    source = value['source_key']
    _need(type(source) is list and len(source) == 2)
    _uuid(source[0])
    _need(type(source[1]) is str and 1 <= len(source[1]) <= 200)
    for key in ('input_sha', 'attributes_sha'):
        _need(type(value[key]) is str and _SHA.fullmatch(value[key]) is not None)
    scope = value['lineage_scope']
    _need(type(scope) is dict and set(scope) == _SCOPE
        and scope['kind'] == 'plain_managed_numeric_report')
    _uuid(scope['workspace_id']); _uuid(scope['package_id'])
    _need(type(scope['entity_type']) is str and bool(scope['entity_type'].strip()))
    _need(type(value['business_day']) is str
        and date.fromisoformat(value['business_day']).isoformat() == value['business_day'])
    instant = _instant(value['observed_at'])
    canonical_json(sample)
    return value, instant


def _key(value, field):
    return tuple(value[field]) if field == 'source_key' else value[field]


def independence_issues(training, evaluation):
    """Return technical overlap diagnostics, never physical independence.

    Pure callers may provide empty source dictionaries. Only feedback_samples
    independently establishes their actual DB/receipt/proof authority.
    """
    try:
        _need(type(training) is list and type(evaluation) is list)
        arms = [[_fingerprints(sample) for sample in rows] for rows in (training, evaluation)]
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise ValueError(ERROR) from None
    issues = []
    if not training:
        issues.append('训练反馈为空，不能证明样本隔离')
    if not evaluation:
        issues.append('评估反馈为空，不能证明样本隔离')
    scopes = [value['lineage_scope'] for arm in arms for value, _ in arm]
    if scopes and any(not _same(scopes[0], scope) for scope in scopes[1:]):
        issues.append('样本工作区、业务包或对象类型不同，不能作为同一比较范围')
    for label, samples, arm in zip(('训练', '评估'), (training, evaluation), arms):
        if len({sample['id'] for sample in samples}) != len(samples):
            issues.append(label + '集合重复引用同一人工反馈')
        for field in _DUPLICATES:
            values = [_key(value, field) for value, _ in arm]
            if len(set(values)) != len(values):
                issues.append(label + '集合重复使用' + _LABELS[field])
    if {sample['id'] for sample in training} & {sample['id'] for sample in evaluation}:
        issues.append('训练与评估重复引用同一人工反馈')
    for field in (*_DUPLICATES, 'entity_id'):
        left, right = ({_key(value, field) for value, _ in arm} for arm in arms)
        if left & right:
            issues.append('训练与评估重用' + _LABELS[field])
    if all(arms):
        left, right = ([instant for _, instant in arm] for arm in arms)
        if max(min(left), min(right)) <= min(max(left), max(right)):
            issues.append('训练与评估的原观测时间包络重叠或接触')
    return issues


def _producer_shape(manifest):
    """Exact compatibility whitelist for an independently scoped producer."""
    value = copy.deepcopy(manifest)
    for key in ('name', 'owner', 'description'):
        del value[key]
    metadata = value['specification']['managed_numeric_rule']
    for key in ('title', 'rationale', 'creation_request_id', 'creation_fingerprint', 'entity_id'):
        del metadata[key]
    # Keep the original producer threshold, all field schemas and policy: this
    # is compatibility with the baseline, not permission to use the candidate.
    return canonical_json(value)


def _business_need(value, message=ERROR, status=409):
    if not value:
        raise HTTPException(status, message)


def _references(refs):
    try:
        _need(type(refs) is list and 1 <= len(refs) <= 30)
        seen = set()
        for ref in refs:
            _need(type(ref) is dict and set(ref) == {'feedback_id', 'expected_version'})
            identity = _uuid(ref['feedback_id'])
            _need(type(ref['expected_version']) is int and ref['expected_version'] >= 1)
            _need(identity not in seen)
            seen.add(identity)
        canonical_json(refs)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise HTTPException(422, '请选择1至30条不重复的原反馈及准确版本') from None


def feedback_samples(db, p, refs, baseline_manifest, partition):
    """Read latest submitted, inventory-complete, currently usable feedback.

    No write/commit/repair is performed. A freeze caller must serialize its
    command and feedback scope and retain these original detached snapshots;
    the original saved basis is never rebuilt using today's Observation version.
    """
    from sqlalchemy import select
    from . import threshold_evolution_api_v147 as feedback
    from .contracts import digest
    from .services import published
    from .threshold_evolution_models_v147 import FeedbackRevision
    from .threshold_feedback_history_v148 import inventory_chain, require_current_basis

    _business_need(type(partition) is str and partition in {'training', 'evaluation'}, status=422)
    _references(refs)
    try:
        baseline = _managed(baseline_manifest)
    except ValueError:
        raise HTTPException(422, '原基线不符合受管理的单数值规则契约') from None
    samples, roots, observations = [], set(), set()
    for ref in refs:
        row = feedback._feedback_row(db, p, ref['feedback_id'])
        # Refresh actual rows in an expire_on_commit=False Session before the
        # existing inventory reader examines its complete root and tail.
        list(db.scalars(select(FeedbackRevision).where(FeedbackRevision.workspace_id == p.workspace_id,
            FeedbackRevision.root_id == row.root_id).execution_options(populate_existing=True)))
        rows = inventory_chain(db, p, row.root_id)
        row = next((item for item in rows if item.id == ref['feedback_id']), None)
        _business_need(row is not None and row.version == ref['expected_version'],
            '原反馈版本已改变，请保留草稿并重新选择已提交判断')
        submitted = [item for item in rows if item.status == 'submitted']
        _business_need(bool(submitted) and submitted[-1].id == row.id and row.status == 'submitted',
            '只能使用此反馈根最新正式提交的判断，旧改判或未提交稿不能替代')
        _business_need(row.root_id not in roots and row.observation_id not in observations,
            '同一反馈根或原登记不能重复计作多个样本')
        roots.add(row.root_id); observations.add(row.observation_id)
        require_current_basis(db, p, rows)
        basis = copy.deepcopy(row.data['basis'])
        proof = basis['proof']
        producer = proof['extractor']['manifest']
        try:
            producer = _managed(producer)
            if partition == 'training':
                _need(_same(producer, baseline))
            else:
                _need(_producer_shape(producer) == _producer_shape(baseline))
        except ValueError:
            raise HTTPException(409, '样本原生产规则与冻结基线的对象、字段、单位或阈值不兼容') from None
        metadata = producer['specification']['managed_numeric_rule']
        for identity, kind in ((basis['baseline_capability_id'], 'tool'),
                               (basis['source_id'], 'source'), (metadata['package_id'], 'domain_package')):
            published(db, p, identity, kind)
        original = proof['original_request']
        sample = {'id': row.id, 'feedback_snapshot': feedback._snapshot(row), 'basis': basis,
            'producer_manifest': copy.deepcopy(producer), 'fingerprints': {
                'root_id': row.root_id, 'observation_id': row.observation_id,
                'entity_id': basis['entity_id'],
                'source_key': [basis['source_id'], original['source_record_id']],
                'input_sha': proof['original_input_hash'], 'attributes_sha': digest(original['attributes']),
                'job_id': proof['job']['id'], 'invocation_id': proof['invocation']['id'],
                'data_event_id': proof['receipt']['event_id'], 'observed_at': original['observed_at'],
                'business_day': original['business_day'],
                'lineage_scope': {'kind': 'plain_managed_numeric_report', 'workspace_id': p.workspace_id,
                    'package_id': metadata['package_id'], 'entity_type': metadata['entity_type']},
            }}
        try:
            _fingerprints(sample)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            raise HTTPException(409, ERROR) from None
        samples.append(copy.deepcopy(sample))
    return samples
