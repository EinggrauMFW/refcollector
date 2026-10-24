/**
 * RefCollector Local Web Application Frontend
 * Minimal local-assets single-page application.
 * Plain vanilla ECMAScript 2022. No external frameworks or remote assets.
 */

(() => {
  "use strict";

  // --- STATE ---
  const state = {
    csrfToken: null,
    principal: null,
    capabilities: null,
    executorStatus: null,
    projects: [],
    projectsCursor: null,
    currentProjectId: null,
    currentProject: null,
    sources: [],
    sourcesCursor: null,
    currentSourceId: null,
    currentSource: null,
    jobs: [],
    activeJobs: new Map(), // jobId -> poll metadata
    currentJobId: null,
    currentJob: null,
    references: [],
    referencesCursor: null,
    referencesTotal: 0,
    selectedReferenceId: null,
    selectedReference: null,
    checkedReferenceIds: new Set(),
    referenceFilter: {
      query: "",
      sort: "order",
      reason: "",
      extraction: "",
      identity: "",
      discovery: "",
      classification: "",
      classification_is_null: false,
      local_copy: "",
      retrieval: "",
      source_id: "",
      job_id: ""
    },
    artifacts: [],
    globalArtifacts: [],
    globalJobs: [],
    currentArtifact: null,
    artifactOffset: 0,
    activeRepair: null,
    settings: null
  };

  // --- DOM HELPER ---
  // Strictly uses createElement and textContent. Never uses innerHTML with data.
  function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [key, val] of Object.entries(attrs)) {
      if (key === "className" || key === "class") {
        el.className = val;
      } else if (key.startsWith("on") && typeof val === "function") {
        el.addEventListener(key.slice(2).toLowerCase(), val);
      } else if (key === "style" && typeof val === "object") {
        Object.assign(el.style, val);
      } else if (val !== null && val !== undefined && val !== false) {
        el.setAttribute(key, val === true ? "" : val);
      }
    }
    for (const child of children) {
      if (child === null || child === undefined) continue;
      if (typeof child === "string" || typeof child === "number") {
        el.appendChild(document.createTextNode(String(child)));
      } else if (child instanceof Node) {
        el.appendChild(child);
      } else if (Array.isArray(child)) {
        for (const c of child) {
          if (c instanceof Node) el.appendChild(c);
          else if (c !== null && c !== undefined) el.appendChild(document.createTextNode(String(c)));
        }
      }
    }
    return el;
  }

  function clearElement(el) {
    while (el.firstChild) {
      el.removeChild(el.firstChild);
    }
  }

  // --- NOTIFICATIONS & ACCESSIBILITY ---
  function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    const toast = h("div", { class: `toast toast-${type}` }, message);
    container.appendChild(toast);
    announce(message);
    setTimeout(() => {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 4500);
  }

  function announce(message, priority = "polite") {
    const announcer = document.getElementById(priority === "assertive" ? "aria-alerts" : "aria-announcer");
    if (announcer) {
      announcer.textContent = "";
      setTimeout(() => { announcer.textContent = message; }, 50);
    }
  }

  // --- HTTP & API CLIENT ---
  // In-memory pending mutations: fingerprint -> { idempotencyKey, inFlight }
  // Never stored in browser storage (no document bodies or secrets persisted).
  const pendingMutations = new Map();
  const fileIdentities = new WeakMap();
  const secretIdentities = new WeakMap();

  function getMutationFingerprint(path, body) {
    if (!body || typeof body !== "object") return null;
    if (typeof FormData !== "undefined" && body instanceof FormData) {
      if (typeof body.get === "function" && body.get("idempotency_key")) {
        const file = body.get("file");
        let fileId = "";
        if (file && typeof file === "object") {
          fileId = fileIdentities.get(file);
          if (!fileId) {
            fileId = crypto.randomUUID();
            fileIdentities.set(file, fileId);
          }
        } else if (file) {
          fileId = String(file);
        }
        const fileName = (file && typeof file === "object" && file.name) ? file.name : (typeof file === "string" ? file : "");
        const purpose = body.get("purpose") || "";
        const occId = body.get("occurrence_id") || "";
        const verHint = body.get("version_hint") || "";
        const declared = body.get("declared_bytes") || "";
        return `${path}::formdata::${fileId}::${fileName}::${purpose}::${occId}::${verHint}::${declared}`;
      }
      return null;
    }
    if (!("idempotency_key" in body)) return null;
    const clone = { ...body };
    if (clone.change && clone.change.type === "set_provider_key") {
      let secretId = "";
      if (typeof clone.change === "object") {
        secretId = secretIdentities.get(clone.change);
        if (!secretId) {
          secretId = crypto.randomUUID();
          secretIdentities.set(clone.change, secretId);
        }
      }
      clone.change = {
        ...clone.change,
        value: `[SECRET-ID:${secretId}]`
      };
    }
    delete clone.idempotency_key;
    return `${path}::${JSON.stringify(clone)}`;
  }

  function makeIdempotencyKey() {
    return crypto.randomUUID();
  }

  async function api(path, options = {}) {
    const method = options.method || "GET";
    const headers = {
      "Accept": "application/json",
      ...(options.headers || {})
    };

    // Ensure session & CSRF token for mutating requests
    if (method !== "GET" && method !== "HEAD") {
      if (!state.csrfToken) {
        await initSession();
      }
      if (state.csrfToken) {
        headers["X-CSRF-Token"] = state.csrfToken;
      }
    }

    let fp = null;
    let body = options.body;
    const isFormData = typeof FormData !== "undefined" && body instanceof FormData;

    // Central mutation handling: track/add/override idempotency only where idempotency_key is present
    if (body && typeof body === "object") {
      if (isFormData) {
        if (typeof body.get === "function" && body.get("idempotency_key")) {
          fp = getMutationFingerprint(path, body);
          if (fp) {
            const pending = pendingMutations.get(fp);
            if (pending && pending.inFlight) {
              // Prevent duplicate double-click mutation
              throw new Error("Request already in progress. Please wait.");
            }
            const key = (pending && pending.idempotencyKey)
              ? pending.idempotencyKey
              : (body.get("idempotency_key") || crypto.randomUUID());
            if (typeof body.set === "function") {
              body.set("idempotency_key", key);
            }
            pendingMutations.set(fp, { idempotencyKey: key, inFlight: true });
          }
        }
      } else {
        if ("idempotency_key" in body) {
          fp = getMutationFingerprint(path, body);
          if (fp) {
            const pending = pendingMutations.get(fp);
            if (pending && pending.inFlight) {
              // Prevent duplicate double-click mutation
              throw new Error("Request already in progress. Please wait.");
            }
            // Reuse key until successful response, retaining across transport failures
            const key = (pending && pending.idempotencyKey)
              ? pending.idempotencyKey
              : (body.idempotency_key || crypto.randomUUID());
            body.idempotency_key = key;
            pendingMutations.set(fp, { idempotencyKey: key, inFlight: true });
          }
        }
        headers["Content-Type"] = "application/json";
        body = JSON.stringify(body);
      }
    }

    let response;
    let result;
    try {
      response = await fetch(path, {
        method,
        headers,
        body
      });

      if (!response.ok) {
        let errorData = null;
        try {
          errorData = await response.json();
        } catch {
          // Not JSON
        }
        const err = new Error(errorData?.message || errorData?.detail || response.statusText);
        err.status = response.status;
        err.data = errorData;
        throw err;
      }

      if (response.status === 204) {
        result = null;
      } else {
        const contentType = (response.headers && typeof response.headers.get === "function" && response.headers.get("content-type")) || "";
        if (contentType.includes("application/json")) {
          result = await response.json();
        } else if (typeof response.text === "function") {
          result = await response.text();
        } else if (typeof response.json === "function") {
          result = await response.json();
        } else {
          result = null;
        }
      }

      // Successful response and fully decoded body: clear pending
      if (fp) {
        pendingMutations.delete(fp);
      }
      return result;
    } catch (err) {
      // Decode error, HTTP error, or transport failure: release inFlight and retain key
      if (fp) {
        const pending = pendingMutations.get(fp);
        if (pending) pending.inFlight = false;
      }
      throw err;
    }
  }

  async function initSession() {
    try {
      const data = await api("/api/session");
      if (data) {
        if (data.csrf_token) {
          state.csrfToken = data.csrf_token;
        }
        if (data.principal) {
          state.principal = data.principal;
          const badge = document.getElementById("user-principal-badge");
          if (badge) badge.textContent = `User: ${data.principal}`;
        }
      }
    } catch (err) {
      console.warn("Session initialization:", err.message);
    }
  }

  // --- MODAL CONTROLS ---
  function openModal(modalId) {
    const modal = document.getElementById(modalId);
    const backdrop = document.getElementById("modal-backdrop");
    if (modal && backdrop) {
      backdrop.style.display = "block";
      modal.setAttribute("open", "");
    }
  }

  function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    const backdrop = document.getElementById("modal-backdrop");
    if (modal) {
      modal.removeAttribute("open");
      if (typeof modal.close === "function" && modal.open) {
        modal.close();
      }
    }
    if (backdrop) {
      backdrop.style.display = "none";
    }

    if (modalId === "modal-confirm-intent") {
      dismissConfirmationModal();
    } else if (modalId === "modal-review-intent") {
      activeReviewContext = null;
    } else if (modalId === "modal-create-export") {
      state.exportProjectContext = null;
      state.exportProjectRevision = null;
    }
  }

  document.querySelectorAll("[data-close]").forEach(btn => {
    btn.addEventListener("click", () => {
      closeModal(btn.getAttribute("data-close"));
    });
  });

  const modalBackdropEl = document.getElementById("modal-backdrop");
  if (modalBackdropEl) {
    modalBackdropEl.addEventListener("click", () => {
      document.querySelectorAll("dialog[open]").forEach(d => closeModal(d.id));
    });
  }

  document.querySelectorAll("dialog").forEach(dialog => {
    dialog.addEventListener("cancel", () => {
      closeModal(dialog.id);
    });
  });

  // --- VIEW ROUTING ---
  function switchView(viewName) {
    if (viewName !== "settings") {
      clearSecretInputs();
    }
    document.querySelectorAll(".nav-tab").forEach(tab => {
      tab.classList.toggle("active", tab.getAttribute("data-view") === viewName);
    });
    document.querySelectorAll(".view-panel").forEach(panel => {
      panel.classList.toggle("active", panel.id === `view-${viewName}`);
    });
    window.location.hash = viewName;

    if (viewName === "dashboard") loadDashboard();
    else if (viewName === "projects") loadProjects();
    else if (viewName === "sources") loadSources();
    else if (viewName === "jobs") loadJobs();
    else if (viewName === "references") loadReferences();
    else if (viewName === "artifacts") loadArtifacts();
    else if (viewName === "settings") loadSettings();
  }

  document.querySelectorAll(".nav-tab").forEach(tab => {
    tab.addEventListener("click", () => {
      switchView(tab.getAttribute("data-view"));
    });
  });

  // --- DASHBOARD LOADER ---
  async function loadDashboard() {
    try {
      const [caps, exec, jobsOut, artOut] = await Promise.all([
        api("/api/capabilities"),
        api("/api/executor/status"),
        api("/api/jobs?limit=10"),
        api("/api/artifacts?limit=10")
      ]);

      state.capabilities = caps;
      state.executorStatus = exec;
      state.globalJobs = jobsOut.jobs || [];
      state.globalArtifacts = artOut.artifacts || [];

      renderCapabilities();
      renderExecutorStatus();
      renderGlobalJobs();
      renderGlobalArtifacts();
    } catch (err) {
      showToast(`Dashboard load failed: ${err.message}`, "bad");
    }
  }

  function renderCapabilities() {
    const caps = state.capabilities;
    if (!caps) return;

    document.getElementById("server-version-badge").textContent = `v${caps.server_version} (${caps.release})`;
    document.getElementById("cap-release").textContent = caps.release;
    document.getElementById("cap-stages").textContent = (caps.stages || []).join(", ");
    document.getElementById("cap-source-types").textContent = (caps.source_types || []).join(", ");
    document.getElementById("cap-export-formats").textContent = (caps.export_formats || []).join(", ");
    document.getElementById("cap-scopes").textContent = (caps.scopes_granted || []).join(", ");

    const provBox = document.getElementById("capabilities-providers");
    clearElement(provBox);
    (caps.providers || []).forEach(p => {
      const cls = p.configured ? "badge-ok" : "badge-warn";
      const badge = h("span", { class: `badge ${cls}` }, `${p.name}: ${p.configured ? "Configured" : "Not configured"}`);
      provBox.appendChild(badge);
    });
  }

  function renderExecutorStatus() {
    const exec = state.executorStatus;
    const badge = document.getElementById("executor-status-badge");
    const details = document.getElementById("executor-details");
    if (!exec) return;

    badge.textContent = `Executor: ${exec.state}`;
    badge.className = `badge ${exec.state === "owned" ? "badge-ok" : exec.state === "stale" ? "badge-bad" : "badge-warn"}`;

    clearElement(details);
    const dl = h("dl", { class: "meta-list" },
      h("dt", {}, "State"), h("dd", {}, exec.state),
      h("dt", {}, "Local Process Owner"), h("dd", {}, exec.is_local_owner ? "Yes" : "No"),
      h("dt", {}, "Owner PID"), h("dd", {}, exec.owner_pid !== null ? String(exec.owner_pid) : "None"),
      h("dt", {}, "Last Heartbeat"), h("dd", {}, exec.heartbeat_at || "Never"),
      h("dt", {}, "Observed At"), h("dd", {}, exec.observed_at)
    );
    details.appendChild(dl);
  }

  function renderGlobalJobs() {
    const tbody = document.getElementById("tbody-global-jobs");
    clearElement(tbody);

    if (state.globalJobs.length === 0) {
      tbody.appendChild(h("tr", {}, h("td", { colspan: 6, class: "text-muted" }, "No global jobs found.")));
      return;
    }

    state.globalJobs.forEach(j => {
      const tr = h("tr", {},
        h("td", {}, j.job_id),
        h("td", {}, j.project_name || j.project_id),
        h("td", {}, h("span", { class: `pill ${getJobStatusClass(j.state)}` }, j.state)),
        h("td", {}, (j.stages || []).join(", ")),
        h("td", {}, j.created_at || "-"),
        h("td", {},
          h("button", {
            type: "button",
            class: "btn btn-sm",
            onClick: () => {
              state.currentProjectId = j.project_id;
              updateProjectSelect();
              switchView("jobs");
              inspectJob(j.job_id);
            }
          }, "Inspect")
        )
      );
      tbody.appendChild(tr);
    });
  }

  function renderGlobalArtifacts() {
    const tbody = document.getElementById("tbody-global-artifacts");
    clearElement(tbody);

    if (state.globalArtifacts.length === 0) {
      tbody.appendChild(h("tr", {}, h("td", { colspan: 6, class: "text-muted" }, "No global artifacts found.")));
      return;
    }

    state.globalArtifacts.forEach(a => {
      const d = a.descriptor;
      const tr = h("tr", {},
        h("td", {}, d.artifact_id),
        h("td", {}, a.project_name || a.project_id),
        h("td", {}, d.kind),
        h("td", {}, d.media_type),
        h("td", {}, `${d.bytes} B`),
        h("td", {},
          h("button", {
            type: "button",
            class: "btn btn-sm",
            onClick: () => downloadProjectFile(a.project_id, "artifact", d.artifact_id, d.name)
          }, "Download")
        )
      );
      tbody.appendChild(tr);
    });
  }

  // --- PROJECTS LOADER ---
  async function loadProjects() {
    try {
      const data = await api("/api/projects?limit=50");
      state.projects = data.projects || [];
      state.projectsCursor = data.next_cursor;
      updateProjectSelect();
      renderProjectsTable();

      if (state.currentProjectId) {
        await loadProjectDetail(state.currentProjectId);
      }
    } catch (err) {
      showToast(`Failed loading projects: ${err.message}`, "bad");
    }
  }

  function updateProjectSelect() {
    const select = document.getElementById("global-project-select");
    const current = state.currentProjectId;
    clearElement(select);

    select.appendChild(h("option", { value: "" }, "No Project Selected"));
    state.projects.forEach(p => {
      const opt = h("option", { value: p.project_id }, `${p.name} (Rev ${p.revision})`);
      if (p.project_id === current) opt.selected = true;
      select.appendChild(opt);
    });
    syncModeLinks();
  }

  document.getElementById("global-project-select").addEventListener("change", async (e) => {
    state.currentProjectId = e.target.value || null;
    syncModeLinks();
    if (state.currentProjectId) {
      await loadProjectDetail(state.currentProjectId);
    } else {
      document.getElementById("project-detail-card").style.display = "none";
    }
    const currentHash = window.location.hash.replace("#", "");
    if (currentHash === "sources") loadSources();
    else if (currentHash === "jobs") loadJobs();
    else if (currentHash === "references") loadReferences();
    else if (currentHash === "artifacts") loadArtifacts();
    else if (currentHash === "settings") loadSettings();
  });

  function renderProjectsTable() {
    const tbody = document.getElementById("tbody-projects");
    clearElement(tbody);

    if (state.projects.length === 0) {
      tbody.appendChild(h("tr", {}, h("td", { colspan: 6, class: "text-muted" }, "No projects exist. Create one to begin.")));
      return;
    }

    state.projects.forEach(p => {
      const tr = h("tr", {
        onClick: () => {
          state.currentProjectId = p.project_id;
          updateProjectSelect();
          loadProjectDetail(p.project_id);
        }
      },
        h("td", {}, p.name),
        h("td", {}, p.project_id),
        h("td", {}, String(p.revision)),
        h("td", {}, String(p.counts?.occurrences ?? 0)),
        h("td", {}, p.updated_at || "-"),
        h("td", {},
          h("button", {
            type: "button",
            class: "btn btn-sm btn-primary",
            onClick: (e) => {
              e.stopPropagation();
              state.currentProjectId = p.project_id;
              updateProjectSelect();
              switchView("references");
            }
          }, "Open References")
        )
      );
      tbody.appendChild(tr);
    });
  }

  async function loadProjectDetail(projectId) {
    try {
      const proj = await api(`/api/projects/${projectId}`);
      if (state.currentProjectId !== projectId) return null;
      state.currentProject = proj;
      renderProjectDetail();
      return proj;
    } catch (err) {
      if (state.currentProjectId !== projectId) return null;
      showToast(`Failed loading project detail: ${err.message}`, "bad");
      return null;
    }
  }

  function renderProjectDetail() {
    const p = state.currentProject;
    if (!p) return;

    const card = document.getElementById("project-detail-card");
    card.style.display = "block";

    document.getElementById("project-detail-title").textContent = p.name;
    document.getElementById("project-detail-revision").textContent = `Rev ${p.revision}`;
    document.getElementById("proj-network-mode").textContent = p.network_mode || "metadata";
    document.getElementById("proj-downloads-enabled").textContent = p.download_enabled ? "Yes" : "No";
    document.getElementById("proj-policy-profile").textContent = p.policy?.profile || "-";
    document.getElementById("proj-version-policy").textContent = p.policy?.version_policy || "-";

    const countsGrid = document.getElementById("project-counts-summary");
    clearElement(countsGrid);

    const counts = p.counts || {};
    const totalOccurrences = counts.occurrences || 0;

    const dimensions = [
      { key: "Total Occurrences", val: totalOccurrences },
      { key: "Extraction", val: formatCountMap(counts.by_extraction) },
      { key: "Identity", val: formatCountMap(counts.by_identity) },
      { key: "Discovery", val: formatCountMap(counts.by_discovery) },
      { key: "Classification", val: formatCountMap(counts.by_classification) },
      { key: "Local Copies", val: formatCountMap(counts.by_local_copy) },
      { key: "Retrieval", val: formatCountMap(counts.by_retrieval) }
    ];

    dimensions.forEach(d => {
      const box = h("div", { class: "count-card" },
        h("div", { class: "count-card-title" }, d.key),
        h("div", {}, String(d.val))
      );
      countsGrid.appendChild(box);
    });
  }

  function formatCountMap(map) {
    if (!map || typeof map !== "object") return "0";
    return Object.entries(map).map(([k, v]) => `${k}: ${v}`).join(", ") || "0";
  }

  // --- CREATE PROJECT ---
  document.getElementById("btn-open-create-project").addEventListener("click", () => {
    openModal("modal-create-project");
  });

  document.getElementById("form-create-project").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = document.getElementById("create-proj-name").value.trim();
    const profile = document.getElementById("create-proj-profile").value;
    const versionPolicy = document.getElementById("create-proj-version-policy").value;

    try {
      const out = await api("/api/projects", {
        method: "POST",
        body: {
          name,
          policy_profile: profile,
          version_policy: versionPolicy,
          idempotency_key: makeIdempotencyKey()
        }
      });
      showToast(`Project created: ${out.project_id}`, "ok");
      closeModal("modal-create-project");
      document.getElementById("form-create-project").reset();
      state.currentProjectId = out.project_id;
      await loadProjects();
    } catch (err) {
      showToast(`Project creation failed: ${err.message}`, "bad");
    }
  });

  // --- SOURCES LOADER ---
  async function loadSources() {
    if (!state.currentProjectId) {
      clearElement(document.getElementById("tbody-sources"));
      document.getElementById("tbody-sources").appendChild(
        h("tr", {}, h("td", { colspan: 9, class: "text-muted" }, "Select a project first."))
      );
      return;
    }

    try {
      const data = await api(`/api/projects/${state.currentProjectId}/sources`);
      state.sources = data.sources || [];
      state.sourcesCursor = data.next_cursor;
      renderSourcesTable();
    } catch (err) {
      showToast(`Failed loading sources: ${err.message}`, "bad");
    }
  }

  function renderSourcesTable() {
    const tbody = document.getElementById("tbody-sources");
    clearElement(tbody);

    if (state.sources.length === 0) {
      tbody.appendChild(h("tr", {}, h("td", { colspan: 9, class: "text-muted" }, "No sources imported. Import citation text or a local file to begin.")));
      return;
    }

    state.sources.forEach(s => {
      const tr = h("tr", {
        onClick: () => inspectSource(s.source_id)
      },
        h("td", {}, s.source_id),
        h("td", {}, s.label || "-"),
        h("td", {}, s.type),
        h("td", {}, String(s.pages)),
        h("td", {}, String(s.sections)),
        h("td", {}, h("span", { class: "badge" }, s.extraction_state)),
        h("td", {}, String(s.occurrence_count)),
        h("td", {}, s.imported_at || "-"),
        h("td", {},
          h("button", {
            type: "button",
            class: "btn btn-sm",
            onClick: (e) => {
              e.stopPropagation();
              inspectSource(s.source_id);
            }
          }, "Inspect")
        )
      );
      tbody.appendChild(tr);
    });
  }

  async function inspectSource(sourceId) {
    state.currentSourceId = sourceId;
    try {
      const data = await api(`/api/projects/${state.currentProjectId}/sources/${sourceId}`);
      state.currentSource = data;
      renderSourceDetail();
    } catch (err) {
      showToast(`Failed loading source detail: ${err.message}`, "bad");
    }
  }

  function renderSourceDetail() {
    const s = state.currentSource;
    if (!s) return;

    const card = document.getElementById("source-detail-card");
    card.style.display = "block";

    document.getElementById("source-detail-title").textContent = `Source: ${s.source.label || s.source.source_id} (Rev ${s.revision})`;

    const warnBox = document.getElementById("source-preflight-warnings");
    if (s.preflight && s.preflight.warnings && s.preflight.warnings.length > 0) {
      warnBox.style.display = "block";
      clearElement(warnBox);
      warnBox.appendChild(h("strong", {}, "Preflight Warnings:"));
      const ul = h("ul", {});
      s.preflight.warnings.forEach(w => ul.appendChild(h("li", {}, w)));
      warnBox.appendChild(ul);
    } else {
      warnBox.style.display = "none";
    }

    const sectionsList = document.getElementById("source-sections-list");
    clearElement(sectionsList);
    (s.sections || []).forEach(sec => {
      sectionsList.appendChild(h("li", {}, `Pages: ${sec.page_start} to ${sec.page_end} (${sec.occurrence_count || 0} references)`));
    });

    const infoBox = document.getElementById("source-extraction-info");
    clearElement(infoBox);
    const ext = s.extraction || {};
    infoBox.appendChild(h("dl", { class: "meta-list" },
      h("dt", {}, "State"), h("dd", {}, ext.state || "-"),
      h("dt", {}, "Adapter"), h("dd", {}, ext.adapter || "-"),
      h("dt", {}, "Occurrences"), h("dd", {}, String(ext.occurrences ?? "-"))
    ));

    // Safe Source Page Preview state
    const previewSection = document.getElementById("source-preview-section");
    if (previewSection) {
      currentSourcePreviewState.sourceId = s.source.source_id;
      currentSourcePreviewState.pageIndex = 0;
      currentSourcePreviewState.totalPages = s.source.pages || 1;
      const indicator = document.getElementById("source-preview-page-indicator");
      if (indicator) indicator.textContent = `Page 0 of ${Math.max(0, currentSourcePreviewState.totalPages - 1)}`;
      const prevBtn = document.getElementById("btn-preview-prev-page");
      if (prevBtn) prevBtn.disabled = true;
      const nextBtn = document.getElementById("btn-preview-next-page");
      if (nextBtn) nextBtn.disabled = currentSourcePreviewState.totalPages <= 1;
      const statusBox = document.getElementById("source-preview-status");
      if (statusBox) {
        statusBox.style.display = "block";
        statusBox.className = "notice-box text-muted";
        statusBox.textContent = "Select a page and click Load Page to preview server-rendered raster.";
      }
      const stage = document.getElementById("source-preview-stage");
      if (stage) stage.style.display = "none";
    }
  }

  // --- SAFE SOURCE PAGE RASTER PREVIEW ---
  const currentSourcePreviewState = {
    sourceId: null,
    pageIndex: 0,
    totalPages: 1,
    scale: 1.0,
    activeObjectUrl: null
  };

  const modalPreviewState = {
    projectId: null,
    sourceId: null,
    occurrenceId: null,
    pageIndex: 0,
    span: null,
    citation: "",
    scale: 1.0,
    activeObjectUrl: null
  };

  async function loadSourceRasterPage(projectId, sourceId, pageIndex, scale = 1.0) {
    const url = `/api/projects/${projectId}/sources/${sourceId}/pages/${pageIndex}?scale=${scale}`;
    const res = await fetch(url, {
      method: "GET",
      headers: { "Accept": "image/png" }
    });
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try {
        const err = await res.json();
        msg = err.message || err.detail || msg;
      } catch {
        // Not JSON
      }
      const error = new Error(msg);
      error.status = res.status;
      throw error;
    }
    const overlayHeader = res.headers && typeof res.headers.get === "function"
      ? res.headers.get("x-span-overlay-supported")
      : null;
    const spanOverlaySupported = overlayHeader !== "0";
    const blob = await res.blob();
    return {
      objectUrl: URL.createObjectURL(blob),
      spanOverlaySupported
    };
  }

  function renderBoundingBoxes(container, spans, citationText) {
    clearElement(container);
    if (!Array.isArray(spans) || spans.length === 0) return;
    spans.forEach((span, idx) => {
      if (!span.bbox || span.bbox.length !== 4) return;
      const [x0, y0, x1, y1] = span.bbox;
      const left = Math.max(0, Math.min(1, Math.min(x0, x1))) * 100;
      const top = Math.max(0, Math.min(1, Math.min(y0, y1))) * 100;
      const width = Math.max(0, Math.min(100 - left, Math.abs(x1 - x0) * 100));
      const height = Math.max(0, Math.min(100 - top, Math.abs(y1 - y0) * 100));

      const label = `Span ${idx + 1} on page ${span.page_index}${span.page_label ? ` (${span.page_label})` : ""}`;
      const box = h("div", {
        class: "raster-bbox-highlight",
        role: "region",
        tabindex: "0",
        "aria-label": label,
        title: citationText ? `${label}: ${citationText}` : label,
        style: {
          left: `${left}%`,
          top: `${top}%`,
          width: `${width}%`,
          height: `${height}%`
        }
      });
      container.appendChild(box);
    });
  }

  async function loadSourcePreview(pageIndex) {
    if (!state.currentProjectId || !currentSourcePreviewState.sourceId) return;
    const statusBox = document.getElementById("source-preview-status");
    const stage = document.getElementById("source-preview-stage");
    const img = document.getElementById("source-preview-img");
    const overlays = document.getElementById("source-preview-overlays");
    const caption = document.getElementById("source-preview-citation-caption");
    const scaleSelect = document.getElementById("source-preview-scale-select");
    const scale = parseFloat(scaleSelect ? scaleSelect.value : "1.0") || 1.0;

    statusBox.style.display = "block";
    statusBox.className = "notice-box text-muted";
    statusBox.textContent = `Rendering page ${pageIndex} raster (scale ${scale})...`;

    try {
      if (currentSourcePreviewState.activeObjectUrl) {
        URL.revokeObjectURL(currentSourcePreviewState.activeObjectUrl);
        currentSourcePreviewState.activeObjectUrl = null;
      }
      const pageResult = await loadSourceRasterPage(
        state.currentProjectId,
        currentSourcePreviewState.sourceId,
        pageIndex,
        scale
      );
      currentSourcePreviewState.activeObjectUrl = pageResult.objectUrl;
      currentSourcePreviewState.pageIndex = pageIndex;
      currentSourcePreviewState.scale = scale;

      img.src = pageResult.objectUrl;
      clearElement(overlays);
      clearElement(caption);

      const relevantSpans = [];
      (state.references || []).forEach(r => {
        if (r.source_id === currentSourcePreviewState.sourceId && Array.isArray(r.spans)) {
          r.spans.forEach(sp => {
            if (sp.page_index === pageIndex) {
              relevantSpans.push({ ...sp, citation: r.raw_citation || r.edited_citation });
            }
          });
        }
      });

      if (!pageResult.spanOverlaySupported) {
        caption.appendChild(h("div", { class: "notice-box notice-warning mb-2" },
          "Page rotation detected: span overlay highlighting is unavailable for rotated pages. Page raster and accessible citation text remain available."
        ));
        if (relevantSpans.length > 0) {
          caption.appendChild(h("p", {}, `Page ${pageIndex} contains ${relevantSpans.length} reference citation span(s):`));
          const ul = h("ul", { class: "simple-list" });
          relevantSpans.forEach(sp => {
            ul.appendChild(h("li", {}, `${sp.citation || "Citation"} [BBox: ${(sp.bbox || []).map(n => typeof n === "number" ? n.toFixed(3) : n).join(", ")}]`));
          });
          caption.appendChild(ul);
        } else {
          caption.appendChild(h("p", { class: "text-muted" }, `No reference spans recorded on page ${pageIndex}.`));
        }
      } else {
        if (relevantSpans.length > 0) {
          renderBoundingBoxes(overlays, relevantSpans, relevantSpans[0].citation);
          caption.textContent = `Page ${pageIndex} contains ${relevantSpans.length} reference citation span(s).`;
        } else {
          caption.textContent = `Page ${pageIndex} rendered safely. No spans recorded on this page.`;
        }
      }

      statusBox.style.display = "none";
      stage.style.display = "flex";

      document.getElementById("source-preview-page-indicator").textContent = `Page ${pageIndex} of ${Math.max(0, currentSourcePreviewState.totalPages - 1)}`;
      document.getElementById("btn-preview-prev-page").disabled = pageIndex <= 0;
      document.getElementById("btn-preview-next-page").disabled = pageIndex >= (currentSourcePreviewState.totalPages - 1);
    } catch (err) {
      statusBox.style.display = "block";
      statusBox.className = "notice-box notice-warning";
      statusBox.textContent = `Source page raster preview unavailable: ${err.message}`;
      stage.style.display = "none";
    }
  }

  async function openSpanPreview(occ, span) {
    if (!state.currentProjectId) return;
    const sourceId = occ.source_id;
    if (!sourceId) {
      showToast("Reference does not have an associated source ID.", "warn");
      return;
    }

    modalPreviewState.projectId = state.currentProjectId;
    modalPreviewState.sourceId = sourceId;
    modalPreviewState.occurrenceId = occ.occurrence_id;
    modalPreviewState.pageIndex = span.page_index ?? 0;
    modalPreviewState.span = span;
    modalPreviewState.citation = occ.raw_citation || occ.edited_citation || "";
    modalPreviewState.scale = 1.0;

    document.getElementById("modal-preview-title").textContent = `Source Page Preview - Page ${modalPreviewState.pageIndex} (Source: ${sourceId})`;
    document.getElementById("modal-preview-citation-text").textContent = modalPreviewState.citation || "No citation text.";
    const bboxText = span.bbox ? `[${span.bbox.map(n => typeof n === "number" ? n.toFixed(3) : n).join(", ")}]` : "None";
    document.getElementById("modal-preview-span-info").textContent = `Page Index: ${span.page_index} | Label: ${span.page_label || "None"} | BBox: ${bboxText}`;

    openModal("modal-preview-page");
    await loadModalPagePreview(modalPreviewState.pageIndex);
  }

  async function loadModalPagePreview(pageIndex) {
    const statusBox = document.getElementById("modal-preview-status");
    const stage = document.getElementById("modal-preview-stage");
    const img = document.getElementById("modal-preview-img");
    const overlays = document.getElementById("modal-preview-overlays");
    const scaleSelect = document.getElementById("modal-preview-scale-select");
    const scale = parseFloat(scaleSelect ? scaleSelect.value : "1.0") || 1.0;

    statusBox.style.display = "block";
    statusBox.className = "notice-box text-muted";
    statusBox.textContent = `Loading page ${pageIndex} raster (scale ${scale})...`;

    try {
      if (modalPreviewState.activeObjectUrl) {
        URL.revokeObjectURL(modalPreviewState.activeObjectUrl);
        modalPreviewState.activeObjectUrl = null;
      }
      const pageResult = await loadSourceRasterPage(
        modalPreviewState.projectId,
        modalPreviewState.sourceId,
        pageIndex,
        scale
      );
      modalPreviewState.activeObjectUrl = pageResult.objectUrl;
      modalPreviewState.pageIndex = pageIndex;
      modalPreviewState.scale = scale;

      img.src = pageResult.objectUrl;
      clearElement(overlays);

      if (!pageResult.spanOverlaySupported) {
        statusBox.style.display = "block";
        statusBox.className = "notice-box notice-warning";
        statusBox.textContent = "Page rotation detected: span bounding box highlight is unavailable on rotated pages. Full page raster and citation text remain available.";
      } else {
        statusBox.style.display = "none";
        if (modalPreviewState.span && modalPreviewState.span.page_index === pageIndex) {
          renderBoundingBoxes(overlays, [modalPreviewState.span], modalPreviewState.citation);
        }
      }

      stage.style.display = "flex";

      document.getElementById("modal-preview-page-indicator").textContent = `Page ${pageIndex}`;
      document.getElementById("btn-modal-preview-prev").disabled = pageIndex <= 0;
    } catch (err) {
      statusBox.style.display = "block";
      statusBox.className = "notice-box notice-warning";
      statusBox.textContent = `Raster preview unavailable: ${err.message}`;
      stage.style.display = "none";
    }
  }

  // Preview Event Listeners
  document.getElementById("btn-preview-prev-page").addEventListener("click", () => {
    if (currentSourcePreviewState.pageIndex > 0) {
      loadSourcePreview(currentSourcePreviewState.pageIndex - 1);
    }
  });

  document.getElementById("btn-preview-next-page").addEventListener("click", () => {
    if (currentSourcePreviewState.pageIndex < currentSourcePreviewState.totalPages - 1) {
      loadSourcePreview(currentSourcePreviewState.pageIndex + 1);
    }
  });

  document.getElementById("btn-preview-load").addEventListener("click", () => {
    loadSourcePreview(currentSourcePreviewState.pageIndex);
  });

  document.getElementById("source-preview-scale-select").addEventListener("change", () => {
    loadSourcePreview(currentSourcePreviewState.pageIndex);
  });

  document.getElementById("btn-modal-preview-prev").addEventListener("click", () => {
    if (modalPreviewState.pageIndex > 0) {
      loadModalPagePreview(modalPreviewState.pageIndex - 1);
    }
  });

  document.getElementById("btn-modal-preview-next").addEventListener("click", () => {
    loadModalPagePreview(modalPreviewState.pageIndex + 1);
  });

  document.getElementById("modal-preview-scale-select").addEventListener("change", () => {
    loadModalPagePreview(modalPreviewState.pageIndex);
  });

  // --- IMPORT SOURCE MODAL ---
  document.getElementById("btn-open-import-source").addEventListener("click", () => {
    if (!state.currentProjectId) {
      showToast("Select a project before importing sources.", "warn");
      return;
    }
    openModal("modal-import-source");
  });

  document.getElementById("import-source-type").addEventListener("change", (e) => {
    const val = e.target.value;
    document.getElementById("import-type-text-group").style.display = val === "text" ? "block" : "none";
    document.getElementById("import-type-file-group").style.display = val === "local_file" ? "block" : "none";
    const uploadGroup = document.getElementById("import-type-upload-group");
    if (uploadGroup) uploadGroup.style.display = val === "browser_upload" ? "block" : "none";
    document.getElementById("btn-submit-import").disabled = false;
  });

  document.getElementById("form-import-source").addEventListener("submit", async (e) => {
    e.preventDefault();
    const type = document.getElementById("import-source-type").value;

    if (type === "browser_upload") {
      const fileInput = document.getElementById("import-source-file");
      const file = fileInput && fileInput.files && fileInput.files[0];
      if (!file) {
        showToast("Select a PDF file to upload.", "warn");
        return;
      }
      const fd = new FormData();
      fd.append("file", file);
      fd.append("purpose", "source");
      fd.append("declared_bytes", String(file.size));
      fd.append("idempotency_key", makeIdempotencyKey());

      try {
        const out = await api(`/api/projects/${state.currentProjectId}/uploads/pdf`, {
          method: "POST",
          body: fd
        });
        const sourceOut = out?.source || (out?.source_id ? out : null);
        showToast(`Source imported: ${sourceOut?.source_id || "success"}`, "ok");
        closeModal("modal-import-source");
        document.getElementById("form-import-source").reset();
        await loadSources();
        if (state.currentProjectId) await loadProjectDetail(state.currentProjectId);
      } catch (err) {
        showToast(`Source upload failed: ${err.message}`, "bad");
        // Keep fileInput selected on failure so user can retry with preserved idempotency key
      }
      return;
    }

    let inputPayload = null;
    if (type === "text") {
      const text = document.getElementById("import-source-text").value.trim();
      const label = document.getElementById("import-source-text-label").value.trim();
      if (!text) {
        showToast("Enter citation text to import.", "warn");
        return;
      }
      inputPayload = { type: "text", text, label: label || null };
    } else if (type === "local_file") {
      const path = document.getElementById("import-source-path").value.trim();
      if (!path) {
        showToast("Enter local file path to import.", "warn");
        return;
      }
      inputPayload = { type: "local_file", path };
    }

    try {
      const out = await api(`/api/projects/${state.currentProjectId}/sources/import`, {
        method: "POST",
        body: {
          project_id: state.currentProjectId,
          input: inputPayload,
          idempotency_key: makeIdempotencyKey()
        }
      });
      showToast(`Source imported: ${out.source_id}`, "ok");
      closeModal("modal-import-source");
      document.getElementById("form-import-source").reset();
      await loadSources();
      if (state.currentProjectId) await loadProjectDetail(state.currentProjectId);
    } catch (err) {
      showToast(`Source import failed: ${err.message}`, "bad");
    }
  });

  // --- EDIT SOURCE SECTIONS ---
  document.getElementById("btn-edit-sections").addEventListener("click", () => {
    if (!state.currentSource) return;
    const s = state.currentSource;
    const currentSections = s.sections || [];
    const promptText = currentSections.map(sec => `${sec.page_start}-${sec.page_end}`).join(", ");
    const input = prompt("Enter section page ranges (e.g. 1-10, 15-20):", promptText);
    if (input === null) return;

    const ranges = input.split(",").map(part => {
      const [start, end] = part.trim().split("-").map(Number);
      return { page_start: isNaN(start) ? 0 : start, page_end: isNaN(end) ? (isNaN(start) ? 0 : start) : end };
    }).filter(r => r.page_end >= r.page_start);

    if (ranges.length === 0) {
      showToast("Provide at least one valid page range.", "warn");
      return;
    }

    setSourceSections(s.source.source_id, ranges, s.revision);
  });

  async function setSourceSections(sourceId, sections, expectedRevision) {
    try {
      await api(`/api/projects/${state.currentProjectId}/sources/${sourceId}/sections`, {
        method: "POST",
        body: {
          project_id: state.currentProjectId,
          source_id: sourceId,
          sections,
          expected_source_revision: expectedRevision,
          idempotency_key: makeIdempotencyKey()
        }
      });
      showToast("Sections updated. Re-extraction job required.", "ok");
      await inspectSource(sourceId);
    } catch (err) {
      showToast(`Failed updating sections: ${err.message}`, "bad");
    }
  }

  // --- JOBS LOADER & CONTROLS ---
  async function loadJobs() {
    const projId = state.currentProjectId;
    if (!projId) {
      clearElement(document.getElementById("tbody-jobs"));
      document.getElementById("tbody-jobs").appendChild(
        h("tr", {}, h("td", { colspan: 5, class: "text-muted" }, "Select a project first."))
      );
      return;
    }

    try {
      const data = await api(`/api/projects/${projId}/jobs`);
      if (state.currentProjectId !== projId) return;
      state.jobs = data.jobs || [];
      renderJobsTable();
    } catch (err) {
      if (state.currentProjectId !== projId) return;
      showToast(`Failed loading jobs: ${err.message}`, "bad");
    }
  }

  function updateMatchingJobSummary(jobDetail) {
    if (!jobDetail || !jobDetail.job_id || !Array.isArray(state.jobs)) return;
    const matching = state.jobs.find(j => j.job_id === jobDetail.job_id);
    if (matching) {
      matching.state = jobDetail.state;
      if (jobDetail.created_at && !matching.created_at) {
        matching.created_at = jobDetail.created_at;
      }
      renderJobsTable();
    }
  }

  function renderJobsTable() {
    const tbody = document.getElementById("tbody-jobs");
    clearElement(tbody);

    if (state.jobs.length === 0) {
      tbody.appendChild(h("tr", {}, h("td", { colspan: 5, class: "text-muted" }, "No jobs found for this project.")));
      return;
    }

    state.jobs.forEach(j => {
      const tr = h("tr", {
        onClick: () => inspectJob(j.job_id)
      },
        h("td", {}, j.job_id),
        h("td", {}, h("span", { class: `pill ${getJobStatusClass(j.state)}` }, j.state)),
        h("td", {}, (j.stages || []).join(", ")),
        h("td", {}, j.created_at || "-"),
        h("td", {},
          h("button", {
            type: "button",
            class: "btn btn-sm",
            onClick: (e) => {
              e.stopPropagation();
              inspectJob(j.job_id);
            }
          }, "Inspect")
        )
      );
      tbody.appendChild(tr);
    });
  }

  function getJobStatusClass(stateName) {
    if (stateName === "completed") return "pill-ok";
    if (stateName === "failed" || stateName === "cancelled") return "pill-bad";
    if (stateName === "paused" || stateName === "completed_with_warnings") return "pill-warn";
    return "";
  }

  async function inspectJob(jobId) {
    const inspectProjectId = state.currentProjectId;
    if (!inspectProjectId) return;
    state.currentJobId = jobId;
    try {
      const data = await api(`/api/projects/${inspectProjectId}/jobs/${jobId}`);
      if (state.currentProjectId !== inspectProjectId) return;
      state.currentJob = data;
      renderJobDetail();
      updateMatchingJobSummary(data);

      // Start polling if not terminal
      if (!isTerminalState(data.state)) {
        scheduleJobPoll(jobId, data.next_poll_after_ms || 1000);
      }
    } catch (err) {
      if (state.currentProjectId !== inspectProjectId) return;
      showToast(`Failed loading job: ${err.message}`, "bad");
    }
  }

  function isTerminalState(s) {
    return ["completed", "completed_with_warnings", "failed", "cancelled"].includes(s);
  }

  function renderJobDetail() {
    const j = state.currentJob;
    if (!j) return;

    const card = document.getElementById("job-detail-card");
    card.style.display = "block";
    document.getElementById("job-detail-title").textContent = `Job: ${j.job_id} [${j.state}]`;

    // Stage counts breakdown
    const breakdownBox = document.getElementById("job-stages-breakdown");
    clearElement(breakdownBox);

    (j.stages || []).forEach(stage => {
      // NOTE: stage.queued already includes stage.retry_wait.
      // Include failed_retryable once. Do not add retry_wait.
      const totalStageItems = (stage.queued || 0) + (stage.running || 0) + (stage.succeeded || 0) +
                              (stage.failed_retryable || 0) + (stage.failed_permanent || 0) +
                              (stage.skipped || 0) + (stage.cancelled || 0);

      const stageCard = h("div", { class: "count-card" },
        h("div", { class: "count-card-title" }, `${stage.name} (Total: ${totalStageItems})`),
        h("div", {}, `Queued: ${stage.queued} (includes ${stage.retry_wait || 0} in retry wait)`),
        h("div", {}, `Running: ${stage.running}`),
        h("div", {}, `Succeeded: ${stage.succeeded}`),
        h("div", {}, `Failed (Permanent): ${stage.failed_permanent}`),
        h("div", {}, `Failed (Retryable): ${stage.failed_retryable}`),
        h("div", {}, `Skipped: ${stage.skipped}`),
        h("div", {}, `Cancelled: ${stage.cancelled || 0}`)
      );
      breakdownBox.appendChild(stageCard);
    });

    // Warnings
    const warnBox = document.getElementById("job-warnings-box");
    if (j.warnings && j.warnings.length > 0) {
      warnBox.style.display = "block";
      clearElement(warnBox);
      warnBox.appendChild(h("strong", {}, "Warnings:"));
      const ul = h("ul", {});
      j.warnings.forEach(w => ul.appendChild(h("li", {}, w)));
      warnBox.appendChild(ul);
    } else {
      warnBox.style.display = "none";
    }

    // Results
    const resBox = document.getElementById("job-results-box");
    if (j.results) {
      resBox.style.display = "block";
      clearElement(resBox);
      resBox.appendChild(h("h4", {}, "Job Results:"));
      resBox.appendChild(h("p", {}, `Occurrences Processed: ${j.results.occurrence_count}`));
      if (j.results.artifact_ids && j.results.artifact_ids.length > 0) {
        resBox.appendChild(h("p", {}, `Artifacts Generated: ${j.results.artifact_ids.join(", ")}`));
      }
    } else {
      resBox.style.display = "none";
    }
  }

  // --- JOB POLLING MANAGER ---
  function scheduleJobPoll(jobId, delayMs) {
    if (state.activeJobs.has(jobId)) {
      clearTimeout(state.activeJobs.get(jobId).timer);
    }

    // Bound poll interval between 250 ms and 30000 ms
    const boundedDelay = Math.max(250, Math.min(30000, delayMs));

    const pollMeta = {
      jobId,
      delay: boundedDelay,
      timer: setTimeout(() => executeJobPoll(jobId), boundedDelay)
    };
    state.activeJobs.set(jobId, pollMeta);
  }

  async function executeJobPoll(jobId) {
    if (document.hidden) {
      // Pause poll when page hidden
      return;
    }

    const pollProjectId = state.currentProjectId;
    if (!pollProjectId) return;

    try {
      const data = await api(`/api/projects/${pollProjectId}/jobs/${jobId}`);
      if (state.currentProjectId !== pollProjectId) return;

      if (state.currentJobId === jobId) {
        state.currentJob = data;
        renderJobDetail();
      }

      updateMatchingJobSummary(data);

      if (isTerminalState(data.state)) {
        state.activeJobs.delete(jobId);
        showToast(`Job ${jobId} finished with status: ${data.state}`, data.state === "completed" ? "ok" : "warn");
        await loadProjectDetail(pollProjectId);
        await loadReferences();
        if (state.currentProjectId === pollProjectId) {
          await loadJobs();
        }
      } else {
        const nextDelay = data.state === "paused" ? 5000 : (data.next_poll_after_ms || 1000);
        scheduleJobPoll(jobId, nextDelay);
      }
    } catch (err) {
      if (state.currentProjectId !== pollProjectId) return;
      console.warn(`Polling error for job ${jobId}:`, err.message);
      // Exponential backoff
      const currentMeta = state.activeJobs.get(jobId);
      const nextDelay = Math.min(30000, (currentMeta?.delay || 1000) * 2);
      scheduleJobPoll(jobId, nextDelay);
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      // Resume polling immediately when tab becomes visible
      for (const [jobId] of state.activeJobs.entries()) {
        executeJobPoll(jobId);
      }
    }
  });

  // --- JOB CONTROLS ---
  async function sendJobControl(action) {
    if (!state.currentJobId) return;
    try {
      const out = await api(`/api/projects/${state.currentProjectId}/jobs/${state.currentJobId}/control`, {
        method: "POST",
        body: {
          project_id: state.currentProjectId,
          job_id: state.currentJobId,
          action,
          idempotency_key: makeIdempotencyKey()
        }
      });
      showToast(`Action '${action}' accepted. New state: ${out.state}`, "ok");
      await inspectJob(state.currentJobId);
    } catch (err) {
      showToast(`Job control error: ${err.message}`, "bad");
    }
  }

  document.getElementById("btn-job-pause").addEventListener("click", () => sendJobControl("pause"));
  document.getElementById("btn-job-resume").addEventListener("click", () => sendJobControl("resume"));
  document.getElementById("btn-job-cancel").addEventListener("click", () => {
    if (confirm("Are you sure you want to cancel this job?")) sendJobControl("cancel");
  });
  document.getElementById("btn-job-retry").addEventListener("click", () => sendJobControl("retry_failed"));

  // --- START JOB MODAL ---
  document.getElementById("btn-open-start-job").addEventListener("click", async () => {
    if (!state.currentProjectId) {
      showToast("Select a project first.", "warn");
      return;
    }
    await loadSources();
    const sourceSelect = document.getElementById("start-job-source-id");
    clearElement(sourceSelect);
    state.sources.forEach(s => {
      sourceSelect.appendChild(h("option", { value: s.source_id }, `${s.label || s.source_id} (${s.occurrence_count} refs)`));
    });
    openModal("modal-start-job");
  });

  document.getElementById("start-job-scope-type").addEventListener("change", (e) => {
    const isSource = e.target.value === "source";
    document.getElementById("job-scope-source-group").style.display = isSource ? "block" : "none";
    document.getElementById("job-scope-occ-group").style.display = isSource ? "none" : "block";
  });

  document.getElementById("form-start-job").addEventListener("submit", async (e) => {
    e.preventDefault();
    const stages = Array.from(document.querySelectorAll("input[name='job-stage']:checked")).map(cb => cb.value);
    if (stages.length === 0) {
      showToast("Select at least one stage.", "warn");
      return;
    }

    const scopeType = document.getElementById("start-job-scope-type").value;
    let scope = null;
    if (scopeType === "source") {
      const sourceId = document.getElementById("start-job-source-id").value;
      if (!sourceId) {
        showToast("Select a source.", "warn");
        return;
      }
      scope = { source_ids: [sourceId] };
    } else {
      const idsRaw = document.getElementById("start-job-occ-ids").value.trim();
      const ids = idsRaw.split(",").map(s => s.trim()).filter(Boolean);
      if (ids.length === 0) {
        showToast("Provide at least one occurrence ID.", "warn");
        return;
      }
      scope = { occurrence_ids: ids };
    }

    const refresh = document.getElementById("start-job-refresh").checked;

    try {
      const out = await api(`/api/projects/${state.currentProjectId}/jobs`, {
        method: "POST",
        body: {
          project_id: state.currentProjectId,
          stages,
          scope,
          options: { refresh },
          idempotency_key: makeIdempotencyKey()
        }
      });
      showToast(`Job queued: ${out.job_id}`, "ok");
      closeModal("modal-start-job");
      await loadJobs();
      inspectJob(out.job_id);
    } catch (err) {
      showToast(`Failed starting job: ${err.message}`, "bad");
    }
  });

  // --- FILTER BUILDER ---
  function buildActiveReferenceFilter() {
    const f = state.referenceFilter;
    const filter = {};
    if (f.source_id) filter.source_id = f.source_id;
    if (f.job_id) filter.job_id = f.job_id;
    if (f.extraction) filter.extraction = f.extraction;
    if (f.identity) filter.identity = f.identity;
    if (f.discovery) filter.discovery = f.discovery;
    if (f.classification) {
      if (f.classification === "__null__") filter.classification_is_null = true;
      else filter.classification = f.classification;
    }
    if (f.local_copy) filter.local_copy = f.local_copy;
    if (f.retrieval) filter.retrieval = f.retrieval;
    if (f.reason) filter.reason = f.reason;
    return filter;
  }

  // --- REFERENCES LOADER & MASTER-DETAIL ---
  async function loadReferences(cursor = null) {
    if (!state.currentProjectId) {
      clearElement(document.getElementById("tbody-references"));
      document.getElementById("tbody-references").appendChild(
        h("tr", {}, h("td", { colspan: 7, class: "text-muted" }, "Select a project first."))
      );
      return;
    }

    const params = new URLSearchParams();
    params.set("limit", "50");
    if (cursor) params.set("cursor", cursor);

    const f = state.referenceFilter;
    if (f.query) params.set("query", f.query.slice(0, 500));
    if (f.sort) params.set("sort", f.sort);
    if (f.reason) params.set("reason", f.reason);
    if (f.extraction) params.set("extraction", f.extraction);
    if (f.identity) params.set("identity", f.identity);
    if (f.discovery) params.set("discovery", f.discovery);
    if (f.classification) {
      if (f.classification === "__null__") {
        params.set("classification_is_null", "true");
      } else {
        params.set("classification", f.classification);
      }
    }
    if (f.local_copy) params.set("local_copy", f.local_copy);
    if (f.retrieval) params.set("retrieval", f.retrieval);
    if (f.source_id) params.set("source_id", f.source_id);
    if (f.job_id) params.set("job_id", f.job_id);

    try {
      const data = await api(`/api/projects/${state.currentProjectId}/references?${params.toString()}`);
      state.references = data.occurrences || [];
      state.referencesCursor = data.next_cursor;
      state.referencesTotal = data.total || 0;

      renderReferencesTable();
      updateFilterSummary();
    } catch (err) {
      showToast(`Failed loading references: ${err.message}`, "bad");
    }
  }

  function renderReferencesTable() {
    const tbody = document.getElementById("tbody-references");
    clearElement(tbody);

    if (state.references.length === 0) {
      tbody.appendChild(h("tr", {}, h("td", { colspan: 7, class: "text-muted" }, "No reference occurrences match current filters.")));
      return;
    }

    state.references.forEach((r, idx) => {
      const isSelected = r.occurrence_id === state.selectedReferenceId;
      const isChecked = state.checkedReferenceIds.has(r.occurrence_id);

      const chk = h("input", {
        type: "checkbox",
        checked: isChecked,
        onClick: (e) => {
          e.stopPropagation();
          if (e.target.checked) state.checkedReferenceIds.add(r.occurrence_id);
          else state.checkedReferenceIds.delete(r.occurrence_id);
          updateSelectionScope();
        }
      });

      const tr = h("tr", {
        class: isSelected ? "row-selected" : "",
        onClick: () => inspectReference(r.occurrence_id)
      },
        h("td", {}, chk),
        h("td", {}, String(idx + 1)),
        h("td", {}, r.edited_text || r.raw_text),
        h("td", {}, h("span", { class: "badge" }, r.extraction)),
        h("td", {}, h("span", { class: `badge ${getIdentityClass(r.identity?.state)}` }, r.identity?.state || "-")),
        h("td", {}, h("span", { class: "badge" }, r.access?.discovery || "-")),
        h("td", {}, h("span", { class: "badge" }, r.local_copy || "-"))
      );
      tbody.appendChild(tr);
    });
  }

  function getIdentityClass(stateName) {
    if (stateName === "accepted_auto" || stateName === "accepted_user") return "badge-ok";
    if (stateName === "needs_review") return "badge-warn";
    if (stateName === "unresolved" || stateName === "rejected") return "badge-bad";
    return "";
  }

  function updateFilterSummary() {
    const hasQuery = Boolean(state.referenceFilter.query && state.referenceFilter.query.trim());
    const btnAll = document.getElementById("btn-download-all-filter");
    const warnQuery = document.getElementById("query-filter-warning");

    document.getElementById("filter-summary-text").textContent = `Total matches: ${state.referencesTotal}`;
    document.getElementById("btn-refs-next").disabled = !state.referencesCursor;

    if (hasQuery) {
      btnAll.disabled = true;
      btnAll.title = "All-filter retrieval does not support text search queries. Clear search or select occurrences on page.";
      if (warnQuery) warnQuery.style.display = "inline";
    } else {
      btnAll.disabled = state.referencesTotal === 0;
      btnAll.title = state.referencesTotal === 0
        ? "No matching occurrences"
        : "Queue retrieval for all occurrences matching active filters";
      if (warnQuery) warnQuery.style.display = "none";
    }
  }

  function updateSelectionScope() {
    const count = state.checkedReferenceIds.size;
    document.getElementById("selection-scope-text").textContent = `Selected on page: ${count}`;
    document.getElementById("btn-download-selected").disabled = count === 0;
    document.getElementById("btn-draft-selected").disabled = count === 0;
  }

  document.getElementById("chk-select-all-page").addEventListener("change", (e) => {
    const checked = e.target.checked;
    state.references.forEach(r => {
      if (checked) state.checkedReferenceIds.add(r.occurrence_id);
      else state.checkedReferenceIds.delete(r.occurrence_id);
    });
    renderReferencesTable();
    updateSelectionScope();
  });

  // Filter input bindings with debounce for search query
  let searchDebounceTimer = null;
  document.getElementById("filter-query").addEventListener("input", (e) => {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => {
      state.referenceFilter.query = e.target.value.trim();
      loadReferences();
    }, 300);
  });

  document.getElementById("filter-sort").addEventListener("change", (e) => {
    state.referenceFilter.sort = e.target.value;
    loadReferences();
  });

  document.getElementById("filter-reason").addEventListener("change", (e) => {
    state.referenceFilter.reason = e.target.value.trim();
    loadReferences();
  });

  const dimensionSelectIds = [
    { el: "filter-extraction", key: "extraction" },
    { el: "filter-identity", key: "identity" },
    { el: "filter-discovery", key: "discovery" },
    { el: "filter-classification", key: "classification" },
    { el: "filter-local-copy", key: "local_copy" },
    { el: "filter-retrieval", key: "retrieval" }
  ];

  dimensionSelectIds.forEach(d => {
    document.getElementById(d.el).addEventListener("change", (e) => {
      state.referenceFilter[d.key] = e.target.value;
      loadReferences();
    });
  });

  document.getElementById("btn-reset-filters").addEventListener("click", () => {
    document.getElementById("filter-query").value = "";
    document.getElementById("filter-reason").value = "";
    dimensionSelectIds.forEach(d => { document.getElementById(d.el).value = ""; });
    state.referenceFilter = {
      query: "",
      sort: "order",
      reason: "",
      extraction: "",
      identity: "",
      discovery: "",
      classification: "",
      classification_is_null: false,
      local_copy: "",
      retrieval: "",
      source_id: "",
      job_id: ""
    };
    loadReferences();
  });

  // --- REFERENCE DETAIL INSPECTOR ---
  async function inspectReference(occurrenceId) {
    state.selectedReferenceId = occurrenceId;
    renderReferencesTable();

    try {
      const data = await api(
        `/api/projects/${state.currentProjectId}/references/${occurrenceId}?sections=candidates&sections=evidence&sections=access&sections=local_copies&sections=files&sections=history`
      );
      state.selectedReference = data;
      renderReferenceDetail();
    } catch (err) {
      showToast(`Failed loading reference detail: ${err.message}`, "bad");
    }
  }

  function renderReferenceDetail() {
    const data = state.selectedReference;
    if (!data) return;

    document.getElementById("reference-detail-empty").style.display = "none";
    document.getElementById("reference-detail-content").style.display = "block";

    const occ = data.occurrence;
    document.getElementById("ref-detail-title").textContent = `Occurrence: ${occ.occurrence_id}`;
    document.getElementById("ref-detail-rev-badge").textContent = `Rev ${occ.revision}`;

    // Citation Text
    document.getElementById("ref-detail-raw").textContent = occ.raw_text;
    const editedBox = document.getElementById("ref-detail-edited-box");
    if (occ.edited_text) {
      editedBox.style.display = "block";
      document.getElementById("ref-detail-edited").textContent = occ.edited_text;
    } else {
      editedBox.style.display = "none";
    }

    // Spans
    const spansBox = document.getElementById("ref-detail-spans");
    clearElement(spansBox);
    if (occ.spans && occ.spans.length > 0) {
      occ.spans.forEach(s => {
        const item = h("div", { class: "span-item" },
          h("span", {}, `Page ${s.page_index} (${s.page_label || "No label"}) [BBox: ${(s.bbox || []).map(n => typeof n === "number" ? n.toFixed(3) : n).join(", ")}]`),
          h("button", {
            type: "button",
            class: "btn btn-sm btn-primary",
            onClick: () => openSpanPreview(occ, s)
          }, "Preview Span")
        );
        spansBox.appendChild(item);
      });
    } else {
      spansBox.textContent = "No spans recorded.";
    }

    // Work Metadata
    const workBox = document.getElementById("ref-detail-work");
    clearElement(workBox);
    if (data.work) {
      const w = data.work;
      const authorsStr = (w.authors || []).map(a => typeof a === "string" ? a : (a.family ? `${a.family}, ${a.given || ""}` : a.literal || "")).join("; ");
      workBox.appendChild(h("dl", { class: "meta-list" },
        h("dt", {}, "Title"), h("dd", {}, w.title || "-"),
        h("dt", {}, "Authors"), h("dd", {}, authorsStr || "-"),
        h("dt", {}, "Year"), h("dd", {}, String(w.year || "-")),
        h("dt", {}, "Venue"), h("dd", {}, w.venue || "-"),
        h("dt", {}, "DOI"), h("dd", {}, w.doi || "-"),
        h("dt", {}, "URL"), h("dd", {}, w.url || "-")
      ));
    } else {
      workBox.textContent = "No work metadata matched.";
    }

    // Candidates
    const candidatesBox = document.getElementById("ref-detail-candidates");
    clearElement(candidatesBox);
    if (data.candidates && data.candidates.length > 0) {
      data.candidates.forEach(c => {
        const item = h("div", { class: `candidate-item ${c.rejected ? "rejected" : ""}` },
          h("div", { class: "candidate-header" },
            h("strong", {}, `Rank #${c.rank}: ${c.metadata?.title || "Untitled"}`),
            h("span", { class: "badge" }, c.rejected ? "Rejected" : "Available")
          ),
          h("p", { class: "text-muted" }, (c.reasons || []).join(" · ") || "No reason sentences."),
          h("div", { class: "actions mt-2", style: { display: "flex", flexWrap: "wrap", gap: "6px" } },
            h("button", {
              type: "button",
              class: "btn btn-sm btn-review-accept",
              title: "Two-step trusted user review approval (accepted_user)",
              onClick: () => startTrustedReview(occ, c, "accept")
            }, "Review & Approve (User)"),
            h("button", {
              type: "button",
              class: "btn btn-sm btn-review-reject",
              title: "Two-step trusted user review rejection",
              onClick: () => startTrustedReview(occ, c, "reject")
            }, "Review & Reject (User)"),
            h("button", {
              type: "button",
              class: "btn btn-sm",
              title: "Ordinary agent candidate selection",
              onClick: () => executeSelectCandidate(occ.occurrence_id, c.candidate_id, occ.revision)
            }, "Select (Agent)"),
            h("button", {
              type: "button",
              class: "btn btn-sm",
              title: "Ordinary agent candidate rejection",
              onClick: () => executeRejectCandidate(occ.occurrence_id, c.candidate_id, occ.revision)
            }, "Reject (Agent)")
          )
        );
        candidatesBox.appendChild(item);
      });
    } else {
      candidatesBox.textContent = "No candidates found.";
    }

    // Evidence
    const evidenceBox = document.getElementById("ref-detail-evidence");
    clearElement(evidenceBox);
    if (data.evidence && data.evidence.length > 0) {
      const ul = h("ul", { class: "simple-list" });
      data.evidence.forEach(ev => {
        ul.appendChild(h("li", {}, `Provider: ${ev.provider} | Record Key: ${ev.record_key} | Fetched: ${ev.fetched_at}`));
      });
      evidenceBox.appendChild(ul);
    } else {
      evidenceBox.textContent = "No evidence records.";
    }

    // Access History & Locations
    const accessBox = document.getElementById("ref-detail-access");
    clearElement(accessBox);
    if (data.access && data.access.length > 0) {
      data.access.forEach(acc => {
        const accItem = h("div", { class: "meta-box mt-2" },
          h("div", {}, `Discovery: ${acc.discovery} | Classification: ${acc.classification || "unclassified"} | Checked: ${acc.checked_at}`),
          h("div", { class: "text-muted mt-1" }, `Consulted: ${(acc.sources_consulted || []).join(", ") || "None"}`)
        );
        if (acc.locations && acc.locations.length > 0) {
          const locList = h("ul", { class: "simple-list mt-1" });
          acc.locations.forEach(loc => {
            locList.appendChild(h("li", {}, `URL: ${loc.url} (${loc.host_type || "unknown"}) - PDF: ${loc.is_pdf ? "Yes" : "No"}`));
          });
          accItem.appendChild(locList);
        }
        accessBox.appendChild(accItem);
      });
    } else {
      accessBox.textContent = "No access checks recorded.";
    }

    // Local Copies & Files
    const filesBox = document.getElementById("ref-detail-files");
    clearElement(filesBox);
    if (data.local_copies && data.local_copies.length > 0) {
      data.local_copies.forEach(lc => {
        const item = h("div", { class: "meta-box mt-2" },
          h("div", {}, `Copy ID: ${lc.local_copy_id} | State: ${lc.state} | SHA256: ${(lc.sha256 || "").slice(0, 16)}...`),
          h("div", { class: "actions mt-2" },
            h("button", {
              type: "button",
              class: "btn btn-sm btn-primary",
              onClick: () => downloadProjectFile(state.currentProjectId, "local_copy", lc.local_copy_id, "paper.pdf")
            }, "Download Copy")
          )
        );
        filesBox.appendChild(item);
      });
    } else {
      filesBox.textContent = "No local copies attached.";
    }

    // Audit Trail
    const histBox = document.getElementById("ref-detail-history");
    clearElement(histBox);
    if (data.history && data.history.length > 0) {
      const ul = h("ul", { class: "simple-list" });
      data.history.forEach(item => {
        if (item.kind === "decision") {
          ul.appendChild(h("li", {}, `Decision: ${item.action} on ${item.candidate_id || ""} at ${item.at} (${item.provenance?.channel || ""})`));
        } else {
          ul.appendChild(h("li", {}, `Audit [${item.type}]: at ${item.at} (Rev ${item.project_revision})`));
        }
      });
      histBox.appendChild(ul);
    } else {
      histBox.textContent = "No audit events recorded.";
    }
  }

  // --- ASYNC STALE EDIT PRESERVATION & REPAIR ---
  async function executeUpdateReference(change) {
    try {
      await api(`/api/projects/${state.currentProjectId}/references/update`, {
        method: "POST",
        body: {
          project_id: state.currentProjectId,
          change,
          idempotency_key: makeIdempotencyKey()
        }
      });
      showToast("Reference update saved.", "ok");
      closeModal("modal-repair-reference");
      state.activeRepair = null;
      document.getElementById("repair-conflict-alert").style.display = "none";
      await inspectReference(state.selectedReferenceId);
      await loadReferences();
      if (state.currentProjectId) await loadProjectDetail(state.currentProjectId);
    } catch (err) {
      if (err.status === 409) {
        showToast("Conflict: The reference was changed on the server.", "bad");
        await handleStaleConflict(change);
      } else {
        showToast(`Repair failed: ${err.message}`, "bad");
      }
    }
  }

  async function handleStaleConflict(change) {
    try {
      const freshData = await api(
        `/api/projects/${state.currentProjectId}/references/${change.occurrence_id}?sections=candidates&sections=evidence&sections=access`
      );
      const freshOcc = freshData.occurrence;
      const freshRev = freshOcc.revision;

      // Make sure the repair modal is open to present the comparison
      const modal = document.getElementById("modal-repair-reference");
      if (!modal.hasAttribute("open")) {
        setupRepairModal(`Conflict: ${change.type} on ${change.occurrence_id}`, (c) => {
          c.appendChild(h("p", { class: "text-muted" }, `Operation: ${change.type}`));
        });
      }

      const alertBox = document.getElementById("repair-conflict-alert");
      alertBox.style.display = "block";
      clearElement(alertBox);

      alertBox.appendChild(h("strong", {}, "Stale Revision Conflict Detected: "));
      alertBox.appendChild(document.createTextNode(
        `Server is at revision ${freshRev} (your edit expected revision was ${change.expected_revision ?? "unknown"}). `
      ));

      // Present current server data for comparison
      const serverBox = h("div", { class: "meta-box mt-2 mb-2" },
        h("strong", {}, "Current Server Record: ")
      );

      if (change.type === "edit_text" || change.type === "split") {
        serverBox.appendChild(h("p", { class: "mt-1 mb-1 text-muted" }, "Server Citation Text:"));
        serverBox.appendChild(h("pre", { class: "code-block cite-serif" },freshOcc.edited_text || freshOcc.raw_text));
        serverBox.appendChild(h("p", { class: "text-muted" }, "Your unsaved edits remain in the form below. Compare before rebasing."));
      } else if (change.type === "select_candidate" || change.type === "reject_candidate") {
        serverBox.appendChild(h("p", { class: "mt-1 mb-1 text-muted" },
          `Target Candidate ID: ${change.candidate_id} | Server Identity: ${freshOcc.identity?.state || "unknown"}`
        ));
        serverBox.appendChild(h("p", { class: "notice-warning mt-1" },
          "Warning: Stale candidate selection cannot silently rebase. Verify candidate ID and reasons against current server candidates before confirming."
        ));
      } else if (change.type === "merge") {
        serverBox.appendChild(h("p", { class: "notice-warning mt-1" },
          "Warning: Stale merge cannot silently rebase. Occurrences or revisions were modified concurrently on the server. Inspect all occurrence revisions before confirming."
        ));
      } else if (change.type === "set_fields" || change.type === "set_doi" || change.type === "set_field_locks") {
        serverBox.appendChild(h("p", { class: "mt-1 mb-1 text-muted" },
          `Server DOI: ${freshData.work?.doi || "none"} | Title: ${freshData.work?.title || "none"}`
        ));
      } else {
        serverBox.appendChild(h("p", { class: "mt-1 mb-1 text-muted" },
          `Server State: extraction=${freshOcc.extraction}, identity=${freshOcc.identity?.state}`
        ));
      }
      alertBox.appendChild(serverBox);

      alertBox.appendChild(h("p", { class: "text-muted mb-2" },
        "Your unsaved edits remain in the form fields below. Compare them with the server state above."
      ));

      // Explicit rebase confirmation button (Do not change expected_revision automatically, do not auto-resubmit)
      const confirmBtn = h("button", {
        type: "button",
        class: "btn btn-sm btn-warn mt-1",
        onClick: () => {
          if (change.expected_revision !== undefined) {
            change.expected_revision = freshRev;
          }
          if (change.expected_revisions && typeof change.expected_revisions === "object") {
            change.expected_revisions[change.occurrence_id] = freshRev;
          }
          confirmBtn.disabled = true;
          confirmBtn.textContent = `Rebased to Revision ${freshRev}`;
          showToast(`Expected revision updated to ${freshRev}. Review your form and click 'Save Repair'.`, "ok");
          announce(`Expected revision updated to ${freshRev}. Click Save Repair when ready.`);
        }
      }, `Confirm and Rebase to Server Revision ${freshRev}`);

      alertBox.appendChild(confirmBtn);
      announce(`Conflict warning: Server is at revision ${freshRev}. Review server data and confirm rebase.`);
    } catch (fetchErr) {
      console.warn("Failed fetching fresh reference on conflict:", fetchErr.message);
    }
  }

  async function executeSelectCandidate(occurrenceId, candidateId, rev) {
    const note = prompt("Optional note for candidate selection:");
    if (note === null) return;
    const changeObj = {
      type: "select_candidate",
      occurrence_id: occurrenceId,
      candidate_id: candidateId,
      note: note.trim() || null,
      expected_revision: rev
    };
    setupRepairModal("Select Candidate", (c) => {
      c.appendChild(h("div", { class: "form-group" },
        h("p", {}, `Candidate ID: ${candidateId}`),
        h("p", {}, `Expected Revision: ${rev}`),
        h("p", { class: "text-muted" }, note ? `Note: ${note}` : "No note provided.")
      ));
      document.getElementById("form-repair-reference").onsubmit = (e) => {
        e.preventDefault();
        executeUpdateReference(changeObj);
      };
    });
    await executeUpdateReference(changeObj);
  }

  async function executeRejectCandidate(occurrenceId, candidateId, rev) {
    const note = prompt("Optional note for candidate rejection:");
    if (note === null) return;
    const changeObj = {
      type: "reject_candidate",
      occurrence_id: occurrenceId,
      candidate_id: candidateId,
      note: note.trim() || null,
      expected_revision: rev
    };
    setupRepairModal("Reject Candidate", (c) => {
      c.appendChild(h("div", { class: "form-group" },
        h("p", {}, `Candidate ID: ${candidateId}`),
        h("p", {}, `Expected Revision: ${rev}`),
        h("p", { class: "text-muted" }, note ? `Note: ${note}` : "No note provided.")
      ));
      document.getElementById("form-repair-reference").onsubmit = (e) => {
        e.preventDefault();
        executeUpdateReference(changeObj);
      };
    });
    await executeUpdateReference(changeObj);
  }

  // Repair Modals Setup
  function setupRepairModal(title, buildFormFn) {
    document.getElementById("modal-repair-title").textContent = title;
    document.getElementById("repair-conflict-alert").style.display = "none";
    const container = document.getElementById("repair-fields-container");
    clearElement(container);
    buildFormFn(container);
    openModal("modal-repair-reference");
  }

  document.getElementById("btn-action-edit-text").addEventListener("click", () => {
    if (!state.selectedReference) return;
    const occ = state.selectedReference.occurrence;
    setupRepairModal("Edit Citation Text", (c) => {
      c.appendChild(h("div", { class: "form-group" },
        h("label", { for: "repair-edit-text" }, "Citation Text:"),
        h("textarea", { id: "repair-edit-text", rows: 6, required: true }, occ.edited_text || occ.raw_text)
      ));
      const changeObj = {
        type: "edit_text",
        occurrence_id: occ.occurrence_id,
        text: "",
        expected_revision: occ.revision
      };
      document.getElementById("form-repair-reference").onsubmit = (e) => {
        e.preventDefault();
        changeObj.text = document.getElementById("repair-edit-text").value.trim();
        executeUpdateReference(changeObj);
      };
    });
  });

  document.getElementById("btn-action-split").addEventListener("click", () => {
    if (!state.selectedReference) return;
    const occ = state.selectedReference.occurrence;
    setupRepairModal("Split Reference", (c) => {
      c.appendChild(h("p", { class: "text-muted" }, "Provide 2 to 20 reference parts (one per line):"));
      c.appendChild(h("div", { class: "form-group" },
        h("textarea", { id: "repair-split-parts", rows: 8, required: true }, occ.edited_text || occ.raw_text)
      ));
      const changeObj = {
        type: "split",
        occurrence_id: occ.occurrence_id,
        parts: [],
        expected_revision: occ.revision
      };
      document.getElementById("form-repair-reference").onsubmit = (e) => {
        e.preventDefault();
        const lines = document.getElementById("repair-split-parts").value.split("\n").map(l => l.trim()).filter(Boolean);
        if (lines.length < 2 || lines.length > 20) {
          showToast("Split requires between 2 and 20 parts.", "warn");
          return;
        }
        changeObj.parts = lines.map(t => ({ text: t }));
        executeUpdateReference(changeObj);
      };
    });
  });

  document.getElementById("btn-action-merge").addEventListener("click", () => {
    if (!state.selectedReference) return;
    const occ = state.selectedReference.occurrence;
    setupRepairModal("Merge References", (c) => {
      c.appendChild(h("div", { class: "form-group" },
        h("label", { for: "repair-merge-ids" }, "Comma-separated Occurrence IDs (2 to 10):"),
        h("input", { type: "text", id: "repair-merge-ids", value: occ.occurrence_id, required: true })
      ));
      c.appendChild(h("div", { class: "form-group" },
        h("label", { for: "repair-merge-text" }, "Combined Citation Text (Optional):"),
        h("textarea", { id: "repair-merge-text", rows: 4 }, occ.edited_text || occ.raw_text)
      ));
      const changeObj = {
        type: "merge",
        occurrence_ids: [],
        text: null,
        expected_revisions: {}
      };
      document.getElementById("form-repair-reference").onsubmit = (e) => {
        e.preventDefault();
        const ids = document.getElementById("repair-merge-ids").value.split(",").map(s => s.trim()).filter(Boolean);
        if (ids.length < 2 || ids.length > 10) {
          showToast("Merge requires 2 to 10 occurrence IDs.", "warn");
          return;
        }
        const revMap = {};
        ids.forEach(id => { revMap[id] = id === occ.occurrence_id ? occ.revision : 0; });
        changeObj.occurrence_ids = ids;
        changeObj.text = document.getElementById("repair-merge-text").value.trim() || null;
        changeObj.expected_revisions = revMap;
        executeUpdateReference(changeObj);
      };
    });
  });

  document.getElementById("btn-action-exclude").addEventListener("click", () => {
    if (!state.selectedReference) return;
    const occ = state.selectedReference.occurrence;
    setupRepairModal("Exclude Reference", (c) => {
      c.appendChild(h("div", { class: "form-group" },
        h("label", { for: "repair-exclude-reason" }, "Reason for exclusion:"),
        h("input", { type: "text", id: "repair-exclude-reason", placeholder: "e.g. not a bibliographic citation" })
      ));
      const changeObj = {
        type: "exclude",
        occurrence_id: occ.occurrence_id,
        reason: null,
        expected_revision: occ.revision
      };
      document.getElementById("form-repair-reference").onsubmit = (e) => {
        e.preventDefault();
        changeObj.reason = document.getElementById("repair-exclude-reason").value.trim() || null;
        executeUpdateReference(changeObj);
      };
    });
  });

  document.getElementById("btn-action-restore").addEventListener("click", () => {
    if (!state.selectedReference) return;
    const occ = state.selectedReference.occurrence;
    setupRepairModal("Restore Reference", (c) => {
      c.appendChild(h("div", { class: "form-group" },
        h("label", { for: "repair-restore-reason" }, "Reason for restore:"),
        h("input", { type: "text", id: "repair-restore-reason", placeholder: "e.g. incorrectly excluded" })
      ));
      const changeObj = {
        type: "restore",
        occurrence_id: occ.occurrence_id,
        reason: null,
        expected_revision: occ.revision
      };
      document.getElementById("form-repair-reference").onsubmit = (e) => {
        e.preventDefault();
        changeObj.reason = document.getElementById("repair-restore-reason").value.trim() || null;
        executeUpdateReference(changeObj);
      };
    });
  });

  document.getElementById("btn-action-set-doi").addEventListener("click", () => {
    if (!state.selectedReference) return;
    const occ = state.selectedReference.occurrence;
    setupRepairModal("Set Known DOI", (c) => {
      c.appendChild(h("div", { class: "form-group" },
        h("label", { for: "repair-doi-val" }, "DOI:"),
        h("input", { type: "text", id: "repair-doi-val", placeholder: "10.xxxx/xxxxx", required: true, minlength: 7, maxlength: 300 })
      ));
      const changeObj = {
        type: "set_doi",
        occurrence_id: occ.occurrence_id,
        doi: "",
        expected_revision: occ.revision
      };
      document.getElementById("form-repair-reference").onsubmit = (e) => {
        e.preventDefault();
        changeObj.doi = document.getElementById("repair-doi-val").value.trim();
        executeUpdateReference(changeObj);
      };
    });
  });

  document.getElementById("btn-action-set-fields").addEventListener("click", () => {
    if (!state.selectedReference) return;
    const occ = state.selectedReference.occurrence;
    const w = state.selectedReference.work || {};
    setupRepairModal("Set Work Fields", (c) => {
      c.appendChild(h("div", { class: "form-group" },
        h("label", { for: "repair-field-title" }, "Title:"),
        h("input", { type: "text", id: "repair-field-title", value: w.title || "" })
      ));
      c.appendChild(h("div", { class: "form-group" },
        h("label", { for: "repair-field-year" }, "Year:"),
        h("input", { type: "number", id: "repair-field-year", value: w.year || "" })
      ));
      c.appendChild(h("div", { class: "form-group" },
        h("label", { for: "repair-field-venue" }, "Venue:"),
        h("input", { type: "text", id: "repair-field-venue", value: w.venue || "" })
      ));
      const changeObj = {
        type: "set_fields",
        occurrence_id: occ.occurrence_id,
        fields: {},
        expected_revision: occ.revision
      };
      document.getElementById("form-repair-reference").onsubmit = (e) => {
        e.preventDefault();
        const fields = {};
        const title = document.getElementById("repair-field-title").value.trim();
        const year = document.getElementById("repair-field-year").value.trim();
        const venue = document.getElementById("repair-field-venue").value.trim();
        if (title) fields.title = title;
        if (year) fields.year = parseInt(year, 10);
        if (venue) fields.venue = venue;

        changeObj.fields = fields;
        executeUpdateReference(changeObj);
      };
    });
  });

  document.getElementById("btn-action-set-locks").addEventListener("click", () => {
    if (!state.selectedReference) return;
    const occ = state.selectedReference.occurrence;
    const locks = state.selectedReference.locks || {};
    setupRepairModal("Set Field Locks", (c) => {
      c.appendChild(h("div", { class: "checkbox-group" },
        h("label", {}, h("input", { type: "checkbox", id: "lock-title", checked: !!locks.title }), " Lock Title"),
        h("label", {}, h("input", { type: "checkbox", id: "lock-authors", checked: !!locks.authors }), " Lock Authors"),
        h("label", {}, h("input", { type: "checkbox", id: "lock-year", checked: !!locks.year }), " Lock Year"),
        h("label", {}, h("input", { type: "checkbox", id: "lock-doi", checked: !!locks.doi }), " Lock DOI")
      ));
      const changeObj = {
        type: "set_field_locks",
        occurrence_id: occ.occurrence_id,
        locks: {},
        expected_revision: occ.revision
      };
      document.getElementById("form-repair-reference").onsubmit = (e) => {
        e.preventDefault();
        changeObj.locks = {
          title: document.getElementById("lock-title").checked,
          authors: document.getElementById("lock-authors").checked,
          year: document.getElementById("lock-year").checked,
          doi: document.getElementById("lock-doi").checked
        };
        executeUpdateReference(changeObj);
      };
    });
  });

  document.getElementById("btn-action-mark-unresolved").addEventListener("click", () => {
    if (!state.selectedReference) return;
    const occ = state.selectedReference.occurrence;
    if (confirm("Mark this reference as unresolved?")) {
      executeUpdateReference({
        type: "mark_unresolved",
        occurrence_id: occ.occurrence_id,
        expected_revision: occ.revision
      });
    }
  });

  document.getElementById("btn-action-attach-file").addEventListener("click", () => {
    if (!state.selectedReference) return;
    const occ = state.selectedReference.occurrence;
    setupRepairModal("Attach Local Copy File", (c) => {
      c.appendChild(h("div", { class: "form-group" },
        h("label", { for: "repair-attach-method" }, "Attachment Method:"),
        h("select", { id: "repair-attach-method" },
          h("option", { value: "upload" }, "Browser PDF Upload (multipart)"),
          h("option", { value: "path" }, "Server Local File Path (Allowed Folder)")
        )
      ));
      const uploadGrp = h("div", { id: "repair-attach-upload-group", class: "form-group" },
        h("label", { for: "repair-attach-file" }, "Select PDF File:"),
        h("input", { type: "file", id: "repair-attach-file", accept: "application/pdf,.pdf" })
      );
      const pathGrp = h("div", { id: "repair-attach-path-group", class: "form-group", style: { display: "none" } },
        h("label", { for: "repair-attach-path" }, "Local File Path (Must be in allowed folders):"),
        h("input", { type: "text", id: "repair-attach-path", placeholder: "/path/to/paper.pdf" })
      );
      c.appendChild(uploadGrp);
      c.appendChild(pathGrp);
      c.appendChild(h("div", { class: "form-group" },
        h("label", { for: "repair-attach-hint" }, "Version Hint:"),
        h("select", { id: "repair-attach-hint" },
          h("option", { value: "" }, "Unknown"),
          h("option", { value: "published" }, "Published"),
          h("option", { value: "accepted" }, "Accepted (AAM)"),
          h("option", { value: "submitted" }, "Submitted (Preprint)")
        )
      ));

      const methodSelect = document.getElementById("repair-attach-method");
      methodSelect.addEventListener("change", (e) => {
        const isUpload = e.target.value === "upload";
        uploadGrp.style.display = isUpload ? "block" : "none";
        pathGrp.style.display = isUpload ? "none" : "block";
      });

      document.getElementById("form-repair-reference").onsubmit = async (e) => {
        e.preventDefault();
        const method = document.getElementById("repair-attach-method").value;
        const hint = document.getElementById("repair-attach-hint").value || null;

        if (method === "upload") {
          const fileInput = document.getElementById("repair-attach-file");
          const file = fileInput && fileInput.files && fileInput.files[0];
          if (!file) {
            showToast("Select a PDF file to attach.", "warn");
            return;
          }
          const fd = new FormData();
          fd.append("file", file);
          fd.append("purpose", "local_copy");
          fd.append("declared_bytes", String(file.size));
          fd.append("occurrence_id", occ.occurrence_id);
          if (hint) {
            fd.append("version_hint", hint);
          }
          fd.append("idempotency_key", makeIdempotencyKey());

          try {
            const out = await api(`/api/projects/${state.currentProjectId}/uploads/pdf`, {
              method: "POST",
              body: fd
            });
            const attach = out?.attachment || (out?.local_copy_id ? out : null);
            showToast(`File attached: ${attach?.local_copy_id || "success"}`, "ok");
            closeModal("modal-repair-reference");
            await inspectReference(occ.occurrence_id);
            await loadReferences();
            if (state.currentProjectId) await loadProjectDetail(state.currentProjectId);
          } catch (err) {
            showToast(`Attach failed: ${err.message}`, "bad");
            // Preserve file selection on failure for retry
          }
        } else {
          const path = document.getElementById("repair-attach-path").value.trim();
          if (!path) {
            showToast("Enter a local file path.", "warn");
            return;
          }
          try {
            const out = await api(`/api/projects/${state.currentProjectId}/references/${occ.occurrence_id}/attach`, {
              method: "POST",
              body: {
                project_id: state.currentProjectId,
                occurrence_id: occ.occurrence_id,
                path,
                version_hint: hint,
                idempotency_key: makeIdempotencyKey()
              }
            });
            showToast(`File attached: ${out.local_copy_id}`, "ok");
            closeModal("modal-repair-reference");
            await inspectReference(occ.occurrence_id);
            await loadReferences();
            if (state.currentProjectId) await loadProjectDetail(state.currentProjectId);
          } catch (err) {
            showToast(`Attach failed: ${err.message}`, "bad");
          }
        }
      };
    });
  });

  // --- ADD MANUAL REFERENCE ---
  document.getElementById("btn-open-add-reference").addEventListener("click", async () => {
    if (!state.currentProjectId) {
      showToast("Select a project first.", "warn");
      return;
    }
    await loadSources();
    const sourceSelect = document.getElementById("add-ref-source-id");
    clearElement(sourceSelect);
    state.sources.forEach(s => {
      sourceSelect.appendChild(h("option", { value: s.source_id }, `${s.label || s.source_id}`));
    });
    openModal("modal-add-reference");
  });

  document.getElementById("form-add-reference").addEventListener("submit", async (e) => {
    e.preventDefault();
    const sourceId = document.getElementById("add-ref-source-id").value;
    const text = document.getElementById("add-ref-text").value.trim();
    const afterId = document.getElementById("add-ref-after-id").value.trim() || null;

    try {
      const out = await api(`/api/projects/${state.currentProjectId}/references`, {
        method: "POST",
        body: {
          project_id: state.currentProjectId,
          source_id: sourceId,
          text,
          after_occurrence_id: afterId,
          idempotency_key: makeIdempotencyKey()
        }
      });
      showToast(`Reference added: ${out.occurrence_id}`, "ok");
      closeModal("modal-add-reference");
      document.getElementById("form-add-reference").reset();
      await loadReferences();
      if (state.currentProjectId) await loadProjectDetail(state.currentProjectId);
    } catch (err) {
      showToast(`Add reference failed: ${err.message}`, "bad");
    }
  });

  // --- SCRATCH RESOLVER ---
  document.getElementById("btn-open-scratch-resolve").addEventListener("click", () => {
    openModal("modal-scratch-resolve");
  });

  document.getElementById("form-scratch-resolve").addEventListener("submit", async (e) => {
    e.preventDefault();
    const citation = document.getElementById("scratch-citation-text").value.trim() || null;
    const title = document.getElementById("scratch-title").value.trim() || null;
    const yearRaw = document.getElementById("scratch-year").value.trim();
    const year = yearRaw ? parseInt(yearRaw, 10) : null;
    const authorsRaw = document.getElementById("scratch-authors").value.trim();
    const authors = authorsRaw ? authorsRaw.split(";").map(s => s.trim()).filter(Boolean) : [];

    let fields = null;
    if (title || year || authors.length > 0) {
      fields = { title, year, authors };
    }

    try {
      const out = await api("/api/resolve", {
        method: "POST",
        body: {
          citation,
          fields
        }
      });
      renderScratchResults(out);
    } catch (err) {
      showToast(`Scratch resolve error: ${err.message}`, "bad");
    }
  });

  function renderScratchResults(out) {
    const box = document.getElementById("scratch-results-container");
    box.style.display = "block";
    document.getElementById("scratch-hint-badge").textContent = out.decision_hint;

    const list = document.getElementById("scratch-candidates-list");
    clearElement(list);

    (out.candidates || []).forEach(c => {
      const item = h("div", { class: "candidate-item" },
        h("div", { class: "candidate-header" },
          h("strong", {}, `#${c.rank}: ${c.metadata?.title || "Untitled"}`),
          h("span", { class: "badge" }, String(c.metadata?.year || ""))
        ),
        h("p", { class: "text-muted" }, (c.reasons || []).join(" · ") || "No reason sentences.")
      );
      list.appendChild(item);
    });
  }

  // --- EXPORTS & DRAFTS ---
  async function loadArtifacts() {
    if (!state.currentProjectId) {
      clearElement(document.getElementById("tbody-artifacts"));
      document.getElementById("tbody-artifacts").appendChild(
        h("tr", {}, h("td", { colspan: 6, class: "text-muted" }, "Select a project first."))
      );
      return;
    }

    try {
      const data = await api(`/api/projects/${state.currentProjectId}/artifacts`);
      state.artifacts = data.artifacts || [];
      renderArtifactsTable();
    } catch (err) {
      showToast(`Failed loading artifacts: ${err.message}`, "bad");
    }
  }

  function renderArtifactsTable() {
    const tbody = document.getElementById("tbody-artifacts");
    clearElement(tbody);

    if (state.artifacts.length === 0) {
      tbody.appendChild(h("tr", {}, h("td", { colspan: 6, class: "text-muted" }, "No artifacts found for this project.")));
      return;
    }

    state.artifacts.forEach(a => {
      const isText = a.media_type.startsWith("text/") || a.media_type.includes("json");
      const tr = h("tr", {},
        h("td", {}, a.artifact_id),
        h("td", {}, a.name),
        h("td", {}, a.kind),
        h("td", {}, a.media_type),
        h("td", {}, String(a.bytes)),
        h("td", {},
          h("div", { class: "actions" },
            isText ? h("button", {
              type: "button",
              class: "btn btn-sm",
              onClick: () => viewTextArtifact(a)
            }, "Preview") : null,
            h("button", {
              type: "button",
              class: "btn btn-sm btn-primary",
              onClick: () => downloadProjectFile(state.currentProjectId, "artifact", a.artifact_id, a.name)
            }, "Download")
          )
        )
      );
      tbody.appendChild(tr);
    });
  }

  async function viewTextArtifact(descriptor) {
    state.currentArtifact = descriptor;
    state.artifactOffset = 0;
    document.getElementById("artifact-modal-title").textContent = descriptor.name;
    document.getElementById("artifact-text-content").textContent = "Loading preview...";
    openModal("modal-view-artifact");
    await fetchArtifactChunk(descriptor.artifact_id, 0);
  }

  async function fetchArtifactChunk(artifactId, offset) {
    try {
      const data = await api(`/api/projects/${state.currentProjectId}/artifacts/${artifactId}/text?offset=${offset}&length=65536`);
      const container = document.getElementById("artifact-text-content");
      if (offset === 0) {
        container.textContent = data.text || "(Empty artifact)";
      } else {
        container.textContent += data.text || "";
      }

      // Exact next_offset handling
      const nextOffset = (data.next_offset !== null && data.next_offset !== undefined)
        ? data.next_offset
        : (offset + (data.text?.length || 0));
      state.artifactOffset = nextOffset;

      const moreBtn = document.getElementById("btn-artifact-read-more");
      const infoSpan = document.getElementById("artifact-pagination-info");

      if (data.eof === true || data.next_offset === null || !data.text) {
        moreBtn.style.display = "none";
        infoSpan.textContent = `Reached end of artifact (${state.artifactOffset} bytes read).`;
      } else {
        moreBtn.style.display = "inline-block";
        infoSpan.textContent = `Showing bytes 0 to ${state.artifactOffset}.`;
      }
    } catch (err) {
      showToast(`Artifact preview failed: ${err.message}`, "bad");
    }
  }

  document.getElementById("btn-artifact-read-more").addEventListener("click", () => {
    if (state.currentArtifact) {
      fetchArtifactChunk(state.currentArtifact.artifact_id, state.artifactOffset);
    }
  });

  // --- ROBUST FILE DOWNLOAD HANDLING ---
  async function downloadProjectFile(projectId, kind, resourceId, fallbackName = "download") {
    try {
      showToast("Downloading file...", "info");
      const response = await fetch(`/api/projects/${projectId}/files/${kind}/${resourceId}`, {
        method: "GET",
        headers: { "Accept": "*/*" }
      });

      if (!response.ok) {
        let errorMsg = response.statusText;
        try {
          const errData = await response.json();
          errorMsg = errData.message || errData.detail || errorMsg;
        } catch {
          // not JSON
        }
        throw new Error(`HTTP ${response.status}: ${errorMsg}`);
      }

      // Read Content-Disposition filename if provided by server
      let filename = fallbackName;
      const dispHeader = response.headers.get("content-disposition");
      if (dispHeader) {
        const match = dispHeader.match(/filename="?([^";]+)"?/);
        if (match && match[1]) filename = match[1];
      }

      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = blobUrl;
      link.setAttribute("download", filename);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      setTimeout(() => URL.revokeObjectURL(blobUrl), 10000);

      showToast(`Downloaded: ${filename}`, "ok");
    } catch (err) {
      showToast(`Download failed: ${err.message}`, "bad");
    }
  }

  // --- CREATE EXPORT ---
  document.getElementById("btn-open-create-export").addEventListener("click", async () => {
    // Clear export context before new preparation
    state.exportProjectContext = null;
    state.exportProjectRevision = null;

    const projId = state.currentProjectId;
    if (!projId) {
      showToast("Select a project first.", "warn");
      return;
    }
    const freshProject = await loadProjectDetail(projId);
    if (state.currentProjectId !== projId || !freshProject) {
      return;
    }
    state.exportProjectContext = {
      projectId: projId,
      revision: freshProject.revision
    };
    state.exportProjectRevision = freshProject.revision;
    openModal("modal-create-export");
  });

  document.getElementById("form-create-export").addEventListener("submit", async (e) => {
    e.preventDefault();
    const currentPid = state.currentProjectId;
    const exportCtx = state.exportProjectContext;

    if (!currentPid || !exportCtx || exportCtx.projectId !== currentPid) {
      showToast("Project selection changed or invalid. Re-open export dialog.", "bad");
      return;
    }

    const revision = exportCtx.revision;
    if (revision === undefined || revision === null) {
      showToast("Project revision unknown. Re-open export dialog.", "bad");
      return;
    }
    const formats = Array.from(document.querySelectorAll("input[name='export-format']:checked")).map(cb => cb.value);
    if (formats.length === 0) {
      showToast("Select at least one export format.", "warn");
      return;
    }
    const selection = document.getElementById("export-selection").value;

    try {
      const out = await api(`/api/projects/${currentPid}/exports`, {
        method: "POST",
        body: {
          project_id: currentPid,
          project_revision: revision,
          formats,
          selection,
          idempotency_key: makeIdempotencyKey()
        }
      });
      showToast(`Export job queued: ${out.job_id}`, "ok");
      closeModal("modal-create-export");
      switchView("jobs");
      inspectJob(out.job_id);
    } catch (err) {
      showToast(`Create export failed: ${err.message}`, "bad");
    }
  });

  // --- DRAFT REQUESTS ---
  document.getElementById("btn-open-draft-requests").addEventListener("click", () => {
    if (!state.currentProjectId) {
      showToast("Select a project first.", "warn");
      return;
    }
    openModal("modal-draft-requests");
  });

  document.getElementById("btn-draft-selected").addEventListener("click", () => {
    openModal("modal-draft-requests");
  });

  document.getElementById("form-draft-requests").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = document.getElementById("draft-sender-name").value.trim();
    const affiliation = document.getElementById("draft-sender-affiliation").value.trim() || null;
    const purpose = document.getElementById("draft-sender-purpose").value.trim() || null;
    const language = document.getElementById("draft-language").value;
    const groupByAuthor = document.getElementById("draft-group-authors").checked;

    // Use checked IDs if available, else alert
    const occIds = Array.from(state.checkedReferenceIds);
    if (occIds.length === 0) {
      showToast("Select references on the references page to draft requests.", "warn");
      return;
    }

    try {
      const out = await api(`/api/projects/${state.currentProjectId}/drafts`, {
        method: "POST",
        body: {
          project_id: state.currentProjectId,
          scope: { occurrence_ids: occIds },
          sender: { name, affiliation, purpose },
          language,
          group_by_author: groupByAuthor,
          idempotency_key: makeIdempotencyKey()
        }
      });
      showToast(`Draft job queued: ${out.job_id}`, "ok");
      closeModal("modal-draft-requests");
      switchView("jobs");
      inspectJob(out.job_id);
    } catch (err) {
      showToast(`Draft requests failed: ${err.message}`, "bad");
    }
  });

  // --- RETRIEVAL SCOPES: ALL-FILTER VS SELECTED ---
  // Option 1: Retrieve all occurrences matching the active filter
  document.getElementById("btn-download-all-filter").addEventListener("click", async () => {
    if (!state.currentProjectId) return;
    if (state.referenceFilter.query && state.referenceFilter.query.trim()) {
      showToast("All-filter retrieval does not support text search queries. Clear search or select occurrences on page.", "warn");
      return;
    }
    if (state.referencesTotal === 0) {
      showToast("No references match current filter.", "warn");
      return;
    }

    if (!confirm(`Queue retrieval for ALL ${state.referencesTotal} occurrences matching the active filter?`)) {
      return;
    }

    try {
      const activeFilter = buildActiveReferenceFilter();
      const out = await api(`/api/projects/${state.currentProjectId}/download`, {
        method: "POST",
        body: {
          project_id: state.currentProjectId,
          scope: { filter: activeFilter },
          idempotency_key: makeIdempotencyKey()
        }
      });
      showToast(`Download job queued: ${out.job_id}`, "ok");
      switchView("jobs");
      inspectJob(out.job_id);
    } catch (err) {
      showToast(`Download queue failed: ${err.message}`, "bad");
    }
  });

  // Option 2: Retrieve only the checked occurrences on the current page
  document.getElementById("btn-download-selected").addEventListener("click", async () => {
    if (!state.currentProjectId) return;
    const occIds = Array.from(state.checkedReferenceIds);
    if (occIds.length === 0) {
      showToast("No references selected on this page.", "warn");
      return;
    }

    if (!confirm(`Queue retrieval for ${occIds.length} selected references on this page?`)) {
      return;
    }

    try {
      const out = await api(`/api/projects/${state.currentProjectId}/download`, {
        method: "POST",
        body: {
          project_id: state.currentProjectId,
          scope: { occurrence_ids: occIds },
          idempotency_key: makeIdempotencyKey()
        }
      });
      showToast(`Download job queued: ${out.job_id}`, "ok");
      switchView("jobs");
      inspectJob(out.job_id);
    } catch (err) {
      showToast(`Download queue failed: ${err.message}`, "bad");
    }
  });

  // --- SETTINGS & INTENT CONFIRMATION ---
  let currentIntentEpoch = 0;
  let activeIntentConfirmation = null;
  let isConfirmInFlight = false;
  let activeReviewContext = null;

  function setConfirmButtonState(inFlight) {
    const btn = document.getElementById("btn-confirm-intent-action");
    if (!btn) return;
    isConfirmInFlight = inFlight;
    btn.disabled = inFlight;
    if (inFlight) {
      btn.setAttribute("aria-busy", "true");
      btn.textContent = "Confirming...";
    } else {
      btn.removeAttribute("aria-busy");
      btn.textContent = "Confirm Action";
    }
  }

  function dismissConfirmationModal() {
    currentIntentEpoch++;
    if (activeIntentConfirmation) {
      if (typeof activeIntentConfirmation.onCancel === "function") {
        try {
          activeIntentConfirmation.onCancel();
        } catch (err) {
          console.warn("Error in intent onCancel:", err);
        }
      }
      activeIntentConfirmation = null;
    }
    setConfirmButtonState(false);
    clearSecretInputs();
  }

  async function loadSettings() {
    const projId = state.currentProjectId;
    try {
      if (projId) {
        const [globalData, projData] = await Promise.all([
          api("/api/settings"),
          api(`/api/settings?project_id=${encodeURIComponent(projId)}`)
        ]);
        if (state.currentProjectId !== projId) return;
        state.globalSettingsRevision = globalData?.revision || null;
        state.projectSettingsRevision = projData?.revision || null;
        state.settings = projData;
        renderSettingsView(projData);
      } else {
        const globalData = await api("/api/settings");
        if (state.currentProjectId) return;
        state.globalSettingsRevision = globalData?.revision || null;
        state.projectSettingsRevision = null;
        state.settings = globalData;
        renderSettingsView(globalData);
      }
    } catch (err) {
      if (state.currentProjectId !== projId) return;
      showToast(`Failed loading settings: ${err.message}`, "bad");
    }
  }

  function renderSettingsView(data) {
    if (!data) return;

    // Revision badge
    const revBadge = document.getElementById("settings-revision-badge");
    if (revBadge) revBadge.textContent = `Revision: ${data.revision || "-"}`;

    // Warnings
    const warnBox = document.getElementById("settings-warnings-box");
    if (warnBox) {
      clearElement(warnBox);
      if (data.warnings && data.warnings.length > 0) {
        warnBox.style.display = "block";
        data.warnings.forEach(w => {
          warnBox.appendChild(h("div", {}, `Warning: ${w}`));
        });
      } else {
        warnBox.style.display = "none";
      }
    }

    // Global settings fields
    const homeVal = document.getElementById("setting-home-val");
    if (homeVal) homeVal.textContent = data.rc_home || "-";

    const globalDl = document.getElementById("setting-global-downloads-val");
    if (globalDl) {
      globalDl.textContent = data.download_enabled_global ? "Enabled (CLI Master Switch)" : "Disabled (CLI Master Switch)";
      globalDl.className = data.download_enabled_global ? "badge badge-ok" : "badge badge-warn";
    }

    const browserVal = document.getElementById("setting-browser-val");
    if (browserVal) {
      if (data.browser) {
        browserVal.textContent = `${data.browser.enabled ? "Enabled" : "Disabled"} (Configured: ${data.browser.configured ? "Yes" : "No"})`;
      } else {
        browserVal.textContent = "-";
      }
    }

    const provVal = document.getElementById("setting-providers-val");
    if (provVal) {
      provVal.textContent = (data.enabled_providers || []).join(", ") || "None";
    }

    const keysStatus = document.getElementById("setting-keys-status");
    if (keysStatus) {
      clearElement(keysStatus);
      if (data.keys && Object.keys(data.keys).length > 0) {
        Object.entries(data.keys).forEach(([prov, isConfigured]) => {
          const badge = h("span", {
            class: `badge ${isConfigured ? "badge-ok" : "badge-warn"}`,
            style: { marginRight: "6px" }
          }, `${prov}: ${isConfigured ? "Configured" : "Missing"}`);
          keysStatus.appendChild(badge);
        });
      } else {
        keysStatus.textContent = "None configured";
      }
    }

    const limitsVal = document.getElementById("setting-limits-val");
    if (limitsVal) {
      if (data.limits && Object.keys(data.limits).length > 0) {
        limitsVal.textContent = Object.entries(data.limits).map(([k, v]) => `${k}=${v}`).join(", ");
      } else {
        limitsVal.textContent = "Default";
      }
    }

    const mcpVal = document.getElementById("setting-mcp-val");
    if (mcpVal) {
      mcpVal.textContent = (data.mcp_scopes || []).join(", ") || "None";
    }

    const emailVal = document.getElementById("setting-email-val");
    if (emailVal) {
      emailVal.textContent = data.email || "None";
    }

    const foldersList = document.getElementById("setting-folders-list");
    if (foldersList) {
      clearElement(foldersList);
      if (data.allow_folders && data.allow_folders.length > 0) {
        data.allow_folders.forEach(f => {
          foldersList.appendChild(h("li", { class: "code-inline" }, f));
        });
      } else {
        foldersList.appendChild(h("li", { class: "text-muted" }, "No folders allowed."));
      }
    }

    const fallbackVal = document.getElementById("setting-fallback-val");
    if (fallbackVal) {
      fallbackVal.textContent = data.pdf_fallback_url || "Off";
    }

    // Target Project Settings
    const projBadge = document.getElementById("settings-project-badge");
    const projEmpty = document.getElementById("settings-project-empty");
    const projContent = document.getElementById("settings-project-content");

    if (!state.currentProjectId || !data.project) {
      if (projBadge) projBadge.textContent = "No Project Selected";
      if (projEmpty) projEmpty.style.display = "block";
      if (projContent) projContent.style.display = "none";
    } else {
      const proj = data.project;
      const projName = state.projects.find(p => p.project_id === state.currentProjectId)?.name || state.currentProjectId;
      if (projBadge) projBadge.textContent = projName;
      if (projEmpty) projEmpty.style.display = "none";
      if (projContent) projContent.style.display = "block";

      const projNetVal = document.getElementById("setting-proj-network-val");
      if (projNetVal) projNetVal.textContent = proj.network_mode || "-";

      const projDlVal = document.getElementById("setting-proj-downloads-val");
      if (projDlVal) {
        projDlVal.textContent = proj.download_enabled ? "Enabled" : "Disabled";
        projDlVal.className = proj.download_enabled ? "badge badge-ok" : "badge badge-warn";
      }

      const effDlVal = document.getElementById("setting-proj-effective-download");
      if (effDlVal) {
        const eff = Boolean(proj.download_enabled || data.download_enabled_global);
        const sourceDesc = proj.download_enabled
          ? "(Granted by project setting)"
          : data.download_enabled_global
            ? "(Granted by global setting)"
            : "(Disabled globally and in project)";
        effDlVal.textContent = `${eff ? "Active" : "Inactive"} ${sourceDesc}`;
        effDlVal.className = `badge ${eff ? "badge-ok" : "badge-warn"}`;
      }

      const verPolicyVal = document.getElementById("setting-proj-version-policy-val");
      if (verPolicyVal) verPolicyVal.textContent = proj.version_policy || "-";

      const netSelect = document.getElementById("setting-input-proj-network");
      if (netSelect && proj.network_mode) netSelect.value = proj.network_mode;

      const dlSelect = document.getElementById("setting-input-proj-downloads");
      if (dlSelect) dlSelect.value = String(Boolean(proj.download_enabled));
    }
  }

  function clearSecretInputs() {
    const keyInput = document.getElementById("setting-input-key");
    if (keyInput) keyInput.value = "";
  }

  function isPlainObject(v) {
    return v !== null && typeof v === "object" && !Array.isArray(v);
  }

  function formatDiffValue(v) {
    if (v === undefined) return "(none)";
    if (v === null) return "null";
    if (typeof v === "string") return v;
    if (typeof v === "number" || typeof v === "boolean") return String(v);
    return JSON.stringify(v);
  }

  function renderIntentSummary(container, summary) {
    if (!summary || typeof summary !== "object") {
      container.textContent = "No summary details available.";
      return;
    }

    if ("before" in summary || "after" in summary) {
      const table = h("table", { class: "intent-diff-table" },
        h("thead", {},
          h("tr", {},
            h("th", {}, "Property"),
            h("th", {}, "Current (Before)"),
            h("th", {}, "Proposed (After)")
          )
        )
      );
      const tbody = h("tbody", {});

      const bIsMap = isPlainObject(summary.before);
      const aIsMap = isPlainObject(summary.after);

      if (bIsMap || aIsMap) {
        const allKeys = new Set([
          ...Object.keys(bIsMap ? summary.before : {}),
          ...Object.keys(aIsMap ? summary.after : {})
        ]);
        if (allKeys.size > 0) {
          allKeys.forEach(key => {
            const bVal = bIsMap ? summary.before[key] : undefined;
            const aVal = aIsMap ? summary.after[key] : undefined;
            tbody.appendChild(h("tr", {},
              h("td", { class: "code-inline" }, key),
              h("td", { class: "diff-val-old" }, formatDiffValue(bVal)),
              h("td", { class: "diff-val-new" }, formatDiffValue(aVal))
            ));
          });
        } else {
          tbody.appendChild(h("tr", {},
            h("td", {}, summary.property || summary.field || "Value"),
            h("td", { class: "diff-val-old" }, formatDiffValue(summary.before)),
            h("td", { class: "diff-val-new" }, formatDiffValue(summary.after))
          ));
        }
      } else {
        // Scalar / null / boolean / array: render as whole value in one change row
        const propName = summary.property || summary.field || "Value";
        tbody.appendChild(h("tr", {},
          h("td", { class: "code-inline" }, propName),
          h("td", { class: "diff-val-old" }, formatDiffValue(summary.before)),
          h("td", { class: "diff-val-new" }, formatDiffValue(summary.after))
        ));
      }

      table.appendChild(tbody);
      container.appendChild(table);
      return;
    }

    const table = h("table", { class: "intent-diff-table" });
    const tbody = h("tbody", {});
    for (const [k, v] of Object.entries(summary)) {
      tbody.appendChild(h("tr", {},
        h("th", {}, k),
        h("td", {}, typeof v === "object" && v !== null ? JSON.stringify(v, null, 2) : String(v))
      ));
    }
    table.appendChild(tbody);
    container.appendChild(table);
  }

  function showConfirmationModal({ title, purpose, expiresAt, summary, warning, epoch, onConfirm, onCancel }) {
    const titleEl = document.getElementById("intent-modal-title");
    const purposeEl = document.getElementById("intent-purpose-badge");
    const expiryEl = document.getElementById("intent-expiry-text");
    const summaryEl = document.getElementById("intent-summary-container");
    const noticeEl = document.getElementById("intent-notice-box");

    if (titleEl) titleEl.textContent = title || "Confirm Action";
    if (purposeEl) {
      purposeEl.textContent = (purpose || "INTENT").toUpperCase();
      purposeEl.className = `badge ${purpose === "review" ? "badge-ok" : "badge-warn"}`;
    }
    if (expiryEl) {
      expiryEl.textContent = expiresAt ? `Expires: ${new Date(expiresAt).toLocaleTimeString()}` : "";
    }

    if (summaryEl) {
      clearElement(summaryEl);
      renderIntentSummary(summaryEl, summary);
    }

    if (noticeEl) {
      if (warning) {
        noticeEl.textContent = warning;
        noticeEl.style.display = "block";
      } else {
        noticeEl.style.display = "none";
      }
    }

    setConfirmButtonState(false);
    activeIntentConfirmation = { epoch, onConfirm, onCancel };
    openModal("modal-confirm-intent");
  }

  async function executeSettingsChange(change) {
    const isProjectChange = Boolean(change.project_id || (change.type && change.type.startsWith("set_project_")));
    const expectedRevision = isProjectChange
      ? (state.projectSettingsRevision || (state.settings?.project ? state.settings.revision : null))
      : (state.globalSettingsRevision || (state.settings && !state.settings.project ? state.settings.revision : null));

    if (!expectedRevision) {
      showToast("Settings revision unknown. Refresh settings first.", "bad");
      return;
    }

    const epoch = ++currentIntentEpoch;
    const payload = {
      expected_settings_revision: expectedRevision,
      change: change,
      idempotency_key: makeIdempotencyKey()
    };

    let intentOut;
    try {
      // Step 1: Request intent
      intentOut = await api("/api/settings/intent", {
        method: "POST",
        body: payload
      });
    } catch (err) {
      if (epoch === currentIntentEpoch) {
        clearSecretInputs();
        showToast(`Settings intent failed: ${err.message}`, "bad");
      }
      return;
    }

    if (epoch !== currentIntentEpoch) return;

    if (!intentOut || !intentOut.confirmation_token) {
      clearSecretInputs();
      showToast("Server failed to provide a confirmation token.", "bad");
      return;
    }

    // Step 2: Show confirmation dialog
    showConfirmationModal({
      title: "Confirm Settings Change",
      purpose: intentOut.purpose || "settings",
      expiresAt: intentOut.expires_at,
      summary: intentOut.summary,
      warning: null,
      epoch: epoch,
      onConfirm: async () => {
        if (epoch !== currentIntentEpoch) return;
        try {
          // Confirm with exact same payload and idempotency_key plus header
          const out = await api("/api/settings", {
            method: "POST",
            body: payload,
            headers: {
              "X-Confirmation-Token": intentOut.confirmation_token
            }
          });
          if (epoch !== currentIntentEpoch) return;
          showToast("Settings change applied successfully.", "ok");
          closeModal("modal-confirm-intent");
          await loadSettings();
          if (state.currentProjectId) {
            await loadProjectDetail(state.currentProjectId);
          }
        } catch (err) {
          if (epoch !== currentIntentEpoch) return;
          const isTerminal4xx = err.status && err.status >= 400 && err.status < 500;
          if (isTerminal4xx) {
            closeModal("modal-confirm-intent");
            showToast(`Settings confirmation rejected (${err.status}): ${err.message}`, "bad");
          } else {
            showToast(`Network or server error (${err.message}). Click Confirm Action to retry.`, "bad");
          }
          throw err;
        }
      },
      onCancel: () => {
        clearSecretInputs();
      }
    });
  }

  function startTrustedReview(occ, candidate, action) {
    activeReviewContext = { occ, candidate, action };

    const titleEl = document.getElementById("review-modal-title");
    const badgeEl = document.getElementById("review-action-badge");
    const candTitleEl = document.getElementById("review-candidate-title");
    const candReasonsEl = document.getElementById("review-candidate-reasons");
    const expRevEl = document.getElementById("review-expected-rev");
    const noteEl = document.getElementById("review-intent-note");

    if (titleEl) titleEl.textContent = action === "accept" ? "Trusted Review: Accept Candidate" : "Trusted Review: Reject Candidate";
    if (badgeEl) {
      badgeEl.textContent = action.toUpperCase();
      badgeEl.className = `badge ${action === "accept" ? "badge-ok" : "badge-bad"}`;
    }
    if (candTitleEl) candTitleEl.textContent = `Rank #${candidate.rank}: ${candidate.metadata?.title || "Untitled"}`;
    if (candReasonsEl) candReasonsEl.textContent = (candidate.reasons || []).join(" · ") || "No reason sentences.";
    if (expRevEl) expRevEl.textContent = `Rev ${occ.revision}`;
    if (noteEl) noteEl.value = "";

    openModal("modal-review-intent");
  }

  // --- SETTINGS FORM LISTENERS ---
  document.getElementById("btn-refresh-settings").addEventListener("click", () => {
    loadSettings();
  });

  const fallbackPresetSelect = document.getElementById("setting-input-fallback-preset");
  const fallbackCustomInput = document.getElementById("setting-input-fallback-custom");
  if (fallbackPresetSelect && fallbackCustomInput) {
    fallbackPresetSelect.addEventListener("change", (e) => {
      if (e.target.value === "custom") {
        fallbackCustomInput.style.display = "inline-block";
        fallbackCustomInput.required = true;
      } else {
        fallbackCustomInput.style.display = "none";
        fallbackCustomInput.required = false;
      }
    });
  }

  document.getElementById("form-setting-email").addEventListener("submit", async (e) => {
    e.preventDefault();
    const val = document.getElementById("setting-input-email").value.trim();
    if (!val) return;
    await executeSettingsChange({ type: "set_email", value: val });
  });

  document.getElementById("form-setting-folder").addEventListener("submit", async (e) => {
    e.preventDefault();
    const val = document.getElementById("setting-input-folder").value.trim();
    if (!val) return;
    await executeSettingsChange({ type: "add_allowed_folder", path: val });
    document.getElementById("setting-input-folder").value = "";
  });

  document.getElementById("form-setting-key").addEventListener("submit", async (e) => {
    e.preventDefault();
    const provider = document.getElementById("setting-input-provider").value;
    const key = document.getElementById("setting-input-key").value;
    if (!key) return;
    await executeSettingsChange({ type: "set_provider_key", provider: provider, value: key });
  });

  document.getElementById("form-setting-fallback").addEventListener("submit", async (e) => {
    e.preventDefault();
    const preset = document.getElementById("setting-input-fallback-preset").value;
    let fallbackVal = null;
    if (preset === "off") {
      fallbackVal = null;
    } else if (preset === "custom") {
      fallbackVal = document.getElementById("setting-input-fallback-custom").value.trim() || null;
    } else {
      fallbackVal = preset;
    }
    await executeSettingsChange({ type: "set_pdf_fallback", value: fallbackVal });
  });

  document.getElementById("form-setting-proj-network").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.currentProjectId) {
      showToast("No active project selected.", "warn");
      return;
    }
    const mode = document.getElementById("setting-input-proj-network").value;
    await executeSettingsChange({
      type: "set_project_network",
      project_id: state.currentProjectId,
      mode: mode
    });
  });

  document.getElementById("form-setting-proj-downloads").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.currentProjectId) {
      showToast("No active project selected.", "warn");
      return;
    }
    const enabled = document.getElementById("setting-input-proj-downloads").value === "true";
    await executeSettingsChange({
      type: "set_project_downloads",
      project_id: state.currentProjectId,
      enabled: enabled
    });
  });

  // --- INTENT MODAL & REVIEW LISTENERS ---
  document.getElementById("btn-confirm-intent-action").addEventListener("click", async () => {
    if (isConfirmInFlight) return;
    if (!activeIntentConfirmation || typeof activeIntentConfirmation.onConfirm !== "function") return;

    const intentToConfirm = activeIntentConfirmation;
    const confirmingEpoch = intentToConfirm.epoch;
    setConfirmButtonState(true);
    try {
      await intentToConfirm.onConfirm();
    } catch {
      // onConfirm handles reporting and distinguishes terminal vs retryable
    } finally {
      if (activeIntentConfirmation && activeIntentConfirmation.epoch === confirmingEpoch) {
        setConfirmButtonState(false);
      }
    }
  });

  document.getElementById("btn-cancel-intent").addEventListener("click", () => {
    closeModal("modal-confirm-intent");
  });

  document.getElementById("form-review-intent").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!activeReviewContext) return;
    const { occ, candidate, action } = activeReviewContext;
    const note = document.getElementById("review-intent-note").value.trim() || null;
    closeModal("modal-review-intent");

    const epoch = ++currentIntentEpoch;
    const payload = {
      project_id: state.currentProjectId,
      occurrence_id: occ.occurrence_id,
      action: action,
      candidate_id: candidate.candidate_id,
      expected_revision: occ.revision,
      note: note,
      idempotency_key: makeIdempotencyKey()
    };

    const path = `/api/projects/${encodeURIComponent(state.currentProjectId)}/references/${encodeURIComponent(occ.occurrence_id)}`;
    let intentOut;
    try {
      intentOut = await api(`${path}/review-intent`, {
        method: "POST",
        body: payload
      });
    } catch (err) {
      if (epoch === currentIntentEpoch) {
        showToast(`Review intent failed: ${err.message}`, "bad");
      }
      return;
    }

    if (epoch !== currentIntentEpoch) return;

    if (!intentOut || !intentOut.confirmation_token) {
      showToast("Server failed to provide a review confirmation token.", "bad");
      return;
    }

    showConfirmationModal({
      title: `Confirm Trusted ${action === "accept" ? "Approval" : "Rejection"}`,
      purpose: intentOut.purpose || "review",
      expiresAt: intentOut.expires_at,
      summary: intentOut.summary,
      epoch: epoch,
      warning: action === "accept"
        ? "This action records a revision-bound human review approval (accepted_user) via WEB_UI surface. If concurrent edits occurred, this action will fail without automatic rebase."
        : null,
      onConfirm: async () => {
        if (epoch !== currentIntentEpoch) return;
        try {
          const out = await api(`${path}/review`, {
            method: "POST",
            body: payload,
            headers: {
              "X-Confirmation-Token": intentOut.confirmation_token
            }
          });
          if (epoch !== currentIntentEpoch) return;
          showToast(`Candidate review confirmed: ${action}`, "ok");
          closeModal("modal-confirm-intent");
          if (out && Array.isArray(out.changed) && out.changed.length > 0) {
            const targetRef = out.changed[0];
            await inspectReference(targetRef.occurrence_id || occ.occurrence_id);
          } else {
            await inspectReference(occ.occurrence_id);
          }
          await loadReferences();
        } catch (err) {
          if (epoch !== currentIntentEpoch) return;
          const isTerminal4xx = err.status && err.status >= 400 && err.status < 500;
          if (isTerminal4xx) {
            closeModal("modal-confirm-intent");
            showToast(`Review rejected (${err.status}): ${err.message}`, "bad");
          } else {
            showToast(`Network or server error (${err.message}). Click Confirm Action to retry.`, "bad");
          }
          throw err;
        }
      },
      onCancel: () => {
        // No action needed
      }
    });
  });

  // --- THEME (shared with the Library: localStorage "rc-library-theme", data-theme on <html>) ---
  const THEME_KEY = "rc-library-theme";

  function readThemePref() {
    try {
      const v = window.localStorage.getItem(THEME_KEY);
      return v === "light" || v === "dark" ? v : "system";
    } catch (_err) {
      return "system";
    }
  }

  function applyTheme(pref, persist) {
    const root = document.documentElement;
    if (pref === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", pref);
    document.querySelectorAll("[data-theme-choice]").forEach(btn => {
      btn.setAttribute("aria-checked", String(btn.getAttribute("data-theme-choice") === pref));
    });
    if (!persist) return;
    try {
      if (pref === "system") window.localStorage.removeItem(THEME_KEY);
      else window.localStorage.setItem(THEME_KEY, pref);
    } catch (_err) {
      // storage unavailable: the choice still applies to this page
    }
  }

  document.querySelectorAll("[data-theme-choice]").forEach(btn => {
    btn.addEventListener("click", () => applyTheme(btn.getAttribute("data-theme-choice"), true));
  });
  applyTheme(readThemePref(), false);

  // --- MODE SWITCH (Library link follows the selected project) ---
  function syncModeLinks() {
    const link = document.getElementById("mode-link-library");
    if (link) link.setAttribute("href", state.currentProjectId ? `/#/${encodeURIComponent(state.currentProjectId)}` : "/");
  }

  function projectIdFromHash() {
    const m = /^#project=([^&]+)/.exec(window.location.hash);
    if (!m) return null;
    try {
      return decodeURIComponent(m[1]);
    } catch (_err) {
      return null;
    }
  }

  document.getElementById("btn-refresh-dashboard").addEventListener("click", () => {
    loadDashboard();
  });

  // --- INITIALIZATION ---
  async function init() {
    const wantedProject = projectIdFromHash();
    await initSession();
    await loadProjects();
    if (wantedProject && state.projects.some(p => p.project_id === wantedProject)) {
      state.currentProjectId = wantedProject;
      updateProjectSelect();
      await loadProjectDetail(wantedProject);
    }
    const rawHash = window.location.hash.replace("#", "");
    const hash = rawHash && !rawHash.startsWith("project=") ? rawHash : "dashboard";
    if (hash !== "dashboard") await loadDashboard();
    switchView(hash);
  }

  if (typeof globalThis !== "undefined" && typeof globalThis.__TEST_HOOKS__ !== "undefined") {
    globalThis.__TEST_HOOKS__ = {
      state,
      api,
      executeSettingsChange,
      showConfirmationModal,
      dismissConfirmationModal,
      clearSecretInputs,
      setConfirmButtonState,
      get activeIntentConfirmation() { return activeIntentConfirmation; },
      set activeIntentConfirmation(val) { activeIntentConfirmation = val; },
      get currentIntentEpoch() { return currentIntentEpoch; },
      set currentIntentEpoch(val) { currentIntentEpoch = val; },
      get isConfirmInFlight() { return isConfirmInFlight; },
      set isConfirmInFlight(val) { isConfirmInFlight = val; },
      closeModal,
      openModal
    };
  }

  window.addEventListener("DOMContentLoaded", init);
})();
