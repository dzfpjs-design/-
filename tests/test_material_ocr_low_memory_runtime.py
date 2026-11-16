"""Known fixed OCR profiles with explicit synthetic files/process adapters.

No DB, service, real process, OCR, model installation or network is permitted.
These cases are authored for the root's isolated pure run; not run by this agent.
"""
import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import material_ocr_runtime as r

OLD_HASH='55c902bbe51f2cdc9841e8571e9729f4c1a24c628601361a77019fbc7b1040e2'
NEW_HASH='3a11f677a07ca36d3a767379b00546aa41c1c47f2dc659b80344872e7c10d13f'
NEW_ID='rapidocr-3.10.0-ppocrv6-small-cpu-m49-low-memory-v1'


@pytest.fixture(autouse=True)
def forbid_actual_processes(monkeypatch):
    def forbidden(*args,**kwargs):pytest.fail('pure profile test must never start a real process')
    monkeypatch.setattr(r.subprocess,'Popen',forbidden)
    monkeypatch.setattr(r.subprocess,'run',forbidden)


@pytest.fixture
def pair(monkeypatch,tmp_path):
    """Only this fixture authorizes synthetic manifests; production pins stay fixed."""
    root=tmp_path/'two fixed profiles 中文'
    monkeypatch.setattr(r,'local_runtime_root',lambda:root)
    legacy=r._operation_paths();low=r._operation_paths(engine_hash=NEW_HASH)
    def put(base,name,data):
        p=base/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        return {'path':name,'sha256':r._sha(data)}
    py=put(legacy.python.parent.parent,'bin/python',b'explicit nonexecutable interpreter')
    cfg=put(legacy.python.parent.parent,'pyvenv.cfg',b'synthetic private venv')
    base=put(legacy.base_runtime,'lib/bootstrap.py',b'private base')
    code=put(legacy.site,'module.py',b'private python')
    data=put(legacy.site,'resource.data',b'private resource')
    def manifest(paths,id_,name,low_memory):
        return {'id':id_,'name':name,'contract':'synthetic-only','python':'3.12','packages':[],
          'interpreter':{'sha256':py['sha256'],'venv_config_sha256':cfg['sha256'],'private_base':'base-python','private_base_required':True},
          'base_runtime_files':[base],'private_site_files':[code,data],'runtime_files':[code],
          'runner':put(paths.base,'runner.py',b'nonexecuted runner'),
          'config':put(paths.base,'config.json',r._canonical({'params':{'Rec.rec_batch_num':1,'Cls.cls_batch_num':1} if low_memory else {}})),
          'dictionary':put(paths.base,'models/dictionary.json',b'[]'),
          'models':[put(paths.base,'models/model.bin',b'not a model')],
          'notices':[put(paths.base,'NOTICE',b'notice')],
          'limits':{'resident_memory_bytes':1073741824,'wall_timeout_seconds':90},'data_policy':{'cloud_allowed':False}}
    a=manifest(legacy,r._ENGINE_ID,'legacy synthetic',False)
    b=manifest(low,NEW_ID,'low synthetic',True)
    for paths,value in [(legacy,a),(low,b)]:
        (paths.base/'manifest.json').write_bytes(r._canonical(value))
    old=r._sha(r._canonical(a));new=r._sha(r._canonical(b))
    monkeypatch.setattr(r,'_MANIFEST_HASH',old)
    monkeypatch.setattr(r,'LOW_MEMORY_MANIFEST_HASH',new)
    monkeypatch.setattr(r,'_LOW_MEMORY_PROFILE',replace(r._LOW_MEMORY_PROFILE,manifest_hash=new))
    low=r._operation_paths(engine_hash=new)
    monkeypatch.setattr(r.sys,'platform','darwin')
    sandbox=tmp_path/'sandbox-exec';sandbox.write_bytes(b'not executable')
    monkeypatch.setattr(r,'_SANDBOX',sandbox)
    probes=[]
    def probe(*,_paths):
        assert _paths.python==legacy.python
        assert _paths.profile.startswith('(version 1)(allow default)(deny network*)')
        for p in [_paths.base,legacy.python.parent.parent]:
            assert '(deny file-write* (subpath '+json.dumps(str(p),ensure_ascii=False)+'))' in _paths.profile
        probes.append(_paths)
    monkeypatch.setattr(r,'_sandbox_probe',probe)
    return SimpleNamespace(root=root,legacy=legacy,low=low,old_hash=old,new_hash=new,old_manifest=a,new_manifest=b,probes=probes)


def test_production_constants_and_pack_are_source_owned():
    assert r._RUNTIME_EPOCH=='m49' and r._MANIFEST_HASH==OLD_HASH
    assert r.LOW_MEMORY_MANIFEST_HASH==NEW_HASH and r.LOW_MEMORY_ENGINE_ID==NEW_ID
    pack=json.loads((Path(__file__).resolve().parents[1]/'model-packs/local-ocr-low-memory-v1.json').read_bytes())
    assert pack['engine_id']==NEW_ID and pack['install_manifest_sha256']==NEW_HASH
    assert pack['cloud_fallback'] is False and pack['limits']['resident_memory_bytes']==1073741824
    assert pack['parameters']=={'Rec.rec_batch_num':1,'Cls.cls_batch_num':1}


def test_paths_share_only_old_private_venv_and_keep_six_positional_constructor(pair):
    p=pair
    assert p.legacy.base==p.root/'ocr-m49' and p.low.base==p.root/'ocr-m49-low-memory-v1'
    assert p.low.python==p.legacy.python==p.root/'venvs/ocr-m49/bin/python'
    assert p.low.site==p.legacy.site and p.low.base_runtime==p.legacy.base_runtime
    old=r._RuntimePaths(p.legacy.root,p.legacy.base,p.legacy.python,p.legacy.site,p.legacy.base_runtime,p.legacy.profile)
    assert old==p.legacy and old.engine_hash is None
    assert p.low.engine_hash==p.new_hash


def test_default_status_and_profiles_do_not_mutate_global_selection(pair):
    p=pair;before=(r._RUNTIME_EPOCH,r._MANIFEST_HASH,r._ENGINE_ID)
    default=r.status();both=r.profiles();explicit=r.status(engine_hash=p.new_hash)
    assert default['ready'] and default['id']==r._ENGINE_ID and default['manifest_hash']==p.old_hash
    assert [x['id'] for x in both]==[r._ENGINE_ID,NEW_ID] and explicit==both[1]
    for value in both:
        assert value['ready'] and value['manifest_hash']==value['manifest']['manifest_hash']
        assert r._sha(r._canonical(value['manifest']))==value['summary_hash']
    assert before==(r._RUNTIME_EPOCH,r._MANIFEST_HASH,r._ENGINE_ID)


@pytest.mark.parametrize('hash_', ['0'*64,'m49-low-memory-v1','../ocr-m49',None])
def test_unrecognized_explicit_recognition_hash_never_resolves_or_runs(monkeypatch,hash_):
    monkeypatch.setattr(r,'local_runtime_root',lambda:pytest.fail('unknown hash cannot resolve a root'))
    with pytest.raises(HTTPException):r.recognize(b'png',expected_manifest_hash=hash_)


def test_legacy_m45_hash_does_not_change_default_m49(monkeypatch):
    monkeypatch.setattr(r,'local_runtime_root',lambda:pytest.fail('no legacy auto selection'))
    with pytest.raises(HTTPException):r.recognize(b'png',expected_manifest_hash=r._RUNTIME_PROFILES['m45']['manifest_hash'])
    assert r._RUNTIME_EPOCH=='m49'


def test_both_full_installations_use_their_manifest_and_same_private_pins(pair):
    p=pair
    a,ah=r._installation(_paths=p.legacy);b,bh=r._installation(_paths=p.low)
    assert (a,ah)==(p.old_manifest,p.old_hash) and (b,bh)==(p.new_manifest,p.new_hash)
    assert p.probes==[p.legacy,p.low]


@pytest.mark.parametrize('tamper',['manifest_swap','private_base','private_data','interpreter','config','site_link','base_link','sandbox_policy'])
def test_profile_binding_and_all_private_guards_fail_before_probe(pair,monkeypatch,tamper,tmp_path):
    p=pair;selected=p.low
    if tamper=='manifest_swap':(p.low.base/'manifest.json').write_bytes(r._canonical(p.old_manifest))
    elif tamper=='private_base':(p.low.base_runtime/'lib/bootstrap.py').write_bytes(b'changed')
    elif tamper=='private_data':(p.low.site/'resource.data').write_bytes(b'changed')
    elif tamper=='interpreter':
        other=tmp_path/'outside/bin/python';other.parent.mkdir(parents=True);other.write_bytes(b'explicit nonexecutable interpreter')
        selected=replace(p.low,python=other)
    elif tamper=='config':(p.low.base/'config.json').write_bytes(b'{}')
    elif tamper=='site_link':(p.low.site/'badlink').symlink_to(p.low.site,target_is_directory=True)
    elif tamper=='base_link':
        alias=p.root/'alias';alias.symlink_to(p.low.base,target_is_directory=True);selected=replace(p.low,base=alias)
    else:selected=replace(p.low,profile='(version 1)(allow default)')
    with pytest.raises(HTTPException):r._installation(_paths=selected)
    assert not p.probes


def test_explicit_profile_cannot_be_mixed_with_other_paths(pair):
    with pytest.raises(HTTPException):r._execute(b'png','recognize',engine_hash=pair.new_hash,_paths=pair.legacy)
    assert not pair.probes


def mock_completed_recognition(monkeypatch,pair):
    commands=[]
    class Completed:
        pid=123;returncode=0
        def poll(self):return 0
    def popen(command,**kwargs):
        commands.append(command)
        base=Path(command[6]).parent
        manifest=pair.new_manifest if base==pair.low.base else pair.old_manifest
        hashed=pair.new_hash if base==pair.low.base else pair.old_hash
        assert command[2] in (pair.legacy.profile,pair.low.profile)
        assert command[3]==str(pair.legacy.python) and command[4:6]==['-I','-B']
        request=json.loads(Path(command[-2]).read_bytes());content=Path(request['input']).read_bytes()
        regions=[{'id':'r1','text':'仅合成返回','box':[0,0,1,1],'score':0.5}]
        value={'regions':regions,'elapsed_ms':1,'peak_rss_bytes':1,'receipt':{'manifest_hash':hashed,'input_sha256':r._sha(content),'result_sha256':r._sha(r._canonical(regions))}}
        Path(command[-1]).write_bytes(r._canonical({'ok':True,'result':value}))
        return Completed()
    monkeypatch.setattr(r.subprocess,'Popen',popen)
    return commands


def test_interleaved_profile_operations_never_use_a_mutable_global_or_other_package(pair,monkeypatch):
    calls=mock_completed_recognition(monkeypatch,pair)
    before=(r._RUNTIME_EPOCH,r._MANIFEST_HASH,r._ENGINE_ID)
    low=r.recognize(b'png',expected_manifest_hash=pair.new_hash)
    old=r.recognize(b'png',expected_manifest_hash=pair.old_hash)
    low2=r.recognize(b'png',expected_manifest_hash=pair.new_hash)
    assert [Path(c[6]).parent for c in calls]==[pair.low.base,pair.legacy.base,pair.low.base]
    assert low['receipt']['manifest']['id']==low2['receipt']['manifest']['id']==NEW_ID
    assert old['receipt']['manifest']['id']==r._ENGINE_ID
    assert before==(r._RUNTIME_EPOCH,r._MANIFEST_HASH,r._ENGINE_ID)


def test_low_memory_guard_prevents_launch_before_any_inference(pair,monkeypatch):
    class Cancelled(Exception):pass
    def guard():raise Cancelled()
    with pytest.raises(Cancelled):r.recognize(b'png',expected_manifest_hash=pair.new_hash,guard=guard)


@pytest.mark.parametrize('hash_name',['old_hash','new_hash'])
def test_both_profiles_keep_original_rss_guard_and_kill_owned_process(pair,monkeypatch,hash_name):
    killed=[]
    class Running:
        pid=123456;returncode=None
        def poll(self):return self.returncode
        def wait(self):self.returncode=-9
    monkeypatch.setattr(r.subprocess,'Popen',lambda *args,**kwargs:Running())
    monkeypatch.setattr(r,'_resident_bytes',lambda process:1073741825)
    monkeypatch.setattr(r.os,'killpg',lambda pid,sig:killed.append((pid,sig)))
    with pytest.raises(HTTPException,match='内存超过上限'):
        r.recognize(b'png',expected_manifest_hash=getattr(pair,hash_name))
    assert len(killed)==1 and r._MAX_RESIDENT_BYTES==1073741824 and r._WALL_TIMEOUT_SECONDS==90


def test_render_keyword_captures_one_selected_snapshot_for_all_pages(pair,monkeypatch):
    resolutions=[];calls=[]
    monkeypatch.setattr(r,'local_runtime_root',lambda:resolutions.append(1) or pair.root)
    def execute(content,action,**kw):
        calls.append((action,kw))
        return {'page_count':2} if action=='pdf_info' else {'page':kw['page']}
    monkeypatch.setattr(r,'_execute',execute)
    assert r.render_pdf(b'pdf',expected_manifest_hash=pair.new_hash)==[{'page':1},{'page':2}]
    assert len(resolutions)==1 and len(calls)==3
    assert all(kw['_paths'] is calls[0][1]['_paths'] for _,kw in calls)
    assert calls[0][1]['_paths'].engine_hash==pair.new_hash


def test_mismatched_runtime_receipt_still_rejected(pair,monkeypatch):
    mock_completed_recognition(monkeypatch,pair)
    original=r.subprocess.Popen
    def wrong(command,**kw):
        result=original(command,**kw);path=Path(command[-1]);value=json.loads(path.read_bytes())
        value['result']['receipt']['manifest_hash']=pair.old_hash;path.write_bytes(r._canonical(value));return result
    monkeypatch.setattr(r.subprocess,'Popen',wrong)
    with pytest.raises(HTTPException,match='回执与本次原件不一致'):
        r.recognize(b'png',expected_manifest_hash=pair.new_hash)


def test_simultaneous_known_profiles_capture_independent_paths(pair,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    calls=mock_completed_recognition(monkeypatch,pair);original=r.subprocess.Popen;barrier=Barrier(2)
    def interleaved(command,**kw):
        barrier.wait(timeout=2)
        return original(command,**kw)
    monkeypatch.setattr(r.subprocess,'Popen',interleaved)
    before=(r._RUNTIME_EPOCH,r._MANIFEST_HASH,r._ENGINE_ID)
    with ThreadPoolExecutor(max_workers=2) as pool:
        old=pool.submit(r.recognize,b'old',expected_manifest_hash=pair.old_hash)
        low=pool.submit(r.recognize,b'new',expected_manifest_hash=pair.new_hash)
        assert old.result()['receipt']['manifest']['id']==r._ENGINE_ID
        assert low.result()['receipt']['manifest']['id']==NEW_ID
    assert {Path(c[6]).parent for c in calls}=={pair.legacy.base,pair.low.base}
    assert before==(r._RUNTIME_EPOCH,r._MANIFEST_HASH,r._ENGINE_ID)


def test_legacy_m45_default_paths_remain_explicit_while_new_profile_stays_private(monkeypatch,tmp_path):
    monkeypatch.setattr(r,'local_runtime_root',lambda:tmp_path)
    monkeypatch.setattr(r,'_RUNTIME_EPOCH','m45')
    monkeypatch.setattr(r,'_MANIFEST_HASH',r._RUNTIME_PROFILES['m45']['manifest_hash'])
    monkeypatch.setattr(r,'_ENGINE_ID','rapidocr-3.10.0-ppocrv6-small-cpu-m45')
    old=r._operation_paths();low=r._operation_paths(engine_hash=NEW_HASH)
    assert old.base==tmp_path/'ocr-m45' and old.python==tmp_path/'venvs/ocr-m45/bin/python'
    assert low.base==tmp_path/'ocr-m49-low-memory-v1' and low.python==tmp_path/'venvs/ocr-m49/bin/python'
    assert '(deny file-write*' in low.profile and r._RUNTIME_EPOCH=='m45'


def test_new_profile_retains_timeout_kill_path(pair,monkeypatch):
    killed=[]
    class Running:
        pid=123456;returncode=None
        def poll(self):return self.returncode
        def wait(self):self.returncode=-9
    assert r._WALL_TIMEOUT_SECONDS==90
    # Synthetic expired limit proves the ordinary timeout branch without waiting.
    monkeypatch.setattr(r,'_WALL_TIMEOUT_SECONDS',-1)
    monkeypatch.setattr(r.subprocess,'Popen',lambda *args,**kwargs:Running())
    monkeypatch.setattr(r,'_resident_bytes',lambda process:0)
    monkeypatch.setattr(r.os,'killpg',lambda pid,sig:killed.append((pid,sig)))
    with pytest.raises(HTTPException,match='处理超时'):
        r.recognize(b'png',expected_manifest_hash=pair.new_hash)
    assert len(killed)==1


def test_new_profile_keeps_two_mebibyte_result_limit(pair,monkeypatch):
    class Completed:
        pid=123;returncode=0
        def poll(self):return 0
    def oversized(command,**kw):
        Path(command[-1]).write_bytes(b' '*(2*1024*1024+1));return Completed()
    monkeypatch.setattr(r.subprocess,'Popen',oversized)
    with pytest.raises(HTTPException,match='返回结果超过上限'):
        r.recognize(b'png',expected_manifest_hash=pair.new_hash)
