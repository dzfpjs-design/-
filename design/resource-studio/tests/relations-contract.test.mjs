import test from 'node:test';
import assert from 'node:assert/strict';
import {buildRelationQuery,emptyRelationScope,relationTypeProblems,renameObjectRelations,relatedObjectTypes,chinaInstant,chinaInput,persistRelationCache,readRelationCache,validateRelationSources} from '../src/relation-contract.mjs';
import {buildNativeGoal,defaultNativeGoal,taskGoalMetadata} from '../src/native-goal-contract.mjs';
const metadata={package_id:'fixed-package',entity_ids:['selected-root','selected-target'],entities:[{id:'selected-root',type:'site',title:'一号林区'},{id:'selected-target',type:'device',title:'设备甲'},{id:'outside',type:'site',title:'范围外林区'}],relation_types:[{id:'hosts',title:'配置设备',source_types:['site'],target_types:['device'],cardinality:'many',allow_self:false}],fields:{name:'名称'},field_kinds:{name:'string'}};
const form=()=>({...defaultNativeGoal('核对关联设备'),fields:['name'],relationScope:{...emptyRelationScope(),enabled:true,types:['hosts'],roots:['selected-root']}});
test('original task relation query stays bounded and composes with the original formal goal',()=>{
 const result=buildNativeGoal(form(),metadata);assert.deepEqual(result.relation_query,{package_id:'fixed-package',relation_types:['hosts'],root_ids:['selected-root'],direction:'outgoing',max_hops:1,include_roots:false,at:null});
 assert.equal(result.criteria[1].kind,'human_confirmation');assert.deepEqual(result.entity_ids,metadata.entity_ids);
 const legacy=form();delete legacy.relationScope;assert.equal(buildNativeGoal(legacy,metadata).relation_query,undefined);
});
test('scope changes, invalid types, wrong direction, missing metadata and malformed drafts block rather than omit relationship requirements',()=>{
 for(const scope of [{roots:['outside']},{types:['missing']},{hops:4},{hops:1.5},{direction:'both'},{roots:['selected-target']},{types:['hosts','hosts']},{roots:['selected-root','selected-root']},{includeRoots:'yes'}])assert.throws(()=>buildRelationQuery({...form(),relationScope:{...form().relationScope,...scope}},metadata));
 for(const scope of [null,'broken',{enabled:'yes'}])assert.throws(()=>buildRelationQuery({relationScope:scope},metadata),/草稿/);
 assert.throws(()=>buildRelationQuery(form(),{...metadata,relation_types_error:'清单失效'}),/原草稿保留/);
 assert.throws(()=>buildRelationQuery(form(),{...metadata,entities:[]}),/明确选择/);
 assert.equal(buildRelationQuery({},{}),null);
 const reversed={...form(),relationScope:{...form().relationScope,direction:'incoming',roots:['selected-target'],hops:3,includeRoots:true,at:'2026-10-14T09:30'}};
 assert.equal(buildRelationQuery(reversed,metadata).at,'2026-10-14T01:30:00.000Z');
});
test('China times validate real calendar dates without requiring timezone setup',()=>{
 assert.equal(chinaInput(chinaInstant('2026-10-14T09:30')),'2026-10-14T09:30');
 for(const value of ['2026-02-30T08:00','2026-10-14T25:00','2026-10-14','2026-10-14T09:30Z'])assert.throws(()=>chinaInstant(value));
});
test('object rename preserves relation endpoints, while unresolved cardinality and removed endpoints prevent generation',()=>{
 const relation={name:'配置设备',description:'按登记资料定义',source_type:'林区',target_type:'设备',cardinality:'unspecified',allow_self:false},workspace={instanceRelationTypes:[relation]};
 assert.match(relationTypeProblems(workspace,[{name:'林区'},{name:'设备'}]).join(''),/数量限制尚未核对/);
 const renamed=renameObjectRelations(workspace,'林区','巡护区域');assert.equal(relatedObjectTypes(renamed,'林区').length,0);assert.equal(renamed.instanceRelationTypes[0].source_type,'巡护区域');assert.equal(workspace.instanceRelationTypes[0].source_type,'林区');
 assert.equal(renameObjectRelations({instanceRelationTypes:[{source_type:'',target_type:''}]},'','新对象').instanceRelationTypes[0].source_type,'');
 renamed.instanceRelationTypes[0].cardinality='many';assert.deepEqual(relationTypeProblems(renamed,[{name:'巡护区域'},{name:'设备'}]),[]);assert.match(relationTypeProblems(renamed,[{name:'巡护区域'}]).join(''),/已变化/);
});
test('task creation metadata retains fixed relation data while restricting scope to selected objects',()=>{
 const result=taskGoalMetadata([{key:'site',fields:[{key:'name',label:'名称',kind:'文本'}]}],[{id:'selected-root',entity_type:'site'}],metadata);assert.deepEqual(result.entity_ids,['selected-root']);assert.deepEqual(result.entities,metadata.entities);assert.deepEqual(result.relation_types,metadata.relation_types);assert.equal(result.package_id,'fixed-package');
});
test('relationship recovery journal is read back before sending and corrupt records are preserved',()=>{
 const values=new Map(),storage={setItem:(key,value)=>values.set(key,value),getItem:key=>values.get(key)||null};
 const saved={form:{editor:{},reasons:{}},dirty:true,pending:{operation:'create',body:{request_id:'00000000-0000-4000-8000-000000000001',expected_version:1,release_id:'release-a',statement:'人工说明'}}};persistRelationCache(storage,'scope',saved);assert.deepEqual(readRelationCache(storage,'scope'),saved);
 assert.throws(()=>persistRelationCache({...storage,getItem:()=>null},'blocked',saved),/已阻止发送/);values.set('broken','{bad');assert.throws(()=>readRelationCache(storage,'broken'),/原记录保留/);assert.equal(values.get('broken'),'{bad');
});

test('long actual source blocks allow explicit exact excerpts while stale or excessive selections remain blocked',()=>{
 const source='场景甲包含档案乙。'+'完整原段落'.repeat(1500),catalog={evidence_options:[{kind:'document',material_id:'material',blocks:[{block_id:'block',quote:source}]}]},editor={refs:[],documents:[{material_id:'material',block_id:'block',quote:source}]};
 assert.throws(()=>validateRelationSources(editor,catalog),/6000/);const excerpt={...editor,documents:[{...editor.documents[0],quote:'场景甲包含档案乙。'}]};assert.doesNotThrow(()=>validateRelationSources(excerpt,catalog));assert.equal(editor.documents[0].quote,source);
 assert.throws(()=>validateRelationSources({...excerpt,documents:[{...excerpt.documents[0],quote:'重新编写的事实'}]},catalog),/逐字/);assert.throws(()=>validateRelationSources(excerpt,{evidence_options:[]}),/来源已变化/);assert.throws(()=>validateRelationSources({...excerpt,documents:Array(11).fill(excerpt.documents[0])},catalog),/最多选择10条/);
});
