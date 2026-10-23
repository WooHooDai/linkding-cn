import { LitElement, html } from "lit";
import { repeat } from "lit/directives/repeat.js";
import { HIGHLIGHT_COLORS } from "./anchoring/highlighter";
import { READER_ICONS } from "./reader-icons";
import { gettext, ngettext, interpolate } from "../utils/i18n.js";
import { loadReaderSettings, saveReaderSettings } from "./reader-settings.js";
import { showToast } from "../components/toast.js";
import { getCSRFToken } from "../utils/csrf.js";

function normalizeBaseUrl(baseUrl) {
  const value = String(baseUrl || "").trim();
  if (!value) return "";
  return `${value.replace(/\/+$/, "")}/`;
}

function joinPath(baseUrl, path) {
  return `${normalizeBaseUrl(baseUrl)}${String(path || "").replace(/^\/+/, "")}`;
}

export class ReaderSidebar extends LitElement {
  createRenderRoot() { return this; }

  static properties = {
    open: { type: Boolean }, annotations: { type: Array }, bookmarkData: { type: Object },
    assetList: { type: Array }, activeTab: { type: String }, apiBase: { type: String },
    assetsBase: { type: String }, bookmarksIndexUrl: { type: String },
    isEditable: { type: Boolean },
    _allTags: { type: Array, state: true }, _colorPickerId: { type: String, state: true },
    _confirmDelAnnId: { type: String, state: true },
    _renameAssetId: { type: String, state: true }, _renameValue: { type: String, state: true }, _renameOriginal: { type: String, state: true },
    _buttonMode: { type: String, state: true }, _activeAnnId: { type: String, state: true },
    _tagsEditing: { type: Boolean, state: true },
  };

  constructor() {
    super();
    this.open = false; this.annotations = []; this.bookmarkData = {}; this.assetList = []; this.isEditable = true;
    const savedTab = loadReaderSettings().sidebarTab || "annotations";
    this.activeTab = savedTab === "info" ? "details" : savedTab;
    this.apiBase = "/api/";
    this.assetsBase = "/assets";
    this.bookmarksIndexUrl = "/bookmarks";
    this._allTags = []; this._colorPickerId = null; this._colorPickerLeaveTimer = null; this._confirmDelAnnId = null;
    this._renameAssetId = null; this._renameValue = ""; this._renameOriginal = "";
    this._buttonMode = loadReaderSettings().buttonMode || "float";
    this._activeAnnId = null;
    this._tagsEditing = false;
  }

  updated(changed) {
    if (changed.has("activeTab")) saveReaderSettings({ sidebarTab: this.activeTab });
    // textarea 溢出检测
    this.updateComplete.then(() => this._updateTextareaOverflow());
  }

  _updateTextareaOverflow() {
    this.querySelectorAll(".info-textarea").forEach(el => {
      // 兼容不支持 field-sizing:content 的浏览器
      el.style.height = "auto";
      const maxHeight = parseFloat(getComputedStyle(el).maxHeight);
      const hasLimit = !isNaN(maxHeight) && maxHeight > 0;
      el.style.height = hasLimit ? Math.min(el.scrollHeight, maxHeight) + "px" : el.scrollHeight + "px";
      el.classList.toggle("overflows", hasLimit && el.scrollHeight > maxHeight);
    });
  }

  connectedCallback() {
    super.connectedCallback();
    this._out = (e) => {
      if (this._colorPickerId && !e.target.closest(".annotation-color-wrap")) this._colorPickerId = null;
      if (this._confirmDelAnnId && !e.target.closest(".ld-confirm-popup-inline") && !e.target.closest(".annotation-action-delete")) this._confirmDelAnnId = null;
      if (this._activeAnnId && !e.target.closest(".annotation-item")) this._activeAnnId = null;
    };
    document.addEventListener("mousedown", this._out);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    document.removeEventListener("mousedown", this._out);
  }

  async _patchBookmark(f, v) {
    const bm = this.bookmarkData || {}; if (!bm.id) return;
    try {
      const r = await fetch(joinPath(this.apiBase, `bookmarks/${bm.id}/`), {
        method: "PATCH", headers: { "Content-Type": "application/json", "X-CSRFToken": getCSRFToken() },
        body: JSON.stringify({ [f]: v }),
      });
      if (r.ok) { const u = await r.json(); this.bookmarkData = { ...this.bookmarkData, ...u }; this.open = true; document.dispatchEvent(new CustomEvent("bookmark-updated", { detail: u })); }
    } catch {}
  }

  async _patchAsset(assetId, body, reload = true) {
    const bm = this.bookmarkData || {}; if (!bm.id) return false;
    try { const r = await fetch(joinPath(this.apiBase, `bookmarks/${bm.id}/assets/${assetId}/`), { method: "PATCH", headers: { "Content-Type": "application/json", "X-CSRFToken": getCSRFToken() }, body: JSON.stringify(body) }); if (r.ok) { if (reload) this._reloadAssets(); return true; } } catch {} return false;
  }

  async _deleteAsset(assetId) {
    const bm = this.bookmarkData || {};
    if (!bm.id) return false;
    try {
      const r = await fetch(
        joinPath(this.apiBase, `bookmarks/${bm.id}/assets/${assetId}/`),
        { method: "DELETE", headers: { "X-CSRFToken": getCSRFToken() } }
      );
      if (!r.ok) {
        console.error("Failed to delete asset:", r.status, r.statusText);
        return false;
      }
      this._reloadAssets();
      return true;
    } catch (err) {
      console.error("Failed to delete asset:", err);
      return false;
    }
  }

  async _trashBookmark() {
    const bm = this.bookmarkData || {}; if (!bm.id) return;
    try {
      const r = await fetch(joinPath(this.apiBase, `bookmarks/${bm.id}/trash/`), {
        method: "POST",
        headers: { "X-CSRFToken": getCSRFToken() },
      });
      if (r.ok) {
        // 重新获取书签数据以获取 date_deleted
        const d = await fetch(joinPath(this.apiBase, `bookmarks/${bm.id}/`), { headers: { "X-CSRFToken": getCSRFToken() } });
        if (d.ok) {
          const u = await d.json();
          this.bookmarkData = { ...this.bookmarkData, ...u };
        } else {
          this.bookmarkData = { ...this.bookmarkData, is_deleted: true };
        }
        this.open = true;
      }
    } catch {}
  }

  async _restoreBookmark() {
    const bm = this.bookmarkData || {}; if (!bm.id) return;
    try {
      const r = await fetch(joinPath(this.apiBase, `bookmarks/${bm.id}/restore/`), {
        method: "POST",
        headers: { "X-CSRFToken": getCSRFToken() },
      });
      if (r.ok) {
        this.open = true;
        this.bookmarkData = { ...this.bookmarkData, is_deleted: false };
      }
    } catch {}
  }

  async _permanentlyDeleteBookmark() {
    const bm = this.bookmarkData || {}; if (!bm.id) return;
    try {
      const r = await fetch(joinPath(this.apiBase, `bookmarks/${bm.id}/`), {
        method: "DELETE",
        headers: { "X-CSRFToken": getCSRFToken() },
      });
      if (r.ok) {
        // 彻底删除后跳转回书签列表
        window.location.href = this.bookmarksIndexUrl || "/bookmarks";
      }
    } catch {}
  }

  /** 静默 PATCH：发送请求 + 更新按钮 icon，不触发 sidebar 重渲染 */
  async _silentPatch(field, value, btn) {
    const bm = this.bookmarkData || {};
    if (!bm.id) return;
    try {
      const r = await fetch(joinPath(this.apiBase, `bookmarks/${bm.id}/`), {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCSRFToken() },
        body: JSON.stringify({ [field]: value }),
      });
      if (r.ok) {
        const u = await r.json();
        this.bookmarkData = { ...this.bookmarkData, ...u };
        // 只更新被点击按钮的 icon
        if (btn) {
          const icon = this._getStateIcon(field, value);
          if (icon) btn.innerHTML = icon;
        }
      }
    } catch {}
  }

  /** 根据字段和值返回对应的 icon SVG */
  _getStateIcon(field, value) {
    const icons = {
      "is_archived": { true: `<svg width="16" height="16"><use href="#ld-icon-archive-slash"></use></svg>`, false: `<svg width="16" height="16"><use href="#ld-icon-archive"></use></svg>` },
      "shared": { true: `<svg width="16" height="16"><use href="#ld-icon-share"></use></svg>`, false: `<svg width="16" height="16"><use href="#ld-icon-share-x"></use></svg>` },
      "unread": { true: `<svg width="16" height="16"><use href="#ld-icon-unread-x"></use></svg>`, false: `<svg width="16" height="16"><use href="#ld-icon-read-check"></use></svg>` },
    };
    return icons[field]?.[String(value)] || null;
  }

  _reloadAssets() { this.dispatchEvent(new CustomEvent("reload-assets", { bubbles: true, composed: true, detail: { bookmarkId: this.bookmarkData?.id } })); }
  _emitAnn(id, action, extra = {}) { this.dispatchEvent(new CustomEvent("annotation-action", { bubbles: true, composed: true, detail: { id, action, ...extra } })); }
  _handleAnnCopy(annId) { this._emitAnn(annId, "copy"); showToast(gettext("Copied"), { tone: "success", duration: 1500 }); }
  /** 显示确认弹窗（追加到 body，fixed 定位） */
  _showPopup(btn, onConfirm) {
    document.querySelectorAll(".reader-confirm-popup").forEach(el => el.remove());

    const question = btn.getAttribute("ld-confirm-question") || gettext("Are you sure?");
    const isDanger = btn.hasAttribute("ld-confirm-danger");

    const popup = document.createElement("div");
    popup.className = "reader-confirm-popup";
    popup.innerHTML = `<span class="confirm-popup-question">${question}</span><span class="confirm-popup-actions"><button type="button" class="btn btn-sm">${gettext("Cancel")}</button><button type="button" class="btn btn-sm ${isDanger ? "btn-error" : "btn-primary"}">${gettext("Confirm")}</button></span>`;

    popup.style.cssText = "position:fixed;visibility:hidden;";
    document.body.appendChild(popup);

    const rect = btn.getBoundingClientRect();
    const popupW = popup.offsetWidth;
    const popupH = popup.offsetHeight;

    let left = rect.left + rect.width / 2 - popupW / 2;
    if (left < 8) left = 8;
    if (left + popupW > window.innerWidth - 8) left = window.innerWidth - 8 - popupW;

    let top = rect.top - popupH - 6;
    if (top < 8) top = rect.bottom + 6;

    popup.style.cssText = `position:fixed;top:${top}px;left:${left}px;z-index:9999;`;

    const [cancelBtn, confirmBtn] = popup.querySelectorAll(".btn");
    cancelBtn.addEventListener("click", () => popup.remove());
    confirmBtn.addEventListener("click", () => {
      popup.remove();
      onConfirm();
    });

    const onOutside = (e) => {
      if (!popup.contains(e.target)) { popup.remove(); document.removeEventListener("mousedown", onOutside); }
    };
    setTimeout(() => document.addEventListener("mousedown", onOutside), 0);
  }
  _setButtonMode(mode) { this._buttonMode = mode; saveReaderSettings({ buttonMode: mode }); }
  _handleAnnColor(annId, color) { this._colorPickerId = null; this._emitAnn(annId, "change-color", { color }); }

  // ---- Edit helpers ----

  _saveField(field, value) {
    const v = value.trim();
    if (v !== (this.bookmarkData[field] || "")) {
      this.bookmarkData = { ...this.bookmarkData, [field]: v };
      this._patchBookmark(field, v);
    }
  }

  _saveAnnotationNote(annId, value) {
    this._emitAnn(annId, "edit-note", { note: value.trim() });
  }

  _escapeBlur(e) { if (e.key === "Escape") { e.preventDefault(); e.target.blur(); } }

  // --- Tags ---

  /** 点击标签显示态 → 设置编辑状态，由渲染系统处理 */
  _clickTags() {
    if (this._tagsEditing) return;
    this._tagsEditing = true;

    // 等渲染完成后聚焦并绑定事件
    this.updateComplete.then(() => {
      const autocomplete = this.querySelector(".info-tags-wrapper ld-tag-autocomplete");
      if (!autocomplete || !this._tagsEditing) return;

      const onReady = () => {
        const input = autocomplete.querySelector("input");
        if (!input || !this._tagsEditing) return;

        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);

        input.addEventListener("blur", () => {
          setTimeout(() => {
            if (autocomplete.contains(document.activeElement)) return;
            this._finishEditTags(input.value);
          }, 150);
        });

        input.addEventListener("keydown", (e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            // 取消编辑，恢复原值
            this._tagsEditing = false;
          }
        });

        // 监听 commit 事件（回车键保存）
        autocomplete.addEventListener("commit", () => {
          this._finishEditTags(input.value);
        });
      };

      if (autocomplete.updateComplete) {
        autocomplete.updateComplete.then(onReady);
      } else {
        requestAnimationFrame(onReady);
      }
    });
  }

  _finishEditTags(inputValue) {
    if (!this._tagsEditing) return;

    const newTags = (inputValue || "").split(/\s+/).map(s => s.trim()).filter(Boolean);
    const oldTags = this.bookmarkData?.tag_names || [];
    if (JSON.stringify(newTags) !== JSON.stringify(oldTags)) {
      this._patchBookmark("tag_names", newTags);
    }

    this._tagsEditing = false;
  }

  // --- File list ---

  _startRename(assetId, currentName) { this._renameAssetId = String(assetId); this._renameValue = currentName; this._renameOriginal = currentName; this.updateComplete.then(() => { const el = this.querySelector(".info-file-rename-input"); if (el) { el.focus(); el.select(); } }); }
  _saveRename(assetId) { const n = this._renameValue.trim(); if (n && n !== this._renameOriginal) { this._patchAsset(assetId, { display_name: n }, false); this.assetList = (this.assetList || []).map(a => String(a.id) === String(assetId) ? { ...a, display_name: n } : a); } this._renameAssetId = null; this._renameValue = ""; this._renameOriginal = ""; }

  _fmtDate(iso) { if (!iso) return ""; try { const d = new Date(iso); return `${d.getFullYear()}/${String(d.getMonth()+1).padStart(2,"0")}/${String(d.getDate()).padStart(2,"0")}`; } catch { return ""; } }
  _fmtDT(iso) { if (!iso) return ""; try { const d = new Date(iso); return `${d.getFullYear()}/${String(d.getMonth()+1).padStart(2,"0")}/${String(d.getDate()).padStart(2,"0")} ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}:${String(d.getSeconds()).padStart(2,"0")}`; } catch { return ""; } }
  _fmtSize(b) { if (!b) return ""; if (b > 1048576) return (b/1048576).toFixed(1)+" MB"; if (b > 1024) return (b/1024).toFixed(0)+" KB"; return b+" B"; }

  // ---- Render ----

  _renderAnnotationItem(ann) {
    const unresolved = !!ann._unresolved;
    const colorBg = HIGHLIGHT_COLORS[ann.color]?.bg || HIGHLIGHT_COLORS.yellow.bg;
    const colorSolid = colorBg.includes("color-mix(")
      ? colorBg
      : colorBg.replace(/[\d.]+\)$/, "0.8)");
    return html`
      <div class="annotation-item ${unresolved ? "annotation-item--unresolved" : ""}" data-id="${ann.id}"
        data-button-mode="${this._buttonMode}" data-active="${this._activeAnnId === String(ann.id) ? "true" : "false"}">
        <div class="annotation-text-row">
          <div class="annotation-color-bar" style="background-color: ${colorBg}"></div>
          <div class="annotation-text" @click=${() => { if (!unresolved) this._emitAnn(ann.id, "jump"); }}>${ann.selected_text}</div>
        </div>
        <textarea class="annotation-textarea" .value=${ann.note_content || ""}
          placeholder=${gettext("Click to add note")} rows="1"
          @focus=${() => { this._activeAnnId = String(ann.id); }}
          @blur=${(e) => this._saveAnnotationNote(ann.id, e.target.value)}
          @keydown=${this._escapeBlur}></textarea>
        <div class="annotation-actions">
          <span class="annotation-delete-wrap">
            <button class="annotation-action-btn annotation-action-delete" title=${gettext("Delete")}
              @click=${(e) => { e.stopPropagation(); this._confirmDelAnnId = String(ann.id); }}
              .innerHTML=${READER_ICONS["delete"]}></button>
            ${this._confirmDelAnnId === String(ann.id) ? html`
              <span class="ld-confirm-popup-inline">
                <span class="confirm-popup-question">${gettext("Are you sure?")}</span>
                <span class="confirm-popup-actions">
                  <button class="btn btn-sm" @click=${() => this._confirmDelAnnId = null}>${gettext("Cancel")}</button>
                  <button class="btn btn-sm btn-error" @click=${() => { this._confirmDelAnnId = null; this._emitAnn(ann.id, "delete"); }}>${gettext("Delete")}</button>
                </span>
              </span>
            ` : html``}
          </span>
          <span class="annotation-spacer"></span>
          <span class="annotation-color-wrap"
            @mouseenter=${() => { clearTimeout(this._colorPickerLeaveTimer); this._colorPickerId = String(ann.id); }}
            @mouseleave=${() => { this._colorPickerLeaveTimer = setTimeout(() => { this._colorPickerId = null; }, 150); }}>
            <span class="annotation-color-dot" style="background-color: ${colorSolid}" title=${gettext("Change color")}></span>
            ${this._colorPickerId === String(ann.id) ? html`
              <span class="color-picker-popup" @mousedown=${(e) => e.stopPropagation()}>
                ${Object.entries(HIGHLIGHT_COLORS).map(([name, cfg]) => html`
                  <span class="color-picker-option ${name === ann.color ? "selected" : ""}"
                    style="background-color: ${cfg.bg.includes("color-mix(") ? cfg.bg : cfg.bg.replace(/[\d.]+\)$/, "0.8)")}"
                    @click=${(e) => { e.stopPropagation(); this._handleAnnColor(ann.id, name); }}></span>
                `)}
              </span>
            ` : html``}
          </span>
          <button class="annotation-action-btn" title=${gettext("Copy")} @click=${() => this._handleAnnCopy(ann.id)} .innerHTML=${READER_ICONS["copy"]}></button>
          
        </div>
      </div>
    `;
  }

  _renderAnnotationsTab() {
    if (!this.annotations?.length) return html`<div class="sidebar-empty"><span class="sidebar-empty-icon" .innerHTML=${READER_ICONS["empty"]}></span><p>${gettext("No highlights yet")}</p><p class="sidebar-empty-hint">${gettext("Select text in the article to add highlights")}</p></div>`;
    const located = this.annotations.filter(a => !a._unresolved);
    const unresolved = this.annotations.filter(a => a._unresolved);
    const nc = this.annotations.filter(a => a.note_content).length;
    const highlightsText = interpolate(
      ngettext("%(count)s highlight", "%(count)s highlights", this.annotations.length),
      { count: this.annotations.length }
    );
    const notesText = nc > 0
      ? ` · ${interpolate(ngettext("%(count)s note", "%(count)s notes", nc), { count: nc })}`
      : "";
    return html`
      <div class="sidebar-section-header">
        <span>${highlightsText}${notesText}</span>
        <button class="button-mode-toggle ${this._buttonMode === "always" ? "active" : ""}"
          title=${this._buttonMode === "always" ? gettext("Hide action buttons") : gettext("Show action buttons")}
          @click=${() => this._setButtonMode(this._buttonMode === "always" ? "float" : "always")}
          .innerHTML=${this._buttonMode === "always" ? READER_ICONS["eye"] : READER_ICONS["eye-off"]}></button>
      </div>
      <div class="annotation-list">
        ${repeat(located, a => a.id, a => this._renderAnnotationItem(a))}
        ${unresolved.length ? html`
          <div class="annotation-unresolved-divider">${gettext("The following highlights and notes could not be located")}</div>
          ${repeat(unresolved, a => a.id, a => this._renderAnnotationItem(a))}
        ` : html``}
      </div>
    `;
  }

  _renderInfoTab() {
    const bm = this.bookmarkData || {};
    const files = this.assetList || [];
    const editable = this.isEditable !== false;

    if (!editable) {
      return this._renderReadOnlyInfoTab(bm);
    }

    return html`<div class="sidebar-bookmark-info">
      ${bm.preview_image_url ? html`<div class="info-section"><img class="info-preview-image" src="${bm.preview_image_url}" alt=${gettext("Preview")} /></div>` : html``}

      <div class="info-section">
        <textarea class="info-input info-title-input" rows="1"
          placeholder=${gettext("Click to edit title")}
          @blur=${(e) => { e.target.scrollTop = 0; this._saveField("title", e.target.value); }}
          @keydown=${this._escapeBlur}>${bm.title || ""}</textarea>
      </div>

      <div class="info-section">
        <a class="info-url" href="${bm.url || "#"}" target="_blank" rel="noopener">
          ${bm.favicon_url ? html`<img class="info-favicon" src="${bm.favicon_url}" width="16" height="16" />` : html``}
          <span class="info-url-text">${bm.url || "—"}</span>
        </a>
      </div>

      <div class="info-section info-dates">
        ${bm.date_added ? html`<span class="info-date" title="${gettext("Added")} ${this._fmtDT(bm.date_added)}"><span class="info-date-icon" .innerHTML=${READER_ICONS["clock-added"]}></span>${this._fmtDate(bm.date_added)}</span>` : html``}
        ${bm.date_modified ? html`<span class="info-date" title="${gettext("Modified")} ${this._fmtDT(bm.date_modified)}"><span class="info-date-icon" .innerHTML=${READER_ICONS["clock-modified"]}></span>${this._fmtDate(bm.date_modified)}</span>` : html``}
        ${bm.is_deleted && bm.date_deleted ? html`<span class="info-date" title="${gettext("Deleted")} ${this._fmtDT(bm.date_deleted)}"><span class="info-date-icon" .innerHTML=${READER_ICONS["clock-deleted"]}></span>${this._fmtDate(bm.date_deleted)}</span>` : html``}
      </div>

      <div class="info-section">
        <div class="info-tags-wrapper ${this._tagsEditing ? 'ld-editing' : ''}">
          ${this._tagsEditing
            ? html`<ld-tag-autocomplete
                input-value="${(bm.tag_names || []).join(' ')}"
                input-placeholder="${gettext('Click to edit tags')}"
              ></ld-tag-autocomplete>`
            : html`<div class="info-tags-view" @click=${() => this._clickTags()}>
                ${bm.tag_names?.length ? html`<span class="info-tags">${bm.tag_names.map(t => html`<span class="info-tag">${t}</span>`)}</span>` : html`<span class="info-placeholder">${gettext("Click to edit tags")}</span>`}
              </div>`
          }
        </div>
      </div>

      <div class="info-section">
        <div class="info-label">${gettext("Author")}</div>
        <textarea class="info-textarea" .value=${bm.author || ""}
          placeholder=${gettext("Click to edit author")} rows="1"
          @input=${(e) => { e.target.style.height = "auto"; e.target.style.height = e.target.scrollHeight + "px"; e.target.classList.toggle("overflows", e.target.scrollHeight > e.target.clientHeight); }}
          @blur=${(e) => this._saveField("author", e.target.value)}
          @keydown=${this._escapeBlur}></textarea>
      </div>

      <div class="info-section">
        <div class="info-label">${gettext("Description")}</div>
        <textarea class="info-textarea" .value=${bm.description || ""}
          placeholder=${gettext("Click to edit description")} rows="1"
          @input=${(e) => { e.target.style.height = "auto"; e.target.style.height = e.target.scrollHeight + "px"; e.target.classList.toggle("overflows", e.target.scrollHeight > e.target.clientHeight); }}
          @blur=${(e) => this._saveField("description", e.target.value)}
          @keydown=${this._escapeBlur}></textarea>
      </div>

      <div class="info-section">
        <div class="info-label">${gettext("Notes")}</div>
        <textarea class="info-textarea" .value=${bm.notes || ""}
          placeholder=${gettext("Click to edit notes")} rows="1"
          @input=${(e) => { e.target.style.height = "auto"; e.target.style.height = e.target.scrollHeight + "px"; e.target.classList.toggle("overflows", e.target.scrollHeight > e.target.clientHeight); }}
          @blur=${(e) => this._saveField("notes", e.target.value)}
          @keydown=${this._escapeBlur}></textarea>
      </div>

      <div class="info-section">
        <div class="info-label">${gettext("Files")}</div>
        ${files.length ? html`<div class="info-files">${files.map(a => {
          const isRen = this._renameAssetId === String(a.id);
          const fileUrl = a.file ? joinPath(this.assetsBase, `${a.id}`) : null;
          const name = a.display_name || gettext("File");
          const isArticle = String(a.id) === String(bm.latest_article);
          return html`<div class="info-file-item">
            ${isRen ? html`<input class="info-input info-file-rename-input" type="text" .value=${this._renameValue} @input=${e => this._renameValue = e.target.value} @keydown=${e => { if (e.key === "Enter") this._saveRename(a.id); if (e.key === "Escape") { this._renameAssetId = null; this._renameValue = ""; } }} @blur=${() => this._saveRename(a.id)} />`
            : html`<span class="info-file-name" title="${name}" @click=${() => { if (fileUrl) window.open(fileUrl, "_blank"); }}>
              <span class="truncate">${fileUrl ? html`<a href="${fileUrl}" target="_blank" data-turbo="false" class="info-file-link" @click=${(e) => e.stopPropagation()}>${name}</a>` : html`<span>${name}</span>`}${a.status === "pending" ? html`<span class="info-file-status"> (${gettext("queued")})</span>` : html``}${a.status === "failure" ? html`<span class="info-file-status info-file-failed"> (${gettext("failed")})</span>` : html``}</span>
              ${a.file_size ? html`<span class="info-file-size">${this._fmtSize(a.file_size)}</span>` : html``}
            </span>`}
            <div class="info-file-actions">
              <button class="annotation-action-btn" title=${gettext("Rename")} @click=${() => this._startRename(a.id, name)} .innerHTML=${READER_ICONS["rename"]}></button>
              <span class="info-file-delete-wrap">
                <button class="annotation-action-btn annotation-action-delete ${isArticle ? "disabled" : ""}"
                  ld-confirm-question="${gettext("Remove this file?")}"
                  ld-confirm-danger
                  title="${isArticle ? gettext("Cannot delete article asset") : gettext("Remove")}"
                  ?disabled=${isArticle}
                  @click=${(e) => { if (!isArticle) this._showPopup(e.currentTarget, () => this._deleteAsset(a.id)); }}
                  .innerHTML=${READER_ICONS["delete"]}></button>
              </span>
            </div>
          </div>`;
        })}</div>` : html`<div class="info-placeholder">${gettext("No files")}</div>`}
      </div>

      <div class="info-bottom-actions">
        <div class="info-bottom-left">
          <span class="info-action-wrap">
            <button class="info-action-btn"
              ld-confirm-question="${bm.is_archived ? gettext("Unarchive?") : gettext("Archive?")}"
              title=${bm.is_archived ? gettext("Unarchive") : gettext("Archive")}
              @click=${(e) => this._showPopup(e.currentTarget, () => this._silentPatch("is_archived", !bm.is_archived, e.currentTarget))}
              .innerHTML=${bm.is_archived ? READER_ICONS["archive-slash"] : READER_ICONS["archive"]}></button>
          </span>
          <span class="info-action-wrap">
            <button class="info-action-btn"
              ld-confirm-question="${bm.shared ? gettext("Unshare?") : gettext("Share?")}"
              title=${bm.shared ? gettext("Unshare") : gettext("Share")}
              @click=${(e) => this._showPopup(e.currentTarget, () => this._silentPatch("shared", !bm.shared, e.currentTarget))}
              .innerHTML=${bm.shared ? READER_ICONS["share"] : READER_ICONS["share-x"]}></button>
          </span>
          <span class="info-action-wrap">
            <button class="info-action-btn" title=${bm.unread ? gettext("Mark as read") : gettext("Mark as unread")}
              @click=${(e) => this._silentPatch("unread", !bm.unread, e.currentTarget)}
              .innerHTML=${bm.unread ? READER_ICONS["unread-x"] : READER_ICONS["read-check"]}></button>
          </span>
        </div>
        <div class="info-bottom-right">
          ${bm.is_deleted ? html`
            <span class="info-action-wrap">
              <button class="info-action-btn"
                ld-confirm-question="${gettext("Restore bookmark?")}"
                title=${gettext("Restore")}
                @click=${(e) => this._showPopup(e.currentTarget, () => this._restoreBookmark())}
                .innerHTML=${READER_ICONS["restore"]}></button>
            </span>
            <span class="info-action-wrap">
              <button class="info-action-btn info-action-delete"
                ld-confirm-question="${gettext("Permanently delete? This cannot be undone.")}"
                ld-confirm-danger
                title=${gettext("Permanently delete")}
                @click=${(e) => this._showPopup(e.currentTarget, () => this._permanentlyDeleteBookmark())}
                .innerHTML=${READER_ICONS["delete"]}></button>
            </span>
          ` : html`
            <span class="info-action-wrap">
              <button class="info-action-btn info-action-delete"
                ld-confirm-question="${gettext("Move to trash?")}"
                ld-confirm-danger
                title=${gettext("Move to trash")}
                @click=${(e) => this._showPopup(e.currentTarget, () => this._trashBookmark())}
                .innerHTML=${READER_ICONS["delete"]}></button>
            </span>
          `}
        </div>
      </div>
    </div>`;
  }

  _renderReadOnlyInfoTab(bm) {
    return html`<div class="sidebar-bookmark-info">
      ${bm.preview_image_url ? html`<div class="info-section"><img class="info-preview-image" src="${bm.preview_image_url}" alt=${gettext("Preview")} /></div>` : html``}

      <div class="info-section">
        <div class="info-title-display">${bm.title || ""}</div>
      </div>

      <div class="info-section">
        <a class="info-url" href="${bm.url || "#"}" target="_blank" rel="noopener">
          ${bm.favicon_url ? html`<img class="info-favicon" src="${bm.favicon_url}" width="16" height="16" />` : html``}
          <span class="info-url-text">${bm.url || "—"}</span>
        </a>
      </div>

      <div class="info-section info-dates">
        ${bm.date_added ? html`<span class="info-date" title="${gettext("Added")} ${this._fmtDT(bm.date_added)}"><span class="info-date-icon" .innerHTML=${READER_ICONS["clock-added"]}></span>${this._fmtDate(bm.date_added)}</span>` : html``}
        ${bm.date_modified ? html`<span class="info-date" title="${gettext("Modified")} ${this._fmtDT(bm.date_modified)}"><span class="info-date-icon" .innerHTML=${READER_ICONS["clock-modified"]}></span>${this._fmtDate(bm.date_modified)}</span>` : html``}
        ${bm.is_deleted && bm.date_deleted ? html`<span class="info-date" title="${gettext("Deleted")} ${this._fmtDT(bm.date_deleted)}"><span class="info-date-icon" .innerHTML=${READER_ICONS["clock-deleted"]}></span>${this._fmtDate(bm.date_deleted)}</span>` : html``}
      </div>

      <div class="info-section">
        ${bm.tag_names?.length ? html`<span class="info-tags">${bm.tag_names.map(t => html`<span class="info-tag">${t}</span>`)}</span>` : html`<span class="info-placeholder">${gettext("No tags")}</span>`}
      </div>

      ${bm.author ? html`
      <div class="info-section">
        <div class="info-label">${gettext("Author")}</div>
        <div class="info-value-readonly">${bm.author}</div>
      </div>` : html``}

      ${bm.description ? html`
      <div class="info-section">
        <div class="info-label">${gettext("Description")}</div>
        <div class="info-value-readonly">${bm.description}</div>
      </div>` : html``}

      ${bm.notes ? html`
      <div class="info-section">
        <div class="info-label">${gettext("Notes")}</div>
        <div class="info-value-readonly">${bm.notes}</div>
      </div>` : html``}
    </div>`;
  }

  async _addToMyBookmarks() {
    const bm = this.bookmarkData || {};
    if (!bm.url) return;

    // If user already has this bookmark, just navigate to it
    if (bm.existing_bookmark_id) {
      window.location.href = `/bookmarks/${bm.existing_bookmark_id}/read`;
      return;
    }

    // Save scroll position before navigating
    const contentArea = document.getElementById("reader-content");
    const scrollTop = contentArea ? contentArea.scrollTop : 0;

    try {
      const r = await fetch(joinPath(this.apiBase, "bookmarks/"), {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCSRFToken() },
        body: JSON.stringify({ url: bm.url }),
      });
      if (!r.ok) throw new Error("API error");
      const data = await r.json();

      // Store scroll position for restoration on the new page
      try {
        localStorage.setItem("ld:reader:pending-scroll", JSON.stringify({
          bookmarkId: data.id,
          scrollTop: scrollTop,
        }));
      } catch {}

      window.location.href = `/bookmarks/${data.id}/read`;
    } catch {
      if (btn) { btn.disabled = false; btn.textContent = gettext("Failed, please try again"); }
    }
  }

  render() {
    return html`
      <div id="reader-sidebar" data-open=${this.open ? "true" : "false"}>
        <div id="sidebar-tabs">
          <button data-tab="annotations" data-active=${this.activeTab === "annotations" ? "true" : "false"} @click=${() => (this.activeTab = "annotations")}>
            <span class="tab-icon" .innerHTML=${READER_ICONS["tab-annotations"]}></span>${gettext("Highlights")}
          </button>
          <button data-tab="details" data-active=${this.activeTab === "details" ? "true" : "false"} @click=${() => (this.activeTab = "details")}>
            <span class="tab-icon" .innerHTML=${READER_ICONS["tab-details"]}></span>${gettext("Details")}
          </button>
        </div>
        <div id="sidebar-annotations" class="sidebar-tab-content" data-active=${this.activeTab === "annotations" ? "true" : "false"}>${this._renderAnnotationsTab()}</div>
        <div id="sidebar-bookmark-info" class="sidebar-tab-content" data-active=${this.activeTab === "details" ? "true" : "false"}>${this._renderInfoTab()}</div>
      </div>
    `;
  }
}

customElements.define("reader-sidebar", ReaderSidebar);
