# datahidro

*Levantamento Pedal Hidrográfico de candidatas às eleições de 2026 em São Paulo.*

Página mobile-first em **pesquisa.pedalhidrografi.co**, uma tela ("deck") por etapa:

1. **Início**: o que é, como funciona, privacidade.
2. **Deputadas estaduais** · 3. **Deputadas federais** · 4. **Senadoras** ·
   5. **Governadora** · 6. **Presidenta**: até 10 escolhas por cargo numa grade de
   3 colunas (foto, nome de urna, número, partido), com busca (nome, nome
   completo, número, partido), filtro por partido, chip **só mulheres** (vale
   pra todos os cargos e fica lembrado no aparelho) e ordem **pelas mais
   escolhidas** (placar ao vivo) ou **aleatória**, um sorteio ponderado pela
   posição do partido (ver [Ordem aleatória](#ordem-aleatória-gps-partidário-2026)).
7. **Revisar e enviar**: resumo + autorização. A resposta é anônima.
8. **Obrigada + selo**: a pessoa escolhe uma foto, enquadra e baixa/compartilha
   a foto de perfil com o anel "eu participei do datahidro 2026". Tudo no
   navegador: a foto não sobe pro servidor. Ao lado do Story do selo, um
   segundo Story com o placar ("Quem está na frente": totais + top 5 de cada
   cargo), pra divulgar o andamento do levantamento.

## Arquitetura

Frontend na receita das PWAs irmãs (`amora`, `levabici`): sem build,
`index.html` + `style.css` + `app.js`, `sw.js` com `VERSION`, IBM Plex Mono
vendorada em `lib/fonts/`, rotas de hash. Backend na receita do
`levabici/backend`: um Flask (`backend/main.py`) que serve o app e recebe as
respostas. Roda local no filesystem; no Cloud Run, num bucket GCS privado e
versionado. Rotas e estado em `backend/README.md`.

| arquivo | papel |
|---|---|
| `data/vocab.ttl` | ontologia + **fonte única dos cargos** (ordem, rótulos, código TSE, máximo de escolhas) |
| `data/shapes.ttl` | SHACL: portão de escrita das respostas (Violation → 422) e checagem do catálogo |
| `data/candidates.json` | catálogo que o app e o backend usam (gerado pelo ingest) |
| `data/candidates.ttl` | o mesmo catálogo em RDF (gerado) |
| `photos/<SQ>.webp` | miniaturas 180×240 (geradas; fora do git) |
| `backend/rdfmodel.py` | mapeamento único dicionário → RDF (ingest e backend) |

IRIs: vocab `https://id.pedalhidrografi.co/datahidro/terms#`, candidaturas
`…/datahidro/candidatura/2026/<SQ_CANDIDATO>`, respostas
`…/datahidro/resposta/<uuid>` (ainda sem resolver).

## Catálogo: dados do TSE

```sh
pip install rdflib pillow
python3 tools/ingest_tse.py download   # TSE + GPS partidário (de conexão residencial: o CDN do TSE bloqueia IPs de nuvem)
python3 tools/ingest_tse.py build      # todas as candidaturas dos 5 cargos
```

- Todos os gêneros por padrão (`--genders FEMININO` restringe);
  `--on-ballot-only` fica só com quem está na urna (depois que o TSE gera as
  urnas); `--prune-photos` apaga miniaturas que saíram do catálogo.
- Inaptas (renúncia, indeferimento…) saem quando o TSE publica a situação; no
  layout de 2026 ela ainda vem `#NE`, então por enquanto ninguém sai (o
  ingest avisa, e a tela de início só fala de inaptas quando o filtro valeu).
- Se o `download` falhar, baixe `consulta_cand_2026.zip` e
  `foto_cand2026_{SP,BR}_div.zip` em
  <https://dadosabertos.tse.jus.br/dataset/candidatos-2026> e salve em
  `tools/_cache/tse/`.
- O TSE atualiza todo dia (renúncias, indeferimentos): rode de novo, suba a
  `VERSION` do `sw.js` e faça o deploy. Quem tinha marcado uma candidatura que
  saiu é avisado e ela é desmarcada.

Sem rede (desenvolvimento): `python3 tools/make_sample.py && python3
tools/ingest_tse.py build --source tools/_cache/sample` gera um catálogo
**fictício** marcado `"sample": true`. O app mostra uma faixa de aviso e o
`deploy.sh` se recusa a publicar.

Ícones e `og.png`: `python3 tools/make_icons.py`.

### Ordem aleatória: GPS Partidário 2026

A ordem aleatória é um sorteio ponderado por partido, refeito a cada
carregamento da página: peso `1 + (z − z_max)²`, em que `z` é a `media_z` do partido no
[GPS Partidário 2026](https://github.com/deltafolha/gps-partidario-2026) da
Folha de S.Paulo (régua esquerda–direita, negativo = esquerda) e `z_max` é a
maior entre os partidos do catálogo. O partido mais à direita pesa 1 (NOVO) e o
peso cresce com o quadrado da distância até ele (PSTU ~17). O PCO, fora da
tabela, herda a posição do PSTU.

O `download` baixa a `tabela_final.csv` fixada num commit (`GPS_COMMIT` no
ingest); o `build` traduz as siglas históricas da tabela (`GPS_SIGLAS`),
aplica os substitutos (`GPS_PROXIES`) e grava só a `media_z` no
`candidates.json` (`party_lean`); a fórmula fica no `app.js` (`partyWeigher`).
Na ordem por votos, o empate desempata por esse sorteio.

## Rodando local

```sh
pip install -r backend/requirements.txt
cd backend && DATAHIDRO_STATE=../local-state DATAHIDRO_TALLY_MIN_RESPONSES=1 PORT=8626 python3 main.py
# http://localhost:8626 (respostas e placar em local-state/)
```

## Placar e lei eleitoral

O placar é **ao vivo por padrão** (decisão do coletivo, 2026-09: enquete
comunitária de uma semana). Contexto pra quem mantém: a Lei 9.504/97 (art. 33,
§5º) e a Res. TSE 23.600/2019 (art. 23) vedam, durante a campanha (de 16/08
até a eleição), enquete (levantamento espontâneo, sem plano amostral) cujos
resultados permitam inferir a ordem das candidaturas; a divulgação é tratada
como pesquisa sem registro (multa de R$ 53.205 a R$ 106.410). Pra segurar os
números até uma data, ponha `DATAHIDRO_TALLY_OPENS_AT=2026-10-25T17:00:00-03:00`
nas env vars do deploy: o app passa a oferecer só a ordem aleatória e avisa
quando o placar abre.

`DATAHIDRO_TALLY_MIN_RESPONSES` (padrão 1): o placar aparece desde a primeira
resposta. Com poucas respostas dá pra deduzir as escolhas de quem respondeu
primeiro; suba o número se isso passar a importar.

## Privacidade

- A resposta é **anônima**: só as escolhas, o consentimento e a data. Sem nome,
  contato, login ou IP (o limite de envios fica só em memória).
- Mesmo assim, as respostas não têm rota de leitura: o bucket é privado e o
  público vê só o placar. Pra ler:
  ```sh
  gcloud storage cp -r gs://datahidro-pedalhidrografico/responses tools/_cache/
  python3 tools/tally.py tools/_cache/responses             # totais por cargo
  python3 tools/tally.py tools/_cache/responses --ttl all.ttl
  ```
- O selo é desenhado no canvas, no próprio aparelho.

## Verificações antes de publicar

```sh
node --check app.js && node --check sw.js
python3 -m py_compile backend/*.py tools/*.py
python3 -m pyshacl -s data/shapes.ttl -w data/candidates.ttl   # Conforms: True (Warnings = sem foto)
```

E passar pelas 8 telas num navegador de verdade (celular ou janela de ~390px).
**Suba a `VERSION` do `sw.js`** a cada mudança em arquivo servido, inclusive
quando rodar o ingest.

## Deploy

`./deploy.sh` confere o catálogo (real, com fotos), cria/atualiza o bucket
privado e versionado e sobe o serviço `datahidro` no Cloud Run (projeto
`pedal-hidrografico`, `southamerica-east1`, `--max-instances 1`, porque o placar
é read-modify-write sob lock de processo).

No fim, o `deploy.sh` publica o **Worker da Cloudflare** (`cloudflare/`): ele
atende `pesquisa.pedalhidrografi.co` (custom domain, com DNS e certificado
automáticos) e repassa tudo pro `*.run.app`, **mandando o IP do cliente em
`X-Real-IP`**. Sem isso o limite de envios fica desligado, porque atrás do
Worker todo mundo chega com o mesmo IP. O Worker também deixa a borda segurar
o `GET /api/tally` pelos 10 s do `max-age`. Publicar precisa de
`npx wrangler login` ou de `CLOUDFLARE_API_TOKEN` com Workers Scripts:Edit,
Workers Routes:Edit e DNS:Edit; sem credencial, o script só imprime o comando.

Testar o Worker local, contra o backend local:
`npx wrangler dev --config cloudflare/wrangler.toml --var ORIGIN:http://127.0.0.1:8626`.

## Licença

AGPL-3.0 (`LICENSE`). Dados de candidaturas: TSE, dados abertos. Posição dos
partidos (peso do sorteio): GPS Partidário 2026, Folha de S.Paulo
(`deltafolha/gps-partidario-2026`).
