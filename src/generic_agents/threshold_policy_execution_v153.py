"""Actual finite numeric selections. History is never dispatch authority."""
from copy import deepcopy
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select,func,or_
from .models import Audit,Observation,Job,Invocation,Capability,DomainInstance,InstanceRelease,Entity,uid,now
from .services import audit,scoped,published
from .contracts import digest,ObservationCreate
from .security import require
from .relations import lock_current_actor
from .leases import ownership
from .typed_payload_codec_v131 import canonical_json
from .threshold_policy_models_v152 import ThresholdPolicyGrant as Grant,ThresholdPolicyCommand as Command,ThresholdPolicySelection as Selection
from .threshold_policy_protocol_v152 import parse_command,PolicyInput,read_command,canonical_uuid
from .threshold_policy_rules_v153 import project

STATE='threshold_policy.selection_state_v153'
RECOVERY='threshold_policy.recovery_v153'
ERROR='原策略输入、后台任务或调用库存无法核对；原记录保留，未重新调用'
FIELDS=('id','workspace_id','policy_id','actor_id','source_id','source_record_id','observation_id','job_id','choice','status','version','data')
DATA={'schema','grant_pin','review_pin','scope_hash','extractor_manifest','baseline_manifest','wire_meta','command_id',
      'original_request','call_key','reservation','invocation','result','error','late'}
IMMUTABLE=DATA-{'reservation','invocation','result','error','late'}
STATE_KEYS={'schema','selection_id','workspace_id','actor_id','version','status','record_hash','previous_hash','operation','command_id','snapshot'}


def _store():
    from . import threshold_policy_store_v153 as store
    return store


def _need(condition,message=ERROR):
    if not condition:
        if message==ERROR:raise _store().PolicyInventoryError(ERROR)
        raise HTTPException(409,message)


def _hash(value):return _store().hash_value(value)
def _same(a,b):return canonical_json(a)==canonical_json(b)
def snapshot(row):return {key:deepcopy(getattr(row,key)) for key in FIELDS}
def selection_pin(row):return {'id':row.id,'version':row.version,'record_hash':row.record_hash}
def grant_pin(row):return {'id':row.id,'version':row.version,'record_hash':row.record_hash,'epoch':row.data['epoch']}
def _rows(db,query):return list(db.scalars(query.execution_options(populate_existing=True)))


def _seal(db,p,row,operation,*,initial=False):
    previous=None if initial else row.record_hash
    if not initial:row.version+=1
    row.data=deepcopy(row.data);_shape(snapshot(row));row.record_hash=_hash(snapshot(row));db.add(row)
    audit(db,p,STATE,row.id,{'schema':'threshold-policy-selection-state-v153','selection_id':row.id,
        'workspace_id':p.workspace_id,'actor_id':p.user_id,'version':row.version,'status':row.status,
        'record_hash':row.record_hash,'previous_hash':previous,'operation':operation,
        'command_id':row.data['command_id'] if initial else None,'snapshot':snapshot(row)})
    db.flush()


def _shape(saved):
    _need(type(saved) is dict and set(saved)==set(FIELDS))
    for key in ('id','workspace_id','policy_id','actor_id','source_id','observation_id','job_id'):canonical_uuid(saved[key])
    _need(type(saved['version']) is int and saved['version']>=1 and saved['choice'] in {'candidate','baseline'}
        and saved['status'] in {'queued','reserved','completed','failed','stopped'} and type(saved['source_record_id']) is str)
    d=saved['data'];_need(type(d) is dict and set(d)==DATA and d['schema']=='threshold-policy-selection-record-v153')
    pin=d['grant_pin'];_need(type(pin) is dict and set(pin)=={'id','version','record_hash','epoch'}
        and pin['id']==saved['policy_id'] and type(pin['version']) is int and pin['version']>=1
        and type(pin['epoch']) is int and pin['epoch']>=1)
    _need(d['call_key']=='threshold-policy-call:'+saved['policy_id']+':'+saved['id'] and type(d['late']) is bool)
    canonical_uuid(d['command_id']);_need(type(d['wire_meta']) is dict
        and set(d['wire_meta'])=={'request_id','method','path','wire_text','wire_sha256','fingerprint'})
    raw=d['wire_meta']['wire_text'].encode('utf-8');body=parse_command(PolicyInput,read_command(raw))
    from .threshold_evolution_protocol_v147 import wire_sha256,command_fingerprint
    wire=d['wire_meta'];_need(wire['request_id']==body.request_id and wire['method']=='POST'
        and wire['path']=='/api/local-rules/evolution/threshold-policies/'+saved['policy_id']+'/inputs'
        and wire['wire_sha256']==wire_sha256(raw) and wire['fingerprint']==command_fingerprint(
            wire['method'],wire['path'],saved['workspace_id'],saved['actor_id'],wire['wire_sha256']))
    original=d['original_request'];_need(type(original) is dict and original['source_id']==saved['source_id']
        and original['source_record_id']==saved['source_record_id'] and original['processing_version']==body.processing_version
        and original['observed_at']==body.observed_at and original['business_day']==body.business_day
        and _same(original['attributes'],body.attributes) and body.expected_choice==saved['choice']
        and body.expected_version==pin['version'])
    if saved['status']=='queued':_need(d['reservation'] is None and d['invocation'] is None and d['result'] is None and d['error'] is None and not d['late'])
    if d['reservation'] is not None:
        r=d['reservation'];_need(type(r) is dict and set(r)=={'job_id','lease_token','inputs_hash','extractor_id','extractor_hash','role','reserved_at'}
            and r['job_id']==saved['job_id'] and r['role']=='perception');canonical_uuid(r['lease_token'])
    if saved['status'] in {'reserved','completed'}:_need(d['reservation'] is not None)
    if saved['status']=='completed':_need(type(d['result']) is dict and d['invocation']['status']=='succeeded' and not d['late'] and d['error'] is None)
    if saved['status'] in {'failed','stopped'}:_need(type(d['error']) is str and bool(d['error']))


def _call_pin(call):
    return {'id':call.id,'version':call.version,'status':call.status,'capability_id':call.capability_id,
        'input_hash':call.data.get('input_hash'),'capability_hash':call.data.get('capability_hash'),
        'job_id':call.data.get('job_id'),'lease_token':call.data.get('lease_token'),
        'output_hash':_hash(call.data.get('output')),'error_hash':_hash(call.data.get('error'))}


def _call(db,p,row):
    calls=_rows(db,select(Invocation).where(Invocation.workspace_id==p.workspace_id,
        Invocation.idempotency_key==row.data['call_key']))
    _need(len(calls)<=1);return calls[0] if calls else None


def _current_selection_state(db,p,row):
    chain=sorted(_rows(db,select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action==STATE,Audit.object_id==row.id)),key=lambda a:a.data.get('version',0))
    _need(len(chain)==row.version);previous=None
    for version,a in enumerate(chain,1):
        d=a.data;_need(set(d)==STATE_KEYS and d['selection_id']==row.id and d['workspace_id']==p.workspace_id
            and a.actor_id==d['actor_id']==row.actor_id and d['version']==version
            and d['previous_hash']==previous and d['record_hash']==_hash(d['snapshot']))
        _shape(d['snapshot']);previous=d['record_hash']
    _need(previous==row.record_hash and _same(chain[-1].data['snapshot'],snapshot(row)))
    command=scoped(db,Command,row.data['command_id'],p)
    _need(command.operation=='input' and command.outcome=='saved' and command.target_id==row.id
        and _same(command.data['result_snapshot'],chain[0].data['snapshot']))


def _bindings(db,p,row,grant,*,_read_collector=None):
    d=row.data;scope=grant.data['scope'];obs=scoped(db,Observation,row.observation_id,p);job=scoped(db,Job,row.job_id,p)
    _need(d['scope_hash']==_hash(scope) and _same(d['review_pin'],grant.data['review']['pin'])
        and _same(d['baseline_manifest'],grant.data['baseline_manifest']))
    expected=grant.data['candidate_manifest'] if row.choice=='candidate' else grant.data['baseline_manifest']
    extractor=scope['candidate_capability_id'] if row.choice=='candidate' else scope['baseline_id']
    _need(_same(d['extractor_manifest'],expected) and d['original_request']['extractor_id']==extractor
        and d['original_request']['source_id']==scope['source_id'] and d['original_request']['entity_id']==scope['entity_id']
        and row.source_id==scope['source_id'] and obs.entity_id==scope['entity_id']
        and obs.source_id==row.source_id and obs.source_record_id==row.source_record_id
        and obs.processing_version==d['original_request']['processing_version']
        and obs.business_day==d['original_request']['business_day']
        and obs.data.get('threshold_policy_selection_id')==row.id
        and job.data.get('threshold_policy_selection_id')==row.id
        and obs.data.get('threshold_policy_id')==row.policy_id and job.data.get('threshold_policy_id')==row.policy_id
        and obs.data.get('submitted_by')==row.actor_id and _same(obs.data.get('original_request'),d['original_request'])
        and obs.data.get('input_hash')==digest(d['original_request'])
        and _same(obs.data.get('attributes'),d['original_request']['attributes'])
        and job.kind=='process_reported_observation' and job.idempotency_key=='extract:'+obs.id
        and job.data.get('observation_id')==obs.id and job.data.get('actor_id')==row.actor_id
        and job.data.get('extractor_id')==extractor and job.data.get('resume_invocation_key') in {None,d['call_key']})
    from .observation_time_anchor_v135 import same_observation_instant
    same_observation_instant(d['original_request']['observed_at'],obs.observed_at)
    states=_rows(db,select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==_store().STATE_ACTION,Audit.object_id==grant.id))
    matches=[a for a in states if a.data.get('version')==d['grant_pin']['version'] and a.data.get('record_hash')==d['grant_pin']['record_hash']]
    _need(len(matches)==1);saved=matches[0].data['snapshot']
    _need(saved['data']['epoch']==d['grant_pin']['epoch'] and saved['status']==('active' if row.choice=='candidate' else 'rolled_back'))
    call=_call(db,p,row)
    reservation=d['reservation']
    if reservation is not None:
        _need(reservation['extractor_id']==extractor and reservation['extractor_hash']==scope[
            'candidate_hash' if row.choice=='candidate' else 'baseline_hash']
            and reservation['inputs_hash']==digest({'attributes':d['original_request']['attributes']}))
    if call is not None:
        reservation=d['reservation'];_need(reservation is not None and call.capability_id==extractor
            and call.data.get('role')=='perception' and call.data.get('approval') is None
            and call.data.get('side_effect') is False and call.data.get('input_hash')==digest({'attributes':d['original_request']['attributes']})
            and call.data.get('capability_hash')==reservation['extractor_hash']
            and call.data.get('job_id')==row.job_id and call.data.get('lease_token')==reservation['lease_token'])
    if d['invocation'] is not None:_need(call is not None and _same(d['invocation'],_call_pin(call)))
    if row.status=='completed':
        result=d['result'];projection=project(scope,d['original_request']['attributes']);matched=projection[row.choice+'_matched']
        _need(obs.status=='processed' and obs.data.get('invocation_id')==call.id
            and _same(obs.data.get('extracted'),call.data.get('output'))
            and result['actual_matched'] is matched and result['baseline_projection_matched'] is projection['baseline_matched']
            and result['extracted_hash']==_hash(call.data['output']))
    if _read_collector is not None:
        from .threshold_policy_read_inventory_v155 import scoped_read_inventory
        scoped_read_inventory(_read_collector,db,p).stage_binding(db,p,row,grant,(obs,job,call),states)
    return obs,job,call


def _selection_records(db,p,*,_read_collector=None):
    """Whole workspace inventory; no current Grant/Quality gate and no recursion."""
    if _read_collector is not None:
        from .threshold_policy_read_inventory_v155 import scoped_read_inventory
        scoped_read_inventory(_read_collector,db,p).collecting(db,p)
    rows=_rows(db,select(Selection).where(Selection.workspace_id==p.workspace_id));by_id={r.id:r for r in rows};_need(len(by_id)==len(rows))
    states=_rows(db,select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==STATE));ordered={}
    for a in states:
        d=a.data;_need(type(d) is dict and set(d)==STATE_KEYS and d['schema']=='threshold-policy-selection-state-v153'
            and a.object_id==d['selection_id'] and a.actor_id==d['actor_id'] and d['workspace_id']==p.workspace_id
            and d['selection_id'] in by_id and d['record_hash']==_hash(d['snapshot']))
        _shape(d['snapshot']);_need(d['snapshot']['id']==a.object_id and d['snapshot']['version']==d['version'] and d['snapshot']['status']==d['status'])
        _need(a.actor_id==d['snapshot']['actor_id']==by_id[a.object_id].actor_id)
        ordered.setdefault(a.object_id,[]).append(a)
    commands=_rows(db,select(Command).where(Command.workspace_id==p.workspace_id,Command.operation=='input',Command.outcome=='saved'))
    # A surviving actual receipt is an independent ownership trace even if all
    # removable markers and the Selection side of the inventory were lost.
    calls=_rows(db,select(Invocation).where(Invocation.workspace_id==p.workspace_id,
        Invocation.idempotency_key.startswith('threshold-policy-call:')))
    if _read_collector is not None:_read_collector.stage_rows(db,p,(*rows,*states,*commands,*calls))
    by_key={r.data['call_key']:r for r in rows}
    for call in calls:
        owner=by_key.get(call.idempotency_key)
        _need(owner is not None and call.data.get('job_id')==owner.job_id)
    by_target={}
    for command in commands:by_target.setdefault(command.target_id,[]).append(command)
    _need(set(by_target)==set(by_id) and set(ordered)==set(by_id))
    for row in rows:
        _shape(snapshot(row));_need(row.record_hash==_hash(snapshot(row)))
        chain=sorted(ordered[row.id],key=lambda a:a.data['version']);_need(len(chain)==row.version)
        previous=None;old_hash=None
        for index,a in enumerate(chain,1):
            d=a.data;saved=d['snapshot'];_need(d['version']==index and d['previous_hash']==old_hash)
            if previous is None:
                _need(d['operation']=='create' and saved['status']=='queued' and d['command_id']==saved['data']['command_id'])
            else:
                _need(d['command_id'] is None and previous['status'] in {'queued','reserved'}
                    and saved['status'] in {'reserved','completed','failed','stopped'}
                    and _same({k:v for k,v in previous.items() if k not in {'data','version','status'}},{k:v for k,v in saved.items() if k not in {'data','version','status'}})
                    and all(_same(previous['data'][k],saved['data'][k]) for k in IMMUTABLE))
                _need((previous['status'],saved['status'],d['operation']) in {
                    ('queued','reserved','reserve'),('queued','stopped','stop'),('reserved','completed','complete'),
                    ('reserved','failed','fail'),('reserved','stopped','stop')})
                if previous['data']['reservation'] is not None:_need(_same(previous['data']['reservation'],saved['data']['reservation']))
            previous=saved;old_hash=d['record_hash']
        _need(_same(previous,snapshot(row)) and old_hash==row.record_hash)
        matches=by_target[row.id];_need(len(matches)==1);command=matches[0]
        _need(command.id==row.data['command_id'] and command.policy_id==row.policy_id and command.actor_id==row.actor_id
            and _same(command.data['result_snapshot'],chain[0].data['snapshot'])
            and command.command_hash==_hash(_store().command_payload(command)))
        wire=row.data['wire_meta']
        _need(all(_same(getattr(command,key),wire[key]) for key in ('request_id','method','path','wire_sha256','fingerprint'))
            and command.data.get('wire_text')==wire['wire_text'])
        audits=_rows(db,select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==_store().COMMAND_ACTION,Audit.object_id==command.id))
        _need(len(audits)==1 and audits[0].actor_id==command.actor_id and _same(audits[0].data,_store().command_pin(command)))
        grant=scoped(db,Grant,row.policy_id,p)
        if _read_collector is None:_bindings(db,p,row,grant)
        else:
            _bindings(db,p,row,grant,_read_collector=_read_collector)
            _read_collector.stage_lineage(db,p,row,(command,*audits,*chain))
    return rows,ordered


def selection_records(db,p,grant,*,_read_inventory=None):
    try:
        if _read_inventory is not None:
            from .threshold_policy_read_inventory_v155 import scoped_read_inventory
            return scoped_read_inventory(_read_inventory,db,p).rows_for(db,p,grant)
        rows,_=_selection_records(db,p)
        return [r for r in rows if r.policy_id==grant.id]
    except _store().PolicyInventoryError:raise
    except (HTTPException,ValueError,TypeError,KeyError,AttributeError,UnicodeError,OverflowError):raise _store().PolicyInventoryError(ERROR) from None


def selection_inventory(db,p,grant):
    return [selection_view(db,p,r,checked=True) for r in selection_records(db,p,grant)]


def submit_input(db,p,grant,body,command_id,wire_meta):
    require(p,'admin','operator');lock_current_actor(db,p)
    g=_store().get_grant(db,p,grant.id,True);_need(g.version==body.expected_version,'策略版本已变化，请保留原输入并重新核对')
    _store().lock_source_key(db,p,g.data['scope']['source_id'],body.source_record_id)
    choice=_store().current_choice(db,p,g);_need(choice==body.expected_choice,'当前选择与原确认不同，未静默换用规则')
    scope=g.data['scope'];project(scope,body.attributes)
    _need(body.source_record_id not in g.data['frontier']['source_record_ids'],'该来源键属于批准或恢复前的登记，不能补追')
    extractor=scope['candidate_capability_id'] if choice=='candidate' else scope['baseline_id']
    instance=scoped(db,DomainInstance,scope['instance_id'],p);release=scoped(db,InstanceRelease,scope['release_id'],p)
    entity=scoped(db,Entity,scope['entity_id'],p);source=published(db,p,scope['source_id'],'source');cap=published(db,p,extractor)
    from .perception_signals import rule_contract
    contract=rule_contract(db,p,instance,release,entity,source,cap)
    request=ObservationCreate(source_id=scope['source_id'],source_record_id=body.source_record_id,processing_version=body.processing_version,
        entity_id=scope['entity_id'],observed_at=body.observed_at,business_day=body.business_day,extractor_id=extractor,
        startup_mode='manual',attributes=deepcopy(body.attributes))
    from .threshold_policy_intake_v152 import register
    obs,job=register(db,p,request,contract);identity=uid()
    row=Selection(id=identity,workspace_id=p.workspace_id,policy_id=g.id,actor_id=p.user_id,source_id=scope['source_id'],
        source_record_id=body.source_record_id,observation_id=obs.id,job_id=job.id,choice=choice,status='queued',version=1,record_hash='',
        data={'schema':'threshold-policy-selection-record-v153','grant_pin':grant_pin(g),'review_pin':deepcopy(g.data['review']['pin']),
            'scope_hash':_hash(scope),'extractor_manifest':deepcopy(cap.data),'baseline_manifest':deepcopy(g.data['baseline_manifest']),
            'wire_meta':deepcopy(wire_meta),'command_id':command_id,'original_request':deepcopy(obs.data['original_request']),
            'call_key':'threshold-policy-call:'+g.id+':'+identity,'reservation':None,'invocation':None,'result':None,'error':None,'late':False})
    obs.data={**obs.data,'threshold_policy_id':g.id,'threshold_policy_selection_id':identity}
    job.data={**job.data,'threshold_policy_id':g.id,'threshold_policy_selection_id':identity}
    _seal(db,p,row,'create',initial=True);return row


def resolve(db,p,*,observation=None,job=None):
    """Resolve persisted ownership even when a removable JSON marker is absent."""
    if job is not None:
        owners=_rows(db,select(Selection).where(Selection.workspace_id==p.workspace_id,Selection.job_id==job.id))
        trace=db.scalar(select(Audit.id).where(Audit.workspace_id==p.workspace_id,Audit.action==STATE,
            Audit.data['snapshot']['job_id'].as_string()==job.id).limit(1))
        command_trace=db.scalar(select(Command.id).where(Command.workspace_id==p.workspace_id,Command.operation=='input',
            Command.outcome=='saved',Command.data['result_snapshot']['job_id'].as_string()==job.id).limit(1))
        invocation_trace=db.scalar(select(Invocation.id).where(Invocation.workspace_id==p.workspace_id,
            Invocation.idempotency_key.startswith('threshold-policy-call:'),Invocation.data['job_id'].as_string()==job.id).limit(1))
        if owners:
            _need(len(owners)==1 and owners[0].observation_id==job.data.get('observation_id') and job.kind=='process_reported_observation')
        else:_need(trace is None and command_trace is None and invocation_trace is None and not job.data.get('threshold_policy_selection_id'))
    identity=observation.id if observation is not None else job.data.get('observation_id')
    if identity is None:return None
    found=_rows(db,select(Selection).where(Selection.workspace_id==p.workspace_id,Selection.observation_id==identity))
    marker=(observation.data if observation is not None else job.data).get('threshold_policy_selection_id')
    traces=db.scalar(select(Audit.id).where(Audit.workspace_id==p.workspace_id,Audit.action==STATE,
        Audit.data['snapshot']['observation_id'].as_string()==identity).limit(1))
    command=db.scalar(select(Command.id).where(Command.workspace_id==p.workspace_id,Command.operation=='input',
        Command.outcome=='saved',Command.data['result_snapshot']['observation_id'].as_string()==identity).limit(1))
    jobs=_rows(db,select(Job).where(Job.workspace_id==p.workspace_id,Job.data['observation_id'].as_string()==identity))
    call_trace=db.scalar(select(Invocation.id).where(Invocation.workspace_id==p.workspace_id,
        Invocation.idempotency_key.startswith('threshold-policy-call:'),
        Invocation.data['job_id'].as_string().in_([j.id for j in jobs])).limit(1))
    if not found:
        _need(not marker and traces is None and command is None and call_trace is None);return None
    _need(len(found)==1);row=found[0]
    g=_store().get_grant(db,p,row.policy_id,True,check_selections=False)
    rows,_=_selection_records(db,p);row=next((r for r in rows if r.id==row.id),None);_need(row is not None)
    _bindings(db,p,row,g)
    if job is not None:_need(row.job_id==job.id)
    return row,g


def historical_call_key(db,p,observation,job):
    """Read the actual completed selection; never grant fresh execution rights."""
    found=resolve(db,p,observation=observation,job=job)
    if found is None:return None
    row,_=found
    _need(row.status=='completed' and row.observation_id==observation.id and row.job_id==job.id)
    return row.data['call_key']


def _lease(db,p,row,job,call=None):
    owned=ownership(db);_need(owned.get('job_id')==job.id and type(owned.get('lease_token')) is str,
        '此处理缺少原后台租约，未开放规则调用')
    _need(job.status=='running' and job.lease_token==owned['lease_token'] and job.workspace_id==p.workspace_id
        and job.data.get('actor_id')==p.user_id,'后台任务或当前操作者已变化')
    reservation=row.data['reservation']
    if reservation is not None and reservation['lease_token']!=owned['lease_token']:
        _need(call is not None and call.status=='succeeded','原调用已有预约或结果未知，只能核查，不能换租约重调')
        audits=_rows(db,select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==RECOVERY,Audit.object_id==row.id))
        exact=[a for a in audits if a.data.get('to_lease')==owned['lease_token']
            and a.data.get('from_lease')==reservation['lease_token'] and a.data.get('job_id')==job.id
            and _same(a.data.get('invocation'),_call_pin(call))]
        _need(len(exact)==1,'原成功回执尚未在本次租约下只读核对')
    return owned


def _current_policy(db,p,row,g):
    require(p,'admin','operator');lock_current_actor(db,p)
    pin=row.data['grant_pin'];before=(g.version,g.record_hash,g.data['epoch'])
    _need(before==(pin['version'],pin['record_hash'],pin['epoch']),'策略已暂停、恢复或回滚，原排队输入不补追、不换规则')
    if row.choice=='candidate':
        _need(g.status=='active','候选策略已停止，原回执只保留历史')
        _store().fresh_candidate(db,p,g,lock=True)
        _need(datetime.fromisoformat(g.data['expires_at'])>db.scalar(select(func.clock_timestamp())),
            '候选授权已到期，原回执只保留历史')
    else:
        _need(g.status=='rolled_back','此输入没有明确的原基线回滚资格')
        _store().fresh_baseline(db,p,g,lock=True)
    # populate_existing inside current checks must never silently replace a pin.
    _need((g.version,g.record_hash,g.data['epoch'])==before,'策略在当前资格核对时发生变化')


def _authority(db,p,row,g,*,call=None):
    obs,job,actual=_bindings(db,p,row,g);_lease(db,p,row,job,call or actual)
    _current_policy(db,p,row,g)
    return obs,job,actual


def prepare_job(db,p,job):
    owned=resolve(db,p,job=job)
    if owned is None:return None
    row,g=owned
    if row.status=='completed':
        # A later delivery/final-Job recovery cannot create another formal
        # result. The persisted complete inventory is read-only authority.
        return row.data['call_key']
    obs,job,call=_authority(db,p,row,g)
    _need(row.status in {'queued','reserved'},'原策略输入已经终止，只能查看原记录')
    if row.status=='queued':
        _need(call is None);cap=published(db,p,job.data['extractor_id']);lease=_lease(db,p,row,job)
        row.status='reserved';row.data={**row.data,'reservation':{'job_id':job.id,'lease_token':lease['lease_token'],
            'inputs_hash':digest({'attributes':obs.data['attributes']}),'extractor_id':cap.id,
            'extractor_hash':cap.content_hash,'role':'perception','reserved_at':now().isoformat()}}
        _seal(db,p,row,'reserve')
    elif call is None:
        # A committed reservation without a running receipt is ambiguous. The
        # same first transaction may still create it; a recovered lease may not.
        _need(row.data['reservation']['lease_token']==ownership(db)['lease_token'],
            '原预约没有可核对调用，不能换租约重调')
    return row.data['call_key']


def extract_result(db,p,observation,extractor_id):
    """Return (policy key, saved Invocation). A success is adopted without invoke."""
    owned=resolve(db,p,observation=observation)
    if owned is None:return None,None
    row,g=owned;obs,job,call=_authority(db,p,row,g)
    _need(row.status=='reserved' and job.data['extractor_id']==extractor_id)
    if call is not None:
        _need(call.status=='succeeded','原调用已有预约、失败或未知结果，禁止另开调用；请核对原回执')
    return row.data['call_key'],call


def authorize_gateway(db,p,capability_id,agent_role,inputs,key):
    try:
        parts=key.split(':');_need(len(parts)==3 and parts[0]=='threshold-policy-call')
        canonical_uuid(parts[1]);canonical_uuid(parts[2])
        row=scoped(db,Selection,parts[2],p);_need(row.policy_id==parts[1] and row.data['call_key']==key)
        g=_store().get_grant(db,p,row.policy_id,True,check_selections=False)
        # Do not re-read workspace Selection states here: a new reservation is
        # in the owned uncommitted transaction, before its Invocation exists.
        _need(row.record_hash==_hash(snapshot(row)) and row.status=='reserved')
        _current_selection_state(db,p,row)
        obs,job,call=_authority(db,p,row,g)
        reservation=row.data['reservation'];_need(reservation is not None and capability_id==reservation['extractor_id']
            and agent_role=='perception' and _same(inputs,{'attributes':obs.data['attributes']})
            and digest(inputs)==reservation['inputs_hash'])
        if call is not None:_need(call.status=='running' and call.data.get('lease_token')==ownership(db)['lease_token'],
            '该实际调用已保存或已未知，不能再次派发')
    except _store().PolicyInventoryError:raise
    except (ValueError,TypeError,KeyError,AttributeError):raise _store().PolicyInventoryError(ERROR) from None


def before_publish(db,p,observation,state):
    owned=resolve(db,p,observation=observation)
    if owned is None:return False
    row,g=owned;obs,job,call=_authority(db,p,row,g)
    _need(row.status=='reserved' and call is not None and call.id==state['invocation_id']
        and call.status=='succeeded' and state['status']=='succeeded' and _same(call.data.get('output'),state['result']))
    from .perception_signals import guard_rule
    guard_rule(db,p,obs,result=state['result'])
    return True


def completed(db,p,job):
    owned=resolve(db,p,job=job)
    if owned is None:return False
    row,g=owned
    if row.status=='completed':return True
    obs,job,call=_authority(db,p,row,g)
    _need(row.status=='reserved' and call is not None and call.status=='succeeded' and obs.status=='processed')
    projected=project(g.data['scope'],obs.data['attributes'])
    row.status='completed';row.data={**row.data,'invocation':_call_pin(call),'result':{
        'actual_matched':projected[row.choice+'_matched'],'baseline_projection_matched':projected['baseline_matched'],
        'extracted_hash':_hash(call.data['output']),'completed_at':now().isoformat()}}
    _seal(db,p,row,'complete');queue_monitor(db,p,g,{'kind':'selection','selection_id':row.id,'version':row.version,'record_hash':row.record_hash})
    return True


def recover(db,p,job):
    """Policy reservations never take the ordinary attempt/retry path."""
    owned=resolve(db,p,job=job)
    if owned is None:return False
    row,g=owned;obs,job,call=_bindings(db,p,row,g)
    if row.status in {'completed','failed','stopped'}:return True
    if call is None:
        _need(row.status=='queued' or row.data['reservation']['lease_token']==ownership(db).get('lease_token'),
            '原预约无调用回执，停止重调并保留原输入')
        return True
    if call.status=='running':
        call.status='unknown';call.version+=1;call.data={**call.data,'error':'后台中断，原计算结果未保存；保留原调用，禁止自动重调'}
        audit(db,p,'threshold_policy.invocation_unknown_v153',call.id,{'selection_id':row.id,'job_id':job.id})
    if call.status=='succeeded' and row.data['reservation']['lease_token']!=ownership(db).get('lease_token'):
        audit(db,p,RECOVERY,row.id,{'schema':'threshold-policy-recovery-v153','job_id':job.id,
            'from_lease':row.data['reservation']['lease_token'],'to_lease':ownership(db)['lease_token'],'invocation':_call_pin(call)})
        job.data={**job.data,'resume_invocation_key':row.data['call_key']}
    return True


def fail(db,p,job,reason):
    owned=resolve(db,p,job=job)
    if owned is None:return False
    row,g=owned
    if row.status in {'completed','failed','stopped'}:return True
    obs,job,call=_bindings(db,p,row,g);current=False;actor_revoked=False
    from .worker import actor, ActorRevoked
    from .leases import ExecutionUnauthorized
    try:
        actual_actor=actor(db,p.workspace_id,row.actor_id)
    except ActorRevoked:
        actor_revoked=True
    else:
        try:
            _current_policy(db,actual_actor,row,g);current=True
        except _store().PolicyInventoryError:raise
        except (HTTPException,ExecutionUnauthorized):pass
    late=not current and call is not None and call.status=='succeeded'
    failed=current and call is not None and call.status!='running'
    row.status='failed' if failed else 'stopped'
    row.data={**row.data,'invocation':_call_pin(call) if call is not None else None,
        'late':late,'error':('原操作者执行授权已撤销；已停止此输入，原记录保留，不重新计算' if actor_revoked else '实际调用、结果保存失败或结果未知，已停止此输入' if failed else '当前策略或处理条件已停止；原输入不换规则、不重新计算')}
    _seal(db,p,row,'fail' if failed else 'stop')
    if obs.status!='processed':
        obs.status='failed';obs.version+=1;obs.data={**obs.data,'processing_error':row.data['error'],'failure_type':'threshold_policy_stopped'}
    queue_monitor(db,p,g,{'kind':'selection','selection_id':row.id,'version':row.version,'record_hash':row.record_hash})
    return True


def selection_view(db,p,row,checked=False,*,_readiness=None):
    from .threshold_policy_views_v153 import selection_view as display
    return display(db,p,row,checked=checked,_readiness=_readiness)


def ensure_monitor(db,p,g):
    from .threshold_policy_monitor_v153 import ensure_monitor as ensure
    return ensure(db,p,g)


def queue_monitor(db,p,g,trigger):
    from .threshold_policy_monitor_v153 import queue_monitor as queue
    return queue(db,p,g,trigger)


def monitor_view(db,p,g,*,_readiness=None):
    from .threshold_policy_monitor_v153 import monitor_view as display
    return display(db,p,g,_readiness=_readiness)


def validate_monitor_basis(db,p,g,basis,*,_read_inventory=None):
    from .threshold_policy_monitor_v153 import validate_monitor_basis as validate
    if _read_inventory is None:return validate(db,p,g,basis)
    return validate(db,p,g,basis,_read_inventory=_read_inventory)
