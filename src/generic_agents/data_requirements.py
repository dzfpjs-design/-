"""Scoped time/verification-aware evidence selection; excluded values never reach tool inputs."""
from datetime import datetime
from sqlalchemy import select
from .models import Entity, EntityEvent, Observation, now
from .services import scoped
from .contracts import EvidenceRequirement, digest
from .temporal import resolve, matches, instant, question_time
from fastapi import HTTPException
from pydantic import ValidationError
import re


def prepare_request(db,p,request,*,reference=None,_confirmed_plan=False,task=None,lock_sources=True):
    # Internal adapters may already hold a currently authorized, confirmed
    # structural plan. Do not guess its intent again from field names or
    # negations in the original question. This is not a DataRequest field and
    # skips neither scope/tool validation nor evidence/time preparation.
    value=dict(request);reference=reference or now();questions=[]
    if value.get('relation_query'):
        from .relations import validate_query
        value['relation_query']=validate_query(db,p,value['relation_query'],value['entity_ids'])
    from .query_needs import relation_intent,unsupported_intent
    if _confirmed_plan is not True and value['mode']=='structured' and not value.get('relation_query') and relation_intent(value['question']):
        questions.append('问题涉及对象关系，请明确关系类型、起点对象和方向；字段齐全不能替代关系回答')
    from .data_tools import choices,validate_steps,managed_task_kwargs
    if value.get('tool_steps'):value['tool_steps']=validate_steps(db,p,value,value['tool_steps'],
        lock_sources=lock_sources,**managed_task_kwargs(task))
    if _confirmed_plan is not True:
        from .query_field_intent_v246 import context_for_request
        field_context=context_for_request(db,p,value)
        if unsupported_intent(value['question'],field_context=field_context) and not (value.get('tool_steps') or (
            value['mode']=='cloud' and choices(db,p,value['entity_ids'],lock_sources=lock_sources,**managed_task_kwargs(task)))):
            questions.append('当前查询尚不能回答聚合、统计或趋势，请修改问题或接入相应查询工具')
    requirement=value.get('evidence_requirement')
    hint=question_time(value['question'])
    tz=value.get('business_timezone')
    if not tz:
        from .models import Capability
        zones={scoped(db,Capability,scoped(db,Entity,x,p).schema_capability_id,p).data['specification'].get('timezone')
            for x in value['entity_ids']}
        if len(zones)==1 and None not in zones:tz=zones.pop()
    ambiguous_days = re.search(r'过去\s*\d+\s*天',value['question'])
    if not requirement and ambiguous_days:
        questions.append('过去若干天可能指滚动时长或业务日历日，请明确时间范围')
        hint=None
    if not requirement and not hint and re.search(r'去年|今年|上月|本月|\d{4}[-年]\d{1,2}|\d{1,2}月\d{1,2}',value['question']):
        questions.append('请在日期设置中明确起止日期或时点，当前不猜定此日期表达式')
    if requirement and hint and requirement.get('time',{}).get('mode','archive')=='archive':
        questions.append('问题包含日期，但证据设置为无时间要求；请明确查询日期')
    if not requirement and hint:
        requirement={'source':'observations','minimum_verification':'unverified_extraction',
            'time':{**hint,'timezone':tz}}
    if not requirement and any(word in value['question'] for word in ['当前','现在','最新','实时']):
        questions.append('需要实时状态时，请明确可接受时效和证据来源')
    window=None
    if requirement:
        try:
            requirement=EvidenceRequirement.model_validate(requirement).model_dump()
            if requirement['time']['timezone'] is None:requirement['time']['timezone']=tz
            window=resolve(requirement['time'],reference)
            if hint and not ambiguous_days and window['mode'] != 'archive':
                hinted=resolve({**hint,'timezone':tz or requirement['time']['timezone']},reference)
                if (window['start'],window['end']) != (hinted['start'],hinted['end']):
                    questions.append('问题中的业务日期与设置的时间范围不一致，请核对后查询')
        except (HTTPException,ValidationError) as error:
            questions.append(str(error.detail) if isinstance(error,HTTPException) else '时间或证据要求不符合范围限制，请核对设置')
    return {**value,'evidence_requirement':requirement,'resolved_time':window,
        'clarifications':questions,'reference_at':reference.isoformat()}

RANK = {'unverified_extraction': 0, 'recorded': 1, 'confirmed': 2}


def query(db, p, entity_ids, fields, requirement, *, fixed_window=None, learning=True, context='', route_key=None, include_trusted_cases=True, _goal_evaluation_cut=None):
    if _goal_evaluation_cut is not None:
        from .goal_evaluation_query_v288 import _query_clock, need
        need(learning is False and include_trusted_cases is False)
        evaluated, window = _query_clock(db,p,entity_ids,fields,requirement,fixed_window,route_key,_goal_evaluation_cut)
    from .instance_data_receipts import query_context,observation_receipt
    from .state_receipt_guard_v245 import check as reception_status
    query_context(db,p,entity_ids)
    spec = EvidenceRequirement.model_validate(requirement).model_dump()
    if _goal_evaluation_cut is None:
        evaluated = now()
        window = resolve(spec['time'], evaluated) if not fixed_window or spec['time']['mode'] == 'latest' else fixed_window
    entities = [scoped(db, Entity, identifier, p) for identifier in entity_ids]
    evidence, observations, excluded, gaps, conflicts = [], [], [], [], []
    for row in entities:
        admitted = []
        archive_allowed = spec['source'] in {'archive', 'either'}
        archive_reason = None
        if archive_allowed:
            if _goal_evaluation_cut is not None:
                from .goal_evaluation_query_v288 import _archive_at_cut
                if not _archive_at_cut(db,p,row,_goal_evaluation_cut):
                    archive_reason = '当前档案版本晚于本次固定核对时点，不能替代该时点已知资料'
            if archive_reason:
                pass
            elif RANK[spec['minimum_verification']] > RANK['recorded']:
                archive_reason = '档案没有所要求的确认记录'
            elif not matches(row.observed_at, window, evaluated):
                archive_reason = '档案观测时间缺失、在请求窗外或属于未来'
            elif row.valid_until and row.valid_until < (instant(window['at']) if window['mode']=='as_of' else
                    row.observed_at if window['mode']=='range' else evaluated):
                archive_reason = '档案有效期未覆盖所需时点'
            elif window['mode'] == 'as_of':
                changed = db.scalar(select(EntityEvent.id).where(EntityEvent.workspace_id == p.workspace_id,
                    EntityEvent.entity_id == row.id, EntityEvent.created_at > instant(window['at'])).limit(1))
                if changed or row.created_at > instant(window['at']):
                    archive_reason = '现存档案版本无法证明历史时点状态，需相应历史证据'
            if not archive_reason:
                item = {'entity_id':row.id,'version':row.version,
                    'attributes':{k:v for k,v in row.data.items() if not fields or k in fields},
                    'observed_at':row.observed_at.isoformat() if row.observed_at else None,
                    'valid_until':row.valid_until.isoformat() if row.valid_until else None,
                    'source':'resource_entity','verification':'recorded','observation_ids':[]}
                evidence.append(item); admitted.append(item)
            else:
                excluded.append({'entity_id':row.id,'source':'resource_entity','reason':archive_reason,
                    'observed_at':row.observed_at.isoformat() if row.observed_at else None})
        if spec['source'] in {'observations','either'}:
            from sqlalchemy import func
            unclocked=db.scalar(select(func.count()).select_from(Observation).where(
                Observation.workspace_id==p.workspace_id,Observation.entity_id==row.id,
                Observation.status=='extracted_unclocked'))
            if unclocked:
                excluded.append({'entity_id':row.id,'source':'unclocked_video_candidates','count':unclocked,
                    'reason':'视频来源拍摄时间未校准，候选信息不能用于指定时点的状态证据'})
            query = select(Observation).where(Observation.workspace_id == p.workspace_id,
                Observation.entity_id == row.id, Observation.status == 'processed', Observation.observed_at <= evaluated)
            if window['mode'] in {'range','latest'}:
                query = query.where(Observation.observed_at >= instant(window['start']))
                query = query.where(Observation.observed_at <= instant(window['end']) if window['mode']=='latest'
                    else Observation.observed_at < instant(window['end']))
            elif window['mode'] == 'as_of':
                query = query.where(Observation.observed_at <= instant(window['at']))
                if window['max_age_seconds']:
                    from datetime import timedelta
                    query=query.where(Observation.observed_at >= instant(window['at'])-timedelta(seconds=window['max_age_seconds']))
            rows = db.scalars(query.order_by(Observation.observed_at.desc(), Observation.id).limit(21)).all()
            truncated = len(rows) > 20
            for obs in rows[:20]:
                if obs.data.get('candidate_only') or obs.observed_at is None:
                    excluded.append({'entity_id':row.id,'observation_id':obs.id,'reason':'本地资料候选或未知采集时间不能作为现场状态字段'})
                    continue
                pending=reception_status(db,p,obs)
                if pending is not None and pending['status']=='waiting_data':
                    excluded.append({'entity_id':row.id,'observation_id':obs.id,
                        'status':'pending_data_reception','delivery_status':pending['delivery_status'],
                        'reason':'观测信息尚未由数据管理智能体独立接收，暂不作为有效资料'})
                    continue
                reception=observation_receipt(db,p,obs)
                knowledge = None
                if _goal_evaluation_cut is not None:
                    from .goal_evaluation_query_v288 import _observation_at_cut
                    knowledge = _observation_at_cut(db,p,obs,reception,_goal_evaluation_cut)
                    if knowledge is None:
                        excluded.append({'entity_id':row.id,'observation_id':obs.id,
                            'status':'not_received_at_evaluation_cut',
                            'reason':'固定核对时点前尚无可核对的独立收件记录；未把后到回执或观测当作当时事实'})
                        continue
                extracted = obs.data.get('extracted', {})
                verification = obs.data.get('human_verification', {})
                fingerprint = digest({'extracted':extracted,'evidence_ids':obs.data.get('evidence_ids',[]),
                    'invocation_id':obs.data.get('invocation_id')})
                decision = verification.get('decision') if verification.get('input_hash') == fingerprint else None
                rank = 'confirmed' if decision == 'confirmed' else 'unverified_extraction'
                if _goal_evaluation_cut is not None and rank == 'confirmed':
                    from .goal_evaluation_query_v288 import _confirmation_at_cut
                    if not _confirmation_at_cut(obs,verification,_goal_evaluation_cut):
                        excluded.append({'entity_id':row.id,'observation_id':obs.id,
                            'status':'confirmation_not_proven_at_evaluation_cut',
                            'reason':'人工确认时间未知或晚于固定核对时点，不能倒推当时已确认'})
                        continue
                if decision == 'rejected' or RANK[rank] < RANK[spec['minimum_verification']]:
                    excluded.append({'entity_id':row.id,'observation_id':obs.id,'reason':'观测被撤回或尚未达到所需确认等级'})
                    continue
                item = {'observation_id':obs.id,'entity_id':row.id,'version':obs.version,'source_id':obs.source_id,
                    'observed_at':obs.observed_at.isoformat(),'attributes':{k:v for k,v in extracted.get('attributes',{}).items() if not fields or k in fields},
                    'anomalies':extracted.get('anomalies',[]),'evidence_ids':obs.data.get('evidence_ids',[]),
                    'invocation_id':obs.data.get('invocation_id'),'verification':rank,'source':'processed_observation',
                    **reception, **({'goal_evaluation_knowledge_cut_v288':knowledge} if knowledge is not None else {})}
                observations.append(item); admitted.append(item)
            if truncated:
                gaps.append({'entity_id':row.id,'missing_fields':[],'stale':False,'route':'data',
                    'reason':'时间窗超过单对象20条读取上限，请缩小日期范围；结果不作为完整时间序列'})
        counts = {field:sum(item['attributes'].get(field) is not None for item in admitted) for field in fields}
        missing = [field for field,count in counts.items() if count < spec['minimum_records_per_entity']]
        if missing or len(admitted) < spec['minimum_records_per_entity']:
            gaps.append({'entity_id':row.id,'missing_fields':missing or fields,'stale':False,'route':'perception',
                'reason':'符合日期、来源及确认等级的证据不足','field_record_counts':counts})
        alternatives = {}
        for item in admitted:
            if not item.get('observed_at'): continue
            for field,value in item['attributes'].items():
                alternatives.setdefault((field,instant(item['observed_at']).isoformat()),[]).append((digest(value),item))
        for (field,at), values in alternatives.items():
            if len({v for v,_ in values}) > 1:
                conflicts.append({'entity_id':row.id,'field':field,'observed_at':at,
                    'observation_ids':[r['observation_id'] for _,r in values if 'observation_id' in r],
                    'reason':'同一时点的有效来源记录互相矛盾'})
    status = 'insufficient' if gaps or not (evidence or observations) else 'conflict' if conflicts else 'sufficient'
    from .learning import select_cases, eligible
    packages = {r.schema_capability_id for r in entities}
    if not include_trusted_cases:
        memories,decisions=[],[]
    elif learning:
        memories, decisions = select_cases(db,p,packages,fields,context,route_key,status,entity_ids)
    else:
        rows = [m for package in sorted(packages) for m in eligible(db,p,package)][:10]
        memories = [{'id':m.id,'text':m.text,'provenance':m.data} for m in rows]; decisions=[]
    return {'status':status,'evidence':evidence,'observation_evidence':observations,'gaps':gaps,'conflicts':conflicts,
        'excluded_evidence':excluded,'trusted_cases':memories,'learning_decisions':decisions,'evaluated_at':evaluated.isoformat(),
        'evidence_requirement':spec,'resolved_time':window}
