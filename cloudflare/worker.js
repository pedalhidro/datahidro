// datahidro — Worker da Cloudflare na frente do Cloud Run.
//
// pesquisa.pedalhidrografi.co → serviço Cloud Run `datahidro`. Proxy puro, com
// o que o backend precisa:
//   • URL reescrita pro host *.run.app (env.ORIGIN): o Cloud Run responde 404
//     pra Host customizado (mesma regra do cameratopo).
//   • X-Real-IP = CF-Connecting-IP: sem isso todo mundo chega com o IP do
//     Worker e o limite de envios do backend fica desligado.
//   • GET /api/tally passa pelo cache da borda respeitando o max-age=10 da
//     origem: muita gente olhando o placar não vira rajada no Cloud Run.
// ORIGIN não fica no wrangler.toml: o deploy.sh passa com --var (keep_vars).

export default {
  async fetch(request, env) {
    if (!env.ORIGIN) {
      return new Response('datahidro: Worker sem ORIGIN configurado', { status: 503 });
    }
    const incoming = new URL(request.url);
    const target = new URL(incoming.pathname + incoming.search, env.ORIGIN);

    const headers = new Headers(request.headers);
    headers.set('X-Real-IP', request.headers.get('CF-Connecting-IP') || '');
    headers.set('X-Forwarded-Host', incoming.host);
    headers.set('X-Forwarded-Proto', incoming.protocol.replace(':', ''));

    const init = {
      method: request.method,
      headers,
      body: request.method === 'GET' || request.method === 'HEAD' ? null : request.body,
      redirect: 'manual',
    };
    if (request.method === 'GET' && incoming.pathname === '/api/tally') {
      init.cf = { cacheEverything: true };
    }
    return fetch(target, init);
  },
};
