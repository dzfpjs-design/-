import React,{useEffect,useRef,useState} from 'react';

const PAGE_SIZE=20;
const COUNT_LABELS=[
  ['top_level_directories','目录结构'],
  ['top_level_other_content_controls','其他顶层内容控件'],
  ['other_skipped_top_level_with_text','可能被正文提取跳过的其他顶层内容'],
  ['content_controls','内容控件'],
  ['textboxes_all_branches','文本框（含备用分支）'],
  ['alternate_content','备用内容结构'],
  ['alternate_without_supported_branch','需进一步核对的备用内容'],
  ['tables','表格'],['nested_tables','嵌套表格'],['drawings','图形'],['legacy_pictures','旧式图形'],
  ['insertions','插入修订'],['deletions','删除修订'],
  ['moved_from','移出修订'],['moved_to','移入修订'],
];

function validResponse(value){
  return value?.schema==='docx-structure-audit-v1' &&
    ['review_required','no_detected_issues'].includes(value.status) &&
    value.counts && typeof value.counts==='object' && !Array.isArray(value.counts) &&
    Array.isArray(value.issues) && Array.isArray(value.candidates) &&
    value.issues.every(issue=>typeof issue?.message==='string') &&
    value.candidates.every(item=>['directory_entry','textbox'].includes(item?.kind) &&
      item.review_required===true && typeof item.text==='string' &&
      item.location?.scheme==='docx-audit-v1' && item.location.part==='word/document.xml' &&
      Number.isSafeInteger(item.location.body_index) && item.location.body_index>0 &&
      Array.isArray(item.location.child_path) && item.location.child_path.every(n=>Number.isSafeInteger(n)&&n>0));
}

function StructurePanel({materialID,requestJSON}){
  const [state,setState]=useState({kind:'idle'}),[limit,setLimit]=useState(PAGE_SIZE);
  const current=useRef({generation:0,controller:null,alive:true});
  useEffect(()=>{current.current.alive=true;return ()=>{current.current.alive=false;current.current.generation++;current.current.controller?.abort();};},[]);
  const cancel=()=>{current.current.generation++;current.current.controller?.abort();current.current.controller=null;setState({kind:'idle'});setLimit(PAGE_SIZE);};
  const load=async()=>{
    current.current.controller?.abort();const generation=++current.current.generation,controller=new AbortController();
    current.current.controller=controller;setState({kind:'loading'});setLimit(PAGE_SIZE);
    try{
      const data=await requestJSON('/setup/materials/'+encodeURIComponent(materialID)+'/docx-structure?include_candidates=true',{signal:controller.signal});
      if(!current.current.alive||current.current.generation!==generation)return;
      if(!validResponse(data))throw Error('invalid_response');
      setState({kind:'ready',data});
    }catch(error){
      if(!current.current.alive||current.current.generation!==generation)return;
      setState({kind:'error',denied:error?.status===401||error?.status===403});
    }finally{if(current.current.generation===generation)current.current.controller=null;}
  };
  return <details className="docx-structure-review" onToggle={event=>event.currentTarget.open?load():cancel()}>
    <summary>核对 Word 目录与文本框</summary>
    <p>这里仅展示顶层目录条目和按备用内容单分支提取的文本框候选；修订、图像及其他正文仍需结合原件核对。非断行连字符可能是负号或词内连字符，请结合原件判断；可选连字符标为“［可选连字符］”，不代表排版时一定显示。以下内容均待核对，不代表正文已完整解析或业务内容已核实。</p>
    {state.kind==='loading'&&<p role="status">正在读取文档结构，请稍候…</p>}
    {state.kind==='error'&&<div role="alert"><p>{state.denied?'当前账号无法读取这份资料的结构，请核对资料与工作区权限。':'未能读取文档结构，请检查资料状态或稍后重试。'}</p><button type="button" onClick={load}>重试读取</button></div>}
    {state.kind==='ready'&&<>
      <p>{state.data.status==='review_required'?'检测到需要核对的文档结构。':'本次未检出已支持范围内的结构问题，仍需核对原件。'}</p>
      <ul>{COUNT_LABELS.filter(([key])=>Number.isSafeInteger(state.data.counts[key])&&state.data.counts[key]>0).map(([key,label])=><li key={key}>{label}：{state.data.counts[key]}</li>)}</ul>
      {state.data.issues.length>0&&<div><strong>需要注意</strong><ul>{state.data.issues.map((issue,index)=><li key={index}>{issue.message}</li>)}</ul></div>}
      {state.data.candidates.length===0?<p>本次没有检出候选；这不代表其他内容已确认。</p>:<div>
        <p>本次检出目录条目和文本框 {state.data.candidates.length} 条候选，当前显示 {Math.min(limit,state.data.candidates.length)} 条。文本框可能来自备用分支，不代表原文中一定可见。</p>
        <ol>{state.data.candidates.slice(0,limit).map((item,index)=><li key={index}>
          <strong>{item.kind==='directory_entry'?'目录条目候选':'文本框候选'} · 待核对</strong>
          <p style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{item.text.replaceAll('\u00ad','［可选连字符］')}</p>
          <small>来源：Word 文档主体第 {item.location.body_index} 项，结构路径 {item.location.child_path.join(' / ')||'主体'}（非页码）。请与原文件核对。</small>
        </li>)}</ol>
        {limit<state.data.candidates.length&&<button type="button" onClick={()=>setLimit(n=>n+PAGE_SIZE)}>显示更多候选</button>}
      </div>}
    </>}
  </details>;
}

export default function DocxStructureReview({account,materialID,requestJSON}){
  if(!account?.user_id||!account?.workspace_id||typeof materialID!=='string'||!materialID||typeof requestJSON!=='function')return null;
  const scope=JSON.stringify([account.user_id,account.workspace_id,account.role,materialID]);
  return <StructurePanel key={scope} materialID={materialID} requestJSON={requestJSON}/>;
}
