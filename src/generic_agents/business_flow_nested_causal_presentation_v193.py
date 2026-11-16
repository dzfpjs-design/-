"""Read-only original-candidate port provenance for a checked V193 projection.

The caller must cold-check the capture and independent source first. This view
rechecks internal plan/sidecar/analysis consistency and resolves exact original
ordinals. Edited human drafts never inherit the original candidate's causal
declarations, and an entry is always displayed as a proposed interface.
"""
import copy
import re

from . import report_business_flow_wire_v193 as wire
from .contracts import digest


SCHEMA = 'business-flow-nested-causality-view-v193'
ENTRY_LABEL = '流程触发或配置需提供的资料（待绑定）'
ERROR = '原递归候选的动作、端口或来源展示无法核对；未展平或赋予执行资格'
_FLAGS = {'semantic_quality_verified': False, 'source_authority_verified': False,
    'entry_facts_verified': False, 'permissions_verified': False,
    'external_dispatch_allowed': False, 'whole_report_verified': False,
    'can_apply': False, 'can_publish': False, 'can_execute': False}


def _need(condition):
    if not condition:
        raise ValueError(ERROR)


def _at(value, path):
    # Paths are actual members of the rebuilt V192 walk. Only fixed schema
    # tokens and canonical ordinals occur; no names or guessed suffixes.
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


def build_view(projection, *, current_value):
    """Map every original input ordinal to its exact declared source ports."""
    try:
        wire.new_manual(projection)
        analysis, plan = projection['causal_analysis'], projection['wire']
        sidecar = projection['compiled_sidecar']
        actions = analysis['paths']['actions']
        by_input, by_output, originals = {}, {}, {}
        for action in actions:
            path = action['path']
            _need(path not in originals)
            original = _at(plan, path)
            _need(original['name'] == action['name'] and original['actor'] == action['actor']
                and original['role'] == action['role'])
            originals[path] = original
            for field, table in (('input_ports', by_input), ('output_ports', by_output)):
                for port in action[field]:
                    _need(port['path'] not in table and _at(plan, port['path']) == port['label'])
                    table[port['path']] = (action, port)
        entries = {item['path']: item for item in sidecar['entry_ports']}
        inputs = {item['input_path']: item for item in sidecar['input_bindings']}
        _need(len(entries) == len(sidecar['entry_ports']) and len(inputs) == len(sidecar['input_bindings'])
            and set(inputs) == set(by_input))
        evidence_refs = {item['span_ref'] for item in projection['evidence']}
        rows = []
        for path in analysis['paths']['input_paths']:
            action, port = by_input[path]
            original = originals[action['path']]
            _need(set(original['attribution']['citations']) <= evidence_refs)
            sources = []
            for source in inputs[path]['sources']:
                if source in entries:
                    _need(entries[source]['flow_path'] == action['flow_path'])
                    sources.append({'kind': 'entry', 'entry_path': source, 'label': ENTRY_LABEL})
                else:
                    _need(source in by_output)
                    producer, output = by_output[source]
                    _need(producer['flow_path'] == action['flow_path'])
                    raw_producer = originals[producer['path']]
                    sources.append({'kind': 'step', 'action_path': producer['path'],
                        'step_name': raw_producer['name'], 'output_path': source,
                        'output_ordinal': output['ordinal'], 'output_name': output['label'],
                        'actor': raw_producer['actor'], 'role': raw_producer['role']})
            rows.append({'flow_index': action['flow_index'],
                'flow_name': plan['business']['flows'][action['flow_index']]['name'],
                'action_path': action['path'], 'action_name': original['name'], 'step_name': original['name'],
                'actor': original['actor'], 'role': original['role'],
                'control_ancestors': copy.deepcopy(action['control_ancestors']),
                'input_path': path, 'input_ordinal': port['ordinal'], 'input_name': port['label'],
                'sources': sources, 'citations': copy.deepcopy(original['attribution']['citations'])})
        _need(len(rows) == len(by_input))
        value = {'schema': SCHEMA, 'original_candidate_only': True,
            'runtime_entry_binding_verified': False, 'entry_contracts_are_proposals': True,
            'current_draft_matches_original': wire._same(current_value, projection['wire']),
            'baseline_value_hash': digest(projection['wire']), 'current_value_hash': digest(current_value),
            'projection_hash': projection['projection_hash'],
            'compiled_sidecar_hash': projection['compiled_sidecar_hash'],
            'causal_analysis_hash': projection['causal_analysis_hash'],
            'causal_declaration_consistency_only': True, 'rows': rows, **_FLAGS}
        return {**value, 'view_hash': digest(value)}
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None
