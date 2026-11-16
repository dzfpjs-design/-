import NativeQueryGrounding from './NativeQueryGrounding.jsx';
import React from 'react';
import {queryEffectState} from './native-query-effect-contract.mjs';
import {operationLabels,proposalFieldLabel} from './native-query-proposal-contract.mjs';
const unit=value=>value?'（'+value+'）':'（单位未声明，不推断换算）';
const recordUnit=value=>value==='evidence_records'?'原资料的证据记录行':value==='aggregate_rows'?'前序聚合结果行':'尚不能可靠解释的输入行';
export default function NativeQueryEffect({row,catalog={}}){
 catalog=catalog??{};
 const state=queryEffectState(row),effect=state.effect,steps=row.candidate?.tool_steps||[];
 const producer=id=>id==='evidence'?'本任务原资料':steps.some(step=>step.id===id)?'第'+(steps.findIndex(step=>step.id===id)+1)+'步返回的资料':'尚未核实的前序资料';
 const fields=items=>items.map(item=><span key={item.key}>{['value','sample_count','field','operation'].includes(item.key)?proposalFieldLabel(catalog,item.key):item.label===item.key&&!catalog.fields?.some(field=>field.key===item.key)?'尚未核实的登记字段':item.label}{unit(item.unit)}；</span>);
 function ranked(value){if(value.ranked_subset===null)return <p>排名范围尚未可靠解释。</p>;if(!value.ranked_subset)return <p>此输入不继承其他分支的排名子集限制。</p>;return <div><p>仅针对实际返回的排名子集，不能代表全部匹配资料。</p>{value.subset_limits.map((limit,index)=><p key={index}>{producer(limit.step_id)}的返回限制：最多 {limit.parameters.top_k} 条；最低匹配分 {limit.parameters.min_score}；{operationLabels[limit.parameters.match_mode]||'匹配条件待核对'}。匹配分不是业务阈值。</p>)}</div>;}
 function output(value){return <><p>{value.shape==='unknown'?'该产出形态尚不能可靠解释。':value.cardinality==='one_on_success'?'成功时形成一条聚合结果；空输入报错，不生成统计事实。':value.record_unit==='aggregate_rows'?'保留筛选后的聚合结果行，可能为零条。':'逐条保留证据记录，可能为零条；不等同现场实物数。'}</p><p>输出字段：{fields(value.fields)}</p>{ranked(value)}</>;}
 const relation=effect?.source_scope.relation_query;
 const objectName=id=>catalog.objects?.find(item=>item.id===id)?.label||'原任务已选对象';
 const relationName=id=>{const item=catalog.relation_types?.find(item=>item.id===id);return item?.title||item?.label||'原声明关系';};
 return <section className="media-session" aria-label="原需求与服务端计划效果"><h5>核对原需求与实际计划效果</h5><h6>这条建议对应的原需求</h6><p style={{whiteSpace:'pre-wrap'}}>{typeof row.original_question==='string'&&row.original_question.trim()?row.original_question:'此记录未提供可核对的原需求摘要，请查看已保存的原发送；不使用后来编辑的需求替代。'}</p>
 <NativeQueryGrounding row={row}/>
 {state.legacy?<p role="status">这是历史计划，未保存新版服务端效果摘要。下方保留原计划和引用，不补写历史结论。</p>:!state.valid?<p role="alert">实际计划效果未完整读取或与原计划不一致。原计划和原答保留；请刷新核对后再确认或采用。</p>:effect.status==='not_applicable'?<p>此回复要求补充说明，尚未形成可执行计划；不能把模型追问当作已完成查询。</p>:<>
 <p>以下由服务端根据已校验的实际字段、工具及输入关系派生，尚未执行，也不证明含义符合原需求。</p>
 {effect.status==='unknown'&&<p role="alert">该步骤产出或单位尚不能可靠解释，请核对下方原计划；未明确前不能据此摘要确认或采用。</p>}
 <h6>将读取的业务字段</h6><p>{fields(effect.selected_fields)}</p><p>出处由实际证据的对象、版本、登记及来源引用提供；不需要为了“出处”额外读取业务名称。不同资料可有不同元数据，不保证每条都有全部种类。</p>
 <h6>读取范围</h6>{relation?<p>从{relation.root_ids.map(objectName).join('、')}，沿{relation.relation_types.map(relationName).join('、')}，{relation.direction==='incoming'?'反向查找关联来源':'向外查找关联目标'}，最多 {relation.max_hops} 跳；{relation.include_roots?'包括起点':'排除起点'}。{relation.at?'按已保存的历史关系时点':'按当前有效关系'}。读取满足该范围的结果集合，不要求模型猜选某一个到达对象；尚未解析实际到达节点。</p>:<p>仅限本任务明确选择的对象及原资料要求，尚未读取或推断实际记录数。</p>}
 {effect.steps.map((step,index)=><article className="media-session" key={step.id}><h6>第{index+1}步 · {operationLabels[step.operation]||operationLabels[step.method]||'原登记工具'}</h6><p>实际输入来自{producer(step.input_from)}；输入字段：{fields(step.input.fields)}。</p>{ranked(step.input)}{step.denominator&&<><p><strong>统计分母：</strong>{producer(step.denominator.frame_id)}中的{recordUnit(step.denominator.record_unit)}，使用本步输入的全部行。{step.denominator.record_unit==='aggregate_rows'?'前序一行聚合结果不是原始样本总数。':'同一对象的多次登记可以形成多条记录，记录数不等于对象数或实物件数。'}</p><p>{step.denominator.value_requirement==='finite_number_not_boolean'?'必须是有限数值，不接受布尔值。':'字段必须存在、非空且有限；计数不按数值大小或真假跳过。'}缺项不静默省略；没有输入时不产生统计数值。</p></>}{output(step.output)}</article>)}
 <h6>最终交付形态</h6>{effect.final&&output(effect.final)}<p>单位与分母分别核对：登记的通用计数单位不能替代实际输入分母。这里只说明计划，不承诺结果数量、语义正确或业务收益。</p>
 </>}
 </section>;
}
