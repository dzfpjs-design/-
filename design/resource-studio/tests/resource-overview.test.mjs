import test from 'node:test';
import assert from 'node:assert/strict';
import {OVERVIEW_CARDS,overviewSummary} from '../src/resource-overview-contract.mjs';

test('overview describes implemented categories but never treats registrations as online or published instances',()=>{
 assert.deepEqual(OVERVIEW_CARDS.map(x=>x.id),['video','maps','sensor','docs','business','control','models','tools']);
 assert.match(overviewSummary('models',[[{status:'ready'},{status:'failed'}]]),/2 条安装记录 · 1 条本机检查通过/);
 const video=overviewSummary('video',[[{}],[{status:'confirmed',read_authorized:true},{status:'disabled',read_authorized:false}], [{provider_id:'usgs_kilauea',status:'published'}]]);
 assert.match(video,/1 个已授权通道/);assert.match(video,/1 项已发布公开来源/);assert.doesNotMatch(video,/在线|实例已启用/);
 assert.match(overviewSummary('sensor',[[{status:'active'}]]),/接收状态需查看记录/);
 assert.match(overviewSummary('maps',[[],[{provider_id:'nasa_modis',status:'published'}],[]]),/1 项已发布公开影像来源/);
 assert.match(overviewSummary('control',[{connections:[{}],has_more:true}]),/至少 1 个测试设备连接/);
});

test('unknown or malformed backend data is an error, never a false empty or healthy state',()=>{
 assert.throws(()=>overviewSummary('video',[[],[{status:'confirmed'}],[]]));
 assert.throws(()=>overviewSummary('models',[[null]]));
 assert.throws(()=>overviewSummary('maps',[[],{}]));
 assert.throws(()=>overviewSummary('control',[{connections:[],has_more:'yes'}]));
 assert.match(overviewSummary('tools',[[],Array.from({length:100},()=>({kind:'source'}))]),/仅查最近 100 项能力/);
});
