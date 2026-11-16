"""Scoped, paginated human options for saved sensor associations; no authority grant."""
from fastapi import APIRouter, Query, Response, HTTPException
from sqlalchemy import select, func, or_, and_, cast, String, text, literal
from sqlalchemy.dialects.postgresql import JSONB

from .setup_api import P, DB
from .models import Entity, Capability, InstanceRelease, DomainInstance
from .services import scoped, published, as_dict
from .instance_domain import object_display, target_options

router = APIRouter(prefix='/api/mqtt')


def object_option(db, p, entity):
    package = published(db, p, entity.schema_capability_id, 'domain_package')
    properties = package.data['specification']['entity_schemas'][entity.entity_type]['properties']
    display = object_display(entity, package)
    fields = [{'key': key, 'label': spec.get('title') or key,
               **({'unit': spec['unit']} if 'unit' in spec else {})}
        for key, spec in properties.items() if spec.get('type') in {'number', 'integer'}]
    if not fields:
        raise HTTPException(404, '此对象没有可关联的数值字段，请保留原记录并核对对象类型')
    return {'id': entity.id, 'version': entity.version, 'label': display['label'],
        'type': display['type'], 'package_id': package.id, 'entity_type': entity.entity_type,
        'fields': fields, 'instance': target_options(db, p, entity)}


def page_query(db, p, model, query, response, limit, after):
    count = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    if after:
        cursor = scoped(db, model, after, p)
        # The cursor must belong to this filtered list, not just this workspace.
        if not db.scalar(query.with_only_columns(model.id).where(model.id == cursor.id)):
            raise HTTPException(404, '此选择列表的翻页位置不存在，请重新读取')
        query = query.where(or_(model.created_at < cursor.created_at,
            and_(model.created_at == cursor.created_at, model.id < cursor.id)))
    found = db.scalars(query.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)).all()
    rows = found[:limit]
    response.headers['X-Total-Count'] = str(count)
    response.headers['X-Next-Cursor'] = rows[-1].id if len(found) > limit else ''
    response.headers['Cache-Control'] = 'no-store'
    return rows


@router.get('/domain-options')
def domain_options(p: P, db: DB, response: Response,
                   limit: int = Query(default=50, ge=1, le=100),
                   after: str | None = Query(default=None, max_length=36),
                   q: str = Query(default='', max_length=100),
                   instance_id: str | None = Query(default=None, max_length=36)):
    query = select(Entity).join(Capability, Capability.id == Entity.schema_capability_id).where(
        Entity.workspace_id == p.workspace_id, Capability.workspace_id == p.workspace_id,
        Capability.kind == 'domain_package', Capability.status == 'published',
        text("""EXISTS (SELECT 1 FROM jsonb_each(CASE WHEN
          jsonb_typeof(capabilities.data->'specification'->'entity_schemas'->resource_entities.entity_type->'properties')='object'
          THEN capabilities.data->'specification'->'entity_schemas'->resource_entities.entity_type->'properties'
          ELSE '{}'::jsonb END) AS attribute
          WHERE attribute.value->>'type' IN ('number','integer'))"""))
    if instance_id:
        instance = scoped(db, DomainInstance, instance_id, p)
        query = query.where(Entity.namespace == instance.namespace)
    if q.strip():
        query = query.where(or_(Entity.external_id.contains(q.strip(), autoescape=True),
            cast(Entity.data, String).contains(q.strip(), autoescape=True)))
    rows = page_query(db, p, Entity, query, response, limit, after)
    return [object_option(db, p, entity) for entity in rows]


@router.get('/domain-options/{entity_id}')
def selected_object(entity_id: str, p: P, db: DB, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return object_option(db, p, scoped(db, Entity, entity_id, p))


def eligible_rules(p, entity):
    query = select(Capability).where(Capability.workspace_id == p.workspace_id,
        Capability.status == 'published', Capability.kind.in_(['tool', 'specialist_model']),
        Capability.data['execution'] == {'adapter': 'builtin', 'handler': 'numeric_rules'},
        Capability.data['allowed_agent_roles'].contains(['perception']))
    for key in ('cloud_allowed', 'external_allowed'):
        policy = Capability.data['data_policy'][key]
        query = query.where(or_(policy.is_(None), policy == cast(literal('null'), JSONB),
                               policy == cast(literal('false'), JSONB)))
    managed = Capability.data['specification']['managed_numeric_rule']
    return query.where(or_(managed.is_(None), func.jsonb_typeof(managed) == 'null',
        and_(managed['package_id'].as_string() == entity.schema_capability_id,
             managed['entity_type'].as_string() == entity.entity_type)))


@router.get('/rule-options')
def rule_options(entity_id: str, p: P, db: DB, response: Response,
                 limit: int = Query(default=50, ge=1, le=100),
                 after: str | None = Query(default=None, max_length=36),
                 q: str = Query(default='', max_length=100)):
    entity = scoped(db, Entity, entity_id, p)
    published(db, p, entity.schema_capability_id, 'domain_package')
    # Managed rules are tied to the confirmed object schema; legacy local rules
    # remain selectable and are still checked against exact fields on confirmation.
    query = eligible_rules(p, entity)
    if q.strip():
        query = query.where(or_(Capability.name.contains(q.strip(), autoescape=True),
            Capability.data['specification']['managed_numeric_rule']['title'].as_string().contains(q.strip(), autoescape=True)))
    return [as_dict(row) for row in page_query(db, p, Capability, query, response, limit, after)]


@router.get('/rule-options/{rule_id}')
def selected_rule(rule_id: str, entity_id: str, p: P, db: DB, response: Response):
    entity = scoped(db, Entity, entity_id, p)
    published(db, p, entity.schema_capability_id, 'domain_package')
    row = db.scalar(eligible_rules(p, entity).where(Capability.id == rule_id))
    if not row:
        raise HTTPException(404, '此本地规则当前不可选择，请保留原记录并核对规则发布状态')
    response.headers['Cache-Control'] = 'no-store'
    return as_dict(row)
