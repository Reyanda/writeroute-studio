document.addEventListener("DOMContentLoaded", () => {
  "use strict";

  const FIELD_META = {
    text: { label: "Text", icon: "T", color: "var(--color-field-text)" },
    multiline: { label: "Long text", icon: "≡", color: "var(--color-field-text)" },
    number: { label: "Number", icon: "#", color: "var(--color-field-text)" },
    email: { label: "Email", icon: "@", color: "var(--color-field-text)" },
    phone: { label: "Phone", icon: "P", color: "var(--color-field-text)" },
    date: { label: "Date", icon: "D", color: "var(--color-field-date)" },
    checkbox: { label: "Checkbox", icon: "✓", color: "var(--color-field-choice)" },
    radio: { label: "Radio", icon: "●", color: "var(--color-field-choice)" },
    select: { label: "Choice", icon: "⌄", color: "var(--color-field-choice)" },
    comb: { label: "Cells", icon: "▦", color: "var(--color-field-cell)" },
    cell: { label: "Cell", icon: "□", color: "var(--color-field-cell)" },
    table_cell: { label: "Cell", icon: "▤", color: "var(--color-field-cell)" },
    signature: { label: "Signature", icon: "S", color: "var(--color-field-signature)" }
  };

  const state = {
    documentName: "",
    documentData: null,
    slots: {},
    mode: "edit",
    annotations: [],
    searchHits: [],
    selectedAnnotationId: null,
    readTool: "select",
    highlightColor: "yellow",
    highlightPointer: null,
    currentPage: 1,
    zoom: 1,
    fitMode: true,
    selectedId: null,
    tool: "select",
    filter: "all",
    search: "",
    labelsVisible: false,
    history: [],
    future: [],
    nextCustomId: 1,
    inspectorBefore: null,
    inspectorTimer: null,
    pointer: null,
    dragDepth: 0,
    signatureDrawing: false
  };

  const el = {};
  const ids = [
    "app-shell", "btn-upload", "file-upload", "document-select", "document-title", "document-meta",
    "btn-undo", "btn-redo", "btn-shortcuts", "btn-theme", "profile-select", "btn-autofill", "btn-export",
    "btn-redetect", "score-ring", "health-score", "health-copy", "metric-fields", "metric-reviewed", "metric-filled",
    "field-search", "filter-tabs", "count-all", "count-review", "count-filled", "type-summary", "field-list",
    "btn-review-next", "btn-toggle-labels", "btn-prev-page", "page-indicator", "btn-next-page", "canvas-viewport",
    "canvas-stage-wrap", "canvas-stage", "pdf-page", "vector-layer", "annotation-layer", "field-layer", "value-layer", "canvas-empty",
    "btn-empty-upload", "loading-overlay", "loading-title", "loading-detail", "status-dot", "status-message",
    "btn-zoom-out", "btn-zoom-fit", "zoom-indicator", "btn-zoom-in", "btn-close-inspector", "inspector-empty",
    "inspector-form", "selected-kind-icon", "selected-field-name", "selected-field-id", "btn-focus-field",
    "confidence-value", "confidence-bar", "confidence-copy", "field-kind", "field-label", "value-field", "field-value",
    "boolean-field", "field-checked", "signature-field", "btn-draw-signature", "field-required", "field-reviewed",
    "field-x", "field-y", "field-width", "field-height", "field-font-size", "field-color", "field-source",
    "btn-delete-field", "drop-overlay", "signature-modal", "signature-canvas", "btn-close-signature",
    "btn-clear-signature", "btn-apply-signature", "shortcuts-modal", "btn-close-shortcuts", "toast-stack",
    "btn-mode-edit", "btn-mode-read", "edit-tools", "read-tools", "editing-panel", "reading-panel",
    "pdf-search-form", "pdf-search", "pdf-search-count", "pdf-search-results", "annotation-count", "annotation-list",
    "highlight-palette", "annotation-inspector", "annotation-empty", "annotation-form", "annotation-color",
    "annotation-text", "annotation-note", "btn-delete-annotation"
  ];
  ids.forEach((id) => { el[toCamel(id)] = document.getElementById(id); });

  function toCamel(value) {
    return value.replace(/-([a-z])/g, (_, char) => char.toUpperCase());
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function round(value, digits = 2) {
    const factor = 10 ** digits;
    return Math.round(Number(value) * factor) / factor;
  }

  function deepClone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function currentPageData() {
    return state.documentData?.pages?.[state.currentPage - 1] || null;
  }

  function currentFields() {
    return state.slots[String(state.currentPage)] || [];
  }

  function allFields() {
    const fields = [];
    Object.entries(state.slots).forEach(([page, pageFields]) => {
      pageFields.forEach((field) => fields.push({ field, page: Number(page) }));
    });
    return fields;
  }

  function selectedField() {
    if (!state.selectedId) return null;
    return allFields().find(({ field }) => field.id === state.selectedId)?.field || null;
  }

  function fieldPage(fieldId) {
    return allFields().find(({ field }) => field.id === fieldId)?.page || null;
  }

  function fieldKind(field) {
    if (FIELD_META[field.field_kind]) return field.field_kind;
    if (field.slot_type === "checkbox") return "checkbox";
    if (field.slot_type === "signature") return "signature";
    if (field.slot_type === "cell") return "cell";
    if (field.slot_type === "comb_box") return "comb";
    return field.is_table_cell ? "table_cell" : "text";
  }

  function fieldMeta(field) {
    return FIELD_META[fieldKind(field)] || FIELD_META.text;
  }

  function isBooleanField(field) {
    return ["checkbox", "radio"].includes(fieldKind(field));
  }

  function isFilled(field) {
    if (isBooleanField(field)) return Boolean(field.checked);
    return Boolean(String(field.value || "").trim());
  }

  function needsReview(field) {
    return field.review_status !== "confirmed" || Number(field.detection_confidence || 0) < 0.8;
  }

  function normalizeField(field) {
    const normalized = field;
    normalized.field_kind = fieldKind(normalized);
    normalized.detection_source = normalized.detection_source || "geometry";
    normalized.detection_confidence = Number(normalized.detection_confidence ?? 0.7);
    normalized.review_status = normalized.review_status || "unreviewed";
    normalized.required = Boolean(normalized.required);
    normalized.value = normalized.value == null ? "" : String(normalized.value);
    normalized.font_size = Number(normalized.font_size || 9.5);
    normalized.font_color = normalized.font_color || "#172554";
    normalized.align = normalized.align || "left";
    normalized.rect = normalized.rect.map(Number);
    updateDerivedGeometry(normalized);
    return normalized;
  }

  function normalizeSlots(slots) {
    Object.values(slots || {}).forEach((pageFields) => pageFields.forEach(normalizeField));
    return slots || {};
  }

  function updateDerivedGeometry(field) {
    const [x0, y0, x1, y1] = field.rect;
    field.width = round(x1 - x0);
    field.height = round(y1 - y0);
    if (["checkbox", "radio"].includes(fieldKind(field))) {
      field.centroid = [round((x0 + x1) / 2), round((y0 + y1) / 2)];
      field.mark_style = fieldKind(field) === "radio" ? "dot" : (field.mark_style || "tick");
    } else {
      field.baseline_y = round(y1 - Math.max(1.5, Math.min(3, field.height * 0.12)));
    }
    if (fieldKind(field) === "comb") {
      const count = Math.max(2, Number(field.box_count) || Math.round(field.width / 18));
      const cellWidth = field.width / count;
      field.box_count = count;
      field.box_centroids = Array.from({ length: count }, (_, index) => [
        round(x0 + cellWidth * index + cellWidth / 2),
        round(y0 + field.height / 2)
      ]);
      field.box_rects = Array.from({ length: count }, (_, index) => [
        round(x0 + cellWidth * index), y0, round(x0 + cellWidth * (index + 1)), y1
      ]);
    }
  }

  function snapshot() {
    return JSON.stringify(state.slots);
  }

  function commitHistory(before) {
    const after = snapshot();
    if (!before || before === after) return;
    state.history.push(before);
    if (state.history.length > 60) state.history.shift();
    state.future = [];
    updateHistoryButtons();
  }

  function updateHistoryButtons() {
    el.btnUndo.disabled = state.history.length === 0;
    el.btnRedo.disabled = state.future.length === 0;
  }

  function undo() {
    if (!state.history.length) return;
    state.future.push(snapshot());
    state.slots = normalizeSlots(JSON.parse(state.history.pop()));
    if (state.selectedId && !selectedField()) state.selectedId = null;
    renderWorkspace();
    updateHistoryButtons();
    setStatus("Change undone");
  }

  function redo() {
    if (!state.future.length) return;
    state.history.push(snapshot());
    state.slots = normalizeSlots(JSON.parse(state.future.pop()));
    if (state.selectedId && !selectedField()) state.selectedId = null;
    renderWorkspace();
    updateHistoryButtons();
    setStatus("Change restored");
  }

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json") ? await response.json() : null;
    if (!response.ok) {
      throw new Error(data?.detail || `Request failed (${response.status})`);
    }
    return data;
  }

  function setLoading(visible, title = "Reading document structure", detail = "Finding lines, boxes, cells, and native fields…") {
    el.loadingOverlay.hidden = !visible;
    el.loadingTitle.textContent = title;
    el.loadingDetail.textContent = detail;
    el.btnExport.disabled = visible || !state.documentName;
  }

  function setStatus(message, mode = "ready") {
    el.statusMessage.textContent = message;
    el.statusDot.classList.toggle("busy", mode === "busy");
    el.statusDot.classList.toggle("error", mode === "error");
  }

  function toast(title, message = "", mode = "info") {
    const item = document.createElement("div");
    item.className = `toast ${mode}`;
    const copy = document.createElement("div");
    const heading = document.createElement("strong");
    heading.textContent = title;
    copy.appendChild(heading);
    if (message) {
      const body = document.createElement("span");
      body.textContent = message;
      copy.appendChild(body);
    }
    item.appendChild(copy);
    el.toastStack.appendChild(item);
    window.setTimeout(() => item.remove(), 4200);
  }

  async function loadDocuments(preferredName = "") {
    try {
      const data = await api("api/documents");
      const documents = data.documents || [];
      el.documentSelect.replaceChildren();
      documents.forEach((name) => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        el.documentSelect.appendChild(option);
      });
      const stored = window.localStorage.getItem("tracer.lastDocument");
      const fallback = documents[0];
      const target = documents.includes(preferredName) ? preferredName : (documents.includes(stored) ? stored : fallback);
      if (target) {
        el.documentSelect.value = target;
        await loadDocument(target);
      } else {
        showEmptyCanvas();
      }
    } catch (error) {
      setLoading(false);
      showEmptyCanvas();
      setStatus(error.message, "error");
      toast("Could not load documents", error.message, "error");
    }
  }

  async function loadDocument(filename) {
    if (!filename) return;
    setLoading(true, "Identifying fields", `Analysing ${filename}`);
    setStatus("Detecting fields and document structure…", "busy");
    state.selectedId = null;
    state.currentPage = 1;
    state.history = [];
    state.future = [];
    state.annotations = [];
    state.searchHits = [];
    state.selectedAnnotationId = null;
    updateHistoryButtons();

    try {
      const data = await api("api/unbundle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename })
      });
      state.documentName = filename;
      state.documentData = data;
      state.slots = normalizeSlots(data.slots);
      state.fitMode = true;
      el.documentSelect.value = filename;
      el.documentTitle.textContent = filename;
      el.documentMeta.textContent = `${data.page_count} page${data.page_count === 1 ? "" : "s"} · ${data.total_slots} detected fields · Local processing`;
      window.localStorage.setItem("tracer.lastDocument", filename);
      el.canvasEmpty.hidden = true;
      renderWorkspace();
      await renderPage(true);
      setLoading(false);
      const uncertain = allFields().filter(({ field }) => needsReview(field)).length;
      setStatus(`${data.total_slots} fields identified · ${uncertain} ready for review`);
      toast("Document ready", `${data.total_slots} fields across ${data.page_count} page${data.page_count === 1 ? "" : "s"}.`, "success");
    } catch (error) {
      setLoading(false);
      setStatus(error.message, "error");
      toast("Detection failed", error.message, "error");
    }
  }

  function showEmptyCanvas() {
    state.documentName = "";
    state.documentData = null;
    state.slots = {};
    el.canvasEmpty.hidden = false;
    el.loadingOverlay.hidden = true;
    el.documentTitle.textContent = "No PDF open";
    el.documentMeta.textContent = "Add a document to start";
    renderWorkspace();
  }

  async function uploadFile(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      toast("Choose a PDF", "Only PDF documents can be opened in this workspace.", "warning");
      return;
    }
    setLoading(true, "Adding PDF", `Uploading ${file.name} to the local workspace…`);
    setStatus("Adding document…", "busy");
    try {
      const formData = new FormData();
      formData.append("file", file);
      const data = await api("api/upload", { method: "POST", body: formData });
      await loadDocuments(data.filename);
    } catch (error) {
      setLoading(false);
      setStatus(error.message, "error");
      toast("Upload failed", error.message, "error");
    } finally {
      el.fileUpload.value = "";
    }
  }

  function renderWorkspace() {
    renderInventory();
    renderFieldLayer();
    renderAnnotationLayer();
    renderValueLayer();
    renderInspector();
    renderReadingPanels();
    updatePageControls();
  }

  async function renderPage(shouldFit = false) {
    const page = currentPageData();
    if (!page) return;
    el.canvasStage.style.width = `${page.width}px`;
    el.canvasStage.style.height = `${page.height}px`;
    el.pdfPage.src = `api/page-render/${encodeURIComponent(state.documentName)}/${state.currentPage}?dpi=150`;
    el.pdfPage.alt = `Page ${state.currentPage} of ${state.documentName}`;
    updatePageControls();
    renderFieldLayer();
    renderValueLayer();
    if (shouldFit || state.fitMode) {
      await waitForImage(el.pdfPage);
      fitPage();
    } else {
      applyZoom();
    }
  }

  function waitForImage(image) {
    if (image.complete && image.naturalWidth) return Promise.resolve();
    return new Promise((resolve) => {
      const done = () => resolve();
      image.addEventListener("load", done, { once: true });
      image.addEventListener("error", done, { once: true });
    });
  }

  function fitPage() {
    const page = currentPageData();
    if (!page) return;
    const rect = el.canvasViewport.getBoundingClientRect();
    const availableWidth = Math.max(180, rect.width - 86);
    const availableHeight = Math.max(180, rect.height - 124);
    state.zoom = clamp(Math.min(availableWidth / page.width, availableHeight / page.height), 0.25, 1.35);
    state.fitMode = true;
    applyZoom();
  }

  function applyZoom() {
    const page = currentPageData();
    if (!page) return;
    el.canvasStage.style.transform = `scale(${state.zoom})`;
    el.canvasStageWrap.style.width = `${page.width * state.zoom}px`;
    el.canvasStageWrap.style.height = `${page.height * state.zoom}px`;
    el.zoomIndicator.textContent = `${Math.round(state.zoom * 100)}%`;
  }

  function setZoom(nextZoom) {
    state.zoom = clamp(nextZoom, 0.25, 2.5);
    state.fitMode = false;
    applyZoom();
  }

  function updatePageControls() {
    const count = state.documentData?.page_count || 0;
    el.pageIndicator.textContent = count ? `Page ${state.currentPage} of ${count}` : "Page — of —";
    el.btnPrevPage.disabled = state.currentPage <= 1;
    el.btnNextPage.disabled = !count || state.currentPage >= count;
  }

  async function changePage(nextPage) {
    const count = state.documentData?.page_count || 0;
    if (!count) return;
    state.currentPage = clamp(nextPage, 1, count);
    state.selectedId = null;
    document.body.classList.remove("inspector-open");
    renderWorkspace();
    await renderPage(false);
    el.canvasViewport.scrollTo({ top: 0, left: 0, behavior: "smooth" });
    setStatus(`Viewing page ${state.currentPage} of ${count}`);
  }

  function renderInventory() {
    const entries = allFields();
    const total = entries.length;
    const reviewed = entries.filter(({ field }) => field.review_status === "confirmed").length;
    const filled = entries.filter(({ field }) => isFilled(field)).length;
    const uncertain = entries.filter(({ field }) => needsReview(field)).length;
    const average = total
      ? Math.round(entries.reduce((sum, { field }) => sum + Number(field.detection_confidence || 0), 0) / total * 100)
      : 0;

    el.metricFields.textContent = String(total);
    el.metricReviewed.textContent = `${total ? Math.round(reviewed / total * 100) : 0}%`;
    el.metricFilled.textContent = `${total ? Math.round(filled / total * 100) : 0}%`;
    el.healthScore.textContent = total ? `${average}%` : "—";
    el.scoreRing.style.setProperty("--score", `${average * 3.6}deg`);
    el.healthCopy.textContent = total
      ? (uncertain ? `${uncertain} field${uncertain === 1 ? "" : "s"} need a quick check` : "Every field is confirmed")
      : "No detected fields";
    el.countAll.textContent = String(total);
    el.countReview.textContent = String(uncertain);
    el.countFilled.textContent = String(filled);

    renderTypeSummary(entries);
    el.fieldList.replaceChildren();

    const query = state.search.trim().toLowerCase();
    const visible = entries.filter(({ field }) => {
      const matchesSearch = !query || `${field.label || ""} ${fieldKind(field)} ${field.semantic_uri || ""}`.toLowerCase().includes(query);
      if (!matchesSearch) return false;
      if (state.filter === "review") return needsReview(field);
      if (state.filter === "filled") return isFilled(field);
      return true;
    });

    if (!visible.length) {
      const empty = document.createElement("div");
      empty.className = "field-list-empty";
      const copy = document.createElement("div");
      const heading = document.createElement("strong");
      const body = document.createElement("p");
      heading.textContent = total ? "No matching fields" : "No fields identified";
      body.textContent = total ? "Try another filter or search term." : "Use a field tool to add one directly to the page.";
      copy.append(heading, body);
      empty.appendChild(copy);
      el.fieldList.appendChild(empty);
      return;
    }

    let previousPage = null;
    visible.forEach(({ field, page }) => {
      if (page !== previousPage) {
        const group = document.createElement("div");
        group.className = "field-group-label";
        const label = document.createElement("span");
        const count = document.createElement("span");
        label.textContent = `Page ${page}`;
        count.textContent = `${visible.filter((entry) => entry.page === page).length} fields`;
        group.append(label, count);
        el.fieldList.appendChild(group);
        previousPage = page;
      }
      el.fieldList.appendChild(createFieldListItem(field, page));
    });
  }

  function renderTypeSummary(entries) {
    const counts = new Map();
    entries.forEach(({ field }) => counts.set(fieldKind(field), (counts.get(fieldKind(field)) || 0) + 1));
    const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
    el.typeSummary.replaceChildren();
    sorted.slice(0, 5).forEach(([kind, count]) => {
      const meta = FIELD_META[kind] || FIELD_META.text;
      const chip = document.createElement("span");
      chip.className = "type-chip";
      chip.style.setProperty("--chip-color", meta.color);
      chip.textContent = `${meta.label} ${count}`;
      el.typeSummary.appendChild(chip);
    });
  }

  function createFieldListItem(field, page) {
    const meta = fieldMeta(field);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "field-list-item";
    if (field.id === state.selectedId) button.classList.add("active");
    if (needsReview(field)) button.classList.add("needs-review");
    button.style.setProperty("--item-color", meta.color);
    button.dataset.fieldId = field.id;

    const icon = document.createElement("span");
    icon.className = "field-list-icon";
    icon.textContent = meta.icon;

    const copy = document.createElement("span");
    copy.className = "field-list-copy";
    const title = document.createElement("strong");
    title.textContent = field.label || "Untitled field";
    const detail = document.createElement("span");
    const confidence = Math.round(Number(field.detection_confidence || 0) * 100);
    detail.textContent = `${meta.label} · ${confidence}% confidence`;
    copy.append(title, detail);

    const fieldState = document.createElement("span");
    fieldState.className = "field-list-state";
    fieldState.textContent = isFilled(field) ? "✓" : "";
    button.append(icon, copy, fieldState);
    button.addEventListener("click", () => selectField(field.id, page, true));
    return button;
  }

  function renderFieldLayer() {
    el.fieldLayer.replaceChildren();
    el.canvasStage.className = `canvas-stage mode-${state.mode} tool-${state.tool} read-tool-${state.readTool}${state.labelsVisible ? "" : " labels-hidden"}`;
    if (state.mode !== "edit") return;
    currentFields().forEach((field) => {
      const [x0, y0, x1, y1] = field.rect;
      const box = document.createElement("div");
      const kind = fieldKind(field);
      box.className = `field-box kind-${kind}`;
      if (field.id === state.selectedId) box.classList.add("selected");
      if (Number(field.detection_confidence || 0) < 0.8) box.classList.add("low-confidence");
      if (field.review_status === "confirmed") box.classList.add("reviewed");
      box.dataset.fieldId = field.id;
      box.style.left = `${x0}px`;
      box.style.top = `${y0}px`;
      box.style.width = `${x1 - x0}px`;
      box.style.height = `${y1 - y0}px`;

      const label = document.createElement("span");
      label.className = "field-box-label";
      label.textContent = field.label || fieldMeta(field).label;
      const handle = document.createElement("span");
      handle.className = "resize-handle";
      handle.dataset.resizeHandle = "true";
      box.append(label, handle);
      box.addEventListener("click", (event) => {
        event.stopPropagation();
        selectField(field.id, state.currentPage, false);
      });
      box.addEventListener("pointerdown", (event) => beginPointerEdit(event, field, box));
      el.fieldLayer.appendChild(box);
    });
  }

  function selectedAnnotation() {
    return state.annotations.find((annotation) => annotation.id === state.selectedAnnotationId) || null;
  }

  function renderAnnotationLayer() {
    el.annotationLayer.replaceChildren();
    if (state.mode !== "read") return;

    state.searchHits.filter((hit) => hit.page === state.currentPage).forEach((hit) => {
      const marker = document.createElement("button");
      marker.type = "button";
      marker.className = "search-hit-marker";
      const [x0, y0, x1, y1] = hit.rect;
      Object.assign(marker.style, { left: `${x0}px`, top: `${y0}px`, width: `${x1 - x0}px`, height: `${y1 - y0}px` });
      marker.title = `Search result: ${hit.text}`;
      marker.addEventListener("click", (event) => {
        event.stopPropagation();
        addHighlight({ page: hit.page, rects: [hit.rect], text: hit.text, source: "text_search" });
      });
      el.annotationLayer.appendChild(marker);
    });

    state.annotations.filter((annotation) => annotation.page === state.currentPage).forEach((annotation) => {
      (annotation.rects || []).forEach((rect) => {
        const marker = document.createElement("button");
        marker.type = "button";
        marker.className = `annotation-marker color-${annotation.color || "yellow"}`;
        if (annotation.id === state.selectedAnnotationId) marker.classList.add("selected");
        const [x0, y0, x1, y1] = rect;
        Object.assign(marker.style, { left: `${x0}px`, top: `${y0}px`, width: `${x1 - x0}px`, height: `${y1 - y0}px` });
        marker.title = annotation.note || annotation.text || "PDF highlight";
        marker.addEventListener("click", (event) => {
          event.stopPropagation();
          state.selectedAnnotationId = annotation.id;
          renderWorkspace();
        });
        el.annotationLayer.appendChild(marker);
      });
    });

    if (state.highlightPointer?.rect && state.highlightPointer.page === state.currentPage) {
      const [x0, y0, x1, y1] = state.highlightPointer.rect;
      const draft = document.createElement("div");
      draft.className = `annotation-marker draft color-${state.highlightColor}`;
      Object.assign(draft.style, { left: `${x0}px`, top: `${y0}px`, width: `${x1 - x0}px`, height: `${y1 - y0}px` });
      el.annotationLayer.appendChild(draft);
    }
  }

  function renderReadingPanels() {
    if (!el.readingPanel) return;
    el.editingPanel.hidden = state.mode !== "edit";
    el.readingPanel.hidden = state.mode !== "read";
    el.inspectorForm.hidden = state.mode !== "edit" || !selectedField();
    el.inspectorEmpty.hidden = state.mode !== "edit" || Boolean(selectedField());
    el.annotationInspector.hidden = state.mode !== "read";

    el.pdfSearchCount.textContent = String(state.searchHits.length);
    el.annotationCount.textContent = String(state.annotations.length);
    el.pdfSearchResults.replaceChildren();
    if (!state.searchHits.length) {
      const empty = document.createElement("p");
      empty.textContent = "Search the document text layer.";
      el.pdfSearchResults.appendChild(empty);
    } else {
      state.searchHits.forEach((hit) => {
        const row = document.createElement("div");
        row.className = "search-result-row";
        const focus = document.createElement("button");
        focus.type = "button";
        focus.innerHTML = `<strong>Page ${hit.page}</strong><span></span>`;
        focus.querySelector("span").textContent = hit.text;
        focus.addEventListener("click", () => focusPageRect(hit.page, hit.rect));
        const add = document.createElement("button");
        add.type = "button";
        add.className = "result-highlight-button";
        add.textContent = "Highlight";
        add.addEventListener("click", () => addHighlight({ page: hit.page, rects: [hit.rect], text: hit.text, source: "text_search" }));
        row.append(focus, add);
        el.pdfSearchResults.appendChild(row);
      });
    }

    el.annotationList.replaceChildren();
    if (!state.annotations.length) {
      const empty = document.createElement("p");
      empty.textContent = "No highlights yet.";
      el.annotationList.appendChild(empty);
    } else {
      state.annotations.forEach((annotation) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `annotation-list-item color-${annotation.color}`;
        if (annotation.id === state.selectedAnnotationId) button.classList.add("active");
        const title = document.createElement("strong");
        title.textContent = `Page ${annotation.page} · ${annotation.text || "Region highlight"}`;
        const detail = document.createElement("span");
        detail.textContent = annotation.note || `${annotation.color} highlight`;
        button.append(title, detail);
        button.addEventListener("click", () => {
          state.selectedAnnotationId = annotation.id;
          focusPageRect(annotation.page, annotation.rects[0]);
          renderWorkspace();
        });
        el.annotationList.appendChild(button);
      });
    }

    const annotation = selectedAnnotation();
    el.annotationEmpty.hidden = Boolean(annotation);
    el.annotationForm.hidden = !annotation;
    if (annotation) {
      el.annotationColor.value = annotation.color || "yellow";
      el.annotationText.value = annotation.text || "Region highlight";
      el.annotationNote.value = annotation.note || "";
    }
  }

  function renderValueLayer() {
    el.valueLayer.replaceChildren();
    currentFields().forEach((field) => {
      if (!isFilled(field)) return;
      const [x0, y0, x1, y1] = field.rect;
      const preview = document.createElement("div");
      const kind = fieldKind(field);
      preview.className = `value-preview ${kind}-preview`;
      preview.style.left = `${x0 + 2}px`;
      preview.style.top = `${y0 + 1}px`;
      preview.style.width = `${Math.max(1, x1 - x0 - 4)}px`;
      preview.style.height = `${Math.max(1, y1 - y0 - 2)}px`;
      preview.style.fontSize = `${field.font_size || 9.5}px`;
      preview.style.color = field.font_color || "#172554";

      if (kind === "signature" && field.value) {
        const image = document.createElement("img");
        image.src = field.value.startsWith("data:") ? field.value : `data:image/png;base64,${field.value}`;
        image.alt = "";
        preview.appendChild(image);
      } else if (kind === "checkbox") {
        preview.textContent = "✓";
      } else if (kind === "radio") {
        preview.textContent = "●";
      } else {
        preview.textContent = field.value || "";
      }
      el.valueLayer.appendChild(preview);
    });
  }

  async function selectField(fieldId, page = state.currentPage, focusCanvas = false) {
    if (page !== state.currentPage) {
      state.currentPage = page;
      await renderPage(false);
    }
    state.selectedId = fieldId;
    setTool("select");
    document.body.classList.add("inspector-open");
    renderWorkspace();
    if (focusCanvas) focusSelectedField();
    const field = selectedField();
    if (field) setStatus(`${field.label} selected · ${fieldMeta(field).label}`);
  }

  function focusSelectedField() {
    const field = selectedField();
    if (!field) return;
    const [x0, y0, x1, y1] = field.rect;
    const targetLeft = (x0 + x1) / 2 * state.zoom - el.canvasViewport.clientWidth / 2;
    const targetTop = (y0 + y1) / 2 * state.zoom - el.canvasViewport.clientHeight / 2;
    el.canvasViewport.scrollTo({ left: Math.max(0, targetLeft), top: Math.max(0, targetTop), behavior: "smooth" });
  }

  function renderInspector() {
    const field = selectedField();
    el.inspectorEmpty.hidden = Boolean(field);
    el.inspectorForm.hidden = !field;
    if (!field) return;

    const meta = fieldMeta(field);
    const kind = fieldKind(field);
    const confidence = Math.round(Number(field.detection_confidence || 0) * 100);
    const page = fieldPage(field) || state.currentPage;
    const [x0, y0, x1, y1] = field.rect;

    el.selectedKindIcon.textContent = meta.icon;
    el.selectedKindIcon.style.setProperty("--item-color", meta.color);
    el.selectedFieldName.textContent = field.label || "Untitled field";
    el.selectedFieldId.textContent = `Page ${page} · ${field.id}`;
    el.confidenceValue.textContent = `${confidence}%`;
    el.confidenceBar.style.width = `${confidence}%`;
    el.confidenceBar.dataset.level = confidence >= 90 ? "high" : (confidence >= 75 ? "medium" : "low");
    el.confidenceValue.dataset.level = el.confidenceBar.dataset.level;
    el.confidenceCopy.textContent = field.review_status === "confirmed"
      ? "Confirmed by you."
      : (confidence >= 90 ? "Strong automatic match. Confirm when ready." : "Check the type, label, and position.");
    el.fieldKind.value = kind;
    el.fieldLabel.value = field.label || "";
    el.fieldValue.value = isBooleanField(field) ? "" : (field.value || "");
    el.valueField.hidden = isBooleanField(field) || kind === "signature";
    el.booleanField.hidden = !isBooleanField(field);
    el.signatureField.hidden = kind !== "signature";
    el.fieldChecked.setAttribute("aria-checked", String(Boolean(field.checked)));
    el.fieldChecked.querySelector("strong").textContent = field.checked ? "Checked" : "Unchecked";
    el.fieldRequired.checked = Boolean(field.required);
    el.fieldReviewed.checked = field.review_status === "confirmed";
    el.fieldX.value = round(x0, 1);
    el.fieldY.value = round(y0, 1);
    el.fieldWidth.value = round(x1 - x0, 1);
    el.fieldHeight.value = round(y1 - y0, 1);
    el.fieldFontSize.value = field.font_size || 9.5;
    el.fieldColor.value = validHexColor(field.font_color) ? field.font_color : "#172554";
    el.fieldSource.textContent = field.detection_source || "geometry";

    const inputType = { email: "email", phone: "tel", date: "date", number: "text" }[kind] || "text";
    el.fieldValue.type = inputType;
    el.fieldValue.inputMode = kind === "number" ? "numeric" : "text";
    el.fieldValue.maxLength = kind === "cell" ? 1 : 524288;
  }

  function validHexColor(value) {
    return /^#[0-9a-f]{6}$/i.test(value || "");
  }

  function beginPointerEdit(event, field, box) {
    if (state.tool !== "select") return;
    event.preventDefault();
    event.stopPropagation();
    if (state.selectedId !== field.id) {
      state.selectedId = field.id;
      document.body.classList.add("inspector-open");
      el.fieldLayer.querySelectorAll(".field-box.selected").forEach((item) => item.classList.remove("selected"));
      box.classList.add("selected");
      renderInventory();
      renderInspector();
    }
    const isResize = Boolean(event.target.closest("[data-resize-handle]"));
    state.pointer = {
      id: event.pointerId,
      field,
      element: box,
      mode: isResize ? "resize" : "move",
      startX: event.clientX,
      startY: event.clientY,
      startRect: [...field.rect],
      before: snapshot(),
      changed: false
    };
    box.setPointerCapture?.(event.pointerId);
  }

  function movePointerEdit(event) {
    const pointer = state.pointer;
    if (!pointer || (event.pointerId != null && event.pointerId !== pointer.id)) return;
    const page = currentPageData();
    if (!page) return;
    const dx = (event.clientX - pointer.startX) / state.zoom;
    const dy = (event.clientY - pointer.startY) / state.zoom;
    let [x0, y0, x1, y1] = pointer.startRect;

    if (pointer.mode === "move") {
      const width = x1 - x0;
      const height = y1 - y0;
      x0 = clamp(x0 + dx, 0, page.width - width);
      y0 = clamp(y0 + dy, 0, page.height - height);
      x1 = x0 + width;
      y1 = y0 + height;
    } else {
      x1 = clamp(x1 + dx, x0 + 5, page.width);
      y1 = clamp(y1 + dy, y0 + 5, page.height);
    }
    pointer.field.rect = [round(x0), round(y0), round(x1), round(y1)];
    updateDerivedGeometry(pointer.field);
    pointer.element.style.left = `${x0}px`;
    pointer.element.style.top = `${y0}px`;
    pointer.element.style.width = `${x1 - x0}px`;
    pointer.element.style.height = `${y1 - y0}px`;
    pointer.changed = true;
  }

  function endPointerEdit(event) {
    const pointer = state.pointer;
    if (!pointer || (event.pointerId != null && event.pointerId !== pointer.id)) return;
    if (pointer.changed) {
      commitHistory(pointer.before);
      renderWorkspace();
      setStatus(`${pointer.field.label} ${pointer.mode === "move" ? "moved" : "resized"}`);
    }
    state.pointer = null;
  }

  function setMode(mode) {
    state.mode = mode === "read" ? "read" : "edit";
    state.selectedId = null;
    state.selectedAnnotationId = null;
    el.btnModeEdit.classList.toggle("active", state.mode === "edit");
    el.btnModeRead.classList.toggle("active", state.mode === "read");
    el.btnModeEdit.setAttribute("aria-pressed", String(state.mode === "edit"));
    el.btnModeRead.setAttribute("aria-pressed", String(state.mode === "read"));
    el.editTools.hidden = state.mode !== "edit";
    el.readTools.hidden = state.mode !== "read";
    document.body.classList.toggle("reading-mode", state.mode === "read");
    renderWorkspace();
    setStatus(state.mode === "read" ? "Reading mode · search or highlight the document" : "Edit mode · detect, add, and fill fields");
  }

  function setReadTool(tool) {
    state.readTool = tool;
    document.querySelectorAll("[data-read-tool]").forEach((button) => {
      const active = button.dataset.readTool === tool;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    renderAnnotationLayer();
    setStatus(tool === "highlight" ? "Drag across a meaningful passage or region to highlight it" : "Select a search result or existing highlight");
  }

  async function searchDocument() {
    const query = el.pdfSearch.value.trim();
    if (!query || !state.documentName) return;
    setStatus(`Searching for “${query}”…`, "busy");
    try {
      const data = await api("api/search-document", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: state.documentName, query, max_results: 300 })
      });
      state.searchHits = data.hits || [];
      renderWorkspace();
      if (state.searchHits[0]) await focusPageRect(state.searchHits[0].page, state.searchHits[0].rect);
      setStatus(`${state.searchHits.length} text match${state.searchHits.length === 1 ? "" : "es"}`);
    } catch (error) {
      setStatus(error.message, "error");
      toast("Search failed", error.message, "error");
    }
  }

  async function focusPageRect(page, rect) {
    if (page !== state.currentPage) await changePage(page);
    const [x0, y0, x1, y1] = rect;
    const targetLeft = (x0 + x1) / 2 * state.zoom - el.canvasViewport.clientWidth / 2;
    const targetTop = (y0 + y1) / 2 * state.zoom - el.canvasViewport.clientHeight / 2;
    el.canvasViewport.scrollTo({ left: Math.max(0, targetLeft), top: Math.max(0, targetTop), behavior: "smooth" });
  }

  function addHighlight({ page, rects, text = "", source = "manual_region" }) {
    const annotation = {
      id: `ann_${Date.now()}_${state.annotations.length + 1}`,
      page,
      rects: rects.map((rect) => rect.map((value) => round(value))),
      text,
      note: "",
      color: state.highlightColor,
      type: "highlight",
      source,
      confidence: source === "text_search" ? 1 : 0.9
    };
    state.annotations.push(annotation);
    state.selectedAnnotationId = annotation.id;
    state.searchHits = state.searchHits.filter((hit) => !(hit.page === page && JSON.stringify(hit.rect) === JSON.stringify(rects[0])));
    renderWorkspace();
    focusPageRect(page, rects[0]);
    toast("Highlight added", `Page ${page} · ${annotation.color}`, "success");
  }

  function pagePoint(event) {
    const page = currentPageData();
    if (!page) return null;
    const bounds = el.canvasStage.getBoundingClientRect();
    return {
      x: clamp((event.clientX - bounds.left) / state.zoom, 0, page.width),
      y: clamp((event.clientY - bounds.top) / state.zoom, 0, page.height)
    };
  }

  function beginHighlight(event) {
    if (state.mode !== "read" || state.readTool !== "highlight" || event.target.closest(".annotation-marker, .search-hit-marker")) return;
    const point = pagePoint(event);
    if (!point) return;
    event.preventDefault();
    state.highlightPointer = { pointerId: event.pointerId, page: state.currentPage, start: point, rect: [point.x, point.y, point.x, point.y] };
    el.canvasStage.setPointerCapture?.(event.pointerId);
  }

  function moveHighlight(event) {
    const pointer = state.highlightPointer;
    if (!pointer || event.pointerId !== pointer.pointerId) return;
    const point = pagePoint(event);
    if (!point) return;
    pointer.rect = [Math.min(pointer.start.x, point.x), Math.min(pointer.start.y, point.y), Math.max(pointer.start.x, point.x), Math.max(pointer.start.y, point.y)].map((value) => round(value));
    renderAnnotationLayer();
  }

  function endHighlight(event) {
    const pointer = state.highlightPointer;
    if (!pointer || event.pointerId !== pointer.pointerId) return;
    state.highlightPointer = null;
    const rect = pointer.rect;
    if (rect[2] - rect[0] >= 4 && rect[3] - rect[1] >= 3) {
      addHighlight({ page: pointer.page, rects: [rect] });
    } else {
      renderAnnotationLayer();
    }
  }

  function setTool(tool) {
    state.tool = tool;
    document.querySelectorAll("[data-tool]").forEach((button) => {
      const active = button.dataset.tool === tool;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    renderFieldLayer();
    if (tool !== "select") {
      const messages = {
        auto_detect: "Click a missed field or cell group to detect it",
        cell_row: "Click to place a row of eight independent character cells"
      };
      setStatus(messages[tool] || `Click the page to place a ${FIELD_META[tool]?.label.toLowerCase() || tool} field`);
    }
  }

  async function addFieldAt(event) {
    if (state.mode !== "edit") return;
    if (state.tool === "select" || !currentPageData()) {
      if (event.target === el.canvasStage || event.target === el.pdfPage) {
        state.selectedId = null;
        document.body.classList.remove("inspector-open");
        renderWorkspace();
      }
      return;
    }
    if (event.target.closest(".field-box")) return;
    const page = currentPageData();
    const bounds = el.canvasStage.getBoundingClientRect();
    const x = clamp((event.clientX - bounds.left) / state.zoom, 0, page.width - 5);
    const y = clamp((event.clientY - bounds.top) / state.zoom, 0, page.height - 5);
    const kind = state.tool;
    if (kind === "auto_detect") {
      await detectAtPoint(x, y);
      return;
    }
    if (kind === "cell_row") {
      const before = snapshot();
      const groupId = `p${state.currentPage}_manual_cell_group_${state.nextCustomId++}`;
      const cellWidth = 18;
      const cellHeight = 20;
      const count = Math.max(1, Math.min(8, Math.floor((page.width - x) / cellWidth)));
      let firstId = null;
      for (let index = 0; index < count; index += 1) {
        const id = `p${state.currentPage}_custom_${state.nextCustomId++}`;
        firstId ||= id;
        currentFields().push(normalizeField({
          id, field_name: `character_cells_${index + 1}`, label: `Character cells · ${index + 1}`,
          field_kind: "cell", slot_type: "cell", rect: [round(x + index * cellWidth), round(y), round(x + (index + 1) * cellWidth), round(y + cellHeight)],
          value: "", max_length: 1, group_id: groupId, group_index: index, group_size: count,
          group_role: "character_sequence", group_label: "Character cells", font_size: 11,
          font_color: "#172554", align: "center", detection_source: "manual_cell_row",
          detection_confidence: 1, review_status: "confirmed", required: false
        }));
      }
      commitHistory(before);
      state.selectedId = firstId;
      setTool("select");
      renderWorkspace();
      toast("Cell row added", `${count} independent fields were created.`, "success");
      return;
    }
    const dimensions = {
      text: [150, 22], checkbox: [15, 15], radio: [15, 15], date: [110, 22],
      signature: [170, 48], table_cell: [130, 26], cell: [18, 20]
    }[kind] || [140, 22];
    const width = Math.min(dimensions[0], page.width - x);
    const height = Math.min(dimensions[1], page.height - y);
    const before = snapshot();
    const meta = FIELD_META[kind] || FIELD_META.text;
    const id = `p${state.currentPage}_custom_${state.nextCustomId++}`;
    const field = normalizeField({
      id,
      field_name: `custom_${kind}_${state.nextCustomId}`,
      label: `New ${meta.label.toLowerCase()} field`,
      field_kind: kind,
      slot_type: kind === "signature" ? "signature" : (["checkbox", "radio"].includes(kind) ? "checkbox" : (kind === "cell" ? "cell" : "line")),
      rect: [round(x), round(y), round(x + width), round(y + height)],
      value: kind === "checkbox" || kind === "radio" ? "false" : "",
      checked: false,
      font_size: kind === "signature" ? 12 : 9.5,
      font_color: "#172554",
      align: "left",
      is_table_cell: kind === "table_cell",
      max_length: kind === "cell" ? 1 : undefined,
      detection_source: "manual",
      detection_confidence: 1,
      review_status: "confirmed",
      required: false
    });
    currentFields().push(field);
    commitHistory(before);
    state.selectedId = id;
    setTool("select");
    document.body.classList.add("inspector-open");
    renderWorkspace();
    setStatus(`${meta.label} field added · drag or resize to refine placement`);
    toast("Field added", `${meta.label} field placed on page ${state.currentPage}.`, "success");
  }

  async function detectAtPoint(x, y) {
    const before = snapshot();
    setStatus("Inspecting local PDF geometry…", "busy");
    try {
      const data = await api("api/detect-region", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: state.documentName, page: state.currentPage, x, y, radius: 42 })
      });
      const added = [];
      let existingMatch = null;
      (data.fields || []).forEach((candidate) => {
        const match = currentFields().find((field) => {
          const a = field.boundary_rect || field.rect;
          const b = candidate.boundary_rect || candidate.rect;
          return Math.abs(a[0] - b[0]) <= 3 && Math.abs(a[1] - b[1]) <= 3 && Math.abs(a[2] - b[2]) <= 3 && Math.abs(a[3] - b[3]) <= 3;
        });
        if (match) {
          existingMatch ||= match;
          return;
        }
        const field = normalizeField(deepClone(candidate));
        field.id = `p${state.currentPage}_autodetect_${state.nextCustomId++}`;
        field.detection_source = `click_${field.detection_source || "geometry"}`;
        currentFields().push(field);
        added.push(field);
      });

      if (!added.length && !existingMatch) {
        const id = `p${state.currentPage}_custom_${state.nextCustomId++}`;
        const field = normalizeField({
          id, field_name: `cell_${state.nextCustomId}`, label: "New cell", field_kind: "cell", slot_type: "cell",
          rect: [round(clamp(x - 9, 0, page.width - 18)), round(clamp(y - 10, 0, page.height - 20)), round(clamp(x - 9, 0, page.width - 18) + 18), round(clamp(y - 10, 0, page.height - 20) + 20)], value: "", max_length: 1,
          font_size: 11, font_color: "#172554", align: "center", detection_source: "click_fallback",
          detection_confidence: 0.5, review_status: "unreviewed", required: false
        });
        currentFields().push(field);
        added.push(field);
      }

      commitHistory(before);
      state.selectedId = added[0]?.id || existingMatch?.id || null;
      setTool("select");
      renderWorkspace();
      if (added.length) {
        toast("Geometry detected", `${added.length} independent field${added.length === 1 ? "" : "s"} added.`, "success");
        setStatus(`${added.length} local field${added.length === 1 ? "" : "s"} detected`);
      } else {
        toast("Already detected", "That field is already in the document map.", "info");
        setStatus("Existing field selected");
      }
    } catch (error) {
      setStatus(error.message, "error");
      toast("Local detection failed", error.message, "error");
    }
  }

  function applyKind(field, kind) {
    const previousKind = fieldKind(field);
    field.field_kind = kind;
    field.is_table_cell = kind === "table_cell";
    if (["checkbox", "radio"].includes(kind)) {
      field.slot_type = "checkbox";
      field.checked = Boolean(field.checked);
      field.value = field.checked ? "true" : "false";
      field.mark_style = kind === "radio" ? "dot" : "tick";
    } else if (kind === "signature") {
      field.slot_type = "signature";
      field.checked = false;
      if (["checkbox", "radio"].includes(previousKind) || field.value === "true" || field.value === "false") field.value = "";
    } else if (kind === "cell") {
      field.slot_type = "cell";
      field.max_length = 1;
      field.align = "center";
      field.checked = false;
      field.value = String(field.value || "").slice(0, 1);
    } else if (kind === "comb") {
      field.slot_type = "comb_box";
      field.checked = false;
    } else {
      field.slot_type = "line";
      field.checked = false;
      if (field.value === "false" || field.value === "true") field.value = "";
    }
    updateDerivedGeometry(field);
  }

  function deleteSelected() {
    const field = selectedField();
    if (!field) return;
    const before = snapshot();
    const page = fieldPage(field);
    state.slots[String(page)] = state.slots[String(page)].filter((item) => item.id !== field.id);
    state.selectedId = null;
    document.body.classList.remove("inspector-open");
    commitHistory(before);
    renderWorkspace();
    toast("Field removed", `${field.label} was removed. Undo is available.`, "success");
  }

  function beginInspectorEdit() {
    if (!state.inspectorBefore) state.inspectorBefore = snapshot();
  }

  function endInspectorEdit() {
    window.clearTimeout(state.inspectorTimer);
    state.inspectorTimer = null;
    commitHistory(state.inspectorBefore);
    state.inspectorBefore = null;
  }

  function scheduleInspectorCommit() {
    window.clearTimeout(state.inspectorTimer);
    state.inspectorTimer = window.setTimeout(endInspectorEdit, 350);
  }

  function updateInspectorField(mutator, options = {}) {
    const field = selectedField();
    if (!field) return;
    beginInspectorEdit();
    mutator(field);
    if (options.geometry) updateDerivedGeometry(field);
    renderFieldLayer();
    renderValueLayer();
    renderInventory();
    if (options.inspector !== false) renderInspector();
  }

  async function autofill() {
    if (!state.documentName) return;
    const before = snapshot();
    setLoading(true, "Autofilling recognised fields", "Matching the selected example profile to field labels…");
    setStatus("Autofilling recognised fields…", "busy");
    try {
      const data = await api("api/agentic-fill", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: state.documentName, profile_key: el.profileSelect.value })
      });
      state.slots = normalizeSlots(data.slots);
      commitHistory(before);
      renderWorkspace();
      await renderPage(false);
      const count = allFields().filter(({ field }) => isFilled(field)).length;
      setLoading(false);
      setStatus(`${count} recognised fields filled`);
      toast("Autofill complete", `${count} recognised fields were filled. Review before export.`, "success");
    } catch (error) {
      setLoading(false);
      setStatus(error.message, "error");
      toast("Autofill failed", error.message, "error");
    }
  }

  async function exportPdf() {
    if (!state.documentName) return;
    setLoading(true, "Preparing your PDF", "Writing values at the original document coordinates…");
    setStatus("Exporting filled PDF…", "busy");
    try {
      const data = await api("api/fill-and-rebundle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: state.documentName, slots: state.slots, annotations: state.annotations })
      });
      const anchor = document.createElement("a");
      anchor.href = data.download_url;
      anchor.download = data.output_filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setLoading(false);
      setStatus(`Exported ${data.output_filename}`);
      toast("PDF exported", state.annotations.length ? `${state.annotations.length} true PDF highlight annotation${state.annotations.length === 1 ? "" : "s"} included.` : "Your filled document is ready.", "success");
    } catch (error) {
      setLoading(false);
      setStatus(error.message, "error");
      toast("Export failed", error.message, "error");
    }
  }

  function reviewNext() {
    const next = allFields().find(({ field }) => needsReview(field));
    if (!next) {
      toast("Review complete", "Every detected field has been confirmed.", "success");
      return;
    }
    selectField(next.field.id, next.page, true);
  }

  function openModal(modal) {
    modal.hidden = false;
  }

  function closeModal(modal) {
    modal.hidden = true;
  }

  function initializeSignatureCanvas() {
    const canvas = el.signatureCanvas;
    const context = canvas.getContext("2d");
    context.lineCap = "round";
    context.lineJoin = "round";
    context.lineWidth = 3.5;
    context.strokeStyle = "#172554";

    const clear = () => {
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, canvas.width, canvas.height);
    };
    const position = (event) => {
      const rect = canvas.getBoundingClientRect();
      return {
        x: (event.clientX - rect.left) * (canvas.width / rect.width),
        y: (event.clientY - rect.top) * (canvas.height / rect.height)
      };
    };
    canvas.addEventListener("pointerdown", (event) => {
      state.signatureDrawing = true;
      canvas.setPointerCapture?.(event.pointerId);
      const point = position(event);
      context.beginPath();
      context.moveTo(point.x, point.y);
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!state.signatureDrawing) return;
      const point = position(event);
      context.lineTo(point.x, point.y);
      context.stroke();
    });
    const stop = () => { state.signatureDrawing = false; };
    canvas.addEventListener("pointerup", stop);
    canvas.addEventListener("pointercancel", stop);
    el.btnClearSignature.addEventListener("click", clear);
    el.btnDrawSignature.addEventListener("click", () => { clear(); openModal(el.signatureModal); });
    el.btnCloseSignature.addEventListener("click", () => closeModal(el.signatureModal));
    el.btnApplySignature.addEventListener("click", () => {
      const field = selectedField();
      if (!field || fieldKind(field) !== "signature") return;
      const before = snapshot();
      field.value = canvas.toDataURL("image/png");
      field.review_status = "confirmed";
      commitHistory(before);
      closeModal(el.signatureModal);
      renderWorkspace();
      toast("Signature placed", "Resize or move the field to refine placement.", "success");
    });
    clear();
  }

  function initializeTheme() {
    const stored = window.localStorage.getItem("tracer.theme");
    const theme = stored === "dark" ? "dark" : "light";
    document.body.dataset.theme = theme;
  }

  function toggleTheme() {
    const theme = document.body.dataset.theme === "dark" ? "light" : "dark";
    document.body.dataset.theme = theme;
    window.localStorage.setItem("tracer.theme", theme);
  }

  function bindEvents() {
    el.btnUpload.addEventListener("click", () => el.fileUpload.click());
    el.btnEmptyUpload.addEventListener("click", () => el.fileUpload.click());
    el.fileUpload.addEventListener("change", () => uploadFile(el.fileUpload.files?.[0]));
    el.documentSelect.addEventListener("change", () => loadDocument(el.documentSelect.value));
    el.btnRedetect.addEventListener("click", () => loadDocument(state.documentName));
    el.btnUndo.addEventListener("click", undo);
    el.btnRedo.addEventListener("click", redo);
    el.btnTheme.addEventListener("click", toggleTheme);
    el.btnAutofill.addEventListener("click", autofill);
    el.btnExport.addEventListener("click", exportPdf);
    el.btnReviewNext.addEventListener("click", reviewNext);
    el.btnPrevPage.addEventListener("click", () => changePage(state.currentPage - 1));
    el.btnNextPage.addEventListener("click", () => changePage(state.currentPage + 1));
    el.btnZoomOut.addEventListener("click", () => setZoom(state.zoom - 0.1));
    el.btnZoomIn.addEventListener("click", () => setZoom(state.zoom + 0.1));
    el.btnZoomFit.addEventListener("click", fitPage);
    el.btnFocusField.addEventListener("click", focusSelectedField);
    el.btnDeleteField.addEventListener("click", deleteSelected);
    el.btnCloseInspector.addEventListener("click", () => document.body.classList.remove("inspector-open"));
    el.btnModeEdit.addEventListener("click", () => setMode("edit"));
    el.btnModeRead.addEventListener("click", () => setMode("read"));
    el.pdfSearchForm.addEventListener("submit", (event) => { event.preventDefault(); searchDocument(); });
    document.querySelectorAll("[data-read-tool]").forEach((button) => {
      button.addEventListener("click", () => setReadTool(button.dataset.readTool));
    });
    el.highlightPalette.addEventListener("click", (event) => {
      const button = event.target.closest("[data-highlight-color]");
      if (!button) return;
      state.highlightColor = button.dataset.highlightColor;
      el.highlightPalette.querySelectorAll("[data-highlight-color]").forEach((swatch) => swatch.classList.toggle("active", swatch === button));
      const annotation = selectedAnnotation();
      if (annotation) annotation.color = state.highlightColor;
      renderWorkspace();
    });
    el.annotationColor.addEventListener("change", () => {
      const annotation = selectedAnnotation();
      if (!annotation) return;
      annotation.color = el.annotationColor.value;
      state.highlightColor = annotation.color;
      renderWorkspace();
    });
    el.annotationNote.addEventListener("input", () => {
      const annotation = selectedAnnotation();
      if (!annotation) return;
      annotation.note = el.annotationNote.value;
      renderAnnotationLayer();
    });
    el.annotationNote.addEventListener("change", renderReadingPanels);
    el.btnDeleteAnnotation.addEventListener("click", () => {
      if (!state.selectedAnnotationId) return;
      state.annotations = state.annotations.filter((annotation) => annotation.id !== state.selectedAnnotationId);
      state.selectedAnnotationId = null;
      renderWorkspace();
      setStatus("Highlight deleted");
    });
    document.querySelectorAll("[data-menu-command]").forEach((button) => {
      button.addEventListener("click", () => {
        const command = button.dataset.menuCommand;
        if (command === "file") el.fileUpload.click();
        else if (command === "edit") setMode("edit");
        else if (command === "view") fitPage();
        else if (command === "document") loadDocument(state.documentName);
        else if (command === "tools") setMode("read");
        else if (command === "help") openModal(el.shortcutsModal);
      });
    });

    document.querySelectorAll("[data-tool]").forEach((button) => {
      button.addEventListener("click", () => setTool(button.dataset.tool));
    });
    el.canvasStage.addEventListener("click", addFieldAt);
    el.canvasStage.addEventListener("pointerdown", beginHighlight);
    window.addEventListener("pointermove", (event) => { movePointerEdit(event); moveHighlight(event); });
    window.addEventListener("pointerup", (event) => { endPointerEdit(event); endHighlight(event); });
    window.addEventListener("pointercancel", (event) => { endPointerEdit(event); endHighlight(event); });

    el.btnToggleLabels.addEventListener("click", () => {
      state.labelsVisible = !state.labelsVisible;
      el.btnToggleLabels.setAttribute("aria-pressed", String(state.labelsVisible));
      renderFieldLayer();
    });

    el.filterTabs.addEventListener("click", (event) => {
      const button = event.target.closest("[data-filter]");
      if (!button) return;
      state.filter = button.dataset.filter;
      document.querySelectorAll("[data-filter]").forEach((tab) => {
        const active = tab === button;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
      });
      renderInventory();
    });
    el.fieldSearch.addEventListener("input", () => {
      state.search = el.fieldSearch.value;
      renderInventory();
    });

    el.fieldKind.addEventListener("focus", beginInspectorEdit);
    el.fieldKind.addEventListener("change", () => {
      const field = selectedField();
      if (!field) return;
      applyKind(field, el.fieldKind.value);
      field.review_status = "confirmed";
      endInspectorEdit();
      renderWorkspace();
    });

    bindInspectorInput(el.fieldLabel, (field, value) => {
      field.label = value || "Untitled field";
      field.field_name = field.label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || field.id;
    });
    bindInspectorInput(el.fieldValue, (field, value) => { field.value = value; });
    bindInspectorInput(el.fieldFontSize, (field, value) => { field.font_size = clamp(Number(value) || 9.5, 6, 36); });
    bindInspectorInput(el.fieldColor, (field, value) => { field.font_color = value; });
    bindGeometryInput(el.fieldX, "x");
    bindGeometryInput(el.fieldY, "y");
    bindGeometryInput(el.fieldWidth, "width");
    bindGeometryInput(el.fieldHeight, "height");

    el.fieldChecked.addEventListener("click", () => {
      const field = selectedField();
      if (!field) return;
      const before = snapshot();
      field.checked = !field.checked;
      field.value = field.checked ? "true" : "false";
      field.review_status = "confirmed";
      commitHistory(before);
      renderWorkspace();
    });
    el.fieldRequired.addEventListener("change", () => {
      const field = selectedField();
      if (!field) return;
      const before = snapshot();
      field.required = el.fieldRequired.checked;
      commitHistory(before);
      renderWorkspace();
    });
    el.fieldReviewed.addEventListener("change", () => {
      const field = selectedField();
      if (!field) return;
      const before = snapshot();
      field.review_status = el.fieldReviewed.checked ? "confirmed" : "unreviewed";
      commitHistory(before);
      renderWorkspace();
    });

    el.btnShortcuts.addEventListener("click", () => openModal(el.shortcutsModal));
    el.btnCloseShortcuts.addEventListener("click", () => closeModal(el.shortcutsModal));
    [el.shortcutsModal, el.signatureModal].forEach((modal) => {
      modal.addEventListener("click", (event) => { if (event.target === modal) closeModal(modal); });
    });

    window.addEventListener("dragenter", (event) => {
      event.preventDefault();
      state.dragDepth += 1;
      if ([...event.dataTransfer.types].includes("Files")) el.dropOverlay.hidden = false;
    });
    window.addEventListener("dragover", (event) => event.preventDefault());
    window.addEventListener("dragleave", (event) => {
      event.preventDefault();
      state.dragDepth = Math.max(0, state.dragDepth - 1);
      if (!state.dragDepth) el.dropOverlay.hidden = true;
    });
    window.addEventListener("drop", (event) => {
      event.preventDefault();
      state.dragDepth = 0;
      el.dropOverlay.hidden = true;
      uploadFile(event.dataTransfer.files?.[0]);
    });

    window.addEventListener("keydown", handleKeyboard);
    let resizeTimer = null;
    window.addEventListener("resize", () => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => { if (state.fitMode) fitPage(); }, 120);
    });
  }

  function bindInspectorInput(input, mutator) {
    input.addEventListener("focus", beginInspectorEdit);
    input.addEventListener("input", () => {
      const field = selectedField();
      if (!field) return;
      beginInspectorEdit();
      mutator(field, input.value);
      renderFieldLayer();
      renderValueLayer();
      renderInventory();
      el.selectedFieldName.textContent = field.label || "Untitled field";
      scheduleInspectorCommit();
    });
    input.addEventListener("change", endInspectorEdit);
    input.addEventListener("blur", () => { if (state.inspectorBefore) endInspectorEdit(); });
  }

  function bindGeometryInput(input, property) {
    input.addEventListener("focus", beginInspectorEdit);
    input.addEventListener("input", () => {
      const field = selectedField();
      const page = currentPageData();
      if (!field || !page) return;
      beginInspectorEdit();
      let [x0, y0, x1, y1] = field.rect;
      const value = Number(input.value);
      if (!Number.isFinite(value)) return;
      if (property === "x") {
        const width = x1 - x0;
        x0 = clamp(value, 0, page.width - width);
        x1 = x0 + width;
      } else if (property === "y") {
        const height = y1 - y0;
        y0 = clamp(value, 0, page.height - height);
        y1 = y0 + height;
      } else if (property === "width") {
        x1 = clamp(x0 + Math.max(5, value), x0 + 5, page.width);
      } else {
        y1 = clamp(y0 + Math.max(5, value), y0 + 5, page.height);
      }
      field.rect = [round(x0), round(y0), round(x1), round(y1)];
      updateDerivedGeometry(field);
      renderFieldLayer();
      renderValueLayer();
      scheduleInspectorCommit();
    });
    input.addEventListener("change", () => { endInspectorEdit(); renderInspector(); renderInventory(); });
    input.addEventListener("blur", () => { if (state.inspectorBefore) endInspectorEdit(); });
  }

  function handleKeyboard(event) {
    const target = event.target;
    const typing = target.matches("input, textarea, select") || target.isContentEditable;
    const command = event.metaKey || event.ctrlKey;
    if (command && event.key.toLowerCase() === "z") {
      event.preventDefault();
      event.shiftKey ? redo() : undo();
      return;
    }
    if (command && event.key.toLowerCase() === "k") {
      event.preventDefault();
      const searchInput = state.mode === "read" ? el.pdfSearch : el.fieldSearch;
      searchInput.focus();
      searchInput.select();
      return;
    }
    if (typing) return;
    const key = event.key.toLowerCase();
    if (state.mode === "read") {
      if (key === "h") {
        event.preventDefault();
        setReadTool("highlight");
      } else if ((event.key === "Delete" || event.key === "Backspace") && selectedAnnotation()) {
        event.preventDefault();
        el.btnDeleteAnnotation.click();
      } else if (event.key === "Escape") {
        setReadTool("select");
        state.selectedAnnotationId = null;
        renderWorkspace();
      }
      return;
    }
    const tools = { v: "select", t: "text", c: "checkbox", r: "radio", d: "date", s: "signature" };
    if (tools[key]) {
      event.preventDefault();
      setTool(tools[key]);
    } else if ((event.key === "Delete" || event.key === "Backspace") && selectedField()) {
      event.preventDefault();
      deleteSelected();
    } else if (event.key === "Escape") {
      closeModal(el.shortcutsModal);
      closeModal(el.signatureModal);
      setTool("select");
    }
  }

  function init() {
    initializeTheme();
    bindEvents();
    initializeSignatureCanvas();
    setTool("select");
    setMode("edit");
    loadDocuments();
  }

  init();
});
