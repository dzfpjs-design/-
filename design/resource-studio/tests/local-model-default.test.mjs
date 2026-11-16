import {test} from 'node:test';
import assert from 'node:assert/strict';
import {categories,validateConnection} from '../src/catalog.js';
import {makeGatewayForm,cleanGatewayForm,localProtocols} from '../src/gateway-schema.js';

test('professional model protocols default to local while foundation clouds are separate',()=>{
 const form=makeGatewayForm({protocol:'专业模型 HTTP'},'models');
 assert.equal(cleanGatewayForm(form).protocolValues['专业模型 HTTP'].deployment,'本地运行节点');
 const c=categories.find(c=>c.id==='models');
 assert(c.protocols.includes('Qwen')&&c.protocols.includes('DeepSeek')&&c.protocols.includes('本地受管模型'));
 assert(localProtocols.includes('本地受管模型'));
 assert.equal(validateConnection({name:'本地检测节点',protocol:'本地受管模型',endpoint:''},c),'');
});
