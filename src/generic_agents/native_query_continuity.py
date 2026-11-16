"""Metadata for a newly pinned Task; historical proposal parsers remain frozen."""
from fastapi import HTTPException
from .contracts import digest
from .services import scoped,published


def build_context_catalog(db,p,task):
    from .models import Entity
    from .workspace_api import saved_execution
    from .entity_release_compat import verify_task_objects
    from .native_query_catalog import catalog
    from .business_tools import arguments_schema
    from .instance_relation_types import relation_metadata
    from .native_query_proposal_contract import CATALOG_SCHEMA,METHODS,_kind
    _,release,business=saved_execution(db,p,task.id)
    verify_task_objects(db,p,task,release)
    package=published(db,p,task.package_id,'domain_package');spec=package.data['specification']
    if (release.package_id!=package.id or digest(release.data['snapshot'])!=release.configuration_hash
        or spec.get('configuration_hash')!=release.configuration_hash or not business['available']):
        raise HTTPException(409,'原任务固定业务版本与查询目录无法核对')
    ids=task.data.get('entity_ids',[])
    if not ids or len(ids)>30 or len(ids)!=len(set(ids)):
        raise HTTPException(409,'原任务对象范围无效')
    entities=[scoped(db,Entity,identity,p) for identity in ids]
    types={row.entity_type for row in entities}
    definitions={item['key']:item for item in spec.get('object_catalog',[])}
    variants={}
    for entity_type in sorted(types):
        declared={item['key']:item for item in definitions.get(entity_type,{}).get('fields',[])}
        for key,prop in spec['entity_schemas'][entity_type].get('properties',{}).items():
            kind=_kind(prop)
            if kind not in {'string','boolean','number','integer'}:continue
            field=declared.get(key,{})
            variants.setdefault(key,[]).append({'key':key,'label':field.get('label') or prop.get('title') or key,
                'type':kind,'unit':field.get('unit',prop.get('unit')),'entity_type':entity_type})
    fields=[];conflicts=[]
    for key,items in sorted(variants.items()):
        if len({(item['type'],item['unit']) for item in items})!=1:
            conflicts.append({'key':key,'reason':'所选对象类型对同名字段的类型或单位声明不一致'})
        fields.append({name:items[0][name] for name in ['key','label','type','unit']}|
            {'entity_types':[item['entity_type'] for item in items]})
    metadata=relation_metadata(package,entities)
    relations=[{key:item[key] for key in ['id','title','description','source_types','target_types']}
        for item in metadata['relation_types'] if set(item['source_types'])&types and set(item['target_types'])&types]
    tools=[]
    for cap in catalog(db,p,package):
        method=cap.data['specification']['method'];execution=cap.data['execution']
        if method not in METHODS or execution.get('adapter')!='builtin' or execution.get('side_effect') is not False:
            raise HTTPException(409,'查询目录只能使用固定的本机只读筛选、全文和计算能力')
        properties=cap.data['output_schema']['properties']['records']['items']['properties']['values'].get('properties',{})
        output=[{'key':key,'type':_kind(prop),'unit':'条证据记录' if key=='sample_count' else None}
            for key,prop in properties.items() if _kind(prop) in {'string','boolean','number','integer'}]
        tools.append({'id':cap.id,'name':cap.data['description'],'method':method,'content_hash':cap.content_hash,
            'version':cap.release_version,'parameters_schema':arguments_schema(cap),'output_fields':output})
    return {'schema':CATALOG_SCHEMA,
        'objects':[{'id':item['id'],'label':item['title'],'type':item['type'],'type_label':item['type_title']} for item in metadata['entities']],
        'fields':fields,'relation_types':relations,'query_tools':tools,'field_conflicts':conflicts}
