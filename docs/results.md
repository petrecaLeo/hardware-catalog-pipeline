# Resultados da extração: prova

Esta é a medida que vale. As fichas da prova (`data/holdout`) foram escritas antes de qualquer prompt e nunca foram usadas para ajustar nada. O conjunto de desenvolvimento, usado para ajustar os prompts, está em [results-dev.md](results-dev.md).

Modelo `claude-haiku-5-5` | medida em 2026-11-19 | 30 fichas | modo lote

## Resumo

| modo | fichas | campos certos | categoria certa | fichas sem erro |
| --- | --- | --- | --- | --- |
| lote | 30 | 297/300 (99,0%) | 30/30 (100,0%) | 28/30 (93,3%) |
| direto | 30 | 297/300 (99,0%) | 30/30 (100,0%) | 28/30 (93,3%) |

## Custo

Todas as chamadas das fichas do conjunto (classificação, extração e correções), gravadas em `tentativas`. Uma ficha processada em duas rodadas conta duas vezes.

| modo | fichas processadas | chamadas | custo total | custo por ficha |
| --- | --- | --- | --- | --- |
| lote | 30 | 60 | $0.007031 | $0.000234 |
| direto | 30 | 60 | $0.010477 | $0.000349 |

## Acerto por campo

| campo | certos |
| --- | --- |
| `modularidade` | 2/3 (66,7%) |
| `ventoinhas_inclusas` | 5/6 (83,3%) |
| `preco` | 29/30 (96,7%) |
| `altura_mm` | 3/3 (100,0%) |
| `bivolt` | 3/3 (100,0%) |
| `bluetooth` | 3/3 (100,0%) |
| `cache_l3_mb` | 4/4 (100,0%) |
| `capacidade_gb` | 7/7 (100,0%) |
| `certificacao` | 3/3 (100,0%) |
| `chip` | 4/4 (100,0%) |
| `chipset` | 3/3 (100,0%) |
| `clock_base_ghz` | 4/4 (100,0%) |
| `clock_boost_ghz` | 4/4 (100,0%) |
| `clock_boost_mhz` | 4/4 (100,0%) |
| `comprimento_mm` | 4/4 (100,0%) |
| `conector_12v_2x6` | 3/3 (100,0%) |
| `consumo_w` | 4/4 (100,0%) |
| `cooler_incluso` | 4/4 (100,0%) |
| `cor` | 6/6 (100,0%) |
| `escrita_mb_s` | 4/4 (100,0%) |
| `fabricante_chip` | 4/4 (100,0%) |
| `fonte_recomendada_w` | 4/4 (100,0%) |
| `formato` | 10/10 (100,0%) |
| `frequencia_mhz` | 3/3 (100,0%) |
| `garantia_meses` | 30/30 (100,0%) |
| `latencia_cl` | 3/3 (100,0%) |
| `lateral_vidro` | 3/3 (100,0%) |
| `leitura_mb_s` | 4/4 (100,0%) |
| `marca` | 30/30 (100,0%) |
| `memoria_max_gb` | 3/3 (100,0%) |
| `modulos` | 3/3 (100,0%) |
| `nucleos` | 4/4 (100,0%) |
| `pcie_geracao` | 4/4 (100,0%) |
| `pfc_ativo` | 3/3 (100,0%) |
| `portas_sata` | 3/3 (100,0%) |
| `potencia_w` | 3/3 (100,0%) |
| `radiador_mm` | 3/3 (100,0%) |
| `rgb` | 9/9 (100,0%) |
| `rpm` | 4/4 (100,0%) |
| `rpm_max` | 3/3 (100,0%) |
| `ruido_dba` | 3/3 (100,0%) |
| `slots_m2` | 3/3 (100,0%) |
| `slots_memoria` | 3/3 (100,0%) |
| `tamanho` | 3/3 (100,0%) |
| `tbw` | 4/4 (100,0%) |
| `tdp_w` | 4/4 (100,0%) |
| `tensao_v` | 3/3 (100,0%) |
| `threads` | 4/4 (100,0%) |
| `tipo` | 10/10 (100,0%) |
| `tipo_vram` | 4/4 (100,0%) |
| `usb_c_frontal` | 3/3 (100,0%) |
| `ventoinha_mm` | 6/6 (100,0%) |
| `video_integrado` | 4/4 (100,0%) |
| `vram_gb` | 4/4 (100,0%) |
| `wifi` | 3/3 (100,0%) |

## Por categoria

| categoria | fichas | campos certos | categoria certa | fichas sem erro |
| --- | --- | --- | --- | --- |
| processador | 4 | 44/44 (100,0%) | 4/4 (100,0%) | 4/4 (100,0%) |
| placa_mae | 3 | 33/33 (100,0%) | 3/3 (100,0%) | 3/3 (100,0%) |
| memoria_ram | 3 | 33/33 (100,0%) | 3/3 (100,0%) | 3/3 (100,0%) |
| placa_de_video | 4 | 44/44 (100,0%) | 4/4 (100,0%) | 4/4 (100,0%) |
| armazenamento | 4 | 44/44 (100,0%) | 4/4 (100,0%) | 4/4 (100,0%) |
| fonte | 3 | 28/30 (93,3%) | 3/3 (100,0%) | 2/3 (66,7%) |
| gabinete | 3 | 26/27 (96,3%) | 3/3 (100,0%) | 2/3 (66,7%) |
| cooler | 3 | 33/33 (100,0%) | 3/3 (100,0%) | 3/3 (100,0%) |
| outro | 3 | 12/12 (100,0%) | 3/3 (100,0%) | 3/3 (100,0%) |

## Por estilo

| estilo | fichas | campos certos | fichas sem erro |
| --- | --- | --- | --- |
| anuncio_informal | 3 | 31/31 (100,0%) | 3/3 (100,0%) |
| caixa_alta_emoji | 3 | 33/33 (100,0%) | 3/3 (100,0%) |
| de_por | 3 | 30/31 (96,8%) | 2/3 (66,7%) |
| email_fornecedor | 3 | 31/31 (100,0%) | 3/3 (100,0%) |
| ficha_incompleta | 2 | 15/15 (100,0%) | 2/2 (100,0%) |
| ingles | 3 | 32/32 (100,0%) | 3/3 (100,0%) |
| kit | 2 | 15/15 (100,0%) | 2/2 (100,0%) |
| parcelado | 2 | 19/21 (90,5%) | 1/2 (50,0%) |
| pdf_colado | 2 | 21/21 (100,0%) | 2/2 (100,0%) |
| preco_dolar | 2 | 15/15 (100,0%) | 2/2 (100,0%) |
| tabela_colada | 3 | 33/33 (100,0%) | 3/3 (100,0%) |
| unidades_misturadas | 2 | 22/22 (100,0%) | 2/2 (100,0%) |

## Calibração

O acerto dos campos conforme a dúvida que o modelo marcou neles.

| dúvida | campos certos |
| --- | --- |
| sem dúvida | 293/295 (99,3%) |
| media | 2/3 (66,7%) |
| baixa | 2/2 (100,0%) |

## Fila de revisão

Na fila = algum motivo da própria extração ('reprovado' ou 'duvida_baixa'). A auditoria é um sorteio entre as aprovadas e conta à parte.

| fichas | na fila | só na auditoria | no catálogo |
| --- | --- | --- | --- |
| com erro | 1 | 1 | 0 |
| sem erro | 1 | 8 | 19 |

- Fichas com erro pegas pela fila: 1 de 2; pela auditoria: 1.
- Escaparam para o catálogo: 0.
- Alarmes falsos (na fila sem erro): 1. Auditoria sem erro (amostra): 8.

## Erros

| ficha | campo | gabarito | extraído | dúvida | foi para |
| --- | --- | --- | --- | --- | --- |
| hold-019 | `preco` | 299.4 | 298.8 | — | auditoria |
| hold-019 | `modularidade` | "nao_modular" | "modular" | — | auditoria |
| hold-023 | `ventoinhas_inclusas` | 3 | 2 | media | fila |

## Descrições livres (fora do acerto)

| campo | iguais ao gabarito |
| --- | --- |
| `categoria_detalhe` | 3/3 (100,0%) |
| `conexao` | 1/3 (33,3%) |

| ficha | campo | gabarito | extraído |
| --- | --- | --- | --- |
| hold-028 | `conexao` | "USB" | "USB com fio" |
| hold-029 | `conexao` | "PCIe" | "PCIe x1" |

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

<!-- Escrito à mão daqui até o fim: o `cli.py eval --salvar` mantém este trecho. -->

## Limitações observadas na prova

Escritas depois da medida, sem mudar nenhum prompt. Os 3 erros foram os mesmos no lote e no direto.

- **A conta do parcelado (hold-019).** A ficha só traz "6x de R$ 49,90". O modelo multiplicou e errou: 298,80 em vez de 299,40. A regra "só parcelas → o total" deixa a conta com o modelo; o seguro seria extrair o número e o valor das parcelas e multiplicar no código.
- **Leitura invertida (hold-019).** "Todos os cabos presos à fonte" virou `modularidade` "modular", sem dúvida. Nenhuma regra pega um valor válido trocado, e `modularidade` não é campo importante: a ficha só foi para a revisão porque caiu no sorteio da auditoria.
- **Contagem (hold-023).** "duas fans ARGB de 120 mm instaladas e mais uma de 120 mm na traseira" virou 2 ventoinhas (são 3), com dúvida 'media', que não manda para a fila. No lote, a ficha foi para a fila por acaso, por uma dúvida 'baixa' em outro campo (`tamanho`); no direto, foi para o catálogo.
- **As dúvidas avisam pouco.** Nenhum dos 3 erros teve dúvida 'baixa', e todas as 'baixa' caíram em campos certos. O alarme falso, nos dois modos, foi a 'baixa' no preço só em dólar (hold-003), que segue uma regra do prompt e não é dúvida.
- **Amostra pequena.** São 30 fichas: cada erro vale 0,3 ponto no acerto dos campos e 3,3 pontos nas fichas sem erro. As fichas são inventadas, escritas pela mesma pessoa que fez os gabaritos e os prompts.

## Por que o lote custou 67% do direto, e não 50%

O direto rodou logo depois da última rodada do desenvolvimento, com o mesmo prompt, e achou o cache já gravado (só leitura, a 0,1×); o lote gravou o cache de novo (32.151 tokens a 1,25×, US$ 0,0020, 29% do custo dele). Sem essa gravação, o lote sairia a US$ 0,000173 por ficha, 49% do direto.
