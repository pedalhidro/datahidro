"""datahidro backend — serve o app estático e recebe as respostas do levantamento.

Mesma receita do amora/levabici: um Flask só, igual no host local e no Cloud
Run; STORAGE_BACKEND escolhe onde vive o estado (filesystem ou bucket GCS).

Estado:
  responses/<uuid>.ttl   uma resposta por aparelho (reenviar substitui): só
                         as escolhas + consentimento — sem nome, contato ou
                         IP. Mesmo anônimas, NÃO têm rota de leitura (bucket
                         privado; leitura com gcloud + tools/tally.py): o
                         público vê só o placar. Sem auth: a API só escreve.
  tally.json             o placar: totais agregados e anônimos por candidatura,
                         atualizados na MESMA seção crítica que grava a
                         resposta (read-modify-write → premissa de UMA
                         instância, como o levabici: --max-instances 1).

O app manda JSON, não Turtle: o grafo é montado aqui (rdfmodel.py) e o SHACL
(data/shapes.ttl) é o portão — Violation → 422.

Rotas:
  GET  /                 → index.html
  GET  /<path>           → estáticos (app.js, data/candidates.json, photos/…)
  GET  /health           → liveness + resumo do catálogo e do placar
  POST /api/responses    → cria ou substitui a resposta de um aparelho
  GET  /api/tally        → placar: totais anônimos por candidatura, ao vivo
"""

import collections
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory
from rdflib import Graph, Namespace

import rdfmodel
from storage import make_store_from_env

# ---------------------------------------------------------------- config

WEB = Path(os.environ.get("DATAHIDRO_WEB", Path(__file__).resolve().parent.parent))
CATALOG_PATH = WEB / "data" / "candidates.json"
SHAPES_PATH = WEB / "data" / "shapes.ttl"

PUBLIC_BASE = os.environ.get("DATAHIDRO_PUBLIC_BASE", "https://pesquisa.pedalhidrografi.co")
MAX_PAYLOAD = 64 * 1024
RATE_WINDOW_S = 600
RATE_MAX = int(os.environ.get("DATAHIDRO_RATE_MAX") or 30)  # envios / cliente / 10 min

# Placar ao vivo por padrão (decisão do coletivo, 2026-09: enquete comunitária
# de uma semana). DATAHIDRO_TALLY_OPENS_AT (ISO, ex. 2026-10-25T17:00:00-03:00)
# segura os números até essa data, se um dia for preciso — ver o README,
# "Placar e lei eleitoral".
_BRT = timezone(timedelta(hours=-3))
_opens_env = os.environ.get("DATAHIDRO_TALLY_OPENS_AT")
TALLY_OPENS_AT = (datetime.fromisoformat(_opens_env) if _opens_env
                  else datetime(2000, 1, 1, tzinfo=timezone.utc))
if TALLY_OPENS_AT.tzinfo is None:
    TALLY_OPENS_AT = TALLY_OPENS_AT.replace(tzinfo=_BRT)
# Mínimo de respostas pra mostrar totais. Padrão 1: placar desde a primeira
# resposta (decisão do Danilo, 2026-09-12). Com poucas respostas um total pode
# entregar a escolha de alguém — suba isto se isso passar a importar.
TALLY_MIN_RESPONSES = int(os.environ.get("DATAHIDRO_TALLY_MIN_RESPONSES") or 1)
TALLY_KEY = "tally.json"
TALLY_TTL_S = 10  # cache de leitura; toda escrita relê do store

# código, estado local, dotfiles e um eventual venv criado na raiz do repo
BLOCKED_PREFIXES = ("backend/", "tools/", "local-state/", ".", "bin/", "include/",
                    "lib/python", "lib64/", "pyvenv.cfg", "share/")
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; "
       "script-src 'self'; connect-src 'self'; font-src 'self'; manifest-src 'self'; "
       "worker-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
       "frame-ancestors 'none'")

SH = Namespace("http://www.w3.org/ns/shacl#")
RESPONSE_KEY_RE = re.compile(r"^responses/([0-9a-f-]{36})\.ttl$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_PAYLOAD
store = make_store_from_env(
    default_local_root=os.environ.get("DATAHIDRO_STATE", str(WEB / "local-state"))
)

# ---------------------------------------------------------------- catálogo

def _load_catalog():
    """candidates.json → índices. Estático no container: carrega uma vez."""
    if not CATALOG_PATH.exists():
        return None
    cat = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    offices = {o["slug"]: o for o in cat["offices"]}
    by_sq = {}
    for slug, candidates in cat["candidates"].items():
        for c in candidates:
            by_sq[c["sq"]] = (offices[slug], c)
    return {
        "raw": cat,
        "offices": offices,
        "by_sq": by_sq,
    }


CATALOG = _load_catalog()

_shapes = None
_validate_lock = threading.Lock()  # pyshacl não é thread-safe
_write_lock = threading.Lock()  # resposta + placar: read-modify-write serializado


def _violations(data):
    global _shapes
    import pyshacl

    with _validate_lock:
        if _shapes is None:
            _shapes = Graph().parse(str(SHAPES_PATH), format="turtle")
        _, results, _ = pyshacl.validate(data, shacl_graph=_shapes, inference="none",
                                         advanced=True, allow_warnings=True)
    out = []
    for res in results.subjects(SH.resultSeverity, SH.Violation):
        msg = results.value(res, SH.resultMessage) or "violação SHACL"
        val = results.value(res, SH.value)
        out.append(f"{msg}" + (f" ({val})" if val is not None else ""))
    return sorted(set(out))


# ---------------------------------------------------------------- placar

_tally_cache = {"data": None, "read_at": 0.0}


def _now():
    return datetime.now(timezone.utc)


def _read_tally():
    """tally.json do store. Se não existir (primeiro boot, objeto apagado),
    reconstrói varrendo responses/ — lento com muitas respostas no GCS, mas só
    acontece nesses casos. Chamar com _write_lock."""
    text = store.read_text(TALLY_KEY)
    if text:
        return json.loads(text)
    tally = {"version": 1, "responses": 0, "counts": {}, "updated_at": None}
    for key in store.list_keys("responses/"):
        m = RESPONSE_KEY_RE.match(key)
        ttl = store.read_text(key) if m else None
        if not ttl:
            continue
        _, choices = rdfmodel.read_response(ttl, m.group(1))
        tally["responses"] += 1
        for sq in choices:
            tally["counts"][sq] = tally["counts"].get(sq, 0) + 1
    tally["updated_at"] = _now().isoformat(timespec="seconds")
    _write_tally(tally)
    return tally


def _write_tally(tally):
    store.write_text(TALLY_KEY, json.dumps(tally, separators=(",", ":")),
                     content_type="application/json")
    _tally_cache.update(data=tally, read_at=time.monotonic())


def _tally_for_reading():
    if _tally_cache["data"] is None or time.monotonic() - _tally_cache["read_at"] > TALLY_TTL_S:
        with _write_lock:
            _tally_cache.update(data=_read_tally(), read_at=time.monotonic())
    return _tally_cache["data"]


@app.get("/api/tally")
def get_tally():
    is_open = _now() >= TALLY_OPENS_AT
    body = {"open": is_open, "opens_at": TALLY_OPENS_AT.isoformat(),
            "min_responses": TALLY_MIN_RESPONSES, "available": False}
    if is_open:
        tally = _tally_for_reading()
        body["responses"] = tally["responses"]
        if tally["responses"] >= TALLY_MIN_RESPONSES:
            body.update(available=True, updated_at=tally["updated_at"], counts=tally["counts"])
    resp = jsonify(body)
    resp.headers["Cache-Control"] = "public, max-age=10" if is_open else "public, max-age=300"
    return resp


# ---------------------------------------------------------------- limite de envios

_hits = collections.defaultdict(collections.deque)
_hits_lock = threading.Lock()


def _client_ip():
    """IP do cliente pro limite de envios, ou None se não dá pra saber.
    Atrás do Worker da Cloudflare todo mundo chega com o IP do Worker: o
    Worker precisa mandar X-Real-IP (ver README, Deploy). Sem ele, numa
    subrequisição de Worker (header CF-Worker), melhor não limitar do que
    barrar todo mundo junto."""
    if request.headers.get("X-Real-IP"):
        return request.headers["X-Real-IP"].strip()
    if request.headers.get("CF-Worker"):
        return None
    if request.headers.get("CF-Connecting-IP"):
        return request.headers["CF-Connecting-IP"].strip()
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() or request.remote_addr


def _rate_limited(ip):
    if not ip:
        return False
    now = time.monotonic()
    with _hits_lock:
        if len(_hits) > 50_000:  # poda ocasional de IPs antigos
            for k in [k for k, q in _hits.items() if not q or now - q[-1] > RATE_WINDOW_S]:
                del _hits[k]
        q = _hits[ip]
        while q and now - q[0] > RATE_WINDOW_S:
            q.popleft()
        if len(q) >= RATE_MAX:
            return True
        q.append(now)
        return False


# ---------------------------------------------------------------- respostas

def _error(status, message, **extra):
    return jsonify({"error": message, **extra}), status


@app.post("/api/responses")
def post_response():
    if CATALOG is None:
        return _error(503, "catálogo de candidaturas ausente no servidor")
    if _rate_limited(_client_ip()):
        return _error(429, "muitos envios deste endereço; tente de novo em alguns minutos")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error(400, "esperava um objeto JSON")

    response_id = body.get("id")
    if not isinstance(response_id, str) or not rdfmodel.UUID_RE.match(response_id):
        return _error(400, "id inválido (esperava UUID v4)")

    requested = body.get("choices") or {}
    if not isinstance(requested, dict):
        return _error(400, "choices precisa ser um objeto {cargo: [sq, …]}")
    choices, catalog_triples, unknown = [], [], []
    for slug, sqs in requested.items():
        if slug not in CATALOG["offices"] or not isinstance(sqs, list):
            return _error(400, f"cargo desconhecido: {slug}")
        for sq in dict.fromkeys(sqs):  # sem repetição, ordem preservada
            entry = CATALOG["by_sq"].get(sq) if isinstance(sq, str) else None
            if entry is None or entry[0]["slug"] != slug:
                unknown.append(sq)
                continue
            office, candidate = entry
            choices.append(sq)
            catalog_triples.extend(rdfmodel.candidacy_triples(candidate, office["iri"], PUBLIC_BASE))
    if unknown:
        return _error(422, "algumas candidaturas não estão (mais) no catálogo", unknown=unknown)

    consent_version = body.get("consent_version")
    if not isinstance(consent_version, str):
        return _error(400, "consent_version ausente")

    key = f"responses/{response_id}.ttl"
    now = _now()
    with _write_lock:
        existing = store.read_text(key)
        generated, old_choices = (rdfmodel.read_response(existing, response_id)
                                  if existing else (None, []))
        graph = rdfmodel.response_graph(
            response_id, choices,
            consent=body.get("consent") is True,
            consent_version=consent_version,
            generated_at=generated or now,
            modified_at=now if existing else None,
        )
        data = Graph()
        for t in graph:
            data.add(t)
        for t in catalog_triples:
            data.add(t)
        problems = _violations(data)
        if problems:
            return _error(422, "a resposta não passou na validação", violations=problems)

        tally = _read_tally()  # antes de gravar: a reconstrução não conta esta resposta
        store.write_text(key, graph.serialize(format="turtle"))
        counts = tally["counts"]
        for sq in old_choices:
            n = counts.get(sq, 0) - 1
            if n > 0:
                counts[sq] = n
            else:
                counts.pop(sq, None)
        for sq in choices:
            counts[sq] = counts.get(sq, 0) + 1
        if not existing:
            tally["responses"] += 1
        tally["updated_at"] = now.isoformat(timespec="seconds")
        _write_tally(tally)

    return jsonify({
        "id": response_id,
        "iri": str(rdfmodel.response_iri(response_id)),
        "updated": bool(existing),
        "choices": len(choices),
    }), (200 if existing else 201)


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(413)
def api_errors(err):
    if request.path.startswith("/api/"):
        return _error(err.code, getattr(err, "description", None) or err.name)
    return err


# ---------------------------------------------------------------- saúde + estáticos

@app.get("/health")
@app.get("/api/health")
def health():
    cat = CATALOG["raw"] if CATALOG else None
    return jsonify({
        "ok": True,
        "storage": type(store).__name__,
        "catalog": None if cat is None else {
            "sample": cat.get("sample"),
            "generated_at": cat.get("generated_at"),
            "candidates": len(CATALOG["by_sq"]),
        },
        "tally": {"opens_at": TALLY_OPENS_AT.isoformat(), "open": _now() >= TALLY_OPENS_AT,
                  "min_responses": TALLY_MIN_RESPONSES},
    })


@app.get("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.get("/<path:path>")
def static_files(path):
    if path.startswith(BLOCKED_PREFIXES) or "/." in path:
        abort(404)
    return send_from_directory(WEB, path)


@app.after_request
def headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Content-Security-Policy", CSP)
    path = request.path
    if path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    elif path.startswith("/photos/"):
        # fotos mudam só quando o ingest roda de novo; um dia de cache basta
        resp.headers["Cache-Control"] = "public, max-age=86400"
    elif resp.status_code == 200:
        # casca e catálogo: revalida sempre (ETag/304); offline é o SW
        resp.headers["Cache-Control"] = "no-cache"
    return resp


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8626)), debug=True)
