import test from 'node:test';
import assert from 'node:assert/strict';
import {measurementUnit} from '../src/measurement-unit-v342.mjs';

test('unit labels distinguish packet declarations, confirmed mapping and unknown',()=>{
 assert.equal(measurementUnit({unit:'℃',basis:'message'},0),'℃（原报文提供）');
 assert.equal(measurementUnit({unit:'%',basis:'confirmed_source_mapping'},70),'%（按已确认接入映射）');
 assert.equal(measurementUnit({unit:null,basis:'unknown'},3),'单位未核对');
 assert.equal(measurementUnit({unit:'℃',basis:'untrusted'},3),'单位未核对');
 assert.equal(measurementUnit(null,3),'单位未核对');
 assert.equal(measurementUnit({unit:'℃',basis:'message'},'文字'),'—');
});
