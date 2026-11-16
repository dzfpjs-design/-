"""Pure complete-source evidence planning; no model, database or formal approval.

Only ``descriptor`` is persistent catalog metadata. ``index`` is a rebuildable
in-memory text resolver. Source hashes bind the caller's already frozen view;
this module neither reads newer OCR revisions nor reclassifies raw evidence.
"""
import copy
import hashlib
import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import digest
from . import evidence_request_scope_v191 as _request_scope


SOURCE = 'report-evidence-source-v1'
PLAN = 'report-evidence-read-plan-v1'
PLAN_V2 = 'report-evidence-read-plan-v2'
PAYLOAD = 'report-evidence-read-payload-v1'
POLICY = 'contiguous-codepoint-spans-v1'
_HASH = re.compile(r'^[a-f0-9]{64}$')
_REF = re.compile(r'^[wo]:p[1-9][0-9]*$')


class EvidenceCapacityExceeded(ValueError):
    """Only this explicit capacity failure permits a smaller complete group."""


def _safe(value, depth=0):
    if depth > 48:
        raise ValueError('Evidence JSON is too deep')
    if isinstance(value, str):
        value.encode('utf-8', errors='strict')
        if '\x00' in value:
            raise ValueError('Evidence JSON contains NUL')
    elif isinstance(value, dict):
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError('Evidence JSON keys must be strings')
            _safe(key, depth + 1); _safe(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _safe(item, depth + 1)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('Evidence JSON contains a nonfinite number')
    elif value is not None and type(value) not in (int, bool):
        raise ValueError('Evidence value is not JSON')
    return value


def _integer(value, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError('Evidence integer is invalid')
    return value


def _hash(value):
    if type(value) is not str or not _HASH.fullmatch(value):
        raise ValueError('Evidence hash is invalid')
    return value


def _text_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _seal(value, field):
    return {**value, field: digest(value)}


def build_evidence_source(source_hash, ordered_sources, max_span_chars=240):
    """Split exact Python codepoints, preserving whitespace, punctuation and empties.

    Word ``w:pN`` denotes a body block, never a PDF page. Empty blocks have no
    model-readable span; their explicit coverage lives in ``empty_sources``.
    """
    _hash(source_hash); _integer(max_span_chars)
    if max_span_chars > 250:
        raise ValueError('Evidence spans exceed the unchanged quote-length guard')
    if type(ordered_sources) is not list:
        raise ValueError('Evidence sources must be an ordered list')
    blocks, index, seen, empty, span_index = [], {}, set(), [], []
    for pair in ordered_sources:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError('Evidence source must contain reference and text')
        ref, text = pair
        if type(ref) is not str or not _REF.fullmatch(ref) or ref in seen or type(text) is not str:
            raise ValueError('Evidence source reference or text is invalid')
        _safe(text); seen.add(ref)
        for number, start in enumerate(range(0, len(text), max_span_chars), 1):
            end = min(start + max_span_chars, len(text)); span_ref = f'{ref}/s{number}'
            part = text[start:end]
            item = {'source_ref': ref, 'start': start, 'end': end, 'text_hash': _text_hash(part)}
            span_index.append({'span_ref': span_ref, **item})
            index[span_ref] = {**item, 'text': part}
        if not text:
            empty.append(ref)
        blocks.append([ref, text])
    descriptor = {'schema': SOURCE, 'source_hash': source_hash, 'source_blocks_hash': digest(blocks),
        'policy': POLICY, 'max_span_chars': max_span_chars, 'ordered_source_refs': [ref for ref, _ in blocks],
        'counts': {'source_blocks': len(blocks), 'word_blocks': sum(ref[0] == 'w' for ref, _ in blocks),
            'ocr_pages': sum(ref[0] == 'o' for ref, _ in blocks), 'spans': len(index),
            'codepoints': sum(len(text) for _, text in blocks)}, 'empty_sources': empty}
    # The derived index digest is covered, but not duplicated into persisted
    # metadata. Rebuilding the exact frozen blocks reproduces this hash.
    descriptor['catalog_hash'] = digest({**descriptor, 'span_index_hash': digest(span_index)})
    return {'descriptor': descriptor, 'index': index}


def check_evidence_source(catalog):
    """Complete validation, with bounded same-request pure-result reuse only."""
    return _request_scope._check_catalog(catalog, _check_evidence_source_uncached)


def validate_evidence_source(catalog):
    """Validate all actual content without allocating a discarded catalog copy.

    This has the same complete integrity gate as check_evidence_source. It
    returns no authority or reusable handle. Resolution below still selects
    each caller's exact allowed references from its actual input catalog.
    """
    _request_scope._check_catalog(catalog, _check_evidence_source_uncached, detach=False)


def _check_evidence_source_uncached(catalog):
    _safe(catalog)
    if type(catalog) is not dict or set(catalog) != {'descriptor', 'index'}:
        raise ValueError('Evidence catalog shape differs')
    descriptor, index = catalog['descriptor'], catalog['index']
    if (type(descriptor) is not dict or set(descriptor) != {'schema', 'source_hash', 'source_blocks_hash',
            'policy', 'max_span_chars', 'ordered_source_refs', 'counts', 'empty_sources', 'catalog_hash'}
            or descriptor['schema'] != SOURCE or descriptor['policy'] != POLICY or type(index) is not dict
            or type(descriptor['ordered_source_refs']) is not list):
        raise ValueError('Evidence catalog integrity differs')
    _hash(descriptor['source_hash']); limit = _integer(descriptor['max_span_chars'])
    if type(descriptor['counts']) is not dict or set(descriptor['counts']) != {
            'source_blocks', 'word_blocks', 'ocr_pages', 'spans', 'codepoints'}:
        raise ValueError('Evidence catalog counts differ')
    for count in descriptor['counts'].values():
        _integer(count, 0)
    seen, span_ids, blocks = set(), set(), []
    for ref in descriptor['ordered_source_refs']:
        if type(ref) is not str or not _REF.fullmatch(ref) or ref in seen:
            raise ValueError('Evidence source identity differs')
        seen.add(ref); parts, start, number = [], 0, 1
        while f'{ref}/s{number}' in index:
            span_ref = f'{ref}/s{number}'
            item = index[span_ref]
            if (type(item) is not dict or set(item) != {'source_ref', 'start', 'end', 'text', 'text_hash'}
                    or type(item['text']) is not str or not item['text'] or len(item['text']) > limit
                    or item['source_ref'] != ref or type(item['start']) is not int or item['start'] != start
                    or type(item['end']) is not int or item['end'] != start + len(item['text'])
                    or item['text_hash'] != _text_hash(item['text'])):
                raise ValueError('Evidence span integrity differs')
            parts.append(item['text']); span_ids.add(span_ref); start = item['end']; number += 1
        blocks.append([ref, ''.join(parts)])
    if set(index) != span_ids or build_evidence_source(descriptor['source_hash'], blocks, limit) != catalog:
        raise ValueError('Evidence catalog reconstruction differs')
    return catalog


def _resolve(catalog, span_refs):
    if type(span_refs) is not list or any(type(ref) is not str for ref in span_refs) or len(set(span_refs)) != len(span_refs):
        raise ValueError('Evidence span references are invalid')
    if any(ref not in catalog['index'] for ref in span_refs):
        raise ValueError('Evidence span is outside this source')
    return [{'span_ref': ref, **copy.deepcopy(catalog['index'][ref])} for ref in span_refs]


def _ordered_refs(catalog, source_ref):
    refs, number = [], 1
    while f'{source_ref}/s{number}' in catalog['index']:
        refs.append(f'{source_ref}/s{number}'); number += 1
    return refs


def resolve_evidence(catalog, span_refs, allowed_refs):
    """Derive original text; never repair or normalize a model's statement/quote."""
    validate_evidence_source(catalog)
    _resolve(catalog, allowed_refs)
    if type(span_refs) is not list or any(ref not in allowed_refs for ref in span_refs):
        raise ValueError('Evidence span is outside this batch')
    return _resolve(catalog, span_refs)


def resolve_span(catalog, ref, allowed_refs):
    """Resolve one original fragment only with the explicit current batch scope."""
    return resolve_evidence(catalog, [ref], allowed_refs)[0]


def evidence_payload(catalog, span_refs):
    validate_evidence_source(catalog)
    return _payload(catalog, span_refs)


def _payload(catalog, span_refs):
    descriptor = catalog['descriptor']
    return {'schema': PAYLOAD, 'source_hash': descriptor['source_hash'], 'catalog_hash': descriptor['catalog_hash'],
        'spans': [[item['span_ref'], item['text']] for item in _resolve(catalog, span_refs)]}


def _prepare(catalog, refs, callback, maximum, plan_version=PLAN):
    payload = _payload(catalog, refs)
    result = callback(copy.deepcopy(payload))
    fields = {'messages_hash', 'input_summary'} | ({'request_hash'} if plan_version == PLAN_V2 else set())
    if type(result) is not dict or set(result) != fields:
        raise ValueError('Evidence prepare result differs')
    _hash(result['messages_hash']); summary = result['input_summary']; _safe(summary)
    if plan_version == PLAN_V2:
        _hash(result['request_hash'])
    byte_field = 'prepared_request_bytes' if plan_version == PLAN_V2 else 'prepared_messages_bytes'
    if type(summary) is not dict or set(summary) != {byte_field, 'token_bound', 'max_input_bytes'}:
        raise ValueError('Evidence prepare summary differs')
    count = _integer(summary[byte_field])
    if _integer(summary['token_bound']) < count or _integer(summary['max_input_bytes']) < count:
        raise ValueError('Evidence prepare measurement differs')
    if count > maximum:
        raise EvidenceCapacityExceeded('Prepared evidence exceeds the explicit per-call limit')
    return digest(payload), copy.deepcopy(result)


def plan_evidence_reads(catalog, prepare_callback, max_prepared_bytes, max_chunks=24, *, plan_version=PLAN):
    """Prepare whole-view groups first, splitting only on explicit capacity.

    Split at real block boundaries whenever possible; split a single long
    block only along its pre-frozen contiguous span boundaries. No hidden calls
    are authorized: callback invocations only prepare messages, never run them.
    """
    validate_evidence_source(catalog); _integer(max_prepared_bytes); _integer(max_chunks)
    if plan_version not in {PLAN, PLAN_V2}:
        raise ValueError('Unknown evidence planning protocol')
    if max_chunks > 24:
        raise ValueError('Evidence plan cannot authorize more than 24 chunks')
    descriptor = catalog['descriptor']; chunks = []
    def visit(groups):
        refs = [ref for group in groups for ref in group]
        try:
            payload_hash, prepared = _prepare(catalog, refs, prepare_callback, max_prepared_bytes, plan_version)
        except EvidenceCapacityExceeded:
            if len(groups) > 1:
                cut = len(groups) // 2; left, right = groups[:cut], groups[cut:]
            elif len(refs) > 1:
                cut = len(refs) // 2; left, right = [refs[:cut]], [refs[cut:]]
            else:
                raise EvidenceCapacityExceeded('A single frozen evidence span cannot fit') from None
            visit(left); visit(right)
            return
        if len(chunks) >= max_chunks:
            raise EvidenceCapacityExceeded('Complete evidence exceeds the explicit chunk count')
        source_refs = list(dict.fromkeys(catalog['index'][ref]['source_ref'] for ref in refs))
        chunks.append(_seal({'chunk_id': f'map-{len(chunks) + 1}', 'span_refs': refs, 'source_refs': source_refs,
            'payload_hash': payload_hash, **prepared}, 'chunk_hash'))
    groups, view = [], None
    for source_ref in descriptor['ordered_source_refs']:
        refs = _ordered_refs(catalog, source_ref)
        if not refs:
            continue
        if groups and source_ref[0] != view:
            visit(groups); groups = []
        view = source_ref[0]; groups.append(refs)
    if groups:
        visit(groups)
    return _seal({'schema': plan_version, 'source_hash': descriptor['source_hash'], 'catalog_hash': descriptor['catalog_hash'],
        'max_prepared_bytes': max_prepared_bytes, 'max_chunks': max_chunks,
        'empty_sources': copy.deepcopy(descriptor['empty_sources']), 'chunks': chunks}, 'plan_hash')


def check_plan(plan, catalog, prepare_callback, *, plan_version=PLAN):
    """Rebuild every payload and real preparation, then prove exact coverage."""
    validate_evidence_source(catalog); _safe(plan); descriptor = catalog['descriptor']
    if plan_version not in {PLAN, PLAN_V2}:
        raise ValueError('Unknown evidence planning protocol')
    if (type(plan) is not dict or set(plan) != {'schema', 'source_hash', 'catalog_hash', 'max_prepared_bytes',
            'max_chunks', 'empty_sources', 'chunks', 'plan_hash'} or plan['schema'] != plan_version
            or plan['source_hash'] != descriptor['source_hash'] or plan['catalog_hash'] != descriptor['catalog_hash']
            or plan['empty_sources'] != descriptor['empty_sources']
            or plan['plan_hash'] != digest({k: v for k, v in plan.items() if k != 'plan_hash'})
            or type(plan['chunks']) is not list):
        raise ValueError('Evidence plan integrity differs')
    maximum = _integer(plan['max_prepared_bytes']); max_chunks = _integer(plan['max_chunks'])
    if max_chunks > 24:
        raise ValueError('Evidence plan cannot authorize more than 24 chunks')
    if len(plan['chunks']) > max_chunks:
        raise EvidenceCapacityExceeded('Evidence plan exceeds chunk count')
    covered = []
    for number, chunk in enumerate(plan['chunks'], 1):
        if (type(chunk) is not dict or set(chunk) != {'chunk_id', 'span_refs', 'source_refs', 'payload_hash',
                'messages_hash', 'input_summary', 'chunk_hash'} | ({'request_hash'} if plan_version == PLAN_V2 else set()) or chunk['chunk_id'] != f'map-{number}'
                or chunk['chunk_hash'] != digest({k: v for k, v in chunk.items() if k != 'chunk_hash'})):
            raise ValueError('Evidence chunk integrity differs')
        resolved = _resolve(catalog, chunk['span_refs'])
        if (not resolved or chunk['source_refs'] != list(dict.fromkeys(item['source_ref'] for item in resolved))
                or len({item['source_ref'][0] for item in resolved}) != 1):
            raise ValueError('Evidence chunk source/view differs')
        payload_hash, prepared = _prepare(catalog, chunk['span_refs'], prepare_callback, maximum, plan_version)
        if payload_hash != chunk['payload_hash'] or any(prepared[key] != chunk[key] for key in prepared):
            raise ValueError('Evidence prepared messages differ')
        covered.extend(chunk['span_refs'])
    expected = [ref for source in descriptor['ordered_source_refs'] for ref in _ordered_refs(catalog, source)]
    if covered != expected:
        raise ValueError('Evidence plan omits, duplicates or reorders spans')
    return plan


class _Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    @model_validator(mode='before')
    @classmethod
    def safe_json(cls, value):
        return _safe(value)


class EvidenceClaim(_Closed):
    key: Annotated[str, Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')]
    kind: Literal['职责', '来源', '治理', '互动', '业务', '步骤', '条件', '协同', '对象', '关系', '冲突', '其他']
    statement: str = Field(min_length=1, max_length=2000)
    span_refs: list[Annotated[str, Field(pattern=r'^[wo]:p[1-9][0-9]*/s[1-9][0-9]*$')]] = Field(max_length=16)
    status: Literal['explicit', 'inferred', 'unknown']
    explanation: str = Field(max_length=1000)

    @model_validator(mode='after')
    def evidence_status(self):
        if not self.statement.strip() or len(set(self.span_refs)) != len(self.span_refs):
            raise ValueError('Claim statement or duplicate evidence is invalid')
        if self.status == 'explicit' and not self.span_refs:
            raise ValueError('Explicit claim requires current-batch evidence')
        if self.status != 'explicit' and not self.explanation.strip():
            raise ValueError('Inferred or unknown claim requires an explanation')
        return self


class MapResult(_Closed):
    schema: Literal['report-evidence-map-v1']
    claims: list[EvidenceClaim] = Field(max_length=80)


def validate_map_result(value, catalog, allowed_span_refs):
    """Validate original model declarations unchanged; evidence is not an NLU score."""
    validate_evidence_source(catalog); _resolve(catalog, allowed_span_refs)
    result = MapResult.model_validate(value).model_dump()
    keys = [claim['key'] for claim in result['claims']]
    if len(keys) != len(set(keys)):
        raise ValueError('Evidence claim keys repeat')
    allowed = set(allowed_span_refs)
    if any(set(claim['span_refs']) - allowed for claim in result['claims']):
        raise ValueError('Claim cites a span not sent in this batch')
    return result
