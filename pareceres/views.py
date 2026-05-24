import json
import logging
import os
import urllib.parse

from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.http import Http404, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .estado import FaseProcesso
from .models import (
    Admissibilidade, BancoTese, ChatMessage, ComentarioForum, Documento,
    Parecer, Pasta, PostForum, Processo, log_audit,
)
from .validacao import (
    erros_to_response,
    validar_admissibilidade,
    validar_dados_extraidos,
    validar_parecer,
    validar_teses,
)

_log = logging.getLogger(__name__)


# ── Páginas HTML ──────────────────────────────────────────────────────────────


MESES = [
    (1, "Janeiro"), (2, "Fevereiro"), (3, "Março"), (4, "Abril"),
    (5, "Maio"), (6, "Junho"), (7, "Julho"), (8, "Agosto"),
    (9, "Setembro"), (10, "Outubro"), (11, "Novembro"), (12, "Dezembro"),
]


def _garantir_pastas_mensais(user):
    """Cria as 12 pastas mensais se não existirem."""
    existentes = set(user.pastas.values_list("nome", flat=True))
    for pos, (num, nome) in enumerate(MESES):
        label = f"{num:02d} - {nome}"
        if label not in existentes:
            Pasta.objects.create(user=user, nome=label, posicao=pos)


@login_required
def home(request):
    """Página inicial — 12 pastas mensais com processos finalizados."""
    _garantir_pastas_mensais(request.user)
    pastas = (
        request.user.pastas
        .prefetch_related("processos")
        .order_by("posicao")
    )
    return render(request, "pareceres/home.html", {"pastas": pastas})


MAX_PROCESSOS_ANONIMOS = 2


def _get_processo_or_404(request, pk):
    """Busca processo por user autenticado OU session_key anônima."""
    if request.user.is_authenticated:
        return get_object_or_404(Processo, pk=pk, user=request.user)
    session_key = request.session.session_key
    if session_key:
        return get_object_or_404(Processo, pk=pk, session_key=session_key, user__isnull=True)
    raise Http404


def processo_novo(request):
    """Cria novo processo — autenticado ou anônimo (até MAX_PROCESSOS_ANONIMOS)."""
    if request.user.is_authenticated:
        processo = Processo.objects.create(user=request.user)
        return redirect("pareceres:processo_wizard", pk=processo.pk)

    # Anônimo — garantir session
    if not request.session.session_key:
        request.session.create()
    sk = request.session.session_key

    count = Processo.objects.filter(session_key=sk, user__isnull=True).count()
    if count >= MAX_PROCESSOS_ANONIMOS:
        return redirect("/?login_required=1")

    processo = Processo.objects.create(session_key=sk)
    return redirect("pareceres:processo_wizard", pk=processo.pk)


def processo_wizard(request, pk):
    """View principal do wizard — renderiza o stepper."""
    processo = _get_processo_or_404(request, pk)
    passo_atual = FaseProcesso.passo_wizard(processo.fase)

    docs = {
        d.tipo: True
        for d in processo.documentos.only("tipo").all()
    }

    return render(
        request,
        "pareceres/wizard.html",
        {
            "processo": processo,
            "passo_atual": passo_atual,
            "fase": processo.fase,
            "fase_label": FaseProcesso.label(processo.fase),
            "has_consolidado": docs.get("consolidado", False),
            "has_autuacao": docs.get("autuacao", False),
            "has_ata": docs.get("ata", False),
            "meses": MESES,
        },
    )


@login_required
def pdf_proxy(request, pk, tipo):
    """Serve PDFs do processo via proxy para exibir no iframe sem cross-origin."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)

    doc = processo.documentos.filter(tipo=tipo).first()
    if not doc or not doc.arquivo:
        raise Http404

    path = doc.arquivo.name
    if not default_storage.exists(path):
        raise Http404

    filename = urllib.parse.quote(os.path.basename(path))

    def file_iterator(storage_path, chunk_size=65536):
        with default_storage.open(storage_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    response = StreamingHttpResponse(file_iterator(path), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    response["X-Frame-Options"] = "SAMEORIGIN"
    return response


# ── API: Estado ───────────────────────────────────────────────────────────────


@login_required
def api_fase_atual(request, pk):
    """Retorna estado atual do processo (para polling do frontend)."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)

    # Auto-recovery: se processo ficou travado em fase intermediária sem worker
    if processo.fase == FaseProcesso.DOCUMENTOS_EXTRAINDO:
        import datetime as _dt
        from django.utils import timezone
        if processo.updated_at < timezone.now() - _dt.timedelta(seconds=30):
            _log.info("[RECOVERY] Processo %s travado em DOCUMENTOS_EXTRAINDO — avançando", pk)
            processo.fase = FaseProcesso.DOCUMENTOS_EXTRAIDOS
            processo.save(update_fields=["fase"])

    return JsonResponse({
        "fase": processo.fase,
        "passo": FaseProcesso.passo_wizard(processo.fase),
        "label": FaseProcesso.label(processo.fase),
    })


@login_required
@require_POST
def api_avancar_fase(request, pk):
    """Avança a fase do processo (chamado via POST pelo frontend)."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)
    body = json.loads(request.body) if request.body else {}
    proxima = body.get("proxima_fase")

    if not proxima:
        return JsonResponse({"error": "proxima_fase required"}, status=400)

    try:
        processo.avancar_fase(proxima)
        return JsonResponse({
            "fase": processo.fase,
            "passo": FaseProcesso.passo_wizard(processo.fase),
        })
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
def api_task_status(request, task_id):
    """Consulta status de uma Celery task (polling)."""
    try:
        from celery.result import AsyncResult
        result = AsyncResult(task_id)
        data = {
            "task_id": task_id,
            "status": result.status,
            "result": None,
        }
        if result.ready():
            try:
                data["result"] = result.result
            except Exception:
                data["result"] = str(result.result)
        return JsonResponse(data)
    except Exception as e:
        return JsonResponse({"task_id": task_id, "status": "UNKNOWN", "error": str(e)})


@login_required
def api_task_stream(request, task_id):
    """SSE stream do progresso de uma Celery task."""
    import time

    def event_stream():
        from celery.result import AsyncResult
        max_iterations = 120  # 2 minutos max
        for _ in range(max_iterations):
            try:
                result = AsyncResult(task_id)
                status = result.status
                data = json.dumps({"task_id": task_id, "status": status})

                yield f"data: {data}\n\n"

                if result.ready():
                    try:
                        final = result.result
                    except Exception:
                        final = str(result.result)
                    done_data = json.dumps({
                        "task_id": task_id,
                        "status": status,
                        "result": final,
                        "done": True,
                    })
                    yield f"data: {done_data}\n\n"
                    return
            except Exception:
                yield f"data: {json.dumps({'task_id': task_id, 'status': 'UNKNOWN'})}\n\n"

            time.sleep(1)

        yield f"data: {json.dumps({'task_id': task_id, 'status': 'TIMEOUT', 'done': True})}\n\n"

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


# ── API: Confirmar dados extraídos ───────────────────────────────────────────


@login_required
@require_POST
def api_confirmar_dados(request, pk):
    """Salva data_sessao (+ edições opcionais) e avança para admissibilidade."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)

    if processo.fase != FaseProcesso.DOCUMENTOS_EXTRAIDOS:
        return JsonResponse({"error": "Processo não está na fase de dados extraídos."}, status=400)

    body = json.loads(request.body) if request.body else {}

    import datetime
    def _parse(s):
        try:
            return datetime.date.fromisoformat(s)
        except (ValueError, TypeError):
            return None

    # Data da sessão
    data_sessao = body.get("data_sessao", "").strip()
    if data_sessao:
        processo.data_sessao = _parse(data_sessao)

    # Campos do Processo (preenchimento manual pelo MJ)
    if body.get("recorrente", "").strip():
        processo.recorrente = body["recorrente"].strip().upper()
    if body.get("prazo_final", "").strip():
        processo.prazo_final = _parse(body["prazo_final"])
    if body.get("data_protocolo", "").strip():
        processo.data_protocolo = _parse(body["data_protocolo"])
    if body.get("paginas_defesa", "").strip():
        processo.paginas_defesa = body["paginas_defesa"].strip()

    processo.save()

    # Datas da Admissibilidade (preenchidas pelo MJ)
    adm, _ = Admissibilidade.objects.get_or_create(processo=processo)

    data_infracao_str = body.get("data_infracao", "").strip()
    if data_infracao_str:
        adm.data_infracao = _parse(data_infracao_str)
    if body.get("data_na", "").strip():
        adm.data_na = _parse(body["data_na"])
    if body.get("data_np", "").strip():
        adm.data_np = _parse(body["data_np"])
    if body.get("data_instauracao", "").strip():
        adm.data_instauracao = _parse(body["data_instauracao"])
    if body.get("tipo_penalidade", "").strip():
        adm.tipo_penalidade = body["tipo_penalidade"].strip()

    adm.save()

    # ── Gate de validação (spec compliance) ─────────────────────────────
    erros = validar_dados_extraidos(processo)
    if erros and not body.get("force"):
        return JsonResponse(erros_to_response(erros), status=422)
    if erros and body.get("force"):
        log_audit("decisao", processo=processo, fase="force_advance_dados", dados={
            "erros_ignorados": [e.campo for e in erros],
        })

    # ── Confronto de dados (Fase 2 da spec) ─────────────────────────────
    # Compara dados informados pelo julgador com dados extraídos pelo Gemini.
    # Divergência relevante (datas diferentes) → retornar alerta.
    # O julgador pode forçar o avanço com force=true.
    if not body.get("force"):
        doc = processo.documentos.filter(tipo="consolidado").first()
        gemini_raw = doc.extracao_json if doc and doc.extracao_json else {}
        if gemini_raw:
            from pareceres.services.service_documentos import _parse_date_br
            divergencias = []

            def _confrontar(label, valor_julgador, valor_gemini_str):
                if not valor_julgador or not valor_gemini_str:
                    return
                nulos = {"nao_encontrado", "nulo", "null", "none", "n/a", ""}
                if valor_gemini_str.lower().strip() in nulos:
                    return
                val_gemini = _parse_date_br(valor_gemini_str)
                if val_gemini and val_gemini != valor_julgador:
                    divergencias.append({
                        "campo": label,
                        "gemini": valor_gemini_str,
                        "informado": valor_julgador.strftime("%d/%m/%Y"),
                    })

            _confrontar("Data da Infração", adm.data_infracao, gemini_raw.get("data_infracao", ""))
            _confrontar("Data da Notif. Autuação", adm.data_na, gemini_raw.get("data_na", ""))
            _confrontar("Data da Notif. Penalidade", adm.data_np, gemini_raw.get("data_np", ""))
            _confrontar("Data Instauração", adm.data_instauracao, gemini_raw.get("data_instauracao", ""))

            # Confrontar recorrente (nome)
            gemini_recorrente = gemini_raw.get("recorrente", "").strip().upper()
            julgador_recorrente = (processo.recorrente or "").strip().upper()
            if gemini_recorrente and julgador_recorrente and gemini_recorrente not in {"NAO_ENCONTRADO", ""} and gemini_recorrente != julgador_recorrente:
                divergencias.append({
                    "campo": "Recorrente",
                    "gemini": gemini_recorrente,
                    "informado": julgador_recorrente,
                })

            if divergencias:
                return JsonResponse({
                    "divergencias": divergencias,
                    "msg": "Foram encontradas divergências entre os dados informados e os extraídos do PDF. Confirme os dados ou corrija antes de avançar.",
                }, status=409)

    processo.avancar_fase(FaseProcesso.ADMISSIBILIDADE)

    # Disparar cálculo de admissibilidade
    from .tasks import calcular_admissibilidade_task
    try:
        task = calcular_admissibilidade_task.delay(processo.id)
        return JsonResponse({"ok": True, "task_id": task.id, "fase": processo.fase,
                             "passo": FaseProcesso.passo_wizard(processo.fase)})
    except Exception:
        _log.info("[ADMISSIBILIDADE] Sem broker — executando síncrono processo=%s", processo.id)

    try:
        from .services.service_admissibilidade import execute
        result = execute(processo)
        if not result.ok:
            _log.error("[ADMISSIBILIDADE] Falha síncrona: %s — processo=%s", result.erro, processo.id)
            return JsonResponse({"error": result.erro}, status=400)
    except Exception as e:
        _log.error("[ADMISSIBILIDADE] Erro síncrono: %s", e)
        return JsonResponse({"error": "Erro interno ao calcular admissibilidade."}, status=500)

    return JsonResponse({"ok": True, "task_id": "sync", "fase": processo.fase,
                         "passo": FaseProcesso.passo_wizard(processo.fase)})


# ── API: Dados extraídos (leitura) ───────────────────────────────────────────


@login_required
def api_dados_extraidos(request, pk):
    """Retorna dados extraídos do PDF para renderização no frontend."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)

    try:
        adm = processo.admissibilidade
    except Admissibilidade.DoesNotExist:
        adm = None

    def _fmt(d):
        return d.strftime("%d/%m/%Y") if d else ""

    # Dados originais da extração (para confronto visual e confiança)
    doc = processo.documentos.filter(tipo="consolidado").first()
    extracao = doc.extracao_json if doc and doc.extracao_json else {}
    confianca = extracao.get("confianca", {})

    return JsonResponse({
        "recorrente": processo.recorrente or "",
        "prazo_final": _fmt(processo.prazo_final),
        "data_protocolo": _fmt(processo.data_protocolo),
        "paginas_defesa": processo.paginas_defesa or "",
        "data_sessao": _fmt(processo.data_sessao),
        "data_infracao": _fmt(adm.data_infracao) if adm else "",
        "data_na": _fmt(adm.data_na) if adm else "",
        "data_np": _fmt(adm.data_np) if adm else "",
        "data_instauracao": _fmt(adm.data_instauracao) if adm else "",
        "tipo_penalidade": adm.tipo_penalidade if adm else "",
        "gemini_raw": extracao,
        "confianca": {
            "recorrente": confianca.get("recorrente", "MEDIA"),
            "data_infracao": confianca.get("data_infracao", "MEDIA"),
            "data_na": confianca.get("data_na", "MEDIA"),
            "data_np": confianca.get("data_np", "MEDIA"),
            "data_instauracao": confianca.get("data_instauracao", "MEDIA"),
        },
        "provider": extracao.get("provider", ""),
    })


# ── API: Upload de Documentos ────────────────────────────────────────────────


@login_required
@require_POST
def api_documentos_upload(request, pk):
    """Upload do PDF consolidado e dispara extração via Celery."""
    print(f"[DEBUG] api_documentos_upload chamada pk={pk}")
    processo = get_object_or_404(Processo, pk=pk, user=request.user)
    print(f"[DEBUG] processo.fase={processo.fase}")

    if processo.fase != FaseProcesso.DOCUMENTOS:
        return JsonResponse({"error": "Processo não está na fase de documentos."}, status=400)

    f = request.FILES.get("consolidado")
    if not f:
        return JsonResponse({"error": "O PDF consolidado é obrigatório."}, status=400)

    if not f.name.lower().endswith(".pdf"):
        return JsonResponse({"error": "Apenas arquivos PDF são aceitos."}, status=400)

    Documento.objects.update_or_create(
        processo=processo, tipo="consolidado",
        defaults={"arquivo": f},
    )

    # Avançar para extraindo e disparar task
    processo.avancar_fase(FaseProcesso.DOCUMENTOS_EXTRAINDO)

    from .tasks import extrair_documentos_task
    try:
        task = extrair_documentos_task.delay(processo.id)
        return JsonResponse({"ok": True, "task_id": task.id, "fase": processo.fase})
    except Exception:
        _log.info("[DOCUMENTOS] Sem broker — executando síncrono processo=%s", processo.id)

    # Sem broker (dev local) — executar extração síncrona
    try:
        from .services.service_documentos import execute
        result = execute(processo)
        if result.erro:
            _log.error("[DOCUMENTOS] Erro síncrono: %s", result.erro)
    except Exception as e:
        _log.error("[DOCUMENTOS] Erro síncrono: %s", e)
        # Fallback: avançar fase mesmo sem extração para não travar
        processo.fase = FaseProcesso.DOCUMENTOS_EXTRAIDOS
        processo.save(update_fields=["fase"])

    return JsonResponse({"ok": True, "task_id": "sync", "fase": processo.fase})


# ── API: Passo 3 — Admissibilidade ───────────────────────────────────────────


@login_required
def api_admissibilidade_dados(request, pk):
    """Retorna dados da admissibilidade para renderização no frontend."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)

    try:
        adm = processo.admissibilidade
    except Admissibilidade.DoesNotExist:
        return JsonResponse({"error": "Admissibilidade não disponível."}, status=404)

    return JsonResponse({
        "texto": adm.texto_resultado,
        "tabela": adm.tabela_datas_sensiveis,
        "aguardando": processo.fase == FaseProcesso.ADMISSIBILIDADE_AGUARDANDO,
        "flags": {
            "is_tempestivo": adm.is_tempestivo,
            "has_prescricao_punitiva": adm.has_prescricao_punitiva,
            "has_prescricao_intercorrente": adm.has_prescricao_intercorrente,
            "has_prescricao_intercorrente_bienal": adm.has_prescricao_intercorrente_bienal,
            "has_decadencia": adm.has_decadencia,
        },
        "julgador": {
            "tempestivo": adm.julgador_tempestivo,
            "punitiva": adm.julgador_prescricao_punitiva,
            "intercorrente": adm.julgador_prescricao_intercorrente,
            "intercorrente_bienal": adm.julgador_prescricao_intercorrente_bienal,
            "decadencia": adm.julgador_decadencia,
        },
        "fundamentacoes": adm.fundamentacoes or {},
    })


@login_required
@require_POST
def api_admissibilidade_recalcular(request, pk):
    """Re-dispara o cálculo de admissibilidade (quando falhou anteriormente)."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)

    if processo.fase != FaseProcesso.ADMISSIBILIDADE:
        return JsonResponse({"error": "Processo não está pendente de cálculo."}, status=400)

    from .tasks import calcular_admissibilidade_task
    try:
        task = calcular_admissibilidade_task.delay(processo.id)
        return JsonResponse({"ok": True, "task_id": task.id})
    except Exception:
        _log.info("[ADMISSIBILIDADE] Sem broker — recalculando síncrono processo=%s", processo.id)

    try:
        from .services.service_admissibilidade import execute
        result = execute(processo)
        if not result.ok:
            _log.error("[ADMISSIBILIDADE] Falha recálculo: %s — processo=%s", result.erro, processo.id)
            return JsonResponse({"error": result.erro}, status=400)
    except Exception as e:
        _log.error("[ADMISSIBILIDADE] Erro recálculo: %s", e)
        return JsonResponse({"error": "Erro interno ao recalcular admissibilidade."}, status=500)

    return JsonResponse({"ok": True, "task_id": "sync"})


@login_required
@require_POST
def api_admissibilidade_confirmar(request, pk):
    """Salva decisões do julgador na admissibilidade e roteia."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)

    if processo.fase != FaseProcesso.ADMISSIBILIDADE_AGUARDANDO:
        return JsonResponse({"error": "Processo não está aguardando confirmação de admissibilidade."}, status=400)

    body = json.loads(request.body) if request.body else {}

    try:
        adm = processo.admissibilidade
    except Admissibilidade.DoesNotExist:
        return JsonResponse({"error": "Admissibilidade não encontrada."}, status=404)

    def _parse_bool(v):
        if v == "" or v is None:
            return None
        return v in (True, "true", "True")

    adm.julgador_tempestivo = _parse_bool(body.get("julgador_tempestivo"))
    adm.julgador_prescricao_punitiva = _parse_bool(body.get("julgador_punitiva"))
    adm.julgador_prescricao_intercorrente = _parse_bool(body.get("julgador_intercorrente"))
    adm.julgador_prescricao_intercorrente_bienal = _parse_bool(body.get("julgador_intercorrente_bienal"))
    adm.julgador_decadencia = _parse_bool(body.get("julgador_decadencia"))

    # ── Gate de validação (spec compliance) ─────────────────────────────
    erros = validar_admissibilidade(processo, body)
    if erros:
        bloqueantes = any(e.bloqueante for e in erros)
        if not body.get("force") or bloqueantes:
            return JsonResponse(erros_to_response(erros), status=422)
        # force=true e sem bloqueantes → audit log + avança
        log_audit("decisao", processo=processo, fase="force_advance_admissibilidade", dados={
            "erros_ignorados": [e.campo for e in erros],
        })

    # ── Recalcular texto determinístico com flags do julgador ─────────────
    from .services.service_admissibilidade import gerar_texto_prescricao_decadencia
    adm.fundamentacoes = adm.fundamentacoes or {}
    adm.fundamentacoes["texto_prescricao_decadencia"] = gerar_texto_prescricao_decadencia(
        processo, adm, usar_flags_julgador=True,
    )
    adm.save()

    # ── Audit log da decisão do julgador ─────────────────────────────────
    log_audit("decisao", processo=processo, fase="admissibilidade_confirmada", dados={
        "julgador_tempestivo": adm.julgador_tempestivo,
        "julgador_prescricao_punitiva": adm.julgador_prescricao_punitiva,
        "julgador_prescricao_intercorrente": adm.julgador_prescricao_intercorrente,
        "julgador_prescricao_intercorrente_bienal": adm.julgador_prescricao_intercorrente_bienal,
        "julgador_decadencia": adm.julgador_decadencia,
    })

    # Determinar rota
    rota = adm.rota

    if rota in ("A", "B", "C"):
        # Mérito prejudicado — pular teses, ir direto para parecer
        processo.avancar_fase(FaseProcesso.PARECER)
        from .tasks import gerar_parecer_task
        try:
            task = gerar_parecer_task.delay(processo.id)
            return JsonResponse({
                "ok": True, "rota": rota, "task_id": task.id, "skip_teses": True,
            })
        except Exception:
            _log.info("[ADM] Sem broker — fallback síncrono processo=%s rota=%s", processo.id, rota)
            try:
                from .services.service_parecer import execute
                result = execute(processo)
                if not result.ok:
                    _log.error("[ADM] Falha síncrona parecer: %s — processo=%s", result.erro, processo.id)
                    return JsonResponse({"error": result.erro}, status=400)
            except Exception as e:
                _log.error("[ADM] Erro síncrono parecer: %s", e)
                return JsonResponse({"error": "Erro interno ao gerar parecer."}, status=500)
            return JsonResponse({
                "ok": True, "rota": rota, "task_id": "sync", "skip_teses": True,
            })
    else:
        # Rota D — extrair teses
        processo.avancar_fase(FaseProcesso.TESE)
        from .tasks import extrair_teses_task
        try:
            task = extrair_teses_task.delay(processo.id)
            return JsonResponse({
                "ok": True, "rota": "D", "task_id": task.id, "skip_teses": False,
            })
        except Exception:
            _log.info("[ADM] Sem broker — fallback síncrono teses processo=%s", processo.id)
            try:
                from .services.service_teses import execute_extracao, execute_analise
                result = execute_extracao(processo)
                if result.ok:
                    execute_analise(processo)
            except Exception as e:
                _log.error("[ADM] Erro síncrono teses: %s", e)
            return JsonResponse({
                "ok": True, "rota": "D", "task_id": "sync", "skip_teses": False,
            })


# ── API: Passo 4 — Teses ─────────────────────────────────────────────────────


@login_required
def api_teses_dados(request, pk):
    """Retorna as teses e análise para renderização no frontend."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)
    teses = list(processo.teses.order_by("ordem").values(
        "id", "ordem", "titulo", "fundamentacao", "acolhida"
    ))
    # Verificar se a tese é a genérica "nenhuma tese identificada"
    sem_teses = (
        len(teses) == 1
        and "nenhuma tese" in (teses[0].get("titulo", "") or "").lower()
        and not teses[0].get("fundamentacao")
    )
    return JsonResponse({"teses": teses, "sem_teses": sem_teses})


@login_required
@require_POST
def api_teses_reextrair(request, pk):
    """Re-dispara extração + análise de teses (quando a primeira tentativa falhou)."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)

    if processo.fase not in (FaseProcesso.TESE_AGUARDANDO, FaseProcesso.TESE):
        return JsonResponse({"error": "Processo não está na fase de teses."}, status=400)

    # Resetar fase para TESE e re-disparar
    processo.fase = FaseProcesso.TESE
    processo.save(update_fields=["fase"])

    from .tasks import extrair_teses_task
    try:
        task = extrair_teses_task.delay(processo.id)
        return JsonResponse({"ok": True, "task_id": task.id})
    except Exception:
        _log.info("[TESES] Sem broker — fallback síncrono reextração processo=%s", processo.id)
        try:
            from .services.service_teses import execute_extracao, execute_analise
            result = execute_extracao(processo)
            if result.ok:
                execute_analise(processo)
        except Exception as e:
            _log.error("[TESES] Erro síncrono reextração: %s", e)
        return JsonResponse({"ok": True, "task_id": "sync"})


@login_required
@require_POST
def api_teses_confirmar(request, pk):
    """Salva decisões das teses e dispara geração do parecer."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)

    if processo.fase not in (FaseProcesso.TESE_AGUARDANDO, FaseProcesso.TESE):
        return JsonResponse({"error": "Processo não está aguardando confirmação de teses."}, status=400)

    body = json.loads(request.body) if request.body else {}
    decisoes = body.get("decisoes", {})

    # ── Gate de validação ───────────────────────────────────────────────
    erros = validar_teses(processo, decisoes)
    if erros and not body.get("force"):
        return JsonResponse(erros_to_response(erros), status=422)
    if erros and body.get("force"):
        log_audit("decisao", processo=processo, fase="force_advance_teses", dados={
            "erros_ignorados": [e.campo for e in erros],
        })

    # Salvar decisões (tese_id -> true/false)
    for tese in processo.teses.all():
        key = str(tese.id)
        if key in decisoes:
            tese.acolhida = decisoes[key] in (True, "true", "True")
            tese.save(update_fields=["acolhida"])

    # Avançar para parecer
    processo.avancar_fase(FaseProcesso.PARECER)

    from .tasks import gerar_parecer_task
    try:
        task = gerar_parecer_task.delay(processo.id)
        return JsonResponse({"ok": True, "task_id": task.id})
    except Exception:
        _log.info("[TESES] Sem broker — fallback síncrono processo=%s", processo.id)
        try:
            from .services.service_parecer import execute
            result = execute(processo)
            if not result.ok:
                _log.error("[TESES] Falha síncrona parecer: %s — processo=%s", result.erro, processo.id)
                return JsonResponse({"error": result.erro}, status=400)
        except Exception as e:
            _log.error("[TESES] Erro síncrono parecer: %s", e)
            return JsonResponse({"error": "Erro interno ao gerar parecer."}, status=500)
        return JsonResponse({"ok": True, "task_id": "sync"})


# ── API: Passo 5 — Parecer ───────────────────────────────────────────────────


@login_required
def api_parecer_dados(request, pk):
    """Retorna o parecer gerado para renderização no frontend."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)

    try:
        parecer = processo.parecer
    except Parecer.DoesNotExist:
        return JsonResponse({"error": "Parecer não disponível."}, status=404)

    # Tempo de julgamento
    tempo_str = ""
    try:
        diff = (parecer.created_at - processo.created_at).total_seconds()
        m, s = divmod(int(diff), 60)
        tempo_str = f"{m} min {s:02d} s"
    except Exception:
        pass

    _log.info("[PARECER_DADOS] processo=%s blindagem_score=%s checklist_len=%s",
              processo.id, parecer.blindagem_score,
              len(parecer.checklist_auditoria) if parecer.checklist_auditoria else 0)

    return JsonResponse({
        "texto": parecer.conteudo_final,
        "texto_ia": parecer.texto_ia,
        "texto_editado": parecer.texto_editado,
        "provider": parecer.provider,
        "resultado": processo.resultado_final,
        "blindagem_score": parecer.blindagem_score,
        "blindagem_detalhes": parecer.blindagem_detalhes,
        "checklist": parecer.checklist_auditoria,
        "tempo_julgamento": tempo_str,
        "recorrente": processo.recorrente or "",
        "relator": processo.user.get_full_name() or processo.user.username,
        "pa": processo.pa or "",
    })


@login_required
@require_POST
def api_parecer_editar(request, pk):
    """Salva o texto editado do parecer (HTML do editor rich-text)."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)

    try:
        parecer = processo.parecer
    except Parecer.DoesNotExist:
        return JsonResponse({"error": "Parecer não existe."}, status=404)

    body = json.loads(request.body) if request.body else {}
    texto = body.get("texto_editado", "").strip()

    if not texto:
        return JsonResponse({"error": "Texto não pode estar vazio."}, status=400)

    parecer.texto_editado = texto
    parecer.save(update_fields=["texto_editado", "updated_at"])

    _log.info("[PARECER] Texto editado salvo — processo=%s", processo.id)
    return JsonResponse({"ok": True, "conteudo_final": parecer.conteudo_final})


# ── API: Passo 6 — Auditoria / Finalizar ─────────────────────────────────────


@login_required
@require_POST
def api_processo_excluir(request, pk):
    """Exclui um processo e todos os objetos relacionados (cascade)."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)

    # Remover arquivos físicos dos documentos
    for doc in processo.documentos.all():
        if doc.arquivo:
            try:
                default_storage.delete(doc.arquivo.name)
            except Exception:
                pass

    pa = processo.pa
    processo.delete()
    _log.info("[EXCLUIR] Processo PA=%s excluído por user=%s", pa, request.user.username)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def api_auditoria_finalizar(request, pk):
    """Dispara auditoria e finaliza o processo."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)

    if processo.fase not in (FaseProcesso.AUDITORIA, FaseProcesso.PARECER, FaseProcesso.PARECER_GERANDO):
        return JsonResponse({"error": "Processo não está na fase de auditoria."}, status=400)

    # ── Gate de validação (parecer completo?) ───────────────────────────
    body = json.loads(request.body) if request.body else {}
    erros = validar_parecer(processo)
    if erros:
        bloqueantes = any(e.bloqueante for e in erros)
        if not body.get("force") or bloqueantes:
            return JsonResponse(erros_to_response(erros), status=422)
        log_audit("decisao", processo=processo, fase="force_advance_auditoria", dados={
            "erros_ignorados": [e.campo for e in erros],
        })

    # Se não está em AUDITORIA ainda, avançar
    if processo.fase in (FaseProcesso.PARECER, FaseProcesso.PARECER_GERANDO):
        processo.fase = FaseProcesso.AUDITORIA
        processo.save(update_fields=["fase"])

    from .tasks import auditar_parecer_task
    _log.info("[AUDITORIA] Disparando task processo=%s fase=%s", processo.id, processo.fase)
    try:
        task = auditar_parecer_task.delay(processo.id)
        _log.info("[AUDITORIA] Task enviada task_id=%s processo=%s", task.id, processo.id)
        return JsonResponse({"ok": True, "task_id": task.id})
    except Exception as exc:
        _log.warning("[AUDITORIA] Sem broker — fallback síncrono processo=%s erro=%s",
                     processo.id, exc)
        try:
            from .services.service_auditoria import execute
            result = execute(processo)
            _log.info("[AUDITORIA] Fallback resultado ok=%s processo=%s", result.ok, processo.id)
            if not result.ok:
                _log.error("[AUDITORIA] Falha síncrona: %s — processo=%s", result.erro, processo.id)
                return JsonResponse({"error": result.erro}, status=400)
        except Exception as e:
            _log.error("[AUDITORIA] Erro síncrono: %s — processo=%s", e, processo.id,
                       exc_info=True)
            return JsonResponse({"error": "Erro interno na auditoria."}, status=500)
        return JsonResponse({"ok": True, "task_id": "sync"})


# ── API: Pastas ──────────────────────────────────────────────────────────────


@login_required
@require_GET
def api_pastas_listar(request):
    """Lista pastas do usuário com contagem de processos."""
    _garantir_pastas_mensais(request.user)
    from django.db.models import Count, Max
    pastas = request.user.pastas.annotate(
        qtd_processos=Count("processos")
    ).values("id", "nome", "posicao", "qtd_processos")
    return JsonResponse({"pastas": list(pastas)})


@login_required
@require_GET
def api_pasta_processos(request, pasta_id):
    """Lista processos dentro de uma pasta."""
    pasta = get_object_or_404(Pasta, pk=pasta_id, user=request.user)
    processos = pasta.processos.order_by("-updated_at").values(
        "id", "pa", "recorrente", "resultado_final", "fase"
    )
    return JsonResponse({
        "pasta_nome": pasta.nome,
        "processos": list(processos),
    })


@login_required
@require_POST
def api_pasta_criar(request):
    """Cria uma nova pasta."""
    from django.db.models import Max
    body = json.loads(request.body) if request.body else {}
    nome = body.get("nome", "").strip()
    if not nome:
        return JsonResponse({"error": "Nome é obrigatório."}, status=400)

    ultima_posicao = request.user.pastas.aggregate(
        max_pos=Max("posicao")
    )["max_pos"] or 0

    pasta = Pasta.objects.create(
        user=request.user, nome=nome, posicao=ultima_posicao + 1
    )
    return JsonResponse({"id": pasta.id, "nome": pasta.nome, "posicao": pasta.posicao})


@login_required
@require_POST
def api_pasta_renomear(request, pasta_id):
    """Renomeia uma pasta existente."""
    pasta = get_object_or_404(Pasta, pk=pasta_id, user=request.user)
    body = json.loads(request.body) if request.body else {}
    nome = body.get("nome", "").strip()
    if not nome:
        return JsonResponse({"error": "Nome é obrigatório."}, status=400)

    pasta.nome = nome
    pasta.save(update_fields=["nome"])
    return JsonResponse({"ok": True, "nome": pasta.nome})


@login_required
@require_POST
def api_pasta_excluir(request, pasta_id):
    """Exclui pasta (processos ficam sem pasta, não são excluídos)."""
    pasta = get_object_or_404(Pasta, pk=pasta_id, user=request.user)
    pasta.processos.update(pasta=None)
    pasta.delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def api_pasta_reordenar(request):
    """Reordena pastas. Body: {"ordem": [id1, id2, id3]}."""
    body = json.loads(request.body) if request.body else {}
    ordem = body.get("ordem", [])

    for posicao, pasta_id in enumerate(ordem):
        request.user.pastas.filter(pk=pasta_id).update(posicao=posicao)

    return JsonResponse({"ok": True})


@login_required
@require_POST
def api_processo_mover(request, pk):
    """Move processo para uma pasta (ou None para remover da pasta)."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)
    body = json.loads(request.body) if request.body else {}
    pasta_id = body.get("pasta_id")

    if pasta_id:
        pasta = get_object_or_404(Pasta, pk=pasta_id, user=request.user)
        processo.pasta = pasta
    else:
        processo.pasta = None

    processo.save(update_fields=["pasta"])
    return JsonResponse({"ok": True})


# ── API: Feedback ────────────────────────────────────────────────────────────


@login_required
@require_POST
def api_feedback(request, pk):
    """Salva feedback do julgador sobre o parecer gerado."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)

    try:
        parecer = processo.parecer
    except Parecer.DoesNotExist:
        return JsonResponse({"error": "Parecer não existe."}, status=404)

    body = json.loads(request.body) if request.body else {}

    score = body.get("score")
    if score is not None:
        try:
            score = int(score)
            if not (0 <= score <= 100):
                return JsonResponse({"error": "Score deve ser entre 0 e 100."}, status=400)
        except (ValueError, TypeError):
            return JsonResponse({"error": "Score inválido."}, status=400)
        parecer.feedback_score = score

    if "tags" in body:
        parecer.feedback_tags = body["tags"][:500]

    if "notas" in body:
        parecer.feedback_notas = body["notas"]

    parecer.save(update_fields=["feedback_score", "feedback_tags", "feedback_notas", "updated_at"])
    return JsonResponse({"ok": True})


# ── API: Agente Lateral (chat RAG) ──────────────────────────────────────────


@login_required
def api_agente_historico(request, pk):
    """Retorna histórico de mensagens do agente lateral."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)
    mensagens = list(processo.chat_messages.values("role", "content", "created_at"))
    return JsonResponse({"mensagens": mensagens})


@login_required
@require_POST
def api_agente_mensagem(request, pk):
    """Envia mensagem ao agente lateral e retorna resposta RAG."""
    processo = get_object_or_404(Processo, pk=pk, user=request.user)
    body = json.loads(request.body) if request.body else {}
    pergunta = body.get("mensagem", "").strip()

    if not pergunta:
        return JsonResponse({"error": "Mensagem é obrigatória."}, status=400)

    # Salvar mensagem do usuário
    ChatMessage.objects.create(processo=processo, role="user", content=pergunta)

    # Montar contexto do processo
    contexto_parts = [f"PA: {processo.pa}", f"Recorrente: {processo.recorrente}"]
    try:
        adm = processo.admissibilidade
        datas_ctx = []
        if adm.data_infracao:
            datas_ctx.append(f"Infração: {adm.data_infracao}")
        if adm.data_na:
            datas_ctx.append(f"NA: {adm.data_na}")
        if adm.data_np:
            datas_ctx.append(f"NP: {adm.data_np}")
        if datas_ctx:
            contexto_parts.append("Datas: " + ", ".join(datas_ctx))
    except Admissibilidade.DoesNotExist:
        pass

    contexto = "\n".join(contexto_parts)

    # Buscar RAG (Vertex)
    rag_context = ""
    try:
        from .integrations.vertex import search
        rag_result = search(processo, pergunta)
        if rag_result:
            rag_context = rag_result
    except Exception as e:
        _log.warning("[AGENTE] Vertex RAG falhou: %s", e)

    # Chamar Anthropic (Haiku — rápido e barato)
    try:
        import anthropic
        client = anthropic.Anthropic()
        system_prompt = (
            "Você é um assistente jurídico especializado em trânsito (CTB, CONTRAN, CETRAN/SC). "
            "Responda de forma objetiva e cite artigos/resoluções quando aplicável. "
            "Use apenas informações do contexto fornecido e da base normativa.\n\n"
            f"Contexto do processo:\n{contexto}\n\n"
        )
        if rag_context:
            system_prompt += f"Base normativa relevante:\n{rag_context}\n"

        # Histórico recente (últimas 10 mensagens)
        historico = list(
            processo.chat_messages.order_by("-created_at")[:10]
        )
        historico.reverse()
        messages = [
            {"role": m.role, "content": m.content}
            for m in historico
        ]

        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        )
        resposta = resp.content[0].text

        log_audit(
            "ia_request", processo=processo, fase="agente_lateral",
            provider="anthropic", model_name="claude-haiku-4-5-20251001",
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )
    except Exception as e:
        _log.error("[AGENTE] Anthropic falhou: %s", e)
        resposta = "Desculpe, não consegui processar sua consulta no momento. Tente novamente."

    # Salvar resposta
    ChatMessage.objects.create(processo=processo, role="assistant", content=resposta)

    return JsonResponse({"resposta": resposta})


# ── API: Banco de Teses ─────────────────────────────────────────────────────


@login_required
@require_GET
def api_banco_teses_listar(request):
    """Lista teses do usuário + teses públicas da comunidade."""
    filtro = request.GET.get("filtro", "minhas")  # minhas | comunidade | todas
    qs = BancoTese.objects.all()

    if filtro == "minhas":
        qs = qs.filter(user=request.user)
    elif filtro == "comunidade":
        qs = qs.filter(is_public=True).exclude(user=request.user)
    else:
        from django.db.models import Q
        qs = qs.filter(Q(user=request.user) | Q(is_public=True))

    busca = request.GET.get("q", "").strip()
    if busca:
        qs = qs.filter(titulo__icontains=busca)

    teses = list(qs.values(
        "id", "titulo", "conteudo", "tipo_infracao", "is_public",
        "usage_count", "user__username", "created_at",
    )[:100])
    return JsonResponse({"teses": teses})


@login_required
@require_POST
def api_banco_tese_criar(request):
    """Cria uma nova tese no banco."""
    body = json.loads(request.body) if request.body else {}
    titulo = body.get("titulo", "").strip()
    conteudo = body.get("conteudo", "").strip()

    if not titulo or not conteudo:
        return JsonResponse({"error": "Titulo e conteudo sao obrigatorios."}, status=400)

    tese = BancoTese.objects.create(
        user=request.user,
        titulo=titulo,
        conteudo=conteudo,
        tipo_infracao=body.get("tipo_infracao", ""),
        is_public=body.get("is_public", False),
    )
    return JsonResponse({"id": tese.id, "titulo": tese.titulo})


@login_required
@require_POST
def api_banco_tese_editar(request, tese_id):
    """Edita uma tese do banco (apenas do próprio usuário)."""
    tese = get_object_or_404(BancoTese, pk=tese_id, user=request.user)
    body = json.loads(request.body) if request.body else {}

    if "titulo" in body:
        tese.titulo = body["titulo"].strip()
    if "conteudo" in body:
        tese.conteudo = body["conteudo"].strip()
    if "tipo_infracao" in body:
        tese.tipo_infracao = body["tipo_infracao"]
    if "is_public" in body:
        tese.is_public = body["is_public"]

    tese.save()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def api_banco_tese_excluir(request, tese_id):
    """Exclui uma tese do banco (apenas do próprio usuário)."""
    tese = get_object_or_404(BancoTese, pk=tese_id, user=request.user)
    tese.delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def api_banco_tese_importar(request, tese_id):
    """Importa uma tese pública da comunidade para o banco pessoal."""
    tese_orig = get_object_or_404(BancoTese, pk=tese_id, is_public=True)

    copia = BancoTese.objects.create(
        user=request.user,
        titulo=tese_orig.titulo,
        conteudo=tese_orig.conteudo,
        tipo_infracao=tese_orig.tipo_infracao,
        is_public=False,
    )
    tese_orig.usage_count += 1
    tese_orig.save(update_fields=["usage_count"])

    return JsonResponse({"id": copia.id, "titulo": copia.titulo})


@login_required
@require_POST
def api_banco_tese_usar(request, tese_id):
    """Incrementa contador de uso de uma tese."""
    tese = get_object_or_404(BancoTese, pk=tese_id)
    if tese.user != request.user and not tese.is_public:
        return JsonResponse({"error": "Tese nao acessivel."}, status=403)
    tese.usage_count += 1
    tese.save(update_fields=["usage_count"])
    return JsonResponse({"ok": True, "usage_count": tese.usage_count})


# ── API: Fórum ───────────────────────────────────────────────────────────────


@login_required
@require_GET
def api_forum_listar(request):
    """Lista posts do fórum com contagem de comentários e curtidas."""
    from django.db.models import Count
    posts = PostForum.objects.order_by("-is_pinned", "-created_at").annotate(
        qtd_comentarios=Count("comentarios", distinct=True),
        qtd_curtidas=Count("curtidas", distinct=True),
    ).select_related("user")[:50]

    result = []
    for p in posts:
        name = (p.user.first_name.strip() + " " + p.user.last_name.strip()).strip()
        result.append({
            "id": p.id,
            "titulo": p.titulo,
            "conteudo": p.conteudo,
            "is_pinned": p.is_pinned,
            "display_name": name or p.user.username,
            "created_at": p.created_at.isoformat(),
            "qtd_comentarios": p.qtd_comentarios,
            "qtd_curtidas": p.qtd_curtidas,
        })

    # Marcar fórum como visto
    from django.utils import timezone as _tz
    try:
        profile = request.user.profile
        profile.forum_last_seen_at = _tz.now()
        profile.save(update_fields=["forum_last_seen_at"])
    except Exception:
        pass

    return JsonResponse({"posts": result})


@login_required
@require_GET
def api_forum_unread_count(request):
    """Retorna quantidade de posts novos desde o último acesso ao fórum."""
    try:
        last_seen = request.user.profile.forum_last_seen_at
    except Exception:
        last_seen = None

    qs = PostForum.objects.all()
    if last_seen:
        qs = qs.filter(created_at__gt=last_seen)
    count = qs.count()
    return JsonResponse({"unread": count})


@login_required
@require_POST
def api_forum_criar_post(request):
    """Cria um novo post no fórum."""
    body = json.loads(request.body) if request.body else {}
    titulo = body.get("titulo", "").strip()
    conteudo = body.get("conteudo", "").strip()

    if not titulo or not conteudo:
        return JsonResponse({"error": "Titulo e conteudo sao obrigatorios."}, status=400)

    post = PostForum.objects.create(
        user=request.user, titulo=titulo, conteudo=conteudo,
    )
    return JsonResponse({"id": post.id, "titulo": post.titulo})


@login_required
@require_GET
def api_forum_comentarios(request, post_id):
    """Lista comentários de um post."""
    post = get_object_or_404(PostForum, pk=post_id)
    comentarios = []
    for c in post.comentarios.select_related("user").all():
        name = (c.user.first_name.strip() + " " + c.user.last_name.strip()).strip()
        comentarios.append({
            "id": c.id,
            "conteudo": c.conteudo,
            "display_name": name or c.user.username,
            "created_at": c.created_at.isoformat(),
        })
    return JsonResponse({"comentarios": comentarios})


@login_required
@require_POST
def api_forum_comentar(request, post_id):
    """Adiciona comentário a um post."""
    post = get_object_or_404(PostForum, pk=post_id)
    body = json.loads(request.body) if request.body else {}
    conteudo = body.get("conteudo", "").strip()

    if not conteudo:
        return JsonResponse({"error": "Conteudo e obrigatorio."}, status=400)

    comentario = ComentarioForum.objects.create(
        post=post, user=request.user, conteudo=conteudo,
    )
    return JsonResponse({"id": comentario.id})


@login_required
@require_POST
def api_forum_curtir(request, post_id):
    """Toggle curtida em um post."""
    post = get_object_or_404(PostForum, pk=post_id)

    if post.curtidas.filter(pk=request.user.pk).exists():
        post.curtidas.remove(request.user)
        curtiu = False
    else:
        post.curtidas.add(request.user)
        curtiu = True

    return JsonResponse({"ok": True, "curtiu": curtiu, "total": post.curtidas.count()})
