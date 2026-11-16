// Root invokes with invented values; no browser/network/providers.
import fs from 'node:fs';
import {verifyGraphWire,graphWireDigest} from '../src/full-semantic-graph-wire.mjs';
const value=verifyGraphWire(JSON.parse(fs.readFileSync(0,'utf8')));
process.stdout.write(JSON.stringify({whole:await graphWireDigest(value),unsealed:await graphWireDigest(value,{omit:'hash'}),nested:await graphWireDigest(value.nested),array:await graphWireDigest(value.nested.values)}));
