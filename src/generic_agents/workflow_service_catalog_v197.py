"""Installed service implementations; selection is never execution authority."""
from fastapi import HTTPException


def family(capability):
    data = capability if type(capability) is dict else capability.data
    execution = data.get('execution', {})
    if execution.get('adapter') != 'workflow_service':
        raise HTTPException(422, '登记能力不是可识别的流程服务')
    operation = execution.get('operation')
    if operation in {'in_app_notice', 'resense_with_receipt'}:
        from . import workflow_service_v195
        return workflow_service_v195
    if operation == 'resense_from_context':
        from . import workflow_service_context_v196
        return workflow_service_context_v196
    if operation == 'dispatch_tasks':
        from . import workflow_task_dispatch_v198
        return workflow_task_dispatch_v198
    if operation == 'adjust_monitoring_schedule':
        from . import workflow_monitor_schedule_v200
        return workflow_monitor_schedule_v200
    if operation == 'execute_registered_tool_plan':
        from . import workflow_registered_tool_plan_v201
        return workflow_registered_tool_plan_v201
    if operation == 'plan_business_decision_v277':
        from . import workflow_decision_tool_planner_v277
        return workflow_decision_tool_planner_v277
    if operation == 'plan_business_decision_review_v285':
        from . import workflow_decision_tool_planner_v285
        return workflow_decision_tool_planner_v285
    if operation == 'create_goal_evaluation_packet_v288':
        from . import workflow_goal_evaluation_packet_v288
        return workflow_goal_evaluation_packet_v288
    if operation == 'plan_approved_goal_data_v288':
        from . import workflow_goal_data_route_runtime_v288
        return workflow_goal_data_route_runtime_v288
    raise HTTPException(422, '服务操作尚未安装；原登记和输入保留，未派发')


def validate_manifest(manifest):
    return family(manifest).validate_manifest(manifest)


def monitoring_receipt_retained(call, *, db=None, p=None, task=None, job=None):
    """Route an original unknown call to reconciliation, never authorize it.

    The installed implementation still checks the actual immutable inventory,
    current lease and approval before adopting any result. Missing or damaged
    state does not authorize another execute or a replacement request key.
    """
    if call is None or call.status != 'unknown' or type(call.data) is not dict:
        return False
    from .contracts import digest
    from . import workflow_monitor_schedule_v200 as monitor
    from uuid import UUID
    try:
        snapshot = call.data['capability_snapshot']
        execution = snapshot['execution']
        if execution.get('operation') == 'create_goal_evaluation_packet_v288':
            from .workflow_goal_evaluation_packet_v288 import retained_marker_shape
            if retained_marker_shape(call): return True
            # A missing journal is never a retained receipt marker. Only the
            # actual original Qe/SAVED and complete effect inventory may route
            # that fixed local operation to normal reconciliation.
            if db is None or p is None or task is None or job is None: return False
            from .workflow_goal_evaluation_recovery_v294 import retained_query_authority
            return retained_query_authority(db, p, task, job, call)
        if execution.get('operation') == 'plan_approved_goal_data_v288':
            from .workflow_goal_data_route_runtime_v288 import retained_marker_shape
            return retained_marker_shape(call)
        if execution.get('operation') == 'plan_business_decision_review_v285':
            from .workflow_decision_tool_planner_v285 import retained_marker_shape
            return retained_marker_shape(call)
        if execution.get('operation') == 'plan_business_decision_v277':
            from .workflow_decision_tool_planner_v277 import retained_marker_shape
            return retained_marker_shape(call)
        if execution.get('operation') == 'execute_registered_tool_plan':
            from .workflow_registered_tool_plan_v201 import retained_marker_shape
            return retained_marker_shape(call)
        marker = call.data[monitor.CALL_KEY]
        approval = call.data['approval']
        return (execution == {'adapter': 'workflow_service', 'service_actor': 'execution_service',
                    'operation': monitor.OP, 'side_effect': True}
            and digest(snapshot) == call.data['capability_hash']
            and call.data.get('role') == 'business' and call.data.get('side_effect') is True
            and type(approval) is dict and approval.get('schema') == 'workflow-service-approval-v195'
            and approval.get('capability_id') == call.capability_id
            and approval.get('capability_hash') == call.data['capability_hash']
            and approval.get('input_hash') == call.data['input_hash']
            and type(marker) is dict and set(marker) == {'schema', 'installation_id', 'record_hash'}
            and marker['schema'] == monitor.INSTALL
            and type(marker['installation_id']) is str
            and str(UUID(marker['installation_id'])) == marker['installation_id']
            and type(marker['record_hash']) is str and len(marker['record_hash']) == 64
            and all(value in '0123456789abcdef' for value in marker['record_hash']))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        return False
