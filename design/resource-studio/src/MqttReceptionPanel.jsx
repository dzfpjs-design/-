import React, {useEffect, useState} from 'react';
import {json} from './runtime-api';
import {RECEPTION_POLL_MS, receptionEpoch, receptionTime, receptionView, olderReceptionReport} from './mqtt-reception-policy.mjs';
import './mqtt-reception.css';

function DefaultButton({children, ...props}) { return <button type="button" {...props}>{children}</button>; }

export default function MqttReceptionPanel({gateway, Btn = DefaultButton, onShowReceipts}) {
  const epoch = receptionEpoch(gateway);
  const [snapshot, setSnapshot] = useState({epoch: '', gatewayId: null, report: null, requestState: 'loading'});
  const [refreshCount, setRefreshCount] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [now, setNow] = useState(Date.now);
  const matchingSource = snapshot.gatewayId === gateway?.id;
  const report = matchingSource ? snapshot.report : null;
  const sourceReport = report?.gateway_id === gateway?.id ? report : null;
  const requestState = snapshot.epoch === epoch ? snapshot.requestState : 'loading';
  const view = receptionView(gateway, report, {now, receivedAt: snapshot.receivedAt, requestState});

  useEffect(() => {
    if (!gateway?.id) return;
    let alive = true, inFlight = false, controller = null, deadline = null;
    let acceptedReport = snapshot.epoch === epoch ? snapshot.report : null;
    setSnapshot(previous => ({epoch, gatewayId: gateway.id,
      report: previous.gatewayId === gateway.id ? previous.report : null,
      receivedAt: previous.gatewayId === gateway.id ? previous.receivedAt : null, requestState: 'loading'}));
    async function read() {
      if (!alive || inFlight) return;
      inFlight = true;
      setRefreshing(true);
      controller = new AbortController();
      const requestController = controller;
      deadline = setTimeout(() => {
        requestController.abort();
        if (alive) setSnapshot(previous => ({...previous, epoch, gatewayId: gateway.id, requestState: 'error'}));
      }, 10000);
      try {
        const next = await json('/mqtt/gateways/' + encodeURIComponent(gateway.id) + '/reception',
          {signal: requestController.signal, cache: 'no-store'});
        if (!alive || requestController.signal.aborted) return;
        const outdated = olderReceptionReport(acceptedReport, next);
        if (!outdated) acceptedReport = next;
        const receivedAt = Date.now();
        setSnapshot(previous => ({epoch, gatewayId: gateway.id,
          report: outdated ? previous.report : next, receivedAt: outdated ? previous.receivedAt : receivedAt,
          requestState: outdated ? 'outdated' : 'ready'}));
        setNow(receivedAt);
      } catch {
        if (alive) setSnapshot(previous => ({...previous, epoch, gatewayId: gateway.id, requestState: 'error'}));
      } finally {
        clearTimeout(deadline);
        inFlight = false;
        if (alive) setRefreshing(false);
      }
    }
    read();
    const polling = setInterval(read, RECEPTION_POLL_MS);
    return () => { alive = false; clearInterval(polling); clearTimeout(deadline); controller?.abort(); };
  }, [epoch, refreshCount]);

  // Re-evaluate freshness locally even if the next request is delayed or never returns.
  useEffect(() => {
    if (!gateway?.id) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [gateway?.id]);

  return <section className="mqtt-reception-panel" aria-label="实际接收状态">
    <div className="mqtt-reception-heading"><h3>实际接收状态</h3>
      <Btn disabled={!gateway?.id || refreshing} onClick={() => setRefreshCount(value => value + 1)}>
        {refreshing ? '正在刷新…' : '刷新接收状态'}
      </Btn>
    </div>
    <div className="mqtt-reception-summary">
      <span className={'mqtt-reception-status ' + view.tone} role="status" aria-live="polite">{view.label}</span>
      {onShowReceipts && <Btn disabled={!gateway?.id} onClick={onShowReceipts}>查看原件与处理记录</Btn>}
    </div>
    <p className="mqtt-reception-message" role={view.reason === 'request_failed' ? 'alert' : undefined}>{view.detail}</p>
    <dl className="mqtt-reception-times">
      <div><dt>最后后台报告</dt><dd>{receptionTime(sourceReport?.last_reported_at, sourceReport ? '尚无后台报告' : '尚未读取')}</dd></div>
      <div><dt>最后连通</dt><dd>{receptionTime(sourceReport?.last_connected_at, sourceReport ? '尚无连通记录' : '尚未读取')}</dd></div>
      <div><dt>最近原件</dt><dd>{receptionTime(sourceReport?.last_received_at, sourceReport ? '尚无原件记录' : '尚未读取')}</dd></div>
      <div><dt>下次自动重试</dt><dd>{receptionTime(sourceReport?.next_retry_at, sourceReport ? '暂无重试计划' : '尚未读取')}</dd></div>
    </dl>
    <p className="mqtt-reception-scope">后台自动、只读接收已确认主题，不发送设备命令。此状态不代表设备在线或当前安全。</p>
    <details className="mqtt-reception-history"><summary>最近 20 次状态变更{view.history.length > 0 ? '（' + view.history.length + ' 条）' : ''}</summary>
      {!view.history.length ? <p>{view.reason === 'loading' ? '正在读取状态变更记录…' : view.reason === 'request_failed' ? '暂未读取到状态变更记录，可刷新再试。' : '尚无状态变更记录。'}</p> : <>
        <p>以下是历史记录，不表示当前连通。</p>
        <ol>{view.history.map((item, index) => <li key={(item.at || 'unknown') + ':' + index}>
          <strong>{item.label}</strong><span>{receptionTime(item.at, '报告时间未知')}{item.version ? ' · 配置第 ' + item.version + ' 版' : ''}</span>
          {item.error && <p>{item.error}</p>}
        </li>)}</ol>
      </>}
    </details>
  </section>;
}
