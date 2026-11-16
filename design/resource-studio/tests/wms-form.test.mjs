import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeWMSEndpoint,wmsWindow,wmsDimensions,sameWMSRead,savedWMSScope,readWMSDraft,wmsDraftKey,wmsRecordFieldsPresent,wmsSourceConfiguration} from '../src/wms-form.mjs';
test('WMS catalog URL removes only recognized parameters and rejects embedded credentials',()=>{
 assert.equal(normalizeWMSEndpoint('https://example.com/wms?service=WMS&request=GetCapabilities'),'https://example.com/wms');
 for(const value of ['https://user:pass@example.com/wms','https://example.com/wms?token=secret','https://example.com/wms?REQUEST=GetMap','https://example.com/wms?service=WMS&SERVICE=WMS'])assert.throws(()=>normalizeWMSEndpoint(value));
});
test('blank or invalid map bounds and fractional dimensions never become requests',()=>{
 assert.deepEqual(wmsWindow(['104','23','116','27'],'EPSG:4326'),[104,23,116,27]);
 for(const value of [['','23','116','27'],[116,23,104,27],[104,23,116,91]])assert.throws(()=>wmsWindow(value,'EPSG:4326'));
 for(const dims of [['','48'],[33.5,48],[64,2049]])assert.throws(()=>wmsDimensions(...dims));
});
test('uncertain read response must match the exact original connection, nonce and bounds',()=>{
 const pending={connection_id:'c',body:{request_id:'r',scope_id:'s',bbox:[104,23,116,27],width:64,height:48}};
 const row={connection_id:'c',request_id:'r',request:{scope_id:'s',bbox:[104,23,116,27],width:64,height:48}};
 assert.equal(sameWMSRead(row,pending),true);assert.equal(sameWMSRead({...row,connection_id:'other'},pending),false);
 assert.equal(sameWMSRead({...row,request:{...row.request,bbox:[104,23,117,27]}},pending),false);
});
test('approval recovery is bound to the original catalog and corrupted journal is retained',()=>{
 const body={layer:'layer',crs:'EPSG:4326',style:'',bbox:[104,23,116,27]},pending={connection_id:'c',catalog_sha256:'catalog',body};
 assert.ok(savedWMSScope({id:'c',catalog_sha256:'catalog',scopes:{s:{...body,id:'s'}}},pending));
 assert.equal(savedWMSScope({id:'c',catalog_sha256:'different',scopes:{s:body}},pending),null);
 assert.throws(()=>readWMSDraft({getItem:()=>'{bad'},'key'));assert.notEqual(wmsDraftKey('a','user'),wmsDraftKey('b','user'));
});
test('damaged saved map receipts remain visible for recovery without successful controls',()=>{
 const row={id:'read',status:'succeeded',connection_snapshot:{scope:{title:'map'}},request:{bbox:[104,23,116,27],width:64,height:48},scene_id:'scene',result:{transparent_pixels:128}};
 assert.equal(wmsRecordFieldsPresent(row),true);
 for(const damaged of [null,{...row,result:null},{...row,request:null},{...row,connection_snapshot:null},{...row,result:{transparent_pixels:5000}}])assert.equal(wmsRecordFieldsPresent(damaged),false);
 assert.equal(wmsRecordFieldsPresent({...row,status:'failed',result:null}),true);
});
test('publication retries reuse one resource while preserving other work and business changes',()=>{
 const scope={id:'scope',layer:'layer',title:'地图',crs:'EPSG:4326',style:'',bbox:[104,23,116,27]},pending={connection_id:'connection',catalog_sha256:'catalog',scope,body:{scope_id:'scope'},businesses:['影像核查']};
 const connection={id:'connection',name:'服务',catalog_sha256:'catalog',configuration:{endpoint:'https://example.com/wms',secret_ref:''},scopes:{scope},publications:{scope:{capability_id:'cap',manifest_hash:'hash',catalog_sha256:'catalog'}}};
 const initial={businesses:['影像核查'],connections:[{id:'other'}],resources:[{id:'other',name:'其它工作'}]};
 const first=wmsSourceConfiguration(initial,connection,pending),second=wmsSourceConfiguration(first,connection,pending);
 assert.deepEqual(first,second);assert.equal(first.resources[0].name,'其它工作');assert.equal(first.resources.length,2);
 assert.throws(()=>wmsSourceConfiguration({...initial,businesses:[]},connection,pending));
 assert.throws(()=>wmsSourceConfiguration(initial,{...connection,catalog_sha256:'changed'},pending));
 assert.deepEqual(initial.resources,[{id:'other',name:'其它工作'}]);
});
