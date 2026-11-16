"""Isolated tests; actual RTSP and browser playback require integration tests."""
import unittest
import asyncio
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from generic_agents import instance_live_video as video
from generic_agents.models import AuthSession, User, now


class LivePreviewTest(unittest.TestCase):
    def setUp(self):
        self.p = SimpleNamespace(session_id='s', user_id='u', workspace_id='w', role='viewer')
        self.auth = SimpleNamespace(user_id='u', revoked=False, expires_at=now() + timedelta(hours=1))
        self.user = SimpleNamespace(active=True)
        self.member = SimpleNamespace(role='viewer')
        self.resource = {'id': 'c', 'category': 'video', 'protocol': 'RTSP / H.264',
                         'backend_source_proof': {'source_kind': 'media_recorded_video', 'channel_id': 'c', 'hash': 'ok'}}
        self.db = SimpleNamespace(get=lambda model, _: self.auth if model is AuthSession else self.user if model is User else None,
                                  scalar=lambda _: self.member, expire_all=lambda: None)
        configured_runtime = patch.object(video, 'settings', return_value=SimpleNamespace(
            specialist_python=Path('.runtime/venvs/rtdetr/bin/python')))
        configured_runtime.start(); self.addCleanup(configured_runtime.stop)

    def test_session_membership_and_frozen_proof_are_required(self):
        with patch.object(video, 'one_resource', return_value=self.resource), \
             patch.object(video, 'media_resource_proof', return_value={'source_kind': 'media_recorded_video', 'channel_id': 'c', 'hash': 'ok'}), \
             patch.object(video, 'validated_channel', return_value=('channel', 'gateway')):
            self.assertEqual(video._current(self.db, self.p, 'i', 'c')[:2], ('channel', 'gateway'))
            self.auth.revoked = True
            with self.assertRaises(HTTPException): video._current(self.db, self.p, 'i', 'c')
            self.auth.revoked = False
            self.member.role = 'operator'
            with self.assertRaises(HTTPException): video._current(self.db, self.p, 'i', 'c')
            self.member.role = 'viewer'
            self.resource['backend_source_proof']['hash'] = 'old'
            with self.assertRaises(HTTPException): video._current(self.db, self.p, 'i', 'c')

    def test_mjpeg_end_and_disconnect_release_slot_and_close(self):
        proc = SimpleNamespace(closed=False)
        self.assertTrue(video._slots.acquire(blocking=False))
        lease = video._Lease(); lease.proc = proc
        with patch.object(video, '_stop', side_effect=lambda x: setattr(x, 'closed', True)):
            stream = video._frames(lease, b'jpeg', self.p, 'i', 'c', {}, 'source')
            self.assertIn(b'Content-Type: image/jpeg', next(stream))
            stream.close()
            lease.close()
        self.assertTrue(proc.closed)
        self.assertTrue(video._slots.acquire(blocking=False))
        video._slots.release()

    def test_offline_or_invalid_source_returns_bounded_error(self):
        with patch.object(video, '_current', return_value=('channel', 'gateway', {})), \
             patch.object(video, 'source_state', side_effect=ValueError('do not expose upstream address')):
            with self.assertRaises(HTTPException) as error:
                video.live('i', 'c', self.p, self.db)
            self.assertEqual(error.exception.status_code, 409)
            self.assertNotIn('upstream', error.exception.detail)

    def test_child_receives_only_minimal_environment(self):
        proc = MagicMock()
        proc.poll.return_value = 0
        with patch.object(video, '_current', return_value=('channel', 'gateway', {})), \
             patch.object(video, 'source_state', return_value={'source_id': 'live'}), \
             patch.object(video, 'stream_url', return_value='rtsp://127.0.0.1:8554/test'), \
             patch.object(video, '_read_frame', return_value=b'frame'), \
             patch.dict(os.environ, {'GA_DATABASE_URL': 'private-test-value', 'MODEL_TOKEN': 'private-test-value'}), \
             patch.object(video.subprocess, 'Popen', return_value=proc) as spawn:
            result = video.live('i', 'c', self.p, self.db)
            env = spawn.call_args.kwargs['env']
            self.assertEqual(set(env), {'PATH', 'LANG'})
            self.assertNotIn('private-test-value', str(env))
            result.lease.close()

    def test_configured_decoder_runtime_is_used_without_resolving_symlink(self):
        proc = MagicMock(); proc.poll.return_value = 0
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)/'configured-python'
            runtime.symlink_to(sys.executable)
            with patch.object(video, 'settings', return_value=SimpleNamespace(specialist_python=runtime)), \
                 patch.object(video, '_current', return_value=('channel', 'gateway', {})), \
                 patch.object(video, 'source_state', return_value={'source_id': 'live'}), \
                 patch.object(video, 'stream_url', return_value='rtsp://127.0.0.1:8554/test'), \
                 patch.object(video, '_read_frame', return_value=b'frame'), \
                 patch.object(video.subprocess, 'Popen', return_value=proc) as spawn:
                result = video.live('i', 'c', self.p, self.db)
                self.assertEqual(spawn.call_args.args[0][0], str(runtime))
                result.lease.close()

    def test_revocation_while_waiting_for_first_frame_prevents_delivery(self):
        proc = MagicMock()
        proc.poll.return_value = 0
        calls = []
        def current(*_):
            calls.append('authorization')
            if len(calls) > 2: raise HTTPException(403, 'access revoked')
            return ('channel', 'gateway', {'source_kind': 'proof'})
        def first_frame(*_):
            calls.append('frame')
            return b'frame'
        with patch.object(video, '_current', side_effect=current), \
             patch.object(video, 'source_state', return_value={'source_id': 'live'}), \
             patch.object(video, 'stream_url', return_value='rtsp://127.0.0.1:8554/test'), \
             patch.object(video, '_read_frame', side_effect=first_frame), \
             patch.object(video.subprocess, 'Popen', return_value=proc):
            with self.assertRaises(HTTPException): video.live('i', 'c', self.p, self.db)
        self.assertEqual(calls, ['authorization', 'frame', 'authorization'])
        proc.stdout.close.assert_called_once()
        self.assertTrue(video._slots.acquire(blocking=False))
        self.assertTrue(video._slots.acquire(blocking=False))
        video._slots.release(); video._slots.release()

    def test_revocation_during_final_source_check_prevents_first_frame(self):
        proc = MagicMock()
        proc.poll.return_value = 0
        calls = []
        def current(*_):
            calls.append('authorization')
            if calls.count('authorization') == 2:
                raise HTTPException(403, 'access revoked')
            return ('channel', 'gateway', {'source_kind': 'proof'})
        def source(*_):
            calls.append('source')
            return {'source_id': 'live'}
        def first_frame(*_):
            calls.append('frame')
            return b'frame'
        with patch.object(video, '_current', side_effect=current), \
             patch.object(video, 'source_state', side_effect=source), \
             patch.object(video, 'stream_url', return_value='rtsp://127.0.0.1:8554/test'), \
             patch.object(video, '_read_frame', side_effect=first_frame), \
             patch.object(video.subprocess, 'Popen', return_value=proc):
            with self.assertRaises(HTTPException): video.live('i', 'c', self.p, self.db)
        self.assertEqual(calls, ['authorization', 'source', 'frame', 'source', 'authorization'])
        proc.stdout.close.assert_called_once()
        self.assertTrue(video._slots.acquire(blocking=False))
        self.assertTrue(video._slots.acquire(blocking=False))
        video._slots.release(); video._slots.release()

    def test_revocation_during_frame_wait_prevents_next_frame(self):
        lease = MagicMock()
        calls = []
        db = MagicMock()
        session = MagicMock(); session.__enter__.return_value = db
        def frame(*_): calls.append('frame'); return b'next'
        def revoked(*_): calls.append('authorization'); raise HTTPException(403, 'access revoked')
        with patch.object(video, '_read_frame', side_effect=frame), \
             patch.object(video, '_current', side_effect=revoked), \
             patch.object(video, 'engine', return_value=object()), \
             patch.object(video, 'sessionmaker', return_value=lambda: session):
            stream = video._frames(lease, b'first', self.p, 'i', 'c', {}, 'source')
            self.assertIn(b'first', next(stream))
            with self.assertRaises(StopIteration): next(stream)
        self.assertEqual(calls, ['frame', 'authorization'])
        lease.close.assert_called_once()

    def test_revocation_during_periodic_source_check_prevents_next_frame(self):
        lease = MagicMock()
        calls = []
        db = MagicMock()
        session = MagicMock(); session.__enter__.return_value = db
        def frame(*_): calls.append('frame'); return b'next'
        def current(*_):
            calls.append('authorization')
            if calls.count('authorization') == 2: raise HTTPException(403, 'access revoked')
            return 'channel', 'gateway', {'proof': 'ok'}
        def source(*_): calls.append('source'); return {'source_id': 'source'}
        with patch.object(video, '_read_frame', side_effect=frame), \
             patch.object(video, '_current', side_effect=current), \
             patch.object(video, 'source_state', side_effect=source), \
             patch.object(video, 'engine', return_value=object()), \
             patch.object(video, 'sessionmaker', return_value=lambda: session), \
             patch.object(video.time, 'monotonic', side_effect=[0, 11, 11]):
            stream = video._frames(lease, b'first', self.p, 'i', 'c', {'proof': 'ok'}, 'source')
            self.assertIn(b'first', next(stream))
            with self.assertRaises(StopIteration): next(stream)
        self.assertEqual(calls, ['frame', 'authorization', 'source', 'authorization'])
        db.expire_all.assert_called_once()
        lease.close.assert_called_once()

    def test_immediate_asgi_disconnect_stops_child_and_releases_slot(self):
        self.assertTrue(video._slots.acquire(blocking=False))
        lease = video._Lease()
        lease.proc = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        response = video._LiveResponse(lease, iter([b'data']))
        async def receive(): return {'type': 'http.disconnect'}
        async def send(_): raise OSError('client disconnected')
        async def run():
            with self.assertRaises(Exception):
                await response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)
        asyncio.run(run())
        self.assertIsNotNone(lease.proc.poll())
        self.assertTrue(lease.proc.stdout.closed)
        self.assertTrue(video._slots.acquire(blocking=False))
        self.assertTrue(video._slots.acquire(blocking=False))
        self.assertFalse(video._slots.acquire(blocking=False))
        video._slots.release(); video._slots.release()


if __name__ == '__main__': unittest.main()
