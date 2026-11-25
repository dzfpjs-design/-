"""Closed first-HTTP response representation for isolated v2 experiments.

Pure encoding and offline parsing only: no DB persistence, no sender, no
consent, no authorization, and no proof that a network response occurred.
The capture caller must first authenticate the actual intent and CloudCall.
"""
import base64
import copy
import hashlib
import json
from uuid import UUID
from . import cloud_business_reasoning_v197 as transport
from . import same_goal_advice_contracts_v302 as contracts
from .contracts import digest

SCHEMA='same-goal-advice-unlimited-first-response-v2'
MAX_RAW_BYTES=16*1024*1024
_FIELDS={'schema','cloud_call_id','intent_audit_id','raw_base64','raw_sha256','receipt','record_hash'}


def _uuid(value):
    contracts.need(type(value) is str and str(UUID(value))==value)


def seal(*,cloud_call_id,intent_audit_id,raw,receipt):
    """Preserve exact response bytes before parse, bounded without truncation."""
    _uuid(cloud_call_id);_uuid(intent_audit_id)
    contracts.need(type(raw) is bytes and len(raw)<=MAX_RAW_BYTES and type(receipt) is dict
        and receipt.get('call_id')==cloud_call_id and receipt.get('provider')=='deepseek'
        and type(receipt.get('model')) is str and bool(receipt['model'])
        and type(receipt.get('http_status')) is int and 100<=receipt['http_status']<=599)
    contracts.base._json(receipt)
    contracts.need(len(json.dumps(receipt,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8'))<=65536)
    value={'schema':SCHEMA,'cloud_call_id':cloud_call_id,'intent_audit_id':intent_audit_id,
        'raw_base64':base64.b64encode(raw).decode('ascii'),'raw_sha256':hashlib.sha256(raw).hexdigest(),
        'receipt':copy.deepcopy(receipt)}
    value['record_hash']=digest(value)
    return value


def check(value,*,cloud_call_id,intent_audit_id):
    contracts.need(type(value) is dict and set(value)==_FIELDS and value['schema']==SCHEMA
        and value['cloud_call_id']==cloud_call_id and value['intent_audit_id']==intent_audit_id
        and type(value['raw_base64']) is str and len(value['raw_base64'])<=4*((MAX_RAW_BYTES+2)//3)
        and value['record_hash']==digest({k:v for k,v in value.items() if k!='record_hash'}))
    try:raw=base64.b64decode(value['raw_base64'],validate=True)
    except (ValueError,TypeError):contracts.need(False)
    expected=seal(cloud_call_id=cloud_call_id,intent_audit_id=intent_audit_id,raw=raw,receipt=value['receipt'])
    contracts.need(expected==value)
    return raw


def parse_saved(value,*,cloud_call_id,intent_audit_id,schema):
    """Interpret captured bytes offline; never contacts a model or settles money."""
    raw=check(value,cloud_call_id=cloud_call_id,intent_audit_id=intent_audit_id)
    text=raw.decode('utf-8',errors='surrogateescape')
    receipt=transport.response_receipt(text,value['receipt'])
    result,receipt=transport.parse_response(text,schema,receipt)
    return result,receipt
