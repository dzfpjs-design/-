"""Bounded local API readiness; no sign-in, mutation or process restart."""
import time
import httpx


def api_is_ready():
    try:
        r=httpx.get('http://127.0.0.1:8765/health',timeout=1,trust_env=False,follow_redirects=False)
        return r.status_code==200 and r.json().get('status')=='ok' and r.json().get('database')=='postgresql'
    except (httpx.HTTPError,ValueError,AttributeError):return False


def wait_for_api(alive,*,timeout=20,probe=api_is_ready,clock=time.monotonic,pause=time.sleep):
    deadline=clock()+timeout
    while clock()<deadline:
        if not alive():raise RuntimeError('API进程已退出；请核对本项目API日志，原数据保留')
        if probe():return True
        pause(min(.2,max(0,deadline-clock())))
    raise RuntimeError('API进程仍在，但接口或数据库尚未就绪；原服务未自动重启，原数据保留')
