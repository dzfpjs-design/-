"""Explicit whole-design sharing. Private notes, source approval and execution stay separate."""
import copy
import json
from typing import Literal
from uuid import UUID, uuid5

from fastapi import APIRouter
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from . import report_reading_drafts as rd
from . import report_evidence_pipeline as pipeline
from .contracts import digest
from .models import SetupRevision, uid
from .report_configuration_models import ReportConfigurationRecord as Record
from .services import audit, require

router = APIRouter()
KIND = 'full_handoff'
PREVIEW = 'report-full-semantic-configuration-preview-v1'
RECORD = 'report-full-semantic-handoff-v1'
RECEIPT = 'report-full-semantic-handoff-receipt-v1'
LEDGER = 'report-full-semantic-handoff-ledger-v1'
MAX_BYTES = 2 * 1024 * 1024
HASH = r'^[a-f0-9]{64}$'


def fail(code, message, status=409):
    from fastapi import HTTPException
    raise HTTPException(status, {'code': code, 'message': message})


def old():
    from . import report_configuration_handoff
    return report_configuration_handoff


def pure():
    from . import report_full_semantic_configuration_v1
    return report_full_semantic_configuration_v1


class PreviewCommand(rd.SafeContract):
    schema: Literal['report-full-semantic-handoff-command-v1']
    request_id: UUID
    report_revision: int = Field(strict=True, ge=1)
    review_receipt_hash: str = Field(strict=True, pattern=HASH)
    manual_value_hash: str = Field(strict=True, pattern=HASH)
    model_result_hash: str = Field(strict=True, pattern=HASH)
    source_hash: str = Field(strict=True, pattern=HASH)
    catalog_hash: str = Field(strict=True, pattern=HASH)
    context_hash: str = Field(strict=True, pattern=HASH)
    configuration_revision: int = Field(strict=True, ge=1)
    configuration_hash: str = Field(strict=True, pattern=HASH)
    share_scope: Literal['whole_result_excluding_private_note_v1']


class HandoffCommand(PreviewCommand):
    preview_hash: str = Field(strict=True, pattern=HASH)
    consent: Literal[True]

    @field_validator('consent', mode='before')
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError('请明确同意将整份业务设计加入工作区配置')
        return value


def seal(value, key):
    if not rd.jsonb_compatible(value) or len(json.dumps(value, ensure_ascii=False).encode()) > MAX_BYTES:
        fail('full_semantic_capacity', '完整设计超过保存上限；原稿和配置保留', 413)
    return {**value, key: digest(value)}


def command_hash(p, identity, body):
    return digest({'schema': 'report-full-semantic-handoff-command-hash-v1',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'report_id': str(identity),
        'body': body.model_dump(mode='json')})


def handoff_id(p, identity, request_id):
    return str(uuid5(UUID(str(identity)), 'full-semantic-handoff-v1:' + p.workspace_id + ':' + p.user_id + ':' + str(request_id)))


def context(body, identity, record_id):
    return {key: getattr(body, key) for key in ('report_revision', 'review_receipt_hash', 'manual_value_hash',
        'model_result_hash', 'source_hash', 'catalog_hash', 'context_hash')} | {'report_id': str(identity), 'handoff_id': record_id}


def preview_body(body):
    value = body.model_dump(mode='json')
    return PreviewCommand.model_validate({key: value[key] for key in PreviewCommand.model_fields})


def current_manual(row, p, body):
    pipeline.check_row(row, p)
    value = row.data['review_value']; history = row.data['review_history']
    if (row.status not in {'review', 'needs_clarification'} or not history or not isinstance(value, dict)
            or value.get('schema') != 'report-evidence-manual-review-v5'):
        fail('full_semantic_saved_review_required', '请先完整核对并保存本人当前人工稿')
    receipt = history[-1]['receipt']; preview = row.data['preview']
    actual = {'report_revision': row.version, 'review_receipt_hash': receipt['receipt_hash'],
        'manual_value_hash': digest(value), 'model_result_hash': row.data['model_result_hash'],
        'source_hash': preview['source_hash'], 'catalog_hash': pipeline._catalog(preview)['descriptor']['catalog_hash'],
        'context_hash': digest(preview['map_preparation']['context'])}
    if (receipt['accepted_revision'] != row.version or receipt['value_hash'] != digest(value)
            or any(getattr(body, key) != expected for key, expected in actual.items())):
        fail('full_semantic_review_changed', '当前人工稿、来源或保存回执已变化，请保留输入并重新核对')
    return copy.deepcopy(value['result'])


def material_refs(db, p, row, *, lock):
    from .report_full_reading_source import freeze_full_source
    source = row.data['preview']['source_snapshot']
    current = freeze_full_source(db, p, row.data['preview']['body']['selection'], lock=lock)
    if current != source:
        fail('full_semantic_source_changed', '本稿使用的资料或页面版本已变化；不会自动改用新版本')
    return [{**copy.deepcopy(source[key]['ref']), 'name': source[key]['name'],
        'evidence_id': source[key]['evidence_id']} for key in ('original', 'copy')]


def make_preview(p, identity, body, design, flows, materials, conflicts):
    return seal({'schema': PREVIEW, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'report_id': str(identity), 'body': preview_body(body).model_dump(mode='json'),
        'design': copy.deepcopy(design), 'flows': copy.deepcopy(flows), 'materials': copy.deepcopy(materials),
        'conflicts': conflicts, 'source_status': 'unconfirmed', 'can_confirm': False, 'can_publish': False}, 'preview_hash')


def prepare(db, p, row, configuration, body, *, lock):
    old().configuration_cas(configuration, body)
    result = current_manual(row, p, body)
    try:
        design = pure().build_design(result, **context(body, row.id, handoff_id(p, row.id, body.request_id)))
        flows = pure().derive_flows(design)
    except (ValueError, TypeError, KeyError):
        fail('full_semantic_mapping_invalid', '业务名称、完整结构或明确去向无法加入当前配置；人工稿保留', 422)
    existing = set(old().names(configuration.data)) | set(configuration.data.get('flows', {}))
    conflicts = [name for name in flows if name in existing]
    if len(old().names(configuration.data)) + len(flows) > 20:
        fail('full_semantic_business_capacity', '加入后超过20项业务；请先核对业务范围，原配置保留', 422)
    return make_preview(p, row.id, body, design, flows, material_refs(db, p, row, lock=lock), conflicts)


def checked_ledger(row, p, *, private=False):
    try:
        data = row.data
        if (row.workspace_id != p.workspace_id or row.kind != KIND or private and row.actor_id != p.user_id
                or set(data) != {'schema', 'record', 'command', 'receipt', 'ledger_hash'} or data['schema'] != LEDGER
                or data['ledger_hash'] != digest({k: v for k, v in data.items() if k != 'ledger_hash'})):
            raise ValueError('ledger')
        record, receipt = data['record'], data['receipt']
        if set(record) != set(('schema id actor_id workspace_id report_id configuration_id request_id command_hash '
                'preview_hash design materials accepted_revision configuration_hash record_hash').split()):
            raise ValueError('record shape')
        if (record['schema'] != RECORD or record['record_hash'] != digest({k: v for k, v in record.items() if k != 'record_hash'})
                or any(record[k] != getattr(row, k) for k in ('id', 'actor_id', 'workspace_id', 'report_id', 'configuration_id', 'request_id'))):
            raise ValueError('record binding')
        command = HandoffCommand.model_validate(data['command'])
        owner = type('Owner', (), {'user_id': row.actor_id, 'workspace_id': row.workspace_id})()
        if (str(command.request_id) != row.request_id or record['command_hash'] != command_hash(owner, row.report_id, command)
                or record['preview_hash'] != command.preview_hash or record['accepted_revision'] != command.configuration_revision + 1
                or record['id'] != handoff_id(owner, row.report_id, command.request_id)):
            raise ValueError('command')
        pure().check_design(record['design'], **context(command, row.report_id, row.id))
        if record['materials'] == [] or len(record['materials']) != 2:
            raise ValueError('materials')
        for material in record['materials']:
            if set(material) != {'id', 'name', 'version', 'hash', 'evidence_id'}:
                raise ValueError('material shape')
        expected_preview = make_preview(owner, row.report_id, command, record['design'],
            pure().derive_flows(record['design']), record['materials'], [])
        if expected_preview['preview_hash'] != command.preview_hash or receipt != make_receipt(record):
            raise ValueError('receipt')
        seal({k: v for k, v in data.items() if k != 'ledger_hash'}, 'ledger_hash')
        return data
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        fail('full_semantic_record_integrity', '完整业务设计交接记录无法核对；原记录保留')


def make_receipt(record):
    return seal({'schema': RECEIPT, **{k: record[k] for k in ('actor_id', 'workspace_id', 'report_id', 'request_id',
        'command_hash', 'preview_hash', 'accepted_revision', 'configuration_hash', 'record_hash')},
        'handoff_id': record['id'], 'design_hash': record['design']['design_hash']}, 'receipt_hash')


def reply(db, p, row):
    data = checked_ledger(row, p, private=True); config = old().configuration_row(db, p)
    return {'schema': 'report-full-semantic-handoff-reply-v1', 'record': copy.deepcopy(data['record']),
        'receipt': copy.deepcopy(data['receipt']), 'configuration': old().setup().draft_view(config),
        'current_matches': config.version == data['receipt']['accepted_revision']
            and digest(config.data) == data['receipt']['configuration_hash']}


def shared_records(db, p):
    from .report_full_semantic_replacement import active_records
    return active_records(db, p)


def validate_configuration(config):
    designs = config.get('fullSemanticDesigns', [])
    if not isinstance(designs, list) or len(designs) > 20:
        raise ValueError('完整业务设计清单格式无效')
    ids = set()
    names = set(old().names(config))
    for design in designs:
        pure().check_design(design)
        if design['handoff_id'] in ids:
            raise ValueError('完整业务设计不能重复')
        ids.add(design['handoff_id'])
        if any(binding['name'] not in names for binding in design['business_bindings']):
            raise ValueError('完整设计的业务不能从业务清单移除')
    # Check every issued binding even when a proposed edit removes all flows.
    pure().signature_context(config, {})
    for flow in config.get('flows', {}).values():
        pure().signature_context(config, flow)


def protect_configuration(db, p, previous, proposed):
    out = copy.deepcopy(proposed); validate_configuration(out)
    from .report_full_semantic_schema_protection import protect_configuration as protect_schema_adoptions
    out = protect_schema_adoptions(db, p, previous, out)
    from .report_full_semantic_runtime_adoption import protect_configuration as protect_runtime_adoptions
    out = protect_runtime_adoptions(db, p, previous, out)
    from .report_full_semantic_graph_adoption import protect_configuration as protect_graph_adoptions
    protect_graph_adoptions(db, p, previous, out)
    records = shared_records(db, p); issued = {r['id']: r['design'] for r in records}
    designs = {d['handoff_id']: d for d in out.get('fullSemanticDesigns', [])}
    if designs != issued:
        fail('full_semantic_active_version_changed', '必须保留完整设计的当前有效版本；不能恢复旧版本或删除来源')
    for design in previous.get('fullSemanticDesigns', []):
        if designs.get(design['handoff_id']) != design:
            fail('full_semantic_origin_missing', '已加入的完整设计及来源不能删除或改写；原配置保留')
    if any(issued.get(key) != value for key, value in designs.items()):
        fail('full_semantic_origin_invalid', '完整设计不匹配本工作区已保存的共享记录')
    for record in records:
        expected = pure().derive_flows(record['design'])
        for name, original in expected.items():
            flow = old().resolve_flow(out, name)
            ids = {s['id'] for s in original['steps']}
            candidates = [f for f in out.get('flows', {}).values() if f.get('fullSemanticSource') == original['fullSemanticSource']
                or ids.intersection(s.get('id') for s in f.get('steps', []))]
            if name in old().names(out) and flow not in candidates:
                fail('full_semantic_marker_missing', '已加入业务的完整设计来源不能移除或改成旧流程')
            for candidate in candidates:
                if (record['id'] not in designs or candidate.get('fullSemanticSource') != original['fullSemanticSource']):
                    fail('full_semantic_marker_invalid', '完整设计的业务来源或步骤映射被替换')
                if candidate.get('confirmedSignature') or candidate.get('confirmedAt'):
                    fail('full_semantic_review_required', '完整设计尚待正式来源及执行条件核对，不能确认或发布')
    return out


@router.post('/report-evidence-readings/{identity}/full-configuration-preview')
def preview_route(identity: UUID, body: PreviewCommand, p: pipeline.P, db: pipeline.DB):
    require(p, 'admin', 'operator')
    from .report_object_revision_store_v166 import reject_legacy_adoption
    reject_legacy_adoption(db, p, str(identity))
    row = pipeline.owned(db, p, identity)
    return prepare(db, p, row, old().configuration_row(db, p), body, lock=False)


@router.post('/report-evidence-readings/{identity}/full-configuration-handoff')
def submit_route(identity: UUID, body: HandoffCommand, p: pipeline.P, db: pipeline.DB):
    require(p, 'admin', 'operator'); old().nonce_lock(db, p, body.request_id)
    prior = old().request_row(db, p, body.request_id)
    if prior is not None:
        if prior.kind != KIND or prior.actor_id != p.user_id or prior.report_id != str(identity):
            fail('full_semantic_nonce_conflict', '此编号已用于另一操作或范围')
        data = checked_ledger(prior, p, private=True)
        if data['record']['command_hash'] != command_hash(p, identity, body):
            fail('full_semantic_nonce_conflict', '原加入编号不能替换正文；原回执保留')
        return reply(db, p, prior)
    from .report_object_revision_store_v166 import reject_legacy_adoption
    reject_legacy_adoption(db, p, str(identity))
    row = pipeline.owned(db, p, identity, True); configuration = old().configuration_row(db, p, True)
    preview = prepare(db, p, row, configuration, body, lock=True)
    if preview['preview_hash'] != body.preview_hash:
        fail('full_semantic_preview_changed', '完整业务设计预览已变化，请重新核对')
    if preview['conflicts']:
        fail('full_semantic_business_conflict', '存在同名业务；本次不会覆盖已有配置')
    proposed = copy.deepcopy(configuration.data)
    proposed.setdefault('fullSemanticDesigns', []).append(copy.deepcopy(preview['design']))
    proposed.setdefault('businesses', []).extend(preview['flows'])
    proposed.setdefault('flows', {}).update(copy.deepcopy(preview['flows']))
    for material in preview['materials']:
        existing = next((m for m in proposed.setdefault('materials', []) if m.get('id') == material['id']), None)
        if existing is not None and existing.get('hash') != material['hash']:
            fail('full_semantic_material_conflict', '工作区已有资料与本稿来源不同，原配置保留')
        if existing is None:
            proposed['materials'].append({**copy.deepcopy(material), 'archived': False})
    from .setup_automation import synchronize_reviews
    proposed = synchronize_reviews(configuration.data, proposed)
    old().setup().validate_configuration(proposed)
    old().setup().automation_review_actor(db, p)
    record = seal({'schema': RECORD, 'id': preview['design']['handoff_id'], 'actor_id': p.user_id,
        'workspace_id': p.workspace_id, 'report_id': str(identity), 'configuration_id': configuration.id,
        'request_id': str(body.request_id), 'command_hash': command_hash(p, identity, body),
        'preview_hash': body.preview_hash, 'design': preview['design'], 'materials': preview['materials'],
        'accepted_revision': configuration.version + 1, 'configuration_hash': digest(proposed)}, 'record_hash')
    receipt = make_receipt(record)
    data = seal({'schema': LEDGER, 'record': record, 'command': body.model_dump(mode='json'), 'receipt': receipt}, 'ledger_hash')
    saved = Record(id=record['id'], workspace_id=p.workspace_id, actor_id=p.user_id, report_id=str(identity),
        configuration_id=configuration.id, request_id=str(body.request_id), kind=KIND, parent_id=None, data=data)
    checked_ledger(saved, p, private=True)
    db.execute(insert(SetupRevision).values(id=uid(), workspace_id=p.workspace_id, draft_id=configuration.id,
        version=configuration.version, data=configuration.data).on_conflict_do_nothing(index_elements=['draft_id', 'version']))
    db.add(saved); configuration.data = proposed; configuration.version += 1
    db.add(SetupRevision(workspace_id=p.workspace_id, draft_id=configuration.id, version=configuration.version, data=proposed))
    audit(db, p, 'setup.report_configuration.full_handoff', saved.id,
        {'request_id': str(body.request_id), 'record_hash': record['record_hash'], 'accepted_revision': configuration.version})
    db.commit(); return reply(db, p, saved)


@router.get('/report-evidence-readings/{identity}/full-configuration-receipts/{request_id}')
def receipt_route(identity: UUID, request_id: UUID, p: pipeline.ReadP, db: pipeline.DB):
    row = old().request_row(db, p, request_id)
    if row is None or row.kind != KIND or row.actor_id != p.user_id or row.report_id != str(identity):
        fail('receipt_not_found', '尚未找到本人原加入配置回执', 404)
    return reply(db, p, row)


@router.get('/full-semantic-handoffs/{identity}')
def shared_route(identity: UUID, p: pipeline.ReadP, db: pipeline.DB):
    row = db.scalar(select(Record).where(Record.id == str(identity), Record.workspace_id == p.workspace_id, Record.kind == KIND))
    if row is None:
        fail('not_found', '未找到此工作区已共享的完整设计', 404)
    return {'schema': 'report-full-semantic-shared-view-v1', 'record': copy.deepcopy(checked_ledger(row, p)['record']),
        'source_status': 'unconfirmed', 'execution_supported': False}
