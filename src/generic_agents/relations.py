"""Version-bound relationship claims and bounded, workspace-scoped traversal."""
import copy
import re
from sqlalchemy import select, or_
from fastapi import HTTPException
from .models import Relation, RelationEvent, Entity, Observation, DomainInstance, InstanceRelease, Capability, now
from .contracts import RelationType, RelationQuery, digest
from .services import scoped, published, validate, audit, check_version, as_dict
from .security import require
from .temporal import instant


def normalized_time(value):
    """Database timestamps are facts only when they carry an explicit offset."""
    return instant(value.isoformat() if hasattr(value, 'isoformat') else value)


def lock_current_actor(db, p):
    """Keep actual account/role/session authorization valid through commit.

    Authenticated HTTP principals carry a UUID AuthSession row. Trusted local
    workers/tests may use a descriptive session marker; their actual user and
    membership are still checked and locked, never the stale Principal alone.
    """
    from uuid import UUID
    from .models import User, Membership, AuthSession
    try:
        UUID(p.session_id)
        http_session = True
    except (ValueError, TypeError, AttributeError):
        http_session = False
    query = select(User, Membership).join(Membership, Membership.user_id == User.id).where(
        User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role.in_(['admin','operator']), Membership.role == p.role)
    locked = [User, Membership]
    if http_session:
        query = query.add_columns(AuthSession).join(AuthSession, AuthSession.user_id == User.id).where(
            AuthSession.id == p.session_id, AuthSession.revoked.is_(False), AuthSession.expires_at > now())
        locked.append(AuthSession)
    row = db.execute(query.with_for_update(read=True, of=locked).execution_options(populate_existing=True)).first()
    if not row:
        raise HTTPException(403, '当前账号、工作区权限或登录会话已变化，原输入和历史保留，请重新登录后核对')


def generated_instance(db, p, package_id, *, write=False):
    release = db.scalar(select(InstanceRelease).where(InstanceRelease.workspace_id == p.workspace_id,
        InstanceRelease.package_id == package_id))
    if not release:
        return None
    # Lock the instance before any edge, endpoint or evidence row. This includes
    # legacy routes: final acceptance holds FOR SHARE on this same instance.
    instance = scoped(db, DomainInstance, release.instance_id, p, write)
    if write: lock_current_actor(db,p)
    if write and (instance.status != 'partially_active' or instance.active_release_id != release.id):
        raise HTTPException(409, '实例已暂停或关系属于旧版本，请保留记录并核对当前版本')
    package = published(db, p, package_id, 'domain_package')
    if digest(release.data['snapshot']) != release.configuration_hash or package.data['specification'].get('configuration_hash') != release.configuration_hash:
        raise HTTPException(409, '关系所属生成版本的内容摘要无法核对')
    return instance, release


def validate_types(specification):
    types = specification.get('relation_types', {})
    if not isinstance(types, dict) or len(types) > 30:
        raise HTTPException(422, '关系类型必须是最多30项的声明')
    from pydantic import ValidationError
    from .contracts import Manifest
    for name, definition in types.items():
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name):
            raise HTTPException(422, '关系类型编号不符合注册格式')
        try:
            typed = RelationType.model_validate(definition)
        except ValidationError:
            raise HTTPException(422, '关系类型缺少名称、端点类型或有效约束')
        if set(typed.source_types + typed.target_types) - set(specification['entity_schemas']):
            raise HTTPException(422, '关系端点类型必须在本业务包中声明')
        # Apply the same local-only JSON Schema rule as registered entities.
        try:
            Manifest(kind='domain_package', namespace='validation', name='relation', version='1.0.0',
                owner='schema', description='schema', input_schema=typed.attribute_schema)
        except ValueError:
            raise HTTPException(422,'关系属性Schema无效或引用了外部定义')


def definition(db, p, package_id, name):
    package = published(db, p, package_id, 'domain_package')
    raw = package.data['specification'].get('relation_types', {}).get(name)
    if raw is None:
        raise HTTPException(422, '业务包未声明此关系类型')
    return package, RelationType.model_validate(raw).model_dump()


def validate_query(db, p, value, entity_ids):
    spec = RelationQuery.model_validate(value).model_dump()
    if len(set(entity_ids)) != len(entity_ids) or not set(spec['root_ids']).issubset(entity_ids):
        raise HTTPException(422, '关系根对象必须在明确的授权查询范围中')
    if len(set(spec['root_ids'])) != len(spec['root_ids']) or len(set(spec['relation_types'])) != len(spec['relation_types']):
        raise HTTPException(422, '关系类型及根对象不能重复')
    for name in spec['relation_types']:
        definition(db, p, spec['package_id'], name)
    for identifier in entity_ids:
        row = scoped(db, Entity, identifier, p)
        if row.schema_capability_id != spec['package_id']:
            raise HTTPException(422, '关系查询范围必须属于登记的业务包版本')
    if spec['at'] and instant(spec['at']) > now():
        raise HTTPException(422, '不能把未来关系时点当成已核实事实')
    return spec


def evidence_snapshot(db, p, refs, package_id, *, lock=False):
    keys = [(r['kind'], r['id']) for r in refs]
    if len(set(keys)) != len(keys):
        raise HTTPException(422, '同一来源不能重复计为独立证据')
    result = []
    locked_rows = {}
    if lock:
        for kind, identifier in sorted(keys):
            locked_rows[(kind,identifier)] = scoped(db, Entity if kind == 'entity' else Observation, identifier, p, True)
        for source_id in sorted({row.source_id for (kind,_),row in locked_rows.items() if kind == 'observation'}):
            scoped(db, Capability, source_id, p, True)
            published(db,p,source_id,'source')
    for kind, identifier in sorted(keys):
        row = locked_rows[(kind,identifier)] if lock else scoped(db, Entity if kind == 'entity' else Observation, identifier, p)
        if kind == 'entity':
            if row.schema_capability_id != package_id:
                raise HTTPException(422, '关系依据必须来自本业务包对象')
            value = as_dict(row)
        else:
            source=published(db,p,row.source_id,'source')
            if row.status != 'processed' or not row.entity_id:
                raise HTTPException(409, '观测尚未处理或未关联正式对象，不能作为关系依据')
            if row.observed_at is None:raise HTTPException(409,'观测采集时间未知，不能作为已核实的关系依据')
            if normalized_time(row.observed_at)>now():raise HTTPException(409,'未来观测不能作为已存在的关系依据')
            entity = scoped(db, Entity, row.entity_id, p)
            if entity.schema_capability_id != package_id:
                raise HTTPException(422, '关系依据观测不属于业务包')
            verification = row.data.get('human_verification', {})
            fp = digest({'extracted':row.data.get('extracted',{}),'evidence_ids':row.data.get('evidence_ids',[]),
                'invocation_id':row.data.get('invocation_id')})
            if verification.get('input_hash') == fp and verification.get('decision') == 'rejected':
                raise HTTPException(409, '被拒绝的观测不能作为关系依据')
            from .evidence import evidence_bytes
            for evidence_id in row.data.get('evidence_ids', []):
                evidence_bytes(db, p, evidence_id)
            value = {**as_dict(row),'source_capability_hash':source.content_hash}
        result.append({'kind':kind,'id':identifier,'version':row.version,'hash':digest(value),
            'snapshot':copy.deepcopy(value)})
    return result


def payload(db, p, body):
    generated = generated_instance(db, p, body.package_id, write=True)
    package, spec = definition(db, p, body.package_id, body.relation_type)
    endpoints = {identifier:scoped(db, Entity, identifier, p, True)
        for identifier in sorted({body.source_entity_id,body.target_entity_id})}
    source, target = endpoints[body.source_entity_id], endpoints[body.target_entity_id]
    if generated and any(e.namespace != generated[0].namespace for e in endpoints.values()):
        raise HTTPException(422, '关系端点不属于此实例')
    if any(e.schema_capability_id != package.id for e in endpoints.values()):
        raise HTTPException(422, '关系端点必须属于同一登记业务包版本')
    if source.entity_type not in spec['source_types'] or target.entity_type not in spec['target_types']:
        raise HTTPException(422, '关系方向或端点类型不符合业务包定义')
    if source.id == target.id and not spec['allow_self']:
        raise HTTPException(422, '此关系类型不允许自关联')
    validate(spec['attribute_schema'], body.attributes)
    start, end = instant(body.valid_from), instant(body.valid_until) if body.valid_until else None
    if end and end <= start:
        raise HTTPException(422, '关系有效期结束须晚于开始')
    refs = [r.model_dump() for r in body.evidence_refs]
    evidence = evidence_snapshot(db,p,refs,package.id,lock=True)
    if generated:
        real = [r for r in evidence if r['kind'] == 'observation' and r['snapshot']['data'].get('evidence_ids') and r['snapshot']['data'].get('extracted')]
        # New document-backed commands validate and attach originals separately.
        if not real and not getattr(body, 'document_refs', []):
            raise HTTPException(422, '实际关系需有承载内容的观测原件或文档原文；端点档案和关系类型出处不能代替事实依据')
        for ref in evidence:
            entity_id = ref['id'] if ref['kind'] == 'entity' else ref['snapshot']['entity_id']
            if scoped(db, Entity, entity_id, p).namespace != generated[0].namespace:
                raise HTTPException(422, '关系事实依据不属于此实例')
    data = {'attributes':body.attributes,'statement':body.statement,'evidence_refs':refs,
        'evidence_snapshot':evidence,'package_hash':package.content_hash,
        'endpoint_versions':{i:e.version for i,e in endpoints.items()},'reason':body.reason}
    return start, end, data


def event(db,p,row,operation,reason,before=None):
    db.add(RelationEvent(workspace_id=p.workspace_id, relation_id=row.id, actor_id=p.user_id,
        data={'operation':operation,'reason':reason,'before':before,'after':copy.deepcopy(as_dict(row))}))
    audit(db,p,'relation.'+operation,row.id,{'version':row.version,'reason':reason})
    from .quality import changed
    changed(db,p,row.package_id,'relation:'+row.id+':'+str(row.version),row.source_entity_id)
    from .native_goals import source_changed
    for identity in sorted({row.source_entity_id, row.target_entity_id}):
        source_changed(db, p, identity, 'relation:'+row.id+':'+str(row.version))


def claim_hash(row):
    extra = {key: row.data[key] for key in ['instance_binding', 'document_refs', 'document_snapshot'] if key in row.data}
    return digest({'package_id':row.package_id,'relation_type':row.relation_type,
        'source_entity_id':row.source_entity_id,'target_entity_id':row.target_entity_id,
        'valid_from':instant(row.valid_from.isoformat()).isoformat(),
        'valid_until':instant(row.valid_until.isoformat()).isoformat() if row.valid_until else None,
        'data':{key:row.data[key] for key in ['attributes','statement','evidence_refs','evidence_snapshot',
            'package_hash','endpoint_versions']}, **extra})


def create(db,p,body):
    require(p,'admin','operator')
    start,end,data = payload(db,p,body)
    row = Relation(workspace_id=p.workspace_id,package_id=body.package_id,relation_type=body.relation_type,
        source_entity_id=body.source_entity_id,target_entity_id=body.target_entity_id,
        valid_from=start,valid_until=end,data=data)
    db.add(row); db.flush(); row.data={**row.data,'claim_hash':claim_hash(row)}
    event(db,p,row,'create',body.reason)
    return row


def edit(db,p,identifier,body):
    require(p,'admin','operator')
    candidate = scoped(db,Relation,identifier,p)
    generated_instance(db,p,candidate.package_id,write=True)
    row = scoped(db,Relation,identifier,p,True); check_version(row,body.expected_version)
    if (body.package_id,body.relation_type,body.source_entity_id,body.target_entity_id) != (
            row.package_id,row.relation_type,row.source_entity_id,row.target_entity_id):
        raise HTTPException(422,'更换关系类型或端点请新建记录并撤回错误记录，不能改写历史身份')
    before = copy.deepcopy(as_dict(row)); start,end,data = payload(db,p,body)
    row.data=data; row.valid_from=start; row.valid_until=end; row.status='proposed'; row.version+=1
    row.data={**row.data,'claim_hash':claim_hash(row)}
    event(db,p,row,'correct',body.reason,before); return row


def current_problem(db,p,row,at,*,lock=False):
    try:
        if row.data.get('claim_hash')!=claim_hash(row): return '关系声明或版本绑定已变化，需要重新核查'
    except (KeyError, TypeError, ValueError, HTTPException):
        return '关系声明或版本绑定无法核对，需要重新核查'
    try:
        if lock: scoped(db,Capability,row.package_id,p,True)
        package, spec = definition(db,p,row.package_id,row.relation_type)
        if package.content_hash != row.data['package_hash']: return '关系包版本绑定已变化'
        for identifier,version in sorted(row.data['endpoint_versions'].items()):
            if scoped(db,Entity,identifier,p,lock).version != version: return '关系端点档案版本已变化，需要重新核查'
        fresh = evidence_snapshot(db,p,row.data['evidence_refs'],row.package_id,lock=lock)
        if fresh != row.data['evidence_snapshot']: return '关系来源或证据版本已变化，需要重新核查'
    except HTTPException as error:
        return str(error.detail)
    if row.data.get('instance_binding'):
        from .instance_relations import validate_saved_sources
        try: validate_saved_sources(db, p, row, lock=lock)
        except HTTPException as error: return str(error.detail)
    if normalized_time(row.valid_from) > normalized_time(at) or (row.valid_until and normalized_time(row.valid_until) <= normalized_time(at)): return '关系有效期未覆盖所需时点'
    return None


def review(db,p,identifier,body):
    require(p,'admin','operator')
    candidate=scoped(db,Relation,identifier,p)
    generated=generated_instance(db,p,candidate.package_id,write=True)
    row=scoped(db,Relation,identifier,p,True); check_version(row,body.expected_version)
    if generated and body.decision == 'confirmed':
        from .instance_relations import validate_real_sources
        validate_real_sources(db,p,row,generated[0],generated[1],lock=True)
    before=copy.deepcopy(as_dict(row))
    if body.decision=='confirmed':
        problem=current_problem(db,p,row,now(),lock=True)
        if problem: raise HTTPException(409,problem)
    row.status=body.decision; row.version+=1
    row.data={**row.data,'review':{'decision':body.decision,'actor_id':p.user_id,
        'at':now().isoformat(),'reason':body.reason}}
    event(db,p,row,'review',body.reason,before); return row


def traverse(db,p,value,entity_ids):
    spec=validate_query(db,p,value,entity_ids); at=instant(spec['at']) if spec['at'] else now()
    permitted=set(entity_ids); roots=set(spec['root_ids']); visited=set(roots); frontier=set(roots)
    edges=[]; gaps=[]; conflicts=[]; excluded=[]; read_ids=set(); limit=50; truncated=False
    origin = Relation.source_entity_id if spec['direction']=='outgoing' else Relation.target_entity_id
    destination = Relation.target_entity_id if spec['direction']=='outgoing' else Relation.source_entity_id
    for hop in range(1,spec['max_hops']+1):
        # Read boundary edges, but never disclose an out-of-scope endpoint or its attributes.
        rows=db.scalars(select(Relation).where(Relation.workspace_id==p.workspace_id,
            Relation.package_id==spec['package_id'],Relation.relation_type.in_(spec['relation_types']),
            origin.in_(frontier)).order_by(Relation.id).limit(limit-len(read_ids)+1)).all()
        if len(rows)>limit-len(read_ids): rows=rows[:limit-len(read_ids)]; truncated=True
        reached=set(); found=set()
        for row in rows:
            read_ids.add(row.id)
            first=getattr(row,origin.key); other=getattr(row,destination.key)
            if other not in permitted:
                gaps.append({'entity_id':first,'reason':'有关系端点在授权对象范围之外，请明确范围后重新查询','route':'data'})
                continue
            if any((ref['snapshot']['id'] if ref['kind']=='entity' else ref['snapshot'].get('entity_id'))
                    not in permitted for ref in row.data['evidence_snapshot']):
                gaps.append({'entity_id':first,'reason':'关系依据包含授权查询范围之外的对象，请明确范围后重新查询','route':'data'})
                continue
            reason = ('关系尚未人工确认或已拒绝/撤回' if row.status!='confirmed' else current_problem(db,p,row,at))
            if not reason and spec['at'] and instant(row.data['review']['at'])>at:
                reason='当前关系确认晚于历史时点，不能用当前版本证明历史关系'
            if reason:
                excluded.append({'relation_id':row.id,'entity_id':first,'status':row.status,'reason':reason})
                gaps.append({'entity_id':first,'relation_id':row.id,'reason':reason,'route':'data'}); continue
            found.add(first)
            if row.id not in {e['relation_id'] for e in edges}:
                edges.append({'relation_id':row.id,'version':row.version,'relation_type':row.relation_type,
                    'source_entity_id':row.source_entity_id,'target_entity_id':row.target_entity_id,
                    'hop':hop,'attributes':row.data['attributes'],'statement':row.data['statement'],
                    'valid_from':row.valid_from.isoformat(),'valid_until':row.valid_until.isoformat() if row.valid_until else None,
                    'verification':'confirmed','review':copy.deepcopy(row.data['review']),
                    'evidence':copy.deepcopy(row.data['evidence_snapshot']),
                    **({'document_evidence':copy.deepcopy(row.data['document_snapshot'])} if row.data.get('document_snapshot') else {})})
            if other not in visited: reached.add(other)
        for root in frontier & roots - found:
            gaps.append({'entity_id':root,'reason':'没有可用的已确认关系记录；不能据此断言关系不存在','route':'data'})
        visited.update(reached); frontier=reached
        if not frontier or truncated: break
    # max_hops is the explicitly requested query depth, not a demand to prove
    # closure of the entire graph. Edges after that depth are outside this
    # question: do not inspect them or turn a complete one-hop answer into a
    # false data gap. The independent 50-edge budget remains fail-closed below;
    # missing/unconfirmed/expired/out-of-scope evidence inside the requested
    # depth and cardinality conflicts above/below still prevent sufficiency.
    if truncated: gaps.append({'reason':'关系读取超过50条上限，当前证据不完整','route':'data'})
    for name in spec['relation_types']:
        _,rule=definition(db,p,spec['package_id'],name)
        key={'one_per_source':'source_entity_id','one_per_target':'target_entity_id'}.get(rule['cardinality'])
        if key:
            groups={}
            for edge in edges:
                if edge['relation_type']==name: groups.setdefault(edge[key],[]).append(edge)
            for entity,items in groups.items():
                opposite='target_entity_id' if key=='source_entity_id' else 'source_entity_id'
                if len({e[opposite] for e in items})>1:
                    conflicts.append({'entity_id':entity,'relation_ids':[e['relation_id'] for e in items],
                        'reason':'已确认关系违反登记基数约束，保留争议等待责任人核查'})
                # A scoped path alone cannot prove uniqueness. Probe only for the
                # existence of an additional active claim; never expose its ID or
                # an unselected endpoint's facts, and never expand the user scope.
                extra=db.scalar(select(Relation.id).where(Relation.workspace_id==p.workspace_id,
                    Relation.package_id==spec['package_id'],Relation.relation_type==name,
                    getattr(Relation,key)==entity,getattr(Relation,opposite).not_in({e[opposite] for e in items}),
                    Relation.status=='confirmed',Relation.valid_from<=at,
                    or_(Relation.valid_until.is_(None),Relation.valid_until>at)).limit(1))
                if extra:gaps.append({'entity_id':entity,'reason':'基数约束另有未展开的登记关系，当前范围无法证明唯一性，请明确范围后核查','route':'data'})
    selected=sorted(visited if spec['include_roots'] else visited-roots)
    return {'entity_ids':selected,'relation_evidence':edges,'relation_gaps':gaps,
        'relation_conflicts':conflicts,'relation_excluded':excluded,'spec':spec,'evaluated_at':at.isoformat(),
        'read_limit':limit,'truncated':truncated}


def query(db,p,entity_ids,fields,value,**kwargs):
    from .services import query_evidence
    traversal=traverse(db,p,value,entity_ids)
    # Full snapshots belong to the review record; a selected-field query must not
    # expose unrelated source attributes to an answer or the business cloud model.
    for edge in traversal['relation_evidence']:
        for ref in edge['evidence']:
            source=ref['snapshot'];attrs=(source['data'] if ref['kind']=='entity'
                else source['data'].get('extracted',{}).get('attributes',{}))
            selected={k:v for k,v in attrs.items() if not fields or k in fields}
            ref['snapshot']={k:source[k] for k in ['id','entity_id','observed_at','created_at'] if k in source}
            ref['snapshot']['data']=selected if ref['kind']=='entity' else {'extracted':{'attributes':selected},
                'evidence_ids':source['data'].get('evidence_ids',[])}
    result=query_evidence(db,p,traversal['entity_ids'],fields,**kwargs)
    gaps=[*result['gaps'],*traversal['relation_gaps']]
    conflicts=[*result['conflicts'],*traversal['relation_conflicts']]
    return {**result,'status':'conflict' if conflicts else 'insufficient' if gaps or not traversal['entity_ids'] else result['status'],
        'gaps':gaps,'conflicts':conflicts,'relations':traversal,'selected_entity_ids':traversal['entity_ids']}


def case_valid(db,p,evidence):
    """Keep historical reviews; revoked relationship lineage cannot seed trusted reuse."""
    saved=evidence.get('relations')
    if not saved:return True
    if saved.get('relation_gaps') or saved.get('relation_conflicts') or not saved.get('relation_evidence'):return False
    for edge in saved['relation_evidence']:
        try:
            row=scoped(db,Relation,edge['relation_id'],p)
            if row.status!='confirmed' or row.version!=edge['version'] or current_problem(db,p,row,now()):return False
        except HTTPException:return False
    scope=set(saved['spec']['root_ids'])|set(saved['entity_ids'])
    for edge in saved['relation_evidence']:
        for ref in edge['evidence']:
            scope.add(ref['snapshot']['id'] if ref['kind']=='entity' else ref['snapshot']['entity_id'])
    try:fresh=traverse(db,p,saved['spec'],sorted(scope))
    except HTTPException:return False
    keys=lambda result:{(e['relation_id'],e['version']) for e in result['relation_evidence']}
    if fresh['relation_gaps'] or fresh['relation_conflicts'] or keys(fresh)!=keys(saved) or fresh['entity_ids']!=saved['entity_ids']:
        return False
    return True
