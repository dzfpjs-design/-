"""Pure whole-design replacement, not ledger authority, consent or execution.

The outer transaction authenticates the current head/current saved manual,
checks nonce and CAS, and retains the entire previous configuration/history.
This module never maps a rename, merges facts or inherits operational grants.
"""
import copy
import json

from . import report_full_semantic_configuration_v1 as semantic
from .report_full_reading_semantics import _safe

SUMMARY_KEYS = {'added_businesses', 'removed_businesses', 'retained_businesses', 'changed_sections'}
FAMILIES = ('domain', 'global_claims', 'objects', 'relationships', 'businesses', 'questions')
MAX_CONFIG_BYTES = 2 * 1024 * 1024


def need(value, code):
    if not value:
        raise ValueError(code)


def safe(value):
    _safe(value)
    try:
        need(len(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            .encode('utf-8')) <= MAX_CONFIG_BYTES, 'full_semantic_replacement_capacity')
    except (TypeError, UnicodeError, RecursionError):
        raise ValueError('full_semantic_replacement_unsafe_json') from None


def pair(previous, new):
    previous, new = semantic.check_design(previous), semantic.check_design(new)
    need(previous['handoff_id'] != new['handoff_id'], 'full_semantic_replacement_requires_new_id')
    return previous, new


def replacement_changes(previous_design, new_design):
    previous, new = pair(previous_design, new_design)
    old_names = [binding['name'] for binding in previous['business_bindings']]
    new_names = [binding['name'] for binding in new['business_bindings']]
    return {'added_businesses': [name for name in new_names if name not in old_names],
        'removed_businesses': [name for name in old_names if name not in new_names],
        'retained_businesses': [name for name in new_names if name in old_names],
        'changed_sections': [key for key in FAMILIES if previous['result'][key] != new['result'][key]]}


def configuration_names(config):
    entries = config.get('businesses')
    need(type(entries) is list and len(entries) <= 20, 'full_semantic_replacement_business_list')
    names = []
    for entry in entries:
        name = entry if type(entry) is str else entry.get('name') if type(entry) is dict else None
        need(type(name) is str and name.strip() and len(name) <= 100, 'full_semantic_replacement_business_name')
        names.append(name)
    need(len(names) == len(set(names)), 'full_semantic_replacement_duplicate_business')
    return names


def checked_configuration(config):
    need(type(config) is dict, 'full_semantic_replacement_configuration')
    safe(config)
    names = configuration_names(config)
    designs, flows = config.get('fullSemanticDesigns'), config.get('flows')
    need(type(designs) is list and len(designs) <= 20 and type(flows) is dict
        and all(type(flow) is dict for flow in flows.values()), 'full_semantic_replacement_binding_shape')
    for design in designs:
        semantic.check_design(design)
        need(all(binding['name'] in names for binding in design['business_bindings']),
            'full_semantic_replacement_missing_business')
    semantic.signature_context(config, {})
    for flow in flows.values():
        semantic.signature_context(config, flow)
    if 'resources' in config:
        need(type(config['resources']) is list, 'full_semantic_replacement_resources')
        for resource in config['resources']:
            need(type(resource) is dict, 'full_semantic_replacement_resource')
            if 'businesses' in resource:
                need(type(resource['businesses']) is list and all(type(name) is str for name in resource['businesses']),
                    'full_semantic_replacement_resource_businesses')
    if 'setupNotes' in config:
        need(type(config['setupNotes']) is dict and all(type(note) is str for note in config['setupNotes'].values()),
            'full_semantic_replacement_notes')
    if 'gaps' in config:
        need(type(config['gaps']) is list and all(type(gap) is dict
            and ('business' not in gap or type(gap['business']) is str) for gap in config['gaps']),
            'full_semantic_replacement_gaps')
    if 'businessAliases' in config:
        need(type(config['businessAliases']) is dict and all(type(name) is str for name in config['businessAliases'].values()),
            'full_semantic_replacement_aliases')
    if 'automation' in config:
        need(type(config['automation']) is dict, 'full_semantic_replacement_automation')
        if 'policies' in config['automation']:
            policies = config['automation']['policies']
            need(type(policies) is dict and all(type(policy) is dict for policy in policies.values()),
                'full_semantic_replacement_policies')
    return names


def apply_replacement(config, previous_design, new_design):
    previous, new = pair(previous_design, new_design)
    names = checked_configuration(config)
    positions = [index for index, design in enumerate(config['fullSemanticDesigns'])
        if design['handoff_id'] == previous['handoff_id']]
    need(len(positions) == 1 and config['fullSemanticDesigns'][positions[0]] == previous,
        'full_semantic_replacement_previous_changed')
    need(all(design['handoff_id'] != new['handoff_id'] for design in config['fullSemanticDesigns']),
        'full_semantic_replacement_new_id_conflict')
    old_names = {binding['name'] for binding in previous['business_bindings']}
    new_names = [binding['name'] for binding in new['business_bindings']]
    new_set = set(new_names)
    outside = (set(names) | set(config['flows'])) - old_names
    need(not outside.intersection(new_set), 'full_semantic_replacement_business_conflict')
    need(len(names) - len(old_names) + len(new_names) <= 20, 'full_semantic_replacement_business_capacity')
    out = copy.deepcopy(config)
    out['fullSemanticDesigns'][positions[0]] = copy.deepcopy(new)
    businesses, inserted = [], False
    for entry, name in zip(config['businesses'], names):
        if name in old_names:
            if not inserted:
                businesses.extend(new_names); inserted = True
        else:
            businesses.append(copy.deepcopy(entry))
    if not inserted:
        businesses.extend(new_names)
    out['businesses'] = businesses
    fresh = semantic.derive_flows(new)
    flows, inserted = {}, False
    for name, flow in config['flows'].items():
        if name in old_names:
            if not inserted:
                flows.update(fresh); inserted = True
        else:
            flows[name] = copy.deepcopy(flow)
    if not inserted:
        flows.update(fresh)
    out['flows'] = flows
    group, reset_names = old_names | new_set, old_names ^ new_set
    for resource in out.get('resources', []):
        if 'businesses' in resource:
            resource['businesses'] = [name for name in resource['businesses'] if name not in reset_names]
    if 'setupNotes' in out:
        out['setupNotes'] = {name: note for name, note in out['setupNotes'].items() if name not in reset_names}
    if 'gaps' in out:
        out['gaps'] = [gap for gap in out['gaps'] if gap.get('business') not in reset_names]
    if 'businessAliases' in out:
        out['businessAliases'] = {name: target for name, target in out['businessAliases'].items()
            if name not in group and target not in group}
    if 'automation' in out and 'policies' in out['automation']:
        out['automation']['policies'] = {name: policy for name, policy in out['automation']['policies'].items()
            if name not in group}
    checked_configuration(out)
    return out
