/** Bounded MJPEG decoder; no URLs, credentials or media bytes are persisted. */
export async function readDjiFrames(response,onFrame){
 const type=response.headers.get('content-type')||'';
 if(!/^multipart\/x-mixed-replace\s*;\s*boundary=(?:frame|"frame")\s*$/i.test(type)||!response.body?.getReader)throw Error('视频格式不可用');
 const reader=response.body.getReader();let buffer=new Uint8Array(),last=Date.now();
 try{for(;;){let timer;const remaining=Math.max(0,7000-(Date.now()-last));let part;
  try{part=await Promise.race([reader.read(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('画面接收超时')),remaining)})]);}finally{clearTimeout(timer)}
  if(part.done)return;if(!(part.value instanceof Uint8Array)||!part.value.length)continue;
  if(buffer.length+part.value.length>2*1024*1024)throw Error('画面过大');
  const merged=new Uint8Array(buffer.length+part.value.length);merged.set(buffer);merged.set(part.value,buffer.length);buffer=merged;
  while(buffer.length){let pos=-1;for(let i=0;i+3<buffer.length&&i<4096;i++)if(buffer[i]===13&&buffer[i+1]===10&&buffer[i+2]===13&&buffer[i+3]===10){pos=i;break}
   if(pos<0){if(buffer.length>4096)throw Error('画面格式无效');break}
   const header=new TextDecoder('ascii').decode(buffer.subarray(0,pos));const match=/^--frame\r\nContent-Type: image\/jpeg\r\nContent-Length: ([1-9][0-9]*)$/i.exec(header);if(!match)throw Error('画面格式无效');
   const size=Number(match[1]);if(!Number.isSafeInteger(size)||size>1024*1024)throw Error('画面过大');const start=pos+4,end=start+size;if(buffer.length<end+2)break;
   if(buffer[start]!==255||buffer[start+1]!==216||buffer[end-2]!==255||buffer[end-1]!==217||buffer[end]!==13||buffer[end+1]!==10)throw Error('画面数据无效');
   onFrame(buffer.slice(start,end));last=Date.now();buffer=buffer.slice(end+2);
  }
 }}finally{try{reader.cancel().catch(()=>{})}catch{}}
}
