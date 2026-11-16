"""Human task-input metadata from the actual active immutable release.

This read path grants no execution, changes no graph contract, and never picks
an entity on the user's behalf. The original Task creation gate remains final.
"""
import copy
from fastapi import HTTPException
from .contracts import digest
from .instance_api import active
from . import workflow_graph as runtime

SCHEMA = 'workflow-graph-task-inputs-v1'


def validate_associations(release, business, supplied, entity_ids):
    """Reject an explicitly supplied foreign correlation before any dispatch.

    Omitted/nullable inputs retain the graph's original schema semantics. Output
    correlations are checked by the existing wait kernel when they are known.
    """
    if business.get('execution_mode') != 'confirmed_graph_workflow': return
    binding = runtime.business_binding(release, business)
    if binding.get('schema') not in {'report-full-semantic-graph-binding-v4',
            'report-object-graph-binding-v171'}: return
    graph = binding['graph']
    for subscription in graph['subscriptions']:
        ref = graph['references'][subscription['correlation_ref']]
        if ref['source'] != 'input' or ref['field'] not in supplied: continue
        value = supplied[ref['field']]
        if value is not None and (not isinstance(value, str) or value not in entity_ids):
            raise HTTPException(422, '等待结果的关联对象必须从本次已选对象中明确选择')


def describe(db, p, identity, release_id, business_id):
    instance, release = active(db, p, identity)
    if release.id != release_id:
        raise HTTPException(409, '实例发布版本已变化，原任务草稿保留，请核对当前版本')
    if business_id not in instance.data.get('enabled_business_ids', []):
        raise HTTPException(409, '所选业务尚未启用，原任务草稿保留')
    rows = [row for row in release.data['snapshot']['businesses'] if row['id'] == business_id]
    if len(rows) != 1:
        raise HTTPException(409, '发布业务记录无法核对，原任务草稿保留')
    business = rows[0]
    result = {'schema': SCHEMA, 'instance_id': instance.id, 'release_id': release.id,
        'configuration_hash': release.configuration_hash, 'business_id': business_id,
        'business_name': business['name'], 'mode': 'legacy', 'graph_hash': None,
        'binding_hash': None, 'input_schema': None, 'user_input_schema': None,
        'entity_fields': [], 'system_fields': [], 'execution_granted': False}
    if business.get('execution_mode') == 'confirmed_graph_workflow':
        binding = runtime.business_binding(release, business)
        graph = binding['graph']; full = graph['input_schema']
        result.update(mode='graph', graph_hash=digest(graph), binding_hash=binding['binding_hash'],
            input_schema=copy.deepcopy(full))
        user = copy.deepcopy(full)
        properties = user.get('properties', {})
        result['system_fields'] = sorted(set(properties) & runtime.SYSTEM_INPUTS)
        user['properties'] = {key: value for key, value in properties.items() if key not in runtime.SYSTEM_INPUTS}
        user['required'] = [key for key in user.get('required', []) if key not in runtime.SYSTEM_INPUTS]
        # Keep root constraints. Unsupported complex forms remain visibly
        # blocked; this metadata does not relax the actual graph input schema.
        result['user_input_schema'] = user
        entity = {}
        if binding.get('schema') in {'report-full-semantic-graph-binding-v4',
                'report-object-graph-binding-v171'}:
            steps = {step['id']: step for step in business['flow']['steps']}
            for sub in graph['subscriptions']:
                reference = graph['references'][sub['correlation_ref']]
                if reference['source'] != 'input':
                    continue
                field = reference['field']
                if field in runtime.SYSTEM_INPUTS or field not in user['properties']:
                    raise HTTPException(409, '消息关联输入不能替换服务器固定的任务对象，原草稿保留')
                spec = user['properties'][field]
                if reference['kind'] != 'string':
                    raise HTTPException(409, '消息关联输入类型无法核对，原草稿保留')
                label = spec.get('title')
                if not isinstance(label, str) or not label.strip(): label = '等待结果所对应的对象'
                row = entity.setdefault(field, {'field': field, 'label': label,
                    'required': field in user['required'], 'waits': []})
                for node in graph['nodes']:
                    if node['kind'] != 'wait' or node['subscription_id'] != sub['id']: continue
                    source = steps.get(node.get('source_step_id'), {})
                    name = source.get('name') or '等待处理结果'
                    if name not in row['waits']: row['waits'].append(name)
            result['entity_fields'] = list(entity.values())
        labels = {row['field']: row['label'] for row in result['entity_fields']}
        for index, (field, spec) in enumerate(user['properties'].items()):
            if not isinstance(spec.get('title'), str) or not spec['title'].strip():
                spec['title'] = labels.get(field, '业务输入 ' + str(index + 1))
    result['hash'] = digest(result)
    return result
