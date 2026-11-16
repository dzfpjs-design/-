"""Versioned data designs bind actual schema origins and full runtime plans.

The legacy builder and its wire contract stay unchanged. New provenance is an
explicit dependency, never an inference of source truth or an execution grant.
"""
import copy

from . import instance_data_requirements as legacy
from .contracts import digest

SCHEMA = 'instance-data-requirements-v2'
EXTRA = ('semantic_runtime_plan', 'object_origins', 'full_semantic_schema_adoptions',
         'relation_types', 'relation_origins')


def marked(snapshot):
    return any(key in snapshot for key in ('semantic_runtime_plan', 'object_origins',
                                           'full_semantic_schema_adoptions'))


def build_requirements(snapshot):
    from .audio_stream_data_plan import augment_requirements
    if snapshot.get('semantic_runtime_plan',{}).get('schema') in {
            'instance-full-semantic-runtime-plan-v3', 'instance-full-semantic-runtime-plan-v4',
            'instance-object-graph-runtime-plan-v171', 'instance-business-flow-graph-runtime-v1',
            'instance-authored-workflow-runtime-v208'}:
        from .instance_graph_data_requirements_v3 import build_requirements as graph_requirements
        return augment_requirements(snapshot,graph_requirements(snapshot))
    base = legacy.build_requirements(snapshot)
    if not marked(snapshot):
        return augment_requirements(snapshot,base)
    if not all(key in snapshot for key in EXTRA if key != 'semantic_runtime_plan'):
        raise ValueError('data_requirements_v2_missing_provenance')
    values = {key: copy.deepcopy(snapshot.get(key)) for key in (*legacy.DEPENDENCIES, *EXTRA)}
    legacy._bytes(values)
    if (type(values['object_origins']) is not dict
            or type(values['full_semantic_schema_adoptions']) is not list
            or type(values['relation_types']) is not dict or type(values['relation_origins']) is not dict
            or values['semantic_runtime_plan'] is not None and type(values['semantic_runtime_plan']) is not dict):
        raise ValueError('data_requirements_v2_provenance_shape')
    out = {key: copy.deepcopy(value) for key, value in base.items() if key != 'requirements_hash'}
    out.update(schema=SCHEMA, dependency_hash=digest(values),
               dependency_hashes={key: digest(value) for key, value in values.items()})
    return augment_requirements(snapshot,{**out, 'requirements_hash': digest(out)})


def verify_requirements(snapshot):
    from .audio_stream_data_plan import verify as verify_audio_plan
    verify_audio_plan(snapshot)
    if not marked(snapshot) and 'gateway_audio_results' not in snapshot:
        return legacy.verify_requirements(snapshot)
    expected = build_requirements(snapshot)
    if legacy._bytes(snapshot.get('data_requirements')) != legacy._bytes(expected):
        raise ValueError('data_requirements_v2_changed')


def accepted_schema(plan):
    return type(plan) is dict and plan.get('schema') in (legacy.SCHEMA, SCHEMA,'instance-data-requirements-v3')
