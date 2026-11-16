import {test} from 'node:test';
import assert from 'node:assert/strict';
import {localObservationReady,localObservationBody,sameLocalObservation} from '../src/local-observation-form.mjs';
test('local file choices require explicit owned object and local published candidate model',()=>{
 const form={entity_id:'object',extractor_id:'model'};
 assert(localObservationReady(form,[{id:'object'}],[{id:'model',deployment:'local',candidate_only:true}]));
 assert(!localObservationReady(form,[],[{id:'model',deployment:'local',candidate_only:true}]));
 assert(!localObservationReady(form,[{id:'object'}],[{id:'model',deployment:'cloud',candidate_only:true}]));
});
test('lost response recovery retains the original file, object, model and request',()=>{
 const a={request_id:'request',entity_id:'object',extractor_id:'model',evidence_id:'file',sha256:'fixed'};
 assert.deepEqual(localObservationBody(a),{request_id:'request',entity_id:'object',extractor_id:'model',evidence_id:'file'});
 assert.throws(()=>localObservationBody({...a,evidence_id:null}));
 const row={...a,source_sha256:'fixed'};assert(sameLocalObservation(row,a));
 for(const field of ['request_id','entity_id','extractor_id','source_sha256'])assert(!sameLocalObservation({...row,[field]:'changed'},a));
});
