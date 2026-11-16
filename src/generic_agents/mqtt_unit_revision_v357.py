"""Propose and explicitly authorize an immutable *candidate* unit revision.

No execution, source mutation, observation insertion, index update or issue
closure is performed here. A future executor must independently revalidate the
original receipt, mapping, grant, revocation, and downstream receipt chain.
"""
from decimal import Decimal,localcontext,ROUND_HALF_EVEN,InvalidOperation
import json
from fractions import Fraction
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import DDL,ForeignKey,String,UniqueConstraint,event,select,text
from sqlalchemy.orm import Mapped,mapped_column
from .contracts import digest
from .models import Base,Row,Scoped,Payload,Issue,IssueCheck,Audit,MqttGateway,Membership,User
from .services import scoped,audit
from .evidence import evidence_bytes
from .mqtt_messages import _no_duplicate_keys
from .mqtt_unit_quality_v357 import KIND,_source
from .mqtt_binding_routes import active_bindings,checked_revision,allows_receipt
from .mqtt_observations import binding_hash

class MqttUnitCandidateV357(Row,Scoped,Payload,Base):
    __tablename__='mqtt_unit_candidates_v357'
    __table_args__=(UniqueConstraint('workspace_id','request_id'),)
    issue_id:Mapped[str]=mapped_column(ForeignKey('data_quality_issues.id'),index=True)
    request_id:Mapped[str]=mapped_column(String(36))
    actor_id:Mapped[str]=mapped_column(ForeignKey('users.id'))

class MqttUnitGrantV357(Row,Scoped,Payload,Base):
    __tablename__='mqtt_unit_grants_v357'
    __table_args__=(UniqueConstraint('workspace_id','request_id'),UniqueConstraint('candidate_id'))
    candidate_id:Mapped[str]=mapped_column(ForeignKey('mqtt_unit_candidates_v357.id'),index=True)
    request_id:Mapped[str]=mapped_column(String(36))
    actor_id:Mapped[str]=mapped_column(ForeignKey('users.id'))

class MqttUnitRevocationV357(Row,Scoped,Payload,Base):
    __tablename__='mqtt_unit_grant_revocations_v357'
    __table_args__=(UniqueConstraint('workspace_id','request_id'),UniqueConstraint('grant_id'))
    grant_id:Mapped[str]=mapped_column(ForeignKey('mqtt_unit_grants_v357.id'),index=True)
    request_id:Mapped[str]=mapped_column(String(36))
    actor_id:Mapped[str]=mapped_column(ForeignKey('users.id'))

_GUARD="""CREATE OR REPLACE FUNCTION ga_mqtt_unit_revision_v357_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'unit revision evidence is immutable' USING ERRCODE='55000'; END; $$;"""
for table in (MqttUnitCandidateV357.__table__,MqttUnitGrantV357.__table__,MqttUnitRevocationV357.__table__):
    event.listen(table,'after_create',DDL(_GUARD).execute_if(dialect='postgresql'))
    event.listen(table,'after_create',DDL('CREATE TRIGGER ga_mqtt_unit_revision_v357_guard BEFORE UPDATE OR DELETE ON %(fullname)s FOR EACH ROW EXECUTE FUNCTION ga_mqtt_unit_revision_v357_immutable()').execute_if(dialect='postgresql'))


def need(ok,message='原单位问题、关联或授权无法核对；原记录保留'):
    if not ok:raise HTTPException(409,message)


def _member(db,p,admin=False):
    roles={'admin'} if admin else {'admin','operator'}
    if p.role not in roles:raise HTTPException(403,'当前账号无权操作单位修订')
    row=db.scalar(select(Membership).join(User,User.id==Membership.user_id).where(
        Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id,
        Membership.role==p.role,User.active.is_(True)).with_for_update(read=True))
    if row is None:raise HTTPException(403,'当前账号的工作区权限已改变')


def _request(value):
    try:
        if type(value) is not str or str(UUID(value))!=value:raise ValueError()
    except (TypeError,ValueError,AttributeError):raise HTTPException(422,'请求编号无效') from None


def _reason(value):
    if type(value) is not str or not value.strip() or len(value)>500:raise HTTPException(422,'请填写不超过 500 字的原因')


def _source_issue(db,p,issue_id):
    issue=scoped(db,Issue,issue_id,p)
    need(issue.kind==KIND and issue.status=='open' and issue.entity_id is None)
    data=issue.data;proof=data.get('proof') if isinstance(data,dict) else None
    need(isinstance(proof,dict) and data.get('proof_hash')==digest(proof))
    receipt,evt,report,mapping,values,units=_source(db,p,proof.get('receipt_id'))
    wire=proof.get('source_field');spec=mapping.get(wire)
    need(type(spec) is dict and proof.get('mapped_field')==spec.get('attribute')
        and proof.get('receipt_id')==receipt.id and proof.get('event_id')==evt.id
        and proof.get('input_sha256')==receipt.input_sha256 and proof.get('config_hash')==receipt.data['config_hash']
        and proof.get('gateway_id')==receipt.gateway_id and proof.get('evidence_id')==receipt.evidence_id
        and proof.get('original_value')==values.get(wire) and proof.get('reported_unit')==units.get(wire)
        and proof.get('expected_unit')==spec.get('unit') and proof.get('object_verified') is False
        and proof.get('conversion_applied') is False)
    checks=list(db.scalars(select(IssueCheck).where(IssueCheck.workspace_id==p.workspace_id,
        IssueCheck.issue_id==issue.id,IssueCheck.data['operation'].as_string()=='detected').limit(2)))
    audits=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.object_id==issue.id,
        Audit.action=='quality.mqtt_unit_conflict_detected_v357').limit(2)))
    need(len(checks)==len(audits)==1 and checks[0].actor_id==audits[0].actor_id
        and checks[0].data.get('source_hash')==receipt.input_sha256
        and audits[0].data.get('proof_hash')==data['proof_hash'] and audits[0].data.get('event_id')==evt.id
        and audits[0].data.get('detection_mode') in {'manual','automatic'}
        and checks[0].data.get('detection_mode')==audits[0].data.get('detection_mode'))
    incoming=receipt.data.get('intake_issues',[])
    need(incoming and all(isinstance(x,str) and x.startswith('unit_conflict:') for x in incoming)
        and receipt.data.get('issues')==incoming and receipt.data.get('qos')==1 and receipt.data.get('retain') is False,
        '原报文除单位外还有未核问题，须先人工核对')
    return issue,proof,receipt


def _binding(db,p,receipt,proof,binding_id):
    gateway=scoped(db,MqttGateway,receipt.gateway_id,p)
    need(gateway.status=='active' and gateway.owner_id==p.user_id and digest(gateway.data)==receipt.data['config_hash'])
    candidates=[(binding,route) for binding,route in active_bindings(db,p,gateway) if binding.id==binding_id]
    need(len(candidates)==1);binding,route=candidates[0]
    checked_revision(db,p,binding)
    need(binding.gateway_id==receipt.gateway_id and binding.topic==receipt.data['topic']
        and binding.config_hash==receipt.data['config_hash'] and allows_receipt(route,receipt)
        and receipt.data['decoded']['device_external_id']==binding.data['device_external_id'])
    mapped=binding.data.get('field_links',{}).get(proof['mapped_field'])
    need(type(mapped) is str and binding.data.get('field_units',{}).get(mapped)==proof['expected_unit'])
    return binding,route,mapped


class _OriginalNumber:
    def __init__(self,lexeme):self.lexeme=lexeme


def _original_number(db,p,receipt,proof):
    # Parse the verified original bytes again: str(float) can already have
    # lost digits from the source and must never be used as a measurement.
    _,raw=evidence_bytes(db,p,receipt.evidence_id)
    try:
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=_no_duplicate_keys,
            parse_int=_OriginalNumber,parse_float=_OriginalNumber,
            parse_constant=lambda _x:(_ for _ in ()).throw(ValueError('non-finite')))
        field=receipt.data['config_snapshot']['fields'].get('values_key','values')
        item=value[field][proof['source_field']]
        if not isinstance(item,_OriginalNumber) or len(item.lexeme)>80:raise ValueError()
        number=Decimal(item.lexeme)
        if not number.is_finite() or len(number.as_tuple().digits)>64 or not -30<=number.as_tuple().exponent<=15 or number.copy_abs()>Decimal('1e15'):
            raise ValueError()
        if float(number)!=float(proof['original_value']):raise ValueError()
        return item.lexeme,number
    except (KeyError,TypeError,ValueError,UnicodeError,RecursionError,InvalidOperation,OverflowError):
        raise HTTPException(409,'原始数值精度无法安全核对；原报文保留，须人工处理') from None


def _conversion(proof,places,original):
    if type(places) is not int or not 0<=places<=6:raise HTTPException(422,'保留小数位须为 0 至 6')
    units={'C':'C','°C':'C','F':'F','°F':'F'}
    source=units.get(proof.get('reported_unit'));target=units.get(proof.get('expected_unit'))
    need(source in {'C','F'} and target in {'C','F'} and source!=target,'单位未知、不可换算或不属于已支持的温度转换')
    raw=Fraction(original)
    exact=(raw-32)*Fraction(5,9) if source=='F' else raw*Fraction(9,5)+32
    with localcontext() as ctx:
        ctx.prec=80;rounded=(Decimal(exact.numerator)/Decimal(exact.denominator)).quantize(Decimal(1).scaleb(-places),rounding=ROUND_HALF_EVEN)
    return {'formula':'fahrenheit_to_celsius_v1' if source=='F' else 'celsius_to_fahrenheit_v1',
        'exact_numerator':str(exact.numerator),'exact_denominator':str(exact.denominator),
        'rounded_value':format(rounded,'f'),'decimal_places':places,'rounding':'half_even'}


def preview(db,p,issue_id,binding_id,places):
    _member(db,p);issue,proof,receipt=_source_issue(db,p,issue_id);binding,route,field=_binding(db,p,receipt,proof,binding_id)
    lexeme,original=_original_number(db,p,receipt,proof)
    need(proof.get('original_number_lexeme_complete') is True and proof.get('original_number_lexeme')==lexeme)
    result={'schema':'mqtt-unit-revision-candidate-v357','workspace_id':p.workspace_id,'issue_id':issue.id,
        'issue_version':issue.version,'issue_proof_hash':issue.data['proof_hash'],'receipt_id':receipt.id,
        'input_sha256':receipt.input_sha256,'binding_id':binding.id,'binding_hash':binding_hash(binding),
        'route_id':route.id if route else None,'route_version':route.version if route else None,
        'entity_id':binding.entity_id,'business_field':field,'observed_at':receipt.data['decoded']['observed_at'],
        'original_number_lexeme':lexeme,'reported_value':format(original,'f'),'reported_unit':proof['reported_unit'],'target_unit':proof['expected_unit'],
        'conversion':_conversion(proof,places,original),'original_modified':False,'object_fact_updated':False,
        'index_synchronized':False,'issue_resolved':False}
    return {**result,'candidate_hash':digest(result)}


def _saved_candidate(db,p,row):
    data=row.data;body=data.get('body') if isinstance(data,dict) else None;value=data.get('candidate') if isinstance(data,dict) else None
    need(isinstance(body,dict) and isinstance(value,dict) and data.get('body_hash')==digest(body)
        and body.get('issue_id')==row.issue_id and value.get('issue_id')==row.issue_id
        and value.get('candidate_hash')==data.get('candidate_hash')
        and digest({k:v for k,v in value.items() if k!='candidate_hash'})==value.get('candidate_hash'))
    events=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.object_id==row.id,
        Audit.action=='quality.mqtt_unit_candidate_proposed_v357').limit(2)))
    need(len(events)==1 and events[0].actor_id==row.actor_id and events[0].data=={
        'candidate_hash':value['candidate_hash'],'request_id':row.request_id})
    return value


def _saved_grant(db,p,row):
    data=row.data;body=data.get('body') if isinstance(data,dict) else None
    need(isinstance(body,dict) and data.get('body_hash')==digest(body)
        and body.get('candidate_id')==row.candidate_id and body.get('expected_hash')==data.get('candidate_hash')
        and data.get('candidate_only') is True and data.get('execution_authorized') is False
        and data.get('authorization_scope')=='historical_candidate_acknowledgement_only')
    events=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.object_id==row.id,
        Audit.action=='quality.mqtt_unit_candidate_approved_v357').limit(2)))
    need(len(events)==1 and events[0].actor_id==row.actor_id and events[0].data=={
        'candidate_hash':data['candidate_hash'],'request_id':row.request_id})


def propose(db,p,issue_id,binding_id,places,request_id,reason):
    _request(request_id);_reason(reason);_member(db,p)
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'mqtt-unit-candidate:'+p.workspace_id+':'+request_id})
    body={'issue_id':issue_id,'binding_id':binding_id,'decimal_places':places,'reason':reason}
    old=db.scalar(select(MqttUnitCandidateV357).where(MqttUnitCandidateV357.workspace_id==p.workspace_id,MqttUnitCandidateV357.request_id==request_id))
    if old is not None:
        need(old.actor_id==p.user_id and old.data.get('body')==body and old.data.get('body_hash')==digest(body))
        _saved_candidate(db,p,old);return old
    value=preview(db,p,issue_id,binding_id,places)
    row=MqttUnitCandidateV357(workspace_id=p.workspace_id,issue_id=issue_id,request_id=request_id,actor_id=p.user_id,
        data={'body':body,'body_hash':digest(body),'candidate':value,'candidate_hash':value['candidate_hash']})
    db.add(row);db.flush();audit(db,p,'quality.mqtt_unit_candidate_proposed_v357',row.id,{'candidate_hash':value['candidate_hash'],'request_id':request_id})
    return row


def authorize(db,p,candidate_id,request_id,expected_hash,reason):
    _request(request_id);_reason(reason);_member(db,p,admin=True)
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'mqtt-unit-grant:'+p.workspace_id+':'+candidate_id})
    candidate=scoped(db,MqttUnitCandidateV357,candidate_id,p);scoped(db,Issue,candidate.issue_id,p,lock=True)
    body={'candidate_id':candidate_id,'expected_hash':expected_hash,'reason':reason}
    old=db.scalar(select(MqttUnitGrantV357).where(MqttUnitGrantV357.workspace_id==p.workspace_id,MqttUnitGrantV357.request_id==request_id))
    if old is not None:
        need(old.actor_id==p.user_id and old.data.get('body')==body and old.data.get('body_hash')==digest(body));_saved_grant(db,p,old);return old
    need(db.scalar(select(MqttUnitGrantV357.id).where(MqttUnitGrantV357.candidate_id==candidate.id)) is None,'该候选已有原授权')
    saved=_saved_candidate(db,p,candidate);need(saved.get('candidate_hash')==expected_hash)
    active_grant=select(MqttUnitGrantV357.id).join(MqttUnitCandidateV357,MqttUnitCandidateV357.id==MqttUnitGrantV357.candidate_id).where(
        MqttUnitGrantV357.workspace_id==p.workspace_id,MqttUnitCandidateV357.issue_id==candidate.issue_id,
        ~select(MqttUnitRevocationV357.id).where(MqttUnitRevocationV357.grant_id==MqttUnitGrantV357.id).exists())
    need(db.scalar(active_grant.limit(1)) is None,'该问题已有有效授权，请先核对原候选')
    value=preview(db,p,candidate.issue_id,candidate.data['body']['binding_id'],candidate.data['body']['decimal_places'])
    need(value==saved,'原来源或业务关联已变化，请重新核对候选')
    row=MqttUnitGrantV357(workspace_id=p.workspace_id,candidate_id=candidate.id,request_id=request_id,actor_id=p.user_id,
        data={'body':body,'body_hash':digest(body),'candidate_hash':expected_hash,'candidate_only':True,'execution_authorized':False,'authorization_scope':'historical_candidate_acknowledgement_only'})
    db.add(row);db.flush();audit(db,p,'quality.mqtt_unit_candidate_approved_v357',row.id,{'candidate_hash':expected_hash,'request_id':request_id})
    return row


def revoke(db,p,grant_id,request_id,reason):
    _request(request_id);_reason(reason);_member(db,p,admin=True)
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'mqtt-unit-revoke:'+p.workspace_id+':'+grant_id})
    grant=scoped(db,MqttUnitGrantV357,grant_id,p);candidate=scoped(db,MqttUnitCandidateV357,grant.candidate_id,p)
    scoped(db,Issue,candidate.issue_id,p,lock=True);_saved_grant(db,p,grant)
    need(_saved_candidate(db,p,candidate)['candidate_hash']==grant.data['candidate_hash'])
    body={'grant_id':grant_id,'reason':reason}
    old=db.scalar(select(MqttUnitRevocationV357).where(MqttUnitRevocationV357.workspace_id==p.workspace_id,MqttUnitRevocationV357.request_id==request_id))
    if old is not None:
        need(old.actor_id==p.user_id and old.data.get('body')==body and old.data.get('body_hash')==digest(body))
        events=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.object_id==old.id,
            Audit.action=='quality.mqtt_unit_candidate_revoked_v357').limit(2)))
        need(len(events)==1 and events[0].actor_id==old.actor_id and events[0].data=={'grant_id':grant.id,'request_id':request_id})
        return old
    need(db.scalar(select(MqttUnitRevocationV357.id).where(MqttUnitRevocationV357.grant_id==grant.id)) is None,'该候选授权已撤销')
    row=MqttUnitRevocationV357(workspace_id=p.workspace_id,grant_id=grant.id,request_id=request_id,actor_id=p.user_id,
        data={'body':body,'body_hash':digest(body),'grant_id':grant.id})
    db.add(row);db.flush();audit(db,p,'quality.mqtt_unit_candidate_revoked_v357',row.id,{'grant_id':grant.id,'request_id':request_id})
    return row
