"""Preview-only original analysis sources; no packet, consent or model writes.

The detached data/origin metadata and local document bytes are server-only.
This is before any SDK reservation: a sender must separately account for its
own already-reserved budget rather than reuse this preview's capacity gate.
"""
import copy

from fastapi import HTTPException
from pydantic import TypeAdapter

from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_quota_v304 as quota
from . import same_goal_advice_preparation_v304 as documents
from . import business_goal_ready_sources_v288 as originals
from . import business_goal_ready_frame_v288 as frame
from . import workflow_goal_evaluation_packet_v288 as packet
from . import goal_evaluation_provider_v288 as provider
from . import goal_evaluation_query_v288 as queries
from . import full_semantic_graph_v1 as graph
from .contracts import digest
from .models import Capability, Invocation, Job, Plan, Task
from .security import Principal
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288 as Evaluation

SCHEMA = 'same-goal-advice-current-analysis-v305'
ORIGINALS = 'same-goal-advice-current-originals-v305'
ERROR = '原建议计划、当前成熟评价或真实资料原件无法核对；未补资料、授权或发送模型'
GENERATION_REQUIRED = '当前是首次目标评价。请由数据岗位在同一任务追加并完成一次评价，再使用新评价重新准备资料。原准备和授权记录已保留，本次未发送。'


class EvaluationGenerationRequired(HTTPException):
    def __init__(self):
        super().__init__(status_code=409, detail=GENERATION_REQUIRED)


def need(value):
    if not value:raise HTTPException(409,ERROR)


def _current_scope(db,p,task,plan,prep):
    # _scope is a data-post Prepare gate. This Preview is performed by the
    # actual business post, never by impersonating the other post's Principal.
    context=quota._configuration(db,p,task,prep['request'])
    return _match_scope(db,p,task,plan,prep,context)


def _match_scope_facts(db,p,task,plan,prep,context):
    # Shared source identity checks do not grant policy or dispatch authority.
    selected=provider.binding(db,p,task);need(selected is not None)
    nodes={node['id']:node for node in selected['binding']['graph']['nodes']}
    reasoning,data=nodes['reasoning'],nodes['packet']
    need(p.role=='admin' or p.user_id==prep['business_actor_id'])
    need(reasoning['actor_id']==prep['business_actor_id'] and data['actor_id']==prep['data_actor_id']
        and context['source'].id==reasoning['execution']['capability_id']
        and store._pin(context['source'])==prep['source_business_capability_ref']
        and store._pin(context['advice'])==prep['advice_capability_ref']
        and store._pin(context['model'])==prep['model_pin']
        and context['source'].data['specification']['instruction']==prep['instruction']
        and {**context['scope'],'analysis_plan_id':plan.id}==prep['scope'])
    need(queries.query_ref(provider._q0(db,p,task))==prep['original_query_ref'])
    return selected


def _match_scope(db,p,task,plan,prep,context):
    selected=_match_scope_facts(db,p,task,plan,prep,context)
    budget=store._policy(db,p,task,prep['scope'],context['source'],context['advice'],context['model'])
    need(digest(budget)==digest(prep['budget']))
    return selected


def _frame_inputs(task,selected):
    binding=selected['binding'];runtime=graph.check_run(binding['graph'],task.data['graph_runtime'])
    need(runtime['graph_hash']==digest(binding['graph']) and runtime['input_hash']==digest(runtime['inputs']))
    nodes=[node for node in binding['graph']['nodes'] if node['id']=='reasoning'];need(len(nodes)==1)
    execution=nodes[0]['execution'];arguments=execution['arguments']
    need(type(arguments) is dict and set(arguments)=={'candidate_specs','decision_intent'}
        and execution['input_bindings']['question']=='analysis_question')
    question=runtime['inputs']['analysis_question']
    need(type(question) is str and 0<len(question)<=4000 and bool(question.strip()))
    intent=TypeAdapter(c.base.Intent).validate_python(arguments['decision_intent'])
    return {'question':question,'decision_intent':intent,'candidate_specs':frame._specs(arguments)}


@store.checked
def resolve_current(db,p,task,plan):
    """Read one real prepared Plan and its exact current Eval2-or-later sources.

    Caller must consume this DTO locally. It proves source eligibility at this
    read, never consent, payment, a future send, device execution or acceptance.
    """
    need(type(p) is Principal and p.role in {'admin','operator'}
        and type(task) is Task and type(plan) is Plan)
    with db.no_autoflush:
        store._reader(db,p,write=True)
        task=store._fresh(db,p,Task,task.id);plan=store._fresh(db,p,Plan,plan.id)
        need(task.workspace_id==plan.workspace_id==p.workspace_id and plan.task_id==task.id
            and plan.status=='awaiting_consent')
        inventory=store._inventory(db,p,task)
        found=[value for value in inventory if value[0].id==plan.id];need(len(found)==1)
        actual,prep,prepare_job=found[0]
        need(actual is plan and prepare_job.status=='succeeded' and prepare_job.lease_until is None
            and prepare_job.kind==c.PREPARE_KIND
            and (p.role=='admin' or p.user_id==prep['business_actor_id']))
        selected=_current_scope(db,p,task,plan,prep)
        return verified_sources(db,p,task,plan,prep,prepare_job,selected)


def verified_sources(db,p,task,plan,prep,prepare_job,selected):
    """Fixed server readers share source IO, not permission or a cached verdict."""
    document=documents.cold_read(db,p,task,plan)
    return _verified_sources(db,p,task,plan,prep,prepare_job,selected,document,c.seal_source)


def _verified_sources(db,p,task,plan,prep,prepare_job,selected,document,seal_source):
    # Private mechanical assembly only: fixed version-specific entry points
    # must authenticate the original document, policy and actor beforehand.
    need(document is not None and document['marker']==plan.data['document_preparation']
        and document['prepare_job_id']==prepare_job.id
        and document['preparation_snapshot_hash']==prep['record_hash']
        and document['document']['selection']==prep['request']['document_source']
        and type(document['content']) is bytes)
    expected=prep['mature_head'];reference=expected['evaluation_ref']
    current=provider.require_current(db,p,task,expected_ref=reference,lock_sources=False)
    need(current is not None and current['evaluation_ref']==reference
        and current['goal_check_job_id']==expected['goal_check_job_id']
        and current['goal_check_id']==expected['memory_id'] and current['report_hash']==expected['report_hash']
        and current['evidence']['status']=='sufficient' and not current['evidence']['conflicts'])
    head=task.data[provider.KEY]
    need(head['head_hash']==expected['head_hash'] and head['evaluation_ref']==reference
        and head['goal_check_job_id']==expected['goal_check_job_id'])
    row=store._fresh(db,p,Evaluation,reference['id']);record=row.data
    need(row.record_hash==reference['record_hash'] and row.task_id==task.id and row.workspace_id==p.workspace_id
        and row.generation==record['generation'] and row.goal_hash==prep['scope']['goal_hash']
        and row.goal_snapshot_hash==prep['scope']['goal_snapshot_hash']
        and record['original_query_ref']==prep['original_query_ref']
        and record['evaluation_query_ref']==prep['evaluation_query_ref']
        and record['cut_at']==prep['evaluation_cut']
        and record['evaluation_result']['status']==record['evaluation_result']['result']['status']=='sufficient'
        and not record['evaluation_result']['result']['conflicts'])
    producer_job=store._fresh(db,p,Job,row.job_id);call=store._fresh(db,p,Invocation,row.invocation_id)
    cap=store._fresh(db,p,Capability,call.capability_id)
    output=packet.saved_result(db,p,task,producer_job,call,cap,producer_job.data['inputs'])
    need(output['evaluation_ref']==reference and output['route']=='data_ready' and output['query_status']=='sufficient')
    release,package_hash,data=originals._data(db,p,task,output['packet'],live=True)
    need(release.id==selected['release'].id==prep['scope']['release_id']
        and release.instance_id==prep['scope']['instance_id'] and release.package_id==task.package_id
        and data['packet']==output['packet'] and data['packet_id']==row.id
        and digest(data['evaluation_record'])==digest(record))
    manifest = _selected_manifest(prep,db,p)
    frame_inputs = _frame_inputs(task,selected)
    if row.generation == 1:
        raise EvaluationGenerationRequired()
    need(row.generation >= 2)
    source=seal_source({'schema':c.SOURCE,'plan_id':plan.id,'mature_head':copy.deepcopy(expected),
        'original_query_ref':copy.deepcopy(record['original_query_ref']),
        'evaluation_query_ref':copy.deepcopy(record['evaluation_query_ref']),
        'evaluation_cut':record['cut_at'],'evaluation_generation':record['generation'],
        'data_evidence_ref':copy.deepcopy(record['evidence_ref']),
        'document':copy.deepcopy(document['document'])},preparation=prep,manifest=manifest)
    return {'schema':SCHEMA,'task':task,'preparation':copy.deepcopy(prep),'source_record':source,
        'original_sources':{'schema':ORIGINALS,'task_id':task.id,'release_id':release.id,
            'package_id':task.package_id,'package_hash':package_hash,'data':copy.deepcopy(data)},
        'document':{'record':copy.deepcopy(document['document']),'content':document['content'],
            'preparation_marker':copy.deepcopy(document['marker'])},
        'frame_inputs':frame_inputs,'release':release}


def _selected_manifest(prep,db,p):
    cap=store._fresh(db,p,Capability,prep['advice_capability_ref']['id'])
    need(store._pin(cap)==prep['advice_capability_ref'] and cap.content_hash==cap.validated_hash==digest(cap.data))
    return cap.data
