import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToString} from 'react-dom/server';
import {createServer} from 'vite';
test('correction is a separate receipt, never an edit of raw failure or new sensor truth',async()=>{
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 try{
  const Screen=(await server.ssrLoadModule('/src/InputCorrection.jsx')).default;
  const render=detail=>renderToString(React.createElement(Screen,{detail,instanceId:'i',entityId:'e',formKey:'account',canWrite:false,Btn:({children,...props})=>React.createElement('button',props,children)})).replace(/<!--.*?-->/g,'');
  const ready=render({observation_id:'o',correction:{available:false},correction_result:{new_values:{level:39.5}}});
  for(const label of ['原输入、原登记时间和失败回执保留','不代表新的现场观测或异常已排除','已成功处理并送达数据管理智能体','旧失败留在历史中'])assert.ok(ready.includes(label),label);
  assert.ok(!ready.includes('另存更正并处理'));
  const waiting=render({observation_id:'o',correction:{saved:{status:'processed'}}});assert.ok(waiting.includes('原缺口不能关闭'));assert.ok(!waiting.includes('已成功处理并送达'));
 }finally{await server.close();}
});
