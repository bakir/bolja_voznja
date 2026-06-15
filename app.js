const STORAGE_KEY = "bolja_voznja_state";

const CATALOG_FILES = {
  katalog1: "katalog1_final.json",
  katalog2: "katalog2_answers.json",
  katalog3: "katalog3_answers.json",
};

const catalogSelect = document.getElementById("catalog-select");
const prioritizeWeakest = document.getElementById("prioritize-weakest");
const nextBtn = document.getElementById("next-btn");
const prevBtn = document.getElementById("prev-btn");
const nextSeqBtn = document.getElementById("next-seq-btn");
const hardBtn = document.getElementById("hard-btn");
const hideBtn = document.getElementById("hide-btn");
const showHardBtn = document.getElementById("show-hard-btn");
const showHiddenBtn = document.getElementById("show-hidden-btn");
const hardCountEl = document.getElementById("hard-count");
const hiddenCountEl = document.getElementById("hidden-count");
const loadingEl = document.getElementById("loading");
const panelEl = document.getElementById("question-panel");
const emptyEl = document.getElementById("empty-state");
const questionLabel = document.getElementById("question-label");
const questionImage = document.getElementById("question-image");
const questionText = document.getElementById("question-text");
const optionsEl = document.getElementById("options");
const feedbackEl = document.getElementById("feedback");
const notesEl = document.getElementById("notes");
const hardListDialog = document.getElementById("hard-list-dialog");
const hardListEl = document.getElementById("hard-list");
const hardListEmptyEl = document.getElementById("hard-list-empty");

let catalogData = {};
let questionIds = [];
let currentId = null;
let browseMode = "normal";

function defaultQuestionState() {
  return { correctCount: 0, notes: "", hidden: false, hard: false };
}

function defaultState() {
  return {
    settings: {
      catalog: "katalog1",
      prioritizeWeakest: false,
    },
    questions: {},
  };
}

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultState();
    const parsed = JSON.parse(raw);
    return {
      ...defaultState(),
      ...parsed,
      settings: { ...defaultState().settings, ...parsed.settings },
      questions: parsed.questions || {},
    };
  } catch {
    return defaultState();
  }
}

function saveState(state) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function questionKey(catalog, id) {
  return `${catalog}:${id}`;
}

function getQuestionState(id) {
  const state = loadState();
  const key = questionKey(state.settings.catalog, id);
  if (!state.questions[key]) {
    state.questions[key] = defaultQuestionState();
  }
  return { ...defaultQuestionState(), ...state.questions[key] };
}

function setQuestionState(id, patch) {
  const state = loadState();
  const key = questionKey(state.settings.catalog, id);
  state.questions[key] = { ...getQuestionState(id), ...patch };
  saveState(state);
  updateCounts();
}

function getSettings() {
  return loadState().settings;
}

function setSettings(patch) {
  const state = loadState();
  state.settings = { ...state.settings, ...patch };
  saveState(state);
}

function normalizeCatalogPayload(catalog, payload) {
  if (catalog === "katalog1") {
    return payload.questions || payload;
  }
  return payload;
}

async function loadCatalog(catalog) {
  const response = await fetch(CATALOG_FILES[catalog]);
  if (!response.ok) {
    throw new Error(`Failed to load ${CATALOG_FILES[catalog]}`);
  }
  const payload = await response.json();
  catalogData = normalizeCatalogPayload(catalog, payload);
  questionIds = Object.keys(catalogData).sort((a, b) => Number(a) - Number(b));
}

function questionPool() {
  return questionIds.filter((id) => {
    const qState = getQuestionState(id);
    if (browseMode === "hidden") return qState.hidden;
    if (browseMode === "hard") return qState.hard;
    return !qState.hidden;
  });
}

function pickNextQuestionId() {
  const pool = questionPool();
  if (!pool.length) return null;

  const settings = getSettings();
  if (settings.prioritizeWeakest && browseMode === "normal") {
    const counts = pool.map((id) => getQuestionState(id).correctCount ?? 0);
    const minCount = Math.min(...counts);
    const weakest = pool.filter((id) => (getQuestionState(id).correctCount ?? 0) === minCount);
    return weakest[Math.floor(Math.random() * weakest.length)];
  }

  if (pool.length === 1) return pool[0];
  let candidate = pool[Math.floor(Math.random() * pool.length)];
  if (candidate === currentId && pool.length > 1) {
    const others = pool.filter((id) => id !== currentId);
    candidate = others[Math.floor(Math.random() * others.length)];
  }
  return candidate;
}

function hardQuestionIds() {
  return questionIds.filter((id) => getQuestionState(id).hard);
}

function updateCounts() {
  hardCountEl.textContent = String(hardQuestionIds().length);
  hiddenCountEl.textContent = String(
    questionIds.filter((id) => getQuestionState(id).hidden).length,
  );
}

function setBrowseMode(mode) {
  browseMode = mode;
  showHardBtn.classList.toggle("active", mode === "hard");
  showHiddenBtn.classList.toggle("active", mode === "hidden");
}

function renderHardList() {
  const hardIds = hardQuestionIds();
  hardListEl.innerHTML = "";
  hardListEmptyEl.hidden = hardIds.length > 0;

  for (const id of hardIds) {
    const qState = getQuestionState(id);
    const record = catalogData[id];
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    const title = record?.question || `Pitanje ${id}`;
    button.innerHTML = `<strong>#${id}</strong> ${title.slice(0, 80)}${
      title.length > 80 ? "…" : ""
    }<span class="hard-meta">tačno ${qState.correctCount}×${
      qState.hidden ? " · sakriveno" : ""
    }</span>`;
    button.addEventListener("click", () => {
      hardListDialog.close();
      setBrowseMode("hard");
      renderQuestion(id);
    });
    item.appendChild(button);
    hardListEl.appendChild(item);
  }
}

function renderQuestion(id) {
  const record = catalogData[id];
  if (!record) return;

  currentId = id;
  const qState = getQuestionState(id);

  const hardLabel = qState.hard ? " · teško" : "";
  questionLabel.textContent = `Pitanje ${id} · tačno ${qState.correctCount}×${hardLabel}`;
  panelEl.classList.toggle("is-hard", qState.hard);

  questionImage.src = record.question_pic;
  questionImage.alt = `Pitanje ${id}`;

  if (record.question) {
    questionText.hidden = false;
    questionText.textContent = record.question;
  } else {
    questionText.hidden = true;
    questionText.textContent = "";
  }

  optionsEl.innerHTML = "";
  feedbackEl.hidden = true;
  feedbackEl.textContent = "";
  feedbackEl.className = "feedback";

  const optionCount = record.option_count || (record.options ? record.options.length : 0);
  for (let option = 1; option <= optionCount; option += 1) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "option-btn";
    const label = record.options?.[option - 1];
    button.textContent = label ? `${option}. ${label}` : `Odgovor ${option}`;
    button.addEventListener("click", () => checkAnswer(id, option, button));
    optionsEl.appendChild(button);
  }

  notesEl.value = qState.notes || "";
  hardBtn.textContent = qState.hard ? "Ukloni oznaku teško" : "Označi teško";
  hardBtn.classList.toggle("active", qState.hard);
  hideBtn.textContent = qState.hidden ? "Vrati pitanje" : "Sakrij pitanje";

  panelEl.hidden = false;
  emptyEl.hidden = true;
  updateCounts();
}

function checkAnswer(id, selected, button) {
  const record = catalogData[id];
  const correctAnswers = record.answers || [];
  const isCorrect = correctAnswers.includes(selected);

  [...optionsEl.querySelectorAll("button")].forEach((el) => {
    el.disabled = true;
  });

  feedbackEl.hidden = false;
  if (isCorrect) {
    feedbackEl.textContent = "Tačno!";
    feedbackEl.className = "feedback correct";
    button.classList.add("correct");
    const qState = getQuestionState(id);
    setQuestionState(id, { correctCount: (qState.correctCount ?? 0) + 1 });
    const updated = getQuestionState(id);
    const hardLabel = updated.hard ? " · teško" : "";
    questionLabel.textContent = `Pitanje ${id} · tačno ${updated.correctCount}×${hardLabel}`;
  } else {
    feedbackEl.textContent = `Netačno. Tačan odgovor: ${correctAnswers.join(", ")}`;
    feedbackEl.className = "feedback wrong";
    button.classList.add("wrong");
  }
}

function navigateSequential(delta) {
  const pool = questionPool();
  if (!pool.length) {
    panelEl.hidden = true;
    emptyEl.hidden = false;
    currentId = null;
    return;
  }

  let index = currentId ? pool.indexOf(currentId) : -1;
  if (index === -1) {
    index = delta > 0 ? 0 : pool.length - 1;
  } else {
    index = (index + delta + pool.length) % pool.length;
  }
  renderQuestion(pool[index]);
}

function showNextQuestion() {
  const id = pickNextQuestionId();
  if (!id) {
    panelEl.hidden = true;
    emptyEl.hidden = false;
    currentId = null;
    return;
  }
  renderQuestion(id);
}

function syncSettingsUi() {
  const settings = getSettings();
  catalogSelect.value = settings.catalog;
  prioritizeWeakest.checked = settings.prioritizeWeakest;
}

notesEl.addEventListener("input", () => {
  if (!currentId) return;
  setQuestionState(currentId, { notes: notesEl.value });
});

hardBtn.addEventListener("click", () => {
  if (!currentId) return;
  const qState = getQuestionState(currentId);
  setQuestionState(currentId, { hard: !qState.hard });
  renderQuestion(currentId);
});

hideBtn.addEventListener("click", () => {
  if (!currentId) return;
  const qState = getQuestionState(currentId);
  setQuestionState(currentId, { hidden: !qState.hidden });
  showNextQuestion();
});

nextBtn.addEventListener("click", () => {
  setBrowseMode("normal");
  showNextQuestion();
});

prevBtn.addEventListener("click", () => navigateSequential(-1));
nextSeqBtn.addEventListener("click", () => navigateSequential(1));

document.addEventListener("keydown", (event) => {
  const tag = event.target.tagName;
  if (tag === "TEXTAREA" || tag === "INPUT" || tag === "SELECT") return;
  if (hardListDialog.open) return;

  if (event.key === "ArrowRight") {
    event.preventDefault();
    navigateSequential(1);
  } else if (event.key === "ArrowLeft") {
    event.preventDefault();
    navigateSequential(-1);
  }
});

showHardBtn.addEventListener("click", (event) => {
  if (event.shiftKey) {
    renderHardList();
    hardListDialog.showModal();
    return;
  }
  setBrowseMode(browseMode === "hard" ? "normal" : "hard");
  showNextQuestion();
});

showHiddenBtn.addEventListener("click", () => {
  setBrowseMode(browseMode === "hidden" ? "normal" : "hidden");
  showNextQuestion();
});

prioritizeWeakest.addEventListener("change", () => {
  setSettings({ prioritizeWeakest: prioritizeWeakest.checked });
});

catalogSelect.addEventListener("change", async () => {
  const catalog = catalogSelect.value;
  setSettings({ catalog });
  loadingEl.hidden = false;
  panelEl.hidden = true;
  emptyEl.hidden = true;
  try {
    await loadCatalog(catalog);
    setBrowseMode("normal");
    showNextQuestion();
  } catch (error) {
    loadingEl.textContent = error.message;
  } finally {
    loadingEl.hidden = true;
  }
});

async function init() {
  syncSettingsUi();
  setBrowseMode("normal");
  try {
    await loadCatalog(getSettings().catalog);
    loadingEl.hidden = true;
    showNextQuestion();
  } catch (error) {
    loadingEl.textContent = error.message;
  }
}

init();
