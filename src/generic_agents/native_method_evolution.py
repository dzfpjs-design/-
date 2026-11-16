"""Recoverable native-method revisions and frozen, local template comparisons.

These records never enter the legacy learning namespace. Comparison measures
checklist coverage, not instruction quality, human time, or business benefit.
"""
import copy
import re
from datetime import datetime
from functools import wraps
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, StrictInt, TypeAdapter

from .contracts import Contract, digest
from .models import Task, Memory, Entity, Observation, now
from .services import scoped, check_version
from . import native_cases as cases

REVISION = 'native-method-revision-v1'
EVALUATION = 'native_method_evaluation'
EVALUATION_SCHEMA = 'native-method-evaluation-v1'
HASH = r'^[0-9a-f]{64}$'


class FeedbackRef(Contract):
    task_id: str
    use_hash: str = Field(pattern=HASH)
    index: StrictInt = Field(ge=0, le=99)
    entry_hash: str = Field(pattern=HASH)


class Revise(cases.Command):
    parent_case_id: str
    parent_case_version: StrictInt = Field(ge=1)
    parent_record_hash: str = Field(pattern=HASH)
    parent_content_hash: str = Field(pattern=HASH)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default='', max_length=2000)
    limits: str = Field(default='', max_length=2000)
    steps: list[cases.Step] = Field(min_length=1, max_length=5)
    feedback_refs: list[FeedbackRef] = Field(min_length=1, max_length=20)


class Label(Contract):
    kind: Literal['source_identity','source_time','units','comparability','field_verification']
    field: str = Field(min_length=1, max_length=100)
    expectation: Literal['required','unnecessary','unknown']
    reason: str = Field(min_length=1, max_length=2000)


class TestInput(Contract):
    task_id: str
    expected_task_version: StrictInt = Field(ge=1)
    acceptance_hash: str = Field(pattern=HASH)
    labels: list[Label] = Field(min_length=1, max_length=100)


class EvaluateFreeze(cases.CaseCommand):
    action: Literal['freeze']
    proposal_hash: str = Field(pattern=HASH)
    tests: list[TestInput] = Field(min_length=1, max_length=20)


class EvaluateCompare(cases.CaseCommand):
    action: Literal['compare']
    proposal_hash: str = Field(pattern=HASH)
    evaluation_id: str
    expected_evaluation_version: StrictInt = Field(ge=1)
    evaluation_record_hash: str = Field(pattern=HASH)


Evaluate = Annotated[EvaluateFreeze | EvaluateCompare, Field(discriminator='action')]


def command(operation, body):
    return Revise.model_validate(body) if operation == 'revise' else TypeAdapter(Evaluate).validate_python(body)


def fail(reason):
    raise HTTPException(409, reason)


def registered_ids(state, key):
    values = state.get(key, [])
    if (type(values) is not list or len(values) > cases.LIMIT or any(type(value) is not str for value in values)
        or len(set(values)) != len(values)):
        fail('修订或评测登记范围无法核对')
    try:
        if any(str(UUID(value)) != value for value in values): raise ValueError()
    except (ValueError, TypeError): fail('修订或评测编号无法核对')
    return values


def proposal_digest(data):
    return digest({'schema':REVISION,'content':data['content'],'scope':data['scope'],
        'source_proof':data['source_proof'],'evolution':data['evolution']})


def intact(function):
    @wraps(function)
    def checked(*args,**kwargs):
        try: return function(*args,**kwargs)
        except (KeyError,TypeError,ValueError,AttributeError): fail('原生修订、评测或时间证明结构无法核对')
    return checked


def template_evidence(entity_ids, refs, kind):
    """Same deterministic evidence rule for live checks and frozen comparisons."""
    ready = bool(refs)
    reason = '' if ready else '当前资料没有该字段，不能以旧案例补足'
    if ready and kind == 'source_time' and any(not ref['observed_at'] for ref in refs):
        ready, reason = False, '当前资料时间未登记，需要核查，未知不能算已确认'
    if ready and kind == 'units' and (any(not ref['unit'] for ref in refs) or len({ref['unit'] for ref in refs}) != 1):
        ready, reason = False, '当前数值单位未登记，需要核查，未知不能算已确认'
    if ready and kind == 'comparability':
        contexts = [ref['comparison_context'] for ref in refs]
        def complete(context):
            return (isinstance(context, dict) and {'measurement_method','sampling_interval','spatial_scope'}.issubset(context)
                and isinstance(context['measurement_method'], str) and bool(context['measurement_method'].strip())
                and isinstance(context['spatial_scope'], str) and bool(context['spatial_scope'].strip())
                and type(context['sampling_interval']) in (int,float) and 0 < context['sampling_interval'] < float('inf'))
        if (any(len({ref['original_fingerprint'] for ref in refs if ref['entity_id'] == identity
                     and ref['kind'] == 'observation' and ref.get('file_hashes')}) < 2 for identity in entity_ids)
            or any(not complete(context) for context in contexts) or len({digest(context) for context in contexts}) != 1
            or any(not ref['unit'] for ref in refs) or len({ref['unit'] for ref in refs}) != 1
            or any(not ref['observed_at'] for ref in refs)):
            ready, reason = False, '当前资料的单位、采样方法、空间范围或时间条件未一致登记，不能仅凭两份记录确认可比较'
    return ready, reason


def comparison(baseline, candidate, tests, independence):
    """Pure function: no current rows, labels, models, or instructions consulted."""
    keys = ('required_covered','required_missing','unnecessary','unknown','evidence_ready','evidence_blocked','check_count')
    totals = {name:dict.fromkeys(keys,0) for name in ('baseline','candidate')}
    details = []
    for test in tests:
        labels = {(item['kind'], item['field']):item for item in test['labels']}
        if len(labels) != len(test['labels']): fail('冻结标签不能重复')
        arms = {}
        for name, content in (('baseline',baseline), ('candidate',candidate)):
            pairs = {(step['kind'],step['field']) for step in content['steps']}
            counts = dict.fromkeys(keys,0); counts['check_count'] = len(content['steps'])
            counts['required_covered'] = sum(item['expectation'] == 'required' and pair in pairs for pair,item in labels.items())
            counts['required_missing'] = sum(item['expectation'] == 'required' and pair not in pairs for pair,item in labels.items())
            counts['unnecessary'] = sum(item['expectation'] == 'unnecessary' and pair in pairs for pair,item in labels.items())
            counts['unknown'] = sum(item['expectation'] == 'unknown' for item in labels.values()) + len(pairs-set(labels))
            items = []
            for step in content['steps']:
                refs = test['refs'].get(step['field'],[])
                ready, reason = template_evidence(test['entity_ids'],refs,step['kind'])
                counts['evidence_ready' if ready else 'evidence_blocked'] += 1
                items.append({'kind':step['kind'],'field':step['field'],'evidence_ready':ready,'reason':reason})
            arms[name] = items
            for key in keys: totals[name][key] += counts[key]
        unlabeled = sorted(({(step['kind'],step['field']) for content in (baseline,candidate) for step in content['steps']})-set(labels))
        details.append({'task_id':test['task_id'],'baseline_items':arms['baseline'],'candidate_items':arms['candidate'],
            'unlabeled_dimensions':[{'kind':kind,'field':field} for kind,field in unlabeled]})
    reasons = list(independence['reasons'])
    if totals['candidate']['unknown']: reasons.append('测试标签有未知或尚未独立标注的核查项')
    if totals['baseline']['unknown']: reasons.append('基线方法存在未知或尚未独立标注的核查项，不能省略对照')
    if totals['candidate']['required_missing']: reasons.append('修订方法仍遗漏人工标签要求的核查项')
    if totals['candidate']['evidence_blocked']: reasons.append('修订方法存在当前测试证据不足的核查项')
    return {**totals,'delta':{key:totals['candidate'][key]-totals['baseline'][key] for key in keys},
        'tests':details,'review_ready':bool(tests) and independence['passed'] and not reasons,'reasons':reasons,
        'instruction_quality_scored':False,'human_cost_measured':False,'business_improvement_proved':False}


def _instant(value):
    result = datetime.fromisoformat(value) if isinstance(value,str) else value
    if not isinstance(result,datetime) or result.tzinfo is None: fail('原登记时间缺少可核对的时区')
    return result


def snapshot_samples(data):
    """Only declared sample identities; shared recipes/configuration are not samples."""
    files=set();derived=set();diagnostics=[]
    groups=('material_snapshots','image_snapshots','scene_snapshots','comparison_scene_snapshots','audio_snapshots')
    skip={'configuration','installation','recipe','schema','metadata','audio_metadata','scope','business','release'}
    for group in groups:
        items=data.get(group,[])
        if type(items) is not list:
            diagnostics.append(group+'的原件清单结构无法核对');continue
        for index,item in enumerate(items):
            valid_files=set();invalid=False
            def visit(value):
                nonlocal invalid
                if isinstance(value,list):
                    for child in value:visit(child)
                elif isinstance(value,dict):
                    for key,child in value.items():
                        if key in {'sha256','input_sha256'} or group=='material_snapshots' and key=='hash':
                            if type(child) is str and re.fullmatch('[0-9a-f]{64}',child):valid_files.add(child)
                            else:invalid=True
                        elif key=='file_hashes':
                            if type(child) is list and all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in child):valid_files.update(child)
                            else:invalid=True
                        elif key in {'evidence_id','original_evidence_id','input_evidence_id','source_run_id','scene_id','source_scene_id'}:
                            if type(child) is str and child:derived.add(('evidence' if 'evidence' in key else key)+':'+child)
                            else:invalid=True
                        elif key not in skip:visit(child)
            visit(item)
            if isinstance(item,dict) and type(item.get('id')) is str:
                derived.add(('evidence' if group=='image_snapshots' else group)+':'+item['id'])
            if invalid or not valid_files:diagnostics.append(group+'第'+str(index+1)+'个样本的原件内容或派生身份无法证明')
            files.update(valid_files)
    return {'file_hashes':sorted(files),'derived_fingerprints':sorted(derived),'diagnostics':diagnostics}


def footprint(db, p, task):
    proof = cases._origin(db,p,task,True)
    evidence = task.data['native_goal_acceptance']['evidence']
    result = {key:set() for key in ('task_ids','entity_ids','observation_ids','file_hashes','content_fingerprints','derived_fingerprints')}
    result['task_ids'].add(task.id); result['entity_ids'].update(task.data['entity_ids'])
    result['derived_fingerprints'].add(proof['input_snapshots_hash'])
    # The empty materials digest is not a shared original or derivation.
    empty = digest({key:[] for key in ('material_snapshots','image_snapshots','scene_snapshots','comparison_scene_snapshots','audio_snapshots')})
    result['derived_fingerprints'].discard(empty)
    samples=snapshot_samples(task.data)
    result['file_hashes'].update(samples['file_hashes']);result['derived_fingerprints'].update(samples['derived_fingerprints'])
    for kind, rows in (('archive',evidence.get('evidence',[])), ('observation',evidence.get('observation_evidence',[]))):
        for item in rows:
            result['content_fingerprints'].add(digest({'kind':kind,'attributes':item['attributes']}))
    for observation in proof['observations']:
        result['observation_ids'].add(observation['id']); result['file_hashes'].update(observation['file_hashes'])
        result['derived_fingerprints'].update(value for value in (observation.get('input_hash'),observation.get('extracted_hash'),observation.get('invocation_id')) if value)
    return {key:sorted(values) for key,values in result.items()}, proof


def merge_footprints(values):
    return {key:sorted(set().union(*(set(item[key]) for item in values))) for key in
        ('task_ids','entity_ids','observation_ids','file_hashes','content_fingerprints','derived_fingerprints')}


@intact
def independence(exclusions, tests, created_at, source_diagnostics=()):
    reasons = ['来源或反馈原件：'+reason for reason in source_diagnostics]
    created = _instant(created_at)
    seen = {key:set() for key in exclusions}
    for test in tests:
        prefix = test['task_title'] + '：'
        for key in exclusions:
            values = set(test['footprint'][key])
            if values & set(exclusions[key]): reasons.append(prefix+'与来源或反馈的'+key+'重合')
            if values & seen[key]: reasons.append(prefix+'与同批测试的'+key+'重复')
            seen[key].update(values)
        registrations = test['registrations']
        def proven_after(value):
            try: return _instant(value)>created
            except (HTTPException,ValueError,TypeError): return False
        if not registrations or any(not proven_after(item['registered_at']) for item in registrations):
            reasons.append(prefix+'任务、对象或观测未证明在修订创建后登记')
        for item in registrations:
            if item['kind'] == 'observation' and not proven_after(item.get('observed_at')):
                reasons.append(prefix+'观测原始时间未证明晚于修订创建')
        reasons.extend(prefix+reason for reason in test.get('diagnostics',[]))
    return {'passed':bool(tests) and not reasons,'reasons':reasons,'rule':'forward-independent-native-evidence-v1'}


@intact
def _feedback(db,p,reference,parent):
    task = scoped(db,Task,reference.task_id,p,True)
    state = cases._state(task); entries = state['feedback']
    if reference.index >= len(entries): fail('所选反馈未保存或位置已改变')
    entry = entries[reference.index]
    keys={'use_id','use_hash','case_title','actor_id','helpful','note','reason','saved_at'}
    if (type(entry) is not dict or set(entry)!=keys or any(type(entry[key]) is not str for key in keys)
        or entry['helpful'] not in {'helpful','not_helpful','uncertain'} or not entry['reason'].strip()
        or len(entry['reason'])>2000 or len(entry['note'])>2000): fail('原反馈正文或标签结构无法核对')
    _instant(entry['saved_at'])
    if digest(entry) != reference.entry_hash or entry['use_hash'] != reference.use_hash: fail('所选反馈正文或采用摘要已改变')
    cases._permission(db,p,entry['actor_id'],True)
    use = next((item for item in state['uses'] if item['id'] == entry['use_id']),None)
    accepted = task.data.get('native_goal_acceptance') or {}
    if (not use or use['use_hash'] != digest(use['snapshot']) or accepted.get('experience_proof',{}).get('use_hash') != use['use_hash']
        or accepted['experience_proof']['case_snapshot'] != use['snapshot']
        or use['snapshot']['case_id'] != parent.id or use['snapshot']['case_version'] != parent.version
        or use['snapshot']['case_record_hash'] != parent.data['record_hash']):
        fail('反馈未固定消费本次原方法的正式验收证明')
    facts, proof = footprint(db,p,task)
    return {'task_id':task.id,'index':reference.index,'entry':copy.deepcopy(entry),'entry_hash':reference.entry_hash,
        'use_hash':reference.use_hash,'use_snapshot':copy.deepcopy(use['snapshot']),'source_proof':proof,'footprint':facts,
        'input_diagnostics':snapshot_samples(task.data)['diagnostics']}


@intact
def checked_revision(db,p,row,check_current=True):
    data = row.data; lineage = data.get('evolution')
    if (type(lineage) is not dict or lineage.get('schema') != REVISION or data.get('proposal_hash') != proposal_digest(data)
        or not isinstance(lineage.get('feedback'),list) or not 1 <= len(lineage['feedback']) <= 20): fail('原生修订正文或血缘摘要无法核对')
    parent = lineage['parent']
    if (parent['case_id'] == row.id or parent['record_hash'] != parent['data']['record_hash']
        or parent['data']['record_hash'] != digest({k:v for k,v in parent['data'].items() if k != 'record_hash'})
        or parent['data']['content_hash'] != digest(parent['data']['content'])
        or parent['case_version'] != parent['data']['version'] or parent['data']['status'] != 'trusted'
        or parent['data']['source_proof'] != data['source_proof'] or parent['data']['scope'] != data['scope']):
        fail('父方法冻结版本、来源或范围无法核对')
    if not check_current: return
    path = db.info.setdefault('native_method_revision_path',[])
    if row.id in path or len(path) >= 8: fail('修订血缘循环或超过8层核查范围')
    path.append(row.id)
    try:
        current = cases._checked_case(db,p,parent['case_id'],True)
        if current.version != parent['case_version'] or current.data != parent['data'] or current.status != 'trusted':
            fail('父方法已经改变或撤回，不能继续发布或采用派生修订')
        if current.valid_until and current.valid_until <= now(): fail('父方法已到期')
        cases._permission(db,p,current.data['review']['actor_id'],True)
        source = scoped(db,Task,row.task_id,p,True)
        facts,_ = footprint(db,p,source); all_facts = [facts];input_diagnostics=list(snapshot_samples(source.data)['diagnostics'])
        if current.data.get('evolution'):
            all_facts.append(current.data['evolution']['exclusions'])
            input_diagnostics.extend(current.data['evolution'].get('input_diagnostics',[]))
        for frozen in lineage['feedback']:
            reference = FeedbackRef(task_id=frozen['task_id'],use_hash=frozen['use_hash'],index=frozen['index'],entry_hash=frozen['entry_hash'])
            if _feedback(db,p,reference,current) != frozen: fail('反馈正式消费血缘已经改变')
            all_facts.append(frozen['footprint'])
            input_diagnostics.extend(frozen['input_diagnostics'])
        if merge_footprints(all_facts) != lineage['exclusions']: fail('来源及反馈的独立性排除集合无法核对')
        if sorted(set(input_diagnostics)) != lineage.get('input_diagnostics',[]): fail('来源或反馈的原件隔离诊断无法核对')
        if row.status == 'trusted': evaluation_gate(db,p,row)
    finally:
        path.pop()


def _seal_evaluation(row):
    row.data = {**row.data,'status':row.status,'version':row.version,'evaluation_id':row.id}
    row.data = {**row.data,'record_hash':digest({k:v for k,v in row.data.items() if k != 'record_hash'})}


@intact
def checked_evaluation(db,p,identity,task,lock=False):
    row = scoped(db,Memory,identity,p,lock); data = row.data
    if (row.task_id != task.id or identity not in registered_ids(cases._state(task),'evaluation_ids')
        or data.get('type') != EVALUATION or data.get('schema') != EVALUATION_SCHEMA
        or data.get('evaluation_id') != row.id or data.get('source_task_id') != task.id
        or data.get('status') != row.status or data.get('version') != row.version
        or data.get('record_hash') != digest({k:v for k,v in data.items() if k != 'record_hash'})
        or data.get('inputs_hash') != digest(data['tests']) or data.get('labels_hash') != digest([item['labels'] for item in data['tests']])):
        fail('冻结评测或双向登记摘要无法核对')
    if data['independence'] != independence(data['exclusions'],data['tests'],data['revision_created_at'],data.get('source_input_diagnostics',[])): fail('冻结隔离诊断摘要不一致')
    # The immutable freeze receipt predates the comparison. Re-sealing an
    # altered current input/report cannot replace that original saved artifact.
    receipts=[item for item in cases._state(task)['commands'] if item['operation']=='evaluate'
        and item.get('result',{}).get('id')==identity and item['result']['version']==1]
    if len(receipts)!=1: fail('原独立标签冻结回执无法唯一核对')
    frozen=copy.deepcopy(data);frozen.pop('record_hash',None);frozen.pop('compared_at',None)
    frozen.update(version=1,status='labels_saved' if data['independence']['passed'] else 'blocked',comparison=None)
    if receipts[0]['result']['record_hash']!=digest(frozen): fail('冻结输入或标签已偏离原不可变保存回执')
    if data.get('comparison') is not None and data['comparison'] != comparison(data['baseline'],data['candidate'],data['tests'],data['independence']):
        fail('冻结比较结果与原输入、标签不一致')
    return row


def evaluation_gate(db,p,row):
    if not row.data.get('evolution'): return
    pin = row.data.get('latest_evaluation') or {}
    if not pin: fail('修订方法须先保存独立标签并完成冻结比较')
    task = scoped(db,Task,row.task_id,p)
    evaluation = checked_evaluation(db,p,pin['id'],task,True)
    data = evaluation.data
    cases._permission(db,p,data['created_by'],True)
    if (pin != {'id':evaluation.id,'version':evaluation.version,'record_hash':data['record_hash'],'proposal_hash':data['proposal_hash']}
        or data['case_id'] != row.id or data['proposal_hash'] != row.data['proposal_hash']
        or data['candidate'] != row.data['content'] or data['baseline'] != row.data['evolution']['parent']['data']['content']
        or data['exclusions'] != row.data['evolution']['exclusions'] or data['scope'] != row.data['scope'] or evaluation.status != 'compared'
        or not data['comparison']['review_ready']): fail('修订评测过期、隔离不足、存在未知或未完成，不能放行审核')


def receipt(db,p,task,entry):
    original = entry.get('result')
    if original is None:
        context = entry.get('workspace_id') == p.workspace_id and entry.get('task_id') == task.id and entry['actor_id'] == p.user_id
        return {'request_id':entry['request_id'],'operation':entry['operation'],'request_hash':entry['body_hash'],
            'saved':entry['status']=='saved','status':'legacy_unknown' if entry['status']=='saved' else entry['status'],
            'actor_id':entry['actor_id'] if context else None,'workspace_id':p.workspace_id if context else None,
            'task_id':task.id if context else None,'original':None,'current':None}
    if entry.get('actor_id')!=p.user_id or entry.get('workspace_id')!=p.workspace_id or entry.get('task_id')!=task.id:
        fail('原产物回执不属于当前账号、工作区或任务')
    if (set(original) != {'id','version','record_hash','kind'} or type(original['version']) is not int
        or original['kind'] not in {'revision','evaluation'}): fail('原操作产物回执无法核对')
    row = (cases._checked_case(db,p,original['id'],check_origin=False) if original['kind']=='revision'
        else checked_evaluation(db,p,original['id'],task))
    if row.task_id != task.id or row.version < original['version']: fail('原产物与当前任务或版本不一致')
    return {'request_id':entry['request_id'],'operation':entry['operation'],'request_hash':entry['body_hash'],
        'saved':entry['status']=='saved','status':entry['status'],'original':copy.deepcopy(original),
        'actor_id':entry['actor_id'],'workspace_id':entry['workspace_id'],'task_id':entry['task_id'],
        'current':{'id':row.id,'version':row.version,'record_hash':row.data['record_hash'],'status':row.status}}


def _content(body, fields):
    if len({step.id for step in body.steps}) != len(body.steps): raise HTTPException(422,'核查项编号不能重复')
    for step in body.steps:
        if step.field not in fields or step.kind not in fields[step.field]['templates']: raise HTTPException(422,'修订核查模板或字段不属于原登记范围')
    return {key:getattr(body,key) for key in ('title','summary','limits')} | {'steps':[item.model_dump() for item in body.steps]}


def _freeze_test(db,p,spec,row):
    from .workspace_api import saved_execution
    task,_,_ = saved_execution(db,p,spec.task_id,True)
    check_version(task,spec.expected_task_version)
    if task.data.get('native_goal_acceptance_hash') != spec.acceptance_hash: fail('测试任务原验收版本已改变')
    if cases._scope(db,p,task) != row.data['scope']: fail('测试任务与原方法业务范围不一致')
    fields = {item['key']:item for item in cases._fields(db,p,task)}
    pairs = [(item.kind,item.field) for item in spec.labels]
    if len(set(pairs)) != len(pairs) or any(item.field not in fields or item.kind not in fields[item.field]['templates'] or not item.reason.strip() for item in spec.labels):
        raise HTTPException(422,'测试标签重复、缺少理由或不属于原业务模板')
    facts, proof = footprint(db,p,task)
    evidence = task.data['native_goal_acceptance']['evidence']
    refs = {field:cases._refs(db,p,task,evidence,field) for field in fields}
    def stamp(value): return value.isoformat() if isinstance(value,datetime) and value.tzinfo is not None else None
    registrations = [{'kind':'task','id':task.id,'registered_at':stamp(task.created_at)}]
    for identity in facts['entity_ids']:
        entity = scoped(db,Entity,identity,p,True)
        registrations.append({'kind':'archive','id':entity.id,'registered_at':stamp(entity.created_at),'time_basis':'registered_at'})
    for identity in facts['observation_ids']:
        obs = scoped(db,Observation,identity,p,True)
        registrations.append({'kind':'observation','id':obs.id,'registered_at':stamp(obs.created_at),
            'observed_at':stamp(obs.observed_at),'time_basis':'registered_and_observed_at'})
    return {'task_id':task.id,'task_title':task.title,'task_version':task.version,'acceptance_hash':spec.acceptance_hash,
        'source_proof':proof,'scope':copy.deepcopy(row.data['scope']),'entity_ids':list(task.data['entity_ids']),
        'footprint':facts,'registrations':registrations,'refs':refs,'labels':[item.model_dump() for item in spec.labels],
        'diagnostics':snapshot_samples(task.data)['diagnostics'],'registered_at':stamp(task.created_at),'archive_time_basis':'registered_at'}


def execute(db,p,task,body,operation):
    cases._manage(db,p,task)
    state,new = cases._journal(db,p,task,body,operation)
    if not new:
        return receipt(db,p,task,next(item for item in state['commands'] if item['request_id']==body.request_id))
    if operation == 'revise':
        parent = cases._checked_case(db,p,body.parent_case_id,True)
        if parent.task_id != task.id: fail('请在原方法来源任务中另建修订')
        if (parent.status != 'trusted' or parent.version != body.parent_case_version or parent.data['record_hash'] != body.parent_record_hash
            or parent.data['content_hash'] != body.parent_content_hash): fail('原方法当前可信版本或摘要已改变')
        if parent.valid_until and parent.valid_until <= now(): fail('原方法已到期')
        cases._permission(db,p,parent.data['review']['actor_id'],True)
        content = _content(body,{item['key']:item for item in cases._fields(db,p,task)})
        feedback = [_feedback(db,p,ref,parent) for ref in body.feedback_refs]
        if len({(item['task_id'],item['index']) for item in feedback}) != len(feedback): raise HTTPException(422,'同一反馈不能重复选取')
        facts,source = footprint(db,p,task)
        ids = registered_ids(state,'revision_ids')
        if len(ids) >= cases.LIMIT: fail('修订已达100条上限，原方法保留')
        row = Memory(workspace_id=p.workspace_id,task_id=task.id,status='draft',text=body.title,version=1,data={})
        db.add(row);db.flush()
        row.data = {'type':cases.TYPE,'native_package_id':task.package_id,'source_task_id':task.id,'scope':copy.deepcopy(parent.data['scope']),
            'source_proof':source,'content':content,'content_hash':digest(content),'saved_at':now().isoformat(),'created_by':p.user_id,'history':[],
            'evolution':{'schema':REVISION,'created_at':now().isoformat(),'created_by':p.user_id,
                'parent':{'case_id':parent.id,'case_version':parent.version,'record_hash':parent.data['record_hash'],'data':copy.deepcopy(parent.data)},
                'feedback':feedback,'exclusions':merge_footprints([facts]+[item['footprint'] for item in feedback]
                    +([parent.data['evolution']['exclusions']] if parent.data.get('evolution') else [])),
                'input_diagnostics':sorted(set(snapshot_samples(task.data)['diagnostics']+[reason for item in feedback for reason in item['input_diagnostics']]
                    +(parent.data['evolution'].get('input_diagnostics',[]) if parent.data.get('evolution') else [])))}}
        state['revision_ids'] = ids+[row.id]; cases._seal_case(row)
        kind = 'revision'
    else:
        row = cases._case_request(db,p,task,body)
        if not row.data.get('evolution') or row.status != 'draft': fail('仅可对原生修订草稿冻结评测')
        if row.data['proposal_hash'] != body.proposal_hash: fail('修订正文或反馈血缘已改变')
        revision = row
        if body.action == 'freeze':
            if len({item.task_id for item in body.tests}) != len(body.tests): raise HTTPException(422,'测试任务不能重复')
            tests = [_freeze_test(db,p,item,revision) for item in body.tests]
            isolation = independence(revision.data['evolution']['exclusions'],tests,revision.data['evolution']['created_at'],revision.data['evolution'].get('input_diagnostics',[]))
            ids = registered_ids(state,'evaluation_ids')
            if len(ids) >= cases.LIMIT: fail('冻结评测已达100条上限，旧标签保留')
            row = Memory(workspace_id=p.workspace_id,task_id=task.id,status='labels_saved' if isolation['passed'] else 'blocked',text='原生方法独立标签',version=1,data={})
            db.add(row);db.flush()
            row.data = {'type':EVALUATION,'schema':EVALUATION_SCHEMA,'source_task_id':task.id,'case_id':revision.id,
                'case_version':revision.version,'proposal_hash':body.proposal_hash,'revision_created_at':revision.data['evolution']['created_at'],
                'scope':copy.deepcopy(revision.data['scope']),'baseline':copy.deepcopy(revision.data['evolution']['parent']['data']['content']),
                'candidate':copy.deepcopy(revision.data['content']),'exclusions':copy.deepcopy(revision.data['evolution']['exclusions']),
                'source_input_diagnostics':copy.deepcopy(revision.data['evolution'].get('input_diagnostics',[])),
                'tests':tests,'inputs_hash':digest(tests),'labels_hash':digest([item['labels'] for item in tests]),
                'independence':isolation,'comparison':None,'created_by':p.user_id,'saved_at':now().isoformat()}
            state['evaluation_ids'] = ids+[row.id]; _seal_evaluation(row)
            revision.version += 1
            revision.data = {**revision.data,'latest_evaluation':{'id':row.id,'version':row.version,
                'record_hash':row.data['record_hash'],'proposal_hash':body.proposal_hash}}
            cases._seal_case(revision)
        else:
            row = checked_evaluation(db,p,body.evaluation_id,task,True)
            check_version(row,body.expected_evaluation_version)
            if (row.data['record_hash'] != body.evaluation_record_hash or row.data['case_id'] != revision.id
                or row.data['proposal_hash'] != body.proposal_hash or row.status not in {'labels_saved','blocked'}): fail('冻结评测版本已改变或已经比较')
            result = comparison(row.data['baseline'],row.data['candidate'],row.data['tests'],row.data['independence'])
            row.version += 1; row.status = 'compared' if result['review_ready'] else 'blocked'
            row.data = {**row.data,'comparison':result,'compared_at':now().isoformat()}; _seal_evaluation(row)
            revision.version += 1
            revision.data = {**revision.data,'latest_evaluation':{'id':row.id,'version':row.version,'record_hash':row.data['record_hash'],'proposal_hash':body.proposal_hash}}
            cases._seal_case(revision)
        kind = 'evaluation'
    cases._finish(db,p,task,state,body,operation)
    state['commands'][-1]['result'] = {'id':row.id,'version':row.version,'record_hash':row.data['record_hash'],'kind':kind}
    state['commands'][-1].update(workspace_id=p.workspace_id,task_id=task.id)
    cases._save(task,state)
    return receipt(db,p,task,state['commands'][-1])


def view(db,p,task,can_manage):
    state = cases._state(task); revisions=[]; evaluations=[]; feedback_options=[]
    for identity in registered_ids(state,'evaluation_ids'):
        row = checked_evaluation(db,p,identity,task); data=row.data
        evaluations.append({'id':row.id,'version':row.version,'status':row.status,'record_hash':data['record_hash'],
            **{key:copy.deepcopy(data[key]) for key in ('case_id','proposal_hash','inputs_hash','labels_hash','independence','comparison','tests')},
            'review_ready':bool(data.get('comparison',{} ) and data['comparison']['review_ready']),
            'reasons':(data['comparison']['reasons'] if data.get('comparison') else data['independence']['reasons'])})
    for identity in registered_ids(state,'revision_ids'):
        row = cases._checked_case(db,p,identity,check_origin=False)
        problem=''
        try: cases._checked_case(db,p,identity)
        except HTTPException as error: problem=str(error.detail)
        permissions = {}
        for operation,status in (('edit','draft'),('evaluate','draft'),('submit','draft'),('review','candidate'),('revoke','trusted')):
            reason = '' if operation=='revoke' else problem
            if not can_manage: reason='请由原任务发起人或管理员管理方法'
            elif row.status != status: reason='当前修订状态不支持此动作'
            elif operation in {'submit','review'} and not reason:
                try: evaluation_gate(db,p,row)
                except HTTPException as error: reason=str(error.detail)
            permissions[operation]={'allowed':not reason,'reason':reason}
        parent=row.data['evolution']['parent']; pin=row.data.get('latest_evaluation')
        latest=next((copy.deepcopy(item) for item in evaluations if pin and item['id']==pin['id']),None)
        if latest and latest['proposal_hash'] != row.data['proposal_hash']:
            latest['review_ready']=False;latest['reasons']=['修订正文已改变，旧比较仅供历史查看']
        revisions.append({'id':row.id,'version':row.version,'status':row.status,'record_hash':row.data['record_hash'],
            'content_hash':row.data['content_hash'],'proposal_hash':row.data['proposal_hash'],**copy.deepcopy(row.data['content']),
            'parent':{'case_id':parent['case_id'],'case_version':parent['case_version'],'record_hash':parent['record_hash'],
                'content_hash':parent['data']['content_hash'],**copy.deepcopy(parent['data']['content'])},
            'feedback_refs':[{key:item[key] for key in ('task_id','index','entry_hash','use_hash')} for item in row.data['evolution']['feedback']],
            'created_at':row.data['evolution']['created_at'],'permissions':permissions,'latest_evaluation':latest})
    # Feedback is found through saved native uses, never through legacy ranking.
    root_case = cases._checked_case(db,p,state['case_id'],check_origin=False) if state.get('case_id') else None
    diagnostics=['只比较核查模板覆盖与证据就绪；不自动评价文字、人工成本或业务收益']
    if root_case:
        from sqlalchemy import select
        candidates = db.scalars(select(Task).where(Task.workspace_id==p.workspace_id,
            Task.status=='workflow_completed',Task.id!=task.id,
            Task.data['native_experience']['uses'].is_not(None)).order_by(Task.created_at.desc(),Task.id).limit(cases.LIMIT+1)).all()
        if len(candidates) <= cases.LIMIT:
            parents=[root_case]+[scoped(db,Memory,item['id'],p) for item in revisions if item['status']=='trusted']
            for parent in parents:
                if parent.status != 'trusted': continue
                try:
                    cases._checked_case(db,p,parent.id)
                    cases._permission(db,p,parent.data['review']['actor_id'])
                    if parent.valid_until and parent.valid_until <= now(): continue
                except HTTPException: continue
                for other in candidates:
                    for index,item in enumerate(cases._state(other)['feedback']):
                        ref=FeedbackRef(task_id=other.id,use_hash=item['use_hash'],index=index,entry_hash=digest(item))
                        try: _feedback(db,p,ref,parent)
                        except HTTPException: continue
                        feedback_options.append({'task_id':other.id,'task_title':other.title,'index':index,'entry_hash':ref.entry_hash,
                            'parent_case_id':parent.id,'parent_case_version':parent.version,'parent_record_hash':parent.data['record_hash'],
                            'parent_content_hash':parent.data['content_hash'],
                            **{key:item[key] for key in ('use_hash','helpful','note','reason','saved_at')}})
        else: diagnostics.append('反馈来源超过100条读取范围，不能显示不完整的选择清单')
    active = cases._active_use(state)
    origin = ({'id':task.id,'title':task.title} if root_case else
        {'id':active['snapshot']['source_proof']['task_id'],'title':active['snapshot']['source_proof']['source_task_label']} if active else None)
    baseline = ({'id':root_case.id,'version':root_case.version,'status':root_case.status,
        'record_hash':root_case.data['record_hash'],'content_hash':root_case.data['content_hash'],**copy.deepcopy(root_case.data['content'])} if root_case else None)
    allowed = can_manage and bool(feedback_options)
    return {'schema':'native-method-evolution-v1','permissions':{'revise':{'allowed':allowed,'reason':'' if allowed else '需要当前有权管理的可信原方法及已正式消费的反馈'}},
        'origin_task':origin,'baseline':baseline,'feedback_options':feedback_options,'revisions':revisions,'evaluations':evaluations,'diagnostics':diagnostics}
