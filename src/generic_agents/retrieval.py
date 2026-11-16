"""Source-preserving lexical/semantic ranking over explicitly selected evidence."""
import copy,math,re,unicodedata,os
from collections import Counter
from datetime import datetime,timedelta
import httpx
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import digest
from .models import VectorCache,DataRun,now
from .services import published,scoped
from .leases import checkpoint

def text_tokens(text):
    value=unicodedata.normalize('NFKC',text).casefold()
    terms=re.findall(r'[a-z0-9_]+',value)
    for span in re.findall(r'[\u3400-\u9fff]+',value):
        terms.extend(span);terms.extend(span[i:i+2] for i in range(len(span)-1))
    return terms

def texts(inputs):
    records=inputs['records'];field=inputs['field'];query=inputs['text'].strip()
    if not query or len(query)>512:raise HTTPException(422,'检索文字须为1至512字符')
    values=[r['values'].get(field) for r in records]
    if any(not isinstance(v,str) or not v.strip() or len(v)>4096 for v in values):
        raise HTTPException(422,'检索字段含缺项、非文字或过长记录；未截断或静默跳过')
    return query,values

def ordered(inputs,scores,summary):
    # JSON Schema integer includes integral floats such as 1.0. Interpret only
    # the local slice index; the original input and receipt retain their type.
    limit=inputs['top_k']
    if type(limit) not in {int,float} or not 1<=limit<=20 or limit!=int(limit):
        raise HTTPException(422,'检索返回数量须为1至20的完整整数')
    index=int(limit)
    ranked=sorted(enumerate(scores),key=lambda pair:(-pair[1],digest(inputs['records'][pair[0]])))
    selected=[(i,s) for i,s in ranked if s>=inputs['min_score'] and s>0][:index]
    return {'records':[copy.deepcopy(inputs['records'][i]) for i,s in selected],
        'summary':{**summary,'scanned_records':len(scores),'matched_records':sum(s>=inputs['min_score'] and s>0 for s in scores),
            'returned_records':len(selected),'top_k':inputs['top_k'],'ranked_subset':True,
            'ranking':[{'evidence_refs':inputs['records'][i]['evidence_refs'],'score':round(s,8)} for i,s in selected],
            'meaning':'仅对原范围证据排序，相似度不是事实确认；后序计算仅统计检索返回的证据记录'}}

def fulltext(inputs):
    query,values=texts(inputs);query_terms=set(text_tokens(query))
    if not query_terms:raise HTTPException(422,'检索文字没有可支持的中文或英文词项')
    counts=[Counter(text_tokens(v)) for v in values];avg=sum(sum(c.values()) for c in counts)/max(1,len(counts));scores=[]
    frequency={t:sum(t in c for c in counts) for t in query_terms}
    for text,c in zip(values,counts):
        score=sum(math.log(1+(len(counts)-frequency[t]+.5)/(frequency[t]+.5))*c[t]*2.2/
            (c[t]+1.2*(.25+.75*sum(c.values())/max(1,avg))) for t in query_terms if c[t])
        if inputs['match_mode']=='all_terms' and not query_terms.issubset(c):score=0
        if score and unicodedata.normalize('NFKC',query).casefold() in unicodedata.normalize('NFKC',text).casefold():score+=1
        scores.append(score)
    return ordered(inputs,scores,{'method':'bounded_fulltext_bm25','tokenizer_version':'zh_character_bigram_en_word_v1'})

def vector(value,dimension):
    if not isinstance(value,list) or len(value)!=dimension or any(type(x) not in {int,float} or not math.isfinite(x) for x in value):
        raise HTTPException(422,'向量维度或数值不符合已发布模型契约')
    norm=math.sqrt(math.fsum(x*x for x in value))
    if not math.isfinite(norm) or norm<=0:raise HTTPException(422,'向量无有效范数，不能排序')
    return [x/norm for x in value]

def embedding_profile(cap):
    e=cap.data['execution']
    if e.get('task')!='embedding' or e.get('provider')!='qwen' or e.get('model')!='text-embedding-v4' or e.get('secret_ref')!='DASHSCOPE_API_KEY':
        raise HTTPException(422,'语义检索需要已实现的Qwen text-embedding-v4模型配置')
    if type(e.get('dimensions')) is not int or e['dimensions'] not in {64,128,256,512,768,1024,1536,2048}:
        raise HTTPException(422,'向量维度未显式登记或不受支持')
    if not cap.data['data_policy'].get('cloud_allowed'):raise HTTPException(422,'向量模型未授权云调用')

def cloud_embed(model,p,values):
    from .gateway import ModelBlocked
    from .config import settings
    from .cloud_budget import reserve,finish
    from .cloud_provider_policy import provider_enabled
    if not provider_enabled('qwen'):
        raise ModelBlocked('Qwen 向量服务当前未启用，未发送或预留费用；请配置可用的向量能力')
    embedding_profile(model)
    if not 1<=len(values)<=10 or sum(len(x.encode()) for x in values)>40000:
        raise ModelBlocked('向量批次超过10项或输入字节预算')
    configured=settings().dashscope_api_key
    key=os.getenv('DASHSCOPE_API_KEY') or (configured.get_secret_value() if configured else None)
    if not key:raise ModelBlocked('Qwen向量凭证未配置，未使用模拟向量')
    body={'model':model.data['execution']['model'],'input':values,'dimensions':model.data['execution']['dimensions'],'encoding_format':'float'}
    call=reserve(p,model,sum(len(x.encode()) for x in values)+512,digest(body))
    receipt=None
    try:
        with httpx.Client(timeout=45,follow_redirects=False,trust_env=False) as client:
            response=client.post('https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings',
                headers={'Authorization':'Bearer '+key},json=body)
        if response.status_code!=200:raise ModelBlocked(f'Qwen向量请求失败（HTTP {response.status_code}），未切换为词法检索')
        result=response.json();items=result['data']
        if not isinstance(items,list) or len(items)!=len(values) or any(type(x.get('index')) is not int for x in items) or {x['index'] for x in items}!=set(range(len(values))):
            raise ModelBlocked('向量响应缺项或索引重复，未保存部分结果')
        vectors=[None]*len(values)
        for item in items:vectors[item['index']]=vector(item['embedding'],body['dimensions'])
        receipt={'provider':'qwen','model':body['model'],'dimensions':body['dimensions'],'vector_hashes':[digest(v) for v in vectors],'usage':result.get('usage',{}),
            'request_id':response.headers.get('x-request-id'),'call_id':call}
        finish(call,receipt,'succeeded');return vectors,receipt
    except (ModelBlocked,HTTPException,httpx.HTTPError,ValueError,KeyError,TypeError,OverflowError) as error:
        finish(call)
        if isinstance(error,ModelBlocked):raise
        raise ModelBlocked('向量响应或网络不符合契约，保留费用账本；未采纳或生成替代结果') from error

def invalidate_entity(db,p,identifier):
    rows=db.scalars(select(VectorCache).where(VectorCache.workspace_id==p.workspace_id,
        VectorCache.status=='ready',VectorCache.data['entity_ids'].contains([identifier])).with_for_update()).all()
    for row in rows:
        row.status='invalidated';row.version+=1
        row.data={k:v for k,v in row.data.items() if k not in {'vector','vector_hash'}}
        row.data={**row.data,'invalidated_at':now().isoformat(),'reason':'相关来源已更新或核查状态改变'}

def index_current(db,p,cap,summary):
    """Read-only validation for business evidence and learned-case reuse."""
    model=published(db,p,cap.data['execution']['model_profile_id'],'foundation_model');embedding_profile(model)
    if summary.get('model_hash')!=model.content_hash or summary.get('dimensions')!=model.data['execution']['dimensions']:
        raise HTTPException(409,'检索所用向量模型版本或维度已失效')
    from .models import CloudCall
    if not summary.get('index_ids'):raise HTTPException(409,'检索结果缺少保存索引依据')
    for identifier in summary['index_ids']:
        row=scoped(db,VectorCache,identifier,p);receipt=row.data.get('receipt',{})
        ledger=scoped(db,CloudCall,receipt.get('call_id'),p);position=row.data.get('batch_position')
        if row.status!='ready' or row.model_profile_id!=model.id or row.data.get('model_hash')!=model.content_hash or row.data.get('dimension')!=summary['dimensions'] or digest(row.data.get('vector'))!=row.data.get('vector_hash'):
            raise HTTPException(409,'检索索引已撤回、来源改变或完整性不符')
        if ledger.status!='succeeded' or ledger.capability_id!=model.id or ledger.data.get('receipt')!=receipt or type(position) is not int or not 0<=position<len(receipt.get('vector_hashes',[])) or receipt['vector_hashes'][position]!=row.data.get('vector_hash'):
            raise HTTPException(409,'检索索引与已保存云回执不一致')
        vector(row.data['vector'],summary['dimensions'])

def semantic(db,p,cap,inputs,key,*,cached_only=False):
    if not key.startswith('data-tool:'):raise HTTPException(403,'语义检索只能由已保存且获出域授权的查询执行')
    query,values=texts(inputs);model=published(db,p,cap.data['execution']['model_profile_id'],'foundation_model');embedding_profile(model)
    forbidden={'password','authorization','api_key','phone','email','id_card','身份证','手机号','contact','location'}
    forbidden.update(cap.data['data_policy'].get('redact_fields',[]));forbidden.update(model.data['data_policy'].get('redact_fields',[]))
    if inputs['field'].casefold() in forbidden:raise HTTPException(403,'检索字段在出域脱敏目录，未向向量模型发送')
    if len(values)>30 or sum(len(x.encode()) for x in [query,*values])>40000:
        raise HTTPException(422,'语义检索超过30条或40000字节上限，请拆分明确范围，未截断')
    dimension=model.data['execution']['dimensions'];profile_hash=model.content_hash;cap_hash=cap.content_hash
    records=inputs['records'];refs=sorted({ref for r in records for ref in r['evidence_refs']});entities=sorted({e for r in records for e in r['entity_ids']})
    descriptors=[{'text':query,'entity_ids':entities,'refs':refs,'kind':'query','record_hash':digest(records)}]+[
        {'text':text,'entity_ids':r['entity_ids'],'refs':r['evidence_refs'],'kind':'record','record_hash':digest(r)} for text,r in zip(values,records)]
    def check(*,lock_sources=False):
        db.expire_all();published(db,p,cap.id);published(db,p,model.id)
        if cap.content_hash!=cap_hash or model.content_hash!=profile_hash:raise HTTPException(409,'检索工具或向量模型版本改变，请重新确认查询')
        if key.startswith('data-tool:'):
            row=scoped(db,DataRun,key.split(':')[1],p)
            if row.status=='cancelled':raise HTTPException(409,'查询已取消，未继续向量调用')
            trace=row.data.get('tool_trace',[])
            if not row.data['request'].get('allow_external_tools') or not any(
                t['step']['id']==key.split(':')[2] and t['capability_id']==cap.id and t['input_hash']==digest(inputs) for t in trace):
                raise HTTPException(403,'语义检索输入不属于持久授权查询步骤')
            from .goal_queries import guard
            guard(db,p,row)
            context=row.data.get('tool_context',{})
            if context.get('started_at') and now()>datetime.fromisoformat(context['started_at'])+timedelta(seconds=row.data['request'].get('max_tool_seconds',60)):
                raise HTTPException(409,'查询时间预算已耗尽，未继续向量调用')
            from .services import query_evidence
            from .data_tools import frame
            req=row.data['request'];fresh=query_evidence(db,p,req['entity_ids'],req['fields'],learning=False,
                requirement=req.get('evidence_requirement'),fixed_window=req.get('resolved_time'),relation_query=req.get('relation_query'))
            if fresh['status']!='sufficient' or frame(fresh)!=context.get('source_records'):
                raise HTTPException(409,'向量调用前原始来源已变化，未继续后续批次')
            if lock_sources:
                from .models import Entity,Observation
                # Serialize source invalidation against saving newly received vectors.
                for identifier in sorted(req['entity_ids']):scoped(db,Entity,identifier,p,True)
                for item in sorted(fresh.get('observation_evidence',[]),key=lambda x:x['observation_id']):
                    scoped(db,Observation,item['observation_id'],p,True)
                fresh=query_evidence(db,p,req['entity_ids'],req['fields'],learning=False,
                    requirement=req.get('evidence_requirement'),fixed_window=req.get('resolved_time'),relation_query=req.get('relation_query'))
                if fresh['status']!='sufficient' or frame(fresh)!=context.get('source_records'):
                    raise HTTPException(409,'保存向量前来源已变化，未建立旧来源索引')
    keys=[];vectors=[];missing=[];receipts=[];cache_ids=[]
    check()
    for i,d in enumerate(descriptors):
        cache_key=digest({'model_hash':profile_hash,'dimension':dimension,'text_hash':digest(d['text']),
            'field':inputs['field'],'record_hash':d['record_hash'],'kind':d['kind']})
        keys.append(cache_key)
        row=db.scalar(select(VectorCache).where(VectorCache.workspace_id==p.workspace_id,VectorCache.cache_key==cache_key))
        if row and row.status=='ready':
            expected={**d,'text_hash':digest(d['text'])};expected.pop('text')
            from .models import CloudCall
            receipt=row.data.get('receipt',{});ledger=scoped(db,CloudCall,receipt.get('call_id'),p)
            position=row.data.get('batch_position')
            if ledger.status!='succeeded' or ledger.capability_id!=model.id or ledger.data.get('receipt')!=receipt or type(position) is not int or not 0<=position<len(receipt.get('vector_hashes',[])) or receipt['vector_hashes'][position]!=row.data.get('vector_hash'):
                raise HTTPException(409,'向量索引缺少一致的已保存模型响应账本')
            if any(row.data.get(k)!=v for k,v in expected.items()) or row.model_profile_id!=model.id or row.data.get('model_hash')!=profile_hash or row.data.get('dimension')!=dimension or digest(row.data.get('vector'))!=row.data.get('vector_hash'):
                raise HTTPException(409,'向量索引完整性或来源契约不符，请核查；未自动覆盖')
            vectors.append(vector(row.data['vector'],dimension));cache_ids.append(row.id);receipts.append(row.data['receipt'])
        else:vectors.append(None);missing.append(i)
    if cached_only and missing:raise HTTPException(409,'保存回执所需的向量索引已失效或缺少，未发出新云调用')
    for start in range(0,len(missing),10):
        check();checkpoint(db);batch=missing[start:start+10]
        embedded,receipt=cloud_embed(model,p,[descriptors[i]['text'] for i in batch])
        check(lock_sources=True)
        for position,(i,v) in enumerate(zip(batch,embedded)):
            d=descriptors[i];data={k:copy.deepcopy(value) for k,value in d.items() if k!='text'}
            data.update(model_hash=profile_hash,dimension=dimension,text_hash=digest(d['text']),vector=v,vector_hash=digest(v),receipt=receipt,batch_position=position)
            row=db.scalar(select(VectorCache).where(VectorCache.workspace_id==p.workspace_id,VectorCache.cache_key==keys[i]).with_for_update())
            if row and row.status=='ready':
                # Another authorized query may have built this immutable index while the cloud call waited.
                if any(row.data.get(k)!=data[k] for k in ['model_hash','dimension','text_hash','record_hash','refs','entity_ids','kind']) or digest(row.data.get('vector'))!=row.data.get('vector_hash'):
                    raise HTTPException(409,'并发建立的向量索引不符合当前契约，未覆盖')
                v=vector(row.data['vector'],dimension)
            elif row:row.status='ready';row.data=data;row.version+=1
            else:
                from sqlalchemy.dialects.postgresql import insert
                db.execute(insert(VectorCache).values(workspace_id=p.workspace_id,model_profile_id=model.id,cache_key=keys[i],data=data)
                    .on_conflict_do_nothing(index_elements=['workspace_id','cache_key']))
                row=db.scalar(select(VectorCache).where(VectorCache.workspace_id==p.workspace_id,VectorCache.cache_key==keys[i]).with_for_update())
                if row.status!='ready' or any(row.data.get(k)!=data[k] for k in ['model_hash','dimension','text_hash','record_hash','refs','entity_ids','kind']) or digest(row.data.get('vector'))!=row.data.get('vector_hash'):
                    raise HTTPException(409,'并发向量保存发生失效或契约冲突，未覆盖')
                v=vector(row.data['vector'],dimension)
            vectors[i]=v;cache_ids.append(row.id);receipts.append(row.data['receipt'])
        checkpoint(db)
    scores=[max(-1,min(1,math.fsum(x*y for x,y in zip(vectors[0],v)))) for v in vectors[1:]]
    output=ordered(inputs,scores,{'method':'source_preserving_semantic_ranking','model_profile_id':model.id,'model_hash':profile_hash,
        'model':model.data['execution']['model'],'dimensions':dimension,'index_version':'selected_evidence_v1',
        'index_ids':sorted(cache_ids),'new_vectors':len(missing),'cached_vectors':len(descriptors)-len(missing),
        'cloud_receipts':[{k:v for k,v in r.items() if k!='vector_hashes'} for identifier,r in sorted({r['call_id']:r for r in receipts}.items())]})
    return output
