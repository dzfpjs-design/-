"""Explain an unfinished recursive human draft without adopting or repairing it.

The exact new source and structural validators remain the gates. Diagnostics
are detached from stored input and grant no semantic or execution authority.
"""
import copy

from pydantic import ValidationError

from . import business_flow_nested_plan_v192 as plan
from . import business_flow_review_diagnostics as previous
from . import report_business_flow_wire_v182 as source_gate
from .contracts import digest
from .report_evidence_plan import resolve_evidence
from .report_full_reading_semantics import _safe


def _citation_paths(value, parts=()):
    if type(value) is dict:
        for key, item in value.items():
            path = (*parts, key)
            if key == 'citations' and type(item) is list:
                yield previous._pointer(path), item
            else:
                yield from _citation_paths(item, path)
    elif type(value) is list:
        for index, item in enumerate(value):
            yield from _citation_paths(item, (*parts, index))


def _location(value, location):
    """Omit Pydantic tagged-union labels, never invent flattened positions."""
    parts, current = [], value
    for item in location:
        if type(current) is dict and item in current:
            parts.append(item); current = current[item]
        elif type(current) is list and type(item) is int and 0 <= item < len(current):
            parts.append(item); current = current[item]
        elif item in {'steps', 'decision', 'parallel', 'repeat'} and type(current) is dict and current.get('kind') == item:
            continue
        else:
            parts.append(item); current = None
    return previous._pointer(parts)


def review(value, *, scope_binding, catalog, allowed_span_refs, context, input_claims):
    try:
        previous._json(value); _safe(value)
        if len(previous._bytes(value)) > previous.MAX_VALUE_BYTES: raise previous._Capacity()
        saved = copy.deepcopy(value)
    except previous._Capacity:
        return previous._unavailable(None, 'draft_capacity', '草稿超过当前诊断容量；原输入未裁剪')
    except previous._PURE_ERRORS:
        return previous._unavailable(None, 'unsafe_draft', '输入不是可安全保存的有界数据；原输入未修改')
    try:
        issues = previous._Issues()
        inputs = copy.deepcopy(dict(scope_binding=scope_binding, catalog=catalog,
            allowed_span_refs=allowed_span_refs, context=context, input_claims=input_claims))
        try:
            binding, _ = source_gate._source({'evidence': []}, **inputs)
            spans = resolve_evidence(inputs['catalog'], inputs['allowed_span_refs'], inputs['allowed_span_refs'])
        except previous._PURE_ERRORS:
            issues.add('source_unavailable', '', '本范围原文与冻结来源无法核对，已有草稿保留')
            return previous._result(saved, issues.items, available=False)
        evidence = [{'span_ref': item['span_ref'], 'source_text': item['text'],
            'text_origin': 'platform_resolved_source'} for item in spans]
        originals = {item['span_ref']: item['text'] for item in spans}
        shaped = structural = source = False
        try:
            # Preflight runs before recursive model validation on every input.
            checked = plan._typed_plan(saved)
            shaped = previous._bytes(checked) == previous._bytes(saved)
            if not shaped: issues.add('shape', '', '字段校验会改变原输入，请完整核对')
        except ValidationError as error:
            if error.error_count() > previous.MAX_ISSUES: raise previous._Capacity()
            for item in error.errors(include_url=False, include_context=False, include_input=False):
                issues.add('shape', _location(saved, item['loc']), '字段不符合递归流程合同：' + item['type'])
        except previous._PURE_ERRORS:
            issues.add('nested_structure', '/business/flows', '阶段、分支或累计容量不完整；请检查完整递归结构')
        if shaped:
            if saved['binding_hash'] != digest(binding):
                issues.add('binding_mismatch', '/binding_hash', '草稿与本业务冻结来源不一致')
            if saved['business']['name'] != binding['name']:
                issues.add('binding_mismatch', '/business/name', '业务名称与已确认的业务范围不一致')
            try:
                plan.check_plan(saved, scope_binding=binding); structural = True
            except previous._PURE_ERRORS:
                issues.add('nested_structure', '/business/flows', '完整阶段或业务范围尚未通过核对')
            for path, refs in _citation_paths(saved):
                previous._duplicates(refs, path, issues, 'duplicate_citation', '同一依据引用重复')
                for index, ref in enumerate(refs):
                    if ref not in originals:
                        issues.add('unknown_ref', path + '/' + str(index), '引用不在本业务的原文范围内', ref)
            try:
                from .report_business_flow_wire_v193 import validate_wire_value
                validate_wire_value(saved, **inputs); source = True
            except previous._PURE_ERRORS:
                issues.add('source_contract', '/business', '流程依据未通过完整原文核对，请对照相邻原文修订')
        result = previous._result(saved, issues.items, available=True, structural=structural,
            source=source, evidence=evidence)
        if len(previous._bytes(result)) > previous.MAX_RESULT_BYTES: raise previous._Capacity()
        return result
    except previous._Capacity:
        return previous._unavailable(saved, 'diagnostic_capacity', '完整诊断超过当前容量，草稿原样保留')
    except Exception:
        return previous._unavailable(saved, 'diagnostic_unavailable', '当前诊断无法完成，草稿保留且未视为通过')
