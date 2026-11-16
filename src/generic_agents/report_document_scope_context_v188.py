"""Cold DOCX paragraph/outline context for a new explicit scope, not a grant.

The real source bytes must reproduce the full manifest and reading. Every Word
catalog span is then checked against its exact accepted paragraph coordinates.
Old reading/catalog/allowed refs remain unchanged; no heading, span identifier,
substring match, source permission or external dispatch scope is guessed.
"""
import copy
import hashlib

from . import docx_structure_manifest_v1 as structures
from . import report_evidence_plan as evidence
from .contracts import digest
from .typed_payload_codec_v131 import canonical_json


SCHEMA = 'report-document-scope-context-v188'
MAX_CONTEXT_BYTES = structures.MAX_CONTEXT_BYTES
ERROR = 'Word原件、完整结构或引用坐标无法核对；未猜测、截断或更改旧范围'
_FLAGS = {'source_authority_verified': False, 'semantic_quality_verified': False,
    'external_dispatch_allowed': False, 'whole_report_verified': False,
    'full_catalog_provenance_verified': False, 'can_apply': False,
    'can_publish': False, 'can_execute': False}


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


def _bytes(value):
    return canonical_json(value).encode('utf-8', errors='strict')


def _paragraph(record, spans_by_source):
    spans = spans_by_source.get(record['legacy_ref'], [])
    return {'paragraph_ref': record['ref'], 'legacy_ref': record['legacy_ref'],
        'text': record['text'], 'text_sha256': record['text_sha256'], 'kind': record['kind'],
        'location': copy.deepcopy(record['location']), 'accepted_xml_path': record['accepted_xml_path'],
        'scope': copy.deepcopy(record['scope']), 'outline': copy.deepcopy(record['outline']),
        'context_issues': copy.deepcopy(record['context_issues']),
        'parent_ref': record['parent_ref'], 'parent_status': record['parent_status'],
        'ancestor_refs': copy.deepcopy(record['ancestor_refs']),
        'catalog_status': 'complete' if spans else 'not_catalogued',
        'span_refs': [item['span_ref'] for item in spans], 'spans': copy.deepcopy(spans)}


def build_context(content, *, reading, manifest, catalog, selected_span_refs, max_bytes):
    """Resolve newly selected Word context only after actual-byte cold checks.

    ``catalog_source_hash`` is the caller's existing combined source identity;
    it is deliberately not equated with the SHA of this one DOCX. OCR rows in
    a mixed catalog are integrity-checked by its public validator, not granted
    provenance here. A selected OCR row cannot become Word section context.
    """
    try:
        _need(type(content) is bytes and type(max_bytes) is int and 1 <= max_bytes <= MAX_CONTEXT_BYTES)
        # A manifest's self-hash alone is never sufficient. This public gate
        # rereads actual inert DOCX bytes and recomputes both accepted views.
        actual = structures.check_manifest(manifest, content, reading=reading)
        checked = evidence.check_evidence_source(catalog)
        _need(type(selected_span_refs) is list and 1 <= len(selected_span_refs) <= len(checked['index']))
        all_refs = list(checked['index'])
        selected = evidence.resolve_evidence(checked, selected_span_refs, all_refs)
        by_ref = {item['ref']: item for item in actual['paragraphs']}
        by_legacy = {item['legacy_ref']: item for item in actual['paragraphs'] if item['legacy_ref'] is not None}
        _need(len(by_ref) == len(actual['paragraphs'])
            and len(by_legacy) == sum(item['legacy_ref'] is not None for item in actual['paragraphs']))

        # Group actual resolver identities; never synthesize /s1, split a ref,
        # use a heading's text as an identifier or locate its first substring.
        word_refs = [ref for ref, item in checked['index'].items() if item['source_ref'].startswith('w:')]
        all_word_spans = evidence.resolve_evidence(checked, word_refs, all_refs)
        spans_by_source = {}
        for span in all_word_spans:
            record = by_legacy.get(span['source_ref'])
            _need(record is not None and type(span['start']) is int and type(span['end']) is int
                and 0 <= span['start'] < span['end'] <= len(record['text'])
                and span['text'] == record['text'][span['start']:span['end']])
            spans_by_source.setdefault(span['source_ref'], []).append(span)
        for source_ref, spans in spans_by_source.items():
            spans.sort(key=lambda item: (item['start'], item['end'], item['span_ref']))
            # A self-resealed prefix of a real paragraph is not a complete
            # catalog block, even when every remaining slice matches bytes.
            _need(''.join(item['text'] for item in spans) == by_legacy[source_ref]['text'])

        matches, paragraphs, headings, unknown = [], [], [], []
        seen_paragraphs, seen_headings = set(), set()
        for span in selected:
            record = by_legacy.get(span['source_ref'])
            _need(record is not None and span['source_ref'].startswith('w:'))
            if record['ref'] not in seen_paragraphs:
                paragraphs.append(_paragraph(record, spans_by_source))
                seen_paragraphs.add(record['ref'])
            context_refs = []
            if record['parent_status'] != 'explicit':
                unknown.append({'span_ref': span['span_ref'], 'paragraph_ref': record['ref'],
                    'reason': record['parent_status']})
            else:
                _need(bool(record['ancestor_refs']) and record['parent_ref'] == record['ancestor_refs'][-1])
                for ancestor_ref in record['ancestor_refs']:
                    ancestor = by_ref.get(ancestor_ref)
                    _need(ancestor is not None and ancestor['outline']['status'] == 'heading'
                        and not ancestor['context_issues'] and ancestor['scope'] == record['scope'])
                    context_refs.append(ancestor_ref)
                    if ancestor_ref not in seen_headings:
                        headings.append(_paragraph(ancestor, spans_by_source))
                        seen_headings.add(ancestor_ref)
                    if not spans_by_source.get(ancestor['legacy_ref']):
                        unknown.append({'span_ref': span['span_ref'], 'paragraph_ref': ancestor_ref,
                            'reason': 'heading_not_in_catalog'})
            matches.append({**copy.deepcopy(span), 'paragraph_ref': record['ref'],
                'paragraph_text_sha256': record['text_sha256'], 'parent_ref': record['parent_ref'],
                'parent_status': record['parent_status'], 'ancestor_refs': copy.deepcopy(record['ancestor_refs']),
                'context_heading_refs': context_refs})
        value = {'schema': SCHEMA, 'source_sha256': hashlib.sha256(content).hexdigest(),
            'normalized_sha256': actual['normalized_sha256'], 'reading_hash': actual['reading_hash'],
            'manifest_hash': actual['manifest_hash'], 'catalog_hash': checked['descriptor']['catalog_hash'],
            'catalog_source_hash': checked['descriptor']['source_hash'],
            'selected_span_refs': copy.deepcopy(selected_span_refs), 'selected_spans': matches,
            'paragraphs': paragraphs, 'headings': headings, 'unknown': unknown,
            'coordinate_space': actual['coordinate_space'], 'hierarchy_policy': actual['hierarchy_policy'],
            'explicit_context_only': True, **_FLAGS}
        _need(value['source_sha256'] == actual['source_sha256'])
        value['context_hash'] = digest(value)
        if len(_bytes(value)) > max_bytes:
            raise evidence.EvidenceCapacityExceeded('完整Word章节上下文超过本次容量；未截断或扩大发送范围')
        return value
    except evidence.EvidenceCapacityExceeded:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None
