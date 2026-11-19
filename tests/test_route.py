import json
from collections import Counter
from types import SimpleNamespace

import pytest

import cli
from pipeline import batch, db, extract, route
from pipeline.fake_client import FakeAPI
from pipeline.rules import Issue
from pipeline.schemas import CATEGORIES, PRODUCT_MODELS
from pipeline.sheets import load_sheets, sheets_path

SHEETS = {sheet["id"]: sheet for sheet in load_sheets(sheets_path("dev"))}


def extracted(tmp_path, api, *sheet_ids):
    """Um banco novo com as fichas dadas já extraídas pela API falsa, ainda sem destino."""
    conn = db.connect(tmp_path / "catalog.db")
    extract_more(conn, api, *sheet_ids)
    return conn


def extract_more(conn, api, *sheet_ids):
    """Guarda as fichas dadas e extrai todas as pendentes com a API falsa, sem rotear."""
    db.add_sheets(conn, "dev", [(sheet_id, SHEETS[sheet_id]["texto"]) for sheet_id in sheet_ids])
    extract.process(api.client, conn)


def routes(conn):
    """{id da ficha: (destino, motivos, auditoria)}."""
    rows = conn.execute(
        "SELECT f.id_origem, p.destino, p.motivos, p.auditoria "
        "FROM produtos p JOIN fichas f ON f.id = p.ficha_id"
    )
    return {row[0]: (row[1], json.loads(row[2]), row[3]) for row in rows}


def doubt(field, level):
    return {"campo": field, "nivel": level, "motivo": "o texto traz dois valores"}


@pytest.mark.parametrize(
    "category, extraction, doubts, reasons",
    [
        ("processador", "valido", [], []),
        ("processador", "reprovado", [], ["reprovado"]),
        ("processador", "valido", [doubt("tdp_w", "baixa")], ["duvida_baixa"]),
        ("outro", "valido", [doubt("preco", "baixa")], ["duvida_baixa"]),
        # A dúvida 'media' só aparece destacada na tela, mesmo num campo importante.
        ("processador", "valido", [doubt("tdp_w", "media")], []),
        # A 'baixa' num campo que não pesa na compra nem na compatibilidade, também.
        ("processador", "valido", [doubt("cooler_incluso", "baixa")], []),
        (
            "fonte",
            "reprovado",
            [doubt("ventoinha_mm", "media"), doubt("potencia_w", "baixa")],
            ["reprovado", "duvida_baixa"],
        ),
    ],
)
def test_review_reasons(category, extraction, doubts, reasons):
    assert route.review_reasons(category, extraction, doubts) == reasons


def test_important_fields_are_fields_of_the_category():
    assert route.IMPORTANT_FIELDS.keys() == PRODUCT_MODELS.keys()
    for category, fields in route.IMPORTANT_FIELDS.items():
        assert "preco" in fields
        assert fields <= set(PRODUCT_MODELS[category].model_fields)


def test_each_reason_with_the_fake_api(tmp_path):
    api = FakeAPI(
        wrong_always={"dev-030"},
        doubts={
            "dev-001": {"tdp_w": "baixa"},
            "dev-002": {"tdp_w": "media"},
            "dev-003": {"cooler_incluso": "baixa"},
        },
    )
    conn = extracted(tmp_path, api, "dev-001", "dev-002", "dev-003", "dev-030", "dev-031")
    assert {destino for destino, _, _ in routes(conn).values()} == {None}

    counts = route.route(conn)

    saved = routes(conn)
    assert saved["dev-001"] == ("revisao", ["duvida_baixa"], 0)
    assert saved["dev-030"] == ("revisao", ["reprovado"], 0)
    # Os erros que sobraram continuam lá, para a tela mostrar.
    [errors] = conn.execute("SELECT erros FROM produtos WHERE ficha_id = 4").fetchone()
    assert [error["campo"] for error in json.loads(errors)] == ["garantia_meses"]
    # A dúvida 'media' e a 'baixa' num campo sem peso não mandam para a revisão. Das duas
    # aprovadas de processador, uma cai na auditoria; a única aprovada de fonte também.
    assert sorted([saved["dev-002"], saved["dev-003"]]) == [
        ("catalogo", [], 0),
        ("revisao", ["auditoria"], 1),
    ]
    assert saved["dev-031"] == ("revisao", ["auditoria"], 1)
    assert counts == Counter(
        produtos=5, catalogo=1, revisao=4, reprovado=1, duvida_baixa=1, auditoria=2
    )

    # Rodar de novo não mexe em nada: só os produtos sem destino são roteados.
    assert route.route(conn) == Counter()
    assert routes(conn) == saved


def test_cut_answer_goes_to_review_with_its_errors_and_complete_doubts(tmp_path):
    # A extração terminou cortada: o erro 'max_tokens' não é campo do produto, e a última dúvida
    # veio pela metade. Ela não é gravada, e o roteamento não quebra.
    conn = db.connect(tmp_path / "catalog.db")
    db.add_sheets(conn, "dev", [("dev-001", SHEETS["dev-001"]["texto"])])
    complete = doubt("preco", "baixa")
    block = SimpleNamespace(input={"nome": "Ryzen 7", "duvidas": [complete, {"campo": "tdp_w"}]})
    cut = Issue("max_tokens", "resposta cortada no limite de max_tokens.")
    result = extract.SheetResult("processador", extract.Answer(block, [cut], True), [cut], [])
    with conn:
        extract.store_product(conn, 1, result)

    route.route(conn)

    row = conn.execute("SELECT duvidas, extracao, erros, destino, motivos FROM produtos").fetchone()
    assert json.loads(row["duvidas"]) == [complete]
    assert row["extracao"] == "reprovado"
    assert json.loads(row["erros"]) == [{"campo": "max_tokens", "mensagem": cut.message}]
    assert row["destino"] == "revisao"
    assert json.loads(row["motivos"]) == ["reprovado", "duvida_baixa"]


def test_audit_sample_is_stratified_repeatable_and_seeded(monkeypatch):
    candidates = [(i, "processador") for i in range(1, 31)]
    candidates += [(i, "fonte") for i in range(31, 36)] + [(36, "cooler")]
    category_of = dict(candidates)
    approved = Counter(category_of.values())

    chosen = route.audit_sample(candidates, approved, Counter())

    # 10% de cada categoria, no mínimo 1: 3 de 30, 1 de 5 e 1 de 1.
    assert Counter(category_of[ficha_id] for ficha_id in chosen) == {
        "processador": 3,
        "fonte": 1,
        "cooler": 1,
    }
    # Repetível, em qualquer ordem de entrada.
    assert route.audit_sample(list(reversed(candidates)), approved, Counter()) == chosen
    # É a semente que decide: com outra, sai outro sorteio.
    monkeypatch.setattr(route, "AUDIT_SEED", route.AUDIT_SEED + 1)
    assert route.audit_sample(candidates, approved, Counter()) != chosen
    # A cota desconta as já auditadas: das 30 de processador (3 na cota), com 2 auditadas sai
    # mais 1; com 3, nenhuma.
    new_cpus = candidates[:5]
    assert len(route.audit_sample(new_cpus, approved, Counter(processador=2))) == 1
    assert route.audit_sample(new_cpus, approved, Counter(processador=3)) == set()


def test_audit_quota_counts_the_whole_database_and_skips_the_audited(tmp_path, monkeypatch):
    api = FakeAPI()
    conn = extracted(tmp_path, api, "dev-001")
    route.route(conn)
    assert routes(conn) == {"dev-001": ("revisao", ["auditoria"], 1)}

    # Com a taxa em 100%, a cota é o total de aprovados do banco menos os já auditados. Um
    # processador novo: 2 aprovados (o dev-001, já roteado, e ele) menos 1 auditado.
    monkeypatch.setattr(route, "AUDIT_RATE", 1.0)
    extract_more(conn, api, "dev-002")
    route.route(conn)
    assert routes(conn)["dev-002"] == ("revisao", ["auditoria"], 1)

    # Uma nova extração (como num reprocessamento) apaga o destino, mas não a auditoria.
    with conn:
        conn.execute("UPDATE fichas SET status = 'pendente'")
    extract_more(conn, api, "dev-003", "dev-004")
    assert routes(conn)["dev-001"] == (None, [], 1)

    # 4 aprovados menos 2 auditados: saem os dois novos, e os já auditados não voltam ao sorteio.
    route.route(conn)
    assert routes(conn) == {
        "dev-001": ("catalogo", [], 1),
        "dev-002": ("catalogo", [], 1),
        "dev-003": ("revisao", ["auditoria"], 1),
        "dev-004": ("revisao", ["auditoria"], 1),
    }


def status_table(out):
    """As linhas da tabela do status: {categoria: [produtos, catálogo, revisão, rejeitado,
    motivos...]}."""
    rows = {}
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0] in (*CATEGORIES, "total"):
            rows[parts[0]] = [int(part) for part in parts[1:]]
    return rows


def database_table(conn):
    """A mesma tabela, contada direto no banco. Os motivos são só os da fila."""
    rows = {category: [0] * 7 for category in CATEGORIES}
    by_destination = conn.execute(
        "SELECT categoria, COUNT(*), TOTAL(destino = 'catalogo'), TOTAL(destino = 'revisao'), "
        "TOTAL(destino = 'rejeitado') FROM produtos GROUP BY categoria"
    )
    for category, *counts in by_destination:
        rows[category][:4] = [int(count) for count in counts]
    by_reason = conn.execute(
        "SELECT p.categoria, m.value, COUNT(*) FROM produtos p, json_each(p.motivos) m "
        "WHERE p.destino = 'revisao' GROUP BY p.categoria, m.value"
    )
    for category, reason, total in by_reason:
        rows[category][4 + route.REASONS.index(reason)] = total
    rows["total"] = [sum(column) for column in zip(*rows.values())]
    return rows


def test_status_matches_the_database(tmp_path, capsys):
    db_file = tmp_path / "catalog.db"
    api = FakeAPI(wrong_always={"dev-030"}, doubts={"dev-001": {"tdp_w": "baixa"}})
    conn = db.connect(db_file)
    db.add_sheets(conn, "dev", [(sheet_id, sheet["texto"]) for sheet_id, sheet in SHEETS.items()])
    extract.process(api.client, conn, limit=10)
    route.route(conn)
    batch.process(api.client, conn, poll_seconds=0)  # o resto, em lote, ainda sem rotear
    capsys.readouterr()

    cli.main(["--db", str(db_file), "status"])
    out = capsys.readouterr().out
    assert "43 produtos ainda sem destino: rode `cli.py route`" in out
    assert status_table(out) == database_table(conn)

    # O roteamento sozinho. As 2 categorias do primeiro roteamento já têm a sua auditoria; as
    # outras 7 ganham uma agora.
    cli.main(["--db", str(db_file), "route"])
    assert capsys.readouterr().out == (
        "roteamento: 43 produtos | 35 no catálogo | 8 na revisão"
        " (1 reprovado, 0 dúvida baixa, 7 auditoria)\n"
    )
    cli.main(["--db", str(db_file), "status"])
    out = capsys.readouterr().out

    assert "fichas pendentes: 0" in out
    assert "sem destino" not in out
    assert status_table(out) == database_table(conn)
    # Um reprovado, uma dúvida baixa e uma auditoria por categoria, como se as 53 fossem roteadas
    # de uma vez: a cota olha o banco inteiro, e não só o roteamento.
    assert status_table(out)["total"] == [53, 42, 11, 0, 1, 1, len(CATEGORIES)]

    # As rodadas e o custo gravado.
    rounds = conn.execute(
        "SELECT r.id, r.modo, COUNT(DISTINCT t.ficha_id), COUNT(*), TOTAL(t.custo) "
        "FROM rodadas r JOIN tentativas t ON t.rodada_id = r.id GROUP BY r.id ORDER BY r.id"
    ).fetchall()
    assert [row[1] for row in rounds] == ["direct", "batch"]
    lines = out.splitlines()
    assert "rodadas: 2" in lines
    for rodada_id, mode, sheets, calls, cost in rounds:
        start = f"  {rodada_id} | {cli.MODE_NAMES[mode]} | "
        end = f" | {sheets} fichas | {calls} chamadas | ${cost:.6f}"
        assert any(line.startswith(start) and line.endswith(end) for line in lines)
    total = conn.execute("SELECT TOTAL(custo) FROM tentativas").fetchone()[0]
    assert f"custo total gravado: ${total:.6f}" in lines

    # A decisão do revisor: quem sai da fila guarda os motivos, mas não conta mais neles.
    ids = dict(conn.execute("SELECT id_origem, id FROM fichas").fetchall())
    db.decide(conn, ids["dev-001"], "aprovar")
    db.decide(conn, ids["dev-030"], "rejeitar")
    cli.main(["--db", str(db_file), "status"])
    out = capsys.readouterr().out
    assert status_table(out) == database_table(conn)
    assert status_table(out)["total"] == [53, 43, 9, 1, 0, 0, len(CATEGORIES)]
