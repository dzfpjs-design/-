// Root-only bridge of NEW owned empty-PG API captures. No legacy/private/raw
// directory is discovered or opened. An explicit directory grants this source
// test access only to the closed fe_UUID serializer-name allowlist below.
// Without configuration this module registers zero tests (not skipped proof).
import test from 'node:test';
import assert from 'node:assert/strict';
import {constants} from 'node:fs';
import {open,lstat,readdir} from 'node:fs/promises';
import {isAbsolute,join} from 'node:path';
import {
  same,fullUUID,fullScope,evidenceCommandHash,verifyEvidencePreview,
  verifyEvidenceDraft,verifyEvidenceReductionPreview,verifyEvidenceResumePreview,
  verifyEvidenceReceipt,verifyEvidenceReply,verifyEvidenceList,verifyEvidenceStatus,
} from '../src/report-evidence-reading-contract.mjs';

const directory=process.env.GA_EVIDENCE_DTO_DIR||process.env.GA_F11_DTO_CAPTURE_DIR;
const names=new Set(['account','preview','start-command','queued','completed-map',
  'reduce-preview','reduce-command','reduce-accepted','reduced','review-command',
  'review-reply','status','list','by-request','resume-preview','resume-command',
  'resume-reply','resume-completed','map-recovery-command','map-recovery-reply',
  'reduce-recovery-command','reduce-recovery-reply']);
const filePattern=/^(fe_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-([a-z][a-z0-9-]*)\.json$/;
let groupsPromise;
async function groups(){
 if(groupsPromise)return groupsPromise;
 groupsPromise=(async()=>{
  assert.ok(isAbsolute(directory),'Capture directory must be explicitly absolute');
  const stat=await lstat(directory);assert.ok(stat.isDirectory()&&!stat.isSymbolicLink(),'Capture directory must be an owned regular directory');
  if(process.getuid)assert.equal(stat.uid,process.getuid(),'Capture directory owner differs');
  const sets=new Map();
  for(const file of (await readdir(directory)).sort()){
   const match=filePattern.exec(file);if(!match||!names.has(match[2]))continue;
   const handle=await open(join(directory,file),constants.O_RDONLY|constants.O_NOFOLLOW);
   try{
    const before=await handle.stat();assert.ok(before.isFile()&&before.nlink===1&&before.size>0&&before.size<=8*1024*1024,'Capture file is not a bounded regular single-link JSON');
    assert.equal(before.mode&0o077,0,'Capture file must remain private to its owner');if(process.getuid)assert.equal(before.uid,process.getuid(),'Capture file owner differs');
    const bytes=await handle.readFile(),after=await handle.stat();assert.ok(before.dev===after.dev&&before.ino===after.ino&&before.size===after.size&&before.mtimeMs===after.mtimeMs&&bytes.length===before.size,'Capture changed during read');
    const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));if(!sets.has(match[1]))sets.set(match[1],{request_id:match[1]});sets.get(match[1])[match[2]]=value;
   }finally{await handle.close();}
  }
  assert.ok(sets.size>0,'No permitted new evidence API captures were found');
  for(const g of sets.values()){assert.ok(g.account&&g.preview&&g['start-command']&&g.queued,'Each fe group needs account, preview, start command and queued API reply');fullScope(g.account);assert.equal(g.preview.body.request_id,g.request_id);assert.equal(g['start-command'].request_id,g.request_id);}
  return [...sets.values()];
 })();return groupsPromise;
}
async function requiring(...fields){const values=(await groups()).filter(g=>fields.every(f=>g[f]!==undefined));assert.ok(values.length>0,'Required owned serializer chain missing: '+fields.join(', '));return values;}
async function entry(kind,body,draft,preview){
 const source=preview||draft.preview;
 return {schema:'report-evidence-reading-command-v1',scope:fullScope({user_id:source.actor_id,workspace_id:source.workspace_id}),kind,
  draft_id:kind==='extract'?null:draft.id,body,
  command_hash:await evidenceCommandHash({user_id:source.actor_id,workspace_id:source.workspace_id},kind,kind==='extract'?null:draft.id,body),
  binding:{material_id:source.body.selection.original_ref.id,source_hash:source.source_hash,preview_hash:body.preview_hash||source.preview_hash,
   map_results_hash:kind==='reduce'?draft.reduction.preview.reduction_preparation.map_results_hash:null,
   remaining_chunks:kind==='resume'?draft.resume_history.find(item=>item.receipt.request_id===body.request_id)?.receipt.remaining_chunks||[]:[]},
  receipt:null,rejection:null,resolution:null};
}

if(directory){
 test('owned API bridge: exact complete preview, queued start receipt and actor/source binding',async()=>{
  for(const g of await groups()){await verifyEvidencePreview(g.preview,g.account,g.preview.body);const e=await entry('extract',g['start-command'],g.queued.draft,g.preview);await verifyEvidenceReceipt(g.queued.receipt,g.account,e);const row=await verifyEvidenceReply(g.queued,g.account,e);assert.equal(row.id,g.queued.draft.id);assert.equal(row.status,'queued');assert.ok(same(row.preview,g.preview),'Queued preview differs from explicitly authorized preview');}
 });
 test('owned API bridge: completed fact groups expose original immutable journals and awaiting-merge state',async()=>{
  for(const g of await requiring('completed-map')){const row=await verifyEvidenceDraft(g['completed-map'],g.account);assert.equal(row.status,'awaiting_merge');assert.equal(row.phase,'awaiting_reduce');assert.ok(row.maps.every(s=>s.status==='succeeded'&&s.raw_response_saved&&s.journal.consumable&&s.result),'Every planned group needs a verifiable saved result');assert.equal(row.maps.length,g.preview.input_summary.map_call_count);}
 });
 test('owned API bridge: separate reduction preview and accepted command bind the full original claim input',async()=>{
  for(const g of await requiring('completed-map','reduce-preview','reduce-command','reduce-accepted')){const row=g['completed-map'],p=g['reduce-preview'];await verifyEvidenceReductionPreview(p,g.account,row);const {preview_hash,consent,...body}=g['reduce-command'];assert.ok(same(body,p.body),'Reduction body differs from displayed preview');assert.equal(preview_hash,p.preview_hash);assert.equal(consent,true);const e=await entry('reduce',g['reduce-command'],g['reduce-accepted'].draft);await verifyEvidenceReply(g['reduce-accepted'],g.account,e);assert.equal(p.reduction_preparation.call_count,1);}
 });
 test('owned API bridge: full V4 generated candidate and human save preserve original declarations and complete semantic fields',async()=>{
  for(const g of await requiring('reduced','review-command','review-reply')){const original=await verifyEvidenceDraft(g.reduced,g.account);assert.ok(original.model_result,'Generated chain needs a saved original V4 result');const e=await entry('review',g['review-command'],original),saved=await verifyEvidenceReply(g['review-reply'],g.account,e);assert.equal(saved.model_result.schema,'report-evidence-candidate-v4');assert.ok(same(saved.model_result,original.model_result),'Human save must not change original model candidate');assert.ok(same(saved.review_value,g['review-command'].value),'Human save differs from accepted full value');assert.equal(g['review-reply'].current_matches,true);assert.equal(saved.can_apply,false);assert.equal(saved.can_publish,false);}
 });
 test('owned API bridge: compact status, private list and original by-request receipt remain readable after human save',async()=>{
  for(const g of await requiring('review-reply','list','status','by-request')){const row=g['review-reply'].draft;await verifyEvidenceList(g.list,g.account);await verifyEvidenceStatus(g.status,g.account,row);const e=await entry('extract',g['start-command'],g.queued.draft,g.preview);const current=await verifyEvidenceReply(g['by-request'],g.account,e);assert.equal(current.id,row.id);assert.ok(current.revision>=g.queued.draft.revision);assert.equal(g.status.draft_id,current.id);assert.ok(g.list.items.some(item=>item.id===current.id),'Private list lost the accepted draft');}
 });
 test('owned API bridge: remaining-only continuation restores original saved stage and never changes original plan',async()=>{
  for(const g of await requiring('resume-preview','resume-command','resume-reply','resume-completed')){const p=g['resume-preview'];await verifyEvidenceResumePreview(p,g.account,g.queued.draft);const e=await entry('resume',g['resume-command'],g['resume-reply'].draft);await verifyEvidenceReply(g['resume-reply'],g.account,e);const final=await verifyEvidenceDraft(g['resume-completed'],g.account);assert.equal(final.status,'awaiting_merge');assert.ok(same(final.preview,g.preview),'Continuation changed original frozen source or plan');assert.equal(p.input_summary.call_count,p.remaining_chunks.length);assert.ok(same(g['resume-reply'].receipt.remaining_chunks,p.remaining_chunks.map(c=>c.chunk_id)),'Continuation receipt expanded the remaining call set');}
 });
 test('owned API bridge: free saved-map and saved-reduction recovery use UUID receipts and preserve the paid start',async()=>{
  for(const g of await requiring('map-recovery-command','map-recovery-reply','reduce-recovery-command','reduce-recovery-reply')){
   for(const prefix of ['map','reduce']){const command=g[prefix+'-recovery-command'],reply=g[prefix+'-recovery-reply'];assert.ok(fullUUID.test(command.request_id),'Free recovery requires a plain UUID');const e=await entry('recovery',command,reply.draft);const row=await verifyEvidenceReply(reply,g.account,e);assert.ok(same(row.start_receipt,g.queued.receipt),'Free recovery changed the original paid authorization');assert.ok(row.recovery_history.some(r=>r.receipt_hash===reply.receipt.receipt_hash),'Free recovery receipt absent from persistent history');}
  }
 });
}
