"""Local, queryable candidate archives from an actually adopted data-agent node.

An archive stores referenced saved products, not new Entity facts or approval.
The gateway owns the transaction committing this archive and its Invocation.
"""
import copy
import hashlib
import json
import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select

from .business_flow_review_store import _bytes, _same
from .contracts import Manifest, digest
from .models import Audit, Capability, DataRun, Evidence, Invocation, Job, Task, now
from .services import audit, published, require, scoped, validate
from .typed_payload_codec_v131 import canonical_json
from .workflow_task_archive_models_v198 import (WorkflowTaskArchive as Archive,
    WorkflowTaskArchiveVersion as Version, WorkflowTaskArchiveReference as Reference,
    WorkflowTaskArchiveCommand as Command)

ADAPTER = 'task_archive'
OP = 'archive_task_candidate'
APPROVAL = 'workflow-task-archive-approval-v198'
RECEIPT = 'workflow-task-archive-receipt-v198'
VALUE = 'workflow-task-archive-value-v198'
VIEW = 'workflow-task-archive-view-v198'
ORIGINAL = 'workflow-task-archive-original-result-v198'
REVISION = 'workflow-task-archive-revision-v198'
COMMAND = 'workflow-task-archive-command-v198'
STATE_KEY = 'task_archive_v198'
SAVED = 'workflow.task_archive.saved_v198'
MAX_BYTES = 8 * 1024 * 1024
MAX_COMMAND_BYTES = 32768
MAX_SOURCES = 512
MAX_VERSIONS = 1000
SHA = re.compile(r'^[a-f0-9]{64}$')
FLAGS = {'proposal_only': True, 'human_confirmed': False, 'formal_archive': False,
    'entity_state_updated': False}
APPROVAL_KEYS = {'schema', 'task_id', 'job_id', 'activation_id', 'graph_hash', 'input_hash',
    'capability_id', 'capability_hash', 'cancel_epoch'}


def need(ok, message='原任务、归档来源或原保存记录无法核对；未重新存档或确认事实', status=409):
    if not ok: raise HTTPException(status, message)


def _id(value):
    try: need(type(value) is str and str(UUID(value)) == value, status=422)
    except (ValueError, TypeError, AttributeError): need(False, status=422)
    return value


def _text(maximum): return {'type': 'string', 'minLength': 1, 'maxLength': maximum, 'pattern': r'\S'}


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def input_schema():
    return _object({'record_type': _text(80), 'title': _text(300), 'summary': _text(8000)})


def output_schema():
    ident = {'type': 'string', 'pattern': r'^[a-f0-9-]{36}$'}
    return _object({'schema': {'type': 'string', 'const': RECEIPT}, 'archive_id': ident,
        'version': {'type': 'integer', 'minimum': 1}, 'version_hash': {'type': 'string', 'pattern': SHA.pattern},
        'task_id': copy.deepcopy(ident), 'status': {'type': 'string', 'const': 'candidate'},
        'simulation_only': {'type': 'boolean'}, **{key: {'type': 'boolean', 'const': value} for key, value in FLAGS.items()}})


def _inputs(value):
    _bytes(value, MAX_COMMAND_BYTES); validate(input_schema(), value)
    return copy.deepcopy(value)


def validate_manifest(manifest):
    data = manifest.model_dump(mode='json') if isinstance(manifest, Manifest) else copy.deepcopy(manifest)
    Manifest.model_validate(data)
    need(data.get('kind') == 'tool' and data.get('allowed_agent_roles') == ['data']
        and _same(data.get('execution'), {'adapter': ADAPTER, 'side_effect': True})
        and _same(data.get('input_schema'), input_schema()) and _same(data.get('output_schema'), output_schema())
        and type(data.get('budget', {}).get('max_attempts')) is int and data['budget']['max_attempts'] == 1
        and data.get('dependencies') == [] and data.get('data_policy', {}).get('cloud_allowed') is False
        and _same(data.get('specification'), {'schema': 'workflow-task-archive-contract-v198',
            'operation': OP, 'candidate_only': True}), '归档须登记为数据智能体本地持久写能力；不能伪装只读或正式确认', 422)
    return data


def make_approval(task, job, node, inputs):
    return {'schema': APPROVAL, 'task_id': task.id, 'job_id': job.id,
        'activation_id': job.data['activation_id'], 'graph_hash': job.data['graph_hash'],
        'input_hash': digest(inputs), 'capability_id': node['execution']['capability_id'],
        'capability_hash': node['execution']['capability_hash'], 'cancel_epoch': job.data['cancel_epoch']}


def validate_call_approval(db, p, cap, role, inputs, key, approval):
    from . import workflow_graph_scope as scope, workflow_graph as workflow
    from .full_semantic_graph_v1 import ability_inputs
    from .leases import ownership
    from .relations import lock_current_actor
    from .layered_approval_store_v175 import guard
    require(p, 'admin', 'operator'); _inputs(inputs)
    need(type(approval) is dict and set(approval) == APPROVAL_KEYS and approval['schema'] == APPROVAL
        and role == 'data' and type(approval['cancel_epoch']) is int and approval['cancel_epoch'] >= 0)
    for name in ('task_id', 'job_id', 'capability_id'): _id(approval[name])
    for name in ('graph_hash', 'input_hash', 'capability_hash'):
        need(type(approval[name]) is str and SHA.fullmatch(approval[name]) is not None)
    original_hash = cap.content_hash
    fresh = db.scalar(select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.id == cap.id).execution_options(populate_existing=True))
    need(fresh is not None); actual = published(db, p, cap.id)
    validate_manifest(actual.data); need(actual.content_hash == original_hash)
    lease=ownership(db)
    need(lease.get('job_id') == approval['job_id'] and bool(lease.get('lease_token')))
    job=db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,Job.id == approval['job_id'],
        Job.kind == scope.KIND,Job.status == 'running',Job.lease_token == lease['lease_token'],
        Job.lease_until > func.clock_timestamp()).execution_options(populate_existing=True))
    need(job is not None and job.data.get('invocation_key') == key and job.data.get('actor_id') == p.user_id
        and job.data.get('task_id') == approval['task_id'])
    # Serialize the local write with cancellation and other Task archive nodes.
    locked = scoped(db,Task,approval['task_id'],p,True)
    task,release,business,graph,state,activation,node=scope._frozen(db,p,locked.id,approval['activation_id'])
    transport=scope._job_matches(task,job,state,activation,node)
    spec=node['execution']
    need(task.id == locked.id and state['status'] in {'running','waiting'} and activation['status'] == 'ready'
        and activation['id'] in state['eligible_ability_ids'] and activation['epoch'] == state['cancel_epoch']
        and node['actor_id'] == p.user_id and spec['agent_role'] == role and spec['adapter'] == ADAPTER
        and spec['capability_id'] == actual.id and spec['capability_hash'] == actual.content_hash
        and spec['release_version'] == actual.release_version and spec['max_attempts'] == 1
        and _same(spec['input_schema'],input_schema()) and _same(spec['output_schema'],output_schema())
        and transport.get('phase') in {'running','waiting_service'}
        and _same(approval,make_approval(task,job,node,inputs)) and _same(job.data['inputs'],inputs)
        and _same(ability_inputs(graph,state,activation['id']),inputs))
    frozen=scope._scope(task,job,node,activation)
    binding = workflow.business_binding(release, business)
    need(binding.get('schema') == 'business-flow-nested-execution-binding-v195'
        and type(binding.get('simulation_only')) is bool
        and binding.get('review_mode') in {'author_self_review', 'engineering_simulation'}
        and binding['simulation_only'] is (binding['review_mode'] == 'engineering_simulation')
        and binding.get('binding_hash') == digest({k:v for k,v in binding.items() if k != 'binding_hash'}))
    guard(db, p, task, record_actor=True); workflow.live(db,p,task,release,business)
    from .workflow_graph_goal_guard_v133 import before_dispatch
    before_dispatch(db,p,task,job,node)
    lock_current_actor(db,p)
    return {'task':task, 'job':job, 'node':node, 'scope':frozen, 'release':release,
        'business':business, 'binding':binding, 'graph':graph, 'state':state, 'capability':actual}


def _events(db, p, action, object_id):
    return list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == object_id)))


def _event(db,p,action,object_id,actor_id,value):
    matches = [row for row in _events(db,p,action,object_id) if _same(row.data,value)]
    need(len(matches) == 1 and matches[0].actor_id == actor_id)


def _source(kind, identity, label, snapshot):
    _bytes(snapshot, MAX_BYTES)
    return {'kind':kind, 'id':identity, 'label':label, 'source_hash':digest(snapshot), 'snapshot':copy.deepcopy(snapshot)}


def collect_sources(db,p,context,current_call):
    """Only real saved products selected by this Task's checked graph runtime."""
    task, state, graph = context['task'], context['state'], context['graph']
    from .full_semantic_graph_v1 import check_run
    check_run(graph,state)
    from .workflow_document_task_integrity_v207 import verify_task_results
    verify_task_results(db, p, task, graph)
    nodes = {row['id']:row for row in graph['nodes']}; sources = []; issues = []
    receipts = task.data.get('workflow_runtime', {}).get('receipts', [])
    need(type(receipts) is list)
    for activation in state['activations'].values():
        node = nodes[activation['node_id']]
        if activation['id'] == context['scope']['activation_id'] or node['kind'] not in {'human','ability'}:
            continue
        if activation['status'] != 'completed':
            if activation['status'] != 'skipped': issues.append({'code':'unfinished_action',
                'activation_id':activation['id'], 'status':activation['status'], 'label':'仍有处理结果待补充'})
            continue
        matches = [row for row in receipts if row.get('activation_id') == activation['id']]
        need(len(matches) == 1); receipt = matches[0]
        need(receipt.get('actor') == node['actor_id'] and _same(receipt.get('output'),activation['output']))
        if node['kind'] == 'human':
            need(receipt.get('execution_mode') == 'human_graph_result')
            command_body = {'activation_id':activation['id'], 'output':activation['output'],
                'actor_id':node['actor_id'], 'expected_version':activation['version']-1, 'cancel_epoch':activation['epoch']}
            commands = [row for row in state['commands'] if row['request_id'] == receipt.get('request_id')]
            need(len(commands) == 1 and commands[0]['op'] == 'complete'
                and commands[0]['actor_id'] == node['actor_id'] and commands[0]['body_hash'] == digest(command_body))
            _event(db,p,'workflow.graph.human_completed',task.id,node['actor_id'],{'activation_id':activation['id']})
            sources.append(_source('human_product',receipt['request_id'],'已保存的人工处理结果',
                {'task_id':task.id,'activation_id':activation['id'],'actor_id':node['actor_id'],
                    'receipt':receipt,'command':commands[0],'output':activation['output']}))
        else:
            transport = task.data.get('graph_jobs',{}).get(activation['id'])
            need(type(transport) is dict and receipt.get('execution_mode') == 'registered_graph_ability'
                and receipt.get('invocation_id') == transport.get('invocation_id'))
            call = scoped(db,Invocation,receipt['invocation_id'],p)
            job = scoped(db,Job,transport['job_id'],p); execution = node['execution']
            need(call.id != current_call.id and call.status == 'succeeded' and call.capability_id == execution['capability_id']
                and call.idempotency_key == job.data.get('invocation_key') == transport.get('invocation_key')
                and call.data.get('job_id') == job.id and job.data.get('task_id') == task.id
                and job.data.get('activation_id') == activation['id'] and job.data.get('actor_id') == node['actor_id']
                and call.data.get('role') == execution['agent_role'] and call.data.get('capability_hash') == execution['capability_hash']
                and call.data.get('input_hash') == job.data.get('input_hash') == digest(receipt['input_snapshot'])
                and _same(call.data.get('output'),activation['output']))
            _event(db,p,'workflow.graph.node_completed',task.id,node['actor_id'],
                {'activation_id':activation['id'],'invocation_id':call.id})
            sources.append(_source('invocation',call.id,'已保存的注册能力结果',{'task_id':task.id,
                'activation_id':activation['id'],'job_id':job.id,'actor_id':node['actor_id'],
                'capability_id':call.capability_id,'capability_hash':call.data['capability_hash'],
                'agent_role':call.data['role'],'input_hash':call.data['input_hash'],
                'output_hash':digest(call.data['output']),'output':call.data['output']}))
            if type(call.data['output']) is dict and call.data['output'].get('schema') == 'business-reasoning-result-v197':
                analysis = call.data['output']['analysis']
                for assessment in analysis['candidate_assessments']:
                    if assessment['assessment'] != 'supported' or assessment['missing_information']:
                        issues.append({'code':'analysis_pending_review','source_id':call.id,
                            'assessment':copy.deepcopy(assessment)})
    seen = set()
    for field in ('native_goal_query_id','data_query_run_id'):
        identity = task.data.get(field)
        if identity is None or identity in seen: continue
        seen.add(identity); run = scoped(db,DataRun,identity,p)
        need(run.data.get('native_goal_task_id') == task.id or run.data.get('goal_delegation',{}).get('task_id') == task.id)
        sources.append(_source('data_run',run.id,'本任务已保存的资料查询',{'task_id':task.id,
            'version':run.version,'status':run.status,'question':run.question,'data':copy.deepcopy(run.data)}))
        if run.status != 'sufficient': issues.append({'code':'query_not_sufficient','source_id':run.id,'status':run.status})
    for item in task.data.get('image_snapshots',[]):
        row = scoped(db,Evidence,item['id'],p)
        need(row.sha256 == item['sha256'])
        sources.append(_source('evidence',row.id,'本任务关联的原始证据引用',{'task_id':task.id,
            'sha256':row.sha256,'size_bytes':row.size_bytes,'media_type':row.media_type,'version':row.version}))
    need(len(sources) <= MAX_SOURCES, '归集来源超过容量；原任务产物保留，未截断或存成完整档案')
    basis = {'task_id':task.id,'task_title':task.title,'actor_id':p.user_id,'instance_id':task.data.get('instance_id'),
        'release_id':context['release'].id,'package_id':task.package_id,'business_id':task.data.get('business_id'),
        'business_name':context['business'].get('name'),'graph_hash':state['graph_hash'],
        'source_binding_hash':context['binding']['binding_hash'],'goal_spec':copy.deepcopy(task.data.get('goal_spec')),
        'goal_hash':task.data.get('goal_hash'),'native_goal_context_hash':task.data.get('native_goal_context_hash'),
        'entity_ids':copy.deepcopy(task.data.get('entity_ids',[])),
        'object_snapshots':copy.deepcopy(task.data.get('object_snapshots',[])),
        'task_status_at_collection':task.status,'task_version_at_collection':task.version}
    result = {'basis':basis,'sources':sources,'issues':issues,
        'review_mode':context['binding']['review_mode'],'simulation_only':context['binding']['simulation_only']}
    if not sources: result['issues'].append({'code':'no_saved_upstream_products','label':'尚无已保存的上游处理产物'})
    _bytes(result,MAX_BYTES)
    return result


def _command(db,p,key):
    need(type(key) is str and 1 <= len(key) <= 200, status=422)
    return db.scalar(select(Command).where(Command.workspace_id == p.workspace_id,Command.request_key == key)
        .execution_options(populate_existing=True))


def _call(db,p,key):
    row = db.scalar(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.idempotency_key == key).execution_options(populate_existing=True))
    need(row is not None); return row


def _read_task(db,p,task_id):
    require(p,'admin','operator','viewer')
    return scoped(db,Task,_id(task_id),p)


def _receipt(head,version):
    return {'schema':RECEIPT,'archive_id':head.id,'version':version.version,'version_hash':version.version_hash,
        'task_id':head.task_id,'status':'candidate','simulation_only':head.simulation_only,**FLAGS}


def _proof(row):
    return {'schema':COMMAND,'id':row.id,'workspace_id':row.workspace_id,'actor_id':row.actor_id,
        'task_id':row.task_id,'archive_id':row.archive_id,'archive_version_id':row.archive_version_id,
        'invocation_id':row.invocation_id,'operation':row.operation,'request_key':row.request_key,
        'body_sha256':row.body_sha256,'response_hash':digest(row.data['response'])}


def _checked_inventory(db,p,head):
    _read_task(db,p,head.task_id)
    versions = list(db.scalars(select(Version).where(Version.workspace_id == p.workspace_id,
        Version.archive_id == head.id).order_by(Version.version)))
    need(len(versions) == head.version and 1 <= len(versions) <= MAX_VERSIONS)
    previous = None; views = []
    for ordinal, version in enumerate(versions,1):
        value = version.data['value']; proof = version.data['proof']; _bytes(value,MAX_BYTES)
        need(version.version == ordinal and version.previous_hash == previous
            and version.value_hash == digest(value) and version.version_hash == digest(proof)
            and _same(proof,{'schema':VALUE,'archive_id':head.id,'source_node_id':head.source_node_id,
                'version_id':version.id,'version':ordinal,
                'task_id':head.task_id,'actor_id':version.actor_id,'request_key':version.request_key,
                'previous_hash':previous,'value_hash':version.value_hash})
            and value['schema'] == VALUE and value['basis']['task_id'] == head.task_id
            and value['review_mode'] in {'author_self_review','engineering_simulation'}
            and type(value['simulation_only']) is bool
            and value['simulation_only'] is (value['review_mode'] == 'engineering_simulation')
            and all(value.get(key) is expected for key,expected in FLAGS.items()))
        refs = list(db.scalars(select(Reference).where(Reference.workspace_id == p.workspace_id,
            Reference.archive_version_id == version.id).order_by(Reference.ordinal)))
        need(len(refs) == len(value['sources']))
        for i,(ref,source) in enumerate(zip(refs,value['sources'])):
            need(ref.ordinal == i and ref.source_kind == source['kind'] and ref.source_id == source['id']
                and ref.source_hash == source['source_hash'] == digest(source['snapshot']) and _same(ref.data,source))
        command = _command(db,p,version.request_key)
        need(command is not None and command.archive_id == head.id and command.archive_version_id == version.id
            and command.actor_id == version.actor_id and command.task_id == head.task_id
            and hashlib.sha256(command.exact_body.encode('utf-8')).hexdigest() == command.body_sha256
            and command.command_hash == digest(_proof(command))
            and _same(command.data['response'],_receipt(head,version)))
        _event(db,p,SAVED,command.id,command.actor_id,_proof(command))
        views.append({'version':ordinal,'version_hash':version.version_hash,'value_hash':version.value_hash,
            'actor_id':version.actor_id,'saved_at':version.created_at.isoformat(),'value':copy.deepcopy(value)})
        previous = version.version_hash
    need(previous == head.current_version_hash and head.status == 'candidate' and head.actor_id == versions[0].actor_id
        and head.record_type == views[-1]['value']['record_type'] and head.title == views[-1]['value']['title']
        and head.summary == views[-1]['value']['summary'] and head.simulation_only is views[-1]['value']['simulation_only'])
    return views


def _checked(db,p,head):
    try: return _checked_inventory(db,p,head)
    except (ValueError,KeyError,TypeError,AttributeError,IndexError): need(False)


def _persist(db,p,task_id,node_id,inputs,capsule,key,raw,*,call=None,head=None):
    need(_command(db,p,key) is None)
    if head is None:
        head = db.scalar(select(Archive).where(Archive.workspace_id == p.workspace_id,Archive.task_id == task_id,
            Archive.source_node_id == node_id).with_for_update().execution_options(populate_existing=True))
    if head is None:
        head = Archive(workspace_id=p.workspace_id,task_id=task_id,source_node_id=node_id,actor_id=p.user_id,
            status='candidate',record_type=inputs['record_type'],title=inputs['title'],summary=inputs['summary'],
            simulation_only=capsule['simulation_only'],version=1,current_version_hash='0'*64,data={})
        db.add(head);db.flush(); previous = None
    else:
        _checked(db,p,head); need(head.version < MAX_VERSIONS)
        need(head.simulation_only is capsule['simulation_only'])
        previous = head.current_version_hash; head.version += 1
        head.record_type=inputs['record_type'];head.title=inputs['title'];head.summary=inputs['summary']
    value = {'schema':VALUE,**copy.deepcopy(inputs),**copy.deepcopy(capsule),
        'summary_verification':'unconfirmed_proposal',**FLAGS}; _bytes(value,MAX_BYTES)
    version = Version(workspace_id=p.workspace_id,archive_id=head.id,actor_id=p.user_id,version=head.version,
        request_key=key,previous_hash=previous,value_hash=digest(value),version_hash='0'*64,data={})
    db.add(version);db.flush()
    proof = {'schema':VALUE,'archive_id':head.id,'source_node_id':head.source_node_id,
        'version_id':version.id,'version':version.version,
        'task_id':task_id,'actor_id':p.user_id,'request_key':key,'previous_hash':previous,'value_hash':version.value_hash}
    version.version_hash=digest(proof);version.data={'value':value,'proof':proof};head.current_version_hash=version.version_hash
    for i,source in enumerate(value['sources']):
        db.add(Reference(workspace_id=p.workspace_id,archive_version_id=version.id,ordinal=i,
            source_kind=source['kind'],source_id=source['id'],source_hash=source['source_hash'],data=copy.deepcopy(source)))
    response = _receipt(head,version)
    command = Command(workspace_id=p.workspace_id,task_id=task_id,archive_id=head.id,archive_version_id=version.id,
        actor_id=p.user_id,invocation_id=call.id if call else None,operation='collect' if call else 'revise',
        request_key=key,version=1,exact_body=raw.decode('utf-8'),body_sha256=hashlib.sha256(raw).hexdigest(),
        command_hash='0'*64,data={'response':response})
    db.add(command);db.flush();command.command_hash=digest(_proof(command));audit(db,p,SAVED,command.id,_proof(command))
    return response,command


def execute(db,p,cap,inputs,key,approval):
    context = validate_call_approval(db,p,cap,'data',inputs,key,approval)
    original = _command(db,p,key)
    call = _call(db,p,key)
    if original is not None or STATE_KEY in call.data:
        return reconcile(db,p,cap,inputs,key,approval,call)
    need(call.status == 'running' and call.capability_id == cap.id and call.data.get('role') == 'data'
        and STATE_KEY not in call.data
        and call.data.get('side_effect') is True and call.data.get('job_id') == context['job'].id
        and call.data.get('input_hash') == digest(inputs) and call.data.get('capability_hash') == cap.content_hash
        and _same(call.data.get('approval'),approval))
    capsule = collect_sources(db,p,context,call)
    body = {'schema':COMMAND,'operation':'collect','request_key':key,'task_id':context['task'].id,
        'invocation_id':call.id,'approval':approval,'inputs':_inputs(inputs)}
    raw = canonical_json(body).encode('utf-8'); _bytes(body,MAX_COMMAND_BYTES)
    response,command = _persist(db,p,context['task'].id,context['node']['id'],inputs,capsule,key,raw,call=call)
    call.data = {**call.data,STATE_KEY:{'command_id':command.id,'archive_id':response['archive_id'],
        'version':response['version'],'version_hash':response['version_hash'],'body_sha256':command.body_sha256}}
    # No commit: the gateway's successful Invocation/outcome is the same write.
    return response


def reconcile(db,p,cap,inputs,key,approval,prior):
    require(p,'admin','operator')
    need(prior.idempotency_key == key and prior.workspace_id == p.workspace_id and prior.capability_id == cap.id
        and prior.data.get('role') == 'data' and prior.data.get('side_effect') is True
        and type(approval) is dict and approval.get('schema') == APPROVAL
        and prior.data.get('job_id') == approval.get('job_id')
        and prior.data.get('capability_hash') == cap.content_hash and prior.data.get('input_hash') == digest(_inputs(inputs))
        and _same(prior.data.get('approval'),approval))
    command = _command(db,p,key)
    need(command is not None, '原存档命令尚无可恢复的已保存记录；保留原任务，不默默重新创建')
    need(command.invocation_id == prior.id and command.task_id == approval['task_id']
        and command.actor_id == p.user_id and command.operation == 'collect')
    need(_same(prior.data.get(STATE_KEY),{'command_id':command.id,'archive_id':command.archive_id,
        'version':command.data['response']['version'],'version_hash':command.data['response']['version_hash'],
        'body_sha256':command.body_sha256}))
    head = scoped(db,Archive,command.archive_id,p); _checked(db,p,head)
    expected_body = {'schema':COMMAND,'operation':'collect','request_key':key,'task_id':head.task_id,
        'invocation_id':prior.id,'approval':approval,'inputs':inputs}
    need(command.exact_body == canonical_json(expected_body))
    return copy.deepcopy(command.data['response'])


def read_original_receipt(db,p,call):
    """Read a complete original collect inventory as a current Task reviewer.

    This is not reconciliation: no current lease, Goal execution guard, new
    archive version or impersonated submitting Principal is used. The actual
    original Job owns the command; the current actor only exercises Task read
    permission. Original nonce/reconcile endpoints keep their owner checks.
    """
    try:
        actual = scoped(db,Invocation,call.id,p)
        need(actual.workspace_id == p.workspace_id and actual.id == call.id
            and actual.status == 'succeeded' and actual.capability_id == call.capability_id
            and actual.idempotency_key == call.idempotency_key and _same(actual.data,call.data))
        job = scoped(db,Job,actual.data['job_id'],p)
        task = _read_task(db,p,job.data['task_id'])
        inputs = _inputs(job.data['inputs']); approval = actual.data.get('approval')
        manifest = validate_manifest(actual.data['capability_snapshot'])
        need(job.kind == 'execute_full_semantic_graph_node'
            and job.data['input_hash'] == actual.data['input_hash'] == digest(inputs)
            and job.idempotency_key == job.data['invocation_key'] == actual.idempotency_key
            and actual.data.get('role') == 'data' and actual.data.get('side_effect') is True
            and digest(manifest) == actual.data['capability_hash']
            and type(approval) is dict and set(approval) == APPROVAL_KEYS
            and _same(approval,{'schema':APPROVAL,'task_id':task.id,'job_id':job.id,
                'activation_id':job.data['activation_id'],'graph_hash':job.data['graph_hash'],
                'input_hash':digest(inputs),'capability_id':actual.capability_id,
                'capability_hash':actual.data['capability_hash'],'cancel_epoch':job.data['cancel_epoch']}))
        command = _command(db,p,actual.idempotency_key)
        need(command is not None and command.invocation_id == actual.id and command.task_id == task.id
            and command.actor_id == job.data['actor_id'] and command.operation == 'collect')
        head = scoped(db,Archive,command.archive_id,p)
        _checked(db,p,head)
        activation = task.data['graph_runtime']['activations'][job.data['activation_id']]
        transport = task.data['graph_jobs'][job.data['activation_id']]
        need(head.task_id == task.id and head.source_node_id == activation['node_id']
            and task.data['graph_runtime']['graph_hash'] == job.data['graph_hash']
            and transport.get('job_id') == job.id and transport.get('invocation_id') == actual.id
            and transport.get('invocation_key') == actual.idempotency_key
            and _same(actual.data.get(STATE_KEY),{'command_id':command.id,'archive_id':head.id,
                'version':command.data['response']['version'],'version_hash':command.data['response']['version_hash'],
                'body_sha256':command.body_sha256}))
        body = {'schema':COMMAND,'operation':'collect','request_key':actual.idempotency_key,
            'task_id':task.id,'invocation_id':actual.id,'approval':approval,'inputs':inputs}
        need(command.exact_body == canonical_json(body)
            and _same(command.data['response'],actual.data.get('output')))
        return copy.deepcopy(command.data['response'])
    except HTTPException:
        raise
    except (ValueError,KeyError,TypeError,AttributeError,IndexError,RecursionError,OverflowError):
        need(False)


def get(db,p,archive_id,version=None):
    head = scoped(db,Archive,_id(archive_id),p); versions = _checked(db,p,head)
    chosen = versions[-1] if version is None else next((row for row in versions if row['version'] == version),None)
    need(chosen is not None,status=404)
    return {'schema':VIEW,'id':head.id,'task_id':head.task_id,'status':'candidate',**copy.deepcopy(chosen),
        'history':[{k:v for k,v in row.items() if k != 'value'} for row in versions],
        'current_source_checked':False,**FLAGS}


def list_archives(db,p,task_id,*,record_type=None,q=None):
    task = _read_task(db,p,task_id)
    need(record_type is None or type(record_type) is str and len(record_type) <= 80,status=422)
    need(q is None or type(q) is str and len(q) <= 200,status=422)
    query = select(Archive).where(Archive.workspace_id == p.workspace_id,Archive.task_id == task.id)
    if record_type is not None: query = query.where(Archive.record_type == record_type)
    if q:
        # Literal substring; '%'/'_' are not implicit SQL wildcard commands.
        term = '%' + q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
        query = query.where(Archive.title.ilike(term,escape='\\') | Archive.summary.ilike(term,escape='\\'))
    rows = list(db.scalars(query.order_by(Archive.created_at.desc()).limit(101)))
    need(len(rows) <= 100,'本任务档案超过单次查询容量，请缩小类型或文本条件',422)
    items = []
    for row in rows:
        versions = _checked(db,p,row); value = versions[-1]['value']
        items.append({'id':row.id,'task_id':row.task_id,'record_type':row.record_type,'title':row.title,
            'summary':row.summary,'version':row.version,'version_hash':row.current_version_hash,'status':'candidate',
            'simulation_only':row.simulation_only,'issue_count':len(value['issues']),**FLAGS})
    return {'schema':'workflow-task-archive-list-v198','task_id':task.id,'items':items,'current_source_checked':False}


def recover(db,p,key,expected_sha256):
    require(p,'admin','operator','viewer'); need(type(expected_sha256) is str and SHA.fullmatch(expected_sha256),status=422)
    command = _command(db,p,key)
    if command is None: return {'schema':ORIGINAL,'request_key':key,'found':False,'body_sha256':None,'result':None}
    need(command.actor_id == p.user_id,'请由原提交账号读取保存回执',403)
    _checked(db,p,scoped(db,Archive,command.archive_id,p))
    need(command.body_sha256 == expected_sha256,'原号不能替换请求内容')
    return {'schema':ORIGINAL,'request_key':key,'found':True,'body_sha256':command.body_sha256,
        'result':copy.deepcopy(command.data['response'])}


def parse_revision(raw):
    need(type(raw) is bytes and len(raw) <= MAX_COMMAND_BYTES,status=413)
    def pairs(items):
        result = {}
        for key,value in items:
            need(key not in result,status=422);result[key]=value
        return result
    def invalid(_value): need(False,status=422)
    try: body = json.loads(raw.decode('utf-8',errors='strict'),object_pairs_hook=pairs,parse_constant=invalid)
    except (ValueError,UnicodeError,TypeError): need(False,status=422)
    try: _bytes(body,MAX_COMMAND_BYTES)
    except (ValueError,TypeError,UnicodeError,RecursionError): need(False,status=422)
    need(type(body) is dict and set(body) == {'schema','request_id','expected_version','source_version_hash',
        'record_type','title','summary'} and body['schema'] == REVISION,status=422)
    _id(body['request_id']);need(type(body['expected_version']) is int and body['expected_version'] >= 1,status=422)
    need(type(body['source_version_hash']) is str and SHA.fullmatch(body['source_version_hash']),status=422)
    _inputs({key:body[key] for key in ('record_type','title','summary')})
    return body


def revise(db,p,archive_id,raw):
    require(p,'admin','operator');body=parse_revision(raw); _id(archive_id)
    original = _command(db,p,body['request_id'])
    if original is not None:
        need(original.actor_id == p.user_id and original.archive_id == archive_id and original.exact_body == raw.decode('utf-8'))
        _checked(db,p,scoped(db,Archive,archive_id,p));return copy.deepcopy(original.data['response'])
    head = scoped(db,Archive,archive_id,p,True);views = _checked(db,p,head)
    # A same-nonce peer may have committed while this request waited for the
    # head lock. Recover that exact command before applying a stale CAS test.
    original = _command(db,p,body['request_id'])
    if original is not None:
        need(original.actor_id == p.user_id and original.archive_id == archive_id and original.exact_body == raw.decode('utf-8'))
        return copy.deepcopy(original.data['response'])
    if head.version != body['expected_version'] or head.current_version_hash != body['source_version_hash']:
        raise HTTPException(409, {'code':'workflow_task_archive_version_conflict','not_applied':True,
            'request_key':body['request_id'],'body_sha256':hashlib.sha256(raw).hexdigest(),
            'message':'档案已产生新版本；本次修订未应用，原输入保留，请先查看当前版本'})
    value = views[-1]['value'];capsule={key:copy.deepcopy(value[key]) for key in ('basis','sources','issues','review_mode','simulation_only')}
    response,_command_row = _persist(db,p,head.task_id,head.source_node_id,
        {key:body[key] for key in ('record_type','title','summary')},capsule,body['request_id'],raw,head=head)
    db.commit();return response
