const STORE_KEY = "blocked";
const ENABLED_KEY = "enabled";

const listEl = document.getElementById("list");
const emptyEl = document.getElementById("empty");
const countEl = document.getElementById("count");
const enabledEl = document.getElementById("enabled");
const formEl = document.getElementById("add-form");
const valueEl = document.getElementById("new-value");
const errorEl = document.getElementById("error");

const TYPE_LABELS = {
  domain: "Dominio completo (+ subdominios)",
  url: "URL específica"
};

function normalizeDomain(value) {
  let v = value.trim().toLowerCase();
  v = v.replace(/^[a-z]+:\/\//, "");
  v = v.replace(/\/.*$/, "");
  v = v.replace(/^www\./, "");
  return v;
}

function normalizeUrl(value) {
  let v = value.trim().toLowerCase();
  v = v.replace(/^https?:\/\//, "");
  return v.replace(/\/$/, "");
}

function isValidDomain(value) {
  if (value === "localhost") return true;
  if (/^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/.test(value)) return true;
  return /^[a-z0-9.-]+\.[a-z]{2,}$/.test(value);
}

async function getState() {
  const data = await browser.storage.local.get([STORE_KEY, ENABLED_KEY]);
  return {
    blocked: data[STORE_KEY] || [],
    enabled: data[ENABLED_KEY] !== false
  };
}

async function saveState(blocked, enabled) {
  await browser.storage.local.set({ [STORE_KEY]: blocked, [ENABLED_KEY]: enabled });
}

function setError(message) {
  errorEl.hidden = !message;
  errorEl.textContent = message || "";
}

function render(blocked) {
  listEl.textContent = "";
  countEl.textContent = `(${blocked.length})`;
  emptyEl.hidden = blocked.length > 0;

  for (const entry of blocked) {
    const li = document.createElement("li");
    li.className = "item";

    const info = document.createElement("div");
    info.className = "item-info";

    const value = document.createElement("span");
    value.className = "item-value";
    value.textContent = entry.value;

    const type = document.createElement("span");
    type.className = "item-type";
    type.textContent = TYPE_LABELS[entry.type] || entry.type;

    info.append(value, type);

    const remove = document.createElement("button");
    remove.className = "btn danger";
    remove.textContent = "Quitar";
    remove.addEventListener("click", async () => {
      const state = await getState();
      const next = state.blocked.filter((item) => item.id !== entry.id);
      await saveState(next, state.enabled);
      render(next);
    });

    li.append(info, remove);
    listEl.append(li);
  }
}

async function init() {
  const state = await getState();
  enabledEl.checked = state.enabled;
  render(state.blocked);

  enabledEl.addEventListener("change", async () => {
    const current = await getState();
    await saveState(current.blocked, enabledEl.checked);
  });

  formEl.addEventListener("submit", async (event) => {
    event.preventDefault();
    const raw = valueEl.value;
    const type = formEl.querySelector('input[name="type"]:checked').value;
    const state = await getState();

    if (!raw.trim()) {
      setError("Escribe un dominio o una URL.");
      return;
    }

    let value;
    let valid;
    if (type === "domain") {
      value = normalizeDomain(raw);
      valid = isValidDomain(value);
      if (!valid) {
        setError("Dominio inválido. Ej: youtube.com (sin https://, sin www y sin carpetas).");
        return;
      }
    } else {
      value = normalizeUrl(raw);
      valid = value.includes(".");
      if (!valid) {
        setError("URL inválida. Ej: reddit.com/r/funny");
        return;
      }
    }

    if (state.blocked.some((item) => item.value === value && item.type === type)) {
      setError("Ese sitio ya está en tu lista.");
      return;
    }

    const entry = {
      id: Date.now().toString(36) + Math.random().toString(36).slice(2, 7),
      type,
      value
    };
    const next = [...state.blocked, entry];
    await saveState(next, state.enabled);
    valueEl.value = "";
    setError("");
    render(next);
  });
}

init();