import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';

test('live polling uses durable versions, recovers errors and isolates account changes',async t=>{
  const dom=new JSDOM('<div id="app"></div>',{url:'http://localhost'});
  globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.localStorage=dom.window.localStorage;
  Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
  const held=new Set();Object.defineProperty(dom.window.navigator,'locks',{value:{request:async(name,opt,fn)=>{
    if(held.has(name))return fn(null);held.add(name);try{return await fn({name})}finally{held.delete(name)}
  }}});
  globalThis.IS_REACT_ACT_ENVIRONMENT=true;
  const require=createRequire(import.meta.url),React=require('react'),{createRoot}=require('react-dom/client'),{act}=React;
  const built=await build({entryPoints:['src/UavSimulationStudio.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'});
  const loaded={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,loaded,loaded.exports);
  const Studio=loaded.exports.default,root=createRoot(document.getElementById('app'));
  t.after(async()=>{await act(async()=>{root.unmount()});dom.window.close()});
  const baseRow={id:'m1',event_id:'事件甲',status:'awaiting_approval',version:1,plan_hash:'hash',request_id:'original',
    plan:{distance_m:30,duration_seconds:70,battery_percent:100,wind_m_s:2,limitations:'仅模拟'},reasons:[],alerts:[]};
  let latest=baseRow,listCount=0,holdNext=false,resolveHeld,failing=false,currentAccount='old';
  const other={...baseRow,id:'m2',event_id:'事件乙',version:1};
  const request=async(path,options={})=>{
    if(path==='/uav-simulation/missions'&&!options.method){
      listCount++;
      if(holdNext){holdNext=false;return await new Promise(resolve=>{resolveHeld=resolve})}
      if(failing)throw Error('offline');
      return {missions:[currentAccount==='old'?latest:other]};
    }
    if(path.endsWith('/start')){latest={...baseRow,version:3,status:'flying'};return latest}
    if(path.endsWith('/return')){latest={...baseRow,version:6,status:'returned'};return latest}
    if(path.endsWith('/m1'))return baseRow;
    throw Error('unexpected request '+path);
  };
  const account={workspace_id:'w1',user_id:'u1',role:'admin'},props={account,request,_pollIntervalMs:15};
  await act(async()=>{root.render(React.createElement(Studio,props));await new Promise(r=>setTimeout(r,1))});
  const button=text=>[...document.querySelectorAll('button')].find(x=>x.textContent.includes(text));
  await act(async()=>{button('事件甲').click();await new Promise(r=>setTimeout(r,1))});
  holdNext=true;
  await act(async()=>{await new Promise(r=>setTimeout(r,25))});
  assert.equal(typeof resolveHeld,'function','第二次轮询应处于在途状态');
  await act(async()=>{button('确认方案并派发模拟任务').click();await new Promise(r=>setTimeout(r,1))});
  assert.match(document.querySelector('article').textContent,/模拟飞行中/);
  await act(async()=>{resolveHeld({missions:[{...baseRow,version:2,status:'connection_lost'}]});await new Promise(r=>setTimeout(r,1))});
  assert.match(document.querySelector('article').textContent,/模拟飞行中/);
  assert.doesNotMatch(document.querySelector('article').textContent,/已失联/);
  latest={...baseRow,version:4,status:'low_battery',alerts:['模拟无人机电量不足']};
  await act(async()=>{await new Promise(r=>setTimeout(r,25))});
  assert.match(document.querySelector('article').textContent,/电量不足/);
  assert.match(document.querySelector('ul').textContent,/电量不足/);
  holdNext=true;resolveHeld=undefined;
  await act(async()=>{await new Promise(r=>setTimeout(r,25))});
  assert.equal(typeof resolveHeld,'function');
  await act(async()=>{button('确认模拟返航').click();await new Promise(r=>setTimeout(r,1))});
  await act(async()=>{resolveHeld({missions:[{...baseRow,version:5,status:'flying'}]});await new Promise(r=>setTimeout(r,1))});
  assert.match(document.querySelector('article').textContent,/已模拟返航/);
  assert.match(document.querySelector('ul').textContent,/已模拟返航/);
  failing=true;
  await act(async()=>{await new Promise(r=>setTimeout(r,25))});
  assert.match(document.getElementById('app').textContent,/状态更新失败/);
  failing=false;
  await act(async()=>{await new Promise(r=>setTimeout(r,25))});
  assert.doesNotMatch(document.getElementById('app').textContent,/状态更新失败/);
  holdNext=true;resolveHeld=undefined;
  await act(async()=>{await new Promise(r=>setTimeout(r,25))});
  assert.equal(typeof resolveHeld,'function');
  currentAccount='new';
  await act(async()=>{root.render(React.createElement(Studio,{...props,account:{...account,workspace_id:'w2',user_id:'u2'}}));await new Promise(r=>setTimeout(r,1))});
  await act(async()=>{resolveHeld({missions:[{...baseRow,version:100,status:'connection_lost'}]});await new Promise(r=>setTimeout(r,1))});
  assert.match(document.getElementById('app').textContent,/事件乙/);
  assert.doesNotMatch(document.getElementById('app').textContent,/事件甲/);
});
