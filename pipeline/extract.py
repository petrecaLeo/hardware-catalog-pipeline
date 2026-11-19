"""Extração no modo direto: classificar → extrair com o molde da categoria → validar → corrigir.

Cada etapa ('classify' e 'extract') é um pedido com a ferramenta forçada e strict. A resposta passa
pelo esquema e pelas regras. Se falhar, a conversa continua com a resposta errada e o erro exato
num tool_result com is_error, até EXTRA_ATTEMPTS vezes. Uma resposta cortada (max_tokens) também
gasta uma tentativa, mas volta igual, só com mais tokens: não há o que corrigir nela.

O modo lote (`batch.py`) usa as mesmas peças: `next_params` (a regra das novas tentativas, que
monta o pedido seguinte), `read_answer` (a resposta), `call_cost` (o custo), `final_category`,
`store_product` e `round_summary`.

Uso: `process(client, conn)`, com o cliente recebido de fora (o real ou `FakeAPI().client`).
"""

from dataclasses import dataclass

from pipeline import db
from pipeline.config import (
    CACHE_READ_FACTOR,
    CACHE_WRITE_FACTOR,
    CLASSIFY_MAX_TOKENS,
    CLASSIFY_RETRY_MAX_TOKENS,
    EXTRA_ATTEMPTS,
    EXTRACT_MAX_TOKENS,
    EXTRACT_RETRY_MAX_TOKENS,
    MODEL,
    PRICES,
    PROMPTS_DIR,
)
from pipeline.rules import Issue, validate_classification, validate_product
from pipeline.schemas import CATEGORIES
from pipeline.tools import CLASSIFY_TOOL, EXTRACT_TOOLS

STAGES = ("classify", "extract")
PROMPTS = {stage: (PROMPTS_DIR / f"{stage}.md").read_text(encoding="utf-8") for stage in STAGES}
# As fichas do dev que aparecem como exemplo nos prompts. Elas não contam na avaliação, porque o
# modelo já viu a resposta. Trocou um exemplo? Troque aqui também (um teste confere).
EXAMPLE_SHEETS = frozenset({"dev-018", "dev-036", "dev-047"})
MAX_TOKENS = {"classify": CLASSIFY_MAX_TOKENS, "extract": EXTRACT_MAX_TOKENS}
RETRY_MAX_TOKENS = {"classify": CLASSIFY_RETRY_MAX_TOKENS, "extract": EXTRACT_RETRY_MAX_TOKENS}
DOUBT_KEYS = {"campo", "nivel", "motivo"}

CORRECTION = (
    "A resposta não passou na validação:\n{errors}\n"
    "Releia a ficha e chame a ferramenta de novo com tudo corrigido. Se a ficha não traz o dado, "
    "use null em vez de trocar por outro número."
)


# --- Uma chamada.


def sheet_message(text: str) -> dict:
    """A mensagem do usuário com a ficha. A tag separa a ficha dos exemplos do prompt."""
    return {"role": "user", "content": f"<ficha>\n{text}\n</ficha>"}


def request_params(stage: str, category: str | None, messages: list, max_tokens: int) -> dict:
    """Os parâmetros de `messages.create` (ou do `params` de um pedido do lote)."""
    tool = CLASSIFY_TOOL if stage == "classify" else EXTRACT_TOOLS[category]
    return {
        "model": MODEL,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
        # O cache guarda o molde e o prompt (as ferramentas vêm antes do system). A classificação
        # repete esse começo em toda ficha, e a extração, em toda ficha da mesma categoria.
        "system": [
            {"type": "text", "text": PROMPTS[stage], "cache_control": {"type": "ephemeral"}}
        ],
        "tools": [tool],
        "tool_choice": {"type": "tool", "name": tool["name"]},
        "messages": messages,
    }


@dataclass
class Answer:
    """Uma resposta lida: o bloco tool_use e o que há de errado nele (vazio = válida)."""

    # O ToolUseBlock, ou None se a resposta não trouxe um. None sem `cut`: o pedido do lote voltou
    # sem resposta (errored, expired ou canceled).
    block: object | None
    issues: list[Issue]
    cut: bool  # cortada: vai de novo com mais tokens, sem conversa de correção

    @property
    def data(self) -> dict | None:
        return None if self.block is None else self.block.input

    @property
    def error(self) -> str | None:
        return "\n".join(issue.message for issue in self.issues) or None


def read_answer(message, stage: str, text: str, category: str | None = None) -> Answer:
    """Lê a resposta pelo tipo do bloco e valida: a classificação ou os campos da categoria."""
    block = next((block for block in message.content if block.type == "tool_use"), None)
    if message.stop_reason == "max_tokens":
        issue = Issue("max_tokens", "resposta cortada no limite de max_tokens.")
        return Answer(block, [issue], True)
    if block is None:
        issue = Issue("tool_use", f"a resposta não chamou a ferramenta ({message.stop_reason}).")
        return Answer(None, [issue], True)
    if stage == "classify":
        return Answer(block, validate_classification(block.input), False)
    return Answer(block, validate_product(category, block.input, text, doubts=True), False)


def correction_messages(messages: list, block, issues: list[Issue]) -> list:
    """A conversa seguida da resposta errada e do erro exato, num tool_result com is_error.

    O tool_use é montado à mão (no lote, a resposta vem de outro pedido).
    """
    wrong = {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    errors = "\n".join(f"- {issue.message}" for issue in issues)
    result = {
        "type": "tool_result",
        "tool_use_id": block.id,
        "is_error": True,
        "content": CORRECTION.format(errors=errors),
    }
    return [
        *messages,
        {"role": "assistant", "content": [wrong]},
        {"role": "user", "content": [result]},
    ]


def call_cost(usage, mode: str = "direct") -> float:
    """US$ de uma chamada: entrada, saída, escrita no cache (1,25×) e leitura do cache (0,1×)."""
    price_in, price_out = PRICES[mode]
    tokens = (
        usage.input_tokens * price_in
        + usage.output_tokens * price_out
        + (usage.cache_creation_input_tokens or 0) * price_in * CACHE_WRITE_FACTOR
        + (usage.cache_read_input_tokens or 0) * price_in * CACHE_READ_FACTOR
    )
    return tokens / 1_000_000


def usage_line(usage, cost: float) -> str:
    """`tokens: 16 in / 41 out | cost: $0.000221`, com o cache no meio quando houver."""
    line = f"tokens: {usage.input_tokens} in / {usage.output_tokens} out"
    write, read = usage.cache_creation_input_tokens or 0, usage.cache_read_input_tokens or 0
    if write or read:
        line += f" | cache: {write} write / {read} read"
    return f"{line} | cost: ${cost:.6f}"


# --- Uma ficha.


@dataclass
class Call:
    stage: str
    number: int  # 1 = primeira resposta; depois, as novas tentativas
    answer: Answer
    usage: object
    cost: float
    stop_reason: str | None


@dataclass
class SheetResult:
    category: str
    extraction: Answer  # a última resposta da extração
    issues: list[Issue]  # os erros que sobraram, da classificação e da extração; vazio = válido
    calls: list[Call]


def next_params(stage: str, text: str, category: str | None, answers: list[Answer]) -> dict | None:
    """O pedido seguinte de uma etapa, a partir das respostas que ela já teve, ou None se ela
    terminou. É a regra das novas tentativas, a mesma nos dois modos:
    - resposta válida, ou já foram EXTRA_ATTEMPTS novas tentativas: a etapa terminou;
    - resposta cortada: o mesmo pedido, com mais max_tokens;
    - erro de validação: a conversa de correção;
    - sem resposta (só no lote: errored, expired ou canceled): o mesmo pedido.
    """
    if answers and (not answers[-1].issues or len(answers) > EXTRA_ATTEMPTS):
        return None
    messages = [sheet_message(text)]
    max_tokens = MAX_TOKENS[stage]
    for answer in answers:
        if answer.cut:
            max_tokens = RETRY_MAX_TOKENS[stage]
        elif answer.block is not None:
            messages = correction_messages(messages, answer.block, answer.issues)
    return request_params(stage, category, messages, max_tokens)


def run_stage(client, stage: str, text: str, category: str | None, calls: list[Call]) -> Answer:
    """Uma etapa com as novas tentativas. Guarda cada chamada em `calls` e devolve a última."""
    answers = []
    while params := next_params(stage, text, category, answers):
        message = client.messages.create(**params)
        answer = read_answer(message, stage, text, category)
        answers.append(answer)
        number, cost = len(answers), call_cost(message.usage)
        print(f"  {stage} {number}: {usage_line(message.usage, cost)}")
        calls.append(Call(stage, number, answer, message.usage, cost, message.stop_reason))
        if answer.issues:
            print("    " + answer.error.replace("\n", "\n    "))
    return answers[-1]


def final_category(classification: Answer) -> str:
    """A categoria em que a ficha é extraída, depois da última resposta da classificação."""
    category = (classification.data or {}).get("categoria")
    if category not in CATEGORIES:
        # Só acontece se a classificação falhar em todas as tentativas (cortada ou, no lote, sem
        # resposta). O produto fica reprovado pelos erros da classificação, e o revisor vê o que
        # foi possível extrair.
        return "outro"
    return category


def extract_sheet(client, text: str) -> SheetResult:
    """Classifica e extrai uma ficha, com as correções. Não grava nada."""
    calls = []
    classification = run_stage(client, "classify", text, None, calls)
    category = final_category(classification)
    extraction = run_stage(client, "extract", text, category, calls)
    return SheetResult(category, extraction, classification.issues + extraction.issues, calls)


def split_doubts(data: dict | None) -> tuple[dict, list[dict]]:
    """Os campos e as dúvidas de uma resposta da extração.

    Uma resposta cortada pode vir sem as dúvidas, ou com a última pela metade: ficam só as
    completas, que o roteamento, a tela e a avaliação leem.
    """
    fields = dict(data or {})
    doubts = fields.pop("duvidas", None)
    if not isinstance(doubts, list):
        doubts = []
    doubts = [doubt for doubt in doubts if isinstance(doubt, dict) and DOUBT_KEYS <= doubt.keys()]
    return fields, doubts


def store_product(conn, ficha_id: int, result: SheetResult) -> None:
    """Grava o produto da ficha, sem commit: os campos, as dúvidas e o resultado da extração
    (válido ou reprovado, com os erros que sobraram). O destino fica para o roteamento."""
    fields, doubts = split_doubts(result.extraction.data)
    extraction = "reprovado" if result.issues else "valido"
    errors = [{"campo": issue.field, "mensagem": issue.message} for issue in result.issues]
    db.save_product(conn, ficha_id, result.category, fields, doubts, extraction, errors)


def save_result(conn, rodada_id: int, ficha_id: int, result: SheetResult) -> None:
    """Grava as chamadas e o produto da ficha num commit só."""
    with conn:
        for call in result.calls:
            db.add_attempt(
                conn,
                ficha_id,
                rodada_id,
                call.stage,
                call.number,
                call.answer.data,
                call.stop_reason,
                call.answer.error,
                call.usage,
                call.cost,
            )
        store_product(conn, ficha_id, result)


# --- Uma rodada.


@dataclass
class Summary:
    sheets: int = 0
    valid: int = 0
    failed: int = 0
    calls: int = 0
    retries: int = 0  # chamadas além da primeira de cada etapa (correções e cortes)
    cost: float = 0.0


def round_summary(conn, rodada_id: int) -> Summary:
    """Os números de uma rodada, lidos do banco (no lote, também os de antes de uma retomada)."""
    totals = db.round_totals(conn, rodada_id)
    return Summary(
        sheets=totals["fichas"],
        valid=totals["validas"],
        failed=totals["reprovadas"],
        calls=totals["chamadas"],
        retries=int(totals["novas_tentativas"]),
        cost=totals["custo"],
    )


def process(client, conn, conjunto: str | None = None, limit: int | None = None) -> Summary:
    """Processa as fichas pendentes no modo direto, uma por vez, e grava tudo numa rodada nova."""
    sheets = db.pending_sheets(conn, conjunto, limit)
    if not sheets:
        return Summary()
    rodada_id = db.start_round(conn, "direct")
    for sheet in sheets:
        print(f"[{sheet['id_origem']}]")
        result = extract_sheet(client, sheet["texto"])
        save_result(conn, rodada_id, sheet["id"], result)
        if result.issues:
            print(f"  → reprovada ({result.category}) depois das tentativas")
        else:
            print(f"  → válida ({result.category})")
    return round_summary(conn, rodada_id)
