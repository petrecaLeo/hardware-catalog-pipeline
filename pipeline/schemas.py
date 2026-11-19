"""Modelos dos produtos, um por categoria: a fonte única da verdade.

Daqui saem os moldes das ferramentas (tools.py) e a validação de formato das respostas.

O que o strict pede destes modelos:
- todo campo é obrigatório e aceita null, menos os que nunca podem faltar (nome e, no
  'outro', categoria_detalhe). Assim o modelo não precisa inventar valor e os campos saem
  na ordem daqui, com as dúvidas por último;
- valores de enum minúsculos com "_", porque o strict não garante a caixa.

A unidade vai no nome do campo (consumo_w, comprimento_mm) e a descrição diz o que o valor é e,
quando o texto costuma confundir, o que ele não é. É a descrição que orienta o modelo.
O `title` é o rótulo do campo na tela ("Garantia (meses)"). Ele não vai para os moldes das
ferramentas (tools.py tira todo "title"), então mudar um rótulo não muda o pedido ao modelo.
As faixas de valor (TDP plausível, preço > 0...) ficam nas regras, não aqui.
"""

from types import NoneType
from typing import Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, create_model


class StrictModel(BaseModel):
    # strict: "8" (texto) não vira 8; é erro de formato. forbid: campo a mais também é erro.
    model_config = ConfigDict(strict=True, extra="forbid")


class Product(StrictModel):
    """Campos comuns a todas as categorias."""

    nome: str = Field(
        title="Nome", description="Nome do produto para a vitrine, sem preço nem frases de venda."
    )
    marca: str | None = Field(
        title="Marca",
        description=(
            "Marca do produto. Costuma vir no nome, logo depois do tipo do produto. "
            "Na placa de vídeo, a marca da placa, não a do chip."
        ),
    )
    preco: float | None = Field(
        title="Preço (R$)",
        description=(
            "Preço à vista em reais (R$), só o número (R$ 1.299,90 = 1299.9). "
            "Em 'de/por', o 'por'; se só houver parcelas, o total. "
            "null se não houver preço em reais (ex.: só US$)."
        ),
    )
    garantia_meses: int | None = Field(
        title="Garantia (meses)", description="Garantia em meses (1 ano = 12)."
    )


class Cpu(Product):
    nucleos: int | None = Field(title="Núcleos", description="Número de núcleos (cores).")
    threads: int | None = Field(title="Threads", description="Número de threads.")
    clock_base_ghz: float | None = Field(
        title="Clock base (GHz)", description="Frequência base em GHz."
    )
    clock_boost_ghz: float | None = Field(
        title="Clock boost (GHz)", description="Frequência máxima (boost/turbo) em GHz."
    )
    cache_l3_mb: int | None = Field(
        title="Cache L3 (MB)", description="Cache L3 em MB, sem somar o L2."
    )
    tdp_w: int | None = Field(
        title="TDP (W)",
        description=(
            "TDP (potência base) em watts: o que o processador gasta. Não é o pico do turbo "
            "nem o que a placa-mãe ou o cooler suportam."
        ),
    )
    video_integrado: bool | None = Field(
        title="Vídeo integrado", description="Tem vídeo integrado?"
    )
    cooler_incluso: bool | None = Field(
        title="Cooler incluso", description="Vem com cooler na caixa?"
    )


class Motherboard(Product):
    chipset: str | None = Field(
        title="Chipset", description="Chipset, só o código (ex.: 'B650', 'Z790')."
    )
    formato: Literal["atx", "micro_atx", "mini_itx", "e_atx"] | None = Field(
        title="Formato", description="Formato (tamanho) da placa."
    )
    slots_memoria: int | None = Field(
        title="Slots de memória", description="Quantidade de slots de memória RAM."
    )
    memoria_max_gb: int | None = Field(
        title="Memória máxima (GB)", description="Memória RAM máxima que a placa aceita, em GB."
    )
    slots_m2: int | None = Field(title="Slots M.2", description="Quantidade de slots M.2 para SSD.")
    portas_sata: int | None = Field(title="Portas SATA", description="Quantidade de portas SATA.")
    wifi: bool | None = Field(title="Wi-Fi", description="Tem Wi-Fi integrado?")
    bluetooth: bool | None = Field(title="Bluetooth", description="Tem Bluetooth integrado?")


class Ram(Product):
    tipo: Literal["ddr3", "ddr4", "ddr5"] | None = Field(
        title="Tipo", description="Geração da memória."
    )
    formato: Literal["dimm", "sodimm"] | None = Field(
        title="Formato", description="'dimm' para desktop, 'sodimm' para notebook."
    )
    capacidade_gb: int | None = Field(
        title="Capacidade (GB)", description="Capacidade total do kit em GB (2x16GB = 32)."
    )
    modulos: int | None = Field(
        title="Módulos", description="Quantidade de pentes no kit (2x16GB = 2)."
    )
    frequencia_mhz: int | None = Field(
        title="Frequência (MHz)", description="Frequência em MHz (DDR5-6000 ou 6000 MT/s = 6000)."
    )
    latencia_cl: int | None = Field(
        title="Latência (CL)", description="Latência CAS, só o número (CL30 = 30)."
    )
    tensao_v: float | None = Field(title="Tensão (V)", description="Tensão em volts (ex.: 1.35).")
    rgb: bool | None = Field(title="RGB", description="Tem iluminação RGB?")


class Gpu(Product):
    chip: str | None = Field(
        title="Chip",
        description="Chip gráfico, sem a marca da placa (ex.: 'RTX 5070', 'RX 9070 XT').",
    )
    fabricante_chip: Literal["nvidia", "amd", "intel"] | None = Field(
        title="Fabricante do chip", description="Quem fabrica o chip gráfico."
    )
    vram_gb: int | None = Field(title="VRAM (GB)", description="Memória de vídeo em GB.")
    tipo_vram: Literal["gddr5", "gddr6", "gddr6x", "gddr7"] | None = Field(
        title="Tipo de VRAM", description="Tipo da memória de vídeo."
    )
    clock_boost_mhz: int | None = Field(
        title="Clock boost (MHz)", description="Clock boost do chip em MHz."
    )
    consumo_w: int | None = Field(
        title="Consumo (W)",
        description=(
            "Consumo da placa em watts (TGP/TBP): o que ela gasta. "
            "Não é a fonte recomendada nem o que o conector aguenta."
        ),
    )
    fonte_recomendada_w: int | None = Field(
        title="Fonte recomendada (W)",
        description="Potência de fonte recomendada para o PC, em watts. Não é o consumo da placa.",
    )
    comprimento_mm: float | None = Field(
        title="Comprimento (mm)", description="Comprimento da placa em mm (26,8 cm = 268)."
    )


class Storage(Product):
    tipo: Literal["ssd_nvme", "ssd_sata", "hd"] | None = Field(
        title="Tipo", description="Tipo do disco."
    )
    formato: Literal["m2", "2_5_pol", "3_5_pol"] | None = Field(
        title="Formato", description="Formato físico: M.2, 2,5 ou 3,5 polegadas."
    )
    pcie_geracao: int | None = Field(
        title="Geração do PCIe", description="Geração do PCIe do SSD NVMe (PCIe 4.0 = 4)."
    )
    capacidade_gb: int | None = Field(
        title="Capacidade (GB)", description="Capacidade em GB (1 TB = 1000)."
    )
    leitura_mb_s: int | None = Field(
        title="Leitura (MB/s)", description="Leitura sequencial máxima em MB/s."
    )
    escrita_mb_s: int | None = Field(
        title="Escrita (MB/s)", description="Escrita sequencial máxima em MB/s."
    )
    rpm: int | None = Field(title="Rotação (RPM)", description="Rotação do HD em RPM.")
    tbw: int | None = Field(
        title="Durabilidade (TBW)", description="Durabilidade em TBW (terabytes escritos)."
    )


class PowerSupply(Product):
    potencia_w: int | None = Field(
        title="Potência (W)",
        description="Potência nominal (contínua) em watts: o que a fonte entrega. Não é o pico.",
    )
    certificacao: (
        Literal[
            "nenhuma",
            "80_plus",
            "80_plus_bronze",
            "80_plus_silver",
            "80_plus_gold",
            "80_plus_platinum",
            "80_plus_titanium",
        ]
        | None
    ) = Field(
        title="Certificação",
        description=(
            "Selo 80 Plus ('80_plus' é o selo branco). 'nenhuma' se o texto diz que não tem."
        ),
    )
    modularidade: Literal["nao_modular", "semi_modular", "modular"] | None = Field(
        title="Modularidade", description="Cabos fixos, só parte removível ou todos removíveis."
    )
    pfc_ativo: bool | None = Field(title="PFC ativo", description="Tem PFC ativo?")
    bivolt: bool | None = Field(
        title="Bivolt", description="Aceita 110 V e 220 V sem chave seletora?"
    )
    conector_12v_2x6: bool | None = Field(
        title="Conector 12V-2x6",
        description="Tem conector 12V-2x6 ou 12VHPWR (16 pinos) para a placa de vídeo?",
    )
    ventoinha_mm: int | None = Field(
        title="Ventoinha (mm)", description="Tamanho da ventoinha em mm."
    )


class Case(Product):
    tamanho: Literal["full_tower", "mid_tower", "mini_tower", "sff"] | None = Field(
        title="Tamanho", description="Tamanho do gabinete ('sff' é o compacto)."
    )
    cor: str | None = Field(title="Cor", description="Cor principal, em minúsculas (ex.: 'preto').")
    lateral_vidro: bool | None = Field(
        title="Lateral de vidro", description="Tem lateral de vidro temperado?"
    )
    ventoinhas_inclusas: int | None = Field(
        title="Ventoinhas inclusas",
        description="Quantas ventoinhas já vêm instaladas. Não é quantas o gabinete suporta.",
    )
    rgb: bool | None = Field(title="RGB", description="Tem iluminação RGB?")
    usb_c_frontal: bool | None = Field(
        title="USB-C frontal", description="Tem USB-C no painel frontal?"
    )


class Cooler(Product):
    tipo: Literal["air_cooler", "water_cooler"] | None = Field(
        title="Tipo", description="Air cooler (dissipador com ventoinha) ou water cooler (AIO)."
    )
    altura_mm: float | None = Field(
        title="Altura (mm)", description="Altura do air cooler em mm (15,5 cm = 155)."
    )
    radiador_mm: int | None = Field(
        title="Radiador (mm)",
        description="Tamanho do radiador do water cooler em mm (ex.: 240, 360).",
    )
    ventoinhas_inclusas: int | None = Field(
        title="Ventoinhas inclusas", description="Quantas ventoinhas vêm com o cooler."
    )
    ventoinha_mm: int | None = Field(
        title="Ventoinha (mm)", description="Tamanho das ventoinhas em mm."
    )
    rpm_max: int | None = Field(
        title="Rotação máxima (RPM)", description="Rotação máxima das ventoinhas em RPM."
    )
    ruido_dba: float | None = Field(title="Ruído (dBA)", description="Ruído máximo em dB(A).")
    rgb: bool | None = Field(title="RGB", description="Tem iluminação RGB?")


class Other(Product):
    categoria_detalhe: str = Field(
        title="O que é",
        description="O que é o produto, em poucas palavras (ex.: 'hub USB', 'pasta térmica').",
    )
    cor: str | None = Field(title="Cor", description="Cor principal, em minúsculas (ex.: 'preto').")
    conexao: str | None = Field(
        title="Conexão", description="Como liga ao PC (ex.: 'USB-C', 'Bluetooth')."
    )


def _with_doubts(fields: type[Product]) -> type[Product]:
    """O modelo da extração: os campos da categoria e, por último, as dúvidas sobre eles.

    É montado aqui porque o enum de `campo` sai dos campos de cada categoria.
    """
    doubt = create_model(
        f"{fields.__name__}Doubt",
        __base__=StrictModel,
        campo=(Literal[tuple(fields.model_fields)], Field(description="O campo com dúvida.")),
        nivel=(
            Literal["media", "baixa"],
            Field(
                description=(
                    "'media': o valor é provável, mas o texto é ambíguo; "
                    "'baixa': o valor pode estar errado."
                )
            ),
        ),
        motivo=(str, Field(description="Por que há dúvida, em poucas palavras.")),
    )
    return create_model(
        f"{fields.__name__}Extraction",
        __base__=fields,
        duvidas=(
            list[doubt],
            Field(
                description=(
                    "Os campos em que você não tem certeza do valor, um item por campo. Lista "
                    "vazia quando o texto deixa tudo claro. Campo que o texto não traz fica null "
                    "e não é dúvida."
                )
            ),
        ),
    )


# A versão dos campos acima. Cada produto do banco guarda a versão com que foi extraído; ela sobe
# quando os campos mudam, para saber quais produtos refazer.
SCHEMA_VERSION = 1

# Os campos de cada categoria, sem dúvidas: é o formato do gabarito e da edição na tela.
PRODUCT_MODELS: dict[str, type[Product]] = {
    "processador": Cpu,
    "placa_mae": Motherboard,
    "memoria_ram": Ram,
    "placa_de_video": Gpu,
    "armazenamento": Storage,
    "fonte": PowerSupply,
    "gabinete": Case,
    "cooler": Cooler,
    "outro": Other,
}
# O modelo da extração de cada categoria: os campos e as dúvidas. É o que valida a resposta.
MODELS = {category: _with_doubts(fields) for category, fields in PRODUCT_MODELS.items()}
CATEGORIES = tuple(MODELS)


class Classification(StrictModel):
    categoria: Literal[CATEGORIES] = Field(
        description=(
            "Tipo do produto. 'armazenamento' é SSD ou HD; 'fonte' é a fonte de alimentação do "
            "PC; 'cooler' é só o de processador (a ar ou water cooler). Ventoinha avulsa, hub, "
            "cabo, pasta térmica e tudo o que não couber nas outras é 'outro'."
        )
    )
    categoria_detalhe: str | None = Field(
        description=(
            "Só quando a categoria é 'outro': o que é o produto, em poucas palavras "
            "(ex.: 'hub USB'). Nas outras categorias, null."
        )
    )


# O tipo de cada campo, como a tela e o assistente o mostram. O esquema é strict: o número vai como
# número ("8" não vale 8).
FIELD_KINDS = {str: "texto", int: "inteiro", float: "numero", bool: "booleano"}


def field_specs(category: str | None) -> list[dict]:
    """Os campos de uma categoria (None: os comuns a todas), na ordem do esquema: o nome, o
    rótulo, o tipo, as opções (enum), se aceita null e a descrição (a mesma que orienta o modelo).

    A tela monta o formulário da revisão com isso, e o assistente confere os filtros da busca.
    """
    model = Product if category is None else PRODUCT_MODELS[category]
    specs = []
    for name, info in model.model_fields.items():
        args = get_args(info.annotation)
        nullable = NoneType in args
        [kind] = [arg for arg in args if arg is not NoneType] if nullable else [info.annotation]
        options = list(get_args(kind)) if get_origin(kind) is Literal else None
        specs.append(
            {
                "campo": name,
                "rotulo": info.title,
                "tipo": "opcao" if options else FIELD_KINDS[kind],
                "opcoes": options,
                "aceita_null": nullable,
                "descricao": info.description,
            }
        )
    return specs
