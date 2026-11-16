"""Operator relationship evidence, review and immutable history API."""
from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy import select
from .db import session
from .security import authenticated, Principal
from .models import Relation, RelationEvent
from .contracts import RelationCreate, RelationEdit, RelationReview
from .services import scoped, as_dict
from . import relations

router=APIRouter(prefix='/api/relations')
DB=Annotated[object,Depends(session)]
P=Annotated[Principal,Depends(authenticated)]


@router.get('')
def list_relations(p:P,db:DB):
    rows=db.scalars(select(Relation).where(Relation.workspace_id==p.workspace_id)
        .order_by(Relation.created_at.desc()).limit(101)).all()
    return {'items':[as_dict(row) for row in rows[:100]],'truncated':len(rows)>100}


@router.post('',status_code=201)
def create_relation(body:RelationCreate,p:P,db:DB):
    row=relations.create(db,p,body); db.commit(); return as_dict(row)


@router.get('/{identifier}')
def get_relation(identifier:str,p:P,db:DB):
    return as_dict(scoped(db,Relation,identifier,p))


@router.get('/{identifier}/history')
def history(identifier:str,p:P,db:DB):
    scoped(db,Relation,identifier,p)
    return [as_dict(row) for row in db.scalars(select(RelationEvent).where(
        RelationEvent.workspace_id==p.workspace_id,RelationEvent.relation_id==identifier)
        .order_by(RelationEvent.created_at,RelationEvent.id))]


@router.patch('/{identifier}')
def correct_relation(identifier:str,body:RelationEdit,p:P,db:DB):
    row=relations.edit(db,p,identifier,body); db.commit(); return as_dict(row)


@router.post('/{identifier}/review')
def review_relation(identifier:str,body:RelationReview,p:P,db:DB):
    row=relations.review(db,p,identifier,body); db.commit(); return as_dict(row)
