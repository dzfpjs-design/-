"""V205 request boundaries; native reader boundaries have independent tests."""
import hashlib
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import runtime_documents as d
from generic_agents.runtime_document_api import Action, SearchCreate, Upload, NewVersion, integer


@pytest.mark.parametrize('value', [True, 1.0, '1', 0, -1])
def test_explicit_actions_require_real_positive_cas_integer(value):
    with pytest.raises(ValidationError): Action(request_id=uuid4(), expected_version=value, reason='核对后重试')


def test_upload_requires_real_same_bytes_and_only_four_inert_document_types():
    raw = b'original'; body = {'file_name': 'report.md', 'file_size': len(raw), 'file_sha256': hashlib.sha256(raw).hexdigest()}
    assert d.validate_upload(body, raw) == 'text/markdown'
    for altered in (b'changed!', b'', raw + b'x'):
        with pytest.raises(HTTPException): d.validate_upload(body, altered)
    for name in ('../report.md', 'report.docm', 'sheet.xlsx', 'image.png', 'sftp://server/report.txt'):
        with pytest.raises(HTTPException): d.validate_upload({**body, 'file_name': name}, raw)


@pytest.mark.parametrize('value', ['01', '+1', '1.0', ' 1', '-1', 'true', ''])
def test_multipart_integer_has_one_canonical_json_representation(value):
    with pytest.raises(HTTPException): integer(value)


def test_request_models_preserve_literal_keyword_and_require_version_parent():
    query = SearchCreate(request_id=uuid4(), release_id=uuid4(), keyword='[a-z]+ OR 1=1')
    assert query.keyword == '[a-z]+ OR 1=1'  # Not regex/SQL/model instructions.
    upload = Upload(request_id=uuid4(), release_id=uuid4(), title='运行报告', file_name='report.txt',
        file_size=1, file_sha256='a' * 64)
    with pytest.raises(ValidationError): NewVersion.model_validate(upload.model_dump())


def test_search_hit_excerpt_bounds_original_span_and_keeps_unicode_literal_match():
    text = '前' * 10000 + 'Straße marker' + '后' * 10000
    full = {'text': text, 'block_hash': 'original-hash'}
    hit = d.search_hit(full, 'STRASSE')
    assert len(hit['text']) <= 1200 and 'strasse' in hit['text'].casefold() and hit['text_is_excerpt']
    assert text[hit['char_span']['start']:hit['char_span']['end']] == hit['text']
    assert hit['block_hash'] == full['block_hash']
    short = d.search_hit({'text': 'Original marker'}, 'marker')
    assert short['text'] == 'Original marker' and not short['text_is_excerpt']
