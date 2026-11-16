"""Fixed bounded local coverage child; no PDF parsing in the main worker."""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from .document_parsers import ParseProblem
from .runtime_document_parser import _environment, _stop, _pairs, _inert, _limits

MAX_INPUT = 20 * 1024 * 1024
MAX_OUTPUT = 256 * 1024
_BOOTSTRAP = "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('generic_agents.runtime_document_ocr_coverage',run_name='__main__')"


def examine_pdf(content, *, guard=None):
    if type(content) is not bytes or not 0 < len(content) <= MAX_INPUT: raise ParseProblem('PDF原件为空或超过20MiB')
    with tempfile.TemporaryDirectory(prefix='ga-runtime-ocr-coverage-') as folder:
        os.chmod(folder, 0o700); incoming, outgoing = Path(folder) / 'in', Path(folder) / 'out'
        incoming.write_bytes(content); os.chmod(incoming, 0o600)
        process = None
        try:
            with incoming.open('rb') as source, outgoing.open('w+b') as target:
                os.chmod(outgoing, 0o600)
                if guard: guard()
                process = subprocess.Popen([sys.executable, '-I', '-B', '-X', 'utf8', '-c', _BOOTSTRAP,
                    str(Path(__file__).resolve().parent.parent), '--child'], stdin=source, stdout=target,
                    stderr=subprocess.DEVNULL, cwd=folder, env=_environment(folder), start_new_session=True, close_fds=True)
                started = time.monotonic(); checked = 0
                while True:
                    now = time.monotonic()
                    if guard and now >= checked: guard(); checked = now + .5
                    if now - started > 60: raise ParseProblem('PDF页范围核对超过60秒；原件保留，请核对后重试')
                    if os.fstat(target.fileno()).st_size > MAX_OUTPUT: raise ParseProblem('PDF页范围结果超过上限')
                    if process.poll() is not None: break
                    time.sleep(.05)
                if guard: guard()
                target.seek(0)
                try: value = json.loads(target.read(MAX_OUTPUT + 1), object_pairs_hook=_pairs)
                except (ValueError, UnicodeError): raise ParseProblem('PDF页范围尚未核对，未选择任何假定页面') from None
                if process.returncode or type(value) is not dict or set(value) != {'page_count', 'pages', 'warnings'}:
                    raise ParseProblem('PDF损坏、加密或页范围无法核对；原件保留')
                count = value['page_count']
                if type(count) is not int or not 1 <= count <= 500 or type(value['pages']) is not list or len(value['pages']) != count:
                    raise ParseProblem('PDF页数或覆盖范围无效')
                for number, page in enumerate(value['pages'], 1):
                    if (type(page) is not dict or set(page) != {'page_number', 'has_native_text', 'has_image', 'has_annotations'}
                            or type(page['page_number']) is not int or page['page_number'] != number
                            or any(type(page[k]) is not bool for k in ('has_native_text', 'has_image', 'has_annotations'))):
                        raise ParseProblem('PDF页范围结构无法核对')
                if type(value['warnings']) is not list or any(type(x) is not str for x in value['warnings']) or sum(map(len, value['warnings'])) > 16384:
                    raise ParseProblem('PDF覆盖提示超过上限')
                return value
        finally:
            if not _stop(process) and sys.exc_info()[0] is None: raise ParseProblem('PDF页范围子进程未能核对退出')


def _main():
    os.environ.clear(); _limits(); sys.addaudithook(_inert)
    try:
        content = sys.stdin.buffer.read(MAX_INPUT + 1)
        if not content or len(content) > MAX_INPUT: raise ValueError()
        from pypdf import PdfReader
        from .material_ocr_coverage import coverage
        parsed = PdfReader(io.BytesIO(content))
        if parsed.is_encrypted or not 1 <= len(parsed.pages) <= 500: raise ValueError()
        declared = coverage(content, 'source.pdf'); units = {item['page']: item for item in declared['units']}
        pages = [{'page_number': number, 'has_native_text': units[number]['has_native_text'] if number in units else True,
            'has_image': units[number]['has_image'] if number in units else False,
            'has_annotations': units[number]['has_annotations'] if number in units else False}
            for number in range(1, len(parsed.pages) + 1)]
        value = {'page_count': len(pages), 'pages': pages, 'warnings': declared['gaps']}
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        if len(raw) > MAX_OUTPUT: raise ValueError()
        sys.stdout.buffer.write(raw); return 0
    except Exception: return 1


if __name__ == '__main__':
    if sys.argv[1:] != ['--child']: raise SystemExit(2)
    raise SystemExit(_main())
