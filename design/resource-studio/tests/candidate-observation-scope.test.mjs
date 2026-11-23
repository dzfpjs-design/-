import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';

test('对象字段适用范围只显示经过匹配的类型并说明运行时限制', async()=>{
 const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
 const built=await build({entryPoints:['src/CandidateObservationScopeSummary.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'}),module={exports:{}};
 new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
 const Component=module.exports.default,render=scope=>renderToStaticMarkup(React.createElement(Component,{scope}));
 const matched=render({status:'matched',object_names:['可用对象'],check:'requested_observation_field_presence_only'});
 assert.match(matched,/可用对象/);assert.match(matched,/类型、单位和实际观测能力/);assert.doesNotMatch(matched,/所有对象/);
 assert.match(render({status:'pending',object_names:[],check:'requested_observation_field_presence_only'}),/待核/);
 assert.match(render({status:'matched',object_names:[],check:'requested_observation_field_presence_only'}),/无法核对/);
 assert.equal(render(null),'');
});
