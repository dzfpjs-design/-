// No fixtures, network, credentials or synthetic fallback. Root supplies capture.
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {pixelNeed as need,pixelScope,checkedPixelOptions,checkedPixelRun,checkedPixelList,checkedPixelOriginal,checkedPixelRecovery,checkedPixelCommandResult} from '../src/pixel-change-v249-protocol.mjs';
const exact=(v,keys)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join('|')===keys.sort().join('|');
need(process.argv.length===3,'Supply one explicit actual API capture path');const capture=JSON.parse(await readFile(process.argv[2],'utf8'));
need(exact(capture,['schema','account','instance_id','required_checks','checks'])&&capture.schema==='pixel-change-real-capture-v249'&&exact(capture.account,['user_id','workspace_id']));
const scope=pixelScope(capture.account,capture.instance_id),kinds=new Set(['options','run','list','create_recovery','command_result']);
need(Array.isArray(capture.required_checks)&&capture.required_checks.length>=5&&capture.required_checks.every(v=>typeof v==='string'&&v.length>0)&&new Set(capture.required_checks).size===capture.required_checks.length);
need(Array.isArray(capture.checks)&&capture.checks.length===capture.required_checks.length);const labels=new Set(),totals={};
for(const row of capture.checks){need(exact(row,['label','kind','original','value'])&&capture.required_checks.includes(row.label)&&!labels.has(row.label)&&kinds.has(row.kind));labels.add(row.label);if(['create_recovery','command_result'].includes(row.kind)){const p=await checkedPixelOriginal(row.original,scope,webcrypto);if(row.kind==='create_recovery'){need(p.operation==='create');await checkedPixelRecovery(row.value,scope,p,webcrypto);}else{need(p.operation!=='create');await checkedPixelCommandResult(row.value,scope,p,webcrypto);}}else{need(row.original===null);if(row.kind==='options')checkedPixelOptions(row.value,scope);else if(row.kind==='run')await checkedPixelRun(row.value,scope,webcrypto);else await checkedPixelList(row.value,scope,webcrypto);}totals[row.kind]=(totals[row.kind]||0)+1;}
need([...kinds].every(kind=>totals[kind]>0));console.log(JSON.stringify({schema:'pixel-change-real-check-v249',checked:capture.checks.length,types:totals,passed:true}));
