"""Read-only, value-free copy plan for one original existing-record need.

The descriptor is not a NeedCreate command or authority. Current IDs and full
field definitions are independently rebuilt; the ordinary create/guard path
remains the only destination for an explicitly edited new human request.
"""
import copy
from fastapi import HTTPException
from .models import InstanceRelease
from .services import scoped
from .contracts import digest

SCHEMA='workspace-data-need-copy-v246'


def _fields(release,entity,fields,labels):
    from .entity_release_compat import object_contract
    snapshot=release.data['snapshot']
    if digest(snapshot)!=release.configuration_hash:raise HTTPException(409,'原发布版本内容摘要无法核对')
    contract=object_contract(snapshot,entity.entity_type)
    declared={field['key']:field for field in contract['fields']}
    result=[]
    for key in fields:
        field=declared.get(key);prop=contract['schema']['properties'].get(key)
        result.append({'key':key,'label':field['label'] if field else labels.get(key,key),
            'kind':field['kind'] if field else None,'unit':field.get('unit') if field else None,
            'schema_hash':digest(prop) if field and type(prop) is dict else None})
    return result


def descriptor(db,p,row,context,entity):
    request=row.data['request'];requirement=request.get('evidence_requirement') or {};time=requirement.get('time') or {}
    conditions={'purpose':request['question'],'fields':copy.deepcopy(request['fields']),
        'source':requirement.get('source'),'confirmation':None,'max_age_hours':None}
    reasons=[]
    if conditions['source']=='observations':
        conditions['confirmation']=requirement.get('minimum_verification')
        seconds=time.get('max_age_seconds')
        if time.get('mode')=='latest' and type(seconds) is int and seconds%3600==0 and 1<=seconds//3600<=720:
            conditions['max_age_hours']=seconds//3600
        else:reasons.append('原时间条件不能直接复制，请重新明确登记时效')
        if conditions['confirmation'] not in {'confirmed','unverified_extraction'}:
            reasons.append('原确认要求不能直接复制，请重新核对证据等级')
    elif conditions['source']!='archive' or time.get('mode')!='archive':
        reasons.append('原资料来源或时间要求不能直接复制，请重新选择')
    if requirement.get('minimum_records_per_entity',1)!=1:
        reasons.append('原样本量要求超出此表单复制范围，请保留原需求并重新核对')
    source={'query_id':row.id,'query_version':row.version,'request_id':context['request_id'],'body_hash':context['body_hash'],
        'context_hash':row.data['workspace_need_context_hash'],
        'request_hash':context['query_request_hash'],'instance_id':context['instance_id'],'release_id':context['release_id'],
        'business_id':context['business_id'],'entity_id':context['entity_id'],'object_definition_hash':context.get('object_definition_hash')}
    empty=[{'key':key,'label':context['field_labels'].get(key,key),'kind':None,'unit':None,'schema_hash':None}
        for key in request['fields']]
    source_fields=copy.deepcopy(empty)
    target={'instance_id':context['instance_id'],'release_id':None,'business_id':context['business_id'],
        'entity_id':context['entity_id'],'object_definition_hash':None,'fields':[]}
    try:
        release=scoped(db,InstanceRelease,context['release_id'],p)
        if release.instance_id!=context['instance_id']:raise HTTPException(409,'原需求所属版本无法核对')
        source_fields=_fields(release,entity,request['fields'],context['field_labels'])
    except (HTTPException,KeyError,TypeError,ValueError,AttributeError):
        reasons.append('原字段定义无法核对，不能仅按名称复制')
    try:
        from .instance_api import active
        from .entity_release_compat import require_entity
        instance,release=active(db,p,context['instance_id'])
        target['release_id']=release.id
        if context['business_id'] not in instance.data.get('enabled_business_ids',[]):
            reasons.append('原业务当前未启用，请重新选择业务')
        definition=require_entity(db,p,instance,release,entity)
        target['object_definition_hash']=digest(definition)
        target['fields']=_fields(release,entity,request['fields'],context['field_labels'])
        if target['fields']!=source_fields or any(field['kind'] is None or field['schema_hash'] is None for field in source_fields):
            reasons.append('当前字段类型、单位或完整约束与原条件不一致，请重新选择字段')
    except (HTTPException,KeyError,TypeError,ValueError,AttributeError):
        reasons.append('当前实例版本或对象范围不可用于复制，请保留原条件并重新核对')
    if p.role not in {'admin','operator'}:reasons.append('当前账号只读，不能复制为新的提交草稿')
    value={'schema':SCHEMA,'source':source,'conditions':conditions,'source_fields':source_fields,'target':target,
        'can_copy':not reasons,'reasons':list(dict.fromkeys(reasons))}
    return {**value,'descriptor_hash':digest(value)}
