import {test} from 'node:test';
import assert from 'node:assert/strict';
global.sessionStorage={getItem(){return null;},setItem(){},removeItem(){}};
const {DraftSync}=await import('../src/runtime-api.js');
const {saveFlow}=await import('../src/setup-schema.js');
const storage=()=>{const values=new Map();return {setItem(k,v){values.set(k,v);},getItem(k){return values.get(k);}};};
const tails=new Map(),locks={request(key,_options,operation){const result=(tails.get(key)||Promise.resolve()).then(operation);tails.set(key,result.catch(()=>{}));return result;}};
const response=obj=>new Response(JSON.stringify(obj),{status:200,headers:{'content-type':'application/json'}});

for(const when of ['in flight','after acknowledgement'])test(`candidate edit ${when} keeps latest human input`,async()=>{
 const s=storage(),requests=[],states=[];let release;
 const first={version:1,businesses:['巡护'],flows:{巡护:{candidateKey:'patrol',steps:[{id:'step-1',candidateKey:'observe',name:'检查',purpose:'原目的'}]}}};
 const second=structuredClone(first);second.flows.巡护.steps[0].purpose='用户继续编辑的内容';
 global.fetch=async(_url,options)=>{const input=JSON.parse(options.body);requests.push(input);
  if(requests.length===1)await new Promise(r=>{release=r;});
  const value=structuredClone(input.configuration);value.flows.巡护.candidateBasisHash='server-flow';value.flows.巡护.steps[0].candidateBasisHash='server-step';
  return response({revision:input.revision+1,configuration:value});};
 const sync=new DraftSync(s,'candidate-draft',4,v=>states.push(v),{locks});sync.save(first);
 await new Promise(r=>setTimeout(r,0));
 if(when==='in flight')sync.save(second);
 release();await sync.wait();
 if(when==='after acknowledgement'){sync.save(second);await sync.wait();}
 assert.deepEqual(requests.map(x=>x.revision),[4,5]);
 assert.equal(requests[1].configuration.flows.巡护.steps[0].purpose,'用户继续编辑的内容');
 assert.equal(requests[1].configuration.flows.巡护.steps[0].candidateBasisHash,undefined);
 assert.equal(JSON.parse(s.getItem('candidate-draft')).configuration.flows.巡护.steps[0].purpose,'用户继续编辑的内容');
 assert.equal(states.at(-1).state,'saved');
});

test('normal continuous edits do not restore the prior suggestion as current evidence',()=>{
 const original={candidateKey:'patrol',trigger:'按计划',output:'记录',steps:[{id:'step-1',candidateKey:'observe',name:'检查',purpose:'原目的',agent:'业务智能体',output:'记录',sourceId:'m',citation:'原文',citations:[{material_id:'m',block_id:'b1',quote:'原文'}],inferenceReason:'旧建议',condition:'',nextId:'auto',elseId:'end'}]};
 let w={instanceProfile:{},materials:[{id:'m',name:'资料',hash:'hash',archived:false}],businesses:['巡护'],flows:{巡护:original}};
 let step={...w.flows.巡护.steps[0],purpose:'人工更正目的'};
 w=saveFlow(w,'巡护',{...w.flows.巡护,steps:[step]});
 assert.deepEqual(w.flows.巡护.steps[0].citations,[]);assert.equal(w.flows.巡护.steps[0].inferenceReason,'');
 step={...w.flows.巡护.steps[0],output:'人工更正产出'};
 w=saveFlow(w,'巡护',{...w.flows.巡护,steps:[step]});
 assert.deepEqual(w.flows.巡护.steps[0].citations,[]);assert.equal(w.flows.巡护.steps[0].inferenceReason,'');
 assert.ok(w.flows.巡护.steps[0].basisHistory.length);
});
