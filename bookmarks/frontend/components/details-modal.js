import { setAfterPageLoadFocusTarget } from "../utils/focus.js";
import { handleBookmarkAction } from "../utils/bookmark-action.js";
import { getCSRFToken } from "../utils/csrf.js";
import { Modal } from "./modal.js";

function gettext(s) {
  return window.gettext ? window.gettext(s) : s;
}

class DetailsModal extends Modal {
  init() {
    super.init();

    this.bookmarkId = this.dataset.bookmarkId;
    this.apiBase = "/api/";
    this._actionForm = this.querySelector(".modal-footer");
    this._data = {
      url: this.dataset.bookmarkUrl || "",
      title: this.dataset.bookmarkTitle || "",
      description: this.dataset.bookmarkDescription || "",
      notes: this.dataset.bookmarkNotes || "",
      preview_image_remote_url: this.dataset.bookmarkPreviewImageRemoteUrl || "",
      tag_names: (this.dataset.bookmarkTagNames || "")
        .split(" ")
        .filter(Boolean),
    };
    this._pendingMetadata = null;
    this._urlEditing = false;
    this._refreshShouldRetryPreview = false;

    // 不自动聚焦
    requestAnimationFrame(() => {
      if (document.activeElement && this.contains(document.activeElement)) {
        document.activeElement.blur();
      }
    });

    // ---- 标题（内联编辑） ----
    const titleInput = this.querySelector(".bookmark-title-input");
    if (titleInput) {
      titleInput.addEventListener("input", () => this._autoResize(titleInput));
      titleInput.addEventListener("blur", (e) => {
        e.target.scrollTop = 0;
        if (this._urlEditing && this._pendingMetadata) return;
        this._patchBookmark("title", e.target.value);
      });
    }

    // ---- 描述/备注 textarea（内联编辑） ----
    this.querySelectorAll(".detail-textarea").forEach((el) => {
      el.addEventListener("input", () => this._autoResize(el));
      el.addEventListener("blur", (e) => {
        const field = e.target.dataset.field;
        if (this._urlEditing && this._pendingMetadata) return;
        if (field) this._patchBookmark(field, e.target.value);
      });
      el.addEventListener("keydown", (e) => {
        if (e.key === "Escape") { e.preventDefault(); e.target.blur(); }
      });
    });

    // 所有 textarea 的初始高度计算统一到下一帧，批量读写只触发 1 次 reflow
    requestAnimationFrame(() => {
      const allTextareas = [titleInput, ...this.querySelectorAll(".detail-textarea")].filter(Boolean);
      this._batchAutoResize(allTextareas);
    });

    // Turbo morph 会就地更新 textarea 内容，但不会重新触发 connectedCallback/init，
    // 导致 textarea 的 style.height 保持旧值。监听 morph 事件，在完成后重新计算高度。
    this._onMorph = (event) => {
      if (event.target !== this) return;
      // morph 尚未执行，等两帧确保 DOM 已更新且布局已完成
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const titleInput2 = this.querySelector(".bookmark-title-input");
          const allTextareas = [titleInput2, ...this.querySelectorAll(".detail-textarea")].filter(Boolean);
          this._batchAutoResize(allTextareas);
        });
      });
    };
    document.addEventListener("turbo:before-morph-element", this._onMorph);

    // ---- 状态 chips（API 局部刷新） ----
    this.querySelectorAll(".detail-status-chip[data-chip-field]").forEach((btn) => {
      btn.addEventListener("click", () => this._toggleChip(btn));
    });

    // ---- 清空按钮 ----
    this.querySelectorAll(".detail-clear-btn").forEach((btn) => {
      btn.addEventListener("mousedown", (e) => {
        e.preventDefault();
        const field = btn.dataset.clearField;
        const textarea = this.querySelector(`.detail-textarea[data-field="${field}"]`);
        if (textarea) {
          textarea.value = "";
          this._autoResize(textarea);
          textarea.focus();
          this._patchBookmark(field, "");
        }
      });
    });

    // ---- 标签（点击显示态 → 动态创建 ld-tag-autocomplete） ----
    this.querySelector(".detail-tags-view")?.addEventListener("click", () =>
      this._startEditTags(),
    );

    // ---- URL 内联编辑 ----
    this.querySelector("#edit-url-btn")?.addEventListener("click", () =>
      this._startEditUrl(),
    );
    this.querySelector("#url-save-btn")?.addEventListener("click", () =>
      this._saveUrl(),
    );
    this.querySelector("#url-cancel-btn")?.addEventListener("click", () =>
      this._cancelEditUrl(),
    );
    // Save/Refresh 按钮阻止 input 失焦
    this.querySelector("#url-save-btn")?.addEventListener("mousedown", (e) => e.preventDefault());
    this.querySelector("#refresh-metadata-btn")?.addEventListener("mousedown", (e) => e.preventDefault());
    this.querySelector("#refresh-metadata-btn")?.addEventListener("click", () =>
      this._refreshMetadata(),
    );

    // ---- 文件操作 ----
    this.addEventListener("click", (e) => {
      const target = e.target.closest("[data-action]");
      if (!target || !this.contains(target)) return;
      const action = target.dataset.action;

      if (action === "rename-asset") { this._startRenameAsset(target.dataset.assetId); return; }
      if (action === "remove-asset") {
        this._addFormInput("remove_asset", target.dataset.assetId);
        this._actionForm?.requestSubmit();
        return;
      }
      if (action === "create-snapshot") {
        this._addFormInput("create_html_snapshot", this.bookmarkId);
        this._actionForm?.requestSubmit();
        return;
      }
      if (action === "upload-asset") {
        this.querySelector("[data-upload-file-input]")?.click();
        return;
      }
    });

    this.querySelector("[data-upload-file-input]")?.addEventListener("change", (e) => {
      if (e.target.files[0]) {
        this._addFormInput("upload_asset", this.bookmarkId);
        this._actionForm?.requestSubmit();
      }
    });

    // ---- trash / restore 按钮（API 局部处理） ----
    this.querySelectorAll("button[data-action]").forEach((btn) => {
      const action = btn.dataset.action;
      if (action === "trash" || action === "restore") {
        btn._onConfirm = () => this._executeAction(btn.dataset.action);
      }
    });
  }

  // ---- textarea 自动高度 ----

  _autoResize(el) {
    const maxHeight = parseFloat(getComputedStyle(el).maxHeight);
    const hasLimit = !isNaN(maxHeight) && maxHeight > 0;
    el.style.height = "auto";
    const naturalHeight = el.scrollHeight;
    el.style.overflowY = hasLimit && naturalHeight > maxHeight ? "auto" : "hidden";
    el.style.height = hasLimit ? Math.min(naturalHeight, maxHeight) + "px" : naturalHeight + "px";
  }

  /**
   * 批量调整多个 textarea 高度：先统一读，再统一写，只触发 1 次 reflow。
   */
  _batchAutoResize(els) {
    // Phase 1: 所有元素先设为 auto（使布局失效）
    for (const el of els) {
      el.style.height = "auto";
    }
    // Phase 2: 统一读取 scrollHeight（1 次 reflow）
    const measured = els.map((el) => {
      const maxHeight = parseFloat(getComputedStyle(el).maxHeight);
      const hasLimit = !isNaN(maxHeight) && maxHeight > 0;
      return { el, hasLimit, maxHeight, naturalHeight: el.scrollHeight };
    });
    // Phase 3: 统一写入
    for (const { el, hasLimit, maxHeight, naturalHeight } of measured) {
      el.style.overflowY = hasLimit && naturalHeight > maxHeight ? "auto" : "hidden";
      el.style.height = hasLimit ? Math.min(naturalHeight, maxHeight) + "px" : naturalHeight + "px";
    }
  }

  // ---- 状态 chip 切换 ----

  async _toggleChip(btn) {
    const field = btn.dataset.chipField;
    const currentValue = btn.dataset.chipValue === "true";
    const newValue = !currentValue;
    const actionMap = { is_archived: newValue ? "archive" : "unarchive", shared: newValue ? "share" : "unshare", unread: newValue ? "mark_as_unread" : "mark_as_read" };
    const action = actionMap[field];
    if (!action) return;

    handleBookmarkAction({
      bookmarkId: this.bookmarkId,
      action,
      onOptimistic: () => {
        this._applyChipState(btn, field, newValue);
        this._syncListItemField(field, newValue);
      },
      onRollback: () => {
        this._applyChipState(btn, field, currentValue);
        this._syncListItemField(field, currentValue);
      },
    });
  }

  _applyChipState(btn, field, value) {
    btn.dataset.chipValue = String(value);
    const active = field === "unread" ? !value : value;
    btn.classList.toggle("active", active);
    const iconHref = active ? btn.dataset.chipActiveIcon : btn.dataset.chipInactiveIcon;
    const text = active ? btn.dataset.chipActiveText : btn.dataset.chipInactiveText;
    this._replaceUse(btn.querySelector("svg use"), iconHref);
    btn.querySelector("span").textContent = text;
  }

  // 同步列表项（直接操作 DOM，不触发全局刷新）
  _replaceUse(oldUse, href) {
    if (!oldUse) return;
    const newUse = document.createElementNS("http://www.w3.org/2000/svg", "use");
    newUse.setAttribute("href", href);
    oldUse.replaceWith(newUse);
  }

  _syncListItemField(field, value) {
    const item = document.querySelector(`li[data-bookmark-id="${this.bookmarkId}"]`);
    if (!item) return;

    switch (field) {
      case "unread": {
        item.classList.toggle("unread", value);
        const btn = item.querySelector('button[data-action="mark_as_read"], button[data-action="mark_as_unread"]');
        if (btn) {
          this._replaceUse(btn.querySelector("svg.action-icon use"), value ? "#ld-icon-unread-x" : "#ld-icon-read-check");
          btn.dataset.action = value ? "mark_as_read" : "mark_as_unread";
        }
        break;
      }
      case "shared": {
        const btn = item.querySelector('button[data-action="share"], button[data-action="unshare"]');
        if (btn) {
          this._replaceUse(btn.querySelector("svg.action-icon use"), value ? "#ld-icon-share" : "#ld-icon-share-x");
          btn.dataset.action = value ? "unshare" : "share";
          btn.name = value ? "unshare" : "share";
        }
        break;
      }
      case "title": {
        const titleSpan = item.querySelector(".title-link span");
        if (titleSpan) titleSpan.textContent = value || "";
        break;
      }
      case "url": {
        const prevUrl = this._data._prevUrl;
        if (prevUrl) {
          item.querySelectorAll("a[href]").forEach(a => {
            if (a.getAttribute("href") === prevUrl) a.setAttribute("href", value);
          });
        }
        const urlDisplay = item.querySelector(".url-display");
        if (urlDisplay) urlDisplay.textContent = value;
        // 同步弹窗内 URL 显示态
        const modalUrlLink = this.querySelector(".detail-url-link");
        if (modalUrlLink) {
          modalUrlLink.href = value;
          modalUrlLink.textContent = value;
        }
        break;
      }
      case "description": {
        const descText = item.querySelector(".description-text");
        if (descText) descText.textContent = value || "";
        break;
      }
      case "preview_image_remote_url": {
        const previewImg = item.querySelector("img.preview-image");
        if (previewImg) {
          previewImg.src = value || "";
          previewImg.style.display = value ? "" : "none";
        }
        // 同步弹窗内预览图
        const modalPreviewImg = this.querySelector(".info-preview-image");
        if (modalPreviewImg) {
          modalPreviewImg.src = value || "";
          const section = modalPreviewImg.closest(".detail-section");
          if (section) section.style.display = value ? "" : "none";
        }
        break;
      }
      case "tag_names": {
        const tags = Array.isArray(value) ? value : [];
        const tagsContainer = item.querySelector(".tags");
        if (!tagsContainer) break;
        if (tags.length === 0) {
          tagsContainer.remove();
        } else {
          tagsContainer.replaceChildren();
          tags.forEach((tag, i) => {
            if (i > 0) tagsContainer.appendChild(document.createTextNode(" "));
            const link = document.createElement("a");
            link.href = `?q=%23${encodeURIComponent(tag)}`;
            link.textContent = tag;
            tagsContainer.appendChild(link);
          });
        }
        break;
      }
    }
  }

  // ---- trash / restore 操作 ----

  async _executeAction(action) {
    handleBookmarkAction({
      bookmarkId: this.bookmarkId,
      action,
      onOptimistic: () => {
        this.clearInert();
        this.remove();
      },
    });
  }

  // ---- form 辅助 ----

  _addFormInput(name, value) {
    if (!this._actionForm) return;
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    input.dataset.dynamic = "true";
    this._actionForm.appendChild(input);
    setTimeout(() => input.remove(), 200);
  }

  // ---- URL 内联编辑（同容器切换） ----

  _startEditUrl() {
    const wrapper = this.querySelector(".detail-url-wrapper");
    if (!wrapper || wrapper.classList.contains("editing")) return;

    const input = wrapper.querySelector(".detail-url-input");
    if (!input) return;

    wrapper.classList.add("editing");
    this._urlEditing = true;
    input.value = this._data.url;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);

    // Enter 保存，Escape 取消
    const onKey = (e) => {
      if (e.key === "Enter") { e.preventDefault(); this._saveUrl(); }
      if (e.key === "Escape") { this._cancelEditUrl(); }
    };
    input.addEventListener("keydown", onKey);
    input._onKey = onKey;

    // 失焦 → 取消编辑（不保存），Save/Refresh 按钮的 mousedown preventDefault 会阻止失焦
    const onBlur = () => {
      setTimeout(() => {
        if (wrapper.classList.contains("editing")) {
          this._cancelEditUrl();
        }
      }, 150);
      input.removeEventListener("blur", onBlur);
    };
    input.addEventListener("blur", onBlur);
    input._onBlur = onBlur;
  }

  async _saveUrl() {
    const wrapper = this.querySelector(".detail-url-wrapper");
    const input = wrapper?.querySelector(".detail-url-input");
    if (!wrapper || !input) return;

    const newUrl = input.value.trim();
    wrapper.classList.remove("editing");
    this._urlEditing = false;
    input.removeEventListener("keydown", input._onKey);
    input.removeEventListener("blur", input._onBlur);

    // 只有点击保存时才真正把刷新得到的元数据写入数据库
    const { previewChanged } = await this._applyPendingMetadata();

    let urlChanged = false;
    if (newUrl && newUrl !== this._data.url) {
      urlChanged = true;
      await this._patchBookmark("url", newUrl);
    }

    // 若重新抓取后远程图未变化且本地文件仍缺失，PATCH 不会发出，
    // 因此显式触发一次本地预览图重试。
    const shouldRetryPreview = this._refreshShouldRetryPreview && !previewChanged;
    if (shouldRetryPreview) {
      await this._retryPreviewImage();
    }

    if (
      (previewChanged || urlChanged || shouldRetryPreview) &&
      !this._data.preview_image_url
    ) {
      await this._waitForPreviewImage();
    }
    this._refreshShouldRetryPreview = false;
  }

  _cancelEditUrl() {
    const wrapper = this.querySelector(".detail-url-wrapper");
    const input = wrapper?.querySelector(".detail-url-input");
    if (!wrapper || !input) return;

    wrapper.classList.remove("editing");
    this._urlEditing = false;
    input.removeEventListener("keydown", input._onKey);
    input.removeEventListener("blur", input._onBlur);

    this._revertPendingMetadata();
  }

  async _applyPendingMetadata() {
    if (!this._pendingMetadata) return { previewChanged: false };
    const pending = this._pendingMetadata;
    this._pendingMetadata = null;

    const patches = [];
    let previewChanged = false;
    const fields = ["title", "description", "preview_image_remote_url"];
    for (const field of fields) {
      if (
        Object.prototype.hasOwnProperty.call(pending, field) &&
        pending[field] !== this._data[field]
      ) {
        if (field === "preview_image_remote_url") previewChanged = true;
        patches.push(this._patchBookmark(field, pending[field]));
      }
    }
    await Promise.all(patches);
    return { previewChanged };
  }

  _revertPendingMetadata() {
    this._refreshShouldRetryPreview = false;
    if (!this._pendingMetadata) return;
    this._pendingMetadata = null;

    // 恢复为数据库中已保存的值
    const titleEl = this.querySelector(".bookmark-title-input");
    if (titleEl) {
      titleEl.value = this._data.title;
      this._autoResize(titleEl);
    }
    const descEl = this.querySelector('.detail-textarea[data-field="description"]');
    if (descEl) {
      descEl.value = this._data.description;
      this._autoResize(descEl);
    }
    this._setModalPreviewImage(this._data.preview_image_remote_url);
  }

  _syncPreviewImage(src) {
    const listItem = document.querySelector(
      `li[data-bookmark-id="${this.bookmarkId}"]`,
    );
    const listImg = listItem?.querySelector("img.preview-image");
    if (listImg) {
      listImg.onerror = null;
      listImg.src = src;
      listImg.style.display = "";
    }

    const modalImg = this.querySelector(".info-preview-image");
    if (modalImg) {
      modalImg.onerror = null;
      modalImg.src = src;
      modalImg.style.display = "";
      const section = modalImg.closest("[data-preview-section]");
      if (section) section.style.display = "";
    }
  }

  async _retryPreviewImage() {
    try {
      const r = await fetch(
        `${this.apiBase}bookmarks/${this.bookmarkId}/refresh-preview-image/`,
        {
          method: "POST",
          headers: { "X-CSRFToken": getCSRFToken() },
        },
      );
      if (!r.ok) return;
      const data = await r.json();
      this._data = { ...this._data, ...data };
      if (data.preview_image_url) {
        this._syncPreviewImage(data.preview_image_url);
      }
    } catch (err) {
      console.error("Refresh preview image failed:", err);
    }
  }

  async _waitForPreviewImage() {
    // 下载在 Huey 后台执行，完成后通过查询接口切回本地预览图。
    for (let attempt = 0; attempt < 30; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      if (!this.isConnected) return;
      try {
        const r = await fetch(`${this.apiBase}bookmarks/${this.bookmarkId}/`);
        if (!r.ok) continue;
        const data = await r.json();
        this._data = { ...this._data, ...data };
        if (data.preview_image_url) {
          this._syncPreviewImage(data.preview_image_url);
          return;
        }
        if (!data.preview_image_remote_url) return;
      } catch (err) {
        // Keep polling; a transient failure should not stop the refresh.
      }
    }
  }

  _setModalPreviewImage(src) {
    let img = this.querySelector(".info-preview-image");
    if (!img) {
      let section = this.querySelector("[data-preview-section]");
      if (!section) {
        section = document.createElement("div");
        section.className = "detail-section";
        section.dataset.previewSection = "";
        const label = document.createElement("div");
        label.className = "detail-label";
        label.textContent = gettext("Preview image");
        section.appendChild(label);
        const ref = this.querySelector(".detail-dates");
        if (ref) {
          ref.after(section);
        } else {
          this.querySelector(".modal-body")?.appendChild(section);
        }
      }
      img = document.createElement("img");
      img.className = "info-preview-image";
      img.alt = "";
      img.referrerPolicy = "strict-origin-when-cross-origin";
      img.onerror = () => {
        img.style.display = "none";
      };
      section.appendChild(img);
    }
    img.src = src || "";
    img.style.display = src ? "" : "none";
    const section = img.closest(".detail-section");
    if (section) section.style.display = src ? "" : "none";
  }

  // ---- 重新抓取元数据（条件性更新，与编辑页面逻辑一致） ----

  async _refreshMetadata() {
    const urlInput = this.querySelector(".detail-url-input");
    const url = urlInput?.value?.trim() || this._data.url;
    if (!url) return;

    this._refreshShouldRetryPreview = false;
    try {
      const apiUrl = `${this.apiBase}bookmarks/check?url=${encodeURIComponent(url)}&ignore_cache=true`;
      const r = await fetch(apiUrl);
      if (!r.ok) return;
      const data = await r.json();
      const metadata = data.metadata;
      const existingBookmark = data.bookmark;

      this._refreshShouldRetryPreview =
        existingBookmark &&
        String(existingBookmark.id) === String(this.bookmarkId) &&
        !!existingBookmark.preview_image_remote_url &&
        !existingBookmark.preview_image_url;

      // 只更新 UI 并暂存，真正写库要等点击“保存”
      const pending = {};

      // (1) 标题：获取到的标题非空 → 替换
      if (metadata.title && metadata.title !== this._data.title) {
        pending.title = metadata.title;
        const el = this.querySelector(".bookmark-title-input");
        if (el) { el.value = metadata.title; this._autoResize(el); }
      }

      // (2) 描述：获取到的描述非空，且与现有描述不同 → 覆盖
      if (metadata.description && metadata.description !== this._data.description) {
        pending.description = metadata.description;
        const el = this.querySelector('.detail-textarea[data-field="description"]');
        if (el) { el.value = metadata.description; this._autoResize(el); }
      }

      // (3) 预览图：获取到的预览图非空，且与现有 remote_url 不同 → 替换
      if (
        metadata.preview_image &&
        metadata.preview_image !== (this._data.preview_image_remote_url || "")
      ) {
        pending.preview_image_remote_url = metadata.preview_image;
        this._setModalPreviewImage(metadata.preview_image);
      }

      this._pendingMetadata = Object.keys(pending).length ? pending : null;
    } catch (err) {
      console.error("Refresh metadata failed:", err);
    }
  }

  // ---- API PATCH（保存 + 局部同步列表项） ----

  async _patchBookmark(field, value) {
    const current = this._data[field];
    let newValue;

    if (Array.isArray(current) || Array.isArray(value)) {
      newValue = Array.isArray(value) ? value : String(value).split(/\s+/).map((s) => s.trim()).filter(Boolean);
      if (JSON.stringify(newValue) === JSON.stringify(current)) return;
    } else {
      newValue = String(value).trim();
      if (newValue === (current || "")) return;
    }

    // URL 变更前记录旧值，用于定位列表中的链接
    if (field === "url") this._data._prevUrl = this._data.url;

    this._data[field] = newValue;

    try {
      const r = await fetch(`${this.apiBase}bookmarks/${this.bookmarkId}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCSRFToken() },
        body: JSON.stringify({ [field]: newValue }),
      });
      if (r.ok) {
        const data = await r.json();
        this._data = { ...this._data, ...data };
        this._syncListItemField(field, newValue);
        if (data.preview_image_url) {
          this._syncPreviewImage(data.preview_image_url);
        }
      }
    } catch (err) {
      console.error("Save failed:", err);
    }
  }

  // ---- 标签（显示态 ↔ 编辑态，编辑态动态创建 ld-tag-autocomplete） ----

  _startEditTags() {
    const wrapper = this.querySelector(".detail-tags-wrapper");
    if (!wrapper || wrapper._editing) return;
    wrapper._editing = true;
    wrapper.classList.add("ld-editing");

    // 动态创建 ld-tag-autocomplete（每次都是全新实例，避免状态残留）
    const autocomplete = document.createElement("ld-tag-autocomplete");
    autocomplete.setAttribute("input-value", this._data.tag_names.join(" "));
    autocomplete.setAttribute("input-placeholder", gettext("Click to edit tags"));
    wrapper.appendChild(autocomplete);

    // 等组件渲染完成后聚焦（LitElement 渲染是异步的）
    const onReady = () => {
      const input = autocomplete.querySelector("input");
      if (!input) return;
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);

      input.addEventListener("blur", () => {
        setTimeout(() => {
          if (autocomplete.contains(document.activeElement)) return;

          const newValue = (input.value || "").split(/\s+/).map(s => s.trim()).filter(Boolean);
          if (JSON.stringify(newValue) !== JSON.stringify(this._data.tag_names)) {
            this._patchBookmark("tag_names", newValue);
          }

          autocomplete.remove();
          wrapper._editing = false;
          wrapper.classList.remove("ld-editing");
          this._updateTagDisplay(newValue);
        }, 150);
      });

      input.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          autocomplete.remove();
          wrapper._editing = false;
          wrapper.classList.remove("ld-editing");
          // 恢复原标签显示
          this._updateTagDisplay(this._data.tag_names);
        }
      });

      // 监听 commit 事件（回车键保存）
      autocomplete.addEventListener("commit", () => {
        const newValue = (input.value || "").split(/\s+/).map(s => s.trim()).filter(Boolean);
        if (JSON.stringify(newValue) !== JSON.stringify(this._data.tag_names)) {
          this._patchBookmark("tag_names", newValue);
        }

        autocomplete.remove();
        wrapper._editing = false;
        wrapper.classList.remove("ld-editing");
        this._updateTagDisplay(newValue);
      });
    };

    if (autocomplete.updateComplete) {
      autocomplete.updateComplete.then(onReady);
    } else {
      requestAnimationFrame(onReady);
    }
  }

  _updateTagDisplay(tagNames) {
    const view = this.querySelector(".detail-tags-view");
    if (!view) return;
    if (tagNames.length) {
      view.innerHTML = `<span class="info-tags">${tagNames.map((t) => `<span class="info-tag">${t}</span>`).join("")}</span>`;
    } else {
      view.innerHTML = `<span class="info-placeholder">${gettext("Click to edit tags")}</span>`;
    }
  }

  // ---- 文件重命名 ----

  _startRenameAsset(assetId) {
    const item = this.querySelector(`.info-file-item[data-asset-id="${assetId}"]`);
    if (!item) return;
    const nameEl = item.querySelector(".info-file-link, .info-file-name");
    if (!nameEl) return;
    const currentName = nameEl.textContent.trim();

    const input = document.createElement("input");
    input.className = "info-file-rename-input";
    input.type = "text";
    input.value = currentName;
    nameEl.replaceWith(input);
    input.focus();
    input.select();

    const save = () => {
      const newName = input.value.trim();
      if (newName && newName !== currentName) {
        // API PATCH 重命名，不刷新整个页面
        fetch(`${this.apiBase}bookmarks/${this.bookmarkId}/assets/${assetId}/`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json", "X-CSRFToken": getCSRFToken() },
          body: JSON.stringify({ display_name: newName }),
        }).catch(err => console.error("Rename failed:", err));
      }
      // 恢复为链接显示
      const link = document.createElement("a");
      link.className = "info-file-link";
      link.href = `/assets/${assetId}`;
      link.target = "_blank";
      link.dataset.turbo = "false";
      link.textContent = newName || currentName;
      link.title = newName || currentName;
      input.replaceWith(link);
    };

    input.addEventListener("blur", save);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") input.blur();
      if (e.key === "Escape") { input.value = currentName; input.blur(); }
    });
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._onMorph) {
      document.removeEventListener("turbo:before-morph-element", this._onMorph);
      this._onMorph = null;
    }
  }

  // ---- 关闭 ----

  doClose() {
    super.doClose();
    const bookmarkId = this.dataset.bookmarkId;
    if (bookmarkId) {
      setAfterPageLoadFocusTarget(
        `ul.bookmark-list li[data-bookmark-id='${bookmarkId}'] a.view-action`,
      );
    }
  }
}

customElements.define("ld-details-modal", DetailsModal);
