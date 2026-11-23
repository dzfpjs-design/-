"""Check only the static observation-field prerequisites of new V288 releases.

This does not adopt a graph, authorise a device or revalidate historical
releases. Requested-observation ports carry field keys, not types or units.
Runtime still has to check each selected object's actual type and unit.
"""


def observation_scope(binding, catalog, schemas):
    from .authored_goal_data_workflow_contracts_v288 import BINDING
    if not isinstance(binding, dict) or binding.get('schema') != BINDING:
        return None
    requested = [pin for candidate in binding['candidate_specs']
                 for pin in candidate['capability_pins']
                 if pin['delivery'] == 'requested_observation']
    if not requested:
        return None
    fields = {port['field'] for pin in requested for port in pin['ports']}
    missing_ports = any(not pin['ports'] for pin in requested)
    possible = []
    for obj in catalog:
        schema = schemas.get(obj['key'], {})
        props = schema.get('properties', {})
        if not missing_ports and fields and fields <= set(props):
            possible.append(obj['name'])
    return {'status': 'matched' if possible else 'pending',
            'object_names': possible,
            'check': 'requested_observation_field_presence_only'}


def issue(scope):
    if scope is None or scope['status'] == 'matched':
        return None
    return '补充观测候选与当前对象定义尚未匹配：没有一种对象类型包含全部所需字段。请返回业务流程核对候选端口，或修改对象定义后重新检查。'
