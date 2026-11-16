import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

export const runtimeSource=await readFile(new URL('../../src/RuntimeSetup.jsx',import.meta.url),'utf8');

// Extract the current component function rather than maintaining a copy of it.
export function runtimeFunctionSource(name,source=runtimeSource){
 assert.match(name,/^[A-Za-z_$][\w$]*$/);
 const match=new RegExp('^ (?:async )?function '+name+'\\(', 'm').exec(source);
 assert.ok(match,'actual RuntimeSetup function '+name);
 const begin=match.index,open=source.indexOf('{',begin);
 let depth=0,quote=null,lineComment=false,blockComment=false;
 for(let i=open;i<source.length;i++){
  const c=source[i],next=source[i+1];
  if(lineComment){if(c==='\n')lineComment=false;continue;}
  if(blockComment){if(c==='*'&&next==='/'){blockComment=false;i++;}continue;}
  if(quote){if(c==='\\'){i++;continue;}if(c===quote)quote=null;continue;}
  if(c==='/'&&next==='/'){lineComment=true;i++;continue;}
  if(c==='/'&&next==='*'){blockComment=true;i++;continue;}
  if(c==='"'||c==="'"||c==='`'){quote=c;continue;}
  if(c==='{')depth++;
  if(c==='}'&&!--depth)return source.slice(begin,i+1);
 }
 throw Error('RuntimeSetup function boundary '+name);
}
