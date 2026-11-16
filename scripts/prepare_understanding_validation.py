"""Prepare isolated synthetic validation only; this does not call a cloud model."""
import json
import os
import secrets
from pathlib import Path
import httpx
from sqlalchemy.orm import Session
from generic_agents.db import engine, migrate
from generic_agents.models import User, Workspace, Membership
from generic_agents.security import password_hash

ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
access_path=ROOT/'.runtime/understanding-validation-access.json'
migrate()
if access_path.exists():access=json.loads(access_path.read_text())
else:
    access={'username':'understanding_validation_'+secrets.token_hex(4),'password':secrets.token_urlsafe(32)}
    with Session(engine()) as db:
        user=User(username=access['username'],password_hash=password_hash(access['password']))
        workspace=Workspace(name='资料理解合成验收（无真实业务资料）')
        db.add_all([user,workspace]);db.flush()
        db.add(Membership(workspace_id=workspace.id,user_id=user.id,role='admin'));db.commit()
        access['workspace_id']=workspace.id
    access_path.write_text(json.dumps(access));access_path.chmod(0o600)

sample='''合成验收资料：设备异常复核（虚构对象，无真实现场数据）。
领域：设备运维。管理对象是已登记的设备。
业务名称：设备异常复核。收到设备新采集数据时启动，最终产出带来源的已复核记录。
第一步由多模感知智能体读取并检查新采集数据，识别异常，输出异常线索和原始观测引用。
发现异常须立即报告业务智能体。业务智能体核对目标并安排人工复核，不能自动操作设备。
第三步由值班人员进行人工复核，输出异常是否成立及核查依据。
最后数据管理智能体保存原始观测引用、异常线索和人工复核结论，保留版本与来源。
资料没有给出异常阈值、通知接收人和设备动作权限，理解结果应提出待明确问题，不可捏造。
'''
sample_path=ROOT/'.runtime/synthetic-setup-report.txt';sample_path.write_text(sample);sample_path.chmod(0o600)
with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=20) as c:
    r=c.post('/api/login',json={'username':access['username'],'password':access['password']});r.raise_for_status()
    c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':access['workspace_id']})
    existing=c.get('/api/setup/understanding/models');existing.raise_for_status()
    for provider in ['qwen','deepseek']:
        if any(m['name']=='setup_'+provider for m in existing.json()):continue
        value=json.loads((ROOT/'examples'/('qwen.json' if provider=='qwen' else 'deepseek.json')).read_text())
        value.update(namespace='setup_validation',name='setup_'+provider,owner='隔离合成验收',description='仅用于已授权的非敏感合成理解验收')
        value['execution']['max_tokens']=4096
        r=c.post('/api/capabilities',json=value);r.raise_for_status();cap=r.json()
        for action in ['validate','publish']:
            r=c.post(f"/api/capabilities/{cap['id']}/{action}",json={'expected_version':cap['version'],'reason':'非敏感合成理解验证'});r.raise_for_status();cap=r.json()
    current=c.get('/api/setup/draft');current.raise_for_status();current=current.json()
    if not current['configuration']:
        value={'version':1,'connections':[],'resources':[],'businesses':[],'gaps':[],'materials':[],
               'instanceProfile':{'name':'资料理解真实云验收','domain':'设备运维','goal':'根据合成报告生成带原文依据的设备异常复核流程，核对修改后保存。'}}
        c.put('/api/setup/draft',json={'revision':current['revision'],'configuration':value}).raise_for_status()
print('已准备隔离验收账户、合成资料与两个已发布模型；未调用云端、未读取真实报告。')
