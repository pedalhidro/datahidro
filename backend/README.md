# backend — datahidro

Um Flask só (receita do `amora/backend` e do `levabici/backend`): serve o app
estático e recebe as respostas. `STORAGE_BACKEND` escolhe onde vive o estado:
filesystem (`DATAHIDRO_STATE`) ou bucket GCS (`GCS_BUCKET`).

```text
gunicorn → main.py (Flask, --workers 1)
  ├─ GET  /, /<path>       → app estático (bloqueia backend/, tools/, local-state/, dotfiles)
  ├─ GET  /health          → liveness + catálogo carregado + config do placar
  ├─ POST /api/responses   → {id, choices: {slug: [sq, …]}, consent, consent_version}
  │                          201 nova · 200 substituída · 422 {violations | unknown} · 429
  └─ GET  /api/tally       → {open, opens_at, min_responses, available, responses, counts}
```

## Estado

- `responses/<uuid>.ttl`: uma resposta por aparelho (o app gera o UUID v4 e o
  guarda no localStorage; reenviar substitui, preservando
  `prov:generatedAtTime` e carimbando `dcterms:modified`). Só escolhas +
  consentimento. **Sem rota de leitura.**
- `tally.json`: o placar, `{responses, counts: {sq: n}, updated_at}`, atualizado
  na mesma seção crítica que grava a resposta (subtrai as escolhas antigas,
  soma as novas). Se sumir, é reconstruído varrendo `responses/` no primeiro
  acesso.

## Portão SHACL

O app manda JSON; `rdfmodel.response_graph` monta o grafo e o backend junta a
ele as triplas de catálogo **só das candidaturas escolhidas**
(`rdfmodel.candidacy_triples`, a mesma função que gera o `candidates.ttl`).
Contra `data/shapes.ttl`: candidatura fora do catálogo viola `sh:class`, mais
de 10 num cargo viola `sh:qualifiedMaxCount`, qualquer propriedade extra viola
`sh:closed`. Violation → 422; Warning (resposta sem escolha) passa. Antes do
SHACL, SQ inexistente ou no cargo errado já volta 422 com `unknown`, e o app
desmarca e pede revisão.

## Variáveis de ambiente

| var | padrão | |
|---|---|---|
| `STORAGE_BACKEND` | `local` | `local` ou `gcs` |
| `GCS_BUCKET` | | obrigatório com `gcs` |
| `DATAHIDRO_STATE` | `<repo>/local-state` | raiz do store local |
| `DATAHIDRO_WEB` | raiz do repo | estáticos (no container: `/app/web`) |
| `DATAHIDRO_PUBLIC_BASE` | `https://pesquisa.pedalhidrografi.co` | base das URLs de foto nas triplas |
| `DATAHIDRO_TALLY_OPENS_AT` | (aberto) | ISO com fuso; antes disso `/api/tally` não manda números |
| `DATAHIDRO_TALLY_MIN_RESPONSES` | `1` | mínimo de respostas pra mostrar totais |
| `DATAHIDRO_RATE_MAX` | `30` | envios por cliente (`X-Real-IP`) a cada 10 min |

## Concorrência

- gunicorn `--workers 1` + Cloud Run `--max-instances 1`: `_write_lock`
  serializa resposta + placar; pyshacl roda sob lock próprio (não é
  thread-safe).
- `storage.py` é cópia do levabici (que é cópia do amora): bug corrigido num,
  corrige nos outros.

## Histórico

O bucket tem versionamento: toda resposta substituída e todo `tally.json`
viram gerações recuperáveis.

```sh
BUCKET=datahidro-pedalhidrografico
gcloud storage ls -a "gs://${BUCKET}/tally.json"
gcloud storage cat "gs://${BUCKET}/responses/<uuid>.ttl#GENERATION"
```

Mexeu em `responses/` na mão? Reconstrua o placar com
`python3 tools/tally.py <pasta> --tally-json tally.json` e suba por cima de
`gs://${BUCKET}/tally.json`.
