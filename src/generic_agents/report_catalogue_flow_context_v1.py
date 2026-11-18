"""Read-only, per-business human questions over an already verified catalogue.

This projection is displayed to a person; it never changes a plan, transmits
text to a model, or treats a question or its resolution as a source fact.
"""
import copy

MAX_EVIDENCE_PAGE = 10


def relevant_questions(document, scope_id):
    if type(document) is not dict or type(scope_id) is not str or not scope_id:
        raise ValueError('无法核对业务目录与范围')
    selected = {position for group in document['allocation_groups']
                if scope_id in group['business_keys'] for position in group['input_positions']}
    return [copy.deepcopy(question) for question in document['questions']
            if scope_id in question['candidate_business_keys']
            or selected.intersection(question['input_positions'])]


def question_evidence(projection, question, *, offset, limit):
    if (type(offset) is not int or offset < 0 or type(limit) is not int
            or not 1 <= limit <= MAX_EVIDENCE_PAGE):
        raise ValueError('原文分页范围无法核对')
    positions = question['input_positions']
    assignments = projection['assignments']
    if (type(positions) is not list or type(assignments) is not list
            or any(type(p) is not int or p < 0 or p >= len(assignments) for p in positions)):
        raise ValueError('原声明位置无法核对')
    records = []
    for position in positions[offset:offset + limit]:
        row = assignments[position]
        if type(row) is not dict or row.get('input_position') != position:
            raise ValueError('原声明顺序无法核对')
        claim = row['claim']
        records.append({'input_position': position, 'claim_hash':row['claim_hash'],
            'claim':{key:copy.deepcopy(claim[key]) for key in ('statement','status','explanation')},
            'source_spans':copy.deepcopy(row['source_spans'])})
    return {'offset':offset,'limit':limit,'total':len(positions),
            'has_more':offset + limit < len(positions),'records':records}
