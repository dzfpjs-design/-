from uuid import uuid4
import pytest
from pydantic import ValidationError
from generic_agents import formal_continuation_contracts_v345 as c

@pytest.fixture
def definition():return {'steps':[{'key':'review','actor_id':'reviewer'}, {'key':'first','actor_id':'clerk'}, {'key':'second','actor_id':'chief'}]}

def apply(state,op,actor,definition,**body):return c.apply(state,op,body,actor,definition,manager=actor=='owner')

def test_steps_are_ordered_and_never_complete_the_goal(definition):
 s=apply(c.initial(),'activate','owner',definition)
 for key,actor in [('review','reviewer'),('first','clerk'),('second','chief')]:
  s=apply(s,'complete_step',actor,definition,step_key=key,result='已处理')
 assert s['phase']=='awaiting_acceptance' and len(s['completed'])==3
 with pytest.raises(c.TransitionError):apply(s,'complete_step','chief',definition,step_key='second',result='重复')

def test_current_actor_can_return_one_step_without_losing_history(definition):
 s=apply(c.initial(),'activate','owner',definition);s=apply(s,'complete_step','reviewer',definition,step_key='review',result='初核')
 s=apply(s,'return_step','clerk',definition,step_key='first')
 assert s['next_step']==0 and s['completed']=={}
 s=apply(s,'complete_step','reviewer',definition,step_key='review',result='补充核对')
 assert s['completed']['review']['result']=='补充核对'

def test_other_actor_and_wrong_step_cannot_act(definition):
 s=apply(c.initial(),'activate','owner',definition)
 for actor,key in [('owner','review'),('clerk','review'),('reviewer','first')]:
  with pytest.raises(c.TransitionError):apply(s,'complete_step',actor,definition,step_key=key,result='伪造')

def test_withdraw_retains_completed_content_and_requires_manager(definition):
 s=apply(c.initial(),'activate','owner',definition);s=apply(s,'complete_step','reviewer',definition,step_key='review',result='保留')
 with pytest.raises(c.TransitionError):apply(s,'withdraw','clerk',definition)
 s=apply(s,'withdraw','owner',definition);assert s['phase']=='withdrawn' and s['completed']['review']['result']=='保留'
 with pytest.raises(c.TransitionError):apply(s,'activate','owner',definition)

def test_closed_contract_rejects_forged_result_and_non_boolean_ack():
 base={'request_id':str(uuid4()),'expected_task_version':1,'execution_id':str(uuid4()),'context_hash':'a'*64,'acknowledge_cross_version':True,'reason':'确认资料范围'}
 assert c.Preview.model_validate(base).acknowledge_cross_version
 with pytest.raises(ValidationError):c.Preview.model_validate({**base,'acknowledge_cross_version':'true'})
 with pytest.raises(ValidationError):c.Preview.model_validate({**base,'approved':True})
 with pytest.raises(ValidationError):c.CompleteStep.model_validate({'request_id':str(uuid4()),'expected_version':1,'reason':'提交','step_key':'review','result':'  '})

def test_null_text_cannot_be_saved():
 with pytest.raises(ValidationError):
  c.CompleteStep.model_validate({'request_id':str(uuid4()),'expected_version':1,'reason':'提交','step_key':'review','result':'a\x00b'})


def test_last_assignee_can_return_completed_last_step_without_goal_acceptance(definition):
 s=apply(c.initial(),'activate','owner',definition)
 for key,actor in [('review','reviewer'),('first','clerk'),('second','chief')]:
  s=apply(s,'complete_step',actor,definition,step_key=key,result='已处理')
 with pytest.raises(c.TransitionError):apply(s,'return_step','clerk',definition,step_key='second')
 s=apply(s,'return_step','chief',definition,step_key='second')
 assert s['phase']=='in_progress' and s['next_step']==2 and 'second' not in s['completed']

def test_abandonment_requires_exact_original_scope():
 original={'request_id':str(uuid4()),'expected_version':1,'reason':'确认'}
 good={'request_id':original['request_id'],'operation':'activate','plan_id':str(uuid4()),'original_body':original,'reason':'结束原申请'}
 assert c.Abandon.model_validate(good).original_body==original
 with pytest.raises(ValidationError):c.Abandon.model_validate({**good,'request_id':str(uuid4())})
 with pytest.raises(ValidationError):c.Abandon.model_validate({**good,'plan_id':None})
 with pytest.raises(ValidationError):c.Abandon.model_validate({**good,'original_body':{**original,'extra':'x'}})
