"""服务器维护的流程/自动化确认边界；自由触发说明永远不是执行授权。"""
import copy,re
from .contracts import digest
from .instance_compiler import names,flow_for,signature_value,identifier

ACTIONS=('observe','state','data','supplement','write','learn')
REVIEW_REASON='业务流程或自动化规则已变化，请回第4步核对自动化范围并明确确认；原规则保留。'


def policy_for(config,name):
    seen=set();automation=config.get('automation')
    policies=automation.get('policies',{}) if isinstance(automation,dict) else {}
    if not isinstance(policies,dict):return None
    while name and name not in seen:
        seen.add(name)
        if name in policies:return policies[name]
        name=config.get('businessAliases',{}).get(name)
    return None


def flow_hash(config,name):
    flow=flow_for(config,name);value=copy.deepcopy(signature_value(config,flow))
    steps=value.get('steps') or [];ids={s.get('id'):str(n) for n,s in enumerate(steps) if s.get('id')}
    for step in steps:
        step.pop('id',None)
        for key in ['nextId','elseId']:
            if key in step:step[key]=ids.get(step[key],step[key])
    return digest(value)


def policy_hash(policy):
    return digest({k:v for k,v in policy.items() if k!='flow_review'})


def safe_policy():
    return {'triggers':['人工发起'],'time':'08:00','timezone':'Asia/Shanghai','budgetEnabled':False,'budget':50,
        'rules':{action:{'mode':'仅给出建议','resources':[],'role':'','scope':'','cap':'','limitsEnabled':False,
            'limit':10,'cooldown':10,'receipt':True,'cancel':True,'idempotent':True} for action in ACTIONS}}


def conservative(policy):
    return (isinstance(policy,dict) and policy.get('triggers')==['人工发起']
        and isinstance(policy.get('rules'),dict) and set(policy['rules'])==set(ACTIONS)
        and all(isinstance(r,dict) and r.get('mode')=='仅给出建议' and not r.get('resources') for r in policy['rules'].values()))


def metadata(config,name,policy,status,actor_id=None,confirmed_at=None):
    return {'version':1,'status':status,'flow_hash':flow_hash(config,name),'policy_hash':policy_hash(policy),
        'reason':REVIEW_REASON if status=='needs_review' else '默认仅人工发起，全部动作仅给出建议。' if status=='conservative_safe' else '已明确核对当前流程与自动化范围。',
        'actor_id':actor_id,'confirmed_at':confirmed_at}


def review_state(config,name):
    policy=policy_for(config,name);fh=flow_hash(config,name)
    base={'business_id':identifier('business',name),'business_name':name,'flow_hash':fh,'policy_hash':policy_hash(policy) if isinstance(policy,dict) else None}
    if not isinstance(policy,dict) or not isinstance(policy.get('rules'),dict):
        return {**base,'status':'missing','reason':'尚未保存此业务的自动化范围，请回第4步配置。'}
    m=policy.get('flow_review')
    if m is None:return {**base,'status':'legacy_reviewed','reason':'原有自动化范围保留；流程或规则修改后需重新核对。'}
    valid=(isinstance(m,dict) and set(m)=={'version','status','flow_hash','policy_hash','reason','actor_id','confirmed_at'}
        and type(m.get('version')) is int and m['version']==1 and m.get('status') in {'needs_review','confirmed','conservative_safe'}
        and m.get('flow_hash')==fh and m.get('policy_hash')==base['policy_hash'])
    if not valid or m['status']=='needs_review':return {**base,'status':'needs_review','reason':REVIEW_REASON}
    if m['status']=='conservative_safe' and not conservative(policy):
        return {**base,'status':'needs_review','reason':REVIEW_REASON}
    return {**base,'status':m['status'],'reason':m['reason']}


def review_summary(config):
    return {'version':1,'businesses':[review_state(config,name) for name in names(config)]}


def synchronize_reviews(previous,incoming):
    """忽略客户端确认元数据，只有服务器原确认且语义未变才可保留。"""
    out=copy.deepcopy(incoming);automation=out.get('automation')
    current=names(out)
    old_names=names(previous)
    if not isinstance(automation,dict) or not isinstance(automation.get('policies'),dict):
        if not current:return out
        automation={'version':1,'policies':{}};out['automation']=automation
    for policy in automation['policies'].values():
        if isinstance(policy,dict):policy.pop('flow_review',None)
    for name in current:
        policy=policy_for(out,name)
        original=name if name in old_names else out.get('businessAliases',{}).get(name,name)
        old=policy_for(previous,original)
        if not isinstance(policy,dict):
            if isinstance(old,dict):
                policy={'rules':{}};policy['flow_review']=metadata(out,name,policy,'needs_review')
                automation['policies'][name]=policy
            elif original not in old_names:
                policy=safe_policy();policy['flow_review']=metadata(out,name,policy,'conservative_safe')
                automation['policies'][name]=policy
            continue
        if not isinstance(old,dict):
            policy=copy.deepcopy(policy);policy['flow_review']=metadata(out,name,policy,'conservative_safe' if conservative(policy) else 'needs_review')
            automation['policies'][name]=policy
            continue
        clean=copy.deepcopy(policy);changed=(original!=name or original not in old_names
            or flow_hash(previous,original)!=flow_hash(out,name) or policy_hash(old)!=policy_hash(clean))
        old_review=old.get('flow_review')
        if changed:clean['flow_review']=metadata(out,name,clean,'needs_review')
        elif old_review is not None:clean['flow_review']=copy.deepcopy(old_review)
        automation['policies'][name]=clean
    # 当前移除不恢复旧规则；完整旧人稿保存在 SetupRevision，现有旁支记录仅保留服务器元数据。
    current=set(current)
    for name,policy in automation['policies'].items():
        old=policy_for(previous,name)
        if name not in current and isinstance(old,dict) and isinstance(policy,dict) and old.get('flow_review') is not None:
            policy['flow_review']=copy.deepcopy(old['flow_review'])
    return out


def applied_policies(previous,value,chosen):
    out=synchronize_reviews(previous,value)
    for business in chosen:
        if business['operation']!='upsert':continue
        name=business['name'];original=business['original_name'] or name
        policy=policy_for(previous,original) or policy_for(previous,name)
        if policy is not None:continue
        if original in names(previous):continue
        out.setdefault('automation',{'version':1,'policies':{}}).setdefault('policies',{})[name]=safe_policy()
        p=out['automation']['policies'][name];p['flow_review']=metadata(out,name,p,'conservative_safe')
    return out
