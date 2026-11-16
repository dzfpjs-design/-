"""Pure private human-review values, separate from provider wire/projections.

Requires an authenticated caller and a previously verified original candidate.
The caller supplies the immutable model result, source and accepted review
nonce/revision. This module grants no permissions and does not save or execute.
Human values never carry provider wire hashes, receipts, or model-assigned IDs.
"""
import copy
import json
import re
from typing import Literal
from uuid import UUID

from pydantic import Field

from .contracts import digest
from .report_evidence_semantics import validate_evidence_result
from .report_evidence_wire_v3 import CandidateWireV5, candidate_with_ids, CANDIDATE_PROJECTION
from .report_full_reading_semantics import Closed


MANUAL_VALUE = 'report-evidence-manual-review-v5'
MANUAL_PROJECTION = 'report-evidence-manual-projection-v5'
MANUAL_CANDIDATE = 'report-evidence-manual-candidate-v5'
MANUAL_IDS = 'report-evidence-manual-ids-v1'
CONTEXT = 'report-evidence-manual-context-v5'
MAX_REVIEW_BYTES = 512 * 1024
_KEY = re.compile(r'^[a-z][a-z0-9_]{0,39}$')
_HASH = re.compile(r'^[a-f0-9]{64}$')


class ManualReviewValueV5(Closed):
    schema: Literal['report-evidence-manual-review-v5']
    result: CandidateWireV5
    note: str = Field(max_length=12000, strict=True)


def _safe(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError('Human review is not safe JSON') from None
    if len(raw) > MAX_REVIEW_BYTES:
        raise ValueError('Human review capacity exceeded; original drafts must remain saved')


def _keys(items):
    result = {}
    for position, item in enumerate(items):
        key = item.get('key')
        if type(key) is not str or not _KEY.fullmatch(key) or key == 'end' or key in result:
            raise ValueError('Original candidate has an invalid or duplicate local key')
        result[key] = position
    return result


def _reference(key, index):
    if type(key) is not str or key not in index:
        raise ValueError('Original candidate reference is missing; no end fallback')
    return index[key]


def manual_from_projection(result, note=''):
    """Exact inverse view of an already verified platform candidate; no repair."""
    if type(result) is not dict or result.get('schema') != CANDIDATE_PROJECTION:
        raise ValueError('Expected a verified platform candidate projection')
    original = copy.deepcopy(result)
    value = copy.deepcopy(result)
    value['schema'] = 'report-evidence-candidate-wire-v5'
    _keys(value['global_claims'])
    for claim in value['global_claims']:
        claim.pop('key')
    for business in value['businesses']:
        steps, people = _keys(business['steps']), _keys(business['participants'])
        for step in business['steps']:
            step.pop('key')
            for old, new in (('next_key', 'next_index'), ('else_key', 'else_index')):
                ref = step.pop(old)
                step[new] = None if ref == 'end' else _reference(ref, steps)
        for person in business['participants']:
            person.pop('key')
        for collection in ('interactions', 'conditions', 'coordination'):
            _keys(business[collection])
            for item in business[collection]:
                item.pop('key')
                refs = item.pop('step_keys')
                if len(set(refs)) != len(refs):
                    raise ValueError('Original candidate repeats a step reference')
                item['step_indices'] = [_reference(ref, steps) for ref in refs]
                if collection == 'interactions':
                    item['from_participant_index'] = _reference(item.pop('from_participant'), people)
                    item['to_participant_index'] = _reference(item.pop('to_participant'), people)
    raw = {'schema': MANUAL_VALUE, 'result': value, 'note': note}
    checked = ManualReviewValueV5.model_validate(raw).model_dump()
    _safe(checked)
    if checked != raw or result != original:
        raise ValueError('Human inverse conversion changed the input')
    return checked


def _context(value, catalog):
    keys = {'schema', 'actor_id', 'workspace_id', 'draft_id', 'request_id',
        'accepted_revision', 'model_result_hash', 'source_hash', 'catalog_hash'}
    if type(value) is not dict or set(value) != keys or value['schema'] != CONTEXT:
        raise ValueError('Human review context is not closed')
    if any(type(value[key]) is not str or str(UUID(value[key])) != value[key]
            for key in ('actor_id', 'workspace_id', 'draft_id', 'request_id')):
        raise ValueError('Human review scope differs')
    if (type(value['accepted_revision']) is not int or value['accepted_revision'] < 2
            or any(type(value[key]) is not str or not _HASH.fullmatch(value[key])
                for key in ('model_result_hash', 'source_hash', 'catalog_hash'))
            or value['source_hash'] != catalog['descriptor']['source_hash']
            or value['catalog_hash'] != catalog['descriptor']['catalog_hash']):
        raise ValueError('Human review revision or source differs')
    return copy.deepcopy(value)


def build_manual_projection(value, *, review_context, catalog, allowed_span_refs, context):
    """Validate a human value without inventing a provider/journal binding."""
    if type(value) is not dict:
        raise ValueError('Human review value must be an object')
    checked = ManualReviewValueV5.model_validate(value).model_dump()
    if checked != value:
        raise ValueError('Human review changed during validation')
    _safe(checked)
    identity = _context(review_context, catalog)
    identity_hash, value_hash = digest(identity), digest(checked)
    def human_id(prefix, path):
        return prefix + digest({'domain': MANUAL_IDS, 'review_context_hash': identity_hash,
            'value_hash': value_hash, 'path': path})[:32]
    result = candidate_with_ids(checked['result'], human_id)
    internal = {**copy.deepcopy(result), 'schema': 'report-evidence-candidate-v4'}
    if validate_evidence_result(internal, catalog, allowed_span_refs, context) != internal:
        raise ValueError('Human internal validation view changed')
    result['schema'] = MANUAL_CANDIDATE
    projection = {'schema': MANUAL_PROJECTION, 'derivation_version': MANUAL_IDS,
        'review_context': identity, 'review_context_hash': identity_hash,
        'value_hash': value_hash, 'context_hash': digest(context),
        'allowed_span_refs_hash': digest(allowed_span_refs), 'result': result}
    _safe(projection)
    return {**projection, 'projection_hash': digest(projection)}


def check_manual_projection(projection, value, **inputs):
    expected = build_manual_projection(value, **inputs)
    if type(projection) is not dict or projection != expected:
        raise ValueError('Human projection differs from its saved value and original scope')
    return expected
