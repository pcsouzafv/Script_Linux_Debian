import json


def build_runtime_dashboard_html(*, api_prefix: str) -> str:
    overview_path = f"{api_prefix}/helpdesk/runtime/overview"
    overview_path_json = json.dumps(overview_path)
    template = """<!doctype html>
<html lang="pt-BR">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Runtime Dashboard</title>
    <style>
      :root {
        color-scheme: light;
        --bg: #f3efe6;
        --panel: rgba(255, 252, 246, 0.92);
        --panel-strong: #fffdf8;
        --line: rgba(43, 52, 69, 0.12);
        --ink: #1f2933;
        --muted: #5b6574;
        --accent: #a33b20;
        --accent-soft: rgba(163, 59, 32, 0.12);
        --good: #1f7a4d;
        --warn: #9a5a00;
        --bad: #a12622;
        --shadow: 0 18px 44px rgba(39, 36, 31, 0.12);
        --radius: 18px;
      }

      * { box-sizing: border-box; }

      body {
        margin: 0;
        min-height: 100vh;
        font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
        color: var(--ink);
        background:
          radial-gradient(circle at top left, rgba(198, 127, 55, 0.20), transparent 28%),
          radial-gradient(circle at top right, rgba(25, 117, 113, 0.16), transparent 22%),
          linear-gradient(180deg, #f8f4eb 0%, var(--bg) 100%);
      }

      .shell {
        width: min(1440px, calc(100vw - 32px));
        margin: 0 auto;
        padding: 28px 0 40px;
      }

      .hero {
        display: grid;
        gap: 18px;
        margin-bottom: 24px;
      }

      .hero-card,
      .panel {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: var(--radius);
        box-shadow: var(--shadow);
        backdrop-filter: blur(14px);
      }

      .hero-card {
        padding: 24px;
        display: grid;
        gap: 14px;
      }

      .eyebrow {
        font-size: 12px;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: var(--accent);
        font-weight: 700;
      }

      h1 {
        margin: 0;
        font-family: "Space Grotesk", "Segoe UI", sans-serif;
        font-size: clamp(32px, 5vw, 54px);
        line-height: 0.94;
        letter-spacing: -0.04em;
      }

      .hero p {
        margin: 0;
        color: var(--muted);
        max-width: 880px;
        font-size: 16px;
        line-height: 1.55;
      }

      .controls {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 14px;
        align-items: end;
      }

      .field {
        display: grid;
        gap: 8px;
      }

      .field label {
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--muted);
        font-weight: 700;
      }

      .field input {
        width: 100%;
        border: 1px solid rgba(31, 41, 51, 0.16);
        border-radius: 12px;
        background: var(--panel-strong);
        padding: 14px 16px;
        color: var(--ink);
        font-size: 15px;
      }

      .actions {
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
      }

      button {
        appearance: none;
        border: none;
        border-radius: 999px;
        padding: 14px 18px;
        font: inherit;
        font-weight: 700;
        cursor: pointer;
        transition: transform 180ms ease, opacity 180ms ease;
      }

      button:hover { transform: translateY(-1px); }
      button:active { transform: translateY(0); }

      .primary {
        background: linear-gradient(135deg, #c7502e 0%, #972c16 100%);
        color: white;
      }

      .ghost {
        background: var(--accent-soft);
        color: var(--accent);
      }

      .meta {
        display: flex;
        flex-wrap: wrap;
        gap: 12px 18px;
        color: var(--muted);
        font-size: 14px;
      }

      .grid {
        display: grid;
        grid-template-columns: repeat(12, minmax(0, 1fr));
        gap: 16px;
      }

      .panel {
        padding: 18px;
        display: grid;
        gap: 14px;
        overflow: hidden;
      }

      .span-3 { grid-column: span 3; }
      .span-4 { grid-column: span 4; }
      .span-5 { grid-column: span 5; }
      .span-6 { grid-column: span 6; }
      .span-7 { grid-column: span 7; }
      .span-8 { grid-column: span 8; }
      .span-12 { grid-column: span 12; }

      .panel h2,
      .panel h3 {
        margin: 0;
        font-family: "Space Grotesk", "Segoe UI", sans-serif;
        letter-spacing: -0.02em;
      }

      .panel h2 { font-size: 22px; }
      .panel h3 { font-size: 16px; color: var(--muted); }

      .kpi-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
      }

      .kpi {
        padding: 14px;
        border-radius: 14px;
        background: rgba(255, 255, 255, 0.72);
        border: 1px solid var(--line);
      }

      .kpi strong {
        display: block;
        font-size: 28px;
        line-height: 1;
        margin-bottom: 6px;
      }

      .kpi span { color: var(--muted); font-size: 13px; }

      .status-pill {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        width: fit-content;
        padding: 8px 12px;
        border-radius: 999px;
        font-size: 13px;
        font-weight: 700;
        background: rgba(31, 122, 77, 0.10);
        color: var(--good);
      }

      .status-pill.warn { background: rgba(154, 90, 0, 0.10); color: var(--warn); }
      .status-pill.bad { background: rgba(161, 38, 34, 0.10); color: var(--bad); }

      .list {
        display: grid;
        gap: 10px;
        margin: 0;
        padding: 0;
        list-style: none;
      }

      .list li {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        padding-bottom: 10px;
        border-bottom: 1px solid var(--line);
      }

      .list li:last-child { border-bottom: none; padding-bottom: 0; }
      .list small { color: var(--muted); }

      table {
        width: 100%;
        border-collapse: collapse;
        font-size: 14px;
      }

      th,
      td {
        text-align: left;
        padding: 10px 12px;
        border-bottom: 1px solid var(--line);
        vertical-align: top;
      }

      th {
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--muted);
      }

      tr:last-child td { border-bottom: none; }

      .mono { font-family: "IBM Plex Mono", "Fira Code", monospace; font-size: 13px; }
      .muted { color: var(--muted); }
      .error { color: var(--bad); font-weight: 700; }
      .empty { color: var(--muted); font-style: italic; }

      @media (max-width: 1080px) {
        .controls { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .span-3, .span-4, .span-5, .span-6, .span-7, .span-8 { grid-column: span 12; }
      }

      @media (max-width: 720px) {
        .shell { width: min(100vw - 20px, 1440px); padding-top: 18px; }
        .controls { grid-template-columns: 1fr; }
        h1 { font-size: 34px; }
        .kpi-grid { grid-template-columns: 1fr; }
      }
    </style>
  </head>
  <body>
    <div class="shell">
      <section class="hero">
        <div class="hero-card">
          <div class="eyebrow">backend runtime</div>
          <h1>Painel operacional do orquestrador</h1>
          <p>
            Use esta tela para acompanhar o que esta vivo no backend agora: integracoes, sessoes do WhatsApp,
            trilha de auditoria, fila de automacao e resumo operacional dos tickets.
          </p>
          <div class="controls">
            <div class="field">
              <label for="audit-token">Audit token</label>
              <input id="audit-token" type="password" placeholder="X-Helpdesk-Audit-Key" autocomplete="off" />
            </div>
            <div class="field">
              <label for="automation-token">Automation read token</label>
              <input id="automation-token" type="password" placeholder="X-Helpdesk-Automation-Read-Key" autocomplete="off" />
            </div>
            <div class="field">
              <label for="refresh-ms">Refresh (ms)</label>
              <input id="refresh-ms" type="number" min="3000" step="1000" value="10000" />
            </div>
            <div class="actions">
              <button id="load-button" class="primary" type="button">Atualizar painel</button>
              <button id="toggle-button" class="ghost" type="button">Auto refresh: off</button>
            </div>
          </div>
          <div class="meta">
            <span>Endpoint: <span class="mono" id="endpoint-label"></span></span>
            <span id="last-updated">Sem coleta ainda.</span>
            <span id="request-status">Aguardando leitura do runtime.</span>
          </div>
        </div>
      </section>

      <section class="grid">
        <article class="panel span-4">
          <h2>Visao geral</h2>
          <div id="health-pill" class="status-pill warn">Sem dados</div>
          <ul class="list" id="health-list"></ul>
        </article>

        <article class="panel span-4">
          <h2>Integracoes</h2>
          <ul class="list" id="integration-list"></ul>
        </article>

        <article class="panel span-4">
          <h2>Mensageria</h2>
          <div id="messaging-pill" class="status-pill warn">Sem dados</div>
          <ul class="list" id="messaging-list"></ul>
        </article>

        <article class="panel span-6">
          <h2>Tickets</h2>
          <div class="kpi-grid" id="ticket-kpis"></div>
          <ul class="list" id="ticket-distribution"></ul>
        </article>

        <article class="panel span-6">
          <h2>Automacao</h2>
          <div class="kpi-grid" id="automation-kpis"></div>
          <ul class="list" id="automation-distribution"></ul>
        </article>

        <article class="panel span-12">
          <h2>Containers Docker</h2>
          <div class="muted" id="docker-summary">Sem leitura dos containers.</div>
          <div id="docker-apps-table"></div>
          <div id="docker-table"></div>
        </article>

        <article class="panel span-5">
          <h2>Sessoes ativas</h2>
          <div class="muted" id="session-summary">Sem leitura de sessoes.</div>
          <div id="sessions-table"></div>
        </article>

        <article class="panel span-7">
          <h2>Auditoria recente</h2>
          <div class="muted" id="audit-summary">Sem leitura de auditoria.</div>
          <div id="audit-table"></div>
        </article>
      </section>
    </div>

    <script>
      const OVERVIEW_PATH = __OVERVIEW_PATH__;
      const endpointLabel = document.getElementById("endpoint-label");
      const auditTokenInput = document.getElementById("audit-token");
      const automationTokenInput = document.getElementById("automation-token");
      const refreshInput = document.getElementById("refresh-ms");
      const loadButton = document.getElementById("load-button");
      const toggleButton = document.getElementById("toggle-button");
      const requestStatus = document.getElementById("request-status");
      const lastUpdated = document.getElementById("last-updated");
      const healthList = document.getElementById("health-list");
      const integrationList = document.getElementById("integration-list");
      const messagingList = document.getElementById("messaging-list");
      const ticketKpis = document.getElementById("ticket-kpis");
      const ticketDistribution = document.getElementById("ticket-distribution");
      const automationKpis = document.getElementById("automation-kpis");
      const automationDistribution = document.getElementById("automation-distribution");
      const dockerSummary = document.getElementById("docker-summary");
      const dockerAppsTable = document.getElementById("docker-apps-table");
      const dockerTable = document.getElementById("docker-table");
      const sessionsTable = document.getElementById("sessions-table");
      const auditTable = document.getElementById("audit-table");
      const sessionSummary = document.getElementById("session-summary");
      const auditSummary = document.getElementById("audit-summary");
      const healthPill = document.getElementById("health-pill");
      const messagingPill = document.getElementById("messaging-pill");

      endpointLabel.textContent = OVERVIEW_PATH;

      const storageKeys = {
        audit: "helpdesk-runtime-audit-token",
        automation: "helpdesk-runtime-automation-token",
        refresh: "helpdesk-runtime-refresh-ms",
      };

      auditTokenInput.value = sessionStorage.getItem(storageKeys.audit) || "";
      automationTokenInput.value = sessionStorage.getItem(storageKeys.automation) || "";
      refreshInput.value = sessionStorage.getItem(storageKeys.refresh) || refreshInput.value;

      let refreshTimer = null;

      function setPill(element, text, tone) {
        element.textContent = text;
        element.className = `status-pill${tone ? ` ${tone}` : ""}`;
      }

      function setStatus(text, isError = false) {
        requestStatus.textContent = text;
        requestStatus.className = isError ? "error" : "";
      }

      function formatTime(value) {
        if (!value) return "n/a";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return value;
        return date.toLocaleString();
      }

      function renderPairs(container, pairs) {
        container.innerHTML = pairs
          .map(([label, value]) => `<li><span>${label}</span><small>${value}</small></li>`)
          .join("") || '<div class="empty">Sem dados.</div>';
      }

      function renderKpis(container, items) {
        container.innerHTML = items
          .map((item) => `
            <div class="kpi">
              <strong>${item.value}</strong>
              <span>${item.label}</span>
            </div>
          `)
          .join("");
      }

      function renderSimpleTable(container, columns, rows, emptyMessage) {
        if (!rows.length) {
          container.innerHTML = `<div class="empty">${emptyMessage}</div>`;
          return;
        }
        const head = columns.map((column) => `<th>${column.label}</th>`).join("");
        const body = rows
          .map((row) => `<tr>${columns.map((column) => `<td>${row[column.key] ?? "-"}</td>`).join("")}</tr>`)
          .join("");
        container.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
      }

      function applyOverview(data) {
        const healthTone = data.health.status === "ok" ? "" : "bad";
        setPill(healthPill, `${data.health.status} | ${data.health.environment}`, healthTone);
        setPill(
          messagingPill,
          `${data.messaging.resolved_delivery_provider} | ${data.messaging.configured ? "configured" : "mock"}`,
          data.messaging.configured ? "" : "warn",
        );

        renderPairs(healthList, [
          ["Servico", data.health.service],
          ["API prefix", data.health.api_prefix],
          ["Bind", `${data.health.host}:${data.health.port}`],
          ["Identity provider", data.identity_provider],
        ]);

        renderPairs(integrationList, [
          ["GLPI", `${data.glpi.mode} | ${data.glpi.status}`],
          ["Zabbix", `${data.zabbix.mode} | ${data.zabbix.status}`],
          ["Operational store", `${data.operational_store.mode} | ${data.operational_store.status}`],
          ["Queue backend", `${data.queue.mode} | ${data.queue.status}`],
          ["Automation runner", `${data.automation_runner.mode} | ${data.automation_runner.status}`],
          ["Docker monitor", `${data.docker.mode} | ${data.docker.status}`],
          ["LLM", `${data.llm.provider} | ${data.llm.status}`],
          ["Audit storage", data.operational_store.audit_storage_mode],
          ["Session storage", data.operational_store.session_storage_mode],
        ]);

        renderPairs(messagingList, [
          ["Configured provider", data.messaging.delivery_provider],
          ["Resolved provider", data.messaging.resolved_delivery_provider],
          ["Meta configured", data.messaging.meta_configured ? "yes" : "no"],
          ["Evolution configured", data.messaging.evolution_configured ? "yes" : "no"],
          ["Verify token", data.messaging.webhook_verify_token_configured ? "configured" : "missing"],
          ["Signature validation", data.messaging.signature_validation_enabled ? "enabled" : "disabled"],
        ]);

        renderKpis(ticketKpis, [
          { label: "Total snapshots", value: data.ticket_operations.total_tickets },
          { label: "Backlog aberto", value: data.ticket_operations.unresolved_backlog_count },
          { label: "Backlog sem dono", value: data.ticket_operations.unassigned_backlog_count },
          { label: "Taxa de resolucao", value: `${data.ticket_operations.resolution_rate_percent}%` },
        ]);
        renderPairs(ticketDistribution, [
          ["Cobertura de atribuicao", `${data.ticket_operations.backlog_assignment_coverage_percent}%`],
          ["Alta prioridade", data.ticket_operations.high_priority_backlog_count],
          ["Resolvidos", data.ticket_operations.resolved_ticket_count],
          ["Canal dominante", Object.entries(data.ticket_operations.source_channel_counts || {})[0]?.join(" = ") || "n/a"],
        ]);

        renderKpis(automationKpis, [
          { label: "Jobs totais", value: data.automation.total_jobs },
          { label: "Fila primaria", value: data.automation.queue_depth },
          { label: "Dead-letter", value: data.automation.dead_letter_queue_depth },
          { label: "Executando", value: (data.automation.execution_status_counts || {}).running || 0 },
        ]);
        renderPairs(automationDistribution, [
          ["Queue mode", data.automation.queue_mode],
          ["Aguardando aprovacao", (data.automation.approval_status_counts || {}).pending || 0],
          ["Queued", (data.automation.execution_status_counts || {}).queued || 0],
          ["Retry agendado", (data.automation.execution_status_counts || {})["retry-scheduled"] || 0],
          ["Runner projects", data.automation_runner.project_count],
          ["Catalogo runner", data.automation_runner.catalog_entry_count],
        ]);

        dockerSummary.textContent = `${data.docker.application_count} stack(s), ${data.docker.total_containers} container(es), ${data.docker.running_count} em execucao, ${data.docker.unhealthy_count} unhealthy.`;
        renderSimpleTable(
          dockerAppsTable,
          [
            { key: "application_name", label: "Aplicacao" },
            { key: "status", label: "Status" },
            { key: "application_services", label: "Servicos principais" },
            { key: "support_services", label: "Dependencias" },
            { key: "notes", label: "Leitura operacional" },
          ],
          (data.docker.applications || []).map((application) => ({
            ...application,
            application_services: (application.application_services || []).join(", ") || "-",
            support_services: (application.support_services || []).join(", ") || "-",
            notes: (application.notes || []).join(" | ") || "-",
          })),
          "Nenhum stack Docker identificado no host atual.",
        );
        renderSimpleTable(
          dockerTable,
          [
            { key: "application_name", label: "Aplicacao" },
            { key: "name", label: "Container" },
            { key: "service_role", label: "Role" },
            { key: "image", label: "Image" },
            { key: "state", label: "State" },
            { key: "health_status", label: "Health" },
            { key: "compose", label: "Compose" },
            { key: "ports", label: "Ports" },
          ],
          (data.docker.containers || []).map((container) => ({
            ...container,
            application_name: container.application_name || container.compose_project || "standalone",
            health_status: container.health_status || "-",
            compose: [container.compose_project, container.compose_service].filter(Boolean).join(" / ") || "-",
            ports: container.ports || "-",
          })),
          "Nenhum container encontrado no host atual.",
        );

        sessionSummary.textContent = `${data.sessions.total_sessions} sessao(oes) ativa(s) listadas no runtime.`;
        renderSimpleTable(
          sessionsTable,
          [
            { key: "phone_number_masked", label: "Phone" },
            { key: "requester_display_name", label: "Requester" },
            { key: "flow_name", label: "Flow" },
            { key: "stage", label: "Stage" },
            { key: "updated_at", label: "Updated" },
          ],
          (data.sessions.sessions || []).map((session) => ({
            ...session,
            updated_at: formatTime(session.updated_at),
          })),
          "Nenhuma sessao ativa registrada.",
        );

        auditSummary.textContent = `${data.audit.recent_event_count} evento(s) recentes carregados do runtime.`;
        renderSimpleTable(
          auditTable,
          [
            { key: "created_at", label: "When" },
            { key: "event_type", label: "Event" },
            { key: "source_channel", label: "Source" },
            { key: "status", label: "Status" },
            { key: "ticket_id", label: "Ticket" },
          ],
          (data.audit.recent_events || []).map((event) => ({
            ...event,
            created_at: formatTime(event.created_at),
            ticket_id: event.ticket_id || "-",
          })),
          "Nenhum evento recente encontrado.",
        );

        lastUpdated.textContent = `Ultima leitura: ${formatTime(data.generated_at)}`;
      }

      async function loadOverview() {
        const auditToken = auditTokenInput.value.trim();
        const automationToken = automationTokenInput.value.trim();
        if (!auditToken || !automationToken) {
          setStatus("Preencha audit token e automation read token para consultar o runtime.", true);
          return;
        }

        sessionStorage.setItem(storageKeys.audit, auditToken);
        sessionStorage.setItem(storageKeys.automation, automationToken);
        sessionStorage.setItem(storageKeys.refresh, refreshInput.value.trim());
        setStatus("Consultando backend runtime...");

        try {
          const response = await fetch(OVERVIEW_PATH, {
            headers: {
              "X-Helpdesk-Audit-Key": auditToken,
              "X-Helpdesk-Automation-Read-Key": automationToken,
            },
          });

          const contentType = response.headers.get("content-type") || "";
          const body = contentType.includes("application/json") ? await response.json() : null;

          if (!response.ok) {
            const detail = body && body.detail ? body.detail : `HTTP ${response.status}`;
            throw new Error(detail);
          }

          applyOverview(body);
          setStatus("Runtime atualizado com sucesso.");
        } catch (error) {
          setStatus(`Falha ao consultar runtime: ${error.message}`, true);
        }
      }

      function toggleAutoRefresh() {
        if (refreshTimer) {
          clearInterval(refreshTimer);
          refreshTimer = null;
          toggleButton.textContent = "Auto refresh: off";
          return;
        }
        const intervalMs = Math.max(Number(refreshInput.value) || 10000, 3000);
        refreshTimer = setInterval(loadOverview, intervalMs);
        toggleButton.textContent = `Auto refresh: on (${intervalMs}ms)`;
      }

      loadButton.addEventListener("click", loadOverview);
      toggleButton.addEventListener("click", toggleAutoRefresh);
    </script>
  </body>
</html>
"""
    return template.replace("__OVERVIEW_PATH__", overview_path_json)