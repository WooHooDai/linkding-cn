import {
  applyRequestHeaders,
  stripPreferenceQueryParams,
} from "./helpers";

const DOMAIN_VIEW_MODE_STORAGE_KEY = "ld:domain-view-mode";
const DOMAIN_COMPACT_MODE_STORAGE_KEY = "ld:domain-compact-mode";
const DOMAIN_QUERY_PARAMS = ["domain_view", "domain_compact"];

function normalizeViewMode(value) {
  if (value === "icon" || value === "full") {
    return value;
  }
  return null;
}

export function parseStoredCompactMode(value) {
  if (value === "1") {
    return true;
  }
  if (value === "0") {
    return false;
  }
  return null;
}

export function stripDomainPreferenceParams(href) {
  return stripPreferenceQueryParams(href, DOMAIN_QUERY_PARAMS);
}

export function getDomainStateFromElement(domainTree) {
  if (!domainTree) {
    return null;
  }

  return {
    currentViewMode: normalizeViewMode(domainTree.dataset.domainViewMode),
    currentCompactMode: domainTree.dataset.domainCompactMode !== "false",
  };
}

export function getRenderedDomainState() {
  return getDomainStateFromElement(document.querySelector("[ld-domain-tree]"));
}

export function getStoredDomainState() {
  return {
    storedViewMode: normalizeViewMode(
      window.localStorage.getItem(DOMAIN_VIEW_MODE_STORAGE_KEY),
    ),
    storedCompactMode: parseStoredCompactMode(
      window.localStorage.getItem(DOMAIN_COMPACT_MODE_STORAGE_KEY),
    ),
  };
}

export function buildDomainRequestHeaders({
  currentViewMode,
  currentCompactMode,
  storedViewMode,
  storedCompactMode,
}) {
  const viewMode = storedViewMode || currentViewMode;
  const compactMode = storedCompactMode ?? currentCompactMode;

  if (!viewMode || compactMode === null || compactMode === undefined) {
    return null;
  }

  return {
    "X-Linkding-Domain-View": viewMode,
    "X-Linkding-Domain-Compact": compactMode ? "1" : "0",
  };
}

export function applyDomainRequestHeaders(headerBag, domainHeaders) {
  applyRequestHeaders(headerBag, domainHeaders);
}

export function storeRenderedDomainPreferences({
  currentViewMode,
  currentCompactMode,
}) {
  if (currentViewMode) {
    window.localStorage.setItem(DOMAIN_VIEW_MODE_STORAGE_KEY, currentViewMode);
  }
  if (currentCompactMode !== null && currentCompactMode !== undefined) {
    window.localStorage.setItem(
      DOMAIN_COMPACT_MODE_STORAGE_KEY,
      currentCompactMode ? "1" : "0",
    );
  }
}

export function storeDomainPreferenceTargets({
  targetViewMode,
  targetCompactMode,
}) {
  const normalizedViewMode = normalizeViewMode(targetViewMode);
  if (normalizedViewMode) {
    window.localStorage.setItem(DOMAIN_VIEW_MODE_STORAGE_KEY, normalizedViewMode);
  }

  const parsedCompactMode = parseStoredCompactMode(targetCompactMode);
  if (parsedCompactMode !== null) {
    window.localStorage.setItem(
      DOMAIN_COMPACT_MODE_STORAGE_KEY,
      parsedCompactMode ? "1" : "0",
    );
  }
}

let domainDisplayPreferencesRegistered = false;

export function registerDomainDisplayPreferences() {
  if (domainDisplayPreferencesRegistered) {
    return;
  }

  document.addEventListener("turbo:before-fetch-request", (event) => {
    const domainHeaders = buildDomainRequestHeaders({
      ...getRenderedDomainState(),
      ...getStoredDomainState(),
    });

    if (!domainHeaders) {
      return;
    }

    event.detail.fetchOptions.headers ||= {};
    applyDomainRequestHeaders(event.detail.fetchOptions.headers, domainHeaders);
  });

  domainDisplayPreferencesRegistered = true;
}
