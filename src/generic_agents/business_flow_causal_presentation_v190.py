"""Human-readable original-candidate causal declarations, with no new grant.

Callers must cold-check the original capture/source and V190 projection first.
This read-only view checks internal consistency again, and never transfers an
original candidate's analysis to an edited manual draft or real runtime data.
"""
import copy
import re

from . import report_business_flow_wire_v190 as wire
from . import report_business_flow_wire_v184 as plans
from .contracts import digest


SCHEMA = 'business-flow-causal-source-view-v190'
ENTRY_LABEL = '流程触发或配置需提供的资料（待绑定）'
ERROR = '原候选的因果端口、编译位置或来源展示无法核对；未改写原稿或声称执行资格'
_FLAGS = {'semantic_quality_verified': False, 'source_authority_verified': False,
    'entry_facts_verified': False, 'permissions_verified': False,
    'external_dispatch_allowed': False, 'whole_report_verified': False,
    'can_apply': False, 'can_publish': False, 'can_execute': False}


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


def _same(left, right):
    return wire._bytes(left) == wire._bytes(right)


def _at(value, path):
    # These paths must already be exact members of V189's actual AST walk.
    # They contain only fixed schema keys and canonical original ordinals.
    _need(type(path) is str and path.startswith('/') and '~' not in path)
    for token in path.split('/')[1:]:
        if type(value) is dict:
            _need(token in value)
            value = value[token]
        elif type(value) is list:
            _need(re.fullmatch(r'0|[1-9][0-9]*', token) is not None)
            index = int(token)
            _need(index < len(value))
            value = value[index]
        else:
            _need(False)
    return value


def _node_ids(projection, actions):
    manifest = projection['compilation_manifest']
    inner = projection['plan_projection']
    raw_flows, flows = projection['raw_plan']['business']['flows'], projection['wire']['business']['flows']
    _need(manifest['schema'] == plans.COMPILATION and manifest['algorithm'] == plans.ALGORITHM
        and _same(manifest, inner['compilation_manifest'])
        and projection['compilation_manifest_hash'] == inner['compilation_manifest_hash'] == digest(manifest)
        and manifest['raw_plan_value_hash'] == digest(projection['raw_plan'])
        and manifest['compiled_wire_value_hash'] == digest(projection['wire'])
        and type(manifest['flows']) is list and len(manifest['flows']) == len(raw_flows) == len(flows))
    names = plans._display_names(raw_flows)
    mapping = {}
    for index, (raw_flow, flow, frozen) in enumerate(zip(raw_flows, flows, manifest['flows'], strict=True)):
        # Run the actual frozen pure compiler; do not guess IDs from names,
        # ordinals or hashes. It reads no catalog, source file, DB or permission.
        expected_flow, expected_manifest = plans._FlowCompiler(index, raw_flow, names[index]).compile()
        _need(_same(flow, expected_flow) and _same(frozen, expected_manifest))
        seen_ids = set()
        expected_paths = {item['path'] for item in actions if item['flow_index'] == index}
        matched = set()
        for item in frozen['nodes']:
            _need(type(item) is dict and set(item) == {'ast_path', 'node_id'}
                and type(item['ast_path']) is str and type(item['node_id']) is str
                and item['node_id'] not in seen_ids)
            seen_ids.add(item['node_id'])
            if item['ast_path'] not in expected_paths:
                # No row is inferred for a non-action synthetic compiler node.
                continue
            _need(item['ast_path'] not in mapping)
            mapping[item['ast_path']] = item['node_id']
            matched.add(item['ast_path'])
        _need(matched == expected_paths)
    _need(set(mapping) == {item['path'] for item in actions})
    return mapping


def build_view(projection, *, current_value):
    """Present every original input, preserving its exact raw source ordinal."""
    try:
        wire.new_manual(projection)
        analysis = projection['causal_analysis']
        actions = analysis['paths']['actions']
        sidecar = projection['compiled_sidecar']
        raw_plan = projection['raw_plan']
        node_ids = _node_ids(projection, actions)
        by_input, by_output, entries = {}, {}, {}
        action_values = {}
        for action in actions:
            _need(action['path'] not in action_values)
            original = _at(raw_plan, action['path'])
            action_values[action['path']] = original
            for field, table in (('input_ports', by_input), ('output_ports', by_output)):
                for port in action[field]:
                    _need(port['path'] not in table and _at(raw_plan, port['path']) == port['label'])
                    table[port['path']] = (action, port)
        for entry in sidecar['entry_ports']:
            _need(entry['path'] not in entries)
            entries[entry['path']] = entry
        bindings = {item['input_path']: item for item in sidecar['input_bindings']}
        _need(len(bindings) == len(sidecar['input_bindings']) and set(bindings) == set(by_input))
        evidence_refs = {item['span_ref'] for item in projection['wire']['evidence']}
        rows = []
        for path in analysis['paths']['input_paths']:
            action, port = by_input[path]
            original = action_values[action['path']]
            _need(set(original['attribution']['citations']) <= evidence_refs)
            sources = []
            for source_path in bindings[path]['sources']:
                if source_path in entries:
                    _need(entries[source_path]['flow_path'] == action['flow_path'])
                    sources.append({'kind': 'entry', 'label': ENTRY_LABEL})
                else:
                    _need(source_path in by_output)
                    producer, output = by_output[source_path]
                    _need(producer['flow_path'] == action['flow_path'])
                    raw_producer = action_values[producer['path']]
                    sources.append({'kind': 'step', 'step_name': raw_producer['name'],
                        'output_name': output['label'], 'actor': raw_producer['actor'], 'role': raw_producer['role']})
            rows.append({'flow_index': action['flow_index'],
                'flow_name': projection['wire']['business']['flows'][action['flow_index']]['name'],
                'node_id': node_ids[action['path']], 'step_name': original['name'],
                'input_name': port['label'], 'sources': sources,
                'citations': copy.deepcopy(original['attribution']['citations'])})
        _need(len(rows) == len(by_input))
        value = {'schema': SCHEMA, 'original_candidate_only': True,
            'runtime_entry_binding_verified': False,
            'current_draft_matches_original': _same(current_value, projection['wire']),
            'baseline_value_hash': digest(projection['wire']), 'current_value_hash': digest(current_value),
            'projection_hash': projection['projection_hash'],
            'compiled_sidecar_hash': projection['compiled_sidecar_hash'],
            'causal_analysis_hash': projection['causal_analysis_hash'],
            'causal_declaration_consistency_only': True, 'rows': rows, **_FLAGS}
        return {**value, 'view_hash': digest(value)}
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None
