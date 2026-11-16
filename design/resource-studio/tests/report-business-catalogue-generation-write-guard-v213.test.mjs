// Client lock/guard routing only. No provider, paid source or real API is faked.
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto,randomUUID} from 'node:crypto';
import {CatalogueGenerationClientV211} from '../src/report-business-catalogue-generation-v211.mjs';
import {Storage} from './report-catalogue-allocation-v213-fixture.mjs';

function fixture(){
 const account={user_id:randomUUID(),workspace_id:randomUUID(),role:'operator'},producer={id:randomUUID(),revision:3},storage=new Storage(),state={rows:[],fail:false,posts:[]};
 const request=async(path,options={})=>{if(options.method==='POST'){state.posts.push({path,options});throw Error('No paid operation is allowed in guard tests');}if(path==='/me')return structuredClone(account);if(path.startsWith('/setup/report-saved-map-designs?')){if(state.fail)throw Object.assign(Error('读取完整库存失败'),{status:503});return {schema:'report-saved-map-design-list-v178',producer_id:producer.id,items:structuredClone(state.rows)};}throw Error('Unexpected guard-only request '+path);};
 const c=new CatalogueGenerationClientV211({account,producer,request,storage,crypto:webcrypto,locks:{request:(_k,_o,fn)=>fn()},models:[],currentAvailable:()=>true});
 const base=()=>({schema:'report-saved-map-design-view-v176',id:randomUUID(),producer_id:producer.id,status:'failed'});
 const catalogue=()=>({...base(),version:2,revision:3,model_id:randomUUID(),job_id:randomUUID(),activity_id:randomUUID(),generation_profile:'catalogue-v211',source_snapshot_hash:'a'.repeat(64),preview_hash:'b'.repeat(64),note:'已保留的旧活动',error:'原失败保留',result:null,manual_value:null,call_count_limit:1,reserved_call_id:null,catalogue:null,semantic_quality_verified:false,can_apply:false,can_publish:false});
 return {c,state,base,catalogue};
}

test('generation write guard requires successful complete inventory, while protected leave remains possible',async()=>{
 const {c,state}=fixture();state.fail=true;await c.open();assert.equal(c.activitiesLoaded,false);assert.equal(c.guard(),true,'protected local form is not trapped on a read failure');assert.equal(c.guard({write:true}),false);
 state.fail=false;await c.refresh();assert.equal(c.activitiesLoaded,true);assert.equal(c.guard({write:true}),true);assert.equal(state.posts.length,0);
});

test('a refresh failure preserves old activities but never preserves their old write permission',async()=>{
 const {c,state,catalogue}=fixture();state.rows=[catalogue()];await c.open();assert.equal(c.items.length,1);assert.equal(c.guard({write:true}),true);const original=structuredClone(c.items);
 state.fail=true;await assert.rejects(()=>c.refresh(),/库存失败/);assert.deepEqual(c.items,original);assert.equal(c.activitiesLoaded,true);assert.equal(c.guard(),true);assert.equal(c.guard({write:true}),false);assert.equal(state.posts.length,0);
});

for(const profile of ['catalogue-allocation-v213','indexed-v179',null])for(const status of ['queued','running','response_unknown'])test(`registered ${profile||'legacy'} ${status} blocks sibling and own new paid activity`,async()=>{
 const {c,state,base}=fixture();const row={...base(),status,...(profile?{generation_profile:profile}:{})};state.rows=[row];await c.open();assert.equal(c.guard(),true,'background work is durable, so leaving is distinct from spending');assert.equal(c.guard({write:true}),false);assert.equal(await c.prepare(),false);assert.match(c.error,/已有活动/);assert.equal(state.posts.length,0);
});

test('an unknown family cannot be used as an empty or fresh write inventory',async()=>{
 const {c,state,base}=fixture();state.rows=[{...base(),generation_profile:'unregistered-v999'}];await c.open();assert.equal(c.activitiesLoaded,false);assert.equal(c.guard({write:true}),false);assert.equal(state.posts.length,0);
});
