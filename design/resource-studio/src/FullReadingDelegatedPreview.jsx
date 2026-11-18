import React, {useEffect, useRef, useState} from 'react';
import {json} from './runtime-api';
import {digest, fullInt, fullKeys, fullSha, fullUUID, same, verifyFullDraft} from './report-full-reading-contract.mjs';
import {verifyEvidencePreview} from './report-evidence-reading-contract.mjs';

const need = value => { if (!value) throw Error('invalid'); };
const assumptions = ['runtime_code_not_independently_attested', 'database_and_audit_integrity_assumed',
  'no_unobserved_external_gateway_bypass_assumed'];
const basisKeys = 'schema,workspace_id,actor_id,original_draft_id,original_revision,original_job_id,original_start_receipt_hash,original_source_hash,original_projection_hash,original_first_raw_hash,original_first_candidate_receipt_hash,original_messages_hashes,new_messages_hashes,new_preview_hash,operator_asserted_history_hash,history_digest_verification,history_assessment,historical_second_send,source_relationship,new_analysis,not_original_batch_recovery,authorization_basis,user_reviewed_history,user_confirmed_business,duplicate_cost_risk';
const assessmentKeys = 'schema,workspace_id,actor_id,original_draft_id,original_revision,original_start_receipt_hash,original_source_hash,second_messages_hash,original_job_id,original_job_data_hash,original_job_failed_single_attempt_no_active_lease,original_authorization_and_failure_audit_verified,first_response_ledger_and_candidate_verified,second_stage_pending_and_workspace_ledger_clear,independent_transport_or_provider_proof,conclusion,assumptions,assessment_hash';

function readingCommand(row) {
  const body = row.preview.body;
  return {request_id: 'fl_' + crypto.randomUUID(), revision: body.revision, model_id: body.model_id,
    material_refs: structuredClone(body.material_refs), selection: structuredClone(body.selection), note: body.note};
}

async function checked(value, account, row, command) {
  await verifyFullDraft(row, account);
  need(fullKeys(value, 'schema,basis,reading_preview,can_start,start_blocker,can_apply,can_publish,linked_preview_hash') &&
    value.schema === 'report-evidence-delegated-preview-v1' && value.can_start === false &&
    typeof value.start_blocker === 'string' && value.start_blocker.length > 0 &&
    value.can_apply === false && value.can_publish === false && fullSha.test(value.linked_preview_hash || ''));
  const {linked_preview_hash, ...unsigned} = value;
  need(await digest(unsigned) === linked_preview_hash);
  const b = value.basis, p = value.reading_preview, old = row.preview;
  need(fullKeys(b, basisKeys) && b.schema === 'report-evidence-delegated-basis-v1' &&
    b.workspace_id === account.workspace_id && b.actor_id === account.user_id &&
    b.original_draft_id === row.id && b.original_revision === row.revision && b.original_job_id === row.job_id &&
    b.original_start_receipt_hash === row.start_receipt.receipt_hash && b.original_source_hash === old.source_hash &&
    b.original_projection_hash === old.projection_hash && fullSha.test(b.original_first_raw_hash || '') &&
    fullSha.test(b.original_first_candidate_receipt_hash || '') && b.operator_asserted_history_hash === null &&
    b.history_digest_verification === 'not_provided' && b.historical_second_send === 'not_independently_proven' &&
    b.source_relationship === 'same_report_word_and_ocr_views_overlap' && b.new_analysis === true &&
    b.not_original_batch_recovery === true && b.authorization_basis === 'authenticated_actor_submitted_request' &&
    b.user_reviewed_history === false && b.user_confirmed_business === false && b.duplicate_cost_risk === true);
  await verifyEvidencePreview(p, account, command.reading);
  need(p.preview_hash === b.new_preview_hash && p.source_hash === old.source_hash && p.projection_hash === old.projection_hash &&
    p.configuration_hash === old.configuration_hash && p.published_model_hash === old.model_hash &&
    p.provider === old.provider && p.model === old.model && same(p.body.selection, old.body.selection));
  const oldHashes = old.batches.map(item => item.messages_hash);
  const newHashes = p.map_preparation.plan.chunks.map(item => item.messages_hash);
  need(oldHashes.length === 2 && same(b.original_messages_hashes, oldHashes) &&
    newHashes.length > 0 && same(b.new_messages_hashes, newHashes) && new Set(newHashes).size === newHashes.length &&
    newHashes.every(hash => fullSha.test(hash || '') && !oldHashes.includes(hash)) &&
    p.input_summary.map_call_count === newHashes.length && fullInt(p.input_summary.map_token_bound));
  const a = b.history_assessment;
  need(fullKeys(a, assessmentKeys) && a.schema === 'report-evidence-history-assessment-v1' &&
    fullSha.test(a.assessment_hash || '') && fullSha.test(a.original_job_data_hash || ''));
  const {assessment_hash, ...observation} = a;
  need(await digest(observation) === assessment_hash &&
    ['workspace_id','actor_id','original_draft_id','original_revision','original_start_receipt_hash','original_source_hash','original_job_id']
      .every(key => a[key] === b[key]) && a.second_messages_hash === oldHashes[1] &&
    a.original_job_failed_single_attempt_no_active_lease === true &&
    a.original_authorization_and_failure_audit_verified === true &&
    a.first_response_ledger_and_candidate_verified === true &&
    a.second_stage_pending_and_workspace_ledger_clear === true &&
    a.independent_transport_or_provider_proof === false &&
    a.conclusion === 'conditional_engineering_inference_not_provider_proof' && same(a.assumptions, assumptions));
  return {model: p.model, calls: p.input_summary.map_call_count, bound: p.input_summary.map_token_bound,
    coverage: p.source_snapshot.coverage};
}

/** Preview only: no durable command, no model call, no start or approval. */
export default function FullReadingDelegatedPreview({account, row, Btn='button', request=json}) {
  const canPreview = ['admin', 'operator'].includes(account?.role);
  const expected = !!(fullUUID.test(account?.user_id || '') && fullUUID.test(account?.workspace_id || '') &&
    account?.role && row?.actor_id === account.user_id && row?.workspace_id === account.workspace_id &&
    fullUUID.test(row?.id || '') && fullInt(row.revision, 1) && fullSha.test(row?.view_hash || '') &&
    row.status === 'failed' && row.extractions?.length === 2 && row.preview?.body?.selection);
  const scope = JSON.stringify([account?.user_id, account?.workspace_id, account?.role, row?.id, row?.revision,
    row?.view_hash, row?.preview?.preview_hash]);
  const live = useRef({scope, generation:0, serial:0, controller:null});
  if (live.current.scope !== scope) { live.current.scope = scope; live.current.generation++; live.current.serial++; }
  const generation = live.current.generation, [view, setView] = useState(null);
  const current = view?.scope === scope && view?.generation === generation ? view : null;
  useEffect(() => () => {live.current.serial++; live.current.controller?.abort(); live.current.controller = null;}, [scope]);

  async function load() {
    if (!expected || !canPreview || current?.phase === 'loading') return;
    const serial = ++live.current.serial, ownGeneration = live.current.generation, controller = new AbortController();
    live.current.controller?.abort(); live.current.controller = controller;
    const active = () => live.current.scope === scope && live.current.generation === ownGeneration &&
      live.current.serial === serial && !controller.signal.aborted;
    setView({scope, generation:ownGeneration, phase:'loading'});
    try {
      await verifyFullDraft(row, account);
      const command = {original_draft_id:row.id, original_revision:row.revision, reading:readingCommand(row)};
      const reply = await request('/setup/report-evidence-delegated/preview', {method:'POST', signal:controller.signal,
        cache:'no-store', headers:{'X-Workspace-ID':account.workspace_id}, body:JSON.stringify(command)});
      const result = await checked(reply, account, row, command);
      if (active()) setView({scope, generation:ownGeneration, phase:'ready', result});
    } catch (error) {
      if (active()) setView({scope, generation:ownGeneration, phase:'error',
        message:error?.status === 401 || error?.status === 403 ? '当前账号或工作区权限已变化，内容已隐藏。' :
          error?.status === 409 ? '原报告、现有任务或模型配置暂无法核对，已保存内容保留。' :
          error?.message === 'invalid' ? '预览与原记录不一致，已停止显示。' : '暂时无法核对，请稍后重试。'});
    } finally { if (live.current.controller === controller) live.current.controller = null; }
  }
  if (!expected) return null;
  const result = current?.result;
  return <section aria-label="独立分段分析预览"><h4>分段分析这份报告</h4>
    <p>本次操作只核对服务器中的报告与模型范围，不创建任务、不调用模型。</p>
    {!canPreview && <p>当前账号只能查看已保存记录，不能核对新的分析范围。</p>}
    <Btn type="button" disabled={!canPreview || current?.phase === 'loading'} onClick={load}>
      {current?.phase === 'loading' ? '正在核对分析范围…' : result ? '重新核对分析范围' : '核对分段分析范围'}
    </Btn>
    {current?.phase === 'error' && <p role="alert">{current.message}</p>}
    {result && <div role="status">
      <p>这将是一个独立的新分析；原任务和原回答保留，不是恢复原来的第二批。</p>
      <p>服务器记录支持第二批未发送的工程推断，但缺少供应商或独立网络证明；结论依赖运行环境、账本完整及没有其他外部发送的假设。</p>
      <p>{result.model} · Word 正文 {result.coverage.word_blocks} 段，PDF 识别文字 {result.coverage.selected_pages} 页；两种视图来自同一份报告，内容有重叠，不是独立证据。</p>
      <p>计划分 {result.calls} 批，保守额度上限 {result.bound.toLocaleString()} token；费用金额尚无法确定，资料重叠可能造成额外费用。后续汇总可能另行计费。</p>
      <p>识别文字、图片和图表含义仍需核对；机器候选不等于人工确认的业务，也不会自动应用。</p>
      <p>当前定向额度和安全派发尚未接通，暂时不能开始。本次核对操作不会创建分析任务或调用模型；既往发送状态仍需核验。</p>
    </div>}
  </section>;
}
