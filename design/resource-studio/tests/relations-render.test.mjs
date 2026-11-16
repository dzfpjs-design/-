import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import {emptyRelationScope} from '../src/relation-contract.mjs';
test('real relationship controls and source evidence stay human readable beside their original goal',async()=>{
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 try{
  const {default:Editor,NativeRelationResult,RelationSources}=await server.ssrLoadModule('/src/NativeRelationEditor.jsx');
  const {default:Types,RelationTypeSummary}=await server.ssrLoadModule('/src/RelationTypesEditor.jsx');
  const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));
  const metadata={package_id:'hidden-package',entity_ids:['hidden-site','hidden-device'],entities:[{id:'hidden-site',type:'site',title:'林区甲'},{id:'hidden-device',type:'device',title:'设备乙'}],relation_types:[{id:'hidden-type',title:'安装设备',source_types:['site'],target_types:['device'],cardinality:'many'}],fields:{hidden_field:'安装情况'},field_units:{hidden_field:null}};
  const form={relationScope:{...emptyRelationScope(),enabled:true,types:['hidden-type'],roots:['hidden-site']}};
  const html=render(Editor,{form,onChange(){},metadata});for(const phrase of ['林区甲','设备乙','安装设备','所选任务对象是读取范围','最多沿几层关系核对','北京时间'])assert.ok(html.includes(phrase),phrase);assert.doesNotMatch(html.replace(/<[^>]*>/g,''),/hidden-package|hidden-type|hidden-site|hidden-device|hidden_field/);
  const malformed=render(Editor,{form:{relationScope:{enabled:true,types:'bad',roots:[]}},onChange(){},metadata});assert.match(malformed,/明确移除无法恢复的关系范围/);
  const docs=[{material_name:'安装验收单',quote:'设备乙安装于林区甲，已经现场核对。',version:2,evidence_id:'hidden-evidence'}];
  const edge={relation_type:'hidden-type',source_entity_id:'hidden-site',target_entity_id:'hidden-device',hop:1,version:2,statement:'设备乙安装于林区甲',valid_from:'2026-10-14T00:00:00Z',review:{reason:'与验收原件一致',at:'2026-10-14T00:02:00Z'},document_evidence:docs,evidence:[{kind:'observation',version:2,snapshot:{id:'hidden-record',observed_at:'2026-10-14T00:00:00Z',data:{evidence_ids:['hidden-source-evidence'],extracted:{attributes:{hidden_field:'现场逐一核对，位置相符'}}}}}]};
  const result=render(NativeRelationResult,{goal:{...metadata,goal:{relation_query:{relation_types:['hidden-type'],root_ids:['hidden-site'],direction:'outgoing',max_hops:1}},check:{evidence:{relations:{evaluated_at:'2026-10-14T00:03:00Z',relation_evidence:[edge],relation_gaps:[{reason:'另一条关系尚待确认'}],relation_conflicts:[]}}}}});
  for(const phrase of ['现场逐一核对，位置相符','安装验收单','设备乙安装于林区甲，已经现场核对。','与验收原件一致','另一条关系尚待确认','正式人工验收','查看登记来源原件 1'])assert.ok(result.includes(phrase),phrase);assert.doesNotMatch(result.replace(/<[^>]*>/g,''),/hidden-record|hidden-field|hidden-type|hidden-evidence|hidden-site/);
  const type={name:'安装设备',description:'人工补充现场登记限制',source_type:'林区',target_type:'设备',cardinality:'many',allow_self:false},original={...type,description:'资料原定义',citations:[{quote:'林区可安装设备'}]};
  const types=render(Types,{workspace:{instanceRelationTypes:[type],instanceRelationOrigins:{安装设备:{relationship:original}}},objects:[{name:'林区'},{name:'设备'}],onCommit(){},Btn:props=>React.createElement('button',props),Field:props=>React.createElement('label',null,props.label,props.children),Citation:props=>React.createElement('blockquote',null,props.citation.quote)});assert.match(types,/原建议已有人工调整/);assert.match(types,/林区可安装设备/);assert.match(types,/不能用于证明人工改值/);
  const historical=render(RelationTypeSummary,{types:[type],origins:{安装设备:{relationship:{...original,citations:[{quote:'此生成版本冻结的原关系出处'}]}}},Citation:props=>React.createElement('blockquote',null,props.citation.quote)});assert.match(historical,/此生成版本冻结的原关系出处/);assert.match(historical,/本版本关系定义已按原建议人工调整/);assert.match(historical,/不能证明改值/);
  assert.match(render(RelationSources,{snapshots:[],documents:[]}),/不能仅按端点名称或类型定义确认事实/);
 }finally{await server.close();}
});
