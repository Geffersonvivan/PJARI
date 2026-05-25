"""
Management command: auditar_pjari

Executa 12 cenarios de teste contra JariMath e logica de roteamento,
cobrindo 100% das rotas (A/B/C/D), filtros temporais (1/2/3),
desconto COVID, trava bienal, e blindagem CETRAN/SC.

Uso:
    python manage.py auditar_pjari
    python manage.py auditar_pjari --cenario 5
    python manage.py auditar_pjari --verbose
"""

import datetime
from dataclasses import dataclass, field
from typing import Optional

from django.core.management.base import BaseCommand

from pareceres.math import JariMath


@dataclass
class Cenario:
    id: int
    nome: str
    rota_esperada: str
    # Datas de entrada
    data_infracao: datetime.date
    data_sessao: datetime.date
    data_protocolo: datetime.date
    prazo_final: datetime.date
    data_expedicao_autuacao: datetime.date
    # Extras opcionais
    marcos_interruptivos: list = field(default_factory=list)
    tipo_penalidade: str = ""
    tem_flagrante: Optional[bool] = None
    data_conclusao_multa: Optional[datetime.date] = None
    data_conhecimento_infracao: Optional[datetime.date] = None
    data_decisao_final: Optional[datetime.date] = None
    # Resultados esperados
    expect_tempestivo: Optional[bool] = None
    expect_punitiva: bool = False
    expect_intercorrente: bool = False
    expect_bienal: bool = False
    expect_decadencia: bool = False
    descricao: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# 12 CENARIOS DE TESTE
# ─────────────────────────────────────────────────────────────────────────────

CENARIOS = [
    # ── ROTA A: Intempestivo ──
    Cenario(
        id=1,
        nome="Rota A — Intempestivo simples",
        rota_esperada="A",
        descricao="Protocolo apos prazo final. Sem prescricao/decadencia.",
        data_infracao=datetime.date(2023, 6, 15),
        data_expedicao_autuacao=datetime.date(2023, 6, 20),
        data_sessao=datetime.date(2024, 3, 10),
        data_protocolo=datetime.date(2024, 2, 1),
        prazo_final=datetime.date(2024, 1, 15),
        expect_tempestivo=False,
        expect_punitiva=False,
        expect_intercorrente=False,
        expect_bienal=False,
        expect_decadencia=False,
    ),
    # ── ROTA B: Prescricao Punitiva ──
    Cenario(
        id=2,
        nome="Rota B — Prescricao punitiva (5 anos expirados)",
        rota_esperada="B",
        descricao="Infracao > 5 anos antes da sessao, sem marcos interruptivos.",
        data_infracao=datetime.date(2018, 1, 10),
        data_expedicao_autuacao=datetime.date(2018, 2, 15),
        data_sessao=datetime.date(2024, 3, 10),
        data_protocolo=datetime.date(2024, 1, 5),
        prazo_final=datetime.date(2024, 1, 20),
        expect_tempestivo=True,
        expect_punitiva=True,
        expect_intercorrente=False,
        expect_bienal=False,
        expect_decadencia=False,  # FILTRO 1 blinda
    ),
    Cenario(
        id=3,
        nome="Rota B — Prescricao punitiva com COVID",
        rota_esperada="D",
        descricao="Infracao 2019 + desconto COVID 256 dias empurra aniversario alem da sessao.",
        data_infracao=datetime.date(2019, 6, 1),
        data_expedicao_autuacao=datetime.date(2019, 7, 10),
        data_sessao=datetime.date(2024, 11, 15),
        data_protocolo=datetime.date(2024, 9, 1),
        prazo_final=datetime.date(2024, 10, 1),
        marcos_interruptivos=[],
        expect_tempestivo=True,
        expect_punitiva=False,  # 5yr=2024-06-01 + 256d COVID = 2025-02-12 > sessao
        expect_intercorrente=False,
        expect_bienal=False,
        expect_decadencia=False,  # FILTRO 1 blinda
    ),
    Cenario(
        id=4,
        nome="Rota B — Prescricao punitiva com marco interruptivo",
        rota_esperada="D",
        descricao="Marco interruptivo reseta contagem. Sessao dentro dos 5 anos do marco.",
        data_infracao=datetime.date(2018, 3, 1),
        data_expedicao_autuacao=datetime.date(2018, 4, 15),
        data_sessao=datetime.date(2024, 6, 10),
        data_protocolo=datetime.date(2024, 5, 1),
        prazo_final=datetime.date(2024, 5, 20),
        marcos_interruptivos=[datetime.date(2020, 8, 15)],
        expect_tempestivo=True,
        expect_punitiva=False,  # 5yr do marco = 2025-08-15 > sessao
        expect_intercorrente=False,
        expect_bienal=False,
        expect_decadencia=False,  # FILTRO 1 blinda
    ),
    # ── ROTA B: Prescricao Intercorrente ──
    Cenario(
        id=5,
        nome="Rota B — Prescricao intercorrente (3 anos protocolo→sessao)",
        rota_esperada="B",
        descricao="Mais de 3 anos entre protocolo e sessao.",
        data_infracao=datetime.date(2021, 5, 1),
        data_expedicao_autuacao=datetime.date(2021, 6, 10),
        data_sessao=datetime.date(2025, 2, 10),
        data_protocolo=datetime.date(2021, 8, 1),
        prazo_final=datetime.date(2021, 9, 1),
        expect_tempestivo=True,
        expect_punitiva=False,
        expect_intercorrente=True,  # 3yr do protocolo = 2024-08-01 < sessao
        expect_bienal=False,
        expect_decadencia=False,  # FILTRO 2 multa default
    ),
    # ── ROTA B: Prescricao Bienal ──
    Cenario(
        id=6,
        nome="Rota B — Prescricao bienal (protocolo >= 01/01/2024)",
        rota_esperada="B",
        descricao="Protocolo em 2024, sessao > 2 anos depois.",
        data_infracao=datetime.date(2023, 11, 1),
        data_expedicao_autuacao=datetime.date(2023, 11, 15),
        data_sessao=datetime.date(2026, 3, 15),
        data_protocolo=datetime.date(2024, 1, 10),
        prazo_final=datetime.date(2024, 2, 10),
        expect_tempestivo=True,
        expect_punitiva=False,
        expect_intercorrente=False,
        expect_bienal=True,  # 2yr = 2026-01-10 < sessao
        expect_decadencia=False,
    ),
    Cenario(
        id=7,
        nome="Trava bienal — protocolo antes de 01/01/2024",
        rota_esperada="D",
        descricao="Protocolo em 2023: bienal NAO SE APLICA mesmo com > 2 anos.",
        data_infracao=datetime.date(2022, 6, 1),
        data_expedicao_autuacao=datetime.date(2022, 7, 10),
        data_sessao=datetime.date(2026, 3, 15),
        data_protocolo=datetime.date(2023, 8, 1),
        prazo_final=datetime.date(2023, 9, 1),
        expect_tempestivo=True,
        expect_punitiva=False,
        expect_intercorrente=False,
        expect_bienal=False,  # Trava: protocolo < 2024
        expect_decadencia=False,
    ),
    # ── ROTA C: Decadencia ──
    Cenario(
        id=8,
        nome="Rota C — Decadencia FILTRO 3 (>180 dias flagrante)",
        rota_esperada="C",
        descricao="Infracao pos-22/10/2021, flagrante, >180 dias ate notificacao.",
        data_infracao=datetime.date(2023, 1, 15),
        data_expedicao_autuacao=datetime.date(2023, 8, 20),
        data_sessao=datetime.date(2024, 3, 10),
        data_protocolo=datetime.date(2024, 1, 5),
        prazo_final=datetime.date(2024, 2, 5),
        tem_flagrante=True,
        expect_tempestivo=True,
        expect_punitiva=False,
        expect_intercorrente=False,
        expect_bienal=False,
        expect_decadencia=True,  # 217 dias > 180
    ),
    Cenario(
        id=9,
        nome="Rota D — Decadencia FILTRO 1 blindada (CETRAN 381/2022)",
        rota_esperada="D",
        descricao="Infracao antes de 12/04/2021: decadencia proibida pelo CETRAN.",
        data_infracao=datetime.date(2019, 3, 10),
        data_expedicao_autuacao=datetime.date(2020, 6, 15),
        data_sessao=datetime.date(2024, 3, 10),
        data_protocolo=datetime.date(2024, 1, 5),
        prazo_final=datetime.date(2024, 2, 5),
        expect_tempestivo=True,
        expect_punitiva=False,
        expect_intercorrente=False,
        expect_bienal=False,
        expect_decadencia=False,  # Blindagem CETRAN 381/2022
    ),
    Cenario(
        id=10,
        nome="Rota C — Decadencia FILTRO 2 (multa, >180 dias)",
        rota_esperada="C",
        descricao="Infracao entre 12/04-21/10/2021, multa, flagrante, >180 dias.",
        data_infracao=datetime.date(2021, 5, 10),
        data_expedicao_autuacao=datetime.date(2022, 1, 15),
        data_sessao=datetime.date(2024, 3, 10),
        data_protocolo=datetime.date(2024, 1, 5),
        prazo_final=datetime.date(2024, 2, 5),
        tipo_penalidade="multa",
        tem_flagrante=True,
        expect_tempestivo=True,
        expect_punitiva=False,
        expect_intercorrente=False,
        expect_bienal=False,
        expect_decadencia=True,  # 250 dias > 180
    ),
    Cenario(
        id=11,
        nome="Decadencia FILTRO 2 — Suspensao blindada (CETRAN)",
        rota_esperada="D",
        descricao="FILTRO 2 + suspensao: CETRAN blinda. Nao configura decadencia.",
        data_infracao=datetime.date(2021, 7, 1),
        data_expedicao_autuacao=datetime.date(2022, 5, 10),
        data_sessao=datetime.date(2024, 3, 10),
        data_protocolo=datetime.date(2024, 1, 5),
        prazo_final=datetime.date(2024, 2, 5),
        tipo_penalidade="suspensao",
        expect_tempestivo=True,
        expect_punitiva=False,
        expect_intercorrente=False,
        expect_bienal=False,
        expect_decadencia=False,  # CETRAN blinda suspensao no FILTRO 2
    ),
    # ── ROTA D: Merito ──
    Cenario(
        id=12,
        nome="Rota D — Merito puro (tudo OK)",
        rota_esperada="D",
        descricao="Tempestivo, sem prescricao, sem decadencia. Vai para analise de teses.",
        data_infracao=datetime.date(2024, 2, 1),
        data_expedicao_autuacao=datetime.date(2024, 2, 20),
        data_sessao=datetime.date(2024, 8, 15),
        data_protocolo=datetime.date(2024, 5, 1),
        prazo_final=datetime.date(2024, 6, 1),
        expect_tempestivo=True,
        expect_punitiva=False,
        expect_intercorrente=False,
        expect_bienal=False,
        expect_decadencia=False,
    ),
]


def _determinar_rota(tempestivo, punitiva, intercorrente, bienal, decadencia):
    """Replica Admissibilidade.rota sem precisar de instancia Django."""
    if decadencia:
        return "C"
    if punitiva or intercorrente or bienal:
        return "B"
    if tempestivo is False:
        return "A"
    return "D"


def _desconto_covid_para_punitiva(ultimo_marco):
    """Calcula desconto COVID para prescricao punitiva."""
    if ultimo_marco and ultimo_marco <= JariMath.FIM_COVID_SUSPENSAO:
        if ultimo_marco >= JariMath.INICIO_COVID_SUSPENSAO:
            return (JariMath.FIM_COVID_SUSPENSAO - ultimo_marco).days + 1
        return JariMath.DIAS_SUSPENSAO_COVID
    return 0


class Command(BaseCommand):
    help = "Audita 100%% da logica JariMath + roteamento contra 12 cenarios deterministicos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--cenario", type=int, default=0,
            help="Executar apenas um cenario especifico (1-12).",
        )
        parser.add_argument(
            "--verbose", action="store_true",
            help="Exibir relatorios detalhados de cada calculo.",
        )

    def handle(self, *args, **options):
        cenario_id = options["cenario"]
        verbose = options["verbose"]

        cenarios = CENARIOS
        if cenario_id:
            cenarios = [c for c in CENARIOS if c.id == cenario_id]
            if not cenarios:
                self.stderr.write(self.style.ERROR(f"Cenario {cenario_id} nao encontrado."))
                return

        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO("=" * 70))
        self.stdout.write(self.style.HTTP_INFO("  AUDITORIA P-JARI — JariMath + Roteamento"))
        self.stdout.write(self.style.HTTP_INFO("=" * 70))
        self.stdout.write("")

        total = len(cenarios)
        ok = 0
        falhas = []

        for c in cenarios:
            erros_cenario = []

            # 1. Tempestividade
            tempestivo = JariMath.check_tempestividade(c.data_protocolo, c.prazo_final)
            if tempestivo != c.expect_tempestivo:
                erros_cenario.append(
                    f"  tempestividade: got={tempestivo}, expected={c.expect_tempestivo}"
                )

            # 2. Prescricao Punitiva
            marcos = c.marcos_interruptivos or []
            ultimo_marco = max(marcos) if marcos else c.data_infracao
            desconto_covid = _desconto_covid_para_punitiva(ultimo_marco)
            punitiva, rel_punitiva = JariMath.check_prescription_punitiva(
                c.data_infracao, c.data_sessao,
                marcos_interruptivos=marcos or None,
                desconto_covid_dias=desconto_covid,
            )
            if punitiva != c.expect_punitiva:
                erros_cenario.append(
                    f"  punitiva: got={punitiva}, expected={c.expect_punitiva}"
                )

            # 3. Prescricao Intercorrente (trienal)
            intercorrente, rel_inter = JariMath.check_prescription_intercorrente(
                c.data_protocolo, c.data_sessao
            )
            if intercorrente != c.expect_intercorrente:
                erros_cenario.append(
                    f"  intercorrente: got={intercorrente}, expected={c.expect_intercorrente}"
                )

            # 4. Prescricao Bienal
            bienal, rel_bienal = JariMath.check_prescription_intercorrente_bienal(
                c.data_protocolo, c.data_sessao
            )
            if bienal != c.expect_bienal:
                erros_cenario.append(
                    f"  bienal: got={bienal}, expected={c.expect_bienal}"
                )

            # 5. Decadencia
            decadencia, rel_decadencia = JariMath.check_decadencia(
                data_infracao=c.data_infracao,
                data_expedicao_autuacao=c.data_expedicao_autuacao,
                data_decisao_final=c.data_decisao_final,
                tipo_penalidade=c.tipo_penalidade or None,
                data_conclusao_multa=c.data_conclusao_multa,
                tem_flagrante=c.tem_flagrante,
                data_conhecimento_infracao=c.data_conhecimento_infracao,
            )
            if decadencia != c.expect_decadencia:
                erros_cenario.append(
                    f"  decadencia: got={decadencia}, expected={c.expect_decadencia}"
                )

            # 6. Roteamento
            rota = _determinar_rota(tempestivo, punitiva, intercorrente, bienal, decadencia)
            if rota != c.rota_esperada:
                erros_cenario.append(
                    f"  rota: got={rota}, expected={c.rota_esperada}"
                )

            # Resultado
            if erros_cenario:
                self.stdout.write(self.style.ERROR(
                    f"  FALHA  #{c.id:02d} {c.nome}"
                ))
                for e in erros_cenario:
                    self.stdout.write(self.style.ERROR(e))
                falhas.append(c)
            else:
                self.stdout.write(self.style.SUCCESS(
                    f"  OK     #{c.id:02d} {c.nome} -> Rota {rota}"
                ))
                ok += 1

            if verbose:
                self.stdout.write(f"         {c.descricao}")
                self.stdout.write(f"         tempestivo={tempestivo} punitiva={punitiva} "
                                  f"intercorrente={intercorrente} bienal={bienal} "
                                  f"decadencia={decadencia}")
                if punitiva or c.expect_punitiva:
                    self.stdout.write(f"         [PUNITIVA] {rel_punitiva}")
                if decadencia or c.expect_decadencia:
                    self.stdout.write(f"         [DECADENCIA]\n{rel_decadencia}")
                self.stdout.write("")

        # Resumo
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO("-" * 70))
        pct = (ok / total * 100) if total else 0
        resumo = f"  Resultado: {ok}/{total} cenarios OK ({pct:.0f}%)"

        if falhas:
            self.stdout.write(self.style.ERROR(resumo))
            self.stdout.write(self.style.ERROR(
                f"  Falhas: {', '.join(f'#{f.id}' for f in falhas)}"
            ))
        else:
            self.stdout.write(self.style.SUCCESS(resumo))
            self.stdout.write(self.style.SUCCESS("  TODOS OS CENARIOS PASSARAM"))

        self.stdout.write(self.style.HTTP_INFO("-" * 70))
        self.stdout.write("")
