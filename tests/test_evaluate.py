import pytest

import cli
from pipeline import batch, db, evaluate, extract, route
from pipeline.evaluate import Item
from pipeline.fake_client import WRONG_WARRANTY, FakeAPI
from pipeline.sheets import SETS, load_sheets, sheets_path

SHEETS = {sheet["id"]: sheet for sheet in load_sheets(sheets_path("dev"))}


def doubt(field, level):
    return {"campo": field, "nivel": level, "motivo": "dois valores no texto"}


# Quatro fichas pequenas, com gabarito e produto inventados. Os campos de cada gabarito são poucos
# para a conta ser conferida de cabeça (a avaliação compara os campos que o gabarito tem).
HAND_ITEMS = [
    # TDP errado, com dúvida 'baixa': foi para a fila. Marca em caixa alta e sem acento conta
    # certo; 100 = 100.0. → 3/4.
    Item(
        "t-1",
        "tabela",
        {"categoria": "processador", "categoria_detalhe": None, "nome": "Processador A",
         "marca": "Graúna", "preco": 100.0, "tdp_w": 65, "video_integrado": True},
        "processador",
        {"nome": "Outro nome", "marca": "GRAUNA", "preco": 100, "tdp_w": 148,
         "video_integrado": True},
        [doubt("tdp_w", "baixa")],
        ["duvida_baixa"],
    ),
    # Preço null no lugar de 1499, com dúvida 'media': foi direto para o catálogo (escapou). A cor
    # com caixa e espaço conta certo. O detalhe e a conexão ficam fora do acerto. → 2/3.
    Item(
        "t-2",
        "ingles",
        {"categoria": "outro", "categoria_detalhe": "monitor", "nome": "Monitor B",
         "marca": "Irapuã", "preco": 1499.0, "cor": "preto", "conexao": "HDMI"},
        "outro",
        {"nome": "Monitor B", "marca": "Irapuã", "preco": None, "cor": " Preto ",
         "categoria_detalhe": "monitor gamer", "conexao": "hdmi"},
        [doubt("preco", "media")],
        [],
    ),
    # Classificada como 'outro' em vez de 'fonte': a extração não tem `bivolt`, que conta como
    # erro. Caiu na auditoria. → 2/3.
    Item(
        "t-3",
        "tabela",
        {"categoria": "fonte", "categoria_detalhe": None, "nome": "Fonte C", "marca": "Rivacor",
         "preco": 149.9, "bivolt": False},
        "outro",
        {"nome": "Fonte C", "marca": "Rivacor", "preco": 149.9,
         "categoria_detalhe": "fonte", "cor": None, "conexao": None},
        [],
        ["auditoria"],
    ),
    # Tudo certo, mas reprovada: alarme falso. → 3/3.
    Item(
        "t-4",
        "ingles",
        {"categoria": "memoria_ram", "categoria_detalhe": None, "nome": "Memória D",
         "marca": "Siriema", "preco": 300.0, "capacidade_gb": 32},
        "memoria_ram",
        {"nome": "Memória D", "marca": "Siriema", "preco": 300.0, "capacidade_gb": 32},
        [doubt("marca", "baixa"), doubt("marca", "media")],
        ["reprovado"],
    ),
]


def counts(score):
    return (score.right, score.total)


def test_score_of_a_hand_built_case():
    report = evaluate.score(HAND_ITEMS)

    assert counts(report.total.fields) == (10, 13)
    assert counts(report.total.category) == (3, 4)
    assert counts(report.total.clean) == (1, 4)  # só a t-4 não tem erro
    assert {name: counts(group.fields) for name, group in report.by_category.items()} == {
        "processador": (3, 4),
        "outro": (2, 3),
        "fonte": (2, 3),
        "memoria_ram": (3, 3),
    }
    assert {name: counts(group.fields) for name, group in report.by_style.items()} == {
        "tabela": (5, 7),
        "ingles": (5, 6),
    }
    assert {name: counts(score) for name, score in report.by_field.items()} == {
        "marca": (4, 4),
        "preco": (3, 4),
        "tdp_w": (0, 1),
        "video_integrado": (1, 1),
        "cor": (1, 1),
        "bivolt": (0, 1),
        "capacidade_gb": (1, 1),
    }
    # Calibração: na t-4, a 'baixa' vale sobre a 'media' do mesmo campo.
    assert {level: counts(score) for level, score in report.by_doubt.items()} == {
        "sem dúvida": (9, 10),
        "baixa": (1, 2),
        "media": (0, 1),
    }
    # Fila: t-1 pega pela fila, t-2 escapou, t-3 pega pela auditoria, t-4 alarme falso.
    assert report.queue == {
        (True, "fila"): 1,
        (True, "catalogo"): 1,
        (True, "auditoria"): 1,
        (False, "fila"): 1,
    }
    assert [(e.sheet_id, e.field, e.expected, e.doubt, e.route) for e in report.errors] == [
        ("t-1", "tdp_w", 65, "baixa", "fila"),
        ("t-2", "preco", 1499.0, "media", "catalogo"),
        ("t-3", "categoria", "fonte", "sem dúvida", "auditoria"),
        ("t-3", "bivolt", False, "sem dúvida", "auditoria"),
    ]
    assert report.errors[-1].got is evaluate.MISSING
    # As descrições livres ficam à parte.
    assert {name: counts(score) for name, score in report.descriptive.items()} == {
        "categoria_detalhe": (0, 1),
        "conexao": (1, 1),
    }
    assert [(e.sheet_id, e.field, e.got) for e in report.descriptive_diffs] == [
        ("t-2", "categoria_detalhe", "monitor gamer")
    ]

    text = evaluate.markdown("holdout", [report])
    assert "| com erro | 1 | 1 | 1 |" in text
    assert "| sem erro | 1 | 0 | 0 |" in text
    assert "- Escaparam para o catálogo: 1: t-2 (preco)." in text
    assert "| t-3 | `bivolt` | false | (ausente) | — | auditoria |" in text


def processed_dev(tmp_path):
    """O dev inteiro processado e roteado com a API falsa: um erro que fica (garantia 999 na
    dev-030, reprovada), um erro corrigido na 2ª tentativa (dev-031: vale a última resposta), uma
    dúvida 'media' num campo certo e uma 'baixa' num campo importante certo (alarme falso)."""
    db_file = tmp_path / "catalog.db"
    conn = db.connect(db_file)
    db.add_sheets(conn, "dev", [(sheet_id, sheet["texto"]) for sheet_id, sheet in SHEETS.items()])
    api = FakeAPI(
        wrong_always={"dev-030"},
        wrong_first={"dev-031"},
        doubts={"dev-001": {"tdp_w": "media"}, "dev-002": {"nucleos": "baixa"}},
    )
    extract.process(api.client, conn)
    route.route(conn)
    return db_file, conn


def test_eval_with_the_fake_api(tmp_path, monkeypatch, capsys):
    db_file, conn = processed_dev(tmp_path)
    report = evaluate.evaluate(conn, "dev")

    # As 3 fichas de exemplo do prompt ficam de fora.
    assert counts(report.total.clean) == (49, 50)
    assert report.missing == report.unrouted == 0
    fields = report.total.fields
    assert fields.right == fields.total - 1
    assert [(e.sheet_id, e.field, e.expected, e.got, e.route) for e in report.errors] == [
        ("dev-030", "garantia_meses", SHEETS["dev-030"]["gabarito"]["garantia_meses"],
         WRONG_WARRANTY, "fila")
    ]
    assert counts(report.total.category) == (50, 50)
    assert counts(report.by_doubt["media"]) == (1, 1)
    assert counts(report.by_doubt["baixa"]) == (1, 1)
    rows = conn.execute(
        "SELECT f.id_origem FROM produtos p JOIN fichas f ON f.id = p.ficha_id "
        "WHERE p.motivos = '[\"auditoria\"]'"
    )
    audited = len({row[0] for row in rows} - extract.EXAMPLE_SHEETS)
    assert report.queue == {
        (True, "fila"): 1,
        (False, "fila"): 1,
        (False, "auditoria"): audited,
        (False, "catalogo"): 48 - audited,
    }

    # O custo vem de todas as chamadas, com as fichas de exemplo: 53 × 2 e mais 3 correções.
    [cost] = report.costs
    total = conn.execute("SELECT TOTAL(custo) FROM tentativas").fetchone()[0]
    assert (cost["modo"], cost["fichas"], cost["chamadas"], cost["custo"]) == (
        "direct", 53, 109, total
    )

    # A avaliação mede o modelo: a edição humana do produto não muda nada.
    with conn:
        conn.execute("UPDATE produtos SET campos = '{}'")
    assert evaluate.evaluate(conn, "dev") == report

    # Pelo terminal, gravando o arquivo: os números são refeitos, e o trecho escrito à mão no fim
    # do arquivo fica.
    results = {name: tmp_path / f"{name}.md" for name in SETS}
    notes = f"{evaluate.NOTES_MARK}\n\n## Observações\n\nEscritas à mão.\n"
    results["dev"].write_text(f"números antigos\n\n{notes}", encoding="utf-8")
    monkeypatch.setattr(evaluate, "RESULTS_FILES", results)
    cli.main(["--db", str(db_file), "eval", "--conjunto", "dev", "--salvar"])
    out = capsys.readouterr().out
    text = results["dev"].read_text(encoding="utf-8")
    assert "números antigos" not in text
    assert text.removesuffix("\n" + notes) in out
    assert text.endswith("\n" + notes)
    assert "| direto | 50 | 491/492 (99,8%) | 50/50 (100,0%) | 49/50 (98,0%) |" in text
    assert f"| direto | 53 | 109 | ${total:.6f} | ${total / 53:.6f} |" in text
    assert "dev-018, dev-036, dev-047" in text


def test_compare_with_another_database(tmp_path, capsys):
    db_file, conn = processed_dev(tmp_path)
    other = db.connect(tmp_path / "lote.db")
    db.add_sheets(other, "dev", [(sheet_id, sheet["texto"]) for sheet_id, sheet in SHEETS.items()])
    batch.process(FakeAPI().client, other, poll_seconds=0)
    route.route(other)
    capsys.readouterr()

    cli.main(["--db", str(db_file), "eval", "--conjunto", "dev", "--comparar",
              str(tmp_path / "lote.db")])
    out = capsys.readouterr().out
    assert "| lote | 50 | 492/492 (100,0%) | 50/50 (100,0%) | 50/50 (100,0%) |" in out
    assert "| lote | 53 | 106 |" in out


def test_example_sheets_are_the_ones_in_the_prompts():
    # Uma ficha com gabarito cujo texto está num prompt é exemplo, e não pode contar na avaliação.
    in_prompts = {
        sheet["id"]
        for name in SETS
        for sheet in load_sheets(sheets_path(name))
        if any(sheet["texto"].strip() in prompt for prompt in extract.PROMPTS.values())
    }
    assert in_prompts == extract.EXAMPLE_SHEETS


def test_eval_without_products(tmp_path):
    db_file = tmp_path / "catalog.db"
    db.connect(db_file).close()
    with pytest.raises(SystemExit, match="nenhum produto do conjunto holdout"):
        cli.main(["--db", str(db_file), "eval", "--conjunto", "holdout"])
