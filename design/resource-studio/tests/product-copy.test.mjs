import {test} from 'node:test';
import assert from 'node:assert/strict';
import {initialWorkspace} from '../src/catalog.js';
import {productWorkspace,productEditorCache,connectionName} from '../src/product-copy.js';
test('copy migration preserves user work and only renames known initial defaults',()=>{
 const w=initialWorkspace();w.connections[0].name='园区视频网关（示例）';w.connections[1].name='用户的示例影像服务';w.connections[0].gatewayConfig={name:'园区视频网关（示例）',protocolValues:{ONVIF:{network:'用户范围'}},settings:{timeout:60}};w.connections[0].gatewayReport={rows:[['认证','模拟通过']]};w.notes='用户说明';w.resources[0].businesses=['日常巡护'];
 const next=productWorkspace(w);assert.equal(next.connections[0].name,'园区视频网关');assert.equal(next.connections[1].name,w.connections[1].name);assert.deepEqual(next.resources,w.resources);assert.equal(next.notes,'用户说明');assert.deepEqual(next.connections[0].gatewayConfig.protocolValues,w.connections[0].gatewayConfig.protocolValues);assert.equal(next.connections[0].gatewayReport.rows[0][1],'通过');assert.equal(w.connections[0].name,'园区视频网关（示例）');
 const cache={forms:{one:{name:'园区视频网关（示例）',endpoint:'https://example.invalid',settings:{value:60}}},selected:{video:'one'},discarded:{form:{name:'自定义名称'}}};const updated=productEditorCache(cache);assert.equal(updated.forms.one.name,'园区视频网关');assert.deepEqual(updated.forms.one.settings,cache.forms.one.settings);assert.deepEqual(updated.selected,cache.selected);assert.equal(connectionName('自定义示例名称'),'自定义示例名称');
});
