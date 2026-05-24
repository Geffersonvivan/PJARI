# Lógica de Julgamento P-JARI — Relatório Completo

> Documento de referência técnica descrevendo o fluxo de ponta a ponta do motor de julgamento do P-JARI SC, desde a ingestão do PDF até a finalização do parecer.

---

## 1. Visão Geral

O P-JARI é um sistema de assessoria a julgamentos da JARI (Junta Administrativa de Recursos de Infrações) de Santa Catarina. O motor de julgamento (`chat/engine/`) processa recursos de infrações de trânsito em **8 fases sequenciais**, cada uma com responsabilidades bem definidas.

### Princípios Fundamentais

- **Criatividade proibida, inferência proibida**: o sistema nunca inventa fatos, datas, normas ou conclusões.
- **Fonte única da verdade**: o RAG do Inventário Normativo (Vertex AI) é o "GPS" jurídico obrigatório.
- **Python calcula, LLM redige**: todos os cálculos de datas e prazos são feitos pelo `JariMath` (Python puro). Os LLMs apenas leem os resultados e redigem o texto jurídico.
- **Julgador soberano**: as flags automáticas (is_tempestivo, has_prescricao, etc.) são apenas sugestões. O membro julgador pode confirmar ou inverter cada resultado. Suas escolhas (`julgador_*`) são a referência oficial para todas as fases seguintes.

### Modelo Central: `Parecer` (`chat/models.py`)

O `Parecer` é o registro central — todas as fases leem e escrevem seus campos. Campos-chave:

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `status_fase` | int | Estado atual do processo (1–8, com half-steps 10/31/41) |
| `data_sessao` | date | Data da sessão de julgamento (Pergunta 1) |
| `data_protocolo` | date | Data do protocolo do recurso JARI (Pergunta 3) |
| `prazo_final` | date | Prazo final para interposição do recurso (Pergunta 2) |
| `paginas_defesa` | str | Páginas da defesa recursal no PDF (Pergunta 4) |
| `recorrente` | str | Nome do recorrente (extraído do PDF, maiúsculas) |
| `data_infracao` | date | Data da infração (extraída na F3) |
| `tipo_penalidade` | str | multa/advertencia/suspensao/cassacao |
| `tem_flagrante` | bool | Se a autuação foi em flagrante |
| `data_conhecimento_infracao` | date | Marco para multa sem flagrante (FILTRO 3) |
| `data_conclusao_multa` | date | Marco para suspensão/cassação (FILTRO 3) |
| `data_totalizacao_pontos` | date | Marco para suspensão por acúmulo de pontos |
| `is_tempestivo` | bool | Resultado automático da tempestividade |
| `has_prescricao_punitiva` | bool | Resultado automático da prescrição punitiva |
| `has_prescricao_intercorrente` | bool | Resultado automático da prescrição intercorrente trienal |
| `has_prescricao_intercorrente_bienal` | bool | Resultado automático da prescrição intercorrente bienal |
| `has_decadencia` | bool | Resultado automático da decadência |
| `julgador_tempestivo` | bool | Decisão do julgador (override da automática) |
| `julgador_prescricao_punitiva` | bool | Decisão do julgador |
| `julgador_prescricao_intercorrente` | bool | Decisão do julgador |
| `julgador_prescricao_intercorrente_bienal` | bool | Decisão do julgador |
| `julgador_decadencia` | bool | Decisão do julgador |
| `tese` | text | Tese defensiva extraída |
| `analise_tese_texto` | text | Análise cruzada das teses (RAG + jurisprudência) |
| `parecer_final` | text | Texto completo do parecer gerado |
| `blindagem_score` | int | Score de conformidade da auditoria (0–100) |

### Máquina de Estados (`status_fase`)

```
1 (Coleta) → 10 (Confirmar F1) → 2 (DIR) → 3 (Calculando F3) → 31 (Confirmar Admiss.)
                                                                        ↓
                                                          ┌─── prejudica mérito? ───┐
                                                          │ SIM                     │ NÃO
                                                          ↓                         ↓
                                                    5 (Parecer)              4 (Extração Tese)
                                                          ↑                    → 41 (Confirmar Tese)
                                                          │                         │
                                                          └─────────────────────────┘
                                                          ↓
                                                    6 (Auditoria) → 7 (Seleção Pasta) → 8 (Finalizado)
```

---

## 2. Fases do Motor

### FASE 1 — Coleta de Dados (`phase_1.py`)

**Objetivo**: Ingerir o PDF do processo e coletar os 4 dados obrigatórios do julgador.

**Fluxo**:
1. O julgador faz upload do PDF "Consolidado" (documento único com todo o dossiê).
2. O upload dispara a task Celery `processar_fase1_task` que:
   - Extrai a infração via `PDFExtractor.extract_infracao_from_pdf()` (pyMuPDF local, sem API).
   - Extrai o nome do recorrente via regex em padrões comuns (RECORRENTE, CONDUTOR, AUTUADO, INTERESSADO).
   - Avança para `status_fase = 10` (aguardando confirmação).
3. Na fase 10, o julgador confirma/corrige os dados no formulário:
   - **Data da Sessão de Julgamento** (obrigatória)
   - **Prazo Final do Protocolo** (obrigatória)
   - **Data do Protocolo do Recurso JARI** (obrigatória)
   - **Páginas da Defesa** (obrigatória)
   - Recorrente (pré-preenchido se extraído)

**Regra de Ouro**: As respostas das 4 perguntas têm **precedência absoluta** sobre qualquer dado encontrado nos documentos. Se o PDF diz uma coisa e o julgador informou outra, prevalece o julgador.

**Validações**:
- Datas em formato DD/MM/AAAA (aceita YYYY-MM-DD e DD-MM-YYYY como fallback).
- Todos os 4 campos são obrigatórios — o sistema bloqueia o avanço sem eles.

**Provider LLM**: Nenhum (extração local via pyMuPDF + regex).

**Celery**: `processar_fase1_task` — fila `fast`, `time_limit=480s`, `max_retries=3`.

**Saída**: Campos salvos no `Parecer`. Avança para Fase 2.

---

### FASE 2 — DIR: Integridade e Regularidade (`phase_2.py`)

**Objetivo**: Extrair todas as datas do dossiê e gerar a Tabela de Datas Sensíveis para validação humana.

**Fluxo**:
1. `PDFExtractor.extract_dates_from_pdf()` extrai datas do PDF com texto nativo ou OCR automático (Tesseract para PDFs escaneados).
2. Chamada ao **Anthropic (Claude)** via `generate_phase2_report()` para gerar a tabela estruturada.
3. Se Anthropic falhar, **fallback para Gemini** via `generate_phase2_report()` (Gemini Files API é melhor para PDFs escaneados).
4. A resposta é um JSON estruturado (via `response_schema`) com:
   - `recorrente` — nome extraído
   - `tipo_penalidade` — multa/advertencia/suspensao/cassacao
   - `tem_flagrante` — SIM/NAO
   - `data_conhecimento_infracao`, `data_conclusao_multa`, `data_totalizacao_pontos`
   - `tabela_markdown` — tabela formatada para exibição

**Campos extraídos e salvos**:
- `recorrente` (forçado MAIÚSCULAS)
- `tipo_penalidade`
- `data_conclusao_multa`, `tem_flagrante`, `data_conhecimento_infracao`, `data_totalizacao_pontos`
- `tabela_datas_sensiveis` (markdown da tabela)

**Validações de consistência**:
- Notificação anterior à infração → aviso.
- Sessão anterior à infração → aviso.
- PDF com <500 chars pós-OCR → aviso de ilegibilidade.

**Edição inline**: O julgador pode corrigir qualquer campo via formato `campo: valor` ou via formulário `F2_FIELDS:{json}`.

**Bloqueios**:
- `data_sessao < data_infracao` → bloqueio fatal (inconsistência cronológica).
- Suspensão sem `data_totalizacao_pontos` → bloqueio (campo obrigatório para cálculo correto).

**Provider LLM**: Anthropic (Claude) com fallback Gemini.

**Celery**: `processar_fase2_task` — fila `fast`, `time_limit=360s`, `max_retries=3`.

**Saída**: Tabela de datas exibida ao julgador. Ao confirmar com "ok", avança para Fase 3.

---

### FASE 3 — Admissibilidade: Cálculos Matemáticos (`phase_3.py`)

**Objetivo**: Calcular tempestividade, prescrição (punitiva, intercorrente trienal e bienal) e decadência usando Python puro (`JariMath`), e gerar o texto de resultado via LLM.

**Fluxo**:
1. **Parser semântico de datas**: extrai datas da tabela F2 por rótulos (INFRAÇÃO, NA, NP, INSTAURAÇÃO).
2. **Cálculos JariMath** (5 verificações independentes).
3. **Geração de texto** via Anthropic (Claude) com os resultados matemáticos como input.
4. Avança para `status_fase = 31` (aguardando confirmação do julgador).

#### 3.1 Tempestividade (`JariMath.check_tempestividade`)

**Regra**: CTB Art. 285 — se `data_protocolo > prazo_final`, o recurso é **intempestivo**.

**Parâmetros**:
- `data_protocolo` (Pergunta 3 da F1)
- `prazo_final` (Pergunta 2 da F1)

**Lógica**:
```python
return data_protocolo <= prazo_final  # True = tempestivo
```

**Resultado**: `is_tempestivo` (bool) — `True` = tempestivo, `False` = intempestivo.

#### 3.2 Prescrição Punitiva — 5 anos (`JariMath.check_prescription_punitiva`)

**Regra**: Lei 9.873/99 — prazo de 5 anos civis do último ato interruptivo válido.

**Parâmetros**:
- `data_infracao` — data da infração (ou `data_totalizacao_pontos + 1 dia` para suspensão por pontos)
- `data_sessao` — data da sessão de julgamento (Pergunta 1)
- `marcos_interruptivos` — datas de atos formais válidos (NA, NP, Instauração) entre infração e sessão
- `desconto_covid_dias` — 0 ou 256 dias (Res. CONTRAN 782/2020) se último marco ≤ 30/11/2020

**Lógica**:
```python
ultimo_marco = max(marcos_interruptivos) ou data_infracao
aniversario = ultimo_marco + 5 anos (mesmo dia/mês)
aniversario += desconto_covid_dias  # se aplicável
return data_sessao > aniversario  # True = prescrito
```

**Tratamento de 29/02**: Se o ano do aniversário não é bissexto, usa 01/03 (não 28/02).

**Marco para suspensão por pontos**: `data_totalizacao_pontos + 1 dia` em vez de `data_infracao`.

**Resultado**: `has_prescricao_punitiva` (bool).

#### 3.3 Prescrição Intercorrente Trienal — 3 anos (`JariMath.check_prescription_intercorrente`)

**Regra**: Lei 9.873/99 — prazo de 3 anos civis entre o protocolo JARI e a sessão de julgamento.

**Parâmetros**:
- `data_protocolo` (Pergunta 3 da F1) — início do prazo
- `data_sessao` (Pergunta 1 da F1) — fim do prazo

**Lógica**:
```python
aniversario = data_protocolo + 3 anos (mesmo dia/mês)
return data_sessao > aniversario  # True = prescrito
```

**Restrição**: análise feita exclusivamente entre estas duas datas. Nenhuma movimentação processual intermediária é considerada.

**Resultado**: `has_prescricao_intercorrente` (bool) + declaração textual.

#### 3.4 Prescrição Intercorrente Bienal — 2 anos (`JariMath.check_prescription_intercorrente_bienal`)

**Regra**: Art. 285, §6º, c/c art. 289-A do CTB (Lei 14.229/2021).

**Trava de segurança**: Aplica-se **somente** a protocolos de recurso a partir de **01/01/2024**. Se `data_protocolo < 01/01/2024`, retorna "NÃO SE APLICA".

**Parâmetros**: Idênticos à trienal.

**Lógica**:
```python
if data_protocolo < 01/01/2024:
    return False, "NÃO SE APLICA"
aniversario = data_protocolo + 2 anos
return data_sessao > aniversario
```

**Resultado**: `has_prescricao_intercorrente_bienal` (bool) + declaração textual.

#### 3.5 Decadência (`JariMath.check_decadencia`)

**Regra**: CTB + Leis 14.071/2020 e 14.229/2021 + Parecer CETRAN/SC 381/2022.

**Parâmetros**:
- `data_infracao` — data da infração
- `data_expedicao_autuacao` — data da notificação/autuação (NA ou NP)
- `data_decisao_final` — data da decisão final (para FILTRO 2)
- `tipo_penalidade` — multa/advertencia/suspensao/cassacao
- `data_conclusao_multa` — marco para suspensão/cassação no FILTRO 3
- `tem_flagrante` — True/False/None
- `data_conhecimento_infracao` — marco para multa sem flagrante

**Sistema de Filtros Temporais**:

| Filtro | Período da Infração | Regra |
|--------|---------------------|-------|
| **FILTRO 1** | Até 11/04/2021 (inclusive) | **NÃO SE APLICA** — decadência 180/360 dias PROIBIDA (CETRAN/SC 381/2022). Análise apenas por prescrição punitiva. |
| **FILTRO 2** | 12/04/2021 a 21/10/2021 | Multa/Advertência: 180 dias (flagrante) ou 360 dias (sem flagrante). Suspensão/Cassação: **NÃO SE APLICA** (Nota CETRAN/SC 02/03/2023). |
| **FILTRO 3** | A partir de 22/10/2021 | Todas as penalidades. Multa com flagrante: 180 dias da infração. Multa sem flagrante: 360 dias do conhecimento. Suspensão/Cassação: 360 dias da conclusão da multa. |

**COVID-19 (Res. CONTRAN 782/2020)**:
- Período de suspensão: 20/03/2020 a 30/11/2020 (256 dias corridos).
- Para infrações anteriores ao período COVID: desconto integral de 256 dias.
- Para infrações dentro do período COVID: desconto proporcional (dias restantes até 30/11/2020).
- **Não se aplica à prescrição intercorrente** (mede inércia do Estado, não prazo de defesa do cidadão).

**Resultado**: `has_decadencia` (bool) + relatório textual detalhado.

#### Geração de texto (LLM)

Os resultados matemáticos são passados ao **Anthropic (Claude)** via `generate_phase3_report()` como um resumo estruturado. O LLM:
- Redige o resultado técnico automático nos 5 blocos obrigatórios com cálculo fundamentado.
- Gera o quadro-resumo de opções (A = confirmar / B = afastar) para cada item.
- Emite tags de decisão parsáveis: `[DECISAO_ADMISSIBILIDADE_TEMPESTIVIDADE:SIM_OU_NAO]` etc.

**Provider LLM**: Anthropic (Claude).

**Celery**: `processar_fase3_admissibilidade_task` — fila `fast`, `time_limit=360s`, `max_retries=3`.

**Otimização**: Pré-cálculo em background (`processar_fase3_precompute_task`) — executa F3 enquanto o julgador revisa F2, sem avançar `status_fase`.

---

### FASE 31 — Confirmação da Admissibilidade (`phase_3_confirm.py`)

**Objetivo**: O julgador confirma ou inverte cada resultado técnico automático. Suas escolhas tornam-se as flags oficiais para todas as fases seguintes.

**Input do julgador**: Formato livre com parsing semântico:
- "ok" / "confirmo" → aceita todos os resultados automáticos.
- "Tempestividade - A / Prescrição Punitiva - B / ..." → escolhas individuais.
- Aceita variações naturais: "acolhida", "concordo", "aprovado", "correto", "aceito", "reconheço".

**Semântica ABSOLUTA (A/B)**:

| Item | A (positivo) | B (negativo) |
|------|-------------|--------------|
| Tempestividade | TEMPESTIVO (admissível) | INTEMPESTIVO (inadmissível) |
| Prescrição Punitiva | SIM (prescrito) | NÃO |
| Prescrição Intercorrente Trienal | SIM (prescrito) | NÃO |
| Prescrição Intercorrente Bienal | SIM (prescrito) | NÃO |
| Decadência | SIM (configurada) | NÃO / NÃO SE APLICA |

**Bloqueios hard-coded**:

| Cenário | Ação |
|---------|------|
| Decadência SIM + Filtro 1 (infração < 12/04/2021) | **CONVERSÃO BLOQUEADA** — CETRAN/SC 381/2022 veda absolutamente. Sistema recusa e exige redigitação. |
| Decadência SIM + Filtro 2 + Suspensão/Cassação | **CONVERSÃO BLOQUEADA** — Nota CETRAN/SC 02/03/2023 restringe a multas/advertências. |

**Campos salvos**: `julgador_tempestivo`, `julgador_prescricao_punitiva`, `julgador_prescricao_intercorrente`, `julgador_prescricao_intercorrente_bienal`, `julgador_decadencia`.

**Roteamento por precedência**:

```
1º ROTA C — Decadência SIM → pula mérito → Fase 5 (DEFERIDO)
2º ROTA B — Prescrição SIM (qualquer) → pula mérito → Fase 5 (DEFERIDO)
3º ROTA A — Intempestividade CONFIGURADA → pula mérito → Fase 5 (INDEFERIDO)
4º ROTA D — Nenhum filtro ativo → Fase 4 (análise de mérito)
```

Quando múltiplas flags estão ativas, aplica-se a rota de **número menor** (mais prioritária).

**Saída**:
- Se prejudica mérito (Rotas A/B/C): salva `tese = "MÉRITO PREJUDICADO (...)"`, avança para **Fase 5** via `gerar_parecer_task`.
- Se não prejudica (Rota D): avança para **Fase 4** via `processar_fase4_task`.

---

### FASE 4 — Extração e Análise de Teses (`phase_4.py` + `phase_4_confirm.py`)

**Objetivo**: Extrair as teses defensivas do recurso e analisá-las com base normativa (RAG) e jurisprudencial.

#### 4.1 Extração de Teses (status_fase = 4)

**Fluxo**:
1. **Anthropic (Claude)** via `extract_tese()` lê as páginas indicadas (Pergunta 4) e identifica as teses.
2. Se nenhuma tese identificada: `tese = "Nenhuma tese defensiva identificada"` → rota direta para **INDEFERIDO** (§425-427).
3. Tese exibida ao julgador para confirmação.

**Interação do julgador**:
- "ok" → aceita a tese e avança para análise.
- Qualquer outro texto → refina a tese via `refine_tese()` (Anthropic reprocessa com dica do julgador).

**Provider LLM**: Anthropic (Claude).

**Celery**: `processar_fase4_task` — fila `fast`.

#### 4.2 Análise de Teses (status_fase = 41)

**Fluxo**:
1. **PJARI-CACHE**: Verifica cache de pacotes pré-digeridos (CAG) por tipo de infração. Se miss, busca por núcleo da tese.
2. Se cache miss, busca em paralelo (ThreadPoolExecutor):
   - **Vertex AI** (`search_documents`) — RAG contra o Inventário Normativo.
   - **Perplexity** (`search_tese`) — jurisprudência web.
3. **Anthropic (Claude)** via `analyze_tese()` — análise cruzada: tese × Vertex (norma) × Perplexity (jurisprudência).
4. Se o resultado contém tags `[DECISAO_TESE_N]` → exibe ao julgador para confirmação (**status_fase = 41**).
5. Se não contém tags (mérito prejudicado) → avança direto para Fase 5.

**Cache**: `PjariCacheEntry` — 7 dias de validade. Busca por artigo CTB na infração (CAG) ou por núcleo semântico da tese.

**Providers LLM**: Vertex AI (RAG), Perplexity (jurisprudência), Anthropic (análise).

**Celery**: `processar_fase4_analise_task` — fila `fast`.

#### 4.3 Confirmação de Teses (status_fase = 41)

**Input do julgador**: Formato livre com parsing:
- "Acolhida" / "A" → tese acolhida (ao menos uma = DEFERIDO).
- "Não Acolhida" / "B" → tese rejeitada (todas rejeitadas = INDEFERIDO).
- "Deferir" / "Indeferir" também aceitos.

**Resultado**: Appenda ao `analise_tese_texto` a diretriz absoluta com o resultado exigido (DEFERIDO/INDEFERIDO) para a Fase 5.

**Saída**: Avança para **Fase 5** via `gerar_parecer_task`.

---

### FASE 5 — Parecer Técnico Final (`phase_5.py`)

**Objetivo**: Gerar o documento jurídico completo (parecer técnico) em bloco único.

**Fluxo**:
1. Se mérito prejudicado: `vertex_result` e `perplexity_result` setados como "Não aplicável".
2. Se mérito analisado: busca RAG (Vertex) e jurisprudência (Perplexity) em paralelo (reutiliza se já foi buscado na F4).
3. **Anthropic (Claude)** via `validate_and_generate_parecer()` gera o parecer usando:
   - Flags do julgador (F31)
   - Teses e análise (F4)
   - Fundamentação normativa (Vertex RAG)
   - Jurisprudência (Perplexity)

**Estrutura do parecer gerado**:
```
PARECER JARI
  RECORRENTE: [nome]
  RELATOR: [usuário autenticado]
  DATA SESSÃO: [DD/MM/AAAA]
  RESULTADO: [DEFERIDO/INDEFERIDO]

EMENTA (máx 6 linhas, maiúsculas)
RELATÓRIO (máx 10 linhas)
FUNDAMENTAÇÃO JURÍDICA
  1. ADMISSIBILIDADE
  2. TESES DEFENSIVAS
  3. PRESCRIÇÃO E DECADÊNCIA
    3.1 Prescrição Punitiva
    3.2 Prescrição Intercorrente Trienal
    3.3 Prescrição Intercorrente Bienal
    3.4 Decadência
  4. MATERIALIDADE
  5. GARANTIAS PROCESSUAIS
```

**Regras de resultado**:
- **DEFERIDO** se: qualquer prescrição SIM, ou decadência SIM, ou ao menos uma tese acolhida.
- **INDEFERIDO** se: intempestividade configurada sem prescrição/decadência, ou todas as teses rejeitadas.

**Dossiê de Fontes**: Extraído do texto via markers `DOSSIE_START`/`DOSSIE_END`, salvo em `dossie_fontes`.

**Limpeza de PDFs**: Se o parecer é válido (>200 chars, sem erros), os PDFs são deletados do storage (GCS em produção) para economia.

**Provider LLM**: Anthropic (Claude).

**Celery**: `gerar_parecer_task` — fila `heavy`, `time_limit=600s`, `max_tasks_per_child=20`.

**Saída**: `parecer_final` salvo. `fase5_provider = 'Claude'`. Avança para Fase 6.

---

### FASE 6 — Auditoria de Conformidade (`phase_6.py`)

**Objetivo**: Validar o parecer gerado contra as regras do spec e gerar um score de conformidade.

**Fluxo**:
1. O julgador vê o parecer e pode:
   - Digitar "ok" → executa auditoria.
   - Usar "Editar texto" → corrige o parecer antes da auditoria.
2. **Validação programática (JariMath soberano)**: checklist de 10 itens ponderados.
3. **Auditoria qualitativa via Claude** (`audit_parecer`): checklist dos 10 itens do spec.

#### Checklist programático (10 itens)

| # | Item | Peso | Tipo |
|---|------|------|------|
| 2 | RESULTADO: compatibilidade flags↔DEFERIDO/INDEFERIDO | 5 | Fatal |
| 1a | CABEÇALHO: nome do recorrente presente | 1 | Aviso |
| 1b | CABEÇALHO: data da sessão presente | 1 | Aviso |
| 10a | VEDAÇÕES: menção a motor de IA (Perplexity, Gemini, etc.) | 1 | Aviso |
| 10b | VEDAÇÕES: menção a fases internas (Fase 1, Fase 2, etc.) | 1 | Aviso |
| 10c | VEDAÇÕES: emojis no parecer | 1 | Aviso |

**Score**: `(10 - pontos_fatais - pontos_avisos) / 10 × 100`.

**Bloqueio fatal**: Se o resultado (DEFERIDO/INDEFERIDO) é incompatível com as flags do julgador:
- Prescrição/Decadência SIM ou tese acolhida → deveria ser DEFERIDO.
- Intempestividade sem prescrição/decadência → deveria ser INDEFERIDO.
- **Sistema bloqueia avanço** e exige correção do texto.
- Email de alerta enviado ao admin.

**Saída**: `blindagem_score`, `blindagem_detalhes`, `checklist_auditoria_json`, `tempo_julgamento_segundos` salvos. Avança para Fase 7.

---

### FASE 7 — Seleção de Pasta (`phase_7.py`)

**Objetivo**: O julgador seleciona a pasta de destino para salvar o processo.

**Fluxo**:
1. Lista as pastas do usuário (`Pasta` model).
2. Julgador seleciona clicando no card.
3. Salva: `pasta`, `nome_processo = "Parecer {recorrente}"`, `is_saved = True`, `status_fase = 8`.
4. Desconta 1 crédito (para usuários não-PRO, via `F()` atômico para evitar race condition).

**Saída**: Processo finalizado e salvo.

---

### FASE 8 — Processo Finalizado (`phase_8.py`)

**Objetivo**: Exibir o parecer salvo com dossiê de fontes.

O processo fica em estado de leitura. O julgador pode editar o parecer via TinyMCE (salvo em `ParecerFinal.conteudo_html`). A versão canônica é acessada via `Parecer.conteudo_final` (prioriza edição humana sobre IA).

---

## 3. Pipeline Assíncrono (Celery)

Todas as fases com chamada LLM rodam como tasks Celery para não bloquear o Gunicorn.

### Filas

| Fila | Concurrency | Tasks |
|------|-------------|-------|
| `fast` | 16 | F1, F2, F3 (admissibilidade), F3 (precompute), F4 (extração), F4 (análise) |
| `heavy` | 8 | F5 (gerar_parecer) — `max_tasks_per_child=20` para reciclar workers |

### Retry e Erros

| Tipo de Erro | Comportamento |
|-------------|---------------|
| Transitório (504, 529, 502, timeout, rate_limit) | Retry com backoff: 10s, 20s, 30s (máx 3 tentativas) |
| Permanente (PERMISSION_DENIED, API_KEY_INVALID, billing) | Sem retry. Log CRITICAL + Sentry. Mensagem amigável ao usuário. |
| Soft time limit | Degrada graciosamente — fallback para fluxo manual. |

### Frontend

- SSE em `/chat/stream/<task_id>/` para progresso em tempo real.
- Polling JSON em `/chat/task-status/<task_id>/` como fallback.

---

## 4. Integrações LLM

### Anthropic / Claude (`chat/integrations/anthropic.py`)

- **F2**: `generate_phase2_report()` — extração de datas e tabela.
- **F3**: `generate_phase3_report()` — texto de admissibilidade.
- **F4**: `extract_tese()` — extração de teses. `refine_tese()` — refinamento. `analyze_tese()` — análise cruzada. `get_cache_key_from_tese()` — chave para cache.
- **F5**: `validate_and_generate_parecer()` — parecer completo.
- **F6**: `audit_parecer()` — checklist qualitativo de auditoria.

### Gemini (`chat/integrations/gemini.py`)

- **F1**: Upload de PDFs via Files API (polling até `ACTIVE`).
- **F2**: Fallback de `generate_phase2_report()` quando Anthropic falha.
- Limites de campo em `_LIMITES` para caber na janela de prompt.

### Vertex AI (`chat/integrations/vertex.py`)

- **F4/F5**: `search_documents()` — RAG contra o Inventário Normativo (Discovery Engine).
- Cache Redis de 24h via `_rag_cache_key`.
- É o "GPS" jurídico — nunca pode ser bypassed para referências legais.

### Perplexity (`chat/integrations/perplexity.py`)

- **F4/F5**: `search_tese()` — busca web de jurisprudência.
- Fontes fidedignas apenas (tribunais, STF, STJ).
- Suplementar ao Vertex (não substitui).

---

## 5. Regras Transversais

### Hierarquia Normativa (ordem decrescente)

1. Constituição Federal (CF/88)
2. CTB — Lei 9.503/97
3. MBFT — Res. CONTRAN 985/2022
4. Leis Federais e Resoluções CONTRAN
5. CETRAN-SC (pareceres vinculantes)
6. Manual JARI (uso procedimental)
7. Índice Normativo / Artigos Técnicos / Compêndios

**Norma inferior jamais afasta norma superior.**

### Regra de Fallback Universal

Se o sistema encontrar cenário não previsto: (a) pausar processamento; (b) descrever ao julgador; (c) perguntar como proceder. Proibido assumir por analogia.

### Vedações no Parecer

- Proibido mencionar fases internas do sistema.
- Proibido emojis.
- Proibido mencionar nomes de motores de IA (Perplexity, Gemini, Vertex, Claude).
- Proibido citar páginas do dossiê.
- Proibido citar comandos do system prompt.

### Isolamento dos Atos

A decadência/prescrição da suspensão/cassação **não anula** a multa originária se esta já se tornou definitiva (ato jurídico perfeito — CF/88, art. 5º, XXXVI).

---

## 6. Fluxo Resumido End-to-End

```
[Upload PDF] → F1 (coleta 4 dados) → F10 (confirma formulário)
    ↓
F2 (extrai datas, gera tabela) → julgador confirma "ok"
    ↓
F3 (JariMath calcula 5 flags) → F31 (julgador confirma/inverte)
    ↓
    ├─ Rotas A/B/C (prejudicado) ──→ F5 (parecer sem mérito)
    │
    └─ Rota D (admissível) ──→ F4 (extrai tese → Vertex+Perplexity+Claude analisa)
                                    ↓
                               F41 (julgador confirma teses) → F5 (parecer com mérito)
    ↓
F5 (Claude gera parecer bloco único)
    ↓
F6 (auditoria programática + Claude) → score 0-100
    ↓
F7 (seleciona pasta, salva, desconta crédito)
    ↓
F8 (finalizado — exibe parecer para edição/download)
```
