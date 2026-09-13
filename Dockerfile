# datahidro — container do backend (mesma receita do amora/levabici).
#   • Cloud Run:  STORAGE_BACKEND=gcs + GCS_BUCKET (ver deploy.sh)
#   • Local:      docker build -t datahidro . && docker run --rm -p 8626:8080 datahidro
# Em produção o build é via `gcloud run deploy --source .` (Cloud Build).

FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/main.py backend/storage.py backend/rdfmodel.py ./

# O app estático inteiro, com data/ e photos/ (o .dockerignore poda .git,
# local-state, tools/_cache…; o Flask ainda bloqueia backend/ e tools/).
COPY . ./web/

ENV DATAHIDRO_WEB=/app/web \
    PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# --workers 1: tally.json (placar) é read-modify-write sob lock de processo (house
# rule); Cloud Run com max-instances=1 no deploy.sh.
CMD exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 8 \
      --timeout 60 --access-logfile - main:app
