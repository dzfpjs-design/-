"""Fixed-scope local business/perception tool for one authorised Dock OSD."""
from uuid import UUID
from fastapi import HTTPException
from .contracts import Manifest, digest

HANDLER = 'dji_cloud_dock_status_v1'
NAMESPACE = 'dji_cloud_dock_status'
SCHEMA = 'dji-cloud-dock-status-tool-v1'


def _name(gateway_id): return 'dock_' + str(UUID(gateway_id)).replace('-', '')


def manifest(gateway_id, device_sn, configuration_hash):
    gateway_id = str(UUID(gateway_id))
    if (not isinstance(device_sn, str) or not 1 <= len(device_sn) <= 64
        or any(not (c.isascii() and (c.isalnum() or c in '_-')) for c in device_sn)
        or not isinstance(configuration_hash, str) or len(configuration_hash) != 64
        or any(c not in '0123456789abcdef' for c in configuration_hash)):
        raise HTTPException(422, '大疆状态工具的设备范围无效')
    identity = {'gateway_id': gateway_id, 'device_sn': device_sn, 'configuration_hash': configuration_hash}
    return Manifest(kind='tool', namespace=NAMESPACE, name=_name(gateway_id), version='1.0.0',
        owner='工作区大疆状态接入', description='只读取已授权机场最近一条 OSD 状态和证据；不发指令、不判断可飞。',
        allowed_agent_roles=['business','perception','data'],
        input_schema={'type':'object','properties':{},'additionalProperties':False},
        output_schema={'type':'object','additionalProperties':False,
            'required':['schema','connection','receiver','latest','notice'],
            'properties':{'schema':{'const':SCHEMA},'connection':{'type':'object'},
                          'receiver':{'type':'object'},'latest':{'type':['object','null']},'notice':{'type':'string'}}},
        execution={'adapter':'builtin','handler':HANDLER,'side_effect':False},
        data_policy={'deployment':'local','cloud_allowed':False,'external_allowed':False},
        budget={'max_attempts':2,'max_concurrency':1}, dependencies=[],
        specification={'schema':SCHEMA, **identity})


def applies(value):
    execution = value.get('execution') if isinstance(value,dict) else None
    return isinstance(value,dict) and (value.get('namespace')==NAMESPACE or
        isinstance(execution,dict) and execution.get('handler')==HANDLER)


def validate_manifest(value):
    data = value.model_dump(mode='json') if hasattr(value,'model_dump') else value
    try:
        spec = data['specification']
        expected = manifest(spec['gateway_id'],spec['device_sn'],spec['configuration_hash']).model_dump(mode='json')
        if digest(data) != digest(expected): raise ValueError()
    except (KeyError,TypeError,ValueError,AttributeError,HTTPException):
        raise HTTPException(422,'大疆只读工具必须保持服务端固定的设备、角色和只读合同') from None
    return data


def execute(db,p,cap,inputs):
    validate_manifest(cap.data)
    if inputs != {}: raise HTTPException(422,'设备范围已固定，不接受其他输入')
    from .dji_cloud_api import read_authorized_dock_status
    spec=cap.data['specification']
    value=read_authorized_dock_status(db,p,spec['gateway_id'],spec['device_sn'])
    if value['connection']['configuration_hash'] != spec['configuration_hash']:
        raise HTTPException(409,'设备接入配置已变化，请重新登记工具')
    return {'schema':SCHEMA, **value}
