import json

import anthropic
import pytest
from anthropic.types import Message, ToolUseBlock
from anthropic.types.messages import MessageBatch

from pipeline.config import EXTRACT_MAX_TOKENS, MODEL
from pipeline.fake_client import DOUBT_REASON, UNKNOWN, WRONG_WARRANTY, FakeAPI
from pipeline.rules import validate_classification, validate_product
from pipeline.sheets import SETS, load_sheets, sheets_path, split_answer_key
from pipeline.tools import CLASSIFY_TOOL, EXTRACT_TOOLS

SHEETS = {sheet["id"]: sheet for name in SETS for sheet in load_sheets(sheets_path(name))}
UNKNOWN_TEXT = "Mousepad gamer 90x40 cm, borda costurada, base emborrachada. R$ 59,90"


def ask(tool, text, *, history=(), max_tokens=EXTRACT_MAX_TOKENS):
    """Os parâmetros de um pedido como o pipeline manda: ferramenta forçada, thinking disabled."""
    return {
        "model": MODEL,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
        "tools": [tool],
        "tool_choice": {"type": "tool", "name": tool["name"]},
        "messages": [{"role": "user", "content": f"<ficha>\n{text}\n</ficha>"}, *history],
    }


def extract(sheet_id, **options):
    sheet = SHEETS[sheet_id]
    return ask(EXTRACT_TOOLS[sheet["gabarito"]["categoria"]], sheet["texto"], **options)


def tool_use(message):
    return next(block for block in message.content if block.type == "tool_use")


def correction(message, error):
    """A conversa de correção: a resposta errada e o erro num tool_result com is_error."""
    block = tool_use(message)
    wrong = {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    result = {"type": "tool_result", "tool_use_id": block.id, "is_error": True, "content": error}
    return [{"role": "assistant", "content": [wrong]}, {"role": "user", "content": [result]}]


def product(sheet_id):
    return split_answer_key(SHEETS[sheet_id]["gabarito"])[1]


def test_every_sheet_gets_its_answer_key():
    # Todas as fichas dos dois conjuntos: também garante que nenhuma é confundida com outra.
    client = FakeAPI().client
    for sheet in SHEETS.values():
        classification, fields = split_answer_key(sheet["gabarito"])
        answer = client.messages.create(**ask(CLASSIFY_TOOL, sheet["texto"]))
        assert tool_use(answer).input == classification, sheet["id"]
        answer = client.messages.create(**extract(sheet["id"]))
        assert tool_use(answer).input == fields | {"duvidas": []}, sheet["id"]


def test_answer_is_an_sdk_message_with_plausible_usage():
    client = FakeAPI().client
    text = SHEETS["dev-001"]["texto"]
    params = extract("dev-001")
    message = client.messages.create(**params)

    assert isinstance(message, Message)
    assert message.stop_reason == "tool_use"
    [block] = message.content
    assert isinstance(block, ToolUseBlock)
    assert block.name == "extrair_processador"
    assert validate_product("processador", block.input, text, doubts=True) == []

    # O mesmo pedido gasta os mesmos tokens. A extração (molde maior) gasta mais que a classificação.
    assert client.messages.create(**params).usage == message.usage
    classification = client.messages.create(**ask(CLASSIFY_TOOL, text))
    assert 500 < classification.usage.input_tokens < message.usage.input_tokens < 3000
    assert 0 < classification.usage.output_tokens < message.usage.output_tokens < EXTRACT_MAX_TOKENS
    assert message.usage.cache_read_input_tokens == message.usage.cache_creation_input_tokens == 0


def example(sheet_id):
    """Um exemplo few-shot como o prompt pode trazer: a ficha do dev e a resposta certa."""
    sheet = SHEETS[sheet_id]
    answer = json.dumps(sheet["gabarito"], ensure_ascii=False)
    return f"<exemplo>\n<ficha>\n{sheet['texto']}\n</ficha>\n{answer}\n</exemplo>"


@pytest.mark.parametrize("sheet_id", ["dev-030", None])
def test_examples_are_never_taken_for_the_sheet(sheet_id):
    # Fichas do dev como exemplos, no system e na mensagem, antes e depois da ficha pedida. Sem
    # gabarito (None), a resposta é a neutra, não a de um exemplo.
    text = SHEETS[sheet_id]["texto"] if sheet_id else UNKNOWN_TEXT
    params = ask(CLASSIFY_TOOL, text)
    params["system"] = f"Classifique o produto da ficha.\n{example('dev-018')}"
    message = params["messages"][0]
    message["content"] = f"{example('dev-002')}\n{message['content']}\n{example('dev-048')}"

    answer = tool_use(FakeAPI().client.messages.create(**params)).input
    if sheet_id:
        assert answer == split_answer_key(SHEETS[sheet_id]["gabarito"])[0]
    else:
        assert answer == {"categoria": "outro", "categoria_detalhe": UNKNOWN}


@pytest.mark.parametrize("fault, fixed", [("wrong_first", True), ("wrong_always", False)])
def test_wrong_value_and_correction(fault, fixed):
    sheet_id, category = "dev-030", SHEETS["dev-030"]["gabarito"]["categoria"]
    client = FakeAPI(**{fault: {sheet_id}}).client
    params = extract(sheet_id)
    first = client.messages.create(**params)

    assert tool_use(first).input["garantia_meses"] == WRONG_WARRANTY
    text = SHEETS[sheet_id]["texto"]
    issues = validate_product(category, tool_use(first).input, text, doubts=True)
    assert [issue.field for issue in issues] == ["garantia_meses"]  # as regras pegam

    params["messages"] += correction(first, issues[0].message)
    second = tool_use(client.messages.create(**params)).input
    if fixed:
        assert second == product(sheet_id) | {"duvidas": []}
    else:
        assert second["garantia_meses"] == WRONG_WARRANTY


def test_cut_until_max_tokens_is_raised():
    client = FakeAPI(cut={"dev-001"}).client

    cut = client.messages.create(**extract("dev-001"))
    assert cut.stop_reason == "max_tokens"
    assert cut.usage.output_tokens == EXTRACT_MAX_TOKENS
    assert "duvidas" not in tool_use(cut).input

    whole = client.messages.create(**extract("dev-001", max_tokens=EXTRACT_MAX_TOKENS + 200))
    assert whole.stop_reason == "tool_use"
    assert tool_use(whole).input == product("dev-001") | {"duvidas": []}
    # A classificação da mesma ficha não é cortada.
    classification = client.messages.create(**ask(CLASSIFY_TOOL, SHEETS["dev-001"]["texto"]))
    assert classification.stop_reason == "tool_use"


def test_injected_doubt():
    client = FakeAPI(doubts={"dev-001": ("tdp_w", "baixa")}).client
    data = tool_use(client.messages.create(**extract("dev-001"))).input
    assert data["duvidas"] == [{"campo": "tdp_w", "nivel": "baixa", "motivo": DOUBT_REASON}]
    assert validate_product("processador", data, SHEETS["dev-001"]["texto"], doubts=True) == []


def test_unknown_sheet_gets_a_neutral_valid_answer():
    text = UNKNOWN_TEXT
    client = FakeAPI().client

    classification = tool_use(client.messages.create(**ask(CLASSIFY_TOOL, text))).input
    assert classification == {"categoria": "outro", "categoria_detalhe": UNKNOWN}
    assert validate_classification(classification) == []
    # Também numa categoria qualquer, se o pipeline pedir.
    for category in ("outro", "fonte"):
        data = tool_use(client.messages.create(**ask(EXTRACT_TOOLS[category], text))).input
        assert validate_product(category, data, text, doubts=True) == []


def test_batch_errors_order_and_resend():
    fake = FakeAPI(errored={"dev-002"}, expired={"dev-003"}, out_of_order=True, in_progress_polls=1)
    batches = fake.client.messages.batches
    requests = [
        {"custom_id": f"f00{n}-extract-1", "params": extract(f"dev-00{n}")} for n in (1, 2, 3, 4)
    ]

    batch = batches.create(requests=requests)
    assert isinstance(batch, MessageBatch)
    assert batch.processing_status == "in_progress"
    assert batches.retrieve(batch.id).processing_status == "in_progress"
    ended = batches.retrieve(batch.id)
    assert ended.processing_status == "ended"
    counts = ended.request_counts
    assert (counts.processing, counts.succeeded, counts.errored, counts.expired) == (0, 2, 1, 1)

    results = list(batches.results(batch.id))
    assert [item.custom_id for item in results] == [
        "f004-extract-1",
        "f003-extract-1",
        "f002-extract-1",
        "f001-extract-1",
    ]
    by_id = {item.custom_id: item.result for item in results}
    assert by_id["f001-extract-1"].type == "succeeded"
    assert tool_use(by_id["f001-extract-1"].message).input == product("dev-001") | {"duvidas": []}
    assert by_id["f002-extract-1"].type == "errored"
    assert by_id["f002-extract-1"].error.error.message == "API falsa: erro interno simulado"
    assert by_id["f003-extract-1"].type == "expired"

    # O reenvio igual passa: cada erro do lote acontece uma vez só.
    again = batches.create(requests=requests[1:3])
    batches.retrieve(again.id)  # ainda in_progress
    assert {item.custom_id: item.result.type for item in batches.results(again.id)} == {
        "f002-extract-1": "succeeded",
        "f003-extract-1": "succeeded",
    }


def test_batch_rejects_what_the_real_api_rejects():
    batches = FakeAPI().client.messages.batches
    with pytest.raises(anthropic.BadRequestError, match="custom_id"):
        batches.create(requests=[{"custom_id": "ficha 1", "params": extract("dev-001")}])
    with pytest.raises(anthropic.NotFoundError):
        batches.retrieve("msgbatch_de_outro_cliente")

    # Pedido inválido dentro do lote volta como errored, como na API real.
    params = extract("dev-001")
    del params["thinking"]
    batch = batches.create(requests=[{"custom_id": "f001-extract-1", "params": params}])
    [item] = batches.results(batch.id)
    assert item.result.type == "errored"
    assert item.result.error.error.type == "invalid_request_error"


def drop_thinking(params):
    del params["thinking"]


def add_prefill(params):
    params["messages"].append({"role": "assistant", "content": '{"nome": '})


def forget_tool_result(params):
    block = {"type": "tool_use", "id": "toolu_1", "name": params["tools"][0]["name"], "input": {}}
    params["messages"] += [
        {"role": "assistant", "content": [block]},
        {"role": "user", "content": "Corrija a garantia."},
    ]


def unforce_tool(params):
    params["tool_choice"] = {"type": "auto"}


@pytest.mark.parametrize(
    "change, message",
    [
        (drop_thinking, "thinking"),
        (add_prefill, "prefill"),
        (forget_tool_result, "tool_result"),
        (unforce_tool, "tool_choice"),
    ],
)
def test_requests_the_real_api_refuses(change, message):
    params = extract("dev-001")
    change(params)
    with pytest.raises(anthropic.BadRequestError, match=message):
        FakeAPI().client.messages.create(**params)
