"""Human-reviewed config recovery and paid proposal reuse, with no cloud calls."""
import copy
from uuid import NAMESPACE_URL,uuid5
from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from .contracts import Contract, digest
from .models import SetupMaterial, SetupRevision, SetupAnalysis, uid, now
from .setup_api import router, P, DB, draft, draft_view, material_view, validate_configuration
from .services import scoped, audit, check_version
from .security import require
from .evidence import evidence_bytes


class RestoreRequest(Contract):
    revision: int = Field(ge=1)
    target_revision: int = Field(ge=1)


class RestoreConfirm(RestoreRequest):
    preview_hash: str


class ReuseRequest(Contract):
    version: int = Field(ge=1)
    revision: int = Field(ge=1)


def history_row(db,p,revision):
    row=db.scalar(select(SetupRevision).where(SetupRevision.workspace_id==p.workspace_id,
        SetupRevision.version==revision))
    if not row:raise HTTPException(404,'所选配置历史不存在')
    return row


def recovery_plan(db,p,body):
    config=draft(db,p,True);check_version(config,body.revision)
    historical=history_row(db,p,body.target_revision)
    if not historical.data:raise HTTPException(409,'初始空配置不能作为恢复目标')
    value=copy.deepcopy(historical.data);validate_configuration(value)
    metadata={};warnings=[];material_changes=[];changed_sources=set()
    old_materials=value.get('materials',[])
    for old in old_materials:
        if not isinstance(old,dict) or not old.get('id'):raise HTTPException(409,'旧版本资料引用不完整')
        m=scoped(db,SetupMaterial,old['id'],p,True)
        if old.get('hash')!=m.sha256:raise HTTPException(409,'旧版本原件摘要不匹配，恢复已阻止')
        # Detect missing/corrupt originals before asking a human to approve the proposal.
        ev,_=evidence_bytes(db,p,m.evidence_id)
        if ev.sha256!=m.sha256:raise HTTPException(409,'原件引用与摘要不匹配，恢复已阻止')
        wanted={'note':str(old.get('note',''))[:5000],'kind':str(old.get('kind','业务资料'))[:100],
                'archived':bool(old.get('archived',False))}
        metadata[m.id]=wanted
        current=material_view(m,db)
        if any(current[k]!=wanted[k] for k in wanted):
            material_changes.append({'id':m.id,'name':m.name,'before':{k:current[k] for k in wanted},'after':wanted})
            changed_sources.add(m.id)
        if old.get('version')!=m.version or m.status!='parsed':changed_sources.add(m.id)
        if m.status!='parsed':warnings.append(m.name+'原件保留，但解析尚未完成或该模态未支持')
    rows=db.scalars(select(SetupMaterial).where(SetupMaterial.workspace_id==p.workspace_id)
        .order_by(SetupMaterial.created_at,SetupMaterial.id).with_for_update()).all()
    # Newer original files remain accessible; they are not quietly included in older business logic.
    retained=[];material_values=[]
    for m in rows:
        view=material_view(m,db)
        if m.id in metadata:view.update(metadata[m.id])
        else:
            view['archived']=True
            retained.append({'id':m.id,'name':m.name,'was_archived':m.archived})
        material_values.append(view)
    value['materials']=material_values
    confirmed=[name for name,f in value.get('flows',{}).items() if f.get('confirmedSignature')]
    from .flow_confirmation import synchronize_flow_confirmations
    value=synchronize_flow_confirmations(historical.data,value,changed_sources=changed_sources)
    reconfirm=[name for name in confirmed if not value['flows'][name].get('confirmedSignature')]
    # Historical device/connector checks are not proofs that a service still works now.
    for c in value['connections']:
        c['status']='pending';c['lastError']=None
    for r in value['resources']:
        r['status']='candidate';r['checks']={};r['lastError']=None
    # 历史确认只证明历史状态；预览必须展示相对当前配置重新计算后的复核边界。
    from .setup_automation import synchronize_reviews
    value=synchronize_reviews(config.data,value)
    from .report_configuration_handoff import protect_configuration_sources
    value=protect_configuration_sources(db,p,config.data,value,restore=True)
    reconfirm=list(dict.fromkeys([*reconfirm,*[name for name in confirmed if not value['flows'][name].get('confirmedSignature')]]))
    from .setup_understanding import names
    before_names=names(config.data);after_names=names(value)
    plan={'current_revision':config.version,'target_revision':historical.version,
        'configuration':value,'configuration_hash':digest(value),'current_hash':digest(config.data),
        'history_hash':digest(historical.data),'material_changes':material_changes,'retained_newer_materials':retained,
        'material_snapshots':[{'id':m.id,'version':m.version,'hash':m.sha256,'status':m.status,
                               'blocks_hash':digest(m.data.get('blocks',[]))} for m in rows],
        'businesses':{'added':[n for n in after_names if n not in before_names],
            'removed':[n for n in before_names if n not in after_names],
            'changed':[n for n in after_names if n in before_names and value.get('flows',{}).get(n)!=config.data.get('flows',{}).get(n)]},
        'profile_before':config.data.get('instanceProfile',{}),'profile_after':value.get('instanceProfile',{}),
        'flows_requiring_confirmation':reconfirm,'connections_requiring_validation':len(value['connections']),
        'resources_requiring_validation':len(value['resources']),'warnings':warnings}
    plan['preview_hash']=digest(plan)
    return config,plan


@router.post('/draft/restore/preview')
def preview_restore(body:RestoreRequest,p:P,db:DB):
    require(p,'admin','operator');_,plan=recovery_plan(db,p,body);db.commit();return plan


@router.post('/draft/restore')
def restore_draft(body:RestoreConfirm,p:P,db:DB):
    require(p,'admin','operator');config,plan=recovery_plan(db,p,body)
    if plan['preview_hash']!=body.preview_hash:raise HTTPException(409,'恢复范围或资料已变化，请重新核对')
    db.execute(insert(SetupRevision).values(id=uid(),workspace_id=p.workspace_id,draft_id=config.id,
        version=config.version,data=config.data).on_conflict_do_nothing(index_elements=['draft_id','version']))
    for record in plan['configuration']['materials']:
        m=scoped(db,SetupMaterial,record['id'],p,True)
        wanted={k:record[k] for k in ['note','kind']};archived=record['archived']
        if m.archived!=archived or any(m.data.get(k,'' if k=='note' else '业务资料')!=v for k,v in wanted.items()):
            m.archived=archived;m.data={**m.data,**wanted};m.version+=1
            record['version']=m.version
    from .setup_automation import synchronize_reviews
    value=synchronize_reviews(config.data,plan['configuration']);validate_configuration(value)
    from .setup_automation_instruction_guard import protect_automation_instruction_transition
    protect_automation_instruction_transition(config.data,value,restore=True)
    from .report_configuration_handoff import protect_configuration_sources
    value=protect_configuration_sources(db,p,config.data,value,restore=True);validate_configuration(value)
    config.data=value;config.version+=1
    db.add(SetupRevision(workspace_id=p.workspace_id,draft_id=config.id,version=config.version,data=config.data))
    audit(db,p,'setup.restore',config.id,{'from_revision':body.revision,'target_revision':body.target_revision,
        'new_revision':config.version,'preview_hash':body.preview_hash,'reconfirm':plan['flows_requiring_confirmation']})
    db.commit();return draft_view(config)


def core(value,include_objects=False,include_relationships=False):
    keys=['instanceProfile','businesses','flows','businessAliases']
    if include_objects:keys+=['instanceObjects','instanceObjectOrigins']
    if include_relationships:keys+=['instanceRelationTypes','instanceRelationOrigins']
    return {k:copy.deepcopy(value.get(k,{} if k in {'instanceProfile','flows','businessAliases','instanceObjectOrigins','instanceRelationOrigins'} else [])) for k in keys}


@router.post('/understanding/{object_id}/reuse',status_code=201)
def reuse_analysis(object_id:str,body:ReuseRequest,p:P,db:DB):
    require(p,'admin','operator')
    source=scoped(db,SetupAnalysis,object_id,p,True);check_version(source,body.version)
    if source.status not in {'review','applied','undone','cancelled'} or not source.data.get('result'):
        raise HTTPException(409,'该记录没有可复用的完整建议')
    req=source.data['request']
    if req.get('contract_version')==6:raise HTTPException(409,'字段修改不能复用为整业务建议；请基于当前配置核对新修改范围')
    if len(source.data.get('completed',[]))!=len(req['chunks']):raise HTTPException(409,'尚未处理全部资料，不能复用部分结果')
    config=draft(db,p,True);check_version(config,body.revision)
    expected=source.data.get('before_configuration')
    if expected is not None:
        same=digest(core(config.data,req.get('contract_version') in {2,3,4,5},req.get('contract_version')==5))==digest(core(expected,req.get('contract_version') in {2,3,4,5},req.get('contract_version')==5))
    else:
        guard=source.data.get('local_reuse_guard',req)
        same=digest(config.data)==guard.get('configuration_hash')
    if not same:raise HTTPException(409,'原业务或流程已有其他修改，不能直接复用；请先核对或恢复对应历史版本')
    from .setup_understanding import model,validate_result,view,request_contract,verified_agent_aliases
    from .local_proposal_review_submissions_v261 import verify_origin
    local_proof=verify_origin(db,p,source)
    alias_proof=verified_agent_aliases(db,p,source) if local_proof is None else None
    contract=request_contract(req)
    cap=model(db,p,req['model_id'])
    if cap.content_hash!=req['model_hash']:raise HTTPException(409,'原模型版本或权限已变化，不能自动复用')
    for old in req['materials']:
        m=scoped(db,SetupMaterial,old['id'],p,True)
        if m.archived or m.status!='parsed' or m.version!=old['version'] or m.sha256!=old['hash'] or digest(m.data.get('blocks'))!=old['blocks_hash']:
            raise HTTPException(409,'原建议资料或解析结果已变化，须重新核对')
    if req.get('contract_version') in {3,4,5}:
        from .setup_understanding import checked_v3_request, frozen_material
        snapshot={'version':'setup-source-snapshot-v1','materials':[frozen_material(db,p,scoped(db,SetupMaterial,m['id'],p,True)) for m in req['materials']]}
        checked_v3_request(req,cap,snapshot)
    result=source.data['result']
    if digest(result)!=source.data.get('result_hash'):raise HTTPException(409,'原建议检查点已变化，不能复用')
    try:validate_result(result,[b for chunk in req['chunks'] for b in chunk],config.data,contract,request=req)
    except ValueError:raise HTTPException(409,'原建议结构或引用核查未通过，不能复用')
    row=SetupAnalysis(workspace_id=p.workspace_id,status='review',data={
        'request':copy.deepcopy(req),'consent':copy.deepcopy(source.data['consent']),
        'result':copy.deepcopy(result),'result_hash':source.data['result_hash'],
        'completed':list(source.data['completed']),'receipts':[],
        'reused_from':source.id,'original_cloud_source':source.data.get('original_cloud_source',source.id),
        'local_reuse_guard':{'revision':config.version,'configuration_hash':digest(config.data)},
        'review_selection':{'names':source.data.get('selected_names',source.data.get('review_selection',{}).get('names',[])),
                            'accept_domain':source.data.get('selected_domain',False),
                            **({'object_names':source.data.get('selected_object_names',source.data.get('review_selection',{}).get('object_names',[]))} if req.get('contract_version') in {2,3,4,5} else {})},'error':None})
    if req.get('contract_version') in {4,5}:
        row.data={**row.data,'citation_normalizations':copy.deepcopy(source.data.get('citation_normalizations',[]))}
    if alias_proof is not None:
        row.data={**row.data,'agent_alias_normalizations':copy.deepcopy(alias_proof[0]),
                  'agent_alias_normalization_basis':copy.deepcopy(alias_proof[1])}
    if local_proof is not None:
        row.id=str(uuid5(NAMESPACE_URL,'generic-agents/local-review-reuse-v261/'+uid()))
        row.data={**row.data,'local_review_provenance':copy.deepcopy(local_proof),
                  'agent_alias_normalizations':copy.deepcopy(source.data.get('agent_alias_normalizations',[]))}
    db.add(row);db.flush();audit(db,p,'setup.understanding.reuse',row.id,{'source_id':source.id,'revision':config.version,
        'no_cloud_call':True});db.commit();return view(row)
