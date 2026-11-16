"""Original shadow grants and inventories, separate from current eligibility."""
import copy
import hashlib
import math
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select

from .models import Audit, Capability, DomainInstance, Entity, InstanceRelease, Membership, Observation, User
from .services import audit, published, scoped
from .threshold_evolution_protocol_v147 import command_fingerprint, wire_sha256
from .threshold_shadow_models_v151 import ThresholdShadowCommand as Command, ThresholdShadowGrant as Grant
from .threshold_shadow_protocol_v151 import ROOT, canonical_uuid, identify, parse_command, read_command
from .typed_payload_codec_v131 import canonical_json

ERROR = '原影子授权、请求或状态库存无法完整核对；原人稿和记录保留'
STATE_ACTION = 'threshold_shadow.state_v151'
COMMAND_ACTION = 'threshold_shadow.command_v151'
DATA_KEYS = {'schema','scope','evaluation','frontier','epoch','max_inputs','expires_at','title','reason'}
SCOPE_KEYS = {'workspace_id','instance_id','release_id','entity_id','source_id','baseline_id',
    'baseline_hash','candidate_capability_id','candidate_hash','source_hash','rule_id','field',
    'field_label','unit','operator','baseline_threshold','candidate_threshold','instance_name',
    'release_name','entity_name','source_name','baseline_name','candidate_name'}
STATE_KEYS = {'schema','grant_id','version','status','record_hash','previous_hash','operation',
              'actor_id','workspace_id','command_id'}
SNAPSHOT_KEYS = {'id','workspace_id','actor_id','evaluation_id','scope_key','status','version','data'}


class ShadowInventoryError(HTTPException):
    def __init__(self, detail=ERROR): super().__init__(409, detail)


def need(condition, message=ERROR, status=409):
    if not condition: raise HTTPException(status, message)


def hash_value(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _same(left, right): return canonical_json(left) == canonical_json(right)


def _sha(value):
    need(type(value) is str and len(value)==64 and not set(value)-set('0123456789abcdef'))


def _text(value, limit=300):
    need(type(value) is str and bool(value.strip()) and len(value)<=limit)


def _instant(value):
    need(type(value) is str)
    parsed=datetime.fromisoformat(value)
    need(parsed.tzinfo is not None and parsed.utcoffset() is not None)
    return parsed


def scope_key(scope):
    return hash_value({'schema':'threshold-shadow-scope-v151',**{key:scope[key] for key in (
        'workspace_id','instance_id','release_id','entity_id','source_id','baseline_id')}})


def lock_scope(db, scope):
    db.execute(select(func.pg_advisory_xact_lock(int(scope_key(scope)[:15],16))))


def lock_nonce(db,p,nonce):
    key=hash_value({'schema':'threshold-shadow-nonce-v151','workspace_id':p.workspace_id,
                    'actor_id':p.user_id,'request_id':nonce})
    db.execute(select(func.pg_advisory_xact_lock(int(key[:15],16))))


def snapshot(row):
    return {key:copy.deepcopy(getattr(row,key)) for key in SNAPSHOT_KEYS}


def _shape(value):
    need(type(value) is dict and set(value)==SNAPSHOT_KEYS)
    for key in ('id','workspace_id','actor_id','evaluation_id'): canonical_uuid(value[key])
    need(type(value['version']) is int and value['version']>=1 and value['status'] in {'active','paused','ended'})
    data=value['data'];need(type(data) is dict and set(data)==DATA_KEYS
        and data['schema']=='threshold-shadow-grant-record-v151')
    scope=data['scope'];need(type(scope) is dict and set(scope)==SCOPE_KEYS)
    for key in ('workspace_id','instance_id','release_id','entity_id','source_id','baseline_id','candidate_capability_id'):
        canonical_uuid(scope[key])
    need(scope['workspace_id']==value['workspace_id'] and scope['baseline_id']!=scope['candidate_capability_id'])
    for key in ('baseline_hash','candidate_hash','source_hash'): _sha(scope[key])
    for key in ('rule_id','field','field_label','instance_name','release_name','entity_name','source_name','baseline_name','candidate_name'):
        _text(scope[key])
    need(scope['unit'] is None or type(scope['unit']) is str)
    need(scope['operator'] in {'gt','lt'})
    for key in ('baseline_threshold','candidate_threshold'):
        need(type(scope[key]) in (int,float) and math.isfinite(scope[key]) and abs(scope[key])<=1e15)
    need(type(data['epoch']) is int and data['epoch']>=1
        and type(data['max_inputs']) is int and 1<=data['max_inputs']<=30)
    _text(data['title'],200);_text(data['reason'],2000);_instant(data['expires_at'])
    cut=data['frontier'];need(type(cut) is dict and set(cut)=={'schema','observation_ids','source_record_ids'}
        and cut['schema']=='threshold-shadow-frontier-v151')
    for key in ('observation_ids','source_record_ids'):
        need(type(cut[key]) is list and len(cut[key])<=4096 and all(type(x) is str for x in cut[key]))
        need(cut[key]==sorted(set(cut[key])))
    for identity in cut['observation_ids']:canonical_uuid(identity)
    for identity in cut['source_record_ids']:
        need(1<=len(identity)<=200);canonical_json(identity)
    pin=data['evaluation'];need(type(pin) is dict and set(pin)=={
        'schema','id','version','record_hash','freeze_hash','result_hash','candidate_id','freeze','result'}
        and pin['schema']=='threshold-shadow-evaluation-pin-v151' and pin['id']==value['evaluation_id'])
    canonical_uuid(pin['id']);canonical_uuid(pin['candidate_id'])
    need(type(pin['version']) is int and pin['version']>=1)
    for key in ('record_hash','freeze_hash','result_hash'):_sha(pin[key])
    need(type(pin['freeze']) is dict and type(pin['result']) is dict
        and hash_value(pin['freeze'])==pin['freeze_hash'] and hash_value(pin['result'])==pin['result_hash']
        and pin['result'].get('mechanism_gate_passed') is True)
    frozen=pin['freeze']['candidate']
    need(frozen['candidate_id']==pin['candidate_id'] and frozen['baseline_id']==scope['baseline_id']
        and frozen['candidate_capability_id']==scope['candidate_capability_id']
        and hash_value(frozen['baseline_manifest'])==scope['baseline_hash']
        and hash_value(frozen['candidate_manifest'])==scope['candidate_hash']
        and value['scope_key']==scope_key(scope))


def _transition(previous, saved, operation, body):
    _shape(saved)
    if operation=='create':
        need(previous is None and saved['version']==1 and saved['status']=='active'
            and saved['evaluation_id']==body.evaluation_id and saved['data']['epoch']==1
            and saved['data']['evaluation']['version']==body.expected_evaluation_version)
        for key in ('title','reason','expires_at','max_inputs'):
            need(_same(saved['data'][key],getattr(body,key)))
        return
    need(previous is not None and previous['status']!='ended'
        and body.expected_version==previous['version'] and saved['version']==previous['version']+1)
    expected=copy.deepcopy(previous);expected['version']+=1
    expected['status']={'pause':'paused','resume':'active','end':'ended'}[operation]
    expected['data']['reason']=body.reason
    if operation=='pause':need(previous['status']=='active')
    if operation=='resume':
        need(previous['status']=='paused')
        expected['data']['epoch']+=1;expected['data']['frontier']=copy.deepcopy(saved['data']['frontier'])
        for key in ('observation_ids','source_record_ids'):
            need(set(previous['data']['frontier'][key])<=set(saved['data']['frontier'][key]))
    need(_same(expected,saved))


def seal_grant(db,p,grant,operation,command_id=None):
    canonical_uuid(command_id)
    previous=None if operation=='create' else grant.record_hash
    if operation=='create':need(grant.version==1)
    else:grant.version+=1
    grant.record_hash=hash_value(snapshot(grant));db.add(grant)
    audit(db,p,STATE_ACTION,grant.id,{'schema':'threshold-shadow-state-v151','grant_id':grant.id,
        'version':grant.version,'status':grant.status,'record_hash':grant.record_hash,
        'previous_hash':previous,'operation':operation,'actor_id':p.user_id,
        'workspace_id':p.workspace_id,'command_id':command_id})
    db.flush()


def command_payload(row):
    return {'schema':'threshold-shadow-command-pin-v151',**{key:getattr(row,key) for key in (
        'id','workspace_id','actor_id','request_id','method','path','wire_sha256','fingerprint',
        'operation','target_id','outcome')},'data':row.data}


def command_pin(row):
    return {'schema':'threshold-shadow-command-audit-v151',**{key:getattr(row,key) for key in (
        'id','workspace_id','actor_id','request_id','method','path','wire_sha256','fingerprint',
        'operation','target_id','outcome','command_hash')},'result_hash':hash_value(row.data['result_snapshot'])}


def inventory(db,p):
    """Actual complete SQL/Audit inventories in this call, never a cache."""
    try:
        grants=list(db.scalars(select(Grant).where(Grant.workspace_id==p.workspace_id)
            .execution_options(populate_existing=True)))
        rows={row.id:row for row in grants};need(len(rows)==len(grants))
        states=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==STATE_ACTION)
            .execution_options(populate_existing=True)))
        by_grant={};by_version={}
        for issued in states:
            pin=issued.data;need(type(pin) is dict and set(pin)==STATE_KEYS
                and pin['schema']=='threshold-shadow-state-v151' and issued.object_id in rows
                and pin['grant_id']==issued.object_id and pin['workspace_id']==p.workspace_id
                and pin['actor_id']==issued.actor_id and type(pin['version']) is int and pin['version']>=1)
            canonical_uuid(pin['command_id'])
            by_grant.setdefault(issued.object_id,[]).append(issued)
            by_version.setdefault((issued.object_id,pin['version']),[]).append(issued)
        pairs=list(db.execute(select(Command,Audit).join(Audit,and_(Command.workspace_id==Audit.workspace_id,
            Audit.action==COMMAND_ACTION,Audit.object_id==Command.id),full=True).where(or_(
                Command.workspace_id==p.workspace_id,and_(Audit.workspace_id==p.workspace_id,Audit.action==COMMAND_ACTION)))
            .execution_options(populate_existing=True)))
        commands=[];seen=set();nonces=set();saved={}
        for command,issued in pairs:
            need(command is not None and issued is not None and command.id not in seen
                and command.workspace_id==p.workspace_id and command.version==1)
            for key in ('id','workspace_id','actor_id','request_id'):canonical_uuid(getattr(command,key))
            value=command.data;need(type(value) is dict and set(value)=={'schema','wire_text','result_snapshot'}
                and value['schema']=='threshold-shadow-command-record-v151' and type(value['wire_text']) is str)
            raw=value['wire_text'].encode('utf-8');original=read_command(raw)
            target,operation,model=identify(command.method,command.path)
            need(original['request_id']==command.request_id and operation==command.operation
                and (target is None or target==command.target_id) and wire_sha256(raw)==command.wire_sha256
                and command_fingerprint(command.method,command.path,p.workspace_id,command.actor_id,command.wire_sha256)==command.fingerprint
                and command.command_hash==hash_value(command_payload(command)))
            need(issued.workspace_id==p.workspace_id and issued.actor_id==command.actor_id
                and issued.object_id==command.id and issued.action==COMMAND_ACTION
                and _same(issued.data,command_pin(command)))
            result=value['result_snapshot']
            if command.outcome=='saved':
                body=parse_command(model,original);_shape(result)
                need(command.target_id in rows and result['id']==command.target_id
                    and result['workspace_id']==p.workspace_id)
                matched=by_version.get((command.target_id,result['version']),[])
                need(len(matched)==1 and matched[0].data['command_id']==command.id
                    and matched[0].data['record_hash']==hash_value(result))
                saved[command.id]=(command,body)
            else:
                need(command.outcome=='rejected' and type(result) is dict and set(result)=={'status','detail'}
                    and type(result['status']) is int and result['status'] in {409,422})
                _text(result['detail'],2000)
            nonce=(command.actor_id,command.request_id);need(nonce not in nonces)
            nonces.add(nonce);seen.add(command.id);commands.append(command)
        used=set()
        for identity,row in rows.items():
            _shape(snapshot(row));need(row.record_hash==hash_value(snapshot(row)))
            ordered=sorted(by_grant.get(identity,[]),key=lambda a:a.data['version'])
            need(len(ordered)==row.version);previous=None;previous_hash=None
            for index,issued in enumerate(ordered,1):
                pin=issued.data;need(pin['version']==index and pin['previous_hash']==previous_hash)
                pair=saved.get(pin['command_id']);need(pair is not None and pair[0].id not in used)
                command,body=pair;result=command.data['result_snapshot']
                need(command.target_id==identity and command.actor_id==issued.actor_id
                    and command.operation==pin['operation'] and result['version']==index
                    and result['status']==pin['status'] and hash_value(result)==pin['record_hash'])
                _transition(previous,result,command.operation,body)
                if previous is None:need(result['actor_id']==command.actor_id)
                used.add(command.id);previous=result;previous_hash=pin['record_hash']
            need(previous is not None and previous_hash==row.record_hash and _same(previous,snapshot(row)))
        need(used==set(saved))
        return grants,commands
    except ShadowInventoryError: raise
    except HTTPException as exc: raise ShadowInventoryError() from exc
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError,AttributeError,OverflowError):
        raise ShadowInventoryError() from None


def _grant_record(db,p,identity,lock=False):
    canonical_uuid(identity)
    need(type(lock) is bool)
    query=select(Grant).where(Grant.workspace_id==p.workspace_id,Grant.id==identity)
    if lock:query=query.with_for_update()
    row=db.scalar(query.execution_options(populate_existing=True))
    grants,commands=inventory(db,p)
    if row is None:raise HTTPException(404,'影子观察不存在，或不在当前工作区')
    need(any(value.id==row.id for value in grants))
    return row,commands


def get_grant(db,p,identity,lock=False,check_consumptions=True):
    need(type(check_consumptions) is bool)
    row,_=_grant_record(db,p,identity,lock)
    if check_consumptions:
        from .threshold_shadow_execution_v151 import consumption_inventory
        consumption_inventory(db,p,row)
    return row


def _frontier_original(row):
    """Check intake identity at any processing state, without reprocessing it."""
    from .contracts import ObservationCreate
    from .observation_time_anchor_v135 import same_observation_instant
    columns={'source_id','source_record_id','processing_version','entity_id','business_day','observed_at'}
    try:
        canonical_uuid(row.id)
        data=row.data;need(type(data) is dict)
        original=data.get('original_request');need(type(original) is dict)
        body=ObservationCreate.model_validate(original)
        expected=body.model_dump(exclude={'evidence_ids'} if not body.evidence_ids else set())
        need(_same(original,expected) and data.get('input_hash')==hash_value(original))
        for key in columns-{'observed_at'}:
            need(key in original and _same(original[key],getattr(row,key)))
        same_observation_instant(original['observed_at'],row.observed_at)
        for key,value in body.model_dump(exclude=columns).items():
            need(key in data and _same(data[key],value))
    except HTTPException as exc:raise ShadowInventoryError() from exc
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError,AttributeError,OverflowError):
        raise ShadowInventoryError() from None


def frontier(db,p,scope):
    """All existing identities of the source, including other processing versions."""
    need(scope['workspace_id']==p.workspace_id)
    rows=list(db.scalars(select(Observation).where(Observation.workspace_id==p.workspace_id,
        or_(Observation.source_id==scope['source_id'],
            Observation.data['original_request']['source_id'].as_string()==scope['source_id']))
        .order_by(Observation.id).limit(4097)
        .execution_options(populate_existing=True)))
    need(len(rows)<=4096,'该来源已有超过4096条登记，不能截断未来身份边界',422)
    ids={row.id for row in rows}
    audits=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action=='observation.ingest',Audit.object_id.in_(ids)).execution_options(populate_existing=True))) if ids else []
    by_id={}
    for issued in audits:by_id.setdefault(issued.object_id,[]).append(issued)
    # An independently retained intake Audit may not become "no old input".
    orphan=db.scalar(select(Audit.id).outerjoin(Observation,and_(Observation.workspace_id==Audit.workspace_id,
        Observation.id==Audit.object_id)).where(Audit.workspace_id==p.workspace_id,
        Audit.action=='observation.ingest',Observation.id.is_(None)).limit(1))
    if orphan is not None:raise ShadowInventoryError()
    for row in rows:
        _frontier_original(row)
        issued=by_id.get(row.id,[])
        if len(issued)!=1 or issued[0].actor_id!=row.data.get('submitted_by'):
            raise ShadowInventoryError()
        canonical_uuid(row.id)
        need(type(row.source_record_id) is str and 1<=len(row.source_record_id)<=200)
        canonical_json(row.source_record_id)
    return {'schema':'threshold-shadow-frontier-v151','observation_ids':sorted(ids),
            'source_record_ids':sorted({row.source_record_id for row in rows})}


def _approver(db,p,identity,lock=True):
    query=select(User,Membership).join(Membership,Membership.user_id==User.id).where(
        User.id==identity,User.active.is_(True),Membership.workspace_id==p.workspace_id,
        Membership.role=='admin')
    if lock:query=query.with_for_update(read=True,of=[User,Membership])
    row=db.execute(query.execution_options(populate_existing=True)).first()
    need(row is not None,'原批准管理员已停用或失去管理员资格；不能开始新的影子计算',403)


def _pin(evaluation):
    return {'schema':'threshold-shadow-evaluation-pin-v151','id':evaluation.id,'version':evaluation.version,
        'record_hash':evaluation.record_hash,'freeze_hash':evaluation.data['freeze_hash'],
        'result_hash':hash_value(evaluation.data['result']),'candidate_id':evaluation.candidate_id,
        'freeze':copy.deepcopy(evaluation.data['freeze']),'result':copy.deepcopy(evaluation.data['result'])}


def lock_resources(db,p,scope):
    """Stable live scope through the next commit, before Gateway's cap lock.

    The candidate starts exclusive: sharing it and later upgrading in Gateway
    would deadlock two different grants that use the same candidate.
    """
    need(scope['workspace_id']==p.workspace_id)
    locked={}
    for model,key in ((DomainInstance,'instance_id'),(InstanceRelease,'release_id')):
        row=db.scalar(select(model).where(model.workspace_id==p.workspace_id,model.id==scope[key])
            .with_for_update(read=True).execution_options(populate_existing=True))
        need(row is not None,'原实例、发布或对象已不可用，不能开始新的影子计算')
        locked[key]=row
    entity=db.scalar(select(Entity).where(Entity.workspace_id==p.workspace_id,Entity.id==scope['entity_id'])
        .execution_options(populate_existing=True))
    need(entity is not None,'原对象已不可用，不能开始新的影子计算')
    package_id=entity.schema_capability_id
    ids={scope['source_id'],scope['baseline_id'],scope['candidate_capability_id'],
         package_id,locked['release_id'].package_id}
    for identity in sorted(value for value in ids if value is not None):
        row=db.scalar(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.id==identity)
            .with_for_update(read=identity!=scope['candidate_capability_id'])
            .execution_options(populate_existing=True))
        need(row is not None,'原来源、规则或业务包已不可用，不能开始新的影子计算')
    # Managed-rule edits already lock Capability before Entity. Do not invert
    # that order when a formerly published rule has been withdrawn for editing.
    entity=db.scalar(select(Entity).where(Entity.workspace_id==p.workspace_id,Entity.id==scope['entity_id'])
        .with_for_update(read=True).execution_options(populate_existing=True))
    need(entity is not None and entity.schema_capability_id==package_id,
         '原对象所属业务包已变化，请重新核对影子范围')


def evaluation_context(db,p,identity):
    from . import threshold_study_store_v149 as study
    from .models import Job
    from .instance_api import active
    from .instance_domain import object_display
    from .entity_release_compat import require_entity
    evaluation=study.get_record(db,p,'evaluation',identity)
    need(evaluation.status=='completed' and evaluation.data['result'] is not None
        and evaluation.data['result'].get('mechanism_gate_passed') is True,
        '请先完成真实比较并通过机制门；这仍不代表正式试用许可')
    job=scoped(db,Job,evaluation.data['job_id'],p)
    need(job.status=='succeeded','原比较后台尚未完成，请先读取原真实回执')
    study.fresh_evaluation(db,p,evaluation)
    frozen=evaluation.data['freeze'];candidate=frozen['candidate'];training=candidate['training']
    need(type(training) is list and bool(training))
    first=training[0]['basis'];rule=candidate['baseline_manifest']['specification']['rules'][0]
    source_pin=first['proof']['source']
    for sample in training:
        basis=sample['basis']
        need(all(_same(basis[key],first[key]) for key in ('instance_id','release_id','entity_id','source_id',
            'baseline_capability_id','baseline_capability_hash','rule_id','field','unit','operator')),
            '训练依据必须属于同一实例、发布、对象、来源和原规则')
        need(basis['baseline_capability_id']==candidate['baseline_id']
            and basis['baseline_capability_hash']==hash_value(candidate['baseline_manifest'])
            and _same(basis['proof']['source'],source_pin),
            '原训练来源或实际基线与冻结方案不一致')
    instance,release=active(db,p,first['instance_id'])
    need(release.id==first['release_id'],'实例发布已变化，原影子范围不能迁移')
    entity=scoped(db,Entity,first['entity_id'],p);require_entity(db,p,instance,release,entity)
    source=published(db,p,first['source_id'],'source')
    need(source_pin['id']==source.id and source_pin['hash']==source.content_hash
        and _same(source_pin['manifest'],source.data),'原来源已变化，不能继续新的影子观察')
    for manifest in (candidate['baseline_manifest'],candidate['candidate_manifest']):
        metadata=manifest['specification']['managed_numeric_rule']
        need(metadata['entity_id']==entity.id and metadata['package_id']==entity.schema_capability_id
            and metadata['entity_type']==entity.entity_type,'规则不属于此原对象，不能借用另一对象的处理权限')
    changed=candidate['candidate_manifest']['specification']['rules'][0]
    label=object_display(entity,scoped(db,Capability,entity.schema_capability_id,p))['label']
    def name(value,fallback):return value.strip()[:300] if type(value) is str and value.strip() else fallback
    scope={'workspace_id':p.workspace_id,**{key:first[key] for key in ('instance_id','release_id','entity_id','source_id')},
        'baseline_id':candidate['baseline_id'],'baseline_hash':hash_value(candidate['baseline_manifest']),
        'candidate_capability_id':candidate['candidate_capability_id'],'candidate_hash':hash_value(candidate['candidate_manifest']),
        'source_hash':source.content_hash,'rule_id':rule['id'],'field':rule['field'],
        'field_label':first['field_label'],'unit':rule['unit'],'operator':rule['operator'],
        'baseline_threshold':copy.deepcopy(rule['threshold']),'candidate_threshold':copy.deepcopy(changed['threshold']),
        'instance_name':name(instance.data.get('name'),'原实例'),'release_name':'第'+str(release.release_number)+'版',
        'entity_name':name(label,'原对象'),'source_name':name(source.data.get('description'),'人工登记来源'),
        'baseline_name':candidate['baseline_manifest']['specification']['managed_numeric_rule']['title'],
        'candidate_name':candidate['candidate_manifest']['specification']['managed_numeric_rule']['title']}
    return evaluation,scope,_pin(evaluation)


def _current_grant(db,p,grant,lock,readonly_context=None,*,_return_evaluation=False):
    # Return only this call's actual cold evaluation, never a caller/cache row.
    if type(_return_evaluation) is not bool or (_return_evaluation and
            (lock is not False or readonly_context is not None)):
        raise ShadowInventoryError()
    need(grant.workspace_id==p.workspace_id)
    # Only one read-only list projection may share current-source work. No
    # context survives a request or enters a locked authorization path.
    if readonly_context is not None and (lock is not False or type(readonly_context) is not dict):
        raise ShadowInventoryError()
    _approver(db,p,grant.actor_id,lock)
    if lock:lock_resources(db,p,grant.data['scope'])
    key=('evaluation',p.workspace_id,p.user_id,p.role,grant.evaluation_id)
    if readonly_context is None:evaluation,scope,pin=evaluation_context(db,p,grant.evaluation_id)
    else:
        if key not in readonly_context:readonly_context[key]=evaluation_context(db,p,grant.evaluation_id)
        evaluation,scope,pin=readonly_context[key]
    need(_same(pin,grant.data['evaluation']),'原比较版本或原真实结果已变化，不能继续影子计算')
    # Human display names are saved at approval; a rename is not new scope.
    names={'instance_name','release_name','entity_name','source_name','baseline_name','candidate_name'}
    need(_same({k:v for k,v in scope.items() if k not in names},
               {k:v for k,v in grant.data['scope'].items() if k not in names}),
         '原实例、对象、来源或固定规则范围已变化，不能继续影子计算')
    key=('frontier',p.workspace_id,p.user_id,p.role,scope['source_id'])
    if readonly_context is None:frontier(db,p,scope)
    elif key not in readonly_context:readonly_context[key]=frontier(db,p,scope)
    return (grant,evaluation) if _return_evaluation else grant


def fresh_grant(db,p,grant):
    """Locked live authority only; status, expiry and quota are caller gates."""
    return _current_grant(db,p,grant,True)


def options(db,p,evaluation_id):
    result={'schema':'threshold-shadow-options-v151','workspace_id':p.workspace_id,
        'evaluation_id':evaluation_id,'evaluation_version':None,'scope':None,'baseline':None,
        'candidate':None,'eligible':False,'issue':None}
    try:
        evaluation,scope,pin=evaluation_context(db,p,evaluation_id)
        frontier(db,p,scope)
        from .threshold_study_views_v149 import rule_summary
        result.update(evaluation_version=evaluation.version,scope=scope,
            baseline=rule_summary(scope['baseline_id'],pin['freeze']['candidate']['baseline_manifest'],scope['field_label']),
            candidate=rule_summary(scope['candidate_capability_id'],pin['freeze']['candidate']['candidate_manifest'],scope['field_label']),
            eligible=p.role=='admin',issue=None if p.role=='admin' else '仅管理员可以明确批准影子观察')
    except HTTPException as exc:
        if exc.status_code not in {403,404,409,422}:raise
        result['issue']='原比较或当前来源资格暂不可用；原人工作业和历史仍保留'
    return result


def projection_pin(grant):
    return {'version':grant.version,'record_hash':grant.record_hash,
        'snapshot_hash':hash_value(snapshot(grant))}


def view(db,p,grant,*,_verified_inventory=None,_readonly_context=None):
    if _verified_inventory is None:
        need(_readonly_context is None)
        grant,commands=_grant_record(db,p,grant.id)
    else:
        grants,commands,pins=_verified_inventory
        need(grant.workspace_id==p.workspace_id and any(row is grant for row in grants)
            and _same(pins.get(grant.id),projection_pin(grant)))
    from .threshold_shadow_execution_v151 import consumption_inventory
    from .threshold_study_views_v149 import rule_summary
    consumptions=copy.deepcopy(consumption_inventory(db,p,grant));issue=None;source_ok=True
    # Consumption cold reads populate ORM rows again. A concurrent control
    # must not pair its new Grant with the earlier checked command history.
    if _verified_inventory is not None:
        need(_same(pins.get(grant.id),projection_pin(grant)))
    try:_current_grant(db,p,grant,False,_readonly_context)
    except HTTPException as exc:
        if exc.status_code not in {403,404,409,422}:raise
        source_ok=False
        issue='当前依据或原批准管理员资格不可用于新的影子计算；原人工作业和停止入口保留'
    now=db.scalar(select(func.clock_timestamp()))
    expired=_instant(grant.data['expires_at'])<=now
    if issue is None and expired:issue='影子观察已到期；原结果保留，不再接纳新的输入'
    if issue is None and len(consumptions)>=grant.data['max_inputs']:
        issue='影子观察处理次数已用完；已占用次数不退款，原记录保留'
    scope=copy.deepcopy(grant.data['scope']);frozen=grant.data['evaluation']['freeze']['candidate']
    baseline=rule_summary(scope['baseline_id'],frozen['baseline_manifest'],scope['field_label'])
    candidate=rule_summary(scope['candidate_capability_id'],frozen['candidate_manifest'],scope['field_label'])
    if not source_ok:
        baseline['threshold']=None;candidate['threshold']=None
        scope['baseline_threshold']=None;scope['candidate_threshold']=None
        for item in consumptions:
            for key in ('values','baseline_matched','candidate_matched'):item[key]=None
    if issue is None and grant.status=='paused':issue='影子观察已暂停，不接纳新的输入；恢复不会补追暂停期间的登记'
    if issue is None and grant.status=='ended':issue='影子观察已永久结束；原回执与人工作业保留'
    manage=p.role=='admin'
    history=[]
    for command in sorted((row for row in commands if row.outcome=='saved' and row.target_id==grant.id),
                          key=lambda row:row.data['result_snapshot']['version']):
        original=read_command(command.data['wire_text'].encode('utf-8'))
        history.append({'version':command.data['result_snapshot']['version'],
            'operation':command.operation,'reason':original['reason'],'actor_id':command.actor_id})
    return {'schema':'threshold-shadow-view-v151','workspace_id':p.workspace_id,'id':grant.id,
        'version':grant.version,'actor_id':grant.actor_id,'status':grant.status,
        'title':grant.data['title'],'reason':grant.data['reason'],'evaluation_id':grant.evaluation_id,
        'scope':scope,'baseline':baseline,'candidate':candidate,'max_inputs':grant.data['max_inputs'],
        'used_inputs':len(consumptions),'expires_at':grant.data['expires_at'],'epoch':grant.data['epoch'],
        'current_issue':issue,'can_pause':manage and grant.status=='active',
        'can_resume':manage and grant.status=='paused' and source_ok and not expired
            and len(consumptions)<grant.data['max_inputs'],
        'can_end':manage and grant.status!='ended','eligible_for_trial':False,
        'consumptions':consumptions,'human_history':history}
