# datahidro — instruções para assistentes

Leia o `README.md` primeiro; o `../CLAUDE.md` (mapa do workspace) também vale
aqui. Este arquivo guarda só os invariantes deste repo.

## Invariantes

- **PWA estática sem build + backend Flask** (receita amora/levabici). Um
  `app.js`; IBM Plex Mono vendorada em `lib/fonts/`; nada de CDN.
  `backend/storage.py` é cópia do levabici (que é cópia do amora): bug
  corrigido num, corrige nos outros.
- **`sw.js` `VERSION`** (`datahidro-vN`, monotônica): subir a cada mudança em
  arquivo servido, **inclusive rodar o ingest** (`data/candidates.json`,
  `photos/`). `/api/` e `/health` nunca passam pelo cache.
- **`data/vocab.ttl` é a fonte única dos cargos** (ordem, rótulos,
  `skos:notation` = CD_CARGO, `dh:jurisdiction` = SG_UF, máximo). O ingest copia
  pro `candidates.json`; app e backend leem do JSON. `dh:maxChoices` ↔
  `sh:qualifiedMaxCount` no `shapes.ttl`: mudou um, muda o outro. Não duplicar
  rótulo de cargo no JS.
- **`backend/rdfmodel.py` é o mapeamento único dicionário → RDF** (ingest e
  backend). IRIs sob `https://id.pedalhidrografi.co/datahidro/…`; sem blank
  nodes.
- **Portão SHACL**: resposta com Violation → 422. O backend junta ao grafo da
  resposta só as triplas de catálogo das candidaturas escolhidas (`sh:class`
  pega candidatura desconhecida; `sh:qualifiedMaxCount`, o limite por cargo;
  `sh:closed`, qualquer campo a mais).
- **Resposta anônima: DECISÃO do Danilo (2026-09-12).** Só escolhas +
  consentimento. Nome, contato e "pedala com o Pedal Hidrográfico?" foram
  removidos a pedido; não reintroduzir campos pessoais. Respostas sem rota de
  leitura (bucket privado, public access prevention); IP nunca é gravado.
- **Placar ao vivo por padrão: DECISÃO do coletivo (2026-09-12)**, tomada
  cientes da Lei 9.504/97 art. 33 §5º / Res. TSE 23.600/2019 art. 23 (ver
  README). Não reintroduzir embargo por conta própria:
  `DATAHIDRO_TALLY_OPENS_AT` existe pra isso. `DATAHIDRO_TALLY_MIN_RESPONSES`
  padrão 1: placar desde a primeira resposta (DECISÃO do Danilo, 2026-09-12).
- **`tally.json` é read-modify-write sob `_write_lock`**: Cloud Run
  `--max-instances 1` + gunicorn `--workers 1`. Não subir nenhum dos dois sem
  repensar.
- **Limite de envios** usa `X-Real-IP` (mandado pelo Worker em
  `cloudflare/worker.js`, que o `deploy.sh` publica — ORIGIN vai por `--var`,
  com `keep_vars`, nunca no `wrangler.toml`);
  subrequisição de Worker sem ele (header `CF-Worker`) não é limitada, senão
  todo mundo dividiria o mesmo IP.
- **Todas as candidaturas: DECISÃO do Danilo (2026-09-12).** O ingest não
  filtra gênero (padrão `--genders todos`); "deputadas", "presidenta"… são o
  feminino genérico do coletivo, não um recorte. `--genders FEMININO` existe
  mas não é o padrão. No app, o chip **"só mulheres"** (pedido do Danilo)
  esconde `gender: "MASCULINO"` em todos os cargos (global, no localStorage).
  Layout TSE 2026: situação vem `#NE` (nenhuma inapta sai) e não há
  `ST_CANDIDATO_INSERIDO_URNA`.
- **Catálogo de exemplo** (`tools/make_sample.py`) é fictício, temático e
  marcado `"sample": true`; o app mostra faixa de aviso e o `deploy.sh`
  recusa. Nunca publicar; nunca inventar candidatura com nome real.
- **Ordem aleatória ponderada: DECISÃO do Danilo (2026-09-13).** Sorteio sem
  reposição (`weightedShuffle`), com semente nova a cada carregamento da página
  (recarregar sorteia outra ordem, pedido do Danilo; nada no localStorage) e peso por partido
  `1 + (z − z_max)²` (`partyWeigher`, só no `app.js`): z = `media_z` do GPS
  Partidário 2026 da Folha (negativo = esquerda), z_max = a maior do catálogo
  → NOVO pesa 1, PSTU ~17. O ingest baixa a tabela fixada em `GPS_COMMIT`,
  traduz siglas (`GPS_SIGLAS`: PC do B → PCDOB, PMB → DEMOCRATA), aplica
  substitutos (`GPS_PROXIES`: PCO = PSTU, pedido do Danilo) e grava só a
  `media_z` em `candidates.json` (`party_lean`). Commit novo do GPS → conferir
  o resumo do ingest (partido sem posição, sigla sobrando). O resto da
  neutralidade vale: ✓ na marcação (não posição); a ordem de marcação não é
  registrada; na ordem por votos, o empate desempata pelo sorteio.
- **Selo**: tudo no canvas, a foto não sobe. O Story do placar
  (`drawBoardStory`) desenha as fotos de `photos/`: mesmo domínio, então o
  canvas segue exportável — foto servida de outro domínio sem CORS "suja" o
  canvas e quebra o salvar. A CSP (`backend/main.py`) barra
  `style=""` inline e scripts externos: cor dinâmica via `style.setProperty`.
  `drawBadgeArt(ctx, size)` desenha o anel em qualquer tamanho; o selo
  quadrado e o Story (1080×1920, `drawStoryBadge`) chamam a mesma função —
  não duplicar a arte do anel entre os dois formatos. Salvar direto no
  álbum de fotos (não em Arquivos) só existe via `navigator.share`
  (`shareImage`); `downloadImage`/`<a download>` é o único caminho sem
  suporte a Web Share, e por isso vira a ação primária nesse caso
  (`pickPhoto` troca `btn-primary`/`btn-secondary` dinamicamente).
- **Idioma**: UI, comentários e docs em português; identificadores (JS,
  Python, classes CSS, chaves JSON, API, env vars) em inglês. Rotas de hash
  (`#/deputadas-estaduais`) são texto de interface e ficam em português.

## Verificar antes de terminar

1. `node --check app.js && node --check sw.js`;
   `python3 -m py_compile backend/*.py tools/*.py`
2. `python3 -m pyshacl -s data/shapes.ttl -w data/candidates.ttl` → Conforms: True.
3. Backend local (ver README) e passar pelas 8 telas num navegador de verdade
   a ~390px: marcar, buscar, filtrar, trocar a ordem, enviar, montar o selo.
   (O Chromium headless do Playwright nesta máquina precisa de
   `libasound.so.2`: `apt-get download libasound2t64`, `dpkg -x` numa pasta e
   `LD_LIBRARY_PATH`.)
4. Mudou arquivo servido → `sw.js` `VERSION` +1.

## Commits & deploy

Commit só quando pedido; mensagens em inglês. Deploy com `./deploy.sh` (o
pré-voo recusa catálogo de exemplo e foto faltando).
