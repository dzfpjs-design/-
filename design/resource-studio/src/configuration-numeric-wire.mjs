// Numeric provenance only. No source, server hash, permission or save authority.
import {parseTypedJSON,typedRequestJSON,plainTyped,isTypedNumber,numericValue,numericToken} from './typed-numbers-v132.mjs';

const MAX_BYTES=24*1024*1024,MAX_DEPTH=128,MAX_VALUES=1000000;
const INTERNAL='configuration_wire',attachments=new WeakMap();
const ERROR='配置数值、原始类型或数组对应无法无损核对；原稿保留，未猜测或截断。';
const need=(ok,message=ERROR)=>{if(!ok)throw Error(message);};
const object=v=>v!==null&&typeof v==='object'&&!isTypedNumber(v);
const own=(v,k)=>Object.prototype.hasOwnProperty.call(v,k);
const put=(v,k,x)=>Object.defineProperty(v,k,{value:x,enumerable:true,writable:true,configurable:true});
const safeString=v=>typeof v==='string'&&!v.includes('\0')&&!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v);
function entries(value,{attachment=false}={}){
 need(object(value)&&Object.getOwnPropertySymbols(value).length===0);
 const array=Array.isArray(value),prototype=Object.getPrototypeOf(value);
 need(array?prototype===Array.prototype:prototype===Object.prototype||prototype===null);
 const descriptors=Object.getOwnPropertyDescriptors(value),keys=Object.keys(descriptors);
 if(array)need(keys.length===value.length+1&&keys.includes('length')&&Array.from({length:value.length},(_,i)=>String(i)).every(k=>own(descriptors,k)));
 const result=[];
 for(const key of keys){
  if(array&&key==='length')continue;
  const d=descriptors[key];
  if(attachment&&key===INTERNAL){need(attachments.has(value)&&!d.enumerable&&own(d,'value')&&d.value===attachments.get(value));continue;}
  need(safeString(key)&&d.enumerable&&own(d,'value'));result.push([key,d.value]);
 }
 return result;
}
function copy(value,{plain=false,attachment=false}={}){
 const stack=new WeakSet();let count=0;
 function visit(item,depth){
  need(depth<=MAX_DEPTH&&++count<=MAX_VALUES);
  if(isTypedNumber(item)){const number=numericValue(item);need(!Number.isInteger(number)||Number.isSafeInteger(number));return plain?number:item;}
  if(object(item)){
   need(!stack.has(item));stack.add(item);const result=Array.isArray(item)?[]:{};
   for(const[key,child]of entries(item,{attachment:attachment&&depth===0}))put(result,key,visit(child,depth+1));
   stack.delete(item);return result;
  }
  if(typeof item==='number')need(Number.isFinite(item)&&(!Number.isInteger(item)||Number.isSafeInteger(item)));
  else need(item===null||typeof item==='boolean'||safeString(item));
  return item;
 }
 return visit(value,0);
}
function decode(raw){
 need(typeof raw==='string'&&new TextEncoder().encode(raw).length<=MAX_BYTES);
 const typed=parseTypedJSON(raw);copy(typed);return typed;
}
const mirror=value=>typedRequestJSON(copy(value,{plain:true}));
function configuration(value){need(object(value)&&!Array.isArray(value));return value;}
function ownId(value){
 if(!object(value)||Array.isArray(value))return null;
 const item=entries(value).find(([key])=>key==='id');
 return item&&typeof item[1]==='string'&&item[1].length?item[1]:null;
}
function numericRisk(value){
 if(isTypedNumber(value))return numericToken(value)!==typedRequestJSON(numericValue(value));
 return object(value)&&entries(value).some(([,child])=>numericRisk(child));
}
function groupBy(values,key){
 const groups=new Map();for(const item of values){const k=key(item);if(k===null)continue;const group=groups.get(k)||[];group.push(item);groups.set(k,group);}return groups;
}
function rebuild(value,baseline){
 let count=0;
 function visit(item,previous,depth){
  need(depth<=MAX_DEPTH&&++count<=MAX_VALUES);
  if(isTypedNumber(item))return item;
  if(!object(item)){
   if(typeof item==='number'&&isTypedNumber(previous)&&Object.is(item,numericValue(previous)))return previous;
   return item;
  }
  if(Array.isArray(item)){
   if(!Array.isArray(previous))return copy(item);
   const fresh=entries(item).map(([,v],i)=>({i,value:v})),old=entries(previous).map(([,v],i)=>({i,value:v}));
   const matched=new Map(),used=new Set();
   const oldIds=groupBy(old,row=>ownId(row.value)),newIds=groupBy(fresh,row=>ownId(row.value));
   for(const row of fresh){const id=ownId(row.value),prior=oldIds.get(id);if(id!==null&&prior?.length===1&&newIds.get(id)?.length===1){matched.set(row.i,prior[0].i);used.add(prior[0].i);}}
   const oldMirrors=groupBy(old.filter(row=>!used.has(row.i)),row=>mirror(row.value));
   const newMirrors=groupBy(fresh.filter(row=>!matched.has(row.i)&&!isTypedNumber(row.value)),row=>mirror(row.value));
   for(const[key,rows]of newMirrors){
    const candidates=oldMirrors.get(key)||[];if(!candidates.length)continue;
    const wires=new Set(candidates.map(row=>typedRequestJSON(row.value)));
    need(wires.size===1,'数组中相同可见值对应不同原数值类型，无法确定重排对应；原稿保留。');
    // One old 1.0 and two new visible 1s cannot decide which is the insertion.
    if(rows.length>candidates.length)need([...wires][0]===typedRequestJSON(copy(rows[0].value,{plain:true})),
     '数组新增或重排后的相同值对应不唯一；原稿保留，未按位置套用数值。');
    for(let i=0;i<Math.min(rows.length,candidates.length);i++){matched.set(rows[i].i,candidates[i].i);used.add(candidates[i].i);}
   }
   const uncertain=old.filter(row=>!used.has(row.i)).some(row=>numericRisk(row.value));
   return fresh.map(row=>{
    if(matched.has(row.i))return visit(row.value,previous[matched.get(row.i)],depth+1);
    need(!object(row.value)||!uncertain||!entries(row.value).length,
     '修改后的数组条目缺少唯一标识或完整对应，无法保留原数值类型；原稿保留。');
    return copy(row.value);
   });
  }
  const before=object(previous)&&!Array.isArray(previous)?new Map(entries(previous)):new Map(),result={};
  for(const[key,child]of entries(item))put(result,key,visit(child,before.get(key),depth+1));
  return result;
 }
 return visit(value,baseline,0);
}

export function configurationWire(value,baselineWire=null){
 const current=copy(configuration(value));
 const baseline=baselineWire===null?null:configuration(decode(baselineWire));
 return typedRequestJSON(baseline===null?current:rebuild(current,baseline));
}

export function attachConfigurationWire(saved,wire){
 need(object(saved)&&!Array.isArray(saved));
 const snapshot=copy(saved,{plain:true,attachment:true});
 need(Number.isSafeInteger(snapshot.revision)&&snapshot.revision>=1&&own(snapshot,'configuration'));
 const typed=configuration(decode(wire)),canonical=typedRequestJSON(typed);
 need(mirror(configuration(snapshot.configuration))===mirror(typed),
  '配置原文与页面完整镜像不同；未附加不匹配的数值来源。');
 Object.defineProperty(snapshot,INTERNAL,{value:canonical,enumerable:false,writable:false,configurable:false});
 attachments.set(snapshot,canonical);return snapshot;
}

export function savedConfigurationWire(saved){
 need(object(saved)&&!Array.isArray(saved));
 const descriptor=Object.getOwnPropertyDescriptor(saved,INTERNAL);
 if(!descriptor){need(!attachments.has(saved));return null;}
 need(attachments.has(saved)&&!descriptor.enumerable&&own(descriptor,'value')&&descriptor.value===attachments.get(saved));
 const expected=attachments.get(saved);const snapshot=copy(saved,{plain:true,attachment:true});
 need(own(snapshot,'configuration')&&mirror(configuration(snapshot.configuration))===mirror(configuration(decode(expected))),
  '附带原数值的配置镜像已变化；请基于原文明确重建，不能套用旧数值。');
 return expected;
}

export function decodeConfigurationReply(raw){
 const typed=decode(raw);need(object(typed)&&!Array.isArray(typed));
 const fields=new Map(entries(typed));need(fields.has('configuration')&&!fields.has(INTERNAL));
 const revision=fields.get('revision');
 need(isTypedNumber(revision)&&!/[.eE]/.test(numericToken(revision))
  &&Number.isSafeInteger(numericValue(revision))&&numericValue(revision)>=1);
 return attachConfigurationWire(plainTyped(typed),typedRequestJSON(configuration(fields.get('configuration'))));
}
