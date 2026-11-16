import React from 'react';
const states={succeeded:'处理成功',failed:'处理失败',cancelled:'已取消',queued:'等待处理',running:'正在处理',saved:'登记已保存',received:'登记已保存、尚未提取',processed:'已保存提取结果'};

export default function AdviceBasis({audit,references,Reference}) {
  if(!audit)return <p className="advice-history-limit">这份历史建议未保存本地依据核对卡。原建议和当时引用保留，不能按当前资料补写历史判断。</p>;
  const byId=new Map(references.map(r=>[r.id,r]));
  const cite=id=>byId.has(id)?<Reference reference={byId.get(id)}/>:<p>该原引用不可用，请保留记录核对。</p>;
  const original=audit.original_task,review=audit.saved_business_review;
  const relations={same_originals:'文件内容相同，观测独立性未核实',different_originals:'文件内容不同，不能直接比较数量',identity_unverified:'原件身份未核实'};
  return <section className="advice-basis" aria-label="本地依据核对">
    <h4>本地依据核对</h4>
    <p>{audit.scope}。这些内容由本地服务核对保存记录生成。</p>
    {original&&<article><strong>原分析任务：{original.status_label}</strong><p>{original.saved_steps.length} 步处理说明已保存。{original.limit}</p><details><summary>查看原任务已保存步骤与说明</summary>{original.saved_steps.map((s,i)=><div key={i}><strong>{s.name}</strong><p>{s.note||'未提供文字说明'}{s.note_excerpt?'（摘录）':''}</p></div>)}{cite(original.reference_id)}</details></article>}
    {review&&<article><strong>原线索判断已保存：{review.decision}</strong><p>{review.note||'未提供文字说明'}{review.note_excerpt?'（摘录）':''}</p><p>{review.limit}</p><details><summary>核对已保存判断的原引用</summary>{cite(review.reference_id)}</details></article>}
    {audit.records.length>0&&<details><summary>已保存数值与来源（{audit.records.length} 份）</summary>{audit.records.map(r=><article key={r.reference_id}><strong>{r.name}</strong><p>{states[r.processing_status]||'处理状态需核对'} · {r.source_group||'原件身份未核实'} · {r.integrity_verified?'保存记录已核对':'保存记录未通过核对'} · {r.time_verified?'来源时间标为已核实':'来源采集时间未核实'}</p>{!r.values.length&&<p>本次核对卡未列出可直接核对的数值，不表示没有数据或异常。</p>}{r.values.map((v,i)=><p key={i}>{v.label}：{String(v.value)}。{v.origin}。</p>)}{r.values_omitted>0&&<p>还有 {r.values_omitted} 项保存数值未列出，请核对原引用。</p>}<p>{r.limit}</p>{cite(r.reference_id)}</article>)}</details>}
    {audit.comparisons.length>0&&<details><summary>来源关系与数量比较（{audit.comparisons.length} 对）</summary>{audit.comparisons.map((c,i)=><article key={i}><strong>{relations[c.relation]||'来源关系待核对'}</strong><p>{audit.records.find(r=>r.reference_id===c.left_reference_id)?.name} ↔ {audit.records.find(r=>r.reference_id===c.right_reference_id)?.name}</p><p>{c.explanation}</p><p>当前不支持据此判断数值冲突、现场变化或异常。</p>{cite(c.left_reference_id)}{cite(c.right_reference_id)}</article>)}</details>}
    {audit.omitted_references.length>0&&<p role="note">有 {audit.omitted_references.length} 项引用被截取或不能完整读取，未用于本地事实卡；不能把未读取部分当作已核对。</p>}
    <p role="note">{audit.limit}</p>
  </section>;
}
