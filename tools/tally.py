#!/usr/bin/env python3
"""Soma as respostas do datahidro — uso interno do coletivo.

As respostas não têm rota de leitura: baixe do bucket privado e rode aqui.

  gcloud storage cp -r gs://datahidro-pedalhidrografico/responses tools/_cache/
  python3 tools/tally.py tools/_cache/responses                   # totais por cargo
  python3 tools/tally.py tools/_cache/responses --ttl all.ttl     # grafo único (respostas + catálogo)
  python3 tools/tally.py tools/_cache/responses --tally-json tally.json
      # reconstrói o placar (ex.: depois de restaurar respostas na mão); suba com
      # gcloud storage cp tally.json gs://datahidro-pedalhidrografico/tally.json

Rodando local, a pasta é local-state/responses. Usa data/candidates.json pra
dar nome às candidaturas — o mesmo catálogo que estava no ar.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import rdfmodel  # noqa: E402
from rdflib import Graph  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="pasta com os <uuid>.ttl das respostas")
    ap.add_argument("--top", type=int, default=15, help="quantas candidaturas listar por cargo")
    ap.add_argument("--ttl", help="grava um grafo único (respostas + catálogo) neste arquivo")
    ap.add_argument("--tally-json", help="grava um tally.json reconstruído neste arquivo")
    args = ap.parse_args()

    catalog = json.loads((ROOT / "data" / "candidates.json").read_text(encoding="utf-8"))
    by_sq = {c["sq"]: (slug, c) for slug, cs in catalog["candidates"].items() for c in cs}

    counts = collections.Counter()
    picked_someone = collections.Counter()  # respostas que marcaram alguém no cargo
    graph = rdfmodel.bind(Graph()) if args.ttl else None
    total = 0
    for path in sorted(Path(args.folder).glob("*.ttl")):
        if not rdfmodel.UUID_RE.match(path.stem):
            continue
        text = path.read_text(encoding="utf-8")
        _, choices = rdfmodel.read_response(text, path.stem)
        total += 1
        counts.update(choices)
        for slug in {by_sq[sq][0] for sq in choices if sq in by_sq}:
            picked_someone[slug] += 1
        if graph is not None:
            graph.parse(data=text, format="turtle")

    print(f"{total} respostas")
    for office in catalog["offices"]:
        slug = office["slug"]
        ranking = sorted(((n, sq) for sq, n in counts.items() if by_sq.get(sq, (None,))[0] == slug),
                         key=lambda x: (-x[0], by_sq[x[1]][1]["name"]))
        print(f"\n{office['title']} — {picked_someone[slug]} respostas marcaram alguém")
        for n, sq in ranking[:args.top]:
            c = by_sq[sq][1]
            print(f"  {n:>5}  {c['name']} ({c['number']}, {c['party']})")
    outside = [sq for sq in counts if sq not in by_sq]
    if outside:
        print(f"\naviso: {len(outside)} candidaturas das respostas não estão no catálogo atual")

    if graph is not None:
        catalog_ttl = ROOT / "data" / "candidates.ttl"
        if catalog_ttl.exists():
            graph.parse(str(catalog_ttl), format="turtle")
        graph.serialize(destination=args.ttl, format="turtle")
        print(f"\ngrafo consolidado: {args.ttl}")
    if args.tally_json:
        tally = {"version": 1, "responses": total, "counts": dict(counts),
                 "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        Path(args.tally_json).write_text(json.dumps(tally, separators=(",", ":")), encoding="utf-8")
        print(f"placar reconstruído: {args.tally_json}")


if __name__ == "__main__":
    main()
