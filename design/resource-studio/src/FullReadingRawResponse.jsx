import React, {useEffect, useRef, useState} from 'react';
import {json} from './runtime-api';

const SHA = /^[a-f0-9]{64}$/;
const STAGES = new Set(['extract-1', 'extract-2', 'merge']);
const FINISH = new Set([null, 'stop', 'length', 'content_filter', 'tool_calls', 'function_call']);
const BODY = new Set([null, 'complete', 'oversize', 'unsupported_encoding']);
const MAX_CONTENT_BYTES = 1024 * 1024;

function boundScope(account, row, stage) {
  const previewHash = stage === 'merge' ? row?.merge?.preview?.preview_hash : row?.preview?.preview_hash;
  if (!account?.user_id || !account?.workspace_id || !account?.role || !row?.id ||
      !Number.isInteger(row.revision) || row.revision < 1 || !STAGES.has(stage) || !SHA.test(previewHash || '')) return null;
  return {workspace_id:account.workspace_id, actor_id:account.user_id, draft_id:row.id,
          revision:row.revision, stage, preview_hash:previewHash};
}

function checkedResult(value, expected) {
  if (value?.schema !== 'report-full-raw-view-v1' || !value.scope ||
      Object.keys(expected).some(key => value.scope[key] !== expected[key]) ||
      !['available', 'not_saved', 'unavailable'].includes(value.status) ||
      !FINISH.has(value.finish_reason) || !BODY.has(value.response_body_status) ||
      value.can_apply !== false || value.can_publish !== false) throw Error('scope');
  if (value.status === 'not_saved') {
    if (value.scope.raw_hash !== null || value.content !== null || value.content_encoding !== null ||
        value.finish_reason !== null || value.response_body_status !== null) throw Error('shape');
  } else {
    if (!SHA.test(value.scope.raw_hash || '')) throw Error('shape');
    if (value.status === 'available') {
      if (typeof value.content !== 'string' || !value.content ||
          new TextEncoder().encode(value.content).length > MAX_CONTENT_BYTES ||
          !['plain', 'json-ascii-string-v1'].includes(value.content_encoding) ||
          ![null, 'complete'].includes(value.response_body_status)) throw Error('shape');
    } else if (value.content !== null || value.content_encoding !== null) throw Error('shape');
  }
  return {status:value.status, content:value.content, content_encoding:value.content_encoding,
          finish_reason:value.finish_reason, response_body_status:value.response_body_status};
}

function outcome(result) {
  if (result.status === 'not_saved') return '尚未找到已保存的原回答；这不能说明请求未发送或未计费。';
  if (result.response_body_status === 'oversize') return '响应超过保存上限，完整原答未能保存；请核对调用记录，不要直接重新发送。';
  if (result.response_body_status === 'unsupported_encoding') return '响应使用了当前不支持的编码，完整原答未能保存；请核对调用记录，不要直接重新发送。';
  if (result.status === 'unavailable') return '原回答暂无法安全展示，原记录保留；请核对调用记录，不要直接重新发送。';
  if (result.finish_reason === 'length') return '模型报告因输出长度结束，回答可能被截断，不能自动采用。';
  if (result.finish_reason === null) return '此回答缺少可核对的结束原因，不能证明输出完整。';
  if (result.finish_reason === 'stop') return '模型报告正常结束；这不能证明内容准确或没有遗漏。';
  if (result.finish_reason === 'content_filter') return '模型报告内容被过滤，不能把此回答视为完整结果。';
  return '模型未以普通完成状态结束，不能把此回答视为完整结果。';
}

/** The operator may inspect one saved stage; no content is written to storage. */
export default function FullReadingRawResponse({account, row, stage, Btn='button', request=json}) {
  const expected = boundScope(account, row, stage);
  const scope = JSON.stringify([account?.user_id, account?.workspace_id, account?.role, expected]);
  const live = useRef({scope, generation:0, serial:0, controller:null});
  if (live.current.scope !== scope) {
    live.current.scope = scope;
    live.current.generation++;
    live.current.serial++;
  }
  const generation = live.current.generation;
  const [view, setView] = useState(null);
  const current = view?.scope === scope && view?.generation === generation ? view : null;
  useEffect(() => () => {live.current.serial++; live.current.controller?.abort(); live.current.controller = null;}, [scope]);

  async function load() {
    if (!expected || current?.phase === 'loading') return;
    const stamp = ++live.current.serial, ownGeneration = live.current.generation;
    const controller = new AbortController();
    live.current.controller?.abort();
    live.current.controller = controller;
    setView({scope, generation:ownGeneration, phase:'loading'});
    const active = () => live.current.scope === scope && live.current.generation === ownGeneration &&
      live.current.serial === stamp && !controller.signal.aborted;
    try {
      const path = '/setup/report-full-readings/' + encodeURIComponent(expected.draft_id) + '/stages/' +
        encodeURIComponent(stage) + '/raw?revision=' + encodeURIComponent(expected.revision);
      const response = await request(path, {signal:controller.signal, cache:'no-store', headers:{'X-Workspace-ID':account.workspace_id}});
      const result = checkedResult(response, expected);
      if (active()) setView({scope, generation:ownGeneration, phase:'ready', result});
    } catch (error) {
      if (active()) setView({scope, generation:ownGeneration, phase:'error',
        message:error?.status === 401 || error?.status === 403 ? '当前账号无法读取此回答，请重新核对权限。' :
          error?.status === 409 || error?.message === 'scope' || error?.message === 'shape' ?
            '原记录或查看范围已变化，请刷新后重试。' : '原回答暂时无法读取，请重试。'});
    } finally {
      if (live.current.controller === controller) live.current.controller = null;
    }
  }

  if (!expected) return null;
  const label = stage === 'merge' ? '合并' : '抽取' + stage.slice(-1);
  const result = current?.result;
  return <section aria-label={label + '原回答核对'}>
    <p>按需读取已保存的原回答，用于核对；此操作不会重发模型请求，也不会采用或修改内容。</p>
    <Btn type="button" disabled={current?.phase === 'loading'} onClick={load}>
      {current?.phase === 'loading' ? '正在读取…' : current?.phase === 'error' ? '重试读取' + label + '原回答' :
        result ? '重新读取' + label + '原回答' : '查看' + label + '原回答'}
    </Btn>
    {current?.phase === 'error' && <p role="alert">{current.message}</p>}
    {result && <div role="status"><p>{outcome(result)}</p>
      {result.content !== null && <>
        {result.content_encoding === 'json-ascii-string-v1' && <p>原文包含无法直接保存的字符，以下以转义字符串形式保留。</p>}
        <pre aria-label={label + '原回答正文'} style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:'24rem',overflow:'auto'}}>{result.content}</pre>
      </>}
    </div>}
  </section>;
}
