const SETTINGS_PANEL_STORAGE_PREFIX = "ld:settings-panel:";
const SETTINGS_SCROLL_STORAGE_KEY = "ld:settings-scroll:general";
const SETTINGS_DRAFT_STORAGE_PREFIX = "ld:settings-draft:";

function getSettingsLocalStorage() {
  try {
    return window.localStorage;
  } catch (_error) {
    return null;
  }
}

function getSettingsPanelStorageKey(panelId) {
  return `${SETTINGS_PANEL_STORAGE_PREFIX}${panelId}`;
}

function getSettingsDraftStorageKey(formId) {
  return formId ? `${SETTINGS_DRAFT_STORAGE_PREFIX}${formId}` : "";
}

export function getStoredSettingsPanelExpanded(panelId) {
  const storage = getSettingsLocalStorage();
  if (!storage || !panelId) {
    return false;
  }

  return storage.getItem(getSettingsPanelStorageKey(panelId)) === "true";
}

export function setStoredSettingsPanelExpanded(panelId, expanded) {
  const storage = getSettingsLocalStorage();
  if (!storage || !panelId) {
    return;
  }

  storage.setItem(getSettingsPanelStorageKey(panelId), expanded ? "true" : "false");
}

export function getStoredSettingsScrollPosition() {
  const storage = getSettingsLocalStorage();
  if (!storage) {
    return null;
  }

  const storedValue = Number.parseInt(
    storage.getItem(SETTINGS_SCROLL_STORAGE_KEY) || "",
    10,
  );
  if (!Number.isFinite(storedValue) || storedValue <= 0) {
    return null;
  }

  return storedValue;
}

export function setStoredSettingsScrollPosition(scrollTop) {
  const storage = getSettingsLocalStorage();
  if (!storage) {
    return;
  }

  storage.setItem(SETTINGS_SCROLL_STORAGE_KEY, String(Math.round(scrollTop)));
}

export function getStoredSettingsDraft(formId) {
  const storage = getSettingsLocalStorage();
  const key = getSettingsDraftStorageKey(formId);
  if (!storage || !key) {
    return null;
  }

  const rawValue = storage.getItem(key);
  if (rawValue === null) {
    return null;
  }

  try {
    const parsedValue = JSON.parse(rawValue);
    if (parsedValue && typeof parsedValue.value === "string") {
      return parsedValue.value;
    }
    if (typeof parsedValue === "string") {
      setStoredSettingsDraft(formId, parsedValue);
      return parsedValue;
    }
  } catch (_error) {
    // Keep compatibility with legacy plain-string draft values.
    setStoredSettingsDraft(formId, rawValue);
    return rawValue;
  }

  storage.removeItem(key);
  return null;
}

export function setStoredSettingsDraft(formId, value) {
  const storage = getSettingsLocalStorage();
  const key = getSettingsDraftStorageKey(formId);
  if (!storage || !key) {
    return;
  }

  storage.setItem(key, JSON.stringify({ value }));
}

export function clearStoredSettingsDraft(formId) {
  const storage = getSettingsLocalStorage();
  const key = getSettingsDraftStorageKey(formId);
  if (!storage || !key) {
    return;
  }

  storage.removeItem(key);
}
