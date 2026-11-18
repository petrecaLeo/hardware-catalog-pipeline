"""Modo lote: o mesmo fluxo do modo direto (classificar → extrair → corrigir), em lotes.

O lote custa a metade, mas pode levar horas. Cada lote leva o próximo pedido de cada ficha que
ainda não terminou: o primeiro classifica todas, o segundo extrai as classificadas, e os seguintes
levam só as que falharam, até EXTRA_ATTEMPTS novas tentativas por etapa, como no modo direto. Uma
ficha cuja classificação falhou pode ir ao mesmo lote que a extração das outras.

O próximo pedido sai das tentativas que a ficha já tem no banco, nesta rodada, pela mesma regra do
modo direto (`extract.next_params`):
- o lote não respondeu (errored, expired ou canceled): o mesmo pedido de novo. Conta como
  tentativa, mas sem custo; assim um pedido que a API sempre recusa não volta para sempre;
- resposta cortada: o mesmo pedido, com mais max_tokens;
- erro de validação: a conversa de correção.
Como tudo sai do banco, um `process --lote` interrompido continua de onde parou. O lote enviado e
ainda sem resultado (em `lotes`, como 'pendente') é esperado, e não enviado de novo. Os resultados
de um lote são gravados num commit só, junto com o lote como 'concluido': se o processo cair no
meio, nada fica gravado pela metade, e o lote é lido de novo.

O custom_id é `f042-extract-1`: o id da ficha no banco, a etapa e o número da tentativa. Os
resultados chegam fora de ordem, e é o custom_id que diz de qual pedido é cada um.
"""

import json
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

import anthropic
from anthropic.types import ToolUseBlock, Usage

from pipeline import db
from pipeline.config import BATCH_POLL_SECONDS
from pipeline.extract import (
    Answer,
    SheetResult,
    Summary,
    call_cost,
    final_category,
    next_params,
    read_answer,
    round_summary,
    store_product,
    usage_line,
)
from pipeline.rules import Issue
from pipeline.tools import CLASSIFY_TOOL, EXTRACT_TOOLS

# Pedido do lote que voltou sem resposta: não é cobrado.
NO_USAGE = Usage(input_tokens=0, output_tokens=0)
FAILURES = {
    "errored": "o lote devolveu erro: {}",
    "expired": "o pedido expirou no lote sem resposta",
    "canceled": "o pedido foi cancelado no lote",
}


def custom_id_for(ficha_id: int, stage: str, number: int) -> str:
    return f"f{ficha_id:03d}-{stage}-{number}"


def sheet_id_of(custom_id: str) -> int:
    """O id da ficha de um custom_id: 'f042-extract-1' → 42."""
    return int(custom_id.split("-")[0][1:])


@dataclass
class Request:
    """O próximo pedido de uma ficha."""

    sheet: object  # a linha da ficha: id, id_origem e texto
    stage: str
    number: int
    category: str | None  # None na classificação
    params: dict

    @property
    def custom_id(self) -> str:
        return custom_id_for(self.sheet["id"], self.stage, self.number)


# --- O próximo passo de uma ficha, a partir das tentativas no banco.


def stored_answers(rows, stage: str, text: str, category: str | None) -> list[Answer]:
    """As respostas guardadas de uma etapa, lidas de novo como no modo direto (mesmas regras).

    O id do bloco tool_use não é guardado. Na conversa de correção vai um id montado com o
    custom_id da tentativa: ele só precisa ser o mesmo no tool_use e no tool_result.
    """
    answers = []
    for row in rows:
        if row["etapa"] != stage:
            continue
        if row["stop_reason"] is None:  # o lote não respondeu: não há resposta para ler
            answers.append(Answer(None, [Issue("lote", row["erro"])], False))
            continue
        content = []
        if row["resposta"] is not None:
            tool = CLASSIFY_TOOL if stage == "classify" else EXTRACT_TOOLS[category]
            block_id = "toolu_" + custom_id_for(row["ficha_id"], stage, row["numero"])
            block = ToolUseBlock(
                type="tool_use", id=block_id, name=tool["name"], input=json.loads(row["resposta"])
            )
            content.append(block)
        message = SimpleNamespace(content=content, stop_reason=row["stop_reason"])
        answers.append(read_answer(message, stage, text, category))
    return answers


def next_step(conn, rodada_id: int, sheet) -> Request | SheetResult:
    """O próximo pedido da ficha, ou o resultado dela, se as duas etapas terminaram."""
    rows = db.round_attempts(conn, rodada_id, sheet["id"])
    text = sheet["texto"]

    classification = stored_answers(rows, "classify", text, None)
    params = next_params("classify", text, None, classification)
    if params:
        return Request(sheet, "classify", len(classification) + 1, None, params)

    category = final_category(classification[-1])
    extraction = stored_answers(rows, "extract", text, category)
    params = next_params("extract", text, category, extraction)
    if params:
        return Request(sheet, "extract", len(extraction) + 1, category, params)

    # As chamadas já estão no banco: o resultado só serve para gravar o produto.
    issues = classification[-1].issues + extraction[-1].issues
    return SheetResult(category, extraction[-1], issues, [])


def next_requests(conn, rodada_id: int, sheets: list) -> dict[str, Request]:
    """Os pedidos do próximo lote, por custom_id: um para cada ficha que não terminou."""
    requests = {}
    for sheet in sheets:
        step = next_step(conn, rodada_id, sheet)
        if isinstance(step, Request):
            requests[step.custom_id] = step
    return requests


# --- Um lote.


def send_batch(client, conn, rodada_id: int, requests: dict[str, Request]) -> str:
    """Envia o lote e guarda o id dele no banco logo em seguida."""
    batch = client.messages.batches.create(
        requests=[
            {"custom_id": custom_id, "params": request.params}
            for custom_id, request in requests.items()
        ]
    )
    db.add_batch(conn, batch.id, rodada_id, list(requests))
    stages = Counter(request.stage for request in requests.values())
    print(f"lote {batch.id} enviado: {', '.join(f'{n} {s}' for s, n in stages.items())}")
    return batch.id


def wait_for_results(client, lote_id: str, poll_seconds: float) -> dict:
    """Consulta o lote até ele terminar e devolve os resultados por custom_id."""
    while True:
        batch = client.messages.batches.retrieve(lote_id)
        counts = batch.request_counts
        failed = counts.errored + counts.expired + counts.canceled
        print(
            f"  {datetime.now():%H:%M:%S} {counts.processing} processando | "
            f"{counts.succeeded} prontos | {failed} com erro"
        )
        if batch.processing_status == "ended":
            break
        time.sleep(poll_seconds)
    return {item.custom_id: item.result for item in client.messages.batches.results(lote_id)}


def save_results(conn, rodada_id: int, lote_id: str, requests: dict[str, Request], results: dict):
    """Grava as tentativas do lote, o produto das fichas que terminaram e o lote como concluído,
    num commit só: se o processo cair no meio, o lote continua pendente e é lido de novo."""
    finished = []
    with conn:
        for custom_id, request in requests.items():
            result = results.get(custom_id)
            if result is None:  # não estava neste lote: vai no próximo
                continue
            sheet, stage, number = request.sheet, request.stage, request.number
            if result.type == "succeeded":
                message = result.message
                answer = read_answer(message, stage, sheet["texto"], request.category)
                usage, stop_reason = message.usage, message.stop_reason
                response, error = answer.data, answer.error
            else:
                detail = result.error.error.message if result.type == "errored" else ""
                usage, stop_reason = NO_USAGE, None
                response, error = None, FAILURES[result.type].format(detail)
            cost = call_cost(usage, "batch")
            print(f"  [{sheet['id_origem']}] {stage} {number}: {usage_line(usage, cost)}")
            if error:
                print("    " + error.replace("\n", "\n    "))
            db.add_attempt(
                conn,
                sheet["id"],
                rodada_id,
                stage,
                number,
                response,
                stop_reason,
                error,
                usage,
                cost,
            )

        for request in requests.values():
            step = next_step(conn, rodada_id, request.sheet)
            if isinstance(step, SheetResult):
                store_product(conn, request.sheet["id"], step)
                finished.append((request.sheet, step))
        db.set_batch_status(conn, lote_id, "concluido")

    for sheet, result in finished:
        if result.issues:
            print(f"  [{sheet['id_origem']}] → reprovada ({result.category}) depois das tentativas")
        else:
            print(f"  [{sheet['id_origem']}] → válida ({result.category})")


# --- Uma rodada.


def open_round(conn) -> tuple[int | None, list]:
    """A última rodada em lote, se ela tem fichas que não terminaram, e essas fichas."""
    rodada_id, custom_ids = db.last_batch_round(conn)
    ids = {sheet_id_of(custom_id) for custom_id in custom_ids}
    sheets = [sheet for sheet in db.pending_sheets(conn) if sheet["id"] in ids]
    return (rodada_id, sheets) if sheets else (None, [])


def process(
    client,
    conn,
    conjunto: str | None = None,
    limit: int | None = None,
    poll_seconds: float = BATCH_POLL_SECONDS,
) -> Summary:
    """Processa as fichas pendentes em lotes, ou retoma a rodada em lote que não terminou."""
    rodada_id, sheets = open_round(conn)
    if rodada_id is not None:
        print(
            f"retomando a rodada {rodada_id}: {len(sheets)} fichas sem terminar "
            "(--conjunto e --limite valem para a próxima rodada)"
        )
    else:
        sheets = db.pending_sheets(conn, conjunto, limit)
        if not sheets:
            return Summary()
        rodada_id = db.start_round(conn, "batch")

    while requests := next_requests(conn, rodada_id, sheets):
        lote_id = db.pending_batch(conn, rodada_id)
        if lote_id is None:
            lote_id = send_batch(client, conn, rodada_id, requests)
        else:
            print(f"lote {lote_id} já enviado: esperando o resultado")
        try:
            results = wait_for_results(client, lote_id, poll_seconds)
        except anthropic.NotFoundError:
            # A API não tem mais o lote (os resultados ficam lá 29 dias). Nada dele foi salvo,
            # então os mesmos pedidos vão num lote novo.
            print(f"  o lote {lote_id} não existe mais na API: os pedidos vão de novo")
            with conn:
                db.set_batch_status(conn, lote_id, "perdido")
            continue
        save_results(conn, rodada_id, lote_id, requests, results)

    return round_summary(conn, rodada_id)
