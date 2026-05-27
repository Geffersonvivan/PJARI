from django.urls import path
from . import views
from .views_pdf import exportar_parecer_pdf
from .views_relatorio import relatorio_mensal, relatorio_mensal_pdf
from .views_stats import estatisticas_view, estatisticas_gerais_view

app_name = "pareceres"

urlpatterns = [
    # Wizard principal
    path("app/", views.home, name="home"),
    path("processo/novo/", views.processo_novo, name="processo_novo"),
    path("processo/<int:pk>/", views.processo_wizard, name="processo_wizard"),

    # API: Estado e Tasks
    path("api/processo/<int:pk>/fase/", views.api_fase_atual, name="api_fase"),
    path("api/processo/<int:pk>/avancar/", views.api_avancar_fase, name="api_avancar"),
    path("api/task-status/<str:task_id>/", views.api_task_status, name="api_task_status"),
    path("api/task-stream/<str:task_id>/", views.api_task_stream, name="api_task_stream"),

    # API: Upload de Documentos (passo 1)
    path("api/processo/<int:pk>/documentos/", views.api_documentos_upload, name="api_documentos_upload"),

    # API: Dados extraídos + confirmação (passo 2)
    path("api/processo/<int:pk>/dados-extraidos/", views.api_dados_extraidos, name="api_dados_extraidos"),
    path("api/processo/<int:pk>/confirmar-dados/", views.api_confirmar_dados, name="api_confirmar_dados"),

    # API: Passo 3 — Admissibilidade
    path("api/processo/<int:pk>/admissibilidade/", views.api_admissibilidade_dados, name="api_admissibilidade_dados"),
    path("api/processo/<int:pk>/admissibilidade/confirmar/", views.api_admissibilidade_confirmar, name="api_admissibilidade_confirmar"),
    path("api/processo/<int:pk>/admissibilidade/recalcular/", views.api_admissibilidade_recalcular, name="api_admissibilidade_recalcular"),

    # API: Passo 4 — Teses
    path("api/processo/<int:pk>/teses/", views.api_teses_dados, name="api_teses_dados"),
    path("api/processo/<int:pk>/teses/confirmar/", views.api_teses_confirmar, name="api_teses_confirmar"),
    path("api/processo/<int:pk>/teses/reextrair/", views.api_teses_reextrair, name="api_teses_reextrair"),

    # API: Passo 5 — Parecer
    path("api/processo/<int:pk>/parecer/", views.api_parecer_dados, name="api_parecer_dados"),
    path("api/processo/<int:pk>/parecer/editar/", views.api_parecer_editar, name="api_parecer_editar"),

    # API: Passo 6 — Auditoria/Finalizar
    path("api/processo/<int:pk>/auditoria/finalizar/", views.api_auditoria_finalizar, name="api_auditoria_finalizar"),

    # API: Excluir processo
    path("api/processo/<int:pk>/excluir/", views.api_processo_excluir, name="api_processo_excluir"),

    # API: Mover processo para pasta
    path("api/processo/<int:pk>/mover/", views.api_processo_mover, name="api_processo_mover"),

    # API: Feedback
    path("api/processo/<int:pk>/feedback/", views.api_feedback, name="api_feedback"),

    # API: Agente Lateral
    path("api/processo/<int:pk>/agente/", views.api_agente_mensagem, name="api_agente_mensagem"),
    path("api/processo/<int:pk>/agente/historico/", views.api_agente_historico, name="api_agente_historico"),

    # API: Pastas
    path("api/pastas/", views.api_pastas_listar, name="api_pastas_listar"),
    path("api/pastas/criar/", views.api_pasta_criar, name="api_pasta_criar"),
    path("api/pastas/<int:pasta_id>/processos/", views.api_pasta_processos, name="api_pasta_processos"),
    path("api/pastas/<int:pasta_id>/renomear/", views.api_pasta_renomear, name="api_pasta_renomear"),
    path("api/pastas/<int:pasta_id>/excluir/", views.api_pasta_excluir, name="api_pasta_excluir"),
    path("api/pastas/reordenar/", views.api_pasta_reordenar, name="api_pasta_reordenar"),

    # API: Fórum
    path("api/forum/", views.api_forum_listar, name="api_forum_listar"),
    path("api/forum/unread/", views.api_forum_unread_count, name="api_forum_unread_count"),
    path("api/forum/criar/", views.api_forum_criar_post, name="api_forum_criar_post"),
    path("api/forum/<int:post_id>/comentarios/", views.api_forum_comentarios, name="api_forum_comentarios"),
    path("api/forum/<int:post_id>/comentar/", views.api_forum_comentar, name="api_forum_comentar"),
    path("api/forum/<int:post_id>/curtir/", views.api_forum_curtir, name="api_forum_curtir"),

    # API: Banco de Teses
    path("api/teses-banco/", views.api_banco_teses_listar, name="api_banco_teses_listar"),
    path("api/teses-banco/criar/", views.api_banco_tese_criar, name="api_banco_tese_criar"),
    path("api/teses-banco/<int:tese_id>/editar/", views.api_banco_tese_editar, name="api_banco_tese_editar"),
    path("api/teses-banco/<int:tese_id>/excluir/", views.api_banco_tese_excluir, name="api_banco_tese_excluir"),
    path("api/teses-banco/<int:tese_id>/importar/", views.api_banco_tese_importar, name="api_banco_tese_importar"),
    path("api/teses-banco/<int:tese_id>/usar/", views.api_banco_tese_usar, name="api_banco_tese_usar"),

    # Exportação PDF (antes do proxy para não ser capturado por <str:tipo>)
    path("processo/<int:pk>/pdf/exportar/", exportar_parecer_pdf, name="exportar_parecer_pdf"),

    # PDF proxy (iframe viewer)
    path("processo/<int:pk>/pdf/<str:tipo>/", views.pdf_proxy, name="pdf_proxy"),

    # Relatório Mensal
    path("relatorio/<int:pasta_id>/", relatorio_mensal, name="relatorio_mensal"),
    path("relatorio/<int:pasta_id>/pdf/", relatorio_mensal_pdf, name="relatorio_mensal_pdf"),

    # Estatísticas
    path("estatisticas/", estatisticas_view, name="estatisticas"),
    path("estatisticas/global/", estatisticas_gerais_view, name="estatisticas_gerais"),
]
