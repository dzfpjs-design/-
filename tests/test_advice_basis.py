"""Saved evidence projection, not model semantic or field accuracy evaluation."""
import json
from generic_agents.advice_basis import build

def reference(identity,value,cut=False):
    return {'id':identity,'label':'公开依据','text':json.dumps(value,ensure_ascii=False),'excerpt_truncated':cut}

def record(identity,group,count,**patch):
    return reference(identity,{'name':'同名文件','status':'succeeded','kind':'segmentation','integrity_verified':True,
        'source_group':group,'original_identity_basis':'verified_original_byte_set' if group else 'original_identity_unavailable',
        'metrics':{'candidate_masks':count},'reported_fields':[],'source_time_verified':False,**patch})

def test_distinct_original_counts_are_not_a_verified_conflict():
    audit=build([record('record:0','原件组1',3),record('record:1','原件组2',4)])
    assert [r['values'][0]['value'] for r in audit['records']]==[3,4]
    pair=audit['comparisons'][0];assert pair['relation']=='different_originals' and not pair['direct_comparison_supported']
    assert '不能仅凭候选数量差异认定冲突' in pair['explanation']

def test_same_bytes_do_not_prove_same_event_or_independent_measurement():
    audit=build([record('record:0','原件组1',4),record('record:1','原件组1',4)])
    pair=audit['comparisons'][0];assert pair['relation']=='same_originals'
    assert '不证明采集事件相同或观测独立' in pair['explanation'] and not pair['direct_comparison_supported']

def test_unknown_identity_and_incomplete_record_do_not_create_false_facts():
    audit=build([record('record:0',None,3),record('record:1','原件组1',4),record('record:2','原件组1',99,integrity_verified=False)])
    assert audit['comparisons'][0]['relation']=='identity_unverified'
    assert audit['records'][2]['values']==[] and audit['records'][2]['source_group'] is None

def test_saved_review_and_completed_progress_are_explicit_without_reinterpreting_them():
    refs=[reference('signal',{'business_review':{'decision':'not_abnormal','note':'已核对公开工程结果'}}),
          reference('original-task',{'title':'公开流程','status':'workflow_completed','completed_steps':[{'name':'人工核查','note':'说明已经保存'}]})]
    audit=build(refs)
    assert audit['original_task']['completed'] and audit['original_task']['saved_steps'][0]['note']=='说明已经保存'
    assert audit['saved_business_review']['decision']=='不属于业务异常'
    assert '不等于领域结论' in audit['saved_business_review']['limit']

def test_waiting_progress_does_not_become_completed_or_missing_review_become_reviewed():
    audit=build([reference('signal',{'business_review':{}}),reference('original-task',{'title':'公开流程','status':'workflow_waiting','completed_steps':[]})])
    assert not audit['original_task']['completed'] and audit['saved_business_review'] is None

def test_truncated_invalid_and_legacy_excerpts_remain_unknown():
    refs=[record('record:0','原件组1',3),reference('original-task',{'status':'workflow_completed'},True),
          {'id':'record:1','text':'{"metrics":','excerpt_truncated':False},
          reference('record:2',{'kind':'segmentation','status':'succeeded','name':'旧记录','integrity_verified':True,'metrics':{'candidate_masks':4}})]
    audit=build(refs);assert audit['original_task'] is None
    assert audit['omitted_references']==['original-task','record:1']
    assert audit['records'][1]['source_group'] is None and audit['comparisons'][0]['relation']=='identity_unverified'

def test_projection_bounds_values_and_preserves_untrusted_registered_origin():
    fields=[{'name':'字段'+str(i),'value':i} for i in range(9)]+[{'name':'长内容','value':'x'*201}]
    audit=build([record('record:0',None,3,metrics={},reported_fields=fields)])
    row=audit['records'][0];assert len(row['values'])==6 and row['values_omitted']==3
    assert all('未证明为独立测量' in v['origin'] for v in row['values'])

def test_pending_failed_and_cancelled_metrics_do_not_become_completed_measurements():
    for status in ['queued','running','failed','cancelled']:
        audit=build([record('record:0','原件组1',0,status=status)])
        assert not audit['records'][0]['values'] and audit['records'][0]['processing_status']==status

def test_uncertain_review_is_saved_but_not_changed_to_abnormal_or_normal():
    audit=build([reference('signal',{'business_review':{'decision':'uncertain','note':'尚缺现场资料'}})])
    assert audit['saved_business_review']['decision']=='尚不能判断'

def test_structured_quote_matches_complete_frozen_object_with_strict_types_and_no_duplicates():
    from generic_agents.advice_basis import matches_frozen_quote
    refs=[record('record:0','原件组1',3),record('record:1','原件组2',4)]
    audit=build(refs);context={'references':refs,'basis_audit':audit};pair=audit['comparisons'][0]
    reordered=dict(reversed(list(pair.items())))
    assert matches_frozen_quote(json.dumps(reordered,ensure_ascii=False),context)
    for value in [{**pair,'direct_comparison_supported':0},{**pair,'relation':'conflict'}, {'relation':pair['relation']},42]:
        assert not matches_frozen_quote(json.dumps(value),context)
    duplicate=json.dumps(pair).replace('"relation":', '"relation":"conflict","relation":',1)
    assert not matches_frozen_quote(duplicate,context)
    changed={**audit,'limit':'changed outside frozen projection'}
    assert not matches_frozen_quote(json.dumps(pair),{**context,'basis_audit':changed})
