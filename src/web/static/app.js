const params = new URLSearchParams(window.location.search);
const state = {
  projectId: params.get("project") || "25-05",
  projectKey: params.get("key") || sessionStorage.getItem("boilerProjectKey") || "",
  inviteToken: params.get("invite") || "",
  project: null,
  dialog: null,
  allItems: [],
  counters: {},
  expanded: new Set(),
  detailCache: new Map(),
  eventSource: null,
  pollTimer: null,
  uploadCategory: null,
  uploadDocumentType: null,
  currentEditQuestion: null,
  resultsLoadedFor: null,
};

const $ = selector => document.querySelector(selector);
const elements = {
  wizardView: $("#wizardView"), runView: $("#runView"), resultsView: $("#resultsView"),
  projectControl: $("#projectControl"), projectId: $("#projectId"), projectsList: $("#projectsList"),
  openProjectButton: $("#openProjectButton"), newProjectButton: $("#newProjectButton"),
  projectStateBadge: $("#projectStateBadge"), pipelineMode: $("#pipelineMode"),
  progressBar: $("#progressBar"), stageText: $("#stageText"), progressHint: $("#progressHint"),
  chatLog: $("#chatLog"), chatAction: $("#chatAction"), chatForm: $("#chatForm"), chatInput: $("#chatInput"),
  inputChecklist: $("#inputChecklist"), fileInput: $("#fileInput"), errorBox: $("#errorBox"),
  runPercent: $("#runPercent"), runStage: $("#runStage"), runPhrase: $("#runPhrase"), runProgressBar: $("#runProgressBar"),
  summary: $("#summary"), resultsToolbar: $("#resultsToolbar"), searchInput: $("#searchInput"),
  statusFilter: $("#statusFilter"), classFilter: $("#classFilter"), equipmentList: $("#equipmentList"),
  template: $("#equipmentTemplate"), expandWarningsButton: $("#expandWarningsButton"), collapseAllButton: $("#collapseAllButton"),
  sourceFilesButton: $("#sourceFilesButton"), sourceFilesResultButton: $("#sourceFilesResultButton"),
  answersButton: $("#answersButton"), answersResultButton: $("#answersResultButton"), rerunButton: $("#rerunButton"),
  downloadsButton: $("#downloadsButton"), historyButton: $("#historyButton"), shareButton: $("#shareButton"),
  sourceFilesDialog: $("#sourceFilesDialog"), sourceFilesList: $("#sourceFilesList"), closeSourceFilesButton: $("#closeSourceFilesButton"),
  answersDialog: $("#answersDialog"), answersList: $("#answersList"), closeAnswersButton: $("#closeAnswersButton"),
  answerEditDialog: $("#answerEditDialog"), answerEditForm: $("#answerEditForm"), answerEditTitle: $("#answerEditTitle"),
  answerEditSource: $("#answerEditSource"), answerEditMessage: $("#answerEditMessage"), answerEditControl: $("#answerEditControl"),
  answerEditComment: $("#answerEditComment"), resetAnswerButton: $("#resetAnswerButton"), closeAnswerEditButton: $("#closeAnswerEditButton"),
  downloadsDialog: $("#downloadsDialog"), downloadsList: $("#downloadsList"), closeDownloadsButton: $("#closeDownloadsButton"),
  historyDialog: $("#historyDialog"), historyList: $("#historyList"), closeHistoryButton: $("#closeHistoryButton"),
  shareDialog: $("#shareDialog"), closeShareButton: $("#closeShareButton"), shareLabel: $("#shareLabel"),
  shareHours: $("#shareHours"), shareUses: $("#shareUses"), shareAdminToken: $("#shareAdminToken"), createShareButton: $("#createShareButton"), shareResult: $("#shareResult"),
  inviteDialog: $("#inviteDialog"), inviteLabel: $("#inviteLabel"), acceptInviteButton: $("#acceptInviteButton"), inviteError: $("#inviteError"),
};

const statusMeta = {
  ready: { label: "Готово", className: "ready" },
  warning: { label: "С условиями", className: "warning" },
  needs_review: { label: "Нужна проверка", className: "needs-review" },
  missing: { label: "Нет кандидата", className: "missing" },
};
const projectStatusLabels = {
  WAITING_FILES: "Ожидание файлов", PREPROCESSING: "Анализ документов", WAITING_HITL: "Уточнение данных",
  READY_TO_RUN: "Готов к расчёту", RUNNING: "Выполняется", COMPLETED: "Завершён", FAILED: "Ошибка",
};
const categoryLabels = { schemes: "Схема", passports: "Паспорт", template: "Шаблон", additional: "Дополнительный" };

function escapeHtml(value) { return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;"); }
function formatNumber(value, digits = 2) { const n = Number(value); return Number.isFinite(n) ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(n) : "—"; }
function formatBytes(value) { const n = Number(value); if (!Number.isFinite(n)) return "—"; if (n < 1024) return `${n} Б`; if (n < 1024 ** 2) return `${formatNumber(n / 1024, 1)} КБ`; return `${formatNumber(n / 1024 ** 2, 1)} МБ`; }
function formatPrice(candidate) { const price = Number(candidate?.price_rub); return candidate?.price_found && Number.isFinite(price) ? `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(price)} ₽` : "—"; }
function candidateLabel(candidate) { if (!candidate) return "Кандидат не выбран"; return [candidate.vendor, candidate.series, candidate.model].filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).join(" · ") || "Кандидат не выбран"; }
function projectPath(path) { return path; }

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (state.projectKey) headers.set("X-Project-Key", state.projectKey);
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
  return payload;
}

function showError(message) { elements.errorBox.textContent = message; elements.errorBox.classList.remove("hidden"); window.setTimeout(() => elements.errorBox.classList.add("hidden"), 8000); }
function clearError() { elements.errorBox.classList.add("hidden"); elements.errorBox.textContent = ""; }
function setBusy(button, busy, label = "Выполняется…") { if (!button) return; if (busy) { button.dataset.old = button.textContent; button.textContent = label; button.disabled = true; } else { button.textContent = button.dataset.old || button.textContent; button.disabled = false; } }
function actionButton(label, className = "primary-button") { const button = document.createElement("button"); button.type = "button"; button.className = className; button.textContent = label; return button; }
function showView(name) { elements.wizardView.classList.toggle("hidden", name !== "wizard"); elements.runView.classList.toggle("hidden", name !== "run"); elements.resultsView.classList.toggle("hidden", name !== "results"); }
function updateUrl(view = null) { const url = new URL(window.location.href); url.search = ""; if (state.projectId) url.searchParams.set("project", state.projectId); if (state.projectKey) url.searchParams.set("key", state.projectKey); if (view) url.searchParams.set("view", view); history.replaceState({}, "", url); }

async function refreshProjectsList() {
  if (state.inviteToken || state.projectKey) return;
  try { const payload = await api("/api/projects"); elements.projectsList.innerHTML = (payload.projects || []).map(p => `<option value="${escapeHtml(p.project_id)}">${escapeHtml(p.stage || p.status || "")}</option>`).join(""); } catch (_) { elements.projectsList.innerHTML = ""; }
}

async function openProject(projectId = elements.projectId.value.trim()) {
  if (!projectId) return showError("Укажите шифр проекта.");
  stopEventStream(); stopPolling(); clearError();
  state.projectId = projectId; elements.projectId.value = projectId;
  try {
    const dialog = await api(`/api/projects/${encodeURIComponent(projectId)}/dialog`);
    applyDialog(dialog);
    connectEventStream();
    if (dialog.state.status === "COMPLETED") await loadResults();
  } catch (error) {
    state.project = null; renderProjectState(); renderChat([]); showView("wizard"); showError(error.message);
  }
}

async function createProject(inviteToken = null) {
  let projectId = elements.projectId.value.trim();
  if (!projectId || projectId === "25-05" || inviteToken) projectId = inviteToken ? null : `project-${new Date().toISOString().slice(0,16).replaceAll(/[-:T]/g, "")}`;
  try {
    const payload = await api("/api/projects", { method: "POST", body: JSON.stringify({ project_id: projectId, invite_token: inviteToken }) });
    state.projectId = payload.project_id; state.projectKey = payload.project_key || "";
    if (state.projectKey) sessionStorage.setItem("boilerProjectKey", state.projectKey);
    elements.projectId.value = state.projectId; state.inviteToken = ""; elements.inviteDialog.close();
    updateUrl("wizard"); await refreshProjectsList(); await openProject(state.projectId);
  } catch (error) { if (inviteToken) { elements.inviteError.textContent = error.message; elements.inviteError.classList.remove("hidden"); } else showError(error.message); }
}

function applyDialog(dialog) {
  const previous = state.project?.status;
  state.dialog = dialog; state.project = dialog.state;
  renderProjectState(); renderChat(dialog.state.events || []); renderAction(dialog.action, dialog.prompt); renderChecklist();
  if (dialog.state.status === "RUNNING") { showView("run"); updateUrl("run"); }
  else if (dialog.state.status === "COMPLETED") { showView("results"); updateUrl("results"); if (state.resultsLoadedFor !== state.projectId) loadResults(); }
  else { showView("wizard"); updateUrl("wizard"); }
  if (previous !== "COMPLETED" && dialog.state.status === "COMPLETED") loadResults();
}

function renderProjectState() {
  const p = state.project;
  if (!p) { elements.projectStateBadge.textContent = "Не открыт"; elements.progressBar.style.width = "0%"; elements.stageText.textContent = "Создайте или откройте проект."; return; }
  elements.projectStateBadge.textContent = projectStatusLabels[p.status] || p.status;
  elements.projectStateBadge.className = `state-badge ${String(p.status || "").toLowerCase()}`;
  elements.pipelineMode.textContent = p.pipeline_mode ? `режим: ${p.pipeline_mode}` : "";
  elements.progressBar.style.width = `${p.progress || 0}%`; elements.stageText.textContent = `${p.stage || "—"} · ${p.progress || 0}%`; elements.progressHint.textContent = p.progress_hint || "";
  elements.runPercent.textContent = `${p.progress || 0}%`; elements.runStage.textContent = p.stage || "Расчёт"; elements.runPhrase.textContent = p.progress_hint || "Расчёт выполняется"; elements.runProgressBar.style.width = `${p.progress || 0}%`;
  elements.answersButton.disabled = !p.questions_total; elements.answersResultButton.disabled = !p.questions_total;
}

function renderChat(events) {
  elements.chatLog.innerHTML = "";
  const rows = events.length ? events : [{ actor: "bot", text: "Создайте проект, и я проведу вас по всему сценарию." }];
  rows.slice(-80).forEach(event => { const node = document.createElement("div"); node.className = `message ${event.actor === "user" ? "user" : "bot"}`; node.innerHTML = `<span>${escapeHtml(event.text || "")}</span>${event.created_at ? `<small>${new Date(event.created_at).toLocaleTimeString("ru-RU", {hour:"2-digit",minute:"2-digit"})}</small>` : ""}`; elements.chatLog.appendChild(node); });
  requestAnimationFrame(() => { elements.chatLog.scrollTop = elements.chatLog.scrollHeight; });
}

function renderChecklist() {
  const p = state.project || {}; const c = p.file_counts || {};
  const rows = [
    ["Схемы и экспликации", c.schemes || 0, "PDF: ТМ, ОВ, ГСВ, ЭО, ХВО"],
    ["Паспорта оборудования", c.passports || 0, "PDF по тегам электроприёмников"],
    ["Расчётный шаблон", c.template || 0, "XLSX-шаблон ВРУ"],
    ["HITL-уточнения", p.questions_answered || 0, p.questions_total ? `${p.questions_answered} из ${p.questions_total}` : "после анализа документов"],
  ];
  elements.inputChecklist.innerHTML = rows.map(([label,count,note], index) => { const done = index < 3 ? count > 0 : p.questions_total > 0 && p.questions_remaining === 0; return `<div class="check-row-mini ${done ? "done" : ""}"><span class="check-dot">${done ? "✓" : index + 1}</span><div><strong>${escapeHtml(label)}</strong><span>${escapeHtml(note)}</span></div><b>${count || "—"}</b></div>`; }).join("");
}

function renderAction(action, prompt = "") {
  elements.chatAction.innerHTML = prompt ? `<p class="action-prompt">${escapeHtml(prompt)}</p>` : "";
  if (!action) return;
  if (action.type === "scheme_uploads") { const button = actionButton(action.label); button.onclick = () => showSourceFiles(true); elements.chatAction.appendChild(button); return; }
  if (action.type === "scan_schemes") { const button = actionButton(action.label); button.onclick = () => scanSchemes(button); elements.chatAction.appendChild(button); return; }
  if (action.type === "upload") { const button = actionButton(action.label); button.onclick = () => chooseFiles(action); elements.chatAction.appendChild(button); return; }
  if (action.type === "preprocess") { const button = actionButton(action.label); button.onclick = () => preprocessProject(button); elements.chatAction.appendChild(button); return; }
  if (action.type === "question") { renderQuestion(action.question); return; }
  if (["run","retry"].includes(action.type)) { const button = actionButton(action.label); button.onclick = () => runProject(button); elements.chatAction.appendChild(button); return; }
  if (action.type === "results") { const button = actionButton(action.label); button.onclick = () => { showView("results"); loadResults(); }; elements.chatAction.appendChild(button); }
}

function chooseFiles(action) { state.uploadCategory = action.category; state.uploadDocumentType = action.documentType || action.document_type || null; elements.fileInput.accept = action.accept || ""; elements.fileInput.multiple = Boolean(action.multiple); elements.fileInput.value = ""; elements.fileInput.click(); }
async function uploadSelectedFiles() {
  const files = Array.from(elements.fileInput.files || []); if (!files.length || !state.uploadCategory) return;
  const form = new FormData(); files.forEach(file => form.append("files", file));
  let path = `/api/projects/${encodeURIComponent(state.projectId)}/files?category=${encodeURIComponent(state.uploadCategory)}`;
  if (state.uploadDocumentType) path += `&document_type=${encodeURIComponent(state.uploadDocumentType)}`;
  try { await api(path, { method: "POST", body: form }); await refreshDialog(); if (elements.sourceFilesDialog.open) await showSourceFiles(false); } catch (error) { showError(error.message); }
}
async function scanSchemes(button) { setBusy(button,true,"Разбираю…"); try { await api(`/api/projects/${encodeURIComponent(state.projectId)}/scan-schemes`,{method:"POST"}); await refreshDialog(); } catch(error){showError(error.message);} finally{setBusy(button,false);} }
async function preprocessProject(button) { setBusy(button,true,"Анализ…"); try { await api(`/api/projects/${encodeURIComponent(state.projectId)}/preprocess`,{method:"POST"}); await refreshDialog(); } catch(error){showError(error.message);} finally{setBusy(button,false);} }

function renderQuestion(question) {
  const form = document.createElement("form"); form.className = "question-form"; let control;
  if (question.type === "boolean") { const row=document.createElement("div");row.className="choice-row";[["Да",true],["Нет",false]].forEach(([label,value])=>{const b=actionButton(label,"choice-button");b.onclick=()=>submitAnswer(question,value,b);row.appendChild(b)});form.appendChild(row); }
  else { if(question.type==="select"){control=document.createElement("select");(question.options||[]).forEach(option=>{const n=document.createElement("option");n.value=option;n.textContent=question.unit?`${option} ${question.unit}`:option;control.appendChild(n)})} else if(question.type==="multiline"){control=document.createElement("textarea");control.rows=4;control.placeholder=question.placeholder||"Введите значение"} else {control=document.createElement("input");control.type="number";control.step="any";if(question.min!=null)control.min=question.min;if(question.max!=null)control.max=question.max;control.placeholder=question.unit?`Значение, ${question.unit}`:"Значение"} form.appendChild(control); const buttons=document.createElement("div");buttons.className="question-buttons";const submit=actionButton("Ответить");submit.type="submit";buttons.appendChild(submit);if(!question.required){const skip=actionButton("Пропустить","secondary-button");skip.onclick=()=>submitAnswer(question,null,skip,"skip");buttons.appendChild(skip)}form.appendChild(buttons);form.onsubmit=e=>{e.preventDefault();submitAnswer(question,control.value,submit)}}
  if(question.source_file){const source=document.createElement("small");source.className="question-source";source.textContent=`Источник: ${question.source_file}`;form.appendChild(source)} elements.chatAction.appendChild(form);
}
async function submitAnswer(question,value,button,action="answer"){setBusy(button,true,"Сохраняю…");try{await api(`/api/projects/${encodeURIComponent(state.projectId)}/questions/${encodeURIComponent(question.id)}/answer`,{method:"POST",body:JSON.stringify({value,action})});await refreshDialog()}catch(error){showError(error.message)}finally{setBusy(button,false)}}

async function runProject(button) {
  setBusy(button,true,"Проверка…");
  try { const check=await api(`/api/projects/${encodeURIComponent(state.projectId)}/preflight`); if(!check.ok)throw new Error(`Запуск заблокирован: ${check.critical.join("; ")}`); if(check.warnings?.length&&!confirm(`Есть предупреждения:\n\n• ${check.warnings.join("\n• ")}\n\nПродолжить?`))return; await api(`/api/projects/${encodeURIComponent(state.projectId)}/run`,{method:"POST"}); await refreshDialog(); }
  catch(error){showError(error.message)} finally{setBusy(button,false)}
}
async function rerunProject(){if(confirm("Повторить расчёт? Текущий результат будет сохранён в ZIP-архиве."))await runProject(elements.rerunButton)}

async function refreshDialog(){const dialog=await api(`/api/projects/${encodeURIComponent(state.projectId)}/dialog`);applyDialog(dialog);startPollingIfNeeded()}
function connectEventStream(){stopEventStream();if(!window.EventSource||!state.projectId)return;const key=state.projectKey?`?key=${encodeURIComponent(state.projectKey)}`:"";const source=new EventSource(`/api/projects/${encodeURIComponent(state.projectId)}/events${key}`);state.eventSource=source;source.addEventListener("dialog",event=>{try{applyDialog(JSON.parse(event.data))}catch(error){showError(error.message)}});source.onerror=()=>{stopEventStream();startPollingIfNeeded()}}
function stopEventStream(){if(state.eventSource)state.eventSource.close();state.eventSource=null}
function startPollingIfNeeded(){stopPolling();if(state.eventSource||!["RUNNING","PREPROCESSING"].includes(state.project?.status))return;state.pollTimer=setInterval(()=>refreshDialog().catch(error=>showError(error.message)),1300)}
function stopPolling(){if(state.pollTimer)clearInterval(state.pollTimer);state.pollTimer=null}

async function sendChatMessage(event){event.preventDefault();const message=elements.chatInput.value.trim();if(!message||!state.projectId)return;elements.chatInput.value="";try{await api(`/api/projects/${encodeURIComponent(state.projectId)}/chat`,{method:"POST",body:JSON.stringify({message})});await refreshDialog()}catch(error){showError(error.message)}}

async function loadResults(){
  if(!state.projectId)return;elements.equipmentList.innerHTML='<div class="loading">Загрузка результатов…</div>';
  try{const payload=await api(`/api/projects/${encodeURIComponent(state.projectId)}/equipment`);state.allItems=payload.items||[];state.counters=payload.counters||{};state.expanded.clear();state.detailCache.clear();state.resultsLoadedFor=state.projectId;renderSummary(state.counters);renderClassFilter(payload.equipment_classes||[]);renderList()}catch(error){elements.equipmentList.innerHTML=`<div class="inline-error">${escapeHtml(error.message)}</div>`}}
function renderSummary(counters){const rows=[["Всего ЭП",counters.total??0],["Готово",counters.ready??0],["С условиями",counters.warning??0],["Нужна проверка",counters.needs_review??0]];elements.summary.innerHTML=rows.map(([label,value])=>`<div class="summary-card"><strong>${value}</strong><span>${label}</span></div>`).join("")}
function renderClassFilter(classes){const current=elements.classFilter.value;elements.classFilter.innerHTML='<option value="">Все классы ЭП</option>'+classes.map(value=>`<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");elements.classFilter.value=current}
function filteredItems(){const query=elements.searchInput.value.trim().toLowerCase();return state.allItems.filter(item=>{const text=[item.tag,item.display_name,item.equipment_class,candidateLabel(item.selected_candidate)].join(" ").toLowerCase();return(!query||text.includes(query))&&(!elements.statusFilter.value||item.status===elements.statusFilter.value)&&(!elements.classFilter.value||item.equipment_class===elements.classFilter.value)})}
function renderList(){elements.equipmentList.innerHTML="";const items=filteredItems();if(!items.length){elements.equipmentList.innerHTML='<div class="empty-state">Ничего не найдено.</div>';return}for(const item of items){const fragment=elements.template.content.cloneNode(true);const card=fragment.querySelector(".equipment-card"),head=fragment.querySelector(".equipment-head"),body=fragment.querySelector(".equipment-body"),meta=statusMeta[item.status]||statusMeta.missing;card.dataset.tag=item.tag;fragment.querySelector(".tag").textContent=item.tag;fragment.querySelector(".equipment-class").textContent=item.equipment_class||"прочее";fragment.querySelector(".display-name").textContent=item.display_name||"Электроприёмник";fragment.querySelector(".selected-model").textContent=candidateLabel(item.selected_candidate);fragment.querySelector(".nominal").textContent=`номинал ${formatNumber(item.suggested_nominal_a,0)} А`;fragment.querySelector(".candidate-count").textContent=`${item.shortlist_size??0} вариантов`;const badge=fragment.querySelector(".status-badge");badge.textContent=meta.label;badge.classList.add(meta.className);head.onclick=()=>toggleCard(item,card,head,body);elements.equipmentList.appendChild(fragment);if(state.expanded.has(item.tag)){const mounted=elements.equipmentList.querySelector(`[data-tag="${CSS.escape(item.tag)}"]`);expandCard(item,mounted,mounted.querySelector(".equipment-head"),mounted.querySelector(".equipment-body"))}}}
async function toggleCard(item,card,head,body){if(state.expanded.has(item.tag)){state.expanded.delete(item.tag);head.setAttribute("aria-expanded","false");body.hidden=true;return}state.expanded.add(item.tag);await expandCard(item,card,head,body)}
async function expandCard(item,card,head,body){head.setAttribute("aria-expanded","true");body.hidden=false;body.innerHTML='<div class="loading">Загрузка подробностей…</div>';try{let detail=state.detailCache.get(item.tag);if(!detail){detail=await api(`/api/projects/${encodeURIComponent(state.projectId)}/equipment/${encodeURIComponent(item.tag)}?limit=8`);state.detailCache.set(item.tag,detail)}body.innerHTML=renderDetail(detail)}catch(error){body.innerHTML=`<div class="inline-error">${escapeHtml(error.message)}</div>`}}
function renderDetail(detail){const r=detail.requirement||{},n=detail.normative||{},c=detail.consistency||{},warnings=detail.warnings||[],candidates=detail.candidate_options||[],checks=Array.isArray(n.engineering_checks)?n.engineering_checks:[],refs=Array.isArray(n.normative_refs)?n.normative_refs:(Array.isArray(n.normative_hits)?n.normative_hits:[]),explanation=n.readable_explanation||n.why_this_candidate||n.explanation_text||"Объяснение не сформировано.";return `<div class="detail-grid"><section class="detail-section"><h3>Расчётное требование</h3><div class="fact-grid">${fact("Расчётный ток",`${formatNumber(r.estimated_current_a)} А`)}${fact("Ток выбора",`${formatNumber(r.selection_current_a)} А`)}${fact("Номинал",`${formatNumber(r.suggested_nominal_a,0)} А`)}${fact("Класс аппарата",r.device_class)}${fact("Полюса",r.poles)}${fact("Характеристика",r.trip_curve||r.preferred_trip_curve)}${fact("Отключающая способность",`${formatNumber(r.breaking_capacity_ka)} кА`)}${fact("ЧРП",r.has_vfd?"есть":"нет")}</div>${warnings.length?`<div class="warning-list">${warnings.map(x=>`<div>⚠ ${escapeHtml(x)}</div>`).join("")}</div>`:""}</section><section class="detail-section"><h3>Обоснование результата</h3><p>${escapeHtml(explanation)}</p><div class="confidence-row"><span>Verdict: <strong>${escapeHtml(n.verdict||"—")}</strong></span><span>Confidence: <strong>${formatNumber(n.confidence)}</strong></span><span>Consistency: <strong>${escapeHtml(c.status||"—")}</strong></span></div></section></div><section class="detail-section candidates-section"><div class="section-heading"><h3>Кандидаты для выбора</h3><span>${candidates.length} из ${detail.candidate_pool_total??detail.candidate_options_total??candidates.length}</span></div><div class="table-wrap"><table><thead><tr><th>Ранг</th><th>Аппарат</th><th>Параметры</th><th>Цена</th><th>Источник</th><th>Почему подходит</th></tr></thead><tbody>${candidates.map(candidateRow).join("")}</tbody></table></div></section><div class="detail-grid"><section class="detail-section"><h3>Инженерные проверки</h3>${checks.length?checks.map(check=>`<div class="check-row"><strong>${escapeHtml(check.title||check.check||"Проверка")}</strong><span>${escapeHtml(check.status||"—")}</span><p>${escapeHtml(check.message||check.explanation||check.note||"")}</p></div>`).join(""):'<p class="muted">Отдельные проверки не сохранены.</p>'}</section><section class="detail-section"><h3>Нормативные ссылки</h3>${refs.length?refs.slice(0,5).map(ref=>`<div class="reference-row"><strong>${escapeHtml(ref.doc_title||ref.title||ref.source_file||"Источник")}</strong><span>${escapeHtml(ref.section_hint||ref.section||ref.chunk_id||"")}</span></div>`).join(""):'<p class="muted">Ссылки не сохранены.</p>'}</section></div>`}
function fact(label,value){return `<div class="fact"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value??"—")}</strong></div>`}
function candidateRow(candidate,index){const current=candidate.current_range_a?`${candidate.current_range_a} А`:`${formatNumber(candidate.rated_current_a)} А`;const parameters=[candidate.device_class,current,candidate.poles?`${candidate.poles}P`:null,candidate.trip_curve,candidate.breaking_capacity_ka?`${formatNumber(candidate.breaking_capacity_ka)} кА`:null,candidate.rcd_ma?`${formatNumber(candidate.rcd_ma,0)} мА`:null].filter(Boolean).join(" · ");const reasons=Array.isArray(candidate.selector_reasons)?candidate.selector_reasons.join("; "):candidate.review_reason||candidate.reason||"—";return `<tr><td>${candidate.rank??index+1}</td><td><strong>${escapeHtml(candidateLabel(candidate))}</strong><small>${escapeHtml(candidate.price_article||"")}</small></td><td>${escapeHtml(parameters)}</td><td>${escapeHtml(formatPrice(candidate))}</td><td>${escapeHtml(candidate.price_source_domain||candidate.price_source_type||"—")}</td><td>${escapeHtml(reasons)}</td></tr>`}

async function showSourceFiles(open=true){if(!state.projectId)return;elements.sourceFilesList.innerHTML='<div class="loading">Загрузка…</div>';if(open)elements.sourceFilesDialog.showModal();try{const payload=await api(`/api/projects/${encodeURIComponent(state.projectId)}/files`),files=payload.files||[];elements.sourceFilesList.innerHTML=files.length?files.map(file=>`<div class="management-row"><div><strong>${escapeHtml(file.filename)}</strong><span>${escapeHtml(file.document_type||categoryLabels[file.category]||file.category)}</span><small>${formatBytes(file.size)}</small></div><button class="danger-button delete-file" data-id="${file.id}" data-name="${escapeHtml(file.filename)}">Удалить</button></div>`).join(""):'<div class="empty-state">Файлы ещё не загружены.</div>';elements.sourceFilesList.querySelectorAll(".delete-file").forEach(button=>button.onclick=()=>deleteSourceFile(button.dataset.id,button.dataset.name))}catch(error){elements.sourceFilesList.innerHTML=`<div class="inline-error">${escapeHtml(error.message)}</div>`}}
async function deleteSourceFile(id,name){if(!confirm(`Удалить «${name}»? Анализ и HITL будут сброшены.`))return;try{await api(`/api/projects/${encodeURIComponent(state.projectId)}/files/${encodeURIComponent(id)}`,{method:"DELETE"});await refreshDialog();await showSourceFiles(false)}catch(error){showError(error.message)}}

function answerLabel(q){if(q.answered)return formatAnswer(q,q.answer);if(q.skipped)return"Пропущено";if(!q.active)return"Неактивен по условию";return"Ожидает ответа"}function formatAnswer(q,v){if(q.type==="boolean")return v?"Да":"Нет";return`${v??"—"}${q.unit?` ${q.unit}`:""}`}
async function showAnswers(open=true){if(open)elements.answersDialog.showModal();elements.answersList.innerHTML='<div class="loading">Загрузка…</div>';try{const payload=await api(`/api/projects/${encodeURIComponent(state.projectId)}/questions`),questions=payload.questions||[],byId=new Map(questions.map(q=>[q.id,q]));elements.answersList.innerHTML=questions.length?questions.map(q=>`<div class="management-row"><div><strong>${escapeHtml(q.tag||"_meta")} · ${escapeHtml(q.field||q.id)}</strong><span>${escapeHtml(q.message||"")}</span><small>${escapeHtml(answerLabel(q))}</small></div><button class="secondary-button edit-answer" data-id="${q.id}" ${q.active?"":"disabled"}>${q.answered||q.skipped?"Изменить":"Ответить"}</button></div>`).join(""):'<div class="empty-state">Вопросы ещё не сформированы.</div>';elements.answersList.querySelectorAll(".edit-answer").forEach(button=>button.onclick=()=>openAnswerEditor(byId.get(button.dataset.id)))}catch(error){elements.answersList.innerHTML=`<div class="inline-error">${escapeHtml(error.message)}</div>`}}
function buildEditorControl(q){let c;if(q.type==="boolean"){c=document.createElement("select");c.innerHTML='<option value="true">Да</option><option value="false">Нет</option>';if(q.answered)c.value=String(Boolean(q.answer))}else if(q.type==="select"){c=document.createElement("select");(q.options||[]).forEach(o=>{const n=document.createElement("option");n.value=o;n.textContent=q.unit?`${o} ${q.unit}`:o;c.appendChild(n)});if(q.answered)c.value=String(q.answer)}else if(q.type==="multiline"){c=document.createElement("textarea");c.rows=5;c.value=q.answered?q.answer:""}else{c=document.createElement("input");c.type="number";c.step="any";c.value=q.answered?q.answer:""}c.id="answerEditValue";return c}
function openAnswerEditor(q){state.currentEditQuestion=q;elements.answerEditTitle.textContent=`${q.tag||"_meta"} · ${q.field||q.id}`;elements.answerEditSource.textContent=q.source_file?`Источник: ${q.source_file}`:"";elements.answerEditMessage.textContent=q.message||"";elements.answerEditControl.innerHTML="";elements.answerEditControl.appendChild(buildEditorControl(q));elements.answerEditComment.value=q.comment||"";elements.resetAnswerButton.disabled=!q.answered&&!q.skipped;elements.answerEditDialog.showModal()}
async function saveEditedAnswer(event){event.preventDefault();const q=state.currentEditQuestion,c=elements.answerEditControl.querySelector("#answerEditValue");let value=c?.value;if(q.type==="boolean")value=value==="true";try{await api(`/api/projects/${encodeURIComponent(state.projectId)}/questions/${encodeURIComponent(q.id)}/answer`,{method:"POST",body:JSON.stringify({value,action:"answer",comment:elements.answerEditComment.value,replace:true})});elements.answerEditDialog.close();await refreshDialog();await showAnswers(false)}catch(error){showError(error.message)}}
async function resetEditedAnswer(){const q=state.currentEditQuestion;try{await api(`/api/projects/${encodeURIComponent(state.projectId)}/questions/${encodeURIComponent(q.id)}/reset`,{method:"POST"});elements.answerEditDialog.close();await refreshDialog();await showAnswers(false)}catch(error){showError(error.message)}}

async function showDownloads(){elements.downloadsDialog.showModal();elements.downloadsList.innerHTML='<div class="loading">Загрузка…</div>';try{const payload=await api(`/api/projects/${encodeURIComponent(state.projectId)}/downloads`),groups=payload.groups||{};const keyQuery=state.projectKey?`?key=${encodeURIComponent(state.projectKey)}`:"";elements.downloadsList.innerHTML=Object.entries(groups).map(([group,files])=>`<h3>${escapeHtml(group)}</h3>${files.map(file=>`<a class="download-row" href="/api/projects/${encodeURIComponent(state.projectId)}/download/${encodeURIComponent(file.name)}${keyQuery}"><div class="download-copy"><strong>${escapeHtml(file.display_name)}</strong><span>${escapeHtml(file.description)}</span>${file.recommended?'<small class="recommended-tag">Рекомендуемый файл</small>':""}</div><small>${formatBytes(file.size)}</small></a>`).join("")}`).join("")||'<div class="empty-state">Файлы не найдены.</div>'}catch(error){elements.downloadsList.innerHTML=`<div class="inline-error">${escapeHtml(error.message)}</div>`}}
function showHistory(){const events=state.project?.events||[];elements.historyList.innerHTML=events.length?events.map(e=>`<div class="history-event ${e.actor===`user`?`user`:`bot`}"><strong>${e.actor===`user`?`Пользователь`:`Boiler Elec AI`}</strong><span>${escapeHtml(e.text||"")}</span><small>${e.created_at?new Date(e.created_at).toLocaleString("ru-RU"):""}</small></div>`).join(""):'<div class="empty-state">История пока пуста.</div>';elements.historyDialog.showModal()}

async function createShare(){setBusy(elements.createShareButton,true,"Создаю…");try{const headers={};if(elements.shareAdminToken.value)headers["X-Share-Admin-Token"]=elements.shareAdminToken.value;const invite=await api("/api/invites",{method:"POST",headers,body:JSON.stringify({label:elements.shareLabel.value,expires_hours:Number(elements.shareHours.value),max_uses:Number(elements.shareUses.value)})});elements.shareResult.innerHTML=`<strong>Ссылка готова</strong><input value="${escapeHtml(invite.url)}" readonly /><button class="secondary-button wide" id="copyShareButton">Скопировать ссылку</button><small>Действует до ${new Date(invite.expires_at).toLocaleString("ru-RU")}; запусков: ${invite.max_uses}.</small>`;elements.shareResult.classList.remove("hidden");$("#copyShareButton").onclick=async()=>{await navigator.clipboard.writeText(invite.url);$("#copyShareButton").textContent="Скопировано"}}catch(error){showError(error.message)}finally{setBusy(elements.createShareButton,false)}}
async function handleInvite(){try{const invite=await api(`/api/invites/${encodeURIComponent(state.inviteToken)}`);elements.inviteLabel.textContent=invite.label;elements.projectControl.classList.add("hidden");elements.inviteDialog.showModal()}catch(error){elements.inviteError.textContent=error.message;elements.inviteError.classList.remove("hidden");elements.inviteDialog.showModal()}}

function expandWarnings(){filteredItems().filter(item=>["warning","needs_review"].includes(item.status)).forEach(item=>state.expanded.add(item.tag));renderList()}function collapseAll(){state.expanded.clear();renderList()}

[elements.searchInput,elements.statusFilter,elements.classFilter].forEach(el=>{el.addEventListener("input",renderList);el.addEventListener("change",renderList)});
elements.openProjectButton.onclick=()=>openProject(); elements.newProjectButton.onclick=()=>createProject(); elements.projectId.onkeydown=e=>{if(e.key==="Enter")openProject()};
elements.fileInput.onchange=uploadSelectedFiles; elements.chatForm.onsubmit=sendChatMessage;
elements.sourceFilesButton.onclick=()=>showSourceFiles(true); elements.sourceFilesResultButton.onclick=()=>showSourceFiles(true);
elements.answersButton.onclick=()=>showAnswers(true); elements.answersResultButton.onclick=()=>showAnswers(true); elements.rerunButton.onclick=rerunProject;
elements.downloadsButton.onclick=showDownloads; elements.historyButton.onclick=showHistory; elements.shareButton.onclick=()=>elements.shareDialog.showModal();
elements.expandWarningsButton.onclick=expandWarnings; elements.collapseAllButton.onclick=collapseAll;
elements.closeSourceFilesButton.onclick=()=>elements.sourceFilesDialog.close(); elements.closeAnswersButton.onclick=()=>elements.answersDialog.close(); elements.closeAnswerEditButton.onclick=()=>elements.answerEditDialog.close(); elements.closeDownloadsButton.onclick=()=>elements.downloadsDialog.close(); elements.closeHistoryButton.onclick=()=>elements.historyDialog.close(); elements.closeShareButton.onclick=()=>elements.shareDialog.close();
elements.answerEditForm.onsubmit=saveEditedAnswer; elements.resetAnswerButton.onclick=resetEditedAnswer; elements.createShareButton.onclick=createShare; elements.acceptInviteButton.onclick=()=>createProject(state.inviteToken);
document.querySelectorAll(".upload-source-button,.document-upload").forEach(button=>button.onclick=()=>chooseFiles({category:button.dataset.category,documentType:button.dataset.documentType,accept:button.dataset.accept,multiple:button.dataset.multiple==="true"}));
window.addEventListener("beforeunload",()=>{stopEventStream();stopPolling()});

(async function boot(){await refreshProjectsList();if(state.inviteToken)await handleInvite();else await openProject(state.projectId)})();
