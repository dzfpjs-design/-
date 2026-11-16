"""Persisted pixel questions: one tracked planning call, one frozen readonly source."""
import base64
import copy
import hashlib
import json
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select,text,or_,and_
from sqlalchemy.orm import Session
from . import models as m
from .contracts import digest
from .services import scoped,published,require,validate
from .leases import ownership,checkpoint
from . import pixel_question_contracts_v252 as c
from . import pixel_question_catalog_v252 as catalog

KEY=c.FAMILY
CREATED='pixel_question.created_v252'
QUEUED='pixel_question.queued_v252'
RESERVED='pixel_question.reserved_v252'
CAPTURED='pixel_question.response_captured_v252'
PLANNED='pixel_question.planned_v252'
SOURCED='pixel_question.source_frozen_v252'
OUTCOME='pixel_question.answered_v252'
COMMAND='pixel_question.command_v252'
ACTIONS=[CREATED,QUEUED,RESERVED,CAPTURED,PLANNED,SOURCED,OUTCOME,COMMAND]
ERROR='无法核对原问题、模型回答或数据依据；原记录保留，请检查相关资料'


def need(ok,message=ERROR,code=409):
    if not ok:raise HTTPException(code,message)

def same(a,b):return digest(a)==digest(b)

def _wire(raw,contract):
    from .cloud_business_reasoning_v197 import strict_json
    try:return contract.model_validate(strict_json(raw.decode('utf-8'))).model_dump(mode='json')
    except (ValueError,TypeError,UnicodeError):raise HTTPException(422,'原请求正文不是明确且完整的合法JSON') from None

def _audit(db,p,action,object_id,data):
    proof=m.Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=action,object_id=object_id,data=copy.deepcopy(data))
    db.add(proof);db.flush();return proof

def _proof(db,p,action,row_id,expected=None,identity=None):
    values=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==action,m.Audit.object_id==row_id)))
    need(len(values)==1)
    proof=values[0];need(identity is None or proof.id==identity)
    need(proof.actor_id==p.user_id)
    if expected is not None:need(same(proof.data,expected))
    return proof

def _ref(db,p,model,identity):
    row=db.scalar(select(model).where(model.workspace_id==p.workspace_id,model.id==identity))
    need(row is not None)
    return row

def _set(row,state):row.data={**row.data,KEY:copy.deepcopy(state)};row.version+=1

def _nonce(db,p,namespace,value):
    key=int(hashlib.sha256((p.workspace_id+':'+namespace+':'+value).encode()).hexdigest()[:15],16)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':key})

def has_trace(db,p,identity):
    row=db.scalar(select(m.DataRun).where(m.DataRun.workspace_id==p.workspace_id,m.DataRun.id==identity))
    if row is not None and (KEY in row.data or row.data.get('family')==KEY
            or (type(row.data.get('request')) is dict and row.data['request'].get('schema')=='pixel-question-submit-v252')):return True
    if db.scalar(select(m.Audit.id).where(m.Audit.workspace_id==p.workspace_id,m.Audit.object_id==identity,m.Audit.action.in_(ACTIONS)).limit(1)):return True
    if db.scalar(select(m.Job.id).where(m.Job.workspace_id==p.workspace_id,m.Job.kind=='data_query',
        m.Job.data['query_id'].as_string()==identity,m.Job.idempotency_key.startswith('pixel-question-v252:')).limit(1)):return True
    if db.scalar(select(m.Invocation.id).where(m.Invocation.workspace_id==p.workspace_id,
        m.Invocation.idempotency_key.startswith('pixel-question-tool-v252:'+str(identity)+':')).limit(1)):return True
    return db.scalar(select(m.CloudCall.id).where(m.CloudCall.workspace_id==p.workspace_id,
        m.CloudCall.data['gateway_dispatch']['identity']['suggestion_id'].as_string()==identity,
        m.CloudCall.data['gateway_dispatch']['identity']['task_id'].as_string()=='pixel-question-v252').limit(1)) is not None

def _context(row,state):
    return {'schema':'pixel-question-context-v252','query_id':row.id,'workspace_id':row.workspace_id,
        'instance_id':state['request_scope']['instance_id'],'actor_id':row.owner_id,
        'authorization_release_id':state['request']['release_id'],
        'authorization_configuration_hash':state['release_pin']['configuration_hash'],
        'package_id':state['release_pin']['package_id'],'package_hash':state['release_pin']['package_hash'],
        'tool_id':state['tool_pin']['id'],'tool_hash':state['tool_pin']['hash'],
        'model_profile_id':state['request']['model_profile_id'],'model_profile_hash':digest(state['model_snapshot']),
        'request_hash':state['request_hash'],'preview_hash':state['request']['preview_hash']}

def _creation_expected(row,state):
    return {'schema':'pixel-question-created-v252','query_id':row.id,'workspace_id':row.workspace_id,'actor_id':row.owner_id,
        'instance_id':state['context']['instance_id'],'request_id':state['request']['request_id'],
        'request_hash':state['request_hash'],'raw_sha256':state['raw_sha256'],'context_hash':state['context_hash'],
        'initial_job_id':state['execution_job_ids'][0],'preview_hash':state['preview']['preview_hash']}

def checked(db,p,row,*,summary_only=False):
    need(row.owner_id==p.user_id,code=404)
    try:
        s=copy.deepcopy(row.data[KEY]);need(row.data.get('family')==KEY)
        body=c.Submit.model_validate(s['request']).model_dump(mode='json');need(same(body,s['request']))
        raw=base64.b64decode(s['raw_b64'],validate=True);need(hashlib.sha256(raw).hexdigest()==s['raw_sha256'])
        need(same(_wire(raw,c.Submit),body) and digest(body)==s['request_hash'] and row.question==body['question'])
        need(same(_context(row,s),s['context']) and digest(s['context'])==s['context_hash'])
        need(same(s['request_scope'],{'instance_id':s['context']['instance_id'],'task_id':body['task_id'],'entity_id':body['entity_id']}))
        need(s['preview']['preview_hash']==body['preview_hash'] and digest({k:v for k,v in s['preview'].items() if k!='preview_hash'})==body['preview_hash'])
        need(same(s['preview']['input'],c.preview_input(body)) and s['preview']['actor_id']==row.owner_id and s['preview']['workspace_id']==row.workspace_id)
        need(same(s['preview']['result_schema'],c.model_result_schema()))
        release=_ref(db,p,m.InstanceRelease,body['release_id']);instance=_ref(db,p,m.DomainInstance,s['context']['instance_id'])
        package=_ref(db,p,m.Capability,s['release_pin']['package_id'])
        need(release.instance_id==instance.id and release.package_id==package.id and package.namespace==instance.namespace
            and digest(release.data['snapshot'])==release.configuration_hash==s['release_pin']['configuration_hash']
            and digest(package.data)==package.content_hash==s['release_pin']['package_hash'])
        need(catalog.verify(release.data['snapshot']) is not None)
        fixed=package.data['specification'].get(catalog.SLOT)
        need(type(fixed) is dict and same(fixed['tools'],[s['tool_pin']]) and fixed['facet_hash']==release.data['snapshot']['pixel_question_facet_v252']['facet_hash'])
        tool=_ref(db,p,m.Capability,s['tool_pin']['id']);catalog.validate_manifest(tool.data)
        need(tool.content_hash==digest(tool.data)==s['tool_pin']['hash'] and tool.release_version==s['tool_pin']['version']
            and tool.id in package.data['dependencies'] and tool.id in package.data['specification']['data_tool_ids'])
        need(digest(s['model_snapshot'])==s['context']['model_profile_hash'])
        proof=_proof(db,p,CREATED,row.id,_creation_expected(row,s),s['creation_audit_id'])
        need(s['creation_hash']==digest(proof.data))
        jobs=list(db.scalars(select(m.Job).where(m.Job.workspace_id==p.workspace_id,m.Job.kind=='data_query',
            m.Job.idempotency_key.startswith('pixel-question-v252:'+row.id+':'))))
        need(len(jobs)==len(s['execution_job_ids']) and {j.id for j in jobs}==set(s['execution_job_ids']))
        queued=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==QUEUED,m.Audit.object_id==row.id)))
        need(len(queued)==len(jobs) and {a.data.get('job_id') for a in queued}==set(s['execution_job_ids']))
        for j in jobs:
            q=[a for a in queued if a.data.get('job_id')==j.id]
            need(len(q)==1 and q[0].actor_id==p.user_id and same(q[0].data,{'query_id':row.id,'job_id':j.id,'context_hash':s['context_hash'],'recovery_only':j.data.get('pixel_question_cached_only')}))
            need(type(j.data.get('pixel_question_cached_only')) is bool)
            need(j.data.get('query_id')==row.id and j.data.get('actor_id')==p.user_id and j.data.get('context_hash')==s['context_hash']
                and j.data.get('family')==KEY)
        command_proofs=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==COMMAND,m.Audit.object_id==row.id)))
        need({a.id for a in command_proofs}=={v['audit_id'] for v in s['commands']} and len(command_proofs)==len(s['commands']))
        for command_proof in command_proofs:_check_command(db,p,row,s,command_proof)
        if summary_only:return s
        if s['planning']['call_id'] is not None:_checked_cloud(db,p,row,s)
        if s['plan'] is not None:
            validate(c.ir_schema(),s['plan']);need(digest(s['plan'])==s['plan_hash'])
            need(same(s['plan']['scope'],{'instance_id':instance.id,'authorization_release_id':release.id,
                'task_id':body['task_id'],'entity_id':body['entity_id']}))
            _proof(db,p,PLANNED,row.id,plan_audit_expected(row,s),s['plan_audit_id'])
        else:need(s['plan_hash'] is None and s['plan_audit_id'] is None and s['source_snapshot'] is None)
        if s['source_snapshot'] is not None:
            _proof(db,p,SOURCED,row.id,source_audit_expected(row,s),s['source_audit_id'])
        else:
            need(s['source_audit_id'] is None and db.scalar(select(m.Audit.id).where(m.Audit.workspace_id==p.workspace_id,
                m.Audit.object_id==row.id,m.Audit.action==SOURCED)) is None)
        if s['answer'] is not None:
            need(row.status=='succeeded')
            validate(c.answer_schema(),s['answer']);need(s['answer']['query_id']==row.id
                and s['answer']['request_hash']==s['request_hash'] and s['answer']['plan_hash']==s['plan_hash']
                and digest({k:v for k,v in s['answer'].items() if k!='answer_hash'})==s['answer']['answer_hash'])
            call=_ref(db,p,m.Invocation,s['tool']['invocation_id']);need(call.status=='succeeded'
                and same(call.data.get('output'),s['answer']) and s['tool']['output_hash']==digest(s['answer']))
            _proof(db,p,OUTCOME,row.id,{'schema':'pixel-question-answered-v252','query_id':row.id,
                'request_hash':s['request_hash'],'plan_hash':s['plan_hash'],'source_audit_id':s['source_audit_id'],
                'source_snapshot_hash':s['source_snapshot']['snapshot_hash'],'invocation_id':call.id,
                'output_hash':digest(s['answer']),'answer_hash':s['answer']['answer_hash']},s['outcome_audit_id'])
        elif row.status=='succeeded':need(False)
        return s
    except HTTPException as error:
        if error.status_code==422:need(False)
        raise
    except (KeyError,ValueError,TypeError,AttributeError,UnicodeError,IndexError,OverflowError):need(False)

def plan_audit_expected(row,s):
    return {'schema':'pixel-question-plan-v252','query_id':row.id,'creation_hash':s['creation_hash'],
        'context_hash':s['context_hash'],'request_hash':s['request_hash'],'plan_hash':s['plan_hash'],
        'model_call_id':s['planning']['call_id'],'response_sha256':s['planning']['capture']['raw_sha256'],
        'receipt_hash':digest(s['planning']['capture']['receipt']),'explanation_hash':digest(s['plan_explanation'])}

def source_audit_expected(row,s):
    snapshot=s['source_snapshot'];context=s['context']
    return {'schema':'pixel-question-source-frozen-v252','query_id':row.id,'workspace_id':row.workspace_id,
        'instance_id':context['instance_id'],'actor_id':row.owner_id,
        'authorization_release_id':context['authorization_release_id'],
        'authorization_configuration_hash':context['authorization_configuration_hash'],
        'tool_id':context['tool_id'],'tool_hash':context['tool_hash'],'plan_hash':s['plan_hash'],
        'creation_hash':s['creation_hash'],'context_hash':s['context_hash'],'execution_job_id':s['source_job_id'],
        'source_snapshot_hash':snapshot['snapshot_hash'],'source_revision_hash':snapshot['revision_hash'],
        'inventory_hash':snapshot['inventory']['inventory_hash'],'captured_at':snapshot['captured_at'],
        'complete':True,'run_count':len(snapshot['records'])}


def _scope(db,p,instance,task_id,entity_id):
    if task_id is not None:
        task=scoped(db,m.Task,task_id,p);need(task.data.get('instance_id')==instance.id,'任务不在所选业务实例中',404)
        from .workspace_api import saved_execution
        saved_execution(db,p,task.id)
        if entity_id is not None:need(entity_id in task.data.get('entity_ids',[]),'对象不在原任务范围中',404)
    if entity_id is not None:
        entity=scoped(db,m.Entity,entity_id,p);need(entity.namespace==instance.namespace,'对象不在所选业务实例中',404)

def _model(db,p,identity):
    model=published(db,p,identity,'foundation_model');e=model.data['execution']
    need(e.get('provider') in {'qwen','deepseek'} and e.get('task')!='embedding' and model.data['data_policy'].get('cloud_allowed') is True,
        '请选择已发布且允许本次规划的Qwen或DeepSeek模型')
    need('report_evidence_output' not in e and 'native_schema_variant' not in e,
        '该模型配置限定用于其他资料读取协议，请选择通用问题规划配置')
    maximum=e.get('max_tokens',2048);need(type(maximum) is int and 128<=maximum<=8192,'模型输出预算须为128至8192 token')
    return model

def options(db,p,instance_id):
    require(p,'admin','operator');instance=scoped(db,m.DomainInstance,instance_id,p)
    release=None;tool=None;facet=None;reasons=[]
    if instance.active_release_id:
        release=scoped(db,m.InstanceRelease,instance.active_release_id,p)
        need(release.instance_id==instance.id)
        tool=catalog.catalog(db,p,release)
        facet=release.data.get('snapshot',{}).get('pixel_question_facet_v252')
    if instance.status!='partially_active':reasons.append('业务实例尚未启用或已暂停，可以查看历史问题')
    if tool is None:reasons.append('当前业务版本尚未配备影像成果问答，请生成并启用包含该能力的新版本')
    from .cloud_provider_policy import provider_enabled
    models=[]
    for row in db.scalars(select(m.Capability).where(m.Capability.workspace_id==p.workspace_id,m.Capability.kind=='foundation_model',m.Capability.status=='published').order_by(m.Capability.id)):
        e=row.data.get('execution',{})
        if not provider_enabled(e.get('provider')):continue
        if e.get('provider') not in {'qwen','deepseek'} or e.get('task')=='embedding' or row.data.get('data_policy',{}).get('cloud_allowed') is not True or 'report_evidence_output' in e or 'native_schema_variant' in e:continue
        current=_model(db,p,row.id)
        models.append({'id':row.id,'hash':row.content_hash,'version':row.version,'provider':e['provider'],'model':e['model'],'max_tokens':e.get('max_tokens',2048)})
    if not models:reasons.append('尚无可用于问题规划的已发布云模型配置')
    result={'schema':'pixel-question-options-v252','workspace_id':p.workspace_id,'instance_id':instance.id,'actor_id':p.user_id,
        'release_id':release.id if release else None,'configuration_hash':release.configuration_hash if release else None,
        'facet_hash':facet['facet_hash'] if facet else None,'can_ask':not reasons,'reasons':reasons,
        'catalog':{'source_facet':c.FACET,'tool':None if tool is None else {'id':tool.id,'hash':tool.content_hash,'version':tool.release_version,'method':'pixel_machine_query'},
            'metrics':[{'key':key,'label':label,'kind':'integer','unit':'model_result_pixels'} for key,label in zip(c.METRICS,['候选变化像素','共同有效像素','未知像素'])],
            'operations':c.OPERATIONS,'group_by':c.GROUPS,'reception':c.RECEPTIONS,'human_completion':c.HUMANS,
            'time_basis':'machine_result_registered','timezone':'Asia/Shanghai'},'models':models,
        'limits':{'max_source_records':1000,'max_question_characters':4000,'max_registration_days':31,'max_output_groups':1000}}
    return {**result,'options_hash':digest(result)}

def _planning_examples():
    # Synthetic language examples contain no saved source, note, metric value,
    # platform identity or authority. They illustrate the same closed union.
    def candidate(question,operation,metric,quote,metric_quote,reception,filter_quotes):
        return {'question':question,'response':{'status':'candidate','ir':{
            'operation':operation,'metric':metric,'group_by':'none',
            'time':{'basis':'machine_result_registered','registration_days':None,'timezone':'Asia/Shanghai'},
            'filters':{'reception':reception,'human_completion':'any'},
            'grounding':{'operation_quote':quote,'metric_quote':metric_quote,'time_quote':None,'filter_quotes':filter_quotes},
            'limitations':[]},'explanation':'仅按原问题的范围和明确指标查询；没有登记日要求，所以不按日期筛选。'}}
    return [candidate('统计本任务等待数据管理智能体接收的影像分析成果数量。','count',None,'统计',None,
                'waiting_data',['等待数据管理智能体接收']),
        candidate('汇总本任务已接收成果的候选像素总量。','sum','candidate_pixels','汇总','候选像素',
                'received',['已接收']),
        candidate('统计有几份候选变化像素分析成果。','count',None,'统计',None,'any',[]),
        {'question':'本任务今天拍摄的影像实际变化了多少亩？','response':{
            'status':'needs_clarification','questions':['本来源不提供拍摄日、地理面积或现场真实变化，请改问登记日或模型候选像素。'],
            'limitations':['未知拍摄时间，候选成果不是现场事实。']}}]


def _messages(value,catalogue,reference_day):
    # Cloud planning needs semantics, never platform identities or hashes.
    # The complete original UUID scope remains local and is injected AFTER
    # server validation; this projection is idempotent for cold reprepare.
    cloud_catalog={key:copy.deepcopy(catalogue[key]) for key in ['source_facet','metrics','operations',
        'group_by','reception','human_completion','time_basis','timezone']}
    rules={'task':'将原问题转换为影像候选成果只读IntentIR；若含义或日期不明确，返回needs_clarification，不猜测、不生成答案。',
        'catalog':cloud_catalog,'reference_registration_day':reference_day,
        'rules':['来源仅模型候选成果，绝不是对象真实变化、地理面积或现场结论。',
            '登记日是系统登记时间，未知拍摄时间不能代填。',
            '没有明确登记日期或明确相对登记日时，registration_days必须为null且time_quote必须为null。',
            '本任务、当前实例、本对象或所选对象都只是已选范围，绝不是日期，不能推断为今天或参考登记日。',
            '只有明确的YYYY-MM-DD登记日，或今天登记/昨天登记等明确相对登记日，才能设置日期；参考日期只是解释锚，不是默认筛选。',
            '像素计算只使用已接收成果，read/count的metric为null。',
            '像素计算的原问题本身也必须明确只使用已接收成果。原问要求等待接收、未接收或没有说明接收范围时，返回needs_clarification，绝不能擅自改成received。',
            '一个IntentIR只回答一种量；问题同时要求成果份数和像素总量时返回needs_clarification，请用户分开提问，不能忽略其中一个。',
            'grounding全部必须逐字摘自原问题。明确读取/统计直接candidate，不要求人工确认。',
            '只返回七业务键IntentIR，不能输出任何UUID、工具调用、SQL、脚本或数据记录。'],
        'examples':_planning_examples()}
    return [{'role':'system','content':json.dumps(rules,ensure_ascii=False)},
        {'role':'user','content':json.dumps({'question':value['question'],'selected_scope':{
            'instance':'当前实例的影像候选成果','task_limited':value['task_id'] is not None,
            'entity_limited':value['entity_id'] is not None}},ensure_ascii=False)}]

def preview(db,p,instance_id,body):
    value=c.PreviewInput.model_validate(body.model_dump(mode='json') if hasattr(body,'model_dump') else body).model_dump(mode='json')
    opts=options(db,p,instance_id);need(opts['can_ask'],'；'.join(opts['reasons']))
    need(opts['options_hash']==value['options_hash'] and opts['release_id']==value['release_id'],'业务版本或模型目录已变化，请刷新后重新提问')
    instance=scoped(db,m.DomainInstance,instance_id,p);_scope(db,p,instance,value['task_id'],value['entity_id'])
    model=_model(db,p,value['model_profile_id']);need(any(x['id']==model.id for x in opts['models']))
    day=m.now().astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    messages=_messages({**value,'instance_id':instance_id},opts['catalog'],day)
    from .cloud_business_reasoning_v197 import prepare
    prepared=prepare(model,messages,c.model_result_schema())
    result={'schema':'pixel-question-preview-v252','workspace_id':p.workspace_id,'instance_id':instance_id,'actor_id':p.user_id,
        'input':value,'model':{'id':model.id,'hash':model.content_hash,'provider':model.data['execution']['provider'],'model':model.data['execution']['model']},
        'messages':prepared['messages'],'result_schema':c.model_result_schema(),'dispatch_options':prepared['options'],
        'input_bytes':prepared['text_bytes'],'token_bound':prepared['token_bound'],
        'notice':'点击提问并查询会按已发布模型策略发送这一次问题规划；不发送影像、指标值或人工说明。'}
    return {**result,'preview_hash':digest(result)}

def _find_request(db,p,instance_id,request_id):
    scoped(db,m.DomainInstance,instance_id,p)
    proofs=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==CREATED,
        m.Audit.data['request_id'].as_string()==request_id).limit(2)))
    rows=list(db.scalars(select(m.DataRun).where(m.DataRun.workspace_id==p.workspace_id,
        m.DataRun.data[KEY]['request']['request_id'].as_string()==request_id).limit(2)))
    need(len(proofs)<=1 and len(rows)<=1,'同一个申请编号对应多条记录，不能确定原请求')
    if not proofs and not rows:return None
    row=rows[0] if rows else _ref(db,p,m.DataRun,proofs[0].object_id)
    row=_load(db,p,row.id)
    state=checked(db,p,row)
    need(state['context']['instance_id']==instance_id,code=404)
    need(len(proofs)==1 and len(rows)==1 and proofs[0].object_id==row.id)
    return row

def create(db,p,instance_id,body,raw):
    require(p,'admin','operator');_nonce(db,p,'pixel-question-request',body.request_id)
    prior=_find_request(db,p,instance_id,body.request_id)
    if prior is not None:
        state=checked(db,p,prior);need(state['request_hash']==digest(body.model_dump(mode='json')),'原申请编号不能更换问题或范围')
        return view(db,p,prior)
    from .relations import lock_current_actor
    lock_current_actor(db,p)
    instance=scoped(db,m.DomainInstance,instance_id,p,True)
    shown=preview(db,p,instance_id,c.preview_input(body));need(shown['preview_hash']==body.preview_hash,'发送内容或业务条件已变化，请重新核对本次问题')
    value=body.model_dump(mode='json');need(same(_wire(raw,c.Submit),value),'原请求正文与已校验输入不一致',422)
    release=scoped(db,m.InstanceRelease,body.release_id,p);package=published(db,p,release.package_id,'domain_package');tool=catalog.catalog(db,p,release)
    model=_model(db,p,body.model_profile_id)
    row=m.DataRun(id=m.uid(),workspace_id=p.workspace_id,owner_id=p.user_id,question=body.question,status='queued',data={})
    jid=m.uid();state={'schema':c.SCHEMA,'request':value,'request_hash':digest(value),'raw_b64':base64.b64encode(raw).decode('ascii'),
        'raw_sha256':hashlib.sha256(raw).hexdigest(),'request_scope':{'instance_id':instance.id,'task_id':body.task_id,'entity_id':body.entity_id},
        'release_pin':{'configuration_hash':release.configuration_hash,'package_id':package.id,'package_hash':package.content_hash},
        'tool_pin':{'id':tool.id,'hash':tool.content_hash,'version':tool.release_version,'method':'pixel_machine_query'},
        'model_snapshot':copy.deepcopy(model.data),'preview':shown,'creation_audit_id':None,'creation_hash':None,
        'execution_job_ids':[jid],'plan':None,'plan_hash':None,'plan_explanation':None,'plan_audit_id':None,
        'source_snapshot':None,'source_audit_id':None,'source_job_id':None,'tool':None,'answer':None,'outcome_audit_id':None,
        'clarifications':[],'limitations':[],'error':None,'commands':[],
        'planning':{'state':'not_started','call_id':None,'capture':None,'job_id':None}}
    state['context']=_context(row,state);state['context_hash']=digest(state['context'])
    proof=_audit(db,p,CREATED,row.id,_creation_expected(row,state));state['creation_audit_id']=proof.id;state['creation_hash']=digest(proof.data)
    row.data={'family':KEY,KEY:state};db.add(row)
    db.add(m.Job(id=jid,workspace_id=p.workspace_id,kind='data_query',idempotency_key='pixel-question-v252:'+row.id+':initial',
        data={'query_id':row.id,'actor_id':p.user_id,'family':KEY,'context_hash':state['context_hash'],'recovery_only':False,'pixel_question_cached_only':False}))
    db.flush();_audit(db,p,QUEUED,row.id,{'query_id':row.id,'job_id':jid,'context_hash':state['context_hash'],'recovery_only':False})
    db.commit();return view(db,p,row)


def _cloud_expected(row,s):
    v=s['preview'];return {'workspace_id':row.workspace_id,'capability_id':s['context']['model_profile_id'],
        'actor_id':row.owner_id,'request_hash':digest(v['messages']),'dispatch_options_hash':digest(v['dispatch_options']),
        'provider':v['model']['provider'],'model':v['model']['model']}

def _reserved_expected(row,s):
    return {'schema':'pixel-question-reserved-v252','query_id':row.id,'request_hash':s['request_hash'],
        'context_hash':s['context_hash'],'call_id':s['planning']['call_id'],'job_id':s['planning']['job_id'],
        'messages_hash':digest(s['preview']['messages']),'dispatch_options_hash':digest(s['preview']['dispatch_options'])}

def _checked_cloud(db,p,row,s):
    plan=s['planning'];call=_ref(db,p,m.CloudCall,plan['call_id']);expected=_cloud_expected(row,s)
    need(call.capability_id==expected['capability_id'] and call.reserved_tokens==s['preview']['token_bound']
        and all(call.data.get(k)==expected[k] for k in ['actor_id','request_hash','dispatch_options_hash']))
    identity={'suggestion_id':row.id,'job_id':plan['job_id'],'task_id':'pixel-question-v252','request_hash':s['request_hash'],
        'workspace_id':p.workspace_id,'actor_id':p.user_id,'capability_id':call.capability_id}
    need(same(call.data.get('gateway_dispatch',{}).get('identity'),identity) and plan['job_id'] in s['execution_job_ids'])
    _proof(db,p,RESERVED,row.id,_reserved_expected(row,s))
    if plan['capture'] is not None:
        capture=plan['capture'];raw=base64.b64decode(capture['raw_b64'],validate=True)
        need(hashlib.sha256(raw).hexdigest()==capture['raw_sha256'] and capture['receipt'].get('call_id')==call.id)
        _proof(db,p,CAPTURED,row.id,{'schema':'pixel-question-response-captured-v252','query_id':row.id,
            'call_id':call.id,'context_hash':s['context_hash'],'raw_sha256':capture['raw_sha256'],
            'receipt_hash':digest(capture['receipt'])})
        if s['plan'] is not None or s['planning']['state']=='parsed':
            from .cloud_business_reasoning_v197 import parse_response
            _value,normalized=parse_response(raw.decode('utf-8',errors='surrogateescape'),c.model_result_schema(),capture['receipt'])
            need(call.status in {'succeeded','failed'})
            ledger_receipt=call.data.get('receipt');need(type(ledger_receipt) is dict)
            critical=['provider','model','call_id','request_id','usage','finish_reason','response_choice_count']
            need(same({k:normalized.get(k) for k in critical},{k:ledger_receipt.get(k) for k in critical}))
            total=normalized['usage'].get('total_tokens')
            charged=total if type(total) is int and total>=0 else call.reserved_tokens
            need(call.charged_tokens==charged)
    else:need(db.scalar(select(m.Audit.id).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==CAPTURED,m.Audit.object_id==row.id)) is None)
    return call

def _live(db,p,row,job):
    from .relations import lock_current_actor
    lock_current_actor(db,p)
    s=checked(db,p,row);instance=scoped(db,m.DomainInstance,s['context']['instance_id'],p)
    instance=db.scalar(select(m.DomainInstance).where(m.DomainInstance.id==instance.id,m.DomainInstance.workspace_id==p.workspace_id).with_for_update(read=True).execution_options(populate_existing=True))
    # Every write phase linearizes against cancel under the same real Row EX.
    # No lock spans provider IO: planning commits before dispatch/capture.
    row=_load(db,p,row.id,True);s=checked(db,p,row)
    need(instance.status=='partially_active' and instance.active_release_id==s['request']['release_id'],
        '当前业务版本尚未启用或已暂停；原问题和已保存回答仍可查看')
    tool=catalog.catalog(db,p,_ref(db,p,m.InstanceRelease,s['request']['release_id']));need(tool is not None and tool.id==s['tool_pin']['id'])
    _scope(db,p,instance,s['request']['task_id'],s['request']['entity_id'])
    actual=ownership(db);need(actual.get('job_id')==job.id and job.id in s['execution_job_ids']
        and actual.get('lease_token')==job.lease_token and job.kind=='data_query' and job.status=='running'
        and job.workspace_id==p.workspace_id and job.data.get('actor_id')==p.user_id and row.status!='cancelled',
        '本次查询没有有效后台执行权限；原问题保留')
    return s

def _load(db,p,identity,lock=False):
    row=scoped(db,m.DataRun,identity,p,lock)
    if row.owner_id!=p.user_id:
        own=db.scalar(select(m.Audit.id).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==CREATED,
            m.Audit.object_id==row.id,m.Audit.actor_id==p.user_id).limit(1))
        need(own is None);need(False,code=404)
    return row

def _parse_capture(db,p,row,s):
    from .cloud_business_reasoning_v197 import parse_response
    capture=s['planning']['capture'];need(capture is not None,'原规划尚无可恢复的首答，未自动重新发送')
    raw=base64.b64decode(capture['raw_b64'],validate=True).decode('utf-8',errors='surrogateescape')
    result,receipt=parse_response(raw,c.model_result_schema(),capture['receipt'])
    from .cloud_budget import settle_paid_response
    settle_paid_response(db,s['planning']['call_id'],receipt,_cloud_expected(row,s),'succeeded')
    return result

def _plan(db,p,row,job,cached_only):
    s=_live(db,p,row,job)
    if s['plan'] is not None:return s
    if s['planning']['capture'] is None:
        need(s['planning']['call_id'] is None,'原模型调用的回答尚不能确认，未重新付费发送')
        reverse=list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id==p.workspace_id,
            m.CloudCall.data['gateway_dispatch']['identity']['suggestion_id'].as_string()==row.id).limit(2)))
        need(not reverse,'原模型调用仍有独立记录，未再次预留或发送')
        need(not cached_only,'恢复处理只能读取原答，不能再次发送模型请求')
        model=_model(db,p,s['request']['model_profile_id']);need(digest(model.data)==s['context']['model_profile_hash'])
        # Frozen prepared output is public; reprepare original two messages and
        # compare exact bytes before the real gateway adds its one schema prefix.
        day=json.loads(s['preview']['messages'][1]['content'])['reference_registration_day']
        original=_messages({**s['request'],'instance_id':s['context']['instance_id']},
            json.loads(s['preview']['messages'][1]['content'])['catalog'],day)
        from .cloud_business_reasoning_v197 import prepare
        prepared=prepare(model,original,c.model_result_schema());need(same(prepared['messages'],s['preview']['messages'])
            and prepared['token_bound']==s['preview']['token_bound'])
        s['planning']['job_id']=job.id;_set(row,s);db.commit()
        def reserved(call_id):
            current=_load(db,p,row.id);state=_live(db,p,current,job)
            model_now=_model(db,p,state['request']['model_profile_id']);need(model_now.content_hash==state['context']['model_profile_hash'])
            need(state['planning']['call_id'] is None and state['planning']['capture'] is None)
            ledger=_ref(db,p,m.CloudCall,call_id);expected=_cloud_expected(current,state)
            need(ledger.status=='reserved' and ledger.capability_id==model_now.id and ledger.reserved_tokens==state['preview']['token_bound']
                and all(ledger.data.get(k)==expected[k] for k in ['actor_id','request_hash','dispatch_options_hash']))
            need(same(ledger.data.get('gateway_dispatch',{}).get('identity'),{
                'suggestion_id':current.id,'job_id':job.id,'task_id':'pixel-question-v252','request_hash':state['request_hash'],
                'workspace_id':p.workspace_id,'actor_id':p.user_id,'capability_id':model_now.id}))
            state['planning'].update(call_id=call_id,state='reserved');_set(current,state)
            _audit(db,p,RESERVED,current.id,_reserved_expected(current,state));db.commit()
        def captured(raw,receipt):
            # A separate history-only transaction preserves the first paid HTTP
            # response even if cancellation or lease loss revoked execution.
            need(type(raw) is str)
            wire=raw.encode('utf-8',errors='surrogateescape')
            capture={'raw_b64':base64.b64encode(wire).decode('ascii'),'raw_sha256':hashlib.sha256(wire).hexdigest(),'receipt':copy.deepcopy(receipt)}
            from .db import engine
            with Session(engine()) as saved:
                current=_load(saved,p,row.id,True);state=checked(saved,p,current)
                need(state['planning']['call_id']==receipt.get('call_id'))
                if state['planning']['capture'] is not None:need(same(state['planning']['capture'],capture));return
                state['planning'].update(capture=capture,state='response_saved');_set(current,state)
                _audit(saved,p,CAPTURED,current.id,{'schema':'pixel-question-response-captured-v252','query_id':current.id,
                    'call_id':receipt['call_id'],'context_hash':state['context_hash'],'raw_sha256':capture['raw_sha256'],
                    'receipt_hash':digest(receipt)});saved.commit()
        from .gateway import cloud_json
        try:
            cloud_json(model,original,c.model_result_schema(),p,on_reserved=reserved,on_response=captured,
                full_report=False,evidence_stage='business-reasoning-v197',dispatch_identity={
                    'suggestion_id':row.id,'job_id':job.id,'task_id':'pixel-question-v252','request_hash':s['request_hash']})
        finally:
            # Never overwrite a first answer captured in the independent Session.
            db.expire_all()
        row=_load(db,p,row.id);s=_live(db,p,row,job)
    value=_parse_capture(db,p,row,s)
    if value['status']=='needs_clarification':
        row.status='needs_clarification';s['clarifications']=value['questions'];s['limitations']=value['limitations']
        s['planning']['state']='parsed';_set(row,s);db.commit();return None
    day=json.loads(s['preview']['messages'][1]['content'])['reference_registration_day']
    try:
        ir=c.validate_intent(value,row.question,{'instance_id':s['context']['instance_id'],
            'authorization_release_id':s['request']['release_id'],'task_id':s['request']['task_id'],'entity_id':s['request']['entity_id']},reference_day=day)
    except ValueError as error:
        row.status='needs_clarification';s['clarifications']=[str(error)];s['planning']['state']='parsed';_set(row,s);db.commit();return None
    s['plan']=ir;s['plan_hash']=digest(ir);s['plan_explanation']=value['explanation'];s['planning']['state']='parsed'
    proof=_audit(db,p,PLANNED,row.id,plan_audit_expected(row,s));s['plan_audit_id']=proof.id;_set(row,s);db.commit();return s

def _capture_source(db,p,row,job):
    s=_live(db,p,row,job)
    if s['source_snapshot'] is not None:return s
    from .pixel_question_sources_v252 import capture
    from .db import engine
    db.commit()
    # Isolation is set before ANY SELECT in the independent capture connection.
    with Session(engine()) as read_db:
        read_db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        snapshot=capture(read_db,p,s['context']['instance_id'],task_id=s['request']['task_id'],entity_id=s['request']['entity_id'],limit=1000)
    row=_load(db,p,row.id);s=_live(db,p,row,job)
    need(s['source_snapshot'] is None and s['source_audit_id'] is None)
    s['source_snapshot']=snapshot;s['source_job_id']=job.id
    proof=_audit(db,p,SOURCED,row.id,source_audit_expected(row,s));s['source_audit_id']=proof.id;_set(row,s);db.commit();return s


def authorize_tool(db,p,cap,inputs,key):
    catalog.validate_manifest(cap.data);validate(c.tool_input_schema(),inputs)
    row=_load(db,p,inputs['query_id']);s=checked(db,p,row)
    owned=ownership(db);job=_ref(db,p,m.Job,owned.get('job_id'))
    s=_live(db,p,row,job)
    need(cap.id==s['tool_pin']['id'] and cap.content_hash==s['tool_pin']['hash']
        and inputs['plan_hash']==s['plan_hash'] and s['plan'] is not None and s['source_snapshot'] is not None
        and key=='pixel-question-tool-v252:'+row.id+':'+job.id,
        '影像问答工具仅能读取原问题已冻结的数据范围，不能直接传入数据记录')
    from .pixel_question_sources_v252 import verify_saved_snapshot
    verify_saved_snapshot(db,p,row,s['source_snapshot'])
    return row,s,job

def tool_execute(db,p,cap,inputs,key):
    row,s,job=authorize_tool(db,p,cap,inputs,key)
    call=db.scalar(select(m.Invocation).where(m.Invocation.workspace_id==p.workspace_id,m.Invocation.idempotency_key==key))
    need(call is not None and call.status=='running' and call.capability_id==cap.id and call.data.get('role')=='data'
        and call.data.get('input_hash')==digest(inputs) and call.data.get('capability_hash')==cap.content_hash
        and call.data.get('job_id')==job.id and call.data.get('lease_token')==job.lease_token)
    from .pixel_question_sources_v252 import compute,verify_saved_snapshot
    local=compute(s['source_snapshot'],s['plan']);need(local.get('schema')=='pixel-question-computation-v252')
    answer={**local,'schema':'pixel-question-answer-v252','query_id':row.id,'request_hash':s['request_hash'],'plan_hash':s['plan_hash']}
    need(answer.get('source_snapshot_hash')==s['source_snapshot']['snapshot_hash'])
    answer['answer_hash']=digest(answer);validate(c.answer_schema(),answer)
    authorize_tool(db,p,cap,inputs,key);verify_saved_snapshot(db,p,row,s['source_snapshot'])
    return answer

def _verify_tool(db,p,row,s,call):
    need(call.capability_id==s['tool_pin']['id'] and call.data.get('role')=='data'
        and call.data.get('capability_hash')==s['tool_pin']['hash']
        and call.data.get('input_hash')==digest({'query_id':row.id,'plan_hash':s['plan_hash']})
        and call.data.get('job_id') in s['execution_job_ids']
        and call.idempotency_key=='pixel-question-tool-v252:'+row.id+':'+call.data['job_id'])
    if call.status=='succeeded':
        from .pixel_question_sources_v252 import compute
        local=compute(s['source_snapshot'],s['plan']);expected={**local,'schema':'pixel-question-answer-v252',
            'query_id':row.id,'request_hash':s['request_hash'],'plan_hash':s['plan_hash']}
        expected['answer_hash']=digest(expected);need(same(call.data.get('output'),expected))
        proofs=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action=='capability.invoke',m.Audit.object_id==call.id)))
        need(len(proofs)==1 and proofs[0].actor_id==p.user_id and same(proofs[0].data,{'status':'succeeded'}))
    return {'invocation_id':call.id,'job_id':call.data['job_id'],'capability_id':call.capability_id,
        'capability_hash':call.data['capability_hash'],'input_hash':call.data['input_hash'],
        'output_hash':digest(call.data['output']) if call.status=='succeeded' else None,'status':call.status}

def process(db,p,job):
    row=_load(db,p,job.data['query_id']);s=checked(db,p,row)
    if row.status=='cancelled':return 'cancelled'
    if row.status=='succeeded':return 'succeeded'
    s=_live(db,p,row,job);row.status='running';s['error']=None;_set(row,s);db.commit()
    try:
        if _plan(db,p,row,job,bool(job.data.get('recovery_only')) or job.data['pixel_question_cached_only']) is None:return 'needs_clarification'
        row=_load(db,p,row.id);s=_capture_source(db,p,row,job)
        from .pixel_question_sources_v252 import verify_saved_snapshot
        verify_saved_snapshot(db,p,row,s['source_snapshot'])
        # A successful exact SDK call may be reused after a later parent crash.
        calls=list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id==p.workspace_id,
            m.Invocation.idempotency_key.startswith('pixel-question-tool-v252:'+row.id+':')).order_by(m.Invocation.created_at)))
        for old in calls:_verify_tool(db,p,row,s,old)
        successes=[old for old in calls if old.status=='succeeded'];need(len(successes)<=1)
        if successes:call=successes[0]
        else:
            need(not any(old.status in {'running','unknown','created'} for old in calls),'原本机查询调用尚不能确认，请先核对原记录')
            from .gateway import invoke
            call=invoke(db,p,s['tool_pin']['id'],'data',{'query_id':row.id,'plan_hash':s['plan_hash']},'pixel-question-tool-v252:'+row.id+':'+job.id)
        db.expire_all();row=_load(db,p,row.id);s=_live(db,p,row,job)
        s['tool']=_verify_tool(db,p,row,s,call)
        need(call.status=='succeeded','本机查询未成功完成；原调用记录保留，请显式恢复')
        verify_saved_snapshot(db,p,row,s['source_snapshot'])
        s['answer']=copy.deepcopy(call.data['output']);s['error']=None;row.status='succeeded'
        proof=_audit(db,p,OUTCOME,row.id,{'schema':'pixel-question-answered-v252','query_id':row.id,
            'request_hash':s['request_hash'],'plan_hash':s['plan_hash'],'source_audit_id':s['source_audit_id'],
            'source_snapshot_hash':s['source_snapshot']['snapshot_hash'],'invocation_id':call.id,
            'output_hash':digest(s['answer']),'answer_hash':s['answer']['answer_hash']})
        s['outcome_audit_id']=proof.id;_set(row,s);db.commit();return row.status
    except Exception as error:
        from .gateway import ModelBlocked
        from .leases import LeaseLost,ExecutionUnauthorized
        if isinstance(error,(LeaseLost,ExecutionUnauthorized)):raise
        if not isinstance(error,(HTTPException,ModelBlocked,httpx.HTTPError,ValueError,KeyError,TypeError)):raise
        db.rollback();row=_load(db,p,row.id);s=checked(db,p,row)
        if row.status=='cancelled':return 'cancelled'
        # Re-check the actual owned lease/current authority before any failure
        # write. Unknown database commit/connection exceptions are NOT caught.
        _live(db,p,row,job)
        if s['plan'] is not None and s['source_snapshot'] is not None:
            saved_calls=list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id==p.workspace_id,
                m.Invocation.idempotency_key.startswith('pixel-question-tool-v252:'+row.id+':')).order_by(m.Invocation.created_at.desc())))
            if saved_calls:s['tool']=_verify_tool(db,p,row,s,saved_calls[0])
        reverse=bool(s['planning']['call_id']) or bool(db.scalar(select(m.CloudCall.id).where(m.CloudCall.workspace_id==p.workspace_id,
            m.CloudCall.data['gateway_dispatch']['identity']['suggestion_id'].as_string()==row.id).limit(1)))
        if s['planning']['capture'] is None and reverse:
            row.status='unknown_model';s['planning']['state']='unknown';s['error']='模型请求的原回答尚不能确认，未重新发送；请保留原问题并核对记录'
        else:
            row.status='blocked_model' if isinstance(error,ModelBlocked) else 'blocked_evidence' if s['source_snapshot'] is None and s['plan'] is not None else 'blocked_integrity'
            s['error']=str(error.detail) if isinstance(error,HTTPException) and type(error.detail) is str else str(error) if isinstance(error,(ValueError,ModelBlocked)) else ERROR
        _set(row,s);db.commit();return row.status

def _actions(row,s):
    can_cancel=row.status in {'queued','running','unknown_model','blocked_model','blocked_evidence','blocked_integrity','failed'}
    recoverable=s['planning']['capture'] is not None or s['plan'] is not None
    can_retry=row.status in {'blocked_model','blocked_evidence','blocked_integrity','failed','cancelled','unknown_model'} and recoverable
    return {'can_cancel':can_cancel,'cancel_reason':None if can_cancel else '当前问题无需取消',
        'can_retry':can_retry,'retry_reason':None if can_retry else '没有可恢复的原规划回答；不会重新发送未知模型请求，请查看原记录或明确另提新问'}

def view(db,p,row):
    s=checked(db,p,row)
    source_issue=None
    if s['source_snapshot'] is not None:
        from .pixel_question_sources_v252 import verify_saved_snapshot
        try:verify_saved_snapshot(db,p,row,s['source_snapshot'])
        except HTTPException as error:
            if error.status_code!=409:raise
            source_issue='原问题和原查询已保存，现有依据不能核对；请检查相关资料，未重新发送或查询'
    if s['tool'] is not None:
        call=_ref(db,p,m.Invocation,s['tool']['invocation_id']);need(same(s['tool'],_verify_tool(db,p,row,s,call)))
    planning=s['planning'];capture=planning['capture'];source=s['source_snapshot']
    actions=_actions(row,s)
    failure_issue=None
    if row.status=='failed' and not s['error']:
        # Generic worker recovery records failure outside this typed family.
        # Project a safe explanation without rewriting history or classifying
        # an unknown database commit as a successful/failed model transport.
        failure_issue=('后台查询已停止；原问题与已保存的规划仍保留。请检查相关资料后恢复原查询，不会重新发送已保存的模型回答。'
            if actions['can_retry'] else '后台查询已停止；原问题仍保留。请核对原记录，未重新发送模型请求。')
    if source_issue:actions={**actions,'can_retry':False,'retry_reason':'原数据依据无法核对；恢复依据前不能重新处理这个问题'}
    return {'schema':'pixel-question-view-v252','id':row.id,'version':row.version,'workspace_id':p.workspace_id,
        'instance_id':s['context']['instance_id'],'actor_id':p.user_id,'status':'blocked_integrity' if source_issue else row.status,
        'request':s['request'],'request_hash':s['request_hash'],'raw_sha256':s['raw_sha256'],'created_at':row.created_at.isoformat(),
        'plan':s['plan'],'plan_hash':s['plan_hash'],'plan_explanation':s['plan_explanation'],
        'clarifications':s['clarifications'],'limitations':s['limitations'],
        'planning':{'state':planning['state'],'model_profile_id':s['request']['model_profile_id'],
            'model_profile_hash':s['context']['model_profile_hash'],'cloud_call_id':planning['call_id'],
            'response_sha256':capture['raw_sha256'] if capture else None,'receipt_hash':digest(capture['receipt']) if capture else None},
        'source':None if source is None else {'audit_id':s['source_audit_id'],'snapshot_hash':source['snapshot_hash'],
            'revision_hash':source['revision_hash'],'captured_at':source['captured_at'],'complete':True,'run_count':len(source['records'])},
        'tool':s['tool'],'answer':None if source_issue else s['answer'],'error':source_issue or s['error'] or failure_issue,'actions':actions}

def get(db,p,instance_id,identity):
    scoped(db,m.DomainInstance,instance_id,p);row=_load(db,p,identity);s=checked(db,p,row)
    need(s['context']['instance_id']==instance_id,code=404);return view(db,p,row)

def lookup(db,p,instance_id,request_id,expected_body_hash):
    row=_find_request(db,p,instance_id,request_id)
    if row is not None:need(checked(db,p,row)['request_hash']==expected_body_hash,'原申请编号与正文摘要不符')
    return {'schema':'pixel-question-request-recovery-v252','found':row is not None,'state':'found' if row else 'unknown',
        'request_id':request_id,'expected_body_hash':expected_body_hash,'query':view(db,p,row) if row else None}

def history(db,p,instance_id,limit=20,before_id=None):
    scoped(db,m.DomainInstance,instance_id,p)
    need(type(limit) is int and 1<=limit<=50)
    # Independent creation Audits make marker stripping visible, not ordinary
    # queries or missing records silently absent from the history.
    statement=select(m.DataRun).join(m.Audit,m.Audit.object_id==m.DataRun.id).where(m.DataRun.workspace_id==p.workspace_id,
        m.DataRun.owner_id==p.user_id,m.Audit.workspace_id==p.workspace_id,m.Audit.actor_id==p.user_id,m.Audit.action==CREATED,
        m.Audit.data['instance_id'].as_string()==instance_id)
    missing=db.scalar(select(m.Audit.id).outerjoin(m.DataRun,m.DataRun.id==m.Audit.object_id).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.actor_id==p.user_id,m.Audit.action==CREATED,m.Audit.data['instance_id'].as_string()==instance_id,m.DataRun.id.is_(None)).limit(1))
    need(missing is None)
    unsigned=db.scalar(select(m.DataRun.id).outerjoin(m.Audit,and_(m.Audit.object_id==m.DataRun.id,
        m.Audit.workspace_id==p.workspace_id,m.Audit.action==CREATED)).where(m.DataRun.workspace_id==p.workspace_id,
        m.DataRun.owner_id==p.user_id,m.DataRun.data[KEY]['context']['instance_id'].as_string()==instance_id,
        m.Audit.id.is_(None)).limit(1))
    need(unsigned is None)
    if before_id:
        prior=_load(db,p,before_id);need(checked(db,p,prior)['context']['instance_id']==instance_id,code=404)
        statement=statement.where(or_(m.DataRun.created_at<prior.created_at,and_(m.DataRun.created_at==prior.created_at,m.DataRun.id<prior.id)))
    rows=list(db.scalars(statement.order_by(m.DataRun.created_at.desc(),m.DataRun.id.desc()).limit(limit+1)))
    return {'schema':'pixel-question-list-v252','workspace_id':p.workspace_id,'instance_id':instance_id,
        'records':[summary(db,p,row) for row in rows[:limit]],'next_before_id':rows[limit-1].id if len(rows)>limit else None,'has_more':len(rows)>limit}


def _command_proof(db,p,request_id):
    rows=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==COMMAND,
        m.Audit.data['body']['request_id'].as_string()==request_id).limit(2)))
    need(len(rows)<=1,'操作编号对应多条记录，不能确定原操作')
    return rows[0] if rows else None

def _check_command(db,p,row,s,proof):
    need(proof.actor_id==p.user_id,code=404);value=proof.data;body=c.Command.model_validate(value['body']).model_dump(mode='json')
    need(set(value)=={'schema','query_id','instance_id','actor_id','body','body_hash','raw_b64','raw_sha256','request_hash','result'}
        and value['schema']=='pixel-question-command-audit-v252' and value['query_id']==row.id
        and proof.object_id==row.id and value['instance_id']==s['context']['instance_id'] and value['actor_id']==p.user_id
        and digest(body)==value['body_hash'] and body['expected_request_hash']==s['request_hash']==value['request_hash'])
    raw=base64.b64decode(value['raw_b64'],validate=True);need(hashlib.sha256(raw).hexdigest()==value['raw_sha256']
        and same(_wire(raw,c.Command),body))
    pins=[v for v in s['commands'] if v['audit_id']==proof.id]
    need(len(pins)==1 and same(pins[0],{'audit_id':proof.id,'request_id':body['request_id'],'body_hash':value['body_hash'],'result_hash':digest(value['result'])}))
    result=value['result'];need(set(result)=={'schema','operation','request_id','body_hash','command_audit_id','query'}
        and result['schema']=='pixel-question-command-result-v252' and result['command_audit_id']==proof.id
        and result['operation']==body['operation'] and result['request_id']==body['request_id'] and result['body_hash']==value['body_hash']
        and result['query']['id']==row.id and result['query']['request_hash']==s['request_hash']
        and same(result['query']['request'],s['request']) and result['query']['actor_id']==p.user_id)
    return copy.deepcopy(result)

def command(db,p,instance_id,identity,operation,body,raw):
    need(operation==body.operation,'操作路径与正文不符',422);_nonce(db,p,'pixel-question-command',body.request_id)
    row=_load(db,p,identity);s=checked(db,p,row);need(s['context']['instance_id']==instance_id,code=404)
    prior=_command_proof(db,p,body.request_id)
    if prior is not None:
        need(prior.object_id==row.id,'原操作编号已用于另一问题')
        result=_check_command(db,p,row,s,prior);need(result['body_hash']==digest(body.model_dump(mode='json')),'原操作编号不能更换正文')
        return result
    from .relations import lock_current_actor
    lock_current_actor(db,p);row=_load(db,p,row.id,True);s=checked(db,p,row)
    need(row.version==body.version and s['request_hash']==body.expected_request_hash,'问题状态已变化，请刷新后操作')
    need(same(_wire(raw,c.Command),body.model_dump(mode='json')),'原操作正文无法核对',422)
    actions=_actions(row,s);need(actions['can_'+operation],actions[operation+'_reason'] or ERROR)
    if operation=='cancel':row.status='cancelled'
    else:
        # Explicit local resume needs current authority, never a new cloud call.
        opts=options(db,p,instance_id);need(opts['can_ask'] and opts['release_id']==s['request']['release_id'],
            '当前业务版本尚未启用或已变化；可以继续查看原问题')
        active_jobs=list(db.scalars(select(m.Job).where(m.Job.workspace_id==p.workspace_id,m.Job.id.in_(s['execution_job_ids']),m.Job.status.in_(['queued','running']))))
        need(not active_jobs,'原后台查询仍在处理，请等候或核对原记录，未新增处理')
        jid=m.uid();s['execution_job_ids'].append(jid);row.status='queued';s['error']=None
        db.add(m.Job(id=jid,workspace_id=p.workspace_id,kind='data_query',idempotency_key='pixel-question-v252:'+row.id+':retry:'+body.request_id,
            data={'query_id':row.id,'actor_id':p.user_id,'family':KEY,'context_hash':s['context_hash'],'recovery_only':True,'pixel_question_cached_only':True}))
        _audit(db,p,QUEUED,row.id,{'query_id':row.id,'job_id':jid,'context_hash':s['context_hash'],'recovery_only':True})
    proof=m.Audit(id=m.uid(),workspace_id=p.workspace_id,actor_id=p.user_id,action=COMMAND,object_id=row.id,data={})
    _set(row,s);db.flush()
    result={'schema':'pixel-question-command-result-v252','operation':operation,'request_id':body.request_id,
        'body_hash':digest(body.model_dump(mode='json')),'command_audit_id':proof.id,'query':view(db,p,row)}
    proof.data={'schema':'pixel-question-command-audit-v252','query_id':row.id,'instance_id':instance_id,'actor_id':p.user_id,
        'body':body.model_dump(mode='json'),'body_hash':result['body_hash'],'raw_b64':base64.b64encode(raw).decode('ascii'),
        'raw_sha256':hashlib.sha256(raw).hexdigest(),'request_hash':s['request_hash'],'result':copy.deepcopy(result)}
    s['commands'].append({'audit_id':proof.id,'request_id':body.request_id,'body_hash':result['body_hash'],'result_hash':digest(result)})
    row.data={**row.data,KEY:copy.deepcopy(s)}
    db.add(proof);db.commit();return result

def command_lookup(db,p,instance_id,identity,request_id,expected_body_hash):
    row=_load(db,p,identity);s=checked(db,p,row);need(s['context']['instance_id']==instance_id,code=404)
    prior=_command_proof(db,p,request_id);result=None
    if prior is not None:
        need(prior.actor_id==p.user_id,code=404);need(prior.object_id==row.id,'原操作编号已用于另一问题')
        result=_check_command(db,p,row,s,prior);need(result['body_hash']==expected_body_hash,'原操作编号与正文摘要不符')
    return {'schema':'pixel-question-command-recovery-v252','found':prior is not None,'state':'found' if prior else 'unknown',
        'request_id':request_id,'expected_body_hash':expected_body_hash,'result':result}


def summary(db,p,row):
    s=checked(db,p,row,summary_only=True)
    need(row.status in c.STATUS)
    return {'id':row.id,'version':row.version,'status':row.status,'created_at':row.created_at.isoformat(),
        'question':row.question,'scope':{'instance_id':s['context']['instance_id'],
            'authorization_release_id':s['request']['release_id'],'task_id':s['request']['task_id'],'entity_id':s['request']['entity_id']},
        'model_profile_id':s['request']['model_profile_id'],'has_saved_answer':s['answer'] is not None,'has_saved_plan':s['plan'] is not None}


def safe_reference(db,p,row):
    # No generic response contains frozen records, paid raw content or numbers.
    row=_load(db,p,row.id);s=checked(db,p,row,summary_only=True)
    return {'schema':'data-query-reference-v144','id':row.id,'version':row.version,'status':row.status,
        'question':row.question,'created_at':row.created_at.isoformat(),'reference_only':True,'family':KEY,
        'context':{'instance_id':s['context']['instance_id'],'task_id':s['request']['task_id'],
            'detail_path':'/api/workspace/instances/'+s['context']['instance_id']+'/pixel-questions/'+row.id},
        'notice':'这是影像候选成果的原问题，请在原实例的影像成果问答中核对；通用字段查询不采用或改写其结果。'}
