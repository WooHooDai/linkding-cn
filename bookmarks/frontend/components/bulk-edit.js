import { Behavior, registerBehavior } from "./runtime.js";
import { gettext } from "../utils/i18n.js";
import { Selection } from "../utils/selection.js";
import { SelectionView } from "./selection-view.js";
import { MetadataPanel } from "./bulk-metadata-edit.js";
import { BOOKMARK_LIST_SELECTOR } from "../utils/bookmark-list.js";

// List-removing actions clear the selection; others keep it for the next op.
const DEFAULT_REMOVING_ACTIONS = [
  "bulk_archive",
  "bulk_unarchive",
  "bulk_trash",
  "bulk_restore",
  "bulk_delete",
];

// 页面差异全部走根节点上的 data-bulk-*：书签与高亮共用这份逻辑，
// 只需要换选择器、字段名和存储命名空间。存下来的选区是「ID 列表」，
// 跨列表复用没有意义（活动页的 ID 在回收站里不存在），所以每个列表页
// 各用各的命名空间；storage 为空只留给单列表的页面。
class BulkEdit extends Behavior {
  constructor(element) {
    super(element);
    this.config = this._readConfig();

    this.active = element.classList.contains("active");
    this.isStickyOn =
      (
        element.querySelector("[data-bulk-sticky-source]") ||
        // 高亮页没有 section-header，头部是 .highlights-sticky-header
        element.querySelector(".section-header, .highlights-sticky-header")
      )?.dataset.stickyOn === "true";
    this.bulkEditBar = element.querySelector(".bulk-edit-bar");
    this.selection = Selection.from(this._loadState());

    const panelEl = element.querySelector("[data-bulk-metadata-panel]");
    this.metadataPanel = panelEl
      ? new MetadataPanel(panelEl, {
          form: this.config.form,
          onToggle: () => this._syncActionButton(this._currentAction()),
        })
      : null;

    this.view = new SelectionView(element, {
      selectors: this.config.selectors,
      getSelection: () => this.selection,
      isActive: () => this.active,
      onChange: () => {
        this._persist();
        // 面板开着时勾选变化：先同步 hidden 选区，再让统计跟着变
        if (this.metadataPanel?.isOpen()) {
          if (this.config.form) {
            this._injectHiddenFields(this.config.form);
          }
          this.metadataPanel.refreshScope();
        }
        this._onSelectionChange();
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

  _readConfig() {
    const data = this.element.dataset;
    const namespace = data.bulkStorage ? `${data.bulkStorage}-` : "";

    return {
      form:
        this.element.querySelector("[data-bulk-form]") ||
        this.element.querySelector("form.bookmark-actions"),
      field: data.bulkField || "bookmark_id",
      excludeField: data.bulkExcludeField || "bulk_exclude_id",
      // 提交前要把动作写回隐藏字段的页面（高亮页靠 input[name=action] 分派）
      actionField: data.bulkActionField || "",
      actionValue: data.bulkActionValue || "bulk_execute",
      panelAction: data.bulkPanelAction ?? "bulk_edit_metadata",
      removing: new Set(
        (data.bulkRemoving ?? DEFAULT_REMOVING_ACTIONS.join(","))
          .split(",")
          .filter(Boolean),
      ),
      keys: {
        state: `linkding:${namespace}bulk-edit`,
        action: `linkding:${namespace}bulk-action`,
        active: `linkding:${namespace}bulk-active`,
      },
      selectors: {
        container: data.bulkContainer || BOOKMARK_LIST_SELECTOR,
        item: data.bulkItem || "li[ld-bookmark-item]",
        itemId: data.bulkItemId || "bookmarkId",
        group: data.bulkGroup || ".group-header",
        totalAttr: data.bulkTotalAttr || "bookmarksTotal",
      },
    };
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
    this._syncActionButton(this._currentAction());
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
    if (this._isPanelAction(action)) {
      this._injectHiddenFields(event.target.closest("form"));
      this.metadataPanel?.open();
    } else {
      // 用户主动换成别的动作，才丢弃面板里没提交的草稿
      this.metadataPanel?.clearDraft();
    }
  }

  _isPanelAction(action) {
    return !!this.config.panelAction && action === this.config.panelAction;
  }

  _syncActionButton(action) {
    const isPanel = this._isPanelAction(action);
    const execBtn = this.element.querySelector("button[name='bulk_execute']");
    const label = execBtn?.querySelector("span");

    if (label) {
      if (!isPanel) {
        label.textContent = gettext("Execute");
      } else {
        label.textContent = this.metadataPanel?.isOpen()
          ? gettext("Collapse panel")
          : gettext("Expand");
      }
    }
    // 无破坏性的动作（面板动作、页面声明免确认的动作）直接提交
    execBtn?.toggleAttribute(
      "data-skip-confirm",
      isPanel || this._skipConfirm(action),
    );

    if (isPanel) {
      // 这个动作下按钮是面板开合开关，不依赖选区，空选区也能先把面板调出来
      execBtn.disabled = false;
    }

    if (!isPanel) {
      this.metadataPanel?.close();
    }

    this._afterActionSync(action);
  }

  // --- Form ---------------------------------------------------------------

  onSubmit(event) {
    const form = this.config.form;
    if (!form || event.target !== form) {
      return;
    }
    const action = this._currentAction();

    // 元数据改写要先把面板展开，提交换成开/收面板
    if (this._isPanelAction(action) && !form.dataset.metadataSubmitting) {
      event.preventDefault();
      // 先把跨页选区同步进表单，面板的预览请求直接复用这份数据
      this._injectHiddenFields(form);
      if (this.metadataPanel?.isOpen()) {
        this.metadataPanel.close();
      } else {
        this.metadataPanel?.open();
      }
      return;
    }
    delete form.dataset.metadataSubmitting;

    this._injectHiddenFields(form);

    // 页面自己的动作（如高亮页的复制）在这里接管，不再提交表单
    if (this._handleLocalAction(form, action)) {
      event.preventDefault();
      return;
    }

    this._prepareSubmit(form, action);

    if (this.config.removing.has(action)) {
      // Clear the state only — the DOM still has to submit its checkbox values.
      this.selection = new Selection();
      this._clearState();
    }
  }

  _injectHiddenFields(form) {
    if (!form) {
      return;
    }

    form
      .querySelectorAll("input[data-bulk-sync]")
      .forEach((element) => element.remove());

    const { selectAcross, ids, excludeIds } = this.selection.payload(
      this.view.order,
    );
    if (selectAcross) {
      this._appendHiddenFields(form, this.config.excludeField, excludeIds);
    } else {
      this._appendHiddenFields(form, this.config.field, ids);
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

  // 页面钩子：动作切换后的附加 UI（开合颜色选择器、改确认文案等）
  _afterActionSync() {}

  // 页面钩子：选区变化后刷新依赖计数的 UI
  _onSelectionChange() {}

  // 页面钩子：某个动作不需要确认弹窗
  _skipConfirm() {
    return false;
  }

  // 页面钩子：提交前把动作写回表单的隐藏字段
  _prepareSubmit() {}

  // 页面钩子：返回 true 表示这个动作已在本地处理，不提交表单
  _handleLocalAction() {
    return false;
  }

  // --- State persistence --------------------------------------------------

  _loadState() {
    try {
      const raw = sessionStorage.getItem(this.config.keys.state);
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
      sessionStorage.setItem(
        this.config.keys.state,
        JSON.stringify(this.selection),
      );
    } catch (e) {
      // ignore
    }
  }

  _clearState() {
    sessionStorage.removeItem(this.config.keys.state);
    sessionStorage.removeItem(this.config.keys.action);
  }

  _persistAction(action) {
    try {
      if (action) {
        sessionStorage.setItem(this.config.keys.action, action);
      } else {
        sessionStorage.removeItem(this.config.keys.action);
      }
    } catch (e) {
      // ignore
    }
  }

  _loadAction() {
    try {
      return sessionStorage.getItem(this.config.keys.action);
    } catch (e) {
      return null;
    }
  }

  _persistActive(active) {
    try {
      if (active) {
        sessionStorage.setItem(this.config.keys.active, "1");
      } else {
        sessionStorage.removeItem(this.config.keys.active);
      }
    } catch (e) {
      // ignore
    }
  }

  _loadActive() {
    try {
      return sessionStorage.getItem(this.config.keys.active) === "1";
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

    // 先还原动作再进入批量模式：否则 _setActive 会拿服务端默认的第一项
    // 判定成"切走了"，把草稿清掉
    const select = this.element.querySelector("select[name='bulk_action']");
    const action = this._loadAction();
    if (action && select?.querySelector(`option[value="${action}"]`)) {
      select.value = action;
    }

    this._setActive(true);

    if (!this._isPanelAction(action) || !this.metadataPanel) {
      return;
    }

    // 预览要按选区求值，先把 hidden 字段补进表单
    if (this.config.form) {
      this._injectHiddenFields(this.config.form);
    }

    const draft = this.metadataPanel.loadDraft();
    if (draft) {
      this.metadataPanel.applyOps(draft);
    }
    this.metadataPanel.open();
  }
}

registerBehavior("ld-bulk-edit", BulkEdit);

export { BulkEdit };
