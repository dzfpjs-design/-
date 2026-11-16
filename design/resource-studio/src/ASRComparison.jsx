import React, {useEffect, useRef, useState} from 'react';
import {api, json, hashBytes} from './runtime-api';
import './ASRComparison.css';

const percent = value => (value * 100).toFixed(2) + '%';

export default function ASRComparison({account, Btn}) {
  const [open,setOpen] = useState(false), [data,setData] = useState(null), [error,setError] = useState('');
  const [busy,setBusy] = useState(false), [digits,setDigits] = useState(false), [selected,setSelected] = useState('');
  const [audioURL,setAudioURL] = useState(''), [audioState,setAudioState] = useState(''), [audioError,setAudioError] = useState('');
  const [exportState,setExportState] = useState('');
  const [reportURL,setReportURL] = useState('');
  const generation = useRef(0), audioRef = useRef(null), controller = useRef(null), currentURL = useRef('');
  const reportRef = useRef('');
  const storageKey = 'ga-public-asr-comparison:' + account.workspace_id;
  useEffect(()=>()=>{generation.current++;controller.current?.abort();if(currentURL.current)URL.revokeObjectURL(currentURL.current);if(reportRef.current)URL.revokeObjectURL(reportRef.current)},[]);
  async function load() {
    setOpen(true);setBusy(true);setError('');
    try {
      const saved = await json('/specialists/evaluations/asr');
      if(saved.schema!=='public-local-asr-comparison-v1'||saved.cases.length!==40)throw Error('对照结果不完整，请重试。');
      setData(saved);
      setDigits(false);
      let previous='';try{previous=localStorage.getItem(storageKey)||''}catch{}
      setSelected(saved.cases.some(c=>c.id===previous)?previous:saved.cases[0].id);
    } catch(e){setData(null);setError(e.message)} finally {setBusy(false)}
  }
  function clearAudio() {
    generation.current++;controller.current?.abort();audioRef.current?.pause();
    if(currentURL.current)URL.revokeObjectURL(currentURL.current);
    currentURL.current='';setAudioURL('');setAudioState('');setAudioError('');
  }
  function choose(id) {
    clearAudio();setSelected(id);try{localStorage.setItem(storageKey,id)}catch{}
  }
  function filter(value) {
    setDigits(value);
    if(value&&!data.cases.find(c=>c.id===selected)?.contains_digits)choose(data.cases.find(c=>c.contains_digits).id);
  }
  async function loadAudio() {
    clearAudio();const ticket=++generation.current;const abort=new AbortController();controller.current=abort;
    setAudioState('正在读取并核对原录音…');
    try {
      const response=await api('/specialists/evaluations/asr/audio/'+selected+'?workspace_id='+encodeURIComponent(account.workspace_id),{signal:abort.signal});
      const bytes=await response.arrayBuffer();const sha=await hashBytes(bytes);
      if(ticket!==generation.current)return;
      const sample=data.cases.find(c=>c.id===selected);
      if(sha!==sample.input_sha256||sha!==response.headers.get('x-content-sha256'))throw Error('录音原件核对失败，请重试。');
      const url=URL.createObjectURL(new Blob([bytes],{type:'audio/wav'}));currentURL.current=url;setAudioURL(url);setAudioState('原录音已核对，可播放。');
    }catch(e){if(ticket===generation.current){setAudioState('');setAudioError(e.message)}}
  }
  async function download() {
    setExportState('正在准备对照报告…');
    try {
      const response=await api('/specialists/evaluations/asr/export');const bytes=await response.arrayBuffer();
      if(await hashBytes(bytes)!==response.headers.get('x-content-sha256'))throw Error('导出报告核对失败，请重试。');
      if(reportRef.current)URL.revokeObjectURL(reportRef.current);
      const url=URL.createObjectURL(new Blob([bytes],{type:'text/markdown;charset=utf-8'}));reportRef.current=url;setReportURL(url);const link=document.createElement('a');
      link.href=url;link.download='本地语音模型对照.md';link.click();
      setExportState('对照报告已核对，已发起下载。');
    }catch(e){setExportState(e.message)}
  }
  const cases=data?.cases.filter(c=>!digits||c.contains_digits)||[];
  const current=data?.cases.find(c=>c.id===selected);
  return <section className="local-model-card asr-comparison" aria-label="本地语音模型效果对照">
    <div className="asr-comparison-heading"><div><h2>本地语音模型效果对照</h2><p>听同一段原录音，对照作者标签和两份真实转写。候选仍待业务接入，当前默认不变。</p></div>
      <Btn disabled={busy} onClick={()=>open?(clearAudio(),setOpen(false)):load()}>{open?'收起对照':'查看公开效果对照'}</Btn></div>
    {open&&<>
      {busy&&<p role="status">正在核对公开原件与已保存的结果…</p>}
      {error&&<div role="alert"><p className="error">{error}</p><Btn onClick={load}>重试读取对照</Btn></div>}
      {data&&<>
        <p>{data.source} · 本地 CPU · 40 条原录音 · 无提示词或热词</p>
        <p className="asr-scope">整体错误率更低，但数字子集错误更多；不能据此自动替换默认模型。</p>
        <div className="asr-table-wrap"><table><caption>字符错误率（CER，越低越好），包括替换、漏字和多字</caption>
          <thead><tr><th scope="col">模型</th><th scope="col">全部录音</th><th scope="col">含数字的 6 条</th><th scope="col">全部处理时间</th></tr></thead>
          <tbody>{data.models.map(model=><tr key={model.name}><th scope="row">{model.name}<small>{model.status}</small></th><td>{percent(model.cer.rate)}</td><td>{percent(model.digit_cer.rate)}</td><td>{model.wall_seconds.toFixed(2)} 秒</td></tr>)}</tbody>
        </table></div>
        <p>时间包含每条新进程加载模型及推理，不代表常驻服务吞吐；公开朗读结果不代表现场噪声、方言或业务理解。</p>
        <div className="asr-case-controls"><label><input type="checkbox" checked={digits} onChange={e=>filter(e.target.checked)}/> 只看含数字的录音</label>
          <label>选择原录音 <select value={selected} onChange={e=>choose(e.target.value)}>{cases.map(c=><option value={c.id} key={c.id}>{c.name} · {c.seconds.toFixed(2)} 秒</option>)}</select></label></div>
        {current&&<article aria-label="当前原录音对照">
          <h3>{current.name} · {current.seconds.toFixed(2)} 秒</h3>
          <Btn onClick={loadAudio} disabled={audioState.startsWith('正在读取')}>{audioURL?'重新核对原录音':'加载原录音'}</Btn>
          {audioURL&&<audio ref={audioRef} aria-label="播放原录音" controls src={audioURL} onPlay={()=>setAudioState('正在播放原录音。')} onPause={()=>setAudioState('原录音已暂停。')} onEnded={()=>setAudioState('原录音播放结束。')} onError={()=>{setAudioError('浏览器暂不能播放这份原录音，请重新加载或下载原录音。');setAudioState('')}}/>}
          <p role="status">{audioState}</p>
          {audioError&&<p role="alert" className="error">{audioError}</p>}
          {audioURL&&<a href={audioURL} download={current.name+'.wav'}>下载已核对的原录音</a>}
          <div className="asr-reference"><h4>作者标签</h4><p>{current.reference}</p></div>
          <div className="asr-outputs">{current.outputs.map((output,i)=><div key={data.models[i].name}><h4>{data.models[i].name}</h4><p>{output.text||'没有识别文字'}</p><small>字符错误率 {percent(output.cer.rate)} · 处理 {output.seconds.toFixed(2)} 秒</small></div>)}</div>
        </article>}
        <details><summary>来源与验收范围</summary><p>{data.limits}</p>{data.sources.map(source=><p key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.name}</a></p>)}</details>
        <Btn onClick={download} disabled={exportState.startsWith('正在准备')}>下载完整对照报告</Btn><p role="status">{exportState}</p>
        {reportURL&&<a href={reportURL} download="本地语音模型对照.md">再次下载已核对的报告</a>}
      </>}
    </>}
  </section>;
}
