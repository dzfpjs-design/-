"""Independent source facet; the original three-tool catalog is unchanged."""
import json
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import Manifest,digest
from .models import Capability
from .services import published,register,validate_capability,publish
from .pixel_question_contracts_v252 import FACET,OPERATIONS,tool_input_schema,answer_schema

HANDLER='pixel_machine_query_v252'
SLOT='pixel_question_catalog_v252'
FIXED_NAMESPACE='platform_pixel_questions'

def manifest():
    return Manifest.model_validate(json.loads((Path(__file__).resolve().parents[2]/'model-packs'/'native-pixel-question-v252.json').read_text()))

def applies(value):
    return (value.get('namespace')==FIXED_NAMESPACE or value.get('execution',{}).get('handler')==HANDLER
        or value.get('specification',{}).get('source_facet')==FACET)

def validate_manifest(value):
    fixed=manifest().model_dump(mode='json')
    if digest(value)!=digest(fixed):raise HTTPException(422,'影像成果问答工具必须使用已实现的固定只读合同')
    if digest(value['input_schema'])!=digest(tool_input_schema()) or digest(value['output_schema'])!=digest(answer_schema()):
        raise HTTPException(422,'影像问答输入输出与本机实现不符')

def build(snapshot):
    plan=snapshot.get('native_pixel_results_v250')
    if plan is None:return None
    value={'schema':'instance-pixel-question-facet-v252','source_family':'workflow_pixel_change_candidate_v250',
        'pixel_data_plan_hash':digest(plan),'tool_manifest_hash':digest(manifest().model_dump(mode='json')),
        'provider':FACET,'operations':OPERATIONS,'time_basis':'machine_result_registered','timezone':'Asia/Shanghai',
        'source_time_verified':False,'candidate_only':True,'field_truth_verified':False,'max_source_records':1000}
    return {**value,'facet_hash':digest(value)}

def verify(snapshot):
    expected=build(snapshot)
    if expected is None:
        if 'pixel_question_facet_v252' in snapshot:raise HTTPException(409,'本业务版本没有影像成果问答来源')
    elif digest(snapshot.get('pixel_question_facet_v252'))!=digest(expected):
        raise HTTPException(409,'影像成果问答声明无法核对')
    return expected

def ensure(db,p,snapshot):
    if verify(snapshot) is None:return []
    fixed=manifest();row=db.scalar(select(Capability).where(Capability.workspace_id==p.workspace_id,
        Capability.namespace==fixed.namespace,Capability.name==fixed.name,Capability.release_version==fixed.version).with_for_update())
    if row is None:
        row=register(db,p,fixed);validate_capability(db,p,row.id,row.version)
        publish(db,p,row.id,row.version,'随新业务版本配备只读影像成果问答；不产生对象现场事实')
    row=published(db,p,row.id,'tool');validate_manifest(row.data)
    if row.content_hash!=digest(row.data):raise HTTPException(409,'影像成果问答能力内容无法核对')
    return [row]

def pins(snapshot,tools):
    if not tools:return {}
    return {SLOT:{'schema':'pixel-question-catalog-v252','facet_hash':snapshot['pixel_question_facet_v252']['facet_hash'],
        'source_family':'workflow_pixel_change_candidate_v250','tools':[{'id':x.id,'hash':x.content_hash,
            'version':x.release_version,'method':'pixel_machine_query'} for x in tools]}}

def catalog(db,p,release):
    snapshot=release.data.get('snapshot')
    if type(snapshot) is not dict or digest(snapshot)!=release.configuration_hash:raise HTTPException(409,'原业务版本内容无法核对')
    # Old releases remain readable, but never gain a facet from current code.
    if 'pixel_question_facet_v252' not in snapshot:return None
    facet=verify(snapshot);package=published(db,p,release.package_id,'domain_package')
    if digest(package.data)!=package.content_hash or package.data['specification'].get('configuration_hash')!=release.configuration_hash:
        raise HTTPException(409,'问答目录与原业务包无法核对')
    fixed=package.data['specification'].get(SLOT)
    if type(fixed) is not dict or set(fixed)!={'schema','facet_hash','source_family','tools'} or fixed['schema']!='pixel-question-catalog-v252' or fixed['facet_hash']!=facet['facet_hash'] or fixed['source_family']!='workflow_pixel_change_candidate_v250' or len(fixed['tools'])!=1:
        raise HTTPException(409,'影像成果问答目录无法核对')
    pin=fixed['tools'][0]
    if set(pin)!={'id','hash','version','method'} or pin['method']!='pixel_machine_query' or pin['id'] not in package.data['dependencies'] or pin['id'] not in package.data['specification']['data_tool_ids']:
        raise HTTPException(409,'影像问答能力未在真实业务包依赖中登记')
    row=published(db,p,pin['id'],'tool');validate_manifest(row.data)
    if row.content_hash!=pin['hash'] or digest(row.data)!=pin['hash'] or row.release_version!=pin['version']:
        raise HTTPException(409,'影像问答能力版本已变化')
    return row
