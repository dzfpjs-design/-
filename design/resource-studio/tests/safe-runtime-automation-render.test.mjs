import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToString} from 'react-dom/server';
import {build} from 'vite';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
import react from '@vitejs/plugin-react';
import {defaults} from '../src/automation-schema.js';
import {initialWorkspace} from '../src/catalog.js';

test('real SSR runtime automation shows safe choices beside the original trigger and local retry does not imply fresh fees',async(t)=>{
 const priorLocation=globalThis.location,priorStorage=globalThis.sessionStorage;
 globalThis.location={search:'?view=automation'};globalThis.sessionStorage={getItem:()=>null,setItem(){},removeItem(){}};
 await mkdir(resolve('.m62-validation'),{recursive:true});const dir=await mkdtemp(resolve('.m62-validation/.safe-runtime-render-'));
 async function component(name){
  const output=await build({configFile:false,plugins:[react()],logLevel:'silent',build:{ssr:resolve('src/'+name+'.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true}}}});
  const file=resolve(dir,name+'.mjs');await writeFile(file,output.output.find(item=>item.type==='chunk'&&item.isEntry).code);return (await import(pathToFileURL(file).href)).default;
 }
 const Btn=({children,disabled})=>React.createElement('button',{disabled},children),Field=({children,label})=>React.createElement('label',{},label,children),Dialog=({children,title})=>React.createElement('section',{},title,children),Icon=()=>React.createElement('span',{});
 try{
  const Automation=await component('AutomationStudio');
  const workspace={...initialWorkspace(),businesses:['库位资料核查'],flows:{'库位资料核查':{trigger:'只由人工启动，不按计划或异常自动启动',output:'登记核查',steps:[]}},automation:undefined};
  const props={workspace,Icon,Btn,Field,Dialog,runtime:true,onBack(){},onNext(){},onCommitWorkspace(){return true;},onRegisterGuard(){}};
  await t.test('new runtime policies cannot add schedule or anomaly startup',()=>{
  const html=renderToString(React.createElement(Automation,props));
  const checks=new Map([...html.matchAll(/<label><input([^>]*)\/>((?:按计划运行|收到新数据|发现异常|人工启动))<\/label>/g)].map(match=>[match[2],match[1].includes('checked=""')]));
  assert.equal(checks.size,4);assert.equal(checks.get('人工启动'),true);for(const label of ['按计划运行','收到新数据','发现异常'])assert.equal(checks.get(label),false);
  assert.match(html,/当前业务流程的启动说明/);assert.match(html,/只由人工启动，不按计划或异常自动启动/);
  const stored=defaults(['库位资料核查']);stored.policies['库位资料核查'].triggers=['人工发起'];
  const saved=renderToString(React.createElement(Automation,{...props,workspace:{...workspace,automation:stored}}));assert.match(saved,/<input type="checkbox" checked=""\/>人工启动/);assert.doesNotMatch(saved,/<input type="checkbox" checked=""\/>按计划运行/);
  });
  await t.test('real fourth page renders current backend review, viewer restrictions and pending recovery outside edit lock',()=>{
   const account={workspace_id:'w',username:'reader',role:'viewer'};
   const reviewView={revision:8,configuration:workspace,automation_review:{version:1,businesses:[{business_id:'business_0123456789abcdef',business_name:'库位资料核查',status:'missing',reason:'当前尚未保存自动化策略，请先保存规则。',flow_hash:'a'.repeat(64),policy_hash:null}]}};
   const reviewProps={account,reviewView,prepareReview(){},onReviewAdopt(){},onReviewPending(){},onReviewBusy(){},onReviewReload(){}};
   const missing=renderToString(React.createElement(Automation,{...props,...reviewProps}));
   assert.match(missing,/<fieldset class="automation-edit-fields" disabled=""/);assert.match(missing,/尚未保存此业务规则/);assert.match(missing,/请先保存规则/);assert.match(missing,/当前账号只读/);assert.doesNotMatch(missing,/当前流程与规则已确认/);
   const journal={body:{revision:7,business_id:'business_0123456789abcdef',request_id:'11111111-2222-4333-8444-555555555555'},business_name:'库位资料核查'};
   const latest={...reviewView,revision:10,automation_review:{version:1,businesses:[{...reviewView.automation_review.businesses[0],status:'needs_review',policy_hash:'b'.repeat(64),reason:'原确认之后流程已变化'}]},review_receipt:{...journal.body,saved_revision:8,replayed:true}};
   const recovery=renderToString(React.createElement(Automation,{...props,...reviewProps,account:{...account,role:'operator'},reviewPending:journal,reviewView:latest}));
   assert.match(recovery,/流程或规则已变化，待确认/);assert.doesNotMatch(recovery,/当前流程与规则已确认/);assert.match(recovery,/<button>核对上次自动化确认<\/button>/);assert.match(recovery,/<\/fieldset><section class="automation-review-panel"/);assert.doesNotMatch(recovery,/重试原确认请求/);
   const removed=renderToString(React.createElement(Automation,{...props,...reviewProps,workspace:{...workspace,businesses:[]},reviewPending:journal}));assert.match(removed,/核对上次自动化确认/);
  });
  await t.test('real fourth page blocks malformed raw automation with a human recovery message',()=>{
   for(const automation of [{policies:null},{version:1,policies:{},equipment:{devices:{d:null}}},{version:1,policies:{},equipment:{types:{camera:{caps:'bad'}}}},{version:1,policies:{'库位资料核查':{triggers:null,rules:{}}}},{version:1,policies:{'库位资料核查':{rules:{write:{mode:'自动运行',scope:null}}}}},{version:1,policies:{'库位资料核查':{rules:{write:{mode:'自动运行',scope:3}}}}}]){
    const original=JSON.stringify(automation),html=renderToString(React.createElement(Automation,{...props,account:{workspace_id:'w',username:'human',role:'operator'},workspace:{...workspace,automation}}));
    assert.match(html,/已保存的自动化规则格式无法核对/);assert.match(html,/原业务、规则和本页输入保留/);assert.match(html,/<fieldset class="automation-edit-fields" disabled=""/);assert.doesNotMatch(html,/Cannot read|undefined.*map/);assert.equal(JSON.stringify(automation),original);
   }
  });
  const Understanding=await component('UnderstandingDialog');
  await t.test('paid local progress cannot claim another cloud call or fees',()=>{
  const base={id:'local-row',contract_version:3,provider:'qwen',model:'text',source_revision:1,total_batches:1,completed_batches:0,materials:[],result:null,recovery:{mode:'local_only',reason:'仅在本地核对原答，不再HTTP'}};
  const up={Dialog,Btn,Field,materials:[],workspace:{instanceProfile:{domain:'核查'}},account:{workspace_id:'w',username:'p',role:'operator'},onClose(){},onApplied(){},prepareDraft(){}};
  for(const status of ['queued','running']){const local=renderToString(React.createElement(Understanding,{...up,initialRecord:{...base,status}}));assert.match(local,/本地核对已保存原答/);assert.match(local,/不新增云调用费用/);assert.match(local,/停止本地核对/);assert.doesNotMatch(local,/已发送的请求可能仍计费|已发送请求可能仍计费/);}
  const original=renderToString(React.createElement(Understanding,{...up,initialRecord:{...base,status:'running',recovery:{mode:'new_preview_required',reason:'原答尚未保存'}}}));assert.match(original,/已发送的请求可能仍计费/);assert.doesNotMatch(original,/不新增云调用费用/);
  for(const contract_version of [1,2]){const legacy=renderToString(React.createElement(Understanding,{...up,initialRecord:{...base,status:'running',contract_version}}));assert.match(legacy,/已发送请求可能仍计费/);assert.doesNotMatch(legacy,/不新增云调用费用/);}
  });
 }finally{await rm(dir,{recursive:true,force:true});if(priorLocation===undefined)delete globalThis.location;else globalThis.location=priorLocation;if(priorStorage===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=priorStorage;}
});
