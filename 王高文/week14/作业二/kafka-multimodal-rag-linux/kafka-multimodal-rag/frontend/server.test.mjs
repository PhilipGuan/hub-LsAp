import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from 'node:http';
import test from 'node:test';
import { createFrontend } from './server.mjs';

test('serves the UI and proxies binary upload requests intact', async (t) => {
  const upstream = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    response.writeHead(202, { 'content-type': 'application/octet-stream' });
    response.end(Buffer.concat(chunks));
  }).listen(0, '127.0.0.1');
  t.after(() => upstream.close());
  await once(upstream, 'listening');
  const frontend = createFrontend(`http://127.0.0.1:${upstream.address().port}`).listen(0, '127.0.0.1');
  t.after(() => frontend.close());
  await once(frontend, 'listening');
  const root = `http://127.0.0.1:${frontend.address().port}`;
  assert.match(await (await fetch(root)).text(), /知识库问答/);
  const original = Buffer.from([0x25, 0x50, 0x44, 0x46, 0, 255]);
  const result = await fetch(`${root}/api/documents`, { method: 'POST', body: original });
  assert.equal(result.status, 202);
  assert.deepEqual(Buffer.from(await result.arrayBuffer()), original);
});
