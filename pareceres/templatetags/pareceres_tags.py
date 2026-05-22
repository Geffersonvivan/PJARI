from django import template
from pareceres.estado import FaseProcesso

register = template.Library()


@register.filter
def split(value, sep=","):
    return value.split(sep)


@register.filter
def passo_wizard(fase):
    return FaseProcesso.passo_wizard(fase)


@register.filter
def nome_mes(valor):
    """Extrai nome do mês de '01 - Janeiro' → 'Janeiro'."""
    if " - " in str(valor):
        return str(valor).split(" - ", 1)[1]
    return valor


_ABREV = {
    "Janeiro": "Jan", "Fevereiro": "Fev", "Março": "Mar",
    "Abril": "Abr", "Maio": "Mai", "Junho": "Jun",
    "Julho": "Jul", "Agosto": "Ago", "Setembro": "Set",
    "Outubro": "Out", "Novembro": "Nov", "Dezembro": "Dez",
}


@register.filter
def nome_mes_abrev(valor):
    """'01 - Janeiro' → 'Jan'."""
    nome = str(valor).split(" - ", 1)[1] if " - " in str(valor) else str(valor)
    return _ABREV.get(nome, nome)
