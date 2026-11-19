"""API da tela: a revisão humana (a fila, a ficha com a extração, aprovar com edições ou
rejeitar), o catálogo, o resumo e o assistente da loja. A tela (web/) é servida na raiz, pelo mesmo
servidor.

O `{id}` das rotas é o id da ficha no banco (`fichas.id`), que também é o id do produto. O
`id_origem` ('dev-030', 'fonte.txt') vem junto nas respostas só para mostrar: ele não é único.

A decisão do revisor só vale para um produto da fila (`destino = 'revisao'`; fora dela, 409):
aprovar troca o destino para 'catalogo', rejeitar troca para 'rejeitado'. Os motivos não mudam,
e cada decisão vira uma linha em `revisoes`. O catálogo é sempre `destino = 'catalogo'`.

Aprovar valida os campos que vão para o catálogo (os editados ou, sem edição, os de agora) com o
esquema da categoria e as regras, sem a regra do US$: o revisor pode saber o preço em reais de uma
ficha só em dólar. Inválido → 422 com `{"detail": [{campo, mensagem}]}`. Todo 422 sai nesse
formato, também o do pedido fora do formato. A categoria não muda: um produto classificado na
categoria errada é rejeitado.

O assistente (`POST /api/chat`) responde a uma pergunta com o catálogo aprovado (assistant.py). O
histórico da conversa fica no navegador, que manda a pergunta e as trocas anteriores (só o texto:
pergunta e resposta), e o servidor refaz o laço a cada pergunta. O tamanho do histórico tem
limite, para um pedido não virar um gasto sem controle. A resposta traz o custo das chamadas. Sem
a chave da API, ele responde 503 com o que fazer; uma falha da API do modelo vira 502.

Só esta máquina acessa: o servidor escuta em 127.0.0.1 e recusa outro Host (um site que se passa
por 127.0.0.1 com DNS rebinding manda o nome dele no Host). Os POST só aceitam JSON: um formulário
de outro site aberto no navegador manda um POST sem perguntar nada, mas um POST em JSON para outra
origem passa antes pela permissão do CORS, que esta API não dá.

Uso: `create_app(db_file, client)` (`cli.py serve` roda em 127.0.0.1:8000). O `client` é o do
assistente (o real, se não vier). Cada pedido abre a sua conexão com o banco e fecha no fim.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Annotated

import anthropic
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pipeline import assistant, db, route
from pipeline.config import DB_FILE, ROOT_DIR
from pipeline.rules import validate_product
from pipeline.schemas import field_specs

HOST = "127.0.0.1"
PORT = 8000
ALLOWED_HOSTS = [HOST, "localhost"]
WEB_DIR = ROOT_DIR / "web"
# Os limites do pedido ao assistente. Cada pergunta manda o histórico inteiro ao modelo em todas as
# chamadas dela, então o tamanho do histórico é o que pesa no custo. A tela manda só as últimas
# MAX_HISTORY_TURNS trocas e limita a pergunta a MAX_QUESTION_CHARS.
MAX_HISTORY_TURNS = 6
MAX_QUESTION_CHARS = 500
MAX_ANSWER_CHARS = 3000
# Sem chave, o cliente real só falharia na hora da chamada (um TypeError, que viraria um 500).
NO_KEY = (
    "O assistente precisa da chave da API: configure a ANTHROPIC_API_KEY no .env ou rode o serve "
    "com --fake."
)


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # O produto inteiro, como ficou na tela (todos os campos da categoria). Sem ele, aprova os
    # campos como estão.
    campos: dict | None = None


Question = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)
]
AnswerText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_ANSWER_CHARS)
]


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pergunta: Question
    resposta: AnswerText


class Chat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pergunta: Question
    # As trocas anteriores, da mais antiga à mais nova.
    historico: list[Turn] = Field(default=[], max_length=MAX_HISTORY_TURNS)


def create_app(db_file: Path | str = DB_FILE, client=None) -> FastAPI:
    app = FastAPI(title="Catálogo de hardware")
    if client is None:
        # O cliente real do assistente. A chave só é pedida na hora de chamar o modelo.
        client = anthropic.Anthropic()

    @app.middleware("http")
    async def json_posts_only(request: Request, call_next):
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if request.method == "POST" and content_type != "application/json":
            return JSONResponse(
                {"detail": "Mande o pedido em JSON (Content-Type: application/json)."},
                status_code=415,
            )
        return await call_next(request)

    # Adicionado por último, roda antes de tudo: outro Host recebe 400. A porta não conta.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

    @app.exception_handler(RequestValidationError)
    async def malformed_request(request: Request, error: RequestValidationError):
        # O corpo ou o id fora do formato (só com bug na tela) sai como os erros dos campos. No
        # lugar do campo vai o caminho do erro ('body.campos', 'path.ficha_id'), que nunca é um
        # campo do formulário.
        detail = []
        for item in error.errors():
            where = ".".join(str(part) for part in item["loc"])
            message = f"{where}: pedido fora do formato ({item['msg']})."
            detail.append({"campo": where, "mensagem": message})
        return JSONResponse({"detail": detail}, status_code=422)

    def connection():
        conn = db.connect(db_file)
        try:
            yield conn
        finally:
            conn.close()

    def find_product(conn, ficha_id: int):
        row = db.review_product(conn, ficha_id)
        if row is None:
            raise HTTPException(404, f"Ficha {ficha_id} não encontrada ou ainda não processada.")
        return row

    def not_in_queue(ficha_id: int) -> HTTPException:
        return HTTPException(409, f"A ficha {ficha_id} não está na fila de revisão.")

    def decide(conn, ficha_id: int, acao: str, depois: dict | None = None) -> dict:
        if not db.decide(conn, ficha_id, acao, depois):
            raise not_in_queue(ficha_id)
        return {"id": ficha_id, "destino": db.DECISIONS[acao]}

    @app.get("/api/queue")
    def queue(conn=Depends(connection)):
        return [
            {
                "id": row["id"],
                "id_origem": row["id_origem"],
                "categoria": row["categoria"],
                "nome": json.loads(row["campos"]).get("nome"),
                "motivos": json.loads(row["motivos"]),
            }
            for row in db.products_in(conn, "revisao")
        ]

    @app.get("/api/sheets/{ficha_id}")
    def sheet(ficha_id: int, conn=Depends(connection)):
        row = find_product(conn, ficha_id)
        return {
            "id": row["id"],
            "id_origem": row["id_origem"],
            "conjunto": row["conjunto"],
            "texto": row["texto"],
            "categoria": row["categoria"],
            "campos": json.loads(row["campos"]),
            "duvidas": json.loads(row["duvidas"]),
            "extracao": row["extracao"],
            "erros": json.loads(row["erros"]),
            "destino": row["destino"],
            "motivos": json.loads(row["motivos"]),
            "formulario": field_specs(row["categoria"]),
            "tentativas": [dict(attempt) for attempt in db.sheet_attempts(conn, ficha_id)],
        }

    @app.post("/api/sheets/{ficha_id}/approve")
    def approve(ficha_id: int, body: Approval | None = None, conn=Depends(connection)):
        row = find_product(conn, ficha_id)
        if row["destino"] != "revisao":
            raise not_in_queue(ficha_id)
        before = json.loads(row["campos"])
        after = before if body is None or body.campos is None else body.campos
        if issues := validate_product(row["categoria"], after, None):
            raise HTTPException(
                422, [{"campo": issue.field, "mensagem": issue.message} for issue in issues]
            )
        return decide(conn, ficha_id, "aprovar", None if after == before else after)

    @app.post("/api/sheets/{ficha_id}/reject")
    def reject(ficha_id: int, conn=Depends(connection)):
        find_product(conn, ficha_id)
        return decide(conn, ficha_id, "rejeitar")

    @app.get("/api/catalog")
    def catalog(conn=Depends(connection)):
        return [
            {
                "id": row["id"],
                "id_origem": row["id_origem"],
                "categoria": row["categoria"],
                "campos": json.loads(row["campos"]),
            }
            for row in db.products_in(conn, "catalogo")
        ]

    @app.get("/api/summary")
    def summary(conn=Depends(connection)):
        counts = sum(route.counts_by_category(conn).values(), Counter())
        rounds = db.rounds(conn)
        last_round = None
        if rounds:
            last, totals = rounds[-1], db.round_totals(conn, rounds[-1]["id"])
            last_round = {
                "id": last["id"],
                "modo": last["modo"],
                "criado_em": last["criado_em"],
                "fichas": totals["fichas"],
                "chamadas": totals["chamadas"],
                "custo": totals["custo"],
            }
        return {
            "catalogo": counts["catalogo"],
            "fila": counts["revisao"],
            "fila_por_motivo": {reason: counts[reason] for reason in route.REASONS},
            "rejeitados": counts["rejeitado"],
            "custo": {"ultima_rodada": last_round, "total": db.total_cost(conn)},
        }

    @app.post("/api/chat")
    def chat(body: Chat, conn=Depends(connection)):
        if not (client.api_key or client.auth_token or client.credentials):
            raise HTTPException(503, NO_KEY)
        history = [(turn.pergunta, turn.resposta) for turn in body.historico]
        try:
            reply = assistant.answer(client, conn, body.pergunta, history)
        except anthropic.APIError as error:
            raise HTTPException(502, f"A API do modelo falhou: {error.message}") from error
        return {"resposta": reply.text, "chamadas": reply.calls, "custo": reply.cost}

    # A tela, por último: as rotas da API acima têm a preferência. "/" abre o index.html.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
