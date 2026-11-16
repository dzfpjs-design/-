import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';

test('authorised source selection ignores a stale previous source response',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost'});
 globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const require=createRequire(import.meta.url),React=require('react'),{createRoot}=require('react-dom/client'),{act}=React;
 const built=await build({entryPoints:['src/DjiDockStatus.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'});
 const loaded={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,loaded,loaded.exports);
 const Studio=loaded.exports.default,root=createRoot(document.getElementById('root'));
 const A={gateway_id:'g1',gateway_name:'MQTT',gateway_status:'active',device_sn:'A',name:'机场甲'},B={...A,device_sn:'B',name:'机场乙'};
 let releaseA;const delayed=new Promise(resolve=>{releaseA=resolve});
 const view=(source,battery)=>({source,receiver:null,latest:{parsed:true,received_at:'2026-11-15T12:00:00Z',retained:false,state_note:'设备信息需核验',reported:{reported_at:null,mode_code:0,aircraft_in_dock:1,battery_percent:battery,wind_m_s:null,temperature_c:null,rainfall_code:null,position:null,position_note:'坐标未通过设备定位有效性校验'}}});
 const request=async(path)=>path.endsWith('/sources')?{sources:[A,B]}:path.endsWith('/A')?delayed:view(B,85);
 const Btn=({children,...props})=>React.createElement('button',props,children);
 try{
  await act(async()=>{root.render(React.createElement(Studio,{account:{workspace_id:'w1',user_id:'u1'},Btn,request}));await new Promise(r=>setTimeout(r,0))});
  const choose=document.querySelector('select[aria-label="大疆机场状态来源"]');
  await act(async()=>{choose.value='g1:A';choose.dispatchEvent(new window.Event('change',{bubbles:true}));await Promise.resolve()});
  await act(async()=>{choose.value='g1:B';choose.dispatchEvent(new window.Event('change',{bubbles:true}));await new Promise(r=>setTimeout(r,0))});
  await act(async()=>{releaseA(view(A,11));await Promise.resolve();await Promise.resolve()});
  assert.match(document.body.textContent,/机场乙/);assert.match(document.body.textContent,/85%/);assert.doesNotMatch(document.body.textContent,/11%/);
  assert.match(document.body.textContent,/不会发送飞行或控制指令/);
 }finally{await act(async()=>root.unmount());dom.window.close();delete globalThis.window;delete globalThis.document}
});
