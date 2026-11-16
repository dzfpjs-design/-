"""Explicit whole-design replacement; immutable ancestry and current heads.

This protocol never edits a private review, an old ledger, release or task.
The result remains pending formal source review and unsupported execution.
"""
import copy
from typing import Literal
from uuid import UUID, uuid5

from fastapi import APIRouter
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from . import report_evidence_pipeline as pipeline
from . import report_full_semantic_handoff as full
from .contracts import digest
from .models import SetupRevision, uid
from .report_configuration_models import ReportConfigurationRecord as Record
from .services import audit, require

router = APIRouter()
KIND = 'full_replace'
COMMAND = 'report-full-semantic-replace-command-v1'
PREVIEW = 'report-full-semantic-replacement-preview-v1'
RECORD = 'report-full-semantic-replacement-v1'
RECEIPT = 'report-full-semantic-replacement-receipt-v1'
LEDGER = 'report-full-semantic-replacement-ledger-v1'
TARGET_KEYS = ('target_handoff_id', 'target_record_hash', 'target_design_hash')


class PreviewCommand(full.PreviewCommand):
    schema: Literal['report-full-semantic-replace-command-v1']
    target_handoff_id: UUID
    target_record_hash: str = Field(strict=True, pattern=full.HASH)
    target_design_hash: str = Field(strict=True, pattern=full.HASH)


class ReplacementCommand(PreviewCommand):
    preview_hash: str = Field(strict=True, pattern=full.HASH)
    consent: Literal[True]

    @field_validator('consent', mode='before')
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError('请明确同意用完整新设计替换当前共享版本')
        return value


def replacement():
    from . import report_full_semantic_replacement_v1
    return report_full_semantic_replacement_v1


def command_hash(p, identity, body):
    return digest({'schema': 'report-full-semantic-replacement-command-hash-v1',
        'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'report_id': str(identity),
        'body': body.model_dump(mode='json')})


def handoff_id(p, identity, request_id):
    return str(uuid5(UUID(str(identity)), 'full-semantic-replacement-v1:' + p.workspace_id + ':' + p.user_id + ':' + str(request_id)))


def preview_body(body):
    value = body.model_dump(mode='json')
    return PreviewCommand.model_validate({key: value[key] for key in PreviewCommand.model_fields})


def reset_settings(config, previous, design):
    old_names = {b['name'] for b in previous['business_bindings']}
    new_names = {b['name'] for b in design['business_bindings']}
    group, removed = old_names | new_names, old_names ^ new_names
    flows = [config['flows'][name] for name in old_names]
    policies = config.get('automation', {}).get('policies', {})
    return {'step_tools': sum('execution' in step or 'planning_tools' in step for flow in flows for step in flow['steps']),
        'automation_businesses': [name for name in policies if name in group],
        'removed_resource_links': sum(name in removed for resource in config.get('resources', []) for name in resource.get('businesses', [])),
        'removed_notes': sum(name in removed for name in config.get('setupNotes', {})),
        'removed_gaps': sum(item.get('business') in removed for item in config.get('gaps', [])),
        'aliases_removed': sum(name in group or target in group for name, target in config.get('businessAliases', {}).items())}


def proposed_value(config, previous, design, materials):
    out = replacement().apply_replacement(config, previous, design)
    for material in materials:
        existing = next((m for m in out.setdefault('materials', []) if m.get('id') == material['id']), None)
        if existing is not None and existing.get('hash') != material['hash']:
            full.fail('full_semantic_material_conflict', '工作区资料与本稿来源不一致，原配置保留')
        if existing is None:
            out['materials'].append({**copy.deepcopy(material), 'archived': False})
    from .setup_automation import synchronize_reviews, metadata
    out = synchronize_reviews(config, out)
    # All replacement business automation must be deliberately reviewed again.
    for binding in design['business_bindings']:
        name = binding['name']; policy = out['automation']['policies'][name]
        policy['flow_review'] = metadata(out, name, policy, 'needs_review')
    full.old().setup().validate_configuration(out)
    return out


def make_preview(p, identity, body, previous, design, materials, settings, conflicts):
    return full.seal({'schema': PREVIEW, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'report_id': str(identity), 'body': preview_body(body).model_dump(mode='json'),
        'previous': {'handoff_id': previous['id'], 'record_hash': previous['record_hash'], 'design': copy.deepcopy(previous['design'])},
        'design': copy.deepcopy(design), 'flows': full.pure().derive_flows(design), 'materials': copy.deepcopy(materials),
        'changes': replacement().replacement_changes(previous['design'], design), 'reset_settings': copy.deepcopy(settings),
        'conflicts': conflicts, 'source_status': 'unconfirmed', 'can_confirm': False, 'can_publish': False}, 'preview_hash')


def make_receipt(record):
    return full.seal({'schema': RECEIPT, **{key: record[key] for key in (
        'actor_id', 'workspace_id', 'report_id', 'request_id', 'command_hash', 'preview_hash',
        'accepted_revision', 'configuration_hash', 'record_hash', *TARGET_KEYS)},
        'handoff_id': record['id'], 'design_hash': record['design']['design_hash']}, 'receipt_hash')


def revision_value(db, p, record, version):
    row = db.scalar(select(SetupRevision).where(SetupRevision.workspace_id == p.workspace_id,
        SetupRevision.draft_id == record['configuration_id'], SetupRevision.version == version))
    if row is None:
        full.fail('full_semantic_record_integrity', '完整设计的配置历史缺失，原记录保留')
    return row.data


def checked_ledger(db, row, p, previous, *, private=False, before=None, after=None):
    try:
        data = row.data
        if (row.workspace_id != p.workspace_id or row.kind != KIND or private and row.actor_id != p.user_id
                or set(data) != {'schema', 'record', 'command', 'receipt', 'ledger_hash'} or data['schema'] != LEDGER
                or data['ledger_hash'] != digest({k: v for k, v in data.items() if k != 'ledger_hash'})):
            raise ValueError('ledger')
        record = data['record']
        keys = ('schema id actor_id workspace_id report_id configuration_id request_id command_hash preview_hash '
            'design materials accepted_revision configuration_hash record_hash changes reset_settings').split() + list(TARGET_KEYS)
        if set(record) != set(keys) or record['schema'] != RECORD:
            raise ValueError('record shape')
        if (record['record_hash'] != digest({k: v for k, v in record.items() if k != 'record_hash'})
                or any(record[k] != getattr(row, k) for k in ('id', 'actor_id', 'workspace_id', 'report_id', 'configuration_id', 'request_id'))):
            raise ValueError('record binding')
        command = ReplacementCommand.model_validate(data['command'])
        owner = type('Owner', (), {'user_id': row.actor_id, 'workspace_id': row.workspace_id})()
        if (str(command.request_id) != row.request_id or record['command_hash'] != command_hash(owner, row.report_id, command)
                or record['preview_hash'] != command.preview_hash or record['accepted_revision'] != command.configuration_revision + 1
                or row.id != handoff_id(owner, row.report_id, command.request_id)
                or row.parent_id != previous['id'] or str(command.target_handoff_id) != previous['id']
                or any(record[k] != getattr(command, k) for k in ('target_record_hash', 'target_design_hash'))
                or record['target_handoff_id'] != previous['id']
                or command.target_record_hash != previous['record_hash'] or command.target_design_hash != previous['design']['design_hash']
                or any(record[k] != previous[k] for k in ('actor_id', 'workspace_id', 'report_id', 'configuration_id'))):
            raise ValueError('parent or command')
        full.pure().check_design(record['design'], **full.context(command, row.report_id, row.id))
        if (type(record['materials']) is not list or len(record['materials']) != 2
                or any(type(m) is not dict or set(m) != {'id', 'name', 'version', 'hash', 'evidence_id'} for m in record['materials'])):
            raise ValueError('materials')
        prior_config = revision_value(db, p, record, command.configuration_revision) if before is None else before
        accepted_config = revision_value(db, p, record, record['accepted_revision']) if after is None else after
        if digest(prior_config) != command.configuration_hash:
            raise ValueError('prior configuration')
        expected_config = proposed_value(prior_config, previous['design'], record['design'], record['materials'])
        settings = reset_settings(prior_config, previous['design'], record['design'])
        preview = make_preview(owner, row.report_id, command, previous, record['design'], record['materials'], settings, [])
        if (record['changes'] != preview['changes'] or not record['changes']['changed_sections']
                or record['reset_settings'] != settings or preview['preview_hash'] != command.preview_hash
                or accepted_config != expected_config or digest(expected_config) != record['configuration_hash']
                or data['receipt'] != make_receipt(record)):
            raise ValueError('projection or receipt')
        full.seal({k: v for k, v in data.items() if k != 'ledger_hash'}, 'ledger_hash')
        return data
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        full.fail('full_semantic_record_integrity', '完整设计版本记录无法核对；原记录保留')


def record_set(db, p, *, include_configuration=False):
    """Read command/audit inventory and configuration in one SQL snapshot.

    Historical records remain immutable, but another valid writer can append
    between READ COMMITTED statements. Separate inventory reads would mistake
    that valid append for corruption. Full joins retain empty configurations
    and orphan audit/record rows so neither can silently become an empty set.
    The returned configuration is a read snapshot, never a writable ORM row.
    """
    from . import report_object_adoption_v169 as objects
    from .models import Audit, SetupDraft
    from sqlalchemy import Text, and_, cast, outerjoin, true
    from types import SimpleNamespace
    from .typed_payload_codec_v131 import decode_payload
    rt, at, ct = Record.__table__, Audit.__table__, SetupDraft.__table__
    records_query = select(rt).where(rt.c.workspace_id == p.workspace_id,
        rt.c.kind.in_((full.KIND, KIND, objects.KIND))).subquery()
    audits_query = select(at).where(at.c.workspace_id == p.workspace_id,
        at.c.action == objects.ACTION).subquery()
    config_query = select(ct).where(ct.c.workspace_id == p.workspace_id).subquery()
    saved, audited, config = records_query.c, audits_query.c, config_query.c
    inventory = outerjoin(records_query, audits_query, and_(saved.kind == objects.KIND,
        saved.id == audited.object_id, saved.workspace_id == audited.workspace_id), full=True)
    record_fields = ('id', 'workspace_id', 'actor_id', 'report_id', 'configuration_id',
        'request_id', 'parent_id', 'kind')
    # OUTER JOIN may synthesize absent rows. The strict payload decoder must
    # only receive real rows and must keep every actual owner/format column.
    # Select raw TEXT projections plus their canonical envelopes, then use the
    # existing exact decoder after distinguishing absent row IDs. No global
    # nullable-payload exception or JSONB-to-Python coercion is introduced.
    values = db.execute(select(*(getattr(saved, field) for field in record_fields),
        cast(saved.data, Text), saved._ga_data_format, saved._ga_data_canonical,
        audited.id, audited.object_id, config.id, config.workspace_id, config.version,
        cast(config.data, Text), config._ga_data_format, config._ga_data_canonical).select_from(
            outerjoin(inventory, config_query, true(), full=True)).order_by(
                saved.created_at, saved.id, audited.id)).all()
    rows_by_id = {}; new_audits = []; configuration = None
    def payload(table, owner_id, workspace_id, projection, marker, canonical):
        return decode_payload(canonical, marker, {'table': table, 'field': 'data',
            'owner_id': owner_id, 'workspace_id': workspace_id}, projection)
    for value in values:
        fields = dict(zip(record_fields, value[:8]))
        if fields['id'] is not None:
            fields['data'] = payload(Record.__tablename__, fields['id'], fields['workspace_id'], *value[8:11])
            rows_by_id[fields['id']] = SimpleNamespace(**fields)
        if value[11] is not None:
            new_audits.append(SimpleNamespace(id=value[11], object_id=value[12]))
        config_id, config_workspace, config_version, projection, marker, canonical = value[13:19]
        if config_id is not None and configuration is None:
            configuration = SimpleNamespace(id=config_id, version=config_version,
                data=payload(SetupDraft.__tablename__, config_id, config_workspace, projection, marker, canonical))
    rows = list(rows_by_id.values())
    # New adoption commands and audits must exist in both directions, even if
    # a damaged record would otherwise disappear from the selected ancestry.
    new_rows = [row for row in rows if row.kind == objects.KIND]
    if (len(new_rows) != len(new_audits) or {row.id for row in new_rows} != {row.object_id for row in new_audits}
            or len({row.object_id for row in new_audits}) != len(new_audits)):
        full.fail('object_adoption_integrity', '新采用命令与审计记录不完整；已有配置保留')
    pending = {row.id: row for row in rows}; records = {}; children = {}
    for row in rows:
        if row.kind == full.KIND or row.kind == objects.KIND and row.parent_id is None:
            if row.parent_id is not None:
                full.fail('full_semantic_record_integrity', '完整设计根版本异常，原记录保留')
            records[row.id] = (objects.checked_ledger(db, row, p)['record'] if row.kind == objects.KIND
                else full.checked_ledger(row, p)['record']); pending.pop(row.id)
        else:
            if row.parent_id in children:
                full.fail('full_semantic_record_integrity', '同一完整设计出现多个后续版本，原记录保留')
            children[row.parent_id] = row.id
    while pending:
        ready = [row for row in pending.values() if row.parent_id in records]
        if not ready:
            full.fail('full_semantic_record_integrity', '完整设计历史缺失或存在循环，原记录保留')
        for row in ready:
            records[row.id] = (objects.checked_ledger(db, row, p, records[row.parent_id])['record']
                if row.kind == objects.KIND else checked_ledger(db, row, p, records[row.parent_id])['record'])
            pending.pop(row.id)
    heads = {identity: record for identity, record in records.items() if identity not in children}
    if include_configuration:
        if configuration is None:
            full.fail('configuration_missing', '请先保存当前工作区配置')
        return records, heads, children, configuration
    return records, heads, children


def active_records(db, p):
    return list(record_set(db, p)[1].values())


def assert_active_configuration(config, heads):
    full.validate_configuration(config)
    designs = {d['handoff_id']: d for d in config.get('fullSemanticDesigns', [])}
    if designs != {identity: record['design'] for identity, record in heads.items()}:
        full.fail('full_semantic_active_version_changed', '配置必须保留完整设计的当前有效版本，不能恢复旧版本或删除来源')


def prepare(db, p, row, configuration, body, *, lock):
    full.old().configuration_cas(configuration, body)
    result = full.current_manual(row, p, body)
    records, heads, _ = record_set(db, p)
    previous = heads.get(str(body.target_handoff_id))
    if previous is None:
        full.fail('full_semantic_target_changed', '目标已被更新或不是当前共享版本，请保留人工稿并重新核对')
    if previous['actor_id'] != p.user_id or previous['report_id'] != row.id:
        full.fail('not_found', '未找到本人当前报告可更新的共享版本', 404)
    if (previous['configuration_id'] != configuration.id or previous['record_hash'] != body.target_record_hash
            or previous['design']['design_hash'] != body.target_design_hash):
        full.fail('full_semantic_target_changed', '目标设计或原版本回执已变化，原稿保留')
    assert_active_configuration(configuration.data, heads)
    try:
        design = full.pure().build_design(result, **full.context(body, row.id, handoff_id(p, row.id, body.request_id)))
        old_names = {b['name'] for b in previous['design']['business_bindings']}
        new_names = [b['name'] for b in design['business_bindings']]
        outside = (set(full.old().names(configuration.data)) | set(configuration.data.get('flows', {}))) - old_names
        conflicts = [name for name in new_names if name in outside]
        if len(set(full.old().names(configuration.data)) - old_names) + len(new_names) > 20:
            full.fail('full_semantic_business_capacity', '更新后超过20项业务；原配置保留', 422)
        # Exercise strict operational field types even for a conflict preview.
        if not conflicts:
            replacement().apply_replacement(configuration.data, previous['design'], design)
        settings = reset_settings(configuration.data, previous['design'], design)
    except (ValueError, TypeError, KeyError):
        full.fail('full_semantic_mapping_invalid', '完整结构、业务关联或字段格式无法更新，原人工稿及配置保留', 422)
    return make_preview(p, row.id, body, previous, design, full.material_refs(db, p, row, lock=lock), settings, conflicts)


def reply(db, p, row):
    if row.actor_id != p.user_id:
        full.fail('not_found', '未找到本人更新回执', 404)
    records, heads, _ = record_set(db, p); record = records[row.id]
    configuration = full.old().configuration_row(db, p)
    receipt = row.data['receipt']
    return {'schema': 'report-full-semantic-replacement-reply-v1', 'record': copy.deepcopy(record),
        'receipt': copy.deepcopy(receipt), 'configuration': full.old().setup().draft_view(configuration),
        'current_matches': configuration.version == receipt['accepted_revision'] and digest(configuration.data) == receipt['configuration_hash'],
        'is_active': row.id in heads}


@router.post('/report-evidence-readings/{identity}/full-configuration-replacement-preview')
def preview_route(identity: UUID, body: PreviewCommand, p: pipeline.P, db: pipeline.DB):
    require(p, 'admin', 'operator')
    from .report_object_revision_store_v166 import reject_legacy_adoption
    reject_legacy_adoption(db, p, str(identity))
    return prepare(db, p, pipeline.owned(db, p, identity), full.old().configuration_row(db, p), body, lock=False)


@router.post('/report-evidence-readings/{identity}/full-configuration-replacement')
def submit_route(identity: UUID, body: ReplacementCommand, p: pipeline.P, db: pipeline.DB):
    require(p, 'admin', 'operator'); full.old().nonce_lock(db, p, body.request_id)
    prior = full.old().request_row(db, p, body.request_id)
    if prior is not None:
        if (prior.kind != KIND or prior.actor_id != p.user_id or prior.report_id != str(identity)
                or prior.data.get('record', {}).get('command_hash') != command_hash(p, identity, body)):
            full.fail('full_semantic_nonce_conflict', '原更新编号不能更换操作、身份或正文，原回执保留')
        return reply(db, p, prior)
    from .report_object_revision_store_v166 import reject_legacy_adoption
    reject_legacy_adoption(db, p, str(identity))
    row = pipeline.owned(db, p, identity, True); config = full.old().configuration_row(db, p, True)
    preview = prepare(db, p, row, config, body, lock=True)
    if preview['preview_hash'] != body.preview_hash:
        full.fail('full_semantic_preview_changed', '完整新旧版本预览已变化，请重新核对')
    if preview['conflicts']:
        full.fail('full_semantic_business_conflict', '新设计与其他业务同名，本次不覆盖已有业务')
    if not preview['changes']['changed_sections']:
        full.fail('full_semantic_no_content_change', '共享设计内容未变化；私人说明仍保留，不创建重复版本')
    records, _, _ = record_set(db, p); previous = records[str(body.target_handoff_id)]
    proposed = proposed_value(config.data, previous['design'], preview['design'], preview['materials'])
    full.old().setup().automation_review_actor(db, p)
    record = full.seal({'schema': RECORD, 'id': preview['design']['handoff_id'], 'actor_id': p.user_id,
        'workspace_id': p.workspace_id, 'report_id': str(identity), 'configuration_id': config.id,
        'request_id': str(body.request_id), 'command_hash': command_hash(p, identity, body), 'preview_hash': body.preview_hash,
        'design': preview['design'], 'materials': preview['materials'], 'changes': preview['changes'],
        'reset_settings': preview['reset_settings'], 'target_handoff_id': previous['id'],
        'target_record_hash': previous['record_hash'], 'target_design_hash': previous['design']['design_hash'],
        'accepted_revision': config.version + 1, 'configuration_hash': digest(proposed)}, 'record_hash')
    receipt = make_receipt(record)
    data = full.seal({'schema': LEDGER, 'record': record, 'command': body.model_dump(mode='json'), 'receipt': receipt}, 'ledger_hash')
    saved = Record(id=record['id'], workspace_id=p.workspace_id, actor_id=p.user_id, report_id=str(identity),
        configuration_id=config.id, request_id=str(body.request_id), kind=KIND, parent_id=previous['id'], data=data)
    checked_ledger(db, saved, p, previous, private=True, before=config.data, after=proposed)
    db.execute(insert(SetupRevision).values(id=uid(), workspace_id=p.workspace_id, draft_id=config.id,
        version=config.version, data=config.data).on_conflict_do_nothing(index_elements=['draft_id', 'version']))
    db.add(saved); config.data = proposed; config.version += 1
    db.add(SetupRevision(workspace_id=p.workspace_id, draft_id=config.id, version=config.version, data=proposed))
    audit(db, p, 'setup.report_configuration.full_replace', saved.id, {'request_id': str(body.request_id),
        'target_handoff_id': previous['id'], 'record_hash': record['record_hash'], 'accepted_revision': config.version})
    db.commit(); return reply(db, p, saved)


@router.get('/report-evidence-readings/{identity}/full-configuration-replacement-receipts/{request_id}')
def receipt_route(identity: UUID, request_id: UUID, p: pipeline.ReadP, db: pipeline.DB):
    row = full.old().request_row(db, p, request_id)
    if row is None or row.kind != KIND or row.actor_id != p.user_id or row.report_id != str(identity):
        full.fail('receipt_not_found', '尚未找到本人原更新回执', 404)
    return reply(db, p, row)


@router.get('/full-semantic-design-versions/{identity}')
def version_route(identity: UUID, p: pipeline.ReadP, db: pipeline.DB):
    records, heads, children = record_set(db, p); selected = records.get(str(identity))
    if selected is None:
        full.fail('not_found', '未找到此工作区共享设计版本', 404)
    head = selected['id']
    while head in children:
        head = children[head]
    ancestry = []; current = records[head]
    while True:
        ancestry.append(current)
        if current['schema'] == full.RECORD:
            break
        current = records[current['target_handoff_id']]
    history = [{'handoff_id': r['id'], 'record_hash': r['record_hash'], 'design_hash': r['design']['design_hash'],
        'report_revision': r['design']['report_revision'], 'accepted_revision': r['accepted_revision'], 'actor_id': r['actor_id']}
        for r in reversed(ancestry)]
    return full.seal({'schema': 'report-full-semantic-version-view-v1', 'record': copy.deepcopy(selected),
        'active_handoff_id': head, 'is_active': selected['id'] in heads,
        'can_replace': selected['id'] in heads and selected['actor_id'] == p.user_id and p.role in ('admin', 'operator'),
        'history': history, 'source_status': 'unconfirmed', 'execution_supported': False}, 'view_hash')
