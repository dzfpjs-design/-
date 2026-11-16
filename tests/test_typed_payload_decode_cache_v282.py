"""Exact-input warm decoding never carries mutation, identity or authority."""
import copy
import json
import math
from concurrent.futures import ThreadPoolExecutor
import pytest
from generic_agents import typed_payload_codec_v131 as codec

OWNER={'table':'tasks','field':'data','owner_id':'cache-original','workspace_id':'workspace-a'}
MARKER=codec.MARKER_PREFIX+'00000000-0000-4000-8000-000000000001'

@pytest.fixture(autouse=True)
def fresh():
    codec._verified_decode_json.cache_clear()
    yield
    codec._verified_decode_json.cache_clear()

def row(value, owner=OWNER, marker=MARKER):
    return codec.encode_payload(value,owner,marker),marker,owner,json.dumps(value,ensure_ascii=False)

def rejected(args):
    with pytest.raises(codec.TypedPayloadError) as e:codec.decode_payload(*args)
    assert str(e.value)==codec.ERROR

def test_exact_repeat_uses_one_complete_check_and_returns_fresh_nested_trees(monkeypatch):
    calls=[];original=codec._decode_payload_uncached
    def spy(*a):calls.append(a);return original(*a)
    monkeypatch.setattr(codec,'_decode_payload_uncached',spy)
    args=row({'nested':[{'value':1.0}],'zero':-0.0});first=codec.decode_payload(*args);first['nested'][0]['value']='edited'
    second=codec.decode_payload(*args)
    assert len(calls)==1 and second['nested'][0]['value']==1.0 and type(second['nested'][0]['value'])is float
    assert math.copysign(1,second['zero'])==-1 and first is not second
    assert codec._verified_decode_json.cache_info().hits==1

@pytest.mark.parametrize('name',['table','field','owner_id','workspace_id'])
def test_warm_proof_never_transfers_to_changed_actual_row_identity(name):
    args=list(row({'v':1}));codec.decode_payload(*args);args[2]={**OWNER,name:'other'};rejected(args)
    assert codec._verified_decode_json.cache_info().currsize==1

def test_changed_marker_or_envelope_is_not_a_warm_hit():
    args=list(row({'v':1}));codec.decode_payload(*args)
    changed=args.copy();changed[1]=codec.new_marker();rejected(changed)
    changed=args.copy();e=json.loads(changed[0]);e['payload_hash']='0'*64;changed[0]=codec.canonical_json(e);rejected(changed)
    changed=args.copy();changed[0]+=' ';rejected(changed)
    assert codec._verified_decode_json.cache_info().hits==0

@pytest.mark.parametrize('projection',['{"v":true}','{"v":1.0000000000000001}','{"v":2}','{"v":1,"v":1}'])
def test_changed_projection_gets_original_exact_value_and_duplicate_key_checks(projection):
    args=list(row({'v':1.0}));codec.decode_payload(*args);args[3]=projection;rejected(args)

@pytest.mark.parametrize(('value','projection'),[(1.0,'1'),(-0.0,'0'),(10**100,'1e100'),(0.1,'0.100000'),(True,'true')])
def test_warm_cache_keeps_original_numeric_types_and_exact_decimal_equivalence(value,projection):
    args=list(row(value));args[3]=projection
    for _ in range(2):
        decoded=codec.decode_payload(*args);assert type(decoded)is type(value) and codec.canonical_json(decoded)==codec.canonical_json(value)

def test_new_valid_body_same_row_is_decoded_from_new_bytes():
    before=row({'v':1});after=row({'v':2});assert codec.decode_payload(*before)=={'v':1}
    assert codec.decode_payload(*after)=={'v':2};assert codec.decode_payload(*before)=={'v':1}
    assert codec._verified_decode_json.cache_info().misses==2

def test_failed_inputs_are_never_retained():
    args=list(row({'v':1}));args[3]='{"v":2}'
    for _ in range(2):rejected(args)
    info=codec._verified_decode_json.cache_info();assert info.currsize==0 and info.misses==2

def test_large_utf8_and_legacy_inputs_keep_uncached_checks(monkeypatch):
    args=row({'中文':'森林'});size=len(args[0])+len(args[3]);monkeypatch.setattr(codec,'_DECODE_CACHE_MAX_BYTES',size)
    for _ in range(2):assert codec.decode_payload(*args)=={'中文':'森林'}
    assert codec._verified_decode_json.cache_info().misses==0
    assert codec.decode_payload(None,codec.LEGACY_MARKER,OWNER,'{"v":1}')=={'v':1}
    rejected((args[0],codec.LEGACY_MARKER,OWNER,args[3]))
    assert codec._verified_decode_json.cache_info().currsize==0

def test_bounded_cache_evicts_full_old_keys():
    for index in range(20):assert codec.decode_payload(*row({'v':index}))=={'v':index}
    info=codec._verified_decode_json.cache_info();assert info.currsize==info.maxsize==16
    codec.decode_payload(*row({'v':0}));assert codec._verified_decode_json.cache_info().misses==21

def test_parallel_readers_each_receive_detached_values():
    args=row({'v':[1.0]})
    with ThreadPoolExecutor(max_workers=4) as pool:values=list(pool.map(lambda _:codec.decode_payload(*args),range(12)))
    for value in values:value['v'].append('local')
    assert len({id(v['v']) for v in values})==12 and codec.decode_payload(*args)=={'v':[1.0]}

def test_nullable_workspace_is_separate_from_an_actual_workspace():
    nullable={**OWNER,'workspace_id':None};args=row({'v':1},nullable);assert codec.decode_payload(*args)=={'v':1}
    rejected((args[0],args[1],OWNER,args[3]))
