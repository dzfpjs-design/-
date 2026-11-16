"""Finite numeric adoption inventory, separate from live execution authority."""
from copy import deepcopy
from datetime import datetime
import hashlib

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select

from .config import settings
from .models import Audit, Capability, DomainInstance, Entity, InstanceRelease
from .services import audit, published, scoped
from .threshold_policy_models_v152 import ThresholdPolicyGrant as Grant, ThresholdPolicyCommand as Command, ThresholdPolicySelection as Selection
from .threshold_policy_protocol_v152 import canonical_uuid, identify, parse_command, read_command, aware_text
from .threshold_evolution_protocol_v147 import command_fingerprint, wire_sha256
from .typed_payload_codec_v131 import canonical_json

ERROR='有限策略、原请求或状态库存无法完整核对；原记录保留'
UNAVAILABLE='当前审核或固定范围不可用于新的候选输入；历史及暂停、回滚入口保留'
STATE_ACTION='threshold_policy.state_v153'
COMMAND_ACTION='threshold_policy.command_v153'
DATA_KEYS={'schema','scope','review','baseline_manifest','candidate_manifest','purpose','criteria','frontier','epoch',
    'max_candidate_inputs','expires_at','title','reason','rollback','environment_at_approval'}
SNAPSHOT_KEYS={'id','workspace_id','actor_id','review_id','scope_key','status','version','data'}
SELECTION_KEYS={'id','workspace_id','policy_id','actor_id','source_id','source_record_id','observation_id','job_id','choice','status','version','data'}
STATE_KEYS={'schema','policy_id','workspace_id','actor_id','version','status','record_hash','previous_hash',
    'operation','command_id','monitor_basis','snapshot'}


class PolicyInventoryError(HTTPException):
    def __init__(self,detail=ERROR):super().__init__(409,detail)


class PolicySourceError(HTTPException):
    def __init__(self,detail=UNAVAILABLE):super().__init__(409,detail)


def need(value,message=ERROR,status=409):
    if not value:raise HTTPException(status,message)


def hash_value(value):return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()
def same(left,right):return canonical_json(left)==canonical_json(right)
def snapshot(row):return {key:deepcopy(getattr(row,key)) for key in SNAPSHOT_KEYS}
def selection_snapshot(row):return {key:deepcopy(getattr(row,key)) for key in SELECTION_KEYS}


def _sha(value):need(type(value) is str and len(value)==64 and not set(value)-set('0123456789abcdef'))


def _text(value,limit=2000):
    need(type(value) is str and bool(value.strip()) and len(value)<=limit);canonical_json(value)


def scope_key(scope):
    return hash_value({'schema':'threshold-policy-scope-v153',**{key:scope[key] for key in (
        'workspace_id','instance_id','release_id','entity_id','source_id','baseline_id')}})


def lock_scope(db,scope):db.execute(select(func.pg_advisory_xact_lock(int(scope_key(scope)[:15],16))))


def lock_nonce(db,p,nonce):
    key=hash_value({'schema':'threshold-policy-nonce-v153','workspace_id':p.workspace_id,'actor_id':p.user_id,'request_id':nonce})
    db.execute(select(func.pg_advisory_xact_lock(int(key[:15],16))))


def lock_source_key(db,p,source_id,source_record_id):
    key=hash_value({'schema':'threshold-policy-input-key-v153','workspace_id':p.workspace_id,
        'source_id':source_id,'source_record_id':source_record_id})
    db.execute(select(func.pg_advisory_xact_lock(int(key[:15],16))))


def _shape(value):
    from . import quality_review_store_v152 as quality
    from .threshold_shadow_store_v151 import SCOPE_KEYS
    need(type(value) is dict and set(value)==SNAPSHOT_KEYS)
    for key in ('id','workspace_id','actor_id','review_id'):canonical_uuid(value[key])
    need(type(value['version']) is int and value['version']>=1 and value['status'] in {'active','paused','rolled_back'})
    data=value['data'];need(type(data) is dict and set(data)==DATA_KEYS and data['schema']=='threshold-policy-grant-record-v153')
    scope=data['scope'];need(type(scope) is dict and set(scope)==SCOPE_KEYS
        and scope['workspace_id']==value['workspace_id'] and value['scope_key']==scope_key(scope))
    for key in ('workspace_id','instance_id','release_id','entity_id','source_id','baseline_id','candidate_capability_id'):
        canonical_uuid(scope[key])
    for key in ('source_hash','baseline_hash','candidate_hash'):_sha(scope[key])
    for key in ('rule_id','field','field_label','instance_name','release_name','entity_name','source_name','baseline_name','candidate_name'):_text(scope[key],300)
    need(data['purpose'] in {'mechanism_check','business_assessment'} and data['environment_at_approval'] in {'production','verification'})
    need(data['purpose']!='mechanism_check' or data['environment_at_approval']=='verification')
    need(type(data['epoch']) is int and data['epoch']>=1 and type(data['max_candidate_inputs']) is int
        and 1<=data['max_candidate_inputs']<=30)
    aware_text(data['expires_at']);_text(data['title'],200);_text(data['reason'])
    original=data['review'];need(type(original) is dict and set(original)=={'pin','approved_snapshot'})
    approved=original['approved_snapshot'];quality._shape(approved)
    pin=original['pin'];need(type(pin) is dict and set(pin)=={'id','version','record_hash','approval_hash'}
        and approved['id']==value['review_id']==pin['id'] and approved['workspace_id']==value['workspace_id']
        and approved['status']=='approved' and type(pin['version']) is int and pin['version']==approved['version']
        and pin['record_hash']==quality.hash_value(approved) and pin['approval_hash']==approved['approval_hash'])
    frozen=approved['data']['frozen']
    need(same(frozen['scope'],scope) and frozen['purpose']==data['purpose'] and same(frozen['criteria'],data['criteria'])
        and same(frozen['baseline_manifest'],data['baseline_manifest'])
        and same(frozen['candidate_manifest'],data['candidate_manifest'])
        and hash_value(data['baseline_manifest'])==scope['baseline_hash']
        and hash_value(data['candidate_manifest'])==scope['candidate_hash'])
    cut=data['frontier'];need(type(cut) is dict and set(cut)=={'schema','observation_ids','source_record_ids'}
        and cut['schema']=='threshold-shadow-frontier-v151')
    for key in ('observation_ids','source_record_ids'):
        need(type(cut[key]) is list and len(cut[key])<=4096 and all(type(item) is str for item in cut[key])
            and cut[key]==sorted(set(cut[key])))
    for identity in cut['observation_ids']:canonical_uuid(identity)
    for identity in cut['source_record_ids']:_text(identity,200)
    rollback=data['rollback']
    if value['status']!='rolled_back':need(rollback is None)
    else:
        need(type(rollback) is dict and set(rollback)=={'reason','origin','selection_id','basis'}
            and rollback['reason']==data['reason'] and rollback['origin'] in {'human','monitor'})
        if rollback['origin']=='human':need(rollback['selection_id'] is None and rollback['basis'] is None)
        else:
            if rollback['selection_id'] is not None:canonical_uuid(rollback['selection_id'])
            need(type(rollback['basis']) is dict and rollback['selection_id']==rollback['basis'].get('selection_id'))
    canonical_json(value)


def _transition(previous,saved,operation,body,actor_id):
    _shape(saved)
    return _transition_checked(previous,saved,operation,body,actor_id)


def _transition_checked(previous,saved,operation,body,actor_id):
    # Inventory already checked this exact State snapshot. This helper only
    # avoids repeating its structure walk; every transition check remains.
    if operation=='create':
        need(previous is None and saved['version']==1 and saved['status']=='active' and saved['actor_id']==actor_id
            and saved['review_id']==body.review_id and saved['data']['review']['pin']['version']==body.expected_review_version
            and saved['data']['epoch']==1)
        for key in ('title','reason','max_candidate_inputs','expires_at'):need(same(saved['data'][key],getattr(body,key)))
        return
    need(previous is not None and previous['status']!='rolled_back' and saved['version']==previous['version']+1)
    # Only these two mapping levels are edited; nested evidence is read-only
    # and the temporary comparison object is never returned or stored.
    expected={**previous,'version':previous['version']+1,'data':{**previous['data']}}
    if operation=='auto_rollback':
        expected['status']='rolled_back';expected['data']['reason']=saved['data']['reason']
        expected['data']['rollback']=deepcopy(saved['data']['rollback'])
        need(saved['data']['rollback']['origin']=='monitor')
    else:
        need(body.expected_version==previous['version'])
        expected['data']['reason']=body.reason
        if operation=='pause':need(previous['status']=='active');expected['status']='paused'
        elif operation=='resume':
            need(previous['status']=='paused');expected['status']='active';expected['data']['epoch']+=1
            expected['data']['frontier']=deepcopy(saved['data']['frontier'])
            for key in ('observation_ids','source_record_ids'):
                need(set(previous['data']['frontier'][key])<=set(saved['data']['frontier'][key]))
        elif operation=='rollback':
            expected['status']='rolled_back';expected['data']['rollback']={
                'reason':body.reason,'origin':'human','selection_id':None,'basis':None}
        else:need(False)
    need(same(expected,saved))


def seal_grant(db,p,row,operation,command_id=None,monitor_basis=None):
    if operation=='auto_rollback':
        need(command_id is None and type(monitor_basis) is dict)
        from .threshold_policy_execution_v153 import validate_monitor_basis
        validate_monitor_basis(db,p,row,monitor_basis)
        need(same(monitor_basis,row.data['rollback']['basis']))
    else:canonical_uuid(command_id);need(monitor_basis is None)
    previous=None if operation=='create' else row.record_hash
    if operation=='create':need(row.version==1)
    else:row.version+=1
    row.data=deepcopy(row.data);_shape(snapshot(row));row.record_hash=hash_value(snapshot(row));db.add(row)
    audit(db,p,STATE_ACTION,row.id,{'schema':'threshold-policy-state-v153','policy_id':row.id,
        'workspace_id':p.workspace_id,'actor_id':p.user_id,'version':row.version,'status':row.status,
        'record_hash':row.record_hash,'previous_hash':previous,'operation':operation,'command_id':command_id,
        'monitor_basis':deepcopy(monitor_basis),'snapshot':snapshot(row)})
    db.flush()


def command_payload(row):
    return {'schema':'threshold-policy-command-pin-v153',**{key:getattr(row,key) for key in (
        'id','workspace_id','actor_id','request_id','method','path','wire_sha256','fingerprint','operation','target_id','policy_id','outcome')},'data':row.data}


def command_pin(row):
    return {'schema':'threshold-policy-command-audit-v153',**{key:getattr(row,key) for key in (
        'id','workspace_id','actor_id','request_id','method','path','wire_sha256','fingerprint','operation','target_id','policy_id','outcome','command_hash')},
        'result_hash':hash_value(row.data['result_snapshot'])}


def inventory(db,p,*,_read_inventory=None):
    """Only actual per-call inventories; no current-source gates or caches."""
    try:
        if _read_inventory is not None:
            from .threshold_policy_read_inventory_v155 import checked_read_inventory
            checked_read_inventory(_read_inventory,db,p)
        grants=list(db.scalars(select(Grant).where(Grant.workspace_id==p.workspace_id).execution_options(populate_existing=True)))
        rows={row.id:row for row in grants};need(len(rows)==len(grants))
        selections=list(db.scalars(select(Selection).where(Selection.workspace_id==p.workspace_id).execution_options(populate_existing=True)))
        by_selection={row.id:row for row in selections};need(len(by_selection)==len(selections)
            and all(row.policy_id in rows for row in selections))
        states=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==STATE_ACTION)
            .execution_options(populate_existing=True)))
        by_target={};by_version={}
        for issued in states:
            pin=issued.data;need(type(pin) is dict and set(pin)==STATE_KEYS and pin['schema']=='threshold-policy-state-v153'
                and issued.object_id in rows and pin['policy_id']==issued.object_id and pin['workspace_id']==p.workspace_id
                and issued.actor_id==pin['actor_id'] and type(pin['version']) is int and pin['version']>=1)
            _shape(pin['snapshot']);need(pin['snapshot']['id']==issued.object_id and pin['snapshot']['version']==pin['version']
                and pin['snapshot']['status']==pin['status'] and hash_value(pin['snapshot'])==pin['record_hash'])
            by_target.setdefault(issued.object_id,[]).append(issued)
            by_version.setdefault((issued.object_id,pin['version']),[]).append(issued)
        pairs=list(db.execute(select(Command,Audit).join(Audit,and_(Command.workspace_id==Audit.workspace_id,
            Audit.action==COMMAND_ACTION,Audit.object_id==Command.id),full=True).where(or_(
                Command.workspace_id==p.workspace_id,and_(Audit.workspace_id==p.workspace_id,Audit.action==COMMAND_ACTION)))
            .execution_options(populate_existing=True)))
        commands=[];saved={};input_ids=set();nonces=set();seen=set()
        for command,issued in pairs:
            need(command is not None and issued is not None and command.id not in seen
                and command.workspace_id==p.workspace_id and type(command.version) is int and command.version==1)
            for key in ('id','workspace_id','actor_id','request_id'):canonical_uuid(getattr(command,key))
            data=command.data;need(type(data) is dict and set(data)=={'schema','wire_text','result_snapshot'}
                and data['schema']=='threshold-policy-command-record-v153' and type(data['wire_text']) is str)
            raw=data['wire_text'].encode('utf-8');original=read_command(raw);target,operation,model=identify(command.method,command.path)
            need(original['request_id']==command.request_id and command.operation==operation
                and (target is None or target==command.policy_id) and wire_sha256(raw)==command.wire_sha256
                and command_fingerprint(command.method,command.path,p.workspace_id,command.actor_id,command.wire_sha256)==command.fingerprint
                and command.command_hash==hash_value(command_payload(command)))
            need(issued.actor_id==command.actor_id and issued.workspace_id==p.workspace_id
                and issued.object_id==command.id and same(issued.data,command_pin(command)))
            result=data['result_snapshot']
            if command.outcome=='saved':
                body=parse_command(model,original)
                need(command.policy_id in rows)
                if operation=='input':
                    need(type(result) is dict and set(result)==SELECTION_KEYS
                        and result['id']==command.target_id and result['policy_id']==command.policy_id
                        and result['workspace_id']==p.workspace_id and result['actor_id']==command.actor_id
                        and type(result['version']) is int and result['version']==1 and result['status']=='queued'
                        and result['choice']==body.expected_choice and result['source_record_id']==body.source_record_id
                        and command.target_id in by_selection and command.target_id not in input_ids)
                    actual=by_selection[command.target_id]
                    for key in SELECTION_KEYS-{'data','version','status'}:need(same(result[key],getattr(actual,key)))
                    need(result['source_id']==rows[command.policy_id].data['scope']['source_id'])
                    input_ids.add(command.target_id)
                else:
                    need(type(result) is dict and set(result)==SNAPSHOT_KEYS
                        and type(result['version']) is int and result['version']>=1
                        and command.target_id==command.policy_id==result['id'])
                    matched=by_version.get((command.target_id,result['version']),[])
                    need(len(matched)==1 and matched[0].data['command_id']==command.id
                        and same(matched[0].data['snapshot'],result))
                    saved[command.id]=(command,body)
            else:
                need(command.outcome=='rejected' and type(result) is dict and set(result)=={'status','detail'}
                    and type(result['status']) is int and result['status'] in {409,422})
                _text(result['detail'])
            nonce=(command.actor_id,command.request_id);need(nonce not in nonces)
            nonces.add(nonce);seen.add(command.id);commands.append(command)
        need(input_ids==set(by_selection));used=set();live=set()
        for row in grants:
            need(type(row.version) is int and row.version>=1 and row.record_hash==hash_value(snapshot(row)))
            if row.status!='rolled_back':need(row.scope_key not in live);live.add(row.scope_key)
            ordered=sorted(by_target.get(row.id,[]),key=lambda a:a.data['version'])
            need(len(ordered)==row.version);previous=None;previous_hash=None
            for version,issued in enumerate(ordered,1):
                pin=issued.data;need(pin['version']==version and pin['previous_hash']==previous_hash)
                result=pin['snapshot']
                if pin['operation']=='auto_rollback':
                    need(previous is not None and pin['command_id'] is None and type(pin['monitor_basis']) is dict
                        and same(pin['monitor_basis'],result['data']['rollback']['basis']))
                    from .threshold_policy_execution_v153 import validate_monitor_basis
                    if _read_inventory is None:validate_monitor_basis(db,p,row,pin['monitor_basis'])
                    else:validate_monitor_basis(db,p,row,pin['monitor_basis'],_read_inventory=_read_inventory)
                    _transition_checked(previous,result,'auto_rollback',None,issued.actor_id)
                else:
                    pair=saved.get(pin['command_id']);need(pair is not None and pair[0].id not in used and pin['monitor_basis'] is None)
                    command,body=pair;need(command.actor_id==issued.actor_id and command.operation==pin['operation']
                        and command.policy_id==row.id and same(command.data['result_snapshot'],result))
                    _transition_checked(previous,result,command.operation,body,command.actor_id);used.add(command.id)
                previous=result;previous_hash=pin['record_hash']
            need(previous is not None and same(previous,snapshot(row)) and previous_hash==row.record_hash)
        need(used==set(saved))
        if _read_inventory is not None:
            _read_inventory.pin_rows(db,p,(*grants,*selections,*states,*commands,*(issued for _,issued in pairs)))
        return grants,commands
    except PolicyInventoryError:raise
    except HTTPException as error:raise PolicyInventoryError() from error
    except (ValueError,TypeError,KeyError,AttributeError,UnicodeError,RecursionError,OverflowError):raise PolicyInventoryError() from None


def _grant_inventory(db,p,identity,lock=False,*,_read_inventory=None):
    canonical_uuid(identity);need(type(lock) is bool)
    need(_read_inventory is None or lock is False)
    query=select(Grant).where(Grant.workspace_id==p.workspace_id,Grant.id==identity)
    if lock:query=query.with_for_update()
    row=db.scalar(query.execution_options(populate_existing=True))
    if _read_inventory is None:rows,commands=inventory(db,p)
    else:rows,commands=inventory(db,p,_read_inventory=_read_inventory)
    if row is None:raise HTTPException(404,'有限策略不存在，或不在当前工作区')
    need(any(item is row for item in rows))
    return row,commands


def get_grant(db,p,identity,lock=False,check_selections=True):
    need(type(check_selections) is bool)
    row,_=_grant_inventory(db,p,identity,lock)
    if check_selections:
        from .threshold_policy_execution_v153 import selection_records
        selection_records(db,p,row)
    return row


def historical_review(db,p,row):
    """Prove the actual former approval, without requiring it is still current."""
    from . import quality_review_store_v152 as quality
    _,commands=quality.inventory(db,p);saved=row.data['review'];matches=[]
    for command in commands:
        if command.outcome=='saved' and command.operation=='approve' and command.target_id==row.review_id:
            original=command.data['result_snapshot']
            if original['version']==saved['pin']['version'] and same(original,saved['approved_snapshot']):matches.append(original)
    need(len(matches)==1,'原有限策略引用的真实质量批准快照无法核对')
    return matches[0]


def _environment(purpose):
    environment=settings().threshold_policy_environment
    need(environment in {'production','verification'})
    need(purpose!='mechanism_check' or environment=='verification',
        '机制样本不具生产资格；服务器未明确开启隔离验证，不能批准或使用候选',403)
    return environment


def _current_review(db,p,identity,lock):
    from . import quality_review_store_v152 as quality
    if lock:return quality.require_approved_review(db,p,identity,lock=True)
    row=quality.get_review(db,p,identity);need(row.status=='approved','原质量审核已撤回或未批准')
    from .threshold_shadow_store_v151 import _approver
    _approver(db,p,row.approved_by,False)
    frozen,issues,_=quality.freeze_data(db,p,row,lock=False);frozen['approved_by']=row.approved_by
    need(not issues and same(frozen,row.data['frozen']) and quality.hash_value(frozen)==row.approval_hash,
        '原质量审核依据已变化，不能继续候选策略')
    return row,deepcopy(frozen['scope'])


def fresh_candidate(db,p,row,*,lock=True):
    from . import quality_review_store_v152 as quality
    from .threshold_shadow_store_v151 import _approver
    _approver(db,p,row.actor_id,lock)
    review,scope=_current_review(db,p,row.review_id,lock)
    need(same(quality.review_pin(review),row.data['review']['pin'])
        and same(quality.snapshot(review),row.data['review']['approved_snapshot'])
        and same(scope,row.data['scope']),'原策略审批或固定范围已变化')
    _environment(row.data['purpose']);return row


def fresh_baseline(db,p,row,*,lock=True):
    """Rollback uses only the original baseline, never a live candidate gate."""
    historical_review(db,p,row);scope=row.data['scope']
    from .instance_api import active
    from .entity_release_compat import require_entity
    from .local_rules_api import _saved_manifest,require_current_check
    if lock:
        locked={}
        for model,key in ((DomainInstance,'instance_id'),(InstanceRelease,'release_id')):
            actual=db.scalar(select(model).where(model.workspace_id==p.workspace_id,model.id==scope[key])
                .with_for_update(read=True).execution_options(populate_existing=True))
            need(actual is not None,'原实例或发布已不可用，不能声称已恢复基线');locked[key]=actual
        entity=scoped(db,Entity,scope['entity_id'],p);package_id=entity.schema_capability_id
        ids={scope['source_id'],scope['baseline_id'],package_id,locked['release_id'].package_id}
        for identity in sorted(value for value in ids if value is not None):
            actual=db.scalar(select(Capability).where(Capability.workspace_id==p.workspace_id,Capability.id==identity)
                .with_for_update(read=identity!=scope['baseline_id']).execution_options(populate_existing=True))
            need(actual is not None,'原来源或基线已不可用，不能替换另一规则')
        entity=db.scalar(select(Entity).where(Entity.workspace_id==p.workspace_id,Entity.id==scope['entity_id'])
            .with_for_update(read=True).execution_options(populate_existing=True))
        need(entity is not None and entity.schema_capability_id==package_id,'原对象已变化')
    instance,release=active(db,p,scope['instance_id']);need(release.id==scope['release_id'],'实例发布已变化，不能迁移原基线')
    entity=scoped(db,Entity,scope['entity_id'],p);require_entity(db,p,instance,release,entity)
    source=published(db,p,scope['source_id'],'source');baseline=published(db,p,scope['baseline_id'],'tool')
    need(source.content_hash==scope['source_hash'] and baseline.content_hash==scope['baseline_hash']
        and same(baseline.data,row.data['baseline_manifest']),'原来源或固定基线已变化；回滚保留但新输入受阻')
    _saved_manifest(db,p,baseline);require_current_check(db,p,baseline)
    metadata=baseline.data['specification']['managed_numeric_rule']
    need(metadata['entity_id']==entity.id and metadata['package_id']==entity.schema_capability_id
        and metadata['entity_type']==entity.entity_type,'原基线不属于此对象')
    from .perception_signals import rule_contract
    rule_contract(db,p,instance,release,entity,source,baseline)
    return row


def _source(function,*args,**kwargs):
    try:return function(*args,**kwargs)
    except HTTPException as error:
        if error.status_code not in {403,404,409,422}:raise
        raise PolicySourceError() from error


def selection_usage(db,p,row,*,_read_inventory=None):
    from .threshold_policy_execution_v153 import selection_records
    if _read_inventory is None:values=selection_records(db,p,row)
    else:values=selection_records(db,p,row,_read_inventory=_read_inventory)
    need(type(values) is list)
    return values,sum(item.choice=='candidate' for item in values)


def current_choice(db,p,row,*,lock=True):
    if row.status=='rolled_back':_source(fresh_baseline,db,p,row,lock=lock);return 'baseline'
    need(row.status=='active','策略已暂停；不会自动改用其他规则')
    _source(fresh_candidate,db,p,row,lock=lock)
    need(datetime.fromisoformat(row.data['expires_at'])>db.scalar(select(func.clock_timestamp())),
        '策略已到期；新候选受阻，请明确回滚基线')
    _,used=selection_usage(db,p,row)
    need(used<row.data['max_candidate_inputs'],'候选输入次数已用完；不退款，请明确回滚基线')
    return 'candidate'


def _rules(row):
    from .threshold_study_views_v149 import rule_summary
    scope=row.data['scope']
    return (rule_summary(scope['baseline_id'],row.data['baseline_manifest'],scope['field_label']),
        rule_summary(scope['candidate_capability_id'],row.data['candidate_manifest'],scope['field_label']))


def view(db,p,row):
    from .threshold_policy_read_inventory_v155 import PolicyReadInventory
    with PolicyReadInventory(db,p) as read:
        return _display_view(db,p,row,read)


def _display_view(db,p,row,read):
    row,commands=_grant_inventory(db,p,row if type(row) is str else row.id,_read_inventory=read)
    rows,used=selection_usage(db,p,row,_read_inventory=read);scope=deepcopy(row.data['scope']);baseline,candidate=_rules(row)
    from .threshold_policy_read_projection_v153 import PolicyReadProjection
    readiness=PolicyReadProjection(db,p,row,rows,_read_inventory=read)
    issue=None;choice=None;candidate_ok=True;baseline_ok=True
    try:fresh_candidate(db,p,row,lock=False)
    except HTTPException as error:
        if error.status_code not in {403,404,409,422}:raise
        candidate_ok=False;candidate['threshold']=None;scope['candidate_threshold']=None
    try:fresh_baseline(db,p,row,lock=False)
    except HTTPException as error:
        if error.status_code not in {403,404,409,422}:raise
        baseline_ok=False;baseline['threshold']=None;scope['baseline_threshold']=None
    expired=datetime.fromisoformat(row.data['expires_at'])<=db.scalar(select(func.clock_timestamp()))
    if row.status=='rolled_back':
        if baseline_ok:choice='baseline'
        else:issue='已永久回滚候选；原基线当前不可用，新输入受阻，不能声称恢复成功'
    elif row.status=='paused':issue='策略已暂停；原输入保留，恢复不会补追暂停期间登记'
    elif not candidate_ok:issue=UNAVAILABLE
    elif expired:issue='策略已到期；请明确回滚基线，不会静默替换规则'
    elif used>=row.data['max_candidate_inputs']:issue='候选输入次数已用完；已占用不退款，请明确回滚基线'
    else:choice='candidate'
    input_spec=None
    if choice is not None:
        from .threshold_policy_rules_v153 import input_spec as describe_input
        try:
            source=scoped(db,Capability,scope['source_id'],p)
            manifest=row.data['candidate_manifest' if choice=='candidate' else 'baseline_manifest']
            input_spec=describe_input(row.data['scope'],manifest,source.data)
        except (ValueError,TypeError,KeyError):
            choice=None;issue='原选择的输入字段或来源时区无法核对；原记录保留，未开放新登记'
    readiness.finish(db,p,row,candidate_ok,baseline_ok)
    from .threshold_policy_execution_v153 import selection_view
    values=[selection_view(db,p,item,checked=True,_readiness=readiness) for item in rows]
    for item in values:
        readable=baseline_ok if item['choice']=='baseline' else candidate_ok
        if not readable:
            item['value']=None;item['actual_matched']=None;item['baseline_projection_matched']=None
            if item['extractor'] is not None:item['extractor']['threshold']=None
        if not baseline_ok and item['baseline'] is not None:item['baseline']['threshold']=None
    from .threshold_policy_execution_v153 import monitor_view
    monitor=monitor_view(db,p,row,_readiness=readiness)
    history=[]
    for issued in db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==STATE_ACTION,
        Audit.object_id==row.id).order_by(Audit.data['version'].as_integer())):
        pin=issued.data;command=next((c for c in commands if c.id==pin['command_id']),None)
        history.append({'version':pin['version'],'operation':pin['operation'],
            'reason':read_command(command.data['wire_text'].encode('utf-8'))['reason'] if command else pin['snapshot']['data']['reason'],
            'actor_id':pin['actor_id'],'origin':'monitor' if command is None else 'human',
            'selection_id':pin['snapshot']['data']['rollback']['selection_id'] if command is None else None})
    readiness.check(db,p,row);admin=p.role=='admin'
    return {'schema':'threshold-policy-view-v153','workspace_id':p.workspace_id,'id':row.id,'version':row.version,
        'actor_id':row.actor_id,'review_id':row.review_id,'status':row.status,'title':row.data['title'],'reason':row.data['reason'],
        'purpose':row.data['purpose'],'scope':scope,'baseline':baseline,'candidate':candidate,
        'max_candidate_inputs':row.data['max_candidate_inputs'],'used_candidate_inputs':used,
        'expires_at':row.data['expires_at'],'epoch':row.data['epoch'],'choice':choice,'current_issue':issue,
        'can_input':p.role in {'admin','operator'} and choice is not None,'can_pause':admin and row.status=='active',
        'can_resume':admin and row.status=='paused' and candidate_ok and not expired and used<row.data['max_candidate_inputs'],
        'can_rollback':admin and row.status!='rolled_back',
        'production_eligible':row.data['purpose']=='business_assessment' and choice=='candidate',
        'input_spec':input_spec,'monitor':monitor,'selections':values,'history':history}


def options(db,p,review_id):
    from . import quality_review_store_v152 as quality
    result={'schema':'threshold-policy-options-v153','workspace_id':p.workspace_id,'review_id':review_id,
        'review_version':None,'purpose':None,'scope':None,'baseline':None,'candidate':None,
        'max_candidate_inputs_limit':30,'max_duration_seconds':604800,'environment':settings().threshold_policy_environment,
        'can_create':False,'current_issue':None}
    try:
        row,scope=_current_review(db,p,review_id,False)
        from .threshold_study_views_v149 import rule_summary
        frozen=row.data['frozen'];result.update(review_version=row.version,purpose=frozen['purpose'],scope=scope,
            baseline=rule_summary(scope['baseline_id'],frozen['baseline_manifest'],scope['field_label']),
            candidate=rule_summary(scope['candidate_capability_id'],frozen['candidate_manifest'],scope['field_label']))
        _environment(frozen['purpose'])
        grants,_=inventory(db,p)
        if any(g.scope_key==scope_key(scope) and g.status!='rolled_back' for g in grants):
            result['current_issue']='同一原范围已有未回滚策略，请先读取并核对原策略'
            return result
        result['can_create']=p.role=='admin'
        if p.role!='admin':result['current_issue']='只有管理员可以明确批准有限实际使用'
    except HTTPException as error:
        if error.status_code not in {403,404,409,422}:raise
        result['current_issue']=('机制审核不具生产资格；服务器须明确开启隔离验证才能批准候选策略'
            if result['purpose']=='mechanism_check' and result['environment']=='production' else UNAVAILABLE)
        if result['scope'] is not None:
            result['scope']['baseline_threshold']=None;result['scope']['candidate_threshold']=None
            result['baseline']['threshold']=None;result['candidate']['threshold']=None
    return result
