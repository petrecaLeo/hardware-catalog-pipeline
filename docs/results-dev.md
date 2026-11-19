# Resultados da extração: desenvolvimento

Conjunto usado para ajustar os prompts: os números daqui são otimistas. A medida que vale é a da prova, em [results.md](results.md).

Modelo `claude-haiku-5-5` | medida em 2026-11-19 | 50 fichas | modo direto

Ficam de fora as fichas usadas como exemplo no prompt: dev-018, dev-036, dev-047.

## Resumo

| modo | fichas | campos certos | categoria certa | fichas sem erro |
| --- | --- | --- | --- | --- |
| direto | 50 | 492/492 (100,0%) | 50/50 (100,0%) | 50/50 (100,0%) |

## Custo

Todas as chamadas das fichas do conjunto (classificação, extração e correções), gravadas em `tentativas`. Uma ficha processada em duas rodadas conta duas vezes.

| modo | fichas processadas | chamadas | custo total | custo por ficha |
| --- | --- | --- | --- | --- |
| direto | 53 | 106 | $0.021365 | $0.000403 |

## Acerto por campo

| campo | certos |
| --- | --- |
| `altura_mm` | 5/5 (100,0%) |
| `bivolt` | 6/6 (100,0%) |
| `bluetooth` | 6/6 (100,0%) |
| `cache_l3_mb` | 6/6 (100,0%) |
| `capacidade_gb` | 11/11 (100,0%) |
| `certificacao` | 6/6 (100,0%) |
| `chip` | 5/5 (100,0%) |
| `chipset` | 6/6 (100,0%) |
| `clock_base_ghz` | 6/6 (100,0%) |
| `clock_boost_ghz` | 6/6 (100,0%) |
| `clock_boost_mhz` | 5/5 (100,0%) |
| `comprimento_mm` | 5/5 (100,0%) |
| `conector_12v_2x6` | 6/6 (100,0%) |
| `consumo_w` | 5/5 (100,0%) |
| `cooler_incluso` | 6/6 (100,0%) |
| `cor` | 11/11 (100,0%) |
| `escrita_mb_s` | 6/6 (100,0%) |
| `fabricante_chip` | 5/5 (100,0%) |
| `fonte_recomendada_w` | 5/5 (100,0%) |
| `formato` | 17/17 (100,0%) |
| `frequencia_mhz` | 5/5 (100,0%) |
| `garantia_meses` | 50/50 (100,0%) |
| `latencia_cl` | 5/5 (100,0%) |
| `lateral_vidro` | 5/5 (100,0%) |
| `leitura_mb_s` | 6/6 (100,0%) |
| `marca` | 50/50 (100,0%) |
| `memoria_max_gb` | 6/6 (100,0%) |
| `modularidade` | 6/6 (100,0%) |
| `modulos` | 5/5 (100,0%) |
| `nucleos` | 6/6 (100,0%) |
| `pcie_geracao` | 6/6 (100,0%) |
| `pfc_ativo` | 6/6 (100,0%) |
| `portas_sata` | 6/6 (100,0%) |
| `potencia_w` | 6/6 (100,0%) |
| `preco` | 50/50 (100,0%) |
| `radiador_mm` | 5/5 (100,0%) |
| `rgb` | 15/15 (100,0%) |
| `rpm` | 6/6 (100,0%) |
| `rpm_max` | 5/5 (100,0%) |
| `ruido_dba` | 5/5 (100,0%) |
| `slots_m2` | 6/6 (100,0%) |
| `slots_memoria` | 6/6 (100,0%) |
| `tamanho` | 5/5 (100,0%) |
| `tbw` | 6/6 (100,0%) |
| `tdp_w` | 6/6 (100,0%) |
| `tensao_v` | 5/5 (100,0%) |
| `threads` | 6/6 (100,0%) |
| `tipo` | 16/16 (100,0%) |
| `tipo_vram` | 5/5 (100,0%) |
| `usb_c_frontal` | 5/5 (100,0%) |
| `ventoinha_mm` | 11/11 (100,0%) |
| `ventoinhas_inclusas` | 10/10 (100,0%) |
| `video_integrado` | 6/6 (100,0%) |
| `vram_gb` | 5/5 (100,0%) |
| `wifi` | 6/6 (100,0%) |

## Por categoria

| categoria | fichas | campos certos | categoria certa | fichas sem erro |
| --- | --- | --- | --- | --- |
| processador | 6 | 66/66 (100,0%) | 6/6 (100,0%) | 6/6 (100,0%) |
| placa_mae | 6 | 66/66 (100,0%) | 6/6 (100,0%) | 6/6 (100,0%) |
| memoria_ram | 5 | 55/55 (100,0%) | 5/5 (100,0%) | 5/5 (100,0%) |
| placa_de_video | 5 | 55/55 (100,0%) | 5/5 (100,0%) | 5/5 (100,0%) |
| armazenamento | 6 | 66/66 (100,0%) | 6/6 (100,0%) | 6/6 (100,0%) |
| fonte | 6 | 60/60 (100,0%) | 6/6 (100,0%) | 6/6 (100,0%) |
| gabinete | 5 | 45/45 (100,0%) | 5/5 (100,0%) | 5/5 (100,0%) |
| cooler | 5 | 55/55 (100,0%) | 5/5 (100,0%) | 5/5 (100,0%) |
| outro | 6 | 24/24 (100,0%) | 6/6 (100,0%) | 6/6 (100,0%) |

## Por estilo

| estilo | fichas | campos certos | fichas sem erro |
| --- | --- | --- | --- |
| anuncio_informal | 3 | 32/32 (100,0%) | 3/3 (100,0%) |
| caixa_alta_emoji | 5 | 47/47 (100,0%) | 5/5 (100,0%) |
| de_por | 5 | 55/55 (100,0%) | 5/5 (100,0%) |
| email_fornecedor | 5 | 52/52 (100,0%) | 5/5 (100,0%) |
| ficha_incompleta | 4 | 42/42 (100,0%) | 4/4 (100,0%) |
| ingles | 6 | 58/58 (100,0%) | 6/6 (100,0%) |
| kit | 3 | 19/19 (100,0%) | 3/3 (100,0%) |
| parcelado | 4 | 35/35 (100,0%) | 4/4 (100,0%) |
| pdf_colado | 3 | 31/31 (100,0%) | 3/3 (100,0%) |
| preco_dolar | 2 | 15/15 (100,0%) | 2/2 (100,0%) |
| tabela_colada | 6 | 65/65 (100,0%) | 6/6 (100,0%) |
| unidades_misturadas | 4 | 41/41 (100,0%) | 4/4 (100,0%) |

## Calibração

O acerto dos campos conforme a dúvida que o modelo marcou neles.

| dúvida | campos certos |
| --- | --- |
| sem dúvida | 486/486 (100,0%) |
| media | 5/5 (100,0%) |
| baixa | 1/1 (100,0%) |

## Fila de revisão

Na fila = algum motivo da própria extração ('reprovado' ou 'duvida_baixa'). A auditoria é um sorteio entre as aprovadas e conta à parte.

| fichas | na fila | só na auditoria | no catálogo |
| --- | --- | --- | --- |
| com erro | 0 | 0 | 0 |
| sem erro | 1 | 8 | 41 |

- Fichas com erro pegas pela fila: 0 de 0; pela auditoria: 0.
- Escaparam para o catálogo: 0.
- Alarmes falsos (na fila sem erro): 1. Auditoria sem erro (amostra): 8.

## Erros

Nenhum.

## Descrições livres (fora do acerto)

| campo | iguais ao gabarito |
| --- | --- |
| `categoria_detalhe` | 5/6 (83,3%) |
| `conexao` | 3/6 (50,0%) |

| ficha | campo | gabarito | extraído |
| --- | --- | --- | --- |
| dev-048 | `conexao` | "HDMI e DisplayPort" | "HDMI 2.0, DisplayPort 1.4" |
| dev-049 | `conexao` | "2,4 GHz (USB-C) e Bluetooth" | "2.4 GHz (USB-C) e Bluetooth 5.4" |
| dev-051 | `conexao` | "PWM 4 pinos e ARGB 3 pinos" | "PWM 4 pinos e ARGB 5V 3 pinos" |
| dev-053 | `categoria_detalhe` | "hub USB" | "hub USB-C" |

## Regras de comparação

- `nome` não entra: cada loja escreve de um jeito, e o gabarito é só um jeito possível.
- `categoria_detalhe` e `conexao` são descrições livres ("monitor" ou "monitor gamer"): não entram
  no acerto e aparecem à parte, com cada diferença listada.
- Os outros textos (marca, chip, chipset, cor) entram, comparados sem diferença de caixa, acento e
  espaços: "GRAÚNA" = "Graúna", mas "white" ≠ "branco".
- Números, true/false e enums têm que ser iguais ao gabarito (1149 = 1149.0). null só bate com null.
- Categoria errada conta na classificação e também nos campos: campo do gabarito que a extração
  não tem é erro.
- As fichas usadas como exemplo no prompt ficam de fora.
