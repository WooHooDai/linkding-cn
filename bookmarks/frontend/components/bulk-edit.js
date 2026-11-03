import { Behavior, registerBehavior } from "./runtime.js";
import { gettext } from "../utils/i18n.js";
import { Selection } from "../utils/selection.js";
import { SelectionView } from "./selection-view.js";
import { MetadataPanel } from "./bulk-metadata-edit.js";

const STORAGE_KEY = "linkding:bulk-edit";
// 动作也要记住：翻页后 select 会被服务端重新渲染成第一项，用户填的表单就白填了
const ACTION_KEY = "linkding:bulk-action";
// 是否处于批量模式单独记：空选区翻页也要留在模式里，只有「取消」才退出
const ACTIVE_KEY = "linkding:bulk-active";

// List-removing actions clear the selection; others keep it for the next op.
const REMOVING_ACTIONS = new Set([
  "bulk_archive",
  "bulk_unarchive",
  "bulk_trash",
  "bulk_restore",
  "bulk_delete",
]);

// Bulk edit shell: owns the mode (active), the selection state and the form
// payload. Rendering and gestures live in SelectionView, the state itself in
// Selection. Controls and checkboxes are handled by delegation on the root
// element, so a replaced list needs no listener rebinding.
class BulkEdit extends Behavior {
  constructor(element) {
    super(element);

    this.active = element.classList.contains("active");
    this.isStickyOn =
      element.querySelector(".section-header")?.dataset.stickyOn === "true";
    this.bulkEditBar = element.querySelector(".bulk-edit-bar");
    this.selection = Selection.from(this._loadState());

    const panelEl = element.querySelector("[data-bulk-metadata-panel]");
    this.metadataPanel = panelEl
      ? new MetadataPanel(panelEl, {
          form: element.querySelector("form.bookmark-actions"),
          onToggle: () =>
            this._syncActionButton(
              this.element.querySelector("select[name='bulk_action']")?.value,
            ),
        })
      : null;

    this.view = new SelectionView(element, {
      getSelection: () => this.selection,
      isActive: () => this.active,
      onChange: () => {
        this._persist();
        // 面板开着时勾选变化：先同步 hidden 选区，再让统计跟着变
        if (this.metadataPanel?.isOpen()) {
          const form = this.element.querySelector("form.bookmark-actions");
          if (form) {
            this._injectHiddenFields(form);
          }
          this.metadataPanel.refreshScope();
        }
      },
    });

    this.onToggleActive = this.onToggleActive.bind(this);
    this.onActionSelected = this.onActionSelected.bind(this);
    this.onSubmit = this.onSubmit.bind(this);
    this.refresh = this.refresh.bind(this);

    this._bindControls();
    this.view.mount();
    this.refresh();
    this._restore();

    document.addEventListener("bookmark-list-updated", this.refresh);
  }

  destroy() {
    document.removeEventListener("bookmark-list-updated", this.refresh);
    this._unbindControls();
    this.view.unmount();
    this.metadataPanel?.destroy();
  }

  // Re-read the list after it was replaced (turbo stream, pagination, filter).
  refresh() {
    this.view.readLayout();
    this.view.run();
    this._syncActionButton(this._currentAction());
  }

  _currentAction() {
    return this.element.querySelector("select[name='bulk_action']")?.value;
  }

  // --- Mode ---------------------------------------------------------------

  _bindControls() {
    this.element.addEventListener("click", this.onToggleActive);
    this.element.addEventListener("change", this.onActionSelected);
    this.element.addEventListener("submit", this.onSubmit);
  }

  _unbindControls() {
    this.element.removeEventListener("click", this.onToggleActive);
    this.element.removeEventListener("change", this.onActionSelected);
    this.element.removeEventListener("submit", this.onSubmit);
  }

  onToggleActive(event) {
    const toggle = event.target.closest(
      ".bulk-edit-active-toggle, [name='bulk_cancel']",
    );
    if (toggle) {
      this._setActive(!this.active);
    }
  }

  _setActive(active) {
    this.active = active;
    this.element.classList.toggle("active", active);
    if (this.isStickyOn) {
      this.bulkEditBar?.classList.toggle("sticky", active);
    }
    this.view.selectAcross?.classList.toggle("d-none", !active);
    if (active) {
      this._persistActive(true);
      this.view.run();
      this._syncActionButton(this._currentAction());
      return;
    }
    this.selection = new Selection();
    this.view.resetAnchor();
    this._clearState();
    this._persistActive(false);
    this.metadataPanel?.close();
    this.metadataPanel?.clearDraft();
  }

  // 「编辑元数据」没有要在批量条上填的参数：按钮在「展开/收起」间切换
  onActionSelected(event) {
    if (!event.target.matches("select[name='bulk_action']")) {
      return;
    }

    const action = event.target.value;
    this._persistAction(action);
    this._syncActionButton(action);

    // 选中即展开：面板不依赖选区，先摆出来，勾选后统计会自动刷新
    if (action === "bulk_edit_metadata") {
      this._injectHiddenFields(event.target.closest("form"));
      this.metadataPanel?.open();
    } else {
      // 用户主动换成别的动作，才丢弃面板里没提交的草稿
      this.metadataPanel?.clearDraft();
    }
  }

  _syncActionButton(action) {
    const isMetadata = action === "bulk_edit_metadata";
    const execBtn = this.element.querySelector("button[name='bulk_execute']");
    const label = execBtn?.querySelector("span");

    if (label) {
      if (!isMetadata) {
        label.textContent = gettext("Execute");
      } else {
        label.textContent = this.metadataPanel?.isOpen()
          ? gettext("Collapse panel")
          : gettext("Expand");
      }
    }
    execBtn?.toggleAttribute("data-skip-confirm", isMetadata);

    if (isMetadata) {
      // 这个动作下按钮是面板开合开关，不依赖选区，空选区也能先把面板调出来
      execBtn.disabled = false;
    }

    if (!isMetadata) {
      this.metadataPanel?.close();
    }
  }

  // --- Form ---------------------------------------------------------------

  onSubmit(event) {
    if (!event.target.matches("form.bookmark-actions")) {
      return;
    }
    const action = this.element.querySelector(
      "select[name='bulk_action']",
    )?.value;

    // 元数据改写要先把面板展开，提交换成开/收面板
    if (
      action === "bulk_edit_metadata" &&
      !event.target.dataset.metadataSubmitting
    ) {
      event.preventDefault();
      // 先把跨页选区同步进表单，面板的预览请求直接复用这份数据
      this._injectHiddenFields(event.target);
      if (this.metadataPanel?.isOpen()) {
        this.metadataPanel.close();
      } else {
        this.metadataPanel?.open();
      }
      return;
    }
    delete event.target.dataset.metadataSubmitting;

    this._injectHiddenFields(event.target);
    if (REMOVING_ACTIONS.has(action)) {
      // Clear the state only — the DOM still has to submit its checkbox values.
      this.selection = new Selection();
      this._clearState();
    }
  }

  _injectHiddenFields(form) {
    form
      .querySelectorAll("input[data-bulk-sync]")
      .forEach((element) => element.remove());

    const { selectAcross, ids, excludeIds } = this.selection.payload(
      this.view.order,
    );
    if (selectAcross) {
      this._appendHiddenFields(form, "bulk_exclude_id", excludeIds);
    } else {
      this._appendHiddenFields(form, "bookmark_id", ids);
    }
  }

  _appendHiddenFields(form, name, values) {
    for (const value of values) {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      input.value = value;
      input.dataset.bulkSync = "1";
      form.appendChild(input);
    }
  }

  // --- State persistence --------------------------------------------------

  _loadState() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (raw) {
        return JSON.parse(raw);
      }
    } catch (e) {
      // ignore
    }
    return null;
  }

  _persist() {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(this.selection));
    } catch (e) {
      // ignore
    }
  }

  _clearState() {
    sessionStorage.removeItem(STORAGE_KEY);
    sessionStorage.removeItem(ACTION_KEY);
  }

  _persistAction(action) {
    try {
      if (action) {
        sessionStorage.setItem(ACTION_KEY, action);
      } else {
        sessionStorage.removeItem(ACTION_KEY);
      }
    } catch (e) {
      // ignore
    }
  }

  _loadAction() {
    try {
      return sessionStorage.getItem(ACTION_KEY);
    } catch (e) {
      return null;
    }
  }

  _persistActive(active) {
    try {
      if (active) {
        sessionStorage.setItem(ACTIVE_KEY, "1");
      } else {
        sessionStorage.removeItem(ACTIVE_KEY);
      }
    } catch (e) {
      // ignore
    }
  }

  _loadActive() {
    try {
      return sessionStorage.getItem(ACTIVE_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  // A saved selection survives a reload; it also implies bulk mode is on.
  // 动作与草稿一并恢复：翻页或执行后回来，表单还在原处。
  _restore() {
    const modeRequested = this._loadActive();
    if (!modeRequested && this.selection.isEmpty) {
      return;
    }

    // 先还原动作再进入批量模式：否则 _setActive 会拿服务端默认的「标记为已读」
    // 判定成"切走了"，把草稿清掉
    const select = this.element.querySelector("select[name='bulk_action']");
    const action = this._loadAction();
    if (action && select?.querySelector(`option[value="${action}"]`)) {
      select.value = action;
    }

    this._setActive(true);

    if (action !== "bulk_edit_metadata" || !this.metadataPanel) {
      return;
    }

    // 预览要按选区求值，先把 hidden 字段补进表单
    const form = this.element.querySelector("form.bookmark-actions");
    if (form) {
      this._injectHiddenFields(form);
    }

    const draft = this.metadataPanel.loadDraft();
    if (draft) {
      this.metadataPanel.applyOps(draft);
    }
    this.metadataPanel.open();
  }
}

registerBehavior("ld-bulk-edit", BulkEdit);
