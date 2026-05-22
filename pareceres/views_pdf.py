"""
Exportação PDF do parecer técnico.
Usa WeasyPrint para renderizar HTML → PDF.
"""

import logging

import markdown
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string

from .models import ConfiguracaoParecer, Parecer, Processo

_log = logging.getLogger(__name__)


@login_required
def exportar_parecer_pdf(request, pk):
    """Gera PDF do parecer técnico e retorna como download."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)

    try:
        parecer = processo.parecer
    except Parecer.DoesNotExist:
        return HttpResponse("Parecer não disponível.", status=404)

    if not parecer.conteudo_final:
        return HttpResponse("Parecer sem conteúdo.", status=404)

    # Configuração global (cabeçalho/rodapé)
    try:
        config = ConfiguracaoParecer.objects.first()
    except ConfiguracaoParecer.DoesNotExist:
        config = None

    rodape = ""
    if config:
        if processo.resultado_final == "DEFERIDO":
            rodape = config.rodape_deferido
        else:
            rodape = config.rodape_indeferido

    cabecalho_url = ""
    if config and config.cabecalho_imagem:
        cabecalho_url = request.build_absolute_uri(config.cabecalho_imagem.url)

    # Converter Markdown → HTML
    conteudo_html = markdown.markdown(
        parecer.conteudo_final,
        extensions=["tables", "nl2br"],
    )

    # Renderizar HTML do parecer
    html_string = render_to_string("pareceres/parecer_pdf.html", {
        "processo": processo,
        "parecer": parecer,
        "conteudo_html": conteudo_html,
        "cabecalho_url": cabecalho_url,
        "rodape": rodape,
    })

    try:
        from weasyprint import HTML
        pdf_bytes = HTML(string=html_string, base_url=request.build_absolute_uri("/")).write_pdf()
    except ImportError:
        _log.error("WeasyPrint não instalado. Instale com: pip install weasyprint")
        return HttpResponse("Exportação PDF indisponível (WeasyPrint não instalado).", status=500)
    except Exception:
        _log.exception("Erro ao gerar PDF")
        return HttpResponse("Erro ao gerar PDF.", status=500)

    filename = f"parecer_{processo.pa or processo.pk}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
