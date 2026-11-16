"""Closed readonly pixel questions; scope is always injected by the server."""
import re
from datetime import date, timedelta
from typing import Literal
from uuid import UUID
from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator, field_validator
from .contracts import Contract, digest

SCHEMA='pixel-question-v252'
FAMILY='pixel_question_v252'
FACET='received_pixel_machine_results_v250'
OPERATIONS=['read','count','sum','mean','min','max']
METRICS=['candidate_pixels','common_valid_pixels','unknown_pixels']
GROUPS=['none','registration_day','task_id','entity_id','human_completion']
RECEPTIONS=['any','registered','received','waiting_data']
HUMANS=['any','has_completed_receipt','no_completed_receipt']
STATUS=['queued','running','succeeded','needs_clarification','blocked_model','blocked_evidence','blocked_integrity','failed','cancelled','unknown_model']
INVOCATION_STATUS=['created','running','succeeded','failed','unknown','cancelled','blocked_model','blocked_evidence','waiting_receipt']
HASH=r'^[a-f0-9]{64}$'
UUID_RE=r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'

class PreviewInput(Contract):
    schema: Literal['pixel-question-preview-input-v252']='pixel-question-preview-input-v252'
    release_id: StrictStr
    task_id: StrictStr|None=None
    entity_id: StrictStr|None=None
    question: StrictStr=Field(min_length=1,max_length=4000)
    model_profile_id: StrictStr
    options_hash: StrictStr=Field(pattern=HASH)
    @model_validator(mode='after')
    def ids(self):
        for value in (self.release_id,self.task_id,self.entity_id,self.model_profile_id):
            if value is not None and str(UUID(value))!=value:raise ValueError('资源编号必须是标准UUID')
        self.question.encode('utf-8',errors='strict')
        if not self.question.strip():raise ValueError('请填写问题')
        return self

class Submit(PreviewInput):
    schema: Literal['pixel-question-submit-v252']='pixel-question-submit-v252'
    request_id: StrictStr=Field(min_length=12,max_length=100)
    preview_hash: StrictStr=Field(pattern=HASH)
    confirm_cloud: Literal[True]
    @field_validator('request_id')
    @classmethod
    def nonce_unicode(cls,value):
        value.encode('utf-8',errors='strict');return value
    @field_validator('confirm_cloud',mode='before')
    @classmethod
    def strict_consent(cls,value):
        if type(value) is not bool or value is not True:raise ValueError('须明确同意这次规划发送')
        return value
    @model_validator(mode='after')
    def consent(self):
        if type(self.confirm_cloud) is not bool:raise ValueError('须明确同意这次规划发送')
        return self

class Command(Contract):
    schema: Literal['pixel-question-command-v252']='pixel-question-command-v252'
    request_id: StrictStr=Field(min_length=12,max_length=100)
    version: StrictInt=Field(ge=1)
    reason: StrictStr=Field(min_length=1,max_length=2000)
    expected_request_hash: StrictStr=Field(pattern=HASH)
    operation: Literal['cancel','retry']
    @model_validator(mode='after')
    def valid_unicode(self):
        self.request_id.encode('utf-8',errors='strict');self.reason.encode('utf-8',errors='strict');return self


def object_schema(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}

def text_schema(maximum=4000,nullable=False):
    return {'type':['string','null'] if nullable else 'string','maxLength':maximum,**({} if nullable else {'minLength':1})}

def array_schema(item,maximum,minimum=0):
    return {'type':'array','items':item,'minItems':minimum,'maxItems':maximum}

def enum_schema(values,nullable=False):return {'enum':([None]+list(values)) if nullable else list(values)}

def time_schema():
    days={'type':['array','null'],'items':{'type':'string','pattern':r'^\d{4}-\d{2}-\d{2}$'},'minItems':1,'maxItems':31,'uniqueItems':True}
    return object_schema({'basis':{'const':'machine_result_registered'},'registration_days':days,'timezone':{'const':'Asia/Shanghai'}})

def filters_schema():
    return object_schema({'reception':enum_schema(RECEPTIONS),'human_completion':enum_schema(HUMANS)})

def intent_schema():
    return object_schema({'operation':enum_schema(OPERATIONS),'metric':enum_schema(METRICS,True),
        'group_by':enum_schema(GROUPS),'time':time_schema(),'filters':filters_schema(),
        'grounding':object_schema({'operation_quote':text_schema(),'metric_quote':text_schema(nullable=True),
            'time_quote':text_schema(nullable=True),'filter_quotes':array_schema(text_schema(),4)}),
        'limitations':array_schema(text_schema(2000),8)})

def model_result_schema():
    return {'oneOf':[object_schema({'status':{'const':'candidate'},'ir':intent_schema(),'explanation':text_schema(2000)}),
        object_schema({'status':{'const':'needs_clarification'},'questions':array_schema(text_schema(2000),8,1),
            'limitations':array_schema(text_schema(2000),8)})]}

def ir_schema():
    return object_schema({'schema':{'const':'pixel-question-ir-v252'},'source_facet':{'const':FACET},
        'scope':object_schema({'instance_id':{'type':'string','pattern':UUID_RE},
            'authorization_release_id':{'type':'string','pattern':UUID_RE},
            'task_id':{'type':['string','null'],'pattern':UUID_RE},'entity_id':{'type':['string','null'],'pattern':UUID_RE}}),
        **intent_schema()['properties']})

def tool_input_schema():
    return object_schema({'query_id':{'type':'string','pattern':UUID_RE},'plan_hash':{'type':'string','pattern':HASH}})

def answer_schema():
    identifier={'type':'string','pattern':UUID_RE}; sha={'type':'string','pattern':HASH}
    nullable_sha={'type':['string','null'],'pattern':HASH}; integer={'type':'integer','minimum':0,'maximum':1000}
    group=object_schema({'key':text_schema(nullable=True),'run_ids':array_schema(identifier,1000),
        'matched_records':integer,'value_text':text_schema(nullable=True),'value_kind':enum_schema(['integer','rational','none']),
        'numerator':{'type':['string','null'],'pattern':r'^-?(0|[1-9]\d*)$'},
        'denominator':{'type':['integer','null'],'minimum':1}})
    ref=object_schema({'run_id':identifier,'task_id':identifier,'entity_id':identifier,'producer_release_id':identifier,
        'result_hash':nullable_sha,'receipt_hash':nullable_sha,'human_receipt_hashes':array_schema(sha,1000),
        'registered_at':{'type':['string','null']}})
    return object_schema({'schema':{'const':'pixel-question-answer-v252'},'query_id':identifier,'request_hash':sha,
        'plan_hash':sha,'source_snapshot_hash':sha,'operation':enum_schema(OPERATIONS),'metric':enum_schema(METRICS,True),
        'group_by':enum_schema(GROUPS),'time':time_schema(),'filters':filters_schema(),
        'coverage':object_schema({'complete':{'const':True},'scanned_records':integer,'matched_records':integer,
            'unknown_registration_day_excluded':integer,'limit':{'const':1000}}),
        'state_counts':object_schema({key:integer for key in ['processing_records','registered_results','received_results',
            'registered_waiting','unregistered_waiting','producer_failed']}),
        'groups':array_schema(group,1000),'references':array_schema(ref,1000),
        'unit':enum_schema(['processing_records','registered_machine_results','received_machine_results','waiting_machine_results','model_result_pixels','none']),
        'metrics_basis':{'const':'independently_received_results_only'},'time_basis':{'const':'machine_result_registered'},
        'human_basis':{'const':'completed_task_receipts_at_query_snapshot'},'candidate_only':{'const':True},
        'not_authoritative':{'const':True},'field_truth_verified':{'const':False},'source_time_verified':{'const':False},
        'limitations':array_schema(text_schema(2000),32),'answer_hash':sha})

def preview_input(body):
    value=body.model_dump(mode='json') if hasattr(body,'model_dump') else dict(body)
    return PreviewInput.model_validate({key:value[key] for key in PreviewInput.model_fields if key!='schema'}).model_dump(mode='json')

def validate_intent(value,question,scope,*,reference_day=None):
    from .services import validate
    validate(model_result_schema(),value)
    if value['status']=='needs_clarification':return None
    ir=value['ir']; g=ir['grounding']
    quotes=[g['operation_quote'],g['metric_quote'],g['time_quote'],*g['filter_quotes']]
    if any(q is not None and (not q.strip() or q not in question) for q in quotes):raise ValueError('规划的逐字依据不在原问题中，请换一种明确的问法')
    if ir['operation'] in {'read','count'}:
        if ir['metric'] is not None:raise ValueError('读取或计数不能冒充像素计算')
    elif ir['metric'] not in METRICS or ir['filters']['reception']!='received' or g['metric_quote'] is None:
        raise ValueError('像素计算必须明确指标并只使用已接收的成果')
    if re.search(r'拍摄|采集日期|采集时间|公顷|平方|亩|面积|火灾|采伐|现场|真实变化|准确率|召回率|达标',question):
        raise ValueError('本查询只能核对模型候选成果，无法判断拍摄时间、地理面积、现场事实或模型质量')
    tokens={'count':r'统计|多少|数量|几条|几份|计数|总数','sum':r'汇总|累计|加总|总量|总数|合计|总和|求和|总共|一共',
        'mean':r'平均|均值','min':r'最小|最少|最低','max':r'最大|最多|最高','read':r'查看|查询|读取|列出|看|哪些|什么|信息|结果|成果'}
    if not re.search(tokens[ir['operation']],g['operation_quote']):raise ValueError('计算方式没有明确的原问题依据')
    metric_tokens={'candidate_pixels':r'候选|变化像素','common_valid_pixels':r'有效|共同有效','unknown_pixels':r'未知|无效'}
    if ir['metric'] and not re.search(metric_tokens[ir['metric']],g['metric_quote']):raise ValueError('像素指标与原问题不符')
    # A count of analysis reports is different from the pixel amount in them.
    # Do not reject report counts merely because the report name contains 像素.
    record_count=bool(re.search(r'(?:几|多少)\s*(?:份|条|次|个)[^。；！？]*?(?:成果|记录|分析|报告|任务)|(?:成果|记录|报告|分析(?:处理)?)(?:的)?(?:数量|数目|份数|条数|次数|总数)|(?:成果|记录|报告)(?:目前|现在)?(?:总共有|一共有|一共|总共|共有|共计|共|有)?(?:多少|几)\s*(?:份|条|次|个)',question))
    pixel_amount=bool(re.search(r'像素(?:的)?(?:累计|汇总|加总|合计)?(?:总数|数量|总量|数目|总和|之和)|像素(?:一共|共有|有|合计|累计)?(?:多少|几)|(?:多少|几)(?:个)?(?:候选变化|变化候选|共同有效|候选|变化|有效|未知)?像素|(?:累计|汇总|合计|加总)(?:的)?(?:候选变化|变化候选|共同有效|候选|变化|有效|未知)?像素',question))
    if record_count and pixel_amount:
        raise ValueError('问题同时涉及成果份数和像素数量，单次查询无法明确区分，请分开提问')
    if ir['operation']=='count' and '像素' in question and (pixel_amount or not record_count):
        raise ValueError('像素数量不能用分析成果份数代替，请明确要读取或合计的像素指标')
    if ir['operation'] not in {'read','count'} and record_count and not pixel_amount:
        raise ValueError('原问题要求分析成果或记录的份数，不能用像素计算替代')
    if ir['operation'] not in {'read','count'}:
        if re.search(r'待接收|未接收|尚未接收|等待[^。；！？]{0,40}接收',question):
            raise ValueError('原问题要求尚未接收的数据，不能改为计算已接收的像素；请先完成接收或另行提问')
        if not re.search(r'已(?:经)?(?:被|由)?(?:数据管理智能体)?接收|接收完成|收妥',question):
            raise ValueError('像素计算需明确只使用已接收的成果，请在原问题中说明接收范围')
    if re.search('今天|今日|昨天|昨日',question) and not re.search(r'登记|\d{4}-\d{2}-\d{2}',question):
        raise ValueError('请明确要查询哪个登记日；不能把今天默认为拍摄日或接收日')
    group_tokens={'registration_day':r'按.*(?:登记|日期)|每天|每日|逐日','task_id':r'按.*任务|每个任务|各任务',
        'entity_id':r'按.*对象|每个对象|各对象','human_completion':r'按.*人工|按.*核查|分.*完成人工'}
    if ir['group_by']!='none' and not re.search(group_tokens[ir['group_by']],question):
        raise ValueError('分组方式没有明确的原问题依据')
    human=ir['filters']['human_completion']
    if human!='any':
        matching=[q for q in g['filter_quotes'] if re.search('人工|核查|复核',q) and re.search('完成|核查|复核',q)]
        if not matching or (human=='no_completed_receipt' and not any(re.search('未|没有|尚无|无',q) for q in matching)) or (human=='has_completed_receipt' and all(re.search('未|没有|尚无',q) for q in matching)):
            raise ValueError('人工完成筛选缺少原问题依据')
    if ir['operation'] in {'read','count'} and ir['filters']['reception']!='any':
        reception_tokens={'registered':r'登记','received':r'已(?:经)?(?:被|由)?(?:数据管理智能体)?接收|接收完成|收妥','waiting_data':r'待接收|未接收|尚未接收|等待[^。；！？]{0,40}接收'}
        if not any(re.search(reception_tokens[ir['filters']['reception']],q) for q in g['filter_quotes']):
            raise ValueError('接收状态筛选缺少原问题依据')
    days=ir['time']['registration_days']
    if days is not None:
        if g['time_quote'] is None:raise ValueError('登记日期缺少原问题依据')
        for day in days:date.fromisoformat(day)
        literal=set(re.findall(r'\d{4}-\d{2}-\d{2}',g['time_quote']))
        relative=(days==[reference_day] and re.search('今天|今日',g['time_quote'])) or (reference_day is not None and days==[(date.fromisoformat(reference_day)-timedelta(days=1)).isoformat()] and re.search('昨天|昨日',g['time_quote']))
        if set(days)!=literal and not relative:
            raise ValueError('登记日期无法由原问题明确确定，请使用YYYY-MM-DD')
    elif g['time_quote'] is not None:raise ValueError('日期出处未形成明确的登记日选择')
    result={'schema':'pixel-question-ir-v252','source_facet':FACET,'scope':scope,**ir}
    validate(ir_schema(),result)
    return result
