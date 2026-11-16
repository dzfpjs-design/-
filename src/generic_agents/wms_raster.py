"""Pinned, credential-free local derivation under the durable job guard."""
import json,os,subprocess,tempfile,time
from pathlib import Path
from .config import settings
from .geo_codec import file_digest
from .contracts import digest

ROOT=Path(__file__).resolve().parents[2]
RECIPE=json.loads((ROOT/'runtime-packs/wms-rendered-raster-v1.json').read_text())
RECIPE_HASH=digest(RECIPE)


def convert(path,request,outdir,guard):
    runner=Path(__file__).with_name('wms_raster_runner.py');python=settings().geo_python.absolute()
    if not python.is_file() or file_digest(runner)!=RECIPE['runner_sha256']:raise ValueError('本地WMS转换环境或固定处理代码尚未核验')
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT'}}
    env.update(PROJ_NETWORK='OFF',GDAL_PAM_ENABLED='NO',GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR')
    with tempfile.TemporaryDirectory(prefix='ga-wms-raster-') as tmp:
        root=Path(tmp);input_file=root/'request.json';input_file.write_text(json.dumps({**request,'environment':RECIPE['environment']},allow_nan=False))
        command=[str(python),str(runner),'--path',str(Path(path).resolve()),'--request',str(input_file),'--out',str(Path(outdir).resolve())]
        with (root/'output.json').open('w+') as out,(root/'error.log').open('w+') as err:
            process=subprocess.Popen(command,env=env,cwd=root,stdout=out,stderr=err,start_new_session=True);started=time.monotonic()
            try:
                while process.poll() is None:
                    guard()
                    if time.monotonic()-started>45:raise ValueError('本地地图转换超时，原响应保留')
                    time.sleep(.1)
                guard()
                if process.returncode:raise ValueError('本地地图转换失败，请核对PNG响应和分辨率；原响应保留，不重新读取远端')
                out.seek(0);content=out.read(65537)
                if len(content)>65536:raise ValueError('地图转换输出超过限额')
                value=json.loads(content)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=3)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
    if value.get('schema')!='wms-rendered-raster-v1' or value.get('cloud_calls')!=0 or value.get('raw_satellite_bands') is not False:
        raise ValueError('地图转换返回来源声明无效')
    return value
