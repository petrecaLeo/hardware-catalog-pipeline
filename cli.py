"""Linha de comando do pipeline: `.venv/bin/python cli.py <comando>`."""

import argparse
import sys
from pathlib import Path

import anthropic

from pipeline import db, extract
from pipeline.config import DB_FILE
from pipeline.fake_client import FakeAPI
from pipeline.sheets import SETS, load_sheets, sheets_path


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


def process(conn, conjunto: str | None, limit: int | None, fake: bool) -> None:
    client = FakeAPI().client if fake else anthropic.Anthropic()
    print(f"modo direto ({'API falsa' if fake else 'API real'})")
    summary = extract.process(client, conn, conjunto, limit)
    print(
        f"resumo: {summary.sheets} fichas | {summary.valid} válidas | {summary.failed} reprovadas"
        f" | {summary.calls} chamadas ({summary.retries} novas tentativas)"
        f" | custo total: ${summary.cost:.6f}"
    )


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
    process_parser.add_argument(
        "--conjunto", choices=(*SETS, "inbox"), help="só as fichas deste conjunto"
    )
    process_parser.add_argument("--limite", type=int, metavar="N", help="no máximo N fichas")
    process_parser.add_argument(
        "--fake", action="store_true", help="usa a API falsa: sem chave e sem custo"
    )

    args = parser.parse_args(argv)
    conn = db.connect(args.db)
    try:
        if args.command == "ingest":
            ingest(conn, args.paths)
        elif args.command == "process":
            process(conn, args.conjunto, args.limite, args.fake)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
