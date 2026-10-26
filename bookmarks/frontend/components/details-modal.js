import { setAfterPageLoadFocusTarget } from "../utils/focus.js";
import { handleBookmarkAction } from "../utils/bookmark-action.js";
import { getCSRFToken } from "../utils/csrf.js";
import { Modal } from "./modal.js";
import { attachHealthPopover } from "./health.js";
import { initAuthorScroll } from "../utils/author-scroll.js";
import { showToast } from "./toast.js";

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
      author: this.dataset.bookmarkAuthor || "",
      notes: this.dataset.bookmarkNotes || "",
      preview_image_remote_url: this.dataset.bookmarkPreviewImageRemoteUrl || "",
      tag_names: (this.dataset.bookmarkTagNames || "")
        .split(" ")
        .filter(Boolean),
    };
    this._refreshSnapshot = null;
    this._refreshing = false;
    this._urlEditing = false;

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
    // ---- 元数据动作（底部操作栏，不依赖 URL 编辑态） ----
    const refreshBtn = this.querySelector("#refresh-metadata-btn");
    const undoBtn = this.querySelector("#undo-metadata-btn");
    // 阻止失焦：URL 编辑态由点击处理里先落定，抓取会整体覆盖字段
    refreshBtn?.addEventListener("mousedown", (e) => e.preventDefault());
    refreshBtn?.addEventListener("click", () => {
      this._commitEditUrl().then(() => this._refreshMetadata(refreshBtn));
    });
    undoBtn?.addEventListener("mousedown", (e) => e.preventDefault());
    undoBtn?.addEventListener("click", () => this._undoRefresh(undoBtn));

    // ---- 字段级撤销（抓取覆盖的字段行内，事件委托以兼容动态创建的预览图区块） ----
    this.addEventListener("mousedown", (e) => {
      if (e.target.closest("[data-undo-field]")) e.preventDefault();
    });
    this.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-undo-field]");
      if (btn && this.contains(btn)) this._undoField(btn.dataset.undoField, btn);
    });

    // ---- 健康检查（单条） ----
    const healthBtn = this.querySelector("[data-health-check]");
    if (healthBtn) {
      healthBtn.addEventListener("click", () => this._runHealthCheck(healthBtn));
    }
    // 状态 chip 的 dot+状态 区域：悬浮/点击显示健康详情弹层
    const healthChipStatus = this.querySelector("[data-health-chip-status]");
    if (healthChipStatus) {
      this._healthPopoverCleanup = attachHealthPopover(healthChipStatus);
    }

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
      case "author": {
        const title = item.querySelector(".title");
        if (!title) break;
        let authorSpan = title.querySelector(".author");
        if (value) {
          if (!authorSpan) {
            authorSpan = document.createElement("span");
            authorSpan.className = "author";
            const titleLink = title.querySelector(".title-link");
            if (titleLink) {
              titleLink.insertAdjacentElement("afterend", authorSpan);
            } else {
              title.appendChild(authorSpan);
            }
          }
          authorSpan.textContent = value;
          initAuthorScroll(authorSpan);
        } else if (authorSpan) {
          authorSpan.remove();
        }
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
      case "tag_names": {        const tags = Array.isArray(value) ? value : [];
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

    const onKey = (e) => {
      if (e.key === "Enter") { e.preventDefault(); input.blur(); }
      if (e.key === "Escape") { this._cancelEditUrl(); }
    };
    input.addEventListener("keydown", onKey);
    input._onKey = onKey;

    // 失焦即生效：URL 有变化就保存并重新抓取元数据
    const onBlur = () => {
      input.removeEventListener("blur", onBlur);
      input.removeEventListener("keydown", input._onKey);
      this._commitEditUrl().then((changed) => {
        if (changed) {
          this._refreshMetadata(this.querySelector("#refresh-metadata-btn"));
        }
      });
    };
    input.addEventListener("blur", onBlur);
  }

  // 退出 URL 编辑态；有变化时保存。返回是否发生了变化。
  async _commitEditUrl() {
    const wrapper = this.querySelector(".detail-url-wrapper");
    const input = wrapper?.querySelector(".detail-url-input");
    if (!wrapper || !input || !this._urlEditing) return false;

    const newUrl = input.value.trim();
    wrapper.classList.remove("editing");
    this._urlEditing = false;

    if (!newUrl || newUrl === this._data.url) return false;

    const previousUrl = this._data.url;
    await this._patchBookmark("url", newUrl);
    if (!this._data.preview_image_url) {
      await this._waitForPreviewImage();
    }

    // URL 修改也可撤销；连续修改保留最早一份快照
    this._refreshSnapshot = this._refreshSnapshot || {};
    const entry = this._refreshSnapshot.url;
    if (entry) {
      entry.applied = newUrl;
    } else {
      this._refreshSnapshot.url = { before: previousUrl, applied: newUrl };
    }
    this._syncRefreshUI();
    return true;
  }

  _cancelEditUrl() {
    const wrapper = this.querySelector(".detail-url-wrapper");
    const input = wrapper?.querySelector(".detail-url-input");
    if (!wrapper || !input) return;

    input.removeEventListener("keydown", input._onKey);
    // 还原为当前值并退出编辑态（不触发保存）
    input.value = this._data.url;
    wrapper.classList.remove("editing");
    this._urlEditing = false;
  }

  // ---- 元数据抓取状态：即写库 + 可撤销，与其它字段"失焦即保存"保持一致 ----

  _fieldElement(field) {
    if (field === "title") return this.querySelector(".bookmark-title-input");
    if (field === "preview_image_remote_url") return null;
    return this.querySelector(`.detail-textarea[data-field="${field}"]`);
  }

  _fieldValue(field) {
    const el = this._fieldElement(field);
    return el ? el.value : this._data[field] || "";
  }

  _renderFieldValues(values) {
    for (const [field, value] of Object.entries(values)) {
      const el = this._fieldElement(field);
      if (!el) continue;
      el.value = value;
      this._autoResize(el);
    }
  }

  async _patchFields(fields) {
    if (fields.url) this._data._prevUrl = this._data.url;
    const r = await fetch(`${this.apiBase}bookmarks/${this.bookmarkId}/`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "X-CSRFToken": getCSRFToken() },
      body: JSON.stringify(fields),
    });
    if (!r.ok) throw new Error(`PATCH failed: ${r.status}`);

    const data = await r.json();
    this._data = { ...this._data, ...data };
    for (const field of Object.keys(fields)) {
      if (field !== "preview_image_remote_url") {
        this._syncListItemField(field, fields[field]);
      }
    }
    return data;
  }

  _syncRefreshUI() {
    const btn = this.querySelector("#undo-metadata-btn");
    if (btn) btn.style.display = this._refreshSnapshot ? "" : "none";
    // 字段级撤销按钮：该字段存在快照才出现
    this.querySelectorAll("[data-undo-field]").forEach((el) => {
      el.style.display = this._refreshSnapshot?.[el.dataset.undoField] ? "" : "none";
    });
    for (const field of Object.keys(this._refreshSnapshot || {})) {
      this._fieldElement(field)?.classList.add("is-metadata-modified");
    }
  }

  // 单字段撤销：期间被手动改过就只作废快照，不覆盖用户新内容
  async _undoField(field, btn) {
    const entry = this._refreshSnapshot?.[field];
    if (!entry || this._refreshing) return;

    btn?.setAttribute("disabled", "");
    try {
      if (this._fieldValue(field) === entry.applied) {
        const restore = { [field]: entry.before };
        this._renderFieldValues(restore);
        await this._patchFields(restore);

        if (field === "preview_image_remote_url") {
          const src = entry.before;
          this._setModalPreviewImage(src);
          if (src) {
            this._syncPreviewImage(src);
            this._waitForPreviewImage();
          }
        }
        if (field === "url" && !this._data.preview_image_url) {
          this._waitForPreviewImage();
        }
      }

      delete this._refreshSnapshot[field];
      this._fieldElement(field)?.classList.remove("is-metadata-modified");
      if (!Object.keys(this._refreshSnapshot).length) {
        this._refreshSnapshot = null;
      }
      this._syncRefreshUI();
    } catch (err) {
      console.error("Undo metadata refresh failed:", err);
      showToast(gettext("Could not undo the last metadata refresh"), { tone: "error" });
    } finally {
      btn?.removeAttribute("disabled");
    }
  }

  _clearRefreshState() {
    this._refreshSnapshot = null;
    this.querySelectorAll(".is-metadata-modified").forEach((el) =>
      el.classList.remove("is-metadata-modified"),
    );
    this._syncRefreshUI();
  }

  // 用户手动改过某字段后作废其快照，撤销不再覆盖他的新内容
  _dropRefreshSnapshotField(field, value) {
    const entry = this._refreshSnapshot?.[field];
    if (!entry || entry.applied === value) return;

    delete this._refreshSnapshot[field];
    this._fieldElement(field)?.classList.remove("is-metadata-modified");
    if (!Object.keys(this._refreshSnapshot).length) {
      this._refreshSnapshot = null;
    }
    this._syncRefreshUI();
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

  // 当前弹窗预览图是否加载失败（文件缺失/404）。complete=true 且 naturalWidth=0 表示加载出错。
  _isModalPreviewBroken() {
    const img = this.querySelector(".info-preview-image");
    return !!img && !!img.getAttribute("src") && img.complete && img.naturalWidth === 0;
  }

  _setModalPreviewImage(src) {
    let img = this.querySelector(".info-preview-image");
    if (!img) {
      let section = this.querySelector("[data-preview-section]");
      if (!section) {
        section = document.createElement("div");
        section.className = "detail-section";
        section.dataset.previewSection = "";
        const labelRow = document.createElement("div");
        labelRow.className = "detail-label-row";
        const label = document.createElement("span");
        label.className = "detail-label";
        label.textContent = gettext("Preview image");
        labelRow.appendChild(label);
        const undoBtn = document.createElement("button");
        undoBtn.type = "button";
        undoBtn.className = "btn btn-link btn-sm";
        undoBtn.dataset.undoField = "preview_image_remote_url";
        undoBtn.title = gettext("Undo metadata refresh");
        undoBtn.style.display = "none";
        undoBtn.textContent = gettext("Undo");
        labelRow.appendChild(undoBtn);
        section.appendChild(labelRow);
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

  async _runHealthCheck(btn) {
    const url = btn.dataset.checkUrl;
    if (!url) return;
    const recheckLabel = btn.dataset.labelRecheck || "Recheck";
    const checkLabel = btn.dataset.labelCheck || "Check now";
    btn.disabled = true;
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "X-CSRFToken": getCSRFToken() },
      });
      const data = await r.json();
      if (!r.ok || !data.ok) {
        console.error("Health check failed:", data?.error || r.status);
        return;
      }
      const status = data.status || "unknown";
      const statusDisplay = data.status_display || status;
      // 状态指示（小圆点 + 文字）
      const dot = this.querySelector("[data-health-dot]");
      const text = this.querySelector("[data-health-text]");
      if (dot) dot.className = `health-dot health-dot--${status}`;
      if (text) text.textContent = statusDisplay;
      // 详情弹层内容（行元素在模板中始终渲染，用 hidden 控制显隐：
      // 即使书签从未检查过，重检后也能就地补全 HTTP/原因/检查时间，无需整页刷新）
      const popoverStatus = this.querySelector("[data-popover-status]");
      const popoverHttp = this.querySelector("[data-popover-http]");
      const popoverReason = this.querySelector("[data-popover-reason]");
      const popoverChecked = this.querySelector("[data-popover-checked]");
      if (popoverStatus) popoverStatus.textContent = statusDisplay;
      if (popoverHttp) {
        popoverHttp.hidden = !data.http_status;
        popoverHttp.textContent = data.http_status ? `HTTP ${data.http_status}` : "";
      }
      if (popoverReason) {
        popoverReason.hidden = !data.reason;
        popoverReason.textContent = data.reason || "";
      }
      if (popoverChecked) {
        popoverChecked.hidden = !data.checked_at_display;
        popoverChecked.textContent = data.checked_at_display
          ? `${gettext("Checked at")} ${data.checked_at_display}`
          : "";
      }
      // 按钮：仅图标，更新 title
      btn.title = data.checked_at ? recheckLabel : checkLabel;
    } catch (err) {
      console.error("Health check failed:", err);
    } finally {
      btn.disabled = false;
    }
  }


  // ---- 重新抓取元数据：抓到的非空值覆盖并写库，失败可见，可撤销 ----

  async _refreshMetadata(btn) {
    const url = this._data.url;
    if (!url || this._refreshing) return;

    this._refreshing = true;
    btn?.setAttribute("disabled", "");
    btn?.classList.add("is-loading");
    try {
      // 等内联编辑的保存在途请求落定，避免两条 PATCH 乱序
      await this._inflightPatch?.catch(() => {});

      const apiUrl = `${this.apiBase}bookmarks/check?url=${encodeURIComponent(url)}&ignore_cache=true`;
      const r = await fetch(apiUrl);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const { metadata } = await r.json();

      const candidates = {
        title: metadata.title,
        description: metadata.description,
        author: metadata.author,
        preview_image_remote_url: metadata.preview_image,
      };
      const applied = {};
      for (const [field, value] of Object.entries(candidates)) {
        // 抓不到就保留原值（与批量「刷新元数据」一致），预览图也不清空
        if (!value || value === this._fieldValue(field)) continue;
        applied[field] = value;
      }

      if (!Object.keys(applied).length) {
        // 站点数据与当前一致；预览图本地文件缺失时补一次重试
        if (metadata.preview_image && !this._data.preview_image_url) {
          if (this._isModalPreviewBroken()) {
            this._setModalPreviewImage(metadata.preview_image);
          }
          await this._retryPreviewImage();
        }
        this._clearRefreshState();
        return;
      }

      // 连续抓取只保留最早一份快照，撤销回到最初的内容
      this._refreshSnapshot = this._refreshSnapshot || {};
      for (const [field, value] of Object.entries(applied)) {
        const entry = this._refreshSnapshot[field];
        if (entry) {
          entry.applied = value;
        } else {
          this._refreshSnapshot[field] = { before: this._fieldValue(field), applied: value };
        }
      }

      this._renderFieldValues(applied);
      await this._patchFields(applied);
      this._syncRefreshUI();

      if (applied.preview_image_remote_url) {
        this._setModalPreviewImage(applied.preview_image_remote_url);
        this._syncPreviewImage(applied.preview_image_remote_url);
        // 本地文件由后台任务重下，完成后切回本地图
        this._waitForPreviewImage();
      }
    } catch (err) {
      console.error("Refresh metadata failed:", err);
      showToast(gettext("Could not refresh metadata from the website"), { tone: "error" });
    } finally {
      this._refreshing = false;
      btn?.classList.remove("is-loading");
      btn?.removeAttribute("disabled");
    }
  }

  async _undoRefresh(btn) {
    const snapshot = this._refreshSnapshot;
    if (!snapshot || this._refreshing) return;

    btn?.setAttribute("disabled", "");
    try {
      const restore = {};
      for (const [field, entry] of Object.entries(snapshot)) {
        // 期间被手动改过的字段不回滚，避免覆盖用户的新输入
        if (this._fieldValue(field) !== entry.applied) continue;
        restore[field] = entry.before;
      }

      if (Object.keys(restore).length) {
        this._renderFieldValues(restore);
        await this._patchFields(restore);
      }

      if ("preview_image_remote_url" in restore) {
        const src = restore.preview_image_remote_url;
        this._setModalPreviewImage(src);
        if (src) {
          this._syncPreviewImage(src);
          this._waitForPreviewImage();
        }
      }

      this._clearRefreshState();
    } catch (err) {
      console.error("Undo metadata refresh failed:", err);
      showToast(gettext("Could not undo the last metadata refresh"), { tone: "error" });
    } finally {
      btn?.removeAttribute("disabled");
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
      const request = fetch(`${this.apiBase}bookmarks/${this.bookmarkId}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCSRFToken() },
        body: JSON.stringify({ [field]: newValue }),
      });
      // 抓取前要等它落定，否则两条 PATCH 可能乱序
      this._inflightPatch = request;
      const r = await request;
      if (r.ok) {
        const data = await r.json();
        this._data = { ...this._data, ...data };
        this._syncListItemField(field, newValue);
        this._dropRefreshSnapshotField(field, newValue);
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
      view.innerHTML = `<span class="info-tags">${tagNames.map((t) => `<span class="info-tag">#${t}</span>`).join("")}</span>`;
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
    if (this._healthPopoverCleanup) {
      this._healthPopoverCleanup.destroy();
      this._healthPopoverCleanup = null;
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
