"""Durable study inventory and current eligibility, separate from execution."""
import copy
import hashlib
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select

from .models import Audit, Capability, Entity, Job
from .services import audit, published, scoped
from .threshold_evolution_models_v147 import FeedbackRevision
from .threshold_evolution_protocol_v147 import command_fingerprint, wire_sha256
from .threshold_study_models_v149 import (ThresholdStudyCandidate as Candidate,
    ThresholdStudyEvaluation as Evaluation, ThresholdStudyCommand as StudyCommand)
from .threshold_study_protocol_v149 import (CandidateDraft, UpdateCandidate, EvaluationDraft,
    UpdateEvaluation, FreezeCommand, RunCommand, CancelCommand, canonical_uuid, read_command)
from .typed_payload_codec_v131 import canonical_json

ROOT = '/api/local-rules/evolution'
ERROR = '原候选、评估或研究请求库存无法核对；原稿和回执保留'
STATE_ACTION = 'threshold_study.state_v149'
COMMAND_ACTION = 'threshold_study.command_v149'
STATE_KEYS = {'schema','target_kind','target_id','version','status','record_hash','previous_hash',
              'event','actor_id','workspace_id','command_id','job_id'}
MODELS = {'candidate': Candidate, 'evaluation': Evaluation}
WORKER_EVENTS = {'worker_started','call_reserved','call_accepted','completed','failed'}


class StudyInventoryError(HTTPException):
    """Damaged durable evidence is never a permanent human rejection."""
    def __init__(self, detail=ERROR): super().__init__(409, detail)


def inventory_read(function):
    def checked(*args, **kwargs):
        try: return function(*args, **kwargs)
        except StudyInventoryError: raise
        except HTTPException as exc:
            if exc.status_code == 404: raise
            raise StudyInventoryError(exc.detail) from None
    return checked


def need(condition, message=ERROR, status=409):
    if not condition: raise HTTPException(status, message)


def hash_value(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def kind_of(row): return 'candidate' if isinstance(row, Candidate) else 'evaluation'


def snapshot(row):
    fields = ('id','workspace_id','actor_id','status','version','data') + (
        ('baseline_capability_id','candidate_capability_id') if isinstance(row, Candidate) else ('candidate_id',))
    return {key: copy.deepcopy(getattr(row, key)) for key in fields}


def identify(method, path):
    need(type(path) is str and path.startswith(ROOT + '/') and path.isascii(), status=422)
    tail = path[len(ROOT)+1:].split('/')
    family = {'threshold-candidates':'candidate','threshold-evaluations':'evaluation'}
    need(tail[0] in family and len(tail) in (1,2,3), status=422)
    kind = family[tail[0]]; identity = None
    if len(tail) > 1:
        try: identity = canonical_uuid(tail[1])
        except (ValueError, TypeError): raise HTTPException(422, '请保留原研究记录编号') from None
    if len(tail) == 1 and method == 'POST':
        return kind, identity, kind+'.create', CandidateDraft if kind=='candidate' else EvaluationDraft
    if len(tail) == 2 and method == 'PUT':
        return kind, identity, kind+'.update', UpdateCandidate if kind=='candidate' else UpdateEvaluation
    if len(tail) == 3 and method == 'POST':
        action = tail[2]
        if action == 'freeze': return kind, identity, kind+'.freeze', FreezeCommand
        if kind == 'evaluation' and action in {'run','cancel'}:
            return kind, identity, kind+'.'+action, RunCommand if action=='run' else CancelCommand
    raise HTTPException(422, '研究请求的方法和路径不符合协议')


def parse_command(model, value):
    try:
        body = model.model_validate(value)
        need(hash_value(body.model_dump(by_alias=True)) == hash_value(value),
             '请求字段不能被自动转换、补齐或更换', status=422)
        return body
    except ValidationError:
        raise HTTPException(422, '请填写完整的方案、原版本和明确确认，原输入不会被替换') from None


def change_state(db, p, row, event, *, command_id=None, job_id=None, initial=False):
    previous = None if initial else row.record_hash
    if not initial: row.version += 1
    else: need(row.version == 1)
    row.record_hash = hash_value(snapshot(row))
    db.add(row)
    audit(db,p,STATE_ACTION,row.id,{'schema':'threshold-study-state-v149',
        'target_kind':kind_of(row),'target_id':row.id,'version':row.version,'status':row.status,
        'record_hash':row.record_hash,'previous_hash':previous,'event':event,
        'actor_id':p.user_id,'workspace_id':p.workspace_id,'command_id':command_id,'job_id':job_id})
    db.flush()


def command_payload(row):
    return {'schema':'threshold-study-command-pin-v149', **{key:getattr(row,key) for key in (
        'id','workspace_id','actor_id','request_id','method','path','wire_sha256','fingerprint',
        'operation','target_kind','target_id','outcome')}, 'data':row.data}


def command_pin(row):
    return {'schema':'threshold-study-command-audit-v149','command_id':row.id,
        **{key:getattr(row,key) for key in ('command_hash','request_id','actor_id','workspace_id',
            'method','path','wire_sha256','fingerprint','operation','target_kind','target_id','outcome')},
        'result_hash':hash_value(row.data['result_snapshot'])}


def _check_command(db,p,row,issued,*,targets=None,states_by_version=None):
    try:
        need(row.workspace_id==p.workspace_id and row.version==1 and row.outcome in {'saved','rejected'})
        for key in ('id','workspace_id','actor_id','request_id'):canonical_uuid(getattr(row,key))
        value=row.data
        need(type(value) is dict and set(value)=={'schema','wire_text','result_snapshot'}
             and value['schema']=='threshold-study-command-record-v149' and type(value['wire_text']) is str)
        raw=value['wire_text'].encode('utf-8'); original=read_command(raw)
        kind,identity,operation,model=identify(row.method,row.path)
        need(original['request_id']==row.request_id and kind==row.target_kind and operation==row.operation
             and (identity is None or identity==row.target_id) and wire_sha256(raw)==row.wire_sha256
             and command_fingerprint(row.method,row.path,row.workspace_id,row.actor_id,row.wire_sha256)==row.fingerprint
             and row.command_hash==hash_value(command_payload(row)))
        need(issued is not None and issued.workspace_id==p.workspace_id and issued.actor_id==row.actor_id
             and issued.action==COMMAND_ACTION and issued.object_id==row.id
             and canonical_json(issued.data)==canonical_json(command_pin(row)))
        result=value['result_snapshot']
        if row.outcome=='rejected':
            need(type(result) is dict and set(result)=={'schema','status','reason'}
                 and result['schema']=='threshold-study-rejected-v149' and type(result['status']) is int
                 and result['status'] in {409,422} and type(result['reason']) is str and bool(result['reason']))
        else:
            parse_command(model,original);canonical_uuid(row.target_id)
            fields={'id','workspace_id','actor_id','status','version','data'}|(
                {'baseline_capability_id','candidate_capability_id'} if kind=='candidate' else {'candidate_id'})
            need(type(result) is dict and set(result)==fields and result['id']==row.target_id
                 and result['workspace_id']==row.workspace_id and type(result['version']) is int
                 and result['version']>=1)
            need((targets is None)==(states_by_version is None))
            if targets is None:
                need(db.scalar(select(MODELS[kind].id).where(MODELS[kind].workspace_id==p.workspace_id,
                     MODELS[kind].id==row.target_id)) is not None)
                states=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
                    Audit.action==STATE_ACTION,Audit.object_id==row.target_id,
                    Audit.data['version'].as_integer()==result['version'])))
            else:
                need(row.target_id in targets[kind])
                states=states_by_version.get((row.target_id,result['version']),[])
            need(len(states)==1 and states[0].data.get('command_id')==row.id
                 and states[0].data.get('record_hash')==hash_value(result))
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):
        raise HTTPException(409,ERROR) from None
    except HTTPException:
        raise HTTPException(409,ERROR) from None
    return row


@inventory_read
def command_inventory(db,p):
    pairs=list(db.execute(select(StudyCommand,Audit).join(Audit,and_(
        StudyCommand.workspace_id==Audit.workspace_id,Audit.action==COMMAND_ACTION,
        Audit.object_id==StudyCommand.id),full=True).where(or_(
            StudyCommand.workspace_id==p.workspace_id,and_(Audit.workspace_id==p.workspace_id,
                                                          Audit.action==COMMAND_ACTION)))))
    # These are actual complete rows in this call, never cached verification.
    # Keep every state row: duplicate versions must not overwrite one another.
    targets={kind:set(db.scalars(select(model.id).where(model.workspace_id==p.workspace_id)))
             for kind,model in MODELS.items()}
    states=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==STATE_ACTION)))
    states_by_version={}
    for issued in states:
        pin=issued.data
        need(type(pin) is dict and set(pin)==STATE_KEYS and pin.get('schema')=='threshold-study-state-v149')
        kind=pin['target_kind']
        need(type(kind) is str and kind in MODELS and issued.object_id in targets[kind]
             and pin['target_id']==issued.object_id and pin['workspace_id']==p.workspace_id
             and pin['actor_id']==issued.actor_id and type(pin['version']) is int and pin['version']>=1)
        states_by_version.setdefault((issued.object_id,pin['version']),[]).append(issued)
    rows=[];seen=set();nonces=set()
    for row,issued in pairs:
        need(row is not None and issued is not None and row.id not in seen)
        _check_command(db,p,row,issued,targets=targets,states_by_version=states_by_version);key=(row.actor_id,row.request_id)
        need(key not in nonces);seen.add(row.id);nonces.add(key);rows.append(row)
    # Command+CommandAudit may both be missing. The independently retained
    # StateAudit must still forbid "unknown" and a second create with that nonce.
    saved={row.id:row for row in rows if row.outcome=='saved'}
    state_commands=set()
    audited={kind:set() for kind in MODELS}
    for issued in states:
        pin=issued.data
        need(type(pin) is dict and set(pin)==STATE_KEYS and pin.get('schema')=='threshold-study-state-v149')
        kind=pin.get('target_kind');identity=issued.object_id
        need(kind in MODELS and identity in targets[kind] and pin.get('target_id')==identity
             and pin.get('workspace_id')==p.workspace_id and pin.get('actor_id')==issued.actor_id)
        audited[kind].add(identity)
        if pin['command_id'] is not None:
            command=saved.get(pin['command_id'])
            need(command is not None and command.id not in state_commands
                 and command.target_kind==kind and command.target_id==identity
                 and command.operation==pin['event'] and command.actor_id==issued.actor_id
                 and command.data['result_snapshot']['version']==pin['version']
                 and hash_value(command.data['result_snapshot'])==pin['record_hash'])
            state_commands.add(command.id)
    need(state_commands==set(saved) and targets==audited)
    return rows


@inventory_read
def get_record(db,p,kind,identity,lock=False):
    need(kind in MODELS)
    query=select(MODELS[kind]).where(MODELS[kind].workspace_id==p.workspace_id,MODELS[kind].id==identity)
    if lock:query=query.with_for_update()
    row=db.scalar(query.execution_options(populate_existing=True))
    states=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action==STATE_ACTION,Audit.object_id==identity)))
    if row is None:
        need(not states)
        raise HTTPException(404,'候选或评估不存在，或不在当前工作区')
    need(row.version>=1 and row.record_hash==hash_value(snapshot(row)) and len(states)==row.version)
    try:
        ordered=sorted(states,key=lambda a:a.data['version']);previous=None
        commands={c.id:c for c in command_inventory(db,p) if c.target_kind==kind and c.target_id==identity}
        used=set()
        for index,issued in enumerate(ordered,1):
            pin=issued.data
            need(type(pin) is dict and set(pin)==STATE_KEYS and pin['schema']=='threshold-study-state-v149'
                 and pin['target_kind']==kind and pin['target_id']==identity and pin['workspace_id']==p.workspace_id
                 and pin['version']==index and type(pin['version']) is int
                 and pin['actor_id']==issued.actor_id and pin['previous_hash']==previous
                 and type(pin['record_hash']) is str and len(pin['record_hash'])==64)
            if pin['command_id'] is not None:
                command=commands.get(pin['command_id']);need(command is not None and command.id not in used
                    and command.outcome=='saved' and command.operation==pin['event']
                    and command.actor_id==pin['actor_id'] and pin['job_id'] is None
                    and hash_value(command.data['result_snapshot'])==pin['record_hash'])
                used.add(command.id)
            else:need(kind=='evaluation' and pin['event'] in WORKER_EVENTS
                      and pin['job_id']==row.data.get('job_id')
                      and pin['actor_id']==row.data.get('job_actor_id'))
            previous=pin['record_hash']
        need(previous==row.record_hash and ordered[-1].data['status']==row.status
             and used=={c.id for c in commands.values() if c.outcome=='saved'})
        value=row.data
        expected={'schema','draft','freeze','freeze_hash'} if kind=='candidate' else {
            'schema','draft','freeze','freeze_hash','job_id','job_actor_id','reservations','receipts','result','error'}
        need(type(value) is dict and set(value)==expected and value['schema']=='threshold-study-'+kind+'-record-v149')
        frozen=value['freeze']
        if frozen is None:need(value['freeze_hash'] is None and row.status=='draft')
        else:
            need(value['freeze_hash']==hash_value(frozen))
            freezes=[c for c in commands.values() if c.operation==kind+'.freeze' and c.outcome=='saved']
            need(len(freezes)==1 and hash_value(freezes[0].data['result_snapshot']['data']['freeze'])==hash_value(frozen)
                 and freezes[0].data['result_snapshot']['data']['freeze_hash']==value['freeze_hash'])
        if kind=='evaluation':
            need(type(value['reservations']) is dict and type(value['receipts']) is dict)
            if value['job_id'] is None:
                need(not value['reservations'] and not value['receipts'] and value['result'] is None
                     and value['job_actor_id'] is None)
            else:
                job=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
                    Job.id==value['job_id']).execution_options(populate_existing=True))
                need(job is not None)
                from .threshold_study_execution_v149 import checked_execution_inventory
                checked_execution_inventory(db,p,row,job,require_complete=row.status=='completed')
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):
        raise HTTPException(409,ERROR) from None
    return row


def _lock_refs(db,p,refs):
    records=[scoped(db,FeedbackRevision,r['feedback_id'],p) for r in refs]
    # Existing feedback mutation locks its root then uses a read-only basis.
    # Holding Observation SHARE first adds no reverse Observation write lock.
    from .models import Observation
    for identity in sorted({r.observation_id for r in records}):
        db.scalar(select(Observation).where(Observation.workspace_id==p.workspace_id,
            Observation.id==identity).with_for_update(read=True).execution_options(populate_existing=True))
    for identity in sorted({r.root_id for r in records}):
        db.scalar(select(FeedbackRevision).where(FeedbackRevision.workspace_id==p.workspace_id,
            FeedbackRevision.id==identity).with_for_update(read=True).execution_options(populate_existing=True))
    from .threshold_feedback_history_v148 import inventory_chain
    for identity in sorted({r.root_id for r in records}):
        try: inventory_chain(db,p,identity)
        except HTTPException as exc: raise StudyInventoryError(exc.detail) from None


def current_samples(db,p,refs,baseline,partition):
    from .threshold_study_basis_v149 import feedback_samples,ERROR as BASIS_ERROR
    from .threshold_evolution_basis_v147 import ERROR as MACHINE_ERROR
    from .threshold_evolution_api_v147 import ERROR as FEEDBACK_ERROR,LEGACY_ERROR
    try: return feedback_samples(db,p,refs,baseline,partition)
    except HTTPException as exc:
        if type(exc.detail) is str and exc.detail in {BASIS_ERROR,MACHINE_ERROR,FEEDBACK_ERROR,LEGACY_ERROR}:
            raise StudyInventoryError(exc.detail) from None
        raise


def current_manifests(db,p,baseline_id,candidate_id):
    from .local_rules_api import _saved_manifest,require_current_check
    caps={}
    for identity in sorted({baseline_id,candidate_id}):
        cap=db.scalar(select(Capability).where(Capability.workspace_id==p.workspace_id,
            Capability.id==identity).execution_options(populate_existing=True))
        if cap is None: raise HTTPException(404,'规则不存在，或不在当前工作区')
        published(db,p,identity,'tool')
        try: _saved_manifest(db,p,cap);require_current_check(db,p,cap)
        except HTTPException as exc: raise StudyInventoryError(exc.detail) from None
        caps[identity]=cap
    from .threshold_study_basis_v149 import check_candidate_manifests
    try:check_candidate_manifests(caps[baseline_id].data,caps[candidate_id].data)
    except ValueError:raise HTTPException(422,'候选必须是同一对象同一字段与单位的另一已发布单阈值规则') from None
    return copy.deepcopy(caps[baseline_id].data),copy.deepcopy(caps[candidate_id].data)


def candidate_freeze_data(db,p,row,require_error=True):
    baseline,candidate=current_manifests(db,p,row.baseline_capability_id,row.candidate_capability_id)
    refs=row.data['draft']['feedback_refs'];_lock_refs(db,p,refs)
    training=current_samples(db,p,refs,baseline,'training')
    if require_error:
        need(any((x['feedback_snapshot']['data']['label']=='should_alert' and not x['basis']['actual_hit']) or
                 (x['feedback_snapshot']['data']['label']=='should_not_alert' and x['basis']['actual_hit'])
                 for x in training),'至少选择一条最新已提交的误报或漏报判断后再冻结')
    return {'schema':'threshold-candidate-freeze-v149','baseline_manifest':baseline,
            'candidate_manifest':candidate,'training':training}


def frozen_candidate_body(db,p,row):
    need(row.status=='frozen' and row.data['freeze'] is not None,'请先冻结候选方案')
    current=candidate_freeze_data(db,p,row)
    need(hash_value(current)==row.data['freeze_hash'],'原训练判断、规则或依据已变化，请另建候选保留原方案')
    return {'candidate_id':row.id,'candidate_version':row.version,'baseline_id':row.baseline_capability_id,
        'candidate_capability_id':row.candidate_capability_id,**{k:copy.deepcopy(current[k]) for k in (
            'baseline_manifest','candidate_manifest','training')},'candidate_freeze_hash':row.data['freeze_hash']}


def evaluation_freeze_data(db,p,row):
    from .threshold_study_basis_v149 import independence_issues
    candidate=get_record(db,p,'candidate',row.candidate_id)
    body=frozen_candidate_body(db,p,candidate)
    need(row.data['draft']['expected_candidate_version']==candidate.version,'原候选版本已变化')
    refs=row.data['draft']['feedback_refs'];_lock_refs(db,p,refs)
    samples=current_samples(db,p,refs,body['baseline_manifest'],'evaluation')
    issues=independence_issues(body['training'],samples)
    need(not issues,'；'.join(issues))
    need(row.data['draft']['criteria']['max_additional_reviews']<=len(samples),
         f'本次评估选择了{len(samples)}条样本，允许额外核查数不能超过{len(samples)}；请保留草稿并明确修改后另存',status=422)
    return {'schema':'threshold-evaluation-freeze-v149','candidate':body,'samples':samples,
        'criteria':copy.deepcopy(row.data['draft']['criteria']),'purpose':row.data['draft']['purpose'],
        'limitations':row.data['draft']['limitations']}


def fresh_evaluation(db,p,row):
    need(row.data['freeze'] is not None and row.data['freeze_hash']==hash_value(row.data['freeze']))
    current=evaluation_freeze_data(db,p,row)
    need(hash_value(current)==row.data['freeze_hash'],'冻结原标签、规则或处理依据已变化；原比较保留，不能继续新预约')


def lock_nonce(db,p,nonce):
    key=int(hash_value({'scope':'threshold-study-command-v149','workspace_id':p.workspace_id,
                        'actor_id':p.user_id,'request_id':nonce})[:15],16)
    db.execute(select(func.pg_advisory_xact_lock(key)))


# The execution module has no router/model registration and imports this core
# lazily. These wrappers give the worker one stable, explicit store seam.
def owned_evaluation(*args,**kwargs):
    from .threshold_study_execution_v149 import owned_evaluation as call
    return call(*args,**kwargs)
def reserve_call(*args,**kwargs):
    from .threshold_study_execution_v149 import reserve_call as call
    return call(*args,**kwargs)
def saved_call(*args,**kwargs):
    from .threshold_study_execution_v149 import saved_call as call
    return call(*args,**kwargs)
def accept_call(*args,**kwargs):
    from .threshold_study_execution_v149 import accept_call as call
    return call(*args,**kwargs)
def complete_evaluation(*args,**kwargs):
    from .threshold_study_execution_v149 import complete_evaluation as call
    return call(*args,**kwargs)
def fail_evaluation(*args,**kwargs):
    from .threshold_study_execution_v149 import fail_evaluation as call
    return call(*args,**kwargs)
