"""datahidro — modelo RDF compartilhado entre o backend e tools/ingest_tse.py.

Um lugar só pro mapeamento dicionário → triplas: o catálogo publicado
(data/candidates.ttl, gerado pelo ingest) e as triplas de catálogo que o
backend junta ao validar uma resposta saem da MESMA função
(`candidacy_triples`), então não divergem.

IRIs (não dereferenciam ainda — sem resolver, diferente do amora):
  vocab        https://id.pedalhidrografi.co/datahidro/terms#
  candidatura  https://id.pedalhidrografi.co/datahidro/candidatura/2026/<SQ_CANDIDATO>
  resposta     https://id.pedalhidrografi.co/datahidro/resposta/<uuid v4>
Sem blank nodes (convenção do ecossistema).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, PROV, RDF, SKOS, XSD

DH = Namespace("https://id.pedalhidrografi.co/datahidro/terms#")
SCHEMA = Namespace("https://schema.org/")
CAND_BASE = "https://id.pedalhidrografi.co/datahidro/candidatura/2026/"
RESP_BASE = "https://id.pedalhidrografi.co/datahidro/resposta/"
CATALOG_IRI = URIRef("https://id.pedalhidrografi.co/datahidro/candidaturas/2026")
SURVEY = URIRef("https://id.pedalhidrografi.co/datahidro/levantamento/2026")
TSE_DATASET = URIRef("https://dadosabertos.tse.jus.br/dataset/candidatos-2026")

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SQ_RE = re.compile(r"^[0-9]{1,15}$")


def bind(g: Graph) -> Graph:
    g.bind("dh", DH)
    g.bind("schema", SCHEMA)
    g.bind("prov", PROV)
    g.bind("dcterms", DCTERMS)
    g.bind("xsd", XSD)
    g.bind("cand", CAND_BASE)
    g.bind("resp", RESP_BASE)
    return g


def candidacy_iri(sq: str) -> URIRef:
    return URIRef(CAND_BASE + sq)


def response_iri(response_id: str) -> URIRef:
    return URIRef(RESP_BASE + response_id)


def xsd_datetime(dt: datetime) -> Literal:
    iso = dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    return Literal(iso, datatype=XSD.dateTime)


# ---------------------------------------------------------------- vocab

def load_offices(vocab_path: str | Path) -> list[dict]:
    """Cargos do vocab.ttl, em ordem de tela, como dicts prontos pro JSON."""
    g = Graph()
    g.parse(str(vocab_path), format="turtle")

    offices = []
    for o in g.subjects(RDF.type, DH.Office):
        offices.append({
            "slug": str(g.value(o, DH.slug)),
            "iri": str(o),
            "label": str(g.value(o, SKOS.prefLabel)),
            "title": str(g.value(o, DH.deckLabel)),
            "tse_code": str(g.value(o, SKOS.notation)),
            "jurisdiction": str(g.value(o, DH.jurisdiction)),
            "max": int(g.value(o, DH.maxChoices)),
            "order": int(g.value(o, DH.deckOrder)),
        })
    offices.sort(key=lambda o: o["order"])
    return offices


# ---------------------------------------------------------------- candidatura

def candidacy_triples(c: dict, office_iri: str, public_base: str,
                      party_name: str | None = None) -> list[tuple]:
    """Triplas de UMA candidatura. `c` = registro do candidates.json,
    opcionalmente com os campos que só o ingest tem (federation, gender,
    status)."""
    s = candidacy_iri(c["sq"])
    triples = [
        (s, RDF.type, DH.Candidacy),
        (s, DH.office, URIRef(office_iri)),
        (s, DH.tseSequence, Literal(c["sq"])),
        (s, DH.ballotNumber, Literal(c["number"])),
        (s, SCHEMA.name, Literal(c["name"])),
        (s, DH.partyAcronym, Literal(c["party"])),
    ]
    if c.get("full_name"):
        triples.append((s, SCHEMA.alternateName, Literal(c["full_name"])))
    if c.get("photo"):
        triples.append((s, SCHEMA.image, URIRef(public_base.rstrip("/") + "/" + c["photo"])))
    if party_name:
        triples.append((s, DH.partyName, Literal(party_name)))
    if c.get("federation"):
        triples.append((s, DH.federation, Literal(c["federation"])))
    if c.get("gender"):
        triples.append((s, DH.declaredGender, Literal(c["gender"])))
    if c.get("status"):
        triples.append((s, DH.registrationStatus, Literal(c["status"])))
    return triples


# ---------------------------------------------------------------- resposta

def response_graph(response_id: str, choices: list[str], consent: bool,
                   consent_version: str, generated_at: datetime,
                   modified_at: datetime | None = None) -> Graph:
    """Grafo de uma resposta anônima (escolhas + consentimento). Não valida
    nada — quem decide é o SHACL."""
    g = bind(Graph())
    s = response_iri(response_id)
    g.add((s, RDF.type, DH.SurveyResponse))
    g.add((s, DH.survey, SURVEY))
    g.add((s, PROV.generatedAtTime, xsd_datetime(generated_at)))
    if modified_at is not None:
        g.add((s, DCTERMS.modified, xsd_datetime(modified_at)))
    g.add((s, DH.consentGiven, Literal(bool(consent))))
    g.add((s, DH.consentVersion, Literal(consent_version)))
    for sq in choices:
        g.add((s, DH.choice, candidacy_iri(sq)))
    return g


def read_response(ttl_text: str, response_id: str) -> tuple[datetime | None, list[str]]:
    """(prov:generatedAtTime, [SQ escolhidos]) de uma resposta já gravada —
    o horário é preservado ao reenviar; as escolhas antigas saem do placar."""
    g = Graph()
    g.parse(data=ttl_text, format="turtle")
    s = response_iri(response_id)
    choices = sorted(
        str(o)[len(CAND_BASE):] for o in g.objects(s, DH.choice) if str(o).startswith(CAND_BASE)
    )
    value = g.value(s, PROV.generatedAtTime)
    try:
        generated = datetime.fromisoformat(str(value)) if value is not None else None
    except ValueError:
        generated = None
    return generated, choices
