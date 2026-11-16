"""Closed family separation and raw command compatibility, not live authority."""
import copy
import hashlib
import json
from uuid import uuid4
import pytest
from fastapi import HTTPException
from generic_agents import authored_image_workflow_contracts_v243 as image,authored_workflow_store_v208 as old
from tests.test_authored_workflow_v208 import body as old_body


def body():
    actor=str(uuid4())
    return {'schema':'authored-image-workflow-preview-command-v243','configuration_revision':1,'configuration_hash':'a'*64,
        'business_name':'动态图像技术核对','tool_bindings':{key:{'capability_id':str(uuid4()),'capability_hash':'b'*64,'actor_id':actor} for key in image.OPERATIONS},
        'role_bindings':{'原人工核对人':actor},'node_actions':{key:'data' for key in image.OPERATIONS},'external_node_ids':[],'prior_origin':None}


def raw(value):return ('  '+json.dumps(value,ensure_ascii=False)+'\n').encode()


def test_adopt_preview_schema_normalization_never_changes_original_adopt_raw_or_hash():
    preview=body();command={**preview,'schema':'authored-image-workflow-adopt-command-v243',
        'request_id':str(uuid4()),'preview_hash':'c'*64,'consent':True}
    parsed,exact,hashed=image.parse(raw(command),image.Adopt)
    assert exact.encode()==raw(command) and hashed==hashlib.sha256(raw(command)).hexdigest()
    assert image.preview_body(parsed)==preview
    assert parsed.model_dump(mode='json')['schema']==command['schema']
    assert old.preview_body(parsed)==preview


def test_old_v208_dump_raw_and_command_hash_remain_byte_compatible():
    value=old_body();preview=old.Preview.model_validate(value)
    assert old.preview_body(preview)==preview.model_dump(mode='json') and 'schema' not in preview.model_dump()
    command={**value,'request_id':str(uuid4()),'preview_hash':'c'*64,'consent':True}
    parsed,exact,hashed=old.parse(raw(command),old.Adopt)
    p=type('P',(),{'workspace_id':str(uuid4()),'user_id':str(uuid4())})()
    assert old._command_hash(p,parsed)==old.digest({'workspace_id':p.workspace_id,'actor_id':p.user_id,'path':old.PATH,'body':parsed.model_dump(mode='json')})
    assert exact.encode()==raw(command) and hashed==hashlib.sha256(raw(command)).hexdigest()
    with pytest.raises(HTTPException):image.parse(raw(command),image.Adopt)
    with pytest.raises(HTTPException):old.parse(raw(body()),old.Preview)


@pytest.mark.parametrize('field,value',[
    ('configuration_revision',True),('business_name',' '),('graph',{}),('external_node_ids',['quality']),
    ('node_actions',{'quality':'data','dedup':'data','report':'write'}),('source_path','/tmp/private'),
])
def test_client_cannot_supply_graph_execution_or_ambiguous_fields(field,value):
    command=body();command[field]=value
    with pytest.raises(HTTPException):image.parse(raw(command),image.Preview)


@pytest.mark.parametrize('consent',[1,'true',False,None])
def test_adoption_needs_actual_boolean(consent):
    command={**body(),'schema':'authored-image-workflow-adopt-command-v243','request_id':str(uuid4()),'preview_hash':'c'*64,'consent':consent}
    with pytest.raises(HTTPException):image.parse(raw(command),image.Adopt)


@pytest.mark.parametrize('value',[b'{"x":NaN}',b'{"x":1,"x":2}',b'[]',b'\xff',b''])
def test_bad_wire_is_not_reconstructed(value):
    with pytest.raises(HTTPException):image.parse(value,image.Preview)


def test_tools_command_is_a_separate_small_local_only_contract():
    value={'schema':'authored-image-tools-prepare-command-v243','request_id':str(uuid4()),'confirm_local':True}
    assert image.parse(raw(value),image.ToolsCommand)[0].model_dump()==value
    for changed in ({**value,'confirm_local':1},{**value,'url':'https://not-accepted.invalid'},
                    {**value,'module':'caller_code'},{**value,'confirm_local':False}):
        with pytest.raises(HTTPException):image.parse(raw(changed),image.ToolsCommand)
    with pytest.raises(HTTPException) as error:image.parse(b' '*16385,image.ToolsCommand)
    assert error.value.status_code==413
