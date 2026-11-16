"""Identify unit conflicts from independently received original MQTT packets.

This stage records an issue only. It neither converts nor changes observations,
object facts, packet bytes, bindings or retrieval indexes. The caller owns the
transaction and must not present an issue as corrected or resolved.
"""
import json,math,re
from fastapi import HTTPException
from sqlalchemy import select,text
from .contracts import digest
from .evidence import evidence_bytes
from .models import Audit,Event,Issue,IssueCheck,Memory,MqttReceipt,User,Membership
from .mqtt_data import raw_report
from .mqtt_messages import _no_duplicate_keys
from .security import require
from .services import scoped,audit

KIND='mqtt_unit_conflict_v357'
SCHEMA='mqtt-unit-conflict-issue-v357'


def _source(db,p,receipt_id):
    receipt=scoped(db,MqttReceipt,receipt_id,p)
    report=raw_report(db,p,receipt)
    event=db.scalar(select(Event).where(Event.workspace_id==p.workspace_id,Event.kind=='perception.sensor_packet',
        Event.idempotency_key=='mqtt-packet:'+receipt.id))
    if event is None or event.status!='received' or event.data.get('report')!=report or event.data.get('report_hash')!=digest(report):
        raise HTTPException(409,'数据管理尚未核对接收原报文，不能生成单位问题')
    memories=list(db.scalars(select(Memory).where(Memory.workspace_id==p.workspace_id,
        Memory.data['event_id'].as_string()==event.id,Memory.data['type'].as_string()=='sensor_original').limit(2)))
    if len(memories)!=1 or memories[0].status!='candidate' or any(memories[0].data.get(k)!=v for k,v in report.items()):
        raise HTTPException(409,'原报文独立接收记录无法核对')
    _,raw=evidence_bytes(db,p,receipt.evidence_id)
    try:
        packet=json.loads(raw.decode('utf-8'),object_pairs_hook=_no_duplicate_keys,
            parse_constant=lambda _x:(_ for _ in ()).throw(ValueError('non-finite')))
        cfg=receipt.data['config_snapshot'];mapping=cfg['mapping'];fields=cfg['fields']
        values=packet[fields.get('values_key','values')];units=packet.get(fields.get('units_key','units'),{})
        if not isinstance(values,dict) or not isinstance(units,dict):raise ValueError()
    except (KeyError,TypeError,ValueError,UnicodeError,RecursionError):
        raise HTTPException(409,'原报文单位或原值无法核对') from None
    return receipt,event,report,mapping,values,units


def diagnose(db,p,receipt_id,*,mode='manual'):
    """Record a bounded idempotent candidate issue per original conflicting field."""
    if mode not in {'manual','automatic'}:raise ValueError('invalid detection mode')
    require(p,'admin','operator')
    member=db.scalar(select(Membership).join(User,User.id==Membership.user_id).where(
        Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id,
        Membership.role==p.role,User.active.is_(True)).with_for_update(read=True))
    if member is None:raise HTTPException(403,'当前账号的工作区权限已改变')
    receipt,event,report,mapping,values,units=_source(db,p,receipt_id)
    decoded=receipt.data.get('decoded') or {}
    issues=decoded.get('issues',[])
    conflicts=[x[len('unit_conflict:'):] for x in issues if isinstance(x,str) and x.startswith('unit_conflict:')]
    if len(conflicts)!=len(set(conflicts)) or len(conflicts)>64:raise HTTPException(409,'原单位异常清单无法核对')
    locked=db.scalar(text('SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'mqtt-unit-quality-v357:'+p.workspace_id+':'+receipt.id})
    if not locked:raise HTTPException(409,'该原件正在核对，请沿原任务稍后重试')
    from .mqtt_unit_number_v357 import original_number
    _,raw=evidence_bytes(db,p,receipt.evidence_id)
    result=[]
    for field in conflicts:
        matches=[(key,spec) for key,spec in mapping.items() if spec.get('attribute')==field]
        if len(matches)!=1:raise HTTPException(409,'原单位字段映射无法核对')
        wire,spec=matches[0];value=values.get(wire);reported=units.get(wire)
        if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>1e15:raise HTTPException(409,'原测量值无法核对')
        lexeme=original_number(raw,receipt.data['config_snapshot']['fields'].get('values_key','values'),wire)
        reported=reported if isinstance(reported,str) and 0<len(reported)<=40 and not re.search(r'[\x00-\x1f\x7f]',reported) else None
        proof={'schema':SCHEMA,'receipt_id':receipt.id,'event_id':event.id,'evidence_id':receipt.evidence_id,
            'input_sha256':receipt.input_sha256,'config_hash':receipt.data['config_hash'],
            'gateway_id':receipt.gateway_id,'source_field':wire,'mapped_field':field,
            'original_value':value,'original_value_is_approximate':True,
            'original_number_lexeme':lexeme if len(lexeme)<=80 else None,
            'original_number_lexeme_complete':len(lexeme)<=80,'reported_unit':reported,'expected_unit':spec['unit'],
            'object_verified':False,'conversion_applied':False}
        payload={'proof':proof,'proof_hash':digest(proof),'check_key':SCHEMA+':'+receipt.id+':'+field,
            'diagnosis':{'problem':'报文单位与已确认的来源单位不一致，原件已保留，尚未换算。'},
            'suggested_action':'核对原报文、设备单位和业务字段；确认前不要将数值作为对象事实。'}
        rows=list(db.scalars(select(Issue).where(Issue.workspace_id==p.workspace_id,Issue.kind==KIND,
            Issue.data['check_key'].as_string()==payload['check_key']).with_for_update().limit(2)))
        if len(rows)>1:raise HTTPException(409,'原单位问题记录重复，不能替换')
        if rows:
            issue=rows[0]
            if (issue.entity_id is not None or issue.data.get('proof')!=proof
                or issue.data.get('proof_hash')!=payload['proof_hash']
                or issue.data.get('check_key')!=payload['check_key']):
                raise HTTPException(409,'原单位问题已变化，保留记录供核查')
            checks=list(db.scalars(select(IssueCheck).where(IssueCheck.workspace_id==p.workspace_id,
                IssueCheck.issue_id==issue.id,IssueCheck.data['operation'].as_string()=='detected').limit(2)))
            audits=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
                Audit.object_id==issue.id,Audit.action=='quality.mqtt_unit_conflict_detected_v357').limit(2)))
            if (len(checks)!=1 or len(audits)!=1 or checks[0].actor_id!=audits[0].actor_id
                or checks[0].data.get('source_hash')!=proof['input_sha256']
                or checks[0].data.get('diagnosis')!=payload['diagnosis']
                or audits[0].data.get('proof_hash')!=payload['proof_hash']
                or audits[0].data.get('event_id')!=event.id
                or audits[0].data.get('detection_mode') not in {'manual','automatic'}
                or checks[0].data.get('detection_mode')!=audits[0].data.get('detection_mode')):
                raise HTTPException(409,'原单位问题的独立依据无法核对')
        else:
            issue=Issue(workspace_id=p.workspace_id,entity_id=None,kind=KIND,status='open',data=payload);db.add(issue);db.flush()
            db.add(IssueCheck(workspace_id=p.workspace_id,issue_id=issue.id,actor_id=p.user_id,
                data={'operation':'detected','issue_version':issue.version,'diagnosis':payload['diagnosis'],'source_hash':proof['input_sha256'],'detection_mode':mode}))
            audit(db,p,'quality.mqtt_unit_conflict_detected_v357',issue.id,{'proof_hash':payload['proof_hash'],'event_id':event.id,'detection_mode':mode})
        result.append(issue)
    return result
