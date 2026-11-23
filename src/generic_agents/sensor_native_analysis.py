"""One-use, price-bounded DeepSeek analysis of an approved native sensor packet.

Jobs are durable and not retried after dispatch. Saved HTTP answers may be
replayed locally. Nothing here controls equipment or advances a workflow.
"""
from __future__ import annotations
import base64,copy,json,hashlib
from datetime import timedelta,timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi import HTTPException
from .contracts import digest
from .db import engine
from .models import Job,Task,Capability,CloudCall,now
from .security import Principal
from .services import published,audit
from . import sensor_native_packet as packets
from . import sensor_agent_context as sensor
from . import cloud_business_reasoning_v197 as transport
from . import same_goal_advice_price_v307 as prices
from . import same_goal_advice_money_v305 as money
from . import same_goal_advice_store_v302 as advice_store

PREVIEW='sensor_native_analysis_preview_v1';ANALYSIS='sensor_native_analysis_v1';ANALYSIS_UNLIMITED='sensor_native_analysis_unlimited_v2';STOP='sensor_native_analysis_stop_v1'
ANALYSIS_KINDS=(ANALYSIS,ANALYSIS_UNLIMITED)
NOTICE='以下均为设备读数或阈值线索，尚未现场核实；不能当作现场结论，也不能触发处置。'

def preview_key(rid):return 'sensor-native-analysis-preview:'+rid
def send_key(rid):return 'sensor-native-analysis-send:'+rid

def model(db,p,identity):
    cap=db.scalar(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.id==identity).with_for_update(read=True).execution_options(populate_existing=True))
    if not cap:raise HTTPException(404,'未找到模型')
    cap=published(db,p,identity,'foundation_model');execution=cap.data.get('execution',{})
    if (execution.get('provider')!='deepseek' or execution.get('model')!='deepseek-v4-pro'
        or execution.get('thinking_mode')!='disabled' or execution.get('request_profile') is not None
        or execution.get('task')=='embedding' or execution.get('report_evidence_output') is not None
        or execution.get('secret_ref')!='DEEPSEEK_API_KEY' or 'business' not in cap.data.get('allowed_agent_roles',[])
        or cap.data.get('data_policy',{}).get('cloud_allowed') is not True):
        raise HTTPException(409,'仅支持已发布且允许业务调用的 DeepSeek 模型')
    if type(execution.get('max_tokens')) is not int or not 128<=execution['max_tokens']<=8192:raise HTTPException(409,'模型输出上限未正确配置')
    return cap

def _task(db,p,identity,*,lock=False):
    if not lock:
        row=db.scalar(select(Task).where(Task.workspace_id==p.workspace_id,Task.id==identity).with_for_update(read=True).execution_options(populate_existing=True))
        if not row:raise HTTPException(404,'任务不存在')
    task,release,business=packets.task_access(db,p,identity,live=True,lock=lock)
    if task.status in {'workflow_cancelled','cancelled'}:raise HTTPException(409,'任务已取消')
    return task,release,business

def _packet(db,p,task,identity,*,verify=True):
    row=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,Job.id==identity,Job.kind==packets.PACKET).with_for_update(read=True).execution_options(populate_existing=True))
    if not row:raise HTTPException(404,'未找到本任务证据包')
    packets.checked(row,p,task.id);value=row.data['packet']
    if value['release_id']!=task.data['instance_release_id'] or value['entity_ids']!=task.data['entity_ids'] or value['object_binding_hash']!=task.data.get('object_definition_bindings_hash'):
        raise HTTPException(409,'任务对象或发布版本已变化，请重新整理证据')
    if verify:
        records=packets.read_selection(db,p,task,value['selection'])
        if digest(records)!=digest(value['records']):raise HTTPException(409,'证据包来源已变化，请重新核对')
        for entry in value['selection']:sensor._read(db,p,task,entry['kind'],entry['id'],for_cloud=True)
    return row

def _schema(refs):
    return {'type':'object','additionalProperties':False,'required':['summary','observations','suggested_checks'],
        'properties':{'summary':{'type':'string','maxLength':4000},'observations':{'type':'array','maxItems':20,'items':{
        'type':'object','additionalProperties':False,'required':['evidence_refs','assessment','limitations'],'properties':{
        'evidence_refs':{'type':'array','minItems':1,'maxItems':20,'uniqueItems':True,'items':{'enum':refs}},
        'assessment':{'type':'string','maxLength':2000},'limitations':{'type':'string','maxLength':2000}}}},
        'suggested_checks':{'type':'array','maxItems':10,'items':{'type':'string','maxLength':1000}}}}

def _material(packet):
    keys=('object_name','device_name','field_name','value','unit','quality','freshness','freshness_note','source_timestamp','received_at','status','threshold')
    return [{'ref':'R'+str(i+1),**{k:r.get(k) for k in keys}} for i,r in enumerate(packet['records'])]

def prepare(db,p,task,packet,cap,max_output_tokens,max_cost_fen,*,skip_optional_price=False):
    execution=cap.data['execution']
    if not 128<=max_output_tokens<=execution['max_tokens']:raise HTTPException(422,'输出上限超过模型配置')
    refs=['R'+str(i+1) for i in range(len(packet.data['packet']['records']))]
    material=_material(packet.data['packet']);source_policies=[]
    for index,record in enumerate(packet.data['packet']['records']):
        source=published(db,p,record['provenance']['source_id'],'source');policy=source.data.get('data_policy',{})
        if not isinstance(policy,dict) or ('cloud_allowed' in policy and policy['cloud_allowed'] is not True):raise HTTPException(403,'来源云策略禁止或无法核对，不能发送云端')
        state='已允许' if policy.get('cloud_allowed') is True else '未设置'
        source_policies.append({'ref':'R'+str(index+1),'status':state})
        material[index]['source_cloud_policy']=state
    content={'task':task.title,'records':material,'instruction':NOTICE+'逐条引用所提供编号，说明质量和时间限制，给出人工核查建议，不得指挥设备、声称已处理或推断未知时间。'}
    raw_messages=[{'role':'user','content':json.dumps(content,ensure_ascii=False,separators=(',',':'))}]
    # JSONB reorders object keys. Preserve the exact schema insertion order as
    # text because the model gateway embeds that schema in its system message.
    schema_json=json.dumps(_schema(refs),ensure_ascii=False,sort_keys=True,separators=(',',':'))
    schema=json.loads(schema_json);prepared=transport.prepare(cap,raw_messages,schema,output_token_limit=max_output_tokens)
    if any(type(m.get('content')) is not str for m in prepared['messages']):raise HTTPException(409,'仅允许完整可核对的纯文字预览')
    pin=advice_store._pin(cap)
    price_read=None
    if not skip_optional_price:
        try:price_read=prices.current(db,p,pin)
        except HTTPException:
            if max_cost_fen is not None:raise
    price=price_read['price'] if price_read else None
    input_bound=prepared['text_bytes']+1024
    cost=money.ceil_cost_minor(input_bound,max_output_tokens,price['input_cache_miss_minor_per_million'],price['output_minor_per_million']) if price else None
    if max_cost_fen is not None and cost>max_cost_fen:raise HTTPException(409,'保守费用上限超过本次允许金额')
    quote={'currency':'CNY','maximum_cost_fen':cost,'user_limit_fen':max_cost_fen,'price_expires_at':price['expires_at'] if price else None,
        'provider_price_locked':False,'input_token_bound':input_bound,'output_token_bound':max_output_tokens,
        'input_rate_fen_per_million':price['input_cache_miss_minor_per_million'] if price else None,'output_rate_fen_per_million':price['output_minor_per_million'] if price else None}
    if max_cost_fen is None:quote['schema']='sensor-native-unlimited-quote-v2'
    return {'raw_messages':raw_messages,'messages':prepared['messages'],'options':prepared['options'],'output_schema':schema,'schema_json':schema_json,
        'prepared':prepared,'price':price,'price_basis_ref':price_read['basis_ref'] if price_read else None,'quote':quote,'model_pin':pin,
        'model':{'id':cap.id,'name':cap.name,'provider':'deepseek','model':execution['model'],'version':cap.release_version},
        'task_id':task.id,'task_version':task.version,'task_title':task.title,'entity_ids':task.data['entity_ids'],
        'release_id':task.data['instance_release_id'],'object_binding_hash':task.data.get('object_definition_bindings_hash'),
        'packet_id':packet.id,'packet_hash':packet.data['packet_hash'],'notice':NOTICE,
        'evidence_refs':material,'source_policies':source_policies,
        'record_hashes':[r['record_hash'] for r in packet.data['packet']['records']],
        'source_policy_notice':'来源未明确禁止云处理；仅本次经任务负责人逐条预览同意后发送。来源明确禁止时不会发送。'}

def check_current(db,p,task,snapshot):
    if (task.id!=snapshot['task_id'] or task.version!=snapshot['task_version'] or task.title!=snapshot['task_title']
        or task.data['entity_ids']!=snapshot['entity_ids'] or task.data['instance_release_id']!=snapshot['release_id']
        or task.data.get('object_definition_bindings_hash')!=snapshot['object_binding_hash']):raise HTTPException(409,'任务或对象范围已变化，请重新预览')
    packet=_packet(db,p,task,snapshot['packet_id']);cap=model(db,p,snapshot['model_pin']['id'])
    if packet.data['packet_hash']!=snapshot['packet_hash'] or advice_store._pin(cap)!=snapshot['model_pin']:raise HTTPException(409,'原证据或模型已变化，请重新预览')
    quote=snapshot['quote'];unlimited=quote.get('schema')=='sensor-native-unlimited-quote-v2'
    if (unlimited and quote.get('user_limit_fen') is not None) or (not unlimited and type(quote.get('user_limit_fen')) is not int):
        raise HTTPException(409,'预览用量策略无法核对')
    current=prepare(db,p,task,packet,cap,quote['output_token_bound'],quote['user_limit_fen'],skip_optional_price=unlimited)
    # Price validity is time dependent; compare the registered basis and rates,
    # not the advancing clock. Prepared wire bytes must be exactly the preview.
    if ((not unlimited and (current['price_basis_ref']!=snapshot['price_basis_ref'] or current['price']['record_hash']!=snapshot['price']['record_hash'] or current['quote']!=quote))
        or current['prepared']!=snapshot['prepared'] or current['schema_json']!=snapshot['schema_json'] or current['output_schema']!=snapshot['output_schema']):
        raise HTTPException(409,'价格或将发送内容已变化，请重新预览')
    return cap

def public_preview(row):
    s=row.data['snapshot'];return {'preview_id':row.id,'preview_hash':row.data['snapshot_hash'],'model':copy.deepcopy(s['model']),
        'messages':copy.deepcopy(s['messages']),'output_schema':copy.deepcopy(s['output_schema']),'options':copy.deepcopy(s['options']),
        'quote':copy.deepcopy(s['quote']),'notice':NOTICE,'requires_uncertainty_ack':bool(s.get('uncertain_request_ids')),
        'uncertain_request_ids':copy.deepcopy(s.get('uncertain_request_ids',[])),'source_policies':copy.deepcopy(s['source_policies']),
        'source_policy_notice':s['source_policy_notice']}

def public_analysis(row):
    d=row.data;receipt=copy.deepcopy(d.get('receipt'))
    if receipt:
        receipt={k:receipt.get(k) for k in ('provider','model','request_id','call_id','usage','http_status','usage_unavailable','finish_reason') if k in receipt}
    state=d.get('state','queued')
    if state=='dispatching' and d.get('lease_until') and now().isoformat()>=d['lease_until']:state='response_unknown'
    evidence_refs=[{**item,'packet_record_hash':d['snapshot']['record_hashes'][index]} for index,item in enumerate(d['snapshot']['evidence_refs'])]
    return {'id':row.id,'request_id':d['request_id'],'state':state,'packet_id':d['packet_id'],'model_id':d['model_id'],'evidence_refs':evidence_refs,
        'created_at':row.created_at.astimezone(timezone.utc).isoformat(),'result':copy.deepcopy(d.get('result')),'receipt':receipt,
        'cost_status':{'currency':'CNY','maximum_cost_fen':d['snapshot']['quote']['maximum_cost_fen'],'actual_cost_fen':None,
        'explanation':'真实费用以服务商账单为准；未核实账单金额。'},'notice':NOTICE,'error':d.get('error')}

def _principal(db,workspace,actor,*,lock=True):
    from .models import Membership,User
    query=select(Membership.role).join(User,User.id==Membership.user_id).where(Membership.workspace_id==workspace,Membership.user_id==actor,Membership.role.in_(['admin','operator']),User.active.is_(True))
    if lock:query=query.with_for_update(read=True)
    role=db.scalar(query)
    if not role:raise HTTPException(403,'原授权人的业务权限已失效')
    return Principal(actor,workspace,role,'sensor-native-worker')

def _recover(workspace,identity):
    """Replay only a saved first answer; never invokes a model or device."""
    from .cloud_budget import finish_tx
    with Session(engine(),expire_on_commit=False) as db:
        row=db.scalar(select(Job).where(Job.workspace_id==workspace,Job.id==identity,Job.kind.in_(ANALYSIS_KINDS)).with_for_update().execution_options(populate_existing=True))
        if not row or row.data.get('state')!='response_saved':return False
        d=row.data;capture=d.get('capture') or {};call_id=d.get('call_id')
        if not call_id or capture.get('call_id')!=call_id:return False
        try:
            raw_bytes=base64.b64decode(capture['raw_base64'],validate=True)
            if hashlib.sha256(raw_bytes).hexdigest()!=capture['raw_sha256'] or digest(capture['receipt'])!=capture['receipt_hash']:
                raise ValueError('saved_response_integrity')
            raw=raw_bytes.decode('utf-8',errors='surrogateescape')
            value,receipt=transport.parse_response(raw,d['snapshot']['output_schema'],capture['receipt'])
            state='completed';error=None;settlement='succeeded'
        except Exception:
            value=None;receipt=capture.get('receipt');state='response_invalid';error='原始回答已保存，但无法按合同解析；不会重发。';settlement='failed'
        row.data={**d,'state':state,'result':value,'receipt':receipt,'error':error};row.version+=1
        # Saved response and budget settlement commit atomically; a restart
        # cannot leave a completed result with an abandoned reservation.
        finish_tx(db,call_id,receipt,settlement);db.commit()
    return True

def process(workspace,identity):
    """Claim one queued request, dispatch at most once, and persist its first answer."""
    from .ftps_gateway_api import _lock
    from .gateway import cloud_json
    from .models import User
    with Session(engine(),expire_on_commit=False) as db:
        original=db.scalar(select(Job).where(Job.workspace_id==workspace,Job.id==identity,Job.kind.in_(ANALYSIS_KINDS)))
        if not original:return False
        if original.data.get('state')=='response_saved':return _recover(workspace,identity)
        if original.data.get('state')!='queued':return False
        rid=original.data['request_id'];_lock(db,workspace,send_key(rid))
        p=_principal(db,workspace,original.data['actor_id'],lock=False);task,_,_=_task(db,p,original.data['task_id'],lock=True)
        if _principal(db,workspace,p.user_id).role!=p.role:raise HTTPException(403,'原授权人的角色已变化')
        row=db.scalar(select(Job).where(Job.workspace_id==workspace,Job.id==identity,Job.kind.in_(ANALYSIS_KINDS)).with_for_update().execution_options(populate_existing=True))
        if not row or row.data.get('state')!='queued':return False
        if digest(row.data['snapshot'])!=row.data['snapshot_hash']:raise HTTPException(409,'已确认预览完整性无法核对')
        cap=check_current(db,p,task,row.data['snapshot']);snapshot=copy.deepcopy(row.data['snapshot'])
        row.data={**row.data,'state':'dispatching','lease_until':(now()+timedelta(minutes=5)).isoformat(),'call_id':None};row.version+=1
        audit(db,p,'sensor.native.analysis_dispatch_started',task.id,{'analysis_id':row.id,'preview_hash':row.data['snapshot_hash']});db.commit()
    # If this process dies here, dispatching is intentionally never reclaimed.
    with Session(engine(),expire_on_commit=False) as live:
        actor=Principal(p.user_id,workspace,p.role,'sensor-native-worker')
        def reserved(call_id):
            with Session(engine(),expire_on_commit=False) as journal:
                row=journal.scalar(select(Job).where(Job.workspace_id==workspace,Job.id==identity,Job.kind.in_(ANALYSIS_KINDS)).with_for_update().execution_options(populate_existing=True))
                ledger=journal.scalar(select(CloudCall).where(CloudCall.id==call_id,CloudCall.workspace_id==workspace))
                if (not row or row.data.get('state')!='dispatching' or row.data.get('call_id') is not None or not ledger
                    or row.data.get('snapshot_hash')!=digest(snapshot) or row.data.get('snapshot')!=snapshot
                    or ledger.capability_id!=cap.id or ledger.data.get('request_hash')!=snapshot['prepared']['request_hash']
                    or ledger.data.get('dispatch_options_hash')!=snapshot['prepared']['dispatch_options_hash']):raise HTTPException(409,'真实模型预留与预览不一致')
                row.data={**row.data,'call_id':call_id};row.version+=1;journal.commit()
            # These locks remain held by live through the actual HTTP call.
            task,_,_=_task(live,actor,snapshot['task_id'],lock=False)
            current=_principal(live,workspace,p.user_id)
            if current.role!=p.role:raise HTTPException(403,'原授权人的角色已变化')
            check_current(live,current,task,snapshot)
        def captured(raw,receipt):
            raw_bytes=raw.encode('utf-8',errors='surrogateescape');encoded=base64.b64encode(raw_bytes).decode('ascii')
            with Session(engine(),expire_on_commit=False) as journal:
                row=journal.scalar(select(Job).where(Job.workspace_id==workspace,Job.id==identity,Job.kind.in_(ANALYSIS_KINDS)).with_for_update().execution_options(populate_existing=True))
                if not row or row.data.get('state')!='dispatching' or row.data.get('call_id')!=receipt.get('call_id'):raise HTTPException(409,'原首答与原发送不一致')
                row.data={**row.data,'state':'response_saved','capture':{'call_id':receipt['call_id'],'raw_base64':encoded,
                    'raw_sha256':hashlib.sha256(raw_bytes).hexdigest(),'receipt_hash':digest(receipt),'receipt':copy.deepcopy(receipt)}};row.version+=1
                audit(journal,p,'sensor.native.analysis_first_response',snapshot['task_id'],{'analysis_id':identity,'call_id':receipt['call_id']});journal.commit()
        try:
            cloud_json(cap,snapshot['raw_messages'],json.loads(snapshot['schema_json']),actor,on_reserved=reserved,on_response=captured,
                evidence_stage=transport.STAGE,output_token_limit=snapshot['quote']['output_token_bound'])
        except Exception:
            # Any uncertain error is deliberately not retryable. If the first
            # HTTP response was saved, recovery below uses only those bytes.
            pass
        finally:live.rollback()
    if _recover(workspace,identity):return True
    with Session(engine(),expire_on_commit=False) as db:
        row=db.scalar(select(Job).where(Job.workspace_id==workspace,Job.id==identity,Job.kind.in_(ANALYSIS_KINDS)).with_for_update().execution_options(populate_existing=True))
        if row and row.data.get('state')=='dispatching':
            row.data={**row.data,'state':'response_unknown','error':'原次发送或费用结果未核实；不会自动重发。'};row.version+=1;db.commit()
    return True

def tick(workspace,*,limit=4):
    from uuid import UUID
    if str(UUID(workspace))!=workspace or type(limit) is not int or not 1<=limit<=16:raise ValueError('invalid scope')
    with Session(engine()) as db:
        rows=list(db.scalars(select(Job.id).where(Job.workspace_id==workspace,Job.kind.in_(ANALYSIS_KINDS),Job.status=='internal',Job.data['state'].as_string().in_(['queued','response_saved'])).order_by(Job.created_at,Job.id).limit(limit)))
    done=0
    for identity in rows:
        try:done+=bool(process(workspace,identity))
        except Exception:
            # Scope/permission failures remain visible and do not dispatch.
            with Session(engine()) as db:
                row=db.scalar(select(Job).where(Job.workspace_id==workspace,Job.id==identity,Job.kind.in_(ANALYSIS_KINDS)).with_for_update().execution_options(populate_existing=True))
                if row and row.data.get('state')=='queued':row.data={**row.data,'state':'blocked','error':'任务、授权、来源、模型或费用已变化，请重新核对。'};row.version+=1;db.commit()
    return done

def start_workspace_analyzer(workspace,stop_event):
    import threading
    def run():
        while not stop_event.is_set():
            try:tick(workspace)
            except Exception as e:print('传感器证据分析暂不可用：'+type(e).__name__,flush=True)
            if stop_event.wait(5):break
    thread=threading.Thread(target=run,name='sensor-analysis-'+workspace[:8],daemon=True);thread.start();return thread
