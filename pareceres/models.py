from django.db import models
from django.contrib.auth.models import User
from pgvector.django import VectorField, HnswIndex

from .estado import FaseProcesso

# Dimensão dos embeddings (paraphrase-multilingual-MiniLM-L12-v2 → 384)
EMBEDDING_DIM = 384


# ─── Pasta (organização do usuário) ──────────────────────────────────────────


class Pasta(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="pastas", db_index=True
    )
    nome = models.CharField(max_length=255)
    posicao = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["posicao", "-created_at"]

    def __str__(self):
        return f"{self.nome} — {self.user.username}"


# ─── Processo (entidade central — antes era o Parecer God Object) ────────────


class Processo(models.Model):
    """
    Registro central de um recurso JARI.
    Contém apenas identificação + estado. Dados de cada fase vivem em seus models.
    """

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="processos",
        db_index=True, null=True, blank=True,
    )
    session_key = models.CharField(
        max_length=40, blank=True, db_index=True,
        help_text="Chave de sessão para processos anônimos (sem login).",
    )
    pasta = models.ForeignKey(
        Pasta, on_delete=models.SET_NULL, null=True, blank=True, related_name="processos"
    )

    # Identificação (Fase 1 — Perguntas)
    pa = models.CharField("Processo Administrativo", max_length=100, blank=True)
    sgpe = models.CharField("SGPE", max_length=100, blank=True)
    recorrente = models.CharField(max_length=255, blank=True)
    data_sessao = models.DateField("Data da Sessão", null=True, blank=True)
    prazo_final = models.DateField("Prazo Final Recurso JARI", null=True, blank=True)
    data_protocolo = models.DateField("Data Protocolo Recurso JARI", null=True, blank=True)
    paginas_defesa = models.CharField("Páginas da Defesa", max_length=100, blank=True)

    # State Machine
    fase = models.CharField(
        max_length=30,
        choices=FaseProcesso.choices(),
        default=FaseProcesso.DOCUMENTOS,
        db_index=True,
    )

    # Resultado final (denormalizado para queries rápidas)
    resultado_final = models.CharField(
        max_length=15,
        blank=True,
        choices=[
            ("DEFERIDO", "Deferido"),
            ("INDEFERIDO", "Indeferido"),
            ("NAO_CONHECIDO", "Não Conhecido"),
        ],
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Processo"
        verbose_name_plural = "Processos"

    def __str__(self):
        return f"PA {self.pa or '(sem PA)'} — {self.user.username}"

    def avancar_fase(self, proxima: str):
        """Transição de fase com validação."""
        transicoes = FaseProcesso.transicoes_validas()
        validas = transicoes.get(self.fase, [])
        if proxima not in validas:
            raise ValueError(
                f"Transição inválida: {self.fase} -> {proxima}. "
                f"Válidas: {validas}"
            )
        self.fase = proxima
        self.save(update_fields=["fase", "updated_at"])


# ─── Documento (PDFs do processo) ────────────────────────────────────────────


class Documento(models.Model):
    TIPO_CHOICES = [
        ("autuacao", "Autuação"),
        ("consolidado", "Consolidado"),
        ("ata", "Ata de Julgamento"),
    ]

    processo = models.ForeignKey(
        Processo, on_delete=models.CASCADE, related_name="documentos"
    )
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    arquivo = models.FileField(upload_to="uploads/%Y/%m/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    # Extração automática (Gemini) — JSON com campos + confiança
    extracao_json = models.JSONField(blank=True, null=True)

    class Meta:
        unique_together = [("processo", "tipo")]
        verbose_name = "Documento"
        verbose_name_plural = "Documentos"

    def __str__(self):
        return f"{self.get_tipo_display()} — PA {self.processo.pa}"


# ─── Admissibilidade (Fase 3 — prazos e decisões do julgador) ────────────────


class Admissibilidade(models.Model):
    """
    Resultado da análise de admissibilidade (tempestividade, prescrição, decadência).
    Separa flags automáticas (calculadas por jari_math) das escolhas do julgador.
    """

    processo = models.OneToOneField(
        Processo, on_delete=models.CASCADE, related_name="admissibilidade"
    )

    # ── Datas preenchidas pelo MJ (Passo 2) ──
    data_infracao = models.DateField(null=True, blank=True)
    data_infracao_fallback = models.BooleanField(default=False)
    data_na = models.DateField(null=True, blank=True, help_text="Notificação de Autuação")
    data_np = models.DateField(null=True, blank=True, help_text="Notificação de Penalidade")
    data_instauracao = models.DateField(null=True, blank=True, help_text="Instauração do processo")
    data_totalizacao_pontos = models.DateField(null=True, blank=True)
    data_conclusao_multa = models.DateField(null=True, blank=True)
    data_conhecimento_infracao = models.DateField(null=True, blank=True)

    # Tipo de penalidade — determina regras de decadência (FILTRO 2/3)
    tipo_penalidade = models.CharField(
        max_length=20,
        blank=True,
        choices=[
            ("multa", "Multa"),
            ("advertencia", "Advertência"),
            ("suspensao", "Suspensão"),
            ("cassacao", "Cassação"),
        ],
    )
    tem_flagrante = models.BooleanField(null=True, blank=True)

    # ── Flags automáticas (calculadas por jari_math) ──
    is_tempestivo = models.BooleanField(null=True, blank=True)
    has_prescricao_punitiva = models.BooleanField(null=True, blank=True)
    has_prescricao_intercorrente = models.BooleanField(null=True, blank=True)
    has_prescricao_intercorrente_bienal = models.BooleanField(null=True, blank=True)
    has_decadencia = models.BooleanField(null=True, blank=True)

    # ── Escolhas do julgador (prevalecem sobre as flags) ──
    julgador_tempestivo = models.BooleanField(null=True, blank=True)
    julgador_prescricao_punitiva = models.BooleanField(null=True, blank=True)
    julgador_prescricao_intercorrente = models.BooleanField(null=True, blank=True)
    julgador_prescricao_intercorrente_bienal = models.BooleanField(null=True, blank=True)
    julgador_decadencia = models.BooleanField(null=True, blank=True)

    # ── Textos gerados pela IA ──
    texto_resultado = models.TextField(
        blank=True, help_text="Texto completo da análise de admissibilidade"
    )
    fundamentacoes = models.JSONField(
        default=dict, blank=True,
        help_text="Fundamentação individual por critério {tempestivo, punitiva, intercorrente, bienal, decadencia}"
    )
    tabela_datas_sensiveis = models.TextField(blank=True)
    infracao_documento = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "Admissibilidade"
        verbose_name_plural = "Admissibilidades"

    def __str__(self):
        return f"Admissibilidade — PA {self.processo.pa}"

    @property
    def flag_tempestivo(self):
        """Retorna a decisão efetiva (julgador prevalece)."""
        if self.julgador_tempestivo is not None:
            return self.julgador_tempestivo
        return self.is_tempestivo

    @property
    def flag_prescricao_punitiva(self):
        if self.julgador_prescricao_punitiva is not None:
            return self.julgador_prescricao_punitiva
        return self.has_prescricao_punitiva

    @property
    def flag_prescricao_intercorrente(self):
        if self.julgador_prescricao_intercorrente is not None:
            return self.julgador_prescricao_intercorrente
        return self.has_prescricao_intercorrente

    @property
    def flag_prescricao_intercorrente_bienal(self):
        if self.julgador_prescricao_intercorrente_bienal is not None:
            return self.julgador_prescricao_intercorrente_bienal
        return self.has_prescricao_intercorrente_bienal

    @property
    def flag_decadencia(self):
        if self.julgador_decadencia is not None:
            return self.julgador_decadencia
        return self.has_decadencia

    @property
    def rota(self):
        """
        Determina a rota do processo baseada nas flags efetivas.
        C > B > A > D (ordem de prioridade).
        """
        if self.flag_decadencia:
            return "C"
        if self.flag_prescricao_punitiva or self.flag_prescricao_intercorrente or self.flag_prescricao_intercorrente_bienal:
            return "B"
        if self.flag_tempestivo is False:
            return "A"
        return "D"


# ─── Análise de Tese (Fase 4 — uma por tese identificada) ───────────────────


class AnaliseTese(models.Model):
    processo = models.ForeignKey(
        Processo, on_delete=models.CASCADE, related_name="teses"
    )
    ordem = models.IntegerField(default=1)
    titulo = models.CharField(max_length=500)
    fundamentacao = models.TextField(blank=True)
    acolhida = models.BooleanField(null=True, blank=True)

    # Fontes consultadas
    vertex_resultado = models.TextField(blank=True)
    perplexity_resultado = models.TextField(blank=True)

    class Meta:
        ordering = ["ordem"]
        verbose_name = "Análise de Tese"
        verbose_name_plural = "Análises de Teses"

    def __str__(self):
        status = "Acolhida" if self.acolhida else "Não acolhida" if self.acolhida is False else "Pendente"
        return f"Tese {self.ordem}: {self.titulo[:50]} — {status}"


# ─── Parecer (Fase 5 — texto final gerado) ──────────────────────────────────


class Parecer(models.Model):
    processo = models.OneToOneField(
        Processo, on_delete=models.CASCADE, related_name="parecer"
    )

    # Texto gerado pela IA (markdown)
    texto_ia = models.TextField(blank=True)
    provider = models.CharField(max_length=20, blank=True)

    # Texto editado pelo julgador no TinyMCE (HTML)
    texto_editado = models.TextField(blank=True)

    # Auditoria / Blindagem (Fase 6)
    blindagem_score = models.IntegerField(null=True, blank=True)
    blindagem_detalhes = models.TextField(blank=True)
    checklist_auditoria = models.JSONField(blank=True, null=True)

    # Feedback do usuário (Fase 8)
    feedback_score = models.IntegerField(null=True, blank=True)
    feedback_tags = models.CharField(max_length=500, blank=True)
    feedback_notas = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Parecer"
        verbose_name_plural = "Pareceres"

    def __str__(self):
        return f"Parecer — PA {self.processo.pa}"

    @property
    def conteudo_final(self):
        """Retorna texto editado (HTML) se existir, senão o gerado pela IA."""
        return self.texto_editado or self.texto_ia or ""


# ─── Configuração do Parecer (cabeçalho/rodapé) ─────────────────────────────


class ConfiguracaoParecer(models.Model):
    cabecalho_imagem = models.ImageField(
        upload_to="assets/logos/",
        null=True,
        blank=True,
        help_text="Banner completo (PNG/JPG) com proporção wide.",
    )
    rodape_deferido = models.TextField(help_text="HTML para caso de Deferimento")
    rodape_indeferido = models.TextField(help_text="HTML para caso de Indeferimento")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from django.core.cache import cache
        cache.delete("configuracao_parecer")

    class Meta:
        verbose_name = "Configuração do Parecer"
        verbose_name_plural = "Configurações do Parecer"

    def __str__(self):
        return "Configuração Global do Parecer"


# ─── Audit Log (unifica AuditEvent + AiRequestLog) ──────────────────────────


class AuditLog(models.Model):
    CATEGORIA_CHOICES = [
        ("fase", "Transição de Fase"),
        ("ia_request", "Requisição IA"),
        ("erro", "Erro"),
        ("decisao", "Decisão do Julgador"),
        ("upload", "Upload de Documento"),
        ("credito", "Crédito Consumido"),
    ]

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    categoria = models.CharField(max_length=20, choices=CATEGORIA_CHOICES, db_index=True)
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs"
    )
    processo = models.ForeignKey(
        Processo, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs"
    )
    fase = models.CharField(max_length=30, blank=True)

    # Detalhes genéricos
    dados = models.JSONField(default=dict, blank=True)

    # Campos específicos para requisições IA
    provider = models.CharField(max_length=50, blank=True)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    latency_ms = models.IntegerField(default=0)
    model_name = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["categoria", "timestamp"]),
            models.Index(fields=["user", "timestamp"]),
            models.Index(fields=["provider", "-timestamp"]),
        ]
        verbose_name = "Log de Auditoria"
        verbose_name_plural = "Logs de Auditoria"

    def __str__(self):
        return f"{self.categoria} | {self.fase} | {self.timestamp:%d/%m/%Y %H:%M}"


def log_audit(categoria: str, processo=None, fase: str = "", dados: dict = None, **kwargs):
    """Helper para registrar auditoria sem bloquear o fluxo principal."""
    try:
        AuditLog.objects.create(
            categoria=categoria,
            user=processo.user if processo else kwargs.get("user"),
            processo=processo,
            fase=fase,
            dados=dados or {},
            provider=kwargs.get("provider", ""),
            input_tokens=kwargs.get("input_tokens", 0),
            output_tokens=kwargs.get("output_tokens", 0),
            latency_ms=kwargs.get("latency_ms", 0),
            model_name=kwargs.get("model_name", ""),
        )
    except Exception:
        pass


# ─── Cache de Teses (otimização Vertex + Perplexity) ────────────────────────


class ChatMessage(models.Model):
    """Histórico de mensagens do agente lateral (drawer RAG)."""

    ROLE_CHOICES = [
        ("user", "Usuário"),
        ("assistant", "Assistente"),
    ]

    processo = models.ForeignKey(
        Processo, on_delete=models.CASCADE, related_name="chat_messages"
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Mensagem do Chat"
        verbose_name_plural = "Mensagens do Chat"

    def __str__(self):
        return f"{self.role}: {self.content[:60]}"


# ─── Fórum (comunidade de julgadores) ──────────────────────────────────────


class PostForum(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="posts_forum")
    titulo = models.CharField(max_length=300)
    conteudo = models.TextField()
    curtidas = models.ManyToManyField(User, blank=True, related_name="posts_curtidos")
    is_pinned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]
        verbose_name = "Post do Fórum"
        verbose_name_plural = "Posts do Fórum"

    def __str__(self):
        return f"{self.titulo[:60]} — {self.user.username}"


class ComentarioForum(models.Model):
    post = models.ForeignKey(PostForum, on_delete=models.CASCADE, related_name="comentarios")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="comentarios_forum")
    conteudo = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Comentário do Fórum"
        verbose_name_plural = "Comentários do Fórum"

    def __str__(self):
        return f"Re: {self.post.titulo[:30]} — {self.user.username}"


class BancoTese(models.Model):
    """Banco de teses da comunidade — compartilhamento entre julgadores."""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="banco_teses"
    )
    titulo = models.CharField(max_length=500)
    conteudo = models.TextField()
    tipo_infracao = models.CharField(max_length=100, blank=True, db_index=True)
    is_public = models.BooleanField(default=False, db_index=True)
    usage_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-usage_count", "-created_at"]
        verbose_name = "Tese (Banco)"
        verbose_name_plural = "Teses (Banco)"

    def __str__(self):
        vis = "pub" if self.is_public else "priv"
        return f"[{vis}] {self.titulo[:60]} ({self.usage_count} usos)"


# ─── DocumentoNormativo (base RAG local) ───────────────────────────────────


class DocumentoNormativo(models.Model):
    """Fragmento de documento normativo indexado para RAG vetorial."""

    nome_arquivo = models.CharField(max_length=500, db_index=True)
    pagina_inicio = models.IntegerField(default=1)
    texto = models.TextField()
    embedding = VectorField(
        dimensions=EMBEDDING_DIM, null=True, blank=True,
        help_text="Vetor de embedding (pgvector) — gerado por sentence-transformers.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nome_arquivo", "pagina_inicio"]
        verbose_name = "Documento Normativo"
        verbose_name_plural = "Documentos Normativos"
        indexes = [
            models.Index(fields=["nome_arquivo", "pagina_inicio"]),
            HnswIndex(
                name="docnorm_emb_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self):
        return f"{self.nome_arquivo} p.{self.pagina_inicio}"


class CacheTese(models.Model):
    cache_key = models.CharField(max_length=255, unique=True)
    vertex_resultado = models.TextField()
    perplexity_resultado = models.TextField()
    tipo_infracao = models.CharField(max_length=50, blank=True, db_index=True)
    hit_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cache de Tese"
        verbose_name_plural = "Cache de Teses"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.cache_key} (usos: {self.hit_count})"
