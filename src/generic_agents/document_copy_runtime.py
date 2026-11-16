"""Pinned local DOCX renderer: no download, cloud fallback or caller paths.

macOS development adapter. Deny-default sandbox; only private job data and fixed
runtime/system resources can be read. Page review is required for all outputs.
"""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
from fastapi import HTTPException
from .document_copy_policy import normalize_docx, POLICY
from .document_parsers import ParseProblem
from .runtime_paths import local_runtime_root

_MANIFEST_HASH = 'fc577b4df71bd9e15f4aa6d675abb9f2df737bd064b4ec95002695a4fd3c6666'
_ENGINE_ID = 'libreoffice-docx-final-v1'
_SANDBOX = Path('/usr/bin/sandbox-exec')
_TIMEOUT = 180
_MAX_RSS = 1536 * 1024 * 1024  # Polled soft guard, not a kernel memory limit.
_MAX_OUTPUT = 50 * 1024 * 1024
_MAX_DISK = 256 * 1024 * 1024
_LIMITATIONS = ['本机开发版 LibreOfficeDev 26.8 alpha；尚未证明与 Microsoft Word 排版一致。','最终修订视图纳入插入和移入，排除删除和移出，并显示保留的隐藏文字和脚注；不支持的修订结构会阻止转换。','动态字段、目录和页码保留原缓存；活动链接解除。副本移除经核实的内部脚注跳转及其数字说明，页面文字保留。','字体、图片、文本框、脚注和页码须对照原 Word 逐页核对，不直接视为完整理解。']


def _sha(data): return hashlib.sha256(data).hexdigest()


def _blocked(reason): raise HTTPException(409, '本地 Word 转换不可用：'+reason)


def _operation_base():
    # Server configuration only; never derive an installation from release src.
    return local_runtime_root() / 'document-copy-m51'


def _installation(*, base=None):
    try:
        if sys.platform != 'darwin' or not _SANDBOX.is_file(): _blocked('当前系统没有经验证的隔离运行环境')
        base = _operation_base() if base is None else base
        if not base.is_absolute() or '..' in base.parts or not base.is_dir() or base.resolve(strict=True) != base:
            _blocked('未安装固定本地运行包或目录包含别名')
        manifest_path = base/'manifest.json'
        if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > 2*1024*1024:
            _blocked('安装清单缺失或异常')
        raw = manifest_path.read_bytes()
        if _sha(raw) != _MANIFEST_HASH: _blocked('安装清单已改变')
        manifest = json.loads(raw)
        if manifest.get('engine_id') != _ENGINE_ID or manifest.get('revision_policy') != POLICY: _blocked('运行包类型不匹配')
        actual = set()
        for path in base.rglob('*'):
            mode = path.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)): _blocked('运行包包含链接或特殊文件')
            if stat.S_ISREG(mode) and path != manifest_path: actual.add(str(path.relative_to(base)))
        expected = manifest['files']
        if actual != {e['path'] for e in expected} or len(actual) != len(expected): _blocked('运行包文件集合已改变')
        for entry in expected:
            relative = Path(entry['path'])
            if relative.is_absolute() or '..' in relative.parts or str(relative) != entry['path']: _blocked('运行包路径异常')
            path = base/relative
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode) or path.stat().st_size != entry['size'] or bool(mode & 0o111) != entry['executable']:
                _blocked('固定运行文件属性异常')
            if _sha(path.read_bytes()) != entry['sha256']: _blocked('固定运行文件校验失败')
        native = base/manifest['entrypoint']
        if str(native.relative_to(base)) not in actual or native.resolve(strict=True) != native or not os.access(native,os.X_OK):
            _blocked('转换引擎不可执行')
        return manifest, native
    except HTTPException: raise
    except (OSError,RuntimeError,ValueError,KeyError,TypeError): _blocked('固定运行包校验失败，请管理员修复')


def engine_status():
    result={'available':False,'engine_id':_ENGINE_ID,'name':'本地 Word → PDF（需逐页核对）','manifest_hash':_MANIFEST_HASH,'revision_policy':POLICY,'limitations':list(_LIMITATIONS)}
    try: _installation(); result.update(available=True,reason='已安装并校验本地固定运行包；转换不会调用云模型')
    except HTTPException as exc: result['reason']=str(exc.detail)
    return result


def _profile(bundle: Path, job: Path, native: Path):
    # Literal '/' is needed by macOS dyld; it grants only the root directory,
    # not descendants. No home/project/storage content is generally readable.
    read_roots = [bundle,'/System','/usr/lib','/usr/share','/Library/Fonts','/private/var/db/dyld',job]
    roots=''.join('(subpath '+json.dumps(str(p),ensure_ascii=False)+')' for p in read_roots)
    literals=''.join('(literal '+json.dumps(p)+')' for p in ['/','/bin/bash','/dev/null','/dev/random','/dev/urandom'])
    return ('(version 1)(deny default)(deny network*)'
        '(allow process-fork)(allow sysctl-read)(allow file-read-metadata)'
        '(allow process-exec (literal '+json.dumps(str(native),ensure_ascii=False)+')(literal "/bin/bash"))'
        '(allow file-map-executable '+roots+literals+')'
        '(allow file-read* '+roots+literals+')'
        '(allow file-write* (subpath '+json.dumps(str(job),ensure_ascii=False)+')(literal "/dev/null"))')


def _kill(process):
    try: os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError: pass
    try: process.wait(timeout=5)
    except subprocess.TimeoutExpired: pass


def _resident(process):
    # Count the owned process group, not unrelated application/system memory.
    p=subprocess.run(['/bin/ps','-axo','pgid=,rss='],capture_output=True,text=True,timeout=3,env={'PATH':'/usr/bin:/bin'})
    if p.returncode: raise ValueError('无法核对本地转换内存用量')
    total=0
    for line in p.stdout.splitlines():
        values=line.split()
        if len(values)==2 and values[0]==str(process.pid): total+=int(values[1])*1024
    return total


def _disk_size(job):
    total=0
    for p in job.rglob('*'):
        mode=p.lstat().st_mode
        if stat.S_ISLNK(mode) or not(stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError('转换临时目录含异常文件，已停止')
        if stat.S_ISREG(mode): total+=p.stat().st_size
    return total


def convert(content: bytes, *, revision_policy: str, expected_manifest_hash: str, guard=None):
    if expected_manifest_hash != _MANIFEST_HASH: _blocked('所选运行包与当前版本不一致，请重新核对')
    if guard: guard()
    base = _operation_base()
    manifest,native=_installation(base=base)
    try: normalized,normalization=normalize_docx(content,revision_policy)
    except ParseProblem as exc: raise HTTPException(422,str(exc)) from exc
    if guard: guard()
    with tempfile.TemporaryDirectory(prefix='ga-document-copy-m51-') as folder:
        job=Path(folder).resolve(); os.chmod(job,0o700)
        (job/'input.docx').write_bytes(normalized); (job/'out').mkdir(); (job/'profile/user').mkdir(parents=True)
        # Disable macros and update prompts even though active package/field
        # definitions have already been removed or rejected before rendering.
        (job/'profile/user/registrymodifications.xcu').write_text('''<?xml version="1.0" encoding="UTF-8"?><oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item><item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item></oor:items>''')
        cmd=['/usr/bin/sandbox-exec','-p',_profile(native.parents[2],job,native),'/bin/bash','-c','ulimit -c 0; ulimit -f 102400; ulimit -n 128; ulimit -t 120; exec "$@"','ga-local-render',str(native),'-env:UserInstallation='+(job/'profile').as_uri(),'--headless','--norestore','--nodefault','--nofirststartwizard','--convert-to','pdf:writer_pdf_Export:{"ExportBookmarks":{"type":"boolean","value":"false"},"ExportNotes":{"type":"boolean","value":"false"},"ExportFormFields":{"type":"boolean","value":"false"},"IsAddStream":{"type":"boolean","value":"false"}}','--outdir',str(job/'out'),str(job/'input.docx')]
        # Keep all font-cache writes private; do not rely on system/real-home caches.
        (job/'font-cache').mkdir()
        (job/'fonts.conf').write_text('<?xml version="1.0"?><fontconfig><dir>/System/Library/Fonts</dir><dir>/Library/Fonts</dir><cachedir>'+str(job/'font-cache')+'</cachedir><alias><family>serif</family><prefer><family>Songti SC</family><family>Arial Unicode MS</family></prefer></alias><alias><family>sans-serif</family><prefer><family>Heiti SC</family><family>Arial Unicode MS</family></prefer></alias></fontconfig>')
        env={'HOME':str(job),'TMPDIR':str(job),'PATH':'/usr/bin:/bin','LANG':'en_US.UTF-8','LC_ALL':'en_US.UTF-8','SAL_USE_VCLPLUGIN':'svp','FONTCONFIG_FILE':str(job/'fonts.conf'),'XDG_CACHE_HOME':str(job/'font-cache'),'XDG_DATA_HOME':str(job/'data'),'FC_LANG':'zh-cn'}
        with (job/'stdout').open('wb') as out, (job/'stderr').open('wb') as err:
            process=subprocess.Popen(cmd,cwd=job,env=env,stdout=out,stderr=err,stdin=subprocess.DEVNULL,start_new_session=True)
            started=time.monotonic(); checked=0.0
            try:
                while process.poll() is None:
                    if guard: guard()
                    elapsed=time.monotonic()-started
                    if elapsed>_TIMEOUT: raise ValueError('本地 Word 转换超时；原件保留，可查原请求状态')
                    if elapsed-checked>=1:
                        checked=elapsed
                        if _resident(process)>_MAX_RSS: raise ValueError('本地 Word 转换超过内存软限制')
                        if _disk_size(job)>_MAX_DISK: raise ValueError('本地 Word 转换超过临时空间限制')
                    time.sleep(.15)
                if guard: guard()
                pdf_path=job/'out/input.pdf'
                if process.returncode or not pdf_path.is_file() or pdf_path.is_symlink():
                    raise ValueError('本地 Word 未生成有效 PDF；请保留原件，查看请求错误或另存标准 DOCX 后重试')
                if _disk_size(job)>_MAX_DISK or pdf_path.stat().st_size>_MAX_OUTPUT:
                    raise ValueError('转换输出超过安全上限')
                pdf=pdf_path.read_bytes()
            finally:
                # Also kills helpers that might outlive the main process.
                _kill(process)
        if not pdf.startswith(b'%PDF-'): raise ValueError('本地转换结果不是 PDF')
        from .document_copy_pdf import strip_internal_navigation, chinese_text_health
        pdf, navigation = strip_internal_navigation(pdf)
        health = chinese_text_health(pdf, normalization['source_chinese_unique'])
        from pypdf import PdfReader
        page_count=len(PdfReader(io.BytesIO(pdf),strict=True).pages)
        # Recheck after execution; the sandbox cannot modify the fixed package.
        _installation(base=base)
        if guard: guard()
        return {'content':pdf,'receipt':{'contract_version':1,'source_sha256':_sha(content),'pdf_sha256':_sha(pdf),'engine_id':_ENGINE_ID,'manifest_hash':_MANIFEST_HASH,'runtime_version':manifest['runtime_version'],'revision_policy':POLICY,'normalization':normalization,'navigation_cleanup':navigation,'text_health':health,'page_count':page_count,'warnings':list(_LIMITATIONS)+normalization['warnings'],'resource_limits':{'wall_seconds':_TIMEOUT,'rss_soft_bytes':_MAX_RSS,'temporary_soft_bytes':_MAX_DISK,'output_bytes':_MAX_OUTPUT},'sandbox':'deny-default; network denied; fixed runtime/system and private job reads only'}}
