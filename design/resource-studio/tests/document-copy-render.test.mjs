import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToString} from 'react-dom/server';
import {build} from 'vite';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
import react from '@vitejs/plugin-react';
test('conversion first render is safe and saved PDF remains explicitly gated before full-page review',async()=>{
 const previous=globalThis.sessionStorage;globalThis.sessionStorage={getItem:()=>null};await mkdir(resolve('.m62-validation'),{recursive:true});const dir=await mkdtemp(resolve('.m62-validation/.document-copy-render-'));
 try{const output=await build({configFile:false,plugins:[react()],logLevel:'silent',build:{ssr:resolve('src/DocumentCopyReview.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true}}}});await writeFile(resolve(dir,'component.mjs'),output.output.find(item=>item.type==='chunk'&&item.isEntry).code);const {default:Review,DocumentCopyResult}=await import(pathToFileURL(resolve(dir,'component.mjs')).href),Btn=({children,disabled})=>React.createElement('button',{disabled},children),Field=({children,label})=>React.createElement('label',{},label,children);for(const role of ['operator','viewer']){const html=renderToString(React.createElement(Review,{materialID:'source',account:{user_id:'person-a',username:'operator',workspace_id:'workspace-a',role},Btn,Field}));assert.match(html,/正在核对原件/);assert.doesNotMatch(html,/href="blob:/);if(role==='viewer')assert.match(html,/当前账号只读/);}
 const row={status:'succeeded',source_material_version:3,receipt:{page_count:8,warnings:['字体需核对'],pdf_sha256:'a'.repeat(64)}},html=renderToString(React.createElement(DocumentCopyResult,{row,copy:{name:'副本.pdf'},Btn}));assert.match(html,/副本显示保留的隐藏文字及脚注/);assert.match(html,/尚不能直接供云端理解/);assert.match(html,/必须识别并核对全部页面/);assert.match(html,/button disabled="">开始逐页识别与核对/);assert.doesNotMatch(html,/<a /);const verified=renderToString(React.createElement(DocumentCopyResult,{row,copy:{name:'副本.pdf'},verified:{source:{url:'blob:source',name:'原.docx'},copy:{url:'blob:copy',name:'副本.pdf'}},Btn}));assert.match(verified,/href="blob:source"/);assert.match(verified,/href="blob:copy"/);assert.doesNotMatch(verified,/button disabled="">开始逐页识别与核对/);
 }finally{await rm(dir,{recursive:true,force:true});if(previous===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=previous;}
});
