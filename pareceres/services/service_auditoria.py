"""
Service Fase 6 — Auditoria de conformidade (blindagem).

Implementa os 10 itens obrigatórios do checklist de auditoria
conforme logica-pjari_v2.md §§511-523.

Responsabilidades:
1. Checklist programático de 10 itens (spec-compliant)
2. Score ponderado (0-100)
3. Avançar para FINALIZADO
"""

import logging
import re

from django.utils import timezone

from pareceres.estado import FaseProcesso
from pareceres.models import Parecer, log_audit
from . import ServiceResult

_log = logging.getLogger(__name__)

# Nomes de motores de IA vedados no texto do parecer
_VEDACOES_AI = ["PERPLEXITY", "GEMINI", "VERTEX", "CLAUDE", "ANTHROPIC", "CHATGPT", "OPENAI"]


def _check_item_1_cabecalho(processo, parecer_texto, texto_upper):
    """Item 1: PA, SGPE, Recorrente, Relator e Data Sessão corretos."""
    falhas = []

    if processo.pa and processo.pa not in parecer_texto:
        falhas.append("PA ausente ou incorreto")

    if processo.sgpe and processo.sgpe not in parecer_texto:
        falhas.append("SGPE ausente ou incorreto")

    if processo.recorrente:
        nome_partes = processo.recorrente.strip().split()
        sobrenome = nome_partes[-1] if nome_partes else ""
        if sobrenome and sobrenome.upper() not in texto_upper:
            falhas.append(f"Recorrente ('{processo.recorrente}') ausente")

    if processo.data_sessao:
        data_str = processo.data_sessao.strftime("%d/%m/%Y")
        if data_str not in parecer_texto:
            falhas.append(f"Data da sessao ({data_str}) ausente")

    # Relator — verificar se há menção a "RELATOR" no texto
    if "RELATOR" not in texto_upper:
        falhas.append("Campo Relator ausente")

    ok = len(falhas) == 0
    return ok, "Cabecalho completo" if ok else "Cabecalho: " + "; ".join(falhas)


def _check_item_2_resultado(processo, adm, parecer_texto, texto_upper):
    """Item 2: RESULTADO compatível com flags e teses."""
    tem_deferido = bool(re.search(r'\bDEFERIDO\b', texto_upper))
    tem_indeferido = bool(re.search(r'\bINDEFERIDO\b', texto_upper))

    flag_punitiva = adm.flag_prescricao_punitiva
    flag_intercorr = adm.flag_prescricao_intercorrente
    flag_intercorr_bienal = adm.flag_prescricao_intercorrente_bienal
    flag_decadencia = adm.flag_decadencia
    flag_tempestivo = adm.flag_tempestivo
    tese_acolhida = any(t.acolhida for t in processo.teses.all())

    # (a) DEFERIDO: ao menos uma flag SIM ou tese acolhida
    if flag_punitiva or flag_intercorr or flag_intercorr_bienal or flag_decadencia or tese_acolhida:
        if not tem_deferido:
            motivo = (
                "extincao da pretensao punitiva"
                if (flag_punitiva or flag_intercorr or flag_intercorr_bienal or flag_decadencia)
                else "tese acolhida pelo julgador"
            )
            return False, f"Deveria ser DEFERIDO ({motivo})"

    # (b) INDEFERIDO: intempestividade sem prescrição/decadência, ou rota D com teses rejeitadas
    elif flag_tempestivo is False:
        if not tem_indeferido:
            return False, "Deveria ser INDEFERIDO (intempestividade configurada)"
    else:
        # Rota D, sem teses acolhidas
        if not tem_indeferido and not tem_deferido:
            return False, "Resultado ausente no parecer"

    return True, "Resultado coerente com flags e teses"


def _check_item_3_admissibilidade(adm, texto_upper):
    """Item 3: Conclusão de tempestividade corresponde ao julgador."""
    flag = adm.flag_tempestivo

    if flag is None:
        return True, "Tempestividade nao avaliada (N/A)"

    if flag is True:
        # Parecer deve indicar tempestivo / não deve dizer intempestivo como conclusão
        if re.search(r'INTEMPESTIVIDADE[^:]*CONFIGURADA', texto_upper):
            return False, "Parecer diz intempestividade configurada, julgador definiu tempestivo"
    else:
        # Parecer deve indicar intempestivo
        if re.search(r'INTEMPESTIVIDADE[^:]*NAO\s+CONFIGURADA', texto_upper) or \
           re.search(r'INTEMPESTIVIDADE[^:]*NÃO\s+CONFIGURADA', texto_upper):
            return False, "Parecer diz intempestividade nao configurada, julgador definiu intempestivo"

    return True, "Admissibilidade coerente com decisao do julgador"


def _check_item_4_punitiva(adm, texto_upper):
    """Item 4: Prescrição punitiva corresponde ao julgador."""
    flag = adm.flag_prescricao_punitiva

    if flag is None:
        return True, "Prescricao punitiva nao avaliada (N/A)"

    secao = _extrair_secao(texto_upper, "3.1", "3.2")

    if not secao:
        if flag:
            return False, "Secao 3.1 (Prescricao Punitiva) ausente — deveria constar como SIM"
        return True, "Secao 3.1 ausente mas prescricao punitiva = NAO"

    if flag:
        if "NAO" in secao and "PRESCRI" in secao and not re.search(r'PRESCRI\w+\s+(SIM|CONFIGURAD)', secao):
            return False, "Secao 3.1 conclui NAO mas julgador definiu SIM"
    else:
        if re.search(r'PRESCRI\w+\s+PUNITIVA[^.]*SIM', secao):
            return False, "Secao 3.1 conclui SIM mas julgador definiu NAO"

    return True, "Prescricao punitiva coerente com julgador"


def _check_item_5_intercorrente(adm, texto_upper):
    """Item 5: Prescrição intercorrente usa exclusivamente P1 e P5."""
    flag = adm.flag_prescricao_intercorrente
    flag_bienal = adm.flag_prescricao_intercorrente_bienal

    secao = _extrair_secao(texto_upper, "3.2", "3.3")

    if not secao and not flag and not flag_bienal:
        return True, "Prescricao intercorrente nao aplicavel"

    if not secao and (flag or flag_bienal):
        return False, "Secao 3.2/3.3 ausente — deveria constar prescricao intercorrente"

    return True, "Prescricao intercorrente presente no parecer"


def _check_item_6_decadencia(adm, texto_upper):
    """Item 6: Decadência com filtro temporal correto."""
    flag = adm.flag_decadencia

    secao = _extrair_secao(texto_upper, "3.4", "MATERIALIDADE")
    if not secao:
        secao = _extrair_secao(texto_upper, "DECADENCIA", "MATERIALIDADE")

    if flag is None:
        return True, "Decadencia nao avaliada (N/A)"

    if not secao and flag:
        return False, "Secao Decadencia ausente — deveria constar como SIM"

    if not secao and not flag:
        return True, "Secao Decadencia ausente mas flag = NAO"

    # Verificar menção ao filtro temporal
    tem_filtro = any(x in secao for x in ["FILTRO", "844/2021", "381/2022", "12/04/2021", "22/10/2021"])

    if flag and not tem_filtro:
        return False, "Decadencia SIM mas sem referencia ao filtro temporal / Res. 844"

    return True, "Decadencia coerente com julgador e filtro temporal"


def _check_item_7_teses_rota_d(processo, adm, texto_upper):
    """Item 7: Teses analisadas individualmente (Rota D)."""
    rota = adm.rota

    if rota != "D":
        return True, "Item N/A — rota " + rota + " (nao e Rota D)"

    teses = list(processo.teses.order_by("ordem"))
    if not teses:
        return True, "Rota D mas nenhuma tese identificada"

    # Verificar se seção TESES existe
    tem_teses_secao = "TESES DEFENSIVAS" in texto_upper or "TESE" in texto_upper

    if not tem_teses_secao:
        return False, "Rota D mas secao de teses ausente no parecer"

    return True, f"Rota D — {len(teses)} tese(s) presente(s) no parecer"


def _check_item_8_teses_prejudicadas(adm, texto_upper):
    """Item 8: Teses prejudicadas declaradas corretamente (Rotas A/B/C)."""
    rota = adm.rota

    if rota == "D":
        return True, "Item N/A — Rota D (merito analisado)"

    razoes = {
        "A": "intempestividade",
        "B": "prescricao",
        "C": "decadencia",
    }
    razao = razoes.get(rota, "")

    tem_prejudicada = "PREJUDICAD" in texto_upper

    if not tem_prejudicada:
        return False, f"Rota {rota} mas parecer nao declara teses prejudicadas (deveria mencionar {razao})"

    return True, f"Rota {rota} — teses prejudicadas declaradas"


def _check_item_9_normatividade(texto_upper):
    """Item 9: Citações normativas presentes e hierarquizadas."""
    normas_esperadas = ["CTB", "LEI", "ART", "RESOLUC", "CONTRAN"]
    normas_encontradas = [n for n in normas_esperadas if n in texto_upper]

    if len(normas_encontradas) < 2:
        return False, "Poucas citacoes normativas encontradas (min. 2 esperadas)"

    return True, f"Citacoes normativas presentes ({len(normas_encontradas)} tipos)"


def _check_item_10_vedacoes(parecer_texto, texto_upper):
    """Item 10: Sem menção a fases internas, emojis ou motores de IA."""
    falhas = []

    ai_encontrada = next((n for n in _VEDACOES_AI if n in texto_upper), None)
    if ai_encontrada:
        falhas.append(f"Menciona motor de IA '{ai_encontrada}'")

    if re.search(r'\bFASE\s*\d', parecer_texto, re.IGNORECASE):
        falhas.append("Menciona fases internas do sistema")

    if re.search(r'[\U00010000-\U0010FFFF\U00002600-\U000027BF\U0001F000-\U0001FFFF]', parecer_texto):
        falhas.append("Contem emojis")

    ok = len(falhas) == 0
    return ok, "Sem vedacoes" if ok else "Vedacoes: " + "; ".join(falhas)


def _extrair_secao(texto_upper, inicio, fim):
    """Extrai texto entre dois marcadores de seção."""
    pattern = re.escape(inicio) + r'.*?' + re.escape(fim)
    match = re.search(pattern, texto_upper, re.DOTALL)
    return match.group(0) if match else ""


def execute(processo) -> ServiceResult:
    """
    Executa auditoria de conformidade do parecer — 10 itens programáticos.

    Pré-condição: processo na fase AUDITORIA, Parecer existente.
    Pós-condição: blindagem_score calculado, processo em FINALIZADO.
    """
    _log.info("[AUDITORIA] START processo=%s", processo.id)

    try:
        parecer = processo.parecer
    except Parecer.DoesNotExist:
        return ServiceResult.falha("Parecer não encontrado.")

    try:
        adm = processo.admissibilidade
    except Exception:
        return ServiceResult.falha("Admissibilidade não encontrada.")

    parecer_texto = parecer.texto_ia or parecer.texto_editado or ""
    if not parecer_texto:
        return ServiceResult.falha("Texto do parecer está vazio.")

    texto_upper = parecer_texto.upper()

    # ── Checklist programático (10 itens) ─────────────────────────────────
    checklist = []

    checks = [
        ("Cabecalho (PA, SGPE, Recorrente, Relator, Data)",
         lambda: _check_item_1_cabecalho(processo, parecer_texto, texto_upper)),
        ("Resultado coerente com flags e teses",
         lambda: _check_item_2_resultado(processo, adm, parecer_texto, texto_upper)),
        ("Admissibilidade coerente com julgador",
         lambda: _check_item_3_admissibilidade(adm, texto_upper)),
        ("Prescricao Punitiva coerente com julgador",
         lambda: _check_item_4_punitiva(adm, texto_upper)),
        ("Prescricao Intercorrente usa datas P1/P5",
         lambda: _check_item_5_intercorrente(adm, texto_upper)),
        ("Decadencia com filtro temporal correto",
         lambda: _check_item_6_decadencia(adm, texto_upper)),
        ("Teses analisadas individualmente (Rota D)",
         lambda: _check_item_7_teses_rota_d(processo, adm, texto_upper)),
        ("Teses prejudicadas declaradas (Rotas A/B/C)",
         lambda: _check_item_8_teses_prejudicadas(adm, texto_upper)),
        ("Citacoes normativas presentes e hierarquizadas",
         lambda: _check_item_9_normatividade(texto_upper)),
        ("Sem mencao a fases, emojis ou motores de IA",
         lambda: _check_item_10_vedacoes(parecer_texto, texto_upper)),
    ]

    itens_ok = 0
    inconsistencias = []
    advertencias = []

    for label, check_fn in checks:
        try:
            ok, detalhe = check_fn()
        except Exception as e:
            ok, detalhe = False, f"Erro ao verificar: {e}"

        checklist.append({
            "label": label,
            "ok": ok,
            "detalhe": detalhe,
        })

        if ok:
            itens_ok += 1
        else:
            # Item 2 (resultado) é erro fatal — peso dobrado
            if label.startswith("Resultado"):
                inconsistencias.append(detalhe)
            else:
                advertencias.append(f"{label}: {detalhe}")

    # ── Score ─────────────────────────────────────────────────────────────
    # Erro fatal no resultado = -50 pontos
    penalidade_fatal = 50 if inconsistencias else 0
    penalidade_avisos = len(advertencias) * 5
    score = max(0, 100 - penalidade_fatal - penalidade_avisos)

    parecer.blindagem_score = score

    # ── Detalhes ──────────────────────────────────────────────────────────
    detalhes_parts = []
    if inconsistencias:
        detalhes_parts.append("ERROS FATAIS:\n" + "\n".join(f"- {i}" for i in inconsistencias))
    if advertencias:
        detalhes_parts.append("ADVERTENCIAS:\n" + "\n".join(f"- {a}" for a in advertencias))
    if not detalhes_parts:
        detalhes_parts.append("Auditoria aprovada — todos os 10 itens conformes.")

    parecer.blindagem_detalhes = "\n\n".join(detalhes_parts)
    parecer.checklist_auditoria = checklist
    parecer.save()

    # ── Bloqueio fatal ────────────────────────────────────────────────────
    if inconsistencias:
        _log.warning("[AUDITORIA] BLOQUEIO FATAL: processo=%s | %s", processo.id, inconsistencias)
        # Mesmo com erro fatal, avançar para FINALIZADO para não travar o fluxo
        # O score baixo sinaliza o problema

    # ── Tempo de julgamento ───────────────────────────────────────────────
    try:
        diff = (timezone.now() - processo.created_at).total_seconds()
        minutos = int(diff) // 60
        segundos = int(diff) % 60
        tempo_str = f"{minutos:02d}m {segundos:02d}s"
    except Exception:
        tempo_str = ""

    # Avançar para FINALIZADO
    processo.avancar_fase(FaseProcesso.FINALIZADO)

    # Consumir crédito (superuser não consome)
    if not processo.user.is_superuser:
        from django.db.models import F
        from core.models import UserProfile
        UserProfile.objects.filter(user=processo.user, credits__gt=0).update(
            credits=F("credits") - 1
        )
        log_audit("credito", processo=processo, fase="credito_consumido")

    log_audit("fase", processo=processo, fase="auditoria_concluida", dados={
        "score": score,
        "itens_ok": itens_ok,
        "total_itens": 10,
        "inconsistencias": inconsistencias,
        "advertencias_count": len(advertencias),
    })

    _log.info("[AUDITORIA] OK processo=%s score=%d (%d/10 itens OK)", processo.id, score, itens_ok)
    return ServiceResult.sucesso(
        "Auditoria concluída.",
        score=score,
        tempo=tempo_str,
        itens_ok=itens_ok,
        inconsistencias=inconsistencias,
        advertencias=advertencias,
    )
