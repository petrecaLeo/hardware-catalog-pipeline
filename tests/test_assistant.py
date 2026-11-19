import json
import re

import pytest

from pipeline import assistant, db
from pipeline.config import ASSISTANT_MAX_CALLS, ASSISTANT_MAX_TOKENS, MODEL
from pipeline.fake_client import FakeAPI
from pipeline.sheets import load_sheets, sheets_path, split_answer_key

SHEETS = {sheet["id"]: sheet for sheet in load_sheets(sheets_path("dev"))}
# Um catálogo pequeno, com os produtos dos gabaritos. Placas de vídeo: RTX 4060 8 GB R$ 1.899,00,
# Arc B580 12 GB R$ 1.949,00 e RTX 5070 12 GB R$ 4.189,00. Fontes: 650 W não modular R$ 379,90,
# 850 W modular R$ 899,90 e 600 W semi sem preço. SSDs: NVMe 7000 MB/s e SATA 540 MB/s.
IN_CATALOG = [
    "dev-018",
    "dev-021",
    "dev-022",
    "dev-030",
    "dev-031",
    "dev-035",
    "dev-024",
    "dev-026",
]
# A RTX 5090 (32 GB) está na fila de revisão: o assistente não a vê.
IN_REVIEW = "dev-019"


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    """O banco com o catálogo acima e {id_origem: id}. O assistente só lê: um banco para todos."""
    conn = db.connect(tmp_path_factory.mktemp("assistant") / "catalog.db")
    names = [*IN_CATALOG, IN_REVIEW]
    db.add_sheets(conn, "dev", [(name, SHEETS[name]["texto"]) for name in names])
    ids = dict(conn.execute("SELECT id_origem, id FROM fichas").fetchall())
    with conn:
        for name in names:
            category, fields = split_answer_key(SHEETS[name]["gabarito"])
            db.save_product(conn, ids[name], category["categoria"], fields, [], "valido", [])
            destino = "revisao" if name == IN_REVIEW else "catalogo"
            db.set_route(conn, ids[name], destino, [], False)
    yield conn, ids
    conn.close()


def search(categoria=None, filtros=(), texto=None, preco_max=None):
    """A entrada de `buscar_produtos`, como o modelo manda (todo campo presente)."""
    filters = [{"campo": c, "operador": o, "valor": v} for c, o, v in filtros]
    return {"texto": texto, "categoria": categoria, "preco_max": preco_max, "filtros": filters}


def found(conn, ids, data):
    content, is_error = assistant.run_tool(conn, "buscar_produtos", data)
    assert not is_error, content
    result = json.loads(content)
    names = {value: key for key, value in ids.items()}
    return result["encontrados"], [names[product["id"]] for product in result["produtos"]]


@pytest.mark.parametrize(
    "data, expected",
    [
        # Do mais barato ao mais caro; o produto da fila não aparece.
        (search("placa_de_video"), ["dev-021", "dev-022", "dev-018"]),
        (search("placa_de_video", preco_max=2000), ["dev-021", "dev-022"]),
        (
            search(
                "armazenamento", [("tipo", "igual", "ssd_nvme"), ("leitura_mb_s", "minimo", 5000)]
            ),
            ["dev-024"],
        ),
        # O sem preço fica no fim.
        (search("fonte", [("potencia_w", "maximo", 650)]), ["dev-030", "dev-035"]),
        # 'acima' e 'abaixo' não incluem o valor dado ("acima de 540 MB/s").
        (search("armazenamento", [("leitura_mb_s", "acima", 540)]), ["dev-024"]),
        (search("fonte", [("potencia_w", "abaixo", 650)]), ["dev-035"]),
        # Sim/não: o produto sem o dado (null) não passa como false.
        (search("fonte", [("conector_12v_2x6", "igual", False)]), ["dev-030"]),
        # Texto: cada palavra no nome ou na marca, sem diferença de caixa; o campo de texto também.
        (search(texto="rtx ARPOADOR"), ["dev-021", "dev-018"]),
        (search("placa_de_video", [("chip", "igual", "rtx 5070")]), ["dev-018"]),
        # Os campos comuns valem sem categoria.
        (search(filtros=[("preco", "maximo", 400)]), ["dev-026", "dev-030"]),
    ],
)
def test_search_filters_the_approved_catalog(catalog, data, expected):
    conn, ids = catalog
    assert found(conn, ids, data) == (len(expected), expected)


def test_search_result_has_the_price_formatted_and_the_filtered_fields(catalog, monkeypatch):
    conn, ids = catalog
    monkeypatch.setattr(assistant, "SEARCH_LIMIT", 1)
    content, _ = assistant.run_tool(
        conn, "buscar_produtos", search("placa_de_video", [("vram_gb", "igual", 12)])
    )
    # `encontrados` conta todos; a lista para no limite.
    assert json.loads(content) == {
        "encontrados": 2,
        "produtos": [
            {
                "id": ids["dev-022"],
                "categoria": "placa_de_video",
                "nome": SHEETS["dev-022"]["gabarito"]["nome"],
                "preco": "R$ 1.949,00",
                "vram_gb": 12,
            }
        ],
    }


def test_price_is_written_like_the_store():
    assert assistant.brl(18990.5) == "R$ 18.990,50"
    assert assistant.brl(None) == "sem preço"


@pytest.mark.parametrize(
    "name, data, message",
    [
        # O nome do campo é conferido contra os campos da categoria antes de chegar ao SQL.
        (
            "buscar_produtos",
            search("placa_de_video", [("vram_gb') OR 1=1 --", "igual", 12)]),
            "o campo 'vram_gb') OR 1=1 --' não existe em 'placa_de_video'. Campos: nome, marca,",
        ),
        (
            "buscar_produtos",
            search(filtros=[("vram_gb", "minimo", 12)]),
            "o campo 'vram_gb' não existe em todas as categorias (escolha a categoria).",
        ),
        (
            "buscar_produtos",
            search("placa_de_video", [("chip", "minimo", "RTX")]),
            "'minimo' só vale em campo numérico; 'chip' não é.",
        ),
        (
            "buscar_produtos",
            search("placa_de_video", [("vram_gb", "igual", "12 GB")]),
            "'vram_gb' é numérico: o valor tem que ser um número.",
        ),
        (
            "buscar_produtos",
            search("fonte", [("modularidade", "igual", "full")]),
            "'modularidade' tem que ser um destes valores: nao_modular, semi_modular, modular.",
        ),
        ("buscar_produtos", {"texto": "rtx"}, "entrada fora do formato: categoria: Field required"),
        ("comprar", {"id": 1}, "a ferramenta 'comprar' não existe."),
    ],
)
def test_invalid_input_goes_back_to_the_model_as_an_error(catalog, name, data, message):
    content, is_error = assistant.run_tool(catalog[0], name, data)
    assert is_error
    assert content.startswith(message)


def test_product_outside_the_catalog_is_invisible(catalog):
    conn, ids = catalog
    content, is_error = assistant.run_tool(conn, "ver_produto", {"id": ids[IN_REVIEW]})
    assert (content, is_error) == (f"o produto {ids[IN_REVIEW]} não está no catálogo.", True)
    assert IN_REVIEW not in found(conn, ids, search(texto="RTX 5090"))[1]

    content, is_error = assistant.run_tool(conn, "ver_produto", {"id": ids["dev-022"]})
    fields = split_answer_key(SHEETS["dev-022"]["gabarito"])[1]
    expected = {"id": ids["dev-022"], "categoria": "placa_de_video", **fields}
    assert (json.loads(content), is_error) == (expected | {"preco": "R$ 1.949,00"}, False)


def call(name, **data):
    return [(name, data)]


def test_loop_runs_the_tools_until_the_answer_and_adds_up_the_cost(catalog, capsys):
    conn, ids = catalog
    final = "A mais barata com 12 GB é a Noxel Intel Arc B580 Garoa, por R$ 1.949,00."
    api = FakeAPI(
        chat=[
            # O campo errado volta como erro, e o modelo tenta de novo.
            call("buscar_produtos", **search("placa_de_video", [("vram", "igual", 12)])),
            call("buscar_produtos", **search("placa_de_video", [("vram_gb", "igual", 12)])),
            call("ver_produto", id=ids["dev-022"]),
            final,
        ]
    )
    history = [("Vocês vendem placa de vídeo?", "Sim, temos placas de vídeo no catálogo.")]

    question = "Qual a placa de vídeo mais barata com 12 GB?"
    reply = assistant.answer(api.client, conn, question, history)

    assert (reply.text, reply.calls) == (final, 4)
    requests = api.requests
    assert len(requests) == 4
    for params in requests:
        assert params["model"] == MODEL
        assert params["max_tokens"] == ASSISTANT_MAX_TOKENS
        assert params["thinking"] == {"type": "disabled"}
        assert "tool_choice" not in params  # o modelo escolhe a ferramenta
        assert [tool["name"] for tool in params["tools"]] == ["buscar_produtos", "ver_produto"]
        assert params["system"][-1]["cache_control"] == {"type": "ephemeral"}
    # O histórico (só texto) vem antes da pergunta.
    assert requests[0]["messages"] == [
        {"role": "user", "content": "Vocês vendem placa de vídeo?"},
        {"role": "assistant", "content": "Sim, temos placas de vídeo no catálogo."},
        {"role": "user", "content": question},
    ]
    # Cada chamada leva a conversa anterior e os resultados das ferramentas.
    results = [params["messages"][-1]["content"][0] for params in requests[1:]]
    assert [result["is_error"] for result in results] == [True, False, False]
    assert results[0]["content"].startswith("o campo 'vram' não existe em 'placa_de_video'.")
    assert '"preco": "R$ 1.949,00"' in results[1]["content"]
    assert json.loads(results[2]["content"])["nome"] == SHEETS["dev-022"]["gabarito"]["nome"]
    assert len(requests[3]["messages"]) == len(requests[0]["messages"]) + 6

    # Cada chamada imprime o custo, e a resposta traz a soma.
    out = capsys.readouterr().out
    costs = [float(cost) for cost in re.findall(r"chamada \d: tokens: .* \| cost: \$([\d.]+)", out)]
    assert len(costs) == 4 and all(cost > 0 for cost in costs)
    assert reply.cost == pytest.approx(sum(costs), abs=1e-5)
    assert f"resposta: 4 chamadas | custo total: ${reply.cost:.6f}" in out


def test_loop_stops_at_the_call_limit(catalog):
    conn, _ = catalog
    api = FakeAPI(chat=[call("buscar_produtos", **search("fonte"))] * (ASSISTANT_MAX_CALLS + 1))

    reply = assistant.answer(api.client, conn, "Tem fonte?")

    assert (reply.text, reply.calls) == (assistant.TOO_MANY_CALLS, ASSISTANT_MAX_CALLS)
    assert len(api.requests) == ASSISTANT_MAX_CALLS
    assert reply.cost > 0
