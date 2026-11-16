"""Pure, inferred business-scope suggestions for a savable partial draft.

No source claim, original answer or confirmed ledger is repaired here. The
caller supplies independently cold-checked claims/indexed inputs and, when
available, their actual catalog. Catalog consistency alone is not source ACL or
proof that it belongs to that snapshot; the server must establish that binding.

Business-kind machine claims provide editable seed suggestions, not an
authoritative business list. Exact shared citations and literal names in the
referenced ORIGINAL spans only suggest membership. Zero/multiple matches stay
unassigned; they are never silently classified as background or shared. The
returned ledger has the existing V180 eight-key BASE shape and is a partial
draft only. Saving/confirming it, current authority and final semantic quality
belong to the real V181 API, not this module. There is no I/O or model call.
"""
import copy
import re
from functools import wraps
from uuid import NAMESPACE_URL, uuid5

from . import report_indexed_inputs_v178 as codec
from . import report_scoped_design_plan_v180 as planner
from .report_evidence_plan import check_evidence_source

SCHEMA = 'report-scoped-design-proposal-v181'
METHOD = 'exact-seed-span-and-name-v181'
MAX_BYTES = 8 * 1024 * 1024
_KINDS = {'职责', '来源', '治理', '互动', '业务', '步骤', '条件', '协同', '对象', '关系', '冲突', '其他'}


class ScopedDesignProposalError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__('业务分组建议无法核对；原资料保留，未确认、排除或发送：' + code)


def _need(ok, code):
    if not ok:
        raise ScopedDesignProposalError(code)


def _checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ScopedDesignProposalError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError,
                OverflowError, RecursionError):
            raise ScopedDesignProposalError('invalid_structure') from None
    return checked


def _claims(indexed_inputs, snapshot_hash, input_claims):
    indexed = codec.check_indexed_inputs(indexed_inputs, input_claims=input_claims,
        source_snapshot_hash=snapshot_hash)
    for claim in input_claims:
        _need(type(claim.get('kind')) is str and claim['kind'] in _KINDS
            and type(claim.get('statement')) is str and 1 <= len(claim['statement']) <= 2000
            and bool(claim['statement'].strip()) and type(claim.get('span_refs')) is list
            and len(claim['span_refs']) <= 16
            and all(type(ref) is str and bool(ref.strip()) for ref in claim['span_refs'])
            and len(set(claim['span_refs'])) == len(claim['span_refs'])
            and type(claim.get('status')) is str and claim['status'] in {'explicit', 'inferred', 'unknown'}
            and type(claim.get('explanation')) is str and len(claim['explanation']) <= 1000,
            'original_claim_shape')
        _need(bool(claim['span_refs']) if claim['status'] == 'explicit'
            else bool(claim['explanation'].strip()), 'original_claim_evidence')
    return indexed, copy.deepcopy(input_claims)


def _catalog(value, claims):
    if value is None:
        return None
    check_evidence_source(value)
    _need(all(ref in value['index'] for claim in claims for ref in claim['span_refs']),
        'claim_reference_not_in_catalog')
    return copy.deepcopy(value)


def _positions(value, count):
    _need(type(value) is list and len(value) <= count, 'seed_positions')
    previous = -1
    for position in value:
        _need(type(position) is int and previous < position < count and position >= 0,
            'seed_positions')
        previous = position


def _automatic_seeds(claims, snapshot_hash):
    # Identical labels may share a suggested scope, but ALL source positions
    # remain separate in the original input and the seed's provenance.
    groups = {}
    for position, claim in enumerate(claims):
        if claim['kind'] == '业务':
            groups.setdefault(claim['statement'].strip(), []).append(position)
    seeds, labels = [], set()
    for statement, positions in groups.items():
        first = positions[0]
        scope_id = str(uuid5(NAMESPACE_URL, SCHEMA + ':' + snapshot_hash + ':' + str(first)))
        placeholder = len(statement) > 200
        name = '待命名业务候选 ' + str(first + 1) if placeholder else statement
        collision = 0
        while name in labels:
            # This is a clearly derived DISPLAY label, not a clipped statement.
            collision += 1
            name = '待命名业务候选 ' + scope_id + ':' + str(collision)
            placeholder = True
        labels.add(name)
        seeds.append(({'scope_id': scope_id, 'name': name, 'claim_positions': positions},
            'machine_business_claim', 'placeholder' if placeholder else 'machine_statement'))
    return seeds


def _seeds(scope_seeds, claims, snapshot_hash):
    automatic = scope_seeds is None
    if automatic:
        result = _automatic_seeds(claims, snapshot_hash)
    else:
        _need(type(scope_seeds) is list and len(scope_seeds) <= planner.MAX_SCOPES, 'seed_count')
        result = []
        seen, names = set(), set()
        for seed in scope_seeds:
            _need(type(seed) is dict and set(seed) == {'scope_id', 'name', 'claim_positions'}, 'seed_shape')
            scope_id, name = seed['scope_id'], seed['name']
            _need(type(scope_id) is str and 1 <= len(scope_id) <= 128
                and scope_id == scope_id.strip() and all(ord(char) >= 32 for char in scope_id)
                and scope_id not in seen, 'seed_identity')
            _need(type(name) is str and 1 <= len(name) <= 200 and bool(name.strip())
                and name not in names, 'seed_name')
            _positions(seed['claim_positions'], len(claims))
            seen.add(scope_id); names.add(name)
            result.append((copy.deepcopy(seed), 'caller_supplied_unverified', 'caller_supplied'))
    return automatic, result


def _literal(name, text):
    # No fuzzy/stem/casefold inference. ASCII identifiers must also have real
    # word boundaries; Chinese labels use the complete literal name.
    before = r'(?<![A-Za-z0-9_])' if name[0].isascii() and (name[0].isalnum() or name[0] == '_') else ''
    after = r'(?![A-Za-z0-9_])' if name[-1].isascii() and (name[-1].isalnum() or name[-1] == '_') else ''
    return re.search(before + re.escape(name) + after, text) is not None


@_checked
def build_proposal(indexed_inputs, source_snapshot_hash, *, input_claims, scope_seeds=None, catalog=None):
    """Suggest, retain every unresolved input, and return a V180 base draft.

    None seeds derives editable candidates from actual ``kind == 业务`` claims;
    [] explicitly asks for an empty, fully unassigned draft. If automatic seeds
    exceed the existing 64-scope bound, ALL candidates remain listed and the
    ledger stays empty until a caller selects/edits seeds. No first-N clipping.
    """
    frozen, claims = _claims(indexed_inputs, source_snapshot_hash, input_claims)
    original_catalog = _catalog(catalog, claims)
    automatic, seeded = _seeds(scope_seeds, claims, source_snapshot_hash)
    blocked = ['seed_candidates_capacity'] if len(seeded) > planner.MAX_SCOPES else []
    active = [] if blocked else seeded
    scopes = [{'scope_id': seed['scope_id'], 'name': seed['name'],
        'claim_positions': [], 'context_positions': []} for seed, _, _ in active]
    scope_index = {scope['scope_id']: scope for scope in scopes}
    seed_refs = {seed['scope_id']: set(ref for position in seed['claim_positions']
        for ref in claims[position]['span_refs']) for seed, _, _ in active}
    suggestions, unassigned, conflicts = [], [], []
    for position, claim in enumerate(claims):
        matches = []
        for seed, _, naming in active:
            scope_id = seed['scope_id']
            common = [ref for ref in claim['span_refs'] if ref in seed_refs[scope_id]]
            named = [ref for ref in claim['span_refs']
                if original_catalog is not None and naming != 'placeholder'
                and _literal(seed['name'], original_catalog['index'][ref]['text'])]
            rules = []
            if position in seed['claim_positions']:
                rules.append('seed_position')
            if common:
                rules.append('same_original_span')
            if named:
                rules.append('original_name_literal')
            if rules:
                matches.append({'scope_id': scope_id, 'rules': rules,
                    'span_refs': common, 'name_span_refs': named, 'evidence_status': 'inferred'})
        recipients = [item['scope_id'] for item in matches]
        if len(recipients) == 1:
            scope_index[recipients[0]]['claim_positions'].append(position)
        else:
            unassigned.append(position)
            if recipients:
                conflicts.append({'input_position': position, 'candidate_scope_ids': recipients})
        suggestions.append({'input_position': position, 'claim_hash': frozen['ordered_claim_hashes'][position],
            'status': 'suggested' if len(recipients) == 1 else 'conflict' if recipients else 'unassigned',
            'candidate_scope_ids': recipients, 'matches': matches,
            'evidence_status': 'inferred' if recipients else 'unknown'})
    ledger = {'schema': planner.LEDGER, 'source_snapshot_hash': source_snapshot_hash,
        'indexed_inputs_hash': frozen['inputs_hash'], 'input_claims_hash': frozen['input_claims_hash'],
        'input_count': frozen['input_count'], 'scopes': scopes, 'shared': [], 'background': []}
    provenance = []
    for seed, origin, naming in seeded:
        provenance.append({'scope_id': seed['scope_id'], 'origin': origin, 'naming': naming,
            'claim_positions': copy.deepcopy(seed['claim_positions']),
            'claim_hashes': [frozen['ordered_claim_hashes'][position] for position in seed['claim_positions']],
            'span_refs': list(dict.fromkeys(ref for position in seed['claim_positions']
                for ref in claims[position]['span_refs'])), 'evidence_status': 'inferred'})
    value = {'schema': SCHEMA, 'method': METHOD, 'source_snapshot_hash': source_snapshot_hash,
        'indexed_inputs_hash': frozen['inputs_hash'], 'input_claims_hash': frozen['input_claims_hash'],
        'input_count': frozen['input_count'],
        'catalog_hash': original_catalog['descriptor']['catalog_hash'] if original_catalog is not None else None,
        'seed_mode': 'machine_business_claims' if automatic else 'caller_supplied_unverified',
        'scope_seeds': [copy.deepcopy(seed) for seed, _, _ in seeded], 'seed_provenance': provenance,
        'ledger': ledger, 'suggestions': suggestions, 'unassigned_positions': unassigned,
        'conflicts': conflicts, 'blocked_reasons': blocked,
        'source_anchors_verified': False, 'ownership_coverage_verified': False,
        'semantic_quality_verified': False, 'human_confirmed': False,
        'can_apply': False, 'can_publish': False, 'dispatch_allowed': False}
    result = {**value, 'proposal_hash': codec.digest(value)}
    _need(len(codec.canonical(result).encode('utf-8')) <= MAX_BYTES, 'proposal_capacity')
    return result


@_checked
def check_proposal(value, indexed_inputs, source_snapshot_hash, *, input_claims, scope_seeds=None, catalog=None):
    """Cold deterministic rebuild, never accept a self-resealed proposal."""
    expected = build_proposal(indexed_inputs, source_snapshot_hash, input_claims=input_claims,
        scope_seeds=scope_seeds, catalog=catalog)
    _need(type(value) is dict and codec.canonical(value) == codec.canonical(expected), 'proposal_mismatch')
    return expected
