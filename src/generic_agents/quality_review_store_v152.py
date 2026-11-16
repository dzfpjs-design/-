"""Human quality review inventories, independent of current source eligibility.

No reader invokes a tool, repairs an older authority or upgrades a mechanism
assessment into permission to deploy a policy.
"""
from copy import deepcopy
import hashlib

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select

from .models import Audit, Job, Observation, SetupMaterial
from .services import audit, scoped
from .quality_review_models_v152 import ThresholdQualityReview as Review, ThresholdQualityCommand as Command
from .quality_review_protocol_v152 import DRAFT_KEYS, approval_issues, canonical_uuid, identify, parse_command, read_command
from .threshold_evolution_protocol_v147 import command_fingerprint, wire_sha256
from .typed_payload_codec_v131 import canonical_json

ERROR='质量审核、原请求或状态库存无法完整核对；人工记录保留'
UNAVAILABLE='当前比较、影子回执、标签或原件无法用于新的质量批准；人工记录及拒绝、撤回入口保留'
STATE_ACTION='threshold_quality.state_v152'
COMMAND_ACTION='threshold_quality.command_v152'
SNAPSHOT_KEYS={'id','workspace_id','actor_id','evaluation_id','shadow_id','status','version','approved_by','approval_hash','data'}
STATE_KEYS={'schema','review_id','workspace_id','actor_id','version','status','record_hash','previous_hash','operation','command_id'}
CONTEXT_KEYS={'purpose','scope','baseline_manifest','candidate_manifest','evaluation','shadow','samples'}
FROZEN_KEYS={'schema','purpose','scope','criteria','evaluation','shadow','baseline_manifest','candidate_manifest',
    'declarations','evaluation_samples','shadow_samples','label_sources','statistics','approved_by'}


class QualityInventoryError(HTTPException):
    def __init__(self,detail=ERROR):super().__init__(409,detail)


class QualitySourceError(HTTPException):
    def __init__(self,detail=UNAVAILABLE):super().__init__(409,detail)


def need(value,message=ERROR,status=409):
    if not value:raise HTTPException(status,message)


def hash_value(value):return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()
def same(left,right):return canonical_json(left)==canonical_json(right)
def snapshot(row):return {key:deepcopy(getattr(row,key)) for key in SNAPSHOT_KEYS}


def _sha(value):need(type(value) is str and len(value)==64 and not set(value)-set('0123456789abcdef'))


def draft_data(body):
    return {key:deepcopy(body.model_dump(by_alias=True)[key]) for key in DRAFT_KEYS}


def _shape(value):
    need(type(value) is dict and set(value)==SNAPSHOT_KEYS)
    for key in ('id','workspace_id','actor_id','evaluation_id','shadow_id'):canonical_uuid(value[key])
    need(type(value['version']) is int and value['version']>=1 and value['status'] in {'draft','approved','rejected','revoked'})
    data=value['data'];need(type(data) is dict and set(data)=={'schema','draft','context','frozen'}
        and data['schema']=='threshold-quality-review-record-v152')
    draft=data['draft'];need(type(draft) is dict and set(draft)==DRAFT_KEYS)
    from .quality_review_protocol_v152 import QualityDraft
    parse_command(QualityDraft,{'schema':'threshold-quality-draft-v152','request_id':value['id'],**draft})
    need(draft['evaluation_id']==value['evaluation_id'] and draft['shadow_id']==value['shadow_id'])
    context=data['context'];need(type(context) is dict and set(context)==CONTEXT_KEYS
        and context['purpose'] in {'mechanism_check','business_assessment'}
        and type(context['samples']) is list and type(context['scope']) is dict)
    from .threshold_shadow_store_v151 import SCOPE_KEYS
    need(set(context['scope'])==SCOPE_KEYS and context['scope']['workspace_id']==value['workspace_id'])
    need(context['evaluation']['id']==value['evaluation_id'] and context['shadow']['id']==value['shadow_id']
        and type(context['evaluation']['version']) is int and type(context['shadow']['version']) is int
        and context['evaluation']['version']==draft['expected_evaluation_version']
        and context['shadow']['version']==draft['expected_shadow_version'])
    if value['status'] in {'draft','rejected'}:
        need(data['frozen'] is None and value['approved_by'] is None and value['approval_hash'] is None)
    else:
        frozen=data['frozen'];need(type(frozen) is dict and set(frozen)==FROZEN_KEYS
            and frozen['schema']=='threshold-quality-freeze-v152'
            and frozen['purpose']==context['purpose'] and same(frozen['scope'],context['scope'])
            and same(frozen['declarations'],draft) and same(frozen['evaluation'],context['evaluation'])
            and same(frozen['shadow'],context['shadow']) and frozen['approved_by']==value['approved_by'])
        canonical_uuid(value['approved_by']);_sha(value['approval_hash'])
        need(hash_value(frozen)==value['approval_hash'])
    canonical_json(value)


def _transition(previous,saved,operation,body,actor_id):
    _shape(saved)
    return _transition_checked(previous,saved,operation,body,actor_id)


def _transition_checked(previous,saved,operation,body,actor_id):
    # Inventory already shaped this exact Command snapshot; no approval,
    # source or current-actor gate is cached or skipped here.
    if operation=='create':
        need(previous is None and saved['version']==1 and saved['status']=='draft'
            and saved['actor_id']==actor_id and same(saved['data']['draft'],draft_data(body)))
        return
    need(previous is not None and saved['version']==previous['version']+1 and body.expected_version==previous['version'])
    # Only parent mappings are edited. Large nested evidence is compared but
    # never modified through this temporary, unexposed object.
    expected={**previous,'version':previous['version']+1,'data':{**previous['data']}}
    if operation=='update':
        need(previous['status']=='draft')
        expected['evaluation_id']=body.evaluation_id;expected['shadow_id']=body.shadow_id
        expected['data']['draft']=draft_data(body)
        expected['data']['context']=deepcopy(saved['data']['context'])
    elif operation=='approve':
        need(previous['status']=='draft')
        expected['status']='approved';expected['approved_by']=actor_id
        expected['approval_hash']=saved['approval_hash'];expected['data']['frozen']=deepcopy(saved['data']['frozen'])
    elif operation=='reject':need(previous['status']=='draft');expected['status']='rejected'
    elif operation=='revoke':need(previous['status']=='approved');expected['status']='revoked'
    else:need(False)
    need(same(expected,saved))


def seal_review(db,p,row,operation,command_id):
    canonical_uuid(command_id);previous=None if operation=='create' else row.record_hash
    if operation=='create':need(row.version==1)
    else:row.version+=1
    row.data=deepcopy(row.data);_shape(snapshot(row));row.record_hash=hash_value(snapshot(row));db.add(row)
    audit(db,p,STATE_ACTION,row.id,{'schema':'threshold-quality-state-v152','review_id':row.id,
        'workspace_id':p.workspace_id,'actor_id':p.user_id,'version':row.version,'status':row.status,
        'record_hash':row.record_hash,'previous_hash':previous,'operation':operation,'command_id':command_id})
    db.flush()


def command_payload(row):
    return {'schema':'threshold-quality-command-pin-v152',**{key:getattr(row,key) for key in (
        'id','workspace_id','actor_id','request_id','method','path','wire_sha256','fingerprint','operation','target_id','outcome')},'data':row.data}


def command_pin(row):
    return {'schema':'threshold-quality-command-audit-v152',**{key:getattr(row,key) for key in (
        'id','workspace_id','actor_id','request_id','method','path','wire_sha256','fingerprint','operation','target_id','outcome','command_hash')},
        'result_hash':hash_value(row.data['result_snapshot'])}


def inventory(db,p):
    """Both directions of rows, all state versions and Command/Audit inventory."""
    try:
        rows=list(db.scalars(select(Review).where(Review.workspace_id==p.workspace_id).execution_options(populate_existing=True)))
        by_id={row.id:row for row in rows};need(len(by_id)==len(rows))
        states=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action==STATE_ACTION)
            .execution_options(populate_existing=True)))
        by_version={};by_target={}
        for issued in states:
            pin=issued.data;need(type(pin) is dict and set(pin)==STATE_KEYS
                and pin['schema']=='threshold-quality-state-v152' and issued.object_id in by_id
                and pin['review_id']==issued.object_id and pin['workspace_id']==p.workspace_id
                and pin['actor_id']==issued.actor_id and type(pin['version']) is int and pin['version']>=1)
            canonical_uuid(pin['command_id'])
            by_version.setdefault((issued.object_id,pin['version']),[]).append(issued)
            by_target.setdefault(issued.object_id,[]).append(issued)
        pairs=list(db.execute(select(Command,Audit).join(Audit,and_(Command.workspace_id==Audit.workspace_id,
            Audit.action==COMMAND_ACTION,Audit.object_id==Command.id),full=True).where(or_(
                Command.workspace_id==p.workspace_id,and_(Audit.workspace_id==p.workspace_id,Audit.action==COMMAND_ACTION)))
            .execution_options(populate_existing=True)))
        commands=[];saved={};nonces=set();seen=set()
        for command,issued in pairs:
            need(command is not None and issued is not None and command.id not in seen
                and command.workspace_id==p.workspace_id and type(command.version) is int and command.version==1)
            for key in ('id','workspace_id','actor_id','request_id'):canonical_uuid(getattr(command,key))
            data=command.data;need(type(data) is dict and set(data)=={'schema','wire_text','result_snapshot'}
                and data['schema']=='threshold-quality-command-record-v152' and type(data['wire_text']) is str)
            raw=data['wire_text'].encode('utf-8');original=read_command(raw)
            target,operation,model=identify(command.method,command.path)
            need(original['request_id']==command.request_id and operation==command.operation
                and (target is None or target==command.target_id) and wire_sha256(raw)==command.wire_sha256
                and command_fingerprint(command.method,command.path,p.workspace_id,command.actor_id,command.wire_sha256)==command.fingerprint
                and command.command_hash==hash_value(command_payload(command)))
            need(issued.actor_id==command.actor_id and issued.workspace_id==p.workspace_id
                and issued.object_id==command.id and same(issued.data,command_pin(command)))
            result=data['result_snapshot']
            if command.outcome=='saved':
                body=parse_command(model,original);_shape(result)
                need(command.target_id in by_id and result['id']==command.target_id and result['workspace_id']==p.workspace_id)
                matched=by_version.get((command.target_id,result['version']),[])
                need(len(matched)==1 and matched[0].data['command_id']==command.id
                    and matched[0].data['record_hash']==hash_value(result))
                saved[command.id]=(command,body)
            else:
                need(command.outcome=='rejected' and type(result) is dict and set(result)=={'status','detail'}
                    and type(result['status']) is int and result['status'] in {409,422}
                    and type(result['detail']) is str and 1<=len(result['detail'])<=2000)
            nonce=(command.actor_id,command.request_id);need(nonce not in nonces)
            nonces.add(nonce);seen.add(command.id);commands.append(command)
        used=set()
        for row in rows:
            need(type(row.version) is int and row.version>=1 and row.record_hash==hash_value(snapshot(row)))
            ordered=sorted(by_target.get(row.id,[]),key=lambda a:a.data['version'])
            need(len(ordered)==row.version);previous=None;previous_hash=None
            for version,issued in enumerate(ordered,1):
                pin=issued.data;need(pin['version']==version and pin['previous_hash']==previous_hash)
                pair=saved.get(pin['command_id']);need(pair is not None and pair[0].id not in used)
                command,body=pair;result=command.data['result_snapshot']
                need(command.target_id==row.id and command.actor_id==issued.actor_id
                    and command.operation==pin['operation'] and result['version']==version
                    and result['status']==pin['status'] and hash_value(result)==pin['record_hash'])
                _transition_checked(previous,result,command.operation,body,command.actor_id)
                used.add(command.id);previous=result;previous_hash=pin['record_hash']
            need(previous is not None and same(previous,snapshot(row)) and previous_hash==row.record_hash)
        need(used==set(saved));return rows,commands
    except QualityInventoryError:raise
    except HTTPException as error:raise QualityInventoryError() from error
    except (ValueError,TypeError,KeyError,AttributeError,UnicodeError,RecursionError,OverflowError):
        raise QualityInventoryError() from None


def get_review(db,p,identity,lock=False):
    canonical_uuid(identity);need(type(lock) is bool)
    query=select(Review).where(Review.workspace_id==p.workspace_id,Review.id==identity)
    if lock:query=query.with_for_update()
    row=db.scalar(query.execution_options(populate_existing=True));rows,_=inventory(db,p)
    if row is None:raise HTTPException(404,'质量审核不存在，或不在当前工作区')
    need(any(item is row for item in rows));return row


def review_pin(row):
    return {key:deepcopy(getattr(row,key)) for key in ('id','version','record_hash','approval_hash')}


def lock_nonce(db,p,nonce):
    key=hash_value({'schema':'threshold-quality-nonce-v152','workspace_id':p.workspace_id,'actor_id':p.user_id,'request_id':nonce})
    db.execute(select(func.pg_advisory_xact_lock(int(key[:15],16))))


def _source(function,*args,**kwargs):
    try:return function(*args,**kwargs)
    except HTTPException as error:
        if error.status_code not in {403,404,409,422}:raise
        raise QualitySourceError() from error


def _material(db,p,ref,lock=False):
    from .evidence import evidence_bytes
    query=select(SetupMaterial).where(SetupMaterial.workspace_id==p.workspace_id,SetupMaterial.id==ref['material_id'])
    if lock:query=query.with_for_update(read=True)
    row=db.scalar(query.execution_options(populate_existing=True))
    need(row is not None and not row.archived and row.sha256==ref['sha256'],'原件已变化或不可读取，不能用于新的批准')
    evidence,_=evidence_bytes(db,p,row.evidence_id)
    need(evidence.sha256==row.sha256,'原件内容与登记摘要不一致，不能用于新的批准')
    return {'material_id':row.id,'evidence_id':evidence.id,'sha256':row.sha256,'locator':ref['locator']}


def materials(db,p):
    rows=list(db.scalars(select(SetupMaterial).where(SetupMaterial.workspace_id==p.workspace_id,
        SetupMaterial.archived.is_(False)).order_by(SetupMaterial.id).limit(201).execution_options(populate_existing=True)))
    result=[]
    for row in rows[:200]:
        try:_material(db,p,{'material_id':row.id,'sha256':row.sha256,'locator':'原件目录'})
        except HTTPException as error:
            if error.status_code not in {403,404,409,422}:raise
            continue
        result.append({'material_id':row.id,'name':row.name,'sha256':row.sha256,
            'content_path':'/api/setup/materials/'+row.id+'/content'})
    return result,len(rows)>200


def _latest_feedback_chain(db,p,basis,lock=False):
    """Return this lookup's checked chain as well as its submitted head."""
    from . import threshold_evolution_api_v147 as feedback
    from .threshold_feedback_history_v148 import inventory_chain
    root=feedback._root_for_basis(db,p,basis)
    if root is None:return [],None
    rows=inventory_chain(db,p,root,lock)
    submitted=[row for row in rows if row.status=='submitted']
    return rows,submitted[-1] if submitted else None


def _latest_feedback(db,p,basis,lock=False):
    _,latest=_latest_feedback_chain(db,p,basis,lock)
    return latest


def _sample_summary(db,p,group,basis,feedback,scope):
    observation=scoped(db,Observation,basis['observation_id'],p)
    from .threshold_study_views_v149 import _entity_label
    metadata=basis['proof']['extractor']['manifest']['specification']['managed_numeric_rule']
    name=_entity_label(db,p,basis['entity_id'],metadata['package_id'])
    return {'group':group,'observation_id':observation.id,'observation_version':observation.version,
        'instance_id':basis['instance_id'],'baseline_capability_id':basis['baseline_capability_id'],
        # The checked SQL instant is display-only. Keep the original lexical
        # clock in its frozen proof/wire; explicit unknown stays unknown.
        'observed_at':observation.observed_at.isoformat() if observation.observed_at is not None else None,
        'entity_id':basis['entity_id'],
        'entity_name':name,'field':basis['field'],'field_label':basis['field_label'],'unit':basis['unit'],
        'value':deepcopy(basis['actual_value']),'feedback_id':feedback.id if feedback else None,
        'feedback_version':feedback.version if feedback else None,'label':feedback.data['label'] if feedback else None,
        'reason':feedback.data['reason'] if feedback else '',
        'eligible':feedback is not None and feedback.data['label'] in {'should_alert','should_not_alert'},
        'issue':None if feedback and feedback.data['label'] in {'should_alert','should_not_alert'} else '请提交明确的人工判断；未知不能计作通过'}


def _counts(rows,side):
    result={key:0 for key in ('tp','tn','fp','fn','unknown','alerts','total')}
    for row in rows:
        result['total']+=1;matched=row[side+'_matched'];label=row['label']
        if type(matched) is bool and matched:result['alerts']+=1
        if type(matched) is not bool or label not in {'should_alert','should_not_alert'}:result['unknown']+=1;continue
        expected=label=='should_alert';result['tp' if expected and matched else 'fn' if expected else 'fp' if matched else 'tn']+=1
    return result


def _statistics(groups,criteria):
    result={};issues=[];improved=False
    for group,rows in groups.items():
        baseline=_counts(rows,'baseline');candidate=_counts(rows,'candidate');local=[]
        if not rows:local.append('此组没有完整样本，不能以空集合通过')
        if baseline['unknown'] or candidate['unknown']:local.append('此组仍有未知、缺标签或未完成调用')
        if candidate['fn']>baseline['fn']:local.append('候选漏报增加')
        if max(0,candidate['fp']-baseline['fp'])>criteria['max_additional_reviews']:local.append('候选额外人工核查超过冻结上限')
        improved=improved or candidate['fn']<baseline['fn'] or candidate['fp']<baseline['fp']
        result[group]={'baseline':baseline,'candidate':candidate,'issues':local};issues.extend(local)
    if not improved:issues.append('尚无明确的误报或漏报改善，不能批准')
    if sum(max(0,result[group]['candidate']['fp']-result[group]['baseline']['fp']) for group in groups)>criteria['max_additional_reviews']:
        issues.append('两组累计额外人工核查超过冻结上限')
    result['issues']=list(dict.fromkeys(issues));return result


def evidence_context(db,p,evaluation_id,shadow_id,*,lock=False):
    """All actual comparisons and labels, not a client-supplied assessment."""
    from . import threshold_study_store_v149 as study, threshold_shadow_store_v151 as shadow
    from .threshold_shadow_execution_v151 import consumption_inventory, _snapshot as consumption_snapshot
    from .threshold_shadow_models_v151 import ThresholdShadowConsumption as Consumption
    from .threshold_feedback_history_v148 import require_current_basis
    from .threshold_evolution_api_v147 import _snapshot as feedback_snapshot
    if lock:
        # Approval keeps the original evaluation/Job/grant/resource lock order.
        evaluation=study.get_record(db,p,'evaluation',evaluation_id,lock)
        need(evaluation.status=='completed' and evaluation.data['result'] is not None
            and evaluation.data['result'].get('mechanism_gate_passed') is True,'请先完成真实比较并通过机制门')
        job=scoped(db,Job,evaluation.data['job_id'],p);need(job.status=='succeeded','原比较后台尚未成功完成')
        grant=shadow.get_grant(db,p,shadow_id,lock,check_consumptions=False)
        need(grant.evaluation_id==evaluation.id and grant.status=='ended','请选择同一评估已永久结束的影子观察')
        shadow.fresh_grant(db,p,grant)
    else:
        grant=shadow.get_grant(db,p,shadow_id,False,check_consumptions=False)
        need(grant.evaluation_id==evaluation_id and grant.status=='ended','请选择同一评估已永久结束的影子观察')
        actual_grant,evaluation=shadow._current_grant(db,p,grant,False,_return_evaluation=True)
        need(actual_grant is grant and evaluation.workspace_id==p.workspace_id
            and evaluation.id==evaluation_id==grant.evaluation_id and grant.status=='ended')
        need(evaluation.status=='completed' and evaluation.data['result'] is not None
            and evaluation.data['result'].get('mechanism_gate_passed') is True,'请先完成真实比较并通过机制门')
        job=scoped(db,Job,evaluation.data['job_id'],p);need(job.status=='succeeded','原比较后台尚未成功完成')
    pin=shadow._pin(evaluation);need(same(pin,grant.data['evaluation']),'影子与原评估冻结结果不一致')
    consumption_views=consumption_inventory(db,p,grant)
    original=evaluation.data['freeze'];candidate=original['candidate'];scope=deepcopy(grant.data['scope'])
    consumed=list(db.scalars(select(Consumption).where(Consumption.workspace_id==p.workspace_id,
        Consumption.grant_id==grant.id).execution_options(populate_existing=True)))
    by_consumption={row.id:row for row in consumed};need(len(by_consumption)==len(consumption_views))
    training_obs={row['basis']['observation_id'] for row in candidate['training']}
    training_roots={row['feedback_snapshot']['root_id'] for row in candidate['training']}
    summaries=[];source_samples={'evaluation':[],'shadow':[]};groups={'evaluation':[],'shadow':[]};latest={};issues=[]
    seen=set();results={item['id']:item for item in evaluation.data['result']['per_sample']}
    raw_samples=[('evaluation',item['basis'],item,results[item['id']]) for item in original['samples']]
    for item in consumption_views:
        actual=by_consumption[item['id']];basis=actual.data['basis']
        need(type(basis) is dict,'影子输入尚无完整原处理依据')
        if item['status']!='completed' or item['job_status']!='succeeded' or item['late']:
            issues.append('所有已占用影子输入都须完成且不是停止后的晚回')
        raw_samples.append(('shadow',basis,{'id':actual.id,'record_hash':actual.record_hash,
            'snapshot':consumption_snapshot(actual)},item))
    if not consumption_views:issues.append('至少需要一条已实际完成的未来影子输入，空集合不能通过')
    for group,basis,original_sample,result in raw_samples:
        identity=basis['observation_id'];need(identity not in seen and identity not in training_obs,
            '原登记重复，或训练登记被计入质量审核')
        seen.add(identity);rows,label=_latest_feedback_chain(db,p,basis,lock)
        if label:
            need(label.root_id not in training_roots,'训练反馈根不能重复计入质量审核')
            require_current_basis(db,p,rows)
            need(label.data['basis']['basis_hash']==basis['basis_hash'],'标签与原实际处理依据不一致')
            latest[label.id]=label
        summary=_sample_summary(db,p,group,basis,label,scope);summaries.append(summary)
        source_samples[group].append({'original':deepcopy(original_sample),
            'feedback':feedback_snapshot(label) if label else None})
        groups[group].append({'label':label.data['label'] if label else None,
            'baseline_matched':result['baseline_matched'],'candidate_matched':result['candidate_matched']})
    need(len(summaries)<=60,'质量审核最多覆盖60条原评估及影子输入')
    context={'purpose':original['purpose'],'scope':scope,'baseline_manifest':deepcopy(candidate['baseline_manifest']),
        'candidate_manifest':deepcopy(candidate['candidate_manifest']),'evaluation':pin,
        'shadow':{'id':grant.id,'version':grant.version,'record_hash':grant.record_hash},'samples':summaries}
    statistics=_statistics(groups,original['criteria']);issues.extend(statistics['issues'])
    return {'context':context,'evaluation':evaluation,'shadow':grant,'latest':latest,'samples':source_samples,
        'statistics':statistics,'issues':list(dict.fromkeys(issues))}


def validate_refs(db,p,draft,context,*,lock=False):
    """Every provided draft reference is real; missing references remain drafts."""
    from .threshold_evolution_models_v147 import FeedbackRevision
    from .threshold_feedback_history_v148 import inventory_chain
    allowed={sample['observation_id']:sample for sample in context['samples']};roots=set();result=[]
    for ref in draft['label_sources']:
        row=scoped(db,FeedbackRevision,ref['feedback_id'],p)
        rows=inventory_chain(db,p,row.root_id,lock);row=next((r for r in rows if r.id==row.id),None)
        need(row is not None and row.version==ref['expected_version'] and row.observation_id in allowed
            and row.root_id not in roots,'标签引用必须来自此次原评估或影子输入，且版本不重复')
        need(row.baseline_capability_id==allowed[row.observation_id]['baseline_capability_id']
            and row.rule_id==context['scope']['rule_id'],'标签必须属于原样本实际基线和固定规则')
        roots.add(row.root_id)
        result.append({'feedback_id':row.id,'expected_version':row.version,
            'material_refs':[_material(db,p,material,lock) for material in ref['material_refs']]})
    return result


def freeze_data(db,p,row,*,lock=True):
    actual=_source(evidence_context,db,p,row.evaluation_id,row.shadow_id,lock=lock)
    context=actual['context'];draft=row.data['draft']
    need(same(context['evaluation'],row.data['context']['evaluation'])
        and same(context['shadow'],row.data['context']['shadow']),'原评估或影子版本已变化，请保留草稿并重新核对')
    refs=_source(validate_refs,db,p,draft,context,lock=lock)
    required=[sample['feedback_id'] for sample in context['samples']]
    issues=list(actual['issues'])
    if any(value is None for value in required):issues.append('原评估及全部影子输入都须有最新已提交的明确人工判断')
    else:issues.extend(approval_issues(draft,context['purpose'],required))
    expected={r['feedback_id']:r['expected_version'] for r in draft['label_sources']}
    if any(expected.get(identity)!=record.version for identity,record in actual['latest'].items()):
        issues.append('标签必须逐项引用最新已提交版本；不能用旧改判或草稿替代')
    return {'schema':'threshold-quality-freeze-v152','purpose':context['purpose'],
        'scope':deepcopy(row.data['context']['scope']),'criteria':deepcopy(actual['evaluation'].data['freeze']['criteria']),
        'evaluation':deepcopy(context['evaluation']),'shadow':deepcopy(context['shadow']),
        'baseline_manifest':deepcopy(context['baseline_manifest']),'candidate_manifest':deepcopy(context['candidate_manifest']),
        'declarations':deepcopy(draft),'evaluation_samples':actual['samples']['evaluation'],
        'shadow_samples':actual['samples']['shadow'],'label_sources':refs,
        'statistics':actual['statistics'],'approved_by':p.user_id},list(dict.fromkeys(issues)),actual


def require_approved_review(db,p,identity,lock=False):
    row=get_review(db,p,identity,lock)
    need(row.status=='approved','质量审核未批准或已经撤回，不能据此批准新的策略')
    from .threshold_shadow_store_v151 import _approver
    _approver(db,p,row.approved_by,True)
    # The saved approving actor is part of the freeze; the current requester
    # may be a different administrator, without re-signing that human record.
    frozen,issues,_=freeze_data(db,p,row,lock=True)
    frozen['approved_by']=row.approved_by
    need(not issues,'；'.join(issues))
    need(same(frozen,row.data['frozen']) and hash_value(frozen)==row.approval_hash,
        '原质量批准的评估、影子、标签或原件已变化；原记录保留')
    return row,deepcopy(row.data['frozen']['scope'])


def _rules(context):
    from .threshold_study_views_v149 import rule_summary
    scope=context['scope']
    return (rule_summary(scope['baseline_id'],context['baseline_manifest'],scope['field_label']),
        rule_summary(scope['candidate_capability_id'],context['candidate_manifest'],scope['field_label']))


def _human_history(commands,identity):
    result=[]
    for row in sorted((c for c in commands if c.target_id==identity and c.outcome=='saved'),
        key=lambda c:c.data['result_snapshot']['version']):
        body=read_command(row.data['wire_text'].encode('utf-8'))
        result.append({'version':row.data['result_snapshot']['version'],'operation':row.operation,
            'reason':body.get('reason',body.get('sampling_statement','')),'actor_id':row.actor_id})
    return result


def view(db,p,row):
    row=get_review(db,p,row.id);_,commands=inventory(db,p)
    context=deepcopy(row.data['context']);baseline,candidate=_rules(context)
    samples=deepcopy(context['samples']);statistics=None;issues=[];current_issue=None;source_ok=True
    try:
        frozen,issues,actual=freeze_data(db,p,row,lock=False)
        samples=deepcopy(actual['context']['samples']);statistics=actual['statistics']
        if row.status in {'approved','revoked'}:
            frozen['approved_by']=row.approved_by
            need(same(frozen,row.data['frozen']),'原批准依据已变化')
        if row.status=='approved':
            from .threshold_shadow_store_v151 import _approver
            _approver(db,p,row.approved_by,False)
    except HTTPException as error:
        if error.status_code not in {403,404,409,422}:raise
        source_ok=False;current_issue=UNAVAILABLE;statistics=None
        context['scope']['baseline_threshold']=None;context['scope']['candidate_threshold']=None
        baseline['threshold']=None;candidate['threshold']=None
        for sample in samples:sample['value']=None;sample['eligible']=False;sample['issue']=UNAVAILABLE
    manage=p.role=='admin';edit=p.role in {'admin','operator'} and (manage or row.actor_id==p.user_id)
    return {'schema':'threshold-quality-review-view-v152','workspace_id':p.workspace_id,
        'id':row.id,'version':row.version,'actor_id':row.actor_id,'status':row.status,
        'evaluation_id':row.evaluation_id,'evaluation_version':context['evaluation']['version'],
        'shadow_id':row.shadow_id,'shadow_version':context['shadow']['version'],'purpose':context['purpose'],
        **{key:deepcopy(row.data['draft'][key]) for key in ('title','scope_statement','sampling_statement','limitations',
            'unresolved_items','representative_confirmation','label_sources')},
        'scope':context['scope'],'baseline':baseline,'candidate':candidate,'samples':samples,'statistics':statistics,
        'approval_issues':issues,'current_issue':current_issue,'approval_hash':row.approval_hash,'approved_by':row.approved_by,
        'production_eligible':row.status=='approved' and source_ok and not issues and context['purpose']=='business_assessment',
        'can_edit':edit and row.status=='draft','can_approve':manage and row.status=='draft' and source_ok and not issues,
        'can_reject':manage and row.status=='draft','can_revoke':manage and row.status=='approved',
        'human_history':_human_history(commands,row.id)}


def options(db,p,evaluation_id,shadow_id=None):
    from . import threshold_shadow_store_v151 as shadow
    result={'schema':'threshold-quality-options-v152','workspace_id':p.workspace_id,'evaluation_id':evaluation_id,
        'evaluation_version':None,'purpose':None,'scope':None,'baseline':None,'candidate':None,'shadows':[],
        'selected_shadow_id':shadow_id,'shadow_version':None,'samples':[],'materials':[],'materials_has_more':False,
        'current_issue':None,'can_create':False}
    try:
        evaluation,scope,pin=shadow.evaluation_context(db,p,evaluation_id)
        context={'scope':scope,'baseline_manifest':pin['freeze']['candidate']['baseline_manifest'],
            'candidate_manifest':pin['freeze']['candidate']['candidate_manifest']}
        baseline,candidate=_rules(context)
        result.update(evaluation_version=evaluation.version,purpose=evaluation.data['freeze']['purpose'],
            scope=scope,baseline=baseline,candidate=candidate)
        grants,_=shadow.inventory(db,p)
        from .threshold_shadow_execution_v151 import consumption_inventory
        for grant in sorted((r for r in grants if r.evaluation_id==evaluation_id),key=lambda r:r.id):
            rows=consumption_inventory(db,p,grant);completed=sum(r['status']=='completed' and r['job_status']=='succeeded'
                and not r['late'] for r in rows)
            eligible=grant.status=='ended' and bool(rows) and completed==len(rows)
            result['shadows'].append({'id':grant.id,'version':grant.version,'status':grant.status,
                'title':grant.data['title'],'reason':grant.data['reason'],'used_inputs':len(rows),
                'completed_inputs':completed,'eligible':eligible,'issue':None if eligible else '须永久结束且所有已占用输入都有完整成功回执'})
        if shadow_id:
            actual=evidence_context(db,p,evaluation_id,shadow_id)
            result.update(shadow_version=actual['shadow'].version,samples=actual['context']['samples'],
                can_create=p.role in {'admin','operator'})
        else:result['current_issue']='请选择同一评估已结束的影子观察；草稿和质量批准不等于策略授权'
        result['materials'],result['materials_has_more']=materials(db,p)
    except HTTPException as error:
        if error.status_code not in {403,404,409,422}:raise
        result['current_issue']=UNAVAILABLE;result['can_create']=False
        if result['scope'] is not None:
            result['scope']['baseline_threshold']=None;result['scope']['candidate_threshold']=None
            result['baseline']['threshold']=None;result['candidate']['threshold']=None
        for sample in result['samples']:sample['value']=None;sample['eligible']=False;sample['issue']=UNAVAILABLE
    return result
