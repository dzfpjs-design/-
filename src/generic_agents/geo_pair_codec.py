"""Two immutable local originals, credential-free bounded subprocess and pinned recipe."""
import json,os,subprocess,tempfile,time
from pathlib import Path
from .contracts import digest
from .geo_codec import ROOT,RECIPE_HASH as BASE_HASH,file_digest
from .config import settings
RECIPE=json.loads((ROOT/'runtime-packs/geo-pair-v1.json').read_text())
RECIPE_HASH=digest(RECIPE)
V2=json.loads((ROOT/'runtime-packs/geo-pair-v2.json').read_text())
RECIPES={RECIPE['id']:RECIPE,V2['id']:V2}


def recipe_for_hash(value):
    for recipe in RECIPES.values():
        if digest(recipe)==value:return recipe
    raise ValueError('双影像运行配方不在受管版本中；旧记录保留')


def ready(recipe_id='geo-pair-v1'):
    recipe=RECIPES.get(recipe_id)
    if not recipe:raise ValueError('未登记的双影像运行配方')
    python=settings().geo_python.absolute();runner=Path(__file__).with_name('geo_pair_runner.py' if recipe_id=='geo-pair-v1' else 'geo_pair_runner_v2.py')
    if not python.is_file():raise ValueError('本地地理处理环境尚未安装')
    if BASE_HASH!=recipe['base_recipe_hash'] or file_digest(runner)!=recipe['runner_sha256'] or file_digest(runner.with_name('geo_runner.py'))!=recipe['base_runner_sha256']:
        raise ValueError('本地双影像处理代码或依赖配方与固定版本不一致')
    return recipe,python,runner


def call(before,after,request,outdir,guard=lambda:None,*,recipe_id='geo-pair-v1'):
    recipe,python,runner=ready(recipe_id)
    hashes=[file_digest(p) for p in (before,after)]
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT'}}
    env.update(PROJ_NETWORK='OFF',GDAL_PAM_ENABLED='NO',GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR')
    with tempfile.TemporaryDirectory(prefix='ga-geo-pair-') as tmp:
        root=Path(tmp);req=root/'request.json';req.write_text(json.dumps(request,allow_nan=False))
        with (root/'output.json').open('w+') as out,(root/'error.log').open('w+') as err:
            child=subprocess.Popen([str(python),str(runner),'--before',str(Path(before).resolve()),'--after',str(Path(after).resolve()),'--request',str(req),'--out',str(Path(outdir).resolve())],env=env,cwd=root,stdout=out,stderr=err,start_new_session=True)
            started=time.monotonic()
            try:
                while child.poll() is None:
                    guard()
                    if time.monotonic()-started>recipe['timeout_seconds']:raise ValueError('本地双影像比较超时；原件保留')
                    time.sleep(.2)
                guard()
                if child.returncode:
                    err.seek(0);lines=err.read()[-1000:].splitlines();line=lines[-1] if lines else ''
                    if line.startswith('ValueError: '):raise ValueError(line.removeprefix('ValueError: '))
                    raise ValueError('本地双影像比较失败；未联网处理，原件保留')
                out.seek(0);raw=out.read(1024*1024+1)
                if len(raw)>1024*1024:raise ValueError('比较元数据超过本地额度')
                value=json.loads(raw)
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:child.wait(timeout=3)
                    except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)
    if hashes!=[file_digest(p) for p in (before,after)]:raise ValueError('比较期间原件改变；结果未接受')
    if not isinstance(value,dict) or value.get('schema')!='local-rgb-comparison-v1' or value.get('cloud_calls')!=0 or value.get('learned_model') is not False:
        raise ValueError('本地像素比较来源声明无效')
    return value
