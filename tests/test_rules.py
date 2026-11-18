import pytest

from pipeline.rules import Issue, validate_classification, validate_product
from tests.examples import EXAMPLES

SHEET = "Ficha do fornecedor. Preço à vista: R$ 999,00."

# Trocas que viram o SSD NVMe do exemplo em outro tipo de disco, e o water cooler em air cooler.
SATA = {
    "tipo": "ssd_sata",
    "formato": "2_5_pol",
    "pcie_geracao": None,
    "leitura_mb_s": 560,
    "escrita_mb_s": 530,
}
HD = {
    "tipo": "hd",
    "formato": "3_5_pol",
    "pcie_geracao": None,
    "leitura_mb_s": 270,
    "escrita_mb_s": 270,
    "rpm": 7200,
    "tbw": None,
}
AIR = {"tipo": "air_cooler", "altura_mm": 158, "radiador_mm": None, "ventoinhas_inclusas": 1}


def issues(category, changes, text=SHEET):
    """Os erros do exemplo da categoria (uma resposta da extração) com as trocas aplicadas."""
    return validate_product(category, {**EXAMPLES[category], **changes}, text, doubts=True)


def out_of_range(start):
    return f"{start}; confira a unidade e se o número não é de outro dado da ficha."


# Uma linha por regra: categoria, caso bom (de preferência um produto real perto do limite), caso
# ruim (o erro que a regra existe para pegar), campo e mensagem. As trocas valem sobre o exemplo.
RULES = [
    (
        "processador",
        {"nome": "Ryzen 5 8500G"},
        {"nome": "  "},
        "nome",
        "nome: está vazio; use o nome do produto que aparece na ficha.",
    ),
    (
        "outro",  # sem preço mínimo: só > 0
        {"preco": 4.9},  # cabo SATA
        {"preco": 0},
        "preco",
        "preco: tem que ser maior que zero; sem preço em reais, use null.",
    ),
    (
        "cooler",
        {"preco": 14.9},  # cooler simples
        {"preco": 1.299},  # R$ 1.299,00 lido como 1,299
        "preco",
        "preco: 1.299 é barato demais para uma peça (mínimo plausível: R$ 10); no formato "
        "brasileiro, R$ 1.299,90 = 1299.9.",
    ),
    (
        "fonte",
        {"garantia_meses": 144},  # 12 anos
        {"garantia_meses": 999},  # "vitalícia"
        "garantia_meses",
        out_of_range("garantia_meses: 999 meses está fora da faixa plausível (1 a 180 meses)"),
    ),
    (
        "processador",
        {"nucleos": 24, "threads": 24},  # Core Ultra 9 285K, sem Hyper-Threading
        {"threads": 4},
        "threads",
        "threads: 4 é menor que nucleos (8); cada núcleo tem pelo menos uma thread.",
    ),
    (
        "processador",
        {"nucleos": 96, "threads": 192},  # Threadripper
        {"threads": 32},  # i9-14900K: 32 threads, mas 8 são só os núcleos P
        "threads",
        "threads: 32 passa do dobro de nucleos (8); cada núcleo tem no máximo duas threads.",
    ),
    (
        "processador",
        {"clock_base_ghz": 0.8},  # E-core de chip de baixo consumo
        {"clock_base_ghz": 3800, "clock_boost_ghz": None},  # MHz
        "clock_base_ghz",
        out_of_range("clock_base_ghz: 3800 GHz está fora da faixa plausível (0.5 a 6.5 GHz)"),
    ),
    (
        "processador",
        {"clock_boost_ghz": 6.2},  # i9-14900KS
        {"clock_boost_ghz": 5500},
        "clock_boost_ghz",
        out_of_range("clock_boost_ghz: 5500 GHz está fora da faixa plausível (0.5 a 6.5 GHz)"),
    ),
    (
        "processador",
        {"clock_boost_ghz": 3.8},  # sem turbo, o boost é igual à base
        {"clock_boost_ghz": 3.2},
        "clock_boost_ghz",
        "clock_boost_ghz: 3.2 GHz é menor que clock_base_ghz (3.8 GHz); o boost é a frequência "
        "máxima e não fica abaixo da base.",
    ),
    (
        "processador",
        {"tdp_w": 350},  # Threadripper
        {"tdp_w": 1700},  # o número do LGA1700
        "tdp_w",
        out_of_range("tdp_w: 1700 W está fora da faixa plausível (5 a 400 W)"),
    ),
    (
        "placa_mae",
        {"memoria_max_gb": 2048},  # placa de workstation
        {"memoria_max_gb": 6000},  # DDR5-6000
        "memoria_max_gb",
        out_of_range("memoria_max_gb: 6000 GB está fora da faixa plausível (4 a 2048 GB)"),
    ),
    (
        "memoria_ram",
        {"capacidade_gb": 256, "modulos": 4},  # 4x64GB
        {"capacidade_gb": 32768},  # MB
        "capacidade_gb",
        out_of_range("capacidade_gb: 32768 GB está fora da faixa plausível (1 a 1024 GB)"),
    ),
    (
        "memoria_ram",
        {"capacidade_gb": 256, "modulos": 8},  # kit de Threadripper, 8x32GB
        {"modulos": 16},  # o 16 de "2x16GB"
        "modulos",
        out_of_range("modulos: 16 está fora da faixa plausível (1 a 8)"),
    ),
    (
        "memoria_ram",
        {"tipo": "ddr4", "frequencia_mhz": 2133},
        {"tipo": "ddr4", "frequencia_mhz": 1600},  # o clock real da DDR4-3200
        "frequencia_mhz",
        out_of_range(
            "frequencia_mhz: 1600 MHz está fora da faixa plausível para DDR4 (2133 a 5600 MHz)"
        ),
    ),
    (
        "placa_de_video",
        {"vram_gb": 96},  # RTX PRO 6000
        {"vram_gb": 8192},  # MB
        "vram_gb",
        out_of_range("vram_gb: 8192 GB está fora da faixa plausível (1 a 96 GB)"),
    ),
    (
        "placa_de_video",
        {"clock_boost_mhz": 3100},  # RX 9070 XT com overclock de fábrica
        {"clock_boost_mhz": 21000},  # o clock da memória
        "clock_boost_mhz",
        out_of_range("clock_boost_mhz: 21000 MHz está fora da faixa plausível (500 a 3500 MHz)"),
    ),
    (
        "placa_de_video",
        {"consumo_w": 575, "fonte_recomendada_w": 1000},  # RTX 5090
        {"consumo_w": 850, "fonte_recomendada_w": None},  # a fonte recomendada no campo errado
        "consumo_w",
        out_of_range("consumo_w: 850 W está fora da faixa plausível (15 a 700 W)"),
    ),
    (
        "placa_de_video",
        {"consumo_w": 30, "fonte_recomendada_w": 300},  # GT 1030
        {"consumo_w": None, "fonte_recomendada_w": 120},  # o consumo no campo errado
        "fonte_recomendada_w",
        out_of_range("fonte_recomendada_w: 120 W está fora da faixa plausível (250 a 1600 W)"),
    ),
    (
        "placa_de_video",
        {"consumo_w": 304, "fonte_recomendada_w": 750},  # RX 9070 XT
        {"consumo_w": 650},  # o mesmo número nos dois campos
        "consumo_w",
        "consumo_w: 650 W não é menor que fonte_recomendada_w (650 W); a fonte recomendada é "
        "para o PC inteiro, que gasta mais que a placa.",
    ),
    (
        "placa_de_video",
        {"comprimento_mm": 357.6},  # RTX 5090 das maiores
        {"comprimento_mm": 26.85},  # cm
        "comprimento_mm",
        out_of_range("comprimento_mm: 26.85 mm está fora da faixa plausível (100 a 400 mm)"),
    ),
    (
        "armazenamento",
        {"capacidade_gb": 8000},
        {"capacidade_gb": 2},  # TB
        "capacidade_gb",
        out_of_range("capacidade_gb: 2 GB está fora da faixa plausível (16 a 300000 GB)"),
    ),
    (
        "armazenamento",
        SATA,
        {**SATA, "formato": "m2", "pcie_geracao": 3},  # SSD M.2 SATA tratado como NVMe
        "pcie_geracao",
        "pcie_geracao: só existe em SSD NVMe; com tipo 'ssd_sata', fica null.",
    ),
    (
        "armazenamento",
        {**HD, "rpm": 5400},
        {**HD, "rpm": 256},  # o cache de 256 MB
        "rpm",
        out_of_range("rpm: 256 RPM está fora da faixa plausível (4200 a 15000 RPM)"),
    ),
    (
        "armazenamento",
        {**SATA, "tbw": 300},
        {**HD, "tbw": 550},  # a carga de trabalho do HD, em TB/ano
        "tbw",
        "tbw: é a durabilidade de SSD e fica null em HD; o 'TB/ano' do HD é a carga de trabalho "
        "anual, não o TBW.",
    ),
    (
        "armazenamento",
        {"pcie_geracao": 5, "leitura_mb_s": 14900, "escrita_mb_s": 14000},
        {"leitura_mb_s": 12400},  # velocidade de PCIe 5.0 num SSD PCIe 4.0
        "leitura_mb_s",
        out_of_range(
            "leitura_mb_s: 12400 MB/s está fora da faixa plausível para PCIe 4.0 (50 a 8000 MB/s)"
        ),
    ),
    (
        "armazenamento",
        {**HD, "leitura_mb_s": 554, "escrita_mb_s": 554},  # HD com dois atuadores
        {**SATA, "escrita_mb_s": 7},  # GB/s
        "escrita_mb_s",
        out_of_range("escrita_mb_s: 7 MB/s está fora da faixa plausível para SATA (50 a 600 MB/s)"),
    ),
    (
        "fonte",
        {"potencia_w": 120},  # pico PSU
        {"potencia_w": 80},  # o 80 de "80 Plus"
        "potencia_w",
        out_of_range("potencia_w: 80 W está fora da faixa plausível (100 a 2500 W)"),
    ),
    (
        "gabinete",
        {"ventoinhas_inclusas": 0},
        {"ventoinhas_inclusas": 120},  # o tamanho da ventoinha
        "ventoinhas_inclusas",
        out_of_range("ventoinhas_inclusas: 120 está fora da faixa plausível (0 a 12)"),
    ),
    (
        "cooler",
        {**AIR, "altura_mm": 37},  # low profile
        {**AIR, "altura_mm": 15.8},  # cm
        "altura_mm",
        out_of_range("altura_mm: 15.8 mm está fora da faixa plausível (25 a 200 mm)"),
    ),
    (
        "cooler",
        {"radiador_mm": 420, "ventoinha_mm": 140},
        {"radiador_mm": 36},  # cm
        "radiador_mm",
        out_of_range("radiador_mm: 36 mm está fora da faixa plausível (120 a 560 mm)"),
    ),
    (
        "cooler",
        AIR,
        {"altura_mm": 27},  # a espessura do radiador
        "altura_mm",
        "altura_mm: é a altura do air cooler e fica null no water cooler; as medidas do radiador "
        "não entram aqui.",
    ),
    (
        "cooler",
        {**AIR, "ventoinhas_inclusas": 0},  # passivo
        {"ventoinhas_inclusas": 120},
        "ventoinhas_inclusas",
        out_of_range("ventoinhas_inclusas: 120 está fora da faixa plausível (0 a 6)"),
    ),
    (
        "outro",
        {"categoria_detalhe": "pasta térmica"},
        {"categoria_detalhe": " "},
        "categoria_detalhe",
        "categoria_detalhe: está vazio; na categoria 'outro', diga em poucas palavras o que é o "
        "produto (ex.: 'hub USB').",
    ),
]


@pytest.mark.parametrize(
    ("category", "good", "bad", "field", "message"),
    RULES,
    ids=[f"{row[0]}-{row[3]}" for row in RULES],
)
def test_rule(category, good, bad, field, message):
    assert issues(category, good) == []
    assert issues(category, bad) == [Issue(field, message)]


DOLLAR_ISSUE = Issue(
    "preco",
    "preco: a ficha só tem preço em dólar, sem R$; o preço em reais fica null, sem converter.",
)


@pytest.mark.parametrize(
    "text",
    [
        "SSD Tecvana Raio 1TB. Preço sugerido: US$ 89.99",
        "SSD Tecvana Raio 1TB. USD 89.99",
        "SSD Tecvana Raio 1TB. USD89.99",
        "Tecvana Raio 1TB NVMe SSD. MSRP: $89.99",
        "SSD Tecvana Raio 1TB. U$ 89,99",
        "SSD Tecvana Raio 1TB por 89 dólares",
        "SSD Tecvana Raio 1TB, velocidades reais de 7000 MB/s. US$ 89.99",  # "reais" não é R$
    ],
)
def test_price_in_a_dollar_only_sheet(text):
    assert issues("armazenamento", {}, text) == [DOLLAR_ISSUE]
    # O certo nessa ficha é preço null.
    assert issues("armazenamento", {"preco": None}, text) == []


@pytest.mark.parametrize(
    "text",
    [
        "SSD Tecvana Raio 1TB por R$ 1.099,00",
        "Por R$ 459,90 (lá fora, US$ 89.99)",
        "Preço: BRL 459,90 (US$ 89.99)",
        "SSD Tecvana Raio 1TB NVMe, sem preço na ficha",
    ],
)
def test_price_with_reais_or_without_dollar_passes(text):
    assert issues("armazenamento", {}, text) == []


def test_human_edit_skips_the_sheet_rules():
    # Na tela, o revisor pode saber o preço em reais de uma ficha só em dólar: text=None.
    edit = {key: value for key, value in EXAMPLES["armazenamento"].items() if key != "duvidas"}
    assert validate_product("armazenamento", edit, None) == []
    # As outras regras continuam valendo.
    assert validate_product("armazenamento", {**edit, "capacidade_gb": 2}, None) == [
        Issue(
            "capacidade_gb",
            out_of_range("capacidade_gb: 2 GB está fora da faixa plausível (16 a 300000 GB)"),
        )
    ]


def test_doubts_flag_picks_the_model():
    answer_key = {key: value for key, value in EXAMPLES["processador"].items() if key != "duvidas"}
    # Gabarito e edição na tela: só os campos do produto.
    assert validate_product("processador", answer_key, SHEET) == []
    # A resposta da extração tem que trazer as dúvidas.
    assert validate_product("processador", answer_key, SHEET, doubts=True) == [
        Issue(
            "duvidas",
            "duvidas: falta o campo; todo campo vem na resposta, com null quando a ficha não traz "
            "o dado.",
        )
    ]


def without(category, field):
    return {key: value for key, value in EXAMPLES[category].items() if key != field}


@pytest.mark.parametrize(
    ("category", "data", "field", "message"),
    [
        (
            "processador",
            # O TDP de 1700 também está errado, mas as regras só rodam com o formato certo.
            {**EXAMPLES["processador"], "nucleos": "8", "tdp_w": 1700},
            "nucleos",
            "nucleos: tem que ser um número inteiro, sem texto nem unidade.",
        ),
        (
            "gabinete",
            without("gabinete", "cor"),
            "cor",
            "cor: falta o campo; todo campo vem na resposta, com null quando a ficha não traz o "
            "dado.",
        ),
        (
            "processador",
            {**EXAMPLES["processador"], "socket": "AM5"},
            "socket",
            "socket: esse campo não existe nesta categoria.",
        ),
        (
            "processador",
            {
                **EXAMPLES["processador"],
                "duvidas": [{"campo": "tdp_w", "nivel": "alta", "motivo": "?"}],
            },
            "duvidas.0.nivel",
            "duvidas.0.nivel: valor fora da lista; use um destes: 'media' ou 'baixa'.",
        ),
    ],
    ids=["text-in-number", "missing", "extra", "enum-in-doubt"],
)
def test_format_error_comes_alone_in_pt_br(category, data, field, message):
    assert validate_product(category, data, SHEET, doubts=True) == [Issue(field, message)]


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"categoria": "outro", "categoria_detalhe": "hub USB"}, []),
        ({"categoria": "placa_de_video", "categoria_detalhe": None}, []),
        (
            {"categoria": "outro", "categoria_detalhe": None},
            [
                Issue(
                    "categoria_detalhe",
                    "categoria_detalhe: está vazio; na categoria 'outro', diga em poucas palavras "
                    "o que é o produto (ex.: 'hub USB').",
                )
            ],
        ),
        (
            {"categoria": "placa_de_video", "categoria_detalhe": "placa de vídeo"},
            [
                Issue(
                    "categoria_detalhe",
                    "categoria_detalhe: só vale para a categoria 'outro'; em 'placa_de_video', "
                    "fica null.",
                )
            ],
        ),
        (
            {"categoria": "monitor", "categoria_detalhe": None},
            [
                Issue(
                    "categoria",
                    "categoria: valor fora da lista; use um destes: 'processador', 'placa_mae', "
                    "'memoria_ram', 'placa_de_video', 'armazenamento', 'fonte', 'gabinete', "
                    "'cooler' ou 'outro'.",
                )
            ],
        ),
    ],
    ids=[
        "outro-with-detail",
        "other-category",
        "outro-without-detail",
        "detail-without-outro",
        "unknown-category",
    ],
)
def test_classification(data, expected):
    assert validate_classification(data) == expected
