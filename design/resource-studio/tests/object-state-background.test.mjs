import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToString} from 'react-dom/server';
import {createServer} from 'vite';
test('background state separates old receipt, expiry and failure from current physical truth',async()=>{
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 try{
  const Screen=(await server.ssrLoadModule('/src/ObjectStateBackground.jsx')).default;
  const render=(background,props={})=>renderToString(React.createElement(Screen,{background,stateHash:'current',...props})).replace(/<!--.*?-->/g,'');
  assert.match(render({latest:null,next_expiry:null,history:[]}),/尚无后台核对回执/);
  const receipt={id:'r',status:'succeeded',reason:'新登记处理结果已保存',created_at:'2026-10-10T00:00:00Z',result:{checked_at:'2026-10-10T00:00:00Z',state_version:2,state_hash:'old'}};
  const html=render({latest:receipt,next_expiry:{available_at:'2026-10-11T00:00:00Z'},history:[receipt],history_limit:30});
  for(const text of ['此前保存的后台结果','不能把旧回执当作当前状态','只核对已存记录，不采集新数据','不等于现场状态已验证'])assert.ok(html.includes(text),text);
  const failed=render({latest:{...receipt,status:'failed',result:null,error:'原授权已撤销'},history:[]});
  assert.match(failed,/后台核对未完成/);assert.match(failed,/原授权已撤销/);assert.ok(!failed.includes('本地核对已完成'));
  const matched={latest:{...receipt,result:{...receipt.result,state_hash:'current'}},history:[]},basis={stateVersion:2,cache:{status:'saved',reason:null,background_result_matched:true}};
  assert.match(render(matched,basis),/与下方已保存核对结果一致/);
  for(const props of [{...basis,stateVersion:3},{...basis,verified:false},{...basis,cache:{...basis.cache,background_result_matched:false}},{...basis,cache:{...basis.cache,status:'missing'}},{...basis,cache:{...basis.cache,status:'blocked_integrity'}}])assert.ok(!render(matched,props).includes('与下方已保存核对结果一致'),'only a currently verified hash AND version with real saved matched cache may claim consistency');
 }finally{await server.close();}
});
