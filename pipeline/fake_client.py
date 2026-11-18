"""API falsa: o cliente do SDK de verdade, com um transporte que responde a partir dos gabaritos.

`FakeAPI().client` é um `anthropic.Anthropic` comum, só que os pedidos não saem do processo: o
`httpx2.MockTransport` responde no lugar da API. O pipeline usa o cliente sem saber que é falso, e
recebe os objetos do próprio SDK (`Message`, `ToolUseBlock`, `MessageBatch`...). Não precisa de
chave nem gasta nada.

Como responde:
- a ficha do pedido é o texto da última tag `<ficha>...</ficha>` na última mensagem do usuário que
  tem uma. Os exemplos não contam: o system não é lido, e o que estiver dentro de
  `<exemplo>...</exemplo>` é ignorado. Na correção, a ficha vem da primeira mensagem. O texto da
  tag tem que ser o da ficha (sem os espaços das pontas), que é como o banco guarda;
- o tipo de pedido sai da ferramenta forçada em `tool_choice`;
- `classificar_produto`: a categoria e o detalhe do gabarito;
- `extrair_<categoria>`: os campos do gabarito, com `duvidas: []`;
- ficha sem gabarito (inbox), sem a tag `<ficha>`, ou extração noutra categoria: uma resposta
  válida e neutra ('outro', "produto não identificado", campos null), para o `--fake` não quebrar;
- tokens repetíveis e plausíveis, proporcionais ao tamanho do pedido e da resposta;
- pedido que a API real recusaria (thinking ligado, prefill, tool_use sem tool_result, custom_id
  inválido ou repetido) volta com 400, como lá. Pedido sem ferramenta forçada também, porque a API
  falsa só sabe responder a ela. No lote, o pedido inválido volta como resultado `errored`.

Erros injetados, por id de ficha ('dev-007', 'hold-012'):
- `wrong_first`: a extração vem com `garantia_meses` = 999, que as regras pegam. A conversa de
  correção (com um `tool_result` com `is_error`) recebe o gabarito certo;
- `wrong_always`: `garantia_meses` = 999 sempre, mesmo na correção;
- `wrong_class_first`: a classificação vem com `categoria_detalhe` preenchido fora de 'outro' (ou
  null em 'outro'), que a regra pega. A conversa de correção recebe o gabarito certo;
- `cut`: a extração vem cortada (`stop_reason="max_tokens"`, sem `duvidas`) enquanto o
  `max_tokens` não passar de `EXTRACT_MAX_TOKENS`;
- `doubts`: `{id: (campo, nivel)}`, uma dúvida na extração;
- `errored` / `expired`: no lote, o primeiro pedido da ficha volta com esse resultado e o reenvio
  igual passa;
- `out_of_order`: os resultados do lote vêm na ordem inversa à dos pedidos;
- `in_progress_polls`: quantas consultas (`retrieve`) o lote fica 'in_progress' antes de 'ended'.
  O `results()` do SDK também faz uma consulta.

Os lotes ficam na memória do objeto: o id de um lote de outro `FakeAPI` dá 404.
"""

import json
import re
from datetime import UTC, datetime, timedelta

import httpx2
from anthropic import Anthropic, DefaultHttpxClient

from pipeline.config import EXTRACT_MAX_TOKENS
from pipeline.schemas import PRODUCT_MODELS
from pipeline.sheets import SETS, load_sheets, sheets_path, split_answer_key
from pipeline.tools import CLASSIFY_TOOL, EXTRACT_TOOLS

UNKNOWN = "produto não identificado"
# "Garantia vitalícia" lida como 999 meses: um erro que a faixa de garantia_meses pega em toda
# categoria.
WRONG_WARRANTY = 999
# Detalhe numa categoria que não é 'outro': a regra da classificação pega.
WRONG_DETAIL = "peça de computador"
DOUBT_REASON = "o texto traz mais de um valor possível para este campo"
CUSTOM_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}")
SHEET_TAG = re.compile(r"<ficha>(.*?)</ficha>", re.DOTALL)
EXAMPLE_TAG = re.compile(r"<exemplo>.*?</exemplo>", re.DOTALL)
API_URL = "https://api.anthropic.com"
BATCH_PATH = re.compile(r"/v1/messages/batches/([^/]+)(/results)?")
EXTRACT_TOOL_CATEGORY = {tool["name"]: category for category, tool in EXTRACT_TOOLS.items()}


class Rejected(Exception):
    """Um pedido que a API real recusaria com 400."""


class FakeAPI:
    def __init__(
        self,
        *,
        wrong_first=(),
        wrong_always=(),
        wrong_class_first=(),
        cut=(),
        doubts=None,
        errored=(),
        expired=(),
        out_of_order=False,
        in_progress_polls=0,
    ):
        self.wrong_first = set(wrong_first)
        self.wrong_always = set(wrong_always)
        self.wrong_class_first = set(wrong_class_first)
        self.cut = set(cut)
        self.doubts = dict(doubts or {})
        # Cada um acontece uma vez: o id sai do conjunto quando o erro é devolvido.
        self.errored = set(errored)
        self.expired = set(expired)
        self.out_of_order = out_of_order
        self.in_progress_polls = in_progress_polls

        sheets = [sheet for name in SETS for sheet in load_sheets(sheets_path(name))]
        self.sheets = {sheet["texto"].strip(): sheet for sheet in sheets}
        self.batches = {}
        self.answered = 0  # numera os ids das mensagens e dos blocos tool_use

        transport = httpx2.MockTransport(self._handle)
        self.client = Anthropic(
            api_key="chave-da-api-falsa",
            # Fixo: um ANTHROPIC_BASE_URL no ambiente mudaria os caminhos que o transporte espera.
            base_url=API_URL,
            http_client=DefaultHttpxClient(transport=transport),
            # Sem novas tentativas: um erro tem que chegar ao código como erro.
            max_retries=0,
        )

    # --- Transporte: o que seria a API.

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        method, path = request.method, request.url.path
        try:
            if method == "POST" and path == "/v1/messages":
                return httpx2.Response(200, json=self._answer(json.loads(request.content)))
            if method == "POST" and path == "/v1/messages/batches":
                body = json.loads(request.content)
                return httpx2.Response(200, json=self._create_batch(body["requests"]))
        except Rejected as error:
            return _error(400, "invalid_request_error", str(error))
        match = BATCH_PATH.fullmatch(path)
        if method == "GET" and match and match[1] in self.batches:
            batch = self.batches[match[1]]
            if match[2]:
                lines = "".join(_json(item) + "\n" for item in batch["results"])
                return httpx2.Response(200, content=lines.encode())
            return httpx2.Response(200, json=self._retrieve(batch))
        return _error(404, "not_found_error", f"API falsa: {method} {path} não existe")

    # --- Mensagens.

    def _answer(self, params: dict) -> dict:
        """A resposta de `messages.create`, como o JSON da API."""
        if problem := _request_problem(params):
            raise Rejected(f"API falsa: {problem}")
        tool = params["tool_choice"]["name"]
        sheet = self._find_sheet(params["messages"])
        if tool == CLASSIFY_TOOL["name"]:
            data = self._classification(sheet, params["messages"])
        elif tool in EXTRACT_TOOL_CATEGORY:
            data = self._extraction(EXTRACT_TOOL_CATEGORY[tool], sheet, params["messages"])
        else:
            raise Rejected(f"API falsa: só responde à classificação e às extrações, não a '{tool}'")
        stop_reason, output_tokens = "tool_use", 20 + len(_json(data)) * 6 // 5
        cut = sheet is not None and sheet["id"] in self.cut and tool != CLASSIFY_TOOL["name"]
        if cut and params["max_tokens"] <= EXTRACT_MAX_TOKENS:
            # Cortada logo antes das dúvidas, como aconteceu nas chamadas reais.
            del data["duvidas"]
            stop_reason, output_tokens = "max_tokens", params["max_tokens"]

        # Na ordem das chamadas reais: ~850 de entrada na classificação e ~1.900 na extração;
        # ~70 de saída na classificação e de 300 a 400 na extração.
        input_tokens = 350 + len(_json(params)) * 2 // 5
        self.answered += 1
        number = f"{self.answered:04d}"
        block = {"type": "tool_use", "id": f"toolu_fake_{number}", "name": tool, "input": data}
        return {
            "id": f"msg_fake_{number}",
            "type": "message",
            "role": "assistant",
            "model": params["model"],
            "content": [block],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            # A API real manda os campos de cache com 0 quando não há cache.
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }

    def _find_sheet(self, messages: list[dict]) -> dict | None:
        """A ficha com gabarito do pedido: a última tag <ficha> fora de <exemplo>, ou None.

        Procura da última mensagem do usuário para trás, porque na correção a última só tem o
        tool_result. Um exemplo nunca é confundido com a ficha, nem quando ela não tem gabarito.
        """
        for message in reversed(messages):
            if message["role"] != "user":
                continue
            text = EXAMPLE_TAG.sub("", "\n".join(_texts(message["content"])))
            if tags := SHEET_TAG.findall(text):
                return self.sheets.get(tags[-1].strip())
        return None

    def _classification(self, sheet: dict | None, messages: list[dict]) -> dict:
        if sheet is None:
            return {"categoria": "outro", "categoria_detalhe": UNKNOWN}
        data = split_answer_key(sheet["gabarito"])[0]
        if sheet["id"] in self.wrong_class_first and not _is_correction(messages):
            data["categoria_detalhe"] = None if data["categoria"] == "outro" else WRONG_DETAIL
        return data

    def _extraction(self, category: str, sheet: dict | None, messages: list[dict]) -> dict:
        if sheet is None or sheet["gabarito"]["categoria"] != category:
            fields = dict.fromkeys(PRODUCT_MODELS[category].model_fields)
            fields["nome"] = UNKNOWN.capitalize()
            if category == "outro":
                fields["categoria_detalhe"] = UNKNOWN
            return fields | {"duvidas": []}

        fields = split_answer_key(sheet["gabarito"])[1]
        sheet_id = sheet["id"]
        if sheet_id in self.wrong_always or (
            sheet_id in self.wrong_first and not _is_correction(messages)
        ):
            fields["garantia_meses"] = WRONG_WARRANTY
        doubts = []
        if sheet_id in self.doubts:
            field, level = self.doubts[sheet_id]
            doubts.append({"campo": field, "nivel": level, "motivo": DOUBT_REASON})
        return fields | {"duvidas": doubts}

    # --- Lotes.

    def _create_batch(self, requests: list[dict]) -> dict:
        custom_ids = [request["custom_id"] for request in requests]
        if bad := [custom_id for custom_id in custom_ids if not CUSTOM_ID.fullmatch(custom_id)]:
            raise Rejected(f"API falsa: custom_id {bad[0]!r} inválido; use ^[a-zA-Z0-9_-]{{1,64}}$")
        if len(set(custom_ids)) != len(custom_ids):
            raise Rejected("API falsa: custom_id repetido no lote")

        # As respostas saem já na criação; o lote só as entrega depois de 'ended'.
        results = [
            {"custom_id": request["custom_id"], "result": self._batch_result(request["params"])}
            for request in requests
        ]
        if self.out_of_order:
            results.reverse()
        batch = {
            "id": f"msgbatch_fake_{len(self.batches) + 1:04d}",
            "created_at": datetime.now(UTC),
            "results": results,
            "polls_left": self.in_progress_polls,
        }
        self.batches[batch["id"]] = batch
        return _batch_json(batch, ended=False)

    def _batch_result(self, params: dict) -> dict:
        sheet = self._find_sheet(params["messages"])
        sheet_id = sheet["id"] if sheet else None
        if sheet_id in self.errored:
            self.errored.discard(sheet_id)
            error = _error_body("api_error", "API falsa: erro interno simulado")
            return {"type": "errored", "error": error}
        if sheet_id in self.expired:
            self.expired.discard(sheet_id)
            return {"type": "expired"}
        try:
            return {"type": "succeeded", "message": self._answer(params)}
        except Rejected as error:
            return {"type": "errored", "error": _error_body("invalid_request_error", str(error))}

    def _retrieve(self, batch: dict) -> dict:
        if batch["polls_left"] > 0:
            batch["polls_left"] -= 1
            return _batch_json(batch, ended=False)
        return _batch_json(batch, ended=True)


def _request_problem(params: dict) -> str | None:
    """Por que a API real recusaria o pedido com 400, ou None."""
    if params.get("thinking") != {"type": "disabled"}:
        return "thinking: mande {'type': 'disabled'}; com ferramenta forçada, ligado dá 400"
    messages = params["messages"]
    if not messages or messages[-1]["role"] != "user":
        return "a conversa tem que terminar no usuário: o modelo não aceita prefill"
    for current, following in zip(messages, messages[1:]):
        asked = sorted(block["id"] for block in _blocks(current) if block["type"] == "tool_use")
        answered = sorted(
            block["tool_use_id"] for block in _blocks(following) if block["type"] == "tool_result"
        )
        if asked != answered:
            return "cada tool_use precisa do seu tool_result na mensagem seguinte"
    choice = params.get("tool_choice") or {}
    if choice.get("type") != "tool" or choice.get("name") not in [
        tool["name"] for tool in params.get("tools", [])
    ]:
        return "tool_choice: a API falsa só responde a uma ferramenta forçada que está em tools"
    return None


def _blocks(message: dict) -> list[dict]:
    return message["content"] if isinstance(message["content"], list) else []


def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    return [block["text"] for block in content if block["type"] == "text"]


def _is_correction(messages: list[dict]) -> bool:
    return any(
        block["type"] == "tool_result" and block.get("is_error")
        for message in messages
        if message["role"] == "user"
        for block in _blocks(message)
    )


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _error_body(kind: str, message: str) -> dict:
    return {"type": "error", "error": {"type": kind, "message": message}}


def _error(status: int, kind: str, message: str) -> httpx2.Response:
    return httpx2.Response(status, json=_error_body(kind, message))


def _batch_json(batch: dict, ended: bool) -> dict:
    counts = dict.fromkeys(("processing", "succeeded", "errored", "canceled", "expired"), 0)
    if ended:
        for item in batch["results"]:
            counts[item["result"]["type"]] += 1
    else:
        counts["processing"] = len(batch["results"])
    created = batch["created_at"]
    results_url = f"{API_URL}/v1/messages/batches/{batch['id']}/results"
    return {
        "id": batch["id"],
        "type": "message_batch",
        "processing_status": "ended" if ended else "in_progress",
        "request_counts": counts,
        "created_at": created.isoformat(),
        "expires_at": (created + timedelta(hours=24)).isoformat(),
        "ended_at": created.isoformat() if ended else None,
        "archived_at": None,
        "cancel_initiated_at": None,
        "results_url": results_url if ended else None,
    }
