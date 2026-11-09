import { LitElement, html } from "lit";
import { READER_ICONS } from "./reader-icons";
import { gettext } from "../utils/i18n.js";
import {
  loadReaderSettings,
  saveReaderSettings,
  setReaderTheme,
  setReaderSkin,
} from "./reader-settings.js";
import { parseJsonScriptValue } from "./reader-api.js";
import { getCSRFToken } from "../utils/csrf.js";

async function syncReaderSettingsToServer(partial) {
  try {
    const resp = await fetch("/api/user/profile/reader-settings/", {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCSRFToken(),
      },
      body: JSON.stringify(partial),
    });
    if (resp.ok) return await resp.json();
  } catch { /* silent */ }
  return null;
}

async function fetchReaderSettingsFromServer() {
  try {
    const resp = await fetch("/api/user/profile/");
    if (resp.ok) {
      const data = await resp.json();
      return data.reader_settings || {};
    }
  } catch { /* silent */ }
  return {};
}

const SETTINGS_KEY = "ld:reader:settings";
const MOBILE_BREAKPOINT = 768;
const WIDTH_SPLIT_BREAKPOINT = 960;
const WIDTH_MIN = 220;
const WIDE_DEFAULT_MIN = 640;
const WIDE_DEFAULT_MAX = 1200;
const INLINE_SIDEBAR_WIDTH = 340;

const FONT_OPTIONS = [
  { value: "sans", label: gettext("System Default") },
  { value: "sansSerif", label: gettext("Sans Serif") },
  { value: "serif", label: gettext("Serif") },
  { value: "mono", label: gettext("Monospace") },
];

const FONT_FAMILY = {
  // System default stack used by the app theme variables.
  sans: "var(--base-font-family)",
  // Explicit generic sans-serif option.
  sansSerif:
    'Arial, "Helvetica Neue", "Noto Sans", "PingFang SC", "Microsoft YaHei", sans-serif',
  serif: 'Georgia, "Noto Serif", "Times New Roman", serif',
  mono: "var(--mono-font-family)",
};

const DEFAULT_READING_SPEED = 400;

const DEFAULT_SETTINGS = {
  fontSize: "18",
  font: "sans",
  width: "640",
  lineHeight: "1.7",
  readingSpeed: String(DEFAULT_READING_SPEED),
};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function settingsEqual(a, b) {
  return (
    a?.fontSize === b?.fontSize &&
    a?.font === b?.font &&
    a?.width === b?.width &&
    a?.lineHeight === b?.lineHeight &&
    a?.widthMode === b?.widthMode &&
    a?.readingSpeed === b?.readingSpeed
  );
}

function ceilToStep(value, step) {
  if (!Number.isFinite(value) || step <= 0) return value;
  return Math.ceil(value / step) * step;
}

function getVisualViewportWidth() {
  return Math.max(
    0,
    Math.floor(window.visualViewport?.width || window.innerWidth || 0)
  );
}

function getReaderAvailableWidth({
  sidebarOpen = false,
  viewportWidth = getVisualViewportWidth(),
} = {}) {
  const hasInlineSidebar = viewportWidth > MOBILE_BREAKPOINT;
  const sidebarWidth =
    sidebarOpen && hasInlineSidebar ? INLINE_SIDEBAR_WIDTH : 0;
  return Math.max(0, viewportWidth - sidebarWidth);
}

function getDefaultWidthValue() {
  const fallback = Number.parseInt(DEFAULT_SETTINGS.width, 10) || 640;
  const viewport = getVisualViewportWidth();
  if (!Number.isFinite(viewport) || viewport <= 0) return String(fallback);

  let rawDefaultWidth;
  if (viewport < WIDTH_SPLIT_BREAKPOINT) {
    rawDefaultWidth = viewport * 0.9;
  } else {
    const available = Math.max(0, viewport - INLINE_SIDEBAR_WIDTH);
    rawDefaultWidth = clamp(available * 0.72, WIDE_DEFAULT_MIN, WIDE_DEFAULT_MAX);
  }

  const roundedWidth = ceilToStep(rawDefaultWidth, 5);
  const boundedWidth = clamp(
    roundedWidth,
    Math.min(WIDTH_MIN, viewport),
    viewport
  );
  return String(Math.round(boundedWidth));
}

function getDefaultSettings(options = {}) {
  return {
    ...DEFAULT_SETTINGS,
    width: getDefaultWidthValue(),
    widthMode: "auto",
  };
}

function normalizeSettings(partial = {}, options = {}) {
  const defaults = getDefaultSettings(options);
  const merged = { ...defaults, ...partial };

  const fontBounds = getSettingBounds("fontSize");
  const widthBounds = getSettingBounds("width");
  const lineHeightBounds = getSettingBounds("lineHeight");
  const readingSpeedBounds = getSettingBounds("readingSpeed");

  const fontSize = clamp(
    Number.parseFloat(merged.fontSize) || Number.parseFloat(defaults.fontSize),
    fontBounds.min,
    fontBounds.max
  );
  const width = clamp(
    Number.parseFloat(merged.width) || Number.parseFloat(defaults.width),
    widthBounds.min,
    widthBounds.max
  );
  const lineHeight = clamp(
    Number.parseFloat(merged.lineHeight) ||
      Number.parseFloat(defaults.lineHeight),
    lineHeightBounds.min,
    lineHeightBounds.max
  );
  const readingSpeed = clamp(
    Number.parseFloat(merged.readingSpeed) || DEFAULT_READING_SPEED,
    readingSpeedBounds.min,
    readingSpeedBounds.max
  );

  return {
    fontSize: String(Math.round(fontSize)),
    font:
      typeof merged.font === "string" && FONT_FAMILY[merged.font]
        ? merged.font
        : defaults.font,
    width: String(Math.round(width)),
    lineHeight: lineHeight.toFixed(1),
    widthMode: merged.widthMode === "manual" ? "manual" : "auto",
    readingSpeed: String(Math.round(readingSpeed)),
  };
}

function loadSettings(options = {}) {
  const defaults = getDefaultSettings(options);
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return defaults;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return defaults;
    return normalizeSettings(parsed, options);
  } catch {
    return defaults;
  }
}

function saveSettings(settings) {
  saveReaderSettings(settings);
}

function getSettingBounds(key) {
  if (key === "lineHeight") {
    return { min: 1.2, max: 3, step: 0.1 };
  }
  if (key === "width") {
    const viewportMax = getVisualViewportWidth();
    const max = Math.max(0, viewportMax);
    const min = Math.min(WIDTH_MIN, max);
    const step = viewportMax < WIDTH_SPLIT_BREAKPOINT ? 10 : 20;
    return {
      min,
      max,
      step,
    };
  }
  if (key === "readingSpeed") {
    return { min: 100, max: 1000, step: 10 };
  }
  return { min: 12, max: 28, step: 1 };
}

function applySettings(settings, { sidebarOpen = false } = {}) {
  const el = document.documentElement;
  const fontSize =
    Number.parseFloat(settings.fontSize) ||
    Number.parseFloat(DEFAULT_SETTINGS.fontSize);
  const width =
    Number.parseFloat(settings.width) ||
    Number.parseFloat(DEFAULT_SETTINGS.width);
  const lineHeight =
    Number.parseFloat(settings.lineHeight) ||
    Number.parseFloat(DEFAULT_SETTINGS.lineHeight);
  const viewportWidth = getVisualViewportWidth();
  const availableWidth = getReaderAvailableWidth({ sidebarOpen, viewportWidth });
  const maxAllowedWidth = Math.min(viewportWidth, availableWidth);
  const normalizedWidth = clamp(
    Math.round(width),
    Math.min(WIDTH_MIN, maxAllowedWidth),
    maxAllowedWidth
  );

  el.style.setProperty("--reader-font-size", `${Math.round(fontSize)}px`);
  el.style.setProperty(
    "--reader-font-family",
    FONT_FAMILY[settings.font] || FONT_FAMILY[DEFAULT_SETTINGS.font]
  );
  el.style.setProperty("--reader-max-width", `${normalizedWidth}px`);
  el.style.setProperty("--reader-line-height", String(lineHeight));
}

export class ReaderToolbar extends LitElement {
  createRenderRoot() { return this; }

  static properties = {
    title: { type: String },
    progress: { type: Number },
    sidebarOpen: { type: Boolean },
    bookmarkUrl: { type: String },
    snapshotUrl: { type: String },
    isEditable: { type: Boolean },
    _settingsOpen: { type: Boolean, state: true },
    _settings: { type: Object, state: true },
    _fontMenuOpen: { type: Boolean, state: true },
  };

  constructor() {
    super();
    this.title = "";
    this.progress = 0;
    this.sidebarOpen = false;
    this.bookmarkUrl = "";
    this.snapshotUrl = "";
    this.isEditable = true;
    this._settingsOpen = false;
    this._fontMenuOpen = false;
    this._settings = {};
  }

  async _syncFromServer() {
    const server = await fetchReaderSettingsFromServer();
    const local = loadReaderSettings();
    const merged = {};
    if (server.reading_speed != null && local.readingSpeed !== String(server.reading_speed)) {
      merged.readingSpeed = String(server.reading_speed);
    }
    if (server.font_size != null && local.fontSize !== String(server.font_size)) {
      merged.fontSize = String(server.font_size);
    }
    if (server.line_height != null && local.lineHeight !== String(server.line_height)) {
      merged.lineHeight = String(parseFloat(server.line_height).toFixed(1));
    }
    if (Object.keys(merged).length > 0) {
      saveReaderSettings(merged);
      this._settings = normalizeSettings({ ...this._settings, ...merged });
      applySettings(this._settings);
      this.requestUpdate();
      document.dispatchEvent(new CustomEvent("reader-settings-changed"));
    }
  }

  connectedCallback() {
    super.connectedCallback();
    // 延迟到元素挂载后再初始化，确保 getVisualViewportWidth() 返回真实视口宽度
    this._settings = loadSettings();
    saveSettings(this._settings);
    this._syncFromServer();
    this._onTitleUpdate = (e) => {
      if (e.detail.title != null) this.title = e.detail.title || "";
    };
    this._onViewportResize = () => {
      const base = this._settings.widthMode === "auto"
        ? { ...this._settings, width: getDefaultWidthValue() }
        : this._settings;
      const normalized = normalizeSettings(base, { sidebarOpen: this.sidebarOpen });
      if (!settingsEqual(normalized, this._settings)) {
        this._settings = normalized;
        saveSettings(normalized);
      }
      applySettings(normalized, { sidebarOpen: this.sidebarOpen });
      this.requestUpdate();
    };
    document.addEventListener("bookmark-updated", this._onTitleUpdate);
    window.addEventListener("resize", this._onViewportResize);
    window.visualViewport?.addEventListener("resize", this._onViewportResize);
    // 挂载后触发一次，用真实视口宽度校正 auto 模式下的页面宽度
    this._onViewportResize();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    document.removeEventListener("bookmark-updated", this._onTitleUpdate);
    document.removeEventListener("pointerdown", this._onOutsideClick);
    window.removeEventListener("resize", this._onViewportResize);
    window.visualViewport?.removeEventListener("resize", this._onViewportResize);
  }

  _onOutsideClick = (e) => {
    if (!this._settingsOpen) return;
    if (!this.contains(e.target)) {
      this._settingsOpen = false;
      this._fontMenuOpen = false;
    }
  };

  updated(changed) {
    if (changed.has("_settingsOpen")) {
      if (this._settingsOpen) {
        document.addEventListener("pointerdown", this._onOutsideClick);
      } else {
        document.removeEventListener("pointerdown", this._onOutsideClick);
        this._fontMenuOpen = false;
      }
    }
    if (changed.has("sidebarOpen")) {
      const normalized = normalizeSettings(this._settings, {
        sidebarOpen: this.sidebarOpen,
      });
      if (!settingsEqual(normalized, this._settings)) {
        this._settings = normalized;
        saveSettings(normalized);
      }
      applySettings(normalized, { sidebarOpen: this.sidebarOpen });
    }
  }

  _handleAction(action) {
    if (action === "open-original" && this.bookmarkUrl) window.open(this.bookmarkUrl, "_blank");
    else if (action === "open-snapshot" && this.snapshotUrl) window.open(this.snapshotUrl, "_blank");
    else if (action === "toggle-settings") {
      this._settingsOpen = !this._settingsOpen;
      if (!this._settingsOpen) this._fontMenuOpen = false;
    }
    else if (action === "toggle-sidebar") this.dispatchEvent(new CustomEvent("toggle-sidebar", { bubbles: true }));
    else if (action === "add-bookmark") this.dispatchEvent(new CustomEvent("add-bookmark", { bubbles: true }));
  }

  _handleNumberInput(key) {
    const input = this.querySelector(`[data-setting="${key}"]`);
    if (!input) return;
    const num = parseFloat(input.value);
    if (isNaN(num)) return;
    const { min, max } = getSettingBounds(key);
    const clamped = Math.max(min, Math.min(max, num));
    const formatted = key === "lineHeight" ? clamped.toFixed(1) : String(Math.round(clamped));
    const extra = key === "width" ? { widthMode: "manual" } : {};
    const updated = normalizeSettings(
      { ...this._settings, [key]: formatted, ...extra },
      { sidebarOpen: this.sidebarOpen }
    );
    this._settings = updated;
    saveSettings(updated);
    applySettings(updated, { sidebarOpen: this.sidebarOpen });
    this._syncSettingToServer(key, updated);
  }

  _setSettingNum(key, delta) {
    const current = parseFloat(this._settings[key]) || parseFloat(DEFAULT_SETTINGS[key]);
    const { min, max, step } = getSettingBounds(key);
    const newVal = Math.max(min, Math.min(max, current + delta * step));
    const formatted = key === "lineHeight" ? newVal.toFixed(1) : String(Math.round(newVal));
    const extra = key === "width" ? { widthMode: "manual" } : {};
    const updated = normalizeSettings(
      { ...this._settings, [key]: formatted, ...extra },
      { sidebarOpen: this.sidebarOpen }
    );
    this._settings = updated;
    saveSettings(updated);
    applySettings(updated, { sidebarOpen: this.sidebarOpen });
    this._syncSettingToServer(key, updated);
  }

  _handleSliderInput(key, value) {
    const num = parseFloat(value);
    if (isNaN(num)) return;
    const { min, max } = getSettingBounds(key);
    const clamped = Math.max(min, Math.min(max, num));
    const formatted = key === "lineHeight" ? clamped.toFixed(1) : String(Math.round(clamped));
    const extra = key === "width" ? { widthMode: "manual" } : {};
    const updated = normalizeSettings(
      { ...this._settings, [key]: formatted, ...extra },
      { sidebarOpen: this.sidebarOpen }
    );
    this._settings = updated;
    saveSettings(updated);
    applySettings(updated, { sidebarOpen: this.sidebarOpen });
    this._syncSettingToServer(key, updated);
  }

  _syncSettingToServer(key, settings) {
    const serverKeyMap = {
      readingSpeed: "reading_speed",
      fontSize: "font_size",
      lineHeight: "line_height",
    };
    const serverKey = serverKeyMap[key];
    if (serverKey) {
      const val = key === "lineHeight" ? parseFloat(settings[key]) : Number(settings[key]);
      syncReaderSettingsToServer({ [serverKey]: val });
    }
    if (key === "readingSpeed" || key === "fontSize" || key === "lineHeight") {
      document.dispatchEvent(new CustomEvent("reader-settings-changed"));
    }
  }

  _resetSetting(key) {
    const resetValue =
      key === "width"
        ? getDefaultWidthValue()
        : DEFAULT_SETTINGS[key];
    const extra = key === "width" ? { widthMode: "auto" } : {};
    const updated = normalizeSettings(
      {
        ...this._settings,
        [key]: resetValue,
        ...extra,
      },
      { sidebarOpen: this.sidebarOpen }
    );
    this._settings = updated;
    saveSettings(updated);
    applySettings(updated, { sidebarOpen: this.sidebarOpen });
    this._syncSettingToServer(key, updated);
  }

  _setFont(value) {
    const updated = normalizeSettings(
      { ...this._settings, font: value },
      { sidebarOpen: this.sidebarOpen }
    );
    this._settings = updated;
    saveSettings(updated);
    applySettings(updated, { sidebarOpen: this.sidebarOpen });
  }

  _getFontPreviewStack(value) {
    return FONT_FAMILY[value] || FONT_FAMILY[DEFAULT_SETTINGS.font];
  }

  _getFontOption(value) {
    return (
      FONT_OPTIONS.find((opt) => opt.value === value) ||
      FONT_OPTIONS.find((opt) => opt.value === DEFAULT_SETTINGS.font)
    );
  }

  _toggleFontMenu = () => {
    this._fontMenuOpen = !this._fontMenuOpen;
    if (this._fontMenuOpen) {
      requestAnimationFrame(() => {
        const selected = this.querySelector(
          ".settings-font-option[aria-selected='true']"
        );
        selected?.focus();
      });
    }
  };

  _closeFontMenu({ focusTrigger = false } = {}) {
    this._fontMenuOpen = false;
    if (!focusTrigger) return;
    requestAnimationFrame(() => {
      this.querySelector("[data-font-trigger]")?.focus();
    });
  }

  _selectFontOption(value) {
    this._setFont(value);
    this._closeFontMenu({ focusTrigger: true });
  }

  _handleFontTriggerKeydown = (e) => {
    if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (!this._fontMenuOpen) {
        this._toggleFontMenu();
      }
      return;
    }
    if (e.key === "Escape" && this._fontMenuOpen) {
      e.preventDefault();
      this._closeFontMenu({ focusTrigger: true });
    }
  };

  _handleFontMenuKeydown = (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      this._closeFontMenu({ focusTrigger: true });
      return;
    }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const options = Array.from(this.querySelectorAll(".settings-font-option"));
    if (!options.length) return;
    const active = document.activeElement;
    const currentIndex = options.indexOf(active);
    const delta = e.key === "ArrowDown" ? 1 : -1;
    const nextIndex =
      currentIndex < 0
        ? 0
        : (currentIndex + delta + options.length) % options.length;
    options[nextIndex]?.focus();
  };

  _handleSettingsPanelPointerDown = (e) => {
    e.stopPropagation();
    if (!this._fontMenuOpen) return;
    const isFontArea = e.target.closest(".settings-font-select");
    if (!isFontArea) {
      this._fontMenuOpen = false;
    }
  };

  _renderSettingsPanel() {
    if (!this._settingsOpen) return html``;

    const sliderRow = (key, label, unit) => {
      const bounds = getSettingBounds(key);
      const defaultVal = DEFAULT_SETTINGS[key];
      const isDefault = this._settings[key] === defaultVal ||
        (key === "width" && this._settings.widthMode === "auto");
      return html`
      <div class="settings-section">
        <div class="settings-slider-header">
          <span class="settings-label">${label}</span>
          <span class="settings-slider-value">${this._settings[key]}${unit}</span>
        </div>
        <div class="settings-slider-row">
          <input class="settings-slider" type="range"
            min=${String(bounds.min)} max=${String(bounds.max)} step=${String(bounds.step)}
            .value=${this._settings[key]}
            @input=${(e) => this._handleSliderInput(key, e.target.value)} />
          ${!isDefault ? html`
            <button class="settings-reset-btn" title=${gettext("Reset")}
              @click=${() => this._resetSetting(key)} .innerHTML=${READER_ICONS["reset"]}></button>
          ` : ""}
        </div>
      </div>
    `;
    };

    const currentFont = this._getFontOption(this._settings.font);
    const currentFontLabel = currentFont?.label || gettext("System Default");

    const readerSettings = loadReaderSettings();
    const currentTheme = readerSettings.theme || "auto";
    const currentSkin = readerSettings.skin ?? "global";
    const skins = parseJsonScriptValue("reader-skins", []);

    return html`
      <div
        id="reader-settings-panel"
        data-open="true"
        @pointerdown=${this._handleSettingsPanelPointerDown}
      >
        <div class="settings-section">
          <div class="settings-label">${gettext("Theme")}</div>
          <div class="settings-select-wrap">
            <select class="settings-select" .value=${currentTheme}
              @change=${(e) => setReaderTheme(e.target.value)}>
              <option value="auto">${gettext("Follow Global")}</option>
              <option value="light">${gettext("Light")}</option>
              <option value="dark">${gettext("Dark")}</option>
            </select>
          </div>
        </div>
        <div class="settings-section">
          <div class="settings-label">${gettext("Skin")}</div>
          <div class="settings-select-wrap">
            <select class="settings-select" .value=${currentSkin}
              @change=${(e) => setReaderSkin(e.target.value)}>
              <option value="global">${gettext("Follow Global")}</option>
              ${skins.map(
                (skin) => html`<option value=${skin.id}>${skin.name}</option>`
              )}
            </select>
          </div>
        </div>
        <div class="settings-section">
          <div class="settings-label">${gettext("Font")}</div>
          <div class="settings-select-wrap settings-font-select">
            <button
              type="button"
              class="settings-select settings-font-trigger"
              data-font-trigger
              style=${`font-family: ${this._getFontPreviewStack(this._settings.font)};`}
              aria-haspopup="listbox"
              aria-expanded=${this._fontMenuOpen ? "true" : "false"}
              aria-controls="reader-font-listbox"
              @click=${this._toggleFontMenu}
              @keydown=${this._handleFontTriggerKeydown}
            >
              <span class="settings-font-trigger-label">${currentFontLabel}</span>
            </button>
            <span class="settings-select-arrow" .innerHTML=${READER_ICONS["chevron-down"]}></span>
            ${this._fontMenuOpen
              ? html`
                  <div
                    id="reader-font-listbox"
                    class="settings-font-menu"
                    role="listbox"
                    aria-label=${gettext("Font family")}
                    @keydown=${this._handleFontMenuKeydown}
                  >
                    ${FONT_OPTIONS.map(
                      (opt) => html`
                        <button
                          type="button"
                          class="settings-font-option"
                          role="option"
                          aria-selected=${this._settings.font === opt.value ? "true" : "false"}
                          style=${`font-family: ${this._getFontPreviewStack(opt.value)};`}
                          @click=${() => this._selectFontOption(opt.value)}
                        >
                          <span class="settings-font-option-label">${opt.label}</span>
                        </button>
                      `
                    )}
                  </div>
                `
              : html``}
          </div>
        </div>
        ${sliderRow("fontSize", gettext("Font Size"), "px")}
        ${sliderRow("width", gettext("Page Width"), "px")}
        ${sliderRow("lineHeight", gettext("Line Height"), "")}
        ${sliderRow("readingSpeed", gettext("Reading Speed"), " " + gettext("wpm"))}
      </div>
    `;
  }

  render() {
    const progressPct = Math.min(100, Math.max(0, this.progress));
    return html`
      <div id="reader-toolbar">
        <div class="toolbar-main">
          <div class="toolbar-spacer"></div>
          <span class="toolbar-title" .title=${this.title || gettext("Reader")}>${this.title || gettext("Reader")}</span>
          <div class="toolbar-actions">
            <button class="toolbar-btn" title=${gettext("Open original")} @click=${() => this._handleAction("open-original")} .innerHTML=${READER_ICONS["open-original"]}></button>
            ${this.snapshotUrl ? html`<button class="toolbar-btn" title=${gettext("View snapshot")} @click=${() => this._handleAction("open-snapshot")} .innerHTML=${READER_ICONS["open-snapshot"]}></button>` : html``}
            ${!this.isEditable ? html`<button class="toolbar-btn" title=${gettext("Add to my bookmarks")} @click=${() => this._handleAction("add-bookmark")} .innerHTML=${READER_ICONS["add-bookmark"] || READER_ICONS["tab-details"]}></button>` : html``}
            <button class="toolbar-btn" data-active=${this._settingsOpen ? "true" : "false"} title=${gettext("Reading settings")} @click=${() => this._handleAction("toggle-settings")} .innerHTML=${READER_ICONS["font-size"]}></button>
            <button class="toolbar-btn" data-active=${this.sidebarOpen ? "true" : "false"} title=${gettext("Toggle sidebar")} @click=${() => this._handleAction("toggle-sidebar")} .innerHTML=${READER_ICONS["toggle-sidebar"]}></button>
          </div>
        </div>
        <div class="toolbar-progress"><div class="toolbar-progress-bar" style="width: ${progressPct}%"></div></div>
        ${this._renderSettingsPanel()}
      </div>
    `;
  }
}

customElements.define("reader-toolbar", ReaderToolbar);
