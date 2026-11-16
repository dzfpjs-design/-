import React from 'react';
import {ComparisonEvidence} from './GeoPairs';
import {comparisonEvidenceRow} from './comparison-form.mjs';

export default function WorkflowTaskComparison({task,receipt,account,Btn}){
 const sources=task.comparison_scene_snapshots||[],output=receipt?.output,result=output?.spatial_comparison,id=output?.geo_pair_id;
 if(!sources.length)return null;
 const base='/workspace/tasks/'+task.id+'/comparisons/'+id;
 return <section className="local-model-card"><h3>业务双影像对照依据</h3>{sources.map((source,i)=><p key={source.id}>{i?'B对照影像':'A参考影像'}：{source.snapshot.name} · 固定范围 {source.snapshot.roi.join(', ')} · RGB {source.snapshot.configuration.rgb_bands.join('/')} · 采集时间 {source.snapshot.configuration.observed_at||'未知'} <a className="btn" href={'/api/geo/scenes/'+encodeURIComponent(source.scene_id)+'/original?workspace_id='+encodeURIComponent(account.workspace_id)}>下载{i?'B':'A'}{source.snapshot.wms_origin?'WMS派生影像':'影像原件'}</a></p>)}
  <p>{sources.some(source=>source.snapshot.wms_origin)&&'WMS来源是服务渲染图，不是卫星原始波段；采集时间未知。'}两份输入及设置在发起时保存；单幅分割使用另一组任务输入。B范围外及无效像素为未知，不认定无变化。</p>
  {result?<><p>共同有效 {result.common_valid_pixels}/{result.total_pixels} 像素 · 未知 {result.unknown_pixels} · {result.learned_model?'模型变化候选':'差异候选'} {result.candidate_pixels} 像素</p><p>{result.learned_model?'固定概率阈值 '+result.probability_threshold:'固定颜色差异阈值 '+result.threshold} · {result.alignment==='same_grid'?'原网格一致':'B最近邻重投影到A网格'}。实测配准、云影屏蔽及业务变化质量未核验。</p>
   {result.geometry_issue&&<p className="warning-text">地图候选边界未生成：{result.geometry_issue}；像素证据仍保留。</p>}
   <ComparisonEvidence row={comparisonEvidenceRow(id,result)} basePath={base} compact account={account} Btn={Btn}/>
   {result.learned_model&&<div className="actions"><a className="btn" href={'/api'+base+'/artifacts/probability_raster?workspace_id='+encodeURIComponent(account.workspace_id)}>下载业务概率GeoTIFF</a><a className="btn" href={'/api'+base+'/artifacts/change_mask?workspace_id='+encodeURIComponent(account.workspace_id)}>下载业务候选掩膜GeoTIFF</a></div>}
   <a className="btn" href={'/api'+base+'/export?workspace_id='+encodeURIComponent(account.workspace_id)}>导出业务对照与真实核查说明</a><small>说明通过当前业务步骤保存，数据管理回执为候选；不自动更改对象或发布经验。</small>
  </>:<p>本地业务对照尚无已保存结果；运行进度、失败与重试在当前节点中显示，不用资源页样例代替。</p>}
 </section>;
}
