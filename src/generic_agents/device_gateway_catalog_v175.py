"""Registry contracts for the explicit equipment simulator, without credentials.

The dispatch contract deliberately accepts *completed* output only. Accepted or
running POST responses retain the existing unknown-action hold. The platform's
normal read-only receipt recovery can later prove completion using the same key.
This does not add a physical equipment driver or failed-action settlement.
"""
from urllib.parse import urlsplit


def manifests(base_url: str, secret_ref: str):
    parsed = urlsplit(base_url)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
            or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise ValueError('模拟设备目录需要已登记的确切127.0.0.1端口')
    if not secret_ref.startswith('GA_CONNECTOR_SECRET_') or not secret_ref.removeprefix('GA_CONNECTOR_SECRET_').replace('_', '').isalnum():
        raise ValueError('只接受服务端密钥引用')
    common = {'contract_version': '1.0', 'kind': 'tool', 'namespace': 'equipment_simulation',
              'version': '1.0.0', 'owner': '隔离设备联调', 'budget': {'max_attempts': 1, 'max_concurrency': 1},
              'data_policy': {'simulation': True, 'origin_kind': 'simulated_equipment', 'field_verified': False,
                              'cloud_fallback': False, 'physical_device_connected': False, 'external_allowed': True,
                              'test_only': True}}
    output = {'type': 'object', 'required': ['simulation', 'origin_kind', 'field_verified', 'request_id', 'device_id', 'action', 'status'],
              'properties': {'simulation': {'const': True}, 'origin_kind': {'const': 'simulated_equipment'},
                             'field_verified': {'const': False}, 'request_id': {'type': 'string'},
                             'device_id': {'type': 'string'}, 'action': {'type': 'string'}, 'status': {'const': 'succeeded'}}}
    def schema(actions):
        return {'type': 'object', 'additionalProperties': False,
                'required': ['device_id', 'action', 'expected_revision', 'arguments'],
                'properties': {'device_id': {'enum': ['sim-dock-01', 'sim-camera-01', 'sim-weather-01']},
                               'action': {'enum': actions}, 'expected_revision': {'type': ['integer', 'null'], 'minimum': 1},
                               'arguments': {'type': 'object'}}}
    return [
        {**common, 'name': 'read_equipment', 'description': '读取模拟设备状态和遥测；输出不是现场观测。',
         'allowed_agent_roles': ['business', 'perception', 'data'],
         'input_schema': schema(['get_status', 'read_telemetry']), 'output_schema': output,
         'execution': {'adapter': 'http', 'endpoint': base_url + '/invoke', 'secret_ref': secret_ref, 'side_effect': False}},
        {**common, 'name': 'control_equipment',
         'description': '申请模拟飞行/返航/云台动作。受理不等于完成；未获成功原回执前保持在途，不能重发。失败动作需人工核查原回执与恢复。',
         'allowed_agent_roles': ['business', 'perception'],
         'input_schema': schema(['start_mission', 'return_to_dock', 'cancel_mission', 'set_ptz']), 'output_schema': output,
         'execution': {'adapter': 'http', 'endpoint': base_url + '/invoke', 'secret_ref': secret_ref,
                       'side_effect': True, 'approval_policy': 'admin', 'receipt_endpoint': base_url + '/platform-receipts',
                       'result_semantics': 'completed_simulated_action_only'}},
    ]
