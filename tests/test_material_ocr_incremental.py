"""In-memory IO adapters exercise actual incremental OCR handlers; no runtime/DB.

These tests prove queue/checkpoint and compatibility contracts, not OCR quality.
"""
import copy
import hashlib
import io
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BindParameter, BooleanClauseList, BinaryExpression

from generic_agents import material_ocr as o
from generic_agents.contracts import digest
from generic_agents.models import Job, now

SCHEMA = 'material-ocr-preparation-v2'


def evaluate(expr, row):
    if isinstance(expr, BooleanClauseList):
        values = [evaluate(c, row) for c in expr.clauses]
        return all(values) if expr.operator is operators.and_ else any(values)
    if isinstance(expr, BinaryExpression):
        left = getattr(row, expr.left.key)
        right = expr.right.value if isinstance(expr.right, BindParameter) else expr.right
        if expr.operator is operators.eq: return left == right
        if expr.operator is operators.in_op: return left in right
        raise AssertionError(f'unsupported fake predicate {expr.operator}')
    raise AssertionError(type(expr))


class Memory:
    def __init__(self):
        self.rows = []; self.commits = []; self.info = {}; self.lease = True
    def add(self, row):
        if row not in self.rows: self.rows.append(row)
    def flush(self):
        for row in self.rows:
            if row.id is None: row.id = str(uuid4())
            if isinstance(row, Job):
                if row.available_at is None: row.available_at = now()
                if row.status is None: row.status = 'queued'
        keys = [(r.workspace_id, r.idempotency_key) for r in self.rows if isinstance(r, Job)]
        assert len(keys) == len(set(keys)), 'duplicate durable dispatch key'
    def scalars(self, stmt):
        entity = stmt.column_descriptions[0]['entity']
        rows = [r for r in self.rows if isinstance(r, entity)]
        rows = [r for r in rows if all(evaluate(c, r) for c in stmt._where_criteria)]
        return sorted(rows, key=lambda r: getattr(r, 'unit_index', 0))
    def scalar(self, stmt):
        if str(stmt).startswith('SELECT count('):
            table = stmt.get_final_froms()[0].name
            cls = {o.Unit.__tablename__: o.Unit, o.Version.__tablename__: o.Version}[table]
            return sum(isinstance(r, cls) and all(evaluate(c, r) for c in stmt._where_criteria) for r in self.rows)
        rows = self.scalars(stmt)
        assert len(rows) <= 1
        return rows[0] if rows else None
    def rollback(self):
        self.rows=copy.deepcopy(self.commits[-1]) if self.commits else []
    def commit(self):
        from generic_agents.leases import LeaseLost
        if not self.lease: raise LeaseLost('synthetic lost lease at commit')
        self.flush(); self.commits.append(copy.deepcopy(self.rows))


@pytest.fixture
def scene(monkeypatch):
    from generic_agents import material_ocr_runtime as runtime
    db = Memory(); p = SimpleNamespace(user_id=str(uuid4()), workspace_id=str(uuid4()), role='operator')
    content = b'explicit synthetic PDF bytes, never parsed or rendered by an OS process'
    m = o.SetupMaterial(id=str(uuid4()), workspace_id=p.workspace_id, evidence_id=str(uuid4()),
        name='synthetic.pdf', sha256=hashlib.sha256(content).hexdigest(), status='needs_input', archived=False, version=2, data={})
    descriptors = [{'index': i, 'kind': 'pdf_page', 'page': i, 'body_index': None} for i in range(1, 4)]
    manifest = {'engine': 'explicit-inmemory-adapter', 'network': False}
    installed = {'id': 'pure-only', 'ready': True, 'manifest': manifest, 'manifest_hash': digest(manifest), 'summary_hash': digest(manifest)}
    run = o.Run(id=str(uuid4()), workspace_id=p.workspace_id, material_id=m.id, generation=1, version=1, status='queued', data={
        'scope': {'workspace_id':p.workspace_id,'material_id':m.id,'evidence_id':m.evidence_id,'source_sha256':m.sha256,'name':m.name},
        'coverage': {'unit_count':3,'units':descriptors,'status':'needs_ocr'}, 'native_blocks': [], 'native_blocks_hash':digest([]),
        'engine': installed, 'prepared':0, 'epoch':1, 'created_at':now().isoformat(), 'error':None,
        'preparation':{'schema':SCHEMA,'batch_size':1}})
    m.data = {'ocr_run_id':run.id,'ocr_coverage':run.data['coverage'],'ocr_pending':True}; o.seal(run)
    db.add(m); db.add(run)
    state = {'renders':[], 'recognitions':0, 'authorized':True, 'render_hook':None}
    def scoped(db_, cls, id_, principal, lock=False):
        return next(r for r in db_.rows if isinstance(r, cls) and r.id == id_ and r.workspace_id == principal.workspace_id)
    monkeypatch.setattr(o, 'scoped', scoped)
    def actor(*args):
        if not state['authorized']: raise HTTPException(403, 'synthetic permission revoked')
        return 'pure-user'
    monkeypatch.setattr(o, 'current_actor', actor)
    monkeypatch.setattr(o, 'source', lambda db_, p_, mat, run_=None, **kw: content if mat.sha256 == hashlib.sha256(content).hexdigest() else o.fail())
    monkeypatch.setattr(o, 'original', lambda *args: content)
    monkeypatch.setattr(o, 'engine_status', lambda: copy.deepcopy(installed))
    monkeypatch.setattr(o, 'verify_coverage', lambda v: v)
    monkeypatch.setattr(o, 'checked_provenance', lambda *args: None)
    monkeypatch.setattr(o, 'audit', lambda *args: None)
    def checked_unit(db_, p_, mat, r, id_, *args, **kw):
        unit = o.checked_head(scoped(db_, o.Unit, id_, p_))
        assert unit.data['descriptor'] == descriptors[unit.unit_index-1]
        return unit
    monkeypatch.setattr(o, 'checked_unit', checked_unit)
    def guard(*args):
        def check():
            actor()
            if not db.lease:
                from generic_agents.leases import LeaseLost
                raise LeaseLost('synthetic lost lease')
        return check
    monkeypatch.setattr(o, 'runtime_guard', guard)
    image = Image.new('RGB',(2,2),'white'); out=io.BytesIO(); image.save(out,format='PNG'); png=out.getvalue()
    monkeypatch.setattr(runtime, 'pdf_info', lambda value, guard: {'page_count':3,'source_sha256':m.sha256})
    def render(value, *, page, guard):
        state['renders'].append(page)
        if state['render_hook']: state['render_hook']()
        guard()
        receipt={'input_sha256':m.sha256,'output_sha256':hashlib.sha256(png).hexdigest(), 'manifest_hash':installed['manifest_hash'],
                 'summary_hash':installed['summary_hash'],'manifest':manifest}
        return [{'page':page,'png':png,'width':2,'height':2,'receipt':receipt}]
    monkeypatch.setattr(runtime, 'render_pdf', render)
    monkeypatch.setattr(o, 'save_immutable', lambda db_,p_,value,*args: SimpleNamespace(id=str(uuid4()),sha256=hashlib.sha256(value).hexdigest()))
    def recognize(value, *, expected_manifest_hash, guard):
        guard(); state['recognitions']+=1
        regions=[{'id':'r1','text':'原字','box':[0,0,1,1],'score':0.5}]
        return {'regions':regions,'elapsed_ms':1,'receipt':{'input_sha256':hashlib.sha256(png).hexdigest(),
            'manifest_hash':installed['manifest_hash'],'summary_hash':installed['summary_hash'],'manifest':manifest,'result_sha256':digest(regions)}}
    monkeypatch.setattr(runtime, 'recognize', recognize)
    monkeypatch.setattr(o, 'evidence_bytes', lambda *args: (None,png))
    monkeypatch.setattr(o, 'reply', lambda db_, p_, mat, receipt: {'request':o.request_view(receipt),'status':run.status})
    monkeypatch.setattr(o, 'units_manifest', lambda db_,p_,mat,r,**kw: ([x for x in db.rows if isinstance(x,o.Unit)],'synthetic-manifest'))
    return SimpleNamespace(db=db,p=p,m=m,run=run,state=state,installed=installed)


def jobs(s, kind): return [r for r in s.db.rows if isinstance(r, Job) and r.kind==kind]
def units(s): return [r for r in s.db.rows if isinstance(r, o.Unit)]
def start_prepare(s):
    o._queue_prepare(s.db,s.p,s.m,s.run); o.seal(s.run)
    return jobs(s,'prepare_material_ocr')[-1]


def test_first_chunk_queues_unit_strictly_before_next_and_replay_has_no_effect(scene):
    s=scene; first=start_prepare(s); o.process_prepare(s.db,s.p,first)
    assert s.state['renders']==[1] and s.run.data['prepared']==1 and len(units(s))==1
    prep=jobs(s,'prepare_material_ocr'); unit=jobs(s,'recognize_material_ocr_unit')
    assert len(prep)==2 and len(unit)==1
    assert unit[0].available_at < prep[1].available_at
    assert unit[0].created_at is None or unit[0].created_at <= unit[0].available_at
    assert prep[0].data['target']['preparation_contract']==SCHEMA and prep[1].data['target']['cursor']==1
    snapshot=copy.deepcopy([(r.id,r.data) for r in s.db.rows]); o.process_prepare(s.db,s.p,first)
    assert s.state['renders']==[1] and snapshot==[(r.id,r.data) for r in s.db.rows]
    committed=s.db.commits[-1]
    assert sum(isinstance(x,o.Unit) for x in committed)==1
    assert sum(isinstance(x,Job) for x in committed)==3
    assert next(x for x in committed if isinstance(x,o.Run)).data['prepared']==1


def test_claim_order_interleaves_actual_unit_then_next_prepare_without_shrinking_total(scene):
    s=scene; first=start_prepare(s); o.process_prepare(s.db,s.p,first); first.status='succeeded'
    pending=sorted([j for j in s.db.rows if isinstance(j,Job) and j.status=='queued'],key=lambda j:j.available_at)
    assert pending[0].kind=='recognize_material_ocr_unit'
    o.process_unit(s.db,s.p,pending[0]); pending[0].status='succeeded'
    assert s.state['recognitions']==1 and units(s)[0].status=='review' and s.run.status=='running'
    o.process_prepare(s.db,s.p,pending[1])
    assert s.state['renders']==[1,2] and s.run.data['coverage']['unit_count']==3
    view=o.overview(s.db,s.p,s.m)
    assert view['run']['progress']['prepared']==2 and view['run']['progress']['total']==3


@pytest.mark.parametrize('policy',[None,{}, {'schema':SCHEMA,'batch_size':True}, {'schema':SCHEMA,'batch_size':2}, {'schema':'unknown','batch_size':1}, {'schema':SCHEMA,'batch_size':1,'extra':True}])
def test_invalid_explicit_policy_fails_closed_not_legacy(scene,policy):
    s=scene; s.run.data={**s.run.data,'preparation':policy}; o.seal(s.run)
    with pytest.raises(HTTPException): start_prepare(s)
    assert not s.state['renders'] and not jobs(s,'prepare_material_ocr')


def test_future_cursor_and_bool_cursor_reject_before_render(scene):
    s=scene; job=start_prepare(s)
    for value in [1, True, -1, 4]:
        target={**job.data['target'],'cursor':value,'end':min(value+1,3)}
        job.idempotency_key=f'ocr-prepare-v2:{s.run.id}:1:{value}:{target["end"]}'
        job.data={**job.data,'target':target,'target_hash':digest(target)}
        with pytest.raises(HTTPException): o.process_prepare(s.db,s.p,job)
    assert not s.state['renders'] and not units(s)


def test_same_epoch_unit_dispatch_reused_and_saved_raw_never_redispatched(scene):
    s=scene; first=start_prepare(s); o.process_prepare(s.db,s.p,first); u=units(s)[0]
    old=jobs(s,'recognize_material_ocr_unit')[0]; before=copy.deepcopy(u.data)
    o._queue_unit(s.db,s.p,s.m,s.run,u)
    assert jobs(s,'recognize_material_ocr_unit')==[old] and u.data==before
    o.process_unit(s.db,s.p,old); before=copy.deepcopy(u.data)
    o._queue_unit(s.db,s.p,s.m,s.run,u)
    assert len(jobs(s,'recognize_material_ocr_unit'))==1 and u.data==before


def test_cancel_during_render_discards_page_and_next_queue(scene):
    s=scene; first=start_prepare(s)
    def cancelled():
        s.run.status='cancelled';s.run.data={**s.run.data,'epoch':2};o.seal(s.run)
    s.state['render_hook']=cancelled
    o.process_prepare(s.db,s.p,first)
    assert not units(s) and len(jobs(s,'prepare_material_ocr'))==1 and s.run.data['prepared']==0


@pytest.mark.parametrize('change',['permission','lease','source','archive'])
def test_lost_authority_after_render_cannot_checkpoint_or_dispatch(scene,change):
    s=scene; first=start_prepare(s)
    def lost():
        if change=='permission':s.state['authorized']=False
        elif change=='lease':s.db.lease=False
        elif change=='source':s.m.sha256='0'*64
        else:s.m.archived=True
    s.state['render_hook']=lost
    try:o.process_prepare(s.db,s.p,first)
    except (HTTPException, __import__('generic_agents.leases',fromlist=['LeaseLost']).LeaseLost):pass
    assert not units(s) and not jobs(s,'recognize_material_ocr_unit') and s.run.data['prepared']==0


def test_old_run_without_policy_keeps_original_target_and_full_prepare(scene):
    s=scene;s.run.data={k:v for k,v in s.run.data.items() if k!='preparation'};o.seal(s.run)
    job=start_prepare(s); original_target=o._job_target(s.p,s.m,s.run)
    assert job.data['target']==original_target and job.idempotency_key==f'ocr-prepare:{s.run.id}:1'
    o.process_prepare(s.db,s.p,job)
    assert s.state['renders']==[1,2,3] and len(jobs(s,'prepare_material_ocr'))==1
    assert 'preparation' not in s.run.data


def body(value): return SimpleNamespace(model_dump=lambda **kw:copy.deepcopy(value))
def command(s, **extra):
    return {'request_id':str(uuid4()),'expected_material_version':s.m.version,'run_id':s.run.id,**extra}


def test_actual_save_confirm_between_chunks_keeps_cancel_and_retry_preserves_human_work(scene):
    s=scene; first=start_prepare(s); o.process_prepare(s.db,s.p,first)
    rawjob=jobs(s,'recognize_material_ocr_unit')[0]; o.process_unit(s.db,s.p,rawjob)
    u=units(s)[0]; raw=next(x for x in s.db.rows if isinstance(x,o.Version))
    save=command(s, expected_unit_version=u.version,parent_hash=raw.content_hash,reason='逐页对照原图',
        regions=[{'id':'r1','text':'核对后末字🙂','box':[0,0,1,1],'source_region_id':'r1'}])
    o.execute(s.db,s.p,s.m.id,'save',body(save),u.id)
    saved=next(x for x in s.db.rows if isinstance(x,o.Version) and x.kind=='edited')
    assert s.run.status=='running' and s.run.data['prepared']==1
    assert o.overview(s.db,s.p,s.m)['permissions']['cancel']['allowed'] is True
    confirm=command(s,expected_unit_version=u.version,revision_hash=saved.content_hash,reason='本页已核')
    o.execute(s.db,s.p,s.m.id,'unit_confirm',body(confirm),u.id)
    assert u.status=='confirmed' and s.run.status=='running'
    versions=copy.deepcopy([(x.id,x.data,x.content_hash) for x in s.db.rows if isinstance(x,o.Version)])
    cancel=command(s,expected_run_version=s.run.version)
    o.execute(s.db,s.p,s.m.id,'cancel',body(cancel))
    assert s.run.status=='cancelled' and u.status=='confirmed'
    nextold=jobs(s,'prepare_material_ocr')[-1]; o.process_prepare(s.db,s.p,nextold)
    assert s.state['renders']==[1] and len(units(s))==1
    # Original save nonce returns its saved result without CAS or another version.
    reply=o.execute(s.db,s.p,s.m.id,'save',body(save),u.id)
    assert reply['request']['result']['revision_hash']==saved.content_hash
    retry=command(s,expected_run_version=s.run.version)
    o.execute(s.db,s.p,s.m.id,'retry',body(retry))
    assert s.run.data['epoch']==3 and s.run.data['prepared']==1
    newprep=jobs(s,'prepare_material_ocr')[-1]
    assert newprep.data['target']['cursor']==1 and newprep.data['target']['epoch']==3
    o.process_prepare(s.db,s.p,newprep)
    assert s.state['renders']==[1,2] and s.state['recognitions']==1
    assert versions==[(x.id,x.data,x.content_hash) for x in s.db.rows if isinstance(x,o.Version)]
    assert u.status=='confirmed' and len(jobs(s,'recognize_material_ocr_unit'))==2


def test_retry_missing_raw_reuses_image_with_new_epoch_job_before_next_prepare(scene):
    s=scene; first=start_prepare(s);o.process_prepare(s.db,s.p,first);u=units(s)[0]
    image=(u.evidence_id,u.data['image_sha256'],copy.deepcopy(u.data['render']))
    o.execute(s.db,s.p,s.m.id,'cancel',body(command(s,expected_run_version=s.run.version)))
    assert u.status=='cancelled'
    o.execute(s.db,s.p,s.m.id,'retry',body(command(s,expected_run_version=s.run.version)))
    newunit=jobs(s,'recognize_material_ocr_unit')[-1];newprep=jobs(s,'prepare_material_ocr')[-1]
    assert newunit.available_at<newprep.available_at and newunit.data['target']['epoch']==3
    assert image==(u.evidence_id,u.data['image_sha256'],u.data['render']) and s.state['renders']==[1]
    o.process_unit(s.db,s.p,newunit)
    assert s.state['recognitions']==1 and s.run.status=='queued'  # preparation queued, still cancellable
    o.process_prepare(s.db,s.p,newprep)
    assert s.state['renders']==[1,2]


def test_actual_document_confirm_cannot_confirm_only_prepared_prefix(scene):
    s=scene;start_prepare(s)
    with pytest.raises(HTTPException):
        o.execute(s.db,s.p,s.m.id,'document_confirm',body(command(s,expected_run_version=s.run.version,
            units_manifest_hash='synthetic-manifest',reason='cannot approve unavailable pages')))
    assert not any(isinstance(x,o.Confirmation) for x in s.db.rows)
    assert s.m.data['ocr_pending'] is True and s.run.data['coverage']['unit_count']==3


def test_start_creates_explicit_new_policy_without_rewriting_existing_run(scene,monkeypatch):
    s=scene;old=copy.deepcopy(s.run.data)
    s.m.data={'ocr_coverage':s.run.data['coverage']};s.db.rows.remove(s.run)
    monkeypatch.setattr(o,'material_coverage',lambda *args:s.run.data['coverage'])
    monkeypatch.setattr(o,'parse_document',lambda *args:{'blocks':[]})
    value={'request_id':str(uuid4()),'expected_material_version':s.m.version,
           'engine_id':s.installed['id'],'engine_manifest_hash':s.installed['manifest_hash']}
    o.execute(s.db,s.p,s.m.id,'start',body(value))
    new=next(x for x in s.db.rows if isinstance(x,o.Run))
    assert new.data['preparation']=={'schema':SCHEMA,'batch_size':1}
    assert s.run.data==old and new.data['coverage']['unit_count']==3
    receipt_count=sum(isinstance(x,o.Request) for x in s.db.rows)
    o.execute(s.db,s.p,s.m.id,'start',body(value))
    assert len(jobs(s,'prepare_material_ocr'))==1 and sum(isinstance(x,o.Request) for x in s.db.rows)==receipt_count


@pytest.mark.parametrize('crash_point',['after_unit_flush','after_next_job_flush','after_page_commit'])
def test_checkpoint_crashes_do_not_duplicate_rendered_page_dispatch_or_cursor(scene,monkeypatch,crash_point):
    from generic_agents import material_ocr_preparation as prep
    s=scene;first=start_prepare(s);original_commit=s.db.commit; original_flush=s.db.flush
    class Crash(Exception): pass
    tripped=[False]
    def flush():
        original_flush()
        if tripped[0]:return
        oneunit=len(units(s))==1
        hit=(crash_point=='after_unit_flush' and oneunit and not jobs(s,'recognize_material_ocr_unit'))
        hit=hit or (crash_point=='after_next_job_flush' and len(jobs(s,'prepare_material_ocr'))==2)
        if hit:tripped[0]=True;raise Crash()
    def commit():
        original_commit()
        if crash_point=='after_page_commit' and not tripped[0] and len(units(s))==1:
            tripped[0]=True;raise Crash()
    monkeypatch.setattr(s.db,'flush',flush);monkeypatch.setattr(s.db,'commit',commit)
    with pytest.raises(Crash):o.process_prepare(s.db,s.p,first)
    s.db.rollback();s.run=next(x for x in s.db.rows if isinstance(x,o.Run));s.m=next(x for x in s.db.rows if isinstance(x,o.SetupMaterial))
    durable_units=len(units(s));before=len(s.state['renders'])
    o.process_prepare(s.db,s.p,first)
    assert len(units(s))==1 and s.run.data['prepared']==1
    assert len(jobs(s,'prepare_material_ocr'))==2 and len(jobs(s,'recognize_material_ocr_unit'))==1
    assert len(s.state['renders'])==before+(0 if durable_units else 1)
    assert s.state['recognitions']==0


def test_retry_all_prepared_uses_zero_render_finalizer_and_does_not_rerun_raw(scene):
    s=scene;job=start_prepare(s)
    for page in range(1,4):
        o.process_prepare(s.db,s.p,job)
        uj=jobs(s,'recognize_material_ocr_unit')[-1];o.process_unit(s.db,s.p,uj)
        if page<3:job=jobs(s,'prepare_material_ocr')[-1]
    assert s.run.status=='review' and s.run.data['prepared']==3 and s.state['recognitions']==3
    # Explicit failure without dropping saved raw, as a recoverable old Job-tail error.
    s.run.status='failed';s.run.version+=1;o.seal(s.run)
    o.execute(s.db,s.p,s.m.id,'retry',body(command(s,expected_run_version=s.run.version)))
    finalizer=jobs(s,'prepare_material_ocr')[-1]
    assert finalizer.data['target']['cursor']==3 and finalizer.data['target']['end']==3
    o.process_prepare(s.db,s.p,finalizer)
    snap=copy.deepcopy(s.run.data);version=s.run.version
    o.process_prepare(s.db,s.p,finalizer)
    assert s.run.status=='review' and s.run.version==version and s.run.data==snap
    assert s.state['renders']==[1,2,3] and s.state['recognitions']==3


def test_mismatched_existing_unit_job_fails_closed_without_reassigning(scene):
    s=scene;first=start_prepare(s);o.process_prepare(s.db,s.p,first);u=units(s)[0]
    job=jobs(s,'recognize_material_ocr_unit')[0]
    job.data={**job.data,'target_hash':'0'*64}
    before=copy.deepcopy(u.data)
    with pytest.raises(HTTPException):o._queue_unit(s.db,s.p,s.m,s.run,u)
    assert before==u.data and len(jobs(s,'recognize_material_ocr_unit'))==1


def test_raw_unit_target_stays_original_contract_without_prepare_cursor(scene):
    s=scene
    # A v2 run still has the same unit target, without prepare cursor fields.
    o.process_prepare(s.db,s.p,start_prepare(s));u=units(s)[0]
    target=jobs(s,'recognize_material_ocr_unit')[0].data['target']
    assert target==o._job_target(s.p,s.m,s.run,u)
    assert not {'cursor','end','preparation_contract'} & set(target)


@pytest.mark.parametrize('field,value',[('prepared',True),('prepared',-1),('prepared',4),('prepared','0')])
def test_corrupt_persisted_v2_cursor_rejects_before_dispatch(scene,field,value):
    s=scene;s.run.data={**s.run.data,field:value};o.seal(s.run)
    with pytest.raises(HTTPException):start_prepare(s)
    assert not jobs(s,'prepare_material_ocr')
