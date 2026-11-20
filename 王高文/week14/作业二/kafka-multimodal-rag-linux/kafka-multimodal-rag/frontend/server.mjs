import { createReadStream } from 'node:fs';
import { createServer, request as httpRequest } from 'node:http';
import { request as httpsRequest } from 'node:https';
import { pathToFileURL } from 'node:url';

const assets = new Map([
  ['/', ['index.html', 'text/html; charset=utf-8']],
  ['/style.css', ['style.css', 'text/css; charset=utf-8']],
  ['/app.js', ['app.js', 'text/javascript; charset=utf-8']],
]);

const hopHeaders = ['connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade'];

function endJson(response, status, error) {
  response.writeHead(status, { 'content-type': 'application/json; charset=utf-8' });
  response.end(JSON.stringify({ error }));
}

export function createFrontend(apiBase) {
  const api = new URL(apiBase);
  const forward = api.protocol === 'https:' ? httpsRequest : httpRequest;
  return createServer((incoming, response) => {
    const path = incoming.url.split('?')[0];
    if (path.startsWith('/api/')) {
      const headers = { ...incoming.headers, host: api.host };
      for (const header of hopHeaders) delete headers[header];
      const upstream = forward(api, { path: incoming.url, method: incoming.method, headers }, (result) => {
        const responseHeaders = { ...result.headers };
        for (const header of hopHeaders) delete responseHeaders[header];
        response.writeHead(result.statusCode, responseHeaders);
        result.on('error', () => response.destroy());
        result.pipe(response);
      });
      upstream.setTimeout(200_000, () => upstream.destroy());
      upstream.on('error', () => {
        if (!response.headersSent) endJson(response, 502, '后端暂时无法响应。');
        else response.destroy();
      });
      incoming.on('error', () => upstream.destroy());
      response.on('close', () => { if (!response.writableEnded) upstream.destroy(); });
      incoming.pipe(upstream);
      return;
    }
    const asset = assets.get(path);
    if (!asset || !['GET', 'HEAD'].includes(incoming.method)) {
      endJson(response, 404, '页面不存在。');
      return;
    }
    const [filename, type] = asset;
    response.setHeader('content-type', type);
    response.setHeader('x-content-type-options', 'nosniff');
    response.setHeader('cache-control', 'no-cache');
    if (incoming.method === 'HEAD') {
      response.end();
      return;
    }
    const stream = createReadStream(new URL(`./public/${filename}`, import.meta.url));
    stream.on('error', () => response.destroy());
    stream.pipe(response);
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const host = process.env.WEB_HOST || '127.0.0.1';
  const port = Number(process.env.WEB_PORT || 3000);
  createFrontend(process.env.API_BASE_URL || 'http://127.0.0.1:8081').listen(port, host, () => {
    console.log(`RAG web: http://${host}:${port}`);
  });
}
