import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'vite';
import {mount} from '../scripts/react-event-harness.mjs';
import {account,reading,storage} from './report-draft-fixture.mjs';
import {digest} from '../src/report-draft-contract.mjs';

let Runtime,tmp;
test.before(async()=>{
 tmp=await mkdtemp(join(tmpdir(),'report-material-status-'));
 const previous=globalThis.sessionStorage;globalThis.sessionStorage=storage();
 try{
  const out=await build({configFile:false,envDir:tmp,logLevel:'silent',cacheDir:join(tmp,'cache'),build:{ssr:resolve('src/RuntimeSetup.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(resolve('node_modules/react/index.js')).href:id}}}});
  await writeFile(join(tmp,'runtime.mjs'),out.output.find(item=>item.isEntry).code);
  Runtime=(await import(pathToFileURL(join(tmp,'runtime.mjs')))).default;
 }finally{globalThis.sessionStorage=previous;}
});
test.after(()=>rm(tmp,{recursive:true,force:true}));

function nodes(value,out=[]){if(Array.isArray(value))value.forEach(item=>nodes(item,out));else if(value?.props){out.push(value);nodes(value.props.children,out);}return out;}
const text=value=>Array.isArray(value)?value.map(text).join(''):value?.props?text(value.props.children):['string','number'].includes(typeof value)?String(value):'';
const component=(tree,name)=>nodes(tree).find(item=>item.type?.name===name);
const field=(tree,placeholder)=>nodes(tree).find(item=>item.props.placeholder===placeholder);
const clone=value=>structuredClone(value);

async function environment(run){
 const keys=['sessionStorage','localStorage','window','location','history','navigator','fetch','setInterval','clearInterval'];
 const previous=Object.fromEntries(keys.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 const local=storage(),tab=storage(),timers=new Map(),uis=[],calls=[],draftWrites=[];
 const bytes=new TextEncoder().encode('synthetic document body, never sent to a service'),hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(value=>value.toString(16).padStart(2,'0')).join('');
 const queued={id:'70000000-0000-4000-8000-000000000031',name:'fresh-upload.docx',size:bytes.length,hash,version:1,status:'queued',archived:false,kind:'业务资料',note:'',block_count:0};
 const ready={...queued,version:3,status:'needs_input',block_count:3916};
 let user=clone(account),rows=[],delayList=null,listReads=0,bootReads=0,commits=0;
 let record={revision:1,configuration:{instanceProfile:{name:'核对原报告',domain:'林业',goal:'原目标'},materials:[],businesses:[],flows:{},resources:[],connections:[]},automation_review:{version:1,businesses:[]}};
 const setTags=()=>{tab.setItem('ga-setup-csrf','synthetic-status-test-token-not-a-credential');tab.setItem('ga-setup-csrf-user',user.user_id);tab.setItem('ga-setup-csrf-workspace',user.workspace_id);tab.setItem('ga-setup-csrf-username',user.username);};setTags();
 Object.defineProperties(globalThis,{
  sessionStorage:{configurable:true,writable:true,value:tab},localStorage:{configurable:true,writable:true,value:local},
  window:{configurable:true,writable:true,value:{addEventListener(){},removeEventListener(){}}},
  location:{configurable:true,writable:true,value:{search:''}},history:{configurable:true,writable:true,value:{replaceState(){},pushState(){}}},
  navigator:{configurable:true,writable:true,value:{locks:{request:async(key,options,fn)=>fn()}}},
  setInterval:{configurable:true,writable:true,value:fn=>{const id=Symbol();timers.set(id,fn);return id;}},clearInterval:{configurable:true,writable:true,value:id=>timers.delete(id)}
 });
 globalThis.fetch=async(path,options={})=>{
  const method=options.method||'GET';calls.push({path,method,headers:options.headers});let result;
  if(path==='/api/me')result=clone(user);
  else if(path==='/api/session/restore')result={...user,csrf_token:'synthetic-status-test-token-not-a-credential'};
  else if(path==='/api/login'){result={username:user.username,csrf_token:'synthetic-status-test-token-not-a-credential'};}
  else if(path==='/api/setup/session/revoke')result={revoked:true};
  else if(path==='/api/setup/draft'&&method==='GET'){bootReads++;result=clone(record);}
  else if(path==='/api/setup/draft'&&method==='PUT'){const body=JSON.parse(options.body);assert.equal(body.revision,record.revision);draftWrites.push(clone(body));record={...record,revision:record.revision+1,configuration:clone(body.configuration)};result=clone(record);}
  else if(path==='/api/setup/materials'&&method==='POST'){assert.equal(options.body.get('file').name,queued.name);rows=[queued];result=clone(queued);}
  else if(path==='/api/setup/materials/'+queued.id+'/content')return new Response(bytes);
  else if(path==='/api/setup/materials?limit=500'){listReads++;result=delayList?await delayList():clone(rows);}
  else if(path==='/api/setup/report-drafts/active')result={blocked:false,report_draft_active:false,legacy_understanding_active:false,own:[]};
  else if(path.startsWith('/api/setup/report-drafts/readings/')){
   const url=new URL(path,'http://synthetic.invalid'),version=Number(url.searchParams.get('version'));
   assert.equal(url.searchParams.get('hash'),hash);
   const source={...ready,version,evidence_id:'synthetic-source',reading:{...clone(reading),source_sha256:hash,blocks:Array.from({length:3916},(_,i)=>({id:'p'+i,kind:'paragraph',text:'合成正文'+i,location:{view:'accepted_final_v1',body_index:i+1,paragraph_index:i+1}}))}};
   result={schema:'report-reading-material-v1',material:source,source_hash:await digest({schema:'report-reading-source-v1',materials:[source]}),can_apply:false,can_publish:false};
  }else throw Error('Unexpected offline fixture request '+method+' '+path);
  return new Response(JSON.stringify(result),{headers:{'Content-Type':'application/json'}});
 };
 const runtime=mount(Runtime,{});uis.push(runtime);let studio,studioProps,action,actionProps;
 async function settle(){
  for(let i=0;i<3;i++){
   await runtime.flush();const element=component(runtime.render(),'SetupStudio');
   if(element){
    const next={...element.props,onCommit:(...args)=>{commits++;return element.props.onCommit(...args);}};
    if(!studio){studioProps=next;studio=mount(element.type,studioProps);uis.push(studio);}else Object.assign(studioProps,next);
    await studio.flush();const item=component(studio.render(),'ReportDraftMaterialAction');
    if(item){if(!action){actionProps={...item.props};action=mount(item.type,actionProps);uis.push(action);}else Object.assign(actionProps,item.props);await action.flush();}
   }
  }
 }
 try{await settle();await run({runtime,settle,queued,ready,local,tab,calls,draftWrites,get studio(){return studio;},get action(){return action;},get actionProps(){return actionProps;},get studioProps(){return studioProps;},get bootReads(){return bootReads;},get listReads(){return listReads;},get commits(){return commits;},setRows:value=>{rows=clone(value);},setListDelay:value=>{delayList=value;},switchUser:next=>{user=clone(next);record={revision:1,configuration:{instanceProfile:{name:'乙工作区',domain:'其他',goal:'乙内容'},materials:[],businesses:[],flows:{},resources:[],connections:[]},automation_review:{version:1,businesses:[]}};rows=[];},async upload(){const input=nodes(studio.render()).find(item=>item.type==='input'&&item.props.type==='file');assert.ok(input);input.props.onChange({target:{files:[new File([bytes],queued.name,{type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'})],value:'chosen'}});await settle();},async refresh(){await runtime.click('刷新资料处理状态');await settle();}});}
 finally{uis.reverse().forEach(ui=>ui.unmount());for(const[key,descriptor]of Object.entries(previous)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}
}

test('actual Runtime → same SetupStudio → Action updates fresh queued upload without reload or draft write',async()=>environment(async f=>{
 await f.upload();assert.ok(f.action);assert.equal(f.action.render(),null);
 field(f.studio.render(),'希望管理哪些对象、解决什么问题？可结合上传资料补充说明。').props.onChange({target:{value:'人工目标末字Z'}});
 await f.settle();
 field(f.studio.render(),'说明哪些业务或章节需要重点核对').props.onChange({target:{value:'资料说明末字Q'}});
 await f.settle();const originalStudio=f.studio,workspace=f.studioProps.workspace,writeCount=f.draftWrites.length,commitCount=f.commits,readCount=f.bootReads;
 f.setRows([f.ready]);await f.refresh();
 assert.equal(f.studio,originalStudio);assert.equal(f.studioProps.workspace,workspace);assert.equal(f.bootReads,readCount);
 assert.equal(f.draftWrites.length,writeCount);assert.equal(f.commits,commitCount);
 assert.equal(f.actionProps.material.status,'needs_input');assert.equal(f.actionProps.material.version,3);
 assert.match(text(f.action.render()),/接受修订后的正文与表格：3916段/);
 assert.equal(field(f.studio.render(),'希望管理哪些对象、解决什么问题？可结合上传资料补充说明。').props.value,'人工目标末字Z');
 assert.equal(field(f.studio.render(),'说明哪些业务或章节需要重点核对').props.value,'资料说明末字Q');
 assert.equal(f.studioProps.workspace.materials[0].status,'queued');
 await f.action.click('仅根据已读取正文生成');
 const dialog=component(f.runtime.render(),'ReportDraftDialog');assert.ok(dialog);assert.equal(dialog.props.material.version,3);assert.equal(dialog.props.material.hash,f.ready.hash);
 f.setRows([{...f.ready,version:2,status:'parsing'}]);await f.refresh();assert.equal(f.actionProps.material.version,3);assert.match(text(f.action.render()),/接受修订后的正文与表格：3916段/);
}));

test('actual Action reads newly eligible same-version status and local archive suppresses its entry',async()=>environment(async f=>{
 await f.upload();f.setRows([{...f.queued,status:'needs_input'}]);await f.refresh();
 assert.equal(f.actionProps.material.version,1);assert.match(text(f.action.render()),/接受修订后的正文与表格：3916段/);
 const archive=f.studio.button('归档');archive.props.onClick();f.studio.render();
 await f.studio.click('确认归档');await f.settle();await f.studio.click('查看已归档');await f.settle();assert.equal(f.action.render(),null);
}));

test('actual Runtime rejects a late A material refresh after logout and B workspace boot',async()=>environment(async f=>{
 await f.upload();let finish;f.setListDelay(()=>new Promise(resolve=>{finish=resolve;}));
 const refresh=f.runtime.button('刷新资料处理状态').props.onClick();await f.runtime.flush();assert.equal(typeof finish,'function');
 await f.runtime.click('退出');f.setListDelay(null);
 const next={...account,user_id:'70000000-0000-4000-8000-000000000099',workspace_id:'70000000-0000-4000-8000-000000000098',username:'synthetic-other'};f.switchUser(next);
 const form=nodes(f.runtime.render()).find(item=>item.type==='form');assert.ok(form);await form.props.onSubmit({preventDefault(){}});await f.runtime.flush();
 finish([{...f.ready,version:99}]);await refresh;await f.runtime.flush();
 const element=component(f.runtime.render(),'SetupStudio');assert.equal(element.props.account.user_id,next.user_id);assert.deepEqual(element.props.canonicalMaterials,[]);assert.doesNotMatch(text(f.runtime.render()),/fresh-upload\.docx/);
}));

test('actual material progress keeps full OCR coverage reasons in closed native details without changing counts, errors, controls or saving configuration',async()=>environment(async f=>{
 await f.upload();const writeCount=f.draftWrites.length,commitCount=f.commits,reason='合成覆盖说明：文本框阅读顺序、表格对应关系和原生图片仍须核对；未知内容继续保留。'.repeat(8),ocr={coverage_status:'blocked',run_id:null,run_status:null,total:17,confirmed:0,reason};
 f.setRows([{...f.ready,block_count:1571,error:'原资料错误仍须显示',warnings:['原资料警示保留'],ocr}]);await f.refresh();
 const material=()=>nodes(f.runtime.render()).find(n=>n.props.className==='runtime-material');
 const inspect=expectedCount=>{const row=material();assert.ok(row);const detail=nodes(row).find(n=>n.type==='details'&&nodes(n).some(child=>child.type==='summary'&&text(child)==='查看未覆盖内容'));assert.ok(detail);assert.notEqual(detail.props.open,true);assert.equal(nodes(detail).find(n=>n.type==='p').props.children,reason);assert.equal(text(row).split(reason).length-1,1);assert.ok(nodes(row).some(n=>n.type==='p'&&text(n).includes(expectedCount)&&!text(n).includes(reason)));assert.match(text(row),/仍有内容待核对/);assert.match(text(row),/需要补充解析能力/);assert.equal(nodes(row).find(n=>n.props.className==='error').props.children,'原资料错误仍须显示');assert.match(text(row),/原资料警示保留/);assert.ok(nodes(row).some(n=>typeof n.props.onClick==='function'&&text(n)==='识别与核对图片文字'));assert.ok(nodes(row).some(n=>typeof n.props.onClick==='function'&&text(n)==='重试解析'));};
 inspect('Word内嵌图片引用：17处 · 已确认0处');assert.match(text(material()),/上传时解析：1571个文本块/);
 f.setRows([{...f.ready,name:'same-origin-copy.pdf',version:4,block_count:235,error:'原资料错误仍须显示',warnings:['原资料警示保留'],ocr:{...ocr,force_all_pages:true,total:235,confirmed:2}}]);await f.refresh();inspect('PDF全页文字核对：235页 · 已确认2页');assert.equal(f.draftWrites.length,writeCount);assert.equal(f.commits,commitCount);
}));
