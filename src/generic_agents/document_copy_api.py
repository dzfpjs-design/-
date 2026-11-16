"""Authenticated fixed-intent local Word copies."""
from typing import Annotated, Literal
from uuid import UUID
from fastapi import APIRouter, Depends
from pydantic import Field, StrictInt, StrictStr
from .contracts import Contract
from .db import session
from .models import SetupMaterial
from .security import authenticated, Principal
from .services import scoped
from . import document_copy as copies

router = APIRouter()
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
Hash = Annotated[StrictStr, Field(pattern=r'^[a-f0-9]{64}$')]
Positive = Annotated[StrictInt, Field(ge=1)]

class Start(Contract):
    request_id: UUID
    expected_material_version: Positive
    source_sha256: Hash
    engine_manifest_hash: Hash
    revision_policy: Literal['accepted_final_v1']

class Cancel(Contract):
    expected_request_version: Positive
    reason: Annotated[StrictStr, Field(min_length=1, max_length=2000)]

@router.get('/document-copy/engine')
def engine(p:P): return copies.engine_status()

@router.get('/materials/{material_id}/document-copy')
def overview(material_id:str,p:P,db:DB):
    return copies.overview(db,p,scoped(db,SetupMaterial,material_id,p))

@router.post('/materials/{material_id}/document-copy')
def start(material_id:str,body:Start,p:P,db:DB):
    result=copies.start(db,p,material_id,body.model_dump(mode='json'));db.commit();return result

@router.get('/materials/{material_id}/document-copy/requests/{request_id}')
def request(material_id:str,request_id:UUID,p:P,db:DB):
    scoped(db,SetupMaterial,material_id,p)
    return copies.view(copies.find(db,p,material_id,str(request_id)))

@router.post('/materials/{material_id}/document-copy/requests/{request_id}/cancel')
def cancel(material_id:str,request_id:UUID,body:Cancel,p:P,db:DB):
    result=copies.cancel(db,p,material_id,str(request_id),body.model_dump(mode='json'));db.commit();return result
