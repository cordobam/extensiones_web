const STORE_KEY = "blocked";
const ENABLED_KEY = "enabled";

let blockedCache = [];
let enabledCache = true;

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

function findMatch(url) {
  if (!enabledCache) return null;
  const lower = url.toLowerCase();
  let host = null;
  try {
    host = new URL(url).hostname.replace(/^www\./, "");
  } catch (error) {
    return null;
  }
  for (const entry of blockedCache) {
    if (entry.type === "domain") {
      const value = normalizeDomain(entry.value);
      if (host === value || host.endsWith("." + value)) return entry;
    } else {
      if (lower.includes(normalizeUrl(entry.value))) return entry;
    }
  }
  return null;
}

let loading = browser.storage.local.get([STORE_KEY, ENABLED_KEY]);
loading.then((data) => {
  blockedCache = data[STORE_KEY] || [];
  enabledCache = data[ENABLED_KEY] !== false;
});

browser.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local") return;
  if (changes[STORE_KEY]) blockedCache = changes[STORE_KEY].newValue || [];
  if (changes[ENABLED_KEY]) enabledCache = changes[ENABLED_KEY].newValue !== false;
});

browser.webRequest.onBeforeRequest.addListener(
  (details) => {
    const entry = findMatch(details.url);
    if (!entry) return {};
    const params = new URLSearchParams({
      site: entry.value,
      type: entry.type,
      url: details.url
    });
    return { redirectUrl: browser.runtime.getURL("block.html") + "?" + params.toString() };
  },
  { urls: ["<all_urls>"], types: ["main_frame"] },
  ["blocking"]
);