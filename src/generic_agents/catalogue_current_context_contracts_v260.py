"""Strict M1 catalogue reuse requests; no model or current-plan authority."""
import json
from typing import Literal
from uuid import UUID, uuid5
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

MAX_RAW=65536
NAMESPACE=UUID('dd85b80e-84d0-4615-940e-e09562214cd0')
CHILD_NAMESPACE=UUID('9ea8dd0b-d709-4bef-b5d7-f8a85614d143')


def identity(value):
    if type(value) is not str or str(UUID(value))!=value:raise ValueError('canonical identity required')
    return value


def sha(value):
    return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)


class Closed(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)


class DraftRef(Closed):
    id: StrictStr
    workspace_id: StrictStr
    actor_id: StrictStr
    version: StrictInt=Field(ge=1)
    record_hash: StrictStr
    origin_hash: StrictStr
    document_hash: StrictStr
    @field_validator('id','workspace_id','actor_id')
    @classmethod
    def ids(cls,value):return identity(value)
    @field_validator('record_hash','origin_hash','document_hash')
    @classmethod
    def hashes(cls,value):
        if not sha(value):raise ValueError('invalid hash')
        return value


class ConfigurationBasis(Closed):
    revision: StrictInt=Field(ge=1)
    configuration_hash: StrictStr
    @field_validator('configuration_hash')
    @classmethod
    def hash(cls,value):
        if not sha(value):raise ValueError('invalid hash')
        return value


class Fork(Closed):
    schema: Literal['catalogue-current-context-fork-v260']
    request_id: StrictStr
    source_draft_ref: DraftRef
    target_configuration: ConfigurationBasis
    expected_context_hash: StrictStr
    confirm_preserve_saved_document: Literal[True]
    reason: StrictStr=Field(min_length=1,max_length=2000)
    @field_validator('request_id')
    @classmethod
    def nonce(cls,value):return identity(value)
    @field_validator('expected_context_hash')
    @classmethod
    def hash(cls,value):
        if not sha(value):raise ValueError('invalid hash')
        return value
    @field_validator('confirm_preserve_saved_document',mode='before')
    @classmethod
    def consent(cls,value):
        if value is not True:raise ValueError('explicit preservation required')
        return value
    @field_validator('reason')
    @classmethod
    def text(cls,value):
        value.encode('utf-8')
        if '\0' in value or not value.strip():raise ValueError('invalid reason')
        return value


def parse(raw):
    if type(raw) is not bytes or not 0<len(raw)<=MAX_RAW:raise ValueError('request size')
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:raise ValueError('duplicate key')
            value[key]=item
        return value
    def invalid(_):raise ValueError('nonfinite')
    text=raw.decode('utf-8');value=json.loads(text,object_pairs_hook=pairs,parse_constant=invalid)
    stack=[(value,0)];count=0
    while stack:
        item,depth=stack.pop();count+=1
        if depth>24 or count>2000:raise ValueError('structure limit')
        if type(item) is dict:
            for key,child in item.items():
                key.encode('utf-8')
                if '\0' in key:raise ValueError('NUL')
                stack.append((child,depth+1))
        elif type(item) is list:stack.extend((child,depth+1) for child in item)
        elif type(item) is str:
            item.encode('utf-8')
            if '\0' in item:raise ValueError('NUL')
    return Fork.model_validate(value),text


def managed(identity_value):
    return UUID(identity(identity_value)).version==5


def _derived(workspace_id,request_id,operation_label):
    value={'family':'catalogue-current-context-v260','workspace_id':identity(workspace_id),
        'root_request_id':identity(request_id),'operation_label':operation_label}
    spelling=json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    return str(uuid5(NAMESPACE,spelling))


def working_identity(workspace_id,request_id):
    return _derived(workspace_id,request_id,'working_copy')


def child_request(workspace_id,request_id,operation):
    if operation not in {'create','save'}:raise ValueError('unknown child')
    return _derived(workspace_id,request_id,'catalogue_child_'+operation)
