"""Two explicit, durable diagnostic operations; never inference or adoption.

The immutable Study is committed by its POST before a later Evaluate request.
A Study lock permits at most one committed Evaluation, not physically exactly
one CPU execution after an unknown transaction outcome. History never probes a
new runtime; displaying saved numbers still requires all original evidence.
"""
import copy
import hashlib
import json
from datetime import UTC
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import and_, or_, select, text

from . import models as m
from .contracts import digest
from .evidence import evidence_bytes, save_immutable
from .relations import lock_current_actor
from .security import require
from .services import scoped
from .pixel_annotation_models_v256 import PixelAnnotationV256 as Annotation
from .pixel_diagnostic_models_v257 import PixelDiagnosticStudyV257 as Study, PixelDiagnosticEvaluationV257 as Evaluation, PixelDiagnosticCommandV257 as Command
from . import pixel_annotations_v256 as ann
from . import pixel_change_runs_v249 as runs
from . import pixel_diagnostic_protocol_v257 as protocol

CREATED = 'pixel_diagnostic.study_created.v257'
COMPUTED = 'pixel_diagnostic.evaluation_saved.v257'
COMMANDED = 'pixel_diagnostic.command.v257'
ERROR = '原阈值诊断、标注或独立保存依据无法核对；原申请与结果保留'
MAX_RAW = 131072
LIMIT = 1000
REASONS = {
    'annotation_changed': '原标注摘要已变化，请保留原申请，核对标注后明确另拟',
    'study_changed': '原冻结方案摘要不一致，请保留原申请并核对方案',
    'already_evaluated': '这份冻结方案已有保存结果，不重新计算；请打开原结果核对',
    'evaluator_changed': '冻结时的诊断运行环境现不可用或已变化；历史保留，请核对后另拟方案',
    'source_unavailable': '原图、概率或标注原件暂不能完整核对；恢复依据后请明确另拟计算申请',
    'scoring_failed': '本地阈值诊断未能完整保存；原方案和原件保留，请核对后明确重试',
}


def need(ok, message=ERROR, status=409):
    if not ok: raise HTTPException(status, message)


def _json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value: raise ValueError('duplicate JSON key')
            value[key] = item
        return value
    def invalid(_): raise ValueError('nonfinite JSON')
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    pending = [(value, 0)]; visited = 0
    while pending:
        item, depth = pending.pop(); visited += 1
        if visited > 10000 or depth > 32: raise ValueError('JSON structural budget exceeded')
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str or '\x00' in key: raise ValueError('invalid JSON key')
                key.encode('utf-8'); pending.append((child, depth + 1))
        elif type(item) is list: pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if '\x00' in item: raise ValueError('invalid JSON text')
            item.encode('utf-8')
    return value


def parse(raw, operation):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_RAW, '诊断申请为空或超过正文上限', 422)
    try:
        original = raw.decode('utf-8'); value = _json(original)
        model = {'freeze': protocol.Freeze, 'evaluate': protocol.Evaluate}[operation]
        body = model.model_validate(value).model_dump(mode='json')
        return body, original, hashlib.sha256(raw).hexdigest()
    except (ValueError, UnicodeError, RecursionError, KeyError, TypeError, ValidationError):
        raise HTTPException(422, '诊断申请字段、原文或结构不完整，请保留原稿核对') from None


def _raw_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _nonce(db, p, request_id):
    identity = 'pixel-diagnostic-v257\0' + p.workspace_id + '\0' + p.user_id + '\0' + request_id
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),
        {'key': int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], 'big', signed=True)})


def _row(db, p, cls, identity, *, lock=False, internal=False):
    query = select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity)
    if lock: query = query.with_for_update()
    row = db.scalar(query.execution_options(populate_existing=True))
    need(row is not None, ERROR if internal else '未找到此工作区的诊断资料', 409 if internal else 404)
    return row


def _audit(db, p, action, row, origin):
    proof = m.Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=action, object_id=row.id,
        created_at=row.created_at, data=copy.deepcopy(origin))
    db.add(proof); db.flush(); return proof.id


def _proof(db, p, action, row, origin):
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.object_id == row.id).limit(2)))
    need(len(proofs) == 1 and proofs[0].id == row.data['audit_id'] and proofs[0].actor_id == row.actor_id
        and digest(proofs[0].data) == row.record_hash == digest(origin))


def _origin(row, schema):
    return {'schema': schema, 'id': row.id, 'version': row.version, 'workspace_id': row.workspace_id,
        'instance_id': row.instance_id, 'run_id': row.run_id, 'annotation_id': row.annotation_id,
        'actor_id': row.actor_id, 'request_id': row.request_id, 'created_at': row.created_at.astimezone(UTC).isoformat(),
        **({'study_id': row.study_id} if isinstance(row, (Evaluation, Command)) else {}),
        **({'operation': row.operation, 'outcome': row.outcome, 'target_id': row.target_id,
            'raw_sha256': row.raw_sha256} if isinstance(row, Command) else {}),
        'data': {key: value for key, value in row.data.items() if key != 'audit_id'}}


def _scope_meta(db, p, instance_id, run_id, annotation_id, *, lock=False):
    scoped(db, m.DomainInstance, instance_id, p)
    query = select(m.PixelChangeRun).where(m.PixelChangeRun.workspace_id == p.workspace_id, m.PixelChangeRun.id == run_id)
    if lock: query = query.with_for_update(read=True)
    run = db.scalar(query.execution_options(populate_existing=True))
    need(run is not None and run.instance_id == instance_id, '未找到此实例的原比较结果', 404)
    runs.checked(db, p, run, physical=False); need(run.status == 'succeeded')
    ann._inventory(db, p, run_id)
    annotation = _row(db, p, Annotation, annotation_id)
    need(annotation.instance_id == instance_id and annotation.run_id == run_id, '原标注不属于此比较记录', 404)
    body, original, rh = ann.parse(annotation.data['raw'].encode())
    source = annotation.data['source']; result = run.data['result']
    pin = {'id': run.id, 'version': run.version, 'result_hash': run.data['result_hash'], 'width': result['width'], 'height': result['height'],
        'before_sha256': run.data['sources']['before']['sha256'], 'after_sha256': run.data['sources']['after']['sha256'],
        'valid_mask_sha256': result['artifacts']['valid_mask']['sha256']}
    need(annotation.version == 1 and rh == annotation.raw_sha256 and body.request_id == annotation.request_id
        and body.expected_run_version == run.version and body.result_hash == run.data['result_hash']
        and digest(body.model_dump()) == annotation.data['body_hash'] == digest(annotation.data['request'])
        and digest(source) == digest(pin) and digest(ann._origin(annotation)) == annotation.record_hash)
    ann._proof(db, p, ann.SAVED, annotation.id, annotation.actor_id, annotation.data['audit_id'], ann._origin(annotation))
    return run, annotation


def _physical(db, p, instance_id, run_id, annotation_id):
    source = ann._source(db, p, instance_id, run_id)
    annotation = _row(db, p, Annotation, annotation_id, internal=True); ann.checked(db, p, annotation, source)
    run = source[0]
    _, labels = ann._artifact(db, p, annotation, 'labels')
    _, known = ann._artifact(db, p, annotation, 'known_mask')
    valid_ev, valid = runs._artifact(db, p, run, 'valid_mask')
    probability_ev, probability = runs._artifact(db, p, run, 'probability_data')
    _, raw_manifest = runs._artifact(db, p, run, 'manifest')
    try: manifest = _json(raw_manifest.decode('utf-8'))
    except (ValueError, UnicodeError, RecursionError): raise HTTPException(409, ERROR) from None
    need(manifest['schema'] == 'pixel-change-manifest-v249' and manifest['run_id'] == run.id
        and manifest['generation'] == run.data['generation'] and digest(manifest['model_trace']) == digest(run.data['model_trace'])
        and manifest['runtime_hash'] == run.data['runtime_hash'] and digest(manifest['sources']) == digest(run.data['sources']))
    binding = {'workspace_id': p.workspace_id, 'instance_id': instance_id, 'run_id': run_id,
        'run_version': run.version, 'generation': run.data['generation'], 'result_hash': run.data['result_hash'],
        'annotation_id': annotation.id, 'annotation_record_hash': annotation.record_hash, 'annotation_actor_id': annotation.actor_id,
        'width': source[1]['width'], 'height': source[1]['height'], 'before_sha256': source[1]['before_sha256'],
        'after_sha256': source[1]['after_sha256'], 'labels_png_sha256': annotation.data['artifacts']['labels']['sha256'],
        'known_mask_png_sha256': annotation.data['artifacts']['known_mask']['sha256'], 'valid_mask_png_sha256': valid_ev.sha256,
        'probability_npy_sha256': probability_ev.sha256, 'weights_sha256': run.data['model_trace']['weights_sha256'],
        'processor_sha256': manifest['runtime_hash'], 'annotation_labels_sha256': annotation.data['labels_sha256'],
        'annotation_known_mask_sha256': annotation.data['known_mask_sha256']}
    protocol._binding(binding)
    return binding, run.data['request']['threshold_text'], probability, labels, valid


def _study_meta(db, p, row):
    try:
        need(row.version == 1 and set(row.data) == {'schema', 'raw', 'raw_sha256', 'body_hash', 'manifest', 'adapter_hash', 'audit_id'}
            and row.data['schema'] == 'pixel-diagnostic-study-record-v257')
        body, _, raw_hash = parse(row.data['raw'].encode(), 'freeze')
        need(raw_hash == row.data['raw_sha256'] and row.request_id == body['request_id'] and digest(body) == row.data['body_hash'])
        manifest = protocol.checked_manifest(row.data['manifest']); binding = manifest['binding']
        need(binding['workspace_id'] == row.workspace_id == p.workspace_id and binding['instance_id'] == row.instance_id
            and binding['run_id'] == row.run_id and binding['annotation_id'] == row.annotation_id
            and binding['annotation_record_hash'] == body['annotation_record_hash']
            and manifest['candidate_threshold_text'] == body['candidate_threshold_text'] and protocol._sha(row.data['adapter_hash']))
        _proof(db, p, CREATED, row, _origin(row, 'pixel-diagnostic-study-origin-v257'))
        run, annotation = _scope_meta(db, p, row.instance_id, row.run_id, row.annotation_id)
        need(annotation.record_hash == binding['annotation_record_hash'] and run.version == binding['run_version']
            and run.data['generation'] == binding['generation'] and run.data['result_hash'] == binding['result_hash']
            and manifest['baseline_threshold_text'] == run.data['request']['threshold_text'])
        return row
    except HTTPException as error:
        if error.status_code in {401, 403}: raise
        raise HTTPException(409, ERROR) from None
    except (ValueError, KeyError, TypeError, AttributeError): raise HTTPException(409, ERROR) from None


def _evaluation_meta(db, p, row, study):
    try:
        need(row.version == 1 and set(row.data) == {'schema', 'raw', 'raw_sha256', 'body_hash', 'study_record_hash', 'result', 'result_artifact', 'audit_id'}
            and row.data['schema'] == 'pixel-diagnostic-evaluation-record-v257')
        body, _, raw_hash = parse(row.data['raw'].encode(), 'evaluate')
        need(raw_hash == row.data['raw_sha256'] and row.request_id == body['request_id'] and digest(body) == row.data['body_hash']
            and row.study_id == study.id and row.instance_id == study.instance_id and row.run_id == study.run_id
            and row.annotation_id == study.annotation_id and row.actor_id == study.actor_id
            and body['study_record_hash'] == row.data['study_record_hash'] == study.record_hash)
        protocol.checked_result(study.data['manifest'], row.data['result'])
        _proof(db, p, COMPUTED, row, _origin(row, 'pixel-diagnostic-evaluation-origin-v257'))
        return row
    except HTTPException: raise
    except (ValueError, KeyError, TypeError, AttributeError): raise HTTPException(409, ERROR) from None


def _command_meta(db, p, row):
    try:
        need(row.version == 1 and row.operation in {'freeze', 'evaluate'} and row.outcome in {'saved', 'rejected'}
            and set(row.data) == {'schema', 'raw', 'body_hash', 'rejection', 'target_record_hash', 'audit_id'}
            and row.data['schema'] == 'pixel-diagnostic-command-record-v257')
        body, _, rh = parse(row.data['raw'].encode(), row.operation)
        need(row.raw_sha256 == rh and row.request_id == body['request_id'] and digest(body) == row.data['body_hash'])
        if row.outcome == 'rejected':
            need(row.target_id is None and row.data['target_record_hash'] is None
                and type(row.data['rejection']) is dict and set(row.data['rejection']) == {'code', 'reason'}
                and row.data['rejection']['code'] in REASONS and row.data['rejection']['reason'] == REASONS[row.data['rejection']['code']])
        else: need(row.target_id is not None and row.data['rejection'] is None and protocol._sha(row.data['target_record_hash']))
        need((row.operation == 'freeze' and row.study_id == (row.target_id if row.outcome == 'saved' else None))
            or row.operation == 'evaluate' and row.study_id is not None)
        _proof(db, p, COMMANDED, row, _origin(row, 'pixel-diagnostic-command-origin-v257'))
        return row
    except HTTPException: raise
    except (ValueError, KeyError, TypeError, AttributeError): raise HTTPException(409, ERROR) from None


def _inventory(db, p, run_id):
    studies = list(db.scalars(select(Study).where(Study.workspace_id == p.workspace_id, Study.run_id == run_id).limit(LIMIT + 1)))
    evaluations = list(db.scalars(select(Evaluation).where(Evaluation.workspace_id == p.workspace_id, Evaluation.run_id == run_id).limit(LIMIT + 1)))
    commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id, Command.run_id == run_id).limit(LIMIT + 1)))
    need(all(len(rows) <= LIMIT for rows in (studies, evaluations, commands)), '原诊断数量超过完整核对上限')
    for cls, action, rows in [(Study, CREATED, studies), (Evaluation, COMPUTED, evaluations), (Command, COMMANDED, commands)]:
        proofs = list(db.scalars(select(m.Audit.object_id).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == action, m.Audit.data['run_id'].as_string() == run_id).limit(LIMIT + 1)))
        need(len(proofs) == len(rows) and len(set(proofs)) == len(proofs) and set(proofs) == {row.id for row in rows})
    by_study = {row.id: _study_meta(db, p, row) for row in studies}
    by_evaluation = {}
    for row in evaluations:
        need(row.study_id in by_study and row.study_id not in by_evaluation)
        _evaluation_meta(db, p, row, by_study[row.study_id]); by_evaluation[row.study_id] = row
    saved = {'freeze': [], 'evaluate': []}; nonces = set()
    for row in commands:
        _command_meta(db, p, row); key = (row.actor_id, row.request_id); need(key not in nonces); nonces.add(key)
        if row.outcome == 'saved':
            target = by_study.get(row.target_id) if row.operation == 'freeze' else next((e for e in evaluations if e.id == row.target_id), None)
            need(target is not None and target.actor_id == row.actor_id and target.request_id == row.request_id
                and target.instance_id == row.instance_id and target.run_id == row.run_id and target.annotation_id == row.annotation_id
                and target.record_hash == row.data['target_record_hash'] and target.data['raw'] == row.data['raw']
                and target.data['raw_sha256'] == row.raw_sha256 and target.data['body_hash'] == row.data['body_hash'])
            saved[row.operation].append(target.id)
        if row.operation == 'evaluate':
            need(row.study_id in by_study)
            study = by_study[row.study_id]
            need(study.actor_id == row.actor_id and study.annotation_id == row.annotation_id and study.instance_id == row.instance_id)
    need(len(saved['freeze']) == len(set(saved['freeze'])) and set(saved['freeze']) == set(by_study)
        and len(saved['evaluate']) == len(set(saved['evaluate'])) and set(saved['evaluate']) == {row.id for row in evaluations})
    return studies, evaluations, commands


def _existing(db, p, request_id):
    rows = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.actor_id == p.user_id, Command.request_id == request_id).limit(2)))
    audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id, m.Audit.actor_id == p.user_id,
        m.Audit.action.in_([CREATED, COMPUTED, COMMANDED]), m.Audit.data['request_id'].as_string() == request_id).limit(4)))
    studies = list(db.scalars(select(Study).where(Study.workspace_id == p.workspace_id, Study.actor_id == p.user_id, Study.request_id == request_id).limit(2)))
    evaluations = list(db.scalars(select(Evaluation).where(Evaluation.workspace_id == p.workspace_id, Evaluation.actor_id == p.user_id, Evaluation.request_id == request_id).limit(2)))
    if not rows: need(not audits and not studies and not evaluations); return None
    need(len(rows) == 1); row = rows[0]
    _inventory(db, p, row.run_id)
    need(len(audits) == (2 if row.outcome == 'saved' else 1)
        and {audit.object_id for audit in audits} == {row.id, *([row.target_id] if row.outcome == 'saved' else [])}
        and len(studies) == int(row.operation == 'freeze' and row.outcome == 'saved')
        and len(evaluations) == int(row.operation == 'evaluate' and row.outcome == 'saved'))
    return row


def study_view(row):
    return {'schema': 'pixel-diagnostic-study-v257', 'id': row.id, 'version': row.version, 'workspace_id': row.workspace_id,
        'actor_id': row.actor_id, 'instance_id': row.instance_id, 'run_id': row.run_id, 'annotation_id': row.annotation_id,
        'request_id': row.request_id, 'raw_sha256': row.data['raw_sha256'], 'body_hash': row.data['body_hash'],
        'created_at': row.created_at.astimezone(UTC).isoformat(), 'manifest': copy.deepcopy(row.data['manifest']), 'record_hash': row.record_hash}


def _artifact(db, p, row):
    ref = row.data['result_artifact']; ev, raw = evidence_bytes(db, p, ref['evidence_id'])
    need(set(ref) == {'evidence_id', 'sha256', 'size_bytes', 'media_type'}
        and ev.sha256 == ref['sha256'] == hashlib.sha256(raw).hexdigest()
        and ev.size_bytes == ref['size_bytes'] == len(raw) and ev.media_type == ref['media_type'] == 'application/json'
        and raw == _raw_json(row.data['result']) and digest(ev.data) == digest({'purpose': 'pixel_diagnostic_result_v257',
            'study_id': row.study_id, 'evaluation_id': row.id, 'manifest_hash': row.data['result']['manifest_hash'],
            'result_hash': row.data['result']['result_hash'], 'cloud_model_ids': [], 'uploaded_by': row.actor_id}))
    return ev, raw


def _checked_evaluation(db, p, row, study):
    _evaluation_meta(db, p, row, study)
    binding, _, _, _, _ = _physical(db, p, row.instance_id, row.run_id, row.annotation_id)
    need(digest(binding) == digest(study.data['manifest']['binding']))
    _artifact(db, p, row); return row


def evaluation_view(row):
    return {'schema': 'pixel-diagnostic-evaluation-v257', 'id': row.id, 'version': row.version,
        'workspace_id': row.workspace_id, 'actor_id': row.actor_id, 'instance_id': row.instance_id, 'run_id': row.run_id,
        'annotation_id': row.annotation_id, 'study_id': row.study_id, 'request_id': row.request_id,
        'raw_sha256': row.data['raw_sha256'], 'body_hash': row.data['body_hash'], 'created_at': row.created_at.astimezone(UTC).isoformat(),
        'study_record_hash': row.data['study_record_hash'], 'result': copy.deepcopy(row.data['result']),
        'result_artifact': copy.deepcopy(row.data['result_artifact']), 'record_hash': row.record_hash}


def _command_value(db, p, row):
    if row.outcome == 'rejected': return None
    if row.operation == 'freeze': return study_view(_study_meta(db, p, _row(db, p, Study, row.target_id, internal=True)))
    study = _study_meta(db, p, _row(db, p, Study, row.study_id, internal=True))
    evaluation = _row(db, p, Evaluation, row.target_id, internal=True)
    return evaluation_view(_checked_evaluation(db, p, evaluation, study))


def _receipt(db, p, operation, instance_id, run_id, annotation_id, body, original, raw_hash, *, target=None, study_id=None, rejection=None):
    row = Command(id=m.uid(), version=1, created_at=m.now(), workspace_id=p.workspace_id, actor_id=p.user_id,
        instance_id=instance_id, run_id=run_id, annotation_id=annotation_id, request_id=body['request_id'], raw_sha256=raw_hash,
        operation=operation, outcome='saved' if target is not None else 'rejected', target_id=target.id if target else None,
        study_id=study_id, record_hash='0' * 64, data={'schema': 'pixel-diagnostic-command-record-v257', 'raw': original,
            'body_hash': digest(body), 'rejection': {'code': rejection, 'reason': REASONS[rejection]} if rejection else None,
            'target_record_hash': target.record_hash if target else None})
    db.add(row); db.flush(); origin = _origin(row, 'pixel-diagnostic-command-origin-v257'); row.record_hash = digest(origin)
    row.data = {**row.data, 'audit_id': _audit(db, p, COMMANDED, row, origin)}
    db.flush(); _inventory(db, p, run_id); return row


def _match(row, instance_id, run_id, annotation_id, operation, original, raw_hash, study_id=None):
    need(row.instance_id == instance_id and row.run_id == run_id and row.annotation_id == annotation_id
        and row.operation == operation and row.raw_sha256 == raw_hash and row.data['raw'] == original
        and (operation != 'evaluate' or row.study_id == study_id), '原申请号不能用于另一份正文、操作或诊断范围')


def _source_failure(error):
    if isinstance(error, HTTPException) and error.status_code in {401, 403}: raise error
    return 'source_unavailable'


def _runtime(study=None):
    from . import pixel_diagnostic_bridge_v257 as bridge
    current = bridge.adapter_hash(); probe = bridge.probe()
    if study is not None:
        need(current == study.data['adapter_hash'] and digest(probe) == digest(study.data['manifest']['evaluator']), REASONS['evaluator_changed'])
    return current, probe


def freeze(db, p, instance_id, run_id, annotation_id, raw):
    body, original, raw_hash = parse(raw, 'freeze')
    prior = _existing(db, p, body['request_id'])
    if prior: _match(prior, instance_id, run_id, annotation_id, 'freeze', original, raw_hash); return prior
    require(p, 'admin', 'operator'); lock_current_actor(db, p); _nonce(db, p, body['request_id'])
    prior = _existing(db, p, body['request_id'])
    if prior: _match(prior, instance_id, run_id, annotation_id, 'freeze', original, raw_hash); return prior
    _, annotation = _scope_meta(db, p, instance_id, run_id, annotation_id, lock=True)
    if annotation.record_hash != body['annotation_record_hash']:
        return _receipt(db, p, 'freeze', instance_id, run_id, annotation_id, body, original, raw_hash, rejection='annotation_changed')
    try: binding, baseline, _, _, _ = _physical(db, p, instance_id, run_id, annotation_id)
    except (HTTPException, ValueError, KeyError, TypeError, OSError, AttributeError) as error:
        return _receipt(db, p, 'freeze', instance_id, run_id, annotation_id, body, original, raw_hash, rejection=_source_failure(error))
    from . import pixel_diagnostic_bridge_v257 as bridge
    try:
        adapter_hash, probe = _runtime()
        manifest = protocol.checked_manifest(bridge.freeze_manifest(binding, baseline, body['candidate_threshold_text']))
        need(digest(manifest['binding']) == digest(binding) and manifest['baseline_threshold_text'] == baseline
            and manifest['candidate_threshold_text'] == body['candidate_threshold_text'] and digest(manifest['evaluator']) == digest(probe))
    except bridge.DiagnosticRuntimeError as error:
        return _receipt(db, p, 'freeze', instance_id, run_id, annotation_id, body, original, raw_hash,
            rejection='scoring_failed' if error.code == 'scoring_failed' else 'evaluator_changed')
    except (HTTPException, ValueError):
        return _receipt(db, p, 'freeze', instance_id, run_id, annotation_id, body, original, raw_hash, rejection='evaluator_changed')
    row = Study(id=m.uid(), version=1, created_at=m.now(), workspace_id=p.workspace_id, instance_id=instance_id, run_id=run_id,
        annotation_id=annotation_id, actor_id=p.user_id, request_id=body['request_id'], record_hash='0' * 64,
        data={'schema': 'pixel-diagnostic-study-record-v257', 'raw': original, 'raw_sha256': raw_hash, 'body_hash': digest(body),
            'manifest': manifest, 'adapter_hash': adapter_hash})
    db.add(row); db.flush(); origin = _origin(row, 'pixel-diagnostic-study-origin-v257'); row.record_hash = digest(origin)
    row.data = {**row.data, 'audit_id': _audit(db, p, CREATED, row, origin)}
    return _receipt(db, p, 'freeze', instance_id, run_id, annotation_id, body, original, raw_hash, target=row, study_id=row.id)


def evaluate(db, p, instance_id, run_id, annotation_id, study_id, raw):
    body, original, raw_hash = parse(raw, 'evaluate')
    prior = _existing(db, p, body['request_id'])
    if prior: _match(prior, instance_id, run_id, annotation_id, 'evaluate', original, raw_hash, study_id); return prior
    require(p, 'admin', 'operator'); lock_current_actor(db, p); _nonce(db, p, body['request_id'])
    study = _row(db, p, Study, study_id, lock=True); _study_meta(db, p, study)
    need(study.instance_id == instance_id and study.run_id == run_id and study.annotation_id == annotation_id, '此范围没有该冻结方案', 404)
    need(study.actor_id == p.user_id, '只有冻结方案的创建者可明确计算；其他人员可只读或另建本人方案', 403)
    prior = _existing(db, p, body['request_id'])
    if prior: _match(prior, instance_id, run_id, annotation_id, 'evaluate', original, raw_hash, study_id); return prior
    _, evaluations, _ = _inventory(db, p, run_id)
    code = ('study_changed' if body['study_record_hash'] != study.record_hash else
        'already_evaluated' if any(row.study_id == study.id for row in evaluations) else None)
    if code: return _receipt(db, p, 'evaluate', instance_id, run_id, annotation_id, body, original, raw_hash, study_id=study_id, rejection=code)
    from . import pixel_diagnostic_bridge_v257 as bridge
    try: _runtime(study)
    except (bridge.DiagnosticRuntimeError, HTTPException, ValueError):
        return _receipt(db, p, 'evaluate', instance_id, run_id, annotation_id, body, original, raw_hash, study_id=study_id, rejection='evaluator_changed')
    _scope_meta(db, p, instance_id, run_id, annotation_id, lock=True)
    try:
        binding, _, probability, labels, valid = _physical(db, p, instance_id, run_id, annotation_id)
        need(digest(binding) == digest(study.data['manifest']['binding']))
    except (HTTPException, ValueError, KeyError, TypeError, OSError, AttributeError) as error:
        return _receipt(db, p, 'evaluate', instance_id, run_id, annotation_id, body, original, raw_hash, study_id=study_id, rejection=_source_failure(error))
    try:
        result = protocol.checked_result(study.data['manifest'], bridge.evaluate(study.data['manifest'], probability, labels, valid))
    except bridge.DiagnosticRuntimeError as error:
        return _receipt(db, p, 'evaluate', instance_id, run_id, annotation_id, body, original, raw_hash, study_id=study_id,
            rejection='evaluator_changed' if error.code in {'runtime_unavailable', 'evaluator_changed'} else 'scoring_failed')
    except ValueError:
        return _receipt(db, p, 'evaluate', instance_id, run_id, annotation_id, body, original, raw_hash, study_id=study_id, rejection='scoring_failed')
    try:
        fresh, _, _, _, _ = _physical(db, p, instance_id, run_id, annotation_id)
        need(digest(fresh) == digest(binding))
    except (HTTPException, ValueError, KeyError, TypeError, OSError, AttributeError) as error:
        return _receipt(db, p, 'evaluate', instance_id, run_id, annotation_id, body, original, raw_hash, study_id=study_id, rejection=_source_failure(error))
    try:
        with db.begin_nested():
            row = Evaluation(id=m.uid(), version=1, created_at=m.now(), workspace_id=p.workspace_id, instance_id=instance_id, run_id=run_id,
                annotation_id=annotation_id, study_id=study.id, actor_id=p.user_id, request_id=body['request_id'], record_hash='0' * 64,
                data={'schema': 'pixel-diagnostic-evaluation-record-v257', 'raw': original, 'raw_sha256': raw_hash,
                    'body_hash': digest(body), 'study_record_hash': study.record_hash, 'result': result})
            db.add(row); db.flush()
            ev = save_immutable(db, p, _raw_json(result), 'application/json', {'purpose': 'pixel_diagnostic_result_v257',
                'study_id': study.id, 'evaluation_id': row.id, 'manifest_hash': study.data['manifest']['manifest_hash'], 'result_hash': result['result_hash']})
            row.data = {**row.data, 'result_artifact': {'evidence_id': ev.id, 'sha256': ev.sha256, 'size_bytes': ev.size_bytes, 'media_type': ev.media_type}}
            origin = _origin(row, 'pixel-diagnostic-evaluation-origin-v257'); row.record_hash = digest(origin)
            row.data = {**row.data, 'audit_id': _audit(db, p, COMPUTED, row, origin)}
            _artifact(db, p, row)
    except (HTTPException, ValueError, OSError) as error:
        if isinstance(error, HTTPException) and error.status_code in {401, 403}: raise
        return _receipt(db, p, 'evaluate', instance_id, run_id, annotation_id, body, original, raw_hash, study_id=study_id, rejection='scoring_failed')
    return _receipt(db, p, 'evaluate', instance_id, run_id, annotation_id, body, original, raw_hash, target=row, study_id=study_id)


def get(db, p, instance_id, run_id, annotation_id, study_id):
    _scope_meta(db, p, instance_id, run_id, annotation_id); _inventory(db, p, run_id)
    row = _study_meta(db, p, _row(db, p, Study, study_id))
    need(row.instance_id == instance_id and row.run_id == run_id and row.annotation_id == annotation_id, '此范围没有该冻结方案', 404)
    return row


def evaluation_lookup(db, p, instance_id, run_id, annotation_id, study_id):
    study = get(db, p, instance_id, run_id, annotation_id, study_id)
    rows = list(db.scalars(select(Evaluation).where(Evaluation.workspace_id == p.workspace_id, Evaluation.study_id == study.id).limit(2)))
    need(len(rows) <= 1)
    value = evaluation_view(_checked_evaluation(db, p, rows[0], study)) if rows else None
    return {'schema': 'pixel-diagnostic-evaluation-lookup-v257', 'workspace_id': p.workspace_id, 'instance_id': instance_id,
        'run_id': run_id, 'annotation_id': annotation_id, 'study_id': study_id, 'found': value is not None, 'evaluation': value}


def result_artifact(db, p, instance_id, run_id, annotation_id, study_id):
    study = get(db, p, instance_id, run_id, annotation_id, study_id)
    rows = list(db.scalars(select(Evaluation).where(Evaluation.workspace_id == p.workspace_id, Evaluation.study_id == study.id).limit(2)))
    need(len(rows) == 1, '尚未保存这份方案的计算结果', 404)
    _checked_evaluation(db, p, rows[0], study); return _artifact(db, p, rows[0])


def request_lookup(db, p, instance_id, run_id, annotation_id, request_id, raw_sha256):
    need(type(request_id) is str and 12 <= len(request_id) <= 100 and '\x00' not in request_id
        and protocol._sha(raw_sha256), '原申请号或摘要格式不完整', 422)
    try: request_id.encode('utf-8')
    except UnicodeError: raise HTTPException(422, '原申请号含不能保存的字符') from None
    _scope_meta(db, p, instance_id, run_id, annotation_id)
    row = _existing(db, p, request_id)
    if row: need(row.instance_id == instance_id and row.run_id == run_id and row.annotation_id == annotation_id
        and row.raw_sha256 == raw_sha256, '原申请号已经绑定另一份正文或诊断范围')
    return {'schema': 'pixel-diagnostic-request-v257', 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'instance_id': instance_id, 'run_id': run_id, 'annotation_id': annotation_id, 'request_id': request_id,
        'raw_sha256': raw_sha256, 'found': row is not None, 'operation': row.operation if row else None,
        'outcome': row.outcome if row else None, 'study_id': row.study_id if row else None,
        'receipt': _command_value(db, p, row) if row else None,
        'rejection': copy.deepcopy(row.data['rejection']) if row else None, 'original_raw': row.data['raw'] if row else None}


def options(db, p, instance_id, run_id, annotation_id):
    run, annotation = _scope_meta(db, p, instance_id, run_id, annotation_id)
    runtime = None; reason = None; ready = False
    try: _, runtime = _runtime(); ready = True
    except (HTTPException, ValueError): reason = '本地诊断运行环境尚未就绪；原标注和旧方案仍可读取'
    if ready:
        try: _physical(db, p, instance_id, run_id, annotation_id)
        except (HTTPException, ValueError, KeyError, TypeError, OSError, AttributeError) as error:
            _source_failure(error); reason = REASONS['source_unavailable']
    if p.role not in {'admin', 'operator'}: reason = '当前账号可以核对历史，不能冻结新的诊断方案'
    return {'schema': 'pixel-diagnostic-options-v257', 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'instance_id': instance_id, 'run_id': run_id, 'annotation_id': annotation_id,
        'annotation_record_hash': annotation.record_hash, 'can_freeze': ready and reason is None,
        'reason': reason, 'runtime_ready': ready, 'baseline_threshold_text': run.data['request']['threshold_text'],
        'runtime': runtime, 'annotation_counts': copy.deepcopy(annotation.data['counts']), 'release_authorized': False}


def listing(db, p, instance_id, run_id, annotation_id, *, limit=10, before_id=None):
    _scope_meta(db, p, instance_id, run_id, annotation_id); _inventory(db, p, run_id)
    need(type(limit) is int and 10 <= limit <= 20, '分页大小应为10到20', 422)
    if before_id is not None:
        try: need(type(before_id) is str and str(UUID(before_id)) == before_id, '分页位置无效', 422)
        except (ValueError, AttributeError): raise HTTPException(422, '分页位置无效') from None
    query = select(Study).where(Study.workspace_id == p.workspace_id, Study.instance_id == instance_id,
        Study.run_id == run_id, Study.annotation_id == annotation_id)
    if before_id is not None:
        cursor = _row(db, p, Study, before_id)
        need(cursor.instance_id == instance_id and cursor.run_id == run_id and cursor.annotation_id == annotation_id, '分页位置不属于此诊断范围', 404)
        _study_meta(db, p, cursor)
        query = query.where(or_(Study.created_at < cursor.created_at,
            and_(Study.created_at == cursor.created_at, Study.id < cursor.id)))
    rows = list(db.scalars(query.order_by(Study.created_at.desc(), Study.id.desc()).limit(limit + 1)))
    return {'schema': 'pixel-diagnostic-study-list-v257', 'workspace_id': p.workspace_id,
        'instance_id': instance_id, 'run_id': run_id, 'annotation_id': annotation_id, 'before_id': before_id,
        'items': [study_view(row) for row in rows[:limit]], 'has_more': len(rows) > limit,
        'next_cursor': rows[limit - 1].id if len(rows) > limit else None}
