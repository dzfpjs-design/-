"""只基于白名单结构生成候选；模型没有审核、发布或执行权限。"""
import copy
import json
import time
from datetime import timedelta,timezone
from zoneinfo import ZoneInfo
from typing import Literal
from uuid import uuid4
from fastapi import HTTPException
from pydantic import Field, StrictInt, ValidationError
from sqlalchemy import select, func, text, tuple_
from .contracts import Contract, digest
from .models import Memory, Task, Capability, Job, CloudCall, User, Membership, DomainInstance, InstanceRelease, now
from .services import scoped, audit, check_version, published
from . import native_cases as cases
from .evidence_question import model
from .gateway import cloud_json, ModelBlocked, CloudNotSent
from . import gateway
from .leases import checkpoint

TYPE='native_procedure_suggestion'
POLICY='native_method_policy'
JOB='generate_native_method_suggestion'
V1='native-method-projection-v1'
VERSION='native-method-projection-v2'
PATTERNS={
 'source_identity':('identity_current_v1','对照本次原资料，核对资料来源与当前业务对象的对应关系'),
 'source_time':('declared_time_v1','核对本次资料声明的时间是否符合原目标时间范围；不代表设备时钟已经核实'),
 'units':('declared_units_v1','核对本次字段的明确单位；单位缺失或不一致时保存未知'),
 'comparability':('independent_comparison_v1','核对独立原件、字段单位、采样方法、空间范围及时间条件；不能只凭两份记录确认可比较'),
 'field_verification':('current_field_v1','对照本次原资料核对字段；旧任务值不能替代当前依据')}
V1_PATTERNS=copy.deepcopy(PATTERNS)
LOCAL_PATTERNS=copy.deepcopy(PATTERNS)
LOCAL_VERSION='native-method-local-rules-v1'
LOCAL_RULES=[
 '按照原目标必需字段的顺序，分别核对本次当前原资料中的字段；旧任务值不替代当前依据。',
 '明确为数值且单位未知的字段，另加单位核查；缺失或不一致仍保持未知。字段类型尚不明确时也不推断单位。',
 '有真实时间范围要求时，每个必需字段另加时间核查；声明时间不证明设备时钟，也不替代本次目标范围。',
 '只生成必要核查项，最多五项；不执行设备或模型，不自动审核，不把未知事实变成已核实。']
V1_CONTRACT={'version':V1,'field_types':['string','number','integer','boolean','array','object','unknown'],
 'flags':['known','unknown'],'buckets':['0','1','2+'],'templates':list(PATTERNS),
 'patterns':{key:value[0] for key,value in PATTERNS.items()},'max_fields':20,'max_steps':5}
V1_CONTRACT_HASH=digest(V1_CONTRACT)
CONTRACT={**copy.deepcopy(V1_CONTRACT),'version':VERSION,
 'priority_tags':['goal_required','numeric_unit_unknown','required_time_unknown'],
 'goal':{'minimum_verification':['unverified_extraction','recorded','confirmed'],'time_constraint':['none','required']}}
CONTRACT_HASH=digest(CONTRACT)
PROJECTION_CONTRACTS={V1:V1_CONTRACT,VERSION:CONTRACT}
GENERATION_RULES=[
 '优先对照当前原资料核查每个目标必需字段，分别选择field_verification；已知来源不能代替字段核查。',
 '数值字段单位未知时选择units进行核查，单位缺失或不一致仍须保持未知，不猜测单位或量纲。',
 '目标时间要求为required时，每个目标字段都需要source_time核查；已有声明时间不代表符合未来目标范围，也不证明设备时钟。',
 '只有真实时间目标才要求时间检查，不能因unknown标记虚构时间目标。',
 '不能仅凭两条记录推断可比较；旧值、已验收结论不能代替本次当前依据。',
 '只返回steps，逐项包含alias、template、pattern_id三个键；从allowed_pairs选择1至5项不同配对，不输出自由文字或执行指令。']
CAPACITY_ERROR='本次核查需求超过建议容量（最多5项），请缩小明确目标范围后重新预览；原验收与历史保留'
NOTICE='只发送下列有限结构与匿名字段别名；不发送业务名称、实际值、身份或原件。结果只生成待核查草稿。'
FORBIDDEN=['业务和对象名称','字段与节点自由名称','原件、属性值和阈值','身份、位置、凭据和内部标识','人工说明、日志及工具输出']


class Preview(Contract):
    model_profile_id:str
class LocalPreview(Contract):
    pass
class LocalStart(cases.Command):
    preview_hash:str
class Start(cases.Command):
    model_profile_id:str
    preview_hash:str
    cloud_authorized:Literal[True]
class Change(cases.Command):
    expected_suggestion_version:StrictInt=Field(ge=1)
    record_hash:str
class Save(Change):
    title:str=Field(min_length=1,max_length=200)
    summary:str=Field(min_length=1,max_length=2000)
    limits:str=Field(min_length=1,max_length=2000)
    steps:list[cases.Step]=Field(min_length=1,max_length=5)
class Import(Change):
    saved_edit_hash:str
    acceptance_hash:str
    case_id:str|None=None
    expected_case_version:StrictInt|None=Field(default=None,ge=1)
    content_hash:str|None=None
    replace_existing:bool=False
class Retry(Change):
    model_profile_id:str|None=None
    preview_hash:str|None=None
    cloud_authorized:bool=False
    acknowledge_possible_charge:bool=False
class PolicyChange(cases.Command):
    enabled:bool
    expected_policy_version:StrictInt|None=Field(default=None,ge=1)
    policy_record_hash:str|None=None
    model_profile_id:str|None=None
    projection_contract_hash:str|None=None
    generation_contract_hash:str|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')
    preview_hash:str|None=None
    cloud_authorized:bool=False
    daily_limit:StrictInt|None=Field(default=None,ge=1,le=5)
    valid_days:Literal[1,7,30]|None=None
class Recovery(Contract):
    expected_version:StrictInt=Field(ge=1)
    reason:str=Field(min_length=1,max_length=2000)
    operation:Literal['start','local_start','save','decline','cancel','retry','import','policy']
    original_request:dict
    action:Literal['probe','abandon']='probe'
    suggestion_id:str|None=None
MODELS={'start':Start,'local_start':LocalStart,'save':Save,'decline':Change,'cancel':Change,'retry':Retry,'import':Import,'policy':PolicyChange}
class Choice(Contract):
    alias:str=Field(pattern=r'^F[1-9][0-9]?$')
    template:Literal['source_identity','source_time','units','comparability','field_verification']
    pattern_id:str
class Proposal(Contract):
    steps:list[Choice]=Field(min_length=1,max_length=5)


OUTPUT_CONTRACT=Proposal.model_json_schema()


def generation_contract():
    fixed={'task':'propose_current_goal_checks','rules':GENERATION_RULES,'output_contract':OUTPUT_CONTRACT}
    value={'version':'native-method-generation-v2','prompt_version':'native-method-prompt-v2',
        'prompt_hash':digest({'fixed':fixed,'gateway_system':{'role':'system','content':gateway.CLOUD_JSON_SYSTEM_PREFIX+json.dumps({},ensure_ascii=False)},'dispatch_schema':{}}),
        'output_contract_hash':digest(OUTPUT_CONTRACT),
        'rules':copy.deepcopy(GENERATION_RULES),'max_steps':5}
    return {**value,'generation_contract_hash':digest(value)}


def legacy_generation():
    return {'version':'native-method-generation-v1','prompt_version':'native-method-prompt-v1',
        'prompt_hash':None,'output_contract_hash':None,'generation_contract_hash':None,
        'rules':['旧记录仅从许可配对中选择1至5项；未保存完整提示授权，不补造新版授权。'],'max_steps':5}


def request_version(request):
    version=request.get('cloud_input',{}).get('contract_version')
    if version not in PROJECTION_CONTRACTS:fail('未知的原方法投影合同，不能解释或继续发送')
    if request.get('projection_contract_hash')!=digest(PROJECTION_CONTRACTS[version]):fail('原外发白名单合同摘要无法核对')
    return version


def checked_generation(request,for_dispatch=False,cap=None):
    version=request_version(request)
    if version==V1:
        if any(key in request for key in ['generation_contract','dispatch_messages_hash','dispatch_request_messages','dispatch_messages','dispatch_schema','dispatch_options']):
            fail('旧方法记录不能移植新版提示授权')
        if for_dispatch:fail('旧方法请求未固定新版提示，不能发送；请重新预览授权并另建候选')
        return legacy_generation()
    value=request.get('generation_contract')
    if (not isinstance(value,dict) or value.get('version')!='native-method-generation-v2'
        or value.get('generation_contract_hash')!=digest({key:item for key,item in value.items() if key!='generation_contract_hash'})
        or request.get('dispatch_schema')!={}
        or not isinstance(request.get('dispatch_messages'),list) or not isinstance(request.get('dispatch_request_messages'),list)
        or not isinstance(request.get('dispatch_options'),dict)
        or request.get('dispatch_messages_hash')!=digest(request['dispatch_request_messages'])):
        fail('原固定生成规则、输出合同或发送摘要无法核对')
    try:
        messages=request['dispatch_messages'];prepared=request['dispatch_request_messages']
        fixed=json.loads(messages[0]['content']);cloud=fixed.pop('input')
        if (len(messages)!=1 or messages[0]['role']!='user' or len(prepared)!=2 or prepared[0]['role']!='system'
            or cloud!=request['cloud_input'] or fixed.get('rules')!=value['rules']
            or fixed.get('task')!='propose_current_goal_checks' or value.get('prompt_version')!='native-method-prompt-v2'
            or value['output_contract_hash']!=digest(fixed['output_contract'])
            or value['prompt_hash']!=digest({'fixed':fixed,'gateway_system':prepared[0],'dispatch_schema':request['dispatch_schema']})):
            fail('原完整固定提示、规则或输出合同无法核对')
    except (KeyError,IndexError,ValueError,TypeError) as error:
        raise HTTPException(409,'原固定提示结构无法核对，请保留记录待核') from error
    if for_dispatch:
        expected=dispatch(cap,request['cloud_input'])
        if any(request.get(key)!=expected[key] for key in expected):fail('固定生成规则、输出合同或网关消息已改变，请重新预览授权')
    return value


def dispatch(cap,cloud):
    messages=[{'role':'user','content':json.dumps({'task':'propose_current_goal_checks',
        'rules':GENERATION_RULES,'output_contract':OUTPUT_CONTRACT,'input':cloud},ensure_ascii=False,sort_keys=True)}]
    request_messages,_,_=gateway.prepare_cloud_json(cap,messages,{})
    return {'generation_contract':generation_contract(),'dispatch_messages':messages,'dispatch_schema':{},
        'dispatch_request_messages':request_messages,'dispatch_messages_hash':digest(request_messages),
        'dispatch_options':gateway.cloud_json_options(cap.data['execution'])}


def command_body(body):
    value=body.model_dump()
    # 新增的空字段没有授权含义，保留升级前policy原UUID的精确旧形状。
    if isinstance(body,PolicyChange) and value['generation_contract_hash'] is None:value.pop('generation_contract_hash')
    return value


def fail(message):raise HTTPException(409,message)
def signed(value):return {**value,'hash':digest(value)}
def source_kind(request):
    if 'source_kind' not in request:return 'cloud_model'
    if not isinstance(request['source_kind'],str) or request['source_kind'] not in {'local_rules','cloud_model'}:fail('方法建议来源类别未登记，不能解释或操作')
    return request['source_kind']
def local_rules_contract():
    value={'version':LOCAL_VERSION,'rules':copy.deepcopy(LOCAL_RULES),'max_steps':5}
    return {**value,'rules_contract_hash':digest(value)}
def seal(row):
    row.data={**row.data,'id':row.id,'version':row.version,'status':row.status,'workspace_id':row.workspace_id,'task_id':row.task_id}
    row.data={**row.data,'record_hash':digest({k:v for k,v in row.data.items() if k!='record_hash'})}
def checked(db,p,identity,task=None,lock=False,kind=TYPE):
    row=scoped(db,Memory,identity,p,lock)
    data=row.data
    if (data.get('type')!=kind or 'package_id' in data or data.get('id')!=row.id or data.get('version')!=row.version
        or data.get('status')!=row.status or data.get('task_id')!=row.task_id or data.get('workspace_id')!=row.workspace_id
        or data.get('record_hash')!=digest({k:v for k,v in data.items() if k!='record_hash'})
        or task is not None and row.task_id!=task.id):fail('方法建议或授权记录的范围与摘要无法核对')
    if kind==TYPE:
        request=data.get('request')
        if not isinstance(request,dict) or digest(request)!=data.get('request_hash'):fail('方法建议原请求摘要无法核对')
        category=source_kind(request)
        if category=='local_rules':
            try:checked_local_result(row)
            except (KeyError,TypeError,ValueError,IndexError) as error:
                raise HTTPException(409,'本地核查清单的封存依据或结构无法核对') from error
        else:
            if any(key in request or key in data for key in ['local_input','local_rules_contract','local_result','local_quality']):
                fail('云建议不能移植本地规则依据')
            cloud=request.get('cloud_input',{})
            if not isinstance(cloud,dict):fail('旧云建议有限输入结构无法核对')
            if cloud.get('contract_version') in PROJECTION_CONTRACTS:request_version(request)
            elif not (row.status=='failed' and request.get('source_proof') is None and cloud=={} and data.get('automatic')
                and not data.get('inflight') and not any(key in data for key in ['raw_response','cloud_call_id'])):
                fail('旧云建议来源或失败历史不属于已登记合同')
    return row
def scope(db,p,task):
    return cases._scope(db,p,task)
def policy_lock(db,key):
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(key[:16],16)-(1<<63)})
def policy_row(db,p,task,lock=False):
    key=digest(scope(db,p,task))
    if lock:policy_lock(db,key)
    rows=db.scalars(select(Memory).where(Memory.workspace_id==p.workspace_id,Memory.data['type'].as_string()==POLICY,
        Memory.data['scope_hash'].as_string()==key).limit(2)).all()
    if len(rows)>1:fail('同一范围出现多个自动授权，请核对后停用')
    return checked(db,p,rows[0].id,lock=lock,kind=POLICY) if rows else None
def policy_actor(db,p,row,lock=False):
    stmt=select(User.id).join(Membership,Membership.user_id==User.id).where(User.id==row.data['authorized_by'],
        User.active.is_(True),Membership.workspace_id==p.workspace_id,Membership.role=='admin')
    if lock:stmt=stmt.with_for_update(read=True)
    if not db.execute(stmt).first():fail('自动授权人的当前管理员权限已失效')
def valid_policy(db,p,row,lock=False):
    if row.status not in {'enabled','enabled_unlimited_v2'} or not row.data['enabled']:fail('自动生成方法建议已停用')
    if row.data['projection_contract_hash']!=CONTRACT_HASH:fail('结构白名单合同已改变，请重新授权')
    if row.data.get('generation_contract')!=generation_contract() or row.data.get('generation_contract_hash')!=generation_contract()['generation_contract_hash']:
        fail('固定生成规则授权已改变或尚未固定，请重新授权')
    if row.valid_until is None or row.valid_until<=now():fail('自动生成授权已到期')
    if row.valid_until.astimezone(timezone.utc).isoformat()!=row.data['valid_until']:fail('自动授权期限无法核对')
    policy_actor(db,p,row,lock)
    cap=model(db,p,row.data['model_profile_id'])
    if model_hash(cap)!=row.data['model_hash']:fail('自动授权模型版本或权限已改变')
    if row.status=='enabled_unlimited_v2':
        if row.data.get('daily_limit') is not None or cap.data['execution'].get('provider')!='deepseek':fail('不限每日次数的模型授权无法核对')
    elif type(row.data.get('daily_limit')) is not int or not 1<=row.data['daily_limit']<=5:
        fail('自动建议每日限额无法核对')
    return cap
def model_hash(cap):return digest({'content_hash':cap.content_hash,'data':cap.data})
def future_scope(db,p,task,lock=False):
    """已验收历史可读；新增建议只在仍启用的原业务版本内执行。"""
    from .native_goals import saved_context
    context=saved_context(db,p,task)
    def current(kind,identity):
        stmt=select(kind).where(kind.id==identity,kind.workspace_id==p.workspace_id).execution_options(populate_existing=True)
        if lock:stmt=stmt.with_for_update(read=True)
        row=db.scalar(stmt)
        if row is None:fail('原业务执行范围已不存在，历史保留')
        return row
    instance=current(DomainInstance,context['instance_id'])
    if instance.status!='partially_active' or instance.active_release_id!=context['release_id']:
        fail('原实例已暂停或当前配置版本已改变，历史建议保留，不能继续旧范围')
    release=current(InstanceRelease,context['release_id'])
    current(Capability,task.package_id);published(db,p,task.package_id,'domain_package')
    if (release.status!='prepared' or release.instance_id!=instance.id or release.package_id!=task.package_id
        or context['business_id'] not in instance.data.get('enabled_business_ids',[])):
        fail('原业务或配置包已停用或改变，历史建议保留，不能继续旧范围')
    return context
def origin(db,p,task,lock=False):
    if lock:db.info.pop('native_experience_future_work',None)
    future_scope(db,p,task,lock)
    result=cases._origin(db,p,task,lock)
    cases._future_lineage(db,p,task,lock=lock)
    return result
def bucket(value):return '0' if value==0 else '1' if value==1 else '2+'


def necessary_pairs(cloud):
    """只沿真实目标结构；已有时间声明不能替代每个字段的时间范围核查。"""
    aliases=cloud['goal']['required_aliases']
    result={(alias,'field_verification') for alias in aliases}
    result.update((item['alias'],'units') for item in cloud['fields']
        if item['alias'] in aliases and item['type'] in {'integer','number'} and item['flags']['units']=='unknown')
    if cloud['goal']['time_constraint']=='required':result.update((alias,'source_time') for alias in aliases)
    return result


def projection(db,p,task,version=None,*,_proof=None):
    """所有自由资料仅本地核对；出云内容由固定枚举和序号重新构造。"""
    version=version or VERSION
    if version not in PROJECTION_CONTRACTS:fail('未知方法投影合同，不能生成或解释')
    version_patterns=V1_PATTERNS if version==V1 else PATTERNS
    proof=origin(db,p,task) if _proof is None else _proof
    fields=cases._fields(db,p,task)
    if not fields or len(fields)>20:fail('当前字段结构为空或超过20项白名单范围，不能生成建议')
    accepted=task.data['native_goal_acceptance'];evidence=accepted['evidence']
    source=task.data['goal_spec']['evidence']['source']
    if source not in {'archive','observations','either'}:fail('原资料类别不属于白名单合同')
    mapping=[];cloud_fields=[];patterns=[];pairs=[]
    goal_spec=task.data['goal_spec'];time_requirement=goal_spec['evidence']['time']
    time_required=time_requirement['mode']!='archive' or time_requirement.get('max_age_seconds') is not None
    package=scoped(db,Capability,task.package_id,p)
    for index,field in enumerate(fields,1):
        alias='F'+str(index);refs=[]
        for kind,items in [('archive',evidence.get('evidence',[])),('observation',evidence.get('observation_evidence',[]))]:
            if source=='archive' and kind!='archive' or source=='observations' and kind!='observation':continue
            refs.extend((kind,item) for item in items if item.get('attributes',{}).get(field['key']) is not None)
        units=[];contexts=[];fingerprints=[]
        from .models import Entity,Observation
        for kind,item in refs:
            entity=scoped(db,Entity,item['entity_id'],p)
            schema=package.data['specification']['entity_schemas'][entity.entity_type]['properties'].get(field['key'],{})
            unit=schema.get('unit') or schema.get('units');context=None;fingerprint=None
            if kind=='observation':
                obs=scoped(db,Observation,item['observation_id'],p)
                if obs.data.get('mqtt_binding_id'):
                    from .mqtt_observations import find_binding
                    declared=find_binding(db,p,obs.data['mqtt_binding_id']).data['field_units'].get(field['key'])
                    if unit and declared and unit!=declared:fail('原单位声明无法核对')
                    unit=unit or declared
                context=(obs.data.get('original_request') or {}).get('comparison_context')
                saved=next(entry for entry in proof['observations'] if entry['id']==obs.id)
                # JSON登记摘要证明输入血缘，不能单凭不同声明证明独立采样。
                fingerprint=cases.original_content_fingerprint(saved['file_hashes'])
            units.append(unit);contexts.append(context);fingerprints.append((item['entity_id'],fingerprint))
        complete=lambda value:isinstance(value,dict) and isinstance(value.get('measurement_method'),str) and bool(value['measurement_method'].strip()) and isinstance(value.get('spatial_scope'),str) and bool(value['spatial_scope'].strip()) and type(value.get('sampling_interval')) in (int,float) and 0<value['sampling_interval']<float('inf')
        unit_known=bool(refs) and all(isinstance(value,str) and value.strip() for value in units) and len(set(units))==1
        time_known=bool(refs) and all(item.get('observed_at') for _,item in refs)
        comparable=bool(refs) and unit_known and time_known and all(complete(value) for value in contexts) and len({digest(value) for value in contexts})==1 and all(len({fp for identity,fp in fingerprints if identity==entity and fp})>=2 for entity in task.data['entity_ids'])
        flags={'source_identity':'known' if refs else 'unknown','declared_time':'known' if time_known else 'unknown',
            'units':'known' if unit_known else 'unknown','comparability':'known' if comparable else 'unknown'}
        kind=field['type'] if field['type'] in CONTRACT['field_types'] else 'unknown'
        templates=field['templates']
        mapping.append({'alias':alias,'field':field['key'],'field_label':field['label'],'type':kind,'templates':templates})
        cloud_fields.append({'alias':alias,'type':kind,'templates':templates,'flags':flags,'records_bucket':bucket(len(refs))})
        if version==VERSION:
            tags=['goal_required']
            if kind in {'number','integer'} and flags['units']=='unknown':tags.append('numeric_unit_unknown')
            if time_required and flags['declared_time']=='unknown':tags.append('required_time_unknown')
            cloud_fields[-1]['priority_tags']=tags
        for template in templates:
            pattern,instruction=version_patterns[template]
            pairs.append({'alias':alias,'template':template,'pattern_id':pattern})
            patterns.append({'alias':alias,'kind':template,'pattern_id':pattern,'instruction':instruction,'basis_flags':flags})
    criteria=[]
    for value in task.data['goal_spec']['criteria']:
        kind=value['kind']
        if kind not in {'evidence_sufficient','capability_success','field_value','tool_value','query_value','human_confirmation'}:fail('原标准类型不属于结构白名单')
        criteria.append(kind)
    consumed=[]
    for item in accepted.get('experience_proof',{}).get('items',[]):
        match=next((value for value in mapping if value['field']==item['field']),None)
        if match and item['kind'] in PATTERNS:consumed.append({'alias':match['alias'],'kind':item['kind']})
    cloud={'contract_version':version,'fields':cloud_fields,'source':source,'criteria':criteria,
        'consumed_templates':consumed,'allowed_pairs':pairs,'limitations':['proposal_only','unknown_is_not_verified','no_old_values','no_device_or_model_execution','no_automatic_review']}
    capacity=None
    if version==VERSION:
        aliases={item['field']:item['alias'] for item in mapping}
        field_criteria=[]
        for criterion in goal_spec['criteria']:
            if criterion['kind']=='field_value':
                if criterion['field'] not in aliases:fail('原字段标准未映射到必需匿名字段，不能省略')
                item={'kind':'field_value','alias':aliases[criterion['field']]}
                if item not in field_criteria:field_criteria.append(item)
        cloud['goal']={'required_aliases':[aliases[field] for field in goal_spec['required_fields']],
            'field_criteria':field_criteria,'minimum_verification':goal_spec['evidence']['minimum_verification'],
            'time_constraint':'required' if time_required else 'none'}
        necessary=necessary_pairs(cloud)
        if len(necessary)>5:fail(CAPACITY_ERROR)
        capacity={'required_count':len(necessary),'max_steps':5}
    if len(json.dumps(cloud))>30000:fail('结构投影超过本次白名单额度，不能截断生成')
    return {'source_proof':proof,'scope':scope(db,p,task),'cloud_input':cloud,'field_mapping':mapping,'patterns':patterns,
        **({'capacity':capacity} if version==VERSION else {})}


def preview(db,p,task,model_id):
    cases._manage(db,p,task)
    cap=model(db,p,model_id);value=projection(db,p,task)
    request={**value,'task_id':task.id,'task_version':task.version,'origin_hash':digest(value['source_proof']),
        'model_profile_id':cap.id,'model_hash':model_hash(cap),'projection_contract_hash':CONTRACT_HASH,
        **dispatch(cap,value['cloud_input'])}
    request['preview_hash']=digest(request)
    return request,cap
def preview_view(task,request):
    return {key:request[key] for key in ['preview_hash','projection_contract_hash','origin_hash','model_hash','cloud_input','field_mapping','patterns','generation_contract','dispatch_messages_hash','dispatch_options','capacity']}|{'version':task.version,'source_error':None,'notice':NOTICE,'forbidden_content':FORBIDDEN}


def local_preview(db,p,task):
    cases._manage(db,p,task)
    value=projection(db,p,task)
    request={key:copy.deepcopy(value[key]) for key in ['source_proof','scope','field_mapping','patterns','capacity']}
    # 使用相同有限目标结构，但不建立模型配置、外发内容或云授权。
    for item in request['patterns']:
        item['pattern_id'],item['instruction']=LOCAL_PATTERNS[item['kind']]
    request.update(source_kind='local_rules',local_input=value['cloud_input'],task_id=task.id,task_version=task.version,
        origin_hash=digest(value['source_proof']),projection_contract_hash=CONTRACT_HASH,local_rules_contract=local_rules_contract())
    request['preview_hash']=digest(request)
    validate_local_request(request)
    return request


def checked_local_history_input(db,p,task,row):
    """旧任务的固定验收是输入依据；暂停、换版、撤回不重写已保存清单。"""
    request=row.data['request'];proof=request['source_proof'];accepted=task.data.get('native_goal_acceptance')
    if cases._origin(db,p,task,_historical=True)!=proof:fail('本地清单的原正式验收、原件或回执真实性无法核对')
    if (not accepted or proof['acceptance_hash']!=digest(accepted) or task.data.get('native_goal_acceptance_hash')!=digest(accepted)
        or proof['task_id']!=task.id or proof['context_hash']!=task.data.get('native_goal_context_hash')
        or proof['goal_snapshot']!=accepted.get('goal_snapshot')
        or proof['input_snapshots_hash']!=digest({key:task.data.get(key,[]) for key in ('material_snapshots','image_snapshots','scene_snapshots','comparison_scene_snapshots','audio_snapshots')})):
        fail('本地清单与原任务固定验收或资料快照不一致')
    outcome=scoped(db,Memory,proof['outcome_id'],p)
    if (outcome.id!=task.data.get('native_goal_data_receipt_id') or outcome.data.get('type')!='native_goal_outcome'
        or outcome.data.get('task_id')!=task.id or outcome.data.get('acceptance')!=accepted
        or outcome.data.get('acceptance_hash')!=digest(accepted)
        or outcome.data.get('workflow_receipts')!=task.data['workflow_runtime']['receipts']
        or proof['workflow_hash']!=digest(outcome.data['workflow_receipts'])):
        fail('本地清单与原正式归集回执的固定来源不一致')
    frozen=projection(db,p,task,VERSION,_proof=proof)
    expected_patterns=copy.deepcopy(frozen['patterns'])
    for item in expected_patterns:item['pattern_id'],item['instruction']=LOCAL_PATTERNS[item['kind']]
    if (request['local_input']!=frozen['cloud_input'] or request['scope']!=frozen['scope']
        or request['field_mapping']!=frozen['field_mapping'] or request['patterns']!=expected_patterns or request['capacity']!=frozen['capacity']):
        fail('本地清单的冻结输入与原验收结构不一致，不能显示有效结果')


def validate_local_request(request):
    keys={'source_kind','local_input','source_proof','scope','field_mapping','patterns','capacity','task_id','task_version',
        'origin_hash','projection_contract_hash','local_rules_contract','preview_hash'}
    if (set(request)!=keys or source_kind(request)!='local_rules' or request['local_rules_contract']!=local_rules_contract()
        or request['projection_contract_hash']!=CONTRACT_HASH
        or request['preview_hash']!=digest({k:v for k,v in request.items() if k!='preview_hash'})
        or request['origin_hash']!=digest(request['source_proof'])
        or request['source_proof'].get('task_id')!=request['task_id'] or type(request['task_version']) is not int or request['task_version']<1):
        fail('本地规则版本、来源、预览或请求范围无法核对')
    value=request['local_input']
    if (set(value)!={'contract_version','fields','source','criteria','consumed_templates','allowed_pairs','limitations','goal'}
        or value['contract_version']!=VERSION or value['source']!=request['scope']['source']
        or value['source'] not in {'archive','observations','either'}):fail('本地有限目标结构不属于已登记合同')
    fields=value['fields'];mapping=request['field_mapping'];goal=value['goal']
    if not isinstance(fields,list) or not 1<=len(fields)<=20 or len(mapping)!=len(fields):fail('本地字段映射为空或越界')
    if (set(goal)!={'required_aliases','field_criteria','minimum_verification','time_constraint'}
        or goal['time_constraint'] not in {'none','required'} or goal['minimum_verification'] not in CONTRACT['goal']['minimum_verification']):
        fail('本地核查目标条件不属于已登记合同')
    aliases=['F'+str(i+1) for i in range(len(fields))]
    if (len(goal['required_aliases'])!=len(aliases) or set(goal['required_aliases'])!=set(aliases)
        or len(set(item['field'] for item in mapping))!=len(mapping)
        or sorted(item['field'] for item in mapping)!=request['scope']['fields']):fail('本地必需字段与冻结范围不一致')
    pairs=[];patterns=[]
    for alias,item,field in zip(aliases,fields,mapping):
        if (set(item)!={'alias','type','templates','flags','records_bucket','priority_tags'}
            or set(field)!={'alias','field','field_label','type','templates'} or item['alias']!=alias or field['alias']!=alias
            or item['type'] not in CONTRACT['field_types'] or field['type']!=item['type'] or field['templates']!=item['templates']
            or not isinstance(field['field_label'],str) or not isinstance(item['templates'],list) or not item['templates']
            or len(set(item['templates']))!=len(item['templates']) or any(kind not in LOCAL_PATTERNS for kind in item['templates'])
            or set(item['flags'])!={'source_identity','declared_time','units','comparability'}
            or any(flag not in {'known','unknown'} for flag in item['flags'].values()) or item['records_bucket'] not in {'0','1','2+'}):
            fail('本地字段类型、未知标记或模板无法核对')
        tags=['goal_required']
        if item['type'] in {'number','integer'} and item['flags']['units']=='unknown':tags.append('numeric_unit_unknown')
        if goal['time_constraint']=='required' and item['flags']['declared_time']=='unknown':tags.append('required_time_unknown')
        if item['priority_tags']!=tags:fail('本地字段必要核查标记无法核对')
        for kind in item['templates']:
            pattern,instruction=LOCAL_PATTERNS[kind]
            pairs.append({'alias':alias,'template':kind,'pattern_id':pattern})
            patterns.append({'alias':alias,'kind':kind,'pattern_id':pattern,'instruction':instruction,'basis_flags':item['flags']})
    if value['allowed_pairs']!=pairs or request['patterns']!=patterns:fail('本地模式或固定说明不属于已登记规则')
    if any(set(item)!={'kind','alias'} or item['kind']!='field_value' or item['alias'] not in aliases for item in goal['field_criteria']):
        fail('本地字段完成标准不属于有限目标结构')
    required=necessary_pairs(value)
    if len(required)>5:fail(CAPACITY_ERROR)
    if request['capacity']!={'required_count':len(required),'max_steps':5}:fail('本地必要核查容量无法核对')


def deterministic_choices(value):
    necessary=necessary_pairs(value)
    if len(necessary)>5:fail(CAPACITY_ERROR)
    choices=[]
    for alias in value['goal']['required_aliases']:
        for kind in ['field_verification','units','source_time']:
            if (alias,kind) not in necessary:continue
            matches=[item for item in value['allowed_pairs'] if item['alias']==alias and item['template']==kind]
            if len(matches)!=1:fail('本地必要核查缺少唯一受控模式，不能省略')
            choices.append(copy.deepcopy(matches[0]))
    if len(choices)!=len(necessary):fail('本地必要核查顺序或集合无法核对')
    return {'steps':choices}


def checked_local_result(row):
    request=row.data['request'];validate_local_request(request)
    allowed={'type','native_package_id','request','request_hash','authorized_by','authorized_at','progress','history',
        'local_result','proposal','id','version','status','workspace_id','task_id','record_hash','saved_edit',
        'imported_case_id','imported_case_version','local_import'}
    forbidden={'job_id','raw_response','raw_quality','cloud_call_id','provider','model','model_profile_id','model_hash',
        'cloud_input','generation_contract','dispatch_messages','dispatch_request_messages','dispatch_schema','dispatch_messages_hash',
        'dispatch_options','dispatch_options_hash','usage','receipt','call','inflight','automatic','parent_id','dispatch_authorized'}
    if (set(row.data)-allowed or any(key in row.data for key in forbidden) or row.status not in {'needs_review','declined','cancelled','imported'} or request['task_id']!=row.task_id
        or request['scope']['native_package_id']!=row.data['native_package_id']):fail('本地清单不能包含云调用、作业或其它来源证明')
    expected=deterministic_choices(request['local_input'])
    result=signed({'value':expected,'quality':quality(expected,request)})
    if row.data.get('local_result')!=result or row.data.get('proposal')!=signed(normalize(expected,request)):
        fail('本地封存结果、必要核查顺序或原清单无法核对')
    edit=row.data.get('saved_edit')
    if edit is not None:
        if not isinstance(edit,dict) or edit.get('hash')!=digest({key:value for key,value in edit.items() if key!='hash'}):fail('本地人工改稿摘要无法核对')
        fields={item['field']:item for item in request['field_mapping']}
        steps=edit['steps']
        if (not 1<=len(steps)<=5 or len({item['id'] for item in steps})!=len(steps)
            or any(item['field'] not in fields or item['kind'] not in fields[item['field']]['templates']
                or item['field_label']!=fields[item['field']]['field_label'] or item.get('pattern_ids')!=[] or item.get('origin')!='human_edit' for item in steps)):
            fail('本地人工改稿字段、模板或人改来源无法核对')
        actual=quality({'steps':[{'alias':fields[item['field']]['alias'],'template':item['kind']} for item in steps]},request)
        if actual['status']!='passed' or edit.get('quality')!=actual:fail('本地人工改稿的必要核查覆盖无法核对')
    if row.status=='imported':
        imported=row.data.get('local_import')
        if (not edit or not isinstance(imported,dict) or set(imported)!={'case_id','case_version','case_record_hash','case_content_hash','hash'}
            or not isinstance(imported.get('case_id'),str) or not imported['case_id']
            or type(imported.get('case_version')) is not int or imported['case_version']<1
            or any(not isinstance(imported.get(key),str) or len(imported[key])!=64 or any(char not in '0123456789abcdef' for char in imported[key])
                for key in ['case_record_hash','case_content_hash','hash'])
            or imported.get('hash')!=digest({k:v for k,v in imported.items() if k!='hash'})
            or imported.get('case_id')!=row.data.get('imported_case_id') or imported.get('case_version')!=row.data.get('imported_case_version')
            or imported.get('case_content_hash')!=digest(local_case_content(edit))):fail('本地方法首次导入的正文或版本证明无法核对')
    return result


def local_case_content(edit):
    return {key:copy.deepcopy(edit[key]) for key in ['title','summary','limits']}|{
        'steps':[{key:item[key] for key in ['id','kind','field','instruction']} for item in edit['steps']]}


def checked_local_case_origin(db,p,case,task):
    """导入后的候选是API不可变来源；静态读取不反向锁建议，也不重授未来权限。"""
    try:return _checked_local_case_origin(db,p,case,task)
    except (KeyError,TypeError,ValueError,IndexError) as error:
        raise HTTPException(409,'本地程序经验来源的封存结构无法核对') from error


def _checked_local_case_origin(db,p,case,task):
    origin=case.data['suggestion_origin']
    if not isinstance(origin,dict) or set(origin)!={'source_kind','suggestion_id','request_hash','local_result_hash','saved_edit_hash'} or origin['source_kind']!='local_rules':
        fail('本地程序经验来源标记或范围无法核对')
    row=checked(db,p,origin['suggestion_id'],task)
    if source_kind(row.data['request'])!='local_rules' or row.status!='imported':fail('本地程序经验没有对应已导入清单')
    checked_local_history_input(db,p,task,row)
    result=checked_local_result(row);edit=row.data.get('saved_edit');imported=row.data['local_import']
    expected={'source_kind':'local_rules','suggestion_id':row.id,'request_hash':row.data['request_hash'],
        'local_result_hash':result['hash'],'saved_edit_hash':edit['hash']}
    if (origin!=expected or row.data['native_package_id']!=case.data['native_package_id']
        or row.data['request']['scope']!=case.data['scope'] or row.data['request']['source_proof']!=case.data['source_proof']
        or imported['case_id']!=case.id or case.version<imported['case_version'] or case.data['content']!=local_case_content(edit)
        or case.data['content_hash']!=imported['case_content_hash']):fail('本地程序经验与原清单、保存改稿或首次导入正文不一致')
    if case.version==imported['case_version']:
        anchored=case.data['record_hash']==imported['case_record_hash']
    else:
        anchored=any(item.get('revision')==imported['case_version'] and item.get('prior_record_hash')==imported['case_record_hash']
            and item.get('suggestion_origin')==expected and item.get('content_hash')==imported['case_content_hash'] for item in case.data.get('draft_revisions',[]))
        anchored=anchored or any(item.get('prior_record_hash')==imported['case_record_hash'] for item in case.data.get('history',[]))
    if not anchored:fail('本地程序经验首次导入的版本链无法核对')


def local_preview_view(task,request):
    choices=deterministic_choices(request['local_input'])
    return {key:request[key] for key in ['source_kind','preview_hash','projection_contract_hash','origin_hash','local_input',
        'field_mapping','patterns','capacity','local_rules_contract']}|{'version':task.version,'source_error':None,
        'local_quality':signed(quality(choices,request)),**normalize(choices,request)}


def new_local_record(db,p,task,request):
    choices=deterministic_choices(request['local_input'])
    row=Memory(workspace_id=p.workspace_id,task_id=task.id,status='needs_review',text='本地必要核查清单',data={})
    db.add(row);db.flush()
    row.data={'type':TYPE,'native_package_id':task.package_id,'request':request,'request_hash':digest(request),
        'authorized_by':p.user_id,'authorized_at':now().isoformat(),'progress':'本地清单已保存；请核对、修改并明确导入，尚未提交或审核',
        'history':[],'local_result':signed({'value':choices,'quality':quality(choices,request)}),'proposal':signed(normalize(choices,request))}
    seal(row);checked_local_result(row)
    return row
def journal(task):
    commands=copy.deepcopy(task.data.get('native_suggestion_commands',[]))
    if commands and digest(commands)!=task.data.get('native_suggestion_commands_hash'):fail('原建议操作恢复记录摘要不一致')
    return commands
def command(db,p,task,body,operation,target=None):
    cases._manage(db,p,task);commands=journal(task)
    fingerprint=digest({'operation':operation,'target':target,'body':command_body(body)})
    old=next((item for item in commands if item['request_id']==body.request_id),None)
    if old:
        if old['hash']!=fingerprint or old['actor_id']!=p.user_id:fail('原请求编号属于不同内容、目标或作者')
        if old['status']=='abandoned':fail('原请求已明确作废，不能再次提交')
        return commands,old
    check_version(task,body.expected_version)
    if len(commands)>=200:fail('原建议操作记录超过200项，请保留历史并扩展分页存储')
    return commands,None
def finish(db,p,task,body,operation,commands,target=None,result=None):
    commands.append({'request_id':body.request_id,'hash':digest({'operation':operation,'target':target,'body':command_body(body)}),
        'actor_id':p.user_id,'operation':operation,'target':target,'status':'saved','result':result,'at':now().isoformat()})
    task.data={**task.data,'native_suggestion_commands':commands,'native_suggestion_commands_hash':digest(commands)}
    task.version+=1;audit(db,p,'native_suggestion.'+operation,task.id,{'request_id':body.request_id,'suggestion_id':result})
def new_record(db,p,task,request,automatic=None,parent_id=None):
    row=Memory(workspace_id=p.workspace_id,task_id=task.id,status='queued',text='待核查方法建议',data={})
    db.add(row);db.flush()
    row.data={'type':TYPE,'native_package_id':task.package_id,'request':request,'request_hash':digest(request),
        'authorized_by':p.user_id,'authorized_at':now().isoformat(),'parent_id':parent_id,'automatic':automatic,
        'progress':'外发结构与授权已保存，等待生成建议','history':[],'inflight':False}
    job=Job(workspace_id=p.workspace_id,kind=JOB,idempotency_key='native-method-suggestion:'+row.id,
        data={'suggestion_id':row.id,'task_id':task.id,'actor_id':p.user_id})
    db.add(job);db.flush();row.data={**row.data,'job_id':job.id};seal(row)
    return row
def source_current(db,p,task,row,lock=False,for_dispatch=False):
    request=row.data['request']
    if digest(request)!=row.data['request_hash']:fail('原外发白名单合同或摘要无法核对')
    if source_kind(request)=='local_rules':
        checked_local_result(row)
        if for_dispatch:fail('本地清单不能作为云调用授权')
        if origin(db,p,task,lock)!=request['source_proof'] or scope(db,p,task)!=request['scope']:fail('原正式验收来源或未来血缘已改变')
        current=local_preview(db,p,task)
        if any(current[key]!=request[key] for key in ['local_input','field_mapping','patterns','capacity','local_rules_contract']):
            fail('原本地核查结构或规则已改变，请重新核对预览')
        return None
    version=request_version(request)
    if origin(db,p,task,lock)!=request['source_proof'] or scope(db,p,task)!=request['scope']:fail('原正式验收来源或未来血缘已改变')
    cap=model(db,p,request['model_profile_id'])
    if lock:
        cap=db.scalar(select(Capability).where(Capability.id==cap.id,Capability.workspace_id==p.workspace_id)
            .with_for_update(read=True).execution_options(populate_existing=True))
        model(db,p,cap.id)
    if model_hash(cap)!=request['model_hash']:fail('原授权模型版本或权限已改变')
    checked_generation(request,for_dispatch=for_dispatch,cap=cap)
    # Task自己的编辑版本可以改变，但原来源与外发内容不能被替换。
    current=projection(db,p,task,version=version)
    if any(current[key]!=request[key] for key in ['cloud_input','field_mapping','patterns']+(['capacity'] if version==VERSION else [])):
        fail('原结构投影已改变，请重新预览授权')
    return cap
def checked_response(db,p,row):
    if source_kind(row.data['request'])=='local_rules':return checked_local_result(row)
    raw=row.data.get('raw_response')
    if not raw or digest({k:v for k,v in raw.items() if k!='hash'})!=raw.get('hash'):fail('原付费响应摘要无法核对')
    receipt=raw['receipt'];call=scoped(db,CloudCall,receipt['call_id'],p)
    binding={'suggestion_id':row.id,'job_id':row.data['job_id'],'task_id':row.task_id,'request_hash':row.data['request_hash']}
    if (call.id!=row.data.get('cloud_call_id') or call.capability_id!=row.data['request']['model_profile_id']
        or call.status!='succeeded' or call.data.get('actor_id')!=row.data['authorized_by'] or call.data.get('native_suggestion')!=binding
        or any(call.data.get('receipt',{}).get(key)!=receipt.get(key) for key in ['provider','model','usage','request_id','call_id'])):
        fail('保存响应与真实云调用范围、用量或供应商回执不一致')
    if request_version(row.data['request'])==VERSION and (call.data.get('request_hash')!=row.data['request']['dispatch_messages_hash']
        or call.data.get('dispatch_options_hash')!=digest(row.data['request']['dispatch_options'])):
        fail('保存响应与原实际发送消息或参数摘要不一致')
    return raw
def automatic_current(db,p,task,row,lock=False):
    automatic=row.data.get('automatic')
    if not automatic:return
    policy=checked(db,p,automatic['policy_id'],lock=lock,kind=POLICY)
    if policy.version!=automatic['policy_version'] or policy.data['record_hash']!=automatic['policy_hash']:fail('原自动授权版本已改变或停用')
    if digest(scope(db,p,task))!=policy.data['scope_hash']:fail('自动授权不属于原范围')
    valid_policy(db,p,policy,lock)
def normalize(value,request):
    local=source_kind(request)=='local_rules'
    if local:validate_local_request(request)
    version=VERSION if local else request_version(request)
    version_patterns=LOCAL_PATTERNS if local else V1_PATTERNS if version==V1 else PATTERNS
    try:proposal=Proposal.model_validate(value)
    except ValidationError as error:raise HTTPException(409,'模型结构输出不符合受控方法合同，原响应保留') from error
    allowed=request['local_input' if local else 'cloud_input']['allowed_pairs'];steps=[];seen=set()
    for index,choice in enumerate(proposal.steps,1):
        if choice.model_dump() not in allowed:fail('模型选择了未授权的字段、模板或模式，原响应保留')
        pair=(choice.alias,choice.template)
        if pair in seen:fail('模型建议出现重复核查项，原响应保留')
        seen.add(pair);field=next(item for item in request['field_mapping'] if item['alias']==choice.alias)
        steps.append({'id':'item'+str(index),'kind':choice.template,'field':field['field'],'field_label':field['field_label'],
            'instruction':version_patterns[choice.template][1],'pattern_ids':[choice.pattern_id]})
    return {'title':'本地必要核查清单' if local else '核对当前资料与原业务标准',
        'summary':'依据已验收任务的目标结构按固定本地规则生成；请对照本次当前资料核查' if local else '由已验收任务的有限结构生成；请核对这些额外检查是否有用',
        'limits':'只作待核查方法建议，不复制旧事实、不证明现场状态、不自动执行或发布','steps':steps}


def quality(value,request):
    local=source_kind(request)=='local_rules'
    if not local and request_version(request)==V1:return None
    data=request['local_input' if local else 'cloud_input'];necessary=necessary_pairs(data)
    selected={(item['alias'],item['template']) for item in value['steps']}
    missing=[pair for pair in data['allowed_pairs'] if (pair['alias'],pair['template']) in necessary-selected]
    return {'status':'insufficient' if missing else 'passed','required_count':len(necessary),
        'covered_count':len(necessary&selected),'missing_required_pairs':copy.deepcopy(missing)}


def checked_quality(row):
    saved=row.data.get('raw_quality')
    if saved is None:return None
    raw=row.data.get('raw_response')
    if not raw:fail('原答核查覆盖摘要缺少保存响应，请保留记录待核')
    normalize(raw['value'],row.data['request'])
    actual=quality(raw['value'],row.data['request'])
    if actual is None or saved!=signed(actual):
        fail('原答核查覆盖摘要无法核对，请保留原响应待核')
    return saved


def partial_editable(row):
    if row.status!='failed' or row.data.get('error_code')!='insufficient_required_checks':return False
    raw_quality=checked_quality(row)
    if not raw_quality or raw_quality['status']!='insufficient' or row.data.get('proposal')!=signed(normalize(row.data['raw_response']['value'],row.data['request'])):
        fail('合法缺项原答与封存建议不一致，不能放宽人工改稿入口')
    return True


def save_proposal_result(row,result,value,restored=False):
    raw_quality=quality(value,row.data['request'])
    if raw_quality and raw_quality['status']=='insufficient':
        row.status='failed';row.data={**row.data,'inflight':False,'proposal':signed(result),'raw_quality':signed(raw_quality),
            'error_code':'insufficient_required_checks','error':'原模型建议缺少必要核查，原响应与用量保留；可明确人工补齐并另存改稿',
            'progress':'原答核查不完整，尚未导入；人工补齐不计模型通过'}
    else:
        row.status='needs_review';row.data={**row.data,'inflight':False,'proposal':signed(result),
            'raw_quality':signed(raw_quality) if raw_quality else None,'error_code':None,'error':None,
            'progress':'已从保存响应恢复，本次没有云调用' if restored else '方法建议已保存，请修改并明确导入草稿；尚未提交或审核'}


def history(row,operation,reason):
    row.data={**row.data,'history':[*row.data['history'],{'operation':operation,'reason':reason,'at':now().isoformat(),'version':row.version+1}]}
    row.version+=1;seal(row)
def dispatch_identity(row):
    return {'suggestion_id':row.id,'job_id':row.data['job_id'],
        'task_id':row.task_id,'request_hash':row.data['request_hash']}


def checked_not_sent(db,p,row):
    proof=row.data.get('dispatch_not_sent')
    if proof is None:return None
    from .cloud_dispatch_v130 import validate_not_sent_proof
    try:
        validate_not_sent_proof(proof,dispatch_identity(row),p.workspace_id,
            row.data['authorized_by'],row.data['request']['model_profile_id'])
    except (ValueError,TypeError,KeyError) as error:
        raise HTTPException(409,'原调用未发送证明无法核对，请保留记录待核') from error
    if row.data.get('inflight') or row.data.get('raw_response'):
        fail('原调用未发送证明与响应状态不一致')
    original_job=scoped(db,Job,row.data['job_id'],p)
    if (original_job.kind!='generate_native_method_suggestion'
        or original_job.data.get('suggestion_id')!=row.id
        or original_job.data.get('task_id')!=row.task_id
        or original_job.data.get('actor_id')!=row.data['authorized_by']):
        fail('原调用未发送证明与原作业不一致')
    known_id=row.data.get('cloud_call_id')
    if known_id is not None and known_id!=proof['call_id']:
        fail('原调用未发送证明与预算关联不一致')
    if proof['reservation_request_hash'] is not None and proof['reservation_request_hash']!=row.data['request']['dispatch_messages_hash']:
        fail('原调用未发送证明不属于原消息')
    if proof['call_id'] is not None:
        call=scoped(db,CloudCall,proof['call_id'],p)
        if (call.capability_id!=row.data['request']['model_profile_id']
            or call.data.get('actor_id')!=row.data['authorized_by']
            or call.data.get('request_hash')!=proof['reservation_request_hash']):
            fail('原调用未发送证明与真实预算记录不一致')
        expected_dispatch={'schema':'cloud-dispatch-v130','identity':proof['identity'],
            'attempt_id':proof['attempt_id'],'reservation_request_hash':proof['reservation_request_hash']}
        if call.data.get('gateway_dispatch')!=expected_dispatch:
            fail('原调用未发送证明不属于实际预留的网关尝试')
        linked=call.data.get('native_suggestion')
        if linked is not None and linked!=dispatch_identity(row):
            fail('原调用未发送证明借用了其他建议的预算记录')
        if call.status=='succeeded' or call.data.get('receipt'):
            fail('原调用未发送证明与已收到的供应商回执矛盾')
    return proof


def fail_job(db,p,job,reason,*,not_sent=None):
    row=checked(db,p,job.data['suggestion_id'],lock=True)
    if row.status in {'cancelled','declined','imported','needs_review'}:return
    if not_sent is not None:
        row.data={**row.data,'dispatch_not_sent':copy.deepcopy(not_sent),'inflight':False}
        checked_not_sent(db,p,row)
    unknown=row.data.get('inflight') and not row.data.get('raw_response') and (row.data.get('dispatch_authorized') or not row.data.get('cloud_call_id'))
    row.status='response_unknown' if unknown else 'failed'
    progress='本次未发送到云模型，原请求保留；预算记录单独核对，不会自动重发' if not_sent is not None else '建议未完成，原授权与响应回执保留；不会自动重发云请求'
    row.data={**row.data,'error':reason,'progress':progress};history(row,'failed',reason)


def process(db,p,job):
    task=scoped(db,Task,job.data['task_id'],p,True)
    row=checked(db,p,job.data['suggestion_id'],task,True)
    if source_kind(row.data['request'])!='cloud_model':fail('本地清单不属于云生成作业')
    if row.data['job_id']!=job.id or row.data['authorized_by']!=job.data['actor_id']:fail('后台作业与建议原授权不一致')
    if row.status in {'cancelled','declined','imported','needs_review'}:return
    if row.data.get('dispatch_not_sent') is not None:
        # A committed no-send outcome is terminal even if its worker crashed
        # before committing Job.failed. A new lease is not retry authorization.
        checked_not_sent(db,p,row)
        if row.status!='failed':fail('原未发送结果状态不一致，不能重新派发')
        return
    cases._manage(db,p,task)
    cached=row.data.get('raw_response')
    if not cached and (row.data.get('inflight') or job.data.get('recovery_only')):
        fail_job(db,p,job,'上次响应尚未保存，可能已计费；需要明确另建请求，不自动重发');return
    if not cached:
        try:cap=source_current(db,p,task,row,True,for_dispatch=True);automatic_current(db,p,task,row,True)
        except HTTPException as error:fail_job(db,p,job,str(error.detail));return
        row.status='running';row.data={**row.data,'inflight':True,'error':None,'progress':'已固定本次外发授权，正在生成候选；尚未创建或审核方法'}
        history(row,'cloud_started','开始准备本次调用；发送和预算状态以实际网关回执为准');checkpoint(db)
        def authorize_reserved(call_id):
            # 先固定真实预算回执；撤权或停用也不丢关联，不猜测其它相同投影的调用。
            actor=db.info.pop('worker_actor',None)
            try:
                current=checked(db,p,row.id,task,True)
                if current.data.get('cloud_call_id'):fail('本次云调用已经关联，不能重复发送')
                ledger=scoped(db,CloudCall,call_id,p,True)
                if ledger.capability_id!=current.data['request']['model_profile_id'] or ledger.data['actor_id']!=current.data['authorized_by']:fail('云预算回执不属于本次授权')
                ledger.data={**ledger.data,'native_suggestion':{'suggestion_id':current.id,'job_id':job.id,'task_id':task.id,'request_hash':current.data['request_hash']}}
                current.data={**current.data,'cloud_call_id':call_id};seal(current);checkpoint(db)
            finally:
                if actor is not None:db.info['worker_actor']=actor
            # 最后发送线性点：预算登记后重新锁定来源、模型、真实权限和自动授权。
            db.expire_all();current_task=scoped(db,Task,task.id,p,True)
            current=checked(db,p,row.id,current_task,True)
            if current.status!='running':fail('原建议已经停止，未开始新的云发送')
            cases._manage(db,p,current_task);source_current(db,p,current_task,current,True,for_dispatch=True);automatic_current(db,p,current_task,current,True)
            ledger=scoped(db,CloudCall,call_id,p,True)
            if ledger.data.get('request_hash')!=current.data['request']['dispatch_messages_hash'] or ledger.data.get('dispatch_options_hash')!=digest(current.data['request']['dispatch_options']):
                fail('实际云调用的消息或参数摘要不属于原发送授权，未发送HTTP')
            current.data={**current.data,'dispatch_authorized':True,'dispatch_authorized_at':now().isoformat()};seal(current);checkpoint(db)
        def reserved(call_id):
            try:authorize_reserved(call_id)
            except BaseException:
                # 网关用独立事务结束预留；拒绝时先释放本事务的调用/权限锁。
                # 第一阶段已经提交的真实调用关联仍保留，不能因此重发。
                db.rollback()
                raise
        started=time.monotonic()
        try:
            value,receipt=cloud_json(cap,copy.deepcopy(row.data['request']['dispatch_messages']),
                copy.deepcopy(row.data['request']['dispatch_schema']),p,on_reserved=reserved,
                dispatch_identity=dispatch_identity(row))
            receipt={**receipt,'elapsed_ms':round((time.monotonic()-started)*1000)}
        except CloudNotSent as error:
            row=checked(db,p,row.id,task,True)
            fail_job(db,p,job,str(error),not_sent=error.dispatch_proof);return
        except ModelBlocked as error:
            row=checked(db,p,row.id,task,True);fail_job(db,p,job,str(error));return
        except HTTPException as error:
            row=checked(db,p,row.id,task,True);fail_job(db,p,job,str(error.detail));return
        # 保存已付响应不是新业务执行；撤权不能丢掉费用回执。作业租约守卫仍严格保留。
        actor=db.info.pop('worker_actor',None)
        try:
            row=checked(db,p,row.id,task,True)
            if receipt['call_id']!=row.data.get('cloud_call_id'):fail('响应与原云调用回执不一致')
            row.data={**row.data,'raw_response':signed({'value':value,'receipt':receipt}),'progress':'已保存实际云响应，正在本地检查合法配对'}
            seal(row);checkpoint(db);cached=row.data['raw_response']
        finally:
            if actor is not None:db.info['worker_actor']=actor
    checked_response(db,p,row)
    db.expire_all();task=scoped(db,Task,task.id,p,True);row=checked(db,p,row.id,task,True)
    if row.status in {'cancelled','declined','imported'}:return
    source_current(db,p,task,row,True)
    try:result=normalize(cached['value'],row.data['request'])
    except HTTPException as error:
        fail_job(db,p,job,str(error.detail));return
    save_proposal_result(row,result,cached['value']);history(row,'generated','已保存原答配对与必要核查覆盖结果')


def policy_view(db,p,task):
    row=policy_row(db,p,task)
    reason='请由管理员启用或修改同范围后续任务的自动授权'
    enabled_permission=p.role=='admin'
    if enabled_permission:
        try:future_scope(db,p,task)
        except HTTPException as error:enabled_permission=False;reason=str(error.detail)
    disable_permission=bool(row and (p.role=='admin' or row.data['authorized_by']==p.user_id and p.role=='operator'))
    result={'id':None,'enabled':False,'version':None,'record_hash':None,'provider':None,'model':None,
        'projection_contract_hash':CONTRACT_HASH,'daily_limit':None,'valid_until':None,
        'generation_contract':generation_contract(),'generation_contract_hash':generation_contract()['generation_contract_hash'],
        'requires_reauthorization':False,'effective_enabled':False,'authorization_error':None,
        'notice':'只生成待审核建议，不自动发布方法；默认关闭，停用不能撤销已发请求及费用',
        'permissions':{'enable':{'allowed':enabled_permission,'reason':'' if enabled_permission else reason},
            'disable':{'allowed':disable_permission,'reason':'' if disable_permission else reason}}}
    if row:
        result.update({key:row.data.get(key) for key in ['enabled','record_hash','provider','model','daily_limit','valid_until']})
        old=row.data.get('generation_contract')
        if old is not None and (not isinstance(old,dict) or old.get('generation_contract_hash')!=digest({key:item for key,item in old.items() if key!='generation_contract_hash'})):
            fail('自动授权固定生成规则摘要无法核对，请保留记录待核')
        if old is None and row.data['projection_contract_hash']!=V1_CONTRACT_HASH:
            fail('自动授权未保存已登记的固定生成合同，请保留记录待核')
        result.update({'id':row.id,'version':row.version,'projection_contract_hash':row.data['projection_contract_hash'],
            'generation_contract':old or legacy_generation(),'generation_contract_hash':row.data.get('generation_contract_hash'),
            'requires_reauthorization':row.data['projection_contract_hash']!=CONTRACT_HASH or old!=generation_contract()})
        if result['enabled']:
            try:future_scope(db,p,task);valid_policy(db,p,row)
            except (HTTPException,ModelBlocked) as error:
                result['authorization_error']=str(error.detail) if isinstance(error,HTTPException) else str(error)
            else:result['effective_enabled']=True
    return result
def row_view(db,p,task,row):
    row=checked(db,p,row.id,task)
    request=row.data['request']
    local=source_kind(request)=='local_rules'
    if local:
        try:checked_local_history_input(db,p,task,row)
        except (KeyError,TypeError,ValueError,IndexError) as error:
            raise HTTPException(409,'本地清单的固定历史输入无法核对') from error
    local_result=checked_local_result(row) if local else None
    generation=checked_generation(request) if not local and request.get('cloud_input') else None
    raw_quality=None if local else checked_quality(row)
    source_error=''
    if not local and not request.get('cloud_input'):source_error=row.data.get('error') or '尚未形成可发送的来源与生成授权，请重新预览另建候选'
    else:
        try:source_current(db,p,task,row)
        except (HTTPException,KeyError,TypeError,ValueError) as error:source_error=str(error.detail) if isinstance(error,HTTPException) else '来源证明结构无法核对'
    manage=True;reason=''
    try:cases._manage(db,p,task)
    except HTTPException:
        manage=False;reason='当前账号为只读权限，可查看建议与历史，不能修改'
    permissions={key:{'allowed':False,'reason':reason or '当前建议状态不支持此动作'} for key in ['save','decline','cancel','retry','import','local_recover']}
    if manage:
        for key in ['save','decline']:
            editable=row.status=='needs_review' or partial_editable(row)
            permissions[key]={'allowed':editable and not source_error,'reason':source_error or ('' if editable else '当前建议不是可修改的待核查或合法缺项状态')}
        permissions['cancel']={'allowed':row.status in {'queued','running','failed','response_unknown','needs_review'},'reason':''}
        permissions['retry']={'allowed':row.status in {'failed','response_unknown'} and not source_error,'reason':source_error}
        permissions['local_recover']={'allowed':row.status=='failed' and bool(row.data.get('raw_response')) and not partial_editable(row) and not source_error,'reason':source_error or ('原答确有缺项，请人工核对补齐；重复本地恢复不会补齐模型输出' if partial_editable(row) else '')}
        permissions['import']={'allowed':row.status=='needs_review' and bool(row.data.get('saved_edit')) and not source_error,'reason':source_error or ('' if row.data.get('saved_edit') else '请先保存核对后的改稿')}
    if local:
        for key in ['retry','local_recover']:permissions[key]={'allowed':False,'reason':'本地清单已同步保存，不提供云重发或付费响应恢复'}
    proposal=(row.data.get('proposal') or {}).get('steps',[])
    content={k:row.data.get('proposal',{}).get(k,'') for k in ['title','summary','limits']}
    dispatch=None if local else checked_not_sent(db,p,row)
    raw=None if local else row.data.get('raw_response');receipt=(raw or {}).get('receipt',{})
    if raw:checked_response(db,p,row)
    known_call_id=row.data.get('cloud_call_id') or (dispatch or {}).get('call_id')
    if not raw and known_call_id:
        call=scoped(db,CloudCall,known_call_id,p)
        if call.capability_id!=row.data['request']['model_profile_id'] or call.data.get('actor_id')!=row.data['authorized_by']:fail('原云调用范围无法核对')
        receipt=call.data.get('receipt',{})
    call_status='received' if raw else 'unknown' if row.status=='response_unknown' or row.status=='cancelled' and row.data.get('inflight') else 'inflight' if row.data.get('inflight') else 'not_started'
    return {'id':row.id,'version':row.version,'task_id':task.id,'task_version':task.version,'record_hash':row.data['record_hash'],
        'status':row.status,'progress':row.data.get('progress'),'error':row.data.get('error'),'source_error':source_error or None,
        'source_kind':'local_rules' if local else 'cloud_model',
        'local_input':request.get('local_input'),'local_rules_contract':request.get('local_rules_contract'),
        'local_quality':signed(local_result['quality']) if local else None,'local_result_hash':local_result['hash'] if local else None,
        'provider':row.data['request'].get('provider'),'model':row.data['request'].get('model'),'can_manage':manage,
        'cloud_input':request.get('cloud_input'),'field_mapping':request['field_mapping'],'patterns':request['patterns'],
        'generation_contract':generation,'dispatch_messages_hash':request.get('dispatch_messages_hash'),
        'dispatch_options':request.get('dispatch_options'),'capacity':request.get('capacity'),
        'raw_quality':raw_quality,'missing_required_pairs':(raw_quality or {}).get('missing_required_pairs',[]),'error_code':row.data.get('error_code'),
        **content,'steps':proposal,'saved_edit':row.data.get('saved_edit'),'has_saved_response':bool(raw),
        'notice':'由固定本地规则生成，不调用云；清单只提示核查，未知事实仍待本次资料核实' if local else NOTICE,'forbidden_content':FORBIDDEN,'structural_only':True,
        'raw_model_content_hash':digest(raw['value']) if raw else None,'call':None if local else {'status':call_status,'usage':receipt.get('usage',{}),
            'elapsed_ms':receipt.get('elapsed_ms'),
            **({'dispatch':{'status':'not_sent','reservation_state':dispatch['reservation_state'],'proof_hash':dispatch['hash']}} if dispatch else {}),
            'cost':{'status':'unknown','reason':'usage为真实用量；费用尚无结算证明，预算预留不是实付'}},
        'history':row.data['history'],'permissions':permissions,'parent_id':row.data.get('parent_id'),'automatic':bool(row.data.get('automatic'))}


def execute(db,p,task,body,operation,target=None):
    commands,old=command(db,p,task,body,operation,target)
    if old:
        output={'version':task.version,'suggestion':row_view(db,p,task,checked(db,p,old['result'],task))} if old.get('result') else {'version':task.version,'policy':policy_view(db,p,task)}
        if operation=='import':output['experience']=cases.view(db,p,task)
        return output
    row=None;result=None
    if operation=='local_start':
        origin(db,p,task,True)
        request=local_preview(db,p,task)
        if request['preview_hash']!=body.preview_hash:fail('本地来源、目标或规则已改变，请重新核对预览')
        row=new_local_record(db,p,task,request)
    elif operation=='start':
        request,cap=preview(db,p,task,body.model_profile_id)
        if request['preview_hash']!=body.preview_hash:fail('来源、任务、模型或发送结构已改变，请重新核对预览')
        request={**request,'provider':cap.data['execution']['provider'],'model':cap.data['execution']['model']}
        row=new_record(db,p,task,request)
    elif operation=='policy':
        current=policy_row(db,p,task,True)
        if current:
            if body.expected_policy_version!=current.version or body.policy_record_hash!=current.data['record_hash']:fail('自动授权版本已改变，请重新核对')
        elif body.expected_policy_version is not None or body.policy_record_hash is not None:fail('原自动授权记录不存在')
        if body.enabled:
            if p.role!='admin':raise HTTPException(403,'只有管理员能够授权同范围未来任务自动生成建议')
            if not body.cloud_authorized or body.projection_contract_hash!=CONTRACT_HASH or body.generation_contract_hash!=generation_contract()['generation_contract_hash'] or not body.model_profile_id or 'daily_limit' not in body.model_fields_set or body.valid_days is None:fail('启用需要明确模型、白名单与生成合同、期限、每日设置和云授权')
            request,cap=preview(db,p,task,body.model_profile_id)
            if body.daily_limit is None and cap.data['execution'].get('provider')!='deepseek':fail('只有 DeepSeek 自动建议可以不设每日上限')
            if not body.preview_hash or request['preview_hash']!=body.preview_hash:fail('启用预览、来源或模型版本已改变，请重新核对授权')
            if current is None:
                current=Memory(workspace_id=p.workspace_id,task_id=task.id,status='enabled',text='同范围方法建议自动授权',data={});db.add(current);db.flush()
            else:current.version+=1
            current.status='enabled_unlimited_v2' if body.daily_limit is None else 'enabled';current.valid_until=now()+timedelta(days=body.valid_days)
            current.data={**current.data,'type':POLICY,'native_package_id':task.package_id,'scope':request['scope'],'scope_hash':digest(request['scope']),
                'enabled':True,'model_profile_id':cap.id,'model_hash':model_hash(cap),'provider':cap.data['execution']['provider'],'model':cap.data['execution']['model'],
                'authorized_by':p.user_id,'projection_contract_hash':CONTRACT_HASH,
                'generation_contract':copy.deepcopy(request['generation_contract']),'generation_contract_hash':body.generation_contract_hash,
                'daily_limit':body.daily_limit,'valid_until':current.valid_until.isoformat(),
                'history':[*current.data.get('history',[]),{'operation':'enabled','reason':body.reason,'at':now().isoformat(),'version':current.version}]}
        else:
            if not current:fail('当前范围没有可停用的自动授权')
            if p.role!='admin' and p.user_id!=current.data['authorized_by']:raise HTTPException(403,'请由管理员或原授权人停用')
            current.status='disabled';current.version+=1;current.data={**current.data,'enabled':False,
                'history':[*current.data['history'],{'operation':'disabled','reason':body.reason,'at':now().isoformat(),'version':current.version}]}
        seal(current);finish(db,p,task,body,operation,commands)
        return {'version':task.version,'policy':policy_view(db,p,task)}
    else:
        row=checked(db,p,target,task,True)
        local=source_kind(row.data['request'])=='local_rules'
        if row.version!=body.expected_suggestion_version or row.data['record_hash']!=body.record_hash:fail('方法建议版本已改变，本次请求未提交，请重新核对')
        if operation=='cancel':
            if row.status not in {'queued','running','failed','response_unknown','needs_review'}:fail('当前建议不能取消')
            row.status='cancelled';row.data={**row.data,'progress':'已取消后续处理；已发请求及付费响应仍保留'}
        elif operation=='decline':
            if row.status!='needs_review' and not partial_editable(row):fail('当前建议不能拒绝')
            row.status='declined';row.data={**row.data,'progress':'已保存拒绝理由，原建议与响应保留'}
        elif operation=='save':
            if row.status!='needs_review' and not partial_editable(row):fail('当前建议不能保存修改')
            source_current(db,p,task,row,True)
            checked_response(db,p,row)
            fields={item['field']:item for item in row.data['request']['field_mapping']}
            if len({step.id for step in body.steps})!=len(body.steps):fail('核查项编号重复')
            steps=[]
            for step in body.steps:
                if step.field not in fields or step.kind not in fields[step.field]['templates']:fail('人工改稿字段或模板不属于原登记范围')
                steps.append({**step.model_dump(),'field_label':fields[step.field]['field_label'],'pattern_ids':[], 'origin':'human_edit'})
            edited_quality=None
            if local or request_version(row.data['request'])==VERSION:
                edited_quality=quality({'steps':[{'alias':fields[item['field']]['alias'],'template':item['kind']} for item in steps]},row.data['request'])
                if edited_quality['status']!='passed':fail('人工改稿仍缺少必要核查，请保留输入并补齐后保存')
            row.data={**row.data,'saved_edit':signed({'title':body.title,'summary':body.summary,'limits':body.limits,'steps':steps,'saved_at':now().isoformat(),
                **({'quality':edited_quality} if edited_quality else {})})}
            if partial_editable(row):
                row.status='needs_review';row.data={**row.data,'progress':'人工补齐稿已独立保存，原模型缺项记录保留；尚未提交或审核'}
        elif operation=='retry':
            if local:fail('本地清单不提供云重发或付费恢复，请保留历史并重新核对本地预览')
            if row.status not in {'failed','response_unknown'}:fail('当前建议不需要恢复')
            if row.data.get('raw_response'):
                source_current(db,p,task,row,True)
                checked_response(db,p,row)
                result=normalize(row.data['raw_response']['value'],row.data['request'])
                save_proposal_result(row,result,row.data['raw_response']['value'],restored=True)
            else:
                if not body.model_profile_id or not body.preview_hash or not body.cloud_authorized or not body.acknowledge_possible_charge:fail('另建请求可能再次计费，需当前预览和明确授权')
                request,cap=preview(db,p,task,body.model_profile_id)
                if request['preview_hash']!=body.preview_hash:fail('当前外发预览已改变')
                request={**request,'provider':cap.data['execution']['provider'],'model':cap.data['execution']['model']}
                parent=row;row=new_record(db,p,task,request,parent_id=parent.id)
        elif operation=='import':
            if row.status!='needs_review':fail('当前建议不能导入')
            source_current(db,p,task,row,True)
            edit=row.data.get('saved_edit')
            if not edit or edit['hash']!=body.saved_edit_hash or digest({k:v for k,v in edit.items() if k!='hash'})!=edit['hash']:fail('请先核对并保存当前改稿')
            if local or request_version(row.data['request'])==VERSION:
                aliases={item['field']:item['alias'] for item in row.data['request']['field_mapping']}
                actual=quality({'steps':[{'alias':aliases[item['field']],'template':item['kind']} for item in edit['steps']]},row.data['request'])
                if actual['status']!='passed' or edit.get('quality')!=actual:fail('人工改稿必要核查覆盖无法核对，不能导入')
            existing=cases._state(task)['case_id']
            if existing and (not body.replace_existing or body.case_id!=existing):fail('已有草稿，请明确核对原稿后确认替换')
            if not existing and (body.case_id or body.replace_existing):fail('原草稿不存在，不能猜测替换目标')
            checked_response(db,p,row)
            draft=cases.Draft(request_id='m42-import-'+digest(body.request_id),expected_version=task.version,reason=body.reason,
                acceptance_hash=body.acceptance_hash,title=edit['title'],summary=edit['summary'],limits=edit['limits'],
                steps=[{k:item[k] for k in ['id','kind','field','instruction']} for item in edit['steps']],
                case_id=body.case_id,expected_case_version=body.expected_case_version,content_hash=body.content_hash)
            cases.execute(db,p,task,draft,'draft')
            case=cases._checked_case(db,p,cases._state(task)['case_id'],True)
            method_origin={'suggestion_id':row.id,'request_hash':row.data['request_hash'],'saved_edit_hash':edit['hash']}
            if local:method_origin.update(source_kind='local_rules',local_result_hash=checked_local_result(row)['hash'])
            else:method_origin['raw_model_content_hash']=digest(row.data['raw_response']['value'])
            case.data={**case.data,'suggestion_origin':method_origin}
            cases._seal_case(case)
            row.status='imported';row.data={**row.data,'imported_case_id':case.id,'imported_case_version':case.version,'progress':'已导入待核查草稿；尚未提交、批准或采用'}
            if local:row.data={**row.data,'local_import':signed({'case_id':case.id,'case_version':case.version,
                'case_record_hash':case.data['record_hash'],'case_content_hash':case.data['content_hash']})}
        history(row,operation,body.reason)
    finish(db,p,task,body,operation,commands,target,row.id if row else None)
    output={'version':task.version,'suggestion':row_view(db,p,task,row)}
    if operation=='import':output['experience']=cases.view(db,p,task)
    return output


def recover(db,p,task,body):
    cases._manage(db,p,task)
    try:original=MODELS[body.operation].model_validate(body.original_request)
    except ValidationError as error:raise HTTPException(422,'原建议请求结构不完整，不能猜测或作废') from error
    target=body.suggestion_id
    if body.operation not in {'start','local_start','policy'} and not target:raise HTTPException(422,'恢复原操作需要固定建议目标')
    if body.operation in {'start','local_start','policy'} and target:raise HTTPException(422,'此恢复操作不接受建议目标')
    if target:checked(db,p,target,task)
    commands=journal(task);fingerprint=digest({'operation':body.operation,'target':target,'body':command_body(original)})
    old=next((item for item in commands if item['request_id']==original.request_id),None)
    if old and (old['hash']!=fingerprint or old['actor_id']!=p.user_id):fail('原请求编号属于不同作者、内容或目标')
    if not old and body.action=='abandon':
        check_version(task,body.expected_version)
        commands.append({'request_id':original.request_id,'hash':fingerprint,'actor_id':p.user_id,'operation':body.operation,'target':target,'status':'abandoned','at':now().isoformat()})
        task.data={**task.data,'native_suggestion_commands':commands,'native_suggestion_commands_hash':digest(commands)};task.version+=1
        old=commands[-1];audit(db,p,'native_suggestion.request_abandoned',task.id,{'request_id':original.request_id})
    result={'saved':bool(old and old['status']=='saved'),'abandoned':bool(old and old['status']=='abandoned'),'version':task.version}
    if old and old.get('result'):result['suggestion']=row_view(db,p,task,checked(db,p,old['result'],task))
    if old and old['status']=='saved' and body.operation=='import':result['experience']=cases.view(db,p,task)
    if old and old['status']=='saved' and body.operation=='policy':result['policy']=policy_view(db,p,task)
    return result


def schedule_automatic(db,p,task):
    """验收事务只登记作业；任何提炼错误不得改变已保存原业务验收。"""
    row=policy_row(db,p,task,True)
    if not row or row.status not in {'enabled','enabled_unlimited_v2'}:return
    from .security import Principal
    author=Principal(row.data['authorized_by'],p.workspace_id,'admin','automatic-native-suggestion')
    day=str(now().astimezone(ZoneInfo('Asia/Shanghai')).date());scope_hash=row.data['scope_hash']
    key='native-method-auto:'+task.id+':'+task.data['native_goal_acceptance_hash']
    if db.scalar(select(Job.id).where(Job.workspace_id==p.workspace_id,Job.idempotency_key==key)):return
    automatic={'policy_id':row.id,'policy_version':row.version,'policy_hash':row.data['record_hash'],
        'scope_hash':scope_hash,'day':day,'quota_reserved':False}
    request=None;error=None
    try:
        cap=valid_policy(db,author,row,True)
        # 原授权按中国自然日、整个范围共同计数；换版/停启不重置。
        count=db.scalar(select(func.count()).select_from(Memory).where(Memory.workspace_id==p.workspace_id,
            Memory.data['type'].as_string()==TYPE,Memory.data['automatic']['scope_hash'].as_string()==scope_hash,
            Memory.data['automatic']['day'].as_string()==day,Memory.data['automatic']['quota_reserved'].as_boolean().is_(True)))
        if row.data['daily_limit'] is not None and count>=row.data['daily_limit']:fail('同范围今天已达到自动候选限额，换版或停启不会重置额度')
        request,_=preview(db,author,task,cap.id)
        request={**request,'provider':cap.data['execution']['provider'],'model':cap.data['execution']['model']}
        automatic['quota_reserved']=True
    except HTTPException as issue:error=str(issue.detail)
    if request is None:
        request={'source_proof':None,'scope':scope(db,p,task),'cloud_input':{},'field_mapping':[],'patterns':[],
            'model_profile_id':row.data['model_profile_id'],'model_hash':row.data['model_hash'],'projection_contract_hash':CONTRACT_HASH,
            'provider':row.data['provider'],'model':row.data['model']}
    suggestion=new_record(db,author,task,request,automatic)
    job=scoped(db,Job,suggestion.data['job_id'],p,True);job.idempotency_key=key
    if error:
        suggestion.status='failed';suggestion.data={**suggestion.data,'error':error,'progress':'自动建议未发出，原正式验收不受影响'};seal(suggestion)
        job.status='failed';job.data={**job.data,'error':error}


def acceptance_hook(db,p,task):
    try:
        with db.begin_nested():schedule_automatic(db,p,task)
    except Exception:
        # savepoint已回滚调度错误；绝不回滚原正式验收。
        reason='自动方法建议调度失败，原正式验收已保留；没有执行模型或发布方法'
        task.data={**task.data,'native_suggestion_hook_error':reason}
        try:
            with db.begin_nested():
                request={'source_proof':None,'scope':scope(db,p,task),'cloud_input':{},'field_mapping':[],'patterns':[],
                    'model_profile_id':None,'model_hash':None,'projection_contract_hash':CONTRACT_HASH,'provider':None,'model':None}
                row=new_record(db,p,task,request,{'fault_record':True})
                row.status='failed';row.data={**row.data,'error':reason,'progress':reason};seal(row)
                job=scoped(db,Job,row.data['job_id'],p,True);job.status='failed';job.data={**job.data,'error':reason}
        except Exception:
            # 存储不可用时仍在原Task保存本地故障，不伪造建议或调用回执。
            task.data={**task.data,'native_suggestion_hook_error':reason}


def listing(db,p,task,limit=20,after=None):
    stmt=select(Memory).where(Memory.workspace_id==p.workspace_id,Memory.task_id==task.id,Memory.data['type'].as_string()==TYPE)
    if after:
        old=checked(db,p,after,task);stmt=stmt.where(tuple_(Memory.created_at,Memory.id)<tuple_(old.created_at,old.id))
    rows=db.scalars(stmt.order_by(Memory.created_at.desc(),Memory.id.desc()).limit(limit+1)).all()
    allowed=True;reason=''
    try:cases._manage(db,p,task);origin(db,p,task)
    except HTTPException as error:allowed=False;reason='当前账号为只读权限，可查看建议与历史' if p.role=='viewer' else str(error.detail)
    return {'items':[row_view(db,p,task,row) for row in rows[:limit]],'next_cursor':rows[limit-1].id if len(rows)>limit else None,
        'version':task.version,'policy':policy_view(db,p,task),'permissions':{key:{'allowed':allowed,'reason':reason} for key in ['start','local_start']}}
