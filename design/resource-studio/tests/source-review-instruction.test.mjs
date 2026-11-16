import test from 'node:test';
import assert from 'node:assert/strict';
import {SOURCE_REVIEW_INSTRUCTION,validateSourceReviewPreparation} from '../src/source-review-instruction.mjs';
const models=[{id:'ds',provider:'deepseek'},{id:'q',provider:'qwen'}];
const base={mode:'edit',note:SOURCE_REVIEW_INSTRUCTION,model_id:'ds'};
test('source review requires DeepSeek, a document and retained review instruction',()=>{
 assert.doesNotThrow(()=>validateSourceReviewPreparation(base,['doc'],models));
 assert.throws(()=>validateSourceReviewPreparation(base,[],models),/资料/);
 assert.throws(()=>validateSourceReviewPreparation({...base,model_id:'q'},['doc'],models),/DeepSeek/);
 assert.throws(()=>validateSourceReviewPreparation({...base,mode:'understand'},['doc'],models),/逐项修改/);
 assert.throws(()=>validateSourceReviewPreparation({...base,note:'任意新说明'},['doc'],models),/保留复核要求/);
});
