import test from 'node:test';
import assert from 'node:assert/strict';
import {externalCapabilityList} from '../src/external-capability-list.mjs';

const rows=[
  {id:'1', kind:'tool',name:'weather',release_version:'1.2.3',status:'published',data:{contract_version:'1.0',description:'天气服务 https://private.example/api token=secret',execution:{adapter:'http',endpoint:'https://must-not-render.example',secret_ref:'GA_SECRET'}}},
  {id:'2', kind:'specialist_model',name:'trees',release_version:'2.0.0',status:'validated',data:{description:'树木识别',contract_version:'1.0',execution:{adapter:'mcp'}}},
  {id:'3', kind:'foundation_model',name:'cloud',data:{execution:{adapter:'http'}}},
  {id:'4', kind:'tool',name:'local',data:{execution:{adapter:'builtin'}}},
];
test('only external HTTP/MCP records in the requested category, without connection credentials',()=>{
 const tools=externalCapabilityList(rows,'tool');assert.equal(tools.rows.length,1);assert.equal(tools.rows[0].status,'契约已发布');
 assert.equal(tools.rows[0].contractVersion,'1.0');assert.doesNotMatch(JSON.stringify(tools),/private\.example|must-not-render|secret|GA_SECRET/);
 assert.equal(externalCapabilityList(rows,'specialist_model').rows[0].status,'契约已校验');
 assert.equal(externalCapabilityList(rows).rows.length,2);
});
test('limited list is never presented as a complete inventory and invalid responses fail',()=>{
 assert.equal(externalCapabilityList(Array.from({length:100},()=>rows[0])).truncated,true);
 assert.throws(()=>externalCapabilityList({}),/invalid/);
 assert.throws(()=>externalCapabilityList([null]),/invalid/);
 assert.throws(()=>externalCapabilityList([],'foundation_model'),/invalid/);
});
