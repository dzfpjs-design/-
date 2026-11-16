"""Actual inert DOCX parsing, operation isolation and bounded detached reuse.

No database, provider, original report or service fixture is needed.
"""
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import io
import socket
import subprocess
from threading import Barrier
import zipfile

import pytest

from generic_agents import native_report_reading as reading
from generic_agents import report_text_parse_scope as scopes
from generic_agents.document_parsers import ParseProblem


W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def doc(text):
    from xml.sax.saxutils import escape
    parts = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="r1" Type="' + R + '/officeDocument" Target="word/document.xml"/></Relationships>',
        'word/document.xml': '<w:document xmlns:w="' + W + '"><w:body><w:p><w:r><w:t>'
            + escape(text) + '</w:t></w:r></w:p></w:body></w:document>',
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    return out.getvalue()


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail('pure report parsing cannot contact a service or spawn a process')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(socket, 'getaddrinfo', deny)
    monkeypatch.setattr(subprocess, 'Popen', deny)


@pytest.fixture
def parsed(monkeypatch):
    actual = reading._read_docx_uncached
    calls = []

    def counted(content):
        calls.append(content)
        return actual(content)

    monkeypatch.setattr(reading, '_read_docx_uncached', counted)
    return calls, actual


def test_public_read_without_scope_keeps_original_behavior(parsed):
    content = doc('独立正文，不含来源指令权限')
    calls, actual = parsed
    a = reading.read_docx(content); b = reading.read_docx(content)
    assert len(calls) == 2
    assert a == b == actual(content)
    assert a is not b and a['blocks'] is not b['blocks']
    assert a['can_apply'] is a['full_report_confirmed'] is False
    assert scopes.MAX_ENTRIES == 2 and scopes.MAX_SOURCE_BYTES == 64 * 1024 * 1024


def test_identical_bytes_reuse_actual_parser_and_detach_every_return(parsed):
    content = doc('原正文 🌲')
    calls, actual = parsed
    expected = actual(content)
    with scopes.report_text_parse_scope():
        first = reading.read_docx(content)
        first['blocks'][0]['text'] = '调用方修改'
        first['coverage']['gaps'].append({'kind': 'caller', 'count': 1})
        first['warnings'].clear()
        second = reading.read_docx(bytes(bytearray(content)))
        assert second == expected
        second['blocks'].clear()
        assert reading.read_docx(content) == expected
        assert len(calls) == 1
    assert reading.read_docx(content) == expected and len(calls) == 2


def test_only_two_results_retained_third_source_parses_normally(parsed):
    a, b, c = (doc(text) for text in ('第一份', '第二份', '第三份'))
    calls, _ = parsed
    with scopes.report_text_parse_scope():
        for content in (a, b, c, a, b, c):
            result = reading.read_docx(content)
            assert result['blocks'] and result['can_apply'] is False
    assert Counter(calls) == Counter({a: 1, b: 1, c: 2})


@pytest.mark.parametrize('offset, second_parses', [(0, 1), (-1, 2)])
def test_total_source_byte_limit_inclusive_without_truncating(monkeypatch, parsed, offset, second_parses):
    a, b = doc('容量甲'), doc('容量乙')
    calls, _ = parsed
    monkeypatch.setattr(scopes, 'MAX_SOURCE_BYTES', len(a) + len(b) + offset)
    with scopes.report_text_parse_scope():
        for content in (a, b, a, b):
            assert reading.read_docx(content)['coverage']['counts']['source_bytes'] == len(content)
    assert Counter(calls) == Counter({a: 1, b: second_parses})


def test_single_oversize_source_still_uses_the_real_parser(monkeypatch, parsed):
    content = doc('源字节超缓存界限仍正常校验')
    calls, actual = parsed
    monkeypatch.setattr(scopes, 'MAX_SOURCE_BYTES', len(content) - 1)
    with scopes.report_text_parse_scope():
        assert reading.read_docx(content) == actual(content)
        assert reading.read_docx(content) == actual(content)
    assert len(calls) == 2


@pytest.mark.parametrize('content', [b'not-a-zip', doc('   ')])
def test_real_parser_failures_do_not_occupy_slots(parsed, content):
    calls, _ = parsed
    a, b = doc('失败后可读甲'), doc('失败后可读乙')
    with scopes.report_text_parse_scope():
        for _ in range(2):
            with pytest.raises(ParseProblem):
                reading.read_docx(content)
        for valid in (a, b, a, b):
            reading.read_docx(valid)
    assert Counter(calls) == Counter({content: 2, a: 1, b: 1})


def test_policy_change_cannot_reuse_an_accepted_view(monkeypatch, parsed):
    content = doc('修订策略必须一致')
    calls, _ = parsed
    original = reading.POLICY
    with scopes.report_text_parse_scope():
        baseline = reading.read_docx(content)
        monkeypatch.setattr(reading, 'POLICY', 'unsupported-policy')
        with pytest.raises(ParseProblem):
            reading.read_docx(content)
        monkeypatch.setattr(reading, 'POLICY', original)
        assert reading.read_docx(content) == baseline
    assert len(calls) == 2


@pytest.mark.parametrize('field, value', [('MAX_BLOCKS', 1), ('MAX_CHARACTERS', 100)])
def test_changed_successful_limits_form_distinct_cache_entries(monkeypatch, parsed, field, value):
    content = doc('边界一致')
    calls, _ = parsed
    original = getattr(reading, field)
    with scopes.report_text_parse_scope():
        baseline = reading.read_docx(content)
        monkeypatch.setattr(reading, field, value)
        assert reading.read_docx(content) == baseline
        assert reading.read_docx(content) == baseline
        monkeypatch.setattr(reading, field, original)
        assert reading.read_docx(content) == baseline
    assert len(calls) == 2


@pytest.mark.parametrize('field, value', [('MAX_BLOCKS', 0), ('MAX_CHARACTERS', 1)])
def test_stricter_limit_still_rejects_whole_source(monkeypatch, parsed, field, value):
    content = doc('完整正文不得裁剪')
    calls, _ = parsed
    original = getattr(reading, field)
    with scopes.report_text_parse_scope():
        baseline = reading.read_docx(content)
        monkeypatch.setattr(reading, field, value)
        with pytest.raises(ParseProblem):
            reading.read_docx(content)
        monkeypatch.setattr(reading, field, original)
        assert reading.read_docx(content) == baseline
    assert len(calls) == 2


def test_typed_limit_does_not_identify_true_with_one(monkeypatch, parsed):
    content = doc('单段')
    calls, _ = parsed
    with scopes.report_text_parse_scope():
        monkeypatch.setattr(reading, 'MAX_BLOCKS', 1)
        reading.read_docx(content)
        monkeypatch.setattr(reading, 'MAX_BLOCKS', True)
        reading.read_docx(content)
        reading.read_docx(content)
        monkeypatch.setattr(reading, 'MAX_BLOCKS', 1)
        reading.read_docx(content)
    assert len(calls) == 3


def test_parser_callable_identity_is_part_of_key(monkeypatch, parsed):
    content = doc('实现身份必须一致')
    calls, actual = parsed
    other_calls = []

    def changed(content):
        other_calls.append(content)
        return actual(content)

    with scopes.report_text_parse_scope():
        baseline = reading.read_docx(content)
        monkeypatch.setattr(reading, '_read_docx_uncached', changed)
        assert reading.read_docx(content) == baseline
        assert reading.read_docx(content) == baseline
    assert len(calls) == len(other_calls) == 1


def test_nested_scopes_share_and_inner_error_does_not_expire_outer(parsed):
    content = doc('嵌套调用')
    calls, _ = parsed
    with scopes.report_text_parse_scope():
        baseline = reading.read_docx(content)
        with pytest.raises(RuntimeError, match='inner'):
            with scopes.report_text_parse_scope():
                assert reading.read_docx(content) == baseline
                raise RuntimeError('inner')
        assert reading.read_docx(content) == baseline
    assert len(calls) == 1
    with scopes.report_text_parse_scope():
        assert reading.read_docx(content) == baseline
    assert len(calls) == 2


def test_outer_error_resets_scope_and_expires_copied_context(parsed):
    content = doc('退出操作清空')
    calls, _ = parsed
    with pytest.raises(RuntimeError, match='outer'):
        with scopes.report_text_parse_scope():
            reading.read_docx(content)
            copied = copy_context()
            raise RuntimeError('outer')
    reading.read_docx(content)
    copied.run(reading.read_docx, content)
    assert len(calls) == 3


def test_old_copy_context_cannot_reopen_a_previous_operation_cache(parsed):
    content = doc('复制上下文不能跨请求复用')
    calls, _ = parsed
    with scopes.report_text_parse_scope():
        baseline = reading.read_docx(content)
        copied = copy_context()

    def operation():
        with scopes.report_text_parse_scope():
            assert reading.read_docx(content) == baseline
            assert reading.read_docx(content) == baseline

    copied.run(operation)
    copied.run(operation)
    assert len(calls) == 3


def test_copy_context_writes_do_not_mutate_outer_entry_inventory(parsed):
    a, b = doc('原操作来源'), doc('复制操作来源')
    calls, _ = parsed
    with scopes.report_text_parse_scope():
        reading.read_docx(a)
        copied = copy_context()
        copied.run(reading.read_docx, b)
        reading.read_docx(b)
        reading.read_docx(a)
    assert Counter(calls) == Counter({a: 1, b: 2})


def test_decorator_reuses_only_nested_calls_of_the_same_operation(parsed):
    content = doc('装饰器操作边界')
    calls, _ = parsed

    @scopes.report_text_parse_scope()
    def inner():
        return reading.read_docx(content)

    @scopes.report_text_parse_scope()
    def outer():
        assert inner() == inner()

    outer(); outer()
    assert len(calls) == 2


def test_concurrent_threads_inheriting_a_context_have_independent_scopes(parsed):
    content = doc('并发线程独立')
    calls, _ = parsed
    barrier = Barrier(2)

    def worker():
        with scopes.report_text_parse_scope():
            barrier.wait(timeout=10)
            first = reading.read_docx(content)
            assert reading.read_docx(content) == first
            return first

    with scopes.report_text_parse_scope():
        expected = reading.read_docx(content)
        contexts = [copy_context(), copy_context()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(context.run, worker) for context in contexts]
            assert [future.result(timeout=20) for future in futures] == [expected, expected]
        assert reading.read_docx(content) == expected
    assert len(calls) == 3


def test_concurrent_async_tasks_do_not_share_inherited_results(parsed):
    content = doc('并发任务独立')
    calls, _ = parsed

    async def scenario():
        ready = asyncio.Event()

        async def child():
            with scopes.report_text_parse_scope():
                first = reading.read_docx(content)
                await ready.wait()
                assert reading.read_docx(content) == first
                return first

        with scopes.report_text_parse_scope():
            expected = reading.read_docx(content)
            children = [asyncio.create_task(child()), asyncio.create_task(child())]
            await asyncio.sleep(0)
            ready.set()
            assert await asyncio.gather(*children) == [expected, expected]
            assert reading.read_docx(content) == expected

    asyncio.run(scenario())
    assert len(calls) == 3


def test_non_bytes_are_not_coerced_into_cached_valid_source(parsed):
    content = doc('原字节类型不改')
    calls, _ = parsed
    with scopes.report_text_parse_scope():
        reading.read_docx(content)
        for _ in range(2):
            with pytest.raises(ParseProblem):
                reading.read_docx(bytearray(content))
        reading.read_docx(content)
    assert len(calls) == 3
