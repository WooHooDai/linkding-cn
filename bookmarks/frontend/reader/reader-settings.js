import { normalizeBaseUrl, parseJsonScriptValue } from "./reader-api.js";

const SETTINGS_KEY = "ld:reader:settings";
const GLOBAL_SKIN = "global";
const THEME_MODES = new Set(["light", "dark"]);

export function loadReaderSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

export function saveReaderSettings(partial) {
  const current = loadReaderSettings();
  localStorage.setItem(SETTINGS_KEY, JSON.stringify({ ...current, ...partial }));
}

// 全局主题的解析口径与 stylesheets.html 一致：显式 light/dark，否则跟随系统
function resolveGlobalTheme() {
  const mode = document.documentElement.dataset.themeMode;
  if (THEME_MODES.has(mode)) return mode;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyReaderTheme(theme) {
  document.documentElement.setAttribute(
    "data-theme",
    THEME_MODES.has(theme) ? theme : resolveGlobalTheme(),
  );
}

export function setReaderTheme(theme) {
  saveReaderSettings({ theme });
  applyReaderTheme(theme);
}

// ---- 皮肤 ----

function globalSkinId() {
  return String(parseJsonScriptValue("reader-global-skin", "") || "");
}

function skinStylesheetUrl(skinId) {
  const apiBase = normalizeBaseUrl(
    parseJsonScriptValue("reader-api-base-url", "/api/"),
  );
  const root = new URL(apiBase, window.location.href);
  return new URL(`../skin/${skinId || "default"}.css`, root).href;
}

/**
 * 应用阅读页皮肤。skin 为 "global" 时跟随全局设置，空串表示默认皮肤（不挂皮肤表）。
 *
 * 注意不能动 data-role="theme" 那条主样式表：全局皮肤表另有一条 data-role="skin"，
 * 阅读页自己的覆盖再挂一条 data-role="reader-skin"，靠 media 开关禁用全局那条。
 */
export function applyReaderSkin(skin) {
  const requested = skin == null ? GLOBAL_SKIN : String(skin);
  const skinId = requested === GLOBAL_SKIN ? globalSkinId() : requested;
  const globalLink = document.querySelector('link[data-role="skin"]');
  let ownLink = document.querySelector('link[data-role="reader-skin"]');

  if (requested === GLOBAL_SKIN) {
    globalLink?.removeAttribute("media");
    ownLink?.remove();
    return;
  }

  // 覆盖态下先压掉全局皮肤表，再决定要不要挂自己这条
  globalLink?.setAttribute("media", "not all");

  if (!skinId) {
    ownLink?.remove();
    return;
  }

  const nextUrl = skinStylesheetUrl(skinId);
  if (!ownLink) {
    ownLink = document.createElement("link");
    ownLink.rel = "stylesheet";
    ownLink.dataset.role = "reader-skin";
    ownLink.href = nextUrl;
    document.head.appendChild(ownLink);
    return;
  }
  // 同一条皮肤不重复请求
  const currentPath = new URL(ownLink.href, window.location.href).pathname;
  if (currentPath !== new URL(nextUrl).pathname) {
    ownLink.href = nextUrl;
  }
}

export function setReaderSkin(skin) {
  saveReaderSettings({ skin });
  applyReaderSkin(skin);
}
