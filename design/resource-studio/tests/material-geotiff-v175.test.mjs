// Presentation/static seam only; not real browser or GDAL evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {ocrMaterialPresentation,newerOCRMaterials,readOCRMaterialStatus} from '../src/material-ocr-contract.mjs';
import {understandingMaterialEligibility,defaultUnderstandingMaterialIDs} from '../src/understanding-materials.mjs';

test('resource-ready raster is recognized imagery without asserting text/OCR confirmation',()=>{
 const material={status:'resource_ready',resource_kind:'geospatial_raster',source_kind:'geotiff_metadata_v175',blocks:[],block_count:0,ocr:null,warnings:['这是观测资源，不能作为业务需求正文；后续通过遥感资源接入分析。']};
 const view=ocrMaterialPresentation(material);
 assert.equal(view.statusLabel,'已识别影像');assert.equal(view.confirmed,false);
 assert.equal(view.currentOCRReason,null);assert.deepEqual(view.currentWarnings,material.warnings);
});
test('document parsed and unsupported states keep their existing presentation',()=>{
 assert.equal(ocrMaterialPresentation({status:'parsed'}).statusLabel,'正文解析完成');
 assert.equal(ocrMaterialPresentation({status:'needs_input'}).statusLabel,'需要补充解析能力');
});
test('real resource card distinguishes metadata from document text-block counts',async()=>{
 const source=await readFile(new URL('../src/RuntimeSetup.jsx',import.meta.url),'utf8');
 assert.match(source,/m\.resource_kind==='geospatial_raster'\?' · 影像元数据'/);
 assert.match(source,/current\.currentWarnings/);
});

test('authoritative resource state survives refresh and cannot regress to a stale configuration row',async()=>{
 const ready={id:'raster-resource',version:5,status:'resource_ready',resource_kind:'geospatial_raster',source_kind:'geotiff_metadata_v175',block_count:0,ocr:null,geospatial_summary:{crs:'EPSG:32650',observed_at:null}},old={...ready,version:2,status:'needs_input',resource_kind:undefined};
 const restored=await readOCRMaterialStatus(async path=>{assert.equal(path,'/setup/materials?limit=500');return [ready];},()=>true);
 assert.deepEqual(restored,[ready]);assert.deepEqual(newerOCRMaterials([ready],[old]),[ready]);
 assert.equal(ocrMaterialPresentation(restored[0]).statusLabel,'已识别影像');
 assert.equal(await readOCRMaterialStatus(async()=>[ready],()=>false),null,'late response for another scope is not adopted');
 const source=await readFile(new URL('../src/RuntimeSetup.jsx',import.meta.url),'utf8');
 const restore=source.slice(source.indexOf('async function acceptRestore('),source.indexOf('async function acceptRestore(')+600);
 assert.match(restore,/refreshMaterials\(accountRef.current,true\)/);
 assert.equal(restore.includes('setMaterials(saved.configuration.materials'),false);
});

test('resource errors remain visible and metadata cannot become a business document source',()=>{
 const material={id:'raster',version:3,status:'resource_ready',resource_kind:'geospatial_raster',block_count:0,ocr:null,error:'原件状态需要核对',warnings:['观测资源，分析请接遥感资源']};
 const view=ocrMaterialPresentation(material);assert.equal(view.confirmed,false);assert.equal(view.currentError,material.error);assert.deepEqual(view.currentWarnings,material.warnings);
 assert.equal(understandingMaterialEligibility(material).eligible,false);
 assert.deepEqual(defaultUnderstandingMaterialIDs([material]),[]);
});
