
import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToString} from 'react-dom/server';
import {build} from 'vite';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
import react from '@vitejs/plugin-react';

test('OCR component first render is safe before any run, unit, or cached draft exists',async()=>{
 const previousStorage=globalThis.sessionStorage;
 globalThis.sessionStorage={getItem:()=>null,setItem(){},removeItem(){}};
 await mkdir(resolve('.m62-validation'),{recursive:true});const dir=await mkdtemp(resolve('.m62-validation/.ocr-render-regression-'));
 const output=await build({configFile:false,plugins:[react()],logLevel:'silent',build:{ssr:resolve('src/MaterialOCRReview.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true}}}});
 const chunk=output.output.find(item=>item.type==='chunk'&&item.isEntry);await writeFile(resolve(dir,'component.mjs'),chunk.code);
 try {
  const {default:MaterialOCRReview,OCRRegionCrop}=await import(pathToFileURL(resolve(dir,'component.mjs')).href);
  const Btn=({children,disabled})=>React.createElement('button',{disabled},children);
  const Field=({children,label})=>React.createElement('label',{},label,children);
  const crop=renderToString(React.createElement(OCRRegionCrop,{image:{url:'blob:verified-image',width:100,height:80,unitID:'unit-a'},region:{id:'r',box:[20,10,30,20]},index:3,Btn}));
  assert.match(crop,/viewBox="8 0 54 42"/);assert.match(crop,/href="blob:verified-image"/);assert.match(crop,/第 3 处文字/);assert.match(crop,/width="100" height="80"/);
  assert.equal(renderToString(React.createElement(OCRRegionCrop,{image:null,region:null,Btn})), '');
  for(const role of ['admin','operator','viewer']){
   const html=renderToString(React.createElement(MaterialOCRReview,{materialID:'material-no-run',account:{workspace_id:'workspace-a',username:'person-a',role},refreshToken:0,Btn,Field,onClose(){}}));
   assert.match(html,/正在读取服务器资料与核对进度/);
   assert.doesNotMatch(html,/<svg/);
   if(role==='viewer')assert.match(html,/当前账号只读/);
  }
 } finally {await rm(dir,{recursive:true,force:true});if(previousStorage===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=previousStorage;}
});
