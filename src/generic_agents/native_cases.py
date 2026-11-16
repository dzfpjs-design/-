"""Explicit native procedure reuse. Historical methods never become current facts."""
import copy
from typing import Literal
from uuid import uuid4
from datetime import timezone
from pydantic import Field, StrictInt, ValidationError, field_validator
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import Contract, digest
from .models import Task, Memory, Entity, Capability, Invocation, Observation, Membership, User, DomainInstance, now
from .services import scoped, require, check_version, audit, timestamp

TYPE = 'native_procedure_case'
TEMPLATES = {'source_identity':'核对资料来源与对象', 'source_time':'核对资料时间',
    'units':'核对数值单位', 'comparability':'核对资料是否可比较', 'field_verification':'对照原资料核对字段'}
LIMIT = 100


class Command(Contract):
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{12,100}$')
    expected_version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator('reason')
    @classmethod
    def nonblank_reason(cls,value):
        if not value.strip(): raise ValueError('请填写可核对的操作理由')
        return value


class Step(Contract):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,50}$')
    kind: Literal['source_identity','source_time','units','comparability','field_verification']
    field: str = Field(min_length=1, max_length=100)
    instruction: str = Field(default='', max_length=2000)


class Draft(Command):
    acceptance_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default='', max_length=2000)
    limits: str = Field(default='', max_length=2000)
    steps: list[Step] = Field(min_length=1, max_length=5)
    case_id: str | None = None
    expected_case_version: StrictInt | None = Field(default=None, ge=1)
    content_hash: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


class CaseCommand(Command):
    case_id: str
    expected_case_version: StrictInt = Field(ge=1)
    content_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class Review(CaseCommand):
    decision: Literal['trust','reject']
    valid_until: str | None = None


class Restore(CaseCommand):
    revision_version: StrictInt = Field(ge=1)
    revision_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class Adopt(CaseCommand):
    basis_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class UseCommand(Command):
    use_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class Confirm(UseCommand):
    basis_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    item_id: str
    conclusion: Literal['confirmed','unknown']


class Feedback(UseCommand):
    helpful: Literal['helpful','not_helpful','uncertain']
    note: str = Field(default='', max_length=2000)


class Recovery(Contract):
    operation: Literal['draft','restore','submit','review','revoke','adopt','confirm','disengage','feedback','revise','evaluate']
    original_request: dict
    expected_version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    action: Literal['probe','abandon'] = 'probe'


MODELS = {'draft':Draft,'restore':Restore,'submit':CaseCommand,'review':Review,'revoke':CaseCommand,
    'adopt':Adopt,'confirm':Confirm,'disengage':UseCommand,'feedback':Feedback}


def _fail(message):
    raise HTTPException(409, message)


def _permission(db, p, user_id, lock=False):
    stmt = select(User, Membership).join(Membership, Membership.user_id == User.id).where(
        User.id == user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role.in_(['admin','operator']))
    if lock: stmt = stmt.with_for_update(read=True)
    if not db.execute(stmt.execution_options(populate_existing=True)).first():
        raise HTTPException(403, '原确认或审核人的当前工作区权限已失效')


def _manage(db, p, task):
    from .native_goals import manage
    manage(p, task)
    _permission(db, p, p.user_id, True)


def _state(task):
    value = copy.deepcopy(task.data.get('native_experience', {'schema':'native-experience-state-v1',
        'case_id':None,'uses':[],'confirmations':[],'disengagements':[],'feedback':[],'commands':[]}))
    if task.data.get('native_experience') is not None and digest(value) != task.data.get('native_experience_hash'):
        _fail('原经验草稿、采用或核查历史摘要不一致，记录保留待核')
    for key in ('uses','confirmations','disengagements','feedback','commands'):
        if not isinstance(value.get(key), list) or len(value[key]) > LIMIT:
            _fail('经验记录格式或保存范围无法核对')
    return value


def _save(task, state):
    if any(len(state[key]) > LIMIT for key in ('uses','confirmations','disengagements','feedback','commands')):
        _fail('经验操作已达100条上限，原历史保留')
    task.data = {**task.data, 'native_experience':state, 'native_experience_hash':digest(state)}


def _journal(db, p, task, body, operation):
    state = _state(task)
    old = next((entry for entry in state['commands'] if entry['request_id'] == body.request_id), None)
    if old:
        if old['body_hash'] != digest(body.model_dump()) or old['actor_id'] != p.user_id or old['operation'] != operation:
            _fail('原经验请求不能更换内容、动作或提交者')
        if old['status'] == 'abandoned': _fail('原经验请求已明确作废，请保留草稿重新核对')
        return state, False
    check_version(task, body.expected_version)
    if len(state['commands']) >= LIMIT: _fail('经验操作已达100条上限，原历史保留')
    return state, True


def _finish(db, p, task, state, body, operation):
    state['commands'].append({'request_id':body.request_id,'body_hash':digest(body.model_dump()),
        'actor_id':p.user_id,'operation':operation,'status':'saved','reason':body.reason,'at':now().isoformat()})
    state['saved_at'] = now().isoformat()
    _save(task, state)
    task.version += 1
    audit(db, p, 'native_experience.'+operation, task.id, {'request_id':body.request_id,'reason':body.reason})


def _scope(db, p, task):
    from .native_goals import saved_context
    from .workspace_api import saved_execution
    context = saved_context(db, p, task)
    _, release, _ = saved_execution(db, p, task.id)
    package = scoped(db, Capability, task.package_id, p)
    types = sorted({scoped(db, Entity, identity, p).entity_type for identity in context['entity_ids']})
    fields = sorted(task.data['goal_spec']['required_fields'])
    schemas = package.data['specification']['entity_schemas']
    return {'instance_id':context['instance_id'],'business_id':context['business_id'],'release_id':release.id,
        'native_package_id':task.package_id,'object_types':types,'fields':fields,
        'field_schema_hash':digest({kind:{field:schemas[kind]['properties'].get(field) for field in fields} for kind in types}),
        'source':task.data['goal_spec']['evidence']['source']}


def _fields(db, p, task):
    from .workspace_api import saved_execution
    _, release, _ = saved_execution(db, p, task.id)
    package = scoped(db, Capability, task.package_id, p)
    schemas = package.data['specification']['entity_schemas']
    types = sorted({scoped(db, Entity, identity, p).entity_type for identity in task.data['entity_ids']})
    result = []
    for field in task.data.get('goal_spec', {}).get('required_fields', []):
        registered = [schemas[kind]['properties'].get(field, {}) for kind in types]
        registered_types = {item.get('type','unknown') for item in registered}
        kind = next(iter(registered_types)) if len(registered_types)==1 else 'unknown'
        label = next((f['label'] for item in release.data['snapshot']['object_types'] for f in item['fields'] if f['key']==field), field)
        result.append({'key':field,'label':label,'type':kind,
            'templates':[key for key in TEMPLATES if key!='units' or all(item.get('type') in {'integer','number'} for item in registered)]})
    return result


def _origin(db, p, task, lock=False,*,_historical=False):
    try: return _origin_proof(db,p,task,lock,_historical=_historical)
    except (KeyError,TypeError,ValueError,OSError) as error:
        raise HTTPException(409,'原正式验收证明结构或原件无法核对') from error


def _origin_proof(db, p, task, lock=False,*,_historical=False):
    """Check an accepted historical outcome without comparing archive values to today's values."""
    from .native_goals import saved_context, basis, verify_acceptance_inputs, verify_observation_history
    saved_context(db, p, task)
    accepted = task.data.get('native_goal_acceptance')
    if not accepted or digest(accepted) != task.data.get('native_goal_acceptance_hash'):
        _fail('原任务尚未正式验收，或原验收摘要无法核对')
    from .accepted_tool_basis_v129 import accepted_tool_basis
    tool_basis = accepted_tool_basis(task.data['goal_spec'], accepted['evaluation'])
    outcome = scoped(db, Memory, task.data.get('native_goal_data_receipt_id',''), p)
    if (outcome.data.get('type') != 'native_goal_outcome' or outcome.data.get('task_id') != task.id
        or outcome.data.get('acceptance') != accepted or outcome.data.get('acceptance_hash') != digest(accepted)
        or outcome.data.get('goal_snapshot') != task.data['native_goal_context']['goal_snapshot']
        or outcome.data.get('package_id') != task.package_id or outcome.data.get('instance_release_id') != task.data['instance_release_id']
        or outcome.data.get('workflow_receipts') != task.data['workflow_runtime']['receipts']
        or accepted.get('goal_snapshot') != task.data['native_goal_context']['goal_snapshot']
        or accepted['evaluation'].get('basis_hash') != accepted.get('basis_hash')
        or not accepted.get('evaluation',{}).get('completed') or not accepted['evaluation'].get('flow_finished')
        or not accepted['evaluation'].get('data_received') or task.status != 'workflow_completed'
        or accepted.get('basis_hash') != basis(task, accepted['evidence'], tool_basis)):
        _fail('原任务、正式验收与归集回执的双向证明不一致')
    verify_acceptance_inputs(db, p, task)
    from .accepted_tool_history_v129 import validate as verify_original_tools
    verify_original_tools(db, p, task, accepted, tool_basis)
    observations = []
    from .observation_integrity import checked_report, CHECK_ERRORS
    for item in accepted['evidence'].get('observation_evidence', []):
        row = scoped(db, Observation, item['observation_id'], p)
        try:
            report = checked_report(db, p, row)
            if row.entity_id != item['entity_id'] or any(report['attributes'].get(key) != value for key,value in item['attributes'].items()):
                _fail('原验收观测字段或对象证明已改变')
            verify_observation_history(row)
            if report['review'] == 'rejected' or item.get('verification') == 'confirmed' and report['review'] != 'confirmed':
                _fail('原验收登记证明已被纠正或撤回')
            observations.append({'id':row.id,'input_hash':row.data.get('input_hash'),
                'extracted_hash':digest(row.data['extracted']),'invocation_id':row.data['invocation_id'],'file_hashes':report['file_hashes']})
        except CHECK_ERRORS as error:
            raise HTTPException(409, '原验收观测或原件证明无法核对') from error
    if accepted.get('evaluation',{}).get('experience',{}).get('required') and not accepted.get('experience_proof'):
        _fail('原验收缺少程序经验消费证明')
    if accepted.get('experience_proof'):
        proof=accepted['experience_proof']
        if digest(proof)!=accepted.get('experience_proof_hash') or accepted['evaluation'].get('experience')!=proof:
            _fail('原验收程序经验消费摘要不一致')
        state=_state(task)
        use=next((item for item in state['uses'] if item['id']==proof['id']),None)
        if not use or use['snapshot']!=proof.get('case_snapshot') or use['use_hash']!=proof['use_hash'] or digest(use['snapshot'])!=proof['use_hash']:
            _fail('原验收与采用历史的双向证明不一致')
        for item in proof['items']:
            confirmation={key:value for key,value in item['confirmation'].items() if key!='stale'}
            if confirmation not in state['confirmations'] or confirmation['refs']!=item['current_refs'] or confirmation['refs_hash']!=digest(item['current_refs']):
                _fail('原验收与当次清单确认的双向证明不一致')
    if not _historical:_permission(db, p, accepted['actor_id'],lock)
    return {'task_id':task.id,'outcome_id':outcome.id,'acceptance_hash':digest(accepted),
        'context_hash':task.data['native_goal_context_hash'],'goal_snapshot':copy.deepcopy(accepted['goal_snapshot']),
        'input_snapshots_hash':digest({key:task.data.get(key,[]) for key in ('material_snapshots','image_snapshots','scene_snapshots','comparison_scene_snapshots','audio_snapshots')}),
        'workflow_hash':digest(outcome.data['workflow_receipts']),'observations':observations,
        'accepted_at':accepted['at'],'source_task_label':task.title}


def _seal_case(case):
    data = copy.deepcopy(case.data)
    if 'evolution' in data:
        from .native_method_evolution import proposal_digest
        proposal_hash = proposal_digest(data)
        if data.get('proposal_hash') != proposal_hash:
            data.pop('latest_evaluation',None)
        data['proposal_hash'] = proposal_hash
    data['status'] = case.status
    data['case_id'] = case.id
    data['version'] = case.version
    data['valid_until'] = case.valid_until.astimezone(timezone.utc).isoformat() if case.valid_until else None
    case.data = data
    case.data = {**data,'record_hash':digest({k:v for k,v in data.items() if k!='record_hash'})}


def _checked_case(db, p, identity, lock=False, check_origin=True,*,_local_origin=True):
    row = scoped(db, Memory, identity, p, lock)
    data = row.data
    if (data.get('type') != TYPE or 'package_id' in data or not row.task_id
        or data.get('case_id')!=row.id or data.get('status') != row.status or data.get('version') != row.version
        or data.get('record_hash') != digest({k:v for k,v in data.items() if k!='record_hash'})
        or data.get('content_hash') != digest(data.get('content'))
        or data.get('valid_until') != (row.valid_until.astimezone(timezone.utc).isoformat() if row.valid_until else None)):
        _fail('原程序经验、版本或审核证明摘要不一致')
    _draft_revisions(row)
    source = scoped(db, Task, row.task_id, p)
    state = _state(source)
    from .native_method_evolution import registered_ids, checked_revision
    revisions = registered_ids(state,'revision_ids')
    if state['case_id']!=row.id and row.id not in revisions: _fail('程序经验与原任务保存草稿的双向关联不一致')
    if ('evolution' in data) != (row.id in revisions): _fail('修订标记与原任务登记不一致')
    if 'evolution' in data: checked_revision(db,p,row,check_current=check_origin)
    if data.get('source_task_id') != source.id or data.get('scope',{}).get('native_package_id') != data.get('native_package_id'):
        _fail('程序经验来源或业务包关联不一致')
    method_origin=data.get('suggestion_origin') or {}
    if not isinstance(method_origin,dict):_fail('程序经验建议来源结构无法核对')
    if _local_origin and ('source_kind' in method_origin or 'local_result_hash' in method_origin):
        from .native_method_suggestions import checked_local_case_origin
        try:checked_local_case_origin(db,p,row,source)
        except HTTPException:
            if row.status!='revoked':raise
    if check_origin and (_origin(db,p,source,lock) != data['source_proof'] or _scope(db,p,source) != data['scope']):
        _fail('原程序经验的正式验收证明或范围已改变')
    return row


def _draft_revisions(row):
    """旧稿只存一次正文和本地显示标签，不嵌套复制前一版归档或原件实体值。"""
    entries=row.data.get('draft_revisions',[])
    if (not isinstance(entries,list) or len(entries)>LIMIT or
        ('draft_revisions' in row.data and digest(entries)!=row.data.get('draft_revisions_hash'))):
        _fail('旧草稿版本摘要无法核对，请保留记录待核')
    previous=None;version=0
    for item in entries:
        if (not isinstance(item,dict) or item.get('schema')!='native-draft-revision-v1'
            or item.get('case_id')!=row.id or item.get('source_task_id')!=row.task_id
            or item.get('native_package_id')!=row.data.get('native_package_id') or item.get('status')!='draft'
            or type(item.get('revision')) is not int or not version<item['revision']<row.version
            or item.get('scope_hash')!=digest(row.data.get('scope'))
            or item.get('source_proof_hash')!=digest(row.data.get('source_proof'))
            or item.get('content_hash')!=digest(item.get('content'))
            or item.get('previous_revision_hash')!=previous
            or item.get('revision_hash')!=digest({key:value for key,value in item.items() if key!='revision_hash'})):
            _fail('旧草稿正文、来源或版本链无法核对，请保留记录待核')
        try:
            content=item['content'];steps=[Step.model_validate(value) for value in content['steps']]
            if (not 1<=len(steps)<=5 or len({step.id for step in steps})!=len(steps)
                or not isinstance(content['title'],str) or not content['title'] or len(content['title'])>200
                or not isinstance(content['summary'],str) or not isinstance(content['limits'],str)
                or len(content['summary'])>2000 or len(content['limits'])>2000
                or len(item['display_steps'])!=len(steps)
                or any({key:display[key] for key in ['id','kind','field','instruction']}!=step.model_dump()
                    or not isinstance(display['label'],str) or not isinstance(display['field_label'],str)
                    for display,step in zip(item['display_steps'],steps))):
                _fail('旧草稿核查步骤或显示依据无法核对，请保留记录待核')
        except (KeyError,TypeError,ValidationError) as error:
            raise HTTPException(409,'旧草稿正文结构无法核对，请保留记录待核') from error
        previous=item['revision_hash'];version=item['revision']
    return entries


def _archive_draft(db,p,task,row,body):
    entries=_draft_revisions(row)
    if row.status!='draft': _fail('只有当前草稿可以封存并替换；已提交记录不能覆盖')
    if len(entries)>=LIMIT: _fail('已保存100个旧草稿版本，不能继续覆盖；当前稿与全部旧稿均已保留')
    labels={item['key']:item['label'] for item in _fields(db,p,task)}
    content=copy.deepcopy(row.data['content'])
    item={'schema':'native-draft-revision-v1','case_id':row.id,'source_task_id':task.id,
        'native_package_id':row.data['native_package_id'],'revision':row.version,'status':'draft',
        'scope_hash':digest(row.data['scope']),'source_proof_hash':digest(row.data['source_proof']),
        'source_task_label':row.data['source_proof']['source_task_label'],'source_accepted_at':row.data['source_proof']['accepted_at'],
        'content':content,'content_hash':digest(content),'display_steps':[{**step,'label':TEMPLATES[step['kind']],
            'field_label':labels.get(step['field'],step['field'])} for step in content['steps']],
        'saved_at':row.data['saved_at'],'archived_at':now().isoformat(),'reason':body.reason,'request_id':body.request_id,
        'suggestion_origin':copy.deepcopy(row.data.get('suggestion_origin')),
        'prior_record_hash':row.data['record_hash'],'previous_revision_hash':entries[-1]['revision_hash'] if entries else None}
    item['revision_hash']=digest(item)
    entries=[*entries,item]
    row.data={**row.data,'draft_revisions':entries,'draft_revisions_hash':digest(entries)}


def _restore_permission(db,p,task,row):
    try:
        _manage(db,p,task)
        if row.status!='draft': _fail('当前方法已提交或审核，不能恢复覆盖；旧稿仍可查看')
        _checked_case(db,p,row.id)
        entries=_draft_revisions(row)
        if not entries: _fail('尚未封存旧草稿，既往未保存的全文不能补造')
        if len(entries)>=LIMIT: _fail('已保存100个旧草稿版本，不能继续覆盖；当前稿与全部旧稿均已保留')
    except HTTPException as error:
        return {'allowed':False,'reason':'当前账号为只读权限，可查看旧稿，不能恢复' if p.role=='viewer' else str(error.detail)}
    return {'allowed':True,'reason':''}


def draft_revisions(db,p,task,limit=20,after_version=None,revision=None):
    state=_state(task);identity=state['case_id']
    row=_checked_case(db,p,identity,check_origin=False) if identity else None
    if row and row.task_id!=task.id: _fail('旧草稿与当前任务的双向关联不一致，请保留记录待核')
    top={'version':task.version,'case_id':row.id if row else None,'case_version':row.version if row else None,
        'content_hash':row.data['content_hash'] if row else None}
    entries=_draft_revisions(row) if row else []
    permission=_restore_permission(db,p,task,row) if row else {'allowed':False,'reason':'当前任务没有方法草稿'}
    def readable(item,full=False):
        value={key:item[key] for key in ['revision','revision_hash','content_hash','saved_at','archived_at','reason']}
        value.update(title=item['content']['title'],can_restore=permission['allowed'],restore_reason=permission['reason'])
        if full:value.update(summary=item['content']['summary'],limits=item['content']['limits'],steps=item['display_steps'],
            source_task_label=item['source_task_label'],source_accepted_at=item['source_accepted_at'])
        return value
    if revision is not None:
        found=next((item for item in entries if item['revision']==revision),None)
        if found is None:raise HTTPException(404,'所选旧草稿版本不存在；既往未封存的旧稿不能补造')
        return {**top,'item':readable(found,True),'permissions':{'restore':permission}}
    if after_version is not None and not any(item['revision']==after_version for item in entries):
        _fail('旧稿分页位置不属于当前保存版本，请重新读取清单')
    selected=[item for item in reversed(entries) if after_version is None or item['revision']<after_version]
    return {**top,'items':[readable(item) for item in selected[:limit]],
        'next_cursor':selected[limit-1]['revision'] if len(selected)>limit else None}


def _case_request(db, p, task, body, check_origin=True,*,_local_origin=True):
    row = _checked_case(db,p,body.case_id,True,check_origin=check_origin,_local_origin=_local_origin)
    if row.task_id != task.id: _fail('请在原正式验收任务中管理此经验')
    check_version(row, body.expected_case_version)
    if row.data['content_hash'] != body.content_hash: _fail('经验内容版本已改变，请保留草稿重新核对')
    return row


def _trusted(db, p, task, row, lock=False):
    if row.status != 'trusted': _fail('程序经验尚未审核、已拒绝或撤回')
    if row.valid_until and row.valid_until <= now(): _fail('程序经验已到期，请核对后重新审核')
    _permission(db,p,row.data['review']['actor_id'],lock)
    if row.task_id == task.id: _fail('原任务不能采用自身结果作为后续任务经验')
    if _scope(db,p,task) != row.data['scope']: _fail('程序经验与当前实例、业务、版本、对象类型、字段或资料类型不匹配')
    _future_lineage(db,p,scoped(db,Task,row.task_id,p),lock=lock)


def _future_lineage(db,p,task,visited=None,depth=0,lock=False,work=None):
    """Future reuse propagates upstream revocation; historical reading never calls this guard."""
    if depth>8: _fail('上游程序经验超过8层核查范围，不能继续发布或采用')
    visited=set(visited or ())
    work=work if work is not None else db.info.setdefault('native_experience_future_work',{'rows':{},'done':set(),'edges':0})
    state=_state(task)
    proof=task.data.get('native_goal_acceptance',{}).get('experience_proof')
    if not proof:
        if task.data.get('native_goal_acceptance',{}).get('evaluation',{}).get('experience',{}).get('required'):
            _fail('正式消费缺少冻结的上游证明，不能继续复用')
        return
    use=next((item for item in state['uses'] if item['id']==proof.get('id')),None)
    if not use or use['snapshot']!=proof.get('case_snapshot') or use['use_hash']!=proof.get('use_hash'):
        _fail('正式消费与上游冻结采用证明不一致')
    # Only the method actually consumed at formal acceptance propagates lineage.
    for use in [use]:
        snapshot=use['snapshot'];identity=snapshot['case_id']
        edge=(task.id,use['id'],use['use_hash'])
        seen_edges=work.setdefault('edge_keys',set())
        if edge not in seen_edges:
            seen_edges.add(edge);work['edges']+=1
        if work['edges']>100: _fail('上游程序经验超过本次100项核查预算，不能继续复用')
        if identity in visited: _fail('上游程序经验血缘存在循环，不能继续复用')
        if digest(snapshot)!=use['use_hash'] or snapshot['task_id']!=task.id or snapshot['context_hash']!=task.data['native_goal_context_hash']:
            _fail('上游程序经验采用证明不属于原任务')
        row=work['rows'].get(identity)
        if row is None:
            if len(work['rows'])>=100: _fail('上游程序经验超过本次100项核查预算，不能继续复用')
            row=_checked_case(db,p,identity,lock);work['rows'][identity]=row
        if (row.status!='trusted' or row.version!=snapshot['case_version'] or row.data['record_hash']!=snapshot['case_record_hash']
            or row.data['source_proof']!=snapshot['source_proof'] or row.data['scope']!=snapshot['scope']):
            _fail('上游程序经验已撤回、改变或缺少冻结证明，不能发布或采用派生经验')
        if row.valid_until and row.valid_until<=now(): _fail('上游程序经验已到期，不能发布或采用派生经验')
        _permission(db,p,row.data['review']['actor_id'],lock)
        if identity not in work['done']:
            _future_lineage(db,p,scoped(db,Task,row.task_id,p),visited|{identity},depth+1,lock,work)
            work['done'].add(identity)


def _current(db, p, task, lock=False):
    from .native_goals import guard, basis
    from .goals import query_goal
    if lock:
        db.execute(select(DomainInstance).where(DomainInstance.id == task.data['instance_id'],
            DomainInstance.workspace_id == p.workspace_id).with_for_update(read=True).execution_options(populate_existing=True)).scalar_one()
    guard(db,p,task)
    evidence = query_goal(db,p,task,learning=False)
    return evidence, basis(task,evidence)


def _active_use(state):
    removed = {item['use_id'] for item in state['disengagements']}
    return next((item for item in reversed(state['uses']) if item['id'] not in removed), None)


def _checked_use(state, body=None):
    use = _active_use(state)
    if not use or use['use_hash'] != digest(use['snapshot']): _fail('没有有效采用记录，或采用证明摘要不一致')
    if body and body.use_hash != use['use_hash']: _fail('原采用版本已改变，请核对当前记录')
    return use


def original_content_fingerprint(file_hashes):
    """原件集合只按内容去重；排列与重复引用不是额外观测。"""
    return digest(sorted(set(file_hashes))) if file_hashes else None


def _refs(db, p, task, evidence, field):
    from .instance_domain import object_display
    from .observation_integrity import checked_report, CHECK_ERRORS
    from .native_goals import verify_observation_history
    package = scoped(db,Capability,task.package_id,p)
    result = []
    for kind, items in [('archive',evidence.get('evidence',[])),('observation',evidence.get('observation_evidence',[]))]:
        source=task.data['goal_spec']['evidence']['source']
        if source=='archive' and kind!='archive' or source=='observations' and kind!='observation': continue
        for item in items:
            if item.get('entity_id') not in task.data['entity_ids'] or item.get('attributes',{}).get(field) is None: continue
            entity = scoped(db,Entity,item['entity_id'],p)
            ref = {'kind':kind,'entity_id':entity.id,'entity_label':object_display(entity,package)['label'],
                'source_name':'当前业务档案','observed_at':item.get('observed_at'),'version':item.get('version'),
        'value':copy.deepcopy(item['attributes'][field]),'unit':None,'field':field,
                'object_type':entity.entity_type,'comparison_context':None}
            if kind == 'archive':
                if entity.version != item['version'] or entity.data.get(field) != ref['value']: _fail('当前档案已改变，请重新核对')
                schema = package.data['specification']['entity_schemas'][entity.entity_type]['properties'].get(field,{})
                ref['unit'] = schema.get('unit') or schema.get('units')
                ref['source_id'] = entity.id
                ref['comparison_context'] = entity.data.get('comparison_context')
                ref['source_hash'] = digest({'version':entity.version,'value':ref['value'],'observed_at':ref['observed_at']})
                ref['original_fingerprint']=None
            else:
                row = scoped(db,Observation,item['observation_id'],p)
                try: report = checked_report(db,p,row)
                except CHECK_ERRORS as error: raise HTTPException(409,'当前登记原件或提取血缘无法核对') from error
                verify_observation_history(row)
                if row.version != item['version'] or row.entity_id != entity.id or report['attributes'].get(field) != ref['value']:
                    _fail('当前登记版本、对象或字段已改变')
                if report['review'] == 'rejected': _fail('当前登记已经拒绝')
                schema=package.data['specification']['entity_schemas'][entity.entity_type]['properties'].get(field,{})
                unit=schema.get('unit') or schema.get('units')
                if row.data.get('mqtt_binding_id'):
                    from .mqtt_observations import find_binding
                    binding=find_binding(db,p,row.data['mqtt_binding_id'])
                    declared=binding.data['field_units'].get(field)
                    if unit and declared and unit!=declared: _fail('登记单位与原业务字段单位不一致')
                    unit=declared or unit
                ref.update({'source_name':report['source_name'],'source_id':row.id,'input_hash':row.data.get('input_hash'),
                    'invocation_id':report['invocation_id'],'file_hashes':report['file_hashes'],
                    'source_hash':digest(row.data['extracted']), 'unit':unit,
                    'original_fingerprint':original_content_fingerprint(report['file_hashes']) if report['file_hashes'] else digest({
                        'entity_id':row.entity_id,'observed_at':row.observed_at.isoformat(),'attributes':(row.data.get('original_request') or {}).get('attributes')}),
                    'comparison_context':(row.data.get('original_request') or {}).get('comparison_context')})
            ref['ref_hash'] = digest(ref)
            result.append(ref)
    return result


def _item_evidence(db, p, task, evidence, step):
    refs = _refs(db,p,task,evidence,step['field'])
    from .native_method_evolution import template_evidence
    ready,reason = template_evidence(task.data['entity_ids'],refs,step['kind'])
    return refs,ready,reason


def gate(db, p, task, evidence, basis_hash, lock=False):
    """Additional gate only. It cannot turn an original failed standard into success."""
    state = _state(task)
    use = _active_use(state)
    if not use: return {'required':False,'all_confirmed':True,'items':[],'blocked_reason':None,'use_hash':None}
    use = _checked_use(state)
    if use['snapshot']['task_id'] != task.id or use['snapshot']['context_hash'] != task.data['native_goal_context_hash']:
        _fail('采用证明不属于原目标或当前任务')
    blocked = None
    try:
        row = _checked_case(db,p,use['snapshot']['case_id'],lock)
        if row.version != use['snapshot']['case_version'] or row.data['record_hash'] != use['snapshot']['case_record_hash']:
            _fail('采用的经验版本已撤回或改变，请重新核查或明确解除采用')
        _trusted(db,p,task,row,lock)
    except HTTPException as error: blocked = str(error.detail)
    items = []
    labels = {item['key']:item['label'] for item in _fields(db,p,task)}
    for step in use['snapshot']['steps']:
        refs,ready,reason = _item_evidence(db,p,task,evidence,step)
        confirmation = next((item for item in reversed(state['confirmations']) if item['use_id']==use['id'] and item['item_id']==step['id']),None)
        valid = bool(confirmation and confirmation['conclusion']=='confirmed' and confirmation['basis_hash']==basis_hash
            and confirmation['refs_hash']==digest(refs) and confirmation['use_hash']==use['use_hash'])
        if valid:
            try: _permission(db,p,confirmation['actor_id'],lock)
            except HTTPException: valid=False;reason='原核查人的当前权限已失效，请由有权限的人重新核查'
        items.append({**step,'label':TEMPLATES[step['kind']],'field_label':labels[step['field']],
            'current_refs':refs,'evidence_ready':ready,'evidence_reason':reason,'confirmed':valid and ready and not blocked,
            'confirmation':({**confirmation,'stale':not valid} if confirmation else None),
            'can_confirm':not blocked,'confirm_reason':blocked or ''})
    return {'required':True,'all_confirmed':bool(items) and all(item['confirmed'] for item in items),
        'items':items,'blocked_reason':blocked,'use_hash':use['use_hash'],'id':use['id'],
        'case_id':use['snapshot']['case_id'],'case_version':use['snapshot']['case_version'],
        'case_record_hash':use['snapshot']['case_record_hash'],'case_snapshot':copy.deepcopy(use['snapshot']),
        'case_title':use['snapshot']['title'],'adopted_at':use['adopted_at'],'active':True,'current_basis_hash':basis_hash}


def execute(db, p, task, body, operation):
    db.info.pop('native_experience_future_work',None)
    if operation in {'revise','evaluate'}:
        from .native_method_evolution import execute as evolve
        return evolve(db,p,task,body,operation)
    _manage(db,p,task)
    state,new = _journal(db,p,task,body,operation)
    if not new:
        entry=next(item for item in state['commands'] if item['request_id']==body.request_id)
        if entry.get('result'):
            from .native_method_evolution import receipt
            return receipt(db,p,task,entry)
        return
    if operation=='draft':
        source = _origin(db,p,task,True)
        if source['acceptance_hash'] != body.acceptance_hash: _fail('原正式验收版本已改变，请保留草稿核对')
        fields = {item['key']:item for item in _fields(db,p,task)}
        if len({step.id for step in body.steps}) != len(body.steps): raise HTTPException(422,'核查项编号不能重复')
        for step in body.steps:
            if step.field not in fields or step.kind not in fields[step.field]['templates']:
                raise HTTPException(422,'核查模板或字段不属于原登记范围')
        content = {'title':body.title,'summary':body.summary,'limits':body.limits,'steps':[step.model_dump() for step in body.steps]}
        if body.case_id:
            if body.expected_case_version is None or body.content_hash is None: raise HTTPException(422,'修改原稿需固定原稿版本和内容摘要')
            row = _case_request(db,p,task,body)
            if row.status!='draft': _fail('已提交的经验不可覆盖，请保留原审核历史')
            _archive_draft(db,p,task,row,body)
            row.version += 1
        else:
            if body.expected_case_version is not None or body.content_hash is not None: raise HTTPException(422,'新稿不得伪造原稿版本')
            if state['case_id']: _fail('原任务已有经验草稿，请读取原稿后修改')
            row = Memory(workspace_id=p.workspace_id,task_id=task.id,status='draft',text=body.title,data={})
            db.add(row);db.flush();state['case_id']=row.id
        row.data = {**row.data,'type':TYPE,'native_package_id':task.package_id,'source_task_id':task.id,
            'scope':_scope(db,p,task),'source_proof':source,'content':content,'content_hash':digest(content),'saved_at':now().isoformat(),
            'created_by':row.data.get('created_by',p.user_id),'history':row.data.get('history',[])}
        # 人工保存是新正文；云导入调用共同保存后会显式附上本次来源。
        row.data={key:value for key,value in row.data.items() if key!='suggestion_origin'}
        row.text=body.title;_seal_case(row)
    elif operation=='restore':
        row=_case_request(db,p,task,body)
        if row.status!='draft': _fail('当前方法已提交或审核，不能恢复覆盖；旧稿仍可查看')
        selected=next((item for item in _draft_revisions(row) if item['revision']==body.revision_version),None)
        if selected is None or selected['revision_hash']!=body.revision_hash:
            _fail('所选旧草稿版本或摘要已改变，本次未恢复，请重新核对')
        content=copy.deepcopy(selected['content'])
        fields={item['key']:item for item in _fields(db,p,task)}
        if any(step['field'] not in fields or step['kind'] not in fields[step['field']]['templates'] for step in content['steps']):
            _fail('旧草稿字段或核查模板不属于当前原业务结构，不能恢复')
        _archive_draft(db,p,task,row,body)
        row.version+=1;row.text=content['title']
        row.data={**row.data,'content':content,'content_hash':digest(content),'saved_at':now().isoformat()}
        if selected.get('suggestion_origin') is None:
            row.data={key:value for key,value in row.data.items() if key!='suggestion_origin'}
        else:row.data={**row.data,'suggestion_origin':copy.deepcopy(selected['suggestion_origin'])}
        _seal_case(row)
    elif operation in {'submit','review','revoke'}:
        row = _case_request(db,p,task,body,check_origin=operation!='revoke',_local_origin=operation!='revoke')
        if operation!='revoke': _future_lineage(db,p,task,lock=True)
        if operation=='submit' or operation=='review' and body.decision=='trust':
            from .native_method_evolution import evaluation_gate
            evaluation_gate(db,p,row)
        if operation=='submit':
            if row.status!='draft': _fail('只有原草稿可以提交审核')
            row.status='candidate'
        elif operation=='review':
            if row.status!='candidate': _fail('只有已提交的原经验可以审核')
            expiry=timestamp(body.valid_until)
            if expiry and expiry<=now(): raise HTTPException(422,'经验有效期必须是未来有时区的时间')
            row.status='trusted' if body.decision=='trust' else 'rejected';row.valid_until=expiry
            row.data={**row.data,'review':{'actor_id':p.user_id,'reason':body.reason,'decision':body.decision,'at':now().isoformat()}}
        else:
            if row.status!='trusted': _fail('只有当前已审核经验可以撤回')
            row.status='revoked'
        history=copy.deepcopy(row.data['history'])
        if len(history)>=LIMIT: _fail('经验审核历史已达保存上限')
        history.append({'operation':operation,'actor_id':p.user_id,'reason':body.reason,'at':now().isoformat(),'prior_record_hash':row.data['record_hash']})
        row.data={**row.data,'history':history,'saved_at':now().isoformat()};row.version+=1;_seal_case(row)
    elif operation=='adopt':
        evidence,current_basis=_current(db,p,task,True)
        if current_basis!=body.basis_hash: _fail('当前目标资料或流程已改变，请重新核对后采用')
        if _active_use(state): _fail('当前任务已有必核清单，请完成核查或明确解除原采用')
        row=_checked_case(db,p,body.case_id,True)
        check_version(row,body.expected_case_version)
        if row.data['content_hash']!=body.content_hash: _fail('原经验内容已改变')
        _trusted(db,p,task,row,True)
        snapshot={'task_id':task.id,'context_hash':task.data['native_goal_context_hash'],'goal_snapshot':copy.deepcopy(task.data['native_goal_context']['goal_snapshot']),
            'case_id':row.id,'case_version':row.version,'case_record_hash':row.data['record_hash'],'source_proof':copy.deepcopy(row.data['source_proof']),
            'title':row.data['content']['title'],'steps':copy.deepcopy(row.data['content']['steps']),
            'scope':copy.deepcopy(row.data['scope']),'approved_by':row.data['review']['actor_id']}
        state['uses'].append({'id':str(uuid4()),'snapshot':snapshot,'use_hash':digest(snapshot),'adopted_at':now().isoformat(),
            'actor_id':p.user_id,'reason':body.reason,'initial_basis_hash':current_basis})
    elif operation in {'confirm','disengage','feedback'}:
        use = _checked_use(state,body)
        if operation=='disengage':
            from .native_goals import saved_context
            saved_context(db,p,task)
            if task.data.get('native_goal_acceptance'): _fail('已验收任务的采用历史不能解除或覆盖')
            state['disengagements'].append({'use_id':use['id'],'use_hash':use['use_hash'],'case_title':use['snapshot']['title'],
                'actor_id':p.user_id,'reason':body.reason,'disengaged_at':now().isoformat()})
        elif operation=='feedback':
            state['feedback'].append({'use_id':use['id'],'use_hash':use['use_hash'],'case_title':use['snapshot']['title'],
                'actor_id':p.user_id,'helpful':body.helpful,'note':body.note,'reason':body.reason,'saved_at':now().isoformat()})
        else:
            evidence,current_basis=_current(db,p,task,True)
            if current_basis!=body.basis_hash: _fail('当前资料或流程依据已改变，原核查保留，请重新核对')
            result=gate(db,p,task,evidence,current_basis,True)
            if result['blocked_reason']: _fail(result['blocked_reason'])
            item=next((item for item in result['items'] if item['id']==body.item_id),None)
            if not item: raise HTTPException(422,'请选择当前清单中的核查项')
            if body.conclusion=='confirmed' and not item['evidence_ready']: _fail(item['evidence_reason'])
            state['confirmations'].append({'use_id':use['id'],'use_hash':use['use_hash'],'item_id':item['id'],
                'label':item['label'],'basis_hash':current_basis,'refs':copy.deepcopy(item['current_refs']),'refs_hash':digest(item['current_refs']),
                'actor_id':p.user_id,'conclusion':body.conclusion,'reason':body.reason,'confirmed_at':now().isoformat()})
    else: raise HTTPException(422,'不支持此经验动作')
    _finish(db,p,task,state,body,operation)
    if operation in {'draft','restore','submit','review','revoke'} and row.data.get('evolution'):
        from .native_method_evolution import receipt
        state['commands'][-1]['result']={'id':row.id,'version':row.version,'record_hash':row.data['record_hash'],'kind':'revision'}
        state['commands'][-1].update(workspace_id=p.workspace_id,task_id=task.id)
        _save(task,state)
        return receipt(db,p,task,state['commands'][-1])
    if operation in {'adopt','confirm','disengage'} and not task.data.get('native_goal_acceptance'):
        from .native_goals import queue
        queue(db,p,task,'experience:'+body.request_id)


def recover(db,p,task,body):
    _manage(db,p,task)
    from .native_method_evolution import command, receipt
    try:
        original=(command(body.operation,body.original_request) if body.operation in {'revise','evaluate'}
            else MODELS[body.operation].model_validate(body.original_request))
    except ValidationError as error: raise HTTPException(422,'原经验请求结构不完整，不能猜测或作废') from error
    state=_state(task)
    old=next((entry for entry in state['commands'] if entry['request_id']==original.request_id),None)
    if old:
        if old['body_hash']!=digest(original.model_dump()) or old['actor_id']!=p.user_id or old['operation']!=body.operation:
            _fail('原经验请求编号属于不同内容或提交者')
        return {'saved':old['status']=='saved','abandoned':old['status']=='abandoned','version':task.version,
            'operation_result':receipt(db,p,task,old)}
    if body.action=='probe': return {'saved':False,'abandoned':False,'version':task.version,
        'operation_result':{'request_id':original.request_id,'operation':body.operation,'request_hash':digest(original.model_dump()),
            'saved':False,'status':'unknown','actor_id':p.user_id,'workspace_id':p.workspace_id,'task_id':task.id,'original':None,'current':None}}
    check_version(task,body.expected_version)
    state['commands'].append({'request_id':original.request_id,'body_hash':digest(original.model_dump()),'actor_id':p.user_id,
        'workspace_id':p.workspace_id,'task_id':task.id,
        'operation':body.operation,'status':'abandoned','reason':body.reason,'at':now().isoformat()})
    state['saved_at']=now().isoformat();_save(task,state);task.version+=1
    audit(db,p,'native_experience.request_abandoned',task.id,{'request_id':original.request_id,'reason':body.reason})
    return {'saved':False,'abandoned':True,'version':task.version,
        'operation_result':receipt(db,p,task,state['commands'][-1])}


def _case_view(db,p,task,row):
    _checked_case(db,p,row.id,check_origin=False)
    content=row.data['content'];allowed=False;reason='';source_origin_error=None
    method_origin=row.data.get('suggestion_origin') or {}
    if row.status=='revoked' and ('source_kind' in method_origin or 'local_result_hash' in method_origin):
        from .native_method_suggestions import checked_local_case_origin
        try:checked_local_case_origin(db,p,row,scoped(db,Task,row.task_id,p))
        except HTTPException:source_origin_error='原本地清单来源无法核对，仅保留撤回历史'
    try:
        _checked_case(db,p,row.id)
        _trusted(db,p,task,row)
        from .native_goals import manage
        manage(p,task);_permission(db,p,p.user_id)
        if _active_use(_state(task)): _fail('已有核查清单，请先完成或明确解除原采用')
        _current(db,p,task)
        allowed=True
    except HTTPException as error: reason=str(error.detail)
    labels={item['key']:item['label'] for item in _fields(db,p,scoped(db,Task,row.task_id,p))}
    return {'id':row.id,'version':row.version,'status':row.status,'content_hash':row.data['content_hash'],'record_hash':row.data['record_hash'],
        **content,'steps':[{**step,'label':TEMPLATES[step['kind']],'field_label':labels[step['field']]} for step in content['steps']],
        'source_task_label':row.data['source_proof']['source_task_label'],'source_accepted_at':row.data['source_proof']['accepted_at'],
        'saved_at':row.data['saved_at'],'valid_until':row.data.get('valid_until'),
        'review_reason':row.data.get('review',{}).get('reason'),'source_origin_error':source_origin_error,
        'can_adopt':allowed and not source_origin_error,'adopt_reason':source_origin_error or reason}


def view(db,p,task):
    db.info.pop('native_experience_future_work',None)
    from .native_goals import saved_context, manage
    from .workspace_api import saved_execution
    saved_execution(db,p,task.id)
    state=_state(task)
    can_manage=True;manage_reason=''
    try: manage(p,task);_permission(db,p,p.user_id)
    except HTTPException as error:
        can_manage=False
        manage_reason='当前账号为只读权限，可查看核查与验收记录，不能修改程序经验' if p.role=='viewer' else str(error.detail)
    accepted=task.data.get('native_goal_acceptance');source_error=''
    try: _origin(db,p,task)
    except HTTPException as error: source_error=str(error.detail)
    source={'accepted':bool(accepted),'acceptance_hash':task.data.get('native_goal_acceptance_hash'),
        'accepted_at':accepted.get('at') if accepted else None,'title':task.title,'scope_error':source_error,'evidence_refs':[],'future_reuse_error':''}
    if accepted and not source_error:
        try: _future_lineage(db,p,task)
        except HTTPException as error: source['future_reuse_error']=str(error.detail)
    if accepted and not source_error:
        labels={item['key']:item['label'] for item in _fields(db,p,task)}
        from .instance_domain import object_display
        package=scoped(db,Capability,task.package_id,p)
        for kind,entries in [('archive',accepted['evidence'].get('evidence',[])),('observation',accepted['evidence'].get('observation_evidence',[]))]:
            for item in entries:
                entity=scoped(db,Entity,item['entity_id'],p)
                source['evidence_refs'].append({'kind':kind,'entity_label':object_display(entity,package)['label'],
                    'source_name':'已验收业务档案' if kind=='archive' else '已验收登记原件',
                    'observed_at':item.get('observed_at'),'values':[{'field_label':labels.get(field,field),'value':value} for field,value in item['attributes'].items()],
                    'file_hashes':next((entry['file_hashes'] for entry in _origin(db,p,task)['observations'] if entry['id']==item.get('observation_id')),[])})
    draft=None
    if state['case_id']:
        draft=_case_view(db,p,task,_checked_case(db,p,state['case_id'],check_origin=False))
    available=[];current_basis=None;current_error='';use=None
    try:
        scope=_scope(db,p,task)
        rows=[] if accepted else db.scalars(select(Memory).where(Memory.workspace_id==p.workspace_id,Memory.data['type'].as_string()==TYPE,
            Memory.data['scope']['instance_id'].as_string()==scope['instance_id'],
            Memory.data['scope']['business_id'].as_string()==scope['business_id'],
            Memory.data['scope']['release_id'].as_string()==scope['release_id'],Memory.status=='trusted')
            .order_by(Memory.created_at.desc(),Memory.id).limit(LIMIT+1)).all()
        if len(rows)>LIMIT: _fail('程序经验超过100条读取上限，需要分页，不能展示不完整清单')
        available=[_case_view(db,p,task,row) for row in rows if row.task_id!=task.id]
    except HTTPException as error:
        if '100条读取' in str(error.detail): raise
        current_error=str(error.detail)
    active_use=_active_use(state)
    if not accepted:
        try:
            evidence,current_basis=_current(db,p,task)
            if active_use: use=gate(db,p,task,evidence,current_basis)
        except HTTPException as error: current_error=str(error.detail)
    elif active_use:
        # A frozen accepted use is historical, not permission to execute again.
        use={**copy.deepcopy(accepted.get('experience_proof',{})),'active':False,'historical':True,'all_confirmed':True,
            'blocked_reason':None,'current_basis_hash':accepted['basis_hash']}
    if active_use and use is None:
        use={'id':active_use['id'],'use_hash':active_use['use_hash'],'case_id':active_use['snapshot']['case_id'],
            'case_version':active_use['snapshot']['case_version'],'case_title':active_use['snapshot']['title'],
            'adopted_at':active_use['adopted_at'],'active':True,'current_basis_hash':current_basis,'all_confirmed':False,
            'blocked_reason':current_error,'items':[]}
    if use:
        for item in use.get('items',[]):
            item['can_confirm']=bool(item.get('can_confirm') and can_manage and not accepted)
            if not can_manage: item['confirm_reason']=manage_reason
    permissions={key:{'allowed':False,'reason':manage_reason or '此任务当前不能进行该动作'} for key in ('draft','restore','submit','review','revoke','confirm','disengage','feedback')}
    if can_manage:
        permissions['draft']={'allowed':not source_error and (not draft or draft['status']=='draft'),'reason':source_error or ('已提交的经验不可覆盖' if draft and draft['status']!='draft' else '')}
        permissions['restore']=_restore_permission(db,p,task,scoped(db,Memory,draft['id'],p)) if draft else {'allowed':False,'reason':'当前没有可恢复覆盖的方法草稿'}
        for key,status in [('submit','draft'),('review','candidate'),('revoke','trusted')]:
            problem='' if key=='revoke' else source_error or source['future_reuse_error']
            permissions[key]={'allowed':bool(draft and draft['status']==status and not problem),'reason':problem or ('' if draft and draft['status']==status else '原经验状态不支持此动作')}
        permissions['confirm']={'allowed':bool(use and not accepted and not current_error and not use['blocked_reason']),'reason':current_error or (use.get('blocked_reason') if use else '尚未采用核查方法') or ''}
        permissions['disengage']={'allowed':bool(active_use and not accepted),'reason':'' if active_use and not accepted else '没有可解除的当前采用'}
        permissions['feedback']={'allowed':bool(active_use),'reason':'' if active_use else '尚未保存采用记录'}
    from .native_method_evolution import view as evolution_view
    return {'schema':'native-method-experience-v1','task_id':task.id,'version':task.version,'can_manage':can_manage,
        'evolution':evolution_view(db,p,task,can_manage),
        'source':source,'current_basis_hash':current_basis,'field_options':_fields(db,p,task),'template_options':[{'kind':key,'label':label} for key,label in TEMPLATES.items()],
        'draft':draft,'available_cases':available,'cases_total':len(available),'has_more':False,'use':use,'permissions':permissions,'saved_at':state.get('saved_at'),
        'history':{'uses':[{'id':item['id'],'case_title':item['snapshot']['title'],'adopted_at':item['adopted_at'],'use_hash':item['use_hash']} for item in state['uses']],
            'confirmations':[{key:item[key] for key in ('item_id','label','conclusion','reason','confirmed_at','basis_hash')} for item in state['confirmations']],
            'disengagements':[{key:item[key] for key in ('case_title','reason','disengaged_at')} for item in state['disengagements']],
            'feedback':[{key:item[key] for key in ('case_title','helpful','note','reason','saved_at')} for item in state['feedback']]}}
