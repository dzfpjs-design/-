"""Pure human-review diagnostics alongside the unchanged V182 strong gates.

Safe bounded JSON may be an unfinished draft. No candidate is repaired,
normalized, completed or adopted. Platform-resolved original text is returned
separately from the model/human quote. Callers must supply independently checked
cold source inputs and enforce their own IAM, persistence and original-wire
parser; this value API cannot verify HTTP bytes or duplicate original JSON keys.
"""
import copy

from pydantic import ValidationError

from . import report_business_flow_wire_v182 as wire
from .contracts import digest
from .report_evidence_plan import resolve_evidence
from .report_full_reading_semantics import _safe
from .typed_payload_codec_v131 import canonical_json

SCHEMA = 'business-flow-review-diagnostics-v1'
MAX_VALUE_BYTES = wire.MAX_WIRE_BYTES
MAX_RESULT_BYTES = wire.MAX_PROJECTION_BYTES
MAX_ISSUES = 1024
_PURE_ERRORS = (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError)
_UNION_LABELS = {'SequenceEdgeV182', 'ConditionEdgeV182', 'ParallelEdgeV182', 'WaitEdgeV182'}
_FALSE_FLAGS = {'semantic_quality_verified': False, 'can_apply': False, 'can_publish': False, 'can_execute': False}


class _Capacity(ValueError): pass


def _bytes(value): return canonical_json(value).encode('utf-8', errors='strict')


def _json(value, ancestors=(), depth=0):
    if depth > 48: raise ValueError('unsafe_json')
    kind = type(value)
    if value is None or kind in {str, bool, int, float}: return
    if kind not in {dict, list} or any(value is parent for parent in ancestors): raise ValueError('unsafe_json')
    if kind is dict:
        for key, item in value.items():
            if type(key) is not str: raise ValueError('unsafe_json')
            _json(item, (*ancestors, value), depth + 1)
    else:
        for item in value: _json(item, (*ancestors, value), depth + 1)


def _pointer(parts):
    return ''.join('/' + str(item).replace('~', '~0').replace('/', '~1') for item in parts)


def _shape_path(value, location):
    # Union model names are Pydantic error branch labels, not JSON properties.
    parts, current = [], value
    for item in location:
        if type(current) is dict and item in current:
            parts.append(item); current = current[item]
        elif type(current) is list and type(item) is int and 0 <= item < len(current):
            parts.append(item); current = current[item]
        elif item in _UNION_LABELS:
            continue
        else:
            parts.append(item); current = None
    return _pointer(parts)


class _Issues:
    def __init__(self): self.items, self.seen = [], set()

    def add(self, code, path, message, span_ref=None):
        ref = span_ref if type(span_ref) is str and len(span_ref) <= 100 else None
        key = (code, path, message, ref)
        if key in self.seen: return
        if len(self.items) >= MAX_ISSUES: raise _Capacity()
        self.seen.add(key)
        self.items.append({'code': code, 'path': path, 'message': message, 'span_ref': ref})


def _result(value, issues, *, available, structural=False, source=False, evidence=None):
    return {'schema': SCHEMA, 'available': available, 'value': value, 'issues': issues,
        'source_evidence': evidence or [], 'structural_valid': structural, 'source_valid': source, **_FALSE_FLAGS}


def _unavailable(value, code, message):
    return _result(value, [{'code': code, 'path': '', 'message': message, 'span_ref': None}], available=False)


def _duplicates(values, path, issues, code, message):
    seen = set()
    for index, value in enumerate(values):
        if value in seen: issues.add(code, path + '/' + str(index), message)
        seen.add(value)


def _citation_paths(value):
    for f, flow in enumerate(value['business']['flows']):
        root = '/business/flows/' + str(f)
        yield root + '/trigger/attribution/citations', flow['trigger']['attribution']['citations']
        for name in ('nodes', 'edges', 'deliverables'):
            for i, item in enumerate(flow[name]):
                yield root + '/' + name + '/' + str(i) + '/attribution/citations', item['attribution']['citations']
    for i, question in enumerate(value['business']['questions']):
        yield '/business/questions/' + str(i) + '/citations', question['citations']


def _graph_issues(value, issues):
    flows = value['business']['flows']
    _duplicates([flow['flow_id'] for flow in flows], '/business/flows', issues, 'duplicate_id', '流程编号重复；请保留不同流程的独立编号')
    _duplicates([flow['name'] for flow in flows], '/business/flows', issues, 'duplicate_flow_name', '流程名称重复')
    for f, flow in enumerate(flows):
        root = '/business/flows/' + str(f); nodes = flow['nodes']
        ids = [node['node_id'] for node in nodes]; known = set(ids); by_id = {node['node_id']: node for node in nodes}
        for i, identity in enumerate(ids):
            if identity in ids[:i]: issues.add('duplicate_id', root + '/nodes/' + str(i) + '/node_id', '节点编号重复；连线目标无法唯一确定')
        start, ends = flow['start_node'], flow['end_nodes']
        if start not in known: issues.add('missing_start', root + '/start_node', '起始节点不存在')
        _duplicates(ends, root + '/end_nodes', issues, 'duplicate_end', '结束节点重复')
        for i, identity in enumerate(ends):
            if identity not in known: issues.add('missing_end', root + '/end_nodes/' + str(i), '结束节点不存在')
        adjacency = {identity: set() for identity in known}; reverse = {identity: set() for identity in known}
        signatures, groups = set(), {}
        for i, edge in enumerate(flow['edges']):
            path = root + '/edges/' + str(i); signature = canonical_json(edge)
            if signature in signatures: issues.add('duplicate_edge', path, '连线完全重复')
            signatures.add(signature)
            wait = edge['kind'] == 'wait'; origins = edge['from_nodes'] if wait else [edge['from_node']]
            if wait: _duplicates(origins, path + '/from_nodes', issues, 'duplicate_origin', '等待来源节点重复')
            target = edge['to_node']
            if target not in known: issues.add('missing_target', path + '/to_node', '连线目标节点不存在')
            for n, origin in enumerate(origins):
                origin_path = path + ('/from_nodes/' + str(n) if wait else '/from_node')
                if origin not in known: issues.add('missing_origin', origin_path, '连线来源节点不存在')
                if edge['kind'] != 'condition' and origin == target:
                    issues.add('self_edge', path, '此类连线不能连接节点自身')
                if origin in known and target in known:
                    adjacency[origin].add(target); reverse[target].add(origin)
            if edge['kind'] == 'parallel': groups.setdefault(edge['group'], []).append((i, edge['from_node'], target))
        for members in groups.values():
            if len(members) < 2 or len({item[1] for item in members}) != 1 or len({item[2] for item in members}) != len(members):
                issues.add('invalid_parallel_group', root + '/edges/' + str(members[0][0]) + '/group',
                    '同一并行组需要同一个起点和至少两个不同分支')
        for i, identity in enumerate(ends):
            if identity in known and adjacency[identity]: issues.add('end_has_outgoing', root + '/end_nodes/' + str(i), '结束节点仍有后续连线')
        reached = wire._reachable([start], adjacency) if start in known else known
        valid_ends = [identity for identity in ends if identity in known]
        ending = wire._reachable(valid_ends, reverse) if valid_ends else known
        for i, node in enumerate(nodes):
            path = root + '/nodes/' + str(i)
            if node['node_id'] not in reached: issues.add('unreachable', path + '/node_id', '无法从起始节点到达此节点')
            if node['node_id'] not in ending: issues.add('cannot_reach_end', path + '/node_id', '此节点没有可到达结束节点的路径')
            _duplicates(node['inputs'], path + '/inputs', issues, 'duplicate_input', '节点输入名称重复')
            _duplicates(node['outputs'], path + '/outputs', issues, 'duplicate_output', '节点结果名称重复')
        _duplicates([item['name'] for item in flow['deliverables']], root + '/deliverables', issues, 'duplicate_deliverable', '交付结果名称重复')
        for i, item in enumerate(flow['deliverables']):
            path = root + '/deliverables/' + str(i)
            _duplicates(item['produced_by'], path + '/produced_by', issues, 'duplicate_producer', '结果产生节点重复')
            for n, identity in enumerate(item['produced_by']):
                if identity not in known: issues.add('missing_producer', path + '/produced_by/' + str(n), '结果产生节点不存在')
                elif item['name'] not in by_id[identity]['outputs']:
                    issues.add('deliverable_output_missing', path + '/produced_by/' + str(n), '指定节点的结果中没有此项交付名称')


def _evidence_issues(value, originals, issues, *, shaped):
    entries = value.get('evidence') if type(value) is dict else None
    entries = entries if type(entries) is list else []
    refs = []; okay = True
    for i, entry in enumerate(entries):
        if type(entry) is not dict or type(entry.get('span_ref')) is not str: continue
        ref = entry['span_ref']; path = '/evidence/' + str(i)
        if ref in refs:
            issues.add('duplicate_evidence', path + '/span_ref', '同一原片段在依据表中重复', ref); okay = False
        refs.append(ref)
        if ref not in originals:
            issues.add('unknown_ref', path + '/span_ref', '该引用不在本范围已冷核的原文片段内，不能猜配其他来源', ref); okay = False
        elif type(entry.get('quote')) is str and entry['quote'] != originals[ref]:
            issues.add('quote_mismatch', path + '/quote', '此处文字不同于平台解析的原片段全文；请对照原文人工核对，系统未改写原答', ref); okay = False
    if shaped:
        cited = set()
        for path, citations in _citation_paths(value):
            _duplicates(citations, path, issues, 'duplicate_citation', '同一依据引用重复')
            for i, ref in enumerate(citations):
                cited.add(ref)
                if ref not in originals:
                    issues.add('unknown_ref', path + '/' + str(i), '此引用不在本范围原文内', ref); okay = False
                if ref not in refs:
                    issues.add('missing_evidence', path + '/' + str(i), '引用尚未在依据表中列出对应原片段全文', ref); okay = False
        for i, ref in enumerate(refs):
            if ref not in cited:
                issues.add('unused_evidence', '/evidence/' + str(i) + '/span_ref', '依据表中的此片段没有被流程或待核问题引用', ref); okay = False
    return okay


def review(value, *, scope_binding, catalog, allowed_span_refs, context, input_claims):
    """Return a detached, bounded diagnostic; valid flags never authorize use.

    Shape/graph/source validity are still decided by the ACTUAL unchanged wire
    functions. The extra scans only explain failures and can never make those
    strong functions pass. No raw provider exception or source context leaks.
    """
    try:
        _json(value); _safe(value)
        if len(_bytes(value)) > MAX_VALUE_BYTES: raise _Capacity()
        saved = copy.deepcopy(value)
    except _Capacity:
        return _unavailable(None, 'draft_capacity', '草稿超过当前诊断容量；未裁剪或修写原输入')
    except _PURE_ERRORS:
        return _unavailable(None, 'unsafe_draft', '输入不是可安全保存的有界JSON；原输入未修改')
    try:
        issues = _Issues(); shaped = False
        try:
            checked = wire.BusinessFlowWireV182.model_validate(saved).model_dump()
            shaped = _bytes(checked) == _bytes(saved)
            if not shaped: issues.add('shape', '', '字段校验会改变原输入，不能作为完整流程通过')
        except ValidationError as error:
            if error.error_count() > MAX_ISSUES: raise _Capacity()
            for item in error.errors(include_url=False, include_context=False, include_input=False):
                issues.add('shape', _shape_path(saved, item['loc']), '字段不符合当前流程合同：' + item['type'])
        # This disposable view checks ALL original source/context/claim pins
        # without claiming that any answer quote is already correct. It is
        # never returned or saved as a candidate. The untouched answer passes
        # the full _source gate again below, including its actual quotes.
        inputs = copy.deepcopy({'scope_binding': scope_binding, 'catalog': catalog,
            'allowed_span_refs': allowed_span_refs, 'context': context, 'input_claims': input_claims})
        try:
            binding, _ = wire._source({'evidence': []}, **inputs)
            spans = resolve_evidence(inputs['catalog'], inputs['allowed_span_refs'], inputs['allowed_span_refs'])
        except _PURE_ERRORS:
            issues.add('source_unavailable', '', '本范围的原来源、确认范围或声明冻结依据无法冷核；未借用当前其他来源')
            return _result(saved, issues.items, available=False)
        source_evidence = [{'span_ref': item['span_ref'], 'source_text': item['text'],
            'text_origin': 'platform_resolved_source'} for item in spans]
        originals = {item['span_ref']: item['text'] for item in spans}
        evidence_okay = _evidence_issues(saved, originals, issues, shaped=shaped)
        structural, source = False, False
        if shaped:
            _graph_issues(saved, issues)
            binding_okay = saved['binding_hash'] == digest(binding) and saved['business']['name'] == binding['name']
            if saved['binding_hash'] != digest(binding): issues.add('binding_mismatch', '/binding_hash', '回答与本范围冻结依据不一致')
            if saved['business']['name'] != binding['name']: issues.add('binding_mismatch', '/business/name', '业务名称不等于已确认的本范围名称')
            try:
                wire._wire_value(saved, binding); structural = True
            except _PURE_ERRORS:
                issues.add('graph_contract', '/business', '流程未通过原有完整结构、范围或图校验；请核对定位问题')
            try:
                wire._source(saved, **inputs); source = binding_okay and evidence_okay
            except _PURE_ERRORS:
                if evidence_okay: issues.add('source_contract', '/evidence', '依据未通过原有完整来源校验')
        result = _result(saved, issues.items, available=True, structural=structural,
            source=source, evidence=source_evidence)
        if len(_bytes(result)) > MAX_RESULT_BYTES: raise _Capacity()
        return result
    except _Capacity:
        return _unavailable(saved, 'diagnostic_capacity', '完整诊断超过当前容量；未截断为已通过结果，草稿原样保留')
    except Exception:
        # Known bad shape/source is explained above. Unexpected diagnostic
        # failure must remain unavailable and cannot accidentally grant validity.
        return _unavailable(saved, 'diagnostic_unavailable', '当前无法完成流程诊断；草稿原样保留，未视为通过')
