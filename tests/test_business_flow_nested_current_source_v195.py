"""Fresh nested review authority over explicit public I/O fixtures only."""
import copy

import pytest
from fastapi import HTTPException

from generic_agents import business_flow_review_source as source
from generic_agents import report_design_saved_source_v174 as current
from generic_agents.contracts import digest
from tests.test_business_flow_review_api_pure import no_io
from tests.test_business_flow_nested_component_source_v194 import failed_case


def scene(monkeypatch):
    c=failed_case(monkeypatch);loaded=source.load(c.db,c.p,c.row.id)
    c.saved_draft={'attempt_id':c.row.id,'version':2,'source_binding':copy.deepcopy(loaded['source_binding']),
        'baseline_value':copy.deepcopy(loaded['baseline_value']),'value':copy.deepcopy(loaded['baseline_value'])}
    c.reads=[]
    def fresh(db,p,producer_id,*,lock):
        assert db is c.db and p is c.p and producer_id==c.row.producer_id and lock is True
        c.reads.append('current_source');return copy.deepcopy(c.original)
    monkeypatch.setattr(current,'load_source_context',fresh)
    return c


@pytest.mark.parametrize('mode',['author_self_review','engineering_simulation'])
def test_fresh_nested_review_binds_actual_saved_value_and_same_current_materials_without_upgrading_failed_original(monkeypatch,mode):
    c=scene(monkeypatch);before=copy.deepcopy((c.saved_draft,c.row.data,c.saved))
    c.saved_draft['value']['business']['questions']=[]
    result=source.current_nested_review_source(c.db,c.p,c.row.id,c.saved_draft,review_mode=mode)
    assert result['schema']=='business-flow-nested-review-source-v195' and result['review_mode']==mode
    assert result['source_binding_hash']==digest(c.saved_draft['source_binding'])
    assert result['value_hash']==digest(c.saved_draft['value'])
    assert result['original_source_hash']==result['current_source_hash']==digest(c.original)
    assert c.reads==['current_source'] and c.row.status=='failed' and c.row.data['projection'] is None
    assert c.row.data==before[1] and c.saved==before[2]


def test_engineering_preserves_unanswered_questions_and_author_review_blocks_them(monkeypatch):
    c=scene(monkeypatch)
    refs=c.saved_draft['value']['business']['flows'][0]['completion']['attribution']['citations']
    c.saved_draft['value']['business']['questions']=[{'question':'实际执行岗位尚待确定','reason':'工程模拟不代替人员确认','citations':refs}]
    original=copy.deepcopy(c.saved_draft)
    result=source.current_nested_review_source(c.db,c.p,c.row.id,c.saved_draft,review_mode='engineering_simulation')
    assert result['review_mode']=='engineering_simulation' and c.saved_draft==original
    with pytest.raises(HTTPException) as error:
        source.current_nested_review_source(c.db,c.p,c.row.id,c.saved_draft,review_mode='author_self_review')
    assert error.value.status_code==422 and '待核问题' in error.value.detail and c.saved_draft==original


def test_bundle_reuses_only_the_row_checked_by_this_complete_original_read(monkeypatch):
    c=scene(monkeypatch);old=source.attempts.checked_attempt;checks=[]
    def checked(*args,**kwargs):
        checks.append('complete_original_inventory');return old(*args,**kwargs)
    monkeypatch.setattr(source.attempts,'checked_attempt',checked)
    snapshot,loaded=source.nested_review_source_bundle(c.db,c.p,c.row.id,c.saved_draft,review_mode='engineering_simulation')
    assert checks==['complete_original_inventory'] and c.reads==['current_source']
    assert snapshot['source_binding_hash']==digest(loaded['source_binding'])
    assert loaded['baseline_value']==c.saved_draft['baseline_value']
    assert c.row.status=='failed' and c.row.data['projection'] is None


@pytest.mark.parametrize('fault',['binding','baseline','attempt','source','snapshot','unknown_mode','bad_shape','unknown_quote'])
def test_changed_or_unsafe_current_source_cannot_be_qualified_as_engineering_or_human(monkeypatch,fault):
    c=scene(monkeypatch);mode='engineering_simulation'
    if fault=='binding':c.saved_draft['source_binding']['capture_hash']='f'*64
    elif fault=='baseline':c.saved_draft['baseline_value']['business']['name']='其他业务'
    elif fault=='attempt':c.saved_draft['attempt_id']='00000000-0000-4000-8000-000000000000'
    elif fault in {'source','snapshot'}:
        changed=copy.deepcopy(c.original)
        if fault=='source':changed['snapshot']['snapshot_hash']='f'*64
        else:changed['context']['forged_source']=True
        monkeypatch.setattr(current,'load_source_context',lambda *args,**kwargs:copy.deepcopy(changed))
    elif fault=='unknown_mode':mode='implicit_approval'
    elif fault=='bad_shape':c.saved_draft['value']['business']['flows'][0]['phases']=[{'kind':'decision'}]
    else:c.saved_draft['value']['business']['flows'][0]['completion']['attribution']['citations']=['w:p999999/s1']
    with pytest.raises(HTTPException):source.current_nested_review_source(c.db,c.p,c.row.id,c.saved_draft,review_mode=mode)
    assert c.row.status=='failed' and c.row.data['projection'] is None
