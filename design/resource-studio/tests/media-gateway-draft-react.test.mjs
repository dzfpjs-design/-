import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act,useRef,useState} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import {webcrypto} from 'node:crypto';
import {safeGatewayField} from '../src/media-gateway-draft.mjs';

test('video gateway drafts survive remount and failed storage blocks navigation; uncertain requests recover by original ID',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/setup/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT','__mediaFixture','crypto'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const state=globalThis.__mediaFixture={workspace:{resources:[],connections:[]},fail:false,calls:[],guard:null,postCount:0};
 state.api=async(path,opts)=>{if(path==='/media/gateways')return [{id:'existing',version:1,name:'原连接',status:'draft',api_origin:'http://127.0.0.1:9997',rtsp_origin:'rtsp://127.0.0.1:8554'}];if(path.startsWith('/media/gateway-registrations/requests/')){if(state.recoverMismatch)return {id:'new',...state.calls.at(-1),version:2,registration_matches_current:false};const e=Error('未找到');e.status=404;throw e;}if(path==='/media/gateway-registrations'){const body=JSON.parse(opts.body);state.calls.push(body);if(++state.postCount===1)throw Error('断线');return {id:'new',...body,version:1,registration_matches_current:true};}return [];};
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom',plugins:[{name:'media-draft-fixtures',enforce:'pre',resolveId(id){if(/(?:^|\/)runtime-api(?:\.js)?$/.test(id))return '\0media-api';if(['ContinuousStudio','MediaMonitorStudio','VideoDomainStudio','VideoDailyStudio','VideoReadBoundary','AudioSourcePanel'].some(n=>id==='./'+n||id==='./'+n+'.jsx'))return '\0media-component';},load(id){if(id==='\0media-api')return `export const json=(...a)=>globalThis.__mediaFixture.api(...a);`;if(id==='\0media-component')return `import React from 'react';export default ()=>React.createElement('div');`;}}]});
 let root;
 try{
  const Studio=(await server.ssrLoadModule('/src/MediaGatewayStudio.jsx')).default;const {createRoot}=await import('react-dom/client');root=createRoot(document.getElementById('root'));
  const Btn=({children,primary,...p})=>React.createElement('button',p,children),Field=({label,children})=>React.createElement('label',null,label,children);
  function Harness(){const [workspace,setWorkspace]=useState(state.workspace);const ref=useRef(workspace);ref.current=workspace;return React.createElement(Studio,{account:{role:'admin',workspace_id:'ws',user_id:'u'},workspace,Btn,Field,onSave:async()=>{},onAudioSourceGuard:fn=>{state.guard=fn;},onCommit:fn=>{if(state.fail)return false;const next=fn(ref.current);ref.current=next;state.workspace=next;setWorkspace(next);return true;}});}
  const render=async()=>act(async()=>{root.render(React.createElement(Harness));await Promise.resolve();await Promise.resolve();});
  const button=text=>[...document.querySelectorAll('button')].find(x=>x.textContent===text);
  const click=async text=>act(async()=>{assert.ok(button(text),text);button(text).click();await Promise.resolve();await Promise.resolve();});
  const input=(name,value)=>act(async()=>{const el=document.querySelector(`input[aria-label="${name}"]`);Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});
  await render();await click('添加视频网关');await input('网关名称','林场网关');const id=state.workspace.mediaGatewayDraft.request_id;
  state.fail=true;await input('网关名称','林场主网关');await act(async()=>assert.equal(state.guard(),false));assert.equal(state.workspace.mediaGatewayDraft.name,'林场网关');
  state.fail=false;await click('重试保存人稿');assert.equal(state.workspace.mediaGatewayDraft.name,'林场主网关');await act(async()=>assert.equal(state.guard(),true));
  await act(async()=>root.unmount());root=createRoot(document.getElementById('root'));await render();assert.equal(document.querySelector('input[aria-label="网关名称"]').value,'林场主网关');assert.equal(state.workspace.mediaGatewayDraft.request_id,id);
  const select=document.querySelector('select[aria-label="选择视频网关"]');await act(async()=>{select.value='existing';select.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});assert.equal(select.value,'');assert.match(document.body.textContent,/先保存或放弃/);
  await click('保存连接');assert.equal(state.workspace.mediaGatewayDraft.pending,true);assert.equal(state.calls.length,1);assert.ok(button('核对原请求'));assert.equal(button('保存连接'),undefined);
  await click('核对原请求');assert.ok(button('按原请求重试'));await click('按原请求重试');assert.equal(state.calls.length,2);assert.deepEqual(state.calls[1],state.calls[0]);assert.equal(state.workspace.mediaGatewayDraft,null);
  state.postCount=0;state.recoverMismatch=true;await click('添加视频网关');await input('网关名称','另一网关');await click('保存连接');await click('核对原请求');assert.match(document.body.textContent,/后来已被修改/);assert.equal(state.workspace.mediaGatewayDraft.pending,true);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,d] of old){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k];}}
});

test('video gateway draft rejects credential-bearing and non-IP origins',()=>{assert.equal(safeGatewayField('api_origin','http://user:secret@127.0.0.1:9997'),false);assert.equal(safeGatewayField('rtsp_origin','rtsp://127.0.0.1:8554/?token=secret'),false);assert.equal(safeGatewayField('api_origin','http://127.0.0.1:9997'),true);});

test('editing interrupted before PUT can recover with original version; conflicting retry preserves inputs',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/setup/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT','__mediaFixture','crypto'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const state=globalThis.__mediaFixture={workspace:{resources:[],connections:[]},failSave:false,calls:[],conflict:false,row:{id:'existing',version:1,name:'原连接',status:'draft',api_origin:'http://127.0.0.1:9997',rtsp_origin:'rtsp://127.0.0.1:8554'}};
 state.api=async(path,opts)=>{if(path==='/media/gateways')return [state.row];if(path==='/media/gateways/existing'){const body=JSON.parse(opts.body);state.calls.push(body);assert.equal(opts.method,'PUT');if(state.conflict){const e=Error('版本冲突');e.status=409;throw e;}state.row={...state.row,...body,version:body.version+1};return state.row;}return [];};
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom',plugins:[{name:'edit-fixture',enforce:'pre',resolveId(id){if(/(?:^|\/)runtime-api(?:\.js)?$/.test(id))return '\0edit-api';if(['ContinuousStudio','MediaMonitorStudio','VideoDomainStudio','VideoDailyStudio','VideoReadBoundary','AudioSourcePanel'].some(n=>id==='./'+n||id==='./'+n+'.jsx'))return '\0edit-component';},load(id){if(id==='\0edit-api')return `export const json=(...a)=>globalThis.__mediaFixture.api(...a);`;if(id==='\0edit-component')return `import React from 'react';export default ()=>React.createElement('div');`;}}]});
 let root;
 try{
  const Studio=(await server.ssrLoadModule('/src/MediaGatewayStudio.jsx')).default;const {createRoot}=await import('react-dom/client');root=createRoot(document.getElementById('root'));
  const Btn=({children,primary,...p})=>React.createElement('button',p,children),Field=({label,children})=>React.createElement('label',null,label,children);
  function Harness(){const [workspace,setWorkspace]=useState(state.workspace);const ref=useRef(workspace);ref.current=workspace;return React.createElement(Studio,{account:{role:'admin',workspace_id:'ws',user_id:'u'},workspace,Btn,Field,onSave:async()=>{if(state.failSave)throw Error('配置同步失败');},onCommit:fn=>{const next=fn(ref.current);ref.current=next;state.workspace=next;setWorkspace(next);return true;}});}
  const button=text=>[...document.querySelectorAll('button')].find(x=>x.textContent===text);
  const click=async text=>act(async()=>{assert.ok(button(text),text);button(text).click();await Promise.resolve();await Promise.resolve();});
  const input=(name,value)=>act(async()=>{const el=document.querySelector(`input[aria-label="${name}"]`);Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});
  await act(async()=>{root.render(React.createElement(Harness));await Promise.resolve();await Promise.resolve();});
  const select=document.querySelector('select[aria-label="选择视频网关"]');await act(async()=>{select.value='existing';select.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});
  await click('修改连接');await input('网关名称','修改后的连接');state.failSave=true;await click('保存连接');assert.equal(state.calls.length,0);assert.equal(state.workspace.mediaGatewayDraft.pending,true);
  state.failSave=false;await click('核对原请求');assert.ok(button('按原请求重试'));state.conflict=true;await click('按原请求重试');assert.equal(state.calls.length,1);assert.equal(state.calls[0].version,1);assert.equal(state.workspace.mediaGatewayDraft.name,'修改后的连接');assert.equal(state.workspace.mediaGatewayDraft.pending,true);assert.match(document.body.textContent,/再次核对/);assert.equal(button('按原请求重试'),undefined);
  await click('核对原请求');state.conflict=false;await click('按原请求重试');assert.equal(state.calls.length,2);assert.deepEqual(state.calls[1],state.calls[0]);assert.equal(state.workspace.mediaGatewayDraft,null);assert.equal(state.row.name,'修改后的连接');assert.equal(state.row.version,2);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,d] of old){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k];}}
});
