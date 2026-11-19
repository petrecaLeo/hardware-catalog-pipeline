import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from pipeline import batch, db, extract, route
from pipeline.fake_client import DOUBT_REASON, WRONG_WARRANTY, FakeAPI
from pipeline.schemas import PRODUCT_MODELS
from pipeline.sheets import load_sheets, sheets_path, split_answer_key

SHEETS = {sheet["id"]: sheet for sheet in load_sheets(sheets_path("dev"))}
# Os processadores no modo direto e o resto em lote, para o resumo ter duas rodadas. Depois do
# roteamento, ficam na fila: dev-001 (dúvida baixa no TDP), dev-030 (garantia 999 até o fim) e a
# auditoria de cada categoria (um dos processadores 002/003, o SSD dev-029 e a fonte dev-031).
# O outro processador vai para o catálogo.
DIRECT = ("dev-001", "dev-002", "dev-003")
IN_BATCH = ("dev-029", "dev-030", "dev-031")


@pytest.fixture(scope="module")
def filled_db(tmp_path_factory):
    """O banco preenchido pela API falsa, uma vez só: cada teste trabalha numa cópia."""
    path = tmp_path_factory.mktemp("api") / "catalog.db"
    conn = db.connect(path)
    db.add_sheets(conn, "dev", [(i, SHEETS[i]["texto"]) for i in DIRECT + IN_BATCH])
    api = FakeAPI(wrong_always={"dev-030"}, doubts={"dev-001": ("tdp_w", "baixa")})
    extract.process(api.client, conn, limit=len(DIRECT))
    batch.process(api.client, conn, poll_seconds=0)
    route.route(conn)
    conn.close()
    return path


@pytest.fixture
def db_file(filled_db, tmp_path):
    return Path(shutil.copy(filled_db, tmp_path / "catalog.db"))


@pytest.fixture
def conn(db_file):
    conn = db.connect(db_file)
    yield conn
    conn.close()


@pytest.fixture
def client(db_file):
    return TestClient(create_app(db_file), base_url="http://127.0.0.1:8000")


def ids(conn):
    """{id_origem: id da ficha}."""
    return dict(conn.execute("SELECT id_origem, id FROM fichas").fetchall())


def product(conn, sheet_id):
    """(destino, campos, motivos) do produto, lidos do banco."""
    row = conn.execute(
        "SELECT destino, campos, motivos FROM produtos WHERE ficha_id = ?", (sheet_id,)
    ).fetchone()
    return row["destino"], json.loads(row["campos"]), json.loads(row["motivos"])


def reviews(conn):
    """As linhas de `revisoes`: (ficha, ação, antes, depois)."""
    rows = conn.execute("SELECT ficha_id, acao, antes, depois FROM revisoes ORDER BY id")
    return [
        (row[0], row[1], json.loads(row[2]), row[3] and json.loads(row[3])) for row in rows
    ]


def queue_ids(client):
    return [item["id"] for item in client.get("/api/queue").json()]


def catalog(client):
    return {item["id"]: item["campos"] for item in client.get("/api/catalog").json()}


def test_queue_lists_the_review_products_with_their_reasons(client, conn):
    response = client.get("/api/queue")

    assert response.status_code == 200
    queue = {item["id_origem"]: item for item in response.json()}
    [audited_cpu] = {"dev-002", "dev-003"} & queue.keys()
    assert {sheet: item["motivos"] for sheet, item in queue.items()} == {
        "dev-001": ["duvida_baixa"],
        audited_cpu: ["auditoria"],
        "dev-029": ["auditoria"],
        "dev-030": ["reprovado"],
        "dev-031": ["auditoria"],
    }
    assert queue["dev-030"] == {
        "id": ids(conn)["dev-030"],
        "id_origem": "dev-030",
        "categoria": "fonte",
        "nome": "Fonte Quelon Força 650W 80 Plus Bronze",
        "motivos": ["reprovado"],
    }


def test_sheet_shows_text_extraction_errors_attempts_and_form(client, conn):
    sheet = client.get(f"/api/sheets/{ids(conn)['dev-030']}").json()

    assert sheet["texto"] == SHEETS["dev-030"]["texto"].strip()
    fields = split_answer_key(SHEETS["dev-030"]["gabarito"])[1]
    assert sheet["campos"] == fields | {"garantia_meses": WRONG_WARRANTY}
    assert (sheet["categoria"], sheet["extracao"], sheet["destino"]) == (
        "fonte",
        "reprovado",
        "revisao",
    )
    assert [error["campo"] for error in sheet["erros"]] == ["garantia_meses"]
    assert sheet["motivos"] == ["reprovado"]
    # Todas as chamadas, no lote: a classificação e a extração com as 2 novas tentativas.
    attempts = sheet["tentativas"]
    assert [(a["modo"], a["etapa"], a["numero"]) for a in attempts] == [
        ("batch", "classify", 1),
        ("batch", "extract", 1),
        ("batch", "extract", 2),
        ("batch", "extract", 3),
    ]
    assert attempts[0]["erro"] is None
    assert all(a["erro"].startswith("garantia_meses: 999 meses") for a in attempts[1:])
    assert all(a["tokens_entrada"] > 0 and a["tokens_saida"] > 0 for a in attempts)
    assert all(a["custo"] > 0 for a in attempts)
    # O formulário: os campos da categoria, na ordem do esquema, com o tipo de cada um.
    form = {field["campo"]: field for field in sheet["formulario"]}
    assert list(form) == list(PRODUCT_MODELS["fonte"].model_fields)
    assert {name: field["tipo"] for name, field in form.items()} == {
        "nome": "texto",
        "marca": "texto",
        "preco": "numero",
        "garantia_meses": "inteiro",
        "potencia_w": "inteiro",
        "certificacao": "opcao",
        "modularidade": "opcao",
        "pfc_ativo": "booleano",
        "bivolt": "booleano",
        "conector_12v_2x6": "booleano",
        "ventoinha_mm": "inteiro",
    }
    assert form["modularidade"]["opcoes"] == ["nao_modular", "semi_modular", "modular"]
    assert [name for name, field in form.items() if not field["aceita_null"]] == ["nome"]
    assert form["potencia_w"]["descricao"].endswith("Não é o pico.")

    # As dúvidas do modelo vêm junto.
    doubts = client.get(f"/api/sheets/{ids(conn)['dev-001']}").json()["duvidas"]
    assert doubts == [{"campo": "tdp_w", "nivel": "baixa", "motivo": DOUBT_REASON}]


def test_approve_without_edits(client, conn):
    sheet_id = ids(conn)["dev-001"]
    _, fields, _ = product(conn, sheet_id)

    response = client.post(f"/api/sheets/{sheet_id}/approve", json={})

    assert response.status_code == 200
    assert response.json() == {"id": sheet_id, "destino": "catalogo"}
    assert sheet_id not in queue_ids(client)
    assert catalog(client)[sheet_id] == fields
    # Os motivos ficam: dizem por que o roteador mandou para a fila.
    assert product(conn, sheet_id) == ("catalogo", fields, ["duvida_baixa"])
    assert reviews(conn) == [(sheet_id, "aprovar", fields, None)]


@pytest.mark.parametrize(
    "sheet, change",
    [
        # A correção do revisor tira o reprovado da fila.
        ("dev-030", {"garantia_meses": 36}),
        # A ficha só tem preço em US$. A regra do US$ barra o modelo, mas não o revisor, que pode
        # saber o preço em reais.
        ("dev-029", {"preco": 289.9}),
    ],
)
def test_approve_with_a_valid_edit(client, conn, sheet, change):
    sheet_id = ids(conn)[sheet]
    _, before, reasons = product(conn, sheet_id)
    after = before | change

    response = client.post(f"/api/sheets/{sheet_id}/approve", json={"campos": after})

    assert response.status_code == 200
    assert sheet_id not in queue_ids(client)
    assert catalog(client)[sheet_id] == after
    assert product(conn, sheet_id) == ("catalogo", after, reasons)
    assert reviews(conn) == [(sheet_id, "aprovar", before, after)]


RANGE_HINT = "confira a unidade e se o número não é de outro dado da ficha."


@pytest.mark.parametrize(
    "sheet, change, errors",
    [
        (
            "dev-001",
            {"tdp_w": 1700},
            [("tdp_w", f"tdp_w: 1700 W está fora da faixa plausível (5 a 400 W); {RANGE_HINT}")],
        ),
        (
            "dev-001",
            {"nucleos": "8"},
            [("nucleos", "nucleos: tem que ser um número inteiro, sem texto nem unidade.")],
        ),
        (
            "dev-001",
            {"socket": "AM5"},
            [("socket", "socket: esse campo não existe nesta categoria.")],
        ),
        # Sem edição, os campos de agora também passam pela validação: o reprovado não entra no
        # catálogo sem a correção.
        (
            "dev-030",
            None,
            [
                (
                    "garantia_meses",
                    "garantia_meses: 999 meses está fora da faixa plausível (1 a 180 meses); "
                    + RANGE_HINT,
                )
            ],
        ),
    ],
)
def test_invalid_approval_is_422_with_field_and_message(client, conn, sheet, change, errors):
    sheet_id = ids(conn)[sheet]
    before = product(conn, sheet_id)
    body = {} if change is None else {"campos": before[1] | change}

    response = client.post(f"/api/sheets/{sheet_id}/approve", json=body)

    assert response.status_code == 422
    assert response.json() == {
        "detail": [{"campo": field, "mensagem": message} for field, message in errors]
    }
    assert product(conn, sheet_id) == before
    assert reviews(conn) == []


def test_malformed_request_is_422_in_the_same_format(client, conn):
    # Só com bug na tela. No lugar do campo, o caminho do erro, que não é campo do formulário.
    sheet_id = ids(conn)["dev-001"]
    for url, body, where in [
        (f"/api/sheets/{sheet_id}/approve", {"campos": "x"}, "body.campos"),
        (f"/api/sheets/{sheet_id}/approve", {"preco": 1}, "body.preco"),
        ("/api/sheets/abc/reject", {}, "path.ficha_id"),
    ]:
        response = client.post(url, json=body)
        assert response.status_code == 422
        [error] = response.json()["detail"]
        assert error["campo"] == where
        assert error["mensagem"].startswith(f"{where}: pedido fora do formato (")
    assert reviews(conn) == []


def test_failed_without_fields_is_approved_only_with_the_whole_product(client, conn):
    # Reprovado por um erro que não é de campo: no lote, o pedido falhou em todas as tentativas
    # e não há campos. O formulário sai da categoria, e só o produto inteiro aprova.
    sheet_id = ids(conn)["dev-031"]
    lote_error = [{"campo": "lote", "mensagem": "errored"}]
    with conn:
        db.save_product(conn, sheet_id, "fonte", {}, [], "reprovado", lote_error)
        db.set_route(conn, sheet_id, "revisao", ["reprovado"], False)
    names = list(PRODUCT_MODELS["fonte"].model_fields)
    assert {item["id"]: item["nome"] for item in client.get("/api/queue").json()}[sheet_id] is None
    sheet = client.get(f"/api/sheets/{sheet_id}").json()
    assert (sheet["campos"], sheet["erros"]) == ({}, lote_error)
    assert [field["campo"] for field in sheet["formulario"]] == names

    response = client.post(f"/api/sheets/{sheet_id}/approve", json={})
    assert response.status_code == 422
    assert [error["campo"] for error in response.json()["detail"]] == names

    fields = split_answer_key(SHEETS["dev-031"]["gabarito"])[1]
    assert client.post(f"/api/sheets/{sheet_id}/approve", json={"campos": fields}).is_success
    assert catalog(client)[sheet_id] == fields
    assert reviews(conn) == [(sheet_id, "aprovar", {}, fields)]


def test_reject(client, conn):
    sheet_id = ids(conn)["dev-031"]
    _, fields, _ = product(conn, sheet_id)

    response = client.post(f"/api/sheets/{sheet_id}/reject", json={})

    assert response.status_code == 200
    assert response.json() == {"id": sheet_id, "destino": "rejeitado"}
    assert sheet_id not in queue_ids(client)
    assert sheet_id not in catalog(client)
    assert product(conn, sheet_id) == ("rejeitado", fields, ["auditoria"])
    assert reviews(conn) == [(sheet_id, "rejeitar", fields, None)]


def test_decisions_only_from_the_queue_and_unknown_sheet(client, conn):
    [in_catalog] = catalog(client)
    # O reprovado rejeitado: aprovar de novo é 409, e não o 422 dos campos dele.
    sheet_id = ids(conn)["dev-030"]
    client.post(f"/api/sheets/{sheet_id}/reject", json={})

    for target in (in_catalog, sheet_id):
        for action in ("approve", "reject"):
            response = client.post(f"/api/sheets/{target}/{action}", json={})
            assert response.status_code == 409
            assert response.json() == {
                "detail": f"A ficha {target} não está na fila de revisão."
            }
    assert len(reviews(conn)) == 1

    assert client.get("/api/sheets/999").status_code == 404
    assert client.post("/api/sheets/999/approve", json={}).status_code == 404


def test_other_host_and_post_without_json_are_refused(db_file, conn):
    app = create_app(db_file)
    # Um site que se passa por 127.0.0.1 (DNS rebinding) manda o nome dele no Host.
    for host, status in [("evil.example", 400), ("localhost", 200), ("127.0.0.1", 200)]:
        response = TestClient(app, base_url=f"http://{host}:8000").get("/api/queue")
        assert response.status_code == status

    # Um formulário de outro site manda texto ou form, nunca JSON.
    sheet_id = ids(conn)["dev-031"]
    local = TestClient(app, base_url="http://127.0.0.1:8000")
    response = local.post(
        f"/api/sheets/{sheet_id}/reject", content="{}", headers={"content-type": "text/plain"}
    )
    assert response.status_code == 415
    assert product(conn, sheet_id)[0] == "revisao"
    assert reviews(conn) == []


def test_summary_matches_the_database(client, conn, tmp_path):
    empty = TestClient(create_app(tmp_path / "vazio.db"), base_url="http://127.0.0.1:8000")
    assert empty.get("/api/summary").json() == {
        "catalogo": 0,
        "fila": 0,
        "fila_por_motivo": {"reprovado": 0, "duvida_baixa": 0, "auditoria": 0},
        "rejeitados": 0,
        "custo": {"ultima_rodada": None, "total": 0.0},
    }

    client.post(f"/api/sheets/{ids(conn)['dev-001']}/approve", json={})
    client.post(f"/api/sheets/{ids(conn)['dev-031']}/reject", json={})
    summary = client.get("/api/summary").json()

    destinations = dict(conn.execute("SELECT destino, COUNT(*) FROM produtos GROUP BY destino"))
    last_round = conn.execute(
        "SELECT r.id, COUNT(DISTINCT t.ficha_id), COUNT(*), TOTAL(t.custo) "
        "FROM rodadas r JOIN tentativas t ON t.rodada_id = r.id "
        "WHERE r.id = (SELECT MAX(id) FROM rodadas)"
    ).fetchone()
    total = conn.execute("SELECT TOTAL(custo) FROM tentativas").fetchone()[0]
    assert summary["custo"]["ultima_rodada"].pop("criado_em")
    assert summary == {
        "catalogo": destinations["catalogo"],
        "fila": destinations["revisao"],
        # Só a fila conta: dev-030 reprovado e as auditorias do processador e do SSD. O aprovado
        # (dúvida baixa) e o rejeitado (auditoria) saíram dela.
        "fila_por_motivo": {"reprovado": 1, "duvida_baixa": 0, "auditoria": 2},
        "rejeitados": destinations["rejeitado"],
        "custo": {
            "ultima_rodada": {
                "id": last_round[0],
                "modo": "batch",
                "fichas": last_round[1],
                "chamadas": last_round[2],
                "custo": pytest.approx(last_round[3]),
            },
            "total": pytest.approx(total),
        },
    }
