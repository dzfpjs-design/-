"""按资料授权的本地 OCR API；不接受任意引擎或文件路径。"""
from typing import Annotated, Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import Response
from pydantic import Field, StrictInt, StrictStr
from sqlalchemy import select, func
from .contracts import Contract
from .db import session
from .material_ocr_read_session import read_session
from .models import SetupMaterial, MaterialOcrUnit as Unit, MaterialOcrVersion as Version, MaterialOcrConfirmation as Confirmation
from .security import authenticated, Principal
from .services import scoped
from . import material_ocr as ocr

router=APIRouter()
P=Annotated[Principal,Depends(authenticated)]
DB=Annotated[object,Depends(session)]
ReadDB=Annotated[object,Depends(read_session)]
Positive=Annotated[StrictInt,Field(ge=1)]
Hash=Annotated[StrictStr,Field(pattern=r'^[a-f0-9]{64}$')]
Reason=Annotated[StrictStr,Field(min_length=1,max_length=2000)]


class Command(Contract):
    request_id: UUID
    expected_material_version: Positive


class Start(Command):
    engine_id: Annotated[StrictStr,Field(min_length=1,max_length=100)]
    engine_manifest_hash: Hash


class UnitCommand(Command):
    run_id: UUID
    expected_unit_version: Positive
    reason: Reason


class Region(Contract):
    id: Annotated[StrictStr,Field(min_length=1,max_length=100)]
    text: Annotated[StrictStr,Field(max_length=20000)]
    box: list[Annotated[float,Field(strict=True,allow_inf_nan=False)]]=Field(min_length=4,max_length=4)
    source_region_id: Annotated[StrictStr,Field(min_length=1,max_length=100)]|None


class Save(UnitCommand):
    parent_hash: Hash
    regions: list[Region]=Field(max_length=500)


class ConfirmUnit(UnitCommand):
    revision_hash: Hash


class Restore(UnitCommand):
    revision_id: UUID
    revision_hash: Hash


class RunCommand(Command):
    run_id: UUID
    expected_run_version: Positive
    reason: Reason


class ResumeWithProfile(RunCommand):
    engine_id: Annotated[StrictStr,Field(min_length=1,max_length=100)]
    engine_manifest_hash: Hash


class ConfirmDocument(RunCommand):
    units_manifest_hash: Hash


class Abandon(Contract):
    action: Literal['start','save','unit_confirm','restore','document_confirm','retry','cancel','resume_with_profile']
    target: UUID|None
    original_request: dict
    expected_material_version: Positive
    reason: Reason


COMMANDS={'start':Start,'save':Save,'unit_confirm':ConfirmUnit,'restore':Restore,
          'document_confirm':ConfirmDocument,'retry':RunCommand,'cancel':RunCommand,
          'resume_with_profile':ResumeWithProfile}


@router.get('/ocr/engines')
def engines(p:P): return {'items':[ocr.engine_status(),ocr.low_memory_engine_status()]}


@router.get('/materials/{material_id}/ocr')
def get_overview(material_id:str,p:P,db:ReadDB):
    return ocr.overview(db,p,scoped(db,SetupMaterial,material_id,p))


def commit_command(db,p,material_id,action,body,target=None):
    result=ocr.execute(db,p,material_id,action,body,target);db.commit();return result


@router.post('/materials/{material_id}/ocr/start')
def start(material_id:str,body:Start,p:P,db:DB): return commit_command(db,p,material_id,'start',body)


@router.get('/materials/{material_id}/ocr/units')
def units(material_id:str,run_id:UUID,p:P,db:ReadDB,limit:int=Query(20,ge=1,le=100),after:str|None=None):
    material=scoped(db,SetupMaterial,material_id,p);run=ocr.checked_run(db,p,material,str(run_id))
    scope={'workspace_id':p.workspace_id,'material_id':material.id,'run_id':run.id,'generation':run.generation,'kind':'units'}
    frozen=ocr.cursor_decode(after,scope) if after else {**scope,'upper':run.data['prepared'],'last':0}
    query=select(Unit).where(Unit.workspace_id==p.workspace_id,Unit.run_id==run.id,
          Unit.unit_index>frozen['last'],Unit.unit_index<=frozen['upper']).order_by(Unit.unit_index).limit(limit+1)
    found=list(db.scalars(query));items=[]
    total=db.scalar(select(func.count()).select_from(Unit).where(Unit.workspace_id==p.workspace_id,Unit.run_id==run.id,Unit.unit_index<=frozen['upper']))
    if total!=frozen['upper']:ocr.fail()
    for row in found[:limit]:
        ocr.checked_head(row);ocr.checked_unit(db,p,material,run,row.id,image=False)
        items.append(ocr.unit_summary(db,p,run,row))
    cursor=ocr.cursor_encode({**frozen,'last':found[limit-1].unit_index}) if len(found)>limit else None
    return {'material_id':material.id,'material_version':material.version,'run_id':run.id,'generation':run.generation,
            'items':items,'next_cursor':cursor,'total':total}


def unit_scope(db,p,material_id,run_id,unit_id):
    material=scoped(db,SetupMaterial,material_id,p);run=ocr.checked_run(db,p,material,str(run_id))
    unit=ocr.checked_unit(db,p,material,run,unit_id)
    return material,run,unit


@router.get('/materials/{material_id}/ocr/units/{unit_id}')
def get_unit(material_id:str,unit_id:str,run_id:UUID,p:P,db:ReadDB):
    material,run,unit=unit_scope(db,p,material_id,run_id,unit_id);return ocr.unit_detail(db,p,material,run,unit)


def image_response(db,p,unit,expected):
    from .evidence import evidence_bytes
    if expected!=unit.data['image_sha256']:ocr.fail('原图 SHA 与所选冻结页面不一致')
    _,content=evidence_bytes(db,p,unit.evidence_id)
    return Response(content,media_type='image/png',headers={'X-Content-SHA256':expected,'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@router.get('/materials/{material_id}/ocr/units/{unit_id}/image')
def image(material_id:str,unit_id:str,run_id:UUID,image_sha256:Hash,p:P,db:ReadDB):
    material,run,unit=unit_scope(db,p,material_id,run_id,unit_id)
    ocr.checked_versions(db,p,run,unit);return image_response(db,p,unit,image_sha256)


@router.get('/materials/{material_id}/ocr/units/{unit_id}/versions')
def versions(material_id:str,unit_id:str,run_id:UUID,p:P,db:ReadDB,limit:int=Query(20,ge=1,le=100),after:str|None=None):
    material,run,unit=unit_scope(db,p,material_id,run_id,unit_id);all_rows=ocr.checked_versions(db,p,run,unit)
    scope={'workspace_id':p.workspace_id,'material_id':material.id,'run_id':run.id,'unit_id':unit.id,'kind':'versions'}
    frozen=ocr.cursor_decode(after,scope) if after else {**scope,'upper':len(all_rows),'last':0}
    found=[r for r in all_rows if frozen['last']<r.number<=frozen['upper']]
    if len([r for r in all_rows if r.number<=frozen['upper']])!=frozen['upper']:ocr.fail()
    items=[{**ocr.refs(r),'actor_label':r.data['actor_label'],'reason':r.data['reason']} for r in found[:limit]]
    cursor=ocr.cursor_encode({**frozen,'last':found[limit-1].number}) if len(found)>limit else None
    return {'material_id':material.id,'run_id':run.id,'unit_id':unit.id,'items':items,'next_cursor':cursor,'total':frozen['upper']}


@router.get('/materials/{material_id}/ocr/units/{unit_id}/versions/{revision_id}')
def version(material_id:str,unit_id:str,revision_id:str,run_id:UUID,p:P,db:ReadDB):
    material,run,unit=unit_scope(db,p,material_id,run_id,unit_id);rows=ocr.checked_versions(db,p,run,unit)
    found=next((r for r in rows if r.id==revision_id),None)
    if not found:raise HTTPException(404,'此单元没有该旧稿')
    return ocr.version_view(found)


@router.put('/materials/{material_id}/ocr/units/{unit_id}')
def save(material_id:str,unit_id:str,body:Save,p:P,db:DB):return commit_command(db,p,material_id,'save',body,unit_id)


@router.post('/materials/{material_id}/ocr/units/{unit_id}/confirm')
def confirm_unit(material_id:str,unit_id:str,body:ConfirmUnit,p:P,db:DB):return commit_command(db,p,material_id,'unit_confirm',body,unit_id)


@router.post('/materials/{material_id}/ocr/units/{unit_id}/restore')
def restore(material_id:str,unit_id:str,body:Restore,p:P,db:DB):return commit_command(db,p,material_id,'restore',body,unit_id)


@router.post('/materials/{material_id}/ocr/confirm')
def confirm_document(material_id:str,body:ConfirmDocument,p:P,db:DB):return commit_command(db,p,material_id,'document_confirm',body)


@router.post('/materials/{material_id}/ocr/retry')
def retry(material_id:str,body:RunCommand,p:P,db:DB):return commit_command(db,p,material_id,'retry',body)


@router.post('/materials/{material_id}/ocr/resume_with_profile')
def resume_with_profile(material_id:str,body:ResumeWithProfile,p:P,db:DB):
    return commit_command(db,p,material_id,'resume_with_profile',body)


@router.post('/materials/{material_id}/ocr/cancel')
def cancel(material_id:str,body:RunCommand,p:P,db:DB):return commit_command(db,p,material_id,'cancel',body)


@router.get('/materials/{material_id}/ocr/requests/{request_id}')
def probe(material_id:str,request_id:UUID,p:P,db:ReadDB):
    material=scoped(db,SetupMaterial,material_id,p);row=ocr.checked_request(db,p,material,str(request_id))
    if not row:return {'state':'not_found','request':None,'view':ocr.overview(db,p,material),'unit':None}
    return {'state':row.state,**ocr.reply(db,p,material,row)}


@router.post('/materials/{material_id}/ocr/requests/{request_id}/abandon')
def abandon(material_id:str,request_id:UUID,body:Abandon,p:P,db:DB):
    from pydantic import ValidationError
    try:parsed=COMMANDS[body.action].model_validate(body.original_request)
    except ValidationError:raise HTTPException(422,'原请求正文不符合原操作合同')
    if parsed.request_id!=request_id or ((body.action in {'save','unit_confirm','restore'})!=(body.target is not None)):
        raise HTTPException(422,'原请求编号或单元目标不完整')
    # 规范化只由原动作schema产生，UUID不变；不能删字段绕过原body签。
    normalized=Abandon.model_validate({**body.model_dump(mode='json'),'original_request':parsed.model_dump(mode='json')})
    result=ocr.abandon(db,p,material_id,str(request_id),normalized);db.commit();return result


@router.get('/materials/{material_id}/ocr/confirmations/{confirmation_id}/units/{unit_id}/image')
def confirmed_image(material_id:str,confirmation_id:str,unit_id:str,image_sha256:Hash,p:P,db:ReadDB):
    material=scoped(db,SetupMaterial,material_id,p);frozen=ocr.checked_confirmation(db,p,material,confirmation_id)
    item=next((u for u in frozen.data['snapshot']['units'] if u['unit_id']==unit_id),None)
    if not item or item['image_sha256']!=image_sha256:ocr.fail('原图不属于所选冻结确认')
    run=ocr.checked_run(db,p,material,frozen.run_id);unit=ocr.checked_unit(db,p,material,run,unit_id)
    return image_response(db,p,unit,image_sha256)


@router.get('/materials/{material_id}/ocr/citations/{block_id}')
def citation(material_id:str,block_id:str,p:P,db:ReadDB,quote:str=Query(min_length=1,max_length=250)):
    material=scoped(db,SetupMaterial,material_id,p)
    match=__import__('re').fullmatch(r'ocr-([a-f0-9-]{36})-\d+-\d+',block_id)
    if not match:raise HTTPException(404,'没有此冻结OCR引用')
    frozen=ocr.checked_confirmation(db,p,material,match.group(1))
    block=next((b for b in frozen.data['blocks'] if b['id']==block_id),None)
    if not block or quote not in block['text']:ocr.fail('引用文字与冻结确认版本不一致')
    return {'material_id':material.id,'material_version':frozen.material_version,'block':block,
            'confirmation':ocr.confirmation_view(frozen),'status':'confirmed_historical'}
