"""Durable, actor-bound relationship work in a fixed generated domain release."""
import copy
import json
from types import SimpleNamespace
from typing import Literal
from uuid import UUID
from pydantic import Field, StrictInt, ValidationError, field_validator
from fastapi import HTTPException
from sqlalchemy import select, func
from .contracts import Contract, RelationEvidenceRef, digest
from .models import DomainInstance, InstanceRelease, Entity, Relation, RelationEvent, Observation, SetupMaterial, Capability, Audit, now
from .services import scoped, published, require, check_version, as_dict, audit
from .instance_domain import object_display
from . import relations

MAX_COMMANDS = 300

class Command(Contract):
    request_id: UUID
    expected_version: StrictInt = Field(ge=1)
    release_id: str
    reason: str = Field(min_length=1, max_length=2000)
    @field_validator('reason')
    @classmethod
    def meaningful(cls, value):
        if not value.strip(): raise ValueError('请填写操作依据')
        return value

class DocumentRef(Contract):
    material_id: str
    block_id: str
    quote: str = Field(min_length=1, max_length=6000)

class Claim(Command):
    relation_type: str = Field(min_length=1, max_length=100)
    source_entity_id: str
    target_entity_id: str
    attributes: dict = Field(default_factory=dict)
    statement: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[RelationEvidenceRef] = Field(default_factory=list, max_length=10)
    document_refs: list[DocumentRef] = Field(default_factory=list, max_length=10)
    valid_from: str
    valid_until: str | None = None

class Review(Command):
    decision: Literal['confirmed', 'rejected', 'withdrawn']

class Draft(Command):
    draft: dict

class Recovery(Contract):
    release_id: str
    operation: Literal['draft', 'create', 'correct', 'review']
    original_request: dict
    relation_id: str | None = None

class Abandon(Recovery):
    expected_version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


def context(db, p, instance_id, release_id, *, lock=False, write=False):
    instance = scoped(db, DomainInstance, instance_id, p, lock)
    release = scoped(db, InstanceRelease, release_id, p)
    if release.instance_id != instance.id or not release.package_id:
        raise HTTPException(404, '版本不存在或不属于此实例')
    package = scoped(db, Capability, release.package_id, p)
    if digest(release.data['snapshot']) != release.configuration_hash or package.data['specification'].get('configuration_hash') != release.configuration_hash:
        raise HTTPException(409, '关系所属生成版本内容摘要无法核对')
    current = instance.status == 'partially_active' and instance.active_release_id == release.id
    problem = None
    try: published(db, p, package.id, 'domain_package')
    except HTTPException as error: problem = str(error.detail)
    if not current: problem = '实例已暂停、尚未启用或正在查看历史版本'
    if write:
        require(p, 'admin', 'operator')
        if problem: raise HTTPException(409, problem)
    return instance, release, package, problem


def ledger(instance):
    entries = instance.data.get('relation_commands', [])
    if not isinstance(entries, list) or ('relation_commands_hash' in instance.data and digest(entries) != instance.data['relation_commands_hash']):
        raise HTTPException(409, '关系操作历史摘要无法核对，保留原请求')
    if entries and 'relation_commands_hash' not in instance.data:
        raise HTTPException(409, '关系操作历史缺少摘要，保留原请求')
    return entries


def request_guard(db, p, instance, request_id, *, lock=False):
    key = str(request_id)
    if lock:
        # A UUID is not reusable on another instance, actor or operation. The
        # advisory lock closes concurrent first submissions across instances.
        value = UUID(key).int % (2**63 - 1)
        db.execute(select(func.pg_advisory_xact_lock(value)))
    rows = list(db.scalars(select(Audit).where(Audit.data['request_id'].as_string() == key,
        Audit.action.in_(['instance.relation.draft','instance.relation.create','instance.relation.correct',
            'instance.relation.review','instance.relation.abandon'])).limit(2)))
    if len(rows)>1: raise HTTPException(409,'关系请求收据重复，无法核对原操作')
    if rows:
        receipt=rows[0]
        if receipt.workspace_id != p.workspace_id or receipt.actor_id != p.user_id or receipt.object_id != instance.id:
            raise HTTPException(409,'原关系请求编号属于其他提交者或实例，不能更换原请求')
        if receipt.data.get('schema') == 'instance-relation-abandoned-request-v1':
            data=receipt.data
            if digest({k:v for k,v in data.items() if k!='record_hash'}) != data.get('record_hash'):
                raise HTTPException(409,'原关系请求作废收据摘要无法核对')
            return {**data,'actor_id':receipt.actor_id,'status':'abandoned'}
        entry=next((x for x in ledger(instance) if x['request_id']==key),None)
        if not entry or entry['body_hash'] != receipt.data.get('body_hash'):
            raise HTTPException(409,'关系操作历史与原请求收据不一致，保留原请求')


def command_identity(body, operation, relation_id=None):
    return {'operation': operation, 'relation_id': relation_id, 'request': body.model_dump(mode='json')}


def old_command(instance, p, body, operation, relation_id=None, receipt=None):
    key = str(body.request_id)
    item = receipt or next((x for x in ledger(instance) if x['request_id'] == key), None)
    if item and (item['actor_id'] != p.user_id or item['body_hash'] != digest(command_identity(body, operation, relation_id))):
        raise HTTPException(409, '原关系请求编号不能更换提交者、内容或操作')
    return item


def replay(instance, p, body, operation, relation_id=None, receipt=None):
    entry = old_command(instance, p, body, operation, relation_id, receipt)
    if not entry: return None
    if entry.get('status') == 'abandoned':
        raise HTTPException(409, '原关系请求已明确作废，请保留草稿并使用新编号')
    response = entry.get('response')
    if not response or digest(response) != entry.get('response_hash'):
        raise HTTPException(409, '原关系请求保存结果摘要无法核对')
    return copy.deepcopy(response)


def saved_command_count(instance):
    return sum(entry.get('status') != 'abandoned' for entry in ledger(instance))


def abandon_request(db,p,instance,original,operation,relation_id,reason):
    # Recovery never appends another large request/response to the full ledger.
    # The UUID advisory lock plus globally checked Audit receipt serializes a
    # late submission against this compact, actor/scope/body-bound tombstone.
    data={'schema':'instance-relation-abandoned-request-v1','request_id':str(original.request_id),
        'release_id':original.release_id,'operation':operation,'relation_id':relation_id,
        'body_hash':digest(command_identity(original,operation,relation_id)),
        'reason':reason,'at':now().isoformat()}
    data['record_hash']=digest(data)
    audit(db,p,'instance.relation.abandon',instance.id,data)


def append_command(db, p, instance, body, operation, response, relation_id=None, *, abandoned=False, abandon_reason=None):
    entries = ledger(instance)
    if saved_command_count(instance) >= MAX_COMMANDS: raise HTTPException(409, '此实例关系操作已达300次上限，全部历史保留，请联系管理员')
    identity = command_identity(body, operation, relation_id)
    entry = {'request_id': str(body.request_id), 'actor_id': p.user_id,
        'body_hash': digest(identity), **identity, 'at': now().isoformat(),
        'status': 'abandoned' if abandoned else 'saved'}
    if not abandoned: entry.update(response=copy.deepcopy(response), response_hash=digest(response))
    else: entry['abandon_reason'] = abandon_reason
    entries = [*entries, entry]
    instance.data = {**instance.data, 'relation_commands': entries, 'relation_commands_hash': digest(entries)}
    audit(db, p, 'instance.relation.' + ('abandon' if abandoned else operation), instance.id,
        {'request_id': str(body.request_id), 'release_id': body.release_id, 'body_hash': entry['body_hash'],
         'relation_id': relation_id, 'reason': abandon_reason if abandoned else body.reason})


def document_snapshot(db, p, release, refs, *, lock=False):
    from .setup_understanding import frozen_material
    from .instance_compiler import verify_material_snapshots
    allowed = {x['id']: x for x in release.data['snapshot'].get('materials', [])}
    keys = [(r['material_id'], r['block_id'], r['quote']) for r in refs]
    if len(set(keys)) != len(keys): raise HTTPException(422, '同一原文不能重复计为关系证据')
    result = []
    for material_id, block_id, quote in sorted(keys):
        if material_id not in allowed: raise HTTPException(422, '关系文档不属于此固定实例版本')
        material = scoped(db, SetupMaterial, material_id, p, lock)
        verify_material_snapshots(db, p, [allowed[material_id]])
        frozen = frozen_material(db, p, material)
        block = next((b for b in frozen['blocks'] if b['id'] == block_id), None)
        if not block or not quote.strip() or quote not in block['text']:
            raise HTTPException(409, '关系事实原文已变化或引用不是此段逐字原文，请重新核对')
        result.append({'material_id': material_id, 'block_id': block_id, 'quote': quote,
            'material_name': material.name, 'version': frozen['version'], 'sha256': frozen['hash'],
            'evidence_id': frozen['evidence_id'], 'blocks_hash': frozen['blocks_hash'],
            'block_hash': digest(block), 'location': copy.deepcopy(block.get('location', {})),
            'confirmation': frozen['confirmation']})
    return result


def validate_real_sources(db, p, row, instance, release, *, require_fact_mentions=True, lock=False):
    refs = row.data.get('evidence_refs', [])
    snapshots = relations.evidence_snapshot(db, p, refs, row.package_id, lock=lock)
    actual = False
    for item in snapshots:
        entity_id = item['id'] if item['kind'] == 'entity' else item['snapshot']['entity_id']
        if scoped(db, Entity, entity_id, p).namespace != instance.namespace:
            raise HTTPException(422, '关系依据不属于此实例')
        if item['kind'] == 'observation':
            data = item['snapshot']['data']
            actual = actual or bool(data.get('evidence_ids') and data.get('extracted'))
            typed = data.get('extracted', {}).get('relation')
            if isinstance(typed, dict):
                for key, expected in [('source_entity_id',row.source_entity_id),('target_entity_id',row.target_entity_id),('relation_type',row.relation_type)]:
                    if key in typed and typed[key] != expected:
                        raise HTTPException(409, '观测中的明确关系端点或类型与本次声明不一致')
    docs = row.data.get('document_refs', [])
    if docs:
        fresh = document_snapshot(db, p, release, docs, lock=lock)
        if fresh != row.data.get('document_snapshot'): raise HTTPException(409, '关系事实文档版本已变化，请修正后重新确认')
        if require_fact_mentions:
            package = scoped(db, Capability, release.package_id, p)
            names = []
            for entity_id in (row.source_entity_id,row.target_entity_id):
                entity = scoped(db, Entity, entity_id, p)
                display = object_display(entity, package)
                labels = [display['label']] if display['label'] != display['type'] else []
                if entity.external_id and not entity.external_id.startswith('auto_'): labels.append(entity.external_id)
                names.append(labels)
            if not any(all(any(label in item['quote'] for label in labels) for labels in names) for item in fresh):
                raise HTTPException(409, '文档事实引用须同时包含两端具体对象名称或明确业务编号；类型规则不能证明这两个对象的关系，请补充实际原文后重新核查')
        actual = True
    if not actual: raise HTTPException(422, '关系事实需有承载内容的观测原件或文档原文；端点档案与类型出处不足以证明关系')


def validate_saved_sources(db, p, row, *, lock=False):
    binding = row.data['instance_binding']
    instance, release, _, _ = context(db, p, binding['instance_id'], binding['release_id'])
    if row.package_id != release.package_id or binding.get('configuration_hash') != release.configuration_hash:
        raise HTTPException(409, '关系所属实例版本关联无法核对')
    for identity in (row.source_entity_id, row.target_entity_id):
        entity = scoped(db, Entity, identity, p, lock)
        if entity.namespace != instance.namespace or entity.schema_capability_id != release.package_id:
            raise HTTPException(409, '关系端点范围已变化')
    validate_real_sources(db, p, row, instance, release, lock=lock)


def scoped_record(db, p, instance, release, relation_id, *, lock=False):
    row = scoped(db, Relation, relation_id, p, lock)
    if row.package_id != release.package_id:
        raise HTTPException(404, '关系不存在或不属于此实例版本')
    for identifier in (row.source_entity_id, row.target_entity_id):
        entity = scoped(db, Entity, identifier, p)
        if entity.namespace != instance.namespace:
            raise HTTPException(404, '关系不属于此实例')
    return row


def evidence_card(db, p, package, ref):
    snapshot=copy.deepcopy(ref['snapshot'])
    if ref['kind']=='entity':
        entity=scoped(db,Entity,ref['id'],p)
        title='登记档案：'+object_display(entity,package)['label']
        source_name='对象登记档案'
    else:
        entity=scoped(db,Entity,snapshot['entity_id'],p)
        source=scoped(db,Capability,snapshot['source_id'],p)
        source_name=source.data.get('description') or '已登记观测来源'
        title=object_display(entity,package)['label']+' · '+source_name[:100]
        snapshot['source_name']=source_name
    return {'kind':ref['kind'],'id':ref['id'],'title':title,'source_name':source_name,'snapshot':snapshot}


def record_view(db, p, instance, release, package, row):
    source = scoped(db, Entity, row.source_entity_id, p)
    target = scoped(db, Entity, row.target_entity_id, p)
    definition = package.data['specification'].get('relation_types', {}).get(row.relation_type, {})
    try: problem = relations.current_problem(db, p, row, now())
    except (KeyError, TypeError, ValueError): problem = '关系保存内容无法核对，请保留历史并联系管理员'
    conflicts = []
    cardinality = definition.get('cardinality')
    key = {'one_per_source':'source_entity_id','one_per_target':'target_entity_id'}.get(cardinality)
    if key and row.status == 'confirmed':
        opposite = 'target_entity_id' if key == 'source_entity_id' else 'source_entity_id'
        candidates = db.scalars(select(Relation).where(Relation.workspace_id == p.workspace_id,
            Relation.package_id == package.id, Relation.relation_type == row.relation_type,
            Relation.status == 'confirmed', getattr(Relation,key) == getattr(row,key),
            getattr(Relation,opposite) != getattr(row,opposite)).order_by(Relation.id))
        for other in candidates:
            if relations.normalized_time(other.valid_from) <= now() and (not other.valid_until or relations.normalized_time(other.valid_until)>now()):
                conflicts.append({'relation_id':other.id,'reason':'存在违反登记基数约束的另一已确认声明，当前关系链不可用于充分查询或验收'})
    return {**copy.deepcopy(as_dict(row)), 'instance_id': instance.id, 'release_id': release.id,
        'title': definition.get('title', row.relation_type),
        'source_title': object_display(source, package)['label'], 'target_title': object_display(target, package)['label'],
        'current_problem': problem, 'conflicts':conflicts, 'usable':row.status=='confirmed' and not problem and not conflicts, 'document_evidence': copy.deepcopy(row.data.get('document_snapshot', [])),
        'evidence_summary': [evidence_card(db,p,package,r) for r in row.data.get('evidence_snapshot', [])],
        'cardinality_policy': 'conflicting_confirmed_claims_are_preserved_and_block_query'}


def catalog(db, p, instance_id, release_id):
    instance, release, package, problem = context(db, p, instance_id, release_id)
    types = [{'id': key, **copy.deepcopy(value)} for key, value in package.data['specification'].get('relation_types', {}).items()]
    entities = list(db.scalars(select(Entity).where(Entity.workspace_id == p.workspace_id,
        Entity.namespace == instance.namespace, Entity.schema_capability_id == package.id).order_by(Entity.id).limit(201)))
    options = []
    source_problems = []
    ids = [e.id for e in entities[:200]]
    rows = list(db.scalars(select(Observation).where(Observation.workspace_id == p.workspace_id,
        Observation.entity_id.in_(ids), Observation.status == 'processed').order_by(Observation.id).limit(101))) if ids else []
    for row in rows[:100]:
        try:
            snapshot = relations.evidence_snapshot(db, p, [{'kind':'observation','id':row.id}], package.id)[0]['snapshot']
            if not snapshot['data'].get('evidence_ids') or not snapshot['data'].get('extracted'): continue
            from zoneinfo import ZoneInfo
            entity=scoped(db,Entity,row.entity_id,p)
            source=scoped(db,Capability,row.source_id,p)
            source_name=source.data.get('description') or '已登记观测来源'
            clock=relations.normalized_time(row.observed_at).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S 中国时间')
            options.append({'kind':'observation','id':row.id,'entity_id':row.entity_id,
                'title':object_display(entity,package)['label']+' · '+source_name[:100]+' · '+clock,
                'source_name':source_name,'observed_at':row.observed_at.isoformat(),
                'attributes':copy.deepcopy(row.data.get('extracted',{}).get('attributes',{})),
                'evidence_ids':copy.deepcopy(row.data.get('evidence_ids',[])), 'source_id':row.source_id, 'version':row.version})
        except HTTPException as error: source_problems.append({'observation_id':row.id,'reason':str(error.detail)})
    for entry in release.data['snapshot'].get('materials', [])[:100]:
        try:
            material = scoped(db, SetupMaterial, entry['id'], p)
            from .setup_understanding import frozen_material
            from .instance_compiler import verify_material_snapshots
            verify_material_snapshots(db,p,[entry]); frozen = frozen_material(db,p,material)
            options.append({'kind':'document','material_id':material.id,'title':material.name,
                'version':material.version,'evidence_id':material.evidence_id,'sha256':material.sha256,
                'blocks':[{'block_id':b['id'],'quote':b['text'],'location':b.get('location',{})} for b in frozen['blocks']]})
        except HTTPException as error: source_problems.append({'material_id':entry['id'],'reason':str(error.detail)})
    return {'instance_id':instance.id,'release_id':release.id,'package_id':package.id,
        'instance_version':instance.version,'command_limit':MAX_COMMANDS,'commands_remaining':max(0,MAX_COMMANDS-saved_command_count(instance)),'current':problem is None,'can_write':problem is None and p.role in {'admin','operator'},
        'unavailable_reason':problem,'relation_types':types,
        'objects':[{'id':e.id,'type':e.entity_type,'title':object_display(e,package)['label'],'version':e.version} for e in entities[:200]],
        'evidence_options':options,'source_problems':source_problems,
        'truncated':len(entities)>200 or len(rows)>100 or len(release.data['snapshot'].get('materials', []))>100,
        'evidence_policy':'原文和观测是待人工核实的事实依据；类型出处或对象名称不自动证明具体关系',
        'cardinality_policy':'确认保留相互矛盾的声明；冲突会阻断查询和最终验收'}


def list_records(db, p, instance_id, release_id, limit=30, cursor=None):
    instance, release, package, _ = context(db,p,instance_id,release_id)
    query = select(Relation).where(Relation.workspace_id==p.workspace_id, Relation.package_id==package.id)
    if cursor: query=query.where(Relation.id>cursor)
    rows=list(db.scalars(query.order_by(Relation.id).limit(limit+1)))
    return {'items':[record_view(db,p,instance,release,package,r) for r in rows[:limit]],
        'next_cursor':rows[limit-1].id if len(rows)>limit else None,'truncated':len(rows)>limit,'instance_version':instance.version}


def get_record(db,p,instance_id,release_id,relation_id):
    instance, release, package, _=context(db,p,instance_id,release_id)
    row=scoped_record(db,p,instance,release,relation_id)
    return record_view(db,p,instance,release,package,row)


def history(db,p,instance_id,release_id,relation_id):
    instance, release, _, _=context(db,p,instance_id,release_id)
    scoped_record(db,p,instance,release,relation_id)
    rows=db.scalars(select(RelationEvent).where(RelationEvent.workspace_id==p.workspace_id,
        RelationEvent.relation_id==relation_id).order_by(RelationEvent.created_at,RelationEvent.id))
    return {'items':[as_dict(row) for row in rows]}


def get_draft(db,p,instance_id,release_id):
    instance,release,_,problem=context(db,p,instance_id,release_id)
    drafts=instance.data.get('relation_drafts',{})
    if (drafts and 'relation_drafts_hash' not in instance.data) or ('relation_drafts_hash' in instance.data and digest(drafts)!=instance.data['relation_drafts_hash']):
        raise HTTPException(409,'已保存关系草稿摘要无法核对')
    value=drafts.get(release.id,{}).get(p.user_id)
    return {'instance_id':instance.id,'release_id':release.id,'instance_version':instance.version,
        'draft':copy.deepcopy(value['draft']) if value else None,
        'can_write':problem is None and p.role in {'admin','operator'},'unavailable_reason':problem}


def save_draft(db,p,instance_id,body):
    instance,release,_,_=context(db,p,instance_id,body.release_id,lock=True)
    require(p,'admin','operator')
    receipt=request_guard(db,p,instance,body.request_id,lock=True)
    prior=replay(instance,p,body,'draft',receipt=receipt)
    if prior:return prior
    relations.lock_current_actor(db,p)
    context(db,p,instance_id,body.release_id,write=True)
    check_version(instance,body.expected_version)
    if len(json.dumps(body.draft,ensure_ascii=False).encode())>100000:raise HTTPException(422,'关系草稿超过100KB，请保留原文并缩小本次说明')
    get_draft(db,p,instance_id,body.release_id)
    drafts=copy.deepcopy(instance.data.get('relation_drafts',{}))
    drafts.setdefault(release.id,{})[p.user_id]={'draft':copy.deepcopy(body.draft),'actor_id':p.user_id,'at':now().isoformat()}
    instance.data={**instance.data,'relation_drafts':drafts,'relation_drafts_hash':digest(drafts)}
    instance.version+=1
    response={'saved':True,'request_id':str(body.request_id),'operation':'draft','instance_version':instance.version,'draft':copy.deepcopy(body.draft)}
    append_command(db,p,instance,body,'draft',response)
    return response


def mutate(db,p,instance_id,body,operation,relation_id=None):
    instance,release,package,_=context(db,p,instance_id,body.release_id,lock=True)
    require(p,'admin','operator')
    receipt=request_guard(db,p,instance,body.request_id,lock=True)
    prior=replay(instance,p,body,operation,relation_id,receipt)
    if prior:return prior
    relations.lock_current_actor(db,p)
    context(db,p,instance_id,body.release_id,write=True)
    if saved_command_count(instance)>=MAX_COMMANDS:raise HTTPException(409,'关系操作已达上限，原记录保留')
    if operation=='create':check_version(instance,body.expected_version)
    row=scoped_record(db,p,instance,release,relation_id) if relation_id else None
    if operation=='review':
        row=relations.review(db,p,row.id,body)
    else:
        if not body.statement.strip():raise HTTPException(422,'请填写有原文支持的关系声明')
        refs=[x.model_dump() for x in body.document_refs]
        documents=document_snapshot(db,p,release,refs,lock=True)
        forwarded=SimpleNamespace(**body.model_dump(exclude={'document_refs','evidence_refs'}),
            package_id=package.id,document_refs=refs,evidence_refs=body.evidence_refs)
        if operation=='create':row=relations.create(db,p,forwarded)
        else:row=relations.edit(db,p,row.id,forwarded)
        row.data={**row.data,'instance_binding':{'instance_id':instance.id,'release_id':release.id,'configuration_hash':release.configuration_hash},
            'document_refs':refs,'document_snapshot':documents}
        row.data={**row.data,'claim_hash':relations.claim_hash(row)}
        # Replace the event's after snapshot with the full document-backed claim.
        db.flush()
        event=db.scalar(select(RelationEvent).where(RelationEvent.workspace_id==p.workspace_id,
            RelationEvent.relation_id==row.id).order_by(RelationEvent.created_at.desc(),RelationEvent.id.desc()).limit(1))
        event.data={**event.data,'after':copy.deepcopy(as_dict(row))}
        validate_real_sources(db,p,row,instance,release,require_fact_mentions=False)
    instance.version+=1
    db.flush()
    response={'saved':True,'request_id':str(body.request_id),'operation':operation,'instance_version':instance.version,
        'record':record_view(db,p,instance,release,package,row)}
    append_command(db,p,instance,body,operation,response,row.id if operation!='create' else None)
    return response


def recover(db,p,instance_id,request_id,release_id,body=None,*,abandon=False):
    instance,release,_,_=context(db,p,instance_id,release_id,lock=abandon)
    key=str(UUID(str(request_id)))
    receipt=request_guard(db,p,instance,key,lock=abandon)
    entries=ledger(instance)
    old=receipt or next((x for x in entries if x['request_id']==key),None)
    original=None
    if body:
        model={'draft':Draft,'create':Claim,'correct':Claim,'review':Review}[body.operation]
        try: original=model.model_validate(body.original_request)
        except ValidationError: raise HTTPException(422,'原关系请求内容不符合固定操作契约，请保留原内容核对') from None
        if str(original.request_id)!=key or original.release_id!=release_id or body.release_id!=release_id:
            raise HTTPException(409,'恢复请求与原编号或固定版本不一致')
        old=old_command(instance,p,original,body.operation,body.relation_id,receipt)
    if old:
        if old['actor_id']!=p.user_id or old.get('release_id',old.get('request',{}).get('release_id'))!=release_id:
            raise HTTPException(409,'此关系请求属于其他提交者或版本')
        response={'saved':old['status']=='saved','abandoned':old['status']=='abandoned',
            'request_id':key,'instance_version':instance.version,'operation':old['operation']}
        if old['status']=='saved':
            if digest(old.get('response'))!=old.get('response_hash'):raise HTTPException(409,'原请求保存结果摘要无法核对')
            response['response']=copy.deepcopy(old['response'])
        return response
    if abandon:
        require(p,'admin','operator');relations.lock_current_actor(db,p);check_version(instance,body.expected_version)
        instance.version+=1
        abandon_request(db,p,instance,original,body.operation,body.relation_id,body.reason)
    return {'saved':False,'abandoned':abandon,'request_id':key,'instance_version':instance.version}
