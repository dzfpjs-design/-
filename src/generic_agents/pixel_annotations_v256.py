"""Durable human pixel feedback. Exposed feedback is never an independent holdout."""
import copy
import hashlib
import io
import json
from datetime import UTC
from uuid import UUID

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import or_, select, text

from .contracts import digest
from .evidence import evidence_bytes, save_immutable
from .models import Audit, DomainInstance, PixelChangeRun
from .pixel_annotation_models_v256 import PixelAnnotationV256 as Annotation, PixelAnnotationCommandV256 as Command
from .pixel_annotation_contracts_v256 import SaveAnnotation, rasterize, mask_png
from .relations import lock_current_actor
from .security import require
from .services import scoped
from . import pixel_change_runs_v249 as runs

SAVED = 'pixel_annotation.saved.v256'
COMMANDED = 'pixel_annotation.command.v256'
ERROR = '标注原件、版本或独立保存依据尚不能完整核对；原稿保留，请勿换号覆盖。'


def need(ok, message=ERROR, status=409):
    if not ok:
        raise HTTPException(status, message)


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    need(0 < len(raw) <= 131072, '标注申请为空或超过大小上限', 422)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('non-finite JSON')
    try:
        original = raw.decode('utf-8')
        body = SaveAnnotation.model_validate(json.loads(original, object_pairs_hook=pairs, parse_constant=constant))
        serialized = json.dumps(body.model_dump(), ensure_ascii=False, allow_nan=False)
        serialized.encode('utf-8')
        need('\x00' not in body.note and '\x00' not in body.request_id, status=422)
        return body, original, _hash(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, '标注申请格式或字段不完整；请保留原稿并核对输入') from None


def _audit(db, p, action, identity, data):
    row = Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=action, object_id=identity, data=data)
    db.add(row); db.flush()
    return row.id


def _proof(db, p, action, identity, actor_id, audit_id, origin):
    proof = scoped(db, Audit, audit_id, p)
    need(proof.action == action and proof.object_id == identity and proof.actor_id == actor_id
         and digest(proof.data) == digest(origin))
    ids = list(db.scalars(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == identity).limit(2)))
    need(ids == [audit_id])


def _source(db, p, instance_id, run_id, *, lock=False):
    scoped(db, DomainInstance, instance_id, p)
    if lock:
        row = db.scalar(select(PixelChangeRun).where(PixelChangeRun.workspace_id == p.workspace_id,
            PixelChangeRun.instance_id == instance_id, PixelChangeRun.id == run_id).with_for_update())
        need(row is not None, '未找到此实例的原影像比较记录', 404)
        runs.checked(db, p, row)
    else:
        row = runs.get(db, p, instance_id, run_id)
    need(row.status == 'succeeded', '原影像比较尚未成功，不能把缺失结果当作未变化')
    _inventory(db, p, run_id)
    result = row.data['result']
    ev, raw = runs._artifact(db, p, row, 'valid_mask')
    try:
        with Image.open(io.BytesIO(raw)) as image:
            need(image.format == 'PNG' and image.mode == 'L' and image.size == (result['width'], result['height']))
            pixels = image.tobytes()
        need(set(pixels).issubset({0, 255}) and len(pixels) == result['width'] * result['height'])
    except (ValueError, OSError):
        raise HTTPException(409, ERROR) from None
    valid = pixels.translate(bytes([0] * 255 + [1]))
    pin = {'id': row.id, 'version': row.version, 'result_hash': row.data['result_hash'],
        'width': result['width'], 'height': result['height'],
        'before_sha256': row.data['sources']['before']['sha256'],
        'after_sha256': row.data['sources']['after']['sha256'], 'valid_mask_sha256': ev.sha256}
    return row, pin, valid


def _inventory(db, p, run_id):
    """Missing leaves/receipts cannot silently turn an old version into latest."""
    annotation_rows = list(db.scalars(select(Annotation).where(Annotation.workspace_id == p.workspace_id,
        Annotation.run_id == run_id)))
    command_rows = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.run_id == run_id)))
    annotations, commands = [r.id for r in annotation_rows], [r.id for r in command_rows]
    for action, identities in [(SAVED, annotations), (COMMANDED, commands)]:
        proofs = list(db.scalars(select(Audit.object_id).where(Audit.workspace_id == p.workspace_id,
            Audit.action == action, Audit.data['run_id'].as_string() == run_id)))
        need(len(proofs) == len(set(proofs)) and set(proofs) == set(identities))
    saved = list(db.scalars(select(Command.annotation_id).where(Command.workspace_id == p.workspace_id,
        Command.run_id == run_id, Command.outcome == 'saved')))
    need(len(saved) == len(set(saved)) and set(saved) == set(annotations))
    try:
        for row in annotation_rows:
            need(row.version == 1 and row.data['source']['id'] == run_id and digest(_origin(row)) == row.record_hash)
            _proof(db, p, SAVED, row.id, row.actor_id, row.data['audit_id'], _origin(row))
        for row in command_rows:
            need(row.version == 1 and digest(_command_origin(row)) == row.record_hash)
            _proof(db, p, COMMANDED, row.id, row.actor_id, row.data['audit_id'], _command_origin(row))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(409, ERROR) from None


def _origin(row):
    return {'schema': 'pixel-annotation-origin-v256', 'id': row.id, 'workspace_id': row.workspace_id,
        'instance_id': row.instance_id, 'run_id': row.run_id, 'actor_id': row.actor_id,
        'created_at': row.created_at.astimezone(UTC).isoformat(),
        'request_id': row.request_id, 'raw_sha256': row.raw_sha256, 'supersedes_id': row.supersedes_id,
        'data': {k: v for k, v in row.data.items() if k != 'audit_id'}}


def _command_origin(row):
    return {'schema': 'pixel-annotation-command-origin-v256', 'id': row.id, 'workspace_id': row.workspace_id,
        'instance_id': row.instance_id, 'run_id': row.run_id, 'actor_id': row.actor_id,
        'created_at': row.created_at.astimezone(UTC).isoformat(),
        'request_id': row.request_id, 'raw_sha256': row.raw_sha256, 'outcome': row.outcome,
        'annotation_id': row.annotation_id, 'data': {k: v for k, v in row.data.items() if k != 'audit_id'}}


def _artifact(db, p, row, kind, expected_raw=None):
    ref = row.data['artifacts'][kind]
    ev, raw = evidence_bytes(db, p, ref['evidence_id'])
    need(ev.sha256 == ref['sha256'] == _hash(raw) and ev.size_bytes == ref['size_bytes'] == len(raw)
         and ev.media_type == ref['media_type'] == 'image/png'
         and digest(ev.data) == digest({'purpose': 'pixel_annotation_v256', 'annotation_id': row.id,
             'run_id': row.run_id, 'result_hash': row.data['source']['result_hash'], 'kind': kind,
             'cloud_model_ids': [], 'uploaded_by': row.actor_id}))
    if expected_raw is not None:
        need(raw == expected_raw)
    return ev, raw


def checked(db, p, row, source):
    _, pin, valid = source
    try:
        body, original, raw_hash = parse(row.data['raw'].encode('utf-8'))
        need(row.workspace_id == p.workspace_id and row.run_id == pin['id']
             and row.instance_id == source[0].instance_id
             and row.version == 1 and set(row.data) == {'schema', 'raw', 'request', 'body_hash', 'source',
            'supersedes_record_hash', 'counts', 'labels_sha256', 'known_mask_sha256', 'artifacts', 'audit_id'})
        need(row.data['schema'] == 'pixel-annotation-record-v256' and raw_hash == row.raw_sha256
             and body.request_id == row.request_id and body.supersedes_annotation_id == row.supersedes_id
             and body.expected_run_version == pin['version'] and body.result_hash == pin['result_hash']
             and digest(body.model_dump()) == row.data['body_hash'] == digest(row.data['request'])
             and digest(row.data['source']) == digest(pin))
        if row.supersedes_id:
            previous = scoped(db, Annotation, row.supersedes_id, p)
            need(previous.instance_id == row.instance_id and previous.run_id == row.run_id
                 and previous.actor_id == row.actor_id and previous.record_hash == row.data['supersedes_record_hash']
                 and digest(_origin(previous)) == previous.record_hash)
        else:
            need(row.data['supersedes_record_hash'] is None)
        mask = rasterize(pin['width'], pin['height'], body.model_dump()['regions'], valid_mask=valid)
        need(mask['counts'] == row.data['counts'] and mask['labels_sha256'] == row.data['labels_sha256']
             and mask['known_mask_sha256'] == row.data['known_mask_sha256'])
        need(set(row.data['artifacts']) == {'labels', 'known_mask'})
        _artifact(db, p, row, 'labels', mask_png(pin['width'], pin['height'], mask['labels']))
        known_png = mask_png(pin['width'], pin['height'], mask['known_valid_mask'].translate(bytes([0, 255] + [0] * 254)))
        _artifact(db, p, row, 'known_mask', known_png)
        need(row.record_hash == digest(_origin(row)))
        _proof(db, p, SAVED, row.id, row.actor_id, row.data['audit_id'], _origin(row))
    except (HTTPException, KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(409, ERROR) from None
    return row


def view(row):
    data, source = row.data, row.data['source']
    return {'schema': 'pixel-annotation-v256', 'id': row.id, 'version': row.version,
        'workspace_id': row.workspace_id, 'instance_id': row.instance_id, 'run_id': row.run_id,
        'actor_id': row.actor_id, 'created_at': row.created_at, 'request_id': row.request_id,
        'raw_sha256': row.raw_sha256, 'body_hash': data['body_hash'], 'run_version': source['version'],
        'result_hash': source['result_hash'], 'width': source['width'], 'height': source['height'],
        'supersedes_annotation_id': row.supersedes_id, 'note': data['request']['note'],
        'regions': copy.deepcopy(data['request']['regions']), 'counts': copy.deepcopy(data['counts']),
        'labels_sha256': data['labels_sha256'], 'known_mask_sha256': data['known_mask_sha256'],
        'artifacts': copy.deepcopy(data['artifacts']), 'record_hash': row.record_hash}


def _latest(db, p, run_id):
    rows = list(db.scalars(select(Annotation).where(Annotation.workspace_id == p.workspace_id,
        Annotation.run_id == run_id, Annotation.actor_id == p.user_id)))
    if not rows:
        return None
    by_id = {row.id: row for row in rows}
    try:
        for row in rows:
            need(row.version == 1 and digest(_origin(row)) == row.record_hash)
            _proof(db, p, SAVED, row.id, row.actor_id, row.data['audit_id'], _origin(row))
            if row.supersedes_id:
                parent = by_id.get(row.supersedes_id)
                need(parent is not None and parent.id != row.id
                     and row.data['supersedes_record_hash'] == parent.record_hash)
            else:
                need(row.data['supersedes_record_hash'] is None)
        parents = {row.supersedes_id for row in rows if row.supersedes_id}
        heads = set(by_id) - parents
        need(len(heads) == 1, '标注版本链不完整，请保留原稿并核对保存依据')
        head = by_id[next(iter(heads))]
        seen, cursor = set(), head
        while cursor is not None:
            need(cursor.id not in seen)
            seen.add(cursor.id)
            cursor = by_id[cursor.supersedes_id] if cursor.supersedes_id else None
        need(seen == set(by_id), '标注版本存在断开分支，请保留原稿并核对保存依据')
        return head
    except (KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(409, ERROR) from None


def options(db, p, instance_id, run_id):
    source = _source(db, p, instance_id, run_id)
    latest = _latest(db, p, run_id)
    if latest:
        checked(db, p, latest, source)
    allowed = p.role in {'admin', 'operator'}
    return {'schema': 'pixel-annotation-options-v256', 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'instance_id': instance_id, 'run': source[1], 'can_save': allowed,
        'reason': None if allowed else '当前账号只能读取标注，不能保存新的人工标注',
        'latest': view(latest) if latest else None}


def _command(db, p, request_id):
    rows = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.actor_id == p.user_id, Command.request_id == request_id).limit(2)))
    proofs = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.actor_id == p.user_id, Audit.action == COMMANDED,
        Audit.data['request_id'].as_string() == request_id).limit(2)))
    annotations = list(db.scalars(select(Annotation.id).where(Annotation.workspace_id == p.workspace_id,
        Annotation.actor_id == p.user_id, Annotation.request_id == request_id).limit(2)))
    need(len(rows) <= 1 and len(proofs) == len(rows))
    if not rows:
        need(not annotations)
        return None
    row = rows[0]
    need(proofs[0].id == row.data.get('audit_id') and proofs[0].object_id == row.id
         and digest(proofs[0].data) == row.record_hash == digest(_command_origin(row))
         and annotations == ([row.annotation_id] if row.outcome == 'saved' else []))
    return row


def _checked_command(db, p, row, source):
    try:
        body, _, raw_hash = parse(row.data['raw'].encode('utf-8'))
        need(row.version == 1 and set(row.data) == {'raw', 'body_hash', 'rejection', 'annotation_record_hash', 'audit_id'}
             and row.raw_sha256 == raw_hash and row.request_id == body.request_id
             and row.data['body_hash'] == digest(body.model_dump()) and row.outcome in {'saved', 'rejected'}
             and row.record_hash == digest(_command_origin(row)))
        _proof(db, p, COMMANDED, row.id, row.actor_id, row.data['audit_id'], _command_origin(row))
        if row.outcome == 'saved':
            annotation = scoped(db, Annotation, row.annotation_id, p)
            need(row.data['rejection'] is None and annotation.instance_id == row.instance_id
                 and annotation.run_id == row.run_id and annotation.actor_id == row.actor_id
                 and annotation.request_id == row.request_id and annotation.raw_sha256 == row.raw_sha256
                 and annotation.record_hash == row.data['annotation_record_hash'])
            return checked(db, p, annotation, source)
        need(row.annotation_id is None and row.data['annotation_record_hash'] is None
             and set(row.data['rejection']) == {'code', 'reason'}
             and row.data['rejection']['code'] in {'analysis_changed', 'annotation_changed', 'invalid_regions'})
    except (HTTPException, KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(409, ERROR) from None
    return None


def recovery(db, p, instance_id, run_id, request_id, raw_sha256):
    try:
        need(isinstance(request_id, str) and 12 <= len(request_id) <= 100 and '\x00' not in request_id
             and isinstance(raw_sha256, str) and len(raw_sha256) == 64
             and all(c in '0123456789abcdef' for c in raw_sha256), '原申请号或摘要格式不完整', 422)
        request_id.encode('utf-8')
    except UnicodeError:
        raise HTTPException(422, '原申请号含不能保存的字符') from None
    source = _source(db, p, instance_id, run_id)
    row = _command(db, p, request_id)
    annotation = None
    if row:
        need(row.instance_id == instance_id and row.run_id == run_id and row.raw_sha256 == raw_sha256,
             '原申请号已经用于另一份正文或比较记录，请保留原号及原文')
        annotation = _checked_command(db, p, row, source)
    return {'schema': 'pixel-annotation-request-v256', 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'instance_id': instance_id, 'run_id': run_id, 'request_id': request_id, 'raw_sha256': raw_sha256,
        'found': row is not None, 'outcome': row.outcome if row else None,
        'rejection': copy.deepcopy(row.data['rejection']) if row else None,
        'annotation': view(annotation) if annotation else None, 'original_raw': row.data['raw'] if row else None}


def save(db, p, instance_id, run_id, raw):
    require(p, 'admin', 'operator'); lock_current_actor(db, p)
    body, original, raw_hash = parse(raw)
    lock = int.from_bytes(hashlib.sha256((p.workspace_id + '\0' + p.user_id + '\0' + body.request_id).encode()).digest()[:8], 'big', signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock})
    source = _source(db, p, instance_id, run_id, lock=True)
    previous_command = _command(db, p, body.request_id)
    if previous_command:
        need(previous_command.instance_id == instance_id and previous_command.run_id == run_id
             and previous_command.raw_sha256 == raw_hash and previous_command.data['raw'] == original,
             '原申请号已经用于另一份正文或比较记录，请按原文核对')
        _checked_command(db, p, previous_command, source)
        return previous_command
    _, pin, valid = source
    latest = _latest(db, p, run_id)
    if latest:
        checked(db, p, latest, source)
    rejection = None
    mask = None
    if body.expected_run_version != pin['version'] or body.result_hash != pin['result_hash']:
        rejection = {'code': 'analysis_changed', 'reason': '原比较版本或结果已经不同，请保留标注原稿并重新核对原分析'}
    elif body.supersedes_annotation_id != (latest.id if latest else None):
        rejection = {'code': 'annotation_changed', 'reason': '另一份标注已保存；本机原稿保留，请明确读取最新标注后再继续'}
    else:
        try:
            mask = rasterize(pin['width'], pin['height'], body.model_dump()['regions'], valid_mask=valid)
        except ValueError:
            rejection = {'code': 'invalid_regions', 'reason': '标注矩形越界、面积超过上限或输入不完整，请保留原稿并修改'}
    annotation = None
    if rejection is None:
        annotation = Annotation(workspace_id=p.workspace_id, instance_id=instance_id, run_id=run_id,
            actor_id=p.user_id, request_id=body.request_id, supersedes_id=body.supersedes_annotation_id,
            raw_sha256=raw_hash, record_hash='0' * 64, data={})
        db.add(annotation); db.flush()
        artifacts = {}
        for kind, pixels in [('labels', mask['labels']), ('known_mask', mask['known_valid_mask'].translate(bytes([0, 255] + [0] * 254)))]:
            content = mask_png(pin['width'], pin['height'], pixels)
            ev = save_immutable(db, p, content, 'image/png', {'purpose': 'pixel_annotation_v256',
                'annotation_id': annotation.id, 'run_id': run_id, 'result_hash': pin['result_hash'], 'kind': kind})
            _, readback = evidence_bytes(db, p, ev.id); need(readback == content)
            artifacts[kind] = {'evidence_id': ev.id, 'sha256': ev.sha256,
                'size_bytes': ev.size_bytes, 'media_type': ev.media_type}
        annotation.data = {'schema': 'pixel-annotation-record-v256', 'raw': original, 'request': body.model_dump(),
            'body_hash': digest(body.model_dump()), 'source': pin,
            'supersedes_record_hash': latest.record_hash if latest else None,
            'counts': mask['counts'], 'labels_sha256': mask['labels_sha256'],
            'known_mask_sha256': mask['known_mask_sha256'], 'artifacts': artifacts}
        origin = _origin(annotation); annotation.record_hash = digest(origin)
        annotation.data = {**annotation.data, 'audit_id': _audit(db, p, SAVED, annotation.id, origin)}
    command = Command(workspace_id=p.workspace_id, instance_id=instance_id, run_id=run_id,
        actor_id=p.user_id, request_id=body.request_id, raw_sha256=raw_hash,
        outcome='saved' if annotation else 'rejected', annotation_id=annotation.id if annotation else None,
        record_hash='0' * 64, data={'raw': original, 'body_hash': digest(body.model_dump()),
            'rejection': rejection, 'annotation_record_hash': annotation.record_hash if annotation else None})
    db.add(command); db.flush()
    origin = _command_origin(command); command.record_hash = digest(origin)
    command.data = {**command.data, 'audit_id': _audit(db, p, COMMANDED, command.id, origin)}
    db.flush(); _checked_command(db, p, command, source)
    return command


def get(db, p, instance_id, run_id, annotation_id):
    source = _source(db, p, instance_id, run_id)
    row = scoped(db, Annotation, annotation_id, p)
    need(row.instance_id == instance_id and row.run_id == run_id, '标注不属于当前比较记录', 404)
    return checked(db, p, row, source)


def listing(db, p, instance_id, run_id, limit=10):
    source = _source(db, p, instance_id, run_id)
    rows = list(db.scalars(select(Annotation).where(Annotation.workspace_id == p.workspace_id,
        Annotation.instance_id == instance_id, Annotation.run_id == run_id)
        .order_by(Annotation.created_at.desc(), Annotation.id.desc()).limit(limit + 1)))
    return {'schema': 'pixel-annotation-list-v256', 'workspace_id': p.workspace_id, 'instance_id': instance_id,
        'run_id': run_id, 'items': [view(checked(db, p, row, source)) for row in rows[:limit]], 'has_more': len(rows) > limit}


def history(db, p, instance_id, run_id, *, limit=10, before_id=None):
    """Read older immutable versions without shifting offsets or resetting cursors.

    The caller supplies the existing RR read-only Session. Full source and
    reverse inventories are checked before resolving a cursor; deleting its
    physical row while its independent Audit remains is an integrity error.
    """
    need(type(limit) is int and 1 <= limit <= 20, '历史页大小必须是1至20的整数', 422)
    if before_id is not None:
        try:
            need(type(before_id) is str and len(before_id) == 36 and str(UUID(before_id)) == before_id,
                 '历史游标必须是规范小写UUID，请保留当前页面', 422)
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(422, '历史游标必须是规范小写UUID，请保留当前页面') from None
    source = _source(db, p, instance_id, run_id)
    query = select(Annotation).where(Annotation.workspace_id == p.workspace_id,
        Annotation.instance_id == instance_id, Annotation.run_id == run_id)
    if before_id is not None:
        cursor = scoped(db, Annotation, before_id, p)
        need(cursor.instance_id == instance_id and cursor.run_id == run_id,
             '历史游标不属于当前影像比较，未重新读取第一页', 404)
        checked(db, p, cursor, source)
        query = query.where(or_(Annotation.created_at < cursor.created_at,
            (Annotation.created_at == cursor.created_at) & (Annotation.id < cursor.id)))
    rows = list(db.scalars(query.order_by(Annotation.created_at.desc(), Annotation.id.desc()).limit(limit + 1)))
    items = [view(checked(db, p, row, source)) for row in rows[:limit]]
    has_more = len(rows) > limit
    return {'schema': 'pixel-annotation-history-v257', 'workspace_id': p.workspace_id,
        'instance_id': instance_id, 'run_id': run_id, 'before_id': before_id, 'items': items,
        'has_more': has_more, 'next_cursor': items[-1]['id'] if has_more else None}
