"""Read actual GUI-created schedule and save checkpoints; public synthetic RTSP only."""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import CloudCall

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/evidence/local-monitor-v06'
OUT.mkdir(parents=True, exist_ok=True)


def save(name, value):
    path = OUT / (name + '.json'); temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)); temp.replace(path)


def request(c, path):
    response = c.get('/api' + path); response.raise_for_status(); return response.json()


def run(phase):
    access = json.loads((ROOT / '.runtime/understanding-validation-access.json').read_text())
    source = json.loads((ROOT / 'docs/evidence/local-rtsp-v06/selection.json').read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765', trust_env=False, timeout=30) as c:
        for _ in range(100):
            try: c.get('/health').raise_for_status(); break
            except httpx.HTTPError: time.sleep(.1)
        else: raise AssertionError('API did not become healthy after restart')
        r = c.post('/api/login', json={k: access[k] for k in ['username', 'password']}); r.raise_for_status()
        c.headers.update({'X-Workspace-ID': access['workspace_id'], 'X-CSRF-Token': r.json()['csrf_token']})
        def state():
            monitors = request(c, '/media/monitors')
            m = next(x for x in monitors if x['channel_id'] == source['channel_id'])
            sessions = [x for x in request(c, '/media/sessions') if x.get('monitor_id') == m['id']]
            return {'monitor': m, 'sessions': sorted(sessions, key=lambda x: x['monitor_sequence'])}
        if phase in {'wait-failure', 'wait-success', 'final'}:
            deadline = time.monotonic() + 160
            previous = None
            try:
                while time.monotonic() < deadline:
                    value = state(); m = value['monitor']; code = (m['status'], m['attempts'], m['successful_segments'], len(m['outcomes']))
                    if code != previous: print(json.dumps({'state': code}), flush=True); previous = code
                    if phase == 'wait-failure' and m['outcomes']:
                        assert m['outcomes'][0]['capture_status'] == 'failed'
                        assert value['sessions'][0]['evidence_id'] is None and value['sessions'][0]['video_run_id'] is None
                        save('offline', value); break
                    if phase == 'wait-success' and m['successful_segments'] >= 1:
                        save('reconnected', value); break
                    if phase == 'final' and m['status'] == 'exhausted': break
                    time.sleep(1)
                else: raise AssertionError('actual saved monitor checkpoint deadline exceeded')
            finally:
                if phase == 'wait-failure':
                    subprocess.run([sys.executable, str(ROOT / 'scripts/media_runtime.py'), 'publisher-start'], check=True)
        if phase == 'snapshot':
            value = state(); assert value['monitor']['status'] == 'paused' and value['monitor']['current_session_id'] is None
            save('before-restart', value); print('paused state persisted', flush=True)
        elif phase == 'restart-check':
            value = state(); before = json.loads((OUT / 'before-restart.json').read_text())
            assert value == before; save('after-restart', value); print('paused records identical after service restart', flush=True)
        elif phase == 'final':
            m = value['monitor']; assert m['attempts'] == 3 and m['successful_segments'] == 2 and len(m['outcomes']) == 3
            assert m['reserved_seconds'] == 18 and m['continuous_recording'] is False
            videos = []
            for session in value['sessions']:
                if not session['video_run_id']: continue
                detail = request(c, '/videos/' + session['video_run_id'])
                assert detail['status'] == 'succeeded' and detail['source_context']['monitor_id'] == m['id']
                assert len(detail['frames']) == detail['delivery']['data_received'] and detail['delivery']['pending'] == 0
                original = c.get('/api/videos/' + detail['id'] + '/original'); original.raise_for_status()
                assert hashlib.sha256(original.content).hexdigest() == detail['input_sha256']
                videos.append({'id': detail['id'], 'frames': len(detail['frames']), 'delivery': detail['delivery'],
                    'sha256': detail['input_sha256'], 'bytes': len(original.content), 'pipeline_hash': detail['pipeline_hash']})
            with Session(engine()) as db:
                calls = db.scalars(select(CloudCall).where(CloudCall.workspace_id == access['workspace_id'])).all()
                baseline = json.loads((ROOT / '.runtime/setup-recovery-baseline.json').read_text())
                assert sorted(x.id for x in calls) == sorted(baseline['cloud_ids'])
            save('runtime', {**value, 'videos': videos, 'new_cloud_calls': 0,
                'source': 'actual MediaMTX/RTSP public still-image synthetic publisher; not physical device/domain accuracy proof',
                'verified_unix': time.time()})
            print(json.dumps({'attempts': m['attempts'], 'success': m['successful_segments'], 'videos': len(videos), 'new_cloud_calls': 0}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('phase', choices=['wait-failure', 'wait-success', 'snapshot', 'restart-check', 'final'])
    run(parser.parse_args().phase)
