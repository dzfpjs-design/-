"""Read-only display of original V5 fixed-slot Map declarations.

project_saved_facts_v2 is pure. read_saved_facts only reads an already-owned
frozen row and its immutable Map-v1 journal. It performs no consume, settlement,
audit, Job, provider, API call or write. Whole HTTP metadata is checked before
arguments parsing; incomplete/nonunique tools are never salvaged as a first
answer. This display codec is not an accepted Map projection or reducer input.
"""
import copy
import json
import re
from uuid import UUID

from pydantic import ValidationError

from .contracts import digest
from .report_evidence_journal import JournalBinding
from .report_evidence_map_slots_v5 import MapSlotsWireV5, SLOT_NAMES, WIRE_SCHEMA
from .report_evidence_plan import EvidenceClaim, check_evidence_source, resolve_evidence, validate_map_result
from .report_evidence_strict_map import METADATA_KEYS
from .report_evidence_structured_tool_v2 import DELIVERY, parse_arguments
from .report_full_reading_semantics import _safe


SCHEMA = 'report-evidence-saved-facts-v2'
DISPLAY_CODEC = 'report-evidence-slot-display-only-v1'
DISPLAY_IDS = 'report-evidence-saved-slot-id-v2'
GENERATION = 'report-evidence-map-generation-v5'
GENERATION_V6 = 'report-evidence-map-generation-v6'
GENERATION_V7 = 'report-evidence-map-generation-v7'
MAX_VIEW_BYTES = 8 * 1024 * 1024
_FIELDS = ('kind', 'statement', 'span_refs', 'status', 'explanation')
_CONTEXT = {'actor_id', 'workspace_id', 'draft_id', 'draft_revision', 'stage', 'stage_status',
    'generation_schema', 'source_hash', 'catalog_hash', 'messages_hash', 'ocr_revision_kinds'}
_DESCRIPTOR = {'schema', 'binding', 'binding_hash', 'wire_kind', 'encoding', 'wire_bytes', 'wire_sha256',
    'receipt_status', 'receipt_bytes', 'receipt_sha256', 'consumable', 'quarantine_reasons',
    'envelope_bytes', 'envelope_sha256', 'descriptor_hash'}
_HASH = re.compile(r'^[a-f0-9]{64}$')
_STAGE = r'^map-(?:[1-9]|1[0-9]|2[0-4])$'
_SPAN = re.compile(r'^[wo]:p[1-9][0-9]*/s[1-9][0-9]*$')
_MESSAGES = {
    'available': '已保存槽位内容仅供核对；字段、引用与含义仍须分别核对，不代表原协议通过或已采用。',
    'missing': '尚无完整已保存原回答；原编号保留，此处不会补发或调用模型。',
    'quarantined': '原回答已保留但处于隔离状态，暂不能安全显示；不会修复或重发。',
    'not_json': '原回答已保留，但工具未完整结束、唯一指定工具格式未通过，或参数不是严格JSON；没有截取首个工具或修复原答。',
    'unsupported_structure': '原回答已保留，但不是完整80槽的闭合结构；没有截取前80槽、补槽或展示部分结构冒充完整结果。',
}


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _bytes(value):
    _safe(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8', 'strict')


def _issue(path, code, message='字段不符合冻结协议约束；原值未被修正。'):
    return {'path': path, 'code': code, 'message': message}


def _errors(error):
    allowed = {'schema', 'slots', 'state', 'claim', 'key', *_FIELDS, *SLOT_NAMES}
    return [_issue('/' + '/'.join(str(part) if type(part) is int or part in allowed
        else 'unknown_field' for part in item['loc']), item['type'])
        for item in error.errors(include_input=False, include_context=False, include_url=False)]


def _safe_text(value):
    if type(value) is not str:
        return False
    try:
        _safe(value)
        return True
    except (ValueError, UnicodeError):
        return False


def _display_fields(claim):
    fields, issues = {}, []
    for field in _FIELDS:
        value = claim.get(field)
        supported = (_safe_text(value) if field != 'span_refs' else type(value) is list
            and all(_safe_text(ref) for ref in value))
        fields[field] = copy.deepcopy(value) if supported else None
        if field in claim and not supported:
            issues.append(_issue('/' + field, 'unsupported_display_value',
                '此原值不能安全显示为文字或文字列表；None仅表示未显示，原值仍完整保存在原答中。'))
    if set(claim) - set(_FIELDS):
        issues.append(_issue('/', 'extra_fields', '原声明含额外字段；此处只显示五个语义字段，未声称展示完整原对象。'))
    try:
        EvidenceClaim.model_validate({'key': 'display_constraint_check',
            **{field: claim[field] for field in _FIELDS if field in claim}})
    except ValidationError as error:
        issues.extend(_errors(error))
    except (ValueError, TypeError, RecursionError):
        issues.append(_issue('/', 'unsupported_fields'))
    return fields, issues


def _slots(wire):
    if (type(wire) is not dict or set(wire) != {'schema', 'slots'}
            or wire['schema'] != WIRE_SCHEMA or type(wire['slots']) is not dict
            or set(wire['slots']) != set(SLOT_NAMES)):
        return None
    present = []
    for position, name in enumerate(SLOT_NAMES):
        slot = wire['slots'][name]
        if type(slot) is not dict:
            return None
        if slot.get('state') == 'absent' and set(slot) == {'state'}:
            continue
        if (slot.get('state') != 'present' or set(slot) != {'state', 'claim'}
                or type(slot['claim']) is not dict):
            return None
        present.append((position, name, slot['claim']))
    return present


def _delivery(generation):
    _need(generation in {GENERATION, GENERATION_V6, GENERATION_V7}, 'Unknown saved-slot generation')
    if generation == GENERATION_V7:
        return 'qwen-tokenplan-native-json-schema-v1'
    return 'deepseek-strict-report-tool-v3' if generation == GENERATION_V6 else DELIVERY


def _completion(metadata, generation=GENERATION):
    if generation == GENERATION_V7:
        return (metadata['delivery'] == _delivery(generation) and type(metadata['http_status']) is int
            and metadata['http_status'] == 200 and metadata['finish_reason'] == 'stop'
            and type(metadata['response_choice_count']) is int and metadata['response_choice_count'] == 1
            and type(metadata['tool_call_count']) is int and metadata['tool_call_count'] == 0
            and all(metadata[key] is None for key in ('tool_name', 'tool_type', 'tool_call_id', 'tool_calls_hash'))
            and metadata['envelope_valid'] is True and metadata['envelope_errors'] == []
            and metadata['arguments_encoding'] == 'utf-8'
            and type(metadata['usage_available']) is bool and type(metadata['usage_present']) is bool)
    return (metadata['delivery'] == _delivery(generation) and type(metadata['http_status']) is int
        and metadata['http_status'] == 200 and metadata['finish_reason'] == 'tool_calls'
        and type(metadata['response_choice_count']) is int and metadata['response_choice_count'] == 1
        and type(metadata['tool_call_count']) is int and metadata['tool_call_count'] == 1
        and metadata['tool_name'] == 'submit_report_evidence_map_slots' and metadata['tool_type'] == 'function'
        and _safe_text(metadata['tool_call_id']) and bool(metadata['tool_call_id'].strip())
        and metadata['envelope_valid'] is True and metadata['envelope_errors'] == []
        and metadata['arguments_encoding'] == 'utf-8'
        and type(metadata['usage_available']) is bool and type(metadata['usage_present']) is bool)


def _scope(context, catalog, allowed_span_refs, journal, availability, metadata):
    _need(type(context) is dict and set(context) == _CONTEXT
        and availability in _MESSAGES, 'Saved-slot context differs')
    _safe(context); check_evidence_source(catalog)
    _need(all(type(context[key]) is str and str(UUID(context[key])) == context[key]
        for key in ('actor_id', 'workspace_id', 'draft_id'))
        and type(context['draft_revision']) is int and context['draft_revision'] > 0
        and type(context['stage']) is str and re.fullmatch(_STAGE, context['stage'])
        and context['generation_schema'] in {GENERATION, GENERATION_V6, GENERATION_V7}
        and context['stage_status'] in {'pending', 'queued', 'running', 'succeeded', 'failed', 'response_unknown', 'not_started'}
        and all(type(context[key]) is str and _HASH.fullmatch(context[key])
            for key in ('source_hash', 'catalog_hash', 'messages_hash'))
        and context['source_hash'] == catalog['descriptor']['source_hash']
        and context['catalog_hash'] == catalog['descriptor']['catalog_hash']
        and type(context['ocr_revision_kinds']) is dict
        and all(type(ref) is str and re.fullmatch(r'o:p[1-9][0-9]*', ref)
            and kind in {'raw', 'edited', 'confirmed'}
            for ref, kind in context['ocr_revision_kinds'].items()), 'Saved-slot scope differs')
    resolve_evidence(catalog, allowed_span_refs, allowed_span_refs)
    if journal is not None:
        _need(type(journal) is dict and set(journal) == _DESCRIPTOR,
            'Saved-slot descriptor shape differs')
        _safe(journal)
        binding = JournalBinding.model_validate(journal['binding']).model_dump()
        _need(journal['schema'] == 'report-evidence-journal-descriptor-v1'
            and journal['descriptor_hash'] == digest({key: value for key, value in journal.items() if key != 'descriptor_hash'})
            and journal['binding_hash'] == digest(binding)
            and _bytes(binding) == _bytes(journal['binding'])
            and all(binding[key] == context[key] for key in
                ('actor_id', 'workspace_id', 'draft_id', 'stage', 'source_hash', 'catalog_hash', 'messages_hash')),
            'Saved-slot descriptor scope differs')
    _need((availability == 'missing') is (journal is None), 'Saved-slot availability differs')
    if availability == 'quarantined':
        _need(journal['consumable'] is False and metadata is None, 'Saved-slot quarantine differs')
    elif journal is not None:
        _need(journal['consumable'] is True, 'Saved-slot original is quarantined')
    if metadata is not None:
        _need(type(metadata) is dict and set(metadata) == set(METADATA_KEYS),
            'Saved-slot metadata shape differs')
        _safe(metadata)
        _need(journal is not None and metadata['delivery'] == _delivery(context['generation_schema'])
            and metadata['response_body_sha256'] == journal['wire_sha256']
            and metadata['response_body_bytes'] == journal['wire_bytes'], 'Saved-slot original HTTP differs')
    _need(availability in {'missing', 'quarantined'} or metadata is not None,
        'Saved-slot original metadata missing')


def project_saved_facts_v2(context, catalog, allowed_span_refs, wire_value=None, *,
                           journal=None, availability='available', tool_metadata=None):
    """Pure safe original-field display. Public hashes confer no adoption rights.

    None in a field means absent/unsupported DISPLAY value; it never repairs
    the journal. Unencodable parsed wire gets no canonical value hash, retaining
    the original verified arguments-byte identity and a visible protocol issue.
    """
    _scope(context, catalog, allowed_span_refs, journal, availability, tool_metadata)
    facts, issues, origin, valid = [], [], None, None
    if availability == 'not_json':
        valid = False
        issues.append(_issue('/completion', 'response_or_arguments_invalid', _MESSAGES['not_json']))
    if availability == 'available':
        present = _slots(wire_value)
        if present is None:
            availability, valid = 'unsupported_structure', False
            issues.append(_issue('/slots', 'unsupported_structure', _MESSAGES[availability]))
        else:
            _need(type(tool_metadata['arguments_sha256']) is str
                and _HASH.fullmatch(tool_metadata['arguments_sha256'])
                and type(tool_metadata['arguments_bytes']) is int
                and 1 <= tool_metadata['arguments_bytes'] <= 256 * 1024,
                'Saved-slot original argument identity missing')
            value_hash = None
            try:
                _safe(wire_value); value_hash = digest(wire_value)
            except (ValueError, TypeError, UnicodeError, RecursionError):
                issues.append(_issue('/', 'unsupported_wire_values', '原参数含不能安全编码的值；未另造原值哈希，原参数字节身份仍保留。'))
            origin = {'wire_schema': WIRE_SCHEMA, 'codec': DISPLAY_CODEC,
                'wire_value_hash': value_hash, 'arguments_sha256': tool_metadata['arguments_sha256'],
                'arguments_bytes': tool_metadata['arguments_bytes'],
                'slot_positions': [position for position, _, _ in present]}
            try:
                checked = MapSlotsWireV5.model_validate(wire_value).model_dump()
                _need(_bytes(checked) == _bytes(wire_value), 'Saved-slot protocol changed')
                validate_map_result({'schema': 'report-evidence-map-v1', 'claims': [
                    {'key': f'display_slot_{position}', **copy.deepcopy(claim)}
                    for position, _, claim in present]}, catalog, allowed_span_refs)
                valid = _completion(tool_metadata, context['generation_schema']) and value_hash is not None
            except ValidationError as error:
                valid = False; issues.extend(_errors(error))
            except (ValueError, TypeError, UnicodeError, RecursionError):
                valid = False; issues.append(_issue('/', 'protocol_validation'))
            if not _completion(tool_metadata, context['generation_schema']):
                valid = False
                issues.append(_issue('/completion', 'response_incomplete', _MESSAGES['not_json']))
            allowed = set(allowed_span_refs)
            for position, name, claim in present:
                fields, field_issues = _display_fields(claim)
                references = []
                if fields['span_refs'] is not None and len(fields['span_refs']) <= 16:
                    for ref in fields['span_refs']:
                        state = ('invalid' if not _SPAN.fullmatch(ref) else 'outside_batch' if ref not in allowed
                            and ref in catalog['index'] else 'missing' if ref not in catalog['index'] else 'resolved')
                        item = {'span_ref': ref, 'state': state, 'source_ref': None, 'start': None, 'end': None,
                            'text': None, 'source_kind': None, 'source_number': None, 'revision_kind': None}
                        if state == 'resolved':
                            span = catalog['index'][ref]; source = span['source_ref']; ocr = source.startswith('o:')
                            revision = context['ocr_revision_kinds'].get(source) if ocr else None
                            _need(not ocr or revision is not None, 'Saved-slot OCR revision missing')
                            item.update(source_ref=source, start=span['start'], end=span['end'], text=span['text'],
                                source_kind='ocr_page' if ocr else 'word_block', source_number=int(source[3:]), revision_kind=revision)
                        else:
                            field_issues.append(_issue('/span_refs', 'reference_' + state,
                                '引用未在本批冻结证据中正确解析；没有替换或补造原文。'))
                        references.append(item)
                path = '/slots/' + name + '/claim'
                facts.append({'position': position, 'slot_name': name,
                    'display_id': 'sf_' + digest({'schema': DISPLAY_IDS,
                        'binding_hash': journal['binding_hash'],
                        'arguments_sha256': tool_metadata['arguments_sha256'], 'path': path}),
                    'protocol_key': None, 'fields': fields, 'field_constraints_valid': not field_issues,
                    'issues': field_issues, 'references': references, 'meaning_review_status': 'unreviewed'})
    result = {'schema': SCHEMA,
        **{key: copy.deepcopy(value) for key, value in context.items() if key != 'ocr_revision_kinds'},
        'journal': copy.deepcopy(journal), 'availability': availability, 'message': _MESSAGES[availability],
        'original_protocol_valid': valid, 'protocol_issues': issues, 'facts': facts,
        'slot_origin': origin, 'tool_metadata': copy.deepcopy(tool_metadata),
        'meaning_review_status': 'unreviewed', 'eligible_for_reduce': False, 'can_apply': False, 'can_publish': False}
    result['view_hash'] = digest(result)
    _need(len(_bytes(result)) <= MAX_VIEW_BYTES, 'Saved-slot view exceeds capacity')
    return result


def read_saved_facts(pipeline, row, key, context):
    """Read only a Map-v1 journal from an already-owned/check_row-verified row."""
    _need(context['generation_schema'] in {GENERATION, GENERATION_V6, GENERATION_V7} and re.fullmatch(_STAGE, key),
        'Saved-slot route requires a V5 Map')
    stage = pipeline._stage(row, key); preview = row.data['preview']
    catalog = pipeline._catalog(preview); allowed = pipeline._authorized(row, key)['span_refs']
    if stage['reserved_call_id'] is None:
        return project_saved_facts_v2(context, catalog, allowed, availability='missing')
    adapter = pipeline._journal_for(row, key)
    binding = pipeline.journal_binding(row, key, stage['reserved_call_id'])
    _need(binding['schema'] == 'report-evidence-journal-binding-v1', 'Saved-slot Map journal namespace differs')
    root = pipeline._stage_journal_root(row, key)
    try:
        descriptor = adapter.read_descriptor(root, binding)
    except adapter.JournalError as error:
        if stage['journal'] is None and error.code == 'journal_missing_or_unsafe':
            try:
                with adapter._stage(root, binding):
                    pass
            except FileNotFoundError:
                return project_saved_facts_v2(context, catalog, allowed, availability='missing')
        raise ValueError('Saved-slot journal integrity differs') from None
    _need(stage['journal'] is None or _bytes(stage['journal']) == _bytes(descriptor),
        'Saved-slot original descriptor changed')
    if not descriptor['consumable']:
        return project_saved_facts_v2(context, catalog, allowed,
            journal=descriptor, availability='quarantined')
    loaded = adapter.read_response(root, binding, descriptor)
    body, inspected = pipeline.v5.inspect_response(pipeline, row, key, loaded, descriptor)
    metadata = inspected['metadata']
    try:
        if context['generation_schema'] == GENERATION_V7:
            # Display the original five fields even when local constraints
            # fail. Never use the typed adoption parser to hide a bad field.
            from .report_evidence_strict_map import _loads
            _need(metadata['envelope_valid'] is True, 'Native original completion invalid')
            wire = _loads(inspected['arguments'])
        elif context['generation_schema'] == GENERATION_V6:
            from .report_evidence_structured_tool_v3 import parse_arguments as selected_parser
            wire = selected_parser(body, loaded['receipt']['http_status'], 'map-v6')
        else:
            wire = parse_arguments(body, loaded['receipt']['http_status'], 'map-v5')
    except ValueError:
        return project_saved_facts_v2(context, catalog, allowed, journal=descriptor,
            availability='not_json', tool_metadata=metadata)
    return project_saved_facts_v2(context, catalog, allowed, wire,
        journal=descriptor, tool_metadata=metadata)
