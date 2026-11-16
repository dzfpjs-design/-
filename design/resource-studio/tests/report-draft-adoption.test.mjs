import {savedConfigurationWire} from '../src/configuration-numeric-wire.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {transformWithEsbuild} from 'vite';
import {adoptSavedDraft,readDraftRecord} from '../src/draft-cache-contract.mjs';

// Parse the actual component after JSX lowering. No copied adoption logic is
// tested: offsets from the AST select its current nested async function.
const requireFromVite=createRequire(import.meta.resolve('vite'));
const {parseAst}=await import(pathToFileURL(requireFromVite.resolve('rollup/parseAst')).href);
const source=await readFile(new URL('../src/RuntimeSetup.jsx',import.meta.url),'utf8');
const lowered=await transformWithEsbuild(source,'RuntimeSetup.jsx',{loader:'jsx',jsx:'transform'});
const ast=parseAst(lowered.code),matches=[];
function visit(value){
 if(!value||typeof value!=='object')return;
 if(value.type==='FunctionDeclaration'&&value.id?.name==='adoptServerDraft')matches.push(value);
 for(const child of Object.values(value))if(Array.isArray(child))child.forEach(visit);else if(child&&typeof child==='object')visit(child);
}
visit(ast);assert.equal(matches.length,1,'one shipping adoptServerDraft function must be found');
const actualFunction=lowered.code.slice(matches[0].start,matches[0].end);
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
const storage=()=>{const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};};

function fixture(){
 const cache=storage(),cacheKey='ga-setup:synthetic-actor:synthetic-workspace';
 const original={note:'应用前的人工稿'},human={note:'缓存核对期间完整保存的新人稿'};
 const saved={revision:8,configuration:{note:'较早的应用回执'}};
 const prior={generation:3,revision:7,busy:false,next:null,error:null,wait:async()=>{}};
 const sync={current:prior},live={current:original},setWCalls=[],constructors=[];
 const cacheCallbackDone=deferred(),resumeCachePromise=deferred();
 // The callback has released the cache lock, but its request promise has not
 // resumed the component yet. A completed human save can occur in this gap.
 const locks={request:async(_name,_options,operation)=>{
  const result=await operation();cacheCallbackDone.resolve();await resumeCachePromise.promise;return result;
 }};
 class ReplacementSync{constructor(...args){constructors.push(args);}}
 const bindings={accountRef:{current:{workspace_id:'synthetic-workspace',username:'synthetic-actor'}},sync,key:{current:cacheKey},
  adoptSavedDraft:(...args)=>adoptSavedDraft(...args,locks),localStorage:cache,DraftSync:ReplacementSync,
  savedConfigurationWire,setSave:()=>{},setW:value=>setWCalls.push(value),live,setDraftView:()=>{},checkReviewView:value=>value};
 const adopt=new Function(...Object.keys(bindings),'return ('+actualFunction+');')(...Object.values(bindings));
 return {adopt,cache,cacheKey,prior,sync,live,human,saved,setWCalls,constructors,cacheCallbackDone,resumeCachePromise};
}

for(const changed of ['generation-and-revision','generation-only','revision-only']){
 test('actual adoption preserves a completed human draft during cache promise gap: '+changed,async()=>{
  const state=fixture(),result=state.adopt(state.saved);
  await state.cacheCallbackDone.promise;
  assert.equal(readDraftRecord(state.cache,state.cacheKey).revision,8,'cache callback ran before the interleaving');
  if(changed!=='revision-only')state.prior.generation++;
  if(changed!=='generation-only')state.prior.revision=9;
  // All old busy/next/error guards are clear: this is a completed save, not a
  // still-pending edit that the old function already rejected.
  assert.equal(state.prior.busy,false);assert.equal(state.prior.next,null);assert.equal(state.prior.error,null);
  state.cache.setItem(state.cacheKey,JSON.stringify({revision:state.prior.revision,pending:false,configuration:state.human}));
  state.live.current=state.human;
  state.resumeCachePromise.resolve();
  await assert.rejects(result,/人稿|草稿|输入|保留/);
  assert.equal(state.sync.current,state.prior,'the active writer must not be replaced');
  assert.equal(state.constructors.length,0,'no replacement DraftSync is constructed');
  assert.deepEqual(state.setWCalls,[],'no old snapshot is rendered');
  assert.equal(state.live.current,state.human,'the live human draft is preserved');
  assert.deepEqual(readDraftRecord(state.cache,state.cacheKey).configuration,state.human,'the durable new draft is preserved');
 });
}

test('actual adoption accepts an unchanged writer and updates cache, view and live configuration',async()=>{
 const state=fixture(),result=state.adopt(state.saved);
 await state.cacheCallbackDone.promise;state.resumeCachePromise.resolve();
 assert.deepEqual(await result,{preservedPending:false});
 assert.notEqual(state.sync.current,state.prior);
 assert.equal(state.constructors.length,1);
 assert.equal(state.constructors[0][2],state.saved.revision);
 assert.deepEqual(state.setWCalls,[state.saved.configuration]);
 assert.equal(state.live.current,state.saved.configuration);
 assert.deepEqual(readDraftRecord(state.cache,state.cacheKey),{revision:8,pending:false,configuration:state.saved.configuration});
});
