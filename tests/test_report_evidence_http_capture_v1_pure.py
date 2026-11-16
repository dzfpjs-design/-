"""Root-only pure-context + real temporary-FS tests; author did not execute.

All bytes/scopes below are public synthetic. No HTTP, DB, ORM, model, original
document, credentials or environment loader is used or mocked as successful.
Real O_EXCL, permissions, fsync, partial files and readonly checks are exercised.
"""
import copy
import gzip
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


from generic_agents import report_evidence_http_capture_v1 as capture


def context(stage='map-1', call='44444444-4444-4444-8444-444444444444'):
    return {'binding': {'schema': 'report-evidence-journal-binding-v2' if stage == 'reduce' else 'report-evidence-journal-binding-v1',
        'actor_id': '11111111-1111-4111-8111-111111111111',
        'workspace_id': '22222222-2222-4222-8222-222222222222',
        'draft_id': '33333333-3333-4333-8333-333333333333', 'stage': stage,
        'reserved_call_id': call, 'source_hash': 'a' * 64, 'catalog_hash': 'b' * 64,
        'generation_hash': 'c' * 64, 'dispatch_hash': 'd' * 64, 'messages_hash': 'e' * 64},
        'request_hash': 'f' * 64, 'endpoint': capture.ENDPOINT, 'delivery': capture.DELIVERY,
        'schema_proof_hash': '1' * 64, 'profile_hash': '2' * 64, 'token_bound': 12000,
        'max_bytes': capture.REDUCE_LIMIT if stage == 'reduce' else capture.MAP_LIMIT}


def stage_path(root, ctx):
    binding = ctx['binding']
    return root / binding['workspace_id'] / binding['draft_id'] / binding['stage']


def snapshot(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode,
        path.stat().st_mtime_ns, path.stat().st_ino)
        for path in root.rglob('*') if path.is_file()}


class NativeCapturePure(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # macOS /tmp and /var may be symlinks; exercise the real canonical path.
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'capture'

    def prepared(self, ctx=None):
        ctx = context() if ctx is None else ctx
        sink = capture.prepare(self.root, ctx)
        self.addCleanup(sink.close)
        return sink

    def start(self, sink, encoding=None, status=200):
        sink.mark_request_started()
        sink.begin_response(status, encoding, 'PUBLIC_REQUEST_ID')

    def test_context_is_closed_typed_and_fixed_before_any_file_creation(self):
        for mutate in (lambda v: v.update(extra=True), lambda v: v.update(token_bound=True),
                lambda v: v.update(token_bound=1000001), lambda v: v.update(max_bytes=True),
                lambda v: v.update(max_bytes=capture.REDUCE_LIMIT),
                lambda v: v.update(endpoint='https://example.invalid'), lambda v: v.update(delivery='old-tool'),
                lambda v: v.update(request_hash='not-a-hash'), lambda v: v['binding'].update(stage='map-25'),
                lambda v: v['binding'].update(stage='../map-1'), lambda v: v['binding'].update(actor_id=True),
                lambda v: v['binding'].update(stage='reduce')):
            ctx = context(); mutate(ctx)
            with self.subTest(mutate=mutate), self.assertRaises(capture.CaptureError):
                capture.prepare(self.root, ctx)
            self.assertFalse(self.root.exists())
        self.assertEqual(capture.load(self.root, context())['state'], 'missing')
        self.assertFalse(self.root.exists())
        with self.assertRaises(capture.CaptureError): capture.NativeHttpCapture()

    def test_map_and_reduce_capture_original_bytes_without_model_parse_or_adoption(self):
        for number, stage in enumerate(('map-1', 'reduce'), 1):
            ctx = context(stage, f'44444444-4444-4444-8444-{number:012d}')
            sink = self.prepared(ctx); self.start(sink, 'identity', status=400)
            raw = b'{"bad": unescaped "quote"}\x00\xff\n'
            sink.append(raw[:8]); sink.append(raw[8:])
            done = sink.finish(eof=True)
            self.assertEqual(done['state'], 'complete')
            self.assertTrue(done['response_complete']); self.assertTrue(done['ready'])
            self.assertFalse(done['can_adopt']); self.assertEqual(done['http_status'], 400)
            self.assertEqual(done['body_bytes'], len(raw))
            self.assertEqual(done['body_sha256'], capture._hash(raw))
            self.assertEqual(capture.read_body(self.root, ctx, done['capture_hash']), raw)
            self.assertEqual(capture.load(self.root, ctx), done)
            self.assertEqual(ctx, context(stage, f'44444444-4444-4444-8444-{number:012d}'))
            with self.assertRaises(capture.CaptureError): sink.finish(eof=True)
            with self.assertRaises(capture.CaptureError): sink.abort('cancelled')

    def test_stage_and_global_call_claims_cannot_be_reused_or_overwritten(self):
        ctx = context(); sink = self.prepared(ctx)
        original = snapshot(stage_path(self.root, ctx))
        with self.assertRaises(capture.CaptureError): capture.prepare(self.root, ctx)
        changed = copy.deepcopy(ctx); changed['binding']['stage'] = 'map-2'
        with self.assertRaises(capture.CaptureError): capture.prepare(self.root, changed)
        changed = copy.deepcopy(ctx); changed['binding']['workspace_id'] = '55555555-5555-4555-8555-555555555555'
        with self.assertRaises(capture.CaptureError): capture.prepare(self.root, changed)
        changed = context(call='66666666-6666-4666-8666-666666666666')
        with self.assertRaises(capture.CaptureError): capture.prepare(self.root, changed)
        self.assertEqual(snapshot(stage_path(self.root, ctx)), original)
        self.assertFalse(capture.load(self.root, ctx)['request_started'])
        self.assertFalse(capture.load(self.root, ctx)['ready'])
        sink.close()

    def test_no_started_no_headers_or_implicit_eof_can_become_complete(self):
        sink = self.prepared()
        for action in (lambda: sink.begin_response(200, None, None), lambda: sink.append(b'{}'),
                lambda: sink.finish(eof=True)):
            with self.assertRaises(capture.CaptureError): action()
        with self.assertRaises(TypeError): sink.finish()
        for value in (False, 1, None, 'true'):
            with self.subTest(eof=value), self.assertRaises(capture.CaptureError): sink.finish(eof=value)
        self.assertFalse(capture.load(self.root, context())['ready'])
        self.start(sink)
        sink.append(b'{"valid":"JSON but not EOF"}')
        self.assertEqual(capture.load(self.root, context())['state'], 'partial')
        self.assertFalse(capture.load(self.root, context())['response_complete'])

    def test_abort_close_and_partial_json_never_supply_completion(self):
        sink = self.prepared(); self.start(sink)
        raw = b'{"synthetic":"valid complete JSON"}'
        sink.append(raw)
        partial = capture.load(self.root, context())
        self.assertEqual(partial['state'], 'partial'); self.assertFalse(partial['ready'])
        self.assertEqual(capture.read_body(self.root, context(), partial['capture_hash']), raw)
        aborted = sink.abort('transport_error')
        self.assertEqual(aborted['state'], 'partial'); self.assertFalse(aborted['response_complete'])
        self.assertFalse(aborted['ready']); self.assertFalse(aborted['can_adopt'])
        self.assertEqual(aborted['omitted_bytes'], 0)
        with self.assertRaises(capture.CaptureError): sink.finish(eof=True)
        sink.close()
        self.assertEqual(capture.load(self.root, context()), aborted)
        with self.assertRaises(capture.CaptureError): capture.prepare(self.root, context())

    def test_encoding_and_header_safety_do_not_rewrite_raw_bytes(self):
        raw = gzip.compress(b'PUBLIC SYNTHETIC BODY', mtime=0)
        sink = self.prepared(); self.start(sink, 'gzip')
        sink.append(raw)
        done = sink.finish(eof=True)
        self.assertEqual(done['state'], 'quarantined')
        self.assertTrue(done['response_complete']); self.assertFalse(done['ready'])
        self.assertEqual(done['reason'], 'unexpected_content_encoding')
        self.assertEqual(done['http_content_encoding'], 'gzip')
        self.assertEqual(capture.read_body(self.root, context(), done['capture_hash']), raw)
        ctx = context('map-2', '77777777-7777-4777-8777-777777777777')
        other = self.prepared(ctx); other.mark_request_started()
        other.begin_response(200, '\x00gzip', 'private\r\nunsafe')
        other.append(b'original')
        unsafe = other.finish(eof=True)
        self.assertEqual(unsafe['state'], 'quarantined')
        self.assertIsNone(unsafe['http_content_encoding']); self.assertFalse(unsafe['content_encoding_safe'])
        self.assertIsNone(unsafe['request_id']); self.assertFalse(unsafe['request_id_safe'])
        self.assertEqual(capture.read_body(self.root, ctx, unsafe['capture_hash']), b'original')

    def test_overflow_keeps_only_explicit_cap_plus_one_prefix_and_stops(self):
        ctx = context(); sink = self.prepared(ctx); self.start(sink)
        chunk = b'a' * capture.CHUNK_LIMIT
        for _ in range(ctx['max_bytes'] // len(chunk)): sink.append(chunk)
        with self.assertRaises(capture.CaptureError) as failure: sink.append(b'b' * 17)
        self.assertEqual(failure.exception.code, 'capture_capacity_exceeded')
        state = capture.load(self.root, ctx)
        self.assertEqual(state['state'], 'quarantined'); self.assertTrue(state['prefix_only'])
        self.assertFalse(state['response_complete']); self.assertFalse(state['ready'])
        self.assertEqual(state['body_bytes'], ctx['max_bytes'] + 1)
        self.assertEqual(state['observed_bytes'], ctx['max_bytes'] + 17)
        self.assertEqual(state['omitted_bytes'], 16)
        self.assertEqual(capture.read_body(self.root, ctx, state['capture_hash']), b'a' * ctx['max_bytes'] + b'b')
        for action in (lambda: sink.append(b'next'), lambda: sink.finish(eof=True)):
            with self.assertRaises(capture.CaptureError): action()

    def test_chunk_limit_and_raw_byte_types_cannot_hide_truncation(self):
        sink = self.prepared(); self.start(sink)
        for value in ('text', bytearray(b'raw'), memoryview(b'raw'), True):
            with self.subTest(value=type(value)), self.assertRaises(capture.CaptureError): sink.append(value)
        with self.assertRaises(capture.CaptureError) as failure:
            sink.append(b'x' * (capture.CHUNK_LIMIT + 1))
        self.assertEqual(failure.exception.code, 'capture_chunk_limit')
        state = capture.load(self.root, context())
        self.assertEqual((state['body_bytes'], state['observed_bytes'], state['omitted_bytes']),
            (capture.CHUNK_LIMIT, capture.CHUNK_LIMIT + 1, 1))
        self.assertTrue(state['prefix_only']); self.assertFalse(state['response_complete'])
        self.assertFalse(state['can_adopt'])

    def test_real_body_fsync_and_directory_fsync_precede_complete_metadata(self):
        sink = self.prepared(); self.start(sink)
        stage = stage_path(self.root, context())
        body_inode, dir_inode = (stage / 'body.bin').stat().st_ino, stage.stat().st_ino
        events = []
        actual_write, actual_fsync, actual_new = os.write, os.fsync, capture._new
        def write(fd, raw):
            if os.fstat(fd).st_ino == body_inode: events.append('body_write')
            return actual_write(fd, raw)
        def fsync(fd):
            inode = os.fstat(fd).st_ino
            if inode == body_inode: events.append('body_fsync')
            if inode == dir_inode: events.append('dir_fsync')
            return actual_fsync(fd)
        def new(directory, name, raw):
            events.append('new:' + name)
            return actual_new(directory, name, raw)
        with patch.object(capture.os, 'write', side_effect=write), patch.object(capture.os, 'fsync', side_effect=fsync), patch.object(capture, '_new', side_effect=new):
            sink.append(b'PUBLIC BYTES')
            sink.finish(eof=True)
        self.assertLess(events.index('body_write'), events.index('body_fsync'))
        self.assertLess(events.index('body_fsync'), events.index('dir_fsync'))
        self.assertLess(events.index('dir_fsync'), events.index('new:end.json'))
        self.assertEqual(events[-1], 'dir_fsync')

    def test_fsync_failure_retains_original_partial_and_forbids_retry(self):
        sink = self.prepared(); self.start(sink)
        inode = (stage_path(self.root, context()) / 'body.bin').stat().st_ino
        actual = os.fsync
        def broken(fd):
            if os.fstat(fd).st_ino == inode: raise OSError('PUBLIC_SYNTHETIC_FSYNC_FAILURE')
            return actual(fd)
        with patch.object(capture.os, 'fsync', side_effect=broken):
            with self.assertRaises(capture.CaptureError) as failure: sink.append(b'kept before fsync failed')
        self.assertEqual(failure.exception.code, 'capture_not_durable')
        self.assertEqual((stage_path(self.root, context()) / 'body.bin').read_bytes(), b'kept before fsync failed')
        self.assertFalse((stage_path(self.root, context()) / 'end.json').exists())
        with self.assertRaises(capture.CaptureError): sink.finish(eof=True)
        sink.close()
        state = capture.load(self.root, context())
        self.assertEqual(state['state'], 'partial'); self.assertFalse(state['ready'])
        with self.assertRaises(capture.CaptureError): capture.prepare(self.root, context())

    def test_alias_symlink_hardlink_and_unsafe_file_modes_are_rejected(self):
        self.root.mkdir(mode=0o700)
        alias = self.base / 'alias'; alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(capture.CaptureError): capture.prepare(alias, context())
        with self.assertRaises(capture.CaptureError): capture.prepare(self.root / '..' / 'alias', context())
        sink = self.prepared(); self.start(sink); sink.append(b'public'); sink.close()
        stage = stage_path(self.root, context()); body = stage / 'body.bin'
        outside = self.base / 'public-outside'; outside.write_bytes(b'outside'); outside.chmod(0o600)
        saved = body.read_bytes(); body.unlink(); body.symlink_to(outside)
        with self.assertRaises(capture.CaptureError): capture.load(self.root, context())
        self.assertEqual(outside.read_bytes(), b'outside')
        body.unlink(); body.write_bytes(saved); body.chmod(0o600)
        link = self.base / 'public-hardlink'; os.link(body, link)
        with self.assertRaises(capture.CaptureError): capture.load(self.root, context())
        link.unlink(); body.chmod(0o644)
        with self.assertRaises(capture.CaptureError): capture.load(self.root, context())

    def test_context_fences_complete_tamper_and_readonly_load_never_repair(self):
        ctx = context(); sink = self.prepared(ctx); self.start(sink)
        sink.append(b'first')
        old = capture.load(self.root, ctx)
        sink.append(b'second')
        with self.assertRaises(capture.CaptureError): capture.read_body(self.root, ctx, old['capture_hash'])
        done = sink.finish(eof=True); sink.close()
        before = snapshot(self.root)
        for _ in range(2):
            self.assertEqual(capture.load(self.root, ctx), done)
            self.assertEqual(capture.read_body(self.root, ctx, done['capture_hash']), b'firstsecond')
        self.assertEqual(snapshot(self.root), before)
        for mutate in (lambda v: v['binding'].update(actor_id='55555555-5555-4555-8555-555555555555'),
                lambda v: v.update(request_hash='3' * 64), lambda v: v.update(profile_hash='4' * 64)):
            changed = copy.deepcopy(ctx); mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(capture.CaptureError): capture.load(self.root, changed)
        self.assertEqual(snapshot(self.root), before)
        (stage_path(self.root, ctx) / 'body.bin').write_bytes(b'tampered')
        with self.assertRaises(capture.CaptureError): capture.load(self.root, ctx)
        with self.assertRaises(capture.CaptureError): capture.read_body(self.root, ctx, done['capture_hash'])


if __name__ == '__main__':
    unittest.main()
