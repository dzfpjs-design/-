"""Instance ownership checks before saving or activating native graph sources."""
from fastapi import HTTPException

ISSUE = '本地处理结果来源属于已选业务实例；请选择同一实例生成新版本，或重新选择该实例的来源。'


def binding_issues(binding, instance_id):
    from .graph_event_source_families_v136 import require_instance
    for source in binding.get('event_source_bindings', {}).values():
        try:
            require_instance(source['contract'], instance_id)
        except (ValueError, KeyError, TypeError):
            return [ISSUE]
    return []


def require_snapshot_instance(snapshot, instance_id):
    plan = snapshot.get('semantic_runtime_plan', {})
    if plan.get('schema') not in {'instance-full-semantic-runtime-plan-v4',
            'instance-object-graph-runtime-plan-v171', 'instance-business-flow-graph-runtime-v1',
            'instance-authored-workflow-runtime-v208'}:
        return
    for adoption in plan.get('graph_adoptions', []):
        if binding_issues(adoption['binding'], instance_id):
            raise HTTPException(409, ISSUE)
