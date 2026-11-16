"""Pure price arithmetic and real pure transport preparation, no IO authority.

Official metadata and basis/quota/model IDs here are synthetic references.
The CNY 900/2700 minor-unit rates are the parent-reviewed peak/cache-miss test
facts, not a captured official document, a real Price Audit or a sending grant.
The server-limited preparation below is a pure shadow used to exercise the
proposed contract; this does not claim that the installed sender limits tokens.
"""
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from generic_agents import same_goal_advice_money_v305 as money
from generic_agents import cloud_business_reasoning_v197 as transport
from generic_agents.contracts import Manifest, digest

AT = datetime(2026, 11, 2, 21, tzinfo=timezone.utc)


def uid(): return str(uuid4())


@pytest.fixture
def scene():
    model = Manifest(kind='foundation_model',namespace='price_contract_fixture',name='model',version='1.0.0',
        owner='pure fixture',description='No real published model or configured credential',
        input_schema={'type':'object'},output_schema={'type':'object'},allowed_agent_roles=['business'],
        execution={'provider':'deepseek','model':'deepseek-v4-pro','thinking_mode':'disabled',
            'max_tokens':4096,'secret_ref':'DEEPSEEK_API_KEY'},
        data_policy={'cloud_allowed':True},budget={'max_attempts':1,'max_concurrency':1},
        dependencies=[],specification={}).model_dump(mode='json')
    pin={'id':uid(),'content_hash':digest(model),'release_version':'1.0.0'}
    budget={'quota_policy_ref':{'id':uid(),'record_hash':'b'*64},'provider':'deepseek','max_tokens':1024,
        'max_request_bytes':50000,'max_evidence_items':2,'max_cost_minor_units':100,'currency':'CNY',
        'max_actual_calls':1,'reserved_calls':1,'reserved_steps':1,'goal_max_tool_calls':12,'goal_max_steps':12}
    messages=[{'role':'system','content':'按固定合同形成供人工核对的建议。'},
        {'role':'user','content':[{'type':'text','text':'温度 21℃；只比较已批准资料。'}]}]
    schema={'type':'object','properties':{'advice':{'type':'string'}},'required':['advice'],'additionalProperties':False}
    limited=copy.deepcopy(model);limited['execution']['max_tokens']=1024
    prepared=transport.prepare(NS(data=limited),messages,schema)
    unsigned={'schema':money.PRICE,'basis_ref':{'id':uid(),'record_hash':'a'*64},
        'official_original':{'url':money.OFFICIAL_URL,'sha256':'c'*64,'size_bytes':4096},
        'provider':'deepseek','model':'deepseek-v4-pro','currency':'CNY','rate_unit_tokens':1_000_000,
        'input_cache_miss_minor_per_million':900,'output_minor_per_million':2700,
        'cache_discount_assumed':False,'off_peak_discount_assumed':False,
        'checked_at':(AT-timedelta(hours=1)).isoformat(),'expires_at':(AT+timedelta(hours=1)).isoformat(),
        'database_price_authorization_verified':False,'provider_price_lock_guaranteed':False}
    price=money.seal_price(unsigned,now=AT)
    return NS(model=model,pin=pin,budget=budget,prepared=prepared,price=price,unsigned=unsigned,
        messages=messages,schema=schema)


def quote(s,**overrides):
    values={'now':AT,'prepared':s.prepared,'model_pin':s.pin,'model_snapshot':s.model,'budget':s.budget}
    values.update(overrides)
    return money.quote(s.price,**values)


def check(s,value,**overrides):
    values={'now':AT,'prepared':s.prepared,'model_pin':s.pin,'model_snapshot':s.model,'budget':s.budget}
    values.update(overrides)
    return money.check_quote(value,s.price,**values)


def rehash(value):
    value=copy.deepcopy(value);value['record_hash']=money.record_hash(value);return value


def test_actual_pure_prepare_prefix_and_server_limited_output_are_quoted_exactly(scene):
    s=scene;before=copy.deepcopy((s.model,s.prepared,s.price,s.budget))
    value=quote(s)
    assert s.model['execution']['max_tokens']==4096 and value['output_token_bound']==1024
    # prepare() really inserted the registered JSON-schema system prefix.
    assert len(s.prepared['messages'])==len(s.messages)+1
    assert s.prepared['text_bytes']>sum(len(str(x['content']).encode('utf-8')) for x in s.messages)
    assert value['input_token_bound']==s.prepared['text_bytes']+1024
    assert value['total_token_bound']==s.prepared['token_bound']
    exact=(Decimal(value['input_token_bound'])*Decimal(900)+Decimal(1024)*Decimal(2700))/Decimal(1_000_000)
    assert value['maximum_cost_minor_units']==int(exact.to_integral_value(rounding=ROUND_CEILING))
    assert value['maximum_cost_minor_units']<=100
    assert value['purpose']=='calculation-only-not-a-send-grant'
    assert all(value[key] is False for key in money.FLAGS)
    assert check(s,value,now=AT+timedelta(minutes=1))==value
    value['model_pin']['id']=uid()
    assert (s.model,s.prepared,s.price,s.budget)==before


def test_original_4096_transport_cannot_be_priced_as_only_1024(scene):
    s=scene
    original=transport.prepare(NS(data=s.model),s.messages,s.schema)
    assert original['options']['max_tokens']==4096
    with pytest.raises(ValueError):quote(s,prepared=original)
    altered=copy.deepcopy(original);altered['options']['max_tokens']=1024
    altered['dispatch_options_hash']=digest(altered['options'])
    # Old token_bound remains 4096-based; adjusting only options/hash is false.
    with pytest.raises(ValueError):quote(s,prepared=altered)


@pytest.mark.parametrize('counts,expected',[
    ((0,0,0,0),0),((1,0,1,0),1),((1_000_000,0,900,2700),900),
    ((12345,1024,900,2700),14),((0,1,0,1),1)])
def test_minor_units_round_up_exactly_without_float_or_fx(counts,expected):
    assert money.ceil_cost_minor(*counts)==expected


@pytest.mark.parametrize('bad',[-1,True,1.5,float('inf'),float('nan'),'900'])
def test_integer_rates_and_counts_reject_coercion_and_nonfinite_values(bad):
    with pytest.raises(ValueError):money.ceil_cost_minor(1,2,bad,2700)


@pytest.mark.parametrize('fault',['expired','future-check','overlong','naive-date','wrong-url',
    'usd','float-unit','negative-rate','float-rate','bool-rate','fake-auth','price-lock','extra'])
def test_price_basis_is_closed_and_time_bounded_without_claiming_authority(scene,fault):
    s=scene;raw=copy.deepcopy(s.unsigned)
    if fault=='expired':raw['expires_at']=AT.isoformat()
    elif fault=='future-check':raw['checked_at']=(AT+timedelta(minutes=1)).isoformat()
    elif fault=='overlong':raw['expires_at']=(AT+timedelta(days=2)).isoformat()
    elif fault=='naive-date':raw['checked_at']='2026-11-02T20:00:00'
    elif fault=='wrong-url':raw['official_original']['url']='https://example.org/pricing'
    elif fault=='usd':raw['currency']='USD'
    elif fault=='float-unit':raw['rate_unit_tokens']=1000000.0
    elif fault=='negative-rate':raw['input_cache_miss_minor_per_million']=-1
    elif fault=='float-rate':raw['input_cache_miss_minor_per_million']=900.0
    elif fault=='bool-rate':raw['input_cache_miss_minor_per_million']=False
    elif fault=='fake-auth':raw['database_price_authorization_verified']=True
    elif fault=='price-lock':raw['provider_price_lock_guaranteed']=True
    else:raw['external_python']='caller-supplied'
    with pytest.raises(ValueError):money.seal_price(raw,now=AT)


@pytest.mark.parametrize('fault',['text-underreported','tokens-underreported','bad-message-hash','bad-options-hash',
    'thinking-enabled','tools','search','fetch','image','bool-images','bool-max-output','extra-prepared','huge-text'])
def test_actual_request_is_recounted_and_tools_reasoning_images_or_drift_are_refused(scene,fault):
    s=scene;value=copy.deepcopy(s.prepared)
    if fault=='text-underreported':value['text_bytes']-=1
    elif fault=='tokens-underreported':value['token_bound']-=1
    elif fault=='bad-message-hash':value['request_hash']='0'*64
    elif fault=='bad-options-hash':value['dispatch_options_hash']='0'*64
    elif fault=='thinking-enabled':value['options']['thinking']['type']='enabled'
    elif fault in {'tools','search','fetch'}:value['options'][fault]=[]
    elif fault=='image':
        value['messages'].append({'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,AA'}}]})
        value['image_count']=1
    elif fault=='bool-images':value['image_count']=False
    elif fault=='bool-max-output':value['options']['max_tokens']=True
    elif fault=='extra-prepared':value['source_authority']=True
    else:
        value['messages'][0]['content']='a'*(money.MAX_TEXT_BYTES+1)
        value['request_hash']=digest(value['messages'])
        value['text_bytes']=money.MAX_TEXT_BYTES+1
        value['token_bound']=value['text_bytes']+1024+value['options']['max_tokens']
    with pytest.raises(ValueError):quote(s,prepared=value)


@pytest.mark.parametrize('fault',['wrong-pin','wrong-version','thinking-absent','other-model','other-provider',
    'lower-token-budget','usd-budget','money-budget-too-low','wire-bytes-too-small','naive-clock'])
def test_quote_rebinds_model_options_currency_and_real_configured_ceilings(scene,fault):
    s=scene;model=copy.deepcopy(s.model);pin=copy.deepcopy(s.pin);budget=copy.deepcopy(s.budget);now=AT
    if fault=='wrong-pin':pin['content_hash']='0'*64
    elif fault=='wrong-version':pin['release_version']='2.0.0'
    elif fault=='thinking-absent':
        model['execution'].pop('thinking_mode');pin['content_hash']=digest(model)
    elif fault=='other-model':
        model['execution']['model']='deepseek-chat';pin['content_hash']=digest(model)
    elif fault=='other-provider':
        model['execution']['provider']='qwen';pin['content_hash']=digest(model)
    elif fault=='lower-token-budget':budget['max_tokens']=512
    elif fault=='usd-budget':budget['currency']='USD'
    elif fault=='money-budget-too-low':budget['max_cost_minor_units']=1
    elif fault=='wire-bytes-too-small':budget['max_request_bytes']=1
    else:now=AT.replace(tzinfo=None)
    with pytest.raises(ValueError):quote(s,model_snapshot=model,model_pin=pin,budget=budget,now=now)


@pytest.mark.parametrize('field',['maximum_cost_minor_units','input_token_bound','output_token_bound',
    'total_token_bound','request_body_bytes','request_hash','dispatch_options_hash','price_record_hash','budget_hash'])
def test_rehashed_quote_cannot_replace_exact_recomputed_cost_or_request_pins(scene,field):
    s=scene;value=quote(s)
    value[field]=value[field]+1 if type(value[field]) is int else '0'*64
    with pytest.raises(ValueError):check(s,rehash(value))


def test_quote_cannot_grow_permissions_or_survive_price_expiry(scene):
    s=scene;value=quote(s)
    value['cloud_send_authorized']=True
    with pytest.raises(ValueError):check(s,rehash(value))
    original=quote(s)
    with pytest.raises(ValueError):check(s,original,now=AT+timedelta(hours=1))
    assert money.check_price(s.price,now=AT)['database_price_authorization_verified'] is False

