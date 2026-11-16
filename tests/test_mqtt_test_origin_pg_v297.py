"""Real API/PG disclosures and raw custody, not a broker or V288 release proof."""
import copy
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.mqtt_inbox import set_status
from generic_agents.mqtt_test_origin_v297 import ACTION, checked_declaration
from generic_agents.security import Principal
from tests.test_mqtt_inbox import gateway, packet, save


def command(gateway):
    p, identifier, version = gateway
    with Session(engine()) as db: config_hash=digest(db.get(m.MqttGateway,identifier).data)
    return {'request_id':str(uuid4()),'expected_gateway_version':version,'expected_config_hash':config_hash,
        'source_label':'厂家参数模拟传感器','basis_type':'manufacturer_parameters',
        'basis_notes':'协议与参数测试；数值由软件生成，未连接物理设备或验证现场状态。'}


def post(client,gateway,body):
    return client.post('/api/mqtt/gateways/'+gateway[1]+'/test-origin',json=body)


def test_prospective_api_disclosure_survives_independent_session_and_redelivery(client,gateway):
    body=command(gateway)
    response=post(client,gateway,body)
    assert response.status_code==201,response.text
    result=response.json()
    assert result['simulation_only'] is result['test_only'] is True
    assert result['physical_device_connected'] is result['field_verified'] is False
    first=save(gateway,packet()); duplicate=save(gateway,packet(),duplicate=True)
    assert duplicate.id==first.id and duplicate.version>first.version
    with Session(engine()) as db:
        original=db.get(m.MqttReceipt,first.id)
        declaration=checked_declaration(db,gateway[0],original,cut=datetime.now(timezone.utc))
        assert declaration=={'simulation_only':True,'test_only':True,'test_origin':{
            'audit_id':result['audit_id'],'declaration_hash':result['declaration_hash']}}
        assert not db.new and not db.dirty
        assert db.scalar(select(func.count()).select_from(m.Audit).where(m.Audit.action==ACTION))==1
    recovered=post(client,gateway,body)
    assert recovered.status_code==201 and recovered.json()==result
    view=client.get('/api/mqtt/gateways/'+gateway[1]+'/test-origin')
    assert view.status_code==200 and view.json()['declaration']==result


def test_disclosure_does_not_mutate_gateway_or_original_receipt_and_survives_pause(client,gateway):
    body=command(gateway)
    with Session(engine()) as db:
        gw=db.get(m.MqttGateway,gateway[1]); before=copy.deepcopy((gw.version,gw.data,gw.status))
    result=post(client,gateway,body).json(); raw=save(gateway,packet())
    with Session(engine()) as db:
        gw=db.get(m.MqttGateway,gateway[1]); assert (gw.version,gw.data,gw.status)==before
        receipt=db.get(m.MqttReceipt,raw.id); original=copy.deepcopy((receipt.version,receipt.data))
        set_status(db,gateway[0],gateway[1],gateway[2],'paused');db.commit()
    with Session(engine()) as db:
        receipt=db.get(m.MqttReceipt,raw.id)
        assert checked_declaration(db,gateway[0],receipt)['test_origin']['audit_id']==result['audit_id']
        assert (receipt.version,receipt.data)==original
    assert post(client,gateway,body).json()==result  # Same command recovers the old version.


def test_cannot_retroactively_relabel_already_received_packets(client,gateway):
    raw=save(gateway,packet())
    response=post(client,gateway,command(gateway))
    assert response.status_code==409
    with Session(engine()) as db:
        assert checked_declaration(db,gateway[0],db.get(m.MqttReceipt,raw.id)) is None
        assert db.scalar(select(func.count()).select_from(m.Audit).where(m.Audit.action==ACTION))==0


@pytest.mark.parametrize('fault',['version','config','same_version','changed_nonce_body','extra_flags','string_version'])
def test_bad_or_replacement_declaration_preserves_original(client,gateway,fault):
    body=command(gateway); original=post(client,gateway,body)
    assert original.status_code==201
    changed=copy.deepcopy(body)
    if fault=='version': changed.update(request_id=str(uuid4()),expected_gateway_version=gateway[2]+1)
    if fault=='config': changed.update(request_id=str(uuid4()),expected_config_hash='0'*64)
    if fault=='same_version': changed.update(request_id=str(uuid4()))
    if fault=='changed_nonce_body': changed['basis_notes']='changed'
    if fault=='extra_flags': changed['simulation_only']=False
    if fault=='string_version': changed['expected_gateway_version']=str(changed['expected_gateway_version'])
    response=post(client,gateway,changed)
    assert response.status_code==(422 if fault in {'extra_flags','string_version'} else 409)
    assert post(client,gateway,body).json()==original.json()


def test_declaration_after_cut_is_not_used_and_resealed_false_flags_are_rejected(client,gateway):
    response=post(client,gateway,command(gateway)); assert response.status_code==201
    raw=save(gateway,packet())
    with Session(engine()) as db:
        row=db.get(m.Audit,response.json()['audit_id']); receipt=db.get(m.MqttReceipt,raw.id)
        with pytest.raises(HTTPException): checked_declaration(db,gateway[0],receipt,cut='2000-01-01T00:00:00+00:00')
        value={**row.data,'simulation_only':False}
        value['record_hash']=digest({k:v for k,v in value.items() if k!='record_hash'})
        row.data=value; db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): checked_declaration(db,gateway[0],db.get(m.MqttReceipt,raw.id))


def test_internal_viewer_projection_can_read_admin_owned_original_without_execution_rights(client,gateway):
    assert post(client,gateway,command(gateway)).status_code==201
    raw=save(gateway,packet()); p=gateway[0]
    limited=Principal(p.user_id,p.workspace_id,'viewer','bounded-data-receiver')
    with Session(engine()) as db:
        assert checked_declaration(db,limited,db.get(m.MqttReceipt,raw.id))['test_only'] is True
        from generic_agents.mqtt_test_origin_v297 import declare
        with pytest.raises(HTTPException) as error:
            declare(db,limited,gateway[1],command(gateway))
        assert error.value.status_code==403


@pytest.mark.parametrize('revocation',['lower_role','inactive'])
def test_stale_admin_or_disabled_reader_cannot_use_declaration_authority(client,gateway,revocation):
    assert post(client,gateway,command(gateway)).status_code==201
    raw=save(gateway,packet()); p=gateway[0]
    with Session(engine()) as db:
        if revocation=='inactive': db.get(m.User,p.user_id).active=False
        else:
            membership=db.scalar(select(m.Membership).where(m.Membership.user_id==p.user_id,
                m.Membership.workspace_id==p.workspace_id))
            membership.role='viewer'
        db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException): checked_declaration(db,p,db.get(m.MqttReceipt,raw.id))
