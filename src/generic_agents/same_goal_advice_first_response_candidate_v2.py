"""Closed first-HTTP response representation for isolated v2 experiments.

Pure encoding and offline parsing only: no DB persistence, no sender, no
consent, no authorization, and no proof that a network response occurred.
The capture caller must first authenticate the actual intent and CloudCall.
"""
import base64
import copy
import hashlib
import json
import re
import zlib
from uuid import UUID
from . import cloud_business_reasoning_v197 as transport
from . import same_goal_advice_contracts_v302 as contracts
from .contracts import digest

SCHEMA='same-goal-advice-unlimited-first-response-v2'
MAX_RAW_BYTES=16*1024*1024
_FIELDS={'schema','cloud_call_id','intent_audit_id','raw_base64','raw_sha256','receipt','record_hash'}
_RECEIPT_KEYS={'provider','model','call_id','http_status','request_id','usage','usage_unavailable',
    'usage_unavailable_reason','finish_reason','response_choice_count','stage','header_raw_base64','encoding_unverified'}
_HEADER_KEYS={b'content-encoding',b'content-type',b'x-request-id'}


def _headers(receipt):
    headers=receipt.get('header_raw_base64',[])
    contracts.need(type(headers) is list and len(headers)<=16)
    decoded=[]
    try:
        for pair in headers:
            contracts.need(type(pair) is list and len(pair)==2 and all(type(item) is str for item in pair))
            decoded.append(tuple(base64.b64decode(item,validate=True) for item in pair))
    except (ValueError,TypeError):contracts.need(False)
    contracts.need(sum(len(k)+len(v) for k,v in decoded)<=4096
        and all(k.lower() in _HEADER_KEYS for k,_ in decoded))
    for key,value in decoded:
        key=key.lower()
        if key==b'x-request-id':contracts.need(re.fullmatch(rb'[A-Za-z0-9._:-]{1,128}',value) is not None)
        elif key==b'content-encoding':contracts.need(re.fullmatch(rb'[A-Za-z0-9._-]{1,32}',value) is not None)
        else:contracts.need(re.fullmatch(rb'[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+(?:;\s*charset=[A-Za-z0-9._-]+)?',value,re.I) is not None and len(value)<=128)
    return decoded


def _uuid(value):
    contracts.need(type(value) is str and str(UUID(value))==value)


def seal(*,cloud_call_id,intent_audit_id,raw,receipt):
    """Preserve exact response bytes before parse, bounded without truncation."""
    _uuid(cloud_call_id);_uuid(intent_audit_id)
    contracts.need(type(raw) is bytes and len(raw)<=MAX_RAW_BYTES and type(receipt) is dict
        and set(receipt)<=_RECEIPT_KEYS
        and receipt.get('call_id')==cloud_call_id and receipt.get('provider')=='deepseek'
        and type(receipt.get('model')) is str and re.fullmatch(r'[A-Za-z0-9._:/-]{1,200}',receipt['model']) is not None
        and (receipt.get('request_id') is None or type(receipt['request_id']) is str
            and re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',receipt['request_id']) is not None)
        and ('usage' not in receipt or receipt['usage']=={})
        and ('usage_unavailable' not in receipt or receipt['usage_unavailable'] is True)
        and ('usage_unavailable_reason' not in receipt or receipt['usage_unavailable_reason']=='unparsed_original_http')
        and ('finish_reason' not in receipt or receipt['finish_reason'] is None)
        and ('response_choice_count' not in receipt or receipt['response_choice_count'] is None)
        and ('stage' not in receipt or receipt['stage']==transport.STAGE)
        and ('encoding_unverified' not in receipt or type(receipt['encoding_unverified']) is bool)
        and type(receipt.get('http_status')) is int and 100<=receipt['http_status']<=599)
    headers=_headers(receipt)
    request_ids=[value.decode('ascii') for key,value in headers if key.lower()==b'x-request-id']
    contracts.need(not request_ids or len(request_ids)==1 and receipt.get('request_id')==request_ids[0])
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
    if value['receipt'].get('encoding_unverified') is True:
        raise transport._blocked('原回答的内容编码无法核对；原始字节已保留且不重发')
    decoded=_headers(value['receipt'])
    try:encodings=[v.decode('ascii').strip().lower() for k,v in decoded if k.lower()==b'content-encoding']
    except UnicodeError:
        raise transport._blocked('原回答的内容编码无效；原始字节已保留且不重发') from None
    if len(encodings)>1 or (encodings and encodings[0] not in {'','identity','gzip'}):
        raise transport._blocked('原回答的内容编码暂不支持；原始字节已保留且不重发')
    if encodings and encodings[0]=='gzip':
        try:
            decoder=zlib.decompressobj(16+zlib.MAX_WBITS)
            decoded_body=decoder.decompress(raw,transport.MAX_RESPONSE_BYTES+1)
            if len(decoded_body)>transport.MAX_RESPONSE_BYTES or decoder.unconsumed_tail:
                raise ValueError('decompressed response exceeds limit')
            decoded_body+=decoder.flush(transport.MAX_RESPONSE_BYTES+1-len(decoded_body))
            if len(decoded_body)>transport.MAX_RESPONSE_BYTES or not decoder.eof or decoder.unused_data:
                raise ValueError('invalid or oversized gzip response')
            raw=decoded_body
        except (ValueError,zlib.error):
            raise transport._blocked('原回答压缩数据无效或解压超限；原始字节已保留且不重发') from None
    text=raw.decode('utf-8',errors='surrogateescape')
    receipt=transport.response_receipt(text,value['receipt'])
    result,receipt=transport.parse_response(text,schema,receipt)
    return result,receipt
