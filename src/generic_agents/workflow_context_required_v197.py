"""Explicit, read-only narrowing of an independently checked observation context.

Null is a visible missing selection, never a successful fabricated plan. The
returned context remains the original reader's value and provenance; this tool
does not approve collection, generate a context or create any device request.
"""
import copy

from fastapi import HTTPException

from . import workflow_task_context_v196 as reader
from . import workflow_graph_scope as graph_scope
from .business_flow_review_store import _bytes, _same
from .services import validate


HANDLER = 'require_observation_context_v197'
MAX_BYTES = reader.MAX_BYTES + 1024
ERROR = '当前流程的原观察方案、生产者或登记能力无法核对；未安排设备动作'


def _object(properties):
    return {'type': 'object', 'properties': properties,
        'required': list(properties), 'additionalProperties': False}


def input_schema():
    context = copy.deepcopy(reader.context_schema())
    context['type'] = ['object', 'null']
    return _object({'context': context})


def output_schema():
    return _object({'context': copy.deepcopy(reader.context_schema())})


def _need(ok, message=ERROR, status=409):
    if not ok: raise HTTPException(status, message)


def validate_manifest(manifest):
    _need(type(manifest) is dict and manifest.get('kind') == 'tool'
        and manifest.get('allowed_agent_roles') == ['data']
        and _same(manifest.get('execution'), {'adapter': 'builtin',
            'handler': HANDLER, 'side_effect': False})
        and _same(manifest.get('input_schema'), input_schema())
        and _same(manifest.get('output_schema'), output_schema()), status=422)
    _bytes(manifest, MAX_BYTES)
    return copy.deepcopy(manifest)


def _inputs(value):
    _need(type(value) is dict and set(value) == {'context'}
        and (value['context'] is None or type(value['context']) is dict), status=422)
    _bytes(value, MAX_BYTES)
    validate(input_schema(), value)
    return copy.deepcopy(value)


def execute(db, p, cap, inputs, key):
    original = _inputs(inputs)
    validate_manifest(cap.data)
    task, job, node, _ = graph_scope.graph_context(db, p, key, cap)
    _need(node['execution']['agent_role'] == 'data' and _same(job.data['inputs'], original))
    if original['context'] is None:
        raise HTTPException(409, '缺少已选观察方案，请补充信息或确认选择')
    # ProducerPending propagates. The normal worker may wait for the actual
    # producer Job, but this tool must never turn its running state into success.
    checked = reader.checked_context(db, p, original['context'], task=task, dispatch=False)
    _need(_same(checked, original['context']))
    result = {'context': copy.deepcopy(original['context'])}
    validate(output_schema(), result)
    return result
