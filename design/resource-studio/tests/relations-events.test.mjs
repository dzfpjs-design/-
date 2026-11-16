import {relationCacheKey,newRelationEditor} from '../src/relation-contract.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {createServer} from 'vite';
import {mount} from '../scripts/react-event-harness.mjs';
test('real relation form saves/resumes server drafts and recovers a lost response before review/withdraw/history',async()=>{
 const previous={fetch:globalThis.fetch,localStorage:globalThis.localStorage,sessionStorage:globalThis.sessionStorage,window:globalThis.window};
 const cache=new Map(),storage={getItem:key=>cache.get(key)||null,setItem:(key,value)=>cache.set(key,value),removeItem:key=>cache.delete(key)};Object.assign(globalThis,{localStorage:storage,sessionStorage:storage,window:{addEventListener(){},removeEventListener(){}}});
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 let component;
 try{
  const {default:Workspace}=await server.ssrLoadModule('/src/RelationWorkspace.jsx');
  const account={workspace_id:'test-workspace',username:'operator',role:'operator'},release={id:'release-a',snapshot:{object_types:[{fields:[{key:'installation',label:'安装核对内容'}]}]}};
  let version=1,draft=null,rows=[],events=[],lostCreate=true,createCalls=0,guard=null;const commands=new Map(),requests=[];
  const object=(id,type,title)=>({id,type,title,version:1});
  function catalogValue(){return {instance_id:'instance-a',release_id:release.id,instance_version:version,package_id:'package-a',can_write:true,current:true,relation_types:[{id:'installed',title:'安装设备',source_types:['site'],target_types:['device'],cardinality:'many'}],objects:[object('site-a','site','林区甲'),object('device-a','device','设备乙')],evidence_options:[{kind:'observation',id:'observation-a',entity_id:'site-a',title:'安装核对记录',attributes:{installation:'已逐一核对实际安装位置'},observed_at:'2026-10-14T01:00:00Z'}]};}
  globalThis.fetch=async(path,options={})=>{const method=options.method||'GET',url=new URL(path,'http://test'),body=options.body?JSON.parse(options.body):null;requests.push({method,path:url.pathname,body});const finish=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
   if(url.pathname.endsWith('/catalog'))return finish(catalogValue());
   if(url.pathname.endsWith('/draft')&&method==='GET')return finish({draft,instance_version:version,can_write:true});
   if(url.pathname.includes('/requests/')){const response=commands.get(body.original_request.request_id);assert.deepEqual(body.original_request,requests.find(request=>request.body?.request_id===body.original_request.request_id)?.body);return finish(response?{saved:true,abandoned:false,instance_version:version,response}:{saved:false,abandoned:false,instance_version:version});}
   if(url.pathname.endsWith('/history'))return finish({items:events});
   if(url.pathname.endsWith('/relations')&&method==='GET')return finish({items:rows,next_cursor:null,truncated:false,instance_version:version});
   if(url.pathname.endsWith('/draft')&&method==='PUT'){assert.equal(body.expected_version,version);draft=structuredClone(body.draft);version++;const response={saved:true,request_id:body.request_id,operation:'draft',instance_version:version,draft};commands.set(body.request_id,response);return finish(response);}
   if(url.pathname.endsWith('/relations')&&method==='POST'){createCalls++;assert.equal(body.expected_version,version);const row={id:'relation-a',instance_id:'instance-a',release_id:release.id,version:1,status:'proposed',relation_type:body.relation_type,source_entity_id:body.source_entity_id,target_entity_id:body.target_entity_id,source_title:'林区甲',target_title:'设备乙',title:'安装设备',valid_from:body.valid_from,valid_until:null,data:{statement:body.statement,evidence_refs:body.evidence_refs,evidence_snapshot:[{kind:'observation',version:1,snapshot:{observed_at:'2026-10-14T01:00:00Z',data:{extracted:{attributes:{installation:'已逐一核对实际安装位置'}}}}}]}};rows=[row];version++;const response={saved:true,request_id:body.request_id,operation:'create',instance_version:version,record:row};commands.set(body.request_id,response);if(lostCreate){lostCreate=false;throw Error('模拟服务器保存后连接中断');}return finish(response);}
   if(url.pathname.endsWith('/review')){assert.equal(body.expected_version,rows[0].version);const row={...rows[0],version:rows[0].version+1,status:body.decision,data:{...rows[0].data,review:{decision:body.decision,reason:body.reason,at:'2026-10-14T01:05:00Z'}}};rows=[row];version++;events.push({created_at:'2026-10-14T01:05:00Z',data:{operation:'review',reason:body.reason,after:row}});const response={saved:true,request_id:body.request_id,operation:'review',instance_version:version,record:row};commands.set(body.request_id,response);return finish(response);}
   throw Error('unexpected request '+method+' '+url.pathname);
  };
  const props={instanceId:'instance-a',release,account,Btn:'button',onRegisterGuard:value=>{guard=value;}};
  component=mount(Workspace,props);await component.flush();await component.click('登记实际对象关系');await component.input('登记关系类型','installed');await component.input('关系起点对象','site-a');await component.input('关系目标对象','device-a');await component.input('实际关系声明','设备乙实际安装于林区甲');await component.input('关系登记依据','已核对本次实际安装记录');await component.input('关系来源 安装核对记录',true);
  await component.click('登记实际对象关系');assert.equal(component.byAria('实际关系声明').props.value,'');await component.click('继续关系恢复稿 1');assert.equal(component.byAria('实际关系声明').props.value,'设备乙实际安装于林区甲');assert.equal(component.byAria('关系登记依据').props.value,'已核对本次实际安装记录');
  await guard();await component.flush();assert.equal(draft.editor.statement,'设备乙实际安装于林区甲');assert.equal(draft.editor.refs[0],'observation-a');
  component.unmount();cache.clear();component=mount(Workspace,props);await component.flush();assert.equal(component.byAria('实际关系声明').props.value,'设备乙实际安装于林区甲');
  await component.click('保存待核查关系');assert.equal(createCalls,1);assert.ok([...cache.values()].some(value=>value.includes('"operation":"create"')));assert.ok(component.button('核对原关系请求是否已保存'));await assert.rejects(guard(),/结果未明/);
  await component.click('核对原关系请求是否已保存');assert.equal(createCalls,1);assert.ok(![...cache.values()].some(value=>value.includes('"operation":"create"')));assert.equal(rows[0].status,'proposed');
  await component.click('登记实际对象关系');await component.input('实际关系声明','未保存的人稿A不能被纠正B覆盖');await component.input('关系登记依据','保留草稿A理由');await component.click('纠正并重新核查');await component.click('继续关系恢复稿 2');assert.equal(component.byAria('实际关系声明').props.value,'未保存的人稿A不能被纠正B覆盖');assert.equal(component.byAria('关系登记依据').props.value,'保留草稿A理由');
  await component.input('关系核查理由 林区甲 设备乙','原记录内容与关系声明逐项相符');await component.click('确认实际关系');assert.equal(rows[0].status,'confirmed');assert.equal(events.length,1);
  await component.input('关系核查理由 林区甲 设备乙','复核发现需重新确认，撤销原决定');await component.click('撤回或撤销原确认');assert.equal(rows[0].status,'withdrawn');assert.equal(events.length,2);await component.click('查看关系历史');
  assert.equal(events[0].data.after.status,'confirmed');assert.equal(events[1].data.after.status,'withdrawn');assert.ok(requests.filter(request=>request.path.endsWith('/review')).every(request=>request.body.request_id));
 }finally{component?.unmount();await server.close();Object.assign(globalThis,previous);}
});

test('independent proposal apply locks the original body, rejects a mismatched receipt and resumes without another application',async()=>{
 const previous={fetch:globalThis.fetch,localStorage:globalThis.localStorage,sessionStorage:globalThis.sessionStorage};const cache=new Map(),storage={getItem:key=>cache.get(key)||null,setItem:(key,value)=>cache.set(key,value),removeItem:key=>cache.delete(key)};Object.assign(globalThis,{localStorage:storage,sessionStorage:storage});
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let component;
 try{
  const {default:Proposals}=await server.ssrLoadModule('/src/RelationshipProposals.jsx');const row={id:'analysis-a',version:2,contract_version:5,result:{relationships:[{name:'安装设备',source_type:'林区',target_type:'设备',description:'资料建议的关系类型',cardinality:'many',citations:[],inference_reason:''}]}};
  let original,postCount=0,badReceipt=true;const applied=[],errors=[],pendingStates=[];
  globalThis.fetch=async(path,options={})=>{const body=options.body?JSON.parse(options.body):null;const finish=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});if(path==='/api/setup/understanding/analysis-a')return finish(row);if(path.endsWith('/relationships/apply')){postCount++;original=body;throw Error('已保存后模拟丢失响应');}if(path.includes('/relationships/requests/'))return finish({analysis:row,revision:7,configuration:{instanceRelationTypes:[row.result.relationships[0]]},receipt:{request_id:original.request_id,state:'applied',body:{...original,revision:badReceipt?99:original.revision},names:original.names}});throw Error('unexpected '+path);};
  component=mount(Proposals,{record:row,workspace:{instanceObjects:[{name:'林区'},{name:'设备'}]},account:{workspace_id:'workspace-a',username:'operator'},canEdit:true,disabled:false,prepareDraft:async()=>6,onApplied:async saved=>applied.push(saved),onRecord(){},onPending:value=>pendingStates.push(value),run:async action=>{try{await action();}catch(error){errors.push(error.message);}},Citation:'blockquote'});await component.flush();await component.input('采用关系建议 安装设备',true);
  const first=component.button('采用选中的关系建议').props.onClick(),second=component.button('采用选中的关系建议').props.onClick();await Promise.all([first,second]);await component.flush();assert.equal(postCount,1);assert.deepEqual(Object.keys(original).sort(),['names','request_id','revision','version']);assert.deepEqual(original.names,['安装设备']);assert.equal(original.revision,6);assert.equal(pendingStates.at(-1),true);
  await component.click('核对关系采用请求是否已保存');assert.equal(applied.length,0);assert.match(errors.at(-1),/与原请求不一致/);assert.ok([...cache.values()].some(value=>value.includes(original.request_id)));
  badReceipt=false;await component.click('核对关系采用请求是否已保存');assert.equal(applied.length,1);assert.equal(postCount,1);assert.equal(pendingStates.at(-1),false);assert.ok(![...cache.values()].some(value=>value.includes(original.request_id)));
 }finally{component?.unmount();await server.close();Object.assign(globalThis,previous);}
});


test('capacity zero disables new relationship commands while original-request probe and abandon remain available',async()=>{
 const previous={fetch:globalThis.fetch,localStorage:globalThis.localStorage,sessionStorage:globalThis.sessionStorage,window:globalThis.window};const cache=new Map(),storage={getItem:key=>cache.get(key)||null,setItem:(key,value)=>cache.set(key,value),removeItem:key=>cache.delete(key)};Object.assign(globalThis,{localStorage:storage,sessionStorage:storage,window:{addEventListener(){},removeEventListener(){}}});
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let component;
 try{
  const {default:Workspace}=await server.ssrLoadModule('/src/RelationWorkspace.jsx');const account={workspace_id:'capacity-workspace',username:'operator',role:'operator'},release={id:'release-cap',snapshot:{object_types:[]}},original={request_id:'00000000-0000-4000-8000-000000000055',expected_version:1,release_id:release.id,statement:'容量满之前保留的人稿'},form={open:true,editor:{...newRelationEditor(),statement:'容量满之前保留的人稿'},reasons:{}};
  storage.setItem(relationCacheKey(account,'instance-cap',release.id),JSON.stringify({form,dirty:true,pending:{operation:'create',relationId:null,body:original}}));const calls=[];
  globalThis.fetch=async(path,options={})=>{const url=new URL(path,'http://test'),body=options.body?JSON.parse(options.body):null;calls.push({path:url.pathname,method:options.method||'GET',body});let result;
   if(url.pathname.endsWith('/catalog'))result={instance_id:'instance-cap',release_id:release.id,instance_version:9,commands_remaining:0,can_write:true,current:true,relation_types:[{id:'relation-cap',title:'包含',source_types:['site'],target_types:['record']}],objects:[],evidence_options:[]};
   else if(url.pathname.endsWith('/draft'))result={draft:null,instance_version:9,can_write:true};
   else if(url.pathname.endsWith('/relations'))result={items:[],next_cursor:null,truncated:false,instance_version:9};
   else if(url.pathname.includes('/requests/')){assert.deepEqual(body.original_request,original);result={saved:false,abandoned:url.pathname.endsWith('/abandon'),instance_version:9};}
   else throw Error('unexpected '+url.pathname);
   return new Response(JSON.stringify(result),{headers:{'Content-Type':'application/json'}});
  };
  component=mount(Workspace,{instanceId:'instance-cap',release,account,Btn:'button',onRegisterGuard(){}});await component.flush();assert.equal(component.button('登记实际对象关系').props.disabled,true);assert.equal(component.button('保存关系草稿').props.disabled,true);assert.equal(component.button('保存待核查关系').props.disabled,true);assert.equal(component.button('更新关系与来源').props.disabled,false);assert.equal(component.button('核对原关系请求是否已保存').props.disabled,false);
  await component.click('核对原关系请求是否已保存');assert.equal(component.button('作废未保存请求，保留草稿').props.disabled,false);assert.ok([...cache.values()].some(value=>value.includes(original.request_id)));await component.click('作废未保存请求，保留草稿');
  const saved=JSON.parse(storage.getItem(relationCacheKey(account,'instance-cap',release.id)));assert.equal(saved.pending,null);assert.equal(saved.form.editor.statement,form.editor.statement);assert.equal(saved.dirty,true);assert.equal(component.button('登记实际对象关系').props.disabled,true);assert.equal(component.button('更新关系与来源').props.disabled,false);assert.ok(calls.every(call=>call.method==='GET'||call.path.includes('/requests/')));
 }finally{component?.unmount();await server.close();Object.assign(globalThis,previous);}
});

test('inactive historical release permits original-request probe and safe abandon without authorizing retry',async()=>{
 const previous={fetch:globalThis.fetch,localStorage:globalThis.localStorage,sessionStorage:globalThis.sessionStorage,window:globalThis.window};const cache=new Map(),storage={getItem:key=>cache.get(key)||null,setItem:(key,value)=>cache.set(key,value),removeItem:key=>cache.delete(key)};Object.assign(globalThis,{localStorage:storage,sessionStorage:storage,window:{addEventListener(){},removeEventListener(){}}});
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let component;
 try{
  const {default:Workspace}=await server.ssrLoadModule('/src/RelationWorkspace.jsx');const account={workspace_id:'capacity-workspace',username:'operator',role:'operator'},release={id:'release-cap',snapshot:{object_types:[]}},original={request_id:'00000000-0000-4000-8000-000000000055',expected_version:1,release_id:release.id,statement:'容量满之前保留的人稿'},form={open:true,editor:{...newRelationEditor(),statement:'容量满之前保留的人稿'},reasons:{}};
  storage.setItem(relationCacheKey(account,'instance-cap',release.id),JSON.stringify({form,dirty:true,pending:{operation:'create',relationId:null,body:original}}));const calls=[];
  globalThis.fetch=async(path,options={})=>{const url=new URL(path,'http://test'),body=options.body?JSON.parse(options.body):null;calls.push({path:url.pathname,method:options.method||'GET',body});let result;
   if(url.pathname.endsWith('/catalog'))result={instance_id:'instance-cap',release_id:release.id,instance_version:9,commands_remaining:100,can_write:false,current:false,unavailable_reason:'实例已暂停或已换用新版本',relation_types:[{id:'relation-cap',title:'包含',source_types:['site'],target_types:['record']}],objects:[],evidence_options:[]};
   else if(url.pathname.endsWith('/draft'))result={draft:null,instance_version:9,can_write:true};
   else if(url.pathname.endsWith('/relations'))result={items:[],next_cursor:null,truncated:false,instance_version:9};
   else if(url.pathname.includes('/requests/')){assert.deepEqual(body.original_request,original);result={saved:false,abandoned:url.pathname.endsWith('/abandon'),instance_version:9};}
   else throw Error('unexpected '+url.pathname);
   return new Response(JSON.stringify(result),{headers:{'Content-Type':'application/json'}});
  };
  component=mount(Workspace,{instanceId:'instance-cap',release,account,Btn:'button',onRegisterGuard(){}});await component.flush();assert.equal(component.button('登记实际对象关系').props.disabled,true);assert.equal(component.button('保存关系草稿').props.disabled,true);assert.equal(component.button('保存待核查关系').props.disabled,true);assert.equal(component.button('更新关系与来源').props.disabled,false);assert.equal(component.button('核对原关系请求是否已保存').props.disabled,false);assert.equal(component.button('恢复原关系请求').props.disabled,true);
  await component.click('核对原关系请求是否已保存');assert.equal(component.button('作废未保存请求，保留草稿').props.disabled,false);assert.ok([...cache.values()].some(value=>value.includes(original.request_id)));await component.click('作废未保存请求，保留草稿');
  const saved=JSON.parse(storage.getItem(relationCacheKey(account,'instance-cap',release.id)));assert.equal(saved.pending,null);assert.equal(saved.form.editor.statement,form.editor.statement);assert.equal(saved.dirty,true);assert.equal(component.button('登记实际对象关系').props.disabled,true);assert.equal(component.button('更新关系与来源').props.disabled,false);assert.ok(calls.every(call=>call.method==='GET'||call.path.includes('/requests/')));
 }finally{component?.unmount();await server.close();Object.assign(globalThis,previous);}
});
