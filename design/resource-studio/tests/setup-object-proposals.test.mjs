import {test} from 'node:test';
import assert from 'node:assert/strict';
import {configuredObjects,objectProposals,objectChoices,reviewSelection,selectionBody,canApplySelection,schemaObjects,changeObjectField,originChanges,suggestedUnitText,detachObjectOrigin,canReviewUnderstanding,renameObjectDefinition,citationSourceMatches,understandingStartKey,startPending,readStartPending,writeStartPending,clearStartPending,sameStartPending,savedMaterialUpload,materialUploadCounts,numericObjectField,fieldUnit} from '../src/setup-object-proposals.mjs';
const field={label:'高度',kind:'数值',required:true,unit:'m',citations:[{quote:'高度（m）'}],inference_reason:''};
const object={name:'样地',fields:[field],citations:[],inference_reason:'根据业务说明建议登记'};
const record={contract_version:2,status:'review',result:{objects:[object],businesses:[]}};
test('paid v1 object names stay legacy and are never fabricated into grounded fields',()=>{
 const legacy={status:'review',result:{objects:['样地']}};assert.deepEqual(objectProposals(legacy),[]);assert.deepEqual(objectProposals({...legacy,result:{objects:[object]}}),[]);
 assert.deepEqual(selectionBody(legacy,{names:['巡查'],object_names:['样地'],accept_domain:true}),{names:['巡查'],accept_domain:true});
});
test('v2 objects can be applied independently and accepting a domain never selects objects',()=>{
 const selection=reviewSelection({accept_domain:true});assert.deepEqual(selection.object_names,[]);assert.equal(canApplySelection(record,{},selection),true);assert.deepEqual(selectionBody(record,selection),{names:[],object_names:[],accept_domain:true});
 const objectsOnly={names:[],object_names:['样地'],accept_domain:false};assert.equal(canApplySelection(record,{},objectsOnly),true);assert.deepEqual(selectionBody(record,objectsOnly),objectsOnly);
});
test('manual objects and legacy object candidates both block same-name proposals',()=>{
 for(const workspace of [{instanceObjects:[{name:'样地',fields:[{label:'手工字段',kind:'文本',required:false}]}]},{instanceProfile:{objectCandidates:['样地']}}]){
  assert.equal(objectChoices(record,workspace)[0].existing,true);assert.equal(canApplySelection(record,workspace,{object_names:['样地']}),false);
 }
 assert.equal(objectChoices(record,{instanceObjects:[],instanceProfile:{objectCandidates:['样地']}})[0].existing,false);
});
test('review selections restore business and object choices independently',()=>{
 const pending={names:['巡查'],object_names:['样地'],accept_domain:false,pending:true};assert.deepEqual(reviewSelection(JSON.parse(JSON.stringify(pending))),{names:['巡查'],object_names:['样地'],accept_domain:false});
 assert.deepEqual(reviewSelection({names:['旧业务'],accept_domain:true}),{names:['旧业务'],object_names:[],accept_domain:true});
});
test('instance schema fields contain only the four confirmed keys and never inline citations',()=>{
 assert.deepEqual(schemaObjects([object]),[{name:'样地',fields:[{label:'高度',kind:'数值',required:true,unit:'m'}]}]);assert.equal(field.citations.length,1);
 assert.deepEqual(configuredObjects({instanceProfile:{objectCandidates:['样地','样地']}}),[{name:'样地',fields:[{label:'名称',kind:'文本',required:true,unit:null}]}]);
});
test('numeric units remain unknown when absent; switching to text removes the old unit',()=>{
 assert.equal(suggestedUnitText({...field,unit:null}),'原文未明确，保持未知');assert.equal(suggestedUnitText({...field,kind:'文本',unit:null}),'不适用');
 assert.equal(changeObjectField(field,'unit','').unit,null);assert.equal(changeObjectField(field,'kind','文本').unit,null);assert.equal(changeObjectField(field,'unit','cm').unit,'cm');assert.equal(field.unit,'m');
});
test('manual edits are distinguished from cited suggestions without claiming cited verification',()=>{
 assert.deepEqual(originChanges(schemaObjects([object])[0],object),[]);
 const current={name:'人工样地',fields:[{label:'高度',kind:'整数',required:false,unit:'cm'},{label:'备注',kind:'文本',required:false,unit:null}]};
 const changes=originChanges(current,object);assert.equal(changes.length,5);for(const word of ['对象名称','类型','必填','单位','已新增'])assert.ok(changes.some(change=>change.includes(word)));
 assert.match(originChanges({name:'样地',fields:[]},object)[0],/已移除或改名/);
});

test('unit editing preserves spaces needed to type compound units without conversion',()=>{
 const first=changeObjectField(field,'unit','kg '),next=changeObjectField(first,'unit',first.unit+'m-2');assert.equal(first.unit,'kg ');assert.equal(next.unit,'kg m-2');
 assert.deepEqual(originChanges({name:'样地',fields:[{...field,unit:'m '}]},object),[]);
});
test('detaching one origin preserves current manual fields, other origins and analysis history',()=>{
 const origin={analysis_id:'analysis',result_hash:'hash',object},other={analysis_id:'other',object:{name:'设备',fields:[]}};
 const workspace={instanceObjects:[{name:'样地',fields:[{label:'人工字段',kind:'文本',required:true,unit:null}]}],instanceObjectOrigins:{样地:origin,设备:other},history:['原配置']};
 const next=detachObjectOrigin(workspace,'样地');assert.deepEqual(next.instanceObjects,workspace.instanceObjects);assert.deepEqual(next.history,workspace.history);assert.deepEqual(next.instanceObjectOrigins,{设备:other});assert.equal(workspace.instanceObjectOrigins.样地,origin);
});

test('viewer may read saved suggestions but cannot perform review or configuration writes',()=>{assert.equal(canReviewUnderstanding({role:'viewer'}),false);assert.equal(canReviewUnderstanding({role:'admin'}),true);assert.equal(canReviewUnderstanding({role:'operator'}),true);assert.equal(canReviewUnderstanding(),true);});

test('rename is a manual definition and cannot silently reattach an old origin when its name is reused',()=>{
 const original={instanceObjects:[object],instanceObjectOrigins:{样地:{analysis_id:'saved',object}},history:['原已保存草稿']};
 const renamed=renameObjectDefinition(original,0,'人工样地');assert.deepEqual(renamed.instanceObjectOrigins,{});assert.deepEqual(renamed.history,original.history);assert.ok(original.instanceObjectOrigins.样地);
 const reused=renameObjectDefinition({...renamed,instanceObjects:[{name:'',fields:[]}],instanceObjectOrigins:{样地:original.instanceObjectOrigins.样地}},0,'样地');assert.deepEqual(reused.instanceObjectOrigins,{});
});
test('old quote may be read as history but changed, archived or unparsed blocks are not shown as its source',()=>{
 const citation={material_id:'material',block_id:'block',quote:'高度（m）'},material={id:'material',status:'parsed',archived:false,blocks:[{id:'block',text:'字段：高度（m）；保持原单位。'}]};
 assert.equal(citationSourceMatches(material,citation),true);for(const changed of [{...material,archived:true},{...material,status:'parsing'},{...material,blocks:[{id:'block',text:'新字段：温度'}]},{...material,id:'other'}])assert.equal(citationSourceMatches(changed,citation),false);
});

const pendingPreview=()=>({body:{revision:4,model_id:'model',material_ids:['material'],instruction:'核对说明',contract_version:2,request_id:'00000000-0000-4000-8000-000000000001'},preview_hash:'a'.repeat(64),chunks:[[{text:'private original is never cached'}]],context:{profile:{name:'not cached'}}});
const pendingStorage=()=>{const entries=new Map();return {getItem:key=>entries.get(key)||null,setItem:(key,value)=>entries.set(key,value),removeItem:key=>entries.delete(key)};};
test('start recovery is isolated by account and workspace; unresolved scope cannot authorize',()=>{
 const first=understandingStartKey({username:'operator',workspace_id:'one'});assert.notEqual(first,understandingStartKey({username:'operator',workspace_id:'two'}));assert.notEqual(first,understandingStartKey({username:'other',workspace_id:'one'}));assert.equal(understandingStartKey(),null);assert.throws(()=>writeStartPending(pendingStorage(),null,startPending(pendingPreview())),/账号和工作区/);
});
test('pending startup stores only frozen request body and hash, excluding source blocks and preview context',()=>{
 const preview=pendingPreview(),pending=startPending(preview),storage=pendingStorage();writeStartPending(storage,'key',pending);preview.body.material_ids.push('later');const recovered=readStartPending(storage,'key');assert.deepEqual(recovered.body.material_ids,['material']);assert.deepEqual(Object.keys(recovered).sort(),['body','preview_hash']);assert.ok(!JSON.stringify(recovered).includes('private original'));assert.equal(recovered.body.request_id,pending.body.request_id);
});
test('unknown startup is kept with the original nonce and same-body retry never replaces another request',()=>{
 const storage=pendingStorage(),pending=startPending(pendingPreview());writeStartPending(storage,'key',pending);writeStartPending(storage,'key',JSON.parse(JSON.stringify(pending)));assert.equal(sameStartPending(readStartPending(storage,'key'),pending),true);
 const different=startPending({...pendingPreview(),preview_hash:'b'.repeat(64)});assert.throws(()=>writeStartPending(storage,'key',different),/另一个页面/);assert.equal(sameStartPending(readStartPending(storage,'key'),pending),true);assert.equal(clearStartPending(storage,'key',different),false);assert.equal(clearStartPending(storage,'key',pending),true);assert.equal(readStartPending(storage,'key'),null);
});
test('storage failure prevents startup journaling, corruption remains blocked and is never silently discarded',()=>{
 const pending=startPending(pendingPreview());assert.throws(()=>writeStartPending({getItem:()=>null,setItem(){throw Error('quota');}},'key',pending),/无法保存/);
 const storage=pendingStorage();storage.setItem('key',JSON.stringify({...pending,body:{...pending.body,contract_version:1}}));assert.throws(()=>readStartPending(storage,'key'),/无法核对/);assert.throws(()=>writeStartPending(storage,'key',pending),/无法核对/);assert.notEqual(storage.getItem('key'),null);
});
test('pending recovery rejects complete preview data inside request body and requires the fixed v2 UUID',()=>{
 const preview=pendingPreview();for(const body of [{...preview.body,request_id:'new-id'},{...preview.body,context:{raw:'no'}},{...preview.body,contract_version:1}])assert.throws(()=>startPending({...preview,body}),/无法核对/);
});

test('malformed or inaccessible startup cache stays blocked with a readable recovery reason',()=>{const storage=pendingStorage();storage.setItem('key','broken{');assert.throws(()=>readStartPending(storage,'key'),/恢复记录已损坏/);assert.equal(storage.getItem('key'),'broken{');assert.throws(()=>readStartPending({getItem(){throw Error('SecurityError');}},'key'),/暂不能读取/);});

test('upload recovery clears the prior failure without losing the selected file or changing other results',()=>{
 const file={name:'report.docx'},failed={file,name:file.name,error:true,state:'connection failed'};
 const saved=savedMaterialUpload(failed,'saved');assert.equal(saved.error,false);assert.equal(saved.file,file);assert.equal(saved.name,failed.name);assert.equal(failed.error,true);
 assert.deepEqual(materialUploadCounts([saved,{error:true}],3),{saved:1,failed:1,unprocessed:1});assert.deepEqual(materialUploadCounts([saved],1),{saved:1,failed:0,unprocessed:0});assert.deepEqual(materialUploadCounts([],2),{saved:0,failed:0,unprocessed:2});
});
test('runtime numeric field units are read from declared metadata without guessing or converting values',()=>{
 for(const kind of ['数值','整数']){assert.equal(numericObjectField(kind),true);assert.equal(fieldUnit({kind,unit:'℃'}),'℃');assert.equal(fieldUnit({kind,unit:'kg m-2'}),'kg m-2');for(const unit of [null,undefined,'','  ',40])assert.equal(fieldUnit({kind,unit}),null);}
 for(const kind of ['文本','是否','日期',undefined]){assert.equal(numericObjectField(kind),false);assert.equal(fieldUnit({kind,unit:'℃'}),null);}
 const field={kind:'数值',unit:'m',value:125};assert.equal(fieldUnit(field),'m');assert.equal(field.value,125);
});
