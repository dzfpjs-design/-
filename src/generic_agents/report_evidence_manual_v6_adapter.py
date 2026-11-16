"""Pure V6 candidate -> independent V5 human value; no adoption or I/O.

The caller MUST first verify the complete CandidateProjectionV6, original
response/journal, source/catalog, reconciliation and its authorized scope.
This adapter cannot establish origin, permissions, meaning or model quality.
Only a private structural view uses the old candidate tag for validation. It
is neither an original V5 answer nor returned or saved as one. The original
V6 model object and all its external hashes remain unchanged. The existing
512 KiB human-value limit applies; no truncation or implicit approval occurs.
"""
import copy
import re

from .report_evidence_manual_v5 import manual_from_projection
from .report_evidence_reduction_wire_v6 import CANDIDATE
from .report_evidence_wire_v3 import CANDIDATE_PROJECTION, candidate_with_ids


def _original_keys(result):
    """Index exact array positions, never names or a guessed reference."""
    paths = {}

    def collect(items, prefix, base):
        if type(items) is not list:
            raise ValueError('V6 candidate local collection must be an array')
        seen = set()
        for index, item in enumerate(items):
            key = item.get('key') if type(item) is dict else None
            if (type(key) is not str or not re.fullmatch(prefix + r'[a-f0-9]{32}', key)
                    or key in seen):
                raise ValueError('V6 candidate local keys are invalid or repeated')
            seen.add(key)
            paths[(prefix, base + '/' + str(index))] = key

    collect(result['global_claims'], 'gc_', '/result/global_claims')
    for index, business in enumerate(result['businesses']):
        base = '/result/businesses/' + str(index)
        for name, prefix in (('steps', 'st_'), ('participants', 'pa_'),
                             ('interactions', 'in_'), ('conditions', 'co_'),
                             ('coordination', 'cd_')):
            collect(business[name], prefix, base + '/' + name)
    return paths


def _local_edges(value):
    # Match the existing JS manual validation view without fabricating a
    # catalog. Actual evidence/whole-result validation still belongs outside.
    for business in value['result']['businesses']:
        for index, step in enumerate(business['steps']):
            if (step['next_index'] == index or step['else_index'] == index
                    or not step['branch_condition'].strip() and step['else_index'] is not None):
                raise ValueError('V6 candidate contains an invalid local step edge')
        for interaction in business['interactions']:
            if interaction['from_participant_index'] == interaction['to_participant_index']:
                raise ValueError('V6 candidate contains a participant self-link')
        for item in business['coordination']:
            if item['kind'] != 'unspecified' and len(item['step_indices']) < 2:
                raise ValueError('V6 candidate coordination requires its explicit steps')


def manual_from_projection_v6(result, note=''):
    """Convert a caller-verified V6 candidate, retaining every semantic value.

    Keys become only exact business-local indices/null. An inverse reconstruction
    using the original keys must equal the original V6 object field for field.
    The returned three-key ManualReviewValueV5 is an independent human value;
    the caller's future human context must still bind the ORIGINAL V6 model hash.
    """
    if type(result) is not dict or result.get('schema') != CANDIDATE:
        raise ValueError('Expected a caller-verified V6 candidate projection')
    original = copy.deepcopy(result)
    internal_validation_view = copy.deepcopy(original)
    internal_validation_view['schema'] = CANDIDATE_PROJECTION
    try:
        value = manual_from_projection(internal_validation_view, note)
        paths = _original_keys(original)
        _local_edges(value)
        restored = candidate_with_ids(value['result'], lambda prefix, path: paths[(prefix, path)])
    except (KeyError, IndexError, TypeError, AttributeError):
        raise ValueError('V6 candidate structure or exact local reference differs') from None
    restored['schema'] = CANDIDATE
    if restored != original or result != original:
        raise ValueError('V6 manual adapter changed a semantic field or original candidate')
    return value
