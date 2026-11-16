// Preserve the server's Python JSON number spelling without guessing floats.
import {freshNumber,numericValue,numericToken,isTypedNumber,cloneTyped} from './typed-numbers-v132.mjs';
const SCHEMA='report-full-semantic-graph-canonical-wire-v1';
export function graphWireFamilyPath(handoffId,family='v3',kind='root'){
 const suffix={v3:'',v4:'/v4','objects-v171':'/objects-v171'};
 if(typeof handoffId!=='string'||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(handoffId)||!Object.hasOwn(suffix,family)||!['root','revision'].includes(kind))throw Error('完整执行版本或请求路径无法核对，原请求保留。');
 return '/setup/full-semantic-graph-wire/'+handoffId+suffix[family]+(kind==='revision'?'/revisions':'');
}
const MAX_BYTES=24*1024*1024,MAX_DEPTH=128,MAX_VALUES=1000000;
const metadata=new WeakMap();
// Editable copies retain only number spelling, never server/hash authority.
const editableNumbers=new WeakMap();
const pointer=value=>String(value).replaceAll('~','~0').replaceAll('/','~1');
const object=value=>value!==null&&typeof value==='object';
const need=(value,message='完整流程的无损响应无法核对，原输入与请求保留。')=>{if(!value)throw Error(message);};
const order=(a,b)=>{const x=[...a],y=[...b];for(let i=0;i<Math.min(x.length,y.length);i++){const n=x[i].codePointAt(0)-y[i].codePointAt(0);if(n)return n;}return x.length-y.length;};
const safeString=value=>typeof value==='string'&&!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value);

function properties(value){
 need(object(value)&&Object.getOwnPropertySymbols(value).length===0);
 const array=Array.isArray(value),prototype=Object.getPrototypeOf(value);
 need(array?prototype===Array.prototype:prototype===Object.prototype||prototype===null);
 const descriptors=Object.getOwnPropertyDescriptors(value),names=Object.keys(descriptors);
 if(array){
  need(Number.isSafeInteger(value.length)&&names.length===value.length+1&&names.includes('length'));
  for(let i=0;i<value.length;i++)need(Object.hasOwn(descriptors,String(i)));
 }
 const result=[];
 for(const name of names){
  if(array&&name==='length')continue;
  const descriptor=descriptors[name];
  need(safeString(name)&&descriptor.enumerable&&Object.hasOwn(descriptor,'value'));
  result.push([name,descriptor.value]);
 }
 return result;
}

class Reader{
 constructor(text){this.text=text;this.at=0;this.count=0;this.number=/-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y;}
 string(){
  const start=this.at++;need(this.text[start]==='"');let closed=false;
  while(this.at<this.text.length){
   const char=this.text[this.at++];
   if(char==='"'){closed=true;break;}
   need(char.charCodeAt(0)>=32);
   if(char==='\\')this.at++;
  }
  need(closed);const raw=this.text.slice(start,this.at);let value;
  try{value=JSON.parse(raw);}catch{need(false);}
  need(safeString(value)&&JSON.stringify(value)===raw);
  return value;
 }
 value(depth=0){
  need(depth<=MAX_DEPTH&&++this.count<=MAX_VALUES);
  const start=this.at,char=this.text[this.at];let value,fields=null,numbers={};
  if(char==='{'){
   this.at++;value={};fields=[];const seen=new Set();let previous=null;
   if(this.text[this.at]!=='}')while(true){
    const fieldStart=this.at,key=this.string();
    need(!seen.has(key)&&(previous===null||order(previous,key)<0));seen.add(key);previous=key;
    need(this.text[this.at++]===':');const childStart=this.at,child=this.value(depth+1);
    if(typeof child==='number')Object.defineProperty(numbers,key,{value:this.text.slice(childStart,this.at),enumerable:true});
    Object.defineProperty(value,key,{value:child,enumerable:true,writable:true,configurable:true});
    fields.push({key,raw:this.text.slice(fieldStart,this.at)});
    if(this.text[this.at]!==',')break;this.at++;
   }
   need(this.text[this.at++]==='}');
  }else if(char==='['){
   this.at++;value=[];
   if(this.text[this.at]!==']')while(true){const childStart=this.at,child=this.value(depth+1);if(typeof child==='number')numbers[String(value.length)]=this.text.slice(childStart,this.at);value.push(child);if(this.text[this.at]!==',')break;this.at++;}
   need(this.text[this.at++]===']');
  }else if(char==='"')return this.string();
  else if(this.text.startsWith('true',this.at)){this.at+=4;return true;}
  else if(this.text.startsWith('false',this.at)){this.at+=5;return false;}
  else if(this.text.startsWith('null',this.at)){this.at+=4;return null;}
  else{
   this.number.lastIndex=this.at;const token=this.number.exec(this.text);need(token&&token.index===this.at);
   const raw=token[0];this.at=this.number.lastIndex;value=Number(raw);need(Number.isFinite(value));
   // An integer outside this range was rounded by JSON.parse and cannot be
   // compared in full. Finite float tokens retain their original server bytes.
   if(!/[.eE]/.test(raw))need(raw!=='-0'&&Number.isSafeInteger(value));
   return value;
  }
  const record={canonical:this.text.slice(start,this.at),fields,numbers};
  Object.freeze(value);metadata.set(value,record);return value;
 }
}

export function graphWireParseCanonical(text){
 need(typeof text==='string'&&new TextEncoder().encode(text).length<=MAX_BYTES);
 const reader=new Reader(text),value=reader.value();need(reader.at===text.length&&object(value));return value;
}

function numberAt(raw){const reader=new Reader(raw),value=reader.value();need(reader.at===raw.length&&typeof value==='number');return value;}
function numbersFor(value){return metadata.get(value)?.numbers||editableNumbers.get(value)||{};}
export function graphWireSetNumberToken(parent,key,raw){
 need(object(parent)&&!Object.isFrozen(parent)&&Object.hasOwn(parent,key)&&Object.is(parent[key],numberAt(raw)));
 const next={...numbersFor(parent)};Object.defineProperty(next,String(key),{value:raw,enumerable:true,writable:true,configurable:true});editableNumbers.set(parent,next);
}
export function graphWireNumericTokens(value){
 const result={},seen=new WeakSet();let count=0;
 const visit=(item,path,depth)=>{need(depth<=MAX_DEPTH&&++count<=MAX_VALUES);if(!object(item)||isTypedNumber(item))return;need(!seen.has(item));seen.add(item);const tokens=numbersFor(item);for(const [key,child]of properties(item)){const next=path+'/'+pointer(key),raw=tokens[key];if(typeof child==='number'&&typeof raw==='string'&&Object.is(child,numberAt(raw)))Object.defineProperty(result,next,{value:raw,enumerable:true,writable:true,configurable:true});else visit(child,next,depth+1);}seen.delete(item);};
 visit(value,'',0);return result;
}
export function graphWireRestoreNumericTokens(value,tokens,{matchingOnly=false}={}){
 need(object(tokens)&&!Array.isArray(tokens));
 for(const [path,raw]of properties(tokens)){need(path.startsWith('/')&&typeof raw==='string');const parts=path.slice(1).split('/').map(part=>{need(!/~(?![01])/u.test(part));return part.replaceAll('~1','/').replaceAll('~0','~');});let parent=value;for(const key of parts.slice(0,-1)){if(!object(parent)||!Object.hasOwn(parent,key)){parent=null;break;}parent=parent[key];}const key=parts.at(-1),number=numberAt(raw),matches=object(parent)&&Object.hasOwn(parent,key)&&typeof parent[key]==='number'&&(Object.is(parent[key],number)||!matchingOnly&&parent[key]===0&&number===0);if(matchingOnly&&!matches)continue;need(matches&&!Object.isFrozen(parent));parent[key]=number;graphWireSetNumberToken(parent,key,raw);}
 return value;
}
export function graphWireClone(value){
 const seen=new WeakSet();let count=0;
 const copy=(item,depth)=>{need(depth<=MAX_DEPTH&&++count<=MAX_VALUES);if(isTypedNumber(item)||!object(item))return cloneTyped(item);need(!seen.has(item));seen.add(item);const next=Array.isArray(item)?[]:{},tokens={};for(const [key,child]of properties(item)){Object.defineProperty(next,key,{value:copy(child,depth+1),enumerable:true,writable:true,configurable:true});const raw=numbersFor(item)[key];if(typeof child==='number'&&typeof raw==='string'&&Object.is(child,numberAt(raw)))Object.defineProperty(tokens,key,{value:raw,enumerable:true});}if(Object.keys(tokens).length)editableNumbers.set(next,tokens);seen.delete(item);return next;};return copy(value,0);
}
export function graphWireTypedCopy(value){
 const seen=new WeakSet();let count=0;
 const copy=(item,depth)=>{need(depth<=MAX_DEPTH&&++count<=MAX_VALUES);if(isTypedNumber(item)||!object(item))return cloneTyped(item);need(!seen.has(item));seen.add(item);const next=Array.isArray(item)?[]:{};for(const [key,child]of properties(item)){const raw=numbersFor(item)[key],converted=typeof child==='number'&&typeof raw==='string'&&Object.is(child,numberAt(raw))?freshNumber(raw,/[.eE]/.test(raw)?'number':'integer'):copy(child,depth+1);Object.defineProperty(next,key,{value:converted,enumerable:true,writable:true,configurable:true});}seen.delete(item);return next;};return copy(value,0);
}

function equalAndPair(parsed,value,pairs,seen,depth=0){
 need(depth<=MAX_DEPTH&&typeof parsed===typeof value);
 if(!object(parsed)){
  need(!object(value)&&(typeof parsed==='number'?Number.isFinite(value)&&Object.is(parsed,value):parsed===value));
  if(typeof value==='string')need(safeString(value));return;
 }
 need(object(value)&&Array.isArray(parsed)===Array.isArray(value));
 const left=properties(parsed),right=properties(value);
 need(left.length===right.length);
 const source=new Map(right);need(left.every(([key])=>source.has(key)));
 const record=metadata.get(parsed),prior=seen.get(value);
 need(!prior||prior.canonical===record.canonical);seen.set(value,record);
 pairs.push([value,record]);
 for(const [key,child]of left)equalAndPair(child,source.get(key),pairs,seen,depth+1);
}

export function verifyGraphWire(envelope){
 const entries=properties(envelope),values=new Map(entries);
 need(!Array.isArray(envelope)&&entries.length===3&&['schema','value','canonical'].every(key=>values.has(key))
  &&values.get('schema')===SCHEMA&&typeof values.get('canonical')==='string');
 const canonical=values.get('canonical');need(new TextEncoder().encode(canonical).length<=MAX_BYTES);
 const reader=new Reader(canonical),parsed=reader.value();
 need(reader.at===canonical.length&&object(parsed));
 let decoded;try{decoded=JSON.parse(canonical);}catch{need(false);}
 const pairs=[];equalAndPair(parsed,decoded,pairs,new WeakMap());
 equalAndPair(parsed,values.get('value'),pairs,new WeakMap());
 // Keep both the returned parsed tree and the original envelope.value usable.
 // Only immutable data receives metadata; edits must use a separate copy.
 for(let i=pairs.length-1;i>=0;i--){const [value,record]=pairs[i];Object.freeze(value);metadata.set(value,record);}
 return decoded;
}

function canonical(value,omit,depth=0,seen=new WeakSet()){
 need(depth<=MAX_DEPTH);
 if(isTypedNumber(value)){const raw=numericToken(value);need(omit===undefined&&!/[.eE]/.test(raw)&&Number.isSafeInteger(numericValue(value))&&!Object.is(numericValue(value),-0),'本机新数值的摘要必须由服务器核算；原命令与请求保留。');return raw;}
 if(object(value)){
  need(!seen.has(value));seen.add(value);
  const record=metadata.get(value);
  if(record){
   need(Object.isFrozen(value));
   if(omit!==undefined){
    need(record.fields&&safeString(omit)&&record.fields.some(item=>item.key===omit));
    seen.delete(value);return '{'+record.fields.filter(item=>item.key!==omit).map(item=>item.raw).join(',')+'}';
   }
   seen.delete(value);return record.canonical;
  }
  const entries=properties(value);let result;
  if(Array.isArray(value)){
   need(omit===undefined);result='['+entries.map(([,child])=>canonical(child,undefined,depth+1,seen)).join(',')+']';
  }else{
   need(omit===undefined||safeString(omit)&&entries.some(([key])=>key===omit));
   result='{'+entries.filter(([key])=>key!==omit).sort(([a],[b])=>order(a,b))
    .map(([key,child])=>JSON.stringify(key)+':'+canonical(child,undefined,depth+1,seen)).join(',')+'}';
  }
  seen.delete(value);return result;
 }
 need(omit===undefined);
 if(typeof value==='number')need(Number.isSafeInteger(value)&&!Object.is(value,-0),
  '本机新数值的摘要必须由服务器核算；原命令与请求保留。');
 else need(value===null||typeof value==='boolean'||safeString(value));
 return JSON.stringify(value);
}

export function graphWireCanonical(value,{omit}={}){return canonical(value,omit);}

// Request encoding is deliberately separate from canonical hashing. New local
// fractions are ordinary JSON inputs; only a server response certifies their
// canonical spelling. Frozen server children retain their original bytes.
export function graphWireRequestJSON(value){
 let count=0;const seen=new WeakSet();
 function encode(item,depth=0){
  need(depth<=MAX_DEPTH&&++count<=MAX_VALUES);
  if(isTypedNumber(item))return numericToken(item);
  if(object(item)){
   need(!seen.has(item));seen.add(item);
   const source=metadata.get(item);
   if(source){need(Object.isFrozen(item));seen.delete(item);return source.canonical;}
   const entries=properties(item);
   const result=Array.isArray(item)?'['+entries.map(([,child])=>encode(child,depth+1)).join(',')
    +']':'{'+entries.sort(([a],[b])=>order(a,b)).map(([key,child])=>JSON.stringify(key)+':'+encode(child,depth+1)).join(',')+'}';
   seen.delete(item);return result;
  }
  if(typeof item==='number')need(Number.isFinite(item)&&(!Number.isInteger(item)||Number.isSafeInteger(item)));
  else need(item===null||typeof item==='boolean'||safeString(item));
  return typeof item==='number'&&Object.is(item,-0)?'-0.0':JSON.stringify(item);
 }
 const result=encode(value);need(new TextEncoder().encode(result).length<=MAX_BYTES);return result;
}

export async function graphWireDigest(value,{omit,crypto=globalThis.crypto}={}){
 need(crypto?.subtle);const bytes=new TextEncoder().encode(graphWireCanonical(value,{omit}));
 return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))]
  .map(value=>value.toString(16).padStart(2,'0')).join('');
}
