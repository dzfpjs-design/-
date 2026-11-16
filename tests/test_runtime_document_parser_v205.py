"""Real fixed local subprocess tests; Root executes these, not static agents."""
import json
from pathlib import Path

import pytest

from generic_agents import runtime_document_parser as subject
from generic_agents.document_parsers import ParseProblem
from tests.test_runtime_document_parse_boundaries_v205 import pdf_bytes, docx_bytes


def capture_children(monkeypatch):
    original = subject.subprocess.Popen
    captured = []
    def start(command, **kwargs):
        child = original(command, **kwargs)
        captured.append((child, command, kwargs))
        return child
    monkeypatch.setattr(subject.subprocess, 'Popen', start)
    return captured


def assert_reaped_and_private_files_removed(captured):
    assert len(captured) == 1
    child, _command, kwargs = captured[0]
    assert child.poll() is not None
    assert not Path(kwargs['cwd']).exists()


def test_actual_subprocess_reads_utf8_and_has_fixed_command_without_inherited_secrets(monkeypatch):
    monkeypatch.setenv('GA_CONNECTOR_SECRET_TEST', 'must-not-reach-child')
    monkeypatch.setenv('HTTPS_PROXY', 'https://must-not-reach-child.invalid')
    monkeypatch.setenv('PYTHONPATH', '/must-not-select-a-module')
    captured = capture_children(monkeypatch)
    result = subject.parse_runtime_document('第一行原文\n第二行原文'.encode(), '原文.txt')
    assert result['status'] == 'parsed' and result['engine'] == 'native-text-v1'
    assert [block['text'] for block in result['blocks']] == ['第一行原文', '第二行原文']
    _child, command, kwargs = captured[0]
    assert command[1:5] == ['-I', '-B', '-X', 'utf8']
    assert command[6] == subject._BOOTSTRAP and command[-1] == '--child'
    assert '原文.txt' not in command and kwargs['shell'] is False and kwargs['close_fds'] is True
    assert set(kwargs['env']) == {'PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL'}
    assert all('must-not' not in value for value in kwargs['env'].values())
    assert_reaped_and_private_files_removed(captured)


def test_actual_subprocess_preserves_partial_pdf_and_explicit_ocr_gap():
    result = subject.parse_runtime_document(pdf_bytes(['Native first page', None]), 'partial.pdf')
    assert result['status'] == 'needs_input'
    assert result['blocks'][0]['location'] == {'page': 1}
    assert any('OCR' in warning and '2' in warning for warning in result['warnings'])


def test_actual_subprocess_uses_existing_docx_body_reader():
    raw = docx_bytes('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>原始段落</w:t></w:r></w:p></w:body></w:document>')
    result = subject.parse_runtime_document(raw, '原文.docx')
    assert result['status'] == 'parsed' and result['engine'] == 'native-docx-v1'
    assert result['blocks'][0]['text'] == '原始段落'
    assert result['blocks'][0]['location'] == {'body_index': 1}


@pytest.mark.parametrize('raw,name', [(b'not-an-office-file', 'broken.docx'), (b'%PDF-broken', 'broken.pdf'),
    (b'\xff', 'broken.txt')])
def test_actual_subprocess_format_error_is_shared_safe_problem(raw, name):
    with pytest.raises(ParseProblem) as error:
        subject.parse_runtime_document(raw, name)
    assert str(error.value) in subject._SAFE_ERRORS
    assert not any(value in str(error.value) for value in ('Traceback', '/Users/', '/tmp/', 'PYTHONPATH', name))


def test_actual_subprocess_timeout_kills_waits_and_removes_private_temp(monkeypatch):
    captured = capture_children(monkeypatch)
    monkeypatch.setattr(subject, 'MAX_WALL_SECONDS', 0)
    with pytest.raises(ParseProblem, match='超过60秒'):
        subject.parse_runtime_document(b'original', 'bounded.txt')
    assert_reaped_and_private_files_removed(captured)


def test_actual_subprocess_guard_exception_is_not_rewritten_and_owned_child_is_reaped(monkeypatch):
    captured = capture_children(monkeypatch)
    class Cancelled(Exception): pass
    cancelled = Cancelled('server-owned-cancel')
    def guard():
        if captured:
            raise cancelled
    with pytest.raises(Cancelled) as error:
        subject.parse_runtime_document(b'original', 'bounded.txt', guard=guard)
    assert error.value is cancelled
    assert_reaped_and_private_files_removed(captured)


def test_actual_subprocess_output_bound_never_returns_a_truncated_dto(monkeypatch):
    captured = capture_children(monkeypatch)
    monkeypatch.setattr(subject, 'MAX_OUTPUT_BYTES', 32)
    with pytest.raises(ParseProblem):
        subject.parse_runtime_document(b'long enough to exceed the result envelope limit', 'bounded.txt')
    assert_reaped_and_private_files_removed(captured)


@pytest.mark.parametrize('content,name', [(b'x', '../raw.txt'), (b'x', 'document.html'), (b'', 'empty.txt')])
def test_invalid_input_does_not_start_any_child(monkeypatch, content, name):
    monkeypatch.setattr(subject.subprocess, 'Popen', lambda *_args, **_kwargs: pytest.fail('invalid input spawned a child'))
    with pytest.raises(ParseProblem): subject.parse_runtime_document(content, name)


def test_oversized_input_is_rejected_before_spawn(monkeypatch):
    monkeypatch.setattr(subject, 'MAX_INPUT_BYTES', 3)
    monkeypatch.setattr(subject.subprocess, 'Popen', lambda *_args, **_kwargs: pytest.fail('oversized input spawned a child'))
    with pytest.raises(ParseProblem, match='20MiB'): subject.parse_runtime_document(b'four', 'bounded.txt')


@pytest.mark.parametrize('raw', [b'{"ok":true,"ok":false}',
    json.dumps({'ok': True, 'result': {'status': 'parsed', 'blocks': [], 'engine': 'native-text-v1',
        'warnings': [], 'grant_admin': True}}).encode(),
    json.dumps({'ok': False, 'error': '/private/must-not-leak/secret'}).encode()])
def test_invalid_child_protocol_never_grants_authority_or_echoes_private_errors(raw):
    with pytest.raises(ParseProblem) as error: subject._decode(raw, 'original.txt', 0)
    assert str(error.value) == subject._GENERIC


@pytest.mark.parametrize('event', ['socket.__new__', 'socket.connect', 'socket.getaddrinfo',
    'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.fork', 'os.exec'])
def test_child_audit_policy_denies_network_and_another_process(event):
    with pytest.raises(PermissionError): subject._inert(event, ())
