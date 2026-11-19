import hashlib
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import cli
from api.main import create_app
from pipeline import db, extract
from pipeline.fake_client import WRONG_WARRANTY, FakeAPI
from pipeline.sheets import UNKNOWN_STYLE, load_sheets, sheets_path, split_answer_key
from tests import test_data_files as data_tests

SHEETS = {
    sheet["id"]: sheet for name in ("dev", "holdout") for sheet in load_sheets(sheets_path(name))
}
INBOX_TEXT = (
    "Mousepad Gamer Pampa Speed XL, 900 x 400 x 4 mm, base de borracha e bordas costuradas. "
    "Cor preta. Garantia de 6 meses. R$ 89,90 à vista."
)
# A correção do revisor para o mousepad, que a API falsa não conhece ("produto não identificado").
MOUSEPAD = {
    "nome": "Mousepad Gamer Pampa Speed XL",
    "marca": "Pampa",
    "preco": 89.9,
    "garantia_meses": 6,
    "categoria_detalhe": "mousepad",
    "cor": "preto",
    "conexao": None,
}


@pytest.fixture(scope="module")
def filled_db(tmp_path_factory):
    """As fichas extraídas pela API falsa e todas na fila de revisão. dev-030 e hold-002 vêm com a
    garantia errada até o fim; dev-029 só tem preço em US$; 'copia-prova.txt' é o texto da hold-003
    que entrou pela inbox."""
    path = tmp_path_factory.mktemp("corrections") / "catalog.db"
    conn = db.connect(path)
    for conjunto, sheet_ids in [
        ("dev", ["dev-001", "dev-029", "dev-030", "dev-031"]),
        ("holdout", ["hold-002"]),
    ]:
        db.add_sheets(conn, conjunto, [(i, SHEETS[i]["texto"]) for i in sheet_ids])
    db.add_sheets(
        conn,
        "inbox",
        [("mousepad.txt", INBOX_TEXT), ("copia-prova.txt", SHEETS["hold-003"]["texto"])],
    )
    extract.process(FakeAPI(wrong_always={"dev-030", "hold-002"}).client, conn)
    with conn:
        for (sheet_id,) in conn.execute("SELECT ficha_id FROM produtos").fetchall():
            db.set_route(conn, sheet_id, "revisao", ["auditoria"], True)
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


def ids(conn):
    """{id_origem: id da ficha}."""
    return dict(conn.execute("SELECT id_origem, id FROM fichas").fetchall())


@pytest.fixture
def review(db_file, conn):
    """Decide pela API da revisão, como a tela: `review(id_origem, 'approve', campos=None)`."""
    client = TestClient(create_app(db_file), base_url="http://127.0.0.1:8000")

    def decide(origin, action, fields=None):
        body = {} if fields is None else {"campos": fields}
        response = client.post(f"/api/sheets/{ids(conn)[origin]}/{action}", json=body)
        assert response.is_success, response.json()

    return decide


def back_to_queue(conn, origin, fields):
    """Uma nova extração (com estes campos) volta a ficha para a fila, como num reprocessamento."""
    sheet_id = ids(conn)[origin]
    with conn:
        db.save_product(
            conn, sheet_id, SHEETS[origin]["gabarito"]["categoria"], fields, [], "valido", []
        )
        db.set_route(conn, sheet_id, "revisao", ["auditoria"], False)


def fields_of(sheet_id):
    return split_answer_key(SHEETS[sheet_id]["gabarito"])[1]


def export(db_file, out, capsys):
    cli.main(["--db", str(db_file), "export-corrections", "--saida", str(out)])
    return capsys.readouterr().out.splitlines()


def test_the_last_edited_approval_of_each_sheet_is_exported(
    db_file, conn, review, tmp_path, capsys
):
    psu = fields_of("dev-030")
    # Vale a última decisão. A dev-030 foi editada errado (24), voltou com uma nova extração e foi
    # corrigida de novo. A dev-001 e a dev-031 foram editadas e voltaram, e a última decisão foi
    # aprovar sem editar e rejeitar: a edição de antes não conta mais.
    review("dev-030", "approve", psu | {"garantia_meses": 24})
    back_to_queue(conn, "dev-030", psu | {"garantia_meses": WRONG_WARRANTY})
    review("dev-030", "approve", psu)
    for origin, action in [("dev-001", "approve"), ("dev-031", "reject")]:
        review(origin, "approve", fields_of(origin) | {"nome": "Nome editado"})
        back_to_queue(conn, origin, fields_of(origin))
        review(origin, action)
    review("mousepad.txt", "approve", MOUSEPAD)
    out = tmp_path / "corrections.jsonl"

    assert export(db_file, out, capsys) == [f"2 correções exportadas para {out}"]

    new_case = {
        "id": f"corr-{hashlib.sha256(INBOX_TEXT.encode()).hexdigest()[:8]}",
        "texto": INBOX_TEXT,
        "estilo": UNKNOWN_STYLE,
        "pegadinha": (
            'Corrigido na revisão humana: nome: "Produto não identificado" → '
            '"Mousepad Gamer Pampa Speed XL"; marca: null → "Pampa"; preco: null → 89.9; '
            'garantia_meses: null → 6; categoria_detalhe: "produto não identificado" → '
            '"mousepad"; cor: null → "preto".'
        ),
        "gabarito": {"categoria": "outro", **MOUSEPAD},
    }
    # A ficha que o dev já tem sai com o id e o estilo de lá, e o gabarito corrigido é o de lá.
    psu_line = SHEETS["dev-030"] | {
        "pegadinha": "Corrigido na revisão humana: garantia_meses: 999 → 36."
    }
    assert load_sheets(out) == [psu_line, new_case]

    # O caso novo, colado no fim do data/dev/fichas.jsonl sem retoque, passa nos testes de dados das
    # fichas: campos, ids e textos sem repetir, esquema e regras com o texto, estilo e marcas.
    pasted = tmp_path / "fichas.jsonl"
    dev_lines = sheets_path("dev").read_text(encoding="utf-8")
    new_line = out.read_text(encoding="utf-8").splitlines()[1]
    pasted.write_text(dev_lines + new_line + "\n", encoding="utf-8")
    sheets = load_sheets(pasted)
    assert sheets == load_sheets(sheets_path("dev")) + [new_case]
    data_tests.test_sheets_have_the_expected_fields(sheets)
    data_tests.test_ids_are_unique(sheets)
    data_tests.test_texts_are_unique(sheets)
    data_tests.test_answer_keys_pass_schema_and_rules(sheets)
    data_tests.test_every_style_has_enough_sheets(sheets, "dev")
    data_tests.test_no_forbidden_brand(sheets)


def test_held_out_and_dollar_price_corrections_are_left_out(db_file, review, tmp_path, capsys):
    review("hold-002", "approve", fields_of("hold-002"))
    # O texto de uma ficha da prova que entrou pela inbox também é prova.
    review("copia-prova.txt", "approve", fields_of("hold-003") | {"nome": "Nome editado"})
    # A ficha só tem preço em US$: o revisor pode aprovar o preço em reais, mas no gabarito ele é
    # null (a regra do US$, que roda com o texto).
    review("dev-029", "approve", fields_of("dev-029") | {"preco": 289.9})
    out = tmp_path / "corrections.jsonl"

    assert export(db_file, out, capsys) == [
        "dev-029: pulada; como gabarito, a correção não passa nas regras com o texto da ficha: "
        "preco: a ficha só tem preço em dólar, sem R$; o preço em reais fica null, sem converter.",
        "2 correções de fichas da prova (holdout) ficaram de fora: a prova não vira caso de "
        "desenvolvimento",
        f"0 correções exportadas para {out}",
    ]
    assert out.read_text(encoding="utf-8") == ""
