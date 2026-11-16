"""Pure review coverage for the supplied text; never whole-source authority."""
import copy

from pydantic import TypeAdapter

from .contracts import digest

SCHEMA='same-goal-advice-provided-source-review-v309'


def _need(value):
    if not value:raise ValueError('来源审阅必须限于实际提供的非空文字，不能推断全文完整或原件真实')


def provided_scopes(cloud_input,model_value):
    sources=cloud_input['sources'];reviews=model_value['source_reviews']
    _need(type(sources) is list and type(reviews) is list
        and [row['source_ref'] for row in reviews]==[row['source_ref'] for row in sources]
        and len({row['source_ref'] for row in sources})==len(sources))
    result=[]
    for source,review in zip(sources,reviews):
        assessment=review['assessment'];readability=source['readability']
        _need(assessment in {'reviewed','unreadable','incomparable'}
            and readability in {'readable','partial','unreadable'})
        coverage='not_reviewed'
        if assessment=='reviewed':
            _need(readability in {'readable','partial'} and type(source['text']) is str
                and bool(source['text'].strip()))
            coverage='provided_excerpt_only' if readability=='partial' else 'provided_source_text_only'
        result.append({'source_ref':source['source_ref'],'assessment':assessment,
            'input_readability':readability,'review_scope':coverage,
            'full_document_verified':False,'source_authenticity_verified':False})
    return copy.deepcopy(result)


def record(cloud_request,model_value,*,capture_sha256):
    from . import same_goal_advice_contracts_v302 as c
    request=c._parse(c.CloudRequest,cloud_request)
    _need(request['input_hash']==digest(request['decision_input']))
    value=c.check_model_value(request['decision_input'],model_value)
    _need(digest(value)==digest(model_value))
    capture=TypeAdapter(c.Hash).validate_python(capture_sha256)
    result={'schema':SCHEMA,'input_hash':request['input_hash'],'model_value_hash':digest(value),
        'capture_sha256':capture,'sources':provided_scopes(request['decision_input'],value),
        'full_document_verified':False,'source_authenticity_verified':False,'can_dispatch':False}
    return {**result,'record_hash':digest(result)}
