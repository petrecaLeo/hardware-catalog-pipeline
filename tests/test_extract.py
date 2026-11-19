import json

import pytest
from anthropic.types import Usage

import cli
from pipeline import db, extract
from pipeline.config import EXTRACT_MAX_TOKENS, EXTRACT_RETRY_MAX_TOKENS
from pipeline.fake_client import WRONG_DETAIL, WRONG_WARRANTY, FakeAPI
from pipeline.rules import validate_classification, validate_product
from pipeline.schemas import SCHEMA_VERSION
from pipeline.sheets import load_sheets, sheets_path, split_answer_key

SHEETS = {sheet["id"]: sheet for sheet in load_sheets(sheets_path("dev"))}


def run(tmp_path, monkeypatch, api, *sheet_ids):
    """Processa só as fichas dadas num banco novo. Devolve o banco, o resumo e os pedidos."""
    conn = db.connect(tmp_path / "catalog.db")
    db.add_sheets(conn, "dev", [(sheet_id, SHEETS[sheet_id]["texto"]) for sheet_id in sheet_ids])
    sent = []
    create = api.client.messages.create

    def spy(**params):
        sent.append(params)
        return create(**params)

    monkeypatch.setattr(api.client.messages, "create", spy)
    summary = extract.process(api.client, conn)
    return conn, summary, sent


def attempts(conn, etapa="extract"):
    rows = conn.execute(
        "SELECT numero, resposta, erro FROM tentativas WHERE etapa = ? ORDER BY id", (etapa,)
    )
    return [(row["numero"], json.loads(row["resposta"]), row["erro"]) for row in rows]


def product(conn):
    row = conn.execute("SELECT * FROM produtos").fetchone()
    return {
        "extracao": row["extracao"],
        "categoria": row["categoria"],
        "campos": json.loads(row["campos"]),
        "duvidas": json.loads(row["duvidas"]),
        "erros": json.loads(row["erros"]),
    }


def wrong_warranty_issues(sheet_id):
    # A resposta errada da API falsa (garantia 999), validada como o pipeline valida.
    fields = split_answer_key(SHEETS[sheet_id]["gabarito"])[1]
    wrong = fields | {"garantia_meses": WRONG_WARRANTY, "duvidas": []}
    return validate_product("fonte", wrong, SHEETS[sheet_id]["texto"], doubts=True)


def test_happy_path(tmp_path, monkeypatch):
    conn, summary, sent = run(tmp_path, monkeypatch, FakeAPI(), "dev-001", "dev-048")

    assert (summary.sheets, summary.valid, summary.failed) == (2, 2, 0)
    assert (summary.calls, summary.retries) == (4, 0)
    rows = conn.execute("SELECT * FROM produtos ORDER BY ficha_id").fetchall()
    for row, sheet_id in zip(rows, ["dev-001", "dev-048"]):
        classification, fields = split_answer_key(SHEETS[sheet_id]["gabarito"])
        assert row["categoria"] == classification["categoria"]
        assert json.loads(row["campos"]) == fields
        assert (row["extracao"], row["duvidas"], row["erros"]) == ("valido", "[]", "[]")
        assert row["destino"] is None  # o roteamento é do route.py
        assert row["versao_esquema"] == SCHEMA_VERSION
    statuses = conn.execute("SELECT DISTINCT status FROM fichas").fetchall()
    assert [row["status"] for row in statuses] == ["processada"]
    assert [row[0] for row in conn.execute("SELECT etapa FROM tentativas ORDER BY id")] == [
        "classify",
        "extract",
    ] * 2

    # O pedido: ferramenta forçada e strict, thinking desligado, ficha na tag e cache no system.
    params = sent[1]
    assert params["thinking"] == {"type": "disabled"}
    assert params["tool_choice"] == {"type": "tool", "name": "extrair_processador"}
    assert params["tools"][0]["strict"] is True
    assert params["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert params["messages"] == [extract.sheet_message(SHEETS["dev-001"]["texto"])]

    # Sem pendentes, nada é chamado e nenhuma rodada nova é criada.
    assert extract.process(FakeAPI().client, conn).sheets == 0
    assert conn.execute("SELECT COUNT(*) FROM rodadas").fetchone()[0] == 1


def test_value_error_fixed_on_second_attempt(tmp_path, monkeypatch):
    api = FakeAPI(wrong_first={"dev-030"})
    conn, summary, sent = run(tmp_path, monkeypatch, api, "dev-030")

    issues = wrong_warranty_issues("dev-030")
    [(_, first, error), (number, second, no_error)] = attempts(conn)
    assert first["garantia_meses"] == WRONG_WARRANTY
    assert error == "\n".join(issue.message for issue in issues)
    assert (number, second["garantia_meses"], no_error) == (2, 36, None)
    assert product(conn)["extracao"] == "valido"
    assert (summary.valid, summary.retries) == (1, 1)

    # A correção leva a ficha, a resposta errada e a mensagem exata num tool_result com is_error.
    sheet, wrong, result = sent[2]["messages"]
    assert sheet == sent[1]["messages"][0]
    [wrong_block] = wrong["content"]
    assert wrong_block["type"] == "tool_use"
    assert wrong_block["input"]["garantia_meses"] == WRONG_WARRANTY
    [result_block] = result["content"]
    assert result_block["tool_use_id"] == wrong_block["id"]
    assert result_block["is_error"] is True
    assert f"- {issues[0].message}" in result_block["content"]


def test_classification_error_fixed_on_second_attempt(tmp_path, monkeypatch):
    api = FakeAPI(wrong_class_first={"dev-030"})
    conn, summary, sent = run(tmp_path, monkeypatch, api, "dev-030")

    [(_, first, error), (number, second, no_error)] = attempts(conn, "classify")
    assert first == {"categoria": "fonte", "categoria_detalhe": WRONG_DETAIL}
    assert error == "\n".join(issue.message for issue in validate_classification(first))
    assert (number, second, no_error) == (2, {"categoria": "fonte", "categoria_detalhe": None}, None)
    assert (product(conn)["extracao"], product(conn)["categoria"]) == ("valido", "fonte")
    assert (summary.calls, summary.retries) == (3, 1)

    # A mesma conversa de correção da extração: a ficha, a resposta errada e a mensagem exata.
    sheet, wrong, result = sent[1]["messages"]
    assert sent[1]["tool_choice"]["name"] == "classificar_produto"
    assert sheet == extract.sheet_message(SHEETS["dev-030"]["texto"])
    assert wrong["content"][0]["input"] == first
    assert result["content"][0]["is_error"] is True
    assert f"- {error}" in result["content"][0]["content"]


def test_error_that_persists_is_marked(tmp_path, monkeypatch):
    conn, summary, _ = run(tmp_path, monkeypatch, FakeAPI(wrong_always={"dev-030"}), "dev-030")

    assert [(number, error is not None) for number, _, error in attempts(conn)] == [
        (1, True),
        (2, True),
        (3, True),
    ]
    saved = product(conn)
    assert saved["extracao"] == "reprovado"
    assert saved["campos"]["garantia_meses"] == WRONG_WARRANTY
    assert saved["erros"] == [
        {"campo": issue.field, "mensagem": issue.message}
        for issue in wrong_warranty_issues("dev-030")
    ]
    assert (summary.failed, summary.calls, summary.retries) == (1, 4, 2)


def test_cut_answer_is_resent_with_more_tokens(tmp_path, monkeypatch):
    conn, summary, sent = run(tmp_path, monkeypatch, FakeAPI(cut={"dev-001"}), "dev-001")

    [(_, cut, error), (_, complete, no_error)] = attempts(conn)
    assert "duvidas" not in cut and "max_tokens" in error
    assert complete["duvidas"] == [] and no_error is None
    assert product(conn)["extracao"] == "valido"
    # O mesmo pedido, sem conversa de correção, só com mais tokens.
    assert sent[1]["max_tokens"] == EXTRACT_MAX_TOKENS
    assert sent[2]["max_tokens"] == EXTRACT_RETRY_MAX_TOKENS
    assert sent[2]["messages"] == sent[1]["messages"]
    assert summary.retries == 1


def test_cost_is_summed(tmp_path, monkeypatch):
    usage = Usage(
        input_tokens=1000,
        output_tokens=200,
        cache_creation_input_tokens=2000,
        cache_read_input_tokens=10000,
    )
    # 1000 × 0,10 + 200 × 0,50 + 2000 × 0,125 + 10000 × 0,01 = 550 por milhão.
    assert extract.call_cost(usage) == pytest.approx(0.000550)
    assert extract.call_cost(usage, "batch") == pytest.approx(0.000275)
    line = "tokens: 1000 in / 200 out | cache: 2000 write / 10000 read | cost: $0.000550"
    assert extract.usage_line(usage, extract.call_cost(usage)) == line

    api = FakeAPI(wrong_first={"dev-030"})
    conn, summary, _ = run(tmp_path, monkeypatch, api, "dev-001", "dev-030")
    rows = conn.execute("SELECT tokens_entrada, tokens_saida, custo FROM tentativas").fetchall()
    assert len(rows) == summary.calls == 5
    for row in rows:
        expected = (row["tokens_entrada"] * 0.10 + row["tokens_saida"] * 0.50) / 1_000_000
        assert row["custo"] == pytest.approx(expected)
    assert summary.cost == pytest.approx(sum(row["custo"] for row in rows))


def test_cli_processes_all_sheets_with_the_fake_api(tmp_path, capsys):
    db_file = str(tmp_path / "catalog.db")
    cli.main(["--db", db_file, "ingest", str(sheets_path("dev")), str(sheets_path("holdout"))])
    cli.main(["--db", db_file, "process", "--direto", "--fake"])

    lines = capsys.readouterr().out.splitlines()
    # Os problemas simulados do --fake (dev-030 reprovado depois de 2 novas tentativas, e as
    # dúvidas 'baixa' de dev-005 e dev-019) aparecem já na primeira linha.
    assert "modo direto (API falsa, com problemas simulados em dev-005, dev-019, dev-030)" in lines
    assert lines[-2].startswith("resumo: 83 fichas | 82 válidas | 1 reprovadas | 168 chamadas")
    # O custo de cada chamada sai impresso logo depois da resposta.
    assert sum("| cost: $" in line for line in lines) == 168
    conn = db.connect(db_file)
    valid = conn.execute("SELECT COUNT(*) FROM produtos WHERE extracao = 'valido'").fetchone()[0]
    assert valid == 82
    assert db.pending_sheets(conn) == []
    # No fim, o roteamento: nenhum produto fica sem destino, e a fila tem os três motivos.
    assert lines[-1] == (
        "roteamento: 83 produtos | 71 no catálogo"
        " | 12 na revisão (1 reprovado, 2 dúvida baixa, 9 auditoria)"
    )
    assert conn.execute("SELECT COUNT(*) FROM produtos WHERE destino IS NULL").fetchone()[0] == 0
