import {stableStringify} from './content-signature.js';
// PCM stays on this device until the user explicitly submits a completed recording.
export function pcmWav(chunks,sampleRate){
 if(!Number.isInteger(sampleRate)||sampleRate<8000||sampleRate>48000)throw Error('当前录音采样率不支持，请改用音频文件。');
 const count=chunks.reduce((n,c)=>n+c.length,0);
 if(count<sampleRate*.25||count>sampleRate*60)throw Error('录音需在0.25至60秒之间。');
 const data=new ArrayBuffer(44+count*2),v=new DataView(data);
 const str=(at,s)=>{for(let i=0;i<s.length;i++)v.setUint8(at+i,s.charCodeAt(i))};
 str(0,'RIFF');v.setUint32(4,36+count*2,true);str(8,'WAVE');str(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,sampleRate,true);v.setUint32(28,sampleRate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);str(36,'data');v.setUint32(40,count*2,true);
 let at=44;for(const chunk of chunks)for(const value of chunk){if(!Number.isFinite(value))throw Error('录音数据无效，请重新录制。');const f=Math.max(-1,Math.min(1,value));v.setInt16(at,Math.round(f*(f<0?32768:32767)),true);at+=2;}
 return new Blob([data],{type:'audio/wav'});
}
export class LocalRecorder {
 constructor(env,{onSeconds=()=>{},onFull=()=>{},onError=()=>{}}={}){Object.assign(this,{env,onSeconds,onFull,onError,chunks:[],frames:0,cancelled:false});}
 async start(){
  if(!this.env.navigator?.mediaDevices?.getUserMedia||!this.env.AudioContext)throw Error('此浏览器不能录音，请选择音频文件。');
  try{
   const stream=await this.env.navigator.mediaDevices.getUserMedia({audio:{channelCount:{exact:1},echoCancellation:false,noiseSuppression:false,autoGainControl:false},video:false});
   if(this.cancelled){stream.getTracks().forEach(t=>t.stop());return false;}
   this.stream=stream;if(stream.getAudioTracks?.().some(t=>t.getSettings?.().channelCount!==1))throw Error('无法确认设备提供单通道录音，请改用单通道音频文件。');
   for(const t of stream.getAudioTracks?.()||[])t.addEventListener?.('ended',()=>{if(!this.cancelled&&!this.full){this.onError(Error('麦克风已断开，采集已停止，请重新录音或选择音频文件。'));this.cancel();}},{once:true});this.context=new this.env.AudioContext({sampleRate:16000});
   if(this.context.sampleRate<8000||this.context.sampleRate>48000)throw Error('此浏览器采样率不支持，请改用音频文件。');
   this.source=this.context.createMediaStreamSource(stream);this.processor=this.context.createScriptProcessor(4096,1,1);this.gain=this.context.createGain();this.gain.gain.value=0;
   this.processor.onaudioprocess=e=>{
    if(this.cancelled||this.full)return;
    if(e.inputBuffer.numberOfChannels!==1){this.onError(Error('设备返回多通道音频，已停止采集；请选择单通道录音或音频文件。'));this.cancel();return;}
    const input=e.inputBuffer.getChannelData(0),left=this.context.sampleRate*60-this.frames,take=Math.min(left,input.length);
    this.chunks.push(new Float32Array(input.slice(0,take)));this.frames+=take;this.onSeconds(this.frames/this.context.sampleRate);
    if(this.frames===this.context.sampleRate*60){this.full=true;this.onFull();}
   };
   this.source.connect(this.processor);this.processor.connect(this.gain);this.gain.connect(this.context.destination);await this.context.resume();
   if(this.cancelled){await this.release();return false;}return true;
  }catch(e){await this.release();if(this.cancelled)return false;throw Error(e.name==='NotAllowedError'?'未获得麦克风权限，请允许录音或改用音频文件。':e.name==='NotFoundError'?'没有可用麦克风，请改用音频文件。':e.name==='OverconstrainedError'?'设备无法提供单通道录音，请改用音频文件。':e.message||'录音未能开始，请改用音频文件。');}
 }
 async release(){if(this.processor)this.processor.onaudioprocess=null;for(const n of [this.source,this.processor,this.gain])try{n?.disconnect()}catch{}this.stream?.getTracks().forEach(t=>t.stop());try{await this.context?.close()}catch{}}
 async stop(){if(this.stopping)return this.stopping;this.stopping=(async()=>{const rate=this.context?.sampleRate;await this.release();const blob=pcmWav(this.chunks,rate);this.chunks=[];return blob;})();return this.stopping;}
 async cancel(){this.cancelled=true;this.chunks=[];await this.release();}
}
export class VoiceReviewSync {
 constructor({storage,key,row,send,onSaved=()=>{},onStatus=()=>{}}){Object.assign(this,{storage,key,row,send,onSaved,onStatus,next:null,error:null,busy:false});}
 save(text){this.storage.setItem(this.key,JSON.stringify({audio_id:this.row.id,text,version:this.row.version,pending:true}));this.next=text;this.onStatus('saving');this.flush();}
 async flush(){if(this.busy||this.error||this.next===null)return;this.busy=true;try{while(this.next!==null){const text=this.next;this.next=null;try{const row=await this.send(this.row.id,{version:this.row.version,text});this.row=row;this.storage.setItem(this.key,JSON.stringify({audio_id:row.id,text:this.next??text,version:row.version,pending:this.next!==null}));this.onSaved(row);}catch(e){if(this.next===null)this.next=text;throw e;}}this.onStatus('saved');}catch(e){this.error=e;this.onStatus('error',e);}finally{this.busy=false;}}
 async wait(){while(this.busy)await new Promise(r=>setTimeout(r,30));if(this.error||this.next!==null)throw this.error||Error('核对文字尚未保存，请重试。');return this.row;}
 retry(){this.error=null;return this.flush();}
}
export function voiceAlreadyApplied(row,text,target,business,currentText,mode){
 const value=text.trim();if(!value)return false;
 const hasReceipt=Object.values(row?.setup_applications||{}).some(a=>a.reference?.target===target&&(target==='goal'||a.reference.business===business)&&a.reference.text===value);
 return hasReceipt&&(mode==='replace'?currentText.trim()===value:currentText.includes(value));
}
export async function checkedVoiceApplication(intent,result,hash,actorId){
 if(!intent||intent.invalid||!result?.receipt?.reference||!result.draft?.configuration||!Array.isArray(result.draft.configuration.voiceInputs))throw Error('语音应用回执或当前配置不完整，原请求保留。');
 const reference=result.receipt.reference,expected=await hash(new TextEncoder().encode(stableStringify(intent)));
 if(result.receipt.request_hash!==expected||reference.request_id!==intent.request_id||reference.audio_run_id!==intent.audio_run_id||reference.target!==intent.target||reference.business!==intent.business||reference.mode!==intent.mode||reference.text!==intent.text.trim()||reference.deployment!=='local'||reference.cloud_calls!==0||!actorId||reference.actor_id!==actorId)throw Error('语音应用回执与原请求不一致，原请求保留。');
 if(!result.draft.configuration.voiceInputs.some(item=>stableStringify(item)===stableStringify(reference)))throw Error('当前配置未读到相同的语音依据，原请求保留。');
 return result.draft;
}
