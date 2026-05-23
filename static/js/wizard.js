/**
 * P-JARI Wizard — controla o fluxo de 6 passos do processo.
 *
 * Passo 1: Upload do PDF consolidado (modal com drag-and-drop)
 * Passo 2: Revisão dos dados extraídos + data_sessao manual
 * Passo 3: Admissibilidade
 * Passo 4: Teses
 * Passo 5: Parecer
 * Passo 6: Finalizar
 */

(function () {
  "use strict";

  const root = document.getElementById("wizard-root");
  if (!root) return;

  // ── Config do data-* ──────────────────────────────────────────────────
  const processoId = root.dataset.processoId;
  const csrf = root.dataset.csrf;
  let currentPasso = parseInt(root.dataset.passo, 10);
  let currentFase = root.dataset.fase;

  const urls = {
    fase: root.dataset.urlFase,
    avancar: root.dataset.urlAvancar,
    taskStatus: root.dataset.urlTaskStatus,
    wizard: root.dataset.urlWizard,
    documentosUpload: root.dataset.urlDocumentosUpload,
    dadosExtraidos: root.dataset.urlDadosExtraidos,
    confirmarDados: root.dataset.urlConfirmarDados,
    admissibilidadeConfirmar: root.dataset.urlAdmissibilidadeConfirmar,
    admissibilidadeRecalcular: root.dataset.urlAdmissibilidadeRecalcular,
    tesesConfirmar: root.dataset.urlTesesConfirmar,
    parecerDados: root.dataset.urlParecerDados,
    parecerEditar: root.dataset.urlParecerEditar,
    auditoriaFinalizar: root.dataset.urlAuditoriaFinalizar,
  };

  // ── Helpers ────────────────────────────────────────────────────────────

  function api(url, opts = {}) {
    const headers = { "X-CSRFToken": csrf };
    if (!(opts.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }
    return fetch(url, { ...opts, headers: { ...headers, ...opts.headers } })
      .then((r) => r.json());
  }

  function showStep(n) {
    document.querySelectorAll(".wizard-step").forEach((el) => {
      el.classList.toggle("hidden", parseInt(el.dataset.step, 10) !== n);
    });
    currentPasso = n;
    updateStepper(n);
  }

  function updateStepper(n) {
    const segs = document.querySelectorAll("#stepper-bars .progress-seg");
    const labels = document.querySelectorAll("#stepper-bars .progress-label");
    segs.forEach((seg, i) => {
      seg.classList.remove("done", "current");
      if (i + 1 < n) seg.classList.add("done");
      else if (i + 1 === n) seg.classList.add("current");
    });
    labels.forEach((lbl, i) => {
      lbl.classList.remove("active", "done");
      if (i + 1 === n) lbl.classList.add("active");
      else if (i + 1 < n) lbl.classList.add("done");
    });
    const info = document.getElementById("fase-label");
    if (info) info.textContent = "Passo " + n + " de 6";
    const stepInfo = document.querySelector(".text-sm.font-semibold.text-gray-700");
    if (stepInfo) stepInfo.textContent = "Passo " + n + " de 6";
  }

  function showLoading(id) {
    const el = document.getElementById(id);
    if (el) el.classList.remove("hidden");
    if (typeof hidePdfSidebar === "function") hidePdfSidebar();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function hideLoading(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add("hidden");
  }

  function pollTask(taskId, onDone, loadingId) {
    const url = urls.taskStatus.replace("__TASK__", taskId);
    const poll = () => {
      api(url)
        .then((data) => {
          if (data.status === "SUCCESS") {
            if (loadingId) hideLoading(loadingId);
            onDone(data.result);
          } else if (data.status === "FAILURE") {
            if (loadingId) hideLoading(loadingId);
            alert("Erro no processamento. Tente novamente.");
          } else {
            setTimeout(poll, 3000);
          }
        })
        .catch(() => setTimeout(poll, 5000));
    };
    poll();
  }

  function renderMarkdown(container, md) {
    if (typeof marked !== "undefined" && md) {
      container.innerHTML = marked.parse(md);
    } else {
      container.textContent = md || "";
    }
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  // ── Passo 1: Upload do Consolidado (modal drag-and-drop) ──────────────

  // ── Passo 1: Upload — lógica do drop-zone está inline no template.
  //    O template chama window._wizDoUpload() quando o botão é clicado.
  //    O arquivo selecionado fica em window._wizSelectedFile.

  function doUpload() {
    var selectedFile = window._wizSelectedFile;
    console.log("[WIZARD] doUpload chamado, file=", selectedFile);
    if (!selectedFile) { console.error("[WIZARD] Sem arquivo!"); return; }

    const fd = new FormData();
    fd.append("consolidado", selectedFile);

    console.log("[WIZARD] Enviando POST para:", urls.documentosUpload);
    api(urls.documentosUpload, { method: "POST", body: fd, headers: { "X-CSRFToken": csrf } })
        .then((data) => {
          if (data.ok) {
            // Fechar modal e mostrar barra de progresso
            const modal = document.getElementById("modal-documentos");
            if (modal) modal.classList.add("hidden");
            showLoading("doc-loading");

            // Execução síncrona (sem Celery) — reload direto
            if (!data.task_id || data.task_id === "sync") {
              setTimeout(() => location.reload(), 500);
              return;
            }

            // Simular progresso enquanto extrai
            let pct = 5;
            const progressBar = document.getElementById("doc-progress-bar");
            const progressPct = document.getElementById("doc-progress-pct");
            const progressMsg = document.getElementById("doc-progress-msg");
            const messages = [
              [5,  "ENVIANDO DOCUMENTO..."],
              [20, "EXTRAINDO TEXTO DO PDF..."],
              [40, "IDENTIFICANDO DATAS..."],
              [60, "VERIFICANDO PRAZOS..."],
              [78, "ANALISANDO CAMPOS..."],
              [90, "FINALIZANDO EXTRACAO..."],
            ];
            let msgIdx = 0;
            // Inicializar barra imediatamente
            progressBar.style.width = "5%";
            progressPct.textContent = "5%";
            progressMsg.textContent = messages[0][1];
            msgIdx = 1;

            const progressInterval = setInterval(() => {
              if (pct < 95) {
                // Incremento maior no início, desacelera ao final
                var step = pct < 30 ? (Math.random() * 5 + 3) :
                           pct < 70 ? (Math.random() * 3 + 1.5) :
                                      (Math.random() * 1.5 + 0.3);
                pct += step;
                if (pct > 95) pct = 95;
                progressBar.style.width = Math.floor(pct) + "%";
                progressPct.textContent = Math.floor(pct) + "%";
                if (msgIdx < messages.length && pct >= messages[msgIdx][0]) {
                  progressMsg.textContent = messages[msgIdx][1];
                  msgIdx++;
                }
              }
            }, 600);

            pollTask(data.task_id, () => {
              clearInterval(progressInterval);
              progressBar.style.width = "100%";
              progressPct.textContent = "100%";
              progressMsg.textContent = "CONCLUIDO!";
              setTimeout(() => location.reload(), 500);
            });
          } else {
            alert(data.error || "Erro no upload.");
            location.reload();
          }
        })
        .catch(() => {
          alert("Erro de conexao.");
          location.reload();
        });
  }
  window._wizDoUpload = doUpload;

  // ── Passo 2: Dados Extraídos ──────────────────────────────────────────

  // ── Botão colar data (lê clipboard e seta no input type=date) ──────
  function initDatePaste() {
    document.querySelectorAll("#form-dados-extraidos .btn-colar-data").forEach(function(btn) {
      btn.addEventListener("click", function() {
        var inp = btn.closest(".relative").querySelector("input.date-paste");
        if (!inp) return;
        navigator.clipboard.readText().then(function(text) {
          text = text.trim();
          var iso = brDateToIso(text);
          if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
            inp.value = iso;
            btn.classList.add("ok");
            btn.querySelector("i").className = "ph ph-check-circle";
            setTimeout(function() {
              btn.classList.remove("ok");
              btn.querySelector("i").className = "ph ph-clipboard-text";
            }, 1500);
          } else {
            alert("Texto copiado não é uma data válida (DD/MM/AAAA):\n" + text);
          }
        }).catch(function() {
          alert("Não foi possível acessar a área de transferência.\nPermita o acesso ao clipboard.");
        });
      });
    });

    // Manter paste via Ctrl+V também
    document.querySelectorAll("#form-dados-extraidos input.date-paste").forEach(function(inp) {
      inp.addEventListener("paste", function(e) {
        var pasted = (e.clipboardData || window.clipboardData).getData("text").trim();
        var iso = brDateToIso(pasted);
        if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
          e.preventDefault();
          inp.value = iso;
        }
      });
    });
  }

  function brDateToIso(brDate) {
    // "24/06/2024" -> "2024-06-24"
    if (!brDate) return "";
    var parts = brDate.split("/");
    if (parts.length === 3 && parts[2].length === 4) return parts[2] + "-" + parts[1] + "-" + parts[0];
    // Tenta ISO passthrough (já vem como 2024-06-24)
    if (/^\d{4}-\d{2}-\d{2}$/.test(brDate)) return brDate;
    return brDate;
  }

  function loadDadosExtraidos() {
    api(urls.dadosExtraidos)
      .then((data) => {
        setExtractedField("recorrente", data.recorrente);
        setExtractedDateField("prazo-final", data.prazo_final);
        setExtractedDateField("data-protocolo", data.data_protocolo);
        setExtractedField("paginas-defesa", data.paginas_defesa);

        // Datas extraídas do PDF
        setExtractedDateField("data-infracao", data.data_infracao);
        setExtractedDateField("data-na", data.data_na);
        setExtractedDateField("data-np", data.data_np);
        setExtractedDateField("data-instauracao", data.data_instauracao);

        // Data sessão (se já existir)
        if (data.data_sessao) {
          var ds = document.querySelector('[name="data_sessao"]');
          if (ds) ds.value = brDateToIso(data.data_sessao);
        }

        initDatePaste();
      })
      .catch(() => {});
  }

  function setExtractedField(name, value) {
    var field = document.getElementById("field-" + name);
    if (field) field.value = value || "";
  }

  function setExtractedDateField(name, brDate) {
    var field = document.getElementById("field-" + name);
    if (field) field.value = brDateToIso(brDate);
  }

  var formDados = document.getElementById("form-dados-extraidos");
  if (formDados) {
    formDados.addEventListener("submit", function(e) {
      e.preventDefault();

      // Validar campo obrigatório: data_infracao
      var dataInfracaoField = document.getElementById("field-data-infracao");
      if (!dataInfracaoField.value) {
        dataInfracaoField.classList.add("border-red-400", "ring-2", "ring-red-200");
        dataInfracaoField.focus();
        var msg = dataInfracaoField.closest("div").parentElement.querySelector(".field-error-msg");
        if (!msg) {
          msg = document.createElement("p");
          msg.className = "field-error-msg text-xs text-red-500 mt-1 font-medium";
          msg.textContent = "Preencha a data da infracao consultando o PDF ao lado.";
          dataInfracaoField.closest("div").parentElement.appendChild(msg);
        }
        return;
      }
      dataInfracaoField.classList.remove("border-red-400", "ring-2", "ring-red-200");
      var errMsg = dataInfracaoField.closest("div").parentElement.querySelector(".field-error-msg");
      if (errMsg) errMsg.remove();

      var body = {
        data_sessao: formDados.querySelector('[name="data_sessao"]').value,
        recorrente: document.getElementById("field-recorrente").value,
        prazo_final: document.getElementById("field-prazo-final").value,
        data_protocolo: document.getElementById("field-data-protocolo").value,
        paginas_defesa: document.getElementById("field-paginas-defesa").value,
        data_infracao: dataInfracaoField.value,
        data_na: document.getElementById("field-data-na").value,
        data_np: document.getElementById("field-data-np").value,
        data_instauracao: document.getElementById("field-data-instauracao").value,
      };

      showLoading("dados-loading");
      if (typeof hidePdfSidebar === "function") hidePdfSidebar();

      api(urls.confirmarDados, { method: "POST", body: JSON.stringify(body) })
        .then(function(data) {
          if (data.ok && data.task_id && data.task_id !== "sync") {
            pollTask(data.task_id, function() { location.reload(); }, "dados-loading");
          } else if (data.ok) {
            setTimeout(function() { location.reload(); }, 500);
          } else {
            hideLoading("dados-loading");
            alert(data.error || "Erro ao confirmar dados.");
          }
        })
        .catch(function() {
          hideLoading("dados-loading");
          alert("Erro de conexao.");
        });
    });
  }

  // ── Passo 3: Admissibilidade ───────────────────────────────────────────

  var ADM_CFG = [
    { key: "tempestivo",    fundKey: "tempestivo",    apiFlag: "is_tempestivo",                       apiJulgador: "tempestivo",          bodyKey: "julgador_tempestivo",          nome: "Tempestividade",                  icon: "ph-timer",              color: "#2563eb" },
    { key: "punitiva",      fundKey: "punitiva",      apiFlag: "has_prescricao_punitiva",             apiJulgador: "punitiva",            bodyKey: "julgador_punitiva",            nome: "Prescricao Punitiva",             icon: "ph-scales",             color: "#ea580c" },
    { key: "intercorrente", fundKey: "intercorrente",  apiFlag: "has_prescricao_intercorrente",        apiJulgador: "intercorrente",       bodyKey: "julgador_intercorrente",       nome: "Prescricao Intercorrente Trienal", icon: "ph-arrows-clockwise",  color: "#ca8a04" },
    { key: "bienal",        fundKey: "bienal",         apiFlag: "has_prescricao_intercorrente_bienal", apiJulgador: "intercorrente_bienal", bodyKey: "julgador_intercorrente_bienal", nome: "Prescricao Intercorrente Bienal", icon: "ph-calendar-blank",   color: "#7c3aed" },
    { key: "decadencia",    fundKey: "decadencia",     apiFlag: "has_decadencia",                      apiJulgador: "decadencia",          bodyKey: "julgador_decadencia",          nome: "Decadencia",                      icon: "ph-prohibit",           color: "#dc2626" },
  ];

  var admDecisions = {};
  var admFundamentacoes = {};

  function loadAdmissibilidade() {
    var baseUrl = urls.admissibilidadeConfirmar.replace("/confirmar/", "/");
    api(baseUrl)
      .then(function(data) {
        if (data.tabela) renderMarkdown(document.getElementById("adm-tabela"), data.tabela);
        if (data.texto) renderMarkdown(document.getElementById("adm-resultado"), data.texto);
        admFundamentacoes = data.fundamentacoes || {};
        renderAdmCards(data.flags || {}, data.julgador || {});

        if (data.aguardando === false) {
          var erroDiv = document.getElementById("adm-erro");
          var btnConfirmar = document.getElementById("btn-confirmar-adm");
          if (erroDiv) {
            document.getElementById("adm-erro-msg").textContent =
              "O calculo de admissibilidade nao foi concluido. Verifique os dados extraidos ou tente recalcular.";
            erroDiv.classList.remove("hidden");
          }
          if (btnConfirmar) btnConfirmar.classList.add("hidden");
        }
      })
      .catch(function() {});
  }

  function _admResultText(cfg, autoVal) {
    if (cfg.key === "tempestivo") {
      if (autoVal === true) return "TEMPESTIVO";
      if (autoVal === false) return "INTEMPESTIVO";
      return "N/A";
    }
    if (autoVal === true) return "SIM";
    if (autoVal === false) return "NAO";
    return "N/A";
  }

  function _admPill(cfg, autoVal) {
    if (cfg.key === "tempestivo") {
      if (autoVal === true) return { cls: "adm-pill-nao", text: "OK" };
      if (autoVal === false) return { cls: "adm-pill-sim", text: "FORA DO PRAZO" };
    } else {
      if (autoVal === true) return { cls: "adm-pill-sim", text: "SIM" };
      if (autoVal === false) return { cls: "adm-pill-nao", text: "NAO" };
    }
    return { cls: "adm-pill-nsa", text: "N/A" };
  }

  function renderAdmCards(flags, julgador) {
    var container = document.getElementById("adm-cards");
    if (!container) return;
    var html = "";
    var cardIdx = 0;

    ADM_CFG.forEach(function(cfg) {
      var autoVal = flags[cfg.apiFlag];
      var jVal = julgador[cfg.apiJulgador];
      var pill = _admPill(cfg, autoVal);
      var autoText = _admResultText(cfg, autoVal);
      var fund = admFundamentacoes[cfg.fundKey] || "";

      // Modelo relativo: A = CONFIRMAR resultado, B = AFASTAR (inverter)
      // Pre-select: se julgador já decidiu
      var selectedA = false, selectedB = false;
      if (jVal === true) selectedA = true;
      else if (jVal === false) selectedB = true;

      // Mapear a decisão: confirmar = mesmo valor do auto, afastar = inverso
      if (selectedA) admDecisions[cfg.bodyKey] = "true";
      else if (selectedB) admDecisions[cfg.bodyKey] = "false";

      // Detectar se é inversão (julgador diverge do auto)
      var isInvertido = (jVal !== null && jVal !== undefined && jVal !== autoVal);

      html += '<div class="adm-card open' + (isInvertido ? ' adm-invertido' : '') + '" style="border-left-color:' + cfg.color + ';animation-delay:' + (cardIdx * 80) + 'ms;" data-key="' + cfg.key + '">';
      cardIdx++;
      html += '  <div class="adm-card-header" onclick="this.parentElement.classList.toggle(\'open\')">';
      html += '    <div class="adm-icon" style="background:' + cfg.color + '15;color:' + cfg.color + ';"><i class="ph ' + cfg.icon + '"></i></div>';
      html += '    <span class="adm-card-title">' + cfg.nome + '</span>';
      html += '    <span class="adm-pill ' + pill.cls + '">' + pill.text + '</span>';
      if (isInvertido) html += '    <span class="adm-pill-invertido">AFASTADO</span>';
      html += '    <i class="ph ph-caret-down adm-caret"></i>';
      html += '  </div>';
      html += '  <div class="adm-card-body">';

      // Fundamentação (cálculo do sistema)
      if (fund) {
        html += '    <div class="adm-fund"><i class="ph ph-calculator"></i> <strong>Calculo:</strong> ' + escapeHtml(fund) + '</div>';
      }

      // Auto result badge
      html += '    <div class="adm-auto-result"><i class="ph ph-robot"></i> Resultado tecnico automatico: <strong>' + autoText + '</strong></div>';

      // Radio cards: A = CONFIRMAR, B = AFASTAR
      html += '    <div class="radio-grid">';
      html += '      <div class="radio-card' + (selectedA ? ' selected' : '') + '" onclick="selectAdmCard(this,\'' + cfg.bodyKey + '\',\'true\',\'' + cfg.key + '\')">';
      html += '        <div class="radio-dot"></div>';
      html += '        <span class="radio-card-label"><i class="ph ph-check-circle"></i> Confirmar</span>';
      html += '        <span class="radio-card-sub">Manter resultado tecnico</span>';
      html += '      </div>';
      html += '      <div class="radio-card' + (selectedB ? ' selected' : '') + '" onclick="selectAdmCard(this,\'' + cfg.bodyKey + '\',\'false\',\'' + cfg.key + '\')">';
      html += '        <div class="radio-dot"></div>';
      html += '        <span class="radio-card-label"><i class="ph ph-arrows-counter-clockwise"></i> Afastar</span>';
      html += '        <span class="radio-card-sub">Inverter decisao do julgador</span>';
      html += '      </div>';
      html += '    </div>';

      // Aviso de inversão
      html += '    <div class="adm-aviso-inversao" id="aviso-' + cfg.key + '" style="display:none;">';
      html += '      <i class="ph ph-warning-circle"></i> Decisao invertida pelo julgador. O resultado escolhido sera usado no parecer.';
      html += '    </div>';

      html += '  </div>';
      html += '</div>';
    });

    container.innerHTML = html;

    // Mostrar avisos de inversão para seleções existentes
    ADM_CFG.forEach(function(cfg) {
      _updateInversionWarning(cfg.key);
    });
  }

  window.selectAdmCard = function(el, key, value, cfgKey) {
    var parent = el.closest(".radio-grid");
    parent.querySelectorAll(".radio-card").forEach(function(c) { c.classList.remove("selected"); });
    el.classList.add("selected");
    admDecisions[key] = value;
    if (cfgKey) _updateInversionWarning(cfgKey);
  };

  function _updateInversionWarning(cfgKey) {
    var cfg = ADM_CFG.filter(function(c) { return c.key === cfgKey; })[0];
    if (!cfg) return;
    var card = document.querySelector('.adm-card[data-key="' + cfgKey + '"]');
    if (!card) return;
    var aviso = document.getElementById("aviso-" + cfgKey);
    var pillInv = card.querySelector(".adm-pill-invertido");
    var selected = admDecisions[cfg.bodyKey];

    // "false" = Afastar = inversão
    var isAfastado = (selected === "false");

    if (aviso) aviso.style.display = isAfastado ? "flex" : "none";
    if (isAfastado) {
      card.classList.add("adm-invertido");
      if (!pillInv) {
        var pill = document.createElement("span");
        pill.className = "adm-pill-invertido";
        pill.textContent = "AFASTADO";
        var caret = card.querySelector(".adm-caret");
        if (caret) caret.parentNode.insertBefore(pill, caret);
      }
    } else {
      card.classList.remove("adm-invertido");
      if (pillInv) pillInv.remove();
    }
  }

  var btnVoltarDados = document.getElementById("btn-voltar-dados");
  if (btnVoltarDados) {
    btnVoltarDados.addEventListener("click", function() {
      api(urls.avancar, { method: "POST", body: JSON.stringify({ proxima_fase: "documentos_extraidos" }) })
        .then(function(data) {
          if (data.error) { alert(data.error); return; }
          location.reload();
        })
        .catch(function() { alert("Erro de conexao."); });
    });
  }

  var btnRecalcAdm = document.getElementById("btn-recalcular-adm");
  if (btnRecalcAdm) {
    btnRecalcAdm.addEventListener("click", function() {
      showLoading("adm-loading");
      api(urls.admissibilidadeRecalcular, { method: "POST" })
        .then(function(data) {
          if (data.ok) {
            if (!data.task_id || data.task_id === "sync") {
              setTimeout(function() { location.reload(); }, 500);
            } else {
              pollTask(data.task_id, function() { location.reload(); }, "adm-loading");
            }
          } else {
            hideLoading("adm-loading");
            alert(data.error || "Erro ao recalcular admissibilidade.");
          }
        })
        .catch(function() {
          hideLoading("adm-loading");
          alert("Erro de conexao.");
        });
    });
  }

  var btnAdm = document.getElementById("btn-confirmar-adm");
  if (btnAdm) {
    btnAdm.addEventListener("click", function() {
      var body = {};
      ADM_CFG.forEach(function(cfg) {
        body[cfg.bodyKey] = admDecisions[cfg.bodyKey] || "";
      });

      showLoading("adm-loading");

      api(urls.admissibilidadeConfirmar, { method: "POST", body: JSON.stringify(body) })
        .then(function(data) {
          if (data.ok) {
            // Sync fallback — reload direto
            if (!data.task_id || data.task_id === "sync") {
              setTimeout(function() { location.reload(); }, 500);
              return;
            }

            if (data.skip_teses) {
              document.querySelector("#adm-loading .text-gray-600").textContent = "Gerando parecer (merito prejudicado)...";
              pollTask(data.task_id, function() { location.reload(); }, "adm-loading");
            } else {
              document.querySelector("#adm-loading .text-gray-600").textContent = "Extraindo teses...";
              pollTask(data.task_id, function(result) {
                if (result && result.chained) { location.reload(); return; }
                location.reload();
              }, "adm-loading");
            }
          } else {
            hideLoading("adm-loading");
            alert(data.error || "Erro ao confirmar admissibilidade.");
          }
        })
        .catch(function() {
          hideLoading("adm-loading");
          alert("Erro de conexao.");
        });
    });
  }

  function pollFaseChange(expectedPasso, onReady) {
    const check = () => {
      api(urls.fase)
        .then((data) => {
          if (data.passo > expectedPasso || data.fase.includes("aguardando")) {
            onReady(data);
          } else {
            setTimeout(check, 3000);
          }
        })
        .catch(() => setTimeout(check, 5000));
    };
    setTimeout(check, 3000);
  }

  // ── Passo 4: Teses ─────────────────────────────────────────────────────

  var teseDecisions = {};

  function loadTeses() {
    var tesesUrl = urls.tesesConfirmar.replace("/confirmar/", "/");
    api(tesesUrl)
      .then(function(data) {
        var container = document.getElementById("teses-cards");
        var prejudicado = document.getElementById("teses-prejudicado");

        if (!data.teses || data.teses.length === 0) {
          if (prejudicado) prejudicado.classList.remove("hidden");
          if (container) container.innerHTML = "";
          return;
        }

        var html = "";
        data.teses.forEach(function(t) {
          var selA = t.acolhida === true;
          var selB = t.acolhida === false;
          if (selA) teseDecisions[t.id] = "true";
          else if (selB) teseDecisions[t.id] = "false";

          html += '<div class="adm-card" style="border-left-color:#3b82f6;">';
          html += '  <div class="adm-card-header" onclick="this.parentElement.classList.toggle(\'open\')">';
          html += '    <div class="adm-icon" style="background:#eff6ff;color:#3b82f6;"><span style="font-weight:700;font-size:14px;">' + t.ordem + '</span></div>';
          html += '    <span class="adm-card-title">' + escapeHtml(t.titulo) + '</span>';
          html += '    <i class="ph ph-caret-down adm-caret"></i>';
          html += '  </div>';
          html += '  <div class="adm-card-body">';

          // Fundamentação (markdown)
          if (t.fundamentacao) {
            html += '    <div class="prose-jari text-sm bg-gray-50 rounded-lg p-3 mb-3" style="max-height:300px;overflow-y:auto;">' + marked.parse(t.fundamentacao) + '</div>';
          }

          // Radio A/B
          html += '    <div class="radio-grid">';
          html += '      <div class="radio-card' + (selA ? ' selected' : '') + '" onclick="selectTeseCard(this,' + t.id + ',\'true\')">';
          html += '        <div class="radio-dot"></div>';
          html += '        <span class="radio-card-label">Acolhida</span>';
          html += '        <span class="radio-card-sub">Opcao A — Deferida</span>';
          html += '      </div>';
          html += '      <div class="radio-card' + (selB ? ' selected' : '') + '" onclick="selectTeseCard(this,' + t.id + ',\'false\')">';
          html += '        <div class="radio-dot"></div>';
          html += '        <span class="radio-card-label">Nao acolhida</span>';
          html += '        <span class="radio-card-sub">Opcao B — Indeferida</span>';
          html += '      </div>';
          html += '    </div>';

          html += '  </div>';
          html += '</div>';
        });

        container.innerHTML = html;
      })
      .catch(function() {});
  }

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  window.selectTeseCard = function(el, teseId, value) {
    var parent = el.closest(".radio-grid");
    parent.querySelectorAll(".radio-card").forEach(function(c) { c.classList.remove("selected"); });
    el.classList.add("selected");
    teseDecisions[teseId] = value;
  };

  var btnTeses = document.getElementById("btn-confirmar-teses");
  if (btnTeses) {
    btnTeses.addEventListener("click", function() {
      showLoading("tese-loading");
      document.getElementById("tese-loading-msg").textContent = "Gerando parecer...";

      api(urls.tesesConfirmar, { method: "POST", body: JSON.stringify({ decisoes: teseDecisions }) })
        .then(function(data) {
          if (data.ok) {
            if (!data.task_id || data.task_id === "sync") {
              setTimeout(function() { location.reload(); }, 500);
              return;
            }
            pollTask(data.task_id, function() { location.reload(); }, "tese-loading");
          } else {
            hideLoading("tese-loading");
            alert(data.error || "Erro ao confirmar teses.");
          }
        })
        .catch(function() {
          hideLoading("tese-loading");
          alert("Erro de conexao.");
        });
    });
  }

  // ── Passo 5: Parecer ───────────────────────────────────────────────────

  var parecerTextoOriginal = "";

  // ── Copiar por seção (portado do projeto antigo) ──────────────────────
  var COPY_SECTIONS = [
    { label: "Ementa",          from: "EMENTA",                 to: "RELATÓRIO",               skipTitle: true },
    { label: "Relatório",       from: "RELATÓRIO",              to: ["FUNDAMENTAÇÃO JURÍDICA", "ADMISSIBILIDADE"], skipTitle: true },
    { label: "Fundamentação",   from: "ADMISSIBILIDADE", to: ["PARECER FINAL", "VOTO"], trimClosing: true },
    { label: "Voto",            from: ["PARECER FINAL", "VOTO"], to: null },
  ];

  function _findSectionNode(container, keys) {
    var arr = Array.isArray(keys) ? keys : [keys];
    var strongs = container.querySelectorAll("strong");
    for (var i = 0; i < strongs.length; i++) {
      var norm = strongs[i].textContent.trim().replace(/:$/, "").toUpperCase();
      for (var j = 0; j < arr.length; j++) {
        var k = arr[j].toUpperCase();
        if (norm === k || norm.indexOf(k) === 0 || k.indexOf(norm) === 0) return strongs[i];
      }
    }
    return null;
  }

  function _getSectionText(container, fromKeys, toKeys, skipTitle, trimClosing) {
    var startEl = _findSectionNode(container, fromKeys);
    if (!startEl) return null;
    var endEl = toKeys ? _findSectionNode(container, toKeys) : null;
    var block = startEl.closest("p") || startEl.parentElement;
    var endBlock = endEl ? (endEl.closest("p") || endEl.parentElement) : null;
    var parts = [];
    var node = skipTitle ? block.nextElementSibling : block;
    var closingRe = /^(este é o relatório|essa é a fundamentação|esta é a fundamentação)\.?$/i;
    while (node) {
      if (endBlock && node === endBlock) break;
      if (node.textContent) {
        var line = node.textContent.trim();
        if (trimClosing && closingRe.test(line)) { node = node.nextElementSibling; continue; }
        parts.push(line);
      }
      node = node.nextElementSibling;
    }
    return parts.join("\n").trim();
  }

  function _copyBtnFeedback(btn, label, ok) {
    if (ok) {
      btn.innerHTML = '<i class="ph ph-check" style="font-size:13px;color:#16a34a;"></i> Copiado!';
      btn.className = btn.className.replace("border-gray-200 bg-white text-gray-600", "border-green-300 bg-green-50 text-green-700");
    } else {
      btn.innerHTML = '<i class="ph ph-x" style="font-size:13px;"></i> Não encontrada';
    }
    setTimeout(function() {
      btn.innerHTML = '<i class="ph ph-copy" style="font-size:13px;"></i> ' + label;
      btn.className = btn.className.replace("border-green-300 bg-green-50 text-green-700", "border-gray-200 bg-white text-gray-600");
    }, 1500);
  }

  function injectCopyButtons(containerId) {
    var container = document.getElementById(containerId);
    if (!container) return;

    // Remover barra existente se houver
    var existing = container.parentNode.querySelector(".copy-bar");
    if (existing) existing.remove();

    var bar = document.createElement("div");
    bar.className = "copy-bar";
    bar.style.cssText = "display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap;";

    // Botões por seção
    COPY_SECTIONS.forEach(function(cfg) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "px-3 py-1.5 text-xs font-medium rounded-lg border border-gray-200 bg-white text-gray-600 hover:bg-blue-50 hover:border-blue-300 hover:text-blue-700 transition flex items-center gap-1";
      btn.innerHTML = '<i class="ph ph-copy" style="font-size:13px;"></i> ' + cfg.label;
      btn.addEventListener("click", function() {
        var text = _getSectionText(container, cfg.from, cfg.to, cfg.skipTitle, cfg.trimClosing);
        if (!text) { _copyBtnFeedback(btn, cfg.label, false); return; }
        navigator.clipboard.writeText(text).then(function() { _copyBtnFeedback(btn, cfg.label, true); });
      });
      bar.appendChild(btn);
    });

    // Botão "Copiar Tudo"
    var btnAll = document.createElement("button");
    btnAll.type = "button";
    btnAll.className = "px-3 py-1.5 text-xs font-medium rounded-lg border border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100 hover:border-blue-400 transition flex items-center gap-1 ml-auto";
    btnAll.innerHTML = '<i class="ph ph-copy-simple" style="font-size:13px;"></i> Copiar Tudo';
    btnAll.addEventListener("click", function() {
      var text = container.innerText || container.textContent || "";
      navigator.clipboard.writeText(text.trim()).then(function() {
        btnAll.innerHTML = '<i class="ph ph-check" style="font-size:13px;color:#16a34a;"></i> Copiado!';
        setTimeout(function() {
          btnAll.innerHTML = '<i class="ph ph-copy-simple" style="font-size:13px;"></i> Copiar Tudo';
        }, 1500);
      });
    });
    bar.appendChild(btnAll);

    container.parentNode.insertBefore(bar, container);
  }

  function loadParecer() {
    api(urls.parecerDados)
      .then(function(data) {
        // Badge
        var badgeEl = document.getElementById("parecer-badge");
        if (badgeEl && data.resultado) {
          if (data.resultado === "DEFERIDO") {
            badgeEl.innerHTML = '<span class="inline-flex items-center gap-2 bg-green-100 text-green-700 px-5 py-2 rounded-full text-base font-bold"><i class="ph ph-check-circle"></i> DEFERIDO</span>';
          } else {
            badgeEl.innerHTML = '<span class="inline-flex items-center gap-2 bg-red-100 text-red-700 px-5 py-2 rounded-full text-base font-bold"><i class="ph ph-x-circle"></i> INDEFERIDO</span>';
          }
        }

        // Texto
        if (data.texto) {
          parecerTextoOriginal = data.texto;
          renderMarkdown(document.getElementById("parecer-content"), data.texto);
          document.getElementById("parecer-editor").value = data.texto;
          injectCopyButtons("parecer-content");
        }

        // Provider
        var providerEl = document.getElementById("parecer-provider");
        if (providerEl && data.provider) {
          providerEl.textContent = "Gerado por: " + data.provider;
        }

        // Dossiê/fontes
        if (data.blindagem_detalhes) {
          var dossieWrap = document.getElementById("parecer-dossie-wrap");
          if (dossieWrap) {
            dossieWrap.style.display = "";
            renderMarkdown(document.getElementById("parecer-dossie"), data.blindagem_detalhes);
          }
        }
      })
      .catch(function() {});
  }

  // Toggle editor
  var btnToggleEditor = document.getElementById("btn-toggle-editor");
  if (btnToggleEditor) {
    btnToggleEditor.addEventListener("click", function() {
      var viewEl = document.getElementById("parecer-view");
      var editorWrap = document.getElementById("parecer-editor-wrap");
      var isEditing = !editorWrap.classList.contains("hidden");

      if (isEditing) {
        // Switch back to view mode — re-render markdown
        editorWrap.classList.add("hidden");
        viewEl.classList.remove("hidden");
        var editedText = document.getElementById("parecer-editor").value;
        renderMarkdown(document.getElementById("parecer-content"), editedText);
        btnToggleEditor.innerHTML = '<i class="ph ph-pencil-simple"></i> Editar parecer';
      } else {
        // Switch to edit mode
        viewEl.classList.add("hidden");
        editorWrap.classList.remove("hidden");
        btnToggleEditor.innerHTML = '<i class="ph ph-eye"></i> Visualizar';
      }
    });
  }

  var btnFinalizar = document.getElementById("btn-finalizar-parecer");
  if (btnFinalizar) {
    btnFinalizar.addEventListener("click", function() {
      showLoading("audit-loading");

      // Se o editor está visível, salvar edições antes de auditar
      var editorWrap = document.getElementById("parecer-editor-wrap");
      var editorText = document.getElementById("parecer-editor").value;
      var hasEdits = !editorWrap.classList.contains("hidden") || (editorText && editorText !== parecerTextoOriginal);

      function dispararAuditoria() {
        api(urls.auditoriaFinalizar, { method: "POST", body: JSON.stringify({}) })
          .then(function(data) {
            if (data.ok) {
              if (!data.task_id || data.task_id === "sync") {
                setTimeout(function() { location.reload(); }, 500);
                return;
              }
              pollTask(data.task_id, function() { location.reload(); }, "audit-loading");
            } else {
              hideLoading("audit-loading");
              alert(data.error || "Erro ao iniciar auditoria.");
            }
          })
          .catch(function() {
            hideLoading("audit-loading");
            alert("Erro de conexao.");
          });
      }

      if (hasEdits && editorText && urls.parecerEditar) {
        api(urls.parecerEditar, { method: "POST", body: JSON.stringify({ texto_editado: editorText }) })
          .then(function(saveData) {
            if (saveData.ok || saveData.conteudo_final) {
              dispararAuditoria();
            } else {
              hideLoading("audit-loading");
              alert(saveData.error || "Erro ao salvar edicoes do parecer.");
            }
          })
          .catch(function() {
            hideLoading("audit-loading");
            alert("Erro ao salvar edicoes.");
          });
      } else {
        dispararAuditoria();
      }
    });
  }

  // ── Passo 6: Editor final (editar parecer após auditoria) ──────────────
  var btnToggleFinal = document.getElementById("btn-toggle-editor-final");
  if (btnToggleFinal) {
    btnToggleFinal.addEventListener("click", function() {
      var viewEl = document.getElementById("parecer-final-view");
      var editorWrap = document.getElementById("parecer-editor-final-wrap");
      viewEl.classList.add("hidden");
      editorWrap.classList.remove("hidden");
      btnToggleFinal.classList.add("hidden");
    });
  }
  var btnCancelarFinal = document.getElementById("btn-cancelar-editor-final");
  if (btnCancelarFinal) {
    btnCancelarFinal.addEventListener("click", function() {
      document.getElementById("parecer-final-view").classList.remove("hidden");
      document.getElementById("parecer-editor-final-wrap").classList.add("hidden");
      document.getElementById("btn-toggle-editor-final").classList.remove("hidden");
      document.getElementById("parecer-editor-final").value = parecerTextoOriginal;
    });
  }
  var btnSalvarFinal = document.getElementById("btn-salvar-editor-final");
  if (btnSalvarFinal) {
    btnSalvarFinal.addEventListener("click", function() {
      var texto = document.getElementById("parecer-editor-final").value;
      api(urls.parecerEditar, { method: "POST", body: JSON.stringify({ texto_editado: texto }) })
        .then(function(data) {
          if (data.ok) {
            parecerTextoOriginal = texto;
            renderMarkdown(document.getElementById("parecer-final"), texto);
            injectCopyButtons("parecer-final");
            document.getElementById("parecer-final-view").classList.remove("hidden");
            document.getElementById("parecer-editor-final-wrap").classList.add("hidden");
            document.getElementById("btn-toggle-editor-final").classList.remove("hidden");
          } else {
            alert(data.error || "Erro ao salvar.");
          }
        })
        .catch(function() { alert("Erro de conexao."); });
    });
  }

  // ── Passo 6: Finalizado ────────────────────────────────────────────────

  function loadFinalizado() {
    api(urls.parecerDados)
      .then(function(data) {
        // Parecer final
        if (data.texto) {
          parecerTextoOriginal = data.texto;
          renderMarkdown(document.getElementById("parecer-final"), data.texto);
          injectCopyButtons("parecer-final");
          var editorFinal = document.getElementById("parecer-editor-final");
          if (editorFinal) editorFinal.value = data.texto;
        }

        // Tempo de julgamento
        var tempoEl = document.getElementById("tempo-julgamento");
        if (tempoEl && data.tempo_julgamento) {
          tempoEl.textContent = data.tempo_julgamento;
        }

        // Selo de blindagem
        if (data.blindagem_score !== null && data.blindagem_score !== undefined) {
          var score = data.blindagem_score;
          var selo = document.getElementById("blindagem-selo");
          var icon = document.getElementById("blindagem-icon");
          var label = document.getElementById("blindagem-label");

          if (selo) {
            selo.style.display = "";
            var colorClass, iconClass, statusText, pulseClass;
            if (score >= 80) {
              colorClass = "bg-green-50 border-green-200";
              iconClass = "ph ph-shield-check text-green-600";
              statusText = score + "/100 \u00b7 Aprovado";
              pulseClass = "jari-pulse-green";
            } else if (score >= 50) {
              colorClass = "bg-yellow-50 border-yellow-200";
              iconClass = "ph ph-shield-warning text-yellow-600";
              statusText = score + "/100 \u00b7 Aten\u00e7\u00e3o";
              pulseClass = "jari-pulse-yellow";
            } else {
              colorClass = "bg-red-50 border-red-200";
              iconClass = "ph ph-x-circle text-red-600";
              statusText = score + "/100 \u00b7 Reprovado";
              pulseClass = "jari-pulse-red";
            }
            selo.className = "flex-1 flex items-center gap-3 rounded-xl px-5 py-3 border cursor-pointer select-none transition-all " + colorClass + " " + pulseClass;
            icon.className = iconClass;
            icon.style.fontSize = "26px";
            label.textContent = statusText;
          }
        }

        // Info do processo no checklist
        var infoEl = document.getElementById("audit-processo-info");
        if (infoEl) {
          var parts = [];
          if (data.pa) parts.push("<strong>Processo:</strong> " + escapeHtml(data.pa));
          if (data.recorrente) parts.push("<strong>Recorrente:</strong> " + escapeHtml(data.recorrente));
          if (data.relator) parts.push("<strong>Relator:</strong> " + escapeHtml(data.relator));
          infoEl.innerHTML = parts.join(" &nbsp;|&nbsp; ");
        }

        // Checklist de auditoria (itens detalhados)
        var checklist = data.checklist;
        if (checklist && Array.isArray(checklist) && checklist.length > 0) {
          var container = document.getElementById("audit-checklist");
          if (container) {
            var html = "";
            var itensOk = 0;
            checklist.forEach(function(item, idx) {
              var ok = item.ok || item.passed || item.status === true;
              if (ok) itensOk++;
              var dotClass = ok ? "bg-green-500" : "bg-red-500";
              var statusLabel = ok ? "Conforme" : "N\u00e3o conforme";
              var statusColor = ok ? "text-green-700" : "text-red-700";
              var itemLabel = item.label || item.descricao || item.name || "";
              var detalhe = item.detalhe || "";

              html += '<div>';
              html += '<p class="text-sm text-gray-800 leading-relaxed">';
              html += '<strong class="text-gray-900">' + (idx + 1) + '. ' + escapeHtml(itemLabel) + ':</strong> ';
              html += '<span class="inline-flex items-center gap-1 mx-1">';
              html += '<span class="w-2.5 h-2.5 rounded-full inline-block ' + dotClass + '"></span>';
              html += '<span class="font-medium ' + statusColor + '">' + statusLabel + '</span>';
              html += '</span>';
              if (detalhe) html += ' \u2014 ' + escapeHtml(detalhe);
              html += '</p>';
              html += '</div>';
            });
            container.innerHTML = html;

            // Resultado final
            var resultDiv = document.getElementById("audit-resultado-final");
            if (resultDiv) {
              resultDiv.classList.remove("hidden");
              var total = checklist.length;
              if (itensOk === total) {
                resultDiv.className = "mt-8 rounded-xl bg-green-50 border border-green-200 px-5 py-4";
                resultDiv.innerHTML = '<p class="text-sm font-bold text-green-800 italic">' +
                  '"\u2705 RESULTADO FINAL DA AUDITORIA: APROVADO SEM RESSALVAS \u2014 ' + total + '/' + total + ' ITENS CONFORMES"</p>';
              } else {
                resultDiv.className = "mt-8 rounded-xl bg-red-50 border border-red-200 px-5 py-4";
                resultDiv.innerHTML = '<p class="text-sm font-bold text-red-800 italic">' +
                  '"\u26a0\ufe0f RESULTADO FINAL DA AUDITORIA: ' + itensOk + '/' + total + ' ITENS CONFORMES \u2014 REQUER REVIS\u00c3O"</p>';
              }
            }
          }
        }
      })
      .catch(function() {});
  }

  // ── Init: carregar dados do passo atual ─────────────────────────────────

  if (currentPasso === 2) loadDadosExtraidos();
  if (currentPasso === 3) loadAdmissibilidade();
  if (currentPasso === 4) loadTeses();
  if (currentPasso === 5) loadParecer();
  if (currentPasso === 6) loadFinalizado();

  // ── Salvar em Pasta (passo 6) ────────────────────────────────────────────
  var btnSalvarPasta = document.getElementById("btn-salvar-pasta");
  var dropdownMeses = document.getElementById("dropdown-meses");

  if (btnSalvarPasta && dropdownMeses) {
    btnSalvarPasta.addEventListener("click", function(e) {
      e.stopPropagation();
      dropdownMeses.classList.toggle("hidden");
    });

    document.addEventListener("click", function(e) {
      if (!dropdownMeses.contains(e.target) && e.target !== btnSalvarPasta) {
        dropdownMeses.classList.add("hidden");
      }
    });

    dropdownMeses.querySelectorAll(".mes-option").forEach(function(btn) {
      btn.addEventListener("click", function() {
        var pastaNome = this.getAttribute("data-pasta-nome");
        dropdownMeses.classList.add("hidden");

        // Buscar pasta ID pelo nome e mover processo
        fetch("/api/pastas/")
          .then(function(r) { return r.json(); })
          .then(function(data) {
            var pasta = data.pastas.find(function(p) { return p.nome === pastaNome; });
            if (!pasta) {
              alert("Pasta não encontrada. Recarregue a página.");
              return;
            }
            return fetch("/api/processo/" + processoId + "/mover/", {
              method: "POST",
              headers: {"Content-Type": "application/json", "X-CSRFToken": csrf},
              body: JSON.stringify({pasta_id: pasta.id})
            });
          })
          .then(function(r) { if (r) return r.json(); })
          .then(function(data) {
            if (data && data.ok) {
              btnSalvarPasta.innerHTML = '<i class="ph ph-check text-lg"></i> Salvo em ' + pastaNome.split(" - ")[1];
              btnSalvarPasta.classList.remove("bg-amber-600", "hover:bg-amber-700");
              btnSalvarPasta.classList.add("bg-green-600", "hover:bg-green-700");
              setTimeout(function() {
                btnSalvarPasta.innerHTML = '<i class="ph ph-folder-plus text-lg"></i> Salvar';
                btnSalvarPasta.classList.remove("bg-green-600", "hover:bg-green-700");
                btnSalvarPasta.classList.add("bg-amber-600", "hover:bg-amber-700");
              }, 3000);
            }
          });
      });
    });
  }

  // Se está em estado de "extraindo" ou "gerando", fazer polling
  if (currentFase === "documentos_extraindo") {
    var modal = document.getElementById("modal-documentos");
    if (modal) modal.classList.add("hidden");
    showLoading("doc-loading");
    pollFaseChange(1, function() { location.reload(); });
  }
  if (currentFase === "parecer_gerando") {
    showStep(5);
    showLoading("parecer-loading");
    pollFaseChange(5, function() { location.reload(); });
  }

})();
