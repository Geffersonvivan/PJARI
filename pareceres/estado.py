"""
State Machine do Processo JARI.

Cada fase corresponde a um passo do wizard.
Transições são explícitas — não é possível pular fases.
"""


class FaseProcesso:
    # Passo 1: Upload do consolidado
    DOCUMENTOS = "documentos"
    DOCUMENTOS_EXTRAINDO = "documentos_extraindo"
    DOCUMENTOS_EXTRAIDOS = "documentos_extraidos"

    # Passo 2: Revisão dos dados extraídos + data_sessao
    # (usa DOCUMENTOS_EXTRAIDOS acima)

    # Passo 3: Admissibilidade (cálculos + decisão julgador)
    ADMISSIBILIDADE = "admissibilidade"
    ADMISSIBILIDADE_AGUARDANDO = "admissibilidade_aguardando"

    # Passo 4: Teses (extração + análise)
    TESE = "tese"
    TESE_AGUARDANDO = "tese_aguardando"

    # Passo 5: Parecer (geração + edição)
    PARECER = "parecer"
    PARECER_GERANDO = "parecer_gerando"

    # Passo 6: Finalização (auditoria + salvar)
    AUDITORIA = "auditoria"
    FINALIZADO = "finalizado"

    @classmethod
    def choices(cls):
        return [
            (cls.DOCUMENTOS, "1. Documentos"),
            (cls.DOCUMENTOS_EXTRAINDO, "1. Documentos (extraindo)"),
            (cls.DOCUMENTOS_EXTRAIDOS, "2. Dados Extraídos"),
            (cls.ADMISSIBILIDADE, "3. Admissibilidade"),
            (cls.ADMISSIBILIDADE_AGUARDANDO, "3. Admissibilidade (aguardando)"),
            (cls.TESE, "4. Tese"),
            (cls.TESE_AGUARDANDO, "4. Tese (aguardando)"),
            (cls.PARECER, "5. Parecer"),
            (cls.PARECER_GERANDO, "5. Parecer (gerando)"),
            (cls.AUDITORIA, "6. Auditoria"),
            (cls.FINALIZADO, "Finalizado"),
        ]

    @classmethod
    def transicoes_validas(cls):
        """Mapa de transições válidas. Chave = fase atual, valor = fases permitidas."""
        return {
            cls.DOCUMENTOS: [cls.DOCUMENTOS_EXTRAINDO],
            cls.DOCUMENTOS_EXTRAINDO: [cls.DOCUMENTOS_EXTRAIDOS, cls.DOCUMENTOS],
            cls.DOCUMENTOS_EXTRAIDOS: [cls.ADMISSIBILIDADE],
            cls.ADMISSIBILIDADE: [cls.ADMISSIBILIDADE_AGUARDANDO, cls.DOCUMENTOS_EXTRAIDOS],
            cls.ADMISSIBILIDADE_AGUARDANDO: [cls.TESE, cls.PARECER],
            # Rotas A/B/C pulam tese e vão direto pro parecer
            cls.TESE: [cls.TESE_AGUARDANDO, cls.PARECER],
            cls.TESE_AGUARDANDO: [cls.PARECER],
            cls.PARECER: [cls.PARECER_GERANDO, cls.AUDITORIA],
            cls.PARECER_GERANDO: [cls.AUDITORIA, cls.PARECER],
            cls.AUDITORIA: [cls.FINALIZADO],
            cls.FINALIZADO: [],
        }

    @classmethod
    def passo_wizard(cls, fase: str) -> int:
        """Retorna o número do passo no stepper (1-6) para a fase atual."""
        mapa = {
            cls.DOCUMENTOS: 1,
            cls.DOCUMENTOS_EXTRAINDO: 1,
            cls.DOCUMENTOS_EXTRAIDOS: 2,
            cls.ADMISSIBILIDADE: 3,
            cls.ADMISSIBILIDADE_AGUARDANDO: 3,
            cls.TESE: 4,
            cls.TESE_AGUARDANDO: 4,
            cls.PARECER: 5,
            cls.PARECER_GERANDO: 5,
            cls.AUDITORIA: 6,
            cls.FINALIZADO: 6,
        }
        return mapa.get(fase, 1)

    @classmethod
    def label(cls, fase: str) -> str:
        """Retorna label legível da fase."""
        for value, label in cls.choices():
            if value == fase:
                return label
        return fase
