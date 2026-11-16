"""Explicit sharing of caller-confirmed V166 designs, with original-wire recovery.

Sharing never edits private reviews or original model replies. Schema/source and
execution approval remain distinct. Historical reads authenticate saved origin
and command/audit anchors without requiring a new source or confirmation.
"""
import copy
import hashlib
import json
from uuid import UUID, uuid5
from typing import Literal

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .contracts import digest
from . import report_full_semantic_handoff as full
from . import report_object_adoption_source_v169 as sources
from . import report_object_configuration_v169 as pure
from .models import Audit, SetupRevision, uid
from .report_configuration_models import ReportConfigurationRecord as Record
from .services import audit, require
from .typed_payload_codec_v131 import canonical_json

KIND = 'object_adopt_v169'
ACTION = 'setup.report_configuration.object_adopt_v169'
COMMAND = 'report-object-adoption-command-v169'
PREVIEW = 'report-object-adoption-preview-v169'
RECORD = 'report-object-adoption-record-v169'
RECEIPT = 'report-object-adoption-receipt-v169'
LEDGER = 'report-object-adoption-ledger-v169'
SOURCE = 'report-object-adoption-source-v169'
REPLY = 'report-object-adoption-reply-v169'
REQUEST = 'report-object-adoption-request-v169'
MAX_WIRE = 16384
MAX_LEDGER = 24 * 1024 * 1024
SHARE = 'confirmed_design_excluding_private_note_v169'


def fail(code, message, status=409):
    raise HTTPException(status, {'code': code, 'message': message})


def need(value):
    if not value:
        raise ValueError('新结构采用的来源、原命令、版本或保存回执无法核对')


def same(a, b):
    return canonical_json(a) == canonical_json(b)


def identity(value):
    need(type(value) is str and str(UUID(value)) == value)
    return value


def path(report_id):
    return '/api/workspace/report-evidence-readings/' + identity(report_id) + '/object-adoption-v169/commit'


class Target(full.rd.SafeContract):
    handoff_id: UUID
    record_hash: str = Field(strict=True, pattern=full.HASH)
    design_hash: str = Field(strict=True, pattern=full.HASH)


class PreviewCommand(full.rd.SafeContract):
    schema: Literal['report-object-adoption-command-v169']
    request_id: UUID
    origin: dict
    configuration_revision: int = Field(strict=True, ge=1)
    configuration_hash: str = Field(strict=True, pattern=full.HASH)
    share_scope: Literal['confirmed_design_excluding_private_note_v169']
    replace_target: Target | None


class CommitCommand(PreviewCommand):
    preview_hash: str = Field(strict=True, pattern=full.HASH)
    consent: Literal[True]


def parse_wire(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_WIRE:
        raise ValueError('新采用原请求超过保存上限')
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out); out[key] = value
        return out
    def constant(_):
        raise ValueError('请求包含非JSON数值')
    value = json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=pairs, parse_constant=constant)
    need(type(value) is dict and value.get('consent') is True)
    body = CommitCommand.model_validate(value)
    need(same(value, body.model_dump(mode='json')))
    pure.check_origin(body.origin)
    return body


def seal(value, key, maximum=MAX_LEDGER):
    encoded = canonical_json(value).encode('utf-8', 'strict')
    if len(encoded) > maximum:
        fail('object_adoption_capacity', '完整采用记录超过保存容量；私人稿和配置保留，请缩减范围后重新预览', 413)
    return {**value, key: digest(value)}


def command_hash(p, report_id, wire_sha256):
    return digest({'schema': 'report-object-adoption-command-hash-v169',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'report_id': identity(report_id),
        'method': 'POST', 'path': path(report_id), 'wire_sha256': wire_sha256})


def record_id(p, report_id, nonce):
    return str(uuid5(UUID(report_id), 'object-adopt-v169/' + p.workspace_id + '/' + p.user_id + '/' + str(nonce)))


def context(origin, basis, handoff_id):
    return {'handoff_id': handoff_id, 'report_id': origin['report_id'],
        'report_revision': basis['report_revision'], 'review_receipt_hash': origin['confirmed_command_hash'],
        'manual_value_hash': origin['confirmed_value_hash'], 'model_result_hash': basis['model_result_hash'],
        'source_hash': basis['source_hash'], 'catalog_hash': basis['catalog_hash'], 'context_hash': basis['context_hash']}


def preview_body(body):
    return PreviewCommand.model_validate({key: body.model_dump(mode='json')[key] for key in PreviewCommand.model_fields})


def _changes(design, previous):
    names = [b['name'] for b in design['business_bindings']]
    old = [] if previous is None else [b['name'] for b in previous['design']['business_bindings']]
    return {'mode': 'append' if previous is None else 'replace',
        'businesses_added': [n for n in names if n not in old],
        'businesses_removed': [n for n in old if n not in names],
        'businesses_updated': [n for n in names if n in old],
        'object_count': len(design['result']['objects']),
        'field_count': sum(len(obj['fields']) for obj in design['result']['objects']),
        'identity_unestablished_count': sum(obj['business_identity']['status'] == 'not_established' for obj in design['result']['objects'])}


def _materials(value):
    need(type(value) is list and len(value) == 2)
    for item in value:
        need(type(item) is dict and set(item) == {'id', 'name', 'version', 'hash', 'evidence_id'})
        identity(item['id']); identity(item['evidence_id'])
        need(type(item['version']) is int and item['version'] >= 1 and type(item['name']) is str)
        need(type(item['hash']) is str and len(item['hash']) == 64)
    return copy.deepcopy(value)


def _proposed(config, design, materials, previous=None):
    if previous is None:
        out = copy.deepcopy(config)
        out.setdefault('fullSemanticDesigns', []).append(copy.deepcopy(design))
        flows = pure.derive_flows(design)
        out.setdefault('businesses', []).extend(flows)
        out.setdefault('flows', {}).update(flows)
    else:
        from .report_full_semantic_replacement_v1 import apply_replacement
        out = apply_replacement(config, previous['design'], design)
    for material in _materials(materials):
        existing = next((m for m in out.setdefault('materials', []) if m.get('id') == material['id']), None)
        if existing is not None and existing.get('hash') != material['hash']:
            fail('object_adoption_material_conflict', '工作区资料与确认稿来源不一致；原配置保留')
        if existing is None:
            out['materials'].append({**material, 'archived': False})
    from .setup_automation import synchronize_reviews, metadata
    out = synchronize_reviews(config, out)
    if previous is not None:
        for name in pure.derive_flows(design):
            policy = out['automation']['policies'][name]
            policy['flow_review'] = metadata(out, name, policy, 'needs_review')
    full.old().setup().validate_configuration(out)
    return out


def _preview(p, report_id, body, design, materials, conflicts, previous):
    return seal({'schema': PREVIEW, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'report_id': report_id, 'body': preview_body(body).model_dump(mode='json'),
        'design': copy.deepcopy(design), 'flows': pure.derive_flows(design),
        'materials': _materials(materials), 'conflicts': list(conflicts),
        'changes': _changes(design, previous), 'source_status': 'unconfirmed',
        'can_confirm': False, 'can_publish': False}, 'preview_hash', 2 * 1024 * 1024)


def prepare(db, p, report_id, body, *, lock=False):
    source = sources.load_confirmed(db, p, report_id, lock=lock)
    if not same(source['origin'], body.origin):
        fail('object_adoption_confirmation_changed', '当前确认稿或原确认回执已变化；保留选择并重新预览')
    configuration = full.old().configuration_row(db, p, lock)
    full.old().configuration_cas(configuration, body)
    from .report_full_semantic_replacement import record_set, assert_active_configuration
    _, heads, _, snapshot = record_set(db, p, include_configuration=True)
    # Keep the real ORM configuration row (and its write lock on commit).
    # A concurrent preview edit is a normal CAS conflict, not corrupt history.
    full.old().configuration_cas(snapshot, body)
    if configuration.id != snapshot.id:
        fail('configuration_conflict', '工作区配置已变化，请保留选择并重新预览')
    assert_active_configuration(snapshot.data, heads)
    previous = None
    if body.replace_target is not None:
        target = body.replace_target
        previous = heads.get(str(target.handoff_id))
        if (previous is None or previous['actor_id'] != p.user_id or previous['report_id'] != report_id
                or previous['configuration_id'] != configuration.id):
            fail('object_adoption_target_changed', '未找到本人当前报告可替换的有效版本；原配置保留')
        if previous['record_hash'] != target.record_hash or previous['design']['design_hash'] != target.design_hash:
            fail('object_adoption_target_changed', '目标版本或保存回执已变化；请重新核对')
    try:
        design = pure.build_design(source['result'], source['object_identity_confirmations'], source['origin'],
            **context(source['origin'], source['basis'], record_id(p, report_id, body.request_id)))
        names = [b['name'] for b in design['business_bindings']]
        existing = set(full.old().names(configuration.data)) | set(configuration.data.get('flows', {}))
        removed = set() if previous is None else {b['name'] for b in previous['design']['business_bindings']}
        conflicts = [n for n in names if n in existing - removed]
        if len(full.old().names(configuration.data)) - len(removed) + len(names) > 20:
            fail('object_adoption_business_capacity', '采用后超过20项业务；私人稿和配置保留', 422)
        preview = _preview(p, report_id, body, design, source['materials'], conflicts, previous)
    except (ValueError, TypeError, KeyError):
        fail('object_adoption_structure', '确认稿的完整结构或来源无法加入配置；私人稿保留', 422)
    return configuration, preview, previous


def make_receipt(record):
    return seal({'schema': RECEIPT, 'request_id': record['request_id'], 'report_id': record['report_id'],
        'record_id': record['id'], 'record_hash': record['record_hash'], 'wire_sha256': record['wire_sha256'],
        'command_hash': record['command_hash'], 'preview_hash': record['preview_hash'],
        'accepted_revision': record['accepted_revision'], 'configuration_hash': record['configuration_hash'],
        'design_hash': record['design']['design_hash']}, 'receipt_hash')


def _audit_data(record):
    return {key: record[key] for key in ('request_id', 'record_hash', 'wire_sha256', 'accepted_revision')}


def checked_ledger(db, row, p, previous=None):
    try:
        need(row.kind == KIND and row.workspace_id == p.workspace_id)
        data = row.data
        need(type(data) is dict and set(data) == {'schema', 'record', 'command_wire', 'command_wire_sha256',
            'command_body', 'prior_configuration', 'accepted_configuration', 'receipt', 'ledger_hash'}
            and data['schema'] == LEDGER and data['ledger_hash'] == digest({k: v for k, v in data.items() if k != 'ledger_hash'}))
        raw = data['command_wire'].encode('utf-8', 'strict'); body = parse_wire(raw)
        wire_hash = hashlib.sha256(raw).hexdigest(); record = data['record']
        need(same(body.model_dump(mode='json'), data['command_body']) and data['command_wire_sha256'] == wire_hash)
        keys = {'schema', 'id', 'actor_id', 'workspace_id', 'report_id', 'configuration_id', 'request_id',
            'parent_id', 'command_hash', 'wire_sha256', 'preview_hash', 'design', 'materials', 'changes',
            'accepted_revision', 'configuration_hash', 'record_hash'}
        need(type(record) is dict and set(record) == keys and record['schema'] == RECORD)
        need(record['record_hash'] == digest({k: v for k, v in record.items() if k != 'record_hash'}))
        need(all(record[k] == getattr(row, k) for k in ('id', 'workspace_id', 'actor_id', 'report_id', 'configuration_id', 'request_id', 'parent_id')))
        owner = type('Owner', (), {'user_id': row.actor_id, 'workspace_id': row.workspace_id})()
        need(record['id'] == record_id(owner, row.report_id, body.request_id)
            and record['request_id'] == str(body.request_id) and record['wire_sha256'] == wire_hash
            and record['command_hash'] == command_hash(owner, row.report_id, wire_hash)
            and record['accepted_revision'] == body.configuration_revision + 1)
        source = sources.check_saved_origin(db, p, body.origin)
        need(source['origin']['actor_id'] == row.actor_id and source['origin']['report_id'] == row.report_id
            and source['origin']['workspace_id'] == row.workspace_id)
        expected_design = pure.build_design(source['result'], source['object_identity_confirmations'], source['origin'],
            **context(source['origin'], source['basis'], row.id))
        need(same(expected_design, record['design']))
        if previous is None:
            need(row.parent_id is None and body.replace_target is None)
        else:
            target = body.replace_target
            need(target is not None and str(target.handoff_id) == row.parent_id == previous['id']
                and target.record_hash == previous['record_hash'] and target.design_hash == previous['design']['design_hash']
                and previous['actor_id'] == row.actor_id and previous['report_id'] == row.report_id
                and previous['configuration_id'] == row.configuration_id)
        prior_config = data['prior_configuration']
        need(digest(prior_config) == body.configuration_hash)
        from .report_full_semantic_replacement import revision_value
        need(same(prior_config, revision_value(db, p, record, body.configuration_revision))
            and same(data['accepted_configuration'], revision_value(db, p, record, record['accepted_revision'])))
        rebuilt = _proposed(prior_config, expected_design, record['materials'], previous)
        need(same(rebuilt, data['accepted_configuration']) and digest(rebuilt) == record['configuration_hash'])
        preview = _preview(owner, row.report_id, body, expected_design, record['materials'], [], previous)
        need(preview['preview_hash'] == body.preview_hash == record['preview_hash']
            and same(record['changes'], preview['changes']) and same(data['receipt'], make_receipt(record)))
        rows = list(db.scalars(select(Audit).where(Audit.workspace_id == row.workspace_id,
            Audit.action == ACTION, Audit.object_id == row.id)))
        need(len(rows) == 1 and rows[0].actor_id == row.actor_id and same(rows[0].data, _audit_data(record)))
        seal({k: v for k, v in data.items() if k != 'ledger_hash'}, 'ledger_hash')
        return data
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        fail('object_adoption_integrity', '完整采用记录或其独立确认依据无法核对；原稿与配置保留')


def _saved(db, p, row):
    from .report_full_semantic_replacement import record_set
    records, _, _ = record_set(db, p)
    if row.id not in records:
        fail('object_adoption_integrity', '原采用记录不在完整版本历史中')
    return row.data['record']


def reply(db, p, row):
    from .report_full_semantic_replacement import record_set, assert_active_configuration
    records, heads, _, config = record_set(db, p, include_configuration=True)
    record = records.get(row.id)
    if record is None:
        fail('object_adoption_integrity', '原采用记录不在完整版本历史中')
    assert_active_configuration(config.data, heads)
    return {'schema': REPLY, 'record': copy.deepcopy(record), 'receipt': copy.deepcopy(row.data['receipt']),
        'configuration': full.old().setup().draft_view(config)}


def source_view(db, p, report_id):
    # Ownership is checked before catching a recoverable unavailable source.
    from .report_object_revision_store_v166 import _owner
    _owner(db, p, report_id)
    issue = None; source = None
    try:
        source = sources.load_confirmed(db, p, report_id)
    except HTTPException as error:
        if error.status_code not in {403, 409, 422}:
            raise
        issue = '当前尚无可采用的最新确认稿，或来源/权限已有变化；已保存内容保持，请返回核对'
    from .report_full_semantic_replacement import record_set, assert_active_configuration
    _, heads, _, configuration = record_set(db, p, include_configuration=True)
    assert_active_configuration(configuration.data, heads)
    targets = [{'handoff_id': r['id'], 'record_hash': r['record_hash'], 'design_hash': r['design']['design_hash'],
        'business_names': [b['name'] for b in r['design']['business_bindings']],
        'actor_id': r['actor_id'], 'report_id': r['report_id']} for r in heads.values()
        if r['actor_id'] == p.user_id and r['report_id'] == report_id]
    return {'schema': SOURCE, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'report_id': report_id, 'available': source is not None, 'source_issue': issue,
        'origin': None if source is None else source['origin'], 'configuration_revision': configuration.version,
        'configuration_hash': digest(configuration.data), 'targets': targets}


def commit(db, p, report_id, raw):
    try:
        identity(report_id); body = parse_wire(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        fail('object_adoption_wire', '采用原请求格式或字节无法核对；原稿保留', 422)
    require(p, 'admin', 'operator')
    full.old().nonce_lock(db, p, body.request_id)
    prior = full.old().request_row(db, p, body.request_id)
    wire_hash = hashlib.sha256(raw).hexdigest()
    if prior is not None:
        if (prior.kind != KIND or prior.actor_id != p.user_id or prior.report_id != report_id
                or prior.data.get('command_wire_sha256') != wire_hash):
            fail('object_adoption_nonce_conflict', '原采用编号已用于另一正文或范围；不会覆盖原回执')
        return reply(db, p, prior)
    configuration, preview, previous = prepare(db, p, report_id, preview_body(body), lock=True)
    if preview['preview_hash'] != body.preview_hash or preview['conflicts']:
        fail('object_adoption_preview_changed', '预览已变化或有同名业务；原配置保留，请重新核对')
    proposed = _proposed(configuration.data, preview['design'], preview['materials'], previous)
    full.old().setup().automation_review_actor(db, p)
    record = seal({'schema': RECORD, 'id': preview['design']['handoff_id'], 'actor_id': p.user_id,
        'workspace_id': p.workspace_id, 'report_id': report_id, 'configuration_id': configuration.id,
        'request_id': str(body.request_id), 'parent_id': None if previous is None else previous['id'],
        'command_hash': command_hash(p, report_id, wire_hash), 'wire_sha256': wire_hash,
        'preview_hash': body.preview_hash, 'design': preview['design'], 'materials': preview['materials'],
        'changes': preview['changes'], 'accepted_revision': configuration.version + 1,
        'configuration_hash': digest(proposed)}, 'record_hash')
    receipt = make_receipt(record)
    data = seal({'schema': LEDGER, 'record': record, 'command_wire': raw.decode('utf-8'),
        'command_wire_sha256': wire_hash, 'command_body': body.model_dump(mode='json'),
        'prior_configuration': copy.deepcopy(configuration.data), 'accepted_configuration': copy.deepcopy(proposed),
        'receipt': receipt}, 'ledger_hash')
    saved = Record(id=record['id'], workspace_id=p.workspace_id, actor_id=p.user_id,
        report_id=report_id, configuration_id=configuration.id, request_id=str(body.request_id),
        kind=KIND, parent_id=record['parent_id'], data=data)
    db.execute(insert(SetupRevision).values(id=uid(), workspace_id=p.workspace_id, draft_id=configuration.id,
        version=configuration.version, data=configuration.data).on_conflict_do_nothing(index_elements=['draft_id', 'version']))
    db.add(saved); configuration.data = proposed; configuration.version += 1
    db.add(SetupRevision(workspace_id=p.workspace_id, draft_id=configuration.id, version=configuration.version, data=proposed))
    audit(db, p, ACTION, saved.id, _audit_data(record)); db.flush()
    checked_ledger(db, saved, p, previous)
    db.commit()
    return reply(db, p, saved)


def request_result(db, p, report_id, request_id, expected_sha256):
    full.old().nonce_lock(db, p, request_id)
    row = full.old().request_row(db, p, request_id)
    if row is None:
        from .report_object_revision_store_v166 import _owner
        _owner(db, p, report_id)
        # Missing is safe to report only after checking both command/audit
        # directions and the active configuration in the same read snapshot.
        from .report_full_semantic_replacement import record_set, assert_active_configuration
        _, heads, _, configuration = record_set(db, p, include_configuration=True)
        assert_active_configuration(configuration.data, heads)
        return {'schema': REQUEST, 'request_id': str(request_id), 'wire_sha256': expected_sha256,
            'state': 'missing', 'reply': None}
    if row.kind != KIND or row.actor_id != p.user_id or row.report_id != report_id:
        fail('object_adoption_request_missing', '未找到本人当前工作区的原采用请求', 404)
    if row.data.get('command_wire_sha256') != expected_sha256:
        fail('object_adoption_request_hash', '原请求字节摘要不同；原输入与回执保留')
    return {'schema': REQUEST, 'request_id': str(request_id), 'wire_sha256': expected_sha256,
        'state': 'saved', 'reply': reply(db, p, row)}
