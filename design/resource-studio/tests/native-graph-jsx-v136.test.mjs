import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {transformWithEsbuild} from 'vite';

// Parse the actual edited JSX with the installed production compiler. These
// are syntax checks, not a mounted UI, browser, backend or persistence proof.
for(const name of ['FullSemanticGraphEditor','NativeGraphSourcesPanelV136','NativeReceiptStudio','RuntimeResources','BusinessWorkspace']){
 test('actual V136 '+name+' JSX parses with esbuild',async()=>{
  const path=fileURLToPath(new URL('../src/'+name+'.jsx',import.meta.url)),source=await readFile(path,'utf8');
  const output=await transformWithEsbuild(source,path,{loader:'jsx',jsx:'transform',sourcemap:false});
  assert.equal(typeof output.code,'string');assert.ok(output.code.length>0);
 });
}
test('JSX compiler rejects an extra parenthesis in a nested source-family branch',async()=>{
 await assert.rejects(()=>transformWithEsbuild('const View=()=> <p>{native?"本地结果":"处理结果"+(candidate?"候选":"保存"))}</p>;','synthetic-invalid-v136.jsx',{loader:'jsx',jsx:'transform',sourcemap:false}));
});
