"""
Services do pipeline JARI — cada módulo encapsula a lógica de uma fase.

Cada service expõe uma função execute(processo) -> ServiceResult.
Os Celery tasks em pareceres/tasks.py chamam esses services.
"""

from dataclasses import dataclass, field


@dataclass
class ServiceResult:
    """Resultado padronizado dos services de fase."""
    ok: bool = True
    mensagem: str = ""
    dados: dict = field(default_factory=dict)
    erro: str = ""

    @classmethod
    def sucesso(cls, mensagem: str = "", **dados):
        return cls(ok=True, mensagem=mensagem, dados=dados)

    @classmethod
    def falha(cls, erro: str, **dados):
        return cls(ok=False, erro=erro, dados=dados)
