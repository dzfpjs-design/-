"""Build one local, unconsented Evidence from a real Task's saved query rows.

The packet is a candidate comparison source, not a new Entity fact, device
grant, human approval or model consent. The gateway commits its immutable
inventory, original command, successful Invocation and produced audit together.
"""
import copy
import hashlib
import re
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from . import workflow_task_context_v196 as reader
from .business_flow_review_store import _bytes, _same
from .contracts import Manifest, digest
from .models import (Audit, Capability, DataGap, DataRun, Entity, Evidence, InstanceRelease,
    Invocation, Job, Observation, Task)
from .services import audit, published, require, scoped, validate
from .typed_payload_codec_v131 import canonical_json
from .workflow_task_packet_models_v199 import (WorkflowTaskEvidencePacket as Packet,
    WorkflowTaskEvidencePacketCommand as Command)

ADAPTER = 'task_evidence_packet'
OP = 'build_current_task_packet'
APPROVAL = 'workflow-task-evidence-packet-approval-v199'
RECEIPT = 'workflow-task-evidence-packet-receipt-v199'
VIEW = 'workflow-task-evidence-packet-view-v199'
ORIGINAL = 'workflow-task-evidence-packet-original-result-v199'
COMMAND = 'workflow-task-evidence-packet-command-v199'
STATE_KEY = 'task_evidence_packet_v199'
SAVED = 'workflow.task_evidence_packet.saved_v199'
PRODUCED = 'workflow.task_evidence_packet.produced_v199'
PRODUCED_SCHEMA = 'workflow-task-evidence-packet-produced-v199'
APPROVAL_KEYS = {'schema','task_id','job_id','activation_id','graph_hash','input_hash',
    'capability_id','capability_hash','cancel_epoch'}
MAX_BYTES = 8 * 1024 * 1024
MAX_TEXT = 20000
SHA = re.compile(r'^[a-f0-9]{64}$')
UUID_IN_TEXT = re.compile(r'[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}')
FLAGS = {'permissions_verified':False,'can_dispatch':False}


def need(ok,message='原任务证据包、独立库存或观测来源无法核对；原记录保留，未重新生成或授权',status=409):
    if not ok: raise HTTPException(status,message)


def _id(value):
    try: need(type(value) is str and str(UUID(value)) == value,status=422)
    except (ValueError,TypeError,AttributeError): need(False,status=422)
    return value


def _object(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def input_schema(): return reader.input_schema()


def output_schema():
    ident={'type':'string','pattern':r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'}
    packet=_object({'id':ident,'evidence_id':ident,'sha256':{'type':'string','pattern':SHA.pattern},
        'version':{'type':'integer','const':1},'simulation_only':{'type':'boolean'},
        'proposal_only':{'type':'boolean','const':True},'cloud_authorized':{'type':'boolean','const':False}})
    return _object({'schema':{'type':'string','const':RECEIPT},'context_result':reader.output_schema(),
        'evidence_ids':{'type':'array','items':ident,'minItems':1,'maxItems':1},'packet':packet,
        **{key:{'type':'boolean','const':False} for key in FLAGS}})


def checked_result(value):
    _bytes(value,MAX_BYTES);validate(output_schema(),value);reader.checked_result(value['context_result'])
    need(value['context_result']['evidence_package'] is not None
        and value['evidence_ids'] == [value['packet']['evidence_id']]
        and type(value['packet']['version']) is int and value['packet']['version'] == 1
        and value['packet']['proposal_only'] is True and value['packet']['cloud_authorized'] is False
        and all(type(value[key]) is bool and value[key] is False for key in FLAGS))
    return copy.deepcopy(value)


def validate_manifest(manifest):
    data=manifest.model_dump(mode='json') if isinstance(manifest,Manifest) else copy.deepcopy(manifest)
    Manifest.model_validate(data)
    need(data.get('kind') == 'tool' and data.get('allowed_agent_roles') == ['data']
        and _same(data.get('execution'),{'adapter':ADAPTER,'side_effect':True})
        and _same(data.get('input_schema'),input_schema()) and _same(data.get('output_schema'),output_schema())
        and data.get('dependencies') == [] and data.get('data_policy',{}).get('cloud_allowed') is False
        and type(data.get('budget',{}).get('max_attempts')) is int and data['budget']['max_attempts'] == 1
        and _same(data.get('specification'),{'schema':'workflow-task-evidence-packet-contract-v199',
            'operation':OP,'candidate_only':True}), '证据包须登记为一次本地数据持久写能力，不得伪装只读、自动授权或正式确认',422)
    return data


def _fresh(db,p,model,identity):
    row=db.scalar(select(model).where(model.workspace_id == p.workspace_id,model.id == identity)
        .execution_options(populate_existing=True))
    need(row is not None);return row


def make_approval(task,job,node,inputs):
    return {'schema':APPROVAL,'task_id':task.id,'job_id':job.id,'activation_id':job.data['activation_id'],
        'graph_hash':job.data['graph_hash'],'input_hash':digest(inputs),
        'capability_id':node['execution']['capability_id'],'capability_hash':node['execution']['capability_hash'],
        'cancel_epoch':job.data['cancel_epoch']}


def validate_call_approval(db,p,cap,role,inputs,key,approval):
    from . import workflow_graph_scope as scope, workflow_graph as workflow
    from .full_semantic_graph_v1 import ability_inputs
    from .leases import ownership
    from .relations import lock_current_actor
    from .layered_approval_store_v175 import guard
    from .workflow_graph_goal_guard_v133 import before_dispatch
    require(p,'admin','operator');reader._inputs(inputs)
    need(type(key) is str and 1 <= len(key) <= 200 and type(approval) is dict
        and set(approval) == APPROVAL_KEYS and approval['schema'] == APPROVAL and role == 'data'
        and type(approval['cancel_epoch']) is int and approval['cancel_epoch'] >= 0)
    for name in ('task_id','job_id','capability_id'): _id(approval[name])
    for name in ('graph_hash','input_hash','capability_hash'):
        need(type(approval[name]) is str and SHA.fullmatch(approval[name]))
    original_hash=cap.content_hash;actual=_fresh(db,p,Capability,cap.id)
    published(db,p,actual.id,'tool');validate_manifest(actual.data);need(actual.content_hash == original_hash)
    lease=ownership(db);need(lease.get('job_id') == approval['job_id'] and bool(lease.get('lease_token')))
    job=db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,Job.id == approval['job_id'],
        Job.kind == scope.KIND,Job.status == 'running',Job.lease_token == lease['lease_token'],
        Job.lease_until > func.clock_timestamp()).execution_options(populate_existing=True))
    need(job is not None and job.data.get('invocation_key') == key and job.data.get('actor_id') == p.user_id
        and job.data.get('task_id') == approval['task_id'])
    locked=scoped(db,Task,approval['task_id'],p,True)
    task,release,business,graph,state,activation,node=scope._frozen(db,p,locked.id,approval['activation_id'])
    transport=scope._job_matches(task,job,state,activation,node);spec=node['execution']
    need(task.id == locked.id and state['status'] in {'running','waiting'} and activation['status'] == 'ready'
        and activation['id'] in state['eligible_ability_ids'] and activation['epoch'] == state['cancel_epoch']
        and node['actor_id'] == p.user_id and spec['agent_role'] == role and spec['adapter'] == ADAPTER
        and spec['capability_id'] == actual.id and spec['capability_hash'] == actual.content_hash
        and spec['release_version'] == actual.release_version and spec['max_attempts'] == 1
        and _same(spec['input_schema'],input_schema()) and _same(spec['output_schema'],output_schema())
        and transport.get('phase') == 'running' and _same(approval,make_approval(task,job,node,inputs))
        and _same(job.data['inputs'],inputs) and _same(ability_inputs(graph,state,activation['id']),inputs))
    binding=workflow.business_binding(release,business)
    if binding.get('schema') in {'authored-decision-workflow-binding-v278', 'authored-decision-workflow-binding-v286'}:
        from .authored_workflow_store_v208 import check_binding
        need(_same(check_binding(binding), binding) and binding['simulation_only'] is True)
    else:
        need(binding.get('schema') == 'business-flow-nested-execution-binding-v195'
            and binding.get('review_mode') in {'author_self_review','engineering_simulation'}
            and type(binding.get('simulation_only')) is bool
            and binding['simulation_only'] is (binding['review_mode'] == 'engineering_simulation')
            and binding.get('binding_hash') == digest({k:v for k,v in binding.items() if k != 'binding_hash'}))
    guard(db,p,task,record_actor=True);workflow.live(db,p,task,release,business)
    before_dispatch(db,p,task,job,node);lock_current_actor(db,p)
    return {'task':task,'job':job,'node':node,'scope':scope._scope(task,job,node,activation),
        'release':release,'business':business,'binding':binding,'graph':graph,'state':state,'capability':actual}


def _instant(value): return value.isoformat() if value is not None else None


def _public(snapshot,internal_ids=()):
    """Do not move opaque system identities or credentials into cloudable text."""
    forbidden={'authorization','proxy-authorization','cookie','set-cookie','password','api_key','secret_ref',
        'access_token','refresh_token','headers','http_headers'}
    def walk(value):
        if type(value) is str:
            need(not any(identity.lower() in value.lower() for identity in internal_ids),
                '正文包含本任务内部编号；原资料保留，请先提供可公开阅读的名称或字段',422)
            need(re.search(r'(?i)\b(?:bearer\s+\S+|(?:api[_-]?key|password|access[_-]?token)\s*[:=])',value) is None,
                '正文包含疑似凭据；原资料保留，未生成可外发证据',422)
        elif type(value) is dict:
            for key,item in value.items():
                need(type(key) is str and key.lower() not in forbidden,'正文包含凭据或HTTP头字段，未生成证据',422)
                walk(key);walk(item)
        elif type(value) is list:
            for item in value:walk(item)
    walk(snapshot);return snapshot


def _field_metadata(context,entities,fields,package):
    schemas=package.data['specification']['entity_schemas'];types=context['release'].data['snapshot'].get('object_types',[])
    result=[]
    for key in fields:
        choices=[]
        for entity in entities:
            properties=schemas[entity.entity_type].get('properties',{})
            if key not in properties:continue
            prop=properties[key];kind=next((item for item in types if item['key'] == entity.entity_type),None)
            labels=[item for item in (kind.get('fields',[]) if kind else []) if item['key'] == key]
            need(len(labels) <= 1);label=labels[0] if labels else {}
            units=[item for item in (prop.get('unit'),prop.get('x-unit'),label.get('unit')) if item is not None]
            need(not units or all(_same(item,units[0]) for item in units))
            choices.append({'key':key,'label':label.get('label') or prop.get('title') or key,
                'unit':units[0] if units else None})
        need(choices and all(_same(item,choices[0]) for item in choices),'同一业务字段的名称或单位定义不一致，未猜测或合并',422)
        result.append(choices[0])
    return result


def collect_snapshot(db,p,context,context_result):
    """Reconstruct only the frozen DataRun's actual rows; never re-query history."""
    from . import native_goals
    from .instance_data_receipts import observation_receipt
    from .observation_integrity import checked_report
    from .simulation_observation_origin_v177 import guard as origin_guard
    from .http_observation_exchange_v177 import checked_store, snapshot as exchange_snapshot
    from .task_evidence_packet_text_v199 import validate_snapshot
    task=context['task'];package_ref=context_result['evidence_package']
    need(package_ref is not None and task.data.get('native_goal_context'),
        '原任务尚无已核对的正式目标资料查询，未生成空证据包')
    _fresh(db,p,DataRun,package_ref['query']['query_id'])
    goal=native_goals.guard(db,p,task,lock_sources=False);run=reader._run(db,p,task,goal)
    need(run is not None and package_ref['scope']['task_id'] == task.id
        and package_ref['query']['query_id'] == run.id and package_ref['query']['version_at_read'] == run.version
        and package_ref['query']['result_hash_at_read'] == digest(run.data['result']))
    for identity in run.data['data_gap_ids']:_fresh(db,p,DataGap,identity)
    gaps=reader._gaps(db,p,task,run);raw=run.data['result']['result'];fields=run.data['request']['fields']
    need(type(raw) is dict and type(fields) is list and len(fields) == len(set(fields))
        and run.data['result']['status'] in {'sufficient','conflict','insufficient'}
        and package_ref['evidence_status'] == run.data['result']['status'])
    entities=[_fresh(db,p,Entity,identity) for identity in run.data['request']['entity_ids']]
    need(len(entities) == len({row.id for row in entities}) and [row.id for row in entities] == task.data['entity_ids'])
    package=_fresh(db,p,Capability,context['release'].package_id);published(db,p,package.id,'domain_package')
    needed=_field_metadata(context,entities,fields,package)
    refs={row.id:'对象'+str(index+1) for index,row in enumerate(entities)}
    objects={row.id:{'object_ref':refs[row.id],'label':row.data.get('name') or row.data.get('title') or refs[row.id],
        'archive_attributes':{},'observations':[]} for row in entities}
    by_id={row.id:row for row in entities};private=[];seen=set();admitted=[];simulation=context['binding']['simulation_only']
    archive_rows=raw.get('evidence',[]);observed_rows=raw.get('observation_evidence',[])
    need(type(archive_rows) is list and type(observed_rows) is list
        and len(archive_rows)+len(observed_rows) <= reader.MAX_EVIDENCE)
    for row in archive_rows:
        need(type(row) is dict and row.get('entity_id') in by_id and row['entity_id'] not in seen)
        entity=by_id[row['entity_id']];attrs={key:value for key,value in entity.data.items() if not fields or key in fields}
        need(row.get('version') == entity.version and _same(row.get('attributes'),attrs)
            and row.get('observed_at') == _instant(entity.observed_at) and row.get('valid_until') == _instant(entity.valid_until)
            and row.get('source') == 'resource_entity')
        if 'verification' in row:need(row['verification'] == 'recorded')
        seen.add(entity.id);admitted.append(copy.deepcopy(row));objects[entity.id]['archive_attributes']=copy.deepcopy(attrs)
        private.append({'kind':'archive','record':copy.deepcopy(row),'entity':{'id':entity.id,'version':entity.version,
            'entity_type':entity.entity_type,'data':copy.deepcopy(entity.data),'observed_at':_instant(entity.observed_at),
            'valid_until':_instant(entity.valid_until),'package_id':entity.schema_capability_id}})
    observation_ids=set()
    for row in observed_rows:
        need(type(row) is dict and row.get('entity_id') in by_id and row.get('observation_id') not in observation_ids)
        obs=_fresh(db,p,Observation,_id(row['observation_id']));need(obs.entity_id == row['entity_id'])
        _fresh(db,p,Capability,obs.source_id)
        for identity in obs.data.get('evidence_ids',[]):_fresh(db,p,Evidence,identity)
        for identity in (obs.data.get('invocation_id'),):
            if identity:_fresh(db,p,Invocation,identity)
        if obs.data.get('extractor_id'):_fresh(db,p,Capability,obs.data['extractor_id'])
        report=checked_report(db,p,obs);origin=origin_guard(db,p,obs,execution=False,result=report['extracted'])
        reception=observation_receipt(db,p,obs);attrs={key:value for key,value in report['attributes'].items() if not fields or key in fields}
        expected={'observation_id':obs.id,'entity_id':obs.entity_id,'version':obs.version,'source_id':obs.source_id,
            'observed_at':_instant(obs.observed_at),'attributes':attrs,'anomalies':report['extracted'].get('anomalies',[]),
            'evidence_ids':copy.deepcopy(obs.data.get('evidence_ids',[])),'invocation_id':obs.data.get('invocation_id'),
            'verification':'confirmed' if report['review'] == 'confirmed' else 'unverified_extraction',
            'source':'processed_observation',**reception}
        need(report['review'] != 'rejected' and obs.observed_at is not None and _same(row,expected))
        source=_fresh(db,p,Capability,obs.source_id);is_sim=type(origin) is dict and origin.get('simulation') is True
        need(not is_sim or simulation,'模拟来源不能被移入未标记模拟的任务资料包')
        exchange=None;unit_views=[report.get('field_units'),report['extracted'].get('field_units'),
            obs.data.get('field_units'),origin.get('field_units') if type(origin) is dict else None]
        if type(origin) is dict and origin.get('exchange_id'):
            exchange=checked_store(db,p,origin['exchange_id'],lock=False)
            unit_views.append(exchange.data['projection'].get('field_units'))
        units={item['key']:item['unit'] for item in needed}
        for declared in unit_views:
            if declared is None:continue
            need(type(declared) is dict)
            for field in attrs:
                if field in declared and declared[field] is not None:
                    need(field in units and _same(units[field],declared[field]),
                        '原观测单位与业务字段定义不一致，未换算或猜测',422)
        anomalies=[]
        for item in expected['anomalies']:
            need(type(item) is dict and item.get('field') in fields and 'value' in item
                and type(item.get('severity')) is str and bool(item['severity']))
            anomalies.append({key:copy.deepcopy(item[key]) for key in ('field','value','severity')})
        objects[obs.entity_id]['observations'].append({'source_label':source.name,'observed_at':_instant(obs.observed_at),
            'attributes':copy.deepcopy(attrs),'anomalies':anomalies,
            'verification':'human_confirmed' if report['review'] == 'confirmed' else 'candidate','simulation_only':is_sim})
        proof={'kind':'observation','record':copy.deepcopy(row),'observation':{'id':obs.id,'version':obs.version,
            'data':copy.deepcopy(obs.data),'source_id':obs.source_id,'entity_id':obs.entity_id,
            'source_record_id':obs.source_record_id,'processing_version':obs.processing_version,
            'observed_at':_instant(obs.observed_at)},'report':report,'origin':origin,'reception':reception}
        if exchange is not None:proof['http_exchange']=exchange_snapshot(exchange)
        private.append(proof);observation_ids.add(obs.id);admitted.append(copy.deepcopy(row))
    issues=[]
    for row in raw.get('conflicts',[]):
        need(type(row) is dict and row.get('entity_id') in refs and row.get('field') in fields)
        candidates=[item for item in admitted if item['entity_id'] == row['entity_id']
            and row['field'] in item['attributes'] and item.get('observed_at')]
        groups={}
        for item in candidates:groups.setdefault(item['observed_at'],set()).add(canonical_json(item['attributes'][row['field']]))
        need(any(len(values)>1 for values in groups.values()))
        need(set(row.get('observation_ids',[])) <= observation_ids)
        label=next(item['label'] for item in needed if item['key'] == row['field'])
        issues.append(refs[row['entity_id']]+'的“'+label+'”在原查询中存在同一时点多来源冲突，尚未合并为事实。')
    for row in raw.get('gaps',[]):
        need(type(row) is dict and row.get('entity_id') in refs and type(row.get('reason')) is str)
        issues.append(refs[row['entity_id']]+'的原查询缺项说明：'+row['reason'])
    for row in raw.get('excluded_evidence',[]):
        need(type(row) is dict and row.get('entity_id') in refs and type(row.get('reason')) is str)
        issues.append(refs[row['entity_id']]+'有未纳入的原记录：'+row['reason'])
    for row in archive_rows:
        if row.get('observed_at') is None:issues.append(refs[row['entity_id']]+'的档案观测时间未知，未补用当前时钟。')
        if row.get('valid_until') is not None:issues.append(refs[row['entity_id']]+'的原档案有效期至 '+row['valid_until']+'；没有延长其有效期。')
    for issue in context_result['issues']:issues.append(issue['message'])
    reference=run.data['request'].get('reference_at')
    snapshot={'schema':'task-evidence-text-snapshot-v199','task_title':task.title,'simulation_only':simulation,
        'evidence_status':run.data['result']['status'],'reference_at':reference,'required_fields':needed,
        'objects':list(objects.values()),'gaps':[{'object_ref':refs[row.entity_id],
            'fields':copy.deepcopy(row.data['missing_fields']),'status':row.status} for row in gaps],'issues':issues}
    internal_ids=set()
    def internal(value):
        if type(value) is str and UUID_IN_TEXT.fullmatch(value):internal_ids.add(value)
    for value in (p.workspace_id,p.user_id,task.id,task.owner_id,task.package_id,context['release'].id,
            context['release'].instance_id,context['capability'].id,context['job'].id,run.id):internal(value)
    for value in context['scope'].values():internal(value)
    for entity in entities:internal(entity.id)
    for gap in gaps:internal(gap.id)
    for source in private:
        record=source['record']
        for key in ('entity_id','observation_id','source_id','invocation_id'):internal(record.get(key))
        for value in record.get('evidence_ids',[]):internal(value)
        for value in (source.get('origin') or {}).values():internal(value)
    for candidate in [package_ref,*context_result['candidates']]:
        for section in ('scope','producer','query','policy','gap'):
            values=candidate.get(section,{})
            for value in values.values():internal(value)
            for pin in values.get('source_pins',[]):internal(pin['source_id'])
    snapshot=validate_snapshot(_public(snapshot,internal_ids))
    origin = ({'binding_schema': context['binding']['schema']}
        if context['binding'].get('schema') in {'authored-decision-workflow-binding-v278', 'authored-decision-workflow-binding-v286'}
        else {'review_mode': context['binding']['review_mode']})
    provenance={'schema':'workflow-task-evidence-packet-provenance-v199','scope':copy.deepcopy(context['scope']),
        'binding_hash':context['binding']['binding_hash'],**origin,
        'release_id':context['release'].id,'package_hash':package.content_hash,'goal':copy.deepcopy(goal),
        'query':{'id':run.id,'version':run.version,'data':copy.deepcopy(run.data)},'sources':private,
        'gaps':[{'id':row.id,'entity_id':row.entity_id,'version':row.version,'status':row.status,
            'data':copy.deepcopy(row.data)} for row in gaps]}
    _bytes(provenance,MAX_BYTES);return snapshot,provenance


def _command(db,p,key):
    return db.scalar(select(Command).where(Command.workspace_id == p.workspace_id,Command.request_key == key)
        .execution_options(populate_existing=True))


def _call(db,p,key,*,lock=False):
    statement=select(Invocation).where(Invocation.workspace_id == p.workspace_id,Invocation.idempotency_key == key)
    if lock:statement=statement.with_for_update()
    row=db.scalar(statement.execution_options(populate_existing=True));need(row is not None);return row


def _negative_inventory(db,p,key,call=None):
    """Absence requires all family reverse anchors, not only a missing Command."""
    packets=list(db.scalars(select(Packet).where(Packet.workspace_id == p.workspace_id,Packet.request_key == key)
        .limit(2).execution_options(populate_existing=True)))
    calls=list(db.scalars(select(Invocation).where(Invocation.workspace_id == p.workspace_id,
        Invocation.idempotency_key == key).limit(2).execution_options(populate_existing=True)))
    need(len(calls) <= 1 and (call is None or len(calls) == 1 and calls[0].id == call.id))
    original=calls[0] if calls else None
    saved=list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,Audit.action == SAVED,
        Audit.data['request_key'].as_string() == key).limit(2).execution_options(populate_existing=True)))
    matching=Audit.data['request_key_hash'].as_string() == digest(key)
    if original is not None:matching=or_(matching,Audit.object_id == original.id)
    produced=list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,Audit.action == PRODUCED,
        matching).limit(2).execution_options(populate_existing=True)))
    files=list(db.scalars(select(Evidence).where(Evidence.workspace_id == p.workspace_id,
        Evidence.data[STATE_KEY]['request_key_hash'].as_string() == digest(key))
        .limit(2).execution_options(populate_existing=True)))
    retained=original is not None and (STATE_KEY in original.data
        or type(original.data.get('output')) is dict and original.data['output'].get('schema') == RECEIPT)
    need(not packets and not saved and not produced and not files and not retained,
        '原证据包仍有库存或独立回执，但原命令缺失；保留原号等待核对，未重新生成')


def _proof(command):
    return {'schema':COMMAND,'id':command.id,'workspace_id':command.workspace_id,'actor_id':command.actor_id,
        'task_id':command.task_id,'packet_id':command.packet_id,'invocation_id':command.invocation_id,
        'request_key':command.request_key,'body_sha256':command.body_sha256,
        'response_hash':digest(command.data['response']),'packet_hash':command.data['packet_hash']}


def _event(db,p,action,identity,actor_id,value):
    rows=list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,Audit.action == action,
        Audit.object_id == identity).execution_options(populate_existing=True)))
    need(len(rows) == 1 and rows[0].actor_id == actor_id and _same(rows[0].data,value))


def _packet_proof(packet):
    # TIMESTAMPTZ preserves the instant, not the connection's textual offset.
    # Initial Row.now is UTC; use the same spelling on cold reads without
    # rewriting the immutable packet, command or its original stored hash.
    created_at = packet.created_at
    need(isinstance(created_at, datetime) and created_at.tzinfo is not None and created_at.utcoffset() is not None)
    return {'id':packet.id,'workspace_id':packet.workspace_id,'task_id':packet.task_id,'actor_id':packet.actor_id,
        'invocation_id':packet.invocation_id,'job_id':packet.job_id,'request_key':packet.request_key,
        'evidence_id':packet.evidence_id,'sha256':packet.sha256,'context_result_hash':packet.context_result_hash,
        'provenance_hash':packet.provenance_hash,'simulation_only':packet.simulation_only,
        'version':packet.version,'created_at':created_at.astimezone(timezone.utc).isoformat(),'data':copy.deepcopy(packet.data)}


def _marker(command):
    return {'command_id':command.id,'packet_id':command.packet_id,'body_sha256':command.body_sha256,
        'response_hash':digest(command.data['response'])}


def execute(db,p,cap,inputs,key,approval):
    context=validate_call_approval(db,p,cap,'data',inputs,key,approval);call=_call(db,p,key,lock=True)
    if _command(db,p,key) is not None or STATE_KEY in call.data:
        return reconcile(db,p,cap,inputs,key,approval,call)
    need(call.status == 'running' and call.capability_id == cap.id and call.data.get('role') == 'data'
        and call.data.get('side_effect') is True and call.data.get('job_id') == context['job'].id
        and call.data.get('input_hash') == digest(inputs) and call.data.get('capability_hash') == cap.content_hash
        and _same(call.data.get('approval'),approval))
    _negative_inventory(db,p,key,call)
    from . import workflow_task_packet_v199 as actual_module
    result=reader.execute(db,p,cap,inputs,key,_variant=actual_module);reader.checked_result(result)
    need(result['evidence_package'] is not None,'原任务尚无正式目标查询，未生成空证据包')
    from .task_evidence_packet_text_v199 import render_packet,PacketTextError
    try:
        snapshot,provenance=collect_snapshot(db,p,context,result)
        content=render_packet(snapshot)
    except PacketTextError:need(False,'任务证据正文超过容量或不能完整核对；原记录保留，未截断生成',422)
    need(type(content) is bytes and 0 < len(content) <= MAX_TEXT)
    from .evidence import save_evidence,evidence_bytes
    evidence=save_evidence(db,p,content,'text/plain')
    evidence,actual=evidence_bytes(db,p,evidence.id)
    need(actual == content and evidence.media_type == 'text/plain' and evidence.size_bytes == len(content)
        and evidence.data.get('cloud_model_ids') == [] and evidence.data.get('uploaded_by') == p.user_id)
    evidence.data={**evidence.data,STATE_KEY:{'request_key_hash':digest(key),'task_id':context['task'].id,
        'invocation_id':call.id}}
    packet=Packet(workspace_id=p.workspace_id,task_id=context['task'].id,actor_id=p.user_id,
        invocation_id=call.id,job_id=context['job'].id,evidence_id=evidence.id,request_key=key,
        sha256=evidence.sha256,context_result_hash=digest(result),provenance_hash=digest(provenance),
        simulation_only=snapshot['simulation_only'],data={'context_result':copy.deepcopy(result),
            'snapshot':snapshot,'provenance':provenance,'evidence_at_creation':{'id':evidence.id,
                'sha256':evidence.sha256,'media_type':evidence.media_type,'size_bytes':evidence.size_bytes,
                'uploaded_by':p.user_id,'cloud_model_ids':[]}})
    db.add(packet);db.flush()
    output=checked_result({'schema':RECEIPT,'context_result':copy.deepcopy(result),'evidence_ids':[evidence.id],
        'packet':{'id':packet.id,'evidence_id':evidence.id,'sha256':evidence.sha256,'version':1,
            'simulation_only':packet.simulation_only,'proposal_only':True,'cloud_authorized':False},**FLAGS})
    body={'schema':COMMAND,'operation':OP,'request_key':key,'task_id':packet.task_id,
        'invocation_id':call.id,'approval':copy.deepcopy(approval),'inputs':copy.deepcopy(inputs)}
    raw=canonical_json(body);_bytes(body,8192)
    command=Command(workspace_id=p.workspace_id,packet_id=packet.id,task_id=packet.task_id,actor_id=p.user_id,
        invocation_id=call.id,request_key=key,exact_body=raw,body_sha256=hashlib.sha256(raw.encode()).hexdigest(),
        command_hash='0'*64,data={'response':output,'packet_hash':digest(_packet_proof(packet))})
    db.add(command);db.flush();command.command_hash=digest(_proof(command));audit(db,p,SAVED,command.id,_proof(command))
    call.data={**call.data,STATE_KEY:_marker(command)}
    # No commit; a gateway savepoint also rolls back these rows on schema failure.
    return output


def receipt_audit_data(call,job,output):
    checked_result(output)
    need(call.status == 'succeeded' and job.kind == reader.graph_scope.KIND
        and call.data.get('job_id') == job.id and call.idempotency_key == job.data.get('invocation_key')
        and call.data.get('role') == 'data' and call.data.get('side_effect') is True
        and call.data.get('input_hash') == job.data.get('input_hash') and _same(call.data.get('output'),output))
    return {'schema':PRODUCED_SCHEMA,'invocation_id':call.id,'job_id':job.id,'task_id':job.data['task_id'],
        'capability_id':call.capability_id,'capability_hash':call.data['capability_hash'],
        'input_hash':call.data['input_hash'],'output_hash':digest(output),'request_key_hash':digest(call.idempotency_key)}


def _checked_inventory(db,p,packet):
    from .evidence import evidence_bytes
    from .task_evidence_packet_text_v199 import render_packet
    require(p,'admin','operator','viewer');scoped(db,Task,packet.task_id,p)
    need(packet.workspace_id == p.workspace_id and type(packet.version) is int and packet.version == 1)
    command=_command(db,p,packet.request_key);need(command is not None)
    call=_fresh(db,p,Invocation,packet.invocation_id);job=_fresh(db,p,Job,packet.job_id)
    output=checked_result(command.data['response']);result=output['context_result'];value=output['packet']
    need(command.packet_id == packet.id and command.task_id == packet.task_id
        and command.actor_id == packet.actor_id and command.invocation_id == call.id
        and command.request_key == call.idempotency_key == packet.request_key
        and command.version == 1 and command.body_sha256 == hashlib.sha256(command.exact_body.encode()).hexdigest()
        and command.command_hash == digest(_proof(command)) and command.data['packet_hash'] == digest(_packet_proof(packet))
        and _same(call.data.get(STATE_KEY),_marker(command)) and call.data.get('job_id') == job.id
        and job.data.get('task_id') == packet.task_id and job.data.get('actor_id') == packet.actor_id
        and value['id'] == packet.id and value['evidence_id'] == packet.evidence_id and value['sha256'] == packet.sha256
        and value['simulation_only'] is packet.simulation_only and result['evidence_package']['scope']['task_id'] == packet.task_id
        and _same(packet.data['context_result'],result) and packet.context_result_hash == digest(result)
        and packet.provenance_hash == digest(packet.data['provenance']))
    body={'schema':COMMAND,'operation':OP,'request_key':packet.request_key,'task_id':packet.task_id,
        'invocation_id':call.id,'approval':copy.deepcopy(call.data.get('approval')),'inputs':copy.deepcopy(job.data.get('inputs'))}
    need(command.exact_body == canonical_json(body) and call.data.get('input_hash') == digest(body['inputs']))
    source=result['evidence_package'];producer=source['producer'];scope=source['scope']
    need(producer['invocation_id'] == call.id and producer['job_id'] == job.id
        and producer['capability_id'] == call.capability_id and producer['capability_hash'] == call.data['capability_hash']
        and producer['actor_id'] == packet.actor_id and producer['input_hash'] == call.data['input_hash']
        and producer['request_key_hash'] == digest(packet.request_key)
        and producer['activation_id'] == job.data['activation_id']
        and producer['node_id'] == packet.data['provenance']['scope']['node_id']
        and producer['cancel_epoch'] == job.data['cancel_epoch']
        and scope['workspace_id'] == p.workspace_id and scope['graph_hash'] == job.data['graph_hash']
        and scope['release_id'] == packet.data['provenance']['release_id']
        and scope['native_goal_context_hash'] == digest(packet.data['provenance']['goal']))
    _event(db,p,SAVED,command.id,packet.actor_id,_proof(command))
    _event(db,p,PRODUCED,call.id,packet.actor_id,receipt_audit_data(call,job,output))
    evidence=_fresh(db,p,Evidence,packet.evidence_id);evidence,content=evidence_bytes(db,p,evidence.id)
    frozen=packet.data['evidence_at_creation']
    need(_same(frozen,{'id':evidence.id,'sha256':evidence.sha256,'media_type':'text/plain',
        'size_bytes':evidence.size_bytes,'uploaded_by':packet.actor_id,'cloud_model_ids':[]})
        and evidence.sha256 == packet.sha256 and evidence.media_type == 'text/plain'
        and evidence.size_bytes == len(content) and 0 < len(content) <= MAX_TEXT
        and evidence.data.get('uploaded_by') == packet.actor_id and content == render_packet(packet.data['snapshot'])
        and _same(evidence.data.get(STATE_KEY),{'request_key_hash':digest(packet.request_key),
            'task_id':packet.task_id,'invocation_id':call.id})
        and type(evidence.version) is int and evidence.version >= 1
        and type(evidence.data.get('cloud_model_ids')) is list
        and len(evidence.data['cloud_model_ids']) == len(set(evidence.data['cloud_model_ids'])))
    for identifier in evidence.data['cloud_model_ids']:_id(identifier)
    _event(db,p,'evidence.upload',evidence.id,packet.actor_id,
        {'media_type':'text/plain','sha256':packet.sha256,'cloud_model_id':None,'consent':False})
    return command,call,job,evidence,content,output


def _checked(db,p,packet):
    try:return _checked_inventory(db,p,packet)
    except HTTPException:raise
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError,RecursionError,UnicodeError,OSError):
        need(False)


def reconcile(db,p,cap,inputs,key,approval,prior):
    require(p,'admin','operator');reader._inputs(inputs)
    need(prior.workspace_id == p.workspace_id and prior.idempotency_key == key and prior.capability_id == cap.id
        and prior.data.get('role') == 'data' and prior.data.get('side_effect') is True
        and prior.data.get('capability_hash') == cap.content_hash and prior.data.get('input_hash') == digest(inputs)
        and type(approval) is dict and approval.get('schema') == APPROVAL and _same(prior.data.get('approval'),approval))
    command=_command(db,p,key)
    need(command is not None,'原证据包命令尚无已保存库存；保留原号，不默默重新生成')
    need(command.invocation_id == prior.id and command.actor_id == p.user_id)
    packet=_fresh(db,p,Packet,command.packet_id);_,call,_,_,_,output=_checked(db,p,packet)
    need(call.id == prior.id);return copy.deepcopy(output)


def read_original_context(db,p,call):
    """Historical successful full receipt, no native completion/dispatch side effect."""
    command=_command(db,p,call.idempotency_key)
    need(command is not None and command.invocation_id == call.id)
    packet=_fresh(db,p,Packet,command.packet_id);_,actual,_,_,_,output=_checked(db,p,packet)
    need(actual.id == call.id and _same(actual.data['output'],output))
    return copy.deepcopy(output['context_result'])


def model_options(db,p,task,evidence):
    """Current model choices never authorize this Task; history stays readable."""
    try:
        from .workflow_graph import business_binding
        from .business_reasoning_protocols_v276 import for_adapter
        from .workflow_evidence_consent_wait_v199 import _grants
        release=_fresh(db,p,InstanceRelease,task.data['instance_release_id'])
        need(release.instance_id == task.data['instance_id'] and release.package_id == task.package_id)
        businesses=[item for item in release.data['snapshot']['businesses'] if item['id'] == task.data['business_id']]
        need(len(businesses) == 1);binding=business_binding(release,businesses[0]);graph=binding['graph']
        need(binding['schema'] in {'business-flow-nested-execution-binding-v195', 'authored-decision-workflow-binding-v278', 'authored-decision-workflow-binding-v286'}
            and task.data['graph_runtime']['graph_hash'] == digest(graph))
        if binding['schema'] in {'authored-decision-workflow-binding-v278', 'authored-decision-workflow-binding-v286'}:reader._checked_source_binding(binding)
        result={}
        for node in graph['nodes']:
            if node.get('kind') != 'ability':continue
            protocol=for_adapter(node.get('execution',{}).get('adapter'))
            if protocol is None:continue
            pin=node['execution'];cap=_fresh(db,p,Capability,pin['capability_id']);published(db,p,cap.id,'tool')
            need(pin.get('agent_role') == 'business' and cap.content_hash == pin['capability_hash'] and cap.release_version == pin['release_version']
                and _same(cap.data['input_schema'],pin['input_schema']) and _same(cap.data['output_schema'],pin['output_schema']))
            model=protocol.validate_manifest(db,p,cap.data);_fresh(db,p,Capability,model.id);model=published(db,p,model.id,'foundation_model')
            need(model.content_hash == cap.data['execution']['model_profile_hash'])
            result[model.id]={'id':model.id,'name':model.name,'provider':model.data['execution']['provider'],
                'model':model.data['execution']['model'],'content_hash':model.content_hash,
                'authorized':model.id in evidence.data.get('cloud_model_ids',[]) and bool(_grants(db,evidence,model))}
            need(len(result) <= 16)
        return list(result.values()),True
    except (HTTPException,ValueError,TypeError,KeyError,AttributeError,RecursionError):return [],False


def _view(db,p,packet,*,text=True):
    _,_,_,ev,content,output=_checked(db,p,packet);snapshot=packet.data['snapshot'];task=_fresh(db,p,Task,packet.task_id)
    models,available=model_options(db,p,task,ev)
    value={'schema':VIEW,'id':packet.id,'task_id':packet.task_id,'actor_id':packet.actor_id,
        'created_at':packet.created_at.isoformat(),'version':1,'simulation_only':packet.simulation_only,
        'proposal_only':True,'cloud_authorized_at_creation':False,'task_title':snapshot['task_title'],
        'context_status':output['context_result']['status'],'evidence_status':snapshot['evidence_status'],
        'source_summary':{'object_count':len(snapshot['objects']),
            'observation_count':sum(len(row['observations']) for row in snapshot['objects']),
            'gap_count':len(snapshot['gaps']),'issue_count':len(snapshot['issues'])},
        'evidence':{'id':ev.id,'sha256':ev.sha256,'media_type':ev.media_type,'size_bytes':ev.size_bytes,
            'version':ev.version,'cloud_model_ids':copy.deepcopy(ev.data.get('cloud_model_ids',[]))},
        'model_options':models,'model_options_available':available}
    if text:value['text']=content.decode('utf-8')
    return value


def get(db,p,packet_id):return _view(db,p,_fresh(db,p,Packet,_id(packet_id)))


def list_packets(db,p,task_id):
    require(p,'admin','operator','viewer');task=_fresh(db,p,Task,_id(task_id))
    rows=list(db.scalars(select(Packet).where(Packet.workspace_id == p.workspace_id,Packet.task_id == task.id)
        .order_by(Packet.created_at.desc()).limit(101).execution_options(populate_existing=True)))
    need(len(rows) <= 100,'本任务证据包超过单次查询容量，原记录保留',422)
    return {'schema':'workflow-task-evidence-packet-list-v199','task_id':task.id,
        'items':[_view(db,p,row,text=False) for row in rows]}


def recover(db,p,key,expected_sha256):
    require(p,'admin','operator','viewer');need(type(key) is str and 1 <= len(key) <= 200
        and type(expected_sha256) is str and SHA.fullmatch(expected_sha256),status=422)
    command=_command(db,p,key)
    if command is None:
        _negative_inventory(db,p,key)
        return {'schema':ORIGINAL,'request_key':key,'found':False,'body_sha256':None,'result':None}
    need(command.actor_id == p.user_id,'请由原提交账号读取证据包原保存回执',403)
    _,_,_,_,_,output=_checked(db,p,_fresh(db,p,Packet,command.packet_id))
    need(command.body_sha256 == expected_sha256,'原编号不能替换请求内容')
    return {'schema':ORIGINAL,'request_key':key,'found':True,'body_sha256':command.body_sha256,'result':copy.deepcopy(output)}
