import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToString} from 'react-dom/server';
import {createServer} from 'vite';
test('incomplete input names failure and original evidence without claiming a new success',async()=>{
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 try{
  const Screen=(await server.ssrLoadModule('/src/StateInputIssue.jsx')).default;
  const html=renderToString(React.createElement(Screen,{issue:{observation_id:'one',stage:'failed',source_name:'工程来源',attempts:2,input_integrity_verified:false,message:'上一成功登记仅供核查'},instanceId:'i',entityId:'e',stateHash:'hash',formKey:'account',canWrite:false,Btn:({children,...props})=>React.createElement('button',props,children)})).replace(/<!--.*?-->/g,'');
  for(const label of ['处理失败','上一成功登记仅供核查','已尝试2次','原输入未通过完整性核对','核对未完成输入与回执'])assert.ok(html.includes(label),label);
  assert.ok(!html.includes('保存原输入重试'));assert.ok(!html.includes('状态正常'));assert.ok(!html.includes('处理成功'));
 }finally{await server.close();}
});
