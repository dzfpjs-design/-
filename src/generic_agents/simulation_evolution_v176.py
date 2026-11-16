"""Durable SIM labels and genuinely local, temporally separated rule comparison.

This consumer never invokes a Gateway, modifies an Observation, publishes a
rule, replaces its extractor or admits SIM labels into the V147 manual lane.
"""
import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ConfigDict, Field, StrictStr, field_validator
from sqlalchemy import select, text

from .contracts import Contract
from .db import session
from .models import Audit, Capability, DomainInstance, Job, Observation, uid
from .relations import lock_current_actor
from .security import Principal, authenticated, require
from .services import audit, published, scoped
from .simulation_models_v176 import SimulationFeedbackV176 as Feedback
from .simulation_evolution_models_v176 import (SimulationStudyV176 as Study,
    SimulationEvolutionCommandV176 as Command)
from .typed_payload_codec_v131 import canonical_json

router = APIRouter(prefix='/api/workspace/simulation-v176')
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
SCOPE = {'simulation': True, 'origin_kind': 'simulated_equipment',
    'field_verified': False, 'test_only': True, 'production_activation': False}
LABELS = frozenset({'should_alert', 'should_not_alert', 'uncertain'})
PREFIX = '/api/workspace/simulation-v176'
AUDIT = 'simulation.evolution.command.saved_v176'
MAX_BYTES = 128 * 1024


def need(condition, message='模拟反馈、原版本或独立来源无法核对；原记录保留', code=409):
    if not condition:
        raise HTTPException(code, message)


def hash_value(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def same(left, right):
    return canonical_json(left) == canonical_json(right)


def canonical_uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError('请保留原小写UUID编号')
    return value


def finite(value):
    try:
        return type(value) in {int, float} and math.isfinite(value) and abs(value) <= 1e15
    except (ValueError, TypeError, OverflowError):
        return False


class StrictContract(Contract):
    model_config = ConfigDict(extra='forbid', strict=True)


class RequestBody(StrictContract):
    request_id: StrictStr

    @field_validator('request_id', 'observation_id', 'environment_id', 'feedback_id', check_fields=False)
    @classmethod
    def identity(cls, value):
        return canonical_uuid(value)

    @field_validator('reason', 'title', check_fields=False)
    @classmethod
    def meaningful(cls, value):
        canonical_json(value)
        if not value.strip():
            raise ValueError('请填写有内容的说明')
        return value


class FeedbackBody(RequestBody):
    expected_version: int = Field(strict=True, ge=0)
    observation_id: StrictStr
    label: Literal['should_alert', 'should_not_alert', 'uncertain']
    reason: StrictStr = Field(min_length=1, max_length=2000)


class FeedbackRef(StrictContract):
    feedback_id: StrictStr
    expected_version: int = Field(strict=True, ge=1)

    @field_validator('feedback_id')
    @classmethod
    def identity(cls, value):
        return canonical_uuid(value)


class StudyBody(RequestBody):
    environment_id: StrictStr
    title: StrictStr = Field(min_length=1, max_length=200)
    feedback_refs: list[FeedbackRef] = Field(min_length=1, max_length=100)
    candidate_threshold: int | float
    reason: StrictStr = Field(min_length=1, max_length=2000)

    @field_validator('candidate_threshold', mode='before')
    @classmethod
    def numeric(cls, value):
        if not finite(value):
            raise ValueError('候选阈值必须为有限原始数值，不接受布尔值或文字')
        return value

    @field_validator('feedback_refs')
    @classmethod
    def unique(cls, value):
        if len({item.feedback_id for item in value}) != len(value):
            raise ValueError('一条反馈不能重复列入比较')
        return value


class EvaluateBody(RequestBody):
    expected_version: int = Field(strict=True, ge=1)
    expected_study_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')


def read_wire(raw):
    """Check original bytes before Pydantic can coerce or discard information."""
    try:
        need(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, '请求须为128KB以内的原始JSON', 422)
        def pairs(items):
            value = {}
            for key, item in items:
                if key in value:
                    raise ValueError('重复字段')
                value[key] = item
            return value
        exact = raw.decode('utf-8', 'strict')
        value = json.loads(exact, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非有限数值')))
        canonical_json(value)
        need(type(value) is dict, '请保留完整JSON对象', 422)
        canonical_uuid(value.get('request_id'))
        return value, exact, hashlib.sha256(raw).hexdigest()
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise HTTPException(422, '原始请求的JSON、编号或数值类型不符合要求') from None


def numeric_rule(manifest):
    """Same strict gt/lt calculator as numeric_rules; one actual saved field."""
    try:
        need(type(manifest) is dict)
        execution, policy = manifest['execution'], manifest['data_policy']
        rules = manifest['specification']['rules']
        need(execution.get('adapter') == 'builtin' and execution.get('handler') == 'numeric_rules'
             and execution.get('side_effect', False) is False and policy.get('cloud_allowed') is False
             and 'perception' in manifest['allowed_agent_roles']
             and type(rules) is list and len(rules) == 1,
             '本轮仅支持原已发布本地单字段数值规则，不适用于多规则或云模型')
        rule = rules[0]
        need(type(rule) is dict and type(rule.get('id')) is str and bool(rule['id'])
             and type(rule.get('field')) is str and bool(rule['field'])
             and rule.get('operator') in {'gt', 'lt'} and finite(rule.get('threshold')))
        canonical_json(manifest)
        return copy.deepcopy(rule)
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        raise HTTPException(409, '原数值规则结构无法核对') from None


def candidate_manifest(baseline, threshold):
    rule = numeric_rule(baseline)
    need(finite(threshold), '候选阈值须为有限数值', 422)
    need(not same(rule['threshold'], threshold), '候选阈值与原阈值完全相同，请说明有区别的候选方案', 422)
    candidate = copy.deepcopy(baseline)
    candidate['specification']['rules'][0]['threshold'] = copy.deepcopy(threshold)
    numeric_rule(candidate)
    return candidate


def matched(rule, value):
    need(finite(value), '样本字段不是有效原始数值，不能当作没有报警')
    return value > rule['threshold'] if rule['operator'] == 'gt' else value < rule['threshold']


def _time(value):
    need(type(value) is str)
    try:
        result = datetime.fromisoformat(value)
        need(result.tzinfo is not None and result.utcoffset() is not None)
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(409, '原模拟取样时间缺少明确时区，不能分隔训练与验证') from None


def _metrics(rows, side):
    counts = {'tp': 0, 'tn': 0, 'fp': 0, 'fn': 0, 'uncertain': 0}
    for row in rows:
        if row['label'] == 'uncertain':
            counts['uncertain'] += 1
        elif row['label'] == 'should_alert':
            counts['tp' if row[side + '_matched'] else 'fn'] += 1
        else:
            counts['fp' if row[side + '_matched'] else 'tn'] += 1
    count = counts['tp'] + counts['tn'] + counts['fp'] + counts['fn']
    return {**counts, 'labeled_count': count,
        'accuracy': (counts['tp'] + counts['tn']) / count if count else None,
        'class_coverage': bool(counts['tp'] + counts['fn']) and bool(counts['tn'] + counts['fp'])}


def evaluate_frozen(frozen):
    """Pure local comparison; a result is evidence, never a production grant.

    The candidate is fixed before splitting; this function does not fit it on
    validation data or describe a four-sample mechanism check as generalization.
    Equal sample instants stay on one side of the split, including uncertains.
    """
    need(type(frozen) is dict and frozen.get('schema') == 'simulation-study-freeze-v176')
    need(all(type(frozen.get(k)) is type(v) and frozen[k] == v for k, v in SCOPE.items()))
    baseline, candidate = frozen['baseline_manifest'], frozen['candidate_manifest']
    rule, other = numeric_rule(baseline), numeric_rule(candidate)
    left, right = copy.deepcopy(baseline), copy.deepcopy(candidate)
    del left['specification']['rules'][0]['threshold']; del right['specification']['rules'][0]['threshold']
    need(same(left, right), '候选只能改变原单字段阈值，不能改变字段、方向、单位或原模型')
    samples = frozen['samples']; need(type(samples) is list and bool(samples))
    need(frozen.get('sample_inventory_hash') == hash_value(samples), '冻结样本清单摘要不一致')
    seen = {key: set() for key in ('observation_id', 'sample_id', 'invocation_id', 'feedback_id')}
    ordered = []
    for item in samples:
        need(type(item) is dict and item.get('label') in LABELS and item.get('field') == rule['field'])
        for key, values in seen.items():
            identity = item.get(key); need(type(identity) is str and bool(identity) and identity not in values,
                '重复来源不能当作独立训练或验证样本')
            values.add(identity)
        need(type(item.get('reason')) is str and bool(item['reason'].strip())
             and type(item.get('unit')) is str and bool(item['unit'])
             and item['unit'] == samples[0].get('unit')
             and rule.get('unit') in {None, item['unit']})
        value = item['value']; need(finite(value))
        ordered.append((_time(item['observed_at']), {
            'observation_id': item['observation_id'], 'sample_id': item['sample_id'],
            'invocation_id': item['invocation_id'], 'feedback_id': item['feedback_id'],
            'feedback_version': item['feedback_version'], 'feedback_hash': item['feedback_hash'],
            'observed_at': item['observed_at'], 'label': item['label'], 'reason': item['reason'],
            'value': copy.deepcopy(value), 'unit': item['unit'], 'split': None,
            'baseline_matched': matched(rule, value), 'candidate_matched': matched(other, value)}))
    ordered.sort(key=lambda item: (item[0], item[1]['observation_id']))
    boundaries = []
    for split in range(1, len(ordered)):
        train = sum(row['label'] != 'uncertain' for _, row in ordered[:split])
        validation = sum(row['label'] != 'uncertain' for _, row in ordered[split:])
        if train >= 2 and validation >= 2 and ordered[split - 1][0] < ordered[split][0]:
            boundaries.append((abs(train - validation), split))
    base = {'schema': 'simulation-local-threshold-result-v176', **SCOPE,
        'study_hash': hash_value(frozen), 'method': 'local_registered_numeric_rules_gt_lt',
        'candidate_selection': 'human_proposed_before_temporal_split',
        'sample_time_basis': 'original_simulator_sample_time', 'source_time_verified': False,
        'cloud_calls': 0, 'tool_calls': 0, 'observation_writes': 0,
        'production_rule_changes': 0, 'original_extractor_replaced': False,
        'generalization_verified': False, 'field': rule['field'],
        'baseline_threshold': rule['threshold'], 'candidate_threshold': other['threshold'],
        'split': None, 'training': None, 'validation': None, 'recommendation': 'inconclusive',
        'limitations': ['仅用于同一模拟环境、同一对象与同一原数值规则的机制比较；没有现场泛化或模型训练结论'],
        'shadow_started': False, 'production_activation': False}
    if not boundaries:
        return {**base, 'status': 'blocked', 'issue': '需至少4条独立、明确标注的取样，按原取样时间分为训练与验证各至少2条；同一时间不得跨组',
            'accuracy': None, 'per_sample': [row for _, row in ordered],
            'split_hash': None}
    _, split = min(boundaries)
    rows = [row for _, row in ordered]
    for index, row in enumerate(rows):
        row['split'] = 'training' if index < split else 'validation'
    partition = {'training_observation_ids': [row['observation_id'] for row in rows[:split]],
        'validation_observation_ids': [row['observation_id'] for row in rows[split:]],
        'training_latest': rows[split - 1]['observed_at'], 'validation_earliest': rows[split]['observed_at'],
        'uncertain_excluded_from_accuracy': True, 'overlap': False}
    training = {side: _metrics(rows[:split], side) for side in ('baseline', 'candidate')}
    validation = {side: _metrics(rows[split:], side) for side in ('baseline', 'candidate')}
    b, c = validation['baseline'], validation['candidate']
    coverage = all(values['baseline']['class_coverage'] for values in (training, validation))
    recommendation = 'inconclusive'
    if coverage:
        recommendation = ('consider_simulation_shadow_only' if c['accuracy'] > b['accuracy']
            and c['fn'] <= b['fn'] and c['fp'] <= b['fp'] else 'retain_baseline')
    if not coverage:
        base['limitations'].append('训练或验证缺少正例/反例，实际计数可查，但不足以提出阈值改进结论')
    return {**base, 'status': 'completed', 'issue': None, 'split': partition,
        'split_hash': hash_value(partition), 'training': training, 'validation': validation,
        'per_sample': rows, 'recommendation': recommendation}


def _snapshot(row, kind):
    value = {'id': row.id, 'workspace_id': row.workspace_id, 'actor_id': row.actor_id,
        'version': row.version, 'environment_id': row.environment_id,
        'data': copy.deepcopy(row.data)}
    if kind == 'feedback':
        value['observation_id'] = row.observation_id
    else:
        value.update(status=row.status, frozen_hash=row.frozen_hash)
    return value


def _view(row, kind):
    value = _snapshot(row, kind)
    return {**value, **SCOPE, 'record_hash': hash_value(value)}


def _command_value(row):
    return {key: copy.deepcopy(getattr(row, key)) for key in (
        'id', 'workspace_id', 'actor_id', 'request_id', 'operation', 'path',
        'exact_body', 'body_sha256', 'target_kind', 'target_id', 'target_version', 'data')}


def _verify_command(db, p, row):
    need(row.workspace_id == p.workspace_id and row.command_hash == hash_value(_command_value(row)))
    raw = row.exact_body.encode('utf-8'); body, _, wire_hash = read_wire(raw)
    need(row.body_sha256 == wire_hash and body['request_id'] == row.request_id)
    expected = {'command_hash': row.command_hash, 'body_sha256': row.body_sha256,
        'request_id': row.request_id, 'path': row.path, 'operation': row.operation,
        'target_kind': row.target_kind, 'target_id': row.target_id,
        'target_version': row.target_version, 'state_hash': row.data['result']['record_hash']}
    anchors = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == AUDIT, Audit.object_id == row.id).execution_options(populate_existing=True)))
    need(len(anchors) == 1 and anchors[0].actor_id == row.actor_id and same(anchors[0].data, expected),
        '原请求的独立回执锚点无法核对，未创建新反馈或评估')
    result = row.data['result']; value = {k: v for k, v in result.items() if k not in {*SCOPE, 'record_hash'}}
    journal = result['data']['request_journal']
    need(type(journal) is list and len(journal) == row.target_version and same(journal[-1], {
        'request_id': row.request_id, 'body_sha256': row.body_sha256, 'path': row.path,
        'operation': row.operation, 'command_id': row.id, 'target_kind': row.target_kind,
        'target_id': row.target_id, 'target_version': row.target_version}))
    need(result['record_hash'] == hash_value(value)
         and result['id'] == row.target_id and result['version'] == row.target_version
         and result['workspace_id'] == p.workspace_id)
    return body


def _check_command(db, p, row):
    try:
        return _verify_command(db, p, row)
    except HTTPException:
        raise HTTPException(409, '原请求或独立回执内容无法核对；原历史保留') from None
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        raise HTTPException(409, '原请求或独立回执内容无法核对；原历史保留') from None


def checked_record(db, p, kind, identity, *, lock=False):
    model = Feedback if kind == 'feedback' else Study
    if lock:
        row = scoped(db, model, identity, p, True)
    else:
        # Serialize the row snapshot and its independently committed command
        # inventory with legitimate writers. Refresh an already preloaded ORM
        # object; an old identity-map version is not evidence of corruption.
        row = db.scalar(select(model).where(model.workspace_id == p.workspace_id,
            model.id == identity).with_for_update(read=True)
            .execution_options(populate_existing=True))
        need(row is not None, '记录不存在或不在当前工作区', 404)
    commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.target_kind == kind, Command.target_id == identity).order_by(Command.target_version)
        .execution_options(populate_existing=True)))
    need(type(row.version) is int and len(commands) == row.version and bool(commands),
        '原记录的逐版回执不完整，不能以自摘要替代实际保存历史')
    previous = None
    for index, command in enumerate(commands, 1):
        body = _check_command(db, p, command)
        need(command.target_version == index and command.data['previous_record_hash'] == previous)
        need((kind == 'study' and index == 1 and command.operation == 'study.create')
             or body.get('expected_version') == index - 1)
        previous = command.data['result']['record_hash']
    need(same(commands[-1].data['result'], _view(row, kind)),
        '当前反馈或评估与独立原请求保存状态不同，原历史保留')
    return row


def _lock(db, value):
    db.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': int(hash_value(value)[:15], 16)})


def _nonce_lock(db, p, request_id):
    _lock(db, {'kind': 'simulation-evolution-request', 'workspace': p.workspace_id, 'request': request_id})


def _nonce_residue(db, p, request_id):
    """A missing primary receipt cannot erase an already saved human action."""
    anchors = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == AUDIT, Audit.data['request_id'].as_string() == request_id)))
    if anchors:
        return True
    for model in (Feedback, Study):
        remnants = list(db.scalars(select(model).where(model.workspace_id == p.workspace_id,
            model.data['request_journal'].contains([{'request_id': request_id}]))))
        if remnants:
            return True
    return False


def observation_basis(db, p, identity, *, live):
    """Independent Sample + canonical receipt + pinned SIM capabilities, cold."""
    from .simulation_origin_v176 import guard
    from .simulation_loop_v176 import checked_definition
    observation = scoped(db, Observation, identity, p)
    origin = guard(db, p, observation, execution=False)
    need(type(origin) is dict and origin.get('schema') == 'simulation-origin-v176',
        '仅接受有独立模拟取样来源的观测；真实或人工上报不能进入此通道')
    if live:
        list(db.scalars(select(DomainInstance).where(DomainInstance.workspace_id == p.workspace_id,
            DomainInstance.id == origin['instance_id']).with_for_update(read=True)
            .execution_options(populate_existing=True)))
    definition, env, instance, release = checked_definition(db, p, origin['definition_id'], live=live)
    need(origin['environment_id'] == env.id and origin['instance_id'] == instance.id
         and origin['release_id'] == release.id)
    if live:
        # Keep publication/pins stable through the label/study transaction.
        pins = definition.data['pins']
        list(db.scalars(select(Capability).where(Capability.workspace_id == p.workspace_id,
            Capability.id.in_(sorted(pins))).order_by(Capability.id).with_for_update(read=True)
            .execution_options(populate_existing=True)))
        for cap_id, pin in pins.items():
            need(published(db, p, cap_id).content_hash == pin)
        # Re-read the independent origin after acquiring current capability locks.
        origin = guard(db, p, observation, execution=False)
    extractor = scoped(db, Capability, origin['extractor_id'], p)
    rule = numeric_rule(extractor.data)
    attributes = observation.data['attributes']; field = rule['field']
    need(field in attributes and finite(attributes[field]) and field in definition.data['fields'])
    unit = observation.data['field_units'][field]
    need(rule.get('unit') in {None, unit})
    from .simulation_models_v176 import SimulationSampleV176 as Sample
    sample = scoped(db, Sample, origin['sample_id'], p)
    process = scoped(db, Job, sample.data['processing_job_id'], p)
    need(observation.status == 'processed' and process.status == 'succeeded',
        '原模拟观测尚未完成提取，不能作为已标注的有效比较样本')
    expected = {'attributes': copy.deepcopy(attributes), 'anomalies': [], 'method': 'registered_numeric_rules'}
    if matched(rule, attributes[field]):
        expected['anomalies'].append({'field': field, 'value': attributes[field],
            'rule_id': rule['id'], 'severity': rule.get('severity', 'warning')})
    need(same(observation.data.get('extracted'), expected), '原实际数值提取回执与原规则不一致')
    return {'schema': 'simulation-feedback-basis-v176', **SCOPE,
        'environment_id': env.id, 'entity_id': observation.entity_id,
        'observation_id': observation.id, 'sample_id': sample.id,
        'invocation_id': sample.invocation_id, 'source_id': observation.source_id,
        'extractor_id': extractor.id, 'extractor_hash': extractor.content_hash,
        'origin': copy.deepcopy(origin), 'origin_hash': hash_value(origin),
        'observed_at': observation.data['original_request']['observed_at'],
        'time_semantics': 'simulator_sample_time', 'source_time_verified': False,
        'field': field, 'value': copy.deepcopy(attributes[field]), 'unit': unit,
        'baseline_manifest': copy.deepcopy(extractor.data), 'extracted_hash': hash_value(expected)}


def save_feedback(db, p, body):
    _lock(db, {'kind': 'simulation-feedback', 'workspace': p.workspace_id,
        'observation': body.observation_id, 'actor': p.user_id})
    rows = list(db.scalars(select(Feedback).where(Feedback.workspace_id == p.workspace_id,
        Feedback.observation_id == body.observation_id, Feedback.actor_id == p.user_id).with_for_update()))
    need(len(rows) <= 1)
    basis = observation_basis(db, p, body.observation_id, live=True)
    if rows:
        row = checked_record(db, p, 'feedback', rows[0].id, lock=True)
        need(row.version == body.expected_version, '反馈已修改，请核对原版本后再保存')
        need(same(row.data['basis'], basis), '原反馈的取样来源已变化，不能替换成其他证据')
        previous = _view(row, 'feedback')['record_hash']; row.version += 1
        journal = copy.deepcopy(row.data['request_journal'])
    else:
        need(body.expected_version == 0, '此作者尚无该观测的反馈，请以新反馈版本0提交')
        row = Feedback(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
            environment_id=basis['environment_id'], observation_id=body.observation_id, version=1, data={})
        db.add(row); previous = None; journal = []
    row.data = {'schema': 'simulation-feedback-record-v176', **SCOPE,
        'basis': basis, 'label': body.label, 'reason': body.reason, 'request_journal': journal}
    return row, 'feedback', previous


def create_study(db, p, body):
    from .simulation_loop_v176 import environment
    environment(db, p, body.environment_id, live=True)
    refs = sorted(body.feedback_refs, key=lambda item: item.feedback_id)
    frozen = []; bases = []
    # Lock feedback in stable order; later edits cannot race the freeze.
    for ref in refs:
        feedback = checked_record(db, p, 'feedback', ref.feedback_id, lock=True)
        need(feedback.environment_id == body.environment_id and feedback.version == ref.expected_version,
            '反馈环境或版本已变化，请核对后建立新比较')
        basis = observation_basis(db, p, feedback.observation_id, live=True)
        need(same(basis, feedback.data['basis']))
        bases.append(basis)
        frozen.append({'feedback_id': feedback.id, 'feedback_version': feedback.version,
            'feedback_hash': _view(feedback, 'feedback')['record_hash'],
            'feedback_actor_id': feedback.actor_id, 'label': feedback.data['label'],
            'reason': feedback.data['reason'], **{key: copy.deepcopy(basis[key]) for key in (
                'observation_id', 'sample_id', 'invocation_id', 'observed_at', 'field', 'value', 'unit')},
            'basis': copy.deepcopy(basis), 'basis_hash': hash_value(basis)})
    baseline = bases[0]['baseline_manifest']
    for basis in bases:
        need(basis['environment_id'] == body.environment_id and basis['entity_id'] == bases[0]['entity_id']
             and basis['extractor_id'] == bases[0]['extractor_id']
             and same(basis['baseline_manifest'], baseline),
             '本轮比较仅限同环境、同对象、同一原提取器的独立取样，不能据此宣称跨对象泛化')
    for key in ('observation_id', 'sample_id', 'invocation_id'):
        need(len({item[key] for item in frozen}) == len(frozen), '多人对同一取样的意见不能充作多个独立样本')
    freeze = {'schema': 'simulation-study-freeze-v176', **SCOPE,
        'environment_id': body.environment_id, 'entity_id': bases[0]['entity_id'],
        'extractor_id': bases[0]['extractor_id'], 'extractor_hash': bases[0]['extractor_hash'],
        'title': body.title, 'reason': body.reason,
        'baseline_manifest': copy.deepcopy(baseline),
        'candidate_manifest': candidate_manifest(baseline, body.candidate_threshold),
        'samples': frozen, 'sample_inventory_hash': hash_value(frozen),
        'candidate_selection': 'human_proposed_before_temporal_split'}
    row = Study(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        environment_id=body.environment_id, version=1, status='frozen',
        frozen_hash=hash_value(freeze), data={'schema': 'simulation-study-record-v176',
            **SCOPE, 'freeze': freeze, 'result': None, 'request_journal': []})
    db.add(row)
    return row, 'study', None


def evaluate_study(db, p, identity, body):
    row = checked_record(db, p, 'study', identity, lock=True)
    need(row.actor_id == p.user_id or p.role == 'admin', '仅比较方案原作者或管理员可以发起比较', 403)
    need(row.version == body.expected_version and row.frozen_hash == body.expected_study_hash
         and row.frozen_hash == hash_value(row.data['freeze']), '原比较版本或冻结摘要不一致')
    need(row.status == 'frozen', '此比较已有结果；查询原结果，不重复计算或覆盖')
    frozen = row.data['freeze']
    need(frozen['environment_id'] == row.environment_id
         and frozen['sample_inventory_hash'] == hash_value(frozen['samples']))
    for item in frozen['samples']:
        feedback = checked_record(db, p, 'feedback', item['feedback_id'], lock=True)
        need(feedback.version == item['feedback_version']
             and _view(feedback, 'feedback')['record_hash'] == item['feedback_hash'],
             '原反馈已有修订，旧冻结稿保留；请引用新版本另建比较')
        basis = observation_basis(db, p, feedback.observation_id, live=True)
        need(same(basis, item['basis']) and item['basis_hash'] == hash_value(basis)
             and feedback.environment_id == row.environment_id)
    previous = _view(row, 'study')['record_hash']
    result = evaluate_frozen(frozen)
    row.data = {**row.data, 'result': result, 'result_hash': hash_value(result)}
    row.status = result['status']; row.version += 1
    return row, 'study', previous


async def command(request, p, db, operation, model, apply):
    raw = await request.body(); wire, exact, wire_hash = read_wire(raw)
    request_id = wire['request_id']
    _nonce_lock(db, p, request_id)
    prior = db.scalar(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.request_id == request_id).execution_options(populate_existing=True))
    if prior:
        need(prior.actor_id == p.user_id, '仅原提交账号可以恢复这条请求', 403)
        need(prior.path == request.url.path and prior.operation == operation
             and prior.exact_body == exact and prior.body_sha256 == wire_hash,
             '此请求号已有不同正文或操作；未重复保存或运行')
        _check_command(db, p, prior)
        return copy.deepcopy(prior.data['result'])
    need(not _nonce_residue(db, p, request_id),
         '原请求仍有独立回执或目标记录，主命令缺失；禁止重建或返回未保存，请恢复原记录')
    try:
        body = model.model_validate(wire)
    except (ValueError, TypeError):
        raise HTTPException(422, '请求字段、原始数值类型或版本不符合约定') from None
    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    row, kind, previous = apply(body)
    receipt_id = uid()
    journal = copy.deepcopy(row.data['request_journal'])
    need(len(journal) == row.version - 1, '目标记录的原请求链不完整，未继续新操作')
    journal.append({'request_id': request_id, 'body_sha256': wire_hash, 'path': request.url.path,
        'operation': operation, 'command_id': receipt_id, 'target_kind': kind,
        'target_id': row.id, 'target_version': row.version})
    row.data = {**row.data, 'request_journal': journal}
    result = _view(row, kind)
    receipt = Command(id=receipt_id, workspace_id=p.workspace_id, actor_id=p.user_id,
        request_id=request_id, operation=operation, path=request.url.path,
        exact_body=exact, body_sha256=wire_hash, target_kind=kind,
        target_id=row.id, target_version=row.version, data={
            'schema': 'simulation-evolution-command-v176', **SCOPE,
            'previous_record_hash': previous, 'result': result})
    receipt.command_hash = hash_value(_command_value(receipt))
    db.add(receipt)
    audit(db, p, AUDIT, receipt.id, {'command_hash': receipt.command_hash,
        'body_sha256': wire_hash, 'request_id': request_id, 'path': request.url.path, 'operation': operation,
        'target_kind': kind, 'target_id': row.id, 'target_version': row.version,
        'state_hash': result['record_hash']})
    db.commit()
    return result


@router.post('/feedback')
async def feedback(request: Request, p: P, db: DB):
    return await command(request, p, db, 'feedback.save', FeedbackBody,
        lambda body: save_feedback(db, p, body))


@router.get('/feedback')
def feedback_list(p: P, db: DB, environment_id: str, limit: int = Query(default=100, ge=1, le=100), observation_id: str | None = None):
    from .simulation_loop_v176 import environment
    environment(db, p, environment_id, live=False)
    statement = select(Feedback).where(Feedback.workspace_id == p.workspace_id,
        Feedback.environment_id == environment_id)
    if observation_id is not None:
        basis = observation_basis(db, p, observation_id, live=False)
        need(basis['environment_id'] == environment_id)
        statement = statement.where(Feedback.observation_id == observation_id, Feedback.actor_id == p.user_id)
    rows = list(db.scalars(statement.order_by(Feedback.created_at.desc(), Feedback.id).limit(limit)))
    results = {}
    # Acquire multiple reader locks in the same ID order as study writers.
    # Rendering order can stay newest first without creating a lock inversion.
    for row in sorted(rows, key=lambda item: item.id):
        row = checked_record(db, p, 'feedback', row.id)
        need(same(observation_basis(db, p, row.observation_id, live=False), row.data['basis']))
        results[row.id] = _view(row, 'feedback')
    return {**SCOPE, 'rows': [results[row.id] for row in rows], 'limit': limit,
        **({'observation_id': observation_id, 'complete': len(rows) < limit} if observation_id is not None else {})}


@router.post('/studies', status_code=201)
async def study(request: Request, p: P, db: DB):
    return await command(request, p, db, 'study.create', StudyBody,
        lambda body: create_study(db, p, body))


@router.get('/studies/{identity}')
def study_detail(identity: str, p: P, db: DB):
    row = checked_record(db, p, 'study', identity)
    # Cold provenance remains checkable when admission expires. No live locks or
    # activation are attempted in this historical, read-only view.
    for item in row.data['freeze']['samples']:
        need(same(observation_basis(db, p, item['observation_id'], live=False), item['basis']))
    return _view(row, 'study')


@router.post('/studies/{identity}/evaluate')
async def evaluate(identity: str, request: Request, p: P, db: DB):
    return await command(request, p, db, 'study.evaluate', EvaluateBody,
        lambda body: evaluate_study(db, p, identity, body))


@router.get('/evolution/requests/{request_id}')
def original_request(request_id: str, p: P, db: DB,
                     expected_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
    try:
        canonical_uuid(request_id)
    except (TypeError, ValueError):
        raise HTTPException(422, '请保留原小写UUID请求号') from None
    _nonce_lock(db, p, request_id)
    row = db.scalar(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.request_id == request_id).execution_options(populate_existing=True))
    if row is None:
        need(not _nonce_residue(db, p, request_id),
             '原请求仍有独立回执或目标记录，主命令缺失；不能报告未保存，请恢复原记录')
        return {**SCOPE, 'found': False, 'request_id': request_id}
    need(row.actor_id == p.user_id, '仅原提交账号可以恢复这条请求', 403)
    need(row.body_sha256 == expected_sha256, '原请求正文摘要不一致')
    _check_command(db, p, row)
    return {**SCOPE, 'found': True, 'request_id': request_id, 'operation': row.operation,
        'target_id': row.target_id, 'body_sha256': row.body_sha256,
        'result': copy.deepcopy(row.data['result'])}
