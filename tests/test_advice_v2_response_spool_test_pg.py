"""Synthetic private filesystem tests; no application data or report path."""
import os,stat,tempfile
from pathlib import Path
from uuid import uuid4
import pytest
from generic_agents import same_goal_advice_response_spool_test_v2 as spool


@pytest.fixture
def base():
    with tempfile.TemporaryDirectory(prefix='advice-v2-response-spool-') as directory:
        root=Path(directory);ctx={'schema':'advice-v2-private-spool-test-v1','workspace_id':str(uuid4()),
            'call_id':str(uuid4()),'intent_id':str(uuid4()),'permit_id':str(uuid4()),'anchor_hash':'a'*64,
            'provider':'deepseek','model':'synthetic'}
        receipt={'provider':'deepseek','model':'synthetic','call_id':ctx['call_id'],'http_status':200,
            'request_id':None,'usage':{},'usage_unavailable':True,'usage_unavailable_reason':'unparsed_original_http',
            'finish_reason':None,'response_choice_count':None,'stage':'business-reasoning-v197'}
        yield root,ctx,receipt


def test_complete_encrypted_roundtrip_and_lock(base):
    root,ctx,receipt=base;raw=b'private synthetic response'
    with spool.prepare(root,ctx) as sink:
        sink.begin(receipt);sink.append(raw)
        with pytest.raises(spool.SpoolError):spool.load(root,ctx)
        sink.finish()
    assert spool.load(root,ctx)==(raw,receipt)
    stage=root/ctx['call_id'];assert raw not in (stage/'body.bin').read_bytes()
    assert stat.S_IMODE(stage.stat().st_mode)==0o700
    assert all(stat.S_IMODE(item.stat().st_mode)==0o600 for item in stage.iterdir())
    with pytest.raises(spool.SpoolError):spool.prepare(root,ctx)


def test_partial_response_never_promoted(base):
    root,ctx,receipt=base
    with spool.prepare(root,ctx) as sink:sink.begin(receipt);sink.append(b'partial')
    with pytest.raises(spool.SpoolError):spool.load(root,ctx)
    with pytest.raises(spool.SpoolError):spool.prepare(root,ctx)


@pytest.mark.parametrize('changed',['body','metadata','wrong_context','symlink','hardlink'])
def test_tampering_and_unsafe_files_are_refused(base,changed,tmp_path):
    root,ctx,receipt=base
    with spool.prepare(root,ctx) as sink:sink.begin(receipt);sink.append(b'synthetic');sink.finish()
    stage=root/ctx['call_id']
    if changed=='body':
        body=stage/'body.bin';content=body.read_bytes();body.write_bytes(bytes([content[0]^1])+content[1:])
    elif changed=='metadata':
        target=stage/'final.json';target.write_text(target.read_text().replace('"raw_size":9','"raw_size":8'))
    elif changed=='wrong_context':ctx={**ctx,'intent_id':str(uuid4())}
    elif changed=='symlink':
        target=stage/'body.bin';target.unlink();target.symlink_to(tmp_path/'elsewhere')
    else:os.link(stage/'body.bin',tmp_path/'linked')
    with pytest.raises(spool.SpoolError):spool.load(root,ctx)


def test_capacity_and_unsafe_root_fail_closed(base,tmp_path):
    root,ctx,receipt=base
    for _ in range(spool.MAX_SLOTS):
        another={**ctx,'call_id':str(uuid4())}
        with spool.prepare(root,another) as sink:sink.begin({**receipt,'call_id':another['call_id']})
    with pytest.raises(spool.SpoolError):spool.prepare(root,{**ctx,'call_id':str(uuid4())})
    with pytest.raises(spool.SpoolError):spool.prepare(tmp_path/'not-a-test-root',ctx)


def test_failed_write_poisoned_sink_cannot_be_completed(base,monkeypatch):
    root,ctx,receipt=base
    with spool.prepare(root,ctx) as sink:
        sink.begin(receipt)
        monkeypatch.setattr(spool,'MAX_BYTES',3)
        with pytest.raises(spool.SpoolError):sink.append(b'too large')
        with pytest.raises(spool.SpoolError):sink.finish()
    with pytest.raises(spool.SpoolError):spool.load(root,ctx)


def test_failed_completion_fsync_does_not_promote_visible_marker(base,monkeypatch):
    root,ctx,receipt=base
    with spool.prepare(root,ctx) as sink:
        sink.begin(receipt);sink.append(b'synthetic')
        original=spool._replace_reserved
        def fail_after_marker(directory,name,raw):
            original(directory,name,raw)
            if name=='final.json':raise OSError('synthetic sync failure')
        monkeypatch.setattr(spool,'_replace_reserved',fail_after_marker)
        with pytest.raises(OSError):sink.finish()
    with pytest.raises(spool.SpoolError):spool.load(root,ctx)


def test_concurrent_slot_claim_is_exclusive_and_abandoned_slot_is_retained(base):
    from concurrent.futures import ThreadPoolExecutor
    root,ctx,_=base
    def claim():
        try:
            with spool.allocate(root,ctx['call_id']):return 'claimed'
        except spool.SpoolError:return 'rejected'
    with ThreadPoolExecutor(max_workers=4) as pool:result=list(pool.map(lambda _:claim(),range(4)))
    assert result.count('claimed')==1 and result.count('rejected')==3
    with pytest.raises(spool.SpoolError):spool.load(root,ctx)
    with pytest.raises(spool.SpoolError):spool.allocate(root,ctx['call_id'])
