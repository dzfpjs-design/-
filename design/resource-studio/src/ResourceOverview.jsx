import React, {useEffect, useState} from 'react';
import {json} from './runtime-api';
import {OVERVIEW_CARDS, overviewSummary} from './resource-overview-contract.mjs';
import './resource-overview.css';

function OverviewCard({card, account, onChoose}) {
  const [row, setRow] = useState({state:'loading'});
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    setRow({state:'loading'});
    Promise.all(card.paths.map(path => json(path, {cache:'no-store', signal:controller.signal,
      headers:{'X-Workspace-ID':account.workspace_id}})))
      .then(values => overviewSummary(card.id, values))
      .then(summary => {if (alive) setRow({state:'ready',summary});})
      .catch(() => {if (alive && !controller.signal.aborted) setRow({state:'error'});});
    return () => {alive=false; controller.abort();};
  }, [account.workspace_id, account.user_id, account.role, card, attempt]);
  return <article className="resource-overview-card">
    <span className="gateway-overview-type">{card.gateway}</span><h3>{card.title}</h3><p>{card.description}</p>
    <p className="gateway-overview-boundary">{card.boundary}</p>
    {row.state==='loading' ? <p className="resource-overview-status" role="status">正在读取工作区记录…</p> :
     row.state==='error' ? <div className="resource-overview-status resource-overview-error" role="alert"><span>记录暂时无法读取，请检查账号权限或连接。</span><button type="button" onClick={()=>setAttempt(n=>n+1)}>重试</button></div> :
     <p className="resource-overview-status">{row.summary}</p>}
    <button type="button" className="resource-overview-action" onClick={()=>onChoose(card.id)}>管理与新增<span aria-hidden="true"> →</span></button>
  </article>;
}

export default function ResourceOverview({account, onChoose}) {
  return <section className="resource-overview" aria-label="能力总览">
    <div className="resource-overview-intro"><div><h2>网关与能力管理</h2>
      <p>先选择对应网关或接入方式，再新增连接、确认资源并发布到业务实例。一个网关可接入多个来源；按需配置，不必全部启用。</p></div></div>
    <p className="resource-overview-note">卡片介绍可配置的能力，下方是当前工作区记录。已登记或已安装不代表设备在线、模型领域效果已验证，或已在业务实例中启用。后台处理器状态无法从此页确认；未启动时，模型安装和资料处理等任务会停留在排队。</p>
    <div className="resource-overview-grid">{OVERVIEW_CARDS.map(card => <OverviewCard key={card.id} {...{card,account,onChoose}}/>)}</div>
  </section>;
}
