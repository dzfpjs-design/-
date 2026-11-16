// Shared by nested business cards. A queued transport must recheck its
// original lifecycle before touching the network, including POST bodies.
let active=0;
const waiting=[];
// Keep the retained answer readable while context and history are slow.
const LIMIT=3;

export async function withScopedRequest(run,check){
 if(active>=LIMIT)await new Promise(resolve=>waiting.push(resolve));else active++;
 try{check();return await run();}
 finally{const next=waiting.shift();if(next)next();else active--;}
}
