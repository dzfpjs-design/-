"""Actual ASGI validation/commit boundaries; domain/auth IO is explicit."""
import pytest
from uuid import uuid4
from generic_agents import same_goal_advice_api_v304 as api
from tests.test_same_goal_advice_api_v304 import environment
from tests.test_same_goal_advice_contracts_v302 import scene


@pytest.mark.parametrize('kind',['price','preview'])
def test_new_write_routes_reject_caller_urls_or_messages_and_commit_only_real_service_result(monkeypatch,environment,kind):
    e=environment;v=scene();calls=[]
    if kind=='price':
        path='/api/workspace/business-advice/price-bases';module=api.prices;name='authorize';prefix=()
        body={'request_id':str(uuid4()),'model_pin':v.preparation['model_pin'],
            'validity_seconds':600,'reason':'登记固定官方页面的完整价格原件'}
        untrusted={'url':'https://attacker.invalid/price','rates':{'output':0}}
    else:
        path=e.base+'/preparations/plan/preview';module=api.previews;name='prepare';prefix=('task','plan')
        body={'request_id':str(uuid4()),'expected_plan_version':2,
            'expected_goal_hash':v.preparation['scope']['goal_hash'],
            'expected_mature_head_hash':v.preparation['mature_head']['head_hash'],'reason':'核对实际脱敏发送全文'}
        untrusted={'messages':[{'role':'user','content':'replace registered evidence'}]}
    def save(actual,principal,*args):
        assert actual is e.db and principal is e.p and args==(*prefix,body) and e.db.commits==0
        calls.append(args);return {'saved':True,'cloud_send_authorized':False}
    monkeypatch.setattr(module,name,save)
    result=e.client.post(path,json={**body,**untrusted})
    assert result.status_code==422 and not calls and e.db.commits==0
    result=e.client.post(path,json=body)
    assert result.status_code==201 and result.json()=={'saved':True,'cloud_send_authorized':False}
    assert len(calls)==1 and e.db.commits==1


@pytest.mark.parametrize('request_id',[None,'original-request'])
def test_latest_and_original_preview_read_routes_do_not_commit_or_prepare_again(monkeypatch,environment,request_id):
    e=environment;calls=[]
    def read(actual,principal,task_id,plan_id,request_id=None):
        assert actual is e.db and principal is e.p and task_id=='task' and plan_id=='plan'
        calls.append(request_id);return {'phase':'historical_preview' if request_id else 'preview_saved','can_start':False}
    monkeypatch.setattr(api.previews,'get',read)
    monkeypatch.setattr(api.previews,'prepare',lambda *args:pytest.fail('GET must not generate another preview'))
    path=e.base+'/preparations/plan/'+('previews/operations/'+request_id if request_id else 'preview')
    result=e.client.get(path)
    assert result.status_code==200 and result.json()['can_start'] is False and calls==[request_id] and e.db.commits==0


def test_failed_durable_preview_commit_never_returns_saved_success(monkeypatch,environment):
    e=environment;v=scene()
    monkeypatch.setattr(api.previews,'prepare',lambda *args:{'saved':True})
    def fail():raise RuntimeError('preview durability unavailable')
    e.db.commit=fail
    body={'request_id':str(uuid4()),'expected_plan_version':2,'expected_goal_hash':v.preparation['scope']['goal_hash'],
        'expected_mature_head_hash':v.preparation['mature_head']['head_hash'],'reason':'保存预览'}
    with pytest.raises(RuntimeError,match='preview durability unavailable'):
        e.client.post(e.base+'/preparations/plan/preview',json=body)
