import React, {useCallback, useEffect, useRef, useState} from 'react';
import './advice-user-compact.css';
import {presentPreview} from './advice-preview-display.mjs';

// Persist operation numbers before requests, so a lost response or refresh
// cannot silently create a second preparation or preview.
function save(storage, key, value) {
  storage.setItem(key, JSON.stringify(value));
  if (storage.getItem(key) !== JSON.stringify(value)) throw Error('保存失败');
  return value;
}

export default function AdvicePreparationV2({account, task, requestJSON, onAvailability, onRefreshReady,
  storage=globalThis.localStorage, locks=globalThis.navigator?.locks}) {
  const [context, setContext] = useState(null), [items, setItems] = useState([]);
  const [selected, setSelected] = useState(''), [state, setState] = useState(null);
  const [documents, setDocuments] = useState([]), [checked, setChecked] = useState({});
  const [preview, setPreview] = useState(null), [busy, setBusy] = useState(''), [error, setError] = useState('');
  const [pending, setPending] = useState(null), [reachable,setReachable]=useState(false);
  const alive = useRef(0);
  const reads = useRef({context:0, selected:0});
  const selectedRef=useRef(selected);selectedRef.current=selected;
  const availability = useRef(onAvailability); availability.current = onAvailability;
  const refreshReady = useRef(onRefreshReady); refreshReady.current = onRefreshReady;
  const base = '/workspace/tasks/' + encodeURIComponent(task?.id || '') + '/business-advice-v2';
  const key = 'advice-v2:' + JSON.stringify([account?.workspace_id, account?.user_id, task?.id]);
  const call = useCallback((url, body) => requestJSON(url, {method:'POST', body:JSON.stringify(body)}), [requestJSON]);
  const refresh = useCallback(async () => {
    const generation = alive.current, serial = ++reads.current.context;
    try {
      const available = await requestJSON(base + '/availability');
      if (alive.current !== generation || reads.current.context !== serial) return;
      if (available?.enabled===false) {setContext({enabled:false});availability.current?.(false);setError('');return;}
      if (available?.enabled!==true) throw Error('unavailable');
      const ctx = await requestJSON(base + '/context');
      if (alive.current !== generation || reads.current.context !== serial) return;
      if (ctx?.enabled===false) { setContext(ctx); availability.current?.(false); return; }
      if (ctx?.enabled!==true) throw Error('unavailable');
      const list = await requestJSON(base + '/preparations');
      if (alive.current !== generation || reads.current.context !== serial) return;
      setContext(ctx); availability.current?.(true); setItems(list.items); setReachable(true);setError('');
      setSelected(current => current && list.items.some(item => item.plan_id === current)
        ? current : (list.items.at(-1)?.plan_id || ''));
      return list.items;
    } catch { if (alive.current === generation && reads.current.context === serial) {
      setReachable(false);setError('建议暂不可用，请刷新。');
    } }
  }, [base, requestJSON]);
  const load = useCallback(async id => {
    const generation = alive.current, serial = ++reads.current.selected;
    if (!id) {setState(null); setDocuments([]); setPreview(null); return;}
    try {
      const current = await requestJSON(base + '/preparations/' + encodeURIComponent(id));
      let docs = [];
      if (current.phase === 'review' && current.can_preview) docs = (await requestJSON(base + '/preparations/' + encodeURIComponent(id) + '/documents')).documents;
      if (alive.current === generation && reads.current.selected === serial) {setState(current);setDocuments(docs);setChecked({});setError('');}
    } catch {if (alive.current === generation && reads.current.selected === serial) setError('暂时无法读取资料，请刷新重试。');}
  }, [base, requestJSON]);
  useEffect(() => {alive.current++;availability.current?.(null);setReachable(false);setContext(null);setItems([]);setSelected('');setState(null);setDocuments([]);setPreview(null);
    try {setPending(JSON.parse(storage.getItem(key)||'null'));} catch {setError('无法读取待处理的申请。');}
    refresh();return () => {alive.current++;};}, [key, refresh, storage]);
  useEffect(() => {refreshReady.current?.(async()=>{const serial=++reads.current.selected,id=selected;setPreview(null);const items=await refresh();if(items&&id&&items.some(item=>item.plan_id===id)&&reads.current.selected===serial&&selectedRef.current===id)await load(id);});return () => refreshReady.current?.(null);}, [refresh,load,selected]);
  useEffect(() => {setPreview(null);setState(null);setDocuments([]);load(selected);}, [selected, load]);
  const prepare = async source => {
    if (busy || !context?.can_prepare) return;
    const generation=alive.current;setBusy('prepare');setError('');
    try {
      if(!locks?.request)throw Error('无法安全保存申请');
      await locks.request(key,{mode:'exclusive'},async()=>{
        // Another tab may have completed while this tab was waiting.
        const current=await requestJSON(base+'/preparations');
        let original=JSON.parse(storage.getItem(key)||'null');
        if(current.items.length){
          const matching=original&&current.items.find(item=>item.request_id===original.request_id);
          if(matching){storage.removeItem(key);if(alive.current===generation)setPending(null);}
          if(alive.current===generation){setSelected((matching||current.items.at(-1)).plan_id);await refresh();}
          return;
        }
        if (original && (!Number.isFinite(Date.parse(original.expires_at))||Date.parse(original.expires_at)<=Date.now()))
          throw Error('expired preparation');
        if (original && JSON.stringify(original.selection)!==JSON.stringify(source.selection))
          throw Error('已有待确认申请');
        if (!original) {
          if (!globalThis.crypto?.randomUUID) throw Error('无法保存申请');
          original=save(storage,key,{policy_id:crypto.randomUUID(),request_id:crypto.randomUUID(),source_title:source.title,
            goal_hash:context.goal_hash,head_hash:context.head_hash,expires_at:context.expires_at,
            max_tokens:context.max_tokens,selection:source.selection});
          setPending(original);
        }
        const issued=await call(base+'/policy',{request_id:original.policy_id,expected_goal_hash:original.goal_hash,
          expected_mature_head_hash:original.head_hash,expires_at:original.expires_at,
          limits:{max_tokens:original.max_tokens,max_request_bytes:50000,max_evidence_items:2},reason:'准备业务建议'});
        const result=await call(base+'/preparations',{policy_ref:issued.policy_ref,request:{request_id:original.request_id,
          expected_goal_hash:original.goal_hash,expected_mature_head_hash:original.head_hash,
          document_source:original.selection,reason:'准备业务建议'}});
        storage.removeItem(key);
        if(alive.current===generation){setPending(null);setSelected(result.plan_id);await refresh();}
      });
    } catch {if(alive.current===generation)setError('申请结果待确认，请刷新后重试。');}
    finally {if(alive.current===generation)setBusy('');}
  };
  const process = async () => {
    if(busy||!reachable||!selected)return;const generation=alive.current;setBusy('process');setError('');
    try {await call(base+'/preparations/'+encodeURIComponent(selected)+'/documents',{});if(alive.current===generation)await load(selected);}
    catch {if(alive.current===generation)setError('资料暂未整理完成，请刷新后重试。');}
    finally {if(alive.current===generation)setBusy('');}
  };
  const authorize = async item => {
    if(busy||!reachable||!checked[item.id])return;const generation=alive.current;setBusy(item.id);setError('');
    try {await call(base+'/preparations/'+encodeURIComponent(selected)+'/consent',{
      evidence_id:item.id,expected_version:item.version,confirmed:true,reason:'用户确认发送资料分析'});
      if(alive.current===generation)await load(selected);}
    catch {if(alive.current===generation)setError('授权结果待确认，请刷新后查看。');}
    finally {if(alive.current===generation)setBusy('');}
  };
  const showPreview = async () => {
    if(busy||!reachable||!state)return;const generation=alive.current, previewKey=key+':preview:'+selected;setBusy('preview');setError('');setPreview(null);
    try {
      let request=JSON.parse(storage.getItem(previewKey)||'null');
      if(!request) request=save(storage,previewKey,{request_id:crypto.randomUUID(),expected_plan_version:state.version,
        expected_goal_hash:state.goal_hash,expected_mature_head_hash:state.head_hash,reason:'核对分析内容'});
      const result=await call(base+'/preparations/'+encodeURIComponent(selected)+'/preview',request);
      const display=presentPreview(result.messages);
      if(alive.current===generation)setPreview(display);
    } catch {if(alive.current===generation)setError('预览暂不可用，请刷新后重试。');}
    finally {if(alive.current===generation)setBusy('');}
  };
  if(!context?.enabled&&!error)return null;
  return <div className="advice-user-panel">
    {error&&<p role="alert">{error}</p>}
    {!selected&&pending&&(!Number.isFinite(Date.parse(pending.expires_at))||Date.parse(pending.expires_at)<=Date.now())&&<p>申请已过期，请联系管理员核对。</p>}
    {!selected&&context?.can_prepare&&context.sources.filter(source=>!pending||JSON.stringify(source.selection)===JSON.stringify(pending.selection)).map((source,index)=><button className="btn primary" key={index} type="button" disabled={!!busy||!reachable||!!pending&&(!Number.isFinite(Date.parse(pending.expires_at))||Date.parse(pending.expires_at)<=Date.now())} onClick={()=>prepare(source)}>{pending?'继续准备':'使用'+source.title}</button>)}
    {!selected&&pending&&context?.enabled&&!context.sources.some(source=>JSON.stringify(source.selection)===JSON.stringify(pending.selection))&&<p>原资料暂不可用，请联系管理员核对。</p>}
    {!selected&&context?.enabled&&!context.can_prepare&&<p>请联系管理员准备建议。</p>}
    {state?.phase==='waiting'&&(state.can_process?<button className="btn primary" type="button" disabled={!!busy||!reachable} onClick={process}>{busy==='process'?'正在整理…':'整理资料'}</button>:<p>等待资料整理</p>)}
    {state?.phase==='review'&&!state.can_preview&&<p>资料已整理，等待业务人员核对。</p>}
    {state?.phase==='review'&&state.can_preview&&<><h5>核对资料</h5>{documents.map((item,index)=><article key={item.id}>
      <details><summary>资料 {index+1}</summary><pre style={{whiteSpace:'pre-wrap'}}>{item.text}</pre></details>
      {item.authorized?<p>已授权</p>:<><label><input type="checkbox" checked={!!checked[item.id]} onChange={event=>setChecked({...checked,[item.id]:event.target.checked})}/>同意将资料发送给 {context?.model} 分析</label>{' '}
      <button className="btn" type="button" disabled={!!busy||!reachable||!checked[item.id]} onClick={()=>authorize(item)}>确认授权</button></>}
    </article>)}
    <button className="btn primary" type="button" disabled={!!busy||!reachable||!state.can_preview||documents.length===0||documents.some(item=>!item.authorized)} onClick={showPreview}>{busy==='preview'?'正在准备…':'查看分析内容'}</button></>}
    {preview&&<><details><summary>分析内容</summary>
      <h6>分析问题</h6><pre style={{whiteSpace:'pre-wrap'}}>{preview.question}</pre>
      <h6>分析要求</h6><pre style={{whiteSpace:'pre-wrap'}}>{preview.instruction}</pre>
      {preview.lines.length>0&&<><h6>相关情况</h6>{preview.lines.map((line,index)=><p key={index}><strong>{line.label}：</strong>{line.value}</p>)}</>}
      {preview.documents.map((text,index)=><section key={index}><h6>资料 {index+1}</h6><pre style={{whiteSpace:'pre-wrap'}}>{text}</pre></section>)}
    </details><button className="btn primary" type="button" disabled>开始分析</button><p>分析服务暂不可用。</p></>}

  </div>;
}
