"""Closed commands and deterministic manual-flow transitions for a formal continuation.

This module never reads a database, dispatches work or accepts a Goal.
"""
import copy
from typing import Annotated, Literal, Any
from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator
from .business_decision_contracts_v274 import Closed, ID, Hash, Text

SCHEMA='formal-continuation-plan-v345'
COMMAND='formal-continuation-command-v345'
INDEX='formal_continuation_index_v345'
ACTIONS={name:'workflow.formal_continuation.'+name+'_v345' for name in ('create','activate','complete_step','return_step','withdraw')}
ABANDON='workflow.formal_continuation.abandoned_v345'

class Preview(Closed):
    request_id: ID
    expected_task_version: Annotated[StrictInt,Field(ge=1)]
    execution_id: ID
    context_hash: Hash
    acknowledge_cross_version: StrictBool
    reason: Text

    @field_validator('reason')
    @classmethod
    def reason_valid(cls, value):
        if '\x00' in value: raise ValueError('说明不能包含空字符')
        value.encode('utf-8',errors='strict');return value

class Create(Preview):
    preview_hash: Hash

class Action(Closed):
    request_id: ID
    expected_version: Annotated[StrictInt,Field(ge=1)]
    reason: Text

    @field_validator('reason')
    @classmethod
    def reason_valid(cls, value):
        if '\x00' in value: raise ValueError('说明不能包含空字符')
        value.encode('utf-8',errors='strict');return value

class CompleteStep(Action):
    step_key: Annotated[StrictStr,Field(min_length=1,max_length=200)]
    result: Annotated[StrictStr,Field(min_length=1,max_length=12000,pattern=r'\S')]

    @field_validator('result')
    @classmethod
    def result_valid(cls, value):
        if '\x00' in value: raise ValueError('内容不能包含空字符')
        value.encode('utf-8',errors='strict');return value

class ReturnStep(Action):
    step_key: Annotated[StrictStr,Field(min_length=1,max_length=200)]

class Abandon(Closed):
    request_id: ID
    operation: Literal['create','activate','complete_step','return_step','withdraw']
    plan_id: ID | None
    original_body: dict[str,Any]
    reason: Text

    @model_validator(mode='after')
    def original_is_closed(self):
        models={'create':Create,'activate':Action,'complete_step':CompleteStep,'return_step':ReturnStep,'withdraw':Action}
        value=models[self.operation].model_validate(self.original_body).model_dump(mode='json')
        if value['request_id']!=self.request_id or (self.operation=='create')!=(self.plan_id is None):
            raise ValueError('原申请编号或范围不一致')
        if '\x00' in self.reason:raise ValueError('说明不能包含空字符')
        self.reason.encode('utf-8',errors='strict');self.original_body=value
        return self

class TransitionError(ValueError):pass

def initial():return {'phase':'draft','next_step':0,'completed':{}}

def apply(state,operation,body,actor_id,definition,*,manager=False):
    """Apply one verified actor command; caller checks current permissions/locks."""
    state=copy.deepcopy(state);steps=definition['steps'];phase=state['phase'];idx=state['next_step']
    if operation=='activate':
        if phase!='draft' or not manager:raise TransitionError('当前不能确认续段')
        state['phase']='in_progress';return state
    if operation=='withdraw':
        if phase not in {'draft','in_progress','awaiting_acceptance'} or not manager:raise TransitionError('当前不能撤回续段')
        state['phase']='withdrawn';return state
    if operation=='return_step' and phase=='awaiting_acceptance':
        step=steps[-1]
        if actor_id!=step['actor_id'] or body['step_key']!=step['key']:
            raise TransitionError('请由最后一步的责任账号退回')
        state['completed'].pop(step['key'],None);state['next_step']=len(steps)-1;state['phase']='in_progress'
        return state
    if operation not in {'complete_step','return_step'} or phase!='in_progress' or idx>=len(steps):
        raise TransitionError('当前没有可办理的步骤')
    step=steps[idx]
    if actor_id!=step['actor_id'] or body['step_key']!=step['key']:
        raise TransitionError('请由当前步骤指定的责任账号办理')
    if operation=='complete_step':
        state['completed'][step['key']]={'result':body['result'],'actor_id':actor_id}
        state['next_step']=idx+1
        if idx+1==len(steps):state['phase']='awaiting_acceptance'
        return state
    if idx==0:
        state['phase']='draft';return state
    previous=steps[idx-1]['key'];state['completed'].pop(previous,None);state['next_step']=idx-1
    return state
