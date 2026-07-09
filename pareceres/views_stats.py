"""
Dashboard de estatísticas — versão limpa para o novo P-JARI.
"""

import calendar
import json
from datetime import date

from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone

from .models import AuditLog, Parecer, Processo


def _parse_mes_ano(request):
    hoje = timezone.localtime(timezone.now()).date()
    try:
        mes = int(request.GET.get("mes", hoje.month))
        ano = int(request.GET.get("ano", hoje.year))
    except (ValueError, TypeError):
        mes, ano = hoje.month, hoje.year
    if not (1 <= mes <= 12) or not (2000 <= ano <= 2100):
        mes, ano = hoje.month, hoje.year
    return mes, ano


@login_required
def estatisticas_view(request):
    """Dashboard pessoal do usuário."""
    mes, ano = _parse_mes_ano(request)
    _, ultimo_dia = calendar.monthrange(ano, mes)

    base = Processo.objects.filter(
        user=request.user,
        created_at__year=ano,
        created_at__month=mes,
    )

    finalizados = base.exclude(resultado_final="")

    total_julgados = finalizados.count()
    tempo_poupado_horas = (total_julgados * 40) // 60

    # Deferidos / Indeferidos
    deferidos = finalizados.filter(resultado_final="DEFERIDO").count()
    indeferidos = finalizados.filter(resultado_final="INDEFERIDO").count()
    total_finais = deferidos + indeferidos
    taxa_deferimento = round((deferidos / total_finais) * 100) if total_finais > 0 else 0
    taxa_indeferimento = 100 - taxa_deferimento if total_finais > 0 else 0

    # Timeline diária
    por_dia = (
        base.annotate(data=TruncDate("created_at"))
        .values("data")
        .annotate(total=Count("id"))
        .order_by("data")
    )
    dados_dict = {item["data"]: item["total"] for item in por_dia}
    datas = []
    totais = []
    for dia in range(1, ultimo_dia + 1):
        d = date(ano, mes, dia)
        datas.append(d.strftime("%d/%m"))
        totais.append(dados_dict.get(d, 0))

    # Blindagem média
    score_medio = (
        Parecer.objects.filter(
            processo__user=request.user,
            processo__created_at__year=ano,
            processo__created_at__month=mes,
            blindagem_score__isnull=False,
        ).aggregate(avg=Avg("blindagem_score"))["avg"]
    )
    score_medio = round(score_medio or 0, 1)

    # Infrações top 5
    radar = list(
        base.filter(admissibilidade__infracao_documento__gt="")
        .values("admissibilidade__infracao_documento")
        .annotate(total=Count("id"))
        .order_by("-total")[:5]
    )
    radar_infracoes = []
    if radar:
        max_val = radar[0]["total"]
        for item in radar:
            nome = item["admissibilidade__infracao_documento"].strip().upper()
            if len(nome) > 45:
                nome = nome[:42] + "..."
            pct = int((item["total"] / max_val) * 100) if max_val > 0 else 0
            radar_infracoes.append({"nome": nome, "total": item["total"], "pct": pct})

    # Créditos
    try:
        creditos = request.user.profile.credits
        is_pro = request.user.profile.is_pro
    except Exception:
        creditos = 0
        is_pro = False

    return render(request, "pareceres/dashboard.html", {
        "total_julgados": total_julgados,
        "tempo_poupado_horas": tempo_poupado_horas,
        "deferidos": deferidos,
        "indeferidos": indeferidos,
        "taxa_deferimento": taxa_deferimento,
        "taxa_indeferimento": taxa_indeferimento,
        "donut_series": json.dumps([deferidos, indeferidos]),
        "grafico_datas": json.dumps(datas),
        "grafico_totais": json.dumps(totais),
        "score_medio": score_medio,
        "radar_infracoes": radar_infracoes,
        "creditos": creditos,
        "is_pro": is_pro,
        "ano_selecionado": ano,
        "mes_selecionado": mes,
        "mes_ano_input": f"{ano}-{mes:02d}",
    })


@login_required
def estatisticas_gerais_view(request):
    """Dashboard global — apenas admin/superuser ou can_view_global_stats."""
    if not request.user.is_superuser:
        if not getattr(getattr(request.user, "profile", None), "can_view_global_stats", False):
            return HttpResponseForbidden("Acesso negado.")

    mes, ano = _parse_mes_ano(request)
    _, ultimo_dia = calendar.monthrange(ano, mes)

    base = Processo.objects.filter(created_at__year=ano, created_at__month=mes)
    finalizados = base.exclude(resultado_final="")

    total_julgados = finalizados.count()
    tempo_poupado_horas = (total_julgados * 40) // 60

    from django.contrib.auth.models import User
    total_usuarios = User.objects.filter(is_superuser=False).count()

    # Deferidos / Indeferidos
    deferidos = finalizados.filter(resultado_final="DEFERIDO").count()
    indeferidos = finalizados.filter(resultado_final="INDEFERIDO").count()
    total_finais = deferidos + indeferidos
    taxa_deferimento = round((deferidos / total_finais) * 100) if total_finais > 0 else 0
    taxa_indeferimento = 100 - taxa_deferimento if total_finais > 0 else 0

    # Timeline
    por_dia = (
        base.annotate(data=TruncDate("created_at"))
        .values("data")
        .annotate(total=Count("id"))
        .order_by("data")
    )
    dados_dict = {item["data"]: item["total"] for item in por_dia}
    datas = []
    totais = []
    for dia in range(1, ultimo_dia + 1):
        d = date(ano, mes, dia)
        datas.append(d.strftime("%d/%m"))
        totais.append(dados_dict.get(d, 0))

    # Custos IA (do AuditLog)
    from datetime import datetime as dt

    from django.utils import timezone
    # Aware para filtrar AuditLog.timestamp (aware) sem RuntimeWarning/off-by-tz.
    inicio_mes = timezone.make_aware(dt(ano, mes, 1))
    fim_mes = timezone.make_aware(dt(ano, mes + 1, 1) if mes < 12 else dt(ano + 1, 1, 1))
    logs = AuditLog.objects.filter(
        categoria="ia_request",
        timestamp__gte=inicio_mes,
        timestamp__lt=fim_mes,
    )

    tokens_por_provider = {}
    for provider in ("gemini", "vertex", "perplexity", "anthropic"):
        agg = logs.filter(provider__icontains=provider).aggregate(
            in_t=Sum("input_tokens"),
            out_t=Sum("output_tokens"),
            count=Count("id"),
            avg_lat=Avg("latency_ms"),
        )
        tokens_por_provider[provider] = {
            "input": agg["in_t"] or 0,
            "output": agg["out_t"] or 0,
            "count": agg["count"] or 0,
            "avg_latency_ms": round(agg["avg_lat"] or 0),
        }

    # Blindagem
    score_medio = (
        Parecer.objects.filter(
            processo__created_at__year=ano,
            processo__created_at__month=mes,
            blindagem_score__isnull=False,
        ).aggregate(avg=Avg("blindagem_score"))["avg"]
    )
    score_medio = round(score_medio or 0, 1)

    inconsistencias = Parecer.objects.filter(
        processo__created_at__year=ano,
        processo__created_at__month=mes,
        blindagem_score__lt=100,
        blindagem_score__isnull=False,
    ).count()
    taxa_interceptacao = int((inconsistencias / total_julgados) * 100) if total_julgados > 0 else 0

    # Erros recentes
    ultimos_erros = list(
        AuditLog.objects.filter(
            categoria="erro",
            timestamp__gte=inicio_mes,
            timestamp__lt=fim_mes,
        ).order_by("-timestamp").values("timestamp", "fase", "dados")[:20]
    )

    return render(request, "pareceres/dashboard_global.html", {
        "total_julgados": total_julgados,
        "tempo_poupado_horas": tempo_poupado_horas,
        "total_usuarios": total_usuarios,
        "deferidos": deferidos,
        "indeferidos": indeferidos,
        "taxa_deferimento": taxa_deferimento,
        "taxa_indeferimento": taxa_indeferimento,
        "donut_series": json.dumps([deferidos, indeferidos]),
        "grafico_datas": json.dumps(datas),
        "grafico_totais": json.dumps(totais),
        "tokens_por_provider": tokens_por_provider,
        "score_medio": score_medio,
        "taxa_interceptacao": taxa_interceptacao,
        "ultimos_erros": ultimos_erros,
        "ano_selecionado": ano,
        "mes_selecionado": mes,
        "mes_ano_input": f"{ano}-{mes:02d}",
    })
