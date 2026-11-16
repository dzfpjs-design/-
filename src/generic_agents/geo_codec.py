"""Credential-free bounded local raster codec. No file URLs or caller-supplied drivers."""
import hashlib,json,os,subprocess,tempfile,time
from pathlib import Path
from .config import settings
from .contracts import digest
ROOT=Path(__file__).resolve().parents[2]
RECIPE=json.loads((ROOT/'runtime-packs/geo-rgb-v1.json').read_text())
RECIPE_HASH=digest(RECIPE)


def file_digest(path):
    checksum=hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):checksum.update(chunk)
    return checksum.hexdigest()


def call(mode,path,request=None,outdir=None,guard=lambda:None):
    python=settings().geo_python.absolute()
    if not python.is_file():raise ValueError('本地地理处理环境尚未安装，请按固定配方安装')
    if mode not in {'inspect','render','geometry'}:raise ValueError('未登记的影像处理方法')
    if file_digest(Path(__file__).with_name('geo_runner.py'))!=RECIPE['runner_sha256']:raise ValueError('本地地理处理代码与固定配方不一致，需核对版本后明确重试')
    expected=file_digest(path)
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT'}}
    env.update(PROJ_NETWORK='OFF',GDAL_PAM_ENABLED='NO',GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR')
    with tempfile.TemporaryDirectory(prefix='ga-geo-codec-') as tmp:
        root=Path(tmp);command=[str(python),str(Path(__file__).with_name('geo_runner.py')),mode,'--path',str(Path(path).resolve())]
        if request is not None:
            input_path=root/'request.json';input_path.write_text(json.dumps(request,allow_nan=False));command+=['--request',str(input_path)]
        if outdir is not None:command+=['--out',str(Path(outdir).resolve())]
        with (root/'output.json').open('w+') as out,(root/'error.log').open('w+') as err:
            child=subprocess.Popen(command,env=env,cwd=root,stdout=out,stderr=err,start_new_session=True)
            started=time.monotonic()
            try:
                while child.poll() is None:
                    guard()
                    if time.monotonic()-started>60:raise ValueError('本地影像处理超时，原件保留')
                    time.sleep(.2)
                guard()
                if child.returncode:
                    err.seek(0);lines=err.read()[-1000:].splitlines();line=lines[-1] if lines else ''
                    if line.startswith('ValueError: '):raise ValueError(line.removeprefix('ValueError: '))
                    raise ValueError('本地影像解码或空间变换失败；原件保留，未联网处理')
                out.seek(0);value=out.read(2*1024*1024+1)
                if len(value)>2*1024*1024:raise ValueError('空间结果超过本地输出额度')
                result=json.loads(value)
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:child.wait(timeout=3)
                    except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)
    if file_digest(path)!=expected:raise ValueError('处理期间影像原件变化，结果未接受')
    if not isinstance(result,dict):raise ValueError('地理处理返回格式无效')
    return result
