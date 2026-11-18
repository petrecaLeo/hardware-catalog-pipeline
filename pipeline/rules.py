"""Validação dos dados extraídos: primeiro o formato (Pydantic), depois os valores (regras).

O formato diz se os campos e os tipos estão certos. As regras dizem se os valores fazem sentido:
um TDP de 1700 W tem o formato certo e está errado (é o número do LGA1700).

Cada erro sai com o campo e uma mensagem curta em pt-BR, que começa pelo campo e diz o que está
errado e por quê. A mesma mensagem vai para o modelo no loop de correção e para a tela de revisão.

As regras miram os erros que acontecem de verdade: número que pertence a outro campo, unidade
trocada (cm no lugar de mm, MHz no lugar de GHz) e preço em dólar. As faixas cobrem com folga os
produtos reais, porque também valem para a edição humana na tela, e uma faixa apertada impediria
aprovar um produto legítimo.

Uso: `validate_product(categoria, dados, texto_da_ficha)` e `validate_classification(dados)`.
Os dois devolvem uma lista de `Issue`: lista vazia quer dizer válido.
"""

import re
from dataclasses import dataclass

from pydantic import ValidationError

from pipeline.schemas import MODELS, PRODUCT_MODELS, Classification


@dataclass(frozen=True)
class Issue:
    field: str
    message: str


# --- Formato: os erros do Pydantic, traduzidos pelo tipo do erro.

FORMAT_MESSAGES = {
    "missing": "falta o campo; todo campo vem na resposta, com null quando a ficha não traz o dado",
    "extra_forbidden": "esse campo não existe nesta categoria",
    "int_type": "tem que ser um número inteiro, sem texto nem unidade",
    "float_type": "tem que ser um número, sem texto nem unidade",
    "bool_type": "tem que ser true ou false",
    "string_type": "tem que ser um texto",
    "list_type": "tem que ser uma lista",
    "literal_error": "valor fora da lista; use um destes: {expected}",
}


def _format_issue(error) -> Issue:
    field = ".".join(str(part) for part in error["loc"])
    template = FORMAT_MESSAGES.get(error["type"])
    if template is None:
        text = f"formato inválido ({error['msg']})"
    else:
        # O Pydantic lista as opções do enum em inglês: "'a', 'b' or 'c'".
        expected = error.get("ctx", {}).get("expected", "").replace(" or ", " ou ")
        text = template.format(expected=expected)
    return Issue(field, f"{field}: {text}.")


def _format_issues(error: ValidationError) -> list[Issue]:
    return [_format_issue(item) for item in error.errors()]


# --- Valores: faixas plausíveis, (mínimo, máximo), com as pontas incluídas.

COMMON_RANGES = {
    # Fontes chegam a 12 anos (144). Pega "vitalícia" virando 999. Garantia vitalícia fica null.
    "garantia_meses": (1, 180),
}

RANGES = {
    "processador": {
        # O E-core de um chip de baixo consumo tem base de 0,8 GHz; o i9-14900KS chega a 6,2.
        # Pega MHz no lugar de GHz (3800).
        "clock_base_ghz": (0.5, 6.5),
        "clock_boost_ghz": (0.5, 6.5),
        # De chips de baixo consumo (6 W) ao Threadripper (350 W). Pega número de outro campo,
        # como o 1700 de LGA1700. Não dá para pegar os 253 W do turbo da Intel: o Threadripper
        # passa disso.
        "tdp_w": (5, 400),
    },
    "placa_mae": {
        # Placas de workstation (WRX90) aceitam 2 TB. Pega a frequência (DDR5-6000) no lugar.
        "memoria_max_gb": (4, 2048),
    },
    "memoria_ram": {
        # Pega MB no lugar de GB (32768).
        "capacidade_gb": (1, 1024),
        # Kits de Threadripper têm 8 pentes. Pega o 16 de "2x16GB".
        "modulos": (1, 8),
    },
    "placa_de_video": {
        # A RTX PRO 6000 tem 96 GB. Pega MB no lugar de GB (8192).
        "vram_gb": (1, 96),
        # Pega o clock da memória (21000) no lugar do clock do chip.
        "clock_boost_mhz": (500, 3500),
        # Da GT 710 (19 W) à RTX 5090 (575 W), com folga para modelos com overclock de fábrica.
        # Pega a fonte recomendada (850 W) no lugar do consumo.
        "consumo_w": (15, 700),
        # Pega o consumo da placa (120 W) no lugar da fonte do PC inteiro.
        "fonte_recomendada_w": (250, 1600),
        # A RTX 5090 mais longa tem uns 360 mm. Pega cm no lugar de mm (26.8).
        "comprimento_mm": (100, 400),
    },
    "armazenamento": {
        # Do SSD de 16 GB ao SSD de servidor de mais de 100 TB. Pega TB no lugar de GB (2).
        "capacidade_gb": (16, 300_000),
        # HD de notebook (4200) a HD de servidor (15000). Pega o cache (256 MB) no lugar.
        "rpm": (4200, 15000),
    },
    "fonte": {
        # Da pico PSU de 120 W às fontes de 2500 W. Pega o 80 de "80 Plus" no lugar da potência.
        "potencia_w": (100, 2500),
    },
    "gabinete": {
        # Pega o tamanho da ventoinha (120 mm) no lugar da quantidade.
        "ventoinhas_inclusas": (0, 12),
    },
    "cooler": {
        # Do low profile de 37 mm às torres de 170 mm. Pega cm no lugar de mm (15.5).
        "altura_mm": (25, 200),
        # AIO de 120 a 560 mm. Pega cm no lugar de mm (36).
        "radiador_mm": (120, 560),
        # 0 no cooler passivo, 4 no AIO de 480 mm. Pega o tamanho (120 mm) no lugar.
        "ventoinhas_inclusas": (0, 6),
    },
}

# Preço mínimo em reais, abaixo de qualquer peça de verdade (um cooler simples custa mais de
# R$ 15). Pega o formato brasileiro lido errado: "R$ 1.299,90" virando 1.2999. Não vale para
# 'outro', onde um cabo ou um adaptador custa menos que isso: lá, só preço > 0.
MIN_PRICE = 10

# Frequência da memória em MT/s (o número do DDR5-6000), por geração, da mais lenta vendida hoje
# à mais rápida. Pega o clock real, que é a metade (DDR4-3200 = 1600 MHz), e a geração errada.
RAM_MHZ = {"ddr3": (1066, 3200), "ddr4": (2133, 5600), "ddr5": (4000, 12000), None: (1066, 12000)}

# Velocidade máxima de cada interface em MB/s, com folga: nenhum disco passa disso. O mínimo, 50,
# pega GB/s no lugar de MB/s (7).
SATA_MB_S = 600
PCIE_MB_S = {3: 4000, 4: 8000, 5: 16000, 6: 32000}

# A ficha não tem preço em reais (R$, BRL) e tem sinal de dólar (US$, U$, USD, o "$" sozinho de
# uma ficha em inglês, dólar, dollar). A palavra "reais" não conta: "velocidades reais" é comum.
REAL_SIGN = re.compile(r"R\s?\$|\bBRL\b", re.IGNORECASE)
DOLLAR_SIGN = re.compile(r"\$|\bUSD|d[óo]ll?ar", re.IGNORECASE)

# A unidade sai do fim do nome do campo (clock_base_ghz → GHz).
UNITS = {
    "_mb_s": "MB/s",
    "_ghz": "GHz",
    "_mhz": "MHz",
    "_mm": "mm",
    "_w": "W",
    "_gb": "GB",
    "_meses": "meses",
    "rpm": "RPM",
}

MISSING_DETAIL = (
    "categoria_detalhe: está vazio; na categoria 'outro', diga em poucas palavras o que é o "
    "produto (ex.: 'hub USB')."
)


def _number(value):
    # O campo float guarda 3800 como 3800.0; na mensagem fica 3800.
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _with_unit(field, value):
    unit = next((unit for suffix, unit in UNITS.items() if field.endswith(suffix)), None)
    return f"{_number(value)} {unit}" if unit else f"{_number(value)}"


def _out_of_range(field, value, low, high, where=""):
    """O erro de valor fora da faixa, ou None. `where` diz de onde vem a faixa (' para DDR5')."""
    if value is None or low <= value <= high:
        return None
    return Issue(
        field,
        f"{field}: {_with_unit(field, value)} está fora da faixa plausível{where} "
        f"({low} a {_with_unit(field, high)}); confira a unidade e se o número não é de outro "
        "dado da ficha.",
    )


def _filled(*values):
    return all(value is not None for value in values)


def _product_rules(category, product, text):
    if not product.nome.strip():
        yield Issue("nome", "nome: está vazio; use o nome do produto que aparece na ficha.")
    if product.preco is not None:
        if product.preco <= 0:
            yield Issue("preco", "preco: tem que ser maior que zero; sem preço em reais, use null.")
        elif category != "outro" and product.preco < MIN_PRICE:
            yield Issue(
                "preco",
                f"preco: {_number(product.preco)} é barato demais para uma peça (mínimo "
                f"plausível: R$ {MIN_PRICE}); no formato brasileiro, R$ 1.299,90 = 1299.9.",
            )
        if text is not None and not REAL_SIGN.search(text) and DOLLAR_SIGN.search(text):
            yield Issue(
                "preco",
                "preco: a ficha só tem preço em dólar, sem R$; o preço em reais fica null, "
                "sem converter.",
            )
    for field, (low, high) in (COMMON_RANGES | RANGES.get(category, {})).items():
        if issue := _out_of_range(field, getattr(product, field), low, high):
            yield issue


def _cpu_rules(cpu):
    if _filled(cpu.nucleos, cpu.threads):
        if cpu.threads < cpu.nucleos:
            yield Issue(
                "threads",
                f"threads: {cpu.threads} é menor que nucleos ({cpu.nucleos}); cada núcleo tem "
                "pelo menos uma thread.",
            )
        elif cpu.threads > 2 * cpu.nucleos:
            # Pega só os núcleos P da Intel ("8P + 16E") em nucleos, com as threads do chip todo.
            yield Issue(
                "threads",
                f"threads: {cpu.threads} passa do dobro de nucleos ({cpu.nucleos}); cada núcleo "
                "tem no máximo duas threads.",
            )
    base, boost = cpu.clock_base_ghz, cpu.clock_boost_ghz
    if _filled(base, boost) and boost < base:
        yield Issue(
            "clock_boost_ghz",
            f"clock_boost_ghz: {_number(boost)} GHz é menor que clock_base_ghz ({_number(base)} "
            "GHz); o boost é a frequência máxima e não fica abaixo da base.",
        )


def _ram_rules(ram):
    low, high = RAM_MHZ[ram.tipo]
    where = f" para {ram.tipo.upper()}" if ram.tipo else ""
    if issue := _out_of_range("frequencia_mhz", ram.frequencia_mhz, low, high, where):
        yield issue


def _gpu_rules(gpu):
    power, psu = gpu.consumo_w, gpu.fonte_recomendada_w
    if _filled(power, psu) and power >= psu:
        yield Issue(
            "consumo_w",
            f"consumo_w: {power} W não é menor que fonte_recomendada_w ({psu} W); a fonte "
            "recomendada é para o PC inteiro, que gasta mais que a placa.",
        )


def _storage_rules(disk):
    # Pega o SSD M.2 SATA tratado como NVMe.
    if disk.tipo in ("ssd_sata", "hd") and disk.pcie_geracao is not None:
        yield Issue(
            "pcie_geracao",
            f"pcie_geracao: só existe em SSD NVMe; com tipo '{disk.tipo}', fica null.",
        )
    if disk.tipo == "hd" and disk.tbw is not None:
        yield Issue(
            "tbw",
            "tbw: é a durabilidade de SSD e fica null em HD; o 'TB/ano' do HD é a carga de "
            "trabalho anual, não o TBW.",
        )
    if disk.tipo in ("ssd_sata", "hd"):
        where, limit = " para SATA", SATA_MB_S
    elif disk.pcie_geracao in PCIE_MB_S:
        where, limit = f" para PCIe {disk.pcie_geracao}.0", PCIE_MB_S[disk.pcie_geracao]
    else:
        where, limit = "", max(PCIE_MB_S.values())
    for field in ("leitura_mb_s", "escrita_mb_s"):
        if issue := _out_of_range(field, getattr(disk, field), 50, limit, where):
            yield issue


def _cooler_rules(cooler):
    # Pega a espessura do radiador (27 mm) ou a altura da bomba no lugar da altura do air cooler.
    if cooler.tipo == "water_cooler" and cooler.altura_mm is not None:
        yield Issue(
            "altura_mm",
            "altura_mm: é a altura do air cooler e fica null no water cooler; as medidas do "
            "radiador não entram aqui.",
        )


def _other_rules(other):
    if not other.categoria_detalhe.strip():
        yield Issue("categoria_detalhe", MISSING_DETAIL)


# Coerência entre campos de cada categoria. Placa-mãe, fonte e gabinete só têm faixas.
CATEGORY_RULES = {
    "processador": _cpu_rules,
    "memoria_ram": _ram_rules,
    "placa_de_video": _gpu_rules,
    "armazenamento": _storage_rules,
    "cooler": _cooler_rules,
    "outro": _other_rules,
}


def validate_product(
    category: str, data: dict, text: str | None, *, doubts: bool = False
) -> list[Issue]:
    """Os erros de um produto da categoria.

    `text` é a ficha original, para as regras que dependem dela (hoje, só a do US$). Com
    `text=None` essas regras não rodam: é o caso da edição humana na tela, em que o revisor pode
    saber o preço em reais mesmo numa ficha só em dólar.

    `doubts=True` valida a resposta da extração, que traz `duvidas`. Sem ele, valida só os campos
    do produto (gabarito, edição na tela).

    Com erro de formato, só ele volta: as regras precisam dos tipos certos para rodar.
    """
    model = MODELS[category] if doubts else PRODUCT_MODELS[category]
    try:
        product = model.model_validate(data)
    except ValidationError as error:
        return _format_issues(error)
    issues = list(_product_rules(category, product, text))
    if category in CATEGORY_RULES:
        issues += CATEGORY_RULES[category](product)
    return issues


def validate_classification(data: dict) -> list[Issue]:
    """Os erros da classificação: o formato e a regra "'outro' se e somente se tiver detalhe"."""
    try:
        result = Classification.model_validate(data)
    except ValidationError as error:
        return _format_issues(error)
    if result.categoria == "outro" and not (result.categoria_detalhe or "").strip():
        return [Issue("categoria_detalhe", MISSING_DETAIL)]
    if result.categoria != "outro" and result.categoria_detalhe is not None:
        return [
            Issue(
                "categoria_detalhe",
                f"categoria_detalhe: só vale para a categoria 'outro'; em '{result.categoria}', "
                "fica null.",
            )
        ]
    return []
