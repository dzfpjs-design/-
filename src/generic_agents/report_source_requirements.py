"""Pure source-requirement review records; no IO, authorization or semantic seal.

Callers authenticate and persist the original source, already validated effective
candidate.result and review receipts. These hashes prove deterministic comparison,
not who reviewed a requirement or whether its disposition is true. A record does
not complete a source unit, and shared references do not prove obligation coverage.
No original Candidate/manual field, validator, capacity or saved answer is changed.

Pointers are relative to candidate.result and match exact existing JSON names;
there are no aliases, normalization, fuzzy matches, array appends or repairs.
Source unit IDs are the existing frozen block references, never generated IDs.
Historical checked records must come from the caller's authenticated saved state:
an unkeyed check_hash is an integrity check, not a signature or a permission.
"""
import copy
import hashlib
import re

from .contracts import digest
from .report_evidence_plan import check_evidence_source, _safe


SOURCE_UNITS = 'report-source-units-v1'
SOURCE_UNIT = 'report-source-unit-v1'
CHECK = 'report-source-requirement-check-v1'
CHECKED = 'report-source-requirement-checked-v1'
TARGET_ROOTS = ('domain', 'businesses', 'objects', 'relationships', 'global_claims', 'questions')
DISPOSITIONS = ('represented', 'missing', 'incorrect', 'out_of_scope', 'needs_evidence')
_HASH = re.compile(r'^[a-f0-9]{64}$')
_UNIT = re.compile(r'^[wo]:p[1-9][0-9]*$')
_INDEX = re.compile(r'^(0|[1-9][0-9]*)$')
_RECORD_KEYS = {'schema', 'requirement', 'source_unit_ids', 'disposition', 'reason', 'targets',
                'candidate_hash', 'source_hash', 'catalog_hash'}


def _need(value, code):
    if not value: raise ValueError(code)


def _hash(value):
    _need(type(value) is str and _HASH.fullmatch(value), 'requirement_hash_invalid')
    return value


def build_source_units(catalog):
    """One exact original block per unit, including empty and whitespace blocks."""
    check_evidence_source(catalog)
    descriptor = catalog['descriptor']; units = []
    for source_ref in descriptor['ordered_source_refs']:
        spans, text, number = [], [], 1
        while (span_ref := f'{source_ref}/s{number}') in catalog['index']:
            item = catalog['index'][span_ref]
            spans.append({'span_ref': span_ref, 'start': item['start'], 'end': item['end'], 'text_hash': item['text_hash']})
            text.append(item['text']); number += 1
        original = ''.join(text)
        unit = {'schema': SOURCE_UNIT, 'source_unit_id': source_ref,
            'source_hash': descriptor['source_hash'], 'catalog_hash': descriptor['catalog_hash'],
            'spans': spans, 'codepoints': len(original), 'is_empty': not original,
            'text_hash': hashlib.sha256(original.encode('utf-8', 'strict')).hexdigest()}
        units.append({**unit, 'unit_hash': digest(unit)})
    return {'schema': SOURCE_UNITS, 'source_hash': descriptor['source_hash'],
        'catalog_hash': descriptor['catalog_hash'], 'units': units, 'units_hash': digest(units)}


def _tokens(pointer):
    _need(type(pointer) is str and pointer.startswith('/'), 'requirement_pointer_invalid')
    _safe(pointer); tokens = []
    for encoded in pointer[1:].split('/'):
        position, decoded = 0, []
        while position < len(encoded):
            char = encoded[position]
            if char == '~':
                _need(position + 1 < len(encoded) and encoded[position + 1] in '01', 'requirement_pointer_escape')
                decoded.append('~' if encoded[position + 1] == '0' else '/'); position += 2
            else:
                decoded.append(char); position += 1
        tokens.append(''.join(decoded))
    _need(tokens[0] in TARGET_ROOTS, 'requirement_pointer_root')
    return tokens


def _target(candidate_result, pointer):
    value = candidate_result
    for token in _tokens(pointer):
        if type(value) is dict:
            _need(token in value, 'requirement_pointer_missing')
            value = value[token]
        elif type(value) is list:
            _need(_INDEX.fullmatch(token) and len(token) <= len(str(len(value))), 'requirement_pointer_array_index')
            index = int(token)
            _need(index < len(value), 'requirement_pointer_array_bounds'); value = value[index]
        else:
            raise ValueError('requirement_pointer_continues_through_scalar')
    return value


def _candidate(candidate_result, candidate_hash):
    if candidate_result is None:
        _need(candidate_hash is None, 'requirement_candidate_hash_without_value')
        return
    _need(type(candidate_result) is dict, 'requirement_validated_candidate_required')
    _safe(candidate_result); _hash(candidate_hash)
    _need(digest(candidate_result) == candidate_hash, 'requirement_candidate_hash_differs')


def _record(value):
    _need(type(value) is dict and set(value) == _RECORD_KEYS and value['schema'] == CHECK,
          'requirement_record_shape')
    _safe(value)
    for key in ('requirement', 'reason'):
        _need(type(value[key]) is str and 0 < len(value[key]) <= 2000 and value[key].strip(),
              'requirement_nonblank_text')
    units, targets = value['source_unit_ids'], value['targets']
    _need(type(units) is list and 1 <= len(units) <= 20
        and all(type(unit) is str and _UNIT.fullmatch(unit) for unit in units)
        and len(set(units)) == len(units), 'requirement_source_units_invalid')
    _need(type(targets) is list and len(targets) <= 24
        and all(type(pointer) is str for pointer in targets)
        and len(set(targets)) == len(targets), 'requirement_targets_invalid')
    for pointer in targets: _tokens(pointer)
    disposition = value['disposition']
    _need(type(disposition) is str and disposition in DISPOSITIONS, 'requirement_disposition_invalid')
    _need(disposition not in ('represented', 'incorrect') or targets, 'requirement_target_required')
    _need(disposition != 'out_of_scope' or not targets, 'requirement_out_of_scope_targets')
    _hash(value['source_hash']); _hash(value['catalog_hash'])
    if value['candidate_hash'] is None:
        _need(disposition == 'needs_evidence' and not targets, 'requirement_pending_candidate_only')
    else:
        _hash(value['candidate_hash'])
    return copy.deepcopy(value)


def check_requirement_record(value, *, catalog, candidate_result, candidate_hash):
    """Bind a caller-authored requirement; do not infer an obligation or its truth.

candidate_hash is the digest of the already validated effective result itself,
not the provider body, a receipt, wrapper or a previous model-only result. None
and None allow only needs_evidence with no targets, preserving work before AI.
"""
    record = _record(value); units = build_source_units(catalog)
    _candidate(candidate_result, candidate_hash)
    _need(record['source_hash'] == units['source_hash'] and record['catalog_hash'] == units['catalog_hash'],
          'requirement_record_source_differs')
    _need(record['candidate_hash'] == candidate_hash, 'requirement_record_candidate_differs')
    index = {unit['source_unit_id']: unit for unit in units['units']}
    _need(all(unit in index for unit in record['source_unit_ids']), 'requirement_source_unit_missing')
    source_bindings = [{'source_unit_id': unit, 'unit_hash': index[unit]['unit_hash']} for unit in record['source_unit_ids']]
    target_bindings = [{'pointer': pointer, 'target_value_hash': digest(_target(candidate_result, pointer))}
                       for pointer in record['targets']]
    checked = {'schema': CHECKED, 'record': record, 'record_hash': digest(record),
        'source_unit_bindings': source_bindings, 'target_bindings': target_bindings}
    return {**checked, 'check_hash': digest(checked)}


def _checked(value):
    keys = {'schema', 'record', 'record_hash', 'source_unit_bindings', 'target_bindings', 'check_hash'}
    _need(type(value) is dict and set(value) == keys and value['schema'] == CHECKED, 'requirement_checked_shape')
    _safe(value); _hash(value['record_hash']); _hash(value['check_hash'])
    record = _record(value['record'])
    _need(value['record_hash'] == digest(record)
        and value['check_hash'] == digest({key: item for key, item in value.items() if key != 'check_hash'}),
          'requirement_checked_hash_differs')
    sources, targets = value['source_unit_bindings'], value['target_bindings']
    _need(type(sources) is list and len(sources) == len(record['source_unit_ids'])
        and all(type(item) is dict and set(item) == {'source_unit_id', 'unit_hash'}
            and item['source_unit_id'] == unit for item, unit in zip(sources, record['source_unit_ids'])),
          'requirement_checked_unit_bindings')
    _need(type(targets) is list and len(targets) == len(record['targets'])
        and all(type(item) is dict and set(item) == {'pointer', 'target_value_hash'}
            and item['pointer'] == pointer for item, pointer in zip(targets, record['targets'])),
          'requirement_checked_target_bindings')
    for item in sources: _hash(item['unit_hash'])
    for item in targets: _hash(item['target_value_hash'])
    return record


def requirement_record_status(checked, *, catalog, candidate_result, candidate_hash):
    """Read an intact saved record against current inputs; never mutate history.

Changed source/catalog or ANY changed candidate value/version makes it stale,
even if the cited refs or target values happen to remain identical. Same-value
outer row revisions and caller authority are outside this pure API; the caller
binds those separately in the durable review receipt. Unknown checked schemas
or damaged hashes are errors, not a license to display corrupt history as stale.
"""
    record = _checked(checked); units = build_source_units(catalog)
    _candidate(candidate_result, candidate_hash); reasons = []
    if record['source_hash'] != units['source_hash']: reasons.append('source_changed')
    if record['catalog_hash'] != units['catalog_hash']: reasons.append('catalog_changed')
    if record['candidate_hash'] != candidate_hash: reasons.append('candidate_changed')
    if not reasons:
        rebuilt = check_requirement_record(record, catalog=catalog, candidate_result=candidate_result, candidate_hash=candidate_hash)
        _need(rebuilt == checked, 'requirement_checked_does_not_cold_rebuild')
    index = {unit['source_unit_id']: unit for unit in units['units']}
    if any(item['source_unit_id'] not in index or index[item['source_unit_id']]['unit_hash'] != item['unit_hash']
           for item in checked['source_unit_bindings']):
        reasons.append('source_units_changed')
    target_changed = False
    for item in checked['target_bindings']:
        if candidate_result is None:
            target_changed = True; break
        try: actual = digest(_target(candidate_result, item['pointer']))
        except ValueError:
            target_changed = True; break
        if actual != item['target_value_hash']:
            target_changed = True; break
    if target_changed: reasons.append('target_values_changed')
    status = 'stale' if reasons else ('pending_candidate' if candidate_result is None else 'current')
    return {'status': status, 'reasons': reasons}
