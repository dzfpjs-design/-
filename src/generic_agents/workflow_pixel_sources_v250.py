"""Task-owned ordered originals, never a resource-panel result substitution."""
import copy
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import digest
from .models import WorkflowPixelRun, Audit
from .pixel_change_runs_v249 import _images
from .workflow_images import snapshots
from .workflow_pixel_manifest_v250 import ADAPTER, pair_schema
from .services import validate

INPUT_ACTION = 'workflow_pixel.input_frozen_v250'


def need(value, reason='任务的像素原图、顺序或原申请无法核对；原记录保留'):
    if not value:
        raise HTTPException(409, reason)


def steps(release, business):
    if business.get('execution_mode') == 'confirmed_graph_workflow':
        from .workflow_graph import graph_for
        return [{'id': n['id'], 'execution': n['execution']} for n in graph_for(release, business)['nodes']
                if n.get('kind') == 'ability' and n.get('execution', {}).get('adapter') == ADAPTER]
    return [s for s in business['flow']['steps'] if s.get('execution', {}).get('adapter') == ADAPTER]


def legacy_images_allowed(release, business):
    """Recognize an actual independent provider, never an adapter's name."""
    if business.get('execution_mode') == 'confirmed_graph_workflow':
        from .workflow_graph import graph_for
        graph = graph_for(release, business)
        for node in graph['nodes']:
            if node.get('kind') != 'ability':
                continue
            for ref_id in node['execution'].get('input_bindings', {}).values():
                ref = graph['references'].get(ref_id, {})
                if ref.get('source') == 'input' and ref.get('field') == 'task_image_ids':
                    return True
        return False
    return any(binding.get('source') == 'task_image_ids'
        for step in business['flow']['steps']
        for binding in step.get('execution', {}).get('bindings', {}).values())


def freeze(db, p, instance, release, business, selection, entity_ids):
    required = bool(steps(release, business))
    if selection is None:
        if required:
            raise HTTPException(422, '此业务需要明确选择当次前后两张原图，并确认像素对应关系')
        return None
    if not required or len(entity_ids) != 1:
        raise HTTPException(422, '像素比较仅用于已登记像素能力的单对象任务；不推断多对象覆盖范围')
    if selection.before_image_id == selection.after_image_id:
        raise HTTPException(422, '请选择两张不同的原图')
    before, after = snapshots(db, p, instance.id, [selection.before_image_id, selection.after_image_id])
    sources = _images(db, p, instance.id, SimpleNamespace(before_image_id=before['id'], before_sha256=before['sha256'],
        after_image_id=after['id'], after_sha256=after['sha256']))
    value = {'schema': 'workflow-pixel-pair-v250', **sources, 'input_order': 'before_after',
        'pixel_correspondence_confirmed': True, 'full_image_present_confirmed': True,
        'coordinate_space': 'pixel', 'georeference_available': False}
    return {**value, 'pair_hash': digest(value)}


def verify_task(db, p, task):
    value = task.data.get('pixel_pair_snapshot')
    issued = db.scalar(select(WorkflowPixelRun.id).where(WorkflowPixelRun.workspace_id == p.workspace_id,
        WorkflowPixelRun.task_id == task.id).limit(1))
    audit = db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id, Audit.action == INPUT_ACTION,
        Audit.data['task_id'].as_string() == task.id).limit(1))
    if value is None:
        need(issued is None and audit is None)
        return None
    validate(pair_schema(), value)
    need(digest({k: v for k, v in value.items() if k != 'pair_hash'}) == value['pair_hash']
         and len(task.data.get('entity_ids', [])) == 1)
    before, after = value['before'], value['after']
    current = _images(db, p, task.data['instance_id'], SimpleNamespace(before_image_id=before['id'],
        before_sha256=before['sha256'], after_image_id=after['id'], after_sha256=after['sha256']))
    need(digest(current) == digest({'before': before, 'after': after}))
    return copy.deepcopy(value)


def refs(task):
    value = task.data.get('pixel_pair_snapshot')
    need(type(value) is dict)
    return copy.deepcopy(value)


def local_inputs(db, p, task, step, inputs):
    if step['execution'].get('adapter') != ADAPTER:
        return
    pair = verify_task(db, p, task)
    need(pair is not None and digest(inputs) == digest({'task_id': task.id, 'pair_ref': pair}),
         '此业务能力只能读取任务当次确认的两张原图，不能替换为样例或其它输入')
