"""Real HTTP feedback lifecycle; consumes retained originals without rerunning them."""
import hashlib
import json
from uuid import uuid4

ROOT='/api/local-rules/evolution'


def ok(response,status=200):
    assert response.status_code==status,response.text
    return response.json()


def command(client,method,path,body,status=200):
    raw=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    result=ok(client.request(method,path,content=raw,headers={'Content-Type':'application/json'}),status)
    return result,raw


def body(**fields):return {'request_id':str(uuid4()),**fields}


def recover(client,request,raw,path,method):
    return client.get(ROOT+'/requests/'+request['request_id'],params={
        'expected_sha256':hashlib.sha256(raw).hexdigest(),
        'original_method':method,'original_path':path})


def test_match_feedback_save_submit_revision_cancel_and_exact_command_recovery(client,threshold_cases):
    cases=threshold_cases;original=cases['observations']['matched']['record']
    context=ok(client.get(ROOT+'/observations/'+original['id']+'/feedback-context',
                          params={'rule_id':cases['rule_id']}))
    assert context['supported'] is True and context['feedback'] is None
    assert context['basis']['actual_hit'] is True and context['basis']['actual_value']==21
    assert 'proof' not in context['basis']
    create=body(observation_id=original['id'],rule_id=cases['rule_id'],
        expected_observation_version=original['version'],label=None,reason='')
    draft,create_wire=command(client,'POST',ROOT+'/feedback',create,201)
    identity=draft['id'];root=draft['root_id'];path=ROOT+'/feedback/'+identity
    assert root==identity and draft['version']==1 and draft['revision']==1
    assert draft['status']=='draft' and draft['classification']=='unknown'
    assert draft['latest_submitted'] is None and draft['can_submit'] is False
    assert ok(client.get(path))==draft
    save=body(expected_version=draft['version'],label='should_not_alert',
              reason='合成核查：21不应提示，用于误报反馈机制；不证明现场质量')
    saved,save_wire=command(client,'PUT',path,save)
    assert saved['version']==2 and saved['classification']=='FP'
    assert saved['basis']==draft['basis'] and saved['latest_submitted'] is None
    assert ok(client.get(path))==saved
    # Old-tab saves and a second root must not replace the operator's input.
    stale=body(expected_version=1,label='should_alert',reason='过期标签不得覆盖')
    assert client.put(path,json=stale).status_code==409
    duplicate={**create,'request_id':str(uuid4())}
    assert client.post(ROOT+'/feedback',json=duplicate).status_code==409
    assert ok(client.get(path))==saved
    submit=body(expected_version=saved['version'],confirm=True)
    submitted,_=command(client,'POST',path+'/submit',submit)
    assert submitted['status']=='submitted' and submitted['version']==3
    assert submitted['latest_submitted']['id']==identity
    # Same original request can be recovered after later state changes, but no
    # new write is made. Replays return current cold-checked state explicitly.
    repeated=ok(client.request('PUT',path,content=save_wire,
                              headers={'Content-Type':'application/json'}))
    assert repeated==submitted
    recovered=ok(recover(client,save,save_wire,path,'PUT'))
    assert recovered['found'] is True and recovered['feedback']==submitted
    altered=save_wire+b' '
    assert client.request('PUT',path,content=altered,
                          headers={'Content-Type':'application/json'}).status_code==409
    assert recover(client,save,altered,path,'PUT').status_code==409
    assert recover(client,save,save_wire,path+'/submit','POST').status_code==409
    assert ok(client.get(path))==submitted
    revision=body(expected_head_revision=1,label='should_alert',reason='待补充核查，草稿不代替原判断')
    revised,_=command(client,'POST',ROOT+'/feedback/'+root+'/revisions',revision,201)
    assert revised['revision']==2 and revised['previous_id']==identity
    assert revised['latest_submitted']['id']==identity
    assert revised['latest_submitted']['classification']=='FP'
    cancel=body(expected_version=revised['version'],reason='取消尚未核实的修订')
    cancelled,_=command(client,'POST',ROOT+'/feedback/'+revised['id']+'/cancel',cancel)
    assert cancelled['status']=='cancelled' and cancelled['latest_submitted']['id']==identity
    revision3=body(expected_head_revision=2,label='uncertain',reason='保留尚不能判断，不作为负样本')
    latest,_=command(client,'POST',ROOT+'/feedback/'+root+'/revisions',revision3,201)
    assert latest['revision']==3 and latest['previous_id']==revised['id']
    assert latest['classification']=='unknown' and latest['latest_submitted']['id']==identity
    assert [row['status'] for row in latest['history']]==['submitted','cancelled','draft']
    assert ok(client.get(ROOT+'/feedback',params={'observation_id':original['id']}))['feedback']==[latest]
    assert ok(recover(client,create,create_wire,ROOT+'/feedback','POST'))['feedback']['id']==identity


def test_real_no_match_feedback_context_preserves_the_original_miss(client,threshold_cases):
    cases=threshold_cases;record=cases['observations']['nomatch']['record']
    context=ok(client.get(ROOT+'/observations/'+record['id']+'/feedback-context',
                          params={'rule_id':cases['rule_id']}))
    assert context['supported'] is True and context['issue'] is None
    assert context['basis']['actual_hit'] is False and context['basis']['actual_value']==19
    feedback=context['feedback']
    assert feedback['label']=='should_alert' and feedback['classification']=='FN'
    assert feedback['status']=='draft' and feedback['latest_submitted'] is None
    assert feedback['basis']['actual_hit'] is False
