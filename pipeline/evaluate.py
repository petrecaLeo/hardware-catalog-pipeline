"""Avaliação: compara o que o modelo extraiu com os gabaritos de um conjunto (dev ou holdout).

Mede o acerto por campo, por categoria e por estilo, a classificação (categoria certa), a
calibração das dúvidas, a qualidade da fila de revisão e o custo por ficha em cada modo. O
resultado sai em Markdown: no terminal e, com `cli.py eval --salvar`, em docs/.

Uso: `evaluate(conn, conjunto)` → `Report`; `markdown(conjunto, [medida, comparação...])`.
"""

import json
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from pipeline import db
from pipeline.config import DOCS_DIR, MODE_NAMES, MODEL
from pipeline.extract import EXAMPLE_SHEETS, split_doubts
from pipeline.schemas import CATEGORIES, PRODUCT_MODELS
from pipeline.sheets import load_sheets, sheets_path, split_answer_key

# As regras de comparação com o gabarito. Saem também no fim dos resultados.
RULES = """\
- `nome` não entra: cada loja escreve de um jeito, e o gabarito é só um jeito possível.
- `categoria_detalhe` e `conexao` são descrições livres ("monitor" ou "monitor gamer"): não entram
  no acerto e aparecem à parte, com cada diferença listada.
- Os outros textos (marca, chip, chipset, cor) entram, comparados sem diferença de caixa, acento e
  espaços: "GRAÚNA" = "Graúna", mas "white" ≠ "branco".
- Números, true/false e enums têm que ser iguais ao gabarito (1149 = 1149.0). null só bate com null.
- Categoria errada conta na classificação e também nos campos: campo do gabarito que a extração
  não tem é erro.
- As fichas usadas como exemplo no prompt ficam de fora."""
UNSCORED = {"nome"}
DESCRIPTIVE = ("categoria_detalhe", "conexao")
# Os campos de texto do esquema (str), comparados sem caixa, acento e espaços extras.
TEXT_FIELDS = {
    name
    for model in PRODUCT_MODELS.values()
    for name, info in model.model_fields.items()
    if info.annotation in (str, str | None)
}
MISSING = object()  # o campo do gabarito que a extração não tem
NO_DOUBT = "sem dúvida"
# Para onde o roteamento mandou a ficha: a fila (por um motivo da própria extração, como
# 'reprovado' ou 'duvida_baixa'), só a auditoria (sorteio entre as aprovadas) ou o catálogo.
ROUTES = {"fila": "fila", "auditoria": "auditoria", "catalogo": "catálogo"}
RESULTS_FILES = {"holdout": DOCS_DIR / "results.md", "dev": DOCS_DIR / "results-dev.md"}
# No arquivo salvo, o que vem desta linha até o fim é escrito à mão (as observações sobre a
# medida). O `eval --salvar` refaz os números e mantém esse trecho.
NOTES_MARK = "<!-- Escrito à mão daqui até o fim: o `cli.py eval --salvar` mantém este trecho. -->"


@dataclass
class Item:
    """Uma ficha avaliada: o gabarito e o que o pipeline fez com ela."""

    sheet_id: str
    style: str
    answer_key: dict  # o gabarito do jsonl: categoria, categoria_detalhe e os campos
    category: str  # a categoria em que a ficha foi extraída
    fields: dict  # os campos da última resposta da extração
    doubts: list[dict]
    reasons: list[str]  # os motivos do roteamento ([] = foi direto para o catálogo)


@dataclass
class Score:
    right: int = 0
    total: int = 0

    def add(self, right: int, total: int = 1) -> None:
        self.right += right
        self.total += total

    def __str__(self) -> str:
        if not self.total:
            return "—"
        percent = f"{100 * self.right / self.total:.1f}".replace(".", ",")
        return f"{self.right}/{self.total} ({percent}%)"


@dataclass
class Group:
    """Os números de um grupo de fichas: todas, uma categoria ou um estilo."""

    fields: Score = field(default_factory=Score)  # campos certos
    category: Score = field(default_factory=Score)  # fichas com a categoria certa
    clean: Score = field(default_factory=Score)  # fichas sem nenhum erro


@dataclass
class Error:
    sheet_id: str
    field: str
    expected: object
    got: object
    doubt: str
    route: str


@dataclass
class Report:
    total: Group = field(default_factory=Group)
    by_category: dict[str, Group] = field(default_factory=lambda: defaultdict(Group))
    by_style: dict[str, Group] = field(default_factory=lambda: defaultdict(Group))
    by_field: dict[str, Score] = field(default_factory=lambda: defaultdict(Score))
    by_doubt: dict[str, Score] = field(default_factory=lambda: defaultdict(Score))
    queue: Counter = field(default_factory=Counter)  # (tem erro?, rota) → fichas
    errors: list[Error] = field(default_factory=list)
    descriptive: dict[str, Score] = field(default_factory=lambda: defaultdict(Score))
    descriptive_diffs: list[Error] = field(default_factory=list)
    # Do banco, preenchidos por evaluate():
    missing: int = 0  # fichas do conjunto sem produto (não processadas): ficam de fora
    unrouted: int = 0  # produtos ainda sem destino: contam como catálogo
    costs: list = field(default_factory=list)  # db.costs_by_mode

    @property
    def mode(self) -> str:
        return " + ".join(MODE_NAMES[row["modo"]] for row in self.costs) or "—"


# --- A conta.


def normalize(text: str) -> str:
    """Sem caixa, sem acento e com um espaço só entre as palavras."""
    text = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(char for char in text if not unicodedata.combining(char)).split())


def same(name: str, expected, got) -> bool:
    if name in TEXT_FIELDS and isinstance(expected, str) and isinstance(got, str):
        return normalize(expected) == normalize(got)
    return expected == got


def route_of(reasons: list[str]) -> str:
    if set(reasons) - {"auditoria"}:
        return "fila"
    return "auditoria" if reasons else "catalogo"


def doubt_levels(doubts: list[dict]) -> dict[str, str]:
    """O nível de dúvida de cada campo. Com duas dúvidas no mesmo campo, vale a 'baixa'."""
    levels = {}
    for doubt in doubts:
        if levels.get(doubt["campo"]) != "baixa":
            levels[doubt["campo"]] = doubt["nivel"]
    return levels


def score(items: list[Item]) -> Report:
    """Compara cada ficha com o gabarito, pelas regras de RULES."""
    report = Report()
    for item in items:
        expected_category = item.answer_key["categoria"]
        levels = doubt_levels(item.doubts)
        route = route_of(item.reasons)
        errors = []
        if item.category != expected_category:
            errors.append(
                Error(item.sheet_id, "categoria", expected_category, item.category, NO_DOUBT, route)
            )
        right = total = 0
        for name, expected in split_answer_key(item.answer_key)[1].items():
            if name in UNSCORED:
                continue
            got = item.fields.get(name, MISSING)
            ok = same(name, expected, got)
            level = levels.get(name, NO_DOUBT)
            if name in DESCRIPTIVE:
                report.descriptive[name].add(ok)
                if not ok:
                    report.descriptive_diffs.append(
                        Error(item.sheet_id, name, expected, got, level, route)
                    )
                continue
            report.by_field[name].add(ok)
            report.by_doubt[level].add(ok)
            right += ok
            total += 1
            if not ok:
                errors.append(Error(item.sheet_id, name, expected, got, level, route))

        groups = (report.total, report.by_category[expected_category], report.by_style[item.style])
        for group in groups:
            group.fields.add(right, total)
            group.category.add(item.category == expected_category)
            group.clean.add(not errors)
        report.queue[bool(errors), route] += 1
        report.errors += errors
    return report


def load_items(conn, conjunto: str) -> tuple[list[Item], int, int]:
    """As fichas do conjunto que têm produto, sem as de exemplo; quantas ainda não têm produto; e
    quantos produtos estão sem destino."""
    sheets = {
        sheet["id"]: sheet
        for sheet in load_sheets(sheets_path(conjunto))
        if sheet["id"] not in EXAMPLE_SHEETS
    }
    items, unrouted = [], 0
    for row in db.evaluated_products(conn, conjunto):
        sheet = sheets.get(row["id_origem"])
        if sheet is None:  # exemplo do prompt
            continue
        fields, doubts = split_doubts(json.loads(row["resposta"] or "null"))
        unrouted += row["destino"] is None
        items.append(
            Item(
                row["id_origem"],
                sheet["estilo"],
                sheet["gabarito"],
                row["categoria"],
                fields,
                doubts,
                json.loads(row["motivos"]),
            )
        )
    return items, len(sheets) - len(items), unrouted


def evaluate(conn, conjunto: str) -> Report:
    """A avaliação dos produtos de um conjunto num banco, com o custo por modo."""
    items, missing, unrouted = load_items(conn, conjunto)
    report = score(items)
    report.missing, report.unrouted = missing, unrouted
    report.costs = db.costs_by_mode(conn, conjunto)
    return report


# --- O Markdown.

HEADERS = {
    "holdout": (
        "# Resultados da extração: prova\n\n"
        "Esta é a medida que vale. As fichas da prova (`data/holdout`) foram escritas antes de "
        "qualquer prompt e nunca foram usadas para ajustar nada. O conjunto de desenvolvimento, "
        "usado para ajustar os prompts, está em [results-dev.md](results-dev.md)."
    ),
    "dev": (
        "# Resultados da extração: desenvolvimento\n\n"
        "Conjunto usado para ajustar os prompts: os números daqui são otimistas. A medida que vale "
        "é a da prova, em [results.md](results.md)."
    ),
}


def show(value) -> str:
    if value is MISSING:
        return "(ausente)"
    return json.dumps(value, ensure_ascii=False).replace("|", "\\|")


def table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + " --- |" * len(headers)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def error_rows(errors: list[Error], with_route: bool = True) -> list[list]:
    rows = []
    for error in errors:
        row = [error.sheet_id, f"`{error.field}`", show(error.expected), show(error.got)]
        if with_route:
            row += ["—" if error.doubt == NO_DOUBT else error.doubt, ROUTES[error.route]]
        rows.append(row)
    return rows


def markdown(conjunto: str, reports: list[Report]) -> str:
    """Os resultados em Markdown. O primeiro relatório é a medida. Os outros (o mesmo conjunto
    processado noutro modo, noutro banco) só entram no resumo e no custo, para comparar."""
    main = reports[0]
    day = max((row["dia"] for row in main.costs), default="—")
    sheets = main.total.clean.total
    parts = [
        HEADERS[conjunto],
        f"Modelo `{MODEL}` | medida em {day} | {sheets} fichas | modo {main.mode}",
    ]
    if conjunto == "dev":
        examples = ", ".join(sorted(EXAMPLE_SHEETS))
        parts.append(f"Ficam de fora as fichas usadas como exemplo no prompt: {examples}.")
    if main.missing:
        parts.append(f"> Atenção: {main.missing} fichas do conjunto ainda sem produto.")
    if main.unrouted:
        parts.append(
            f"> Atenção: {main.unrouted} produtos sem destino contam como catálogo "
            "(rode `cli.py route`)."
        )

    summary = [
        [r.mode, r.total.clean.total, r.total.fields, r.total.category, r.total.clean]
        for r in reports
    ]
    costs = [
        [
            MODE_NAMES[row["modo"]],
            row["fichas"],
            row["chamadas"],
            f"${row['custo']:.6f}",
            f"${row['custo'] / row['fichas']:.6f}",
        ]
        for r in reports
        for row in r.costs
    ]
    # Os piores campos primeiro.
    fields = sorted(main.by_field.items(), key=lambda kv: (kv[1].right / kv[1].total, kv[0]))
    categories = [
        [name, group.clean.total, group.fields, group.category, group.clean]
        for name in CATEGORIES
        if (group := main.by_category.get(name))
    ]
    styles = [
        [name, group.clean.total, group.fields, group.clean]
        for name, group in sorted(main.by_style.items())
    ]
    doubts = [[level, main.by_doubt.get(level, Score())] for level in (NO_DOUBT, "media", "baixa")]
    descriptive = [[f"`{name}`", main.descriptive.get(name, Score())] for name in DESCRIPTIVE]
    parts += [
        "## Resumo",
        table(["modo", "fichas", "campos certos", "categoria certa", "fichas sem erro"], summary),
        "## Custo",
        "Todas as chamadas das fichas do conjunto (classificação, extração e correções), "
        "gravadas em `tentativas`. Uma ficha processada em duas rodadas conta duas vezes.",
        table(["modo", "fichas processadas", "chamadas", "custo total", "custo por ficha"], costs),
        "## Acerto por campo",
        table(["campo", "certos"], [[f"`{name}`", score] for name, score in fields]),
        "## Por categoria",
        table(["categoria", "fichas", "campos certos", "categoria certa", "fichas sem erro"],
              categories),
        "## Por estilo",
        table(["estilo", "fichas", "campos certos", "fichas sem erro"], styles),
        "## Calibração",
        "O acerto dos campos conforme a dúvida que o modelo marcou neles.",
        table(["dúvida", "campos certos"], doubts),
        *queue_section(main),
        "## Erros",
        table(["ficha", "campo", "gabarito", "extraído", "dúvida", "foi para"],
              error_rows(main.errors))
        if main.errors
        else "Nenhum.",
        "## Descrições livres (fora do acerto)",
        table(["campo", "iguais ao gabarito"], descriptive),
        table(["ficha", "campo", "gabarito", "extraído"],
              error_rows(main.descriptive_diffs, with_route=False))
        if main.descriptive_diffs
        else "Todas iguais.",
        "## Regras de comparação",
        RULES,
    ]
    return "\n\n".join(parts) + "\n"


def save(conjunto: str, text: str) -> Path:
    """Grava os resultados em docs/ no lugar dos antigos, mantendo o trecho escrito à mão."""
    path = RESULTS_FILES[conjunto]
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    if NOTES_MARK in old:
        text += "\n" + old[old.index(NOTES_MARK):]
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def queue_section(report: Report) -> list[str]:
    """A qualidade da fila: onde foram parar as fichas com erro e as sem erro."""
    queue = report.queue
    with_errors = sum(count for (has_error, _), count in queue.items() if has_error)
    escaped = defaultdict(list)
    for error in report.errors:
        if error.route == "catalogo":
            escaped[error.sheet_id].append(error.field)
    escaped_list = "; ".join(f"{sheet} ({', '.join(fields)})" for sheet, fields in escaped.items())
    bullets = [
        f"- Fichas com erro pegas pela fila: {queue[True, 'fila']} de {with_errors}; "
        f"pela auditoria: {queue[True, 'auditoria']}.",
        f"- Escaparam para o catálogo: {queue[True, 'catalogo']}"
        + (f": {escaped_list}." if escaped_list else "."),
        f"- Alarmes falsos (na fila sem erro): {queue[False, 'fila']}. "
        f"Auditoria sem erro (amostra): {queue[False, 'auditoria']}.",
    ]
    rows = [
        [label, *(queue[has_error, route] for route in ROUTES)]
        for label, has_error in (("com erro", True), ("sem erro", False))
    ]
    return [
        "## Fila de revisão",
        "Na fila = algum motivo da própria extração ('reprovado' ou 'duvida_baixa'). A auditoria "
        "é um sorteio entre as aprovadas e conta à parte.",
        table(["fichas", "na fila", "só na auditoria", "no catálogo"], rows),
        "\n".join(bullets),
    ]
