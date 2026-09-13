#!/usr/bin/env python3
"""Catálogo de candidaturas do datahidro a partir dos dados abertos do TSE.

  python3 tools/ingest_tse.py download   # zips do TSE → tools/_cache/tse/
  python3 tools/ingest_tse.py build      # → data/candidates.json + .ttl + photos/<sq>.webp

Fonte: https://dadosabertos.tse.jus.br/dataset/candidatos-2026
  consulta_cand_2026.zip        CSVs por UF (latin-1, ';'), uma linha por candidatura
  foto_cand2026_<UF>_div.zip    fotos de divulgação, F<UF><SQ_CANDIDATO>_div.jpg

O CDN do TSE (Akamai) nega acesso a IPs de datacenter/nuvem: rode `download`
de uma conexão residencial, ou baixe na mão pela página acima e salve os zips
em tools/_cache/tse/.

Filtros padrão (ver `build --help`): só as UFs/cargos do data/vocab.ttl e sem
candidaturas INAPTAS (renúncia, indeferimento, cancelamento…) — quando o TSE já
publica a situação; no layout de 2026 ela ainda vem #NE e ninguém sai. Todos os
gêneros (decisão do coletivo; `--genders FEMININO` restringe).

Dependências: rdflib (sempre) e Pillow (fotos; dispensável com --no-photos).
"""
from __future__ import annotations

import argparse
import collections
import csv
import io
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_TSE = ROOT / "tools" / "_cache" / "tse"
DATA = ROOT / "data"
PHOTOS = ROOT / "photos"

URLS = {
    "consulta_cand_2026.zip":
        "https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/consulta_cand_2026.zip",
    "foto_cand2026_SP_div.zip":
        "https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes2026/fotos/foto_cand2026_SP_div.zip",
    "foto_cand2026_BR_div.zip":
        "https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes2026/fotos/foto_cand2026_BR_div.zip",
}
USER_AGENT = "Mozilla/5.0 (datahidro ingest; +https://pesquisa.pedalhidrografi.co)"

REQUIRED_COLUMNS = {
    "SG_UF", "CD_CARGO", "SQ_CANDIDATO", "NR_CANDIDATO", "NM_URNA_CANDIDATO",
    "NM_CANDIDATO", "SG_PARTIDO", "NM_PARTIDO", "DS_SITUACAO_CANDIDATURA",
    "DS_GENERO",
}
NULLS = {"", "#NULO#", "#NULO", "#NE#", "#NE", "NULO"}
PHOTO_RE = re.compile(r"F[A-Z]{2}(\d+)_div\.(jpe?g|png|webp|bmp)$", re.I)

# conectivos que ficam minúsculos no meio de nome ("Maria da Silva")
LOWERCASE_WORDS = {"da", "das", "de", "do", "dos", "e", "di", "du", "del", "della", "van", "von", "y"}
ROMAN_NUMERALS = {"ii", "iii", "iv", "vi", "vii", "viii"}


def value(row: dict, column: str) -> str | None:
    v = (row.get(column) or "").strip()
    return None if v.upper() in NULLS else v


def title_case(text: str) -> str:
    """TSE publica tudo em CAIXA ALTA → "Professora Maria d'Ávila da Silva"."""
    words = " ".join(text.split()).lower().split(" ")
    out = []
    for i, w in enumerate(words):
        if i > 0 and w in LOWERCASE_WORDS:
            out.append(w)
        elif w in ROMAN_NUMERALS:
            out.append(w.upper())
        else:
            out.append(re.sub(r"(^|[-'’])(\w)", lambda m: m.group(1) + m.group(2).upper(), w))
    return " ".join(out)


def normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o644)  # mkstemp cria 0600; isto aqui é servido publicamente
    os.replace(tmp, path)


# ---------------------------------------------------------------- download

def download(args) -> None:
    CACHE_TSE.mkdir(parents=True, exist_ok=True)
    failed = []
    for name, url in URLS.items():
        dest = CACHE_TSE / name
        if dest.exists() and not args.force:
            print(f"= {dest.relative_to(ROOT)} já existe (--force baixa de novo)")
            continue
        print(f"→ {url}")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                partial = dest.with_suffix(".part")
                done = 0
                with open(partial, "wb") as f:
                    while chunk := resp.read(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            print(f"\r  {done / 1e6:.1f} / {total / 1e6:.1f} MB", end="", flush=True)
                os.replace(partial, dest)
                print(f"\r  ok: {dest.relative_to(ROOT)} ({done / 1e6:.1f} MB)")
        except urllib.error.HTTPError as e:
            print(f"  FALHOU: HTTP {e.code}")
            if e.code == 403:
                print("  O CDN do TSE bloqueia IPs de nuvem/datacenter. Rode de uma conexão"
                      " residencial ou baixe na mão em\n  https://dadosabertos.tse.jus.br/"
                      f"dataset/candidatos-2026 e salve como {dest}")
            elif e.code == 404:
                print("  Link mudou? Confira o nome do arquivo na página do conjunto de dados.")
            failed.append(name)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  FALHOU: {getattr(e, 'reason', e)}")
            failed.append(name)
    if failed:
        sys.exit(f"não baixou: {', '.join(failed)}. Baixe na mão em "
                 f"https://dadosabertos.tse.jus.br/dataset/candidatos-2026 e salve em {CACHE_TSE}")


# ---------------------------------------------------------------- build

def read_rows(zip_path: Path, ufs: set[str], encoding: str):
    with zipfile.ZipFile(zip_path) as zf:
        csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        by_uf = {}
        for n in csvs:
            m = re.search(r"_([A-Z]{2}|BRASIL)\.csv$", n, re.I)
            if m:
                by_uf[m.group(1).upper()] = n
        if all(uf in by_uf for uf in ufs):
            targets = [(by_uf[uf], False) for uf in sorted(ufs)]
        elif "BRASIL" in by_uf:
            targets = [(by_uf["BRASIL"], True)]
        else:
            sys.exit(f"CSV das UFs {sorted(ufs)} não encontrado em {zip_path.name}: {csvs}")
        for name, filter_uf in targets:
            with zf.open(name) as raw:
                reader = csv.DictReader(io.TextIOWrapper(raw, encoding=encoding, newline=""),
                                        delimiter=";", quotechar='"')
                missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
                if missing:
                    sys.exit(f"{name}: colunas obrigatórias ausentes {sorted(missing)}.\n"
                             f"Colunas do arquivo: {reader.fieldnames}")
                for row in reader:
                    if filter_uf and value(row, "SG_UF") not in ufs:
                        continue
                    yield row


def convert_photo(data: bytes, dest: Path, size: tuple[int, int]) -> None:
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        # recorte 3:4 puxado pro alto (rosto), sem distorcer
        im = ImageOps.fit(im, size, method=Image.LANCZOS, centering=(0.5, 0.35))
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=60, method=6)
    write_atomic(dest, buf.getvalue())


def build(args) -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    import rdfmodel
    from rdflib import Graph, Literal
    from rdflib.namespace import DCTERMS, PROV, RDF

    source = Path(args.source).resolve()
    zips = sorted(source.glob("consulta_cand_*.zip"))
    if not zips:
        sys.exit(f"nenhum consulta_cand_*.zip em {source} (rode `download` ou tools/make_sample.py)")
    sample = (source / "SAMPLE").exists()
    genders = None if args.genders.lower() == "todos" else {
        g.strip().upper() for g in args.genders.split(",") if g.strip()}
    excluded = {s.strip().upper() for s in args.exclude_statuses.split(",") if s.strip()}
    width, height = (int(x) for x in args.photo_size.lower().split("x"))

    offices = rdfmodel.load_offices(DATA / "vocab.ttl")
    office_by_key = {(o["jurisdiction"], o["tse_code"]): o for o in offices}
    ufs = {o["jurisdiction"] for o in offices}

    candidates = {o["slug"]: {} for o in offices}
    parties: dict[str, str] = {}
    skipped = collections.Counter()
    status_available = False  # o TSE já publica DS_SITUACAO_CANDIDATURA? (2026: #NE)
    tse_generated_at = None
    for row in read_rows(zips[-1], ufs, args.encoding):
        office = office_by_key.get((value(row, "SG_UF"), value(row, "CD_CARGO")))
        if office is None:
            continue
        tse_generated_at = tse_generated_at or " ".join(
            x for x in (value(row, "DT_GERACAO"), value(row, "HH_GERACAO")) if x)
        status = (value(row, "DS_SITUACAO_CANDIDATURA") or "").upper()
        status_available = status_available or bool(status)
        gender = (value(row, "DS_GENERO") or "").upper()
        if status in excluded:
            skipped[f"situação {status}"] += 1
            continue
        if (args.on_ballot_only and "ST_CANDIDATO_INSERIDO_URNA" in row
                and (value(row, "ST_CANDIDATO_INSERIDO_URNA") or "").upper() != "SIM"):
            skipped["fora da urna"] += 1
            continue
        if genders is not None and gender not in genders:
            skipped[f"gênero {gender or '?'}"] += 1
            continue
        sq = value(row, "SQ_CANDIDATO")
        if not sq or not rdfmodel.SQ_RE.match(sq):
            skipped["SQ inválido"] += 1
            continue
        name = title_case(value(row, "NM_URNA_CANDIDATO") or value(row, "NM_CANDIDATO") or "")
        full_name = title_case(value(row, "NM_SOCIAL_CANDIDATO") or value(row, "NM_CANDIDATO") or "")
        party = value(row, "SG_PARTIDO") or "?"
        parties.setdefault(party, title_case(value(row, "NM_PARTIDO") or party))
        candidates[office["slug"]][sq] = {
            "sq": sq,
            "number": value(row, "NR_CANDIDATO") or "",
            "name": name,
            **({"full_name": full_name} if normalized(full_name) != normalized(name) else {}),
            "party": party,
            # federation/status só pro TTL; gender vai também pro app ("só mulheres"):
            "federation": title_case(value(row, "NM_FEDERACAO")) if value(row, "NM_FEDERACAO") else None,
            "gender": gender or None,
            "status": value(row, "DS_DETALHE_SITUACAO_CAND") or status or None,
        }

    # Mesmo número no mesmo cargo = a mesma vaga na urna: o TSE às vezes lista
    # dois registros (sem situação publicada não dá pra saber qual vale). Fica
    # o mais recente — SQ_CANDIDATO é sequencial.
    merged = 0
    for records in candidates.values():
        latest = {}
        for sq in sorted(records, key=int):
            if records[sq]["number"]:
                latest[records[sq]["number"]] = sq
        for sq in [s for s in records if records[s]["number"] and latest[records[s]["number"]] != s]:
            del records[sq]
            merged += 1

    # ------------------------------------------------ fotos
    with_photo = collections.Counter()
    expected = set()
    if not args.no_photos:
        opened, index = [], {}
        for z in sorted(source.glob("foto_cand*_div.zip")):
            zf = zipfile.ZipFile(z)
            opened.append(zf)
            for n in zf.namelist():
                m = PHOTO_RE.search(n)
                if m:
                    index[m.group(1)] = (zf, n)
        if not index:
            print("aviso: nenhuma foto encontrada (foto_cand*_div.zip) — cards saem com iniciais")
        for slug, records in candidates.items():
            for sq, c in records.items():
                if sq not in index:
                    continue
                dest = PHOTOS / f"{sq}.webp"
                expected.add(dest.name)
                if args.redo_photos or not dest.exists():
                    zf, n = index[sq]
                    try:
                        convert_photo(zf.read(n), dest, (width, height))
                    except Exception as e:  # noqa: BLE001 — foto corrompida não derruba o ingest
                        print(f"aviso: foto {n} ilegível ({e})")
                        expected.discard(dest.name)
                        continue
                c["photo"] = f"photos/{sq}.webp"
                with_photo[slug] += 1
        for zf in opened:
            zf.close()
        stale = [p for p in PHOTOS.glob("*.webp") if p.name not in expected]
        if stale and args.prune_photos:
            for p in stale:
                p.unlink()
            print(f"fotos: {len(stale)} arquivos que saíram do catálogo foram apagados")
        elif stale:
            print(f"fotos: {len(stale)} arquivos em photos/ não estão mais no catálogo"
                  " (use --prune-photos pra apagar)")

    # ------------------------------------------------ JSON do app
    now = datetime.now(timezone.utc)
    app_fields = ("sq", "number", "name", "full_name", "party", "photo", "gender")
    catalog = {
        "version": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "sample": sample,
        "source": {
            "name": "TSE — Portal de Dados Abertos (Candidatos 2026)",
            "url": str(rdfmodel.TSE_DATASET),
            "tse_generated_at": tse_generated_at,
        },
        "filter": {
            "genders": sorted(genders) if genders is not None else None,
            "excluded_statuses": sorted(excluded),
            "status_available": status_available,
            "on_ballot_only": bool(args.on_ballot_only),
        },
        "offices": [{**o, "total": len(candidates[o["slug"]])} for o in offices],
        "parties": dict(sorted(parties.items())),
        "candidates": {
            slug: [{k: c[k] for k in app_fields if c.get(k)}
                   for _, c in sorted(records.items(), key=lambda kv: int(kv[0]))]
            for slug, records in candidates.items()
        },
    }
    write_atomic(DATA / "candidates.json",
                 json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).encode())

    # ------------------------------------------------ Turtle (substrato RDF)
    g = rdfmodel.bind(Graph())
    cat = rdfmodel.CATALOG_IRI
    g.add((cat, RDF.type, rdfmodel.SCHEMA.Dataset))
    g.add((cat, rdfmodel.SCHEMA.name, Literal("Candidaturas do datahidro 2026", lang="pt")))
    g.add((cat, PROV.wasDerivedFrom, rdfmodel.TSE_DATASET))
    g.add((cat, DCTERMS.created, rdfmodel.xsd_datetime(now)))
    if sample:
        g.add((cat, rdfmodel.SCHEMA.description,
               Literal("DADOS FICTÍCIOS DE EXEMPLO — não são candidaturas reais", lang="pt")))
    for o in offices:
        for sq, c in candidates[o["slug"]].items():
            for t in rdfmodel.candidacy_triples(c, o["iri"], args.public_base, parties.get(c["party"])):
                g.add(t)
            g.add((rdfmodel.candidacy_iri(sq), DCTERMS.isPartOf, cat))
    write_atomic(DATA / "candidates.ttl", g.serialize(format="turtle").encode())

    # ------------------------------------------------ resumo
    print(("EXEMPLO (dados fictícios) — " if sample else "")
          + f"catálogo gerado de {zips[-1].name} (TSE: {tse_generated_at or '?'})")
    for o in offices:
        n = len(candidates[o["slug"]])
        print(f"  {o['title']:<20} {n:>5} candidaturas, {with_photo[o['slug']]:>5} com foto")
    for reason, n in sorted(skipped.items()):
        print(f"  fora: {reason}: {n}")
    if merged:
        print(f"  duplicadas (mesmo número no mesmo cargo; ficou o registro mais recente): {merged}")
    if not status_available:
        print("  aviso: o TSE ainda não publica a situação das candidaturas (#NE) — ninguém saiu por inaptidão")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="baixa os zips do TSE pra tools/_cache/tse/")
    d.add_argument("--force", action="store_true", help="baixa de novo o que já existe")
    d.set_defaults(func=download)
    b = sub.add_parser("build", help="gera data/candidates.{json,ttl} e photos/")
    b.add_argument("--source", default=str(CACHE_TSE), help="pasta com os zips (padrão: tools/_cache/tse)")
    b.add_argument("--genders", default="todos",
                   help='DS_GENERO aceitos, separados por vírgula (ex.: FEMININO), ou "todos" (padrão)')
    b.add_argument("--exclude-statuses", default="INAPTO",
                   help="DS_SITUACAO_CANDIDATURA excluídas (padrão: INAPTO)")
    b.add_argument("--on-ballot-only", action="store_true",
                   help="só ST_CANDIDATO_INSERIDO_URNA=SIM (use depois que o TSE gera as urnas)")
    b.add_argument("--public-base", default="https://pesquisa.pedalhidrografi.co",
                   help="base absoluta das URLs de foto no TTL")
    b.add_argument("--encoding", default="latin-1", help="encoding dos CSVs (padrão: latin-1)")
    b.add_argument("--photo-size", default="180x240", help="LxA das miniaturas (padrão: 180x240)")
    b.add_argument("--no-photos", action="store_true", help="não processa fotos")
    b.add_argument("--redo-photos", action="store_true", help="reconverte fotos já existentes")
    b.add_argument("--prune-photos", action="store_true", help="apaga de photos/ o que saiu do catálogo")
    b.set_defaults(func=build)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
