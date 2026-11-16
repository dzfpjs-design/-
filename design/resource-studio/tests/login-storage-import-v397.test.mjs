import test from 'node:test';
import assert from 'node:assert/strict';
test('blocked session storage at module import does not prevent login surface from loading',async()=>{
 const prior=Object.getOwnPropertyDescriptor(globalThis,'sessionStorage');
 Object.defineProperty(globalThis,'sessionStorage',{configurable:true,value:{getItem(){throw new DOMException('blocked','SecurityError');}}});
 try {const mod=await import('../src/runtime-api.js?blocked-import-v397');assert.equal(typeof mod.json,'function');}
 finally {if(prior)Object.defineProperty(globalThis,'sessionStorage',prior);else delete globalThis.sessionStorage;}
});
