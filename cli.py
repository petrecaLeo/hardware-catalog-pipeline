"""Linha de comando do pipeline: `.venv/bin/python cli.py <comando>`."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import anthropic
import uvicorn

from api.main import HOST, PORT, create_app
from pipeline import batch, db, evaluate, extract, route
from pipeline.config import BATCH_POLL_SECONDS, CORRECTIONS_FILE, DB_FILE, MODE_NAMES
from pipeline.fake_client import FakeAPI
from pipeline.sheets import (
    SETS,
    UNKNOWN_STYLE,
    answer_key_issues,
    join_answer_key,
    load_sheets,
    sheets_path,
)

REASON_NAMES = {"reprovado": "reprovado", "duvida_baixa": "dúvida baixa", "auditoria": "auditoria"}
# O `--fake` simula alguns problemas, para a fila de revisão ter um exemplo de cada motivo, além
# da auditoria: uma garantia errada que a correção não conserta (reprovado) e dúvidas do modelo,
# 'baixa' em campo importante (vai para a fila) e 'media' (só aparece destacada na tela).
FAKE_PROBLEMS = {
    "wrong_always": {"dev-030"},
    "doubts": {
        "dev-005": {"tdp_w": "baixa"},
        "dev-019": {"consumo_w": "baixa", "comprimento_mm": "media"},
    },
}


def read_sheets(path: Path) -> tuple[str, list[tuple[str, str]]]:
    """O conjunto e as fichas `(id_origem, texto)` de um .jsonl ou de uma pasta de .txt.

    Só os arquivos com gabarito (data/dev e data/holdout) dão 'dev' e 'holdout': é lá que a
    avaliação procura o gabarito pelo id. Todo o resto é 'inbox'.
    """
    if path.is_dir():
        files = sorted(path.glob("*.txt"))
        return "inbox", [(file.name, file.read_text(encoding="utf-8")) for file in files]
    if not (path.is_file() and path.suffix == ".jsonl"):
        sys.exit(f"{path}: não é um arquivo .jsonl nem uma pasta de .txt")
    answer_key_sets = {sheets_path(name).resolve(): name for name in SETS}
    conjunto = answer_key_sets.get(path.resolve(), "inbox")
    return conjunto, [(sheet["id"], sheet["texto"]) for sheet in load_sheets(path)]


def ingest(conn, paths: list[Path]) -> None:
    total = new_total = 0
    for path in paths:
        conjunto, sheets = read_sheets(path)
        new = db.add_sheets(conn, conjunto, sheets)
        print(f"{path} ({conjunto}): {new} novas, {len(sheets) - new} já existiam")
        total += len(sheets)
        new_total += new
    print(f"total: {new_total} novas, {total - new_total} já existiam")


def process(conn, in_batch: bool, conjunto: str | None, limit: int | None, fake: bool) -> None:
    client = FakeAPI(**FAKE_PROBLEMS).client if fake else anthropic.Anthropic()
    api = "API real"
    if fake:
        simulated = sorted({*FAKE_PROBLEMS["wrong_always"], *FAKE_PROBLEMS["doubts"]})
        api = f"API falsa, com problemas simulados em {', '.join(simulated)}"
    if in_batch:
        print(f"modo lote ({api})")
        # A API falsa responde na hora: não há o que esperar entre as consultas.
        poll_seconds = 0 if fake else BATCH_POLL_SECONDS
        try:
            summary = batch.process(client, conn, conjunto, limit, poll_seconds)
        except KeyboardInterrupt:
            sys.exit("\ninterrompido: rode `process --lote` de novo para retomar o lote pendente")
    else:
        print(f"modo direto ({api})")
        summary = extract.process(client, conn, conjunto, limit)
    print(
        f"resumo: {summary.sheets} fichas | {summary.valid} válidas | {summary.failed} reprovadas"
        f" | {summary.calls} chamadas ({summary.retries} novas tentativas)"
        f" | custo total: ${summary.cost:.6f}"
    )
    print(route_line(route.route(conn)))


def route_line(counts) -> str:
    """`roteamento: 83 produtos | 74 no catálogo | 9 na revisão (0 reprovado, ...)`."""
    reasons = ", ".join(f"{counts[reason]} {REASON_NAMES[reason]}" for reason in route.REASONS)
    return (
        f"roteamento: {counts['produtos']} produtos | {counts['catalogo']} no catálogo"
        f" | {counts['revisao']} na revisão ({reasons})"
    )


def status(conn) -> None:
    """As fichas pendentes, as rodadas com o custo, e os produtos por categoria e destino."""
    print(f"fichas pendentes: {len(db.pending_sheets(conn))}")
    rounds = db.rounds(conn)
    print(f"rodadas: {len(rounds)}")
    total_cost = 0.0
    for rodada in rounds:
        totals = db.round_totals(conn, rodada["id"])
        total_cost += totals["custo"]
        print(
            f"  {rodada['id']} | {MODE_NAMES[rodada['modo']]} | {rodada['criado_em']}"
            f" | {totals['fichas']} fichas | {totals['chamadas']} chamadas"
            f" | ${totals['custo']:.6f}"
        )
    print(f"custo total gravado: ${total_cost:.6f}")

    table = route.counts_by_category(conn)
    table["total"] = sum(table.values(), Counter())
    columns = {
        "produtos": "produtos",
        "catalogo": "catálogo",
        "revisao": "revisão",
        "rejeitado": "rejeitado",
        **REASON_NAMES,
    }
    print()
    print(f"{'categoria':<16}" + "".join(f"{name:>{len(name) + 2}}" for name in columns.values()))
    for category, counts in table.items():
        cells = "".join(f"{counts[key]:>{len(name) + 2}}" for key, name in columns.items())
        print(f"{category:<16}{cells}")
    print("(os motivos contam só a fila de revisão; um produto pode ter mais de um)")
    if unrouted := table["total"]["sem_destino"]:
        print(f"{unrouted} produtos ainda sem destino: rode `cli.py route`")


def run_eval(conn, conjunto: str, compare: list[Path], save: bool) -> None:
    """A avaliação do banco (a medida) e, só para comparar, de outros bancos com o mesmo conjunto
    processado noutro modo. Mostra o Markdown e, com `save`, grava em docs/ (mantendo o trecho
    escrito à mão no fim do arquivo)."""
    reports = [evaluate.evaluate(conn, conjunto)]
    for path in compare:
        if not path.is_file():
            sys.exit(f"{path}: banco não encontrado")
        other = db.connect(path)
        try:
            reports.append(evaluate.evaluate(other, conjunto))
        finally:
            other.close()
    if not all(report.total.clean.total for report in reports):
        sys.exit(f"nenhum produto do conjunto {conjunto} num dos bancos: rode ingest e process")
    text = evaluate.markdown(conjunto, reports)
    print(text)
    if save:
        print(f"salvo em {evaluate.save(conjunto, text)}")


def export_corrections(conn, path: Path) -> None:
    """Cada ficha cuja última decisão na revisão foi aprovar com edição vira uma ficha com
    gabarito, no formato de data/dev/fichas.jsonl. O gabarito é a versão do humano, e a pegadinha
    diz o que ele mudou: é o erro que o caso pega.

    - Ficha nova (da inbox): id 'corr-' + o começo do hash do texto (o mesmo a cada exportação) e
      estilo 'desconhecido'. É um caso novo: a linha pode ser colada no fim do
      data/dev/fichas.jsonl, sem retoque.
    - Ficha que o dev já tem (o mesmo texto): o id e o estilo de lá ('dev-030'). Não é caso novo:
      serve para comparar com o gabarito de lá ou para escolher um exemplo.

    Ficam de fora as fichas da prova, pelo conjunto ou pelo texto (uma cópia que entrou pela
    inbox também): a correção delas no desenvolvimento contaminaria a medida. E a correção que,
    como gabarito, não passa nas regras com o texto da ficha: o revisor pode dar preço em reais a
    uma ficha só em dólar, mas no gabarito esse preço é null.
    """

    def shown(value):
        return json.dumps(value, ensure_ascii=False)

    dev = {sheet["texto"].strip(): sheet for sheet in load_sheets(sheets_path("dev"))}
    held_out_texts = {sheet["texto"].strip() for sheet in load_sheets(sheets_path("holdout"))}
    lines, held_out = [], 0
    for row in db.edited_approvals(conn):
        if row["conjunto"] == "holdout" or row["texto"] in held_out_texts:
            held_out += 1
            continue
        before, after = json.loads(row["antes"]), json.loads(row["depois"])
        changes = "; ".join(
            f"{field}: {shown(before.get(field))} → {shown(value)}"
            for field, value in after.items()
            if before.get(field) != value
        )
        known = dev.get(row["texto"])
        sheet = {
            "id": known["id"] if known else f"corr-{row['hash'][:8]}",
            "texto": row["texto"],
            "estilo": known["estilo"] if known else UNKNOWN_STYLE,
            "pegadinha": f"Corrigido na revisão humana: {changes}.",
            "gabarito": join_answer_key(row["categoria"], after),
        }
        if issues := answer_key_issues(sheet):
            print(
                f"{row['id_origem']}: pulada; como gabarito, a correção não passa nas regras com o"
                f" texto da ficha: {' '.join(issue.message for issue in issues)}"
            )
            continue
        lines.append(shown(sheet) + "\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")
    if held_out:
        print(
            f"{held_out} correções de fichas da prova (holdout) ficaram de fora: a prova não vira"
            " caso de desenvolvimento"
        )
    print(f"{len(lines)} correções exportadas para {path}")


def serve(db_file: Path, fake: bool) -> None:
    """A tela (revisão, catálogo e assistente), só para esta máquina (127.0.0.1)."""
    api = "API falsa" if fake else "API real"
    print(
        f"tela em http://{HOST}:{PORT} (banco: {db_file}; assistente com a {api}; Ctrl-C para"
        " parar)"
    )
    client = FakeAPI().client if fake else None
    uvicorn.run(create_app(db_file, client), host=HOST, port=PORT)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Pipeline de entrada de catálogo de hardware.")
    parser.add_argument(
        "--db", type=Path, default=DB_FILE, help="arquivo do banco (padrão: data/catalog.db)"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    ingest_parser = commands.add_parser("ingest", help="guarda as fichas no banco, sem repetir")
    ingest_parser.add_argument(
        "paths", nargs="+", type=Path, metavar="caminho", help="arquivo .jsonl ou pasta de .txt"
    )

    process_parser = commands.add_parser("process", help="extrai e valida as fichas pendentes")
    mode = process_parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--direto", action="store_true", help="uma chamada por vez, com a correção na hora"
    )
    mode.add_argument(
        "--lote",
        action="store_true",
        help="pela API de lote: metade do preço, pode levar horas; retoma o lote pendente",
    )
    process_parser.add_argument(
        "--conjunto", choices=(*SETS, "inbox"), help="só as fichas deste conjunto"
    )
    process_parser.add_argument("--limite", type=int, metavar="N", help="no máximo N fichas")
    process_parser.add_argument(
        "--fake",
        action="store_true",
        help="usa a API falsa: sem chave e sem custo, com alguns problemas simulados para a fila",
    )

    commands.add_parser(
        "route",
        help="manda os produtos sem destino para o catálogo ou para a revisão "
        "(o process já faz isso no fim)",
    )
    commands.add_parser(
        "status", help="fichas pendentes, rodadas, custo e produtos por categoria e destino"
    )

    eval_parser = commands.add_parser(
        "eval", help="compara os produtos de um conjunto com os gabaritos: acerto, fila e custo"
    )
    eval_parser.add_argument("--conjunto", choices=SETS, required=True)
    eval_parser.add_argument(
        "--comparar",
        type=Path,
        action="append",
        default=[],
        metavar="BANCO",
        help="outro banco com o mesmo conjunto processado noutro modo: entra no resumo e no custo",
    )
    eval_parser.add_argument(
        "--salvar",
        action="store_true",
        help="grava em docs/results.md (holdout) ou docs/results-dev.md (dev)",
    )

    export_parser = commands.add_parser(
        "export-corrections",
        help="as fichas aprovadas com edição viram fichas com gabarito, no formato do dev "
        "(as da prova ficam de fora)",
    )
    export_parser.add_argument(
        "--saida",
        type=Path,
        default=CORRECTIONS_FILE,
        metavar="ARQUIVO",
        help="arquivo .jsonl gerado (padrão: data/corrections.jsonl)",
    )

    serve_parser = commands.add_parser(
        "serve",
        help=f"abre a tela (revisão, catálogo e assistente) em http://{HOST}:{PORT}, só nesta"
        " máquina",
    )
    serve_parser.add_argument(
        "--fake",
        action="store_true",
        help="o assistente usa a API falsa: sem chave e sem custo, mas não lê a pergunta",
    )

    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(args.db, args.fake)
        return
    conn = db.connect(args.db)
    try:
        if args.command == "ingest":
            ingest(conn, args.paths)
        elif args.command == "process":
            process(conn, args.lote, args.conjunto, args.limite, args.fake)
        elif args.command == "route":
            print(route_line(route.route(conn)))
        elif args.command == "status":
            status(conn)
        elif args.command == "eval":
            run_eval(conn, args.conjunto, args.comparar, args.salvar)
        elif args.command == "export-corrections":
            export_corrections(conn, args.saida)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
