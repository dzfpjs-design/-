import test from 'node:test';
import assert from 'node:assert/strict';
import {releasedResources,enabledWorkflowSteps} from './instance-capability-overview.mjs';

function fixture(){return {
 instance:{id:'instance-a',status:'partially_active',active_release_id:'release-a',enabled_business_ids:['business-a']},
 release:{id:'release-a',instance_id:'instance-a',snapshot:{resources:[
  {id:'camera-a',name:'山顶摄像头',category:'video',businesses:['巡护']},
  {id:'model-a',name:'本地识别模型',category:'models',businesses:[]},
  {id:'public-a',name:'公开影像',category:'maps',protocol:'PUBLIC_OBSERVATION',details:{public_source_id:'source-a'}},
  {id:'off-a',name:'已停用服务',category:'maps',status:'paused'}],
  businesses:[{id:'business-a',name:'巡护',flow:{steps:[{id:'step-a',name:'提取画面',execution:{adapter:'local_specialist'}},{id:'human-a',name:'人工核查'}]}},
   {id:'business-b',name:'未启用业务',flow:{steps:[{id:'step-b',name:'不能显示',execution:{adapter:'http'}}]}}]}}
 };}

test('仅来自当前启用实例的发布快照；配置不等于在线或模型有效',()=>{
 const {instance,release}=fixture();
 const rows=releasedResources(instance,release,[{id:'camera-a',status:'available'},{id:'outside',name:'其他工作区'}],[{source_id:'source-a',status:'published'}]);
 assert.deepEqual(rows.map(row=>row.name),['山顶摄像头','本地识别模型','公开影像','已停用服务']);
 assert.match(rows[0].state,/配置核验通过.*在线状态待检查/);
 assert.match(rows[1].state,/可用性待核对/);
 assert.match(rows[2].state,/公开来源已发布/);
 assert.equal(rows[3].state,'配置已停用');
 assert.deepEqual(enabledWorkflowSteps(instance,release).map(s=>s.name),['提取画面']);
});

test('暂停、版本变化、跨实例或快照损坏时不展示旧资源',()=>{
 for(const mutate of [f=>f.instance.status='paused',f=>f.instance.active_release_id='other',f=>f.release.instance_id='other',f=>f.release.snapshot.resources=null]){
  const f=fixture();mutate(f);assert.equal(releasedResources(f.instance,f.release,[],[]),null);
 }
});

test('来源接口失败或无匹配不得推断在线，也不能借用同工作区其他来源',()=>{
 const {instance,release}=fixture();
 const rows=releasedResources(instance,release,null,[{source_id:'elsewhere',status:'published'}]);
 assert.match(rows[0].state,/待核对/);assert.match(rows[2].state,/待核对/);
 assert.ok(!rows.some(row=>row.state==='在线'));
});

test('公开来源显示同实例匹配的易读名称，缺少匹配或名称时保留发布快照名',()=>{
 const {instance,release}=fixture();
 release.snapshot.resources[2].name='nasa_modis';
 const match=releasedResources(instance,release,[],[{source_id:'source-a',name:'NASA MODIS 卫星影像',status:'published'}]);
 assert.equal(match[2].name,'NASA MODIS 卫星影像');
 const missing=releasedResources(instance,release,[],[{source_id:'other',name:'其他实例名称',status:'published'}]);
 assert.equal(missing[2].name,'nasa_modis');
 const empty=releasedResources(instance,release,[],[{source_id:'source-a',name:' ',status:'published'}]);
 assert.equal(empty[2].name,'nasa_modis');
});
