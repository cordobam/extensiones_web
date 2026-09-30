const STORE_KEY = "blocked";

const params = new URLSearchParams(location.search);
const site = params.get("site");
const type = params.get("type");
const original = params.get("url");

const siteEl = document.getElementById("site");
const backBtn = document.getElementById("back");
const removeBtn = document.getElementById("remove");

siteEl.textContent = site ? `Bloqueado: ${site}` : "Bloqueado";
document.title = "Página bloqueada";

backBtn.addEventListener("click", () => {
  if (history.length > 1) {
    history.back();
  } else {
    location.replace("about:home");
  }
});

removeBtn.addEventListener("click", async () => {
  const data = await browser.storage.local.get(STORE_KEY);
  const blocked = data[STORE_KEY] || [];
  if (blocked.length > 0) {
    const filtered = site && type
      ? blocked.filter((entry) => !(entry.value === site && entry.type === type))
      : blocked;
    await browser.storage.local.set({ [STORE_KEY]: filtered });
  }
  setTimeout(() => {
    location.href = original || "about:home";
  }, 150);
});