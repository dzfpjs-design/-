// Root supplies actual HTTP captures. No fixture, HTTP or credential fallback.
// Output contains only decoder kinds/counts, never raw commands or identities.
import assert from 'node:assert/strict';
import {readFile,mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {webcrypto} from 'node:crypto';
import {build} from 'vite';

const path=process.argv[2];assert.ok(path,'Pass one explicit actual capture JSON path');
const capture=JSON.parse(await readFile(path,'utf8'));
assert.deepEqual(Object.keys(capture).sort(),['account','checks','schema']);
assert.equal(capture.schema,'audio-source-ui-dto-capture-v255');
assert.deepEqual(Object.keys(capture.account).sort(),['user_id','workspace_id']);
assert.ok(Array.isArray(capture.checks)&&capture.checks.length>0,'No checks supplied; nothing verified');
const memory=new Map();globalThis.sessionStorage={getItem:key=>memory.get(key)??null,setItem:(key,value)=>memory.set(key,String(value)),removeItem:key=>memory.delete(key)};
if(!globalThis.crypto)Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});
const project=resolve(fileURLToPath(new URL('..',import.meta.url))),require=createRequire(join(project,'package.json')),reactPath=require.resolve('react'),reactURL=pathToFileURL(reactPath).href,folder=await mkdtemp(join(tmpdir(),'actual-audio-v255-'));
try{
 const entry=join(folder,'entry.tsx');await writeFile(entry,'export * from '+JSON.stringify(join(project,'src/AudioSourcePanel.tsx'))+';');
 const result=await build({root:project,configFile:false,envDir:folder,cacheDir:join(folder,'cache'),logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:name=>['react',reactPath,reactURL].includes(name),output:{format:'es',inlineDynamicImports:true,paths:name=>['react',reactPath,reactURL].includes(name)?reactURL:name}}}}),chunk=result.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(chunk);
 const output=join(folder,'decoders.mjs');await writeFile(output,chunk.code);const code=await import(pathToFileURL(output).href),counts={};
 const ordinary={catalog:'checkedAudioCatalog',source_options:'checkedAudioSourceOptions',runtime_options:'checkedAudioStreamOptions',stream_list:'checkedAudioStreamList',stream:'checkedAudioStreamView',signal:'checkedAudioStreamSignal'};
 const receipts={source_receipt:'checkedAudioSourceReceipt',model_receipt:'checkedAudioModelReceipt',request_receipt:'checkedAudioStreamReceipt',source_recovery:'checkedAudioSourceRecovery',model_lookup:'checkedAudioModelLookup',request_recovery:'checkedAudioRequestRecovery',delivery_receipt:'checkedAudioDeliveryReceipt',delivery_lookup:'checkedAudioDeliveryLookup'};
 for(const item of capture.checks){
  assert.equal(item.scope.actor_id,capture.account.user_id);assert.equal(item.scope.workspace_id,capture.account.workspace_id);assert.ok(typeof item.kind==='string');
  if(ordinary[item.kind])await code[ordinary[item.kind]](item.value,item.scope);
  else if(receipts[item.kind]){const original=await code.checkedAudioOriginal(item.original,item.scope);await code[receipts[item.kind]](item.value,original);}
  else if(item.kind==='captures')code.checkedAudioCaptureList(item.value,item.scope,item.stream_id);
  else if(item.kind==='capture')await code.checkedAudioCapture(item.value,item.scope,item.stream_id,item.observation_id);
  else if(item.kind==='delivery')code.checkedAudioDelivery(item.value,item.scope,item.stream_id);
  else if(item.kind==='delivery_options')code.checkedAudioDeliveryOptions(item.value,item.scope,item.stream_id);
  else if(item.kind==='original')await code.checkedAudioOriginal(item.value,item.scope);
  else throw Error('Unsupported actual capture kind');
  counts[item.kind]=(counts[item.kind]||0)+1;
 }
 process.stdout.write(JSON.stringify({checks:capture.checks.length,kinds:counts,verified:true})+'\n');
}finally{await rm(folder,{recursive:true,force:true});}
