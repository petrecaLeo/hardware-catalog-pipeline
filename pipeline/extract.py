"""Extração no modo direto: classificar → extrair com o molde da categoria → validar → corrigir.

Cada etapa ('classify' e 'extract') é um pedido com a ferramenta forçada e strict. A resposta passa
pelo esquema e pelas regras. Se falhar, a conversa continua com a resposta errada e o erro exato
num tool_result com is_error, até EXTRA_ATTEMPTS vezes. Uma resposta cortada (max_tokens) também
gasta uma tentativa, mas volta igual, só com mais tokens: não há o que corrigir nela.

O modo lote reaproveita as peças de uma chamada: `request_params` (o pedido), `read_answer` (a
resposta), `correction_messages` (a conversa de correção) e `call_cost` (o custo).

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
MAX_TOKENS = {"classify": CLASSIFY_MAX_TOKENS, "extract": EXTRACT_MAX_TOKENS}
RETRY_MAX_TOKENS = {"classify": CLASSIFY_RETRY_MAX_TOKENS, "extract": EXTRACT_RETRY_MAX_TOKENS}

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

    block: object | None  # o ToolUseBlock, ou None se a resposta não trouxe um
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


@dataclass
class SheetResult:
    category: str
    extraction: Answer  # a última resposta da extração
    issues: list[Issue]  # os erros que sobraram, da classificação e da extração; vazio = válido
    calls: list[Call]


def run_stage(client, stage: str, text: str, category: str | None, calls: list[Call]) -> Answer:
    """Uma etapa com as novas tentativas. Guarda cada chamada em `calls` e devolve a última."""
    messages = [sheet_message(text)]
    max_tokens = MAX_TOKENS[stage]
    for number in range(1, EXTRA_ATTEMPTS + 2):
        message = client.messages.create(**request_params(stage, category, messages, max_tokens))
        answer = read_answer(message, stage, text, category)
        cost = call_cost(message.usage)
        print(f"  {stage} {number}: {usage_line(message.usage, cost)}")
        calls.append(Call(stage, number, answer, message.usage, cost))
        if not answer.issues:
            break
        print("    " + answer.error.replace("\n", "\n    "))
        if answer.cut:
            max_tokens = RETRY_MAX_TOKENS[stage]
        else:
            messages = correction_messages(messages, answer.block, answer.issues)
    return answer


def extract_sheet(client, text: str) -> SheetResult:
    """Classifica e extrai uma ficha, com as correções. Não grava nada."""
    calls = []
    classification = run_stage(client, "classify", text, None, calls)
    category = (classification.data or {}).get("categoria")
    if category not in CATEGORIES:
        # Só acontece se a classificação vier cortada em todas as tentativas. O produto fica
        # reprovado pelos erros da classificação, e o revisor vê o que foi possível extrair.
        category = "outro"
    extraction = run_stage(client, "extract", text, category, calls)
    return SheetResult(category, extraction, classification.issues + extraction.issues, calls)


def save_result(conn, rodada_id: int, ficha_id: int, result: SheetResult) -> None:
    """Grava as chamadas e o produto da ficha num commit só."""
    fields = dict(result.extraction.data or {})
    doubts = fields.pop("duvidas", None)
    if not isinstance(doubts, list):  # resposta cortada pode vir sem as dúvidas
        doubts = []
    status = "reprovado" if result.issues else "valido"
    reasons = [{"campo": issue.field, "mensagem": issue.message} for issue in result.issues]
    with conn:
        for call in result.calls:
            db.add_attempt(
                conn,
                ficha_id,
                rodada_id,
                call.stage,
                call.number,
                call.answer.data,
                call.answer.error,
                call.usage,
                call.cost,
            )
        db.save_product(conn, ficha_id, result.category, fields, doubts, status, reasons)


# --- Uma rodada.


@dataclass
class Summary:
    sheets: int = 0
    valid: int = 0
    failed: int = 0
    calls: int = 0
    retries: int = 0  # chamadas além da primeira de cada etapa (correções e cortes)
    cost: float = 0.0


def process(client, conn, conjunto: str | None = None, limit: int | None = None) -> Summary:
    """Processa as fichas pendentes no modo direto, uma por vez, e grava tudo numa rodada nova."""
    summary = Summary()
    sheets = db.pending_sheets(conn, conjunto, limit)
    if not sheets:
        return summary
    rodada_id = db.start_round(conn, "direct")
    for sheet in sheets:
        print(f"[{sheet['id_origem']}]")
        result = extract_sheet(client, sheet["texto"])
        save_result(conn, rodada_id, sheet["id"], result)

        summary.sheets += 1
        summary.calls += len(result.calls)
        summary.retries += sum(call.number > 1 for call in result.calls)
        summary.cost += sum(call.cost for call in result.calls)
        if result.issues:
            summary.failed += 1
            print(f"  → reprovada ({result.category}) depois das tentativas")
        else:
            summary.valid += 1
            print(f"  → válida ({result.category})")
    return summary
