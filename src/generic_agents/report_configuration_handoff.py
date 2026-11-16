"""A consented projection into an unconfirmed configuration, never a paid apply."""
import copy
import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID, uuid5

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from . import report_reading_drafts as rd
from . import report_draft_partial_review as partial
from .contracts import digest
from .models import SetupDraft, SetupRevision, SetupMaterial, CloudCall, uid
from .report_configuration_models import ReportConfigurationRecord as Record
from .services import scoped, require, audit
from .material_ocr_read_session import read_session

router = APIRouter()
RegionReadDB = Annotated[object, Depends(read_session)]
HANDOFF = 'report-configuration-handoff-v1'
SOURCE = 'report-configuration-source-review-v1'
RECEIPT = 'report-configuration-receipt-v1'
STEP_FACTS = ('id', 'name', 'purpose', 'agent', 'role', 'output', 'condition', 'nextId', 'elseId')
MAX_RECORD_BYTES = 2 * 1024 * 1024


def fail(code, message, status=409, **extra):
    raise HTTPException(status, {'code': code, 'message': message, **extra})


class PreviewCommand(rd.SafeContract):
    report_revision: int = Field(ge=1, strict=True)
    review_receipt_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    candidate_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    source_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    validation_report_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    configuration_revision: int = Field(ge=1, strict=True)
    configuration_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    business_indexes: list[int] = Field(min_length=1, max_length=20)

    @field_validator('business_indexes', mode='before')
    @classmethod
    def strict_indexes(cls, value):
        if not isinstance(value, list) or any(type(i) is not int or not 0 <= i < 20 for i in value) or len(set(value)) != len(value):
            raise ValueError('业务索引须为不重复的整数')
        return value


class HandoffCommand(PreviewCommand):
    request_id: UUID
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    consent: Literal[True]

    @field_validator('consent', mode='before')
    @classmethod
    def explicit_consent(cls, value):
        if value is not True: raise ValueError('请明确同意向当前工作区共享所选业务及出处')
        return value


class SourceReviewCommand(rd.SafeContract):
    request_id: UUID
    configuration_revision: int = Field(ge=1, strict=True)
    configuration_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    candidate_index: int = Field(ge=0, lt=20, strict=True)
    fact_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    material_refs: list[rd.MaterialRef] = Field(min_length=1, max_length=50)
    consent: Literal[True]

    @field_validator('consent', mode='before')
    @classmethod
    def explicit_consent(cls, value):
        return HandoffCommand.explicit_consent(value)

    @model_validator(mode='after')
    def distinct(self):
        if len({r.id for r in self.material_refs}) != len(self.material_refs): raise ValueError('资料不可重复')
        return self


from .report_source_projection import ProjectionRef


class ProjectedSourceReviewCommand(SourceReviewCommand):
    review_mode: Literal['confirmed_final_view_v1']
    projection_refs: list[ProjectionRef] = Field(min_length=1, max_length=50)
    final_view_confirmed: Literal[True]

    @field_validator('final_view_confirmed', mode='before')
    @classmethod
    def explicit_final_view(cls, value):
        if value is not True: raise ValueError('请明确核对原Word与最终修订副本及当前流程依据')
        return value

    @model_validator(mode='after')
    def unique_projections(self):
        originals = [ref.source_material_id for ref in self.projection_refs]
        copies = [ref.copy_ref.id for ref in self.projection_refs]
        if (len(set(originals)) != len(originals) or len(set(copies)) != len(copies)
                or any(ref.source_material_id == ref.copy_ref.id for ref in self.projection_refs)):
            raise ValueError('每份原件须选择独立且唯一的最终修订副本')
        return self


from .report_region_projection import BindingDraftRef
from .report_source_region_binding import RegionBinding


class MultiRegionSourceReviewCommand(SourceReviewCommand):
    """A sibling contract: no new defaults enter the historical v1 command."""
    review_mode: Literal['confirmed_final_view_regions_v2']
    projection_refs: list[ProjectionRef] = Field(min_length=1, max_length=50)
    final_view_confirmed: Literal[True]
    quote_binding_draft_ref: BindingDraftRef
    region_bindings: list[RegionBinding] = Field(min_length=1, max_length=1000)

    @field_validator('final_view_confirmed', mode='before')
    @classmethod
    def explicit_final_view(cls, value):
        return ProjectedSourceReviewCommand.explicit_final_view(value)

    @model_validator(mode='after')
    def unique_projections(self):
        return ProjectedSourceReviewCommand.unique_projections(self)


def projection_verifier(projection):
    """Strict dispatch; damaged or unknown v2 never falls back to single blocks."""
    from . import report_source_projection as v1
    from . import report_region_projection as v2
    if isinstance(projection, dict):
        if projection.get('schema') == v1.SCHEMA and projection.get('review_mode') == v1.MODE:
            return v1
        if projection.get('schema') == v2.SCHEMA and projection.get('review_mode') == v2.MODE:
            return v2
    fail('source_record_integrity', '来源投影版本无法核对；原记录保留')


def setup():
    from . import setup_api
    return setup_api


def names(config):
    return [item if isinstance(item, str) else item['name'] for item in config.get('businesses', [])]


def resolve_flow(config, name):
    seen = set()
    while name and name not in seen:
        seen.add(name)
        if name in config.get('flows', {}): return config['flows'][name]
        name = config.get('businessAliases', {}).get(name)
    return {}


def fact_hash(name, flow):
    return digest({'name': name, 'trigger': flow.get('trigger', ''), 'output': flow.get('output', ''),
                   'steps': [{key: step.get(key, '') for key in STEP_FACTS} for step in flow.get('steps', [])]})


def basis_hash(flow):
    keys = ('sourceId', 'citation', 'citations', 'inferenceReason')
    return digest({'business': {key: flow.get(key) for key in keys},
                   'steps': [{key: step.get(key) for key in ('id', *keys)} for step in flow.get('steps', [])]})


def public_record(value):
    return {**value, 'record_hash': digest(value)}


def checked_public(record):
    if (not isinstance(record, dict) or record.get('schema') not in {HANDOFF, SOURCE}
            or record.get('record_hash') != digest({k: v for k, v in record.items() if k != 'record_hash'})):
        fail('source_record_integrity', '交接或来源记录无法核对；原记录保留')
    return record


def command_hash(p, kind, identity, body):
    return digest({'kind': kind, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
                   'report_id' if kind == 'handoff' else 'handoff_id': identity,
                   'body': body.model_dump(mode='json')})


def ledger_data(record, command, receipt):
    value = {'record': record, 'command': command.model_dump(mode='json'), 'receipt': receipt}
    if not rd.jsonb_compatible(value) or len(json.dumps(value, ensure_ascii=False).encode()) > MAX_RECORD_BYTES:
        fail('handoff_capacity', '交接记录超过保存上限；原稿及配置未修改', 413)
    return {**value, 'data_hash': digest(value)}


def checked_ledger(row, p, *, private=False):
    try:
        data = row.data; record = checked_public(data['record']); receipt = data['receipt']
        if (row.workspace_id != p.workspace_id or private and row.actor_id != p.user_id
                or data.get('data_hash') != digest({k: v for k, v in data.items() if k != 'data_hash'})
                or record['id'] != row.id or record['actor_id'] != row.actor_id
                or record['workspace_id'] != row.workspace_id or record['report_id'] != row.report_id
                or record['configuration_id'] != row.configuration_id or record['request_id'] != row.request_id
                or record['schema'] != (HANDOFF if row.kind == 'handoff' else SOURCE)
                or row.kind not in {'handoff', 'source_review'}
                or receipt['record_hash'] != record['record_hash'] or receipt['command_hash'] != record['command_hash']
                or receipt['receipt_hash'] != digest({k: v for k, v in receipt.items() if k != 'receipt_hash'})
                or any(receipt[key] != record[key] for key in ('actor_id', 'workspace_id', 'report_id', 'request_id', 'accepted_revision', 'configuration_hash'))):
            raise ValueError()
        principal = type('Owner', (), {'user_id': row.actor_id, 'workspace_id': row.workspace_id})()
        command_type = HandoffCommand if row.kind == 'handoff' else SourceReviewCommand
        if row.kind == 'source_review' and 'projection' in record:
            projection = projection_verifier(record['projection'])
            command_type = MultiRegionSourceReviewCommand if projection.MODE == 'confirmed_final_view_regions_v2' else ProjectedSourceReviewCommand
        command = command_type.model_validate(data['command'])
        identity = row.report_id if row.kind == 'handoff' else row.parent_id
        if (command_hash(principal, row.kind, identity, command) != record['command_hash']
                or str(command.request_id) != row.request_id or receipt['kind'] != row.kind
                or receipt['schema'] != RECEIPT or receipt['handoff_id'] != (row.id if row.kind == 'handoff' else row.parent_id)
                or record['accepted_revision'] != command.configuration_revision + 1):
            raise ValueError()
        if row.kind == 'handoff':
            selected = record['selection']
            if ([i['candidate_index'] for i in selected] != command.business_indexes
                    or any(record[k] != getattr(command, k) for k in ('preview_hash', 'source_hash', 'candidate_hash'))
                    or receipt['preview_hash'] != command.preview_hash
                    or any(i['flow']['reportSource'] != {'handoff_id': row.id, 'candidate_index': i['candidate_index']} for i in selected)):
                raise ValueError()
        elif (record['handoff_id'] != row.parent_id or record['candidate_index'] != command.candidate_index
                or record['fact_hash'] != command.fact_hash or record['material_refs'] != [r.model_dump(mode='json') for r in command.material_refs]
                or receipt.get('source_review_id') != row.id or receipt['preview_hash'] is not None):
            raise ValueError()
        if row.kind == 'source_review' and 'projection' in record:
            if not projection.validate_projection_command(record, command):
                raise ValueError()
        return data
    except (KeyError, TypeError, AttributeError, ValueError, RecursionError):
        fail('source_record_integrity', '交接或来源记录无法核对；原记录保留')


def replay_command(row, p, kind, identity, body):
    if row.actor_id != p.user_id or row.workspace_id != p.workspace_id:
        fail('request_conflict', '此编号已用于另一操作或范围')
    data = checked_ledger(row, p, private=True)
    if row.kind != kind or data['record']['command_hash'] != command_hash(p, kind, identity, body):
        fail('request_conflict', '同一编号不能替换原交接范围或内容')
    return copy.deepcopy(data['receipt'])


def configuration_cas(configuration, body):
    if configuration.version != body.configuration_revision or digest(configuration.data) != body.configuration_hash:
        fail('configuration_conflict', '工作区配置已变化，请保留选择并重新预览')


def saved_value(report, p, body):
    rd.check_row(report, p)
    if report.data.get('schema') == 'report-evidence-draft-state-v1':
        fail('full_semantics_mapping_required', '本稿含分段来源、全局声明与协同关系，当前正式交接尚未提供完整映射；私人候选已保留，不会按旧格式丢弃字段')
    if report.status != 'partial_review' or report.data.get('review_policy') != partial.POLICY:
        fail('saved_review_required', '请先明确复核并保存本人完整人工稿')
    value = partial.current_value(report)
    entries = report.data.get('review_history', []) + report.data.get('local_review_receipts', [])
    current = [entry['receipt'] for entry in entries if entry['receipt'].get('accepted_revision') == report.version]
    if (report.version != body.report_revision or len(current) != 1 or current[0]['receipt_hash'] != body.review_receipt_hash
            or digest(value['result']) != body.candidate_hash or report.data['preview']['source_hash'] != body.source_hash
            or report.data['validation_report']['report_hash'] != body.validation_report_hash):
        fail('saved_review_conflict', '已保存人工稿、来源或回执已变化；未交接')
    return value


def source_refs(business, original, materials):
    sources = {m['id']: m for m in materials}; refs = []
    for scope, candidate in (('current', business), ('original', original)):
        for path, item in [('/business', candidate), *[('/steps/' + str(i), s) for i, s in enumerate(candidate['steps'])]]:
            for i, cite in enumerate(item['citations']):
                material = sources.get(cite['material_id'])
                block = next((b for b in material['reading']['blocks'] if b['id'] == cite['block_id']), None) if material else None
                if not block or cite['quote'] not in rd.old().scrub(block['text']):
                    fail('candidate_source_invalid', '选中业务引用未匹配已授权正文；原稿保留')
                refs.append({'scope': scope, 'path': path + '/citations/' + str(i), 'material_id': material['id'],
                    'version': material['version'], 'hash': material['hash'], 'evidence_id': material['evidence_id'],
                    'reading_hash': digest(material['reading']), 'block_id': block['id'],
                    'location': copy.deepcopy(block['location']), 'quote': cite['quote']})
    return refs


def preview_value(report, p, configuration, body):
    if report.data.get('schema') == 'report-evidence-draft-state-v1':
        fail('full_semantics_mapping_required', '本稿含分段来源、全局声明与协同关系，当前正式交接尚未提供完整映射；私人候选已保留，不会按旧格式丢弃字段')
    if report.data.get('schema') == 'report-full-reading-draft-state-v1':
        fail('full_semantics_mapping_required', '本稿含全文来源、职责与协同关系，当前正式交接尚未提供完整映射；私人候选已保留，不会按旧格式丢弃字段')
    configuration_cas(configuration, body); value = saved_value(report, p, body)
    original = partial.checked_raw(report)['value']; materials = report.data['preview']['source_snapshot']['materials']
    selected = []; conflicts = []
    for index in body.business_indexes:
        if index >= len(value['result']['businesses']) or index >= len(original['businesses']):
            fail('selection_invalid', '请选择已保存且实际存在的业务', 422)
        business = copy.deepcopy(value['result']['businesses'][index]); old = copy.deepcopy(original['businesses'][index])
        if business['operation'] != 'upsert' or business['original_name']:
            fail('selection_invalid', '本次交接仅新增业务，不修改或删除原配置', 422)
        if business['name'] in names(configuration.data) or business['name'] in configuration.data.get('flows', {}):
            conflicts.append(business['name'])
        modified = [key for key in ('name', 'trigger', 'output', 'steps') if business.get(key) != old.get(key)]
        selected.append({'candidate_index': index, 'business': business, 'original_business': old,
            'modified_paths': modified, 'source_refs': source_refs(business, old, materials), 'control_flow_unverified': True})
    if len({s['business']['name'] for s in selected}) != len(selected): fail('selection_invalid', '所选业务名称重复', 422)
    result = {'schema': 'report-configuration-preview-v1', 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'report_id': report.id, 'body': PreviewCommand.model_validate({k: v for k, v in body.model_dump(mode='json').items()
            if k in PreviewCommand.model_fields}).model_dump(mode='json'), 'raw_hash': report.data['raw_response']['hash'],
        'review_value_hash': digest(value), 'selection': selected, 'conflicts': conflicts}
    return {**result, 'preview_hash': digest(result)}


def convert_business(business, handoff_id, index):
    identifiers = {s['key']: str(uuid5(UUID(handoff_id), str(index) + ':' + s['key'])) for s in business['steps']}
    steps = []
    for step in business['steps']:
        citations = copy.deepcopy(step['citations']); first = citations[0] if citations else None
        steps.append({'id': identifiers[step['key']], **{key: step[key] for key in ('name', 'purpose', 'agent', 'role', 'output')},
            'condition': step.get('condition', ''), 'nextId': identifiers.get(step.get('next_key', 'auto'), step.get('next_key', 'auto')),
            'elseId': identifiers.get(step.get('else_key', 'end'), step.get('else_key', 'end')),
            'sourceId': first['material_id'] if first else '', 'citation': '；'.join(c['block_id'] + '：' + c['quote'] for c in citations),
            'citations': citations, 'inferenceReason': step['inference_reason']})
    return {'trigger': business['trigger'], 'output': business['output'], 'steps': steps,
        'citations': copy.deepcopy(business['citations']), 'inferenceReason': business['inference_reason'],
        'confirmedAt': None, 'confirmedSignature': None,
        'reportSource': {'handoff_id': handoff_id, 'candidate_index': index}}


def make_receipt(p, kind, record, preview_hash, handoff_id):
    value = {'schema': RECEIPT, 'kind': kind, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'report_id': record['report_id'], 'handoff_id': handoff_id, 'request_id': record['request_id'],
        'command_hash': record['command_hash'], 'preview_hash': preview_hash,
        'accepted_revision': record['accepted_revision'], 'configuration_hash': record['configuration_hash'],
        'record_hash': record['record_hash']}
    if kind == 'source_review': value['source_review_id'] = record['id']
    return {**value, 'receipt_hash': digest(value)}


def handoff_value(report, p, configuration, body, record_id):
    preview = preview_value(report, p, configuration, body)
    if preview['preview_hash'] != body.preview_hash: fail('preview_changed', '交接预览已变化，请重新核对')
    if preview['conflicts']: fail('business_conflict', '同名业务已有流程；不会覆盖人工配置', names=preview['conflicts'])
    proposed = copy.deepcopy(configuration.data); selection = copy.deepcopy(preview['selection'])
    from .flow_confirmation import synchronize_flow_basis
    for item in selection:
        flow = convert_business(item['business'], record_id, item['candidate_index'])
        old = convert_business(item['original_business'], record_id, item['candidate_index'])
        # Human edits retire copied evidence before entering the shared workflow.
        flow = synchronize_flow_basis(old, flow)
        item['flow'] = copy.deepcopy(flow)
        proposed.setdefault('businesses', []).append(item['business']['name'])
        proposed.setdefault('flows', {})[item['business']['name']] = flow
    source = report.data['preview']['source_snapshot']['materials']
    materials = [{key: copy.deepcopy(m[key]) for key in ('id', 'name', 'version', 'hash', 'evidence_id')} for m in source]
    for m in materials:
        if not any(row.get('id') == m['id'] for row in proposed.setdefault('materials', [])):
            proposed['materials'].append({**m, 'archived': False})
    setup().validate_configuration(proposed)
    record = public_record({'schema': HANDOFF, 'id': record_id, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'report_id': report.id, 'configuration_id': configuration.id, 'request_id': str(body.request_id),
        'command_hash': command_hash(p, 'handoff', report.id, body), 'preview_hash': body.preview_hash,
        'raw_hash': preview['raw_hash'], 'source_hash': body.source_hash, 'candidate_hash': body.candidate_hash,
        'review_value_hash': preview['review_value_hash'], 'selection': selection, 'materials': materials,
        'accepted_revision': configuration.version + 1, 'configuration_hash': digest(proposed)})
    receipt = make_receipt(p, 'handoff', record, body.preview_hash, record_id)
    ledger_data(record, body, receipt)
    return record, proposed, receipt


def exact_mappings(record, materials):
    mappings = []
    for item in record['selection']:
        for ref in item['source_refs']:
            material = materials.get(ref['material_id'])
            if material is None or material.sha256 != ref['hash'] or material.evidence_id != ref['evidence_id']:
                fail('source_changed', '原件摘要或证据已变化；未签发来源复核')
            matches = [block for block in material.data.get('blocks', []) if ref['quote'] in block.get('text', '')]
            if len(matches) != 1:
                fail('source_quote_ambiguous' if matches else 'source_quote_missing',
                     '正文原引用在正式解析块中不唯一或未逐字匹配，请核对原文；草稿保留', path=ref['path'])
            mappings.append({'candidate_index': item['candidate_index'], 'scope': ref['scope'], 'path': ref['path'],
                'material_id': material.id, 'source_block_id': ref['block_id'], 'quote': ref['quote'],
                'target_block_id': matches[0]['id']})
    return mappings


def all_records(db, p):
    # These explicit full-design families have their own closed ledger verifier.
    # protect_configuration_sources always continues through that verifier;
    # passing a V169 object command to checked_ledger here would reject even an
    # unrelated profile/automation save before the real full-design check runs.
    from .report_object_adoption_v169 import KIND as OBJECT_ADOPTION_KIND
    rows = list(db.scalars(select(Record).where(Record.workspace_id == p.workspace_id,
        Record.kind.notin_(('full_handoff', 'full_replace', OBJECT_ADOPTION_KIND)))))
    return [checked_ledger(row, p)['record'] for row in rows]


def protect_value(previous, proposed, handoffs, proofs, valid):
    """Preserve issued origins through rename/aliases, deletion, restore and edits."""
    out = copy.deepcopy(proposed); seen = set()
    def origins(config):
        values = [resolve_flow(config, name).get('reportSource') for name in names(config)]
        return {(v.get('handoff_id'), v.get('candidate_index')) for v in values if isinstance(v, dict)}
    removed = origins(previous) - origins(out)
    new_unmarked = [name for name in names(out) if name not in names(previous) and not resolve_flow(out, name).get('reportSource')]
    if removed and new_unmarked:
        fail('source_origin_replacement', '不能在同次保存删除报告来源并改成无来源新业务；请保留来源改名，或明确分开删除与独立新建')
    for name in names(out):
        flow = resolve_flow(out, name); marker = flow.get('reportSource')
        old_marker = resolve_flow(previous, name).get('reportSource')
        ids = {s.get('id') for s in flow.get('steps', [])}
        issued = [(record, item) for record in handoffs for item in record['selection'] if
            name == item['business']['name'] or ids.intersection(s['id'] for s in item['flow']['steps'])]
        if not marker:
            if issued or old_marker: fail('source_marker_missing', '已交接业务的来源不能删除或改成无来源配置', business=name)
            continue
        if (not isinstance(marker, dict) or set(marker) - {'handoff_id', 'candidate_index', 'source_review_id'}
                or type(marker.get('candidate_index')) is not int):
            fail('source_marker_invalid', '业务来源标识无法核对', business=name)
        record = next((r for r in handoffs if r['id'] == marker.get('handoff_id')), None)
        item = next((i for i in record['selection'] if i['candidate_index'] == marker['candidate_index']), None) if record else None
        origin = (marker.get('handoff_id'), marker['candidate_index'])
        if (item is None or origin in seen or old_marker and any(old_marker.get(k) != marker.get(k) for k in ('handoff_id', 'candidate_index'))
                or any((r['id'], i['candidate_index']) != origin for r, i in issued)):
            fail('source_marker_invalid', '来源被替换、重复或不属于本业务', business=name)
        seen.add(origin)
        proof = proofs.get(marker.get('source_review_id'))
        verified = proof is not None and valid(proof, record, name, flow)
        if not verified:
            old_flow = resolve_flow(previous, name)
            if flow.get('confirmedSignature') and (not old_flow.get('confirmedSignature')
                    or flow['confirmedSignature'] != old_flow.get('confirmedSignature')):
                fail('source_review_required', '完整来源或当前流程事实未通过来源复核，不能确认；人工稿保留', business=name)
            flow['reportSource'] = {key: marker[key] for key in ('handoff_id', 'candidate_index')}
            flow['confirmedSignature'] = None; flow['confirmedAt'] = None
    return out


def current_materials(db, p, wanted, *, ready=False, lock=False):
    from .evidence import evidence_bytes
    from .material_ocr import ensure_publishable
    result = {}
    for ref in sorted(wanted, key=lambda r: r['id']):
        material = scoped(db, SetupMaterial, ref['id'], p, lock)
        if (material.archived or material.sha256 != ref['hash'] or material.version != ref['version']
                or ref.get('evidence_id', material.evidence_id) != material.evidence_id):
            fail('source_changed', '来源已归档或版本/摘要变化，请保留草稿重新核对')
        evidence, content = evidence_bytes(db, p, material.evidence_id)
        if evidence.sha256 != material.sha256 or hashlib.sha256(content).hexdigest() != material.sha256:
            fail('source_integrity', '原件证据摘要无法核对')
        if ready:
            if material.status != 'parsed': fail('source_coverage_pending', '完整报告覆盖/OCR尚未确认；草稿和原回答保留')
            try: ensure_publishable(db, p, material)
            except HTTPException: fail('source_coverage_pending', '完整报告覆盖/OCR尚未确认；草稿和原回答保留')
        result[material.id] = material
    return result


def material_snapshot(material):
    return {'id': material.id, 'version': material.version, 'hash': material.sha256, 'evidence_id': material.evidence_id,
        'blocks_hash': digest(material.data.get('blocks', [])), 'coverage_hash': digest(material.data.get('ocr_coverage')),
        'confirmation_id': material.data.get('ocr_confirmation_id')}


def proof_valid(db, p, proof, handoff, name, flow):
    try:
        checked_public(proof)
        if (proof['schema'] != SOURCE or proof['handoff_id'] != handoff['id'] or proof['source_hash'] != handoff['source_hash']
                or proof['candidate_index'] != flow['reportSource']['candidate_index']
                or proof['id'] != flow['reportSource'].get('source_review_id')
                or proof['fact_hash'] != fact_hash(name, flow) or proof['basis_hash'] != basis_hash(flow)):
            return False
        if 'projection' in proof:
            return projection_verifier(proof['projection']).verify_projection(db, p, proof, handoff, name, flow)
        materials = current_materials(db, p, proof['material_refs'], ready=True)
        return proof['materials'] == [material_snapshot(materials[ref['id']]) for ref in proof['material_refs']]
    except (HTTPException, KeyError, TypeError, AttributeError, ValueError):
        return False


def protect_configuration_sources(db, p, previous, proposed, *, restore=False):
    records = all_records(db, p)
    if restore:
        # History is a recovery proposal, never a fresh human source confirmation.
        proposed = copy.deepcopy(proposed)
        for flow in proposed.get('flows', {}).values():
            marker = flow.get('reportSource')
            if isinstance(marker, dict):
                marker.pop('source_review_id', None)
                flow['confirmedSignature'] = None; flow['confirmedAt'] = None
    protected = protect_value(previous, proposed, [r for r in records if r['schema'] == HANDOFF],
                         {r['id']: r for r in records if r['schema'] == SOURCE},
                         lambda *args: proof_valid(db, p, *args))
    from .report_full_semantic_handoff import protect_configuration as protect_full_semantics
    protected = protect_full_semantics(db,p,previous,protected)
    from .business_flow_execution_adoption import protect_configuration as protect_business_flow_execution
    protect_business_flow_execution(db, p, previous, protected)
    from .authored_workflow_store_v208 import protect_configuration as protect_authored_workflow
    protect_authored_workflow(db, p, previous, protected, restore=restore)
    return protected


def source_states(db, p, configuration, handoff, records):
    proofs = {r['id']: r for r in records if r['schema'] == SOURCE}; states = []
    for item in handoff['selection']:
        matches = [(name, resolve_flow(configuration, name)) for name in names(configuration)
            if resolve_flow(configuration, name).get('reportSource', {}).get('handoff_id') == handoff['id']
            and resolve_flow(configuration, name).get('reportSource', {}).get('candidate_index') == item['candidate_index']]
        name, flow = matches[0] if len(matches) == 1 else (None, {})
        identity = flow.get('reportSource', {}).get('source_review_id')
        verified = bool(name is not None and identity in proofs and proof_valid(db, p, proofs[identity], handoff, name, flow))
        states.append({'candidate_index': item['candidate_index'], 'flow_name': name,
            'fact_hash': fact_hash(name, flow) if name is not None else None, 'source_review_id': identity,
            'verified': verified, 'issues': [] if verified else [{'code': 'source_review_required',
                'message': '须核对完整来源、逐字引用及当前流程事实；不能把缺省顺序当成已规划条件或并行'}]})
    return states


def configuration_source_issues(db, p, config):
    records = all_records(db, p); handoffs = [r for r in records if r['schema'] == HANDOFF]
    proofs = {r['id']: r for r in records if r['schema'] == SOURCE}; result = {}
    for name in names(config):
        one = {**config, 'businesses': [name]}
        try:
            protected = protect_value({}, one, handoffs, proofs, lambda *args: proof_valid(db, p, *args))
            flow = resolve_flow(protected, name)
            if flow.get('reportSource') and not flow['reportSource'].get('source_review_id'):
                result[name] = ['报告来源尚未通过完整覆盖、逐字引用和当前流程事实复核，不能确认或发布']
        except HTTPException as error:
            result[name] = [error.detail['message']]
    return result


def configuration_source_snapshots(db, p, config):
    records = all_records(db, p); proofs = {r['id']: r for r in records if r['schema'] == SOURCE}
    handoffs = {r['id']: r for r in records if r['schema'] == HANDOFF}; result = {}
    for name in names(config):
        flow = resolve_flow(config, name); marker = flow.get('reportSource', {})
        proof = proofs.get(marker.get('source_review_id')); handoff = handoffs.get(marker.get('handoff_id'))
        if proof and handoff and proof_valid(db, p, proof, handoff, name, flow):
            pin = {key: proof[key] for key in ('handoff_id', 'candidate_index', 'fact_hash', 'basis_hash', 'record_hash')}
            pin['source_review_id'] = proof['id']
            if 'projection' in proof:
                formal_ids = [m['id'] for m in proof['materials'] if m['role'] in {'confirmed_view', 'unprojected'}]
            else:
                formal_ids = [ref['id'] for ref in proof['material_refs']]
            for identity in formal_ids:
                if pin not in result.setdefault(identity, []): result[identity].append(pin)
    return result


def verify_report_material_snapshot(db, p, source):
    for pin in source.get('report_sources', []):
        row = owned_record(db, p, pin['source_review_id'], kind='source_review')
        proof = row.data['record']
        if any(proof.get(key) != pin.get(key) for key in ('handoff_id', 'candidate_index', 'fact_hash', 'basis_hash', 'record_hash')):
            fail('source_review_changed', '发布版本的来源复核绑定无法核对')
        if 'projection' in proof:
            handoff = owned_record(db, p, proof['handoff_id']).data['record']
            formal_ids = {m['id'] for m in proof['materials'] if m['role'] in {'confirmed_view', 'unprojected'}}
            if source['id'] not in formal_ids or not projection_verifier(proof['projection']).verify_projection(db, p, proof, handoff):
                fail('source_review_changed', '发布版本的原件、最终修订副本或逐页确认已变化')
            continue
        materials = current_materials(db, p, proof['material_refs'], ready=True)
        if (source['id'] not in materials or proof['materials'] != [material_snapshot(materials[r['id']]) for r in proof['material_refs']]):
            fail('source_review_changed', '发布版本引用的完整来源或解析确认已变化')


def source_review_value(db, p, handoff, configuration, body, record_id):
    configuration_cas(configuration, body)
    matches = [(name, resolve_flow(configuration.data, name)) for name in names(configuration.data)
        if resolve_flow(configuration.data, name).get('reportSource', {}).get('handoff_id') == handoff['id']
        and resolve_flow(configuration.data, name).get('reportSource', {}).get('candidate_index') == body.candidate_index]
    if len(matches) != 1: fail('source_marker_invalid', '待核业务已删除或来源归属无法核对')
    name, flow = matches[0]
    if body.fact_hash != fact_hash(name, flow): fail('source_fact_changed', '当前流程或职责已变化，请重新核对')
    wanted = [r.model_dump(mode='json') for r in body.material_refs]
    original = {m['id']: m for m in handoff['materials']}
    if {r['id'] for r in wanted} != set(original) or any(r['hash'] != original[r['id']]['hash'] for r in wanted):
        fail('source_changed', '来源必须是原报告同一摘要，不能换成其他文件')
    materials = current_materials(db, p, wanted, ready=True, lock=True)
    if any(materials[key].evidence_id != original[key]['evidence_id'] for key in materials):
        fail('source_changed', '来源原件证据已替换')
    selected = next((s for s in handoff['selection'] if s['candidate_index'] == body.candidate_index), None)
    if selected is None: fail('selection_invalid', '来源业务索引无效')
    mappings = exact_mappings({**handoff, 'selection': [selected]}, materials)
    proposed = copy.deepcopy(configuration.data); updated = resolve_flow(proposed, name)
    for item in [updated, *updated.get('steps', [])]:
        for cite in item.get('citations', []):
            block = materials.get(cite['material_id'])
            matching = [b for b in block.data.get('blocks', []) if cite['quote'] in b.get('text', '')] if block else []
            if len(matching) != 1: fail('source_quote_ambiguous' if matching else 'source_quote_missing', '当前依据无法唯一逐字重绑')
            cite['block_id'] = matching[0]['id']
        if 'citation' in item: item['citation'] = '；'.join(c['block_id'] + '：' + c['quote'] for c in item.get('citations', []))
    updated['reportSource'] = {'handoff_id': handoff['id'], 'candidate_index': body.candidate_index, 'source_review_id': record_id}
    updated['confirmedAt'] = None; updated['confirmedSignature'] = None
    for m in proposed.get('materials', []):
        if m.get('id') in materials:
            current = materials[m['id']]; m.update(version=current.version, hash=current.sha256, archived=False)
    setup().validate_configuration(proposed)
    record = public_record({'schema': SOURCE, 'id': record_id, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'report_id': handoff['report_id'], 'configuration_id': configuration.id, 'handoff_id': handoff['id'],
        'source_hash': handoff['source_hash'], 'candidate_index': body.candidate_index, 'request_id': str(body.request_id),
        'command_hash': command_hash(p, 'source_review', handoff['id'], body), 'fact_hash': body.fact_hash,
        'basis_hash': basis_hash(updated), 'material_refs': wanted,
        'materials': [material_snapshot(materials[r['id']]) for r in wanted], 'mappings': mappings,
        'accepted_revision': configuration.version + 1, 'configuration_hash': digest(proposed)})
    receipt = make_receipt(p, 'source_review', record, None, handoff['id'])
    ledger_data(record, body, receipt)
    return record, proposed, receipt


def configuration_row(db, p, lock=False):
    query = select(SetupDraft).where(SetupDraft.workspace_id == p.workspace_id)
    if lock: query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None: fail('configuration_missing', '请先保存当前工作区配置')
    return row


def request_row(db, p, request_id):
    return db.scalar(select(Record).where(Record.workspace_id == p.workspace_id, Record.request_id == str(request_id)))


def owned_record(db, p, identity, *, kind='handoff'):
    row = db.scalar(select(Record).where(Record.workspace_id == p.workspace_id, Record.id == str(identity), Record.kind == kind))
    if row is None: fail('not_found', '未找到此工作区已共享的交接记录', 404)
    checked_ledger(row, p)
    return row


def nonce_lock(db, p, request_id):
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
               {'key': 'report-config:' + p.workspace_id + ':' + str(request_id)})


def commit_record(db, p, configuration, record, proposed, body, receipt, kind, parent_id=None):
    # No private draft writes, no Job, CloudCall or provider activity.
    setup().automation_review_actor(db, p)
    db.execute(insert(SetupRevision).values(id=uid(), workspace_id=p.workspace_id, draft_id=configuration.id,
        version=configuration.version, data=configuration.data).on_conflict_do_nothing(index_elements=['draft_id', 'version']))
    row = Record(id=record['id'], workspace_id=p.workspace_id, actor_id=p.user_id, report_id=record['report_id'],
        configuration_id=configuration.id, request_id=str(body.request_id), kind=kind, parent_id=parent_id,
        data=ledger_data(record, body, receipt))
    db.add(row); configuration.data = proposed; configuration.version += 1
    db.add(SetupRevision(workspace_id=p.workspace_id, draft_id=configuration.id, version=configuration.version, data=proposed))
    audit(db, p, 'setup.report_configuration.' + kind, record['id'], {'request_id': str(body.request_id),
        'report_id': record['report_id'], 'accepted_revision': configuration.version,
        'record_hash': record['record_hash'], 'command_hash': record['command_hash']})
    db.commit()
    return row


def receipt_reply(row, p, configuration):
    data = checked_ledger(row, p, private=True); receipt = copy.deepcopy(data['receipt'])
    return {'receipt': receipt, 'configuration': setup().draft_view(configuration),
        'current_matches': configuration.version == receipt['accepted_revision'] and digest(configuration.data) == receipt['configuration_hash'],
        'handoff' if row.kind == 'handoff' else 'source_review': copy.deepcopy(data['record'])}


@router.post('/report-drafts/{report_id}/configuration-preview')
def preview_handoff(report_id: UUID, body: PreviewCommand, p: rd.P, db: rd.DB):
    require(p, 'admin', 'operator')
    report = rd.owned(db, p, report_id)
    return preview_value(report, p, configuration_row(db, p), body)


@router.post('/report-drafts/{report_id}/configuration-handoff')
def submit_handoff(report_id: UUID, body: HandoffCommand, p: rd.P, db: rd.DB):
    require(p, 'admin', 'operator'); nonce_lock(db, p, body.request_id)
    existing = request_row(db, p, body.request_id)
    if existing:
        replay_command(existing, p, 'handoff', str(report_id), body)
        return receipt_reply(existing, p, configuration_row(db, p))
    report = rd.owned(db, p, report_id, True)
    if report.data.get('schema') == 'report-evidence-draft-state-v1':
        fail('full_semantics_mapping_required', '本稿含分段来源、全局声明与协同关系，当前正式交接尚未提供完整映射；私人候选已保留，不会按旧格式丢弃字段')
    if report.data.get('schema') == 'report-full-reading-draft-state-v1':
        fail('full_semantics_mapping_required', '本稿含全文来源、职责与协同关系，当前正式交接尚未提供完整映射；私人候选已保留，不会按旧格式丢弃字段')
    configuration = configuration_row(db, p, True)
    raw = partial.checked_raw(report)
    partial.checked_call(report, raw, scoped(db, CloudCall, report.data.get('reserved_call_id'), p))
    record, proposed, receipt = handoff_value(report, p, configuration, body, uid())
    current_materials(db, p, record['materials'], lock=True)
    # A deleted business cannot be reintroduced source-less or overwrite its old lineage.
    existing_records = all_records(db, p)
    proposed = protect_value(configuration.data, proposed,
        [r for r in existing_records if r['schema'] == HANDOFF] + [record],
        {r['id']: r for r in existing_records if r['schema'] == SOURCE}, lambda *args: proof_valid(db, p, *args))
    record = public_record({**{k: v for k, v in record.items() if k != 'record_hash'}, 'configuration_hash': digest(proposed)})
    receipt = make_receipt(p, 'handoff', record, body.preview_hash, record['id'])
    row = commit_record(db, p, configuration, record, proposed, body, receipt, 'handoff')
    return receipt_reply(row, p, configuration)


@router.get('/report-drafts/{report_id}/configuration-receipts/{request_id}')
def get_handoff_receipt(report_id: UUID, request_id: UUID, p: rd.P, db: rd.DB):
    row = request_row(db, p, request_id)
    if row is None or row.report_id != str(report_id) or row.kind != 'handoff' or row.actor_id != p.user_id:
        fail('receipt_not_found', '尚未找到本人此交接回执', 404)
    return receipt_reply(row, p, configuration_row(db, p))


@router.get('/report-handoffs/{handoff_id}')
def get_handoff(handoff_id: UUID, p: rd.P, db: rd.DB):
    row = owned_record(db, p, handoff_id); record = row.data['record']
    return {'handoff': copy.deepcopy(record), 'receipt_hash': row.data['receipt']['receipt_hash'],
        'source_states': source_states(db, p, configuration_row(db, p).data, record, all_records(db, p))}


@router.post('/report-handoffs/{handoff_id}/source-review')
def submit_source_review(handoff_id: UUID, body: SourceReviewCommand, p: rd.P, db: rd.DB):
    require(p, 'admin', 'operator'); nonce_lock(db, p, body.request_id)
    existing = request_row(db, p, body.request_id)
    if existing:
        replay_command(existing, p, 'source_review', str(handoff_id), body)
        return receipt_reply(existing, p, configuration_row(db, p))
    parent = owned_record(db, p, handoff_id); configuration = configuration_row(db, p, True)
    record, proposed, receipt = source_review_value(db, p, parent.data['record'], configuration, body, uid())
    row = commit_record(db, p, configuration, record, proposed, body, receipt, 'source_review', parent.id)
    return receipt_reply(row, p, configuration)


@router.get('/report-handoffs/{handoff_id}/source-projection-options')
def get_source_projection_options(handoff_id: UUID, p: rd.P, db: rd.DB,
        candidate_index: int = Query(ge=0, lt=20)):
    from .report_source_projection import projection_options
    parent = owned_record(db, p, handoff_id)
    return projection_options(db, p, parent.data['record'], candidate_index)


@router.post('/report-handoffs/{handoff_id}/projected-source-review')
def submit_projected_source_review(handoff_id: UUID, body: ProjectedSourceReviewCommand, p: rd.P, db: rd.DB):
    from .report_source_projection import projected_review_value
    require(p, 'admin', 'operator'); nonce_lock(db, p, body.request_id)
    existing = request_row(db, p, body.request_id)
    if existing:
        replay_command(existing, p, 'source_review', str(handoff_id), body)
        return receipt_reply(existing, p, configuration_row(db, p))
    parent = owned_record(db, p, handoff_id); configuration = configuration_row(db, p, True)
    record, proposed, receipt = projected_review_value(db, p, parent.data['record'], configuration, body, uid())
    row = commit_record(db, p, configuration, record, proposed, body, receipt, 'source_review', parent.id)
    return receipt_reply(row, p, configuration)


@router.post('/report-handoffs/{handoff_id}/region-source-review')
def submit_region_source_review(handoff_id: UUID, body: MultiRegionSourceReviewCommand, p: rd.P, db: rd.DB):
    from .report_region_projection import region_review_value
    nonce_lock(db, p, body.request_id)
    existing = request_row(db, p, body.request_id)
    if existing:
        replay_command(existing, p, 'source_review', str(handoff_id), body)
        return receipt_reply(existing, p, configuration_row(db, p))
    require(p, 'admin', 'operator')
    parent = owned_record(db, p, handoff_id); configuration = configuration_row(db, p, True)
    record, proposed, receipt = region_review_value(db, p, parent.data['record'], configuration, body, uid())
    row = commit_record(db, p, configuration, record, proposed, body, receipt, 'source_review', parent.id)
    return receipt_reply(row, p, configuration)


@router.get('/report-handoffs/{handoff_id}/source-reviews/{proof_id}/region-bindings/{binding_id}')
def get_region_source_binding(handoff_id: UUID, proof_id: UUID, binding_id: str, p: rd.P, db: RegionReadDB):
    from .report_region_projection import SCHEMA, binding_view
    parent = owned_record(db, p, handoff_id)
    row = owned_record(db, p, proof_id, kind='source_review')
    proof = row.data['record']
    if (row.parent_id != str(handoff_id) or proof.get('projection', {}).get('schema') != SCHEMA
            or len(binding_id) != 64 or any(c not in '0123456789abcdef' for c in binding_id)):
        fail('region_binding_not_found', '未找到此正式来源引用', 404)
    return binding_view(db, p, parent.data['record'], proof, binding_id)


@router.get('/report-handoffs/{handoff_id}/source-review-receipts/{request_id}')
def get_source_review_receipt(handoff_id: UUID, request_id: UUID, p: rd.P, db: rd.DB):
    row = request_row(db, p, request_id)
    if row is None or row.parent_id != str(handoff_id) or row.kind != 'source_review' or row.actor_id != p.user_id:
        fail('receipt_not_found', '尚未找到本人此来源复核回执', 404)
    return receipt_reply(row, p, configuration_row(db, p))
