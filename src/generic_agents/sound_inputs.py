"""Model-independent immutable original audio; upload never performs inference."""
import copy
import hashlib
import json
import os
import stat
from datetime import UTC
from typing import Literal
from uuid import UUID
from fastapi import HTTPException
from pydantic import Field, FiniteFloat, StrictInt, field_validator
from sqlalchemy import select, text, func
from .contracts import Contract, digest
from .models import Audit, DomainInstance, Evidence, uid
from .services import scoped, require
from .sound_models import SoundInput

ACTION = 'sound.input_saved_v1'
MAX_BYTES = 20 * 1024 * 1024
MEDIA = ('audio/wav', 'audio/x-wav', 'audio/wave', 'audio/mpeg', 'audio/mp3',
         'audio/flac', 'audio/x-flac', 'audio/ogg', 'audio/mp4', 'audio/x-m4a')
ERROR = '原音、上传申请或独立保存凭据无法核对，请保留原记录并恢复原件'


def need(value, message=ERROR, code=409):
    if not value:
        raise HTTPException(code, message)


class SoundConfiguration(Contract):
    channel: Literal['mono', 'mix', '1', '2', '3', '4', '5', '6', '7', '8'] = 'mono'
    threshold: FiniteFloat = Field(default=.5, gt=0, lt=1)


class UploadIntent(Contract):
    schema: Literal['sound-upload-v1']
    request_id: str
    name: str = Field(min_length=1, max_length=250)
    media_type: Literal[MEDIA]
    size_bytes: StrictInt = Field(gt=0, le=MAX_BYTES)
    input_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    configuration: SoundConfiguration = Field(default_factory=SoundConfiguration)

    @field_validator('request_id')
    @classmethod
    def request_uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError('请保留原上传的标准编号')
        return value

    @field_validator('name')
    @classmethod
    def safe_name(cls, value):
        if any(c in value for c in ('\x00', '\r', '\n', '/', '\\')):
            raise ValueError('原音名称不能包含路径或控制字符')
        return value


def parse_intent(raw):
    try:
        need(type(raw) is str and len(raw.encode('utf8')) <= 32768, '上传申请过大', 422)
        return UploadIntent.model_validate_json(raw)
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(422, '原音上传申请格式不完整，请保留原文核对') from None


def inspect_header(content, media_type):
    need(type(content) is bytes and 0 < len(content) <= MAX_BYTES, '原音为空或超过20MB', 413)
    wav = len(content) >= 12 and content[:4] == b'RIFF' and content[8:12] == b'WAVE'
    matches = (wav if media_type in {'audio/wav', 'audio/x-wav', 'audio/wave'} else
        content.startswith(b'fLaC') if media_type in {'audio/flac', 'audio/x-flac'} else
        content.startswith(b'OggS') if media_type == 'audio/ogg' else
        len(content) >= 12 and content[4:8] == b'ftyp' if media_type in {'audio/mp4', 'audio/x-m4a'} else
        content.startswith(b'ID3') or len(content) >= 2 and content[0] == 255 and content[1] & 224 == 224)
    need(media_type in MEDIA and matches, '文件头与音频格式不符；未读取外部引用', 415)


def _proof(db, p, row):
    audits = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ACTION, Audit.object_id == row.id).limit(2)))
    expected = {'schema': 'sound-input-saved-audit-v1', 'input_id': row.id,
        'workspace_id': row.workspace_id, 'instance_id': row.instance_id, 'owner_id': row.owner_id,
        'request_id': row.request_id, 'record_hash': digest(row.data)}
    need(len(audits) == 1 and audits[0].actor_id == row.owner_id and digest(audits[0].data) == digest(expected))
    return audits[0]


def input_bytes(db, p, row):
    from .config import settings
    evidence = scoped(db, Evidence, row.evidence_id, p)
    snapshot = row.data['snapshot']
    need(evidence.sha256 == snapshot['input_sha256'] and evidence.size_bytes == snapshot['size_bytes']
        and evidence.media_type == snapshot['media_type'] and evidence.storage_key == p.workspace_id + '/' + evidence.sha256 + '.bin'
        and evidence.data.get('purpose') == 'sound_input_v1' and evidence.data.get('instance_id') == row.instance_id
        and evidence.data.get('uploaded_by') == row.owner_id and evidence.data.get('cloud_model_ids') == [])
    uploads = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'evidence.upload', Audit.object_id == evidence.id).limit(2)))
    need(len(uploads) == 1 and uploads[0].actor_id == row.owner_id and digest(uploads[0].data) == digest({
        'media_type': evidence.media_type, 'sha256': evidence.sha256, 'cloud_model_id': None, 'consent': False}))
    root = settings().storage_dir
    try:
        directory = os.open(root / p.workspace_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            descriptor = os.open(evidence.sha256 + '.bin', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            with os.fdopen(descriptor, 'rb') as stream:
                before = os.fstat(stream.fileno())
                need(stat.S_ISREG(before.st_mode) and before.st_size == evidence.size_bytes <= MAX_BYTES)
                content = stream.read(MAX_BYTES + 1)
                after = os.fstat(stream.fileno())
                need(all(getattr(before, k) == getattr(after, k) for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')))
        finally:
            os.close(directory)
    except OSError:
        raise HTTPException(409, ERROR) from None
    need(len(content) == evidence.size_bytes and hashlib.sha256(content).hexdigest() == evidence.sha256)
    inspect_header(content, evidence.media_type)
    return evidence, content


def checked(db, p, identity):
    row = scoped(db, SoundInput, identity, p)
    try:
        d = row.data; s = d['snapshot']; intent = parse_intent(d['intent_raw'])
        need(row.version == 1 and set(d) == {'schema', 'intent', 'intent_raw', 'raw_sha256', 'body_hash', 'snapshot', 'snapshot_hash'}
            and d['schema'] == 'sound-input-record-v1' and digest(intent.model_dump(mode='json')) == d['body_hash']
            and digest(d['intent']) == d['body_hash'] and hashlib.sha256(d['intent_raw'].encode()).hexdigest() == d['raw_sha256']
            and intent.request_id == row.request_id and digest(s) == d['snapshot_hash'])
        expected = {'schema': 'sound-input-snapshot-v1', 'id': row.id, 'workspace_id': row.workspace_id,
            'instance_id': row.instance_id, 'owner_id': row.owner_id, 'evidence_id': row.evidence_id,
            **{key: getattr(intent, key) for key in ('name', 'media_type', 'size_bytes', 'input_sha256')},
            'configuration': intent.configuration.model_dump(mode='json'),
            'metadata': {'status': 'not_decoded', 'original_channels': None, 'original_sample_rate': None, 'duration_seconds': None},
            'created_at': row.created_at.astimezone(UTC).isoformat()}
        need(digest(s) == digest(expected)); scoped(db, DomainInstance, row.instance_id, p); _proof(db, p, row)
        input_bytes(db, p, row)
        return row
    except HTTPException as error:
        if error.status_code in {404, 413, 415, 422}:
            raise HTTPException(409, ERROR) from None
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(409, ERROR) from None


def view(row):
    return {'schema': 'sound-input-view-v1', 'id': row.id, 'version': row.version, 'status': 'stored',
        'workspace_id': row.workspace_id, 'instance_id': row.instance_id, 'request_id': row.request_id,
        'owner_id': row.owner_id, 'snapshot': copy.deepcopy(row.data['snapshot']), 'snapshot_hash': row.data['snapshot_hash'],
        'upload': {k: row.data[k] for k in ('body_hash', 'raw_sha256')},
        'content_path': '/api/sounds/inputs/' + row.id + '/content', 'created_at': row.created_at.astimezone(UTC).isoformat()}


def find_original(db, p, instance_id, request_id, *, read=True):
    rows = list(db.scalars(select(SoundInput).where(SoundInput.workspace_id == p.workspace_id,
        SoundInput.request_id == request_id).limit(2)))
    anchors = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id, Audit.action == ACTION,
        Audit.data['request_id'].as_string() == request_id).limit(2)))
    if not rows:
        if anchors:
            need(all(a.actor_id == p.user_id and a.data.get('instance_id') == instance_id for a in anchors),
                '无法核对此原上传申请', 404 if read else 409)
        need(not anchors); return None
    need(len(rows) == 1)
    row = rows[0]
    # Scope columns are untrusted until the independent original anchor agrees.
    # A surviving second original cannot be hidden by deleting its input row.
    need(len(anchors) == 1 and anchors[0].object_id == row.id
        and anchors[0].actor_id == row.owner_id
        and anchors[0].data.get('instance_id') == row.instance_id
        and anchors[0].data.get('owner_id') == row.owner_id)
    _proof(db, p, row)
    need(row.owner_id == p.user_id and row.instance_id == instance_id, '无法核对此原上传申请', 404 if read else 409)
    return checked(db, p, row.id)


def save(db, p, instance_id, intent_raw, content):
    intent = parse_intent(intent_raw); actual_sha = hashlib.sha256(content).hexdigest()
    need(len(content) == intent.size_bytes and actual_sha == intent.input_sha256, '原音字节与上传申请不一致', 422)
    inspect_header(content, intent.media_type)
    old = find_original(db, p, instance_id, intent.request_id, read=False)
    if old is not None:
        need(old.data['intent_raw'] == intent_raw, '原上传编号不能替换文件、名称、声道或阈值')
        return old, False
    require(p, 'admin', 'operator')
    from .relations import lock_current_actor
    lock_current_actor(db, p)
    instance = db.scalar(select(DomainInstance).where(DomainInstance.workspace_id == p.workspace_id,
        DomainInstance.id == instance_id).with_for_update(read=True).execution_options(populate_existing=True))
    need(instance is not None, '业务实例不存在', 404)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(digest({'sound_upload': p.workspace_id, 'request_id': intent.request_id})[:15], 16)})
    old = find_original(db, p, instance_id, intent.request_id, read=False)
    if old is not None:
        need(old.data['intent_raw'] == intent_raw, '原上传编号不能更换原文'); return old, False
    need(instance.status == 'partially_active' and instance.active_release_id is not None,
        '业务实例尚未启用或已暂停；可读取原件，但不能保存新原音')
    from .evidence import save_immutable
    evidence = save_immutable(db, p, content, intent.media_type, {'modality': 'audio', 'purpose': 'sound_input_v1', 'instance_id': instance_id})
    row = SoundInput(id=uid(), workspace_id=p.workspace_id, instance_id=instance_id, owner_id=p.user_id,
        request_id=intent.request_id, evidence_id=evidence.id)
    db.add(row); db.flush()
    snapshot = {'schema': 'sound-input-snapshot-v1', 'id': row.id, 'workspace_id': p.workspace_id,
        'instance_id': instance_id, 'owner_id': p.user_id, 'evidence_id': evidence.id,
        **{k: getattr(intent, k) for k in ('name', 'media_type', 'size_bytes', 'input_sha256')},
        'configuration': intent.configuration.model_dump(mode='json'),
        'metadata': {'status': 'not_decoded', 'original_channels': None, 'original_sample_rate': None, 'duration_seconds': None},
        'created_at': row.created_at.astimezone(UTC).isoformat()}
    row.data = {'schema': 'sound-input-record-v1', 'intent': intent.model_dump(mode='json'), 'intent_raw': intent_raw,
        'body_hash': digest(intent.model_dump(mode='json')), 'raw_sha256': hashlib.sha256(intent_raw.encode()).hexdigest(),
        'snapshot': snapshot, 'snapshot_hash': digest(snapshot)}
    db.add(Audit(workspace_id=p.workspace_id, actor_id=p.user_id, action=ACTION, object_id=row.id,
        data={'schema': 'sound-input-saved-audit-v1', 'input_id': row.id, 'workspace_id': p.workspace_id,
            'instance_id': instance_id, 'owner_id': p.user_id, 'request_id': intent.request_id, 'record_hash': digest(row.data)}))
    db.flush(); return checked(db, p, row.id), True
