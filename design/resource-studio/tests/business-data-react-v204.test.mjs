import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {act} from 'react';
import {runStudio,cleanup,ids,id} from './business-data-react-v204-fixture.mjs';
after(cleanup);
test('setup uses actual XLSX selection, durable source save, upload metadata and table preview; no object writes or browser original bytes',async()=>runStudio({},async h=>{
 assert.equal(h.field('数据来源类型').value,'xlsx');await h.edit('数据连接名称','月度登记表');await h.click('保存数据连接');await h.until(()=>h.state.sources.length===1&&h.storage.getItem(h.pendingKey)===null,'source saved');
 assert.equal(h.state.posts[0].body.kind,'xlsx');assert.equal(h.state.readCalls,0,'save never reads remote data');
 const bytes=new Uint8Array([80,75,3,4,0,0,0,0]),file=new h.File([bytes],'业务登记.xlsx',{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
 assert.equal(h.field('选择业务数据原文件').accept,'.xlsx');await h.chooseFile(file);await h.click('上传并读取原文件');await h.until(()=>h.state.uploads.length===1&&h.storage.getItem(h.pendingKey)===null,'upload original recovered');
 await h.edit('选择数据记录组','sheet:台账');assert.ok(h.text().includes('作业站甲'));assert.ok(h.text().includes('公式保留为表达式'));
 assert.deepEqual(h.state.uploads[0].bytes,bytes);assert.equal(h.state.posts.filter(p=>p.path.endsWith('/uploads')).length,1);
 assert.equal(h.container.querySelector('[aria-label="目标对象类型"]'),null,'setup never offers object writes');assert.equal(h.state.posts.some(p=>p.path.includes('/bindings')||p.path.includes('/imports')),false);
 const persisted=Array.from({length:h.storage.length},(_,i)=>h.storage.getItem(h.storage.key(i))).join('\n');assert.equal(persisted.includes('base64'),false);assert.equal(persisted.includes('value_text'),false,'remote rows are not browser drafts');assert.equal(persisted.includes('file_sha256'),true);
 await h.unmount();await h.mount();assert.ok(h.text().includes('月度登记表'));assert.ok(h.text().includes('作业站甲'));assert.equal(h.state.uploads.length,1,'remount does not upload again');
}));
test('real controls traverse saved read history, dynamic schema mapping, full differences, explicit partial import and separate ACK recovery',async()=>runStudio({instance:true,preloaded:true},async h=>{
 await h.click('业务登记表');await h.click('查看这份原资料');await h.edit('选择数据记录组','sheet:台账');await h.edit('目标对象类型','custom_station');
 assert.ok(h.text().includes('自定义作业站'));assert.equal(h.text().includes('日常巡护'),false);
 await h.edit('来源业务编号字段','/编号');await h.edit('来源字段：站点名称','/名称');
 await h.unmount();await h.mount();assert.equal(h.field('来源字段：站点名称').value,'/名称','human mapping survives remount');
 await h.click('预览导入差异');await h.until(()=>h.text().includes('此编号已由人工登记'),'preview rows shown');
 assert.ok(h.text().includes('缺少稳定业务编号'));assert.equal(h.button('确认导入这些数据').disabled,true,'problems require explicit partial choice');assert.equal(h.state.posts.some(p=>p.path.endsWith('/confirm')),false);
 await act(async()=>h.field('只导入无冲突记录').click());await h.settle();await h.click('确认导入这些数据');await h.until(()=>h.text().includes('原资料需重新核对接收'),'import committed independently of ACK');
 assert.equal(h.state.import.counts.imported,1);assert.equal(h.state.import.counts.data_received,0);const reads=h.state.readCalls;
 await h.edit('接收重试说明：作业站甲','已恢复原资料，只恢复数据管理接收。');await h.click('按原记录重试数据接收');await h.until(()=>h.state.rows[0].data_delivery.status==='pending_delivery','ACK resumed with original row');assert.equal(h.state.readCalls,reads);assert.equal(h.state.posts.filter(p=>p.path.endsWith('/confirm')).length,1);assert.equal(h.state.import.counts.data_received,0,'retry is not fabricated received');
 h.state.rows[0].data_received=true;h.state.rows[0].data_delivery.status='received';h.state.import.counts.data_received=1;await h.click('刷新这一批的结果');assert.ok(h.text().includes('数据管理已接收'));assert.equal(h.state.import.counts.data_received,1);
 await h.unmount();await h.mount();assert.ok(h.text().includes('本批')||h.text().includes('部分完成'));assert.ok(h.text().includes('此编号已由人工登记'));
}));
test('saved original followed by /me network failure restores on remount; manual same nonce/raw continuation makes exactly one POST',async()=>runStudio({},async h=>{
 await h.edit('数据连接名称','尚未提交的财产登记');h.state.failMe=1;await h.click('保存数据连接');await h.until(()=>h.storage.getItem(h.pendingKey)!==null,'pending persisted');const original=h.storage.getItem(h.pendingKey),p=JSON.parse(original);assert.equal(h.state.posts.length,0);
 await h.unmount();await h.mount();assert.equal(h.storage.getItem(h.pendingKey),original);await h.click('核对原操作结果');assert.equal(h.state.posts.length,0);await h.click('继续提交原内容');await h.until(()=>h.storage.getItem(h.pendingKey)===null,'recovered');assert.equal(h.state.posts.length,1);assert.equal(h.state.posts[0].raw,p.raw);assert.equal(h.state.posts[0].body.request_id,p.body.request_id);assert.ok(h.storage.getItem(h.key+':received:'+p.body.request_id));
}));
test('root JSON record-set empty key is explicitly selectable and reaches preview without confusing it with unselected',async()=>runStudio({instance:true,preloaded:true},async h=>{
 h.state.sources[0].kind='json';h.state.read.name='业务记录.json';h.state.read.record_sets[0].key='';h.state.read.record_sets[0].label='全部业务记录';
 await h.click('业务登记表');assert.equal(h.field('选择数据记录组').value,'__ga_unselected_dataset_v204__');
 await h.edit('选择数据记录组','');assert.ok(h.text().includes('作业站甲'));await h.edit('目标对象类型','custom_station');await h.edit('来源业务编号字段','/编号');await h.edit('来源字段：站点名称','/名称');await h.click('预览导入差异');await h.until(()=>h.state.import!==null&&h.storage.getItem(h.pendingKey)===null,'root dataset preview persisted');
 const binding=h.state.posts.find(p=>p.path==='/business-data/bindings');assert.ok(binding);assert.equal(binding.body.record_set,'');assert.ok(h.text().includes('此编号已由人工登记'));
}));
test('upload response loss is recovered by original request without selecting or sending any second file',async()=>runStudio({preloaded:true},async h=>{
 await h.click('业务登记表');const file=new h.File([new Uint8Array([80,75,3,4])],'业务登记.xlsx');await h.chooseFile(file);h.state.loseUploadResponse=true;await h.click('上传并读取原文件');await h.until(()=>h.storage.getItem(h.pendingKey)!==null,'lost response pointer kept');assert.equal(h.state.uploads.length,1);
 await h.unmount();await h.mount();await h.click('核对原操作结果');await h.until(()=>h.storage.getItem(h.pendingKey)===null,'existing original recovered');assert.equal(h.state.uploads.length,1);assert.ok(h.text().includes('可查看原资料'));
}));
test('upload not sent before network failure requires same original File after remount; another file cannot replace its nonce',async()=>runStudio({preloaded:true},async h=>{
 await h.click('业务登记表');const file=new h.File([new Uint8Array([80,75,3,4,5])],'业务登记.xlsx');await h.chooseFile(file);h.state.failMe=1;await h.click('上传并读取原文件');await h.until(()=>h.storage.getItem(h.pendingKey)!==null,'upload original durable');const original=h.storage.getItem(h.pendingKey);assert.equal(h.state.uploads.length,0);
 await h.unmount();await h.mount();await h.click('核对原操作结果');await h.click('继续提交原内容');assert.ok(h.text().includes('请重新选择同一个原文件'));assert.equal(h.state.uploads.length,0);
 await h.chooseFile(new h.File([new Uint8Array([1,2,3,4,5])],'业务登记.xlsx'),'恢复原上传文件');await h.click('继续提交原内容');assert.ok(h.text().includes('所选文件与原上传不一致'));assert.equal(h.storage.getItem(h.pendingKey),original);assert.equal(h.state.uploads.length,0);
 await h.chooseFile(file,'恢复原上传文件');await h.click('继续提交原内容');await h.until(()=>h.storage.getItem(h.pendingKey)===null,'same file accepted once');assert.equal(h.state.uploads.length,1);assert.equal(h.state.posts[0].raw,JSON.parse(original).raw);
}));
test('HTTP JSON source queues only an explicit read after save; source save alone never reads',async()=>runStudio({},async h=>{
 await h.edit('数据连接名称','公开业务接口');await h.edit('数据来源类型','http_json');await h.edit('只读接口地址','https://provider.invalid/records');await h.click('保存数据连接');await h.until(()=>h.storage.getItem(h.pendingKey)===null&&h.state.sources.length===1,'HTTP source saved');assert.equal(h.state.readCalls,0);assert.equal(h.state.posts[0].body.kind,'http_json');
 await h.click('读取接口数据');await h.until(()=>h.storage.getItem(h.pendingKey)===null&&h.state.readCalls===1,'explicit read recovered');assert.equal(h.state.posts.filter(p=>p.path.endsWith('/read')).length,1);assert.equal(h.state.uploads.length,0);
}));
test('current schema change keeps human mapping and blocks new preview until explicitly checked against the new schema',async()=>runStudio({instance:true,preloaded:true},async h=>{
 await h.click('业务登记表');await h.edit('选择数据记录组','sheet:台账');await h.edit('目标对象类型','custom_station');await h.edit('来源业务编号字段','/编号');await h.edit('来源字段：站点名称','/名称');h.state.schema='9'.repeat(64);await h.click('刷新数据记录');await h.click('预览导入差异');
 assert.ok(h.text().includes('业务版本或字段已变化'));assert.equal(h.field('来源字段：站点名称').value,'/名称');assert.equal(h.state.posts.some(p=>p.path==='/business-data/bindings'),false);assert.equal(h.storage.getItem(h.pendingKey),null);
}));
for(const changed of ['user','workspace'])test('actual '+changed+' switch hides original rows and user input while retaining pending and refusing stale writes',async()=>runStudio({},async h=>{
 await h.edit('数据连接名称','原账号专属输入');h.state.failMe=1;await h.click('保存数据连接');await h.until(()=>h.storage.getItem(h.pendingKey)!==null,'pending exists');const original=h.storage.getItem(h.pendingKey),old=h.button('核对原操作结果');h.session.setItem(changed==='user'?'ga-setup-csrf-user':'ga-setup-csrf-workspace',id(90));await act(async()=>h.dom.window.dispatchEvent(new h.dom.window.Event('focus')));await h.settle();assert.ok(h.text().includes('已隐藏并保留'));assert.equal(h.container.querySelector('input'),null);assert.equal(h.text().includes('原账号专属输入'),false);const calls=h.state.calls.length;await act(async()=>old.click());await h.settle();assert.equal(h.state.calls.length,calls);assert.equal(h.state.posts.length,0);assert.equal(h.storage.getItem(h.pendingKey),original);
}));
