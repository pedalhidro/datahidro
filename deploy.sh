#!/usr/bin/env bash
# Deploy do datahidro no Cloud Run + bucket GCS PRIVADO e versionado.
# Idempotente: pode rodar de novo à vontade.
set -euo pipefail

PROJECT=pedal-hidrografico
REGION=southamerica-east1
SERVICE=datahidro
BUCKET=datahidro-pedalhidrografico
PUBLIC_BASE=https://pesquisa.pedalhidrografi.co

cd "$(dirname "$0")"

# Pré-voo: nunca publicar sem catálogo, com o catálogo de EXEMPLO ou com foto faltando.
python3 - <<'PY'
import json, pathlib, sys
p = pathlib.Path("data/candidates.json")
if not p.exists():
    sys.exit("data/candidates.json não existe — rode: python3 tools/ingest_tse.py build")
cat = json.loads(p.read_text(encoding="utf-8"))
if cat.get("sample"):
    sys.exit("data/candidates.json é o catálogo de EXEMPLO (dados fictícios) — gere com os zips do TSE")
cs = [c for v in cat["candidates"].values() for c in v]
missing = [c["photo"] for c in cs if c.get("photo") and not pathlib.Path(c["photo"]).exists()]
if missing:
    sys.exit(f"{len(missing)} fotos do catálogo não estão em photos/ (ex.: {missing[0]}) — rode o ingest de novo")
print(f"catálogo ok: {len(cs)} candidaturas, {sum(1 for c in cs if c.get('photo'))} com foto"
      f" (TSE: {cat['source'].get('tse_generated_at')})")
PY
echo "service worker: $(grep -m1 'const VERSION' sw.js)  ← subiu desde o último deploy?"

# Bucket do estado: respostas (anônimas) + placar. PRIVADO — o público só vê o
# placar pela API. VERSIONAMENTO ligado: toda escrita vira geração recuperável.
if ! gcloud storage buckets describe "gs://${BUCKET}" --project "${PROJECT}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET}" \
    --project "${PROJECT}" --location "${REGION}" \
    --uniform-bucket-level-access --public-access-prevention
fi
gcloud storage buckets update "gs://${BUCKET}" --versioning --public-access-prevention --project "${PROJECT}"

# max-instances=1: tally.json é read-modify-write sob lock de processo (mesmo
# desenho do amora/levabici). Não subir sem repensar o locking.
# Pra segurar o placar até uma data, acrescente às env vars:
#   DATAHIDRO_TALLY_OPENS_AT=2026-10-25T17:00:00-03:00
gcloud run deploy "${SERVICE}" \
  --source . \
  --project "${PROJECT}" \
  --region "${REGION}" \
  --allow-unauthenticated \
  --max-instances 1 \
  --memory 512Mi \
  --set-env-vars "STORAGE_BACKEND=gcs,GCS_BUCKET=${BUCKET},DATAHIDRO_PUBLIC_BASE=${PUBLIC_BASE}"

URL=$(gcloud run services describe "${SERVICE}" --project "${PROJECT}" --region "${REGION}" \
  --format='value(status.url)')
echo "Cloud Run: ${URL}"

# Worker da Cloudflare (cloudflare/): pesquisa.pedalhidrografi.co → Cloud Run,
# mandando X-Real-IP. Precisa de `npx wrangler login` feito ou de
# CLOUDFLARE_API_TOKEN (Workers Scripts:Edit, Workers Routes:Edit, DNS:Edit).
WORKER_CMD=(npx --yes wrangler@4 deploy --config cloudflare/wrangler.toml --var "ORIGIN:${URL}")
if [[ -n "${CLOUDFLARE_API_TOKEN:-}" || -f "${HOME}/.config/.wrangler/config/default.toml" ]]; then
  "${WORKER_CMD[@]}"
  echo
  echo "Pronto: https://${PUBLIC_BASE#https://}"
else
  echo
  echo "Falta o Worker da Cloudflare: rode 'npx wrangler login' (ou exporte"
  echo "CLOUDFLARE_API_TOKEN) e depois:"
  echo "  ${WORKER_CMD[*]}"
fi
