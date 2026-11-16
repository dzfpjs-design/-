"""Quality feedback-chain data flow, not DB/source/approval evidence.

Mapped feedback rows stand in for a complete chain returned by the existing
inventory boundary. Study/shadow authority and SQL reads are explicit seams.
The actual latest selection, evidence_context, sample projection, statistics,
feedback snapshot and require_current_basis comparison run unchanged. No test
here claims these synthetic proofs establish real quality or authorization.
"""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import quality_review_store_v152 as quality
from generic_agents import threshold_evolution_api_v147 as feedback
from generic_agents import threshold_feedback_history_v148 as history
from generic_agents import threshold_shadow_execution_v151 as shadow_execution
from generic_agents import threshold_shadow_store_v151 as shadow
from generic_agents import threshold_study_store_v149 as study
from generic_agents import threshold_study_views_v149 as study_views
from generic_agents.models import Job, Observation
from generic_agents.threshold_evolution_models_v147 import FeedbackRevision
from generic_agents.threshold_shadow_models_v151 import ThresholdShadowConsumption as Consumption
from generic_agents.typed_payload_codec_v131 import canonical_json


def identity(number):return f'155abc00-0000-4000-8000-{number:012x}'


WORKSPACE=identity(1);ACTOR=identity(2);EVALUATION=identity(3);SHADOW=identity(4)
EVAL_OBS=identity(5);SHADOW_OBS=identity(6);TRAIN_OBS=identity(7);TRAIN_ROOT=identity(8)
BASELINE=identity(9);ENTITY=identity(10);INSTANCE=identity(11);RELEASE=identity(12)
SOURCE=identity(13);PACKAGE=identity(14);STAMP=datetime(2026,10,21,tzinfo=timezone.utc)


def basis(observation,value):
    result={'schema':'threshold-evolution-feedback-basis-v147','observation_id':observation,
        'observation_version':4,'baseline_capability_id':BASELINE,'baseline_capability_hash':'a'*64,
        'rule_id':'count-rule','field':'count','field_label':'数量','unit':'棵','operator':'gt',
        'threshold':20,'actual_value':value,'actual_hit':value>20,'instance_id':INSTANCE,
        'release_id':RELEASE,'entity_id':ENTITY,'source_id':SOURCE,
        'proof':{'extractor':{'manifest':{'synthetic_only':True,'specification':{
            'managed_numeric_rule':{'package_id':PACKAGE}}}}}}
    result['basis_hash']=feedback._hash({key:item for key,item in result.items()
        if key not in {'basis_hash','observation_version'}})
    return result


def chain(original,statuses,root):
    rows=[]
    for index,status in enumerate(statuses):
        row=FeedbackRevision(id=root if index==0 else identity(int(root[-12:],16)+index),workspace_id=WORKSPACE,
            actor_id=ACTOR,root_id=root,revision=index+1,previous_id=rows[-1].id if rows else None,
            observation_id=original['observation_id'],baseline_capability_id=BASELINE,
            rule_id='count-rule',status=status,version=1 if status=='draft' else 2,
            data={'schema':'threshold-evolution-feedback-record-v147','basis':deepcopy(original),
                'label':'should_alert','reason':' 保留原人工说明 '+str(index),
                'cancel_reason':'明确取消本稿' if status=='cancelled' else None})
        feedback._seal(row);rows.append(row)
    return rows


def row_wire(rows):return canonical_json([feedback._snapshot(row) for row in rows])


def bind_chain(monkeypatch,db,p,original,rows,root):
    reads=[]
    def root_for_basis(actual_db,principal,actual_basis):
        assert actual_db is db and principal is p and actual_basis is original
        reads.append(('root',root));return root
    def checked_chain(actual_db,principal,root_id,lock=False):
        assert actual_db is db and principal is p and root_id==root
        reads.append(('chain',root_id,lock));return rows
    monkeypatch.setattr(feedback,'_root_for_basis',root_for_basis)
    monkeypatch.setattr(history,'inventory_chain',checked_chain)
    return reads


@pytest.mark.parametrize('lock',[False,True])
def test_no_root_keeps_none_semantics_without_reading_or_checking_an_unrelated_chain(monkeypatch,lock):
    db=object();p=SimpleNamespace(workspace_id=WORKSPACE);original=basis(EVAL_OBS,21)
    reads=bind_chain(monkeypatch,db,p,original,[],None)
    rows,latest=quality._latest_feedback_chain(db,p,original,lock)
    assert rows==[] and latest is None
    assert quality._latest_feedback(db,p,original,lock) is None
    assert reads==[('root',None),('root',None)]


@pytest.mark.parametrize('statuses,latest_index',[
    (['draft'],None),(['cancelled'],None),
    (['submitted','cancelled'],0),
    (['submitted','submitted','draft'],1),
    (['submitted','cancelled','submitted','cancelled'],2),
])
@pytest.mark.parametrize('lock',[False,True])
def test_latest_submitted_preserves_the_original_checked_chain_and_wrapper_contract(
        monkeypatch,statuses,latest_index,lock):
    db=object();p=SimpleNamespace(workspace_id=WORKSPACE);original=basis(EVAL_OBS,21)
    rows=chain(original,statuses,identity(30));before=row_wire(rows)
    original_order=tuple(rows);reads=bind_chain(monkeypatch,db,p,original,rows,rows[0].root_id)
    checked,latest=quality._latest_feedback_chain(db,p,original,lock)
    expected=None if latest_index is None else rows[latest_index]
    assert checked is rows and latest is expected
    assert quality._latest_feedback(db,p,original,lock) is expected
    assert reads==[('root',rows[0].root_id),('chain',rows[0].root_id,lock)]*2
    assert tuple(rows)==original_order and row_wire(rows)==before


def test_complete_chain_failure_is_not_downgraded_to_absent_feedback(monkeypatch):
    db=object();p=SimpleNamespace(workspace_id=WORKSPACE);original=basis(EVAL_OBS,21)
    rows=chain(original,['submitted'],identity(30))
    bind_chain(monkeypatch,db,p,original,rows,rows[0].root_id)
    failure=HTTPException(409,'原反馈尾链或独立审计已损坏')
    def broken_chain(*args,**kwargs):raise failure
    monkeypatch.setattr(history,'inventory_chain',broken_chain)
    with pytest.raises(HTTPException) as error:
        quality._latest_feedback_chain(db,p,original)
    assert error.value is failure


def evidence_case(monkeypatch,*,lock=False,eval_statuses=None):
    p=SimpleNamespace(workspace_id=WORKSPACE,user_id=ACTOR,role='admin')
    bases={EVAL_OBS:basis(EVAL_OBS,21),SHADOW_OBS:basis(SHADOW_OBS,19)}
    chains={EVAL_OBS:chain(bases[EVAL_OBS],eval_statuses or ['submitted','cancelled','draft'],identity(30)),
        SHADOW_OBS:chain(bases[SHADOW_OBS],['submitted','submitted','cancelled'],identity(40))}
    by_root={rows[0].root_id:rows for rows in chains.values()}
    sample={'id':identity(50),'basis':bases[EVAL_OBS]}
    result={'mechanism_gate_passed':True,'per_sample':[{'id':sample['id'],
        'baseline_matched':True,'candidate_matched':True}]}
    frozen={'purpose':'mechanism_check','candidate':{'baseline_manifest':{'synthetic_only':'baseline'},
        'candidate_manifest':{'synthetic_only':'candidate'},'training':[{
            'basis':{'observation_id':TRAIN_OBS},'feedback_snapshot':{'root_id':TRAIN_ROOT}}]},
        'samples':[sample],'criteria':{'max_additional_reviews':0}}
    evaluation=SimpleNamespace(id=EVALUATION,workspace_id=WORKSPACE,version=5,status='completed',candidate_id=identity(15),
        record_hash='b'*64,data={'freeze':frozen,'freeze_hash':quality.hash_value(frozen),
            'result':result,'job_id':identity(16)})
    grant=SimpleNamespace(id=SHADOW,version=4,evaluation_id=EVALUATION,status='ended',record_hash='c'*64,
        data={'evaluation':shadow._pin(evaluation),'scope':{'workspace_id':WORKSPACE,'field':'count',
            'field_label':'数量','rule_id':'count-rule','baseline_id':BASELINE,'entity_id':ENTITY}})
    consumption=Consumption(id=identity(51),workspace_id=WORKSPACE,grant_id=SHADOW,
        observation_id=SHADOW_OBS,source_record_id='synthetic-future-1',job_id=identity(17),
        actor_id=ACTOR,status='completed',version=3,record_hash='d'*64,
        data={'basis':bases[SHADOW_OBS]},created_at=STAMP)
    reads=[];checks=[];gates=[];actuals={key:deepcopy(value) for key,value in bases.items()}

    class ReadOnlyConsumptionSQL:
        def scalars(self,statement):
            assert statement.column_descriptions[0]['entity'] is Consumption
            assert statement.get_execution_options().get('populate_existing') is True
            reads.append(('consumption',));return iter([consumption])
    db=ReadOnlyConsumptionSQL()

    def get_evaluation(actual_db,principal,kind,identity_,actual_lock=False):
        assert actual_db is db and principal is p and kind=='evaluation'
        assert identity_==EVALUATION and actual_lock is lock
        gates.append('evaluation');return evaluation
    def get_shadow(actual_db,principal,identity_,actual_lock=False,check_consumptions=True):
        assert actual_db is db and principal is p and identity_==SHADOW
        assert actual_lock is lock and check_consumptions is False
        gates.append('shadow');return grant
    def current_grant(actual_db,principal,row,actual_lock,*,_return_evaluation=False):
        assert actual_db is db and principal is p and row is grant and actual_lock is False
        assert type(_return_evaluation) is bool
        gates.append('current')
        # Dependency contract only: the new carrier's cold read occurs inside
        # current_grant. This fixture does not prove that function's gates.
        return (row,get_evaluation(actual_db,principal,'evaluation',row.evaluation_id)) if _return_evaluation else row
    def fresh_grant(actual_db,principal,row):
        assert actual_db is db and principal is p and row is grant
        gates.append('fresh');return row
    def consumed(actual_db,principal,row):
        assert actual_db is db and principal is p and row is grant
        gates.append('consumption-inventory')
        return [{'id':consumption.id,'status':'completed','job_status':'succeeded','late':False,
                 'baseline_matched':False,'candidate_matched':True}]
    def root_for_basis(actual_db,principal,original):
        assert actual_db is db and principal is p and original is bases[original['observation_id']]
        rows=chains[original['observation_id']];reads.append(('root',rows[0].root_id));return rows[0].root_id
    def checked_chain(actual_db,principal,root_id,actual_lock=False):
        assert actual_db is db and principal is p and actual_lock is lock
        reads.append(('chain',root_id,actual_lock));return by_root[root_id]
    def actual_basis(actual_db,principal,observation_id,rule_id):
        assert actual_db is db and principal is p and rule_id=='count-rule'
        value=actuals[observation_id]
        if isinstance(value,HTTPException):raise value
        return deepcopy(value)
    original_require=history.require_current_basis
    def current_basis(actual_db,principal,rows):
        assert actual_db is db and principal is p and rows is by_root[rows[0].root_id]
        checks.append(rows);return original_require(actual_db,principal,rows)
    def scoped(actual_db,model,identity_,principal):
        assert actual_db is db and principal is p
        if model is Job:
            assert identity_==evaluation.data['job_id'];return SimpleNamespace(status='succeeded')
        assert model is Observation and identity_ in bases
        return SimpleNamespace(id=identity_,version=4,observed_at=STAMP)
    def entity_label(actual_db,principal,entity_id,package_id):
        assert actual_db is db and principal is p and entity_id==ENTITY and package_id==PACKAGE
        return '合成原对象'

    monkeypatch.setattr(study,'get_record',get_evaluation)
    monkeypatch.setattr(shadow,'get_grant',get_shadow)
    monkeypatch.setattr(shadow,'_current_grant',current_grant)
    monkeypatch.setattr(shadow,'fresh_grant',fresh_grant)
    monkeypatch.setattr(shadow_execution,'consumption_inventory',consumed)
    monkeypatch.setattr(feedback,'_root_for_basis',root_for_basis)
    monkeypatch.setattr(history,'inventory_chain',checked_chain)
    monkeypatch.setattr(feedback,'_basis',actual_basis)
    monkeypatch.setattr(history,'require_current_basis',current_basis)
    monkeypatch.setattr(quality,'scoped',scoped)
    monkeypatch.setattr(study_views,'_entity_label',entity_label)
    return SimpleNamespace(db=db,p=p,bases=bases,chains=chains,by_root=by_root,evaluation=evaluation,
        grant=grant,consumption=consumption,reads=reads,checks=checks,gates=gates,actuals=actuals,lock=lock)


def original_inputs(case):
    return canonical_json({'feedback':[feedback._snapshot(row) for rows in case.chains.values() for row in rows],
        'evaluation':case.evaluation.data,'grant':case.grant.data,
        'consumption':shadow_execution._snapshot(case.consumption)})


@pytest.mark.parametrize('lock',[False,True])
def test_evidence_context_passes_each_original_checked_chain_once_to_real_current_basis(monkeypatch,lock):
    case=evidence_case(monkeypatch,lock=lock);before=original_inputs(case)
    result=quality.evidence_context(case.db,case.p,EVALUATION,SHADOW,lock=lock)
    rows=list(case.chains.values())
    assert [entry for entry in case.reads if entry[0]=='chain']==[
        ('chain',chain_[0].root_id,lock) for chain_ in rows]
    assert len(case.checks)==2 and all(actual is expected for actual,expected in zip(case.checks,rows))
    assert case.gates==(['evaluation','shadow','fresh','consumption-inventory'] if lock else
        ['shadow','current','evaluation','consumption-inventory'])
    chosen=[rows[0][0],rows[1][1]]
    assert list(result['latest'])==[row.id for row in chosen]
    assert all(result['latest'][row.id] is row for row in chosen)
    assert [sample['reason'] for sample in result['context']['samples']]==[row.data['reason'] for row in chosen]
    assert result['samples']['evaluation'][0]['feedback']==feedback._snapshot(chosen[0])
    assert result['samples']['shadow'][0]['feedback']==feedback._snapshot(chosen[1])
    assert result['issues']==[] and original_inputs(case)==before


@pytest.mark.parametrize('statuses',[['draft'],['cancelled']])
def test_unsubmitted_chains_remain_unknown_and_do_not_become_quality_labels(monkeypatch,statuses):
    case=evidence_case(monkeypatch,eval_statuses=statuses);before=original_inputs(case)
    result=quality.evidence_context(case.db,case.p,EVALUATION,SHADOW)
    assert len([entry for entry in case.reads if entry[0]=='chain'])==2
    assert len(case.checks)==1 and case.checks[0] is case.chains[SHADOW_OBS]
    assert result['context']['samples'][0]['feedback_id'] is None
    assert result['samples']['evaluation'][0]['feedback'] is None
    assert result['statistics']['evaluation']['baseline']['unknown']==1
    assert result['issues'] and original_inputs(case)==before


@pytest.mark.parametrize('fault',['typed-change','source-error'])
def test_required_current_basis_failure_is_preserved_without_a_second_inventory_read(monkeypatch,fault):
    case=evidence_case(monkeypatch);before=original_inputs(case)
    failure=HTTPException(409,'原成功调用或现代接收依据已损坏')
    if fault=='typed-change':
        # Keep the original hash but change int21 to float21.0. The actual
        # require_current_basis must still perform its whole typed comparison.
        case.actuals[EVAL_OBS]['actual_value']=21.0
    else:case.actuals[EVAL_OBS]=failure
    with pytest.raises(HTTPException) as error:
        quality.evidence_context(case.db,case.p,EVALUATION,SHADOW)
    assert error.value.status_code==409
    if fault=='source-error':assert error.value is failure
    assert [entry for entry in case.reads if entry[0]=='chain']==[
        ('chain',case.chains[EVAL_OBS][0].root_id,False)]
    assert len(case.checks)==1 and case.checks[0] is case.chains[EVAL_OBS]
    assert original_inputs(case)==before


def test_training_root_exclusion_still_runs_before_current_basis_and_without_mutation(monkeypatch):
    case=evidence_case(monkeypatch)
    case.evaluation.data['freeze']['candidate']['training'][0]['feedback_snapshot']['root_id']=case.chains[EVAL_OBS][0].root_id
    case.evaluation.data['freeze_hash']=quality.hash_value(case.evaluation.data['freeze'])
    case.grant.data['evaluation']=shadow._pin(case.evaluation)
    before=original_inputs(case)
    with pytest.raises(HTTPException) as error:
        quality.evidence_context(case.db,case.p,EVALUATION,SHADOW)
    assert error.value.status_code==409 and '训练反馈根' in error.value.detail
    assert case.checks==[] and original_inputs(case)==before
