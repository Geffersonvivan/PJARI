"""Chamada a LLM com fallback entre provedores.

Unifica o padrão "tenta o provedor A; se falhar, tenta o provedor B" usado tanto
na extração de teses (Gemini → Anthropic) quanto na geração do parecer
(Anthropic → Gemini), em vez de reimplementar o try/except em cada serviço (#6).
"""

import logging

_log = logging.getLogger(__name__)


def chamar_com_fallback(primario, secundario, *, label=""):
    """Tenta ``primario()``; se levantar exceção OU retornar None, tenta ``secundario()``.

    Convenção dos callables: retornam o texto (``str``, possivelmente ``""``) em
    sucesso, ou ``None`` em erro — o mesmo padrão de
    ``AnthropicClient.generate_text``. Um ``""`` é um resultado VÁLIDO (ex.: o LLM
    não achou teses) e é retornado como está; só ``None``/exceção contam como
    falha do provedor.

    Retorna o texto do primeiro provedor que responder, ou ``None`` se ambos
    falharem (o chamador então degrada graciosamente).
    """
    for nome, fn in (("primário", primario), ("fallback", secundario)):
        if fn is None:
            continue
        try:
            resultado = fn()
        except Exception as e:
            _log.error("[LLM-FALLBACK] %s: provedor %s levantou: %s", label, nome, e)
            continue
        if resultado is not None:
            if nome == "fallback":
                _log.info("[LLM-FALLBACK] %s: provedor fallback usado", label)
            return resultado
        _log.error("[LLM-FALLBACK] %s: provedor %s retornou None (erro)", label, nome)
    return None
