import React from 'react';
import {displayReportedValue} from './evidence-assessment-form.mjs';
export default function NativeInformationValue({name,value}){
 if(name==='分段机器转写文字'&&Array.isArray(value))return value.length?<ol>{value.map((text,i)=><li key={i}>{text||'本段没有机器转写文字'}</li>)}</ol>:<>没有保存的转写段，不能据此判断没有声音。</>;
 if(name==='语音候选区间'&&Array.isArray(value))return value.length?<ol>{value.map((s,i)=><li key={i}>{Number(s.start_seconds).toFixed(3)}～{Number(s.end_seconds).toFixed(3)}秒（音频内位置，语音候选）</li>)}</ol>:<>本次门槛下没有语音候选区间，不能据此判断录音无内容。</>;
 return <>{displayReportedValue(value)}</>;
}
