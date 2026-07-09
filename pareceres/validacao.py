"""
Gates de validação por transição de fase.

Cada função recebe o Processo (e opcionalmente o body do request)
e retorna uma lista de ErroValidacao. Lista vazia = OK para avançar.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass


@dataclass
class ErroValidacao:
    campo: str
    mensagem: str
    solucao: str
    bloqueante: bool = False  # True = não permite forçar avanço


def erros_to_response(erros: list[ErroValidacao]) -> dict:
    """Converte lista de erros para payload JSON padronizado."""
    bloqueantes = any(e.bloqueante for e in erros)
    return {
        "validation_errors": [asdict(e) for e in erros],
        "pode_forcar": not bloqueantes,
    }


# ── 1→2: Upload de documentos ───────────────────────────────────────────────


def validar_upload_documentos(processo) -> list[ErroValidacao]:
    erros = []
    has_consolidado = processo.documentos.filter(tipo="consolidado").exists()
    if not has_consolidado:
        erros.append(ErroValidacao(
            campo="consolidado",
            mensagem="PDF Consolidado não foi enviado.",
            solucao="Faça o upload do PDF Consolidado antes de avançar.",
        ))
    return erros


# ── 2→3: Dados extraídos → Admissibilidade ──────────────────────────────────


def validar_dados_extraidos(processo) -> list[ErroValidacao]:
    erros = []

    if not processo.data_sessao:
        erros.append(ErroValidacao(
            campo="data_sessao",
            mensagem="Data da Sessão não informada.",
            solucao="Preencha a Data da Sessão de Julgamento.",
        ))

    if not processo.recorrente or not processo.recorrente.strip():
        erros.append(ErroValidacao(
            campo="recorrente",
            mensagem="Nome do Recorrente não informado.",
            solucao="Preencha o nome do Recorrente.",
        ))

    if not processo.prazo_final:
        erros.append(ErroValidacao(
            campo="prazo_final",
            mensagem="Prazo Final para recurso JARI não informado.",
            solucao="Preencha o Prazo Final para interposição do recurso.",
        ))

    if not processo.data_protocolo:
        erros.append(ErroValidacao(
            campo="data_protocolo",
            mensagem="Data de Protocolo do recurso JARI não informada.",
            solucao="Preencha a Data de Protocolo do recurso.",
        ))

    if not processo.paginas_defesa or not processo.paginas_defesa.strip():
        erros.append(ErroValidacao(
            campo="paginas_defesa",
            mensagem="Páginas da Defesa não informadas.",
            solucao="Informe as páginas da defesa recursal (ex: 15-30).",
        ))

    # Data da infração (vive na Admissibilidade). Só a AUSÊNCIA da Admissibilidade
    # (reverse OneToOne) conta como "não informada"; erro de infra deve propagar,
    # não virar um falso "data não informada".
    from django.core.exceptions import ObjectDoesNotExist
    try:
        sem_data_infracao = not processo.admissibilidade.data_infracao
    except ObjectDoesNotExist:
        sem_data_infracao = True
    if sem_data_infracao:
        erros.append(ErroValidacao(
            campo="data_infracao",
            mensagem="Data da Infração não informada.",
            solucao="Consulte o PDF e preencha a Data da Infração.",
        ))

    return erros


# ── 3→4/5: Admissibilidade confirmação ───────────────────────────────────────


LIMIAR_FILTRO1 = _dt.date(2021, 4, 12)
LIMIAR_FILTRO2 = _dt.date(2021, 10, 22)


def validar_admissibilidade(processo, body: dict | None = None) -> list[ErroValidacao]:
    """Valida dados antes de confirmar admissibilidade.

    Se body é fornecido, valida os campos do body (pre-save).
    Se body é None, valida o estado salvo na Admissibilidade.
    """
    erros = []

    try:
        adm = processo.admissibilidade
    except Exception:
        erros.append(ErroValidacao(
            campo="admissibilidade",
            mensagem="Admissibilidade não foi calculada.",
            solucao="Aguarde o cálculo da admissibilidade ou clique em Recalcular.",
            bloqueante=True,
        ))
        return erros

    if not adm.texto_resultado:
        erros.append(ErroValidacao(
            campo="texto_resultado",
            mensagem="Cálculo de admissibilidade não foi concluído.",
            solucao="Aguarde o cálculo ou clique em Recalcular.",
            bloqueante=True,
        ))

    # Validar que todos os 5 campos julgador_* estão definidos
    if body is not None:
        campos_julgador = {
            "julgador_tempestivo": "Tempestividade",
            "julgador_punitiva": "Prescrição Punitiva",
            "julgador_intercorrente": "Prescrição Intercorrente Trienal",
            "julgador_intercorrente_bienal": "Prescrição Intercorrente Bienal",
            "julgador_decadencia": "Decadência",
        }
        for campo, nome in campos_julgador.items():
            valor = body.get(campo)
            if valor is None or valor == "":
                erros.append(ErroValidacao(
                    campo=campo,
                    mensagem=f"Decisão de {nome} não foi informada.",
                    solucao=f"Selecione Confirmar ou Afastar para {nome}.",
                ))

    # Bloqueios CETRAN/SC (hard-coded, bloqueantes)
    julgador_decadencia = None
    if body is not None:
        val = body.get("julgador_decadencia")
        if val is not None and val != "":
            julgador_decadencia = val in (True, "true", "True")
    elif adm.julgador_decadencia is not None:
        julgador_decadencia = adm.julgador_decadencia

    if julgador_decadencia is True and adm.data_infracao:
        if adm.data_infracao < LIMIAR_FILTRO1:
            erros.append(ErroValidacao(
                campo="julgador_decadencia",
                mensagem=(
                    "Decadência não pode ser reconhecida para infrações "
                    "anteriores a 12/04/2021 (Parecer CETRAN/SC 381/2022)."
                ),
                solucao="Corrija a decisão de Decadência para NÃO.",
                bloqueante=True,
            ))

        elif (adm.data_infracao < LIMIAR_FILTRO2
              and adm.tipo_penalidade
              and adm.tipo_penalidade.lower() in ("suspensao", "cassacao")):
            erros.append(ErroValidacao(
                campo="julgador_decadencia",
                mensagem=(
                    "Decadência não se aplica a penalidades de suspensão/cassação "
                    "no período de 12/04/2021 a 21/10/2021 (Nota CETRAN/SC 02/03/2023)."
                ),
                solucao="Corrija a decisão de Decadência para NÃO.",
                bloqueante=True,
            ))

    return erros


# ── 4→5: Teses confirmação ──────────────────────────────────────────────────


def validar_teses(processo, decisoes: dict | None = None) -> list[ErroValidacao]:
    """Valida que todas as teses têm decisão de acolhida."""
    erros = []
    teses = list(processo.teses.all())

    if not teses:
        erros.append(ErroValidacao(
            campo="teses",
            mensagem="Nenhuma tese encontrada para este processo.",
            solucao="Aguarde a extração de teses ou clique em Tentar Novamente.",
        ))
        return erros

    for tese in teses:
        tese_id = str(tese.id)
        if decisoes is not None:
            if tese_id not in decisoes:
                erros.append(ErroValidacao(
                    campo=f"tese_{tese.id}",
                    mensagem=f"Tese \"{tese.titulo[:60]}\" sem decisão.",
                    solucao="Selecione Acolhida ou Não Acolhida para esta tese.",
                ))
        else:
            if tese.acolhida is None:
                erros.append(ErroValidacao(
                    campo=f"tese_{tese.id}",
                    mensagem=f"Tese \"{tese.titulo[:60]}\" sem decisão.",
                    solucao="Selecione Acolhida ou Não Acolhida para esta tese.",
                ))

    return erros


# ── 5→6: Parecer → Auditoria ────────────────────────────────────────────────


def validar_parecer(processo) -> list[ErroValidacao]:
    erros = []

    try:
        parecer = processo.parecer
    except Exception:
        erros.append(ErroValidacao(
            campo="parecer",
            mensagem="Parecer não foi gerado.",
            solucao="Aguarde a geração do parecer ou clique em Regenerar.",
            bloqueante=True,
        ))
        return erros

    if not parecer.conteudo_final:
        erros.append(ErroValidacao(
            campo="conteudo",
            mensagem="Parecer está vazio.",
            solucao="Aguarde a geração do parecer ou edite manualmente.",
            bloqueante=True,
        ))

    if not processo.resultado_final:
        erros.append(ErroValidacao(
            campo="resultado_final",
            mensagem="Resultado final (DEFERIDO/INDEFERIDO) não definido.",
            solucao="O resultado deve ser definido automaticamente pelo sistema.",
            bloqueante=True,
        ))

    return erros


# ── 6→Finalizado: Auditoria ─────────────────────────────────────────────────


def validar_auditoria(processo) -> list[ErroValidacao]:
    erros = []

    try:
        parecer = processo.parecer
    except Exception:
        erros.append(ErroValidacao(
            campo="parecer",
            mensagem="Parecer não encontrado.",
            solucao="Volte e gere o parecer antes de finalizar.",
            bloqueante=True,
        ))
        return erros

    if not parecer.checklist_auditoria:
        erros.append(ErroValidacao(
            campo="checklist_auditoria",
            mensagem="Checklist de auditoria não foi gerado.",
            solucao="Aguarde a auditoria ou clique em Auditar novamente.",
        ))

    if parecer.blindagem_score is None:
        erros.append(ErroValidacao(
            campo="blindagem_score",
            mensagem="Score de blindagem não foi calculado.",
            solucao="Aguarde a auditoria ou clique em Auditar novamente.",
        ))

    return erros
