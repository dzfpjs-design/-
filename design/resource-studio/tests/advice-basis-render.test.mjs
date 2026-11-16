import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToString} from 'react-dom/server';
import {createServer} from 'vite';

test('local basis shows saved progress and limitations while preserving legacy uncertainty',async()=>{
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 try {
  const Screen=(await server.ssrLoadModule('/src/AdviceBasis.jsx')).default;
  const references=[{id:'signal',text:'已保存判断'},{id:'original-task',text:'已保存步骤'},{id:'record:0',text:'固定原件'}];
  const Reference=({reference})=>React.createElement('details',null,React.createElement('summary',null,'原引用'),reference.text);
  const props={references,Reference};
  assert.match(renderToString(React.createElement(Screen,props)),/历史建议未保存本地依据核对卡/);
  const audit={scope:'核对保存记录',original_task:{status_label:'流程已完成',saved_steps:[{name:'人工核查',note:'核查说明已保存'}],reference_id:'original-task',limit:'不能覆盖'},saved_business_review:{decision:'不属于业务异常',note:'公开工程结果',reference_id:'signal',limit:'不等于领域结论'},records:[{reference_id:'record:0',name:'公开原件',integrity_verified:false,values:[],processing_status:'failed',source_group:null,limit:'保留原资料'}],comparisons:[],omitted_references:['record:1'],limit:'不能仅凭差值称为冲突'};
  const html=renderToString(React.createElement(Screen,{...props,audit})).replace(/<!--.*?-->/g,'');
  for(const text of ['流程已完成','1 步处理说明已保存','原线索判断已保存','原件身份未核实','处理失败','不表示没有数据或异常','未用于本地事实卡','不能仅凭差值称为冲突'])assert.ok(html.includes(text),text);
 } finally {await server.close();}
});
