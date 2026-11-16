"""V206 typed request/range/region boundaries; no model or dependency readiness claim."""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import runtime_document_ocr as o
from generic_agents.runtime_document_ocr_api import Start, Action, SearchCreate


def request(**changes):
    return {'request_id': str(uuid4()), 'release_id': str(uuid4()), 'profile_hash': o.PROFILE,
        'range_mode': 'all', 'pages': [], **changes}


@pytest.mark.parametrize('changes', [
    {'pages': [1]}, {'range_mode': 'pages'}, {'range_mode': 'pages', 'pages': [1, 1]},
    {'range_mode': 'pages', 'pages': [True]}, {'range_mode': 'pages', 'pages': [1.0]},
    {'range_mode': 'pages', 'pages': ['1']}, {'range_mode': 'pages', 'pages': [501]},
    {'range_mode': 'pages', 'pages': list(range(1, 102))}, {'range_mode': 'native'}, {'engine_path': '/tmp/model'},
])
def test_ocr_start_has_one_explicit_bounded_range_and_no_executable(changes):
    with pytest.raises(ValidationError): Start.model_validate(request(**changes))


@pytest.mark.parametrize('value', [True, 1.0, '1', 0, -1])
def test_ocr_actions_require_strict_positive_original_event_or_subject_cas(value):
    with pytest.raises(ValidationError): Action(request_id=uuid4(), expected_version=value, reason='核对原结果后继续')


def test_zero_missing_text_selection_is_valid_without_inventing_ocr_pages():
    examined = {'page_count': 2, 'pages': [
        {'page_number': 1, 'has_native_text': True, 'has_image': False, 'has_annotations': False},
        {'page_number': 2, 'has_native_text': True, 'has_image': False, 'has_annotations': False}]}
    assert o.select_pages(examined, 'missing_text', []) == []
    assert o.select_pages(examined, 'all', []) == [1, 2]
    examined['pages'][1]['has_image'] = True
    assert o.select_pages(examined, 'missing_text', []) == [2]
    with pytest.raises(HTTPException): o.select_pages(examined, 'pages', [3])
    with pytest.raises(HTTPException): o.select_pages({'page_count': 101}, 'all', [])


def test_region_normalization_pins_engine_order_pixel_xywh_and_unreviewed_score():
    result = o.normalize_regions([{'text': '机器识别 未经核对', 'box': [3, 4, 17, 10], 'score': .8},
        {'text': '第二框', 'box': [0, 0, 20, 30]}], 20, 30)
    assert result == [{'id': 'r1', 'text': '机器识别 未经核对', 'box': [3, 4, 17, 10], 'score': .8, 'unreviewed': True},
        {'id': 'r2', 'text': '第二框', 'box': [0, 0, 20, 30], 'score': None, 'unreviewed': True}]
    assert o.normalize_regions([], 20, 30) == []  # No text is not proof of a blank page.


@pytest.mark.parametrize('region', [
    {'text': 'x', 'box': [0, 0, 0, 1]}, {'text': 'x', 'box': [-1, 0, 1, 1]},
    {'text': 'x', 'box': [19, 0, 2, 1]}, {'text': 'x', 'box': [False, 0, 1, 1]},
    {'text': 'x', 'box': [float('nan'), 0, 1, 1]}, {'text': 'x', 'box': [0, 0, float('inf'), 1]},
    {'text': 'x', 'box': [0, 0, 1, 1], 'score': True}, {'text': 'x', 'box': [0, 0, 1, 1], 'score': 1.1},
    {'text': False, 'box': [0, 0, 1, 1]},
])
def test_region_nonfinite_bool_out_of_bounds_or_bad_score_is_not_silently_accepted(region):
    with pytest.raises(HTTPException): o.normalize_regions([region], 20, 30)


def test_regions_and_text_exceeding_limits_fail_without_truncation():
    unit = {'text': 'x', 'box': [0, 0, 1, 1]}
    with pytest.raises(HTTPException): o.normalize_regions([unit] * 2001, 20, 30)
    with pytest.raises(HTTPException): o.normalize_regions([{**unit, 'text': 'x' * 2000001}], 20, 30)
    with pytest.raises(HTTPException): o.normalize_regions([], True, 30)
    query = SearchCreate(request_id=uuid4(), release_id=uuid4(), keyword='[literal] OR OCR')
    assert query.keyword == '[literal] OR OCR'
