"""Assistente da loja: responde às perguntas dos clientes só com o catálogo aprovado.

É um laço de ferramentas: o modelo pede uma ferramenta, o código roda e devolve o resultado num
tool_result, e assim por diante até ele responder em texto. As duas ferramentas só leem, e só os
produtos com `destino = 'catalogo'`:
- `buscar_produtos`: texto, categoria, preço máximo e filtros por campo (igual, mínimo, máximo,
  acima, abaixo), do mais barato ao mais caro;
- `ver_produto`: todos os campos de um produto.

O campo de um filtro é conferido contra os campos da categoria (`schemas.field_specs`) antes de
chegar ao SQL, e o valor tem que ser do tipo do campo. Entrada inválida volta ao modelo num
tool_result com is_error e a explicação, e ele tenta de novo. O preço sai formatado pelo código
(R$ 1.234,56), e o prompt manda o modelo copiar, sem calcular.

Cada pergunta refaz o laço do zero, com o histórico de texto da conversa (as perguntas e as
respostas finais), que vem de fora. O laço para em ASSISTANT_MAX_CALLS chamadas ao modelo. O custo
de cada chamada sai impresso, e a resposta traz a soma.

Uso: `answer(client, conn, pergunta, historico)`, com o cliente recebido de fora (o real ou
`FakeAPI().client`).
"""

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, ValidationError

from pipeline import db
from pipeline.config import ASSISTANT_MAX_CALLS, ASSISTANT_MAX_TOKENS, MODEL, PROMPTS_DIR
from pipeline.extract import call_cost, usage_line
from pipeline.schemas import CATEGORIES, StrictModel, field_specs
from pipeline.tools import input_schema

PROMPT = (PROMPTS_DIR / "assistant.md").read_text(encoding="utf-8")
# Quantos produtos a busca devolve, no máximo: os mais baratos.
SEARCH_LIMIT = 10
TOO_MANY_CALLS = (
    f"Não consegui responder com até {ASSISTANT_MAX_CALLS} consultas ao catálogo. "
    "Tente uma pergunta mais direta."
)
NO_ANSWER = "O modelo não escreveu resposta."
CUT_NOTE = " [resposta cortada no limite de tamanho]"


class InvalidInput(Exception):
    """Uma entrada de ferramenta que o código recusa. A mensagem vai para o modelo."""


# --- As ferramentas.


class Filter(StrictModel):
    campo: str = Field(description="O nome do campo, como na lista da ferramenta (ex.: 'vram_gb').")
    operador: Literal["igual", "minimo", "maximo", "acima", "abaixo"] = Field(
        description=(
            "'igual'; 'minimo' (>=) e 'maximo' (<=) incluem o valor dado; 'acima' (>) e 'abaixo' "
            "(<) não incluem. Só 'igual' vale em campo que não é numérico."
        )
    )
    valor: str | float | bool = Field(
        description=(
            "Número em campo numérico (12, e não '12 GB'), true ou false em campo sim/não, e "
            "texto nos outros (num campo de opções, um dos valores da lista)."
        )
    )


class Search(StrictModel):
    texto: str | None = Field(
        description=(
            "Palavras que têm que aparecer no nome ou na marca (ex.: 'RTX 5070'), ou null. "
            "Para tipo, tamanho ou número, prefira categoria e filtros."
        )
    )
    categoria: Literal[CATEGORIES] | None = Field(description="A categoria, ou null para todas.")
    preco_max: float | None = Field(
        description="Preço máximo em reais, só o número (R$ 1.500,00 = 1500), ou null."
    )
    filtros: list[Filter] = Field(
        description="Filtros por campo da categoria, todos ao mesmo tempo. [] para nenhum."
    )


class ProductId(StrictModel):
    id: int = Field(description="O id do produto, como a busca devolveu.")


def _kind(spec: dict) -> str:
    if spec["opcoes"]:
        return " | ".join(spec["opcoes"])
    return {"inteiro": "número", "numero": "número", "booleano": "sim/não"}.get(
        spec["tipo"], spec["tipo"]
    )


def _fields_list() -> str:
    """Os campos de cada categoria e o tipo deles, para o modelo montar os filtros."""
    common = field_specs(None)
    lines = ["- todas: " + ", ".join(f"{spec['campo']} ({_kind(spec)})" for spec in common)]
    for category in CATEGORIES:
        specs = field_specs(category)[len(common) :]
        lines.append(f"- {category}: " + ", ".join(f"{s['campo']} ({_kind(s)})" for s in specs))
    return "\n".join(lines)


SEARCH_TOOL = {
    "name": "buscar_produtos",
    "description": (
        "Busca produtos no catálogo da loja. Devolve `encontrados` (quantos passaram em todos os "
        f"filtros) e até {SEARCH_LIMIT} deles, do mais barato ao mais caro, com id, categoria, "
        "nome, preço e os campos usados nos filtros. Um produto sem o dado de um campo não passa "
        "no filtro desse campo.\n\n"
        "Campos de cada categoria, com o tipo (os de 'todas' valem em qualquer uma; os outros "
        "pedem a categoria):\n" + _fields_list()
    ),
    "strict": True,
    "input_schema": input_schema(Search),
}
PRODUCT_TOOL = {
    "name": "ver_produto",
    "description": (
        "Mostra todos os campos de um produto do catálogo, pelo id que a busca devolveu. Um "
        "campo null é um dado que a loja não tem."
    ),
    "strict": True,
    "input_schema": input_schema(ProductId),
}
TOOLS = [SEARCH_TOOL, PRODUCT_TOOL]


def brl(value: float | None) -> str:
    """O preço como a loja escreve: R$ 1.234,56."""
    if value is None:
        return "sem preço"
    return "R$ " + f"{value:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def checked_filters(categoria: str | None, filtros: list[Filter]) -> list[tuple]:
    """Os filtros como `(campo, operador, valor)` para o SQL, depois de conferir cada um contra os
    campos da categoria (ou os comuns, sem categoria) e o tipo do campo."""
    specs = {spec["campo"]: spec for spec in field_specs(categoria)}
    where = f"em '{categoria}'" if categoria else "em todas as categorias (escolha a categoria)"
    problems, checked = [], []
    for item in filtros:
        spec = specs.get(item.campo)
        if spec is None:
            problems.append(
                f"o campo '{item.campo}' não existe {where}. Campos: {', '.join(specs)}."
            )
            continue
        numeric = spec["tipo"] in ("inteiro", "numero")
        value = item.valor
        if item.operador != "igual" and not numeric:
            problems.append(f"'{item.operador}' só vale em campo numérico; '{item.campo}' não é.")
        elif numeric and (isinstance(value, bool) or not isinstance(value, int | float)):
            problems.append(f"'{item.campo}' é numérico: o valor tem que ser um número.")
        elif spec["tipo"] == "booleano" and not isinstance(value, bool):
            problems.append(f"'{item.campo}' é sim/não: o valor tem que ser true ou false.")
        elif spec["opcoes"] and value not in spec["opcoes"]:
            options = ", ".join(spec["opcoes"])
            problems.append(f"'{item.campo}' tem que ser um destes valores: {options}.")
        elif spec["tipo"] == "texto" and not isinstance(value, str):
            problems.append(f"'{item.campo}' é texto: o valor tem que ser um texto.")
        else:
            checked.append((item.campo, item.operador, value))
    if problems:
        raise InvalidInput("\n".join(problems))
    return checked


def search(conn, query: Search) -> str:
    filters = checked_filters(query.categoria, query.filtros)
    words = (query.texto or "").split()
    rows = db.search_catalog(conn, query.categoria, words, query.preco_max, filters)
    products = []
    for row in rows[:SEARCH_LIMIT]:
        fields = json.loads(row["campos"])
        item = {
            "id": row["id"],
            "categoria": row["categoria"],
            "nome": fields["nome"],
            "preco": brl(fields.get("preco")),
        }
        item |= {campo: fields.get(campo) for campo, _, _ in filters if campo not in item}
        products.append(item)
    return json.dumps({"encontrados": len(rows), "produtos": products}, ensure_ascii=False)


def show_product(conn, product_id: int) -> str:
    row = db.catalog_product(conn, product_id)
    if row is None:
        raise InvalidInput(f"o produto {product_id} não está no catálogo.")
    fields = json.loads(row["campos"])
    product = {"id": row["id"], "categoria": row["categoria"], **fields}
    product["preco"] = brl(fields.get("preco"))
    return json.dumps(product, ensure_ascii=False)


def run_tool(conn, name: str, data: dict) -> tuple[str, bool]:
    """Roda uma ferramenta e devolve o conteúdo do tool_result e se ele é um erro."""
    try:
        if name == SEARCH_TOOL["name"]:
            return search(conn, Search.model_validate(data)), False
        if name == PRODUCT_TOOL["name"]:
            return show_product(conn, ProductId.model_validate(data).id), False
        raise InvalidInput(f"a ferramenta '{name}' não existe.")
    except ValidationError as error:
        # Com strict, a API real não manda entrada fora do formato; fica a defesa.
        problems = [
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        ]
        return "entrada fora do formato: " + "; ".join(problems), True
    except InvalidInput as error:
        return str(error), True


# --- O laço.


@dataclass
class Reply:
    text: str
    calls: int  # chamadas ao modelo
    cost: float  # US$, a soma das chamadas


def request_params(messages: list) -> dict:
    """Os parâmetros de `messages.create`: as ferramentas à escolha do modelo (tool_choice auto)."""
    return {
        "model": MODEL,
        "max_tokens": ASSISTANT_MAX_TOKENS,
        "thinking": {"type": "disabled"},
        # O cache guarda as ferramentas e o prompt, iguais em toda chamada: da segunda chamada da
        # pergunta em diante (e nas perguntas seguintes, por 5 minutos), eles saem a 0,1×.
        "system": [{"type": "text", "text": PROMPT, "cache_control": {"type": "ephemeral"}}],
        "tools": TOOLS,
        "messages": messages,
    }


def final_text(message) -> str:
    text = "".join(block.text for block in message.content if block.type == "text").strip()
    if message.stop_reason == "max_tokens":
        text += CUT_NOTE
    return text.strip() or NO_ANSWER


def answer(client, conn, question: str, history: list[tuple[str, str]] = ()) -> Reply:
    """Responde a uma pergunta. `history` são as trocas anteriores: (pergunta, resposta)."""
    messages = []
    for asked, answered in history:
        messages += [
            {"role": "user", "content": asked},
            {"role": "assistant", "content": answered},
        ]
    messages.append({"role": "user", "content": question})
    print(f"[assistente] {question}")
    cost = 0.0
    for number in range(1, ASSISTANT_MAX_CALLS + 1):
        message = client.messages.create(**request_params(messages))
        call = call_cost(message.usage)
        cost += call
        print(f"  chamada {number}: {usage_line(message.usage, call)}")
        tool_uses = [block for block in message.content if block.type == "tool_use"]
        if message.stop_reason != "tool_use" or not tool_uses:
            reply = Reply(final_text(message), number, cost)
            break
        results = []
        for block in tool_uses:
            content, is_error = run_tool(conn, block.name, block.input)
            shown = json.dumps(block.input, ensure_ascii=False)
            print(f"    {block.name} {shown}" + (f" → erro: {content}" if is_error else ""))
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                }
            )
        messages += [
            {"role": "assistant", "content": message.content},
            {"role": "user", "content": results},
        ]
    else:
        reply = Reply(TOO_MANY_CALLS, ASSISTANT_MAX_CALLS, cost)
    print(f"  resposta: {reply.calls} chamadas | custo total: ${reply.cost:.6f}")
    return reply
