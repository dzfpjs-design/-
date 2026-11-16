"""Read-only display of saved Map fields, never an accepted Map result.

The pure projector keeps wire fields separate from protocol/field constraints.
The route reads only an owned frozen draft and its existing immutable journal.
No recovery, settlement, stage mutation, audit, Job or provider call occurs.
"""
import copy
import json
import re
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from .contracts import digest
from .report_evidence_plan import EvidenceClaim, check_evidence_source, validate_map_result
from .report_evidence_wire_v3 import MapResultWireV3
from .report_full_reading_pipeline import DB, ReadP


router = APIRouter()
SCHEMA = 'report-evidence-saved-facts-v1'
_STAGE = r'^map-(?:[1-9]|1[0-9]|2[0-4])$'
_SPAN = re.compile(r'^[wo]:p[1-9][0-9]*/s[1-9][0-9]*$')
_HASH = re.compile(r'^[a-f0-9]{64}$')
_FIELDS = ('kind', 'statement', 'span_refs', 'status', 'explanation')
_CONTEXT = {'actor_id', 'workspace_id', 'draft_id', 'draft_revision', 'stage', 'stage_status',
    'generation_schema', 'source_hash', 'catalog_hash', 'messages_hash', 'ocr_revision_kinds'}
_DESCRIPTOR = {'schema', 'binding', 'binding_hash', 'wire_kind', 'encoding', 'wire_bytes', 'wire_sha256',
    'receipt_status', 'receipt_bytes', 'receipt_sha256', 'consumable', 'quarantine_reasons',
    'envelope_bytes', 'envelope_sha256', 'descriptor_hash'}
_MESSAGES = {
    'available': '已保存内容仅供核对；原协议结果不变，含义尚未人工核对，未进入汇总。',
    'missing': '尚无完整已保存原回答；原编号保留，此处不会补发或调用模型。',
    'quarantined': '原回答已保留但处于隔离状态，暂不能安全显示；不会修复或重发。',
    'not_json': '原回答已保留，但不是可安全读取的严格JSON；不会猜测修复或重发。',
    'unsupported_structure': '原回答已保留，但不是最多80条声明的可核对结构；没有截取部分内容冒充完整结果。',
}


def _issue(path, code, message='字段不符合冻结协议约束；原值未被修正。'):
    return {'path': path, 'code': code, 'message': message}


def _errors(error):
    result = []
    for item in error.errors(include_input=False, include_context=False, include_url=False):
        path = '/' + '/'.join(str(part) if type(part) is int or part in {
            'schema', 'claims', 'key', *_FIELDS} else 'unknown_field' for part in item['loc'])
        result.append(_issue(path, item['type']))
    return result


def _constraints(claim):
    # Only the five original semantic fields are checked. This dummy key is
    # validation input, never a replacement for the wire key or a model result.
    try:
        EvidenceClaim.model_validate({'key': 'display_constraint_check',
            **{field: claim[field] for field in _FIELDS if field in claim}})
        return []
    except ValidationError as error:
        return _errors(error)
    except (ValueError, TypeError, RecursionError):
        return [_issue('/', 'unsupported_fields')]


def project_saved_facts(context, catalog, allowed_span_refs, wire_value=None, *,
                        journal=None, availability='available', completion_valid=None):
    """Pure projection; ``journal`` is an already verified descriptor, not raw.

    Unsupported Python/JSON field values are never repr'd or returned as JSON.
    All supported strings and string lists remain byte-for-byte unchanged.
    This output is permanently ineligible for adoption or reducer input.
    """
    if type(context) is not dict or set(context) != _CONTEXT or availability not in _MESSAGES:
        raise ValueError('Saved-facts context differs')
    check_evidence_source(catalog)
    if (any(type(context[key]) is not str or str(UUID(context[key])) != context[key]
            for key in ('actor_id', 'workspace_id', 'draft_id'))
            or type(context['draft_revision']) is not int or context['draft_revision'] < 1
            or type(context['stage']) is not str or not re.fullmatch(_STAGE, context['stage'])
            or context['generation_schema'] not in {'report-evidence-map-generation-v1', 'report-evidence-map-generation-v2',
                'report-evidence-map-generation-v3', 'report-evidence-map-generation-v4'}
            or context['stage_status'] not in {'pending', 'queued', 'running', 'succeeded', 'failed', 'response_unknown', 'not_started'}
            or any(type(context[key]) is not str or not _HASH.fullmatch(context[key])
                for key in ('source_hash', 'catalog_hash', 'messages_hash'))
            or context['source_hash'] != catalog['descriptor']['source_hash']
            or context['catalog_hash'] != catalog['descriptor']['catalog_hash']
            or type(allowed_span_refs) is not list or len(set(allowed_span_refs)) != len(allowed_span_refs)
            or any(type(ref) is not str or ref not in catalog['index'] for ref in allowed_span_refs)
            or type(context['ocr_revision_kinds']) is not dict
            or any(type(ref) is not str or not re.fullmatch(r'o:p[1-9][0-9]*', ref)
                or kind not in {'raw', 'edited', 'confirmed'} for ref, kind in context['ocr_revision_kinds'].items())):
        raise ValueError('Saved-facts scope differs')
    if journal is not None:
        if (type(journal) is not dict or set(journal) != _DESCRIPTOR
                or journal['schema'] != 'report-evidence-journal-descriptor-v1'
                or journal['descriptor_hash'] != digest({key: value for key, value in journal.items() if key != 'descriptor_hash'})
                or journal['binding_hash'] != digest(journal['binding'])
                or any(journal['binding'].get(key) != context[key] for key in
                    ('actor_id', 'workspace_id', 'draft_id', 'stage', 'source_hash', 'catalog_hash', 'messages_hash'))):
            raise ValueError('Saved-facts journal differs')
    if availability != 'missing' and journal is None:
        raise ValueError('Saved-facts original descriptor missing')
    if availability == 'missing' and journal is not None:
        raise ValueError('Saved-facts missing state has an original descriptor')
    if availability in {'available', 'not_json'} and journal['consumable'] is not True:
        raise ValueError('Saved-facts original is quarantined')
    if availability == 'quarantined' and journal['consumable'] is not False:
        raise ValueError('Saved-facts quarantine state differs')
    issues, protocol_valid, facts = [], None, []
    if availability == 'available':
        try:
            if context['generation_schema'] in {'report-evidence-map-generation-v3', 'report-evidence-map-generation-v4'}:
                checked = MapResultWireV3.model_validate(wire_value).model_dump()
                if checked != wire_value or any(set(claim['span_refs']) - set(allowed_span_refs)
                        for claim in checked['claims']):
                    raise ValueError('V3 original wire or batch references differ')
                if type(completion_valid) is not bool:
                    raise ValueError('V3 original completion could not be verified')
            else:
                validate_map_result(wire_value, catalog, allowed_span_refs)
            protocol_valid = True
        except ValidationError as error:
            protocol_valid = False; issues = _errors(error)
        except (ValueError, TypeError, RecursionError):
            protocol_valid = False; issues = [_issue('/', 'protocol_validation')]
        if context['generation_schema'] in {'report-evidence-map-generation-v3', 'report-evidence-map-generation-v4'} and completion_valid is not True:
            protocol_valid = False
            issues.append(_issue('/completion', 'response_incomplete', '原回答没有正常完整结束；只读保留，不得采用或免费恢复为成功。'))
        if (type(wire_value) is not dict or type(wire_value.get('claims')) is not list
                or len(wire_value['claims']) > 80):
            availability = 'unsupported_structure'; protocol_valid = False
            issues.append(_issue('/claims', 'unsupported_structure', _MESSAGES[availability]))
        else:
            allowed = set(allowed_span_refs)
            for position, original in enumerate(wire_value['claims']):
                claim = original if type(original) is dict else {}
                fields = {field: copy.deepcopy(claim[field]) if field in claim and (
                    type(claim[field]) is str if field != 'span_refs' else type(claim[field]) is list
                    and all(type(ref) is str for ref in claim[field])) else None for field in _FIELDS}
                field_issues = _constraints(claim); references = []
                refs = fields['span_refs']
                if refs is not None and len(refs) <= 16:
                    for ref in refs:
                        state = ('invalid' if not _SPAN.fullmatch(ref) else 'outside_batch' if ref not in allowed
                            and ref in catalog['index'] else 'missing' if ref not in catalog['index'] else 'resolved')
                        item = {'span_ref': ref, 'state': state, 'source_ref': None, 'start': None, 'end': None,
                            'text': None, 'source_kind': None, 'source_number': None, 'revision_kind': None}
                        if state == 'resolved':
                            span = catalog['index'][ref]; source_ref = span['source_ref']; ocr = source_ref.startswith('o:')
                            revision = context['ocr_revision_kinds'].get(source_ref) if ocr else None
                            if ocr and revision is None: raise ValueError('Saved-facts OCR revision missing')
                            item.update(source_ref=source_ref, start=span['start'], end=span['end'], text=span['text'],
                                source_kind='ocr_page' if ocr else 'word_block', source_number=int(source_ref[3:]), revision_kind=revision)
                        else:
                            field_issues.append(_issue('/span_refs', 'reference_' + state, '引用未在本批冻结证据中正确解析；没有替换或补造原文。'))
                        references.append(item)
                facts.append({'position': position, 'display_id': 'sf_' + digest({
                    'schema': 'report-evidence-saved-fact-id-v1', 'binding_hash': journal['binding_hash'],
                    'wire_sha256': journal['wire_sha256'], 'position': position}),
                    'protocol_key': claim.get('key') if type(claim.get('key')) is str else None,
                    'fields': fields, 'field_constraints_valid': not field_issues, 'issues': field_issues,
                    'references': references, 'meaning_review_status': 'unreviewed'})
    result = {'schema': SCHEMA, **{key: copy.deepcopy(value) for key, value in context.items() if key != 'ocr_revision_kinds'},
        'journal': copy.deepcopy(journal), 'availability': availability, 'message': _MESSAGES[availability],
        'original_protocol_valid': protocol_valid, 'protocol_issues': issues, 'facts': facts,
        'meaning_review_status': 'unreviewed', 'eligible_for_reduce': False, 'can_apply': False, 'can_publish': False}
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 8 * 1024 * 1024:
        raise ValueError('Saved-facts view exceeds capacity')
    return {**result, 'view_hash': digest(result)}


def _integrity():
    raise HTTPException(409, {'code': 'evidence_saved_facts_integrity',
        'message': '已保存内容、回执或冻结出处无法核对；原记录保留，不会修复、重发或进入汇总。'})


@router.get('/report-evidence-readings/{identity}/saved-facts')
def saved_facts_route(identity: UUID, p: ReadP, db: DB, stage: str = Query(..., pattern=_STAGE)):
    from . import report_evidence_pipeline as pipeline
    from . import report_evidence_journal as journal_io
    try:
        try:
            row = pipeline.owned(db, p, identity)
        except HTTPException as error:
            if error.status_code == 409: _integrity()
            raise
        matches = [item for item in row.data['maps'] if item['chunk_id'] == stage]
        if not matches:
            raise HTTPException(404, {'code': 'not_found', 'message': '未找到此草案的原读取分组'})
        item = matches[0]; preview = row.data['preview']; chunk = pipeline._authorized(row, stage)
        context = {'actor_id': row.actor_id, 'workspace_id': row.workspace_id, 'draft_id': row.id,
            'draft_revision': row.version, 'stage': stage, 'stage_status': item['status'],
            'generation_schema': preview['map_preparation']['generation_contract']['schema'],
            'source_hash': preview['source_hash'], 'catalog_hash': preview['map_preparation']['catalog_descriptor']['catalog_hash'],
            'messages_hash': chunk['messages_hash'], 'ocr_revision_kinds': {
                'o:p' + str(page['page']): page['revision_kind'] for page in preview['body']['selection']['pages']}}
        if context['generation_schema'] in {'report-evidence-map-generation-v5', 'report-evidence-map-generation-v6', 'report-evidence-map-generation-v7'}:
            from .report_evidence_saved_facts_v2 import read_saved_facts
            return read_saved_facts(pipeline, row, stage, context)
        catalog = pipeline._catalog(preview); root = pipeline._journal_root()
        if item['reserved_call_id'] is None:
            return project_saved_facts(context, catalog, chunk['span_refs'], availability='missing')
        binding = pipeline.journal_binding(row, stage, item['reserved_call_id'])
        try:
            descriptor = journal_io.read_descriptor(root, binding)
        except journal_io.JournalError as error:
            if item['journal'] is None and error.code == 'journal_missing_or_unsafe':
                # Distinguish a genuinely absent not-yet-written stage from an
                # existing partial/aliased/corrupt directory. Never create it.
                try:
                    with journal_io._stage(root, binding): pass
                except FileNotFoundError:
                    return project_saved_facts(context, catalog, chunk['span_refs'], availability='missing')
            _integrity()
        if item['journal'] is not None and descriptor != item['journal']: _integrity()
        if not descriptor['consumable']:
            return project_saved_facts(context, catalog, chunk['span_refs'], journal=descriptor, availability='quarantined')
        loaded = journal_io.read_response(root, binding, descriptor)
        receipt = loaded['receipt']
        if (receipt.get('call_id') != item['reserved_call_id'] or receipt.get('provider') != preview['provider']
                or receipt.get('model') != preview['model']): _integrity()
        strict = context['generation_schema'] == 'report-evidence-map-generation-v4'
        if strict:
            from . import report_evidence_pipeline_v4 as v4
            from .report_evidence_strict_map import parse_strict_map_arguments, StrictMapError
            body, inspected = v4.inspect_response(pipeline, row, stage, loaded, descriptor)
            try:
                response = {'value': parse_strict_map_arguments(body, receipt['http_status'])}
            except StrictMapError:
                return project_saved_facts(context, catalog, chunk['span_refs'], journal=descriptor, availability='not_json')
        else:
            try:
                response = journal_io.parse_response(root, binding, descriptor)
            except journal_io.JournalError as error:
                if error.code == 'journal_response_not_json':
                    return project_saved_facts(context, catalog, chunk['span_refs'], journal=descriptor, availability='not_json')
                _integrity()
        completion = None
        if context['generation_schema'] == 'report-evidence-map-generation-v3':
            policy = dict(preview['map_preparation']['generation_contract']['input_policy'])
            if policy.pop('single_batch', None) is not True: _integrity()
            completion = (receipt.get('finish_reason') == 'stop' and receipt.get('evidence_profile') == policy
                and type(receipt.get('response_choice_count')) is int and receipt['response_choice_count'] == 1)
        elif strict:
            completion = inspected['metadata']['envelope_valid'] is True
        return project_saved_facts(context, catalog, chunk['span_refs'], response['value'], journal=descriptor,
            completion_valid=completion)
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        _integrity()
