"""Pure synthetic checks; never connect to a database or provider."""
from types import SimpleNamespace

import pytest

from generic_agents.cloud_report_plan_allowance import PlanAllowanceDenied, plan_from_preview, validate_progress
from generic_agents.contracts import digest


def preview():
    return {'map_preparation':{'generation_contract':{'schema':'report-evidence-map-generation-v3',
        'dispatch_options':{'max_tokens':99},'input_policy':{'single_batch':True}},
        'plan':{'chunks':[{'chunk_id':'map-1','messages_hash':digest({'x':1}), 'input_summary':{'token_bound':100}},
                          {'chunk_id':'map-2','messages_hash':digest({'x':2}), 'input_summary':{'token_bound':120}}]}}}


def call(stage, **change):
    item={'status':'succeeded','reserved_tokens':stage['token_bound'],'charged_tokens':20,
          'workspace_id':'w','capability_id':'c','data':{'actor_id':'a','request_hash':stage['request_hash'],
          'dispatch_options_hash':stage['options_hash'],'input_policy_hash':stage['input_policy_hash'],
          'scoped_extra':{'job_id':'j','stage_id':stage['stage_id']},
          'receipt':{'call_id':'first','usage':{'total_tokens':20}}}}
    item.update(change)
    return SimpleNamespace(**item)


def progress(stages, consumed, calls):
    return validate_progress(stages,consumed,calls,workspace_id='w',actor_id='a',capability_id='c',job_id='j')


def test_server_plan_bounds_are_exact_and_ordered():
    stages=plan_from_preview(preview())
    assert [x['stage_id'] for x in stages]==['map-1','map-2']
    assert sum(x['token_bound'] for x in stages)==220
    assert len({x['request_hash'] for x in stages})==2


@pytest.mark.parametrize('case',['duplicate_stage','duplicate_message','bad_bound','too_many_tokens','wrong_version'])
def test_ambiguous_or_unbounded_plan_is_rejected(case):
    source=preview();chunks=source['map_preparation']['plan']['chunks']
    if case=='duplicate_stage':chunks[1]['chunk_id']=chunks[0]['chunk_id']
    elif case=='duplicate_message':chunks[1]['messages_hash']=chunks[0]['messages_hash']
    elif case=='bad_bound':chunks[0]['input_summary']['token_bound']=True
    elif case=='too_many_tokens':chunks[0]['input_summary']['token_bound']=2_000_000
    else:source['map_preparation']['generation_contract']['schema']='old'
    with pytest.raises(PlanAllowanceDenied):plan_from_preview(source)


def test_progress_counts_reserved_not_refunded_tokens():
    stages=plan_from_preview(preview());entry={'stage_id':'map-1','call_id':'first','token_bound':100}
    assert progress(stages,[entry],{'first':call(stages[0])})==100


@pytest.mark.parametrize('changed',[
    {'status':'reserved'},{'status':'failed'},{'charged_tokens':101},
    {'workspace_id':'other'},{'capability_id':'other'},{'charged_tokens':True},
])
def test_unknown_failed_overspent_or_cross_scope_never_allows_next_stage(changed):
    stages=plan_from_preview(preview());entry={'stage_id':'map-1','call_id':'first','token_bound':100}
    with pytest.raises(PlanAllowanceDenied):progress(stages,[entry],{'first':call(stages[0],**changed)})


def test_stage_skipping_duplicate_calls_and_forged_messages_fail_closed():
    stages=plan_from_preview(preview());e1={'stage_id':'map-1','call_id':'first','token_bound':100}
    e2={'stage_id':'map-2','call_id':'first','token_bound':120}
    with pytest.raises(PlanAllowanceDenied):progress(stages,[e2],{'first':call(stages[1])})
    with pytest.raises(PlanAllowanceDenied):progress(stages,[e1,e2],{'first':call(stages[0])})
    c=call(stages[0]);c.data['request_hash']=digest({'forged':1})
    with pytest.raises(PlanAllowanceDenied):progress(stages,[e1],{'first':c})


def test_missing_provider_usage_blocks_next_stage():
    stages=plan_from_preview(preview());entry={'stage_id':'map-1','call_id':'first','token_bound':100}
    c=call(stages[0],charged_tokens=100);c.data['receipt']['usage']={}
    with pytest.raises(PlanAllowanceDenied):progress(stages,[entry],{'first':c})


def test_fourteen_stages_count_each_original_reservation_once():
    source=preview();source['map_preparation']['plan']['chunks']=[
        {'chunk_id':'stage-'+str(i),'messages_hash':digest({'n':i}),
         'input_summary':{'token_bound':100+i}} for i in range(14)]
    stages=plan_from_preview(source);used=[];calls={}
    for i,stage in enumerate(stages):
        cid='call-'+str(i);item={'stage_id':stage['stage_id'],'call_id':cid,'token_bound':stage['token_bound']}
        c=call(stage,charged_tokens=20);c.data['receipt']['call_id']=cid
        calls[cid]=c;used.append(item)
        assert progress(stages,used,calls)==sum(s['token_bound'] for s in stages[:i+1])
    calls['call-4'].charged_tokens=stages[4]['token_bound']+1
    with pytest.raises(PlanAllowanceDenied):progress(stages,used,calls)
