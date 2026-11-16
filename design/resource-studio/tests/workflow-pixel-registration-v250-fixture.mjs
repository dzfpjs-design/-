// Synthetic registration transport. No model, registry DB or production proof.
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {pixelFixture,id,copy,sha,initializePixelControllers} from './pixel-change-v249-fixture.mjs';
import {pixelCanonical} from '../src/pixel-change-v249-protocol.mjs';
import {pixelRegistrationScope,pixelRegistrationPrefix} from '../src/workflow-pixel-registration-v250-protocol.mjs';
let Controller;
export async function initializePixelRegistration(){assert.ok(globalThis.window?.document&&globalThis.sessionStorage);await initializePixelControllers();Controller=(await import('../src/workflow-pixel-registration-v250-controller.mjs')).PixelRegistrationControllerV250;}
export async function registrationFixture(){
 const f=pixelFixture(),sample=await f.ready(),created=await sample.create();f.finish(created.id);sample.dispose();const runID=created.id,scope=pixelRegistrationScope(f.account,f.instanceId,runID),prefix=pixelRegistrationPrefix(scope),state={mode:'normal',found:true,records:new Map(),posts:[],calls:[],optionsGate:null,lookupGate:null,lookupStatus:null,resultEdit:null};
 const options=()=>{const row=f.state.runs.get(runID);return {schema:'workflow-pixel-registration-options-v250',instance_id:f.instanceId,reference_run_id:runID,reference_version:row.version,reference_result_hash:row.result.result_hash,can_register:f.state.identity.role==='admin'&&f.state.modelReady&&f.state.modelGeneration===row.model_trace.generation,reasons:f.state.modelReady?[]:['原本地模型当前尚未核对'],model_trace:copy(row.model_trace),threshold_text:row.request.threshold_text,runtime_hash:'f'.repeat(64)};};
 const result=body=>({schema:'workflow-pixel-registration-v250',request_id:body.request_id,body_hash:sha(pixelCanonical(body)),reference_run_id:runID,reference_result_hash:options().reference_result_hash,capability:{id:id(900),hash:'d'.repeat(64),version:'1.0.0',namespace:'local_pixel_change',name:'pixel_candidates_fixed_v250',kind:'specialist_model'},model_trace:copy(options().model_trace),threshold_text:options().threshold_text,runtime_hash:options().runtime_hash});
 const api=async(path,request={})=>{
  state.calls.push({path,method:request.method||'GET'});
  if(path===f.base+'/runs/'+runID+'/registration-options'){if(state.optionsGate)await state.optionsGate;return options();}
  if(path.startsWith(f.base+'/registrations/requests?')){if(state.lookupGate)await state.lookupGate;if(state.lookupStatus)throw Object.assign(Error('原登记尚不能核对'),{status:state.lookupStatus});const params=new URL(path,'https://registration.invalid').searchParams,nonce=params.get('request_id'),expected=params.get('expected_body_hash'),saved=state.records.get(nonce);if(saved)assert.equal(saved.body_hash,expected);return {schema:'workflow-pixel-registration-recovery-v250',found:!!saved&&state.found,state:saved&&state.found?'found':'unknown',request_id:nonce,expected_body_hash:expected,result:saved&&state.found?copy(saved):null};}
  if(path===f.base+'/runs/'+runID+'/register-business'&&request.method==='POST'){const p=JSON.parse(f.storage.getItem(prefix+'pending'));assert.equal(request.body,p.raw);assert.equal(f.storage.getItem(prefix+'original:'+p.body.request_id),JSON.stringify(p));state.posts.push({raw:request.body,body:copy(p.body)});if(state.mode==='unknown')throw Error('登记回复未知');let saved=state.records.get(p.body.request_id);if(!saved){saved=result(p.body);state.records.set(p.body.request_id,copy(saved));}if(state.mode==='lost')throw Error('登记已保存但回复丢失');return state.resultEdit?state.resultEdit(copy(saved)):copy(saved);}
  return f.api(path,request);
 };
 const make=extra=>new Controller({account:f.account,instanceId:f.instanceId,referenceRunId:runID,request:api,storage:f.storage,session:f.session,locks:f.locks,...extra});
 return {...f,registration:{state,scope,prefix,api,options,result,make},runID};
}
