"""Select an explicit runtime version; old full-design execution stays blocked."""
from sqlalchemy import select
from fastapi import HTTPException

from . import instance_full_semantics as legacy
from .models import Capability, SetupDraft, SetupRevision
from .services import scoped


def modern(value=None):
    if isinstance(value, dict):
        plan = value.get('semantic_runtime_plan', {})
        versions = {version for version in (plan.get('schema') if isinstance(plan, dict) else None,
            value.get('full_semantic_runtime_schema')) if version is not None}
        if len(versions) > 1 or not versions <= {'instance-full-semantic-runtime-plan-v2',
                'instance-full-semantic-runtime-plan-v3', 'instance-full-semantic-runtime-plan-v4',
                'instance-object-graph-runtime-plan-v171', 'instance-business-flow-graph-runtime-v1',
                'instance-authored-workflow-runtime-v208'}:
            raise HTTPException(409, '执行版本未知或标记冲突，原记录保留，不能降级')
        if 'instance-authored-workflow-runtime-v208' in versions:
            from . import authored_workflow_runtime_v208
            return authored_workflow_runtime_v208
        if 'instance-business-flow-graph-runtime-v1' in versions:
            from . import business_flow_execution_runtime
            return business_flow_execution_runtime
        if 'instance-object-graph-runtime-plan-v171' in versions:
            from . import report_object_graph_runtime_v171
            return report_object_graph_runtime_v171
    if isinstance(value, dict) and (value.get('semantic_runtime_plan', {}).get('schema')
            == 'instance-full-semantic-runtime-plan-v4'
            or value.get('full_semantic_runtime_schema') == 'instance-full-semantic-runtime-plan-v4'):
        from . import report_full_semantic_graph_runtime_v4
        return report_full_semantic_graph_runtime_v4
    if isinstance(value, dict) and (value.get('semantic_runtime_plan', {}).get('schema')
            == 'instance-full-semantic-runtime-plan-v3'
            or value.get('full_semantic_runtime_schema') == 'instance-full-semantic-runtime-plan-v3'):
        from . import report_full_semantic_graph_runtime_v3
        return report_full_semantic_graph_runtime_v3
    from . import report_full_semantic_runtime_v2
    return report_full_semantic_runtime_v2


def runtime_marked(value, business_key='businesses'):
    return (any(key in value for key in modern().MARKERS)
        or any(modern().BUSINESS_MARKER in business for business in value.get(business_key, [])
               if isinstance(business, dict))
        or any('full_semantic_graph_origin' in business for business in value.get(business_key, [])
               if isinstance(business, dict))
        or any('authored_workflow_origin' in business for business in value.get(business_key, [])
               if isinstance(business, dict)))


def pins(snapshot):
    if runtime_marked(snapshot):
        if any(key in snapshot for key in legacy.MARKERS):
            raise HTTPException(409, '执行版本标记冲突，原记录保留')
        return modern(snapshot).pins(snapshot)
    return legacy.pins(snapshot)


def issued_schema(db, p, release):
    """An issued configuration cannot silently lose all v2 markers later."""
    version = release.data.get('configuration_revision')
    draft = db.scalar(select(SetupDraft).where(SetupDraft.workspace_id == p.workspace_id))
    if draft is None or type(version) is not int:
        return False
    value = draft.data if draft.version == version else db.scalar(select(SetupRevision.data).where(
        SetupRevision.workspace_id == p.workspace_id, SetupRevision.draft_id == draft.id,
        SetupRevision.version == version))
    if isinstance(value,dict):
        if value.get('authoredWorkflowGraphs'):
            from .authored_workflow_runtime_v208 import SCHEMA
            return SCHEMA
        if value.get('businessFlowExecutions'):
            from .business_flow_execution_runtime import SCHEMA
            return SCHEMA
        if value.get('fullSemanticGraphBindings'):
            from .report_full_semantic_graph_runtime_v4 import for_configuration
            return for_configuration(value).SCHEMA
        if value.get('fullSemanticRuntimeBindings'):return 'instance-full-semantic-runtime-plan-v2'
    return None


def issued_runtime(db,p,release):
    return bool(issued_schema(db,p,release))


def verify_release(db, p, release):
    snapshot = release.data.get('snapshot', {})
    package = scoped(db, Capability, release.package_id, p) if release.package_id else None
    spec = package.data.get('specification', {}) if package else {}
    marked = (runtime_marked(snapshot) or runtime_marked(release.data)
        or runtime_marked(spec, 'business_definitions'))
    required_schema=issued_schema(db,p,release)
    if marked or required_schema:
        if any(key in value for value in (snapshot, release.data, spec) for key in legacy.MARKERS):
            raise HTTPException(409, '完整设计的执行版本冲突，不能降级运行')
        versions = {value.get('full_semantic_runtime_schema') for value in (snapshot, release.data, spec)
            if value.get('full_semantic_runtime_schema')}
        if len(versions) > 1:
            raise HTTPException(409, '完整设计执行版本标记不一致，不能降级运行')
        selected = next((value for value in (snapshot, release.data, spec)
            if value.get('full_semantic_runtime_schema') in {'instance-full-semantic-runtime-plan-v3','instance-full-semantic-runtime-plan-v4','instance-object-graph-runtime-plan-v171','instance-business-flow-graph-runtime-v1','instance-authored-workflow-runtime-v208'}
            or value.get('semantic_runtime_plan', {}).get('schema') in {'instance-full-semantic-runtime-plan-v3','instance-full-semantic-runtime-plan-v4','instance-object-graph-runtime-plan-v171','instance-business-flow-graph-runtime-v1','instance-authored-workflow-runtime-v208'}), snapshot)
        if required_schema and (spec.get('full_semantic_runtime_schema')!=required_schema
                or release.data.get('full_semantic_runtime_schema')!=required_schema
                or snapshot.get('semantic_runtime_plan',{}).get('schema')!=required_schema):
            raise HTTPException(409,'实际签发的流程版本与冻结执行合同不一致，不能降级运行')
        verified = modern(selected).verify_release(db, p, release)
        if verified is None:
            raise HTTPException(409, '已采用的执行版本标记缺失，不能降级运行')
        return verified
    return legacy.verify_release(db, p, release)
