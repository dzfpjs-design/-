import test from 'node:test';import assert from 'node:assert/strict';import {defaults,normalize,problems,preview,parseChange} from '../src/automation-schema.js';import {initialWorkspace} from '../src/catalog.js';
function fixture(){const c=defaults(['A','B']),p=c.policies.A,w=initialWorkspace();p.timezone='Asia/Shanghai';for(const r of Object.values(p.rules))r.mode='仅给出建议';return {c,p,w};}
test('业务规则独立且默认中国时间，无需确认时区',()=>{const {c,p,w}=fixture();p.triggers=['定时运行'];p.timezone='';assert.ok(!problems(p,w,'A').some(x=>x.includes('时区')));assert.equal(defaults(['A']).policies.A.timezone,'Asia/Shanghai');p.rules.supplement.mode='自动运行';assert.equal(c.policies.B.rules.supplement.mode,'执行前确认');});
test('候选或无业务绑定资源不会变成授权',()=>{const {p,w}=fixture();const r=p.rules.supplement;r.mode='自动运行';r.resources=['control-0'];r.cap='云台转向';r.scope='管理区域';assert.match(problems(p,w,'A').join(),/尚未验证接入/);assert.match(problems(p,w,'A').join(),/尚未关联当前业务/);assert.match(problems(p,w,'A').join(),/逐项验证/);assert.match(preview(p,w,'A','感知不足')[0],/暂不能执行/);});
test('控制需身份和回执；写入需能力与幂等',()=>{const {p,w}=fixture();const r=p.rules.write;r.mode='自动运行';r.resources=['tools-0'];r.scope='业务记录';r.cap='业务写操作';r.idempotent=false;assert.match(problems(p,w,'A').join(),/查询执行结果.*失败恢复/);assert.match(problems(p,w,'A').join(),/逐项验证/);});
test('异常报告不因后续确认等待，未知回执禁止盲重试',()=>{const {p,w}=fixture();assert.match(preview(p,w,'A','异常事件')[0],/立即报告/);assert.match(preview(p,w,'A','未知回执')[1],/避免重复/);});
test('学习发布无法直接自动生效',()=>{const {p,w}=fixture();p.rules.learn.mode='自动运行';assert.match(problems(p,w,'A').join(),/不能直接自动发布/);});
test('导入草稿清理凭证与无效模式，未知自然语言不伪造',()=>{const c=defaults(['A']);c.policies.A.rules.write.mode='bad';c.policies.A.rules.write.scope='password=abcdefghijk';const n=normalize(c,['A']);assert.equal(n.policies.A.rules.write.mode,'执行前确认');assert.match(n.policies.A.rules.write.scope,/已移除/);assert.equal(parseChange('请完善一下'),null);assert.equal(parseChange('补充观测改为执行前确认').action,'supplement');assert.equal(parseChange('补充采集改为执行前确认').action,'supplement');});

test('runtime fresh businesses default only to manual startup with every action advisory',()=>{
 const current=normalize(null,['新的业务'],{},true).policies['新的业务'];assert.deepEqual(current.triggers,['人工发起']);assert.ok(Object.values(current.rules).every(rule=>rule.mode==='仅给出建议'));
 const manual=defaults(['A'],true).policies.A;assert.deepEqual(manual.triggers,['人工发起']);assert.ok(Object.values(manual.rules).every(rule=>rule.mode==='仅给出建议'));
 assert.deepEqual(defaults(['A']).policies.A.triggers,['定时运行','异常事件','人工发起']);
});
test('runtime normalization preserves prior human policy and aliases while keeping missing policies conservative',()=>{
 const existing=defaults(['原业务']);existing.policies['原业务'].triggers=['人工发起'];existing.policies['原业务'].rules.write={...existing.policies['原业务'].rules.write,mode:'执行前确认',scope:'人工限定',role:'真实负责人'};
 const before=JSON.stringify(existing),normalized=normalize(existing,['改名业务','新业务'],{'改名业务':'原业务'},true);
 assert.deepEqual(normalized.policies['改名业务'].triggers,['人工发起']);assert.equal(normalized.policies['改名业务'].rules.write.scope,'人工限定');assert.equal(normalized.policies['改名业务'].rules.write.role,'真实负责人');assert.equal(normalized.policies['改名业务'].rules.write.mode,'执行前确认');
 assert.deepEqual(normalized.policies['新业务'].triggers,['人工发起']);assert.ok(Object.values(normalized.policies['新业务'].rules).every(rule=>rule.mode==='仅给出建议'));assert.equal(JSON.stringify(existing),before);
});
