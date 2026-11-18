"""Roteamento: cada produto extraído vai para o catálogo ou para a fila de revisão humana.

Vai para a revisão, com estes motivos:
- 'reprovado': continuou com erro depois das tentativas. Os erros ficam em `produtos.erros`;
- 'duvida_baixa': o modelo deu dúvida 'baixa' num campo importante (IMPORTANT_FIELDS). A dúvida
  'media' não manda para a revisão: ela só aparece destacada na tela;
- 'auditoria': caiu no sorteio, entre as aprovadas automaticamente (sem os motivos acima) que nunca
  foram auditadas. A cota é de cada categoria (amostra estratificada): AUDIT_RATE das aprovadas
  automaticamente do banco inteiro, no mínimo 1, menos as que já foram auditadas. Assim, processar
  aos poucos (`--limite`, a inbox) dá a mesma taxa que processar tudo de uma vez. A semente é fixa:
  o mesmo banco dá sempre o mesmo sorteio.
O resto vai para o catálogo.

Roda no fim de cada `process` e sozinho (`cli.py route`), sempre só nos produtos ainda sem destino:
os que acabaram de ser extraídos. Um produto já roteado não é roteado de novo.
"""

import json
import random
from collections import Counter, defaultdict

from pipeline import db
from pipeline.schemas import CATEGORIES

REASONS = ("reprovado", "duvida_baixa", "auditoria")
AUDIT_RATE = 0.10
AUDIT_SEED = 2026

# Os campos que, errados, fazem o cliente comprar a peça errada: o preço, o que define o produto
# (tipo, capacidade, chip, núcleos, o que é o 'outro') e o que decide se ele funciona com as outras
# peças e na tomada (TDP, consumo, potência, bivolt, formato, medidas). Errar a cor, o RGB ou o
# ruído incomoda, mas não leva a uma compra errada; por isso a dúvida 'baixa' nesses outros campos
# só aparece na tela.
IMPORTANT_FIELDS = {
    "processador": {"preco", "nucleos", "tdp_w", "video_integrado"},
    "placa_mae": {"preco", "chipset", "formato"},
    "memoria_ram": {"preco", "tipo", "formato", "capacidade_gb"},
    "placa_de_video": {
        "preco",
        "chip",
        "vram_gb",
        "consumo_w",
        "fonte_recomendada_w",
        "comprimento_mm",
    },
    "armazenamento": {"preco", "tipo", "formato", "capacidade_gb"},
    "fonte": {"preco", "potencia_w", "bivolt"},
    "gabinete": {"preco", "tamanho"},
    "cooler": {"preco", "tipo", "altura_mm", "radiador_mm"},
    "outro": {"preco", "categoria_detalhe"},
}


def review_reasons(category: str, extraction: str, doubts: list[dict]) -> list[str]:
    """Os motivos para a revisão que saem da própria extração ([] = aprovada automaticamente)."""
    reasons = []
    if extraction == "reprovado":
        reasons.append("reprovado")
    important = IMPORTANT_FIELDS[category]
    if any(doubt["nivel"] == "baixa" and doubt["campo"] in important for doubt in doubts):
        reasons.append("duvida_baixa")
    return reasons


def audit_sample(
    candidates: list[tuple[int, str]], approved: Counter, audited: Counter
) -> set[int]:
    """Sorteia a auditoria entre os candidatos `(ficha_id, categoria)`.

    A cota de cada categoria é AUDIT_RATE das aprovadas automaticamente (`approved`), no mínimo 1,
    menos as que já foram auditadas (`audited`). Cada categoria tem o seu sorteio, com a semente
    fixa e as fichas em ordem: o resultado só depende dos candidatos e das contagens.
    """
    by_category = defaultdict(list)
    for ficha_id, category in candidates:
        by_category[category].append(ficha_id)
    chosen = set()
    for category, ids in by_category.items():
        quota = max(1, round(approved[category] * AUDIT_RATE)) - audited[category]
        size = min(max(quota, 0), len(ids))
        chosen.update(random.Random(f"{AUDIT_SEED}-{category}").sample(sorted(ids), size))
    return chosen


def tally(counts: Counter, destino: str | None, motivos: list[str]) -> None:
    """Conta um produto: o total, o destino ('sem_destino' antes do roteamento) e cada motivo."""
    counts.update(["produtos", destino or "sem_destino", *motivos])


def route(conn) -> Counter:
    """Roteia os produtos sem destino, num commit só, e devolve as contagens do que foi roteado."""
    products = db.unrouted_products(conn)
    reasons = {
        product["ficha_id"]: review_reasons(
            product["categoria"], product["extracao"], json.loads(product["duvidas"])
        )
        for product in products
    }
    # A cota da auditoria olha o banco inteiro: as aprovadas automaticamente (as de agora e as já
    # roteadas sem motivo além da auditoria) e as que já foram auditadas, por categoria.
    approved, audited = Counter(), Counter()
    for row in db.product_routes(conn):
        if row["destino"] is None:
            approved[row["categoria"]] += not reasons[row["ficha_id"]]
        else:
            approved[row["categoria"]] += set(json.loads(row["motivos"])) <= {"auditoria"}
        audited[row["categoria"]] += row["auditoria"]
    candidates = [
        (product["ficha_id"], product["categoria"])
        for product in products
        if not reasons[product["ficha_id"]] and not product["auditoria"]
    ]
    chosen = audit_sample(candidates, approved, audited)

    counts = Counter()
    with conn:
        for product in products:
            ficha_id = product["ficha_id"]
            motivos = reasons[ficha_id] + (["auditoria"] if ficha_id in chosen else [])
            destino = "revisao" if motivos else "catalogo"
            db.set_route(conn, ficha_id, destino, motivos, ficha_id in chosen)
            tally(counts, destino, motivos)
    return counts


def counts_by_category(conn) -> dict[str, Counter]:
    """As contagens de todos os produtos do banco, por categoria (todas, mesmo as vazias)."""
    table = {category: Counter() for category in CATEGORIES}
    for row in db.product_routes(conn):
        tally(table[row["categoria"]], row["destino"], json.loads(row["motivos"]))
    return table
