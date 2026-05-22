# CLAUDE.md

## Project context

P-JARI NEW é a reescrita limpa do P-JARI SC — sistema Django que assessora julgadores da JARI (Junta Administrativa de Recursos de Infrações) de Santa Catarina em recursos de trânsito.

O projeto anterior (em `P-Jari_antigo/`) serve como referência para lógica de negócio, integrações LLM e regras jurídicas. **Nunca modifique os arquivos em P-Jari_antigo/**.

A spec jurídica está em `P-Jari_antigo/logica-pjari_v2.md` — é o contrato do sistema. "Criatividade proibida, inferência proibida."

## Architecture

### Apps
- **`core/`** — Auth (Clerk only), UserProfile, Subscription, TierConfig
- **`pareceres/`** — Processo, Documento, Admissibilidade, AnaliseTese, Parecer, AuditLog

### State Machine (`pareceres/estado.py`)
O `Processo.fase` usa uma state machine explícita com transições validadas. Fases mapeiam para os 6 passos do wizard:
1. Identificação → 2. Documentos → 3. Admissibilidade → 4. Tese → 5. Parecer → 6. Finalizar

### Key design decisions
- **Auth única**: Clerk only (sem Allauth). Middleware JWT em `core/middleware.py`
- **Models desacoplados**: O God Object `Parecer` do projeto antigo foi quebrado em Processo + Documento + Admissibilidade + AnaliseTese + Parecer
- **Wizard > Chat**: Interface mudou de chat para wizard com stepper (6 passos)
- **julgador_* prevalece**: Campos `julgador_*` na Admissibilidade sempre prevalecem sobre flags automáticas `is_*`/`has_*`

### Celery
- **fast queue** (default): fases 1-4
- **heavy queue**: fase 5 (gerar_parecer_task)

## Common commands

```bash
python manage.py runserver
python manage.py migrate
python manage.py makemigrations core pareceres
python manage.py test
```

## What NOT to do
- Never modify files in `P-Jari_antigo/` — it's read-only reference
- Never add Allauth or any second auth system
- Never put phase logic in the Processo model — use `pareceres/services/`
- Never let LLMs compute dates — `jari_math` handles all date calculations
- Never inline prompts in services — keep them in `pareceres/prompts/`
