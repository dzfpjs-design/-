"""Grounded relationship *type* suggestions and explicit durable draft adoption.

This module never creates Entity, Relation, RelationEvent, or runtime facts.
"""
import copy
import hashlib
import re
from typing import Literal
from uuid import UUID
from fastapi import HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert
from .contracts import Contract, digest
from .models import Audit, SetupAnalysis, SetupMaterial, SetupRevision, uid
from .setup_api import router, P, DB, draft, validate_configuration, automation_review_actor
from .services import scoped, require, check_version
from .setup_object_proposals import ObjectCitation


class ProposedRelationship(Contract):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)
    source_type: str = Field(min_length=1, max_length=100)
    target_type: str = Field(min_length=1, max_length=100)
    cardinality: Literal['many', 'one_per_source', 'one_per_target', 'unspecified']
    citations: list[ObjectCitation] = Field(min_length=1, max_length=4)
    inference_reason: str = Field(max_length=500)

    @model_validator(mode='after')
    def clean_names(self):
        for key in ['name', 'description', 'source_type', 'target_type']:
            value=getattr(self,key)
            if not value.strip() or value!=value.strip():
                raise ValueError('关系名称、说明和端点类型不能为空或带首尾空白')
        return self


RELATION_TASK = ('同时阅读资料中明确声明的对象类型之间关系，relationships仅提出关系类型结构，不生成任何具体对象间的关系事实、边、账户权限或设备动作。'
    '关系name、description、source_type、target_type必须忠实于同段原文；端点名称必须完全匹配context.object_names或本次objects中有完整字段定义的对象名。'
    '关系name须包含原文明示的关系动词（如安装、管辖），不替换成原文未声明的管理、归属等其他语义。'
    '每项关系至少引用一条同段原文，原文须明确连接这两类对象并说明该关系；仅提到两个名字、具体对象实例或无关数字不足以提出关系类型。'
    'cardinality只允许many、one_per_source、one_per_target、unspecified；原文明示每个源最多一个目标才用one_per_source，反向唯一才用one_per_target，明示多个才用many。'
    '未明确多重性必须用unspecified，交由人核对，不能从设备数量、页码、业务步骤数或常识猜测约束。依据不足不提出关系，在questions说明；relationships可为空。')

# Conservative declaration vocabulary. Mere co-occurrence never establishes a type.
_RELATION_WORDS=('安装','部署','隶属','属于','归属','管辖','管理','负责','关联','对应','绑定','包含','组成','位于','连接','覆盖','服务','采集','监测','分配','挂接')
_UNCERTAIN=re.compile(r'不|未|禁止|无需|无须|没有|未知|待确认|可能|假设|示例|例如|举例')


def _sentences(quote):
    return [re.sub(r'\s+','',part) for part in re.split(r'[。；;\n!?！？]',quote) if part.strip()]


def _declares(item,sentence):
    source,target=item['source_type'],item['target_type']
    if source==target or source not in sentence or target not in sentence or _UNCERTAIN.search(sentence):
        return False
    # Require a relation word between the two explicitly named types, in the
    # proposed direction. Inverse prose is left for human clarification.
    for match in re.finditer(re.escape(source),sentence):
        end=sentence.find(target,match.end())
        if end<0:continue
        bridge=sentence[match.end():end]
        for word in _RELATION_WORDS:
            if word in bridge and word in item['name'].replace(source,'').replace(target,''):
                return True
    return False


def _unique_per(source,target,sentence):
    return bool(re.search(r'每(?:一)?(?:个|台|名|种|条|项|处|座)?'+re.escape(source)+
        r'.{0,50}?(?:只|仅|最多|唯一).{0,25}?(?:一|1)(?:个|台|名|种|条|项|处|座)?'+re.escape(target)+r'(?!编号|代码|数量|名称|字段|ID|标识)',sentence))


def validate_relationships(values,blocks,config,objects=()):
    if len(values)>30:raise ValueError('关系类型建议最多30项')
    result=[ProposedRelationship.model_validate(x).model_dump() for x in values]
    if len({x['name'] for x in result})!=len(result):raise ValueError('关系类型名称不能重复')
    available={x['name'] for x in config.get('instanceObjects',[])}|{x['name'] for x in objects}
    sources={}
    for block in blocks:sources.setdefault((block['material_id'],block['block_id']),[]).append(block['text'])
    for item in result:
        if {item['source_type'],item['target_type']}-available:
            raise ValueError('关系端点必须对应现有或本次完整定义的对象类型')
        sentences=[]
        for cite in item['citations']:
            if not any(cite['quote'] in text for text in sources.get((cite['material_id'],cite['block_id']),[])):
                raise ValueError('关系引用必须逐字匹配已授权原文的资料与段落')
            sentences.extend(_sentences(cite['quote']))
        declared=[s for s in sentences if _declares(item,s)]
        if not declared:raise ValueError('原文须明确连接两个对象类型并声明此关系，不能凭名称共现或推断捏造关系结构')
        cardinality=item['cardinality']
        if cardinality=='unspecified':continue
        clauses=[part for sentence in sentences for part in re.split(r'[，,]',sentence)]
        reverse={**item,'source_type':item['target_type'],'target_type':item['source_type']}
        multiple=lambda endpoint,s:bool(re.search(r'多(?:个|台|名|种|条|项|处|座)?'+re.escape(endpoint)+r'(?!编号|代码|数量|名称|字段|ID|标识)',s))
        if cardinality=='many':
            proven=any((_declares(item,s) or _declares(reverse,s)) and (
                multiple(item['source_type'],s) or multiple(item['target_type'],s)
                or re.search(r'多对多|一对多',s)) for s in clauses)
        elif cardinality=='one_per_source':
            proven=any(_declares(item,s) and _unique_per(item['source_type'],item['target_type'],s) for s in clauses)
            if any(_declares(item,s) and multiple(item['target_type'],s) for s in clauses):proven=False
        else:
            proven=any(_declares(reverse,s) and _unique_per(item['target_type'],item['source_type'],s) for s in clauses)
            if any(_declares(reverse,s) and multiple(item['source_type'],s) for s in clauses):proven=False
        if not proven:raise ValueError('关系多重性必须由同一关系的明确原文声明；无关数量不能证明，未知须保留unspecified')
    return result


def apply_relationships(config,proposals,selected,*,analysis_id,result_hash,request_id):
    out=copy.deepcopy(config);candidates={x['name']:x for x in proposals}
    if not selected or len(selected)>30 or len(set(selected))!=len(selected) or any(n not in candidates for n in selected):
        raise ValueError('请选择实际存在且不重复的关系类型建议')
    current=copy.deepcopy(out.get('instanceRelationTypes',[]));known={x['name'] for x in current}
    if known&set(selected):raise ValueError('同名关系类型已存在，不会覆盖人工关系结构')
    if len(current)+len(selected)>30:raise ValueError('关系类型最多30项')
    adopted={x['name'] for x in out.get('instanceObjects',[])}
    origins=copy.deepcopy(out.get('instanceRelationOrigins',{}))
    for name in selected:
        item=candidates[name]
        if {item['source_type'],item['target_type']}-adopted:
            raise ValueError('请先采用关系两端的对象类型，再采用关系建议')
        current.append({k:item[k] for k in ['name','description','source_type','target_type','cardinality']}|{'allow_self':False})
        origins[name]={'analysis_id':analysis_id,'result_hash':result_hash,'relationship':copy.deepcopy(item),'request_id':request_id}
    out['instanceRelationTypes']=current;out['instanceRelationOrigins']=origins
    return out


class RelationshipApply(Contract):
    version: int = Field(strict=True, ge=1)
    revision: int = Field(strict=True, ge=1)
    request_id: UUID
    names: list[str] = Field(min_length=1, max_length=30)


_ACTION='setup.understanding.relationships.request'


def _receipt(db,request_id):
    rows=list(db.scalars(select(Audit).where(Audit.action==_ACTION,Audit.data['request_id'].as_string()==str(request_id)).limit(2)))
    if len(rows)>1:raise HTTPException(409,'关系采用请求收据重复，不能确认原提交')
    record=rows[0] if rows else None
    if record is not None and digest({k:v for k,v in record.data.items() if k!='record_hash'})!=record.data.get('record_hash'):
        raise HTTPException(409,'关系采用请求收据完整性无法核对')
    return record


def _bound(record,p,analysis_id,payload=None):
    if (record.workspace_id!=p.workspace_id or record.actor_id!=p.user_id or record.object_id!=analysis_id
        or payload is not None and (record.data.get('body')!=payload or record.data.get('body_hash')!=digest(payload))):
        raise HTTPException(409,'同一关系采用编号不能更换身份、工作区、建议或请求正文')


def _reply(row,config,record,replayed):
    from .setup_understanding import view
    receipt=None if record is None else {k:copy.deepcopy(record.data[k]) for k in ['request_id','state','saved_revision','names','body','body_hash','result_hash']}|{'replayed':replayed}
    return {'state':receipt['state'] if receipt else 'not_found','analysis':view(row),'revision':config.version,
        'configuration':copy.deepcopy(config.data),'receipt':receipt}


def _lock(db,request_id):
    value=int.from_bytes(hashlib.sha256(request_id.bytes).digest()[:8],'big',signed=True)
    db.execute(select(func.pg_advisory_xact_lock(value)))


def _record(db,p,row,config,body,state):
    payload=body.model_dump(mode='json')
    details={'request_id':str(body.request_id),'body':payload,'body_hash':digest(payload),'state':state,
        'saved_revision':config.version,'names':body.names,'result_hash':row.data.get('result_hash')}
    details['record_hash']=digest(details)
    record=Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=_ACTION,object_id=row.id,data=details)
    db.add(record);return record


def _validation_context(req):
    # Original model suggestions target the names in their authorized context.
    # A later explicit human rename must not rewrite that historical evidence.
    return {'businesses':copy.deepcopy(req['context']['businesses']),
        'instanceObjects':[{'name':n} for n in req['context'].get('object_names',[])]}


def checked_candidate(db,p,row,config):
    """Recheck frozen bytes and original paid contract without authorizing a call."""
    from .setup_understanding import (ResultV5, request_contract, checked_v3_request,
        frozen_material, model, validate_result, current)
    req=row.data['request']
    if request_contract(req) is not ResultV5 or row.status not in {'review','applied'}:
        raise HTTPException(409,'仅完整核查的第五版关系类型建议可以采用')
    if len(row.data.get('completed',[]))!=len(req['chunks']) or row.data.get('completed')!=[0]:
        raise HTTPException(409,'全部资料尚未完成，不能采用部分关系建议')
    if row.status=='review':
        current(db,p,row)
    else:
        guard=row.data.get('relationship_guard',{'revision':row.data.get('applied_revision'),'configuration_hash':row.data.get('applied_hash')})
        if config.version!=guard.get('revision') or digest(config.data)!=guard.get('configuration_hash'):
            raise HTTPException(409,'对象采用后配置已有其他修改，请重新核对关系建议')
    cap=model(db,p,req['model_id'])
    if cap.content_hash!=req['model_hash']:raise HTTPException(409,'原模型版本或权限已变化')
    snapshot={'version':'setup-source-snapshot-v1','materials':[]}
    for source in req['materials']:
        material=scoped(db,SetupMaterial,source['id'],p,True)
        if (material.archived or material.status!='parsed' or material.version!=source['version']
            or material.sha256!=source['hash'] or digest(material.data.get('blocks'))!=source['blocks_hash']):
            raise HTTPException(409,'关系依据的资料版本或正文已变化')
        snapshot['materials'].append(frozen_material(db,p,material))
    if row.data.get('consent',{}).get('preview_hash')!=req.get('preview_hash'):
        raise HTTPException(409,'完整资料与原授权无法核对')
    checked_v3_request(req,cap,snapshot)
    result=row.data.get('result')
    if not isinstance(result,dict) or digest(result)!=row.data.get('result_hash'):
        raise HTTPException(409,'原关系建议结果摘要无法核对')
    try:validate_result(result,[b for chunk in req['chunks'] for b in chunk],_validation_context(req),ResultV5,request=req)
    except ValueError as error:raise HTTPException(409,str(error)) from None
    return result


@router.post('/understanding/{object_id}/relationships/apply')
def apply_proposals(object_id:str,body:RelationshipApply,p:P,db:DB):
    require(p,'admin','operator');_lock(db,body.request_id)
    # Match original actor/session authorization under transaction locks as for
    # explicit automation confirmation. The nonce is checked before stale CAS.
    automation_review_actor(db,p)
    row=scoped(db,SetupAnalysis,object_id,p,True);config=draft(db,p,True)
    prior=_receipt(db,body.request_id);payload=body.model_dump(mode='json')
    if prior is not None:
        _bound(prior,p,row.id,payload)
        if prior.data['state']=='abandoned':raise HTTPException(409,'此关系采用请求已放弃；不会接受延迟提交，请重新核对后使用新编号')
        reply=_reply(row,config,prior,True);db.commit();return reply
    check_version(row,body.version);check_version(config,body.revision)
    result=checked_candidate(db,p,row,config)
    before=copy.deepcopy(config.data)
    try:value=apply_relationships(before,result['relationships'],body.names,analysis_id=row.id,
        result_hash=row.data['result_hash'],request_id=str(body.request_id))
    except ValueError as error:raise HTTPException(409,str(error)) from None
    validate_configuration(value)
    db.execute(insert(SetupRevision).values(id=uid(),workspace_id=p.workspace_id,draft_id=config.id,
        version=config.version,data=before).on_conflict_do_nothing(index_elements=['draft_id','version']))
    config.data=value;config.version+=1
    db.add(SetupRevision(workspace_id=p.workspace_id,draft_id=config.id,version=config.version,data=value))
    guard={'revision':config.version,'configuration_hash':digest(value)}
    row.data={**row.data,'relationship_guard':guard,
        'relationship_applications':{**row.data.get('relationship_applications',{}),str(body.request_id):{
            'names':body.names,'result_hash':row.data['result_hash'],'revision':config.version}}}
    if row.status=='review':row.data={**row.data,'local_reuse_guard':guard}
    row.version+=1;record=_record(db,p,row,config,body,'applied')
    reply=_reply(row,config,record,False);db.commit();return reply


@router.get('/understanding/{object_id}/relationships/requests/{request_id}')
def probe_request(object_id:str,request_id:UUID,p:P,db:DB):
    require(p,'admin','operator');row=scoped(db,SetupAnalysis,object_id,p);config=draft(db,p)
    record=_receipt(db,request_id)
    if record is not None:_bound(record,p,row.id)
    reply=_reply(row,config,record,True);db.commit();return reply


@router.post('/understanding/{object_id}/relationships/requests/{request_id}/abandon')
def abandon_request(object_id:str,request_id:UUID,body:RelationshipApply,p:P,db:DB):
    require(p,'admin','operator')
    if request_id!=body.request_id:raise HTTPException(422,'放弃请求必须保留原关系采用编号与完整正文')
    _lock(db,request_id);automation_review_actor(db,p)
    row=scoped(db,SetupAnalysis,object_id,p,True);config=draft(db,p,True)
    record=_receipt(db,request_id);payload=body.model_dump(mode='json')
    if record is not None:_bound(record,p,row.id,payload)
    else:record=_record(db,p,row,config,body,'abandoned')
    reply=_reply(row,config,record,True);db.commit();return reply


def origin_material_ids(config,names):
    from .report_full_semantic_schema_adoption import origin_material_ids as full_material_ids
    return full_material_ids(config,names,'relationship') | {c['material_id'] for name in names
        for c in config.get('instanceRelationOrigins',{}).get(name,{}).get('relationship',{}).get('citations',[])}


def verify_origins(db,p,config,names):
    """Reject forged/transplanted draft provenance before instance compilation."""
    from .setup_understanding import ResultV5,request_contract,checked_v3_request,frozen_material,validate_result
    for name in names:
        origin=config.get('instanceRelationOrigins',{}).get(name)
        if origin is None:continue
        if isinstance(origin,dict) and origin.get('schema') in {
                'report-full-semantic-schema-origin-v1', 'report-object-schema-origin-v170'}:
            from .report_full_semantic_schema_adoption import verify_origin
            verify_origin(db,p,config,name,'relationship')
            continue
        try:
            if set(origin)!={'analysis_id','result_hash','relationship','request_id'}:raise ValueError()
            row=scoped(db,SetupAnalysis,origin['analysis_id'],p)
            req=row.data['request'];result=row.data['result']
            proposal_name=origin['relationship']['name']
            application=row.data.get('relationship_applications',{}).get(origin['request_id'])
            if (request_contract(req) is not ResultV5 or row.status not in {'review','applied','undone'} or not application
                or proposal_name not in application['names'] or application['result_hash']!=origin['result_hash']
                or row.data.get('completed')!=[0] or digest(result)!=row.data['result_hash']
                or origin['result_hash']!=row.data['result_hash']
                or origin['relationship']!=next(x for x in result['relationships'] if x['name']==proposal_name)):
                raise ValueError()
            receipt=_receipt(db,UUID(origin['request_id']))
            if (receipt is None or receipt.workspace_id!=p.workspace_id or receipt.object_id!=row.id
                or receipt.data['state']!='applied' or proposal_name not in receipt.data['names']
                or receipt.data['result_hash']!=origin['result_hash']):raise ValueError()
            snapshot={'version':'setup-source-snapshot-v1','materials':[]}
            for old in req['materials']:
                material=scoped(db,SetupMaterial,old['id'],p)
                if (material.archived or material.status!='parsed' or material.version!=old['version']
                    or material.sha256!=old['hash'] or digest(material.data.get('blocks'))!=old['blocks_hash']):raise ValueError()
                snapshot['materials'].append(frozen_material(db,p,material))
            if row.data.get('consent',{}).get('preview_hash')!=req.get('preview_hash'):raise ValueError()
            checked_v3_request(req,snapshot=snapshot)
            validate_result(result,[b for chunk in req['chunks'] for b in chunk],_validation_context(req),ResultV5,request=req)
        except (HTTPException,ValueError,KeyError,TypeError,StopIteration,AttributeError):
            raise HTTPException(409,'关系类型建议的原文、完整结果或采用收据已变化，请重新核对；已有配置保留') from None
