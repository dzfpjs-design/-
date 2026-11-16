"""V4 private candidates backed by exact, provided evidence spans.

This module checks structure, frozen source scope, lexical legacy guards and
exhaustive input accounting. It cannot certify semantic correctness, equivalent
paraphrases, completeness of a report interpretation, or an executable grant.
The caller must supply the immutable, previously validated Map input index and
the exact span allowlist actually included in its frozen merge payload. This
validator cannot establish what a provider received or what a caller persisted.
"""
import copy
import re
from typing import Annotated, Literal

from pydantic import Field

from .report_evidence_plan import EvidenceClaim, resolve_evidence
from .report_full_reading_semantics import (
    Closed, FullField, FullObject, FullRelationship, SemanticEvidence,
    Participant, Interaction, BusinessCondition, Coordination, FullStep,
    FullBusiness, FullReportResult, validate_full_result,
)


SpanRef = Annotated[str, Field(pattern=r'^[wo]:p[1-9][0-9]*/s[1-9][0-9]*$', max_length=100)]
Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
_INPUT = re.compile(r'^map-([1-9]|1[0-9]|2[0-4])/claims/(0|[1-9]|[1-7][0-9])$')


class SpanCitation(Closed):
    span_ref: SpanRef


class SpanField(FullField):
    citations: list[SpanCitation] = Field(max_length=4)


class SpanObject(FullObject):
    fields: list[SpanField] = Field(min_length=1, max_length=40)
    citations: list[SpanCitation] = Field(max_length=4)


class SpanRelationship(FullRelationship):
    citations: list[SpanCitation] = Field(min_length=1, max_length=4)


class SpanEvidence(SemanticEvidence):
    citations: list[SpanCitation] = Field(max_length=4)


class SpanParticipant(Participant):
    evidence: SpanEvidence


class SpanInteraction(Interaction):
    evidence: SpanEvidence


class SpanCondition(BusinessCondition):
    evidence: SpanEvidence


class SpanCoordination(Coordination):
    evidence: SpanEvidence


class SpanStep(FullStep):
    citations: list[SpanCitation] = Field(max_length=4)


class SpanBusiness(FullBusiness):
    steps: list[SpanStep] = Field(max_length=16)
    citations: list[SpanCitation] = Field(max_length=4)
    participants: list[SpanParticipant] = Field(max_length=40)
    interactions: list[SpanInteraction] = Field(max_length=40)
    conditions: list[SpanCondition] = Field(max_length=40)
    coordination: list[SpanCoordination] = Field(max_length=40)


# Same six required fields, kinds, statuses and explanation/ref constraints as
# Map claims. A global declaration remains independent of any single business.
GlobalClaim = EvidenceClaim


class FullResultV4(FullReportResult):
    schema: Literal['report-evidence-candidate-v4']
    source_hash: Hash
    catalog_hash: Hash
    global_claims: list[GlobalClaim] = Field(max_length=160)
    objects: list[SpanObject] = Field(max_length=40)
    relationships: list[SpanRelationship] = Field(max_length=30)
    businesses: list[SpanBusiness] = Field(max_length=20)


class EvidenceReconciliation(Closed):
    input_id: str = Field(pattern=_INPUT.pattern, max_length=80)
    disposition: Literal['kept', 'duplicate', 'uncertain']
    output_kind: Literal['global_claims', 'businesses', 'objects', 'relationships', 'questions']
    output_index: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=1000)


class FullMergeResultV4(Closed):
    result: FullResultV4
    reconciliation: list[EvidenceReconciliation] = Field(max_length=1920)


def _fail(path, message):
    raise ValueError(f'{path}: {message}')


def _provided(catalog, allowed_refs):
    # No implicit catalog-wide fallback. The public resolver verifies source
    # reconstruction, codepoint positions/hashes, explicit scope and uniqueness.
    resolved = resolve_evidence(catalog, allowed_refs, allowed_refs)
    if any(len(item['text']) > 250 for item in resolved):
        _fail('/allowed_refs', '证据片段超过原逐字引用上限，未截断')
    return {item['span_ref']: item for item in resolved}


def _refs(value):
    """Collect refs only from already closed, typed candidate/claim objects."""
    if isinstance(value, list):
        return set().union(*(_refs(item) for item in value)) if value else set()
    if isinstance(value, dict):
        own = {value['span_ref']} if set(value) == {'span_ref'} else set(value.get('span_refs', []))
        for key, item in value.items():
            if key not in {'span_ref', 'span_refs'}:
                own.update(_refs(item))
        return own
    return set()


def _check_refs(refs, provided, path):
    if refs - set(provided):
        _fail(path, '引用片段不在本次实际提供的范围内')
    if any(not provided[ref]['text'].strip() for ref in refs):
        _fail(path, '纯空白片段不能作为声明的证据')


def _legacy_projection(result, provided):
    # Unique temporary legacy keys distinguish two real spans whose text is
    # identical. Never concatenate spans, repair quotes, or publish these keys.
    # Every legacy quote is one exact original span, at most250 codepoints.
    aliases = {ref: ref[0] + ':p' + str(index + 1) for index, ref in enumerate(provided)}
    source_index = {aliases[ref]: {'text': item['text']} for ref, item in provided.items()}

    def project(value, path):
        if isinstance(value, list):
            if path.endswith('/citations'):
                refs = [cite['span_ref'] for cite in value]
                if len(refs) != len(set(refs)):
                    _fail(path, '同一片段引用不能重复')
            return [project(item, path + '/' + str(index)) for index, item in enumerate(value)]
        if isinstance(value, dict):
            if set(value) == {'span_ref'}:
                ref = value['span_ref']
                return {'source_ref': aliases[ref], 'quote': provided[ref]['text']}
            return {key: project(item, path + '/' + key) for key, item in value.items()}
        return value

    projection = {key: copy.deepcopy(result[key]) for key in
                  ('domain', 'objects', 'relationships', 'businesses', 'questions')}
    projection['schema'] = 'report-business-candidate-v3'
    return project(projection, ''), source_index


def _validate_result(value, catalog, provided, context):
    result = FullResultV4.model_validate(value).model_dump()
    descriptor = catalog['descriptor']
    if result['source_hash'] != descriptor['source_hash']:
        _fail('/source_hash', '候选不属于本次冻结来源')
    if result['catalog_hash'] != descriptor['catalog_hash']:
        _fail('/catalog_hash', '候选不属于本次冻结片段目录')
    _check_refs(_refs(result), provided, '/')
    keys = [item['key'] for item in result['global_claims']]
    if len(keys) != len(set(keys)):
        _fail('/global_claims', '全局声明key不能重复')
    if any(item['status'] == 'unknown' for item in result['global_claims']) and not any(
            question.strip() for question in result['questions']):
        _fail('/questions', '未知全局声明须独立保留待核问题')
    projection, source_index = _legacy_projection(result, provided)
    # Retain every old lexical unit/relationship and timing/direction guard.
    # This temporary check does not replace or normalize the original output.
    validate_full_result(projection, source_index, context)
    return result


def validate_evidence_result(value, catalog, allowed_refs, context):
    """Return all original V4 fields; validation is not semantic approval."""
    return _validate_result(value, catalog, _provided(catalog, allowed_refs), context)


def validate_evidence_merge(value, catalog, allowed_refs, context, input_items):
    """Account for every frozen Map claim without claiming semantic equivalence.

    input_items is the caller's immutable index {map-N/claims/i: validatedClaim}.
    Its full original declarations/evidence remain in the separately persisted
    merge input even when a destination is an unresolved question. Merely
    passing this index here does not prove it was durably saved or sent.
    """
    provided = _provided(catalog, allowed_refs)
    if type(input_items) is not dict or len(input_items) > 1920:
        _fail('/input_items', '缺少完整固定的Map声明索引或超过24批上限')
    claims, groups = {}, {}
    for identity, original in input_items.items():
        match = _INPUT.fullmatch(identity) if type(identity) is str else None
        if not match:
            _fail('/input_items', '输入只能指向map-1至24的claims/0至79')
        claim = EvidenceClaim.model_validate(original).model_dump()
        _check_refs(set(claim['span_refs']), provided, '/input_items/' + identity)
        claims[identity] = claim
        groups.setdefault(int(match[1]), []).append((int(match[2]), claim['key']))
    for positions in groups.values():
        if {index for index, _ in positions} != set(range(len(positions))):
            _fail('/input_items', '每批固定声明索引必须从0连续，不得跳过输入')
        if len({key for _, key in positions}) != len(positions):
            _fail('/input_items', '同批Map声明key不能重复')
    original_refs = set().union(*(set(claim['span_refs']) for claim in claims.values())) if claims else set()
    if set(provided) != original_refs:
        _fail('/allowed_refs', '汇总只接受已验证Map原声明实际引用并提供的片段集合')
    merged = FullMergeResultV4.model_validate(value).model_dump()
    merged['result'] = _validate_result(merged['result'], catalog, provided, context)
    actual = [item['input_id'] for item in merged['reconciliation']]
    if len(actual) != len(set(actual)) or set(actual) != set(claims):
        _fail('/reconciliation', '每个固定Map原声明必须且只能有一个去向')
    for index, item in enumerate(merged['reconciliation']):
        path = f'/reconciliation/{index}'
        targets = merged['result'][item['output_kind']]
        if item['output_index'] >= len(targets):
            _fail(path + '/output_index', '声明去向不存在')
        if not item['reason'].strip():
            _fail(path + '/reason', '声明去向必须说明')
        if item['disposition'] == 'uncertain':
            if item['output_kind'] != 'questions':
                _fail(path, '未解决的声明只能列为问题，原声明仍保留在固定输入中')
        elif item['output_kind'] == 'questions':
            _fail(path, '保留或去重不能用问题替代原声明')
        elif set(claims[item['input_id']]['span_refs']) - _refs(targets[item['output_index']]):
            _fail(path, '保留或去重去向必须包含原声明的全部证据片段')
        if item['disposition'] != 'uncertain':
            status = claims[item['input_id']]['status']
            target = targets[item['output_index']]
            if item['output_kind'] == 'global_claims':
                if status == 'unknown' and target['status'] != 'unknown':
                    _fail(path, '保留的未知声明不能被直接升级为已知或推断')
                if status == 'inferred' and target['status'] == 'explicit':
                    _fail(path, '保留的推断声明不能被直接升级为原文明示')
            elif status != 'explicit' and not target['inference_reason'].strip():
                _fail(path, '非明示原声明保留为对象、关系或业务时仍须解释推断或未知')
            # This blocks direct status laundering only, not incorrect prose.
    if any(claim['status'] == 'unknown' for claim in claims.values()) and not any(
            question.strip() for question in merged['result']['questions']):
        _fail('/questions', 'Map原声明中的未知仍须保留问题，不因归并成为已知')
    return merged
