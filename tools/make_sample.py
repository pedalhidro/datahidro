#!/usr/bin/env python3
"""Gera DADOS FICTÍCIOS no formato do TSE, pra desenvolver e testar sem rede
(o CDN do TSE bloqueia IPs de nuvem):

  python3 tools/make_sample.py                               # → tools/_cache/sample/
  python3 tools/ingest_tse.py build --source tools/_cache/sample

Exercita o caminho real do ingest: CSV latin-1 com ';', marcadores #NULO#,
vices/suplentes (descartados), candidaturas INAPTAS, os dois gêneros e um zip
de fotos com ~20% de candidaturas sem foto.

Nomes e partidos são inventados e temáticos (águas de SP) de propósito —
ninguém confunde com candidatura real. O arquivo SAMPLE na pasta faz o ingest
marcar o catálogo com "sample": true; o app mostra uma faixa de aviso e o
deploy.sh se recusa a publicar.
"""
from __future__ import annotations

import csv
import io
import random
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "tools" / "_cache" / "sample"

COLUMNS = [
    "DT_GERACAO", "HH_GERACAO", "ANO_ELEICAO", "CD_TIPO_ELEICAO", "NM_TIPO_ELEICAO",
    "NR_TURNO", "CD_ELEICAO", "DS_ELEICAO", "DT_ELEICAO", "TP_ABRANGENCIA", "SG_UF",
    "SG_UE", "NM_UE", "CD_CARGO", "DS_CARGO", "SQ_CANDIDATO", "NR_CANDIDATO",
    "NM_CANDIDATO", "NM_URNA_CANDIDATO", "NM_SOCIAL_CANDIDATO", "NR_CPF_CANDIDATO",
    "NM_EMAIL", "CD_SITUACAO_CANDIDATURA", "DS_SITUACAO_CANDIDATURA",
    "CD_DETALHE_SITUACAO_CAND", "DS_DETALHE_SITUACAO_CAND", "TP_AGREMIACAO",
    "NR_PARTIDO", "SG_PARTIDO", "NM_PARTIDO", "NR_FEDERACAO", "NM_FEDERACAO",
    "SG_FEDERACAO", "DS_COMPOSICAO_FEDERACAO", "CD_GENERO", "DS_GENERO",
    "ST_CANDIDATO_INSERIDO_URNA",
]

# (código, descrição, UF, total, fração feminina, dígitos além do número do partido)
OFFICES = [
    (7, "DEPUTADO ESTADUAL", "SP", 2300, 0.35, 3),
    (6, "DEPUTADO FEDERAL", "SP", 1500, 0.35, 2),
    (5, "SENADOR", "SP", 15, 0.35, 1),
    (3, "GOVERNADOR", "SP", 8, 0.5, 0),
    (1, "PRESIDENTE", "BR", 13, 0.2, 0),
]
RUNNING_MATES = {  # descartados pelo ingest (não estão no vocab)
    (4, "VICE-GOVERNADOR", "SP"): 8, (2, "VICE-PRESIDENTE", "BR"): 13,
    (9, "1º SUPLENTE", "SP"): 15, (10, "2º SUPLENTE", "SP"): 15,
}

PARTIES = [  # 20 partidos × 100 = capacidade dos números de 4 dígitos de deputada federal
    (12, "PTAL", "PARTIDO DO TALVEGUE"), (17, "PNAS", "PARTIDO DAS NASCENTES"),
    (23, "PVRZ", "PARTIDO DA VÁRZEA"), (31, "PCOR", "PARTIDO DOS CÓRREGOS"),
    (38, "PESP", "PARTIDO DO ESPIGÃO"), (41, "PBRE", "PARTIDO DO BREJO"),
    (47, "PGAR", "PARTIDO DA GAROA"), (52, "PREP", "PARTIDO DA REPRESA"),
    (58, "PMEA", "PARTIDO DOS MEANDROS"), (63, "PJUS", "PARTIDO DA JUSANTE"),
    (69, "PCAB", "PARTIDO DA CABECEIRA"), (74, "PENC", "PARTIDO DA ENCOSTA"),
    (86, "PBIC", "PARTIDO DAS BICAS"), (91, "PALU", "PARTIDO ALUVIAL"),
    (14, "PMAN", "PARTIDO DOS MANANCIAIS"), (27, "PRIB", "PARTIDO RIBEIRINHO"),
    (33, "PDIV", "PARTIDO DO DIVISOR DE ÁGUAS"), (44, "PFOZ", "PARTIDO DA FOZ"),
    (56, "PORV", "PARTIDO DO ORVALHO"), (77, "PGEO", "PARTIDO GEOMORFOLÓGICO"),
]
FEDERATION = ("FEDERAÇÃO ÁGUAS OCULTAS", "FAO", "PNAS/PCOR/PBIC")

FEMININE_NAMES = ["NASCENTE", "CABECEIRA", "VÁRZEA", "GAROA", "REPRESA", "CORRENTEZA",
                  "ENSEADA", "LAGOA", "CACHOEIRA", "NEBLINA", "VERTENTE", "BICA", "FONTE",
                  "RIBEIRA", "JUSANTE", "MONTANTE", "PLANÍCIE", "MEANDRA", "ALUVIA", "BARRANCA"]
MASCULINE_NAMES = ["CÓRREGO", "RIACHO", "BREJO", "TALVEGUE", "ESPIGÃO", "DIVISOR", "ATERRO",
                   "BUEIRO", "PISCINÃO", "CANAL", "AQUEDUTO", "AÇUDE", "REMANSO", "REBOJO",
                   "BARRANCO", "VALE", "MORRO", "DECLIVE", "DEGRAU", "POÇO"]
WATERS = ["DO ANHANGABAÚ", "DA SARACURA", "DO ITORORÓ", "DO PACAEMBU", "DO IQUIRIRIM",
          "DO TAMANDUATEÍ", "DO ARICANDUVA", "DO CABUÇU", "DO PIRAJUÇARA", "DO UBERABA",
          "DO SAPATEIRO", "DA ÁGUA PRETA", "DA ÁGUA BRANCA", "DO IPIRANGA", "DO TATUAPÉ",
          "DA MOOCA", "DO CARANDIRU", "DO JAGUARÉ", "DAS CORUJAS", "DO MANDAQUI",
          "DO CORDEIRO", "DA TRAIÇÃO", "DO MOINHO VELHO", "DO VERDE", "DO BIXIGA"]
TITLES = ["PROFESSORA", "AGENTE", "CICLISTA", "MESTRA", "DOUTORA"]
PALETTE = [(168, 120, 13), (207, 86, 67), (223, 42, 147), (192, 65, 229),
           (125, 115, 240), (44, 144, 188), (12, 152, 124), (94, 148, 32)]


def fake_photo(rng: random.Random) -> bytes:
    """Retrato-silhueta 161x225 (tamanho típico das fotos de divulgação)."""
    from PIL import Image, ImageDraw

    background = rng.choice(PALETTE)
    im = Image.new("RGB", (161, 225), tuple(min(255, c + 90) for c in background))
    d = ImageDraw.Draw(im)
    skin = rng.choice([(60, 40, 30), (120, 80, 50), (190, 140, 100), (230, 190, 160)])
    d.ellipse((50, 45, 111, 120), fill=skin)
    d.ellipse((15, 125, 146, 290), fill=background)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=70)
    return buf.getvalue()


def main() -> None:
    rng = random.Random(2026)
    DEST.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    base = {
        "DT_GERACAO": now.strftime("%d/%m/%Y"), "HH_GERACAO": now.strftime("%H:%M:%S"),
        "ANO_ELEICAO": "2026", "CD_TIPO_ELEICAO": "2", "NM_TIPO_ELEICAO": "ELEIÇÃO ORDINÁRIA",
        "NR_TURNO": "1", "CD_ELEICAO": "0", "DS_ELEICAO": "EXEMPLO - DADOS FICTÍCIOS",  # latin-1: sem travessão
        "DT_ELEICAO": "04/10/2026", "NR_CPF_CANDIDATO": "-4", "NM_EMAIL": "#NULO#",
        "TP_AGREMIACAO": "PARTIDO ISOLADO",
    }
    rows = {"SP": [], "BR": []}
    photos = {"SP": {}, "BR": {}}
    state = {"sq": 259990000000}
    used_numbers = set()

    def add_row(code, office, uf, female, number, party, unfit=False):
        state["sq"] += rng.randint(1, 9)
        sq = state["sq"]
        party_number, acronym, party_name = party
        first = rng.choice(FEMININE_NAMES if female else MASCULINE_NAMES)
        middle = rng.choice(FEMININE_NAMES if female else MASCULINE_NAMES)
        water = rng.choice(WATERS)
        ballot_name = f"{first} {water}"
        if female and rng.random() < 0.12:
            ballot_name = f"{rng.choice(TITLES)} {first}"
        fed = FEDERATION if acronym in FEDERATION[2].split("/") else ("#NULO#", "#NULO#", "#NULO#")
        rows[uf].append({
            **base, "TP_ABRANGENCIA": "FEDERAL" if uf == "BR" else "ESTADUAL",
            "SG_UF": uf, "SG_UE": uf, "NM_UE": "BRASIL" if uf == "BR" else "SÃO PAULO",
            "CD_CARGO": str(code), "DS_CARGO": office, "SQ_CANDIDATO": str(sq),
            "NR_CANDIDATO": number, "NM_CANDIDATO": f"{first} {middle} {water}",
            "NM_URNA_CANDIDATO": ballot_name,
            "NM_SOCIAL_CANDIDATO": f"{first} {water}" if rng.random() < 0.03 else "#NULO#",
            "CD_SITUACAO_CANDIDATURA": "3" if unfit else "12",
            "DS_SITUACAO_CANDIDATURA": "INAPTO" if unfit else "APTO",
            "CD_DETALHE_SITUACAO_CAND": "6" if unfit else "2",
            "DS_DETALHE_SITUACAO_CAND": rng.choice(["RENÚNCIA", "INDEFERIDO"]) if unfit
            else rng.choice(["DEFERIDO", "DEFERIDO", "AGUARDANDO JULGAMENTO"]),
            "NR_PARTIDO": str(party_number), "SG_PARTIDO": acronym, "NM_PARTIDO": party_name,
            "NR_FEDERACAO": "-1", "NM_FEDERACAO": fed[0], "SG_FEDERACAO": fed[1],
            "DS_COMPOSICAO_FEDERACAO": fed[2],
            "CD_GENERO": "4" if female else "2",
            "DS_GENERO": "FEMININO" if female else "MASCULINO",
            "ST_CANDIDATO_INSERIDO_URNA": "NÃO" if unfit else "SIM",
        })
        if rng.random() < 0.8:
            photos[uf][f"F{uf}{sq}_div.jpg"] = fake_photo(rng)

    for code, office, uf, total, female_share, digits in OFFICES:
        for i in range(total):
            party = PARTIES[i % len(PARTIES)] if digits == 0 else rng.choice(PARTIES)
            while True:
                suffix = "".join(str(rng.randint(0, 9)) for _ in range(digits))
                number = f"{party[0]}{suffix}"
                if (code, number) not in used_numbers:
                    used_numbers.add((code, number))
                    break
            add_row(code, office, uf, rng.random() < female_share, number, party,
                    unfit=rng.random() < 0.05)
    for (code, office, uf), total in RUNNING_MATES.items():
        for i in range(total):
            party = PARTIES[i % len(PARTIES)]
            add_row(code, office, uf, rng.random() < 0.4, str(party[0]), party)

    csv_bytes = {}
    for uf, uf_rows in rows.items():
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=COLUMNS, delimiter=";", quoting=csv.QUOTE_ALL,
                           lineterminator="\r\n")
        w.writeheader()
        w.writerows(uf_rows)
        csv_bytes[uf] = buf.getvalue().encode("latin-1")

    with zipfile.ZipFile(DEST / "consulta_cand_2026.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        for uf, data in csv_bytes.items():
            zf.writestr(f"consulta_cand_2026_{uf}.csv", data)
        zf.writestr("leiame.txt", "DADOS FICTÍCIOS gerados por tools/make_sample.py\n")
    for uf, files in photos.items():
        with zipfile.ZipFile(DEST / f"foto_cand2026_{uf}_div.zip", "w") as zf:
            for name, data in files.items():
                zf.writestr(name, data)
    (DEST / "SAMPLE").write_text("dados fictícios — ver tools/make_sample.py\n", encoding="utf-8")
    print(f"exemplo gerado em {DEST.relative_to(ROOT)} ({sum(len(f) for f in photos.values())} fotos)")


if __name__ == "__main__":
    main()
