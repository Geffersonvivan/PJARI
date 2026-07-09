"""
View para Relatório Mensal de Votos.

Consolida todos os processos finalizados de uma pasta mensal
e gera relatório padronizado com 3 linhas por processo.
"""

import json
import logging
from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.utils.text import get_text_list
from django.views.decorators.http import require_POST

from .models import Pasta, Processo

_log = logging.getLogger(__name__)


def _flag_label(valor, nome_positivo, nome_negativo):
    """Converte flag booleana em texto descritivo."""
    if valor is True:
        return nome_positivo
    if valor is False:
        return nome_negativo
    return ""


def _montar_item(processo):
    """Monta o item do relatório no modelo: cabeçalho + síntese + votos.

    Tudo determinístico — sai direto das flags de Admissibilidade e do
    resultado_final. Sem LLM.
    """
    # Admissibilidade
    try:
        adm = processo.admissibilidade
    except Exception:
        adm = None

    # .all() usa o cache do prefetch_related("teses") do _contexto_relatorio;
    # a ordenação já vem de AnaliseTese.Meta.ordering = ["ordem"]. Evita N+1 (#5).
    teses = list(processo.teses.all())
    resultado = processo.resultado_final

    # ── Cabeçalho: PARECER: NOME / RESULTADO ────────────────────────────────
    numero = processo.pa or f"#{processo.pk}"
    recorrente = processo.recorrente or "Sem nome"
    # Label do resultado a partir dos próprios choices do model (#10) — não
    # duplica o enum; um novo resultado_final aparece automaticamente.
    resultado_label = (processo.get_resultado_final_display() or "—").upper()
    cabecalho = f"PARECER: {recorrente} / {resultado_label}"

    # ── Síntese (parágrafo único) ───────────────────────────────────────────
    tipo_pen = ""
    if adm and adm.tipo_penalidade:
        tipo_pen = adm.get_tipo_penalidade_display()

    partes = [f"Recurso contra {tipo_pen.lower() or 'penalidade de trânsito'}"]
    if adm:
        partes.append("tempestivo" if adm.flag_tempestivo else "intempestivo")
        partes.append(
            "com prescrição punitiva" if adm.flag_prescricao_punitiva
            else "sem prescrição punitiva"
        )
        partes.append(
            "com prescrição intercorrente trienal" if adm.flag_prescricao_intercorrente
            else "sem prescrição intercorrente trienal"
        )
        partes.append(
            "com prescrição bienal" if adm.flag_prescricao_intercorrente_bienal
            else "sem prescrição bienal"
        )
        partes.append("com decadência" if adm.flag_decadencia else "sem decadência")
    sintese = "; ".join(partes) + "."

    # Teses defensivas não acolhidas (só onde há teses rejeitadas)
    rejeitadas = [t for t in teses if t.acolhida is False]
    if rejeitadas:
        titulos = get_text_list([t.titulo.strip() for t in rejeitadas if t.titulo], "e")
        motivos = get_text_list(
            [t.fundamentacao.strip() for t in rejeitadas if t.fundamentacao], "e"
        )
        frase = f" Teses defensivas {titulos} não acolhidas"
        if motivos:
            frase += f", em razão de {motivos}"
        sintese += frase + "."

    # Override manual da síntese (editado inline no relatório) prevalece.
    sintese_editada = (processo.relatorio_sintese_editada or "").strip()
    editado = bool(sintese_editada)
    if editado:
        sintese = sintese_editada

    # ── Votos (conhecimento + mérito) ───────────────────────────────────────
    if resultado == "NAO_CONHECIDO":
        voto_principal = "Voto pelo não conhecimento."
        voto_subsidiario = "Subsidiariamente, voto pelo indeferimento."
    else:
        voto_principal = "Voto pelo conhecimento."
        if resultado == "DEFERIDO":
            voto_subsidiario = "Voto pelo deferimento."
        else:
            voto_subsidiario = "Voto pelo indeferimento."

    return {
        "processo": processo,
        "numero": numero,
        "cabecalho": cabecalho,
        "sintese": sintese,
        "editado": editado,
        "voto_principal": voto_principal,
        "voto_subsidiario": voto_subsidiario,
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


@login_required
@require_POST
def api_relatorio_sintese_editar(request, pk):
    """Salva (ou limpa) o override manual da síntese do relatório do processo.

    texto vazio → remove o override e o relatório volta a gerar a síntese
    automática. Retorna {ok, editado}.
    """
    processo = get_object_or_404(Processo, pk=pk, user=request.user)
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)
    texto = (body.get("sintese") or "").strip()
    processo.relatorio_sintese_editada = texto
    processo.save(update_fields=["relatorio_sintese_editada"])
    return JsonResponse({"ok": True, "editado": bool(texto)})
