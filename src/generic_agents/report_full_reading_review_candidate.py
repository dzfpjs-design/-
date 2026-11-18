"""Conservative, in-memory preparation for review; never changes saved answers."""

from copy import deepcopy

from .report_full_reading_semantics import (
    FullReportResult, _grounded, _relationships, _unique, validate_full_result,
)
from .setup_object_proposals import unit_declared

# Conservative work budgets for preview only. Exceeding either fails closed;
# neither text nor evidence is shortened. The final semantic validation scans
# the same citations again, and each unit guard may inspect the entire suffix
# after every occurrence of its field label.
_MAX_CITATION_SCAN_CHARS = 2_000_000
_MAX_UNIT_WORK_CHARS = 16_000_000


class CandidateBudgetExceeded(ValueError):
    """The exact candidate is too expensive to review safely in one request."""


def _check_preview_work_budget(candidate, source_index):
    citations = []

    def visit(value):
        if isinstance(value, dict):
            for key, part in value.items():
                if key == 'citations':
                    citations.extend(part)
                else:
                    visit(part)
        elif isinstance(value, list):
            for part in value:
                visit(part)

    visit(candidate)
    scan_chars = 0
    for cite in citations:
        source = source_index.get(cite['source_ref'])
        source_text = source.get('text') if isinstance(source, dict) else None
        if type(source_text) is str:
            # Account for both preparation and final validation, plus relation
            # checks performed separately during preparation.
            scan_chars += 3 * (len(source_text) + len(cite['quote']))
        if scan_chars > _MAX_CITATION_SCAN_CHARS:
            raise CandidateBudgetExceeded('预览来源扫描量超过安全上限，无法生成候选；原回答保持不变')

    unit_work = 0
    for obj in candidate['objects']:
        for field in obj['fields']:
            if field['unit'] is None:
                continue
            label = field['label']
            for cite in field['citations']:
                source = source_index.get(cite['source_ref'])
                source_text = source.get('text') if isinstance(source, dict) else None
                for text in (cite['quote'], source_text):
                    if type(text) is str:
                        # count() scans once; each matching suffix can be as
                        # long as the full text. Both passes are budgeted even
                        # if short-circuiting would skip a particular call.
                        # The unit regex can revisit whitespace for each
                        # occurrence of '单位'. Charge a whole-text quadratic
                        # bound for every field-label match, including both
                        # candidate preparation and the final validator.
                        unit_work += 2 * len(text) ** 2 * (1 + text.count(label))
                        if unit_work > _MAX_UNIT_WORK_CHARS:
                            raise CandidateBudgetExceeded('预览单位证据计算量超过安全上限，无法生成候选；原回答保持不变')


def build_candidate(value, source_index, context):
    """Return original, validated candidate and unresolved items, or raise.

    This is not an approval, persistence, recovery or publication operation.
    Neither input nor the returned original answer is modified. The caller must
    bind this result to the correct answer and frozen source before saving it.
    """
    if not isinstance(source_index, dict) or not isinstance(context, dict):
        raise ValueError('/: 缺少冻结读取索引或上下文')
    # Validate the entire input, including entries later put aside, first.
    candidate = FullReportResult.model_validate(value).model_dump()
    original = deepcopy(value)
    existing = context.get('object_names', [])
    if not isinstance(existing, list) or any(type(n) is not str or not n.strip() for n in existing):
        raise ValueError('/context/object_names: 已有对象名称无效')
    objects = _unique(candidate['objects'], 'name', '/objects')
    if objects & set(existing):
        raise ValueError('/objects: 不能在私人新增建议中覆盖已有对象类型')
    _check_preview_work_budget(candidate, source_index)
    pending = []
    for i, obj in enumerate(candidate['objects']):
        _grounded(obj, source_index, f'/objects/{i}')
        for j, field in enumerate(obj['fields']):
            path = f'/objects/{i}/fields/{j}'
            _grounded(field, source_index, path)
            unit = field['unit']
            if unit is not None and not any(
                unit_declared(field['label'], unit, cite['quote'])
                and unit_declared(field['label'], unit, source_index[cite['source_ref']]['text'])
                for cite in field['citations']
            ):
                pending.append({'kind': 'unit', 'path': path + '/unit',
                                'reason': 'unit_evidence_not_established',
                                'original_value': unit,
                                'citations': deepcopy(field['citations'])})
                field['unit'] = None

    # Duplicate names must still be rejected across *all* original relations.
    _unique(candidate['relationships'], 'name', '/relationships')
    kept = []
    declaration_error = '/relationships/0: 引用须正向连接两类对象并声明此关系；共同出现或参与方互动不等于对象关系'
    for index, relation in enumerate(candidate['relationships']):
        try:
            _relationships({'relationships': [relation]}, objects | set(existing), source_index)
        except ValueError as error:
            if str(error) != declaration_error:
                raise
            # A non-unspecified multiplicity is independently unproven when
            # there is no declaration; do not hide that additional error.
            if relation['cardinality'] != 'unspecified':
                raise ValueError(f'/relationships/{index}/cardinality: 多重性未得到证明') from error
            pending.append({'kind': 'relationship', 'path': f'/relationships/{index}',
                            'reason': 'relationship_evidence_not_established',
                            'original_value': deepcopy(relation)})
        else:
            kept.append(relation)
    candidate['relationships'] = kept
    validated = validate_full_result(candidate, source_index, context)
    return {'original': original, 'candidate': validated, 'quarantine': pending,
            'status': 'needs_review', 'confirmed': False}
