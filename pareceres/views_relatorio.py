"""
View para Relatório Mensal de Votos.

Consolida todos os processos finalizados de uma pasta mensal
e gera relatório padronizado com 3 linhas por processo.
"""

import logging
from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string

from .models import Pasta

_log = logging.getLogger(__name__)


def _flag_label(valor, nome_positivo, nome_negativo):
    """Converte flag booleana em texto descritivo."""
    if valor is True:
        return nome_positivo
    if valor is False:
        return nome_negativo
    return ""


def _montar_item(processo):
    """Monta as 3 linhas do relatório para um processo."""
    # Admissibilidade
    try:
        adm = processo.admissibilidade
    except Exception:
        adm = None

    # Teses
    teses = list(processo.teses.order_by("ordem"))

    # Linha 1: Identificação
    numero = processo.pa or f"#{processo.pk}"
    recorrente = processo.recorrente or "Sem nome"
    linha1 = f"Parecer {numero} — {recorrente}"

    # Linha 2: Síntese do caso
    tipo_pen = ""
    if adm and adm.tipo_penalidade:
        tipo_pen = adm.get_tipo_penalidade_display()

    teses_resumo = []
    for t in teses:
        # Pegar apenas as primeiras palavras do título
        titulo_curto = t.titulo[:80]
        if len(t.titulo) > 80:
            titulo_curto += "..."
        teses_resumo.append(titulo_curto)

    if teses_resumo:
        defesa_texto = f"defesa alegou {'; '.join(teses_resumo).lower()}"
    else:
        defesa_texto = "sem teses defensivas identificadas"

    linha2 = f"Recurso contra {tipo_pen.lower() or 'penalidade de trânsito'}; {defesa_texto}."

    # Linha 3: Bloco técnico
    partes = []
    partes.append(tipo_pen or "Penalidade de trânsito")

    if adm:
        # Tempestividade
        temp = adm.flag_tempestivo
        partes.append("tempestivo" if temp else "intempestivo")

        # Prescrição punitiva
        if adm.flag_prescricao_punitiva:
            partes.append("com prescrição punitiva")
        else:
            partes.append("sem prescrição punitiva")

        # Intercorrente trienal
        if adm.flag_prescricao_intercorrente:
            partes.append("com intercorrente trienal")
        else:
            partes.append("sem intercorrente trienal")

        # Bienal
        if adm.flag_prescricao_intercorrente_bienal:
            partes.append("com bienal")
        else:
            partes.append("sem bienal")

        # Decadência
        if adm.flag_decadencia:
            partes.append("com decadência")
        else:
            partes.append("sem decadência")

    # Teses acolhidas/rejeitadas
    acolhidas = sum(1 for t in teses if t.acolhida is True)
    rejeitadas = sum(1 for t in teses if t.acolhida is False)
    if acolhidas and rejeitadas:
        partes.append(f"{acolhidas} tese(s) acolhida(s), {rejeitadas} rejeitada(s)")
    elif acolhidas:
        partes.append("teses acolhidas")
    elif rejeitadas:
        partes.append("teses rejeitadas")

    # Voto
    resultado = processo.resultado_final
    if resultado == "DEFERIDO":
        partes.append("voto pelo deferimento")
    elif resultado == "INDEFERIDO":
        partes.append("voto pelo indeferimento")
    elif resultado == "NAO_CONHECIDO":
        partes.append("voto pelo não conhecimento")

    linha3 = "; ".join(partes)

    return {
        "processo": processo,
        "linha1": linha1,
        "linha2": linha2,
        "linha3": linha3,
        "resultado": resultado,
    }


def _contexto_relatorio(user, pasta_id):
    """Monta contexto compartilhado entre view HTML e PDF."""
    pasta = Pasta.objects.filter(pk=pasta_id, user=user).first()
    if not pasta:
        raise Http404

    processos = (
        pasta.processos
        .exclude(resultado_final="")
        .select_related("admissibilidade")
        .prefetch_related("teses")
        .order_by("created_at")
    )

    itens = [_montar_item(p) for p in processos]

    total = len(itens)
    deferidos = sum(1 for i in itens if i["resultado"] == "DEFERIDO")
    indeferidos = sum(1 for i in itens if i["resultado"] == "INDEFERIDO")
    nao_conhecidos = sum(1 for i in itens if i["resultado"] == "NAO_CONHECIDO")

    nome_mes = pasta.nome
    partes = nome_mes.split(" - ", 1)
    mes_label = partes[1] if len(partes) > 1 else nome_mes
    ano = date.today().year

    return {
        "pasta": pasta,
        "mes_label": mes_label,
        "ano": ano,
        "itens": itens,
        "total": total,
        "deferidos": deferidos,
        "indeferidos": indeferidos,
        "nao_conhecidos": nao_conhecidos,
    }


@login_required
def relatorio_mensal(request, pasta_id):
    """Gera relatório mensal de votos (HTML)."""
    ctx = _contexto_relatorio(request.user, pasta_id)
    return render(request, "pareceres/relatorio_mensal.html", ctx)


@login_required
def relatorio_mensal_pdf(request, pasta_id):
    """Gera relatório mensal de votos (PDF via WeasyPrint)."""
    ctx = _contexto_relatorio(request.user, pasta_id)

    html = render_to_string("pareceres/relatorio_mensal_pdf.html", ctx)

    try:
        from weasyprint import HTML
        pdf_bytes = HTML(string=html).write_pdf()
    except Exception as e:
        _log.error("[RELATORIO-PDF] Erro WeasyPrint: %s", e, exc_info=True)
        return HttpResponse("Erro ao gerar PDF.", status=500)

    filename = f"relatorio_{ctx['mes_label'].lower()}_{ctx['ano']}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
