"""
Re-extração de OCR para processos afetados pelo bug do fast-path de texto nativo.

Contexto: até a correção em `document_ai.py`, páginas escaneadas com uma fina
camada de texto nativo (rodapé de assinatura eletrônica: nome + OAB) tinham o OCR
pulado. O corpo escaneado (a tese) nunca era lido e a Fase 4 retornava
"não foram encontradas teses defensivas". O `ocr_markdown` já salvo no
`extracao_json` continua truncado — este command o regenera.

Estratégia (barata primeiro, cara só nos afetados):
1. DETECÇÃO (grátis, PyMuPDF local): reabre o PDF consolidado e acha páginas que
   o código ANTIGO manteria como nativas (>= 50 chars) mas que o NOVO classifica
   como escaneadas (imagem cobre a página). Se houver ao menos uma, o processo
   foi afetado.
2. --apply: só nos afetados, re-roda o Document AI (custa $) e atualiza APENAS o
   `ocr_markdown` + campos `ocr_*`. NÃO toca em admissibilidade, datas nem em
   nenhuma decisão manual do MJ.
3. --reprocessar-teses: opcionalmente re-roda a Fase 4 nos afetados cujo estado é
   o placeholder "sem teses" (não sobrescreve teses já decididas).

Uso:
    python manage.py reextrair_ocr                      # dry-run: lista afetados
    python manage.py reextrair_ocr --processo 12 34     # restringe a IDs
    python manage.py reextrair_ocr --apply              # regenera OCR dos afetados
    python manage.py reextrair_ocr --apply --reprocessar-teses
"""

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from pareceres.integrations.document_ai import (
    _MIN_CHARS_PAGINA,
    _pagina_escaneada,
)
from pareceres.models import Documento


def _paginas_afetadas(pdf_bytes) -> list[int]:
    """Páginas (1-indexed) que o fast-path ANTIGO manteria nativas mas que o
    critério NOVO trata como escaneadas — i.e., tiveram o OCR pulado por engano."""
    import fitz  # PyMuPDF

    afetadas = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for i, page in enumerate(doc):
            n_chars = len((page.get_text("text") or "").strip())
            era_nativa_antiga = n_chars >= _MIN_CHARS_PAGINA
            if era_nativa_antiga and _pagina_escaneada(page, n_chars):
                afetadas.append(i + 1)
    finally:
        doc.close()
    return afetadas


class Command(BaseCommand):
    help = (
        "Detecta e re-extrai o OCR de processos afetados pelo bug do fast-path "
        "de texto nativo (páginas escaneadas com rodapé de assinatura)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--processo", nargs="+", type=int, default=None,
            help="Restringe a IDs de Processo específicos.",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Aplica a re-extração (custa Document AI). Sem isto é dry-run.",
        )
        parser.add_argument(
            "--reprocessar-teses", action="store_true",
            help="Após re-OCR, re-roda a Fase 4 nos afetados que estão no "
                 "placeholder 'sem teses' (não sobrescreve teses já decididas).",
        )
        parser.add_argument(
            "--excluir-fase", nargs="+", default=None,
            help="Pula processos nestas fases (ex.: --excluir-fase finalizado).",
        )

    def handle(self, *args, **options):
        ids = options["processo"]
        aplicar = options["apply"]
        reprocessar_teses = options["reprocessar_teses"]
        excluir_fase = options["excluir_fase"]

        # O ocr_markdown só é gravado no doc consolidado (o Document AI só
        # processa docs_dict["consolidado"]), então basta varrer esses.
        docs = Documento.objects.filter(tipo="consolidado").select_related("processo")
        if ids:
            docs = docs.filter(processo_id__in=ids)
        if excluir_fase:
            docs = docs.exclude(processo__fase__in=excluir_fase)

        total = 0
        afetados = []
        for doc in docs.iterator():
            if not doc.arquivo:
                continue
            extracao = doc.extracao_json or {}
            if not extracao.get("ocr_markdown"):
                continue  # nunca extraiu OCR — nada a corrigir
            total += 1
            try:
                with default_storage.open(doc.arquivo.name, "rb") as f:
                    pdf_bytes = f.read()
                paginas = _paginas_afetadas(pdf_bytes)
            except Exception as e:
                self.stderr.write(f"  [erro] processo={doc.processo_id} doc={doc.id}: {e}")
                continue
            if paginas:
                afetados.append((doc, paginas))
                p = doc.processo
                self.stdout.write(
                    f"AFETADO processo={p.id} pa={p.pa or '-'} "
                    f"fase={p.fase} páginas_escaneadas={paginas}"
                )

        self.stdout.write(
            f"\n{len(afetados)} de {total} processos com OCR afetados.\n"
        )

        if not aplicar:
            self.stdout.write("Dry-run. Rode com --apply para regenerar o OCR.")
            return

        # ── Aplicação: re-OCR só dos afetados ────────────────────────────────
        from pareceres.services import service_documentos
        from pareceres.services.service_teses import execute_extracao, is_placeholder_tese

        corrigidos = 0
        for doc, paginas in afetados:
            p = doc.processo
            try:
                docs_dict = {"consolidado": doc.arquivo.name}
                ocr_resultado = service_documentos._tentar_document_ai(docs_dict)
                if not (ocr_resultado and ocr_resultado.get("paginas")):
                    self.stderr.write(f"  [skip] processo={p.id}: OCR retornou vazio")
                    continue
                novo_md = service_documentos._ocr_para_markdown(ocr_resultado)
                antigo = (doc.extracao_json or {}).get("ocr_markdown") or ""

                # Não-destrutivo: só sobrescreve se o novo OCR RECUPEROU conteúdo
                # (é mais longo). Se vier igual/menor, o texto salvo já estava bom
                # (PDF pesquisável) — não encolhe o que está no banco. (auto-cura os
                # processos que a versão anterior do fix havia encurtado.)
                if len(novo_md) <= len(antigo):
                    self.stdout.write(
                        f"SKIP processo={p.id} OCR {len(antigo)}→{len(novo_md)} "
                        f"(sem ganho de conteúdo — mantido)"
                    )
                    continue

                extracao = doc.extracao_json or {}
                extracao["ocr_markdown"] = novo_md
                extracao["ocr_confianca"] = ocr_resultado["confianca_media"]
                extracao["ocr_paginas"] = ocr_resultado["total_paginas"]
                extracao["ocr_latency_ms"] = ocr_resultado.get("latency_ms")
                doc.extracao_json = extracao
                doc.save(update_fields=["extracao_json"])
                corrigidos += 1
                self.stdout.write(
                    f"OK processo={p.id} OCR {len(antigo)}→{len(novo_md)} chars "
                    f"(+{len(novo_md) - len(antigo)})"
                )

                if reprocessar_teses:
                    tese_atual = p.teses.order_by("ordem").first()
                    # Só re-roda onde HÁ um placeholder "sem teses". Se ainda não
                    # há tese (None), a Fase 4 nem rodou — vai rodar naturalmente
                    # depois, já com o OCR corrigido; não antecipar.
                    if tese_atual is not None and is_placeholder_tese(tese_atual.titulo):
                        res = execute_extracao(p)
                        self.stdout.write(
                            f"   Fase 4 re-rodada: {getattr(res, 'mensagem', res)}"
                        )
                    elif tese_atual is None:
                        self.stdout.write("   Fase 4 ainda não rodou — só OCR corrigido.")
                    else:
                        self.stdout.write("   Fase 4 preservada (já há teses reais).")
            except Exception as e:
                self.stderr.write(f"  [erro] processo={p.id}: {e}")

        self.stdout.write(f"\n{corrigidos} processo(s) re-extraído(s).")
