"""Linha de comando do pipeline: `.venv/bin/python cli.py <comando>`."""

import argparse
import sys
from collections import Counter
from pathlib import Path

import anthropic

from pipeline import batch, db, extract, route
from pipeline.config import BATCH_POLL_SECONDS, DB_FILE
from pipeline.fake_client import FakeAPI
from pipeline.sheets import SETS, load_sheets, sheets_path

MODE_NAMES = {"direct": "direto", "batch": "lote"}
REASON_NAMES = {"reprovado": "reprovado", "duvida_baixa": "dúvida baixa", "auditoria": "auditoria"}


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
    client = FakeAPI().client if fake else anthropic.Anthropic()
    api = "API falsa" if fake else "API real"
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
    columns = {"produtos": "produtos", "catalogo": "catálogo", "revisao": "revisão", **REASON_NAMES}
    print()
    print(f"{'categoria':<16}" + "".join(f"{name:>{len(name) + 2}}" for name in columns.values()))
    for category, counts in table.items():
        cells = "".join(f"{counts[key]:>{len(name) + 2}}" for key, name in columns.items())
        print(f"{category:<16}{cells}")
    print("(um produto na revisão pode ter mais de um motivo)")
    if unrouted := table["total"]["sem_destino"]:
        print(f"{unrouted} produtos ainda sem destino: rode `cli.py route`")


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
        "--fake", action="store_true", help="usa a API falsa: sem chave e sem custo"
    )

    commands.add_parser(
        "route",
        help="manda os produtos sem destino para o catálogo ou para a revisão "
        "(o process já faz isso no fim)",
    )
    commands.add_parser(
        "status", help="fichas pendentes, rodadas, custo e produtos por categoria e destino"
    )

    args = parser.parse_args(argv)
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
    finally:
        conn.close()


if __name__ == "__main__":
    main()
