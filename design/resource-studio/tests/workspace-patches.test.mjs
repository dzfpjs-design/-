import test from 'node:test';
import assert from 'node:assert/strict';
import {mergeWorkspacePatch} from '../src/workspace-patches.mjs';
test('late save receipts preserve edits in another page and sibling draft',()=>{
 const baseline={instanceProfile:{goal:'old'},localRuleDrafts:{A:{title:'a'},B:{rationale:'old'}}};
 const current={...baseline,instanceProfile:{goal:'new'},localRuleDrafts:{...baseline.localRuleDrafts,B:{rationale:'new'}}};
 const next=mergeWorkspacePatch(current,baseline,{localRuleDrafts:{...baseline.localRuleDrafts,A:{title:'saved'}}});
 assert.equal(next.instanceProfile.goal,'new');assert.equal(next.localRuleDrafts.B.rationale,'new');assert.equal(next.localRuleDrafts.A.title,'saved');
});
test('nested source and rule receipts keep each others latest version',()=>{
 const baseline={mqttGatewayDrafts:{source:{baseVersion:1}},localRuleDrafts:{rule:{baseVersion:1}}};
 const source=mergeWorkspacePatch(baseline,baseline,{mqttGatewayDrafts:{source:{baseVersion:2}}});
 const both=mergeWorkspacePatch(source,baseline,{localRuleDrafts:{rule:{baseVersion:3}}});
 assert.equal(both.mqttGatewayDrafts.source.baseVersion,2);assert.equal(both.localRuleDrafts.rule.baseVersion,3);
});
