// New numeric input only. Original request text and server canonical remain
// separate authorities; no object supplied by a caller can impersonate a token.
const atoms=new WeakMap(),MAX_BYTES=24*1024*1024,MAX_DEPTH=128,MAX_VALUES=1000000;
const ERROR='数值或原请求无法准确保存，请保留输入并核对。';
const need=(ok,message=ERROR)=>{if(!ok)throw Error(message);};
const jsonNumber=/-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y;
const humanNumber=/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
const safeString=s=>typeof s==='string'&&!s.includes('\0')&&!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(s);
const order=(a,b)=>{const x=[...a],y=[...b];for(let i=0;i<Math.min(x.length,y.length);i++){const n=x[i].codePointAt(0)-y[i].codePointAt(0);if(n)return n;}return x.length-y.length;};
class NumericInput{
 toJSON(){throw Error('原数值必须通过原请求保存，输入已保留。');}
 toString(){return atoms.get(this).raw;}
 valueOf(){return atoms.get(this).value;}
}
export const isTypedNumber=value=>value!==null&&typeof value==='object'&&atoms.has(value);
export function numericValue(value){need(isTypedNumber(value)||typeof value==='number'&&Number.isFinite(value));return isTypedNumber(value)?atoms.get(value).value:value;}
export function numericToken(value){need(isTypedNumber(value));return atoms.get(value).token;}
export function numericDisplay(value){return isTypedNumber(value)?atoms.get(value).raw:Object.is(value,-0)?'-0.0':String(value);}

function atom(token,raw=token){
 jsonNumber.lastIndex=0;const match=jsonNumber.exec(token);
 need(match&&match[0]===token&&token.length<=2048);
 const value=Number(token),kind=/[.eE]/.test(token)?'float':'integer';
 need(Number.isFinite(value)&&!(kind==='integer'&&(token==='-0'||!Number.isSafeInteger(value))));
 const result=new NumericInput();atoms.set(result,{token,raw,value,kind});return Object.freeze(result);
}
function integerToken(token){
 const negative=token.startsWith('-'),unsigned=negative?token.slice(1):token;
 const [mantissa,exponent='0']=unsigned.toLowerCase().split('e'),parts=mantissa.split('.');
 let digits=(parts[0]+(parts[1]||'')).replace(/^0+/,'');
 if(!digits){need(!negative,'整数不能使用负零，请填写0。');return '0';}
 const scale=BigInt((parts[1]||'').length)-BigInt(exponent);
 if(scale>0n){
  need(scale<=BigInt(digits.length),'请输入完整且可准确保存的整数。');
  const count=Number(scale);need(/^0*$/.test(digits.slice(-count)),'请输入完整且可准确保存的整数。');
  digits=digits.slice(0,-count)||'0';
 }else{
  need(BigInt(digits.length)-scale<=16n,'整数超出可准确保存的范围。');digits+='0'.repeat(Number(-scale));
 }
 const exact=BigInt(digits);need(exact<=9007199254740991n,'整数超出可准确保存的范围。');
 return (negative?'-':'')+exact.toString();
}
export function freshNumber(raw,kind='number',label='数值'){
 need(['number','integer'].includes(kind));
 if(typeof raw==='number'){
  need(Number.isFinite(raw)&&(!Number.isInteger(raw)||Number.isSafeInteger(raw)),label+'超出可准确保存的范围，请重新填写原数值。');
  raw=Object.is(raw,-0)?'-0.0':String(raw);
 }
 need(typeof raw==='string'&&raw.trim().length>0&&raw.length<=2048,label+'尚未填写完整。');
 const original=raw;raw=raw.trim();need(humanNumber.test(raw),label+'须填写完整的有限数值。');
 let token=raw.replace(/^\+/,'');
 token=token.replace(/^(-?)\./,'$10.').replace(/\.(?=[eE]|$)/,'.0');
 token=token.replace(/^(-?)0+(?=\d)/,'$1');
 need(Number.isFinite(Number(token)),label+'须为有限数值。');
 if(kind==='integer')return atom(integerToken(token),original);
 if(token==='-0')token='-0.0';
 return atom(token,original);
}

function properties(value){
 need(value!==null&&typeof value==='object'&&!Object.getOwnPropertySymbols(value).length);
 const array=Array.isArray(value),prototype=Object.getPrototypeOf(value);
 need(array?prototype===Array.prototype:prototype===Object.prototype||prototype===null);
 const descriptors=Object.getOwnPropertyDescriptors(value),keys=Object.keys(descriptors);
 if(array)need(keys.length===value.length+1&&keys.includes('length')&&Array.from({length:value.length},(_,i)=>String(i)).every(k=>Object.hasOwn(descriptors,k)));
 return keys.filter(k=>!(array&&k==='length')).map(key=>{
  const descriptor=descriptors[key];need(safeString(key)&&descriptor.enumerable&&Object.hasOwn(descriptor,'value'));return [key,descriptor.value];
 });
}
export function typedRequestJSON(value){
 const seen=new WeakSet();let count=0;
 function encode(item,depth=0){
  need(depth<=MAX_DEPTH&&++count<=MAX_VALUES);
  if(isTypedNumber(item))return numericToken(item);
  if(item!==null&&typeof item==='object'){
   need(!seen.has(item));seen.add(item);const fields=properties(item);
   const result=Array.isArray(item)?'['+fields.map(([,v])=>encode(v,depth+1)).join(',')+']':'{'+fields.sort(([a],[b])=>order(a,b)).map(([k,v])=>JSON.stringify(k)+':'+encode(v,depth+1)).join(',')+'}';
   seen.delete(item);return result;
  }
  if(typeof item==='number'){
   need(Number.isFinite(item)&&(!Number.isInteger(item)||Number.isSafeInteger(item)));
   if(Object.is(item,-0))return '-0.0';
  }else need(item===null||typeof item==='boolean'||safeString(item));
  return JSON.stringify(item);
 }
 const result=encode(value);need(new TextEncoder().encode(result).length<=MAX_BYTES);return result;
}

class Reader{
 constructor(text){need(typeof text==='string'&&new TextEncoder().encode(text).length<=MAX_BYTES);this.text=text;this.at=0;this.count=0;}
 space(){while(/[\x20\t\r\n]/.test(this.text[this.at]||'!'))this.at++;}
 string(){
  const start=this.at++;need(this.text[start]==='"');let closed=false;
  while(this.at<this.text.length){const c=this.text[this.at++];if(c==='"'){closed=true;break;}need(c.charCodeAt(0)>=32);if(c==='\\')this.at++;}
  need(closed);let value;try{value=JSON.parse(this.text.slice(start,this.at));}catch{need(false);}
  need(safeString(value));return value;
 }
 value(depth=0){
  this.space();need(depth<=MAX_DEPTH&&++this.count<=MAX_VALUES);const c=this.text[this.at];
  if(c==='{'){
   this.at++;this.space();const result={},keys=new Set();
   if(this.text[this.at]!=='}')while(true){
    this.space();const key=this.string();need(!keys.has(key));keys.add(key);this.space();need(this.text[this.at++]===':');
    Object.defineProperty(result,key,{value:this.value(depth+1),enumerable:true,writable:true,configurable:true});
    this.space();if(this.text[this.at]!==',')break;this.at++;
   }
   need(this.text[this.at++]==='}');return result;
  }
  if(c==='['){this.at++;this.space();const result=[];if(this.text[this.at]!==']')while(true){result.push(this.value(depth+1));this.space();if(this.text[this.at]!==',')break;this.at++;}need(this.text[this.at++]===']');return result;}
  if(c==='"')return this.string();
  for(const [word,value]of [['true',true],['false',false],['null',null]])if(this.text.startsWith(word,this.at)){this.at+=word.length;return value;}
  jsonNumber.lastIndex=this.at;const match=jsonNumber.exec(this.text);need(match&&match.index===this.at);this.at=jsonNumber.lastIndex;return atom(match[0]);
 }
}
export function parseTypedJSON(text){const reader=new Reader(text),value=reader.value();reader.space();need(reader.at===text.length);return value;}

function transform(value,plain){
 const seen=new WeakSet();let count=0;
 function copy(item,depth=0){
  need(depth<=MAX_DEPTH&&++count<=MAX_VALUES);
  if(isTypedNumber(item))return plain?numericValue(item):item;
  if(item===null||typeof item!=='object'){need(item===null||item===undefined||typeof item==='boolean'||typeof item==='number'&&Number.isFinite(item)||safeString(item));return item;}
  need(!seen.has(item));seen.add(item);const fields=properties(item),result=Array.isArray(item)?[]:{};
  for(const [key,child]of fields)Object.defineProperty(result,key,{value:copy(child,depth+1),enumerable:true,writable:true,configurable:true});
  seen.delete(item);return result;
 }
 return copy(value);
}
export const cloneTyped=value=>transform(value,false);
export const plainTyped=value=>transform(value,true);
export function typedEqual(a,b){
 const path=new WeakSet();let count=0;
 function numberRecord(v){if(isTypedNumber(v))return atoms.get(v);need(typeof v==='number'&&Number.isFinite(v));return {value:v,kind:Number.isInteger(v)&&!Object.is(v,-0)?'integer':'float'};}
 function same(x,y,depth=0){
  need(depth<=MAX_DEPTH&&++count<=MAX_VALUES);
  if(isTypedNumber(x)||isTypedNumber(y)||typeof x==='number'||typeof y==='number'){
   const a=numberRecord(x),b=numberRecord(y);return a.kind===b.kind&&Object.is(a.value,b.value);
  }
  if(x===null||typeof x!=='object'||y===null||typeof y!=='object')return Object.is(x,y);
  need(!path.has(x));path.add(x);const left=properties(x),right=properties(y);
  let result=Array.isArray(x)===Array.isArray(y)&&left.length===right.length;
  const values=new Map(right);if(result)result=left.every(([key,v])=>values.has(key)&&same(v,values.get(key),depth+1));
  path.delete(x);return result;
 }
 try{return same(a,b);}catch{return false;}
}
export function embedRequestJSON(prefix,key,exactBody){
 need(prefix!==null&&typeof prefix==='object'&&!Array.isArray(prefix)&&safeString(key)&&!Object.hasOwn(prefix,key));
 parseTypedJSON(exactBody);
 const fields=properties(prefix);fields.push([key,null]);
 const result='{'+fields.sort(([a],[b])=>order(a,b)).map(([name,value])=>JSON.stringify(name)+':'+(name===key?exactBody:typedRequestJSON(value))).join(',')+'}';
 need(new TextEncoder().encode(result).length<=MAX_BYTES);return result;
}
