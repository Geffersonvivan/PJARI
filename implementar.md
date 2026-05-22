# Implementação Frontend — Replicar UI do Projeto Antigo

O backend (services, tasks, models, views, prompts, math) está **100% implementado**.
O gap está no **frontend do wizard** — a UI precisa replicar o visual e interações do projeto antigo.

---

## Comparativo de Fases

| Wizard | Projeto Antigo (fases) | Projeto Novo (fases) | Backend Antigo | Backend Novo | Status Backend |
|--------|----------------------|---------------------|----------------|-------------|----------------|
| **Passo 1** | F1 (upload PDFs) + F10 (confirmação campos) | DOCUMENTOS → EXTRAINDO → EXTRAIDOS | `phase_1.py` (chat sequencial) | `service_documentos.py` | ✅ Pronto |
| **Passo 2** | F2 (tabela datas) + F3→F31 (admissibilidade + julgador) | DOCUMENTOS_EXTRAIDOS → ADMISSIBILIDADE → ADM_AGUARDANDO | `phase_2.py` + `phase_3.py` + `phase_3_confirm.py` | `service_admissibilidade.py` | ✅ Pronto |
| **Passo 3** | F4 (extração teses) + F41 (análise Vertex/Perplexity) | TESE → TESE_AGUARDANDO | `phase_4.py` + `phase_4_confirm.py` | `service_teses.py` | ✅ Pronto |
| **Passo 4** | F5 (parecer Claude) | PARECER → PARECER_GERANDO | `phase_5.py` | `service_parecer.py` | ✅ Pronto |
| **Passo 5** | F6 (auditoria blindagem) | AUDITORIA | `phase_6.py` | `service_auditoria.py` | ✅ Pronto |
| **Passo 6** | F7 (pasta) + F8 (finalizado) | FINALIZADO | `phase_7.py` + `phase_8.py` | Inline na view | ✅ Pronto |

---

## Mudanças de Arquitetura

| Aspecto | Antigo | Novo |
|---------|--------|------|
| **God Object** | 1 model `Parecer` (~50 campos) | 5 models (Processo, Documento, Admissibilidade, AnaliseTese, Parecer) |
| **Phase dispatch** | `JariEngine` + 8 módulos engine/ | 5 services independentes |
| **Interface** | Chat conversacional + wizard | Wizard puro (6 passos) |
| **Campos fase 1** | PA, SGPE, data_sessao + 3 PDFs | Só 1 PDF consolidado + data_sessao manual |
| **Celery tasks** | 8 tasks (incluindo predigestão, sync Drive) | 6 tasks focadas |
| **LLM parecer** | Claude (Anthropic) | Gemini 2.5-flash |

---

## Tarefas por Passo

### Passo 1 — Upload do Consolidado
- [x] Modal drag-and-drop funcional
- [x] Envio do PDF e avanço de fase
- [x] Fallback síncrono sem Celery (dev local)

### Passo 2 — Dados Extraídos + Data Sessão
- [x] Tornar campos editáveis (remover `readonly` dos inputs)
- [x] Melhorar visual da tabela de datas sensíveis (replicar do antigo)
- [x] Validação visual: checkmark verde quando campo preenchido, alerta laranja quando vazio

### Passo 3 — Admissibilidade
- [x] Cards expandíveis por item (Tempestividade, Prescrição Punitiva, Intercorrente, Intercorrente Bienal, Decadência)
- [x] Cada card mostra: resultado automático (is_*/has_*) + override do julgador (radio A/B)
- [x] Pills coloridos: ✓ verde / ✗ vermelho para flags
- [ ] Hard filters (bloqueios): decadência proibida para infrações pré-12/04/2021 (Filtro 1) e suspensão/cassação entre 12/04/2021–21/10/2021 (Filtro 2)
- [x] Texto da admissibilidade renderizado em markdown
- [x] Indicação visual da rota (A/B/C/D) após confirmação

### Passo 4 — Teses
- [x] Cards individuais por tese com título e ordem
- [x] Fundamentação expandível (markdown) com resultados RAG (Vertex + Perplexity)
- [x] Alternativas A (acolhida) / B (não acolhida) por tese
- [x] Caso sem teses: mensagem "Mérito prejudicado" e skip para parecer

### Passo 5 — Parecer
- [x] Badge DEFERIDO (verde) / INDEFERIDO (vermelho) centralizado
- [x] Texto do parecer renderizado em markdown com formatação adequada
- [x] Editor inline para edição do texto (textarea com toggle view/edit)
- [x] Seção de dossiê/fontes (colapsável)
- [x] Botão "Executar Auditoria e Finalizar"

### Passo 6 — Finalizar
- [x] Selo de conformidade com score (0-100%) e cor (verde/amarelo/vermelho)
- [x] Checklist visual de itens de auditoria
- [x] Detalhes da blindagem (expandível)
- [ ] Seleção de pasta para salvar o processo (endpoint não existe ainda)
- [x] Botões: Baixar PDF + Voltar aos Processos
- [x] Estado read-only após finalização

---

## Ordem de Implementação Sugerida

1. **Passo 2** — Dados extraídos (campos editáveis, tabela datas)
2. **Passo 3** — Admissibilidade (cards A/B, hard filters, pills)
3. **Passo 4** — Teses (cards por tese, alternativas A/B)
4. **Passo 5** — Parecer (badge, editor, dossiê)
5. **Passo 6** — Finalizar (checklist, pasta, selo)

---

## Referências

- **Spec jurídica**: `P-Jari_antigo/logica-pjari_v2.md`
- **UI antigo (wizard)**: `P-Jari_antigo/templates/wizard_parecer.html`
- **JS antigo (wizard)**: `P-Jari_antigo/templates/wizard_parecer.html` (inline, linhas 2800+)
- **Prompts**: `pareceres/prompts/phase_3.py`, `phase_4.py`, `phase_5.py`
- **Math**: `pareceres/math.py` (JariMath)
- **Services**: `pareceres/services/service_*.py`
