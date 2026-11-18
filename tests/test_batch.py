import json
import re

import pytest

import cli
from pipeline import batch, db, extract
from pipeline.config import EXTRACT_MAX_TOKENS, EXTRACT_RETRY_MAX_TOKENS
from pipeline.fake_client import WRONG_WARRANTY, FakeAPI
from pipeline.rules import validate_product
from pipeline.sheets import load_sheets, sheets_path, split_answer_key

SHEETS = {sheet["id"]: sheet for sheet in load_sheets(sheets_path("dev"))}


def setup(tmp_path, monkeypatch, api, *sheet_ids):
    """Um banco novo só com as fichas dadas (ids 1, 2...) e os lotes enviados, como
    `{custom_id: params}` na ordem do envio."""
    conn = db.connect(tmp_path / "catalog.db")
    db.add_sheets(conn, "dev", [(sheet_id, SHEETS[sheet_id]["texto"]) for sheet_id in sheet_ids])
    sent = []
    create = api.client.messages.batches.create

    def spy(**kwargs):
        sent.append({request["custom_id"]: request["params"] for request in kwargs["requests"]})
        return create(**kwargs)

    monkeypatch.setattr(api.client.messages.batches, "create", spy)
    return conn, sent


def run(tmp_path, monkeypatch, api, *sheet_ids):
    conn, sent = setup(tmp_path, monkeypatch, api, *sheet_ids)
    summary = batch.process(api.client, conn, poll_seconds=0)
    return conn, summary, sent


def custom_ids(sent):
    return [list(requests) for requests in sent]


def product(conn, ficha_id=1):
    row = conn.execute("SELECT * FROM produtos WHERE ficha_id = ?", (ficha_id,)).fetchone()
    return {
        "extracao": row["extracao"],
        "categoria": row["categoria"],
        "campos": json.loads(row["campos"]),
        "erros": json.loads(row["erros"]),
    }


def numbers(summary):
    return (summary.sheets, summary.valid, summary.failed, summary.calls, summary.retries)


def wrong_warranty(sheet_id):
    """A resposta errada da API falsa (garantia 999) e os erros que as regras dão para ela."""
    fields = split_answer_key(SHEETS[sheet_id]["gabarito"])[1]
    wrong = fields | {"garantia_meses": WRONG_WARRANTY, "duvidas": []}
    return wrong, validate_product("fonte", wrong, SHEETS[sheet_id]["texto"], doubts=True)


def ctrl_c(seconds):
    raise KeyboardInterrupt


def test_results_out_of_order_are_matched_by_custom_id(tmp_path, monkeypatch, capsys):
    api = FakeAPI(out_of_order=True, in_progress_polls=1)
    sheet_ids = ["dev-001", "dev-030", "dev-048"]
    conn, summary, sent = run(tmp_path, monkeypatch, api, *sheet_ids)

    # O 1º lote classifica todas, o 2º extrai. O custom_id diz a ficha, a etapa e a tentativa.
    assert custom_ids(sent) == [
        ["f001-classify-1", "f002-classify-1", "f003-classify-1"],
        ["f001-extract-1", "f002-extract-1", "f003-extract-1"],
    ]
    # Os resultados voltaram invertidos, e cada ficha ficou com o próprio gabarito.
    for ficha_id, sheet_id in enumerate(sheet_ids, 1):
        classification, fields = split_answer_key(SHEETS[sheet_id]["gabarito"])
        saved = product(conn, ficha_id)
        assert (saved["categoria"], saved["campos"]) == (classification["categoria"], fields)
        assert saved["extracao"] == "valido"
    assert numbers(summary) == (3, 3, 0, 6, 0)

    # O pedido é o mesmo do modo direto.
    message = extract.sheet_message(SHEETS["dev-001"]["texto"])
    params = extract.request_params("extract", "processador", [message], EXTRACT_MAX_TOKENS)
    assert sent[1]["f001-extract-1"] == params

    # Cada lote fica no banco com a rodada e o que ele levou.
    lotes = conn.execute("SELECT rodada_id, custom_ids, status FROM lotes ORDER BY rowid")
    assert [(row[0], json.loads(row[1]), row[2]) for row in lotes] == [
        (1, custom_ids(sent)[0], "concluido"),
        (1, custom_ids(sent)[1], "concluido"),
    ]
    assert [row[0] for row in conn.execute("SELECT modo FROM rodadas")] == ["batch"]

    # Cada consulta imprime a hora e o andamento: em andamento, depois terminado.
    out = capsys.readouterr().out
    progress = re.findall(r"^  \d\d:\d\d:\d\d (\d+ processando .*)$", out, re.MULTILINE)
    assert progress == [
        "3 processando | 0 prontos | 0 com erro",
        "0 processando | 3 prontos | 0 com erro",
    ] * 2


def test_errored_and_expired_are_resent_as_they_were(tmp_path, monkeypatch):
    api = FakeAPI(errored={"dev-002"}, expired={"dev-003"})
    conn, summary, sent = run(tmp_path, monkeypatch, api, "dev-001", "dev-002", "dev-003")

    # As duas classificações sem resposta vão no lote seguinte, junto da extração da outra ficha.
    assert custom_ids(sent) == [
        ["f001-classify-1", "f002-classify-1", "f003-classify-1"],
        ["f001-extract-1", "f002-classify-2", "f003-classify-2"],
        ["f002-extract-1", "f003-extract-1"],
    ]
    assert sent[1]["f002-classify-2"] == sent[0]["f002-classify-1"]
    assert sent[1]["f003-classify-2"] == sent[0]["f003-classify-1"]

    failed = conn.execute(
        "SELECT ficha_id, resposta, stop_reason, erro, custo FROM tentativas "
        "WHERE erro IS NOT NULL ORDER BY ficha_id"
    )
    assert [tuple(row) for row in failed] == [
        (2, None, None, "o lote devolveu erro: API falsa: erro interno simulado", 0.0),
        (3, None, None, "o pedido expirou no lote sem resposta", 0.0),
    ]
    assert numbers(summary) == (3, 3, 0, 8, 2)


def test_cut_answer_is_resent_with_more_tokens(tmp_path, monkeypatch):
    conn, summary, sent = run(tmp_path, monkeypatch, FakeAPI(cut={"dev-001"}), "dev-001")

    assert custom_ids(sent) == [["f001-classify-1"], ["f001-extract-1"], ["f001-extract-2"]]
    first, again = sent[1]["f001-extract-1"], sent[2]["f001-extract-2"]
    assert first["max_tokens"] == EXTRACT_MAX_TOKENS
    assert again["max_tokens"] == EXTRACT_RETRY_MAX_TOKENS
    assert again["messages"] == first["messages"]  # sem conversa de correção

    rows = conn.execute("SELECT stop_reason, erro FROM tentativas WHERE etapa = 'extract'")
    [(cut, error), (whole, no_error)] = rows.fetchall()
    assert cut == "max_tokens" and "max_tokens" in error
    assert (whole, no_error) == ("tool_use", None)
    assert product(conn)["extracao"] == "valido"
    assert numbers(summary) == (1, 1, 0, 3, 1)


def test_value_error_fixed_by_a_correction_batch(tmp_path, monkeypatch):
    conn, summary, sent = run(tmp_path, monkeypatch, FakeAPI(wrong_first={"dev-030"}), "dev-030")

    assert custom_ids(sent) == [["f001-classify-1"], ["f001-extract-1"], ["f001-extract-2"]]
    # A correção leva a ficha, a resposta errada e a mensagem exata. O bloco tool_use é montado
    # com o id da tentativa, que não é guardado.
    wrong, issues = wrong_warranty("dev-030")
    sheet, answer, result = sent[2]["f001-extract-2"]["messages"]
    assert sheet == extract.sheet_message(SHEETS["dev-030"]["texto"])
    tool_use = {"type": "tool_use", "id": "toolu_f001-extract-1", "name": "extrair_fonte"}
    assert answer == {"role": "assistant", "content": [tool_use | {"input": wrong}]}
    errors = "\n".join(f"- {issue.message}" for issue in issues)
    assert result["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "toolu_f001-extract-1",
            "is_error": True,
            "content": extract.CORRECTION.format(errors=errors),
        }
    ]
    saved = product(conn)
    assert (saved["extracao"], saved["campos"]["garantia_meses"]) == ("valido", 36)
    assert numbers(summary) == (1, 1, 0, 3, 1)


def test_error_that_persists_is_marked(tmp_path, monkeypatch):
    conn, summary, sent = run(tmp_path, monkeypatch, FakeAPI(wrong_always={"dev-030"}), "dev-030")

    assert custom_ids(sent)[1:] == [["f001-extract-1"], ["f001-extract-2"], ["f001-extract-3"]]
    # A 2ª correção leva a conversa inteira: a ficha e as duas respostas erradas com os erros.
    assert len(sent[3]["f001-extract-3"]["messages"]) == 5
    saved = product(conn)
    assert saved["extracao"] == "reprovado"
    assert saved["erros"] == [
        {"campo": issue.field, "mensagem": issue.message} for issue in wrong_warranty("dev-030")[1]
    ]
    assert numbers(summary) == (1, 0, 1, 4, 2)


def test_interrupted_run_resumes_the_pending_batch(tmp_path, monkeypatch, capsys):
    api = FakeAPI(in_progress_polls=1)  # os lotes falsos ficam na memória desta instância
    conn, sent = setup(tmp_path, monkeypatch, api, "dev-001", "dev-030")
    waits = []
    monkeypatch.setattr(batch.time, "sleep", waits.append)
    add_attempt, saved = db.add_attempt, []

    def ctrl_c_while_saving_the_second_batch(*args):
        # O 2º lote terminou, e o processo cai no meio da gravação dos resultados dele.
        if len(saved) == 3:
            saved.append("interrompido")
            raise KeyboardInterrupt
        saved.append(args)
        add_attempt(*args)

    monkeypatch.setattr(db, "add_attempt", ctrl_c_while_saving_the_second_batch)
    with pytest.raises(KeyboardInterrupt):
        batch.process(api.client, conn, poll_seconds=30)

    # A classificação foi salva. Da extração, nada: a tentativa já gravada foi desfeita, e o
    # lote continua pendente.
    assert [row[0] for row in conn.execute("SELECT status FROM lotes ORDER BY rowid")] == [
        "concluido",
        "pendente",
    ]
    assert [row[0] for row in conn.execute("SELECT etapa FROM tentativas")] == ["classify"] * 2
    assert len(db.pending_sheets(conn)) == 2
    capsys.readouterr()

    summary = batch.process(api.client, conn, poll_seconds=30)

    # Nada foi enviado de novo: o lote pendente foi consultado e lido, e gravado uma vez só.
    assert len(sent) == 2
    out = capsys.readouterr().out
    assert "retomando a rodada 1: 2 fichas sem terminar" in out
    pending_id = conn.execute("SELECT id FROM lotes ORDER BY rowid").fetchall()[1][0]
    assert f"lote {pending_id} já enviado: esperando o resultado" in out
    assert [row[0] for row in conn.execute("SELECT status FROM lotes")] == ["concluido"] * 2
    assert [product(conn, ficha_id)["extracao"] for ficha_id in (1, 2)] == ["valido"] * 2
    assert numbers(summary) == (2, 2, 0, 4, 0)
    assert conn.execute("SELECT COUNT(*) FROM rodadas").fetchone()[0] == 1
    assert waits == [30, 30]


def test_batch_the_api_no_longer_has_is_sent_again(tmp_path, monkeypatch, capsys):
    first = FakeAPI(in_progress_polls=1)
    conn, sent = setup(tmp_path, monkeypatch, first, "dev-001")
    monkeypatch.setattr(batch.time, "sleep", ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        batch.process(first.client, conn, poll_seconds=30)

    # Outra instância da API falsa não conhece o lote (404), como um --fake noutro processo.
    summary = batch.process(FakeAPI().client, conn, poll_seconds=0)

    assert "não existe mais na API: os pedidos vão de novo" in capsys.readouterr().out
    lotes = conn.execute("SELECT custom_ids, status FROM lotes ORDER BY rowid").fetchall()
    assert [(json.loads(row[0]), row[1]) for row in lotes] == [
        (["f001-classify-1"], "perdido"),
        (["f001-classify-1"], "concluido"),
        (["f001-extract-1"], "concluido"),
    ]
    assert numbers(summary) == (1, 1, 0, 2, 0)


def test_cost_uses_the_batch_price(tmp_path, monkeypatch, capsys):
    direct_conn = db.connect(tmp_path / "direto.db")
    db.add_sheets(direct_conn, "dev", [(i, SHEETS[i]["texto"]) for i in ("dev-001", "dev-030")])
    direct = extract.process(FakeAPI().client, direct_conn)
    capsys.readouterr()

    api = FakeAPI(errored={"dev-001"})
    conn, summary, _ = run(tmp_path, monkeypatch, api, "dev-001", "dev-030")

    # Os mesmos pedidos gastam os mesmos tokens; o lote custa a metade. O pedido sem resposta
    # não é cobrado.
    assert summary.cost == pytest.approx(direct.cost / 2)
    rows = conn.execute("SELECT tokens_entrada, tokens_saida, custo FROM tentativas").fetchall()
    for row in rows:
        expected = (row["tokens_entrada"] * 0.05 + row["tokens_saida"] * 0.25) / 1_000_000
        assert row["custo"] == pytest.approx(expected)
    assert summary.cost == pytest.approx(sum(row["custo"] for row in rows))
    # O custo de cada resultado sai impresso, também o do que não teve resposta.
    lines = capsys.readouterr().out.splitlines()
    assert sum("| cost: $" in line for line in lines) == len(rows) == 5
    assert "  [dev-001] classify 1: tokens: 0 in / 0 out | cost: $0.000000" in lines


def test_cli_processes_all_sheets_in_batches_with_the_fake_api(tmp_path, capsys):
    db_file = str(tmp_path / "catalog.db")
    cli.main(["--db", db_file, "ingest", str(sheets_path("dev")), str(sheets_path("holdout"))])
    cli.main(["--db", db_file, "process", "--lote", "--fake"])

    lines = capsys.readouterr().out.splitlines()
    assert lines[-2].startswith("resumo: 83 fichas | 83 válidas | 0 reprovadas | 166 chamadas")
    assert sum("| cost: $" in line for line in lines) == 166
    conn = db.connect(db_file)
    assert db.pending_sheets(conn) == []
    # No fim, o roteamento: nenhum produto fica sem destino.
    assert lines[-1].startswith("roteamento: 83 produtos | ")
    assert conn.execute("SELECT COUNT(*) FROM produtos WHERE destino IS NULL").fetchone()[0] == 0
    assert [tuple(row) for row in conn.execute("SELECT status, COUNT(*) FROM lotes")] == [
        ("concluido", 2)
    ]
