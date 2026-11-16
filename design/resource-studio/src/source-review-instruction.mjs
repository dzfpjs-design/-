export const SOURCE_REVIEW_PREFIX = '【对照原文复核】';
export const SOURCE_REVIEW_INSTRUCTION = SOURCE_REVIEW_PREFIX +
  '请逐项对照本次选择的原文，核查当前已保存业务流程的触发、步骤、条件、顺序、负责角色和输出。' +
  '原文有明确要求而当前字段与之不一致时，必须提出对应字段修改并逐项引用原文；' +
  '原文未明确或无法判断的内容列为待确认，不要把空结果当作全面核实；' +
  '不要猜测或扩展设备、账号和执行授权。';

export function validateSourceReviewPreparation(value, ids, models){
  if(value.mode!=='edit'||!value.note.startsWith(SOURCE_REVIEW_PREFIX))
    throw Error('原文复核须保留复核要求，并使用逐项修改模式。');
  if(!ids.length)throw Error('原文复核请至少选择一份已解析的业务资料。');
  if(!models.some(m=>m.id===value.model_id&&m.provider==='deepseek'))
    throw Error('原文复核请使用已启用并发布的 DeepSeek 模型。');
}
