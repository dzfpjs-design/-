"""Synthetic question projection; no model/provider/real report evidence."""
import copy
import pytest
from generic_agents import report_business_catalogue_edit_v211 as edit
from generic_agents import report_catalogue_flow_context_v1 as subject
from tests.test_report_business_catalogue_v211 import scene, build


def example():
    value, source = scene()
    # Keep every original position, and resolve only ownership so the plan can
    # be confirmed while independent missing information remains open.
    value['allocation_groups'][-1] = {'kind':'business','business_keys':['b1'],
        'input_positions':[6],'reason':'synthetic assignment','question_keys':[]}
    value['questions'] = [value['questions'][0]]
    projection = build(value,source)
    document = edit.from_projection(projection)
    document['questions'][0]['question'] = '两处数量不一致，保留待核。'
    document['questions'][0]['input_positions'] = [0,3]
    document['questions'][0]['candidate_business_keys'] = ['b2']
    return document, projection


def test_scope_keeps_open_problem_and_all_original_evidence_without_affecting_other_scope():
    document, projection = example()
    assert not any(i['blocks_confirmation'] for i in edit.validation(document, 8)['issues'])
    b2 = subject.relevant_questions(document, 'b2')
    assert [x['key'] for x in b2] == ['q_info']
    assert b2[0]['status'] == 'open' and b2[0]['input_positions'] == [0,3]
    assert subject.relevant_questions(document, 'other') == []
    evidence = subject.question_evidence(projection, b2[0], offset=0, limit=1)
    assert evidence['total'] == 2 and evidence['has_more'] is True
    assert evidence['records'][0]['input_position'] == 0
    assert evidence['records'][0]['source_spans'][0]['record']['source_ref'] == 'w:p1'
    later = subject.question_evidence(projection,b2[0],offset=1,limit=1)
    assert later['records'][0]['input_position'] == 3 and later['has_more'] is False
    assert later['records'][0]['source_spans'][0]['record']['source_ref'] == 'w:p4'


def test_resolution_and_original_claims_survive_without_mutating_saved_document():
    document, projection = example(); before = copy.deepcopy(projection)
    document['questions'][0].update(status='resolved',resolution='人工核对原文后仍保留两种来源。')
    result = subject.relevant_questions(document,'b2')[0]
    assert result['resolution'] and result['status'] == 'resolved'
    subject.question_evidence(projection,result,offset=0,limit=10)
    assert projection == before


@pytest.mark.parametrize('offset,limit', [(-1,1),(0,0),(0,11),(True,1),(0,True)])
def test_pagination_rejects_invalid_bounds(offset,limit):
    document, projection = example()
    with pytest.raises(ValueError):
        subject.question_evidence(projection,document['questions'][0],offset=offset,limit=limit)


def test_unrelated_business_remains_nonblocking_and_scope_ledger_does_not_send_question():
    document, _ = example()
    document['questions'][0]['input_positions'] = [2, 3]
    before = copy.deepcopy(document)
    assert subject.relevant_questions(document, 'b1') == []
    assert [q['key'] for q in subject.relevant_questions(document, 'b2')] == ['q_info']
    assert not any(issue['blocks_confirmation'] for issue in edit.validation(document, 8)['issues'])
    _, origin = scene()
    ledger = edit.scope_ledger(document, indexed=origin['indexed'], input_claims=origin['input_claims'],
        source_snapshot_hash=origin['source_snapshot_hash'])
    assert '两处数量不一致，保留待核。' not in str(ledger)
    assert document == before
