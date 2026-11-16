"""Independent structured flow plans compiled to the unchanged V182 draft.

Only V184 plans enter this compiler. Position identifiers and graph edges are
platform derivations; quotes come from the frozen source catalog. Neither a
successful compilation nor its manifest grants semantic/execution authority.
"""
import copy
import hashlib
import json
from collections import Counter
from typing import Literal

from pydantic import Field

from . import report_business_flow_wire_v182 as previous
from .contracts import digest
from .report_full_reading_semantics import Closed

BINDING = previous.BINDING
WIRE = 'report-business-flow-plan-v184'
PROJECTION = 'report-business-flow-projection-v184'
COMPILATION = 'report-business-flow-compilation-manifest-v184'
ALGORITHM = 'positional-phases-to-v182-v1'
MANUAL = previous.MANUAL
MAX_WIRE_BYTES = previous.MAX_WIRE_BYTES
MAX_PROJECTION_BYTES = previous.MAX_PROJECTION_BYTES
MAX_NODES_PER_FLOW = 32
MAX_EDGES_PER_FLOW = 128
ERROR = '结构化流程计划、原文或编译结果无法核对；原回答保留，未修写或执行'
check_binding = previous.check_binding
Attribution = previous.AttributionV182
Trigger = previous.TriggerV182
Question = previous.QuestionV182
Text = previous.Text


class BusinessFlowPlanCapacityError(ValueError):
    """The complete plan cannot fit the existing draft; never truncate it."""


def _need(value):
    if not value:
        raise ValueError(ERROR)


def _bytes(value):
    return previous._bytes(value)


def _same(left, right):
    return _bytes(left) == _bytes(right)


def _set(items):
    """Stable set semantics, with no trimming or text/step normalization."""
    return list(dict.fromkeys(items))


class ActionV184(Closed):
    name: previous.Name
    actor: Literal['business_agent', 'perception_agent', 'data_agent',
        'task_center', 'execution_service', 'human']
    role: str = Field(min_length=2, max_length=200, pattern=r'\S')
    responsibility: Text
    inputs: list[previous.Name] = Field(max_length=20)
    outputs: list[previous.OutputName] = Field(min_length=1, max_length=20)
    attribution: Attribution


class StepsV184(Closed):
    kind: Literal['steps']
    attribution: Attribution
    steps: list[ActionV184] = Field(min_length=1, max_length=16)


class DecisionBranchV184(Closed):
    condition: Text
    attribution: Attribution
    steps: list[ActionV184] = Field(min_length=0, max_length=16)


class DecisionV184(Closed):
    kind: Literal['decision']
    attribution: Attribution
    decision: ActionV184
    branches: list[DecisionBranchV184] = Field(min_length=2, max_length=6)


class ParallelBranchV184(Closed):
    steps: list[ActionV184] = Field(min_length=1, max_length=16)


class ParallelV184(Closed):
    kind: Literal['parallel']
    attribution: Attribution
    dispatch: ActionV184
    branches: list[ParallelBranchV184] = Field(min_length=2, max_length=6)
    wait_mode: Literal['all', 'any']
    join_attribution: Attribution


class RepeatV184(Closed):
    kind: Literal['repeat']
    attribution: Attribution
    steps: list[ActionV184] = Field(min_length=1, max_length=16)
    check: ActionV184
    repeat_if: Text
    finish_if: Text


class FlowV184(Closed):
    name: str = Field(min_length=1, max_length=180, pattern=r'\S')
    trigger: Trigger
    phase_order: Attribution
    phases: list[StepsV184 | DecisionV184 | ParallelV184 | RepeatV184] = Field(min_length=0, max_length=12)
    completion: ActionV184


class BusinessDefinitionV184(Closed):
    name: previous.Name
    flows: list[FlowV184] = Field(min_length=1, max_length=16)
    questions: list[Question] = Field(max_length=80)


class BusinessFlowPlanV184(Closed):
    schema: Literal['report-business-flow-plan-v184']
    binding_hash: previous.Hash
    business: BusinessDefinitionV184


def _schema_for_shape(name, binding_hash):
    _need(type(name) is str and 1 <= len(name) <= 200 and bool(name.strip()))
    previous._hash(binding_hash)
    value = BusinessFlowPlanV184.model_json_schema()
    value['properties']['binding_hash']['const'] = binding_hash
    value['$defs']['BusinessDefinitionV184']['properties']['name']['const'] = name
    return value


def output_schema(scope_binding):
    binding = check_binding(scope_binding)
    return _schema_for_shape(binding['name'], digest(binding))


def manual_value_schema(scope_binding):
    return previous.manual_value_schema(scope_binding)


def _plan_value(value, scope_binding):
    binding = check_binding(scope_binding)
    checked = BusinessFlowPlanV184.model_validate(value).model_dump()
    _need(_same(checked, value) and checked['binding_hash'] == digest(binding)
        and checked['business']['name'] == binding['name'])
    return copy.deepcopy(checked)


def parse_plan(raw, *, scope_binding):
    """Strict original V184 structure; no catalog, repair or authority here."""
    try:
        _need(type(raw) is bytes and 0 < len(raw) <= MAX_WIRE_BYTES)
        value = json.loads(raw.decode('utf-8', errors='strict'),
            object_pairs_hook=previous._pairs, parse_constant=previous._constant)
        return _plan_value(value, scope_binding)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def _attribution(value):
    return {**copy.deepcopy(value), 'citations': _set(value['citations'])}


def _raw_references(plan):
    """Include even AST attributions which produce no graph edge."""
    refs = []
    def add(value):
        refs.extend(value['citations'])
    def actions(values):
        for action in values:
            add(action['attribution'])
    for flow in plan['business']['flows']:
        add(flow['trigger']['attribution']); add(flow['phase_order'])
        for phase in flow['phases']:
            add(phase['attribution'])
            if phase['kind'] in {'steps', 'repeat'}:
                actions(phase['steps'])
            elif phase['kind'] == 'decision':
                actions([phase['decision']])
                for branch in phase['branches']:
                    add(branch['attribution']); actions(branch['steps'])
            else:
                actions([phase['dispatch']]); add(phase['join_attribution'])
                for branch in phase['branches']:
                    actions(branch['steps'])
            if phase['kind'] == 'repeat':
                actions([phase['check']])
        actions([flow['completion']])
    for question in plan['business']['questions']:
        refs.extend(question['citations'])
    return _set(refs)


def _display_names(flows):
    counts = Counter(flow['name'] for flow in flows)
    reserved = set(counts); used = set(); result = []
    for index, flow in enumerate(flows, 1):
        raw = flow['name']; name = raw
        if counts[raw] > 1:
            name = raw + '（流程' + str(index) + '）'
            serial = 1
            while name in reserved or name in used:
                name = raw + '（流程' + str(index) + '·' + str(serial) + '）'
                serial += 1
        _need(len(name) <= 200 and name not in used)
        used.add(name); result.append(name)
    return result


class _FlowCompiler:
    def __init__(self, index, raw, display):
        self.index, self.raw, self.display = index, raw, display
        self.path = '/business/flows/' + str(index)
        self.flow_id = 'flow_' + str(index + 1).zfill(2)
        self.nodes, self.edges, self.node_map, self.edge_map = [], [], [], []
        self.edge_keys = {}

    def node(self, action, path):
        if len(self.nodes) >= MAX_NODES_PER_FLOW:
            raise BusinessFlowPlanCapacityError('完整流程编译超过每流程32个节点；未截断或生成部分流程')
        identity = 'f' + str(self.index + 1).zfill(2) + '_n' + str(len(self.nodes) + 1).zfill(2)
        self.nodes.append({'node_id': identity, **copy.deepcopy(action),
            'inputs': _set(action['inputs']), 'outputs': _set(action['outputs']),
            'attribution': _attribution(action['attribution'])})
        self.node_map.append({'node_id': identity, 'ast_path': path})
        return identity

    def edge(self, kind, origin, target, attribution, paths, **extra):
        value = {'kind': kind, **({'from_nodes': _set(origin)} if kind == 'wait' else {'from_node': origin}),
            'to_node': target, **extra, 'attribution': _attribution(attribution)}
        key = _bytes(value)
        if key in self.edge_keys:
            item = self.edge_map[self.edge_keys[key]]
            item['ast_paths'] = _set(item['ast_paths'] + paths)
            return
        if len(self.edges) >= MAX_EDGES_PER_FLOW:
            raise BusinessFlowPlanCapacityError('完整流程编译超过每流程128条连线；未截断或生成部分流程')
        self.edge_keys[key] = len(self.edges)
        self.edge_map.append({'edge_index': len(self.edges), 'ast_paths': _set(paths)})
        self.edges.append(value)

    def segment(self, actions, path, attribution, attribution_path):
        nodes = [self.node(action, path + '/' + str(i)) for i, action in enumerate(actions)]
        for index in range(len(nodes) - 1):
            self.edge('sequence', nodes[index], nodes[index + 1], attribution,
                [path + '/' + str(index), path + '/' + str(index + 1), attribution_path])
        return nodes

    def compile(self):
        phases = []
        for index, phase in enumerate(self.raw['phases']):
            path = self.path + '/phases/' + str(index)
            att_path = path + '/attribution'
            item = {'phase': phase, 'path': path}
            if phase['kind'] in {'steps', 'repeat'}:
                item['steps'] = self.segment(phase['steps'], path + '/steps', phase['attribution'], att_path)
                item['entry'] = item['steps'][0]
                if phase['kind'] == 'repeat':
                    item['check'] = self.node(phase['check'], path + '/check')
                    self.edge('sequence', item['steps'][-1], item['check'], phase['attribution'],
                        [path + '/steps/' + str(len(phase['steps']) - 1), path + '/check', att_path])
            else:
                key = 'decision' if phase['kind'] == 'decision' else 'dispatch'
                item['entry'] = self.node(phase[key], path + '/' + key)
                item['branches'] = [self.segment(branch['steps'], path + '/branches/' + str(i) + '/steps',
                    phase['attribution'], att_path) for i, branch in enumerate(phase['branches'])]
            phases.append(item)
        completion = self.node(self.raw['completion'], self.path + '/completion')
        for index, item in enumerate(phases):
            phase, path = item['phase'], item['path']
            following = phases[index + 1]['entry'] if index + 1 < len(phases) else completion
            next_path = phases[index + 1]['path'] if index + 1 < len(phases) else self.path + '/completion'
            order_paths = [path, next_path, self.path + '/phase_order']
            if phase['kind'] == 'steps':
                self.edge('sequence', item['steps'][-1], following, self.raw['phase_order'], order_paths)
            elif phase['kind'] == 'decision':
                for branch_index, (branch, nodes) in enumerate(zip(phase['branches'], item['branches'], strict=True)):
                    branch_path = path + '/branches/' + str(branch_index)
                    self.edge('condition', item['entry'], nodes[0] if nodes else following,
                        branch['attribution'], [path + '/decision', branch_path, branch_path + '/condition'],
                        condition=branch['condition'])
                    if nodes:
                        self.edge('sequence', nodes[-1], following, self.raw['phase_order'],
                            order_paths + [branch_path + '/steps/' + str(len(nodes) - 1)])
            elif phase['kind'] == 'parallel':
                group = 'f' + str(self.index + 1).zfill(2) + '_p' + str(index + 1).zfill(2) + '_parallel'
                for branch_index, nodes in enumerate(item['branches']):
                    self.edge('parallel', item['entry'], nodes[0], phase['attribution'],
                        [path + '/dispatch', path + '/branches/' + str(branch_index), path + '/attribution'], group=group)
                self.edge('wait', [nodes[-1] for nodes in item['branches']], following,
                    phase['join_attribution'], [path + '/branches', path + '/wait_mode', path + '/join_attribution', next_path],
                    mode=phase['wait_mode'])
            else:
                self.edge('condition', item['check'], item['steps'][0], phase['attribution'],
                    [path + '/check', path + '/repeat_if', path + '/attribution'], condition=phase['repeat_if'])
                self.edge('condition', item['check'], following, phase['attribution'],
                    [path + '/check', path + '/finish_if', path + '/attribution', next_path], condition=phase['finish_if'])
        trigger = copy.deepcopy(self.raw['trigger'])
        trigger['attribution'] = _attribution(trigger['attribution'])
        flow = {'flow_id': self.flow_id, 'name': self.display, 'trigger': trigger,
            'start_node': phases[0]['entry'] if phases else completion,
            'end_nodes': [completion], 'nodes': self.nodes, 'edges': self.edges,
            'deliverables': [{'name': name, 'description': self.raw['completion']['responsibility'],
                'produced_by': [completion], 'attribution': _attribution(self.raw['completion']['attribution'])}
                for name in _set(self.raw['completion']['outputs'])]}
        manifest = {'flow_id': self.flow_id, 'ast_path': self.path, 'raw_name': self.raw['name'],
            'compiled_name': self.display, 'nodes': self.node_map, 'edges': self.edge_map}
        return flow, manifest


def _compile(plan, originals, allowed_span_refs):
    flows, mappings = [], []
    names = _display_names(plan['business']['flows'])
    for index, (flow, name) in enumerate(zip(plan['business']['flows'], names, strict=True)):
        compiled, mapping = _FlowCompiler(index, flow, name).compile()
        flows.append(compiled); mappings.append(mapping)
    questions = [{**copy.deepcopy(item), 'citations': _set(item['citations'])}
        for item in plan['business']['questions']]
    value = {'schema': previous.WIRE, 'binding_hash': plan['binding_hash'],
        'business': {'name': plan['business']['name'], 'flows': flows, 'questions': questions}}
    used = set()
    for attribution in previous._attributions(value):
        used.update(attribution['citations'])
    for question in questions:
        used.update(question['citations'])
    ordered = [ref for ref in _set(allowed_span_refs) if ref in used]
    _need(set(ordered) == used)
    value['evidence'] = [{'span_ref': ref, 'quote': originals[ref]} for ref in ordered]
    if len(value['evidence']) > 256:
        raise BusinessFlowPlanCapacityError('完整流程引用超过现有人工稿256条依据容量；未丢弃或截断')
    return value, mappings, ordered


def build_projection(raw, *, scope_binding, catalog, allowed_span_refs, context, input_claims):
    plan = parse_plan(raw, scope_binding=scope_binding)
    # Validate ALL model citations, even a phase attribution not selected for a
    # derived edge. The full independent source/claim binding is checked again
    # by the unchanged V182 final validator below.
    spans = previous.resolve_evidence(catalog, allowed_span_refs, allowed_span_refs)
    originals = {item['span_ref']: item['text'] for item in spans}
    _need(set(_raw_references(plan)) <= set(allowed_span_refs)
        and set(_raw_references(plan)) <= set(originals))
    compiled, mappings, used_refs = _compile(plan, originals, allowed_span_refs)
    compiled_raw = _bytes(compiled)
    if len(compiled_raw) > previous.MAX_WIRE_BYTES:
        raise BusinessFlowPlanCapacityError('完整编译流程超过现有人工稿1MiB容量；未截断或生成部分流程')
    checked = previous.build_projection(compiled_raw, scope_binding=scope_binding,
        catalog=catalog, allowed_span_refs=allowed_span_refs, context=context, input_claims=input_claims)
    manifest = {'schema': COMPILATION, 'algorithm': ALGORITHM, 'raw_plan_schema': WIRE,
        'raw_plan_value_hash': digest(plan), 'raw_plan_bytes_sha256': hashlib.sha256(raw).hexdigest(),
        'compiled_wire_schema': previous.WIRE, 'compiled_wire_value_hash': digest(compiled),
        'compiled_wire_bytes_sha256': hashlib.sha256(compiled_raw).hexdigest(),
        'node_id_origin': 'platform_positional', 'edge_origin': 'platform_phase_compilation',
        'quote_origin': 'platform_resolved_source',
        'set_semantics': ['inputs', 'outputs', 'citations', 'from_nodes', 'produced_by', 'identical_compiled_edges'],
        'flows': mappings, 'quotes': [{'span_ref': ref, 'allowed_position': allowed_span_refs.index(ref),
            'text_sha256': hashlib.sha256(originals[ref].encode('utf-8', errors='strict')).hexdigest()} for ref in used_refs],
        'semantic_quality_verified': False, 'can_apply': False, 'can_publish': False, 'can_execute': False}
    value = {key: copy.deepcopy(item) for key, item in checked.items()
        if key not in {'schema', 'projection_hash', 'wire_bytes_sha256', 'wire_bytes'}}
    value.update(schema=PROJECTION, raw_plan_schema=WIRE, raw_plan=plan,
        raw_plan_value_hash=digest(plan), raw_plan_bytes_sha256=hashlib.sha256(raw).hexdigest(), raw_plan_bytes=len(raw),
        compiled_wire_bytes_sha256=hashlib.sha256(compiled_raw).hexdigest(), compiled_wire_bytes=len(compiled_raw),
        compilation_manifest=manifest, compilation_manifest_hash=digest(manifest))
    value['projection_hash'] = digest(value)
    if len(_bytes(value)) > MAX_PROJECTION_BYTES:
        raise BusinessFlowPlanCapacityError('完整计划、编译流程及来源超过8MiB投影容量；未截断或保存部分结果')
    return value


def check_projection(value, raw, **expected_inputs):
    expected = build_projection(raw, **expected_inputs)
    _need(type(value) is dict and _same(value, expected))
    return expected


def new_manual(projection):
    """A V182 draft after checked compilation, never an adoption grant."""
    _need(type(projection) is dict and projection.get('schema') == PROJECTION
        and projection.get('projection_hash') == digest({key: item for key, item in projection.items() if key != 'projection_hash'}))
    manifest = projection.get('compilation_manifest', {})
    _need(manifest.get('schema') == COMPILATION and manifest.get('algorithm') == ALGORITHM
        and projection.get('compilation_manifest_hash') == digest(manifest)
        and manifest.get('raw_plan_value_hash') == digest(projection['raw_plan'])
        and manifest.get('compiled_wire_value_hash') == digest(projection['wire']))
    # The legacy helper creates acknowledgements only. Supply its explicitly
    # compiled DTO; do not claim its wire byte hash describes model output.
    view = {'schema': previous.PROJECTION, 'wire': copy.deepcopy(projection['wire']),
        'scope_binding': copy.deepcopy(projection['scope_binding'])}
    view['projection_hash'] = digest(view)
    return previous.new_manual(view)


def validate_value(value, *, scope_binding, catalog, allowed_span_refs, context, input_claims):
    return previous.validate_value(value, scope_binding=scope_binding, catalog=catalog,
        allowed_span_refs=allowed_span_refs, context=context, input_claims=input_claims)


def confirm_value(value):
    return previous.confirm_value(value)
