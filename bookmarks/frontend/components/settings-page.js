import { Behavior, registerBehavior } from "./runtime.js";
import { showToast } from "./toast.js";
import { gettext } from "../utils/i18n.js";
import { hashIconSvg } from "../utils/svg.js";
import {
  DEFAULT_ITEM_FORMAT,
  DEFAULT_SEPARATOR,
  renderCopyText,
  renderByAction,
} from "../utils/highlight-copy-format.js";
import {
  clearStoredSettingsDraft,
  getStoredSettingsDraft,
  getStoredSettingsPanelExpanded,
  getStoredSettingsScrollPosition,
  setStoredSettingsDraft,
  setStoredSettingsPanelExpanded,
  setStoredSettingsScrollPosition,
} from "../state/settings-preferences";
import Sortable from 'sortablejs';

// 快捷标签新增行的 HTML 模板
function quickTagRowTemplate() {
  return `
    <button type="button" class="settings-module-handle" aria-label="${gettext("Drag to reorder")}">
      <svg width="16" height="16" aria-hidden="true"><use href="#ld-icon-grip"></use></svg>
    </button>
    <div class="settings-qt-controls">
      <button type="button" class="settings-qt-chip-btn" data-qt-field="display_position" data-qt-value="direct" title="${gettext("Display position")}">${gettext("direct")}</button>
      <button type="button" class="settings-qt-chip-btn" data-qt-field="display_mode" data-qt-value="icon" title="${gettext("Display mode")}">${gettext("icon")}</button>
      <button type="button" class="settings-qt-delete-btn" title="${gettext("Delete")}">
        <svg width="14" height="14"><use href="#ld-icon-remove"></use></svg>
      </button>
    </div>
    <label class="form-switch settings-switch">
      <input type="checkbox" data-qt-enabled checked>
      <i class="form-icon"></i>
    </label>
    <div class="settings-qt-main">
      <button type="button" class="settings-qt-icon-btn" data-qt-icon="" title="${gettext("Pick icon")}">
        ${hashIconSvg(16, "settings-qt-icon-placeholder")}
      </button>
      <span class="settings-qt-label-view is-empty" title="${gettext("Click to edit name")}">${gettext("Name")}</span>
      <input type="text" class="settings-qt-label-input form-input" value="" placeholder="${gettext("Name")}">
    </div>
    <div class="settings-qt-sub">
      <div class="settings-qt-tags-wrapper">
        <span class="settings-qt-tags-view is-empty">${gettext("tag1 tag2")}</span>
      </div>
    </div>`;
}

// 依赖行显隐规则表：集中定义触发条件和更新函数，避免条件分散造成耦合。
const DEPENDENT_STATE_RULES = [
  {
    matches(form, hasField) {
      return (
        hasField("bookmark_description_display") ||
        hasField("bookmark_description_max_lines")
      );
    },
    apply(behavior, form) {
      behavior.updateBookmarkDescriptionState(form);
    },
  },
  {
    matches(form, hasField) {
      return hasField("enable_favicons");
    },
    apply(behavior, form) {
      behavior.updateFaviconState(form);
    },
  },
  {
    matches(form, hasField) {
      return hasField("enable_preview_images");
    },
    apply(behavior, form) {
      behavior.updatePreviewImagePlaceholderState(form);
    },
  },
  {
    matches(form, hasField) {
      return form.matches("[data-sharing-settings-form]") || hasField("sharing_mode");
    },
    apply(behavior, form) {
      behavior.updateSharingState(form);
    },
  },
  {
    matches(form, hasField) {
      return form.matches("[data-sidebar-modules-form]") || hasField("show_sidebar");
    },
    apply(behavior, form) {
      behavior.updateSidebarState(form);
    },
  },
  {
    matches(form, hasField) {
      return hasField("enable_random_button") || hasField("random_mode");
    },
    apply(behavior, form) {
      behavior.updateRandomModeState(form);
    },
  },
];

class SettingsPageBehavior extends Behavior {
  // 生命周期：收集节点、初始化状态、绑定事件并执行首屏同步。
  constructor(element) {
    super(element);

this.directoryLinks = Array.from(
      element.querySelectorAll(
        "[data-settings-directory] [data-settings-section-target]",
      ),
    );
    this.sections = this.directoryLinks
      .map((link) =>
        document.getElementById(link.dataset.settingsSectionTarget || ""),
      )
      .filter(Boolean);
    this.scrollContainer = window;
    this.sidebarModuleForms = Array.from(
      element.querySelectorAll("[data-sidebar-modules-form]"),
    );
    this.bookmarkToolbarForms = Array.from(
      element.querySelectorAll("[data-bookmark-toolbar-form]"),
    );
    this.directoryClickHandlers = new Map();
    this.panelToggleButtons = Array.from(
      element.querySelectorAll("[data-settings-panel-toggle]"),
    );
    this.segmentedControls = Array.from(
      element.querySelectorAll(".settings-segmented"),
    );
    this.inlineCheckboxGroups = Array.from(
      element.querySelectorAll(".settings-inline-checkbox-group"),
    );
    this.languageForm =
      element.querySelector("[data-settings-language-form]") || null;
    this.draftForms = Array.from(
      element.querySelectorAll("[data-settings-draft-form]"),
    );
    this.draftInputs = this.draftForms
      .map((form) => form.querySelector("[data-settings-draft-input]"))
      .filter((input) => input instanceof HTMLTextAreaElement);
    this.draftRestoreButtons = this.draftForms
      .map((form) => form.querySelector("[data-settings-restore-draft]"))
      .filter((button) => button instanceof HTMLButtonElement);
    this.nativeResizeState = null;
    this.nativeResizeFrame = null;
    this.helpPopovers = [];
    this.helpPopoverCounter = 0;
    this.lockedDirectorySectionId = null;
    this.directoryLockUntil = 0;
    this.directoryLockTimeoutMs = 1500;
    this.formSubmitStates = new WeakMap();
    this.formStatusTimeouts = new WeakMap();
    this.formDraftSyncFrames = new WeakMap();
    this.adaptiveLayoutFrame = null;

    this.onChange = this.onChange.bind(this);
    this.onSubmit = this.onSubmit.bind(this);
    this.onDraftInput = this.onDraftInput.bind(this);
    this.onDraftTextareaPointerDown = this.onDraftTextareaPointerDown.bind(this);
    this.onDocumentPointerUp = this.onDocumentPointerUp.bind(this);
    this.runNativeResizeStabilizer = this.runNativeResizeStabilizer.bind(this);
    this.onRestoreDraftClick = this.onRestoreDraftClick.bind(this);
    this.onPageHide = this.onPageHide.bind(this);
    this.onScroll = this.onScroll.bind(this);
    this.onPanelToggleClick = this.onPanelToggleClick.bind(this);
    this.onManualScrollIntent = this.onManualScrollIntent.bind(this);
    this.onHelpButtonClick = this.onHelpButtonClick.bind(this);
    this.onDocumentPointerDown = this.onDocumentPointerDown.bind(this);
    this.onDocumentKeyDown = this.onDocumentKeyDown.bind(this);
    this.onWindowResize = this.onWindowResize.bind(this);

    this.element.addEventListener("change", this.onChange);
    this.element.addEventListener("submit", this.onSubmit);
    this.scrollContainer.addEventListener("scroll", this.onScroll, {
      passive: true,
    });
    this.scrollContainer.addEventListener("wheel", this.onManualScrollIntent, {
      passive: true,
    });
    this.scrollContainer.addEventListener("pointerdown", this.onManualScrollIntent, {
      passive: true,
    });
    this.scrollContainer.addEventListener(
      "touchmove",
      this.onManualScrollIntent,
      {
        passive: true,
      },
    );
    document.addEventListener("pointerdown", this.onDocumentPointerDown);
    document.addEventListener("pointerup", this.onDocumentPointerUp);
    document.addEventListener("pointercancel", this.onDocumentPointerUp);
    document.addEventListener("keydown", this.onDocumentKeyDown);
    window.addEventListener("pagehide", this.onPageHide);
    window.addEventListener("resize", this.onWindowResize);

    // 侧边栏功能模块拖拽排序
    this.sortableInstances = [];
    this.sidebarModuleForms.forEach((form) => {
      const list = form.querySelector("[data-sidebar-modules-list]");
      if (!list) return;

      const sortable = Sortable.create(list, {
        handle: ".settings-module-handle", // 指定手柄触发，防止移动端滑动页面时误触
        animation: 150,
        ghostClass: "is-dragging",
        // fallbackOnBody: true,             // 解决某些容器内拖拽受限问题
        // forceFallback: true,
        swapThreshold: 0.65,
        
        // 拖拽结束后的回调
        onEnd: () => {
          this.syncSidebarModules(form);
          this.queueSubmit(form);
        },
      });

      this.sortableInstances.push(sortable);

      this.syncSidebarModules(form);  // 初始同步
    });

    // 书签工具栏拖拽排序（工具栏模块 + 各配置面板）
    this.bookmarkToolbarForms.forEach((form) => {
      // 工具栏模块排序
      const toolbarList = form.querySelector("[data-bookmark-toolbar-list]");
      if (toolbarList) {
        const sortable = Sortable.create(toolbarList, {
          handle: ".settings-module-handle",
          animation: 150,
          ghostClass: "is-dragging",
          swapThreshold: 0.65,
          onEnd: () => {
            this.syncBookmarkToolbarModules(form);
            this.syncToolbarConfigPanels(form);
            this.queueSubmit(form);
          },
        });
        this.sortableInstances.push(sortable);
      }
      this.applyAutoHideDateLock(form);
      this.syncBookmarkToolbarModules(form);
      this.syncToolbarConfigPanels(form);

      // 书签动作排序
      const actionsList = form.querySelector("[data-bookmark-actions-list]");
      if (actionsList) {
        const sortable = Sortable.create(actionsList, {
          handle: ".settings-module-handle",
          animation: 150,
          ghostClass: "is-dragging",
          swapThreshold: 0.65,
          onEnd: () => {
            this.syncBookmarkActions(form);
            this.queueSubmit(form);
          },
        });
        this.sortableInstances.push(sortable);
        this.syncBookmarkActions(form);
      }

      const statusesList = form.querySelector("[data-bookmark-statuses-list]");
      if (statusesList) {
        const sortable = Sortable.create(statusesList, {
          handle: ".settings-module-handle",
          animation: 150,
          ghostClass: "is-dragging",
          swapThreshold: 0.65,
          onEnd: () => {
            this.syncBookmarkStatuses(form);
            this.queueSubmit(form);
          },
        });
        this.sortableInstances.push(sortable);
        this.syncBookmarkStatuses(form);
      }

      const quickEditsList = form.querySelector("[data-bookmark-quick-edits-list]");
      if (quickEditsList) {
        const sortable = Sortable.create(quickEditsList, {
          handle: ".settings-module-handle",
          animation: 150,
          ghostClass: "is-dragging",
          swapThreshold: 0.65,
          onEnd: () => {
            this.syncBookmarkQuickEdits(form);
            this.queueSubmit(form);
          },
        });
        this.sortableInstances.push(sortable);
        this.syncBookmarkQuickEdits(form);
      }

      // 快捷标签拖拽排序
      const quickTagsList = form.querySelector("[data-bookmark-quick-tags-list]");
      if (quickTagsList) {
        const sortable = Sortable.create(quickTagsList, {
          handle: ".settings-module-handle",
          animation: 150,
          ghostClass: "is-dragging",
          swapThreshold: 0.65,
          onEnd: () => {
            this.syncBookmarkQuickTags(form);
            this.queueSubmit(form);
          },
        });
        this.sortableInstances.push(sortable);
        this.syncBookmarkQuickTags(form);
      }
    });

    this.initializeQuickTags();
    this.initializeHelpPopovers();
    this.initializeLanguageControls();
    this.initializeDirectoryLinks();
    this.initializePanelToggles();
    this.initializeDraftForms();
    this.initializeCopyFormatPreview();
    this.applyDependentState();
    this.queueAdaptiveControlLayoutsUpdate();
    this.restoreStoredScrollPosition();
    this.updateDirectoryState();
  }

  // 生命周期：解除事件与动画帧，回收运行期状态。
  destroy() {
    this.element.removeEventListener("change", this.onChange);
    this.element.removeEventListener("submit", this.onSubmit);
    if (this._onQuickTagClick) {
      this.element.removeEventListener("click", this._onQuickTagClick);
    }
    this.scrollContainer.removeEventListener("scroll", this.onScroll);
    this.scrollContainer.removeEventListener("wheel", this.onManualScrollIntent);
    this.scrollContainer.removeEventListener(
      "pointerdown",
      this.onManualScrollIntent,
    );
    this.scrollContainer.removeEventListener(
      "touchmove",
      this.onManualScrollIntent,
    );
    document.removeEventListener("pointerdown", this.onDocumentPointerDown);
    document.removeEventListener("pointerup", this.onDocumentPointerUp);
    document.removeEventListener("pointercancel", this.onDocumentPointerUp);
    document.removeEventListener("keydown", this.onDocumentKeyDown);
    window.removeEventListener("pagehide", this.onPageHide);
    window.removeEventListener("resize", this.onWindowResize);

    if (this.sortableInstances) {
      this.sortableInstances.forEach(instance => {
        if (typeof instance.destroy === 'function') {
          instance.destroy();
        }
      });
      this.sortableInstances = [];
    }

    this.directoryLinks.forEach((link) => {
      const handler = this.directoryClickHandlers.get(link);
      if (handler) {
        link.removeEventListener("click", handler);
      }
    });

    this.panelToggleButtons.forEach((button) => {
      button.removeEventListener("click", this.onPanelToggleClick);
    });

    this.helpPopovers.forEach((wrapper) => {
      const button = wrapper.querySelector("[data-settings-help-button]");
      if (button) {
        button.removeEventListener("click", this.onHelpButtonClick);
      }
    });
    this.draftRestoreButtons.forEach((button) => {
      button.removeEventListener("click", this.onRestoreDraftClick);
    });
    this.draftInputs.forEach((input) => {
      input.removeEventListener("input", this.onDraftInput);
      input.removeEventListener("pointerdown", this.onDraftTextareaPointerDown);
    });
    this.draftForms.forEach((form) => {
      const draftSyncFrame = this.formDraftSyncFrames.get(form);
      if (draftSyncFrame) {
        cancelAnimationFrame(draftSyncFrame);
      }
      this.formDraftSyncFrames.delete(form);
    });
    this.element.querySelectorAll("form").forEach((form) => {
      if (!(form instanceof HTMLFormElement)) {
        return;
      }
      this.clearFormStatusTimeout(form);
      this.formSubmitStates.delete(form);
    });

    if (this.scrollSaveFrame) {
      cancelAnimationFrame(this.scrollSaveFrame);
    }
    if (this.scrollRestoreFrame) {
      cancelAnimationFrame(this.scrollRestoreFrame);
    }
    if (this.panelToggleStabilizeFrame) {
      cancelAnimationFrame(this.panelToggleStabilizeFrame);
    }
    if (this.nativeResizeFrame) {
      cancelAnimationFrame(this.nativeResizeFrame);
      this.nativeResizeFrame = null;
    }
    if (this.adaptiveLayoutFrame) {
      cancelAnimationFrame(this.adaptiveLayoutFrame);
      this.adaptiveLayoutFrame = null;
    }
    this.nativeResizeState = null;

  }

  // 事件处理：草稿恢复按钮点击。
  onRestoreDraftClick(event) {
    event.preventDefault();
    event.stopPropagation();

    const button = event.currentTarget;
    const form = button.closest("form");
    if (!(form instanceof HTMLFormElement)) {
      return;
    }

    if (button.dataset.settingsRestoreState === "discard") {
      this.discardDraft(form);
      return;
    }

    this.restoreDraft(form);
  }

  onDraftInput(event) {
    const input = event.currentTarget;
    const form = input.closest("form");
    if (!(input instanceof HTMLTextAreaElement) || !(form instanceof HTMLFormElement)) {
      return;
    }

    this.clearFormStatus(form);
    this.syncDraftRestoreButton(form);
  }

  onDraftTextareaPointerDown(event) {
    const input = event.currentTarget;
    if (!(input instanceof HTMLTextAreaElement) || !(event instanceof PointerEvent)) {
      return;
    }

    if (event.button !== 0) {
      return;
    }

    if (event.pointerType && event.pointerType !== "mouse") {
      return;
    }

    if (!this.isTextareaResizeHandlePointerDown(event, input)) {
      return;
    }

    this.startNativeResizeStabilizer(input);
  }

  onDocumentPointerUp() {
    if (!this.nativeResizeState) {
      return;
    }

    // Let the stabilizer run a couple more frames, because browsers may apply
    // a final scroll compensation after pointerup.
    this.nativeResizeState.stopRequested = true;
    this.nativeResizeState.settleFrames = 2;
  }

  onPageHide() {
    this.snapshotDraftValues();
  }

  // 事件处理：表单字段变更后联动状态与保存策略。
  onChange(event) {
    // 图标选择器关闭时 popup.remove() 会触发搜索框的 change 事件；
    // selectIcon 中已通过 queueSubmit 提交，此处跳过避免重复 save。
    if (this._suppressChangeFromIconPicker) return;

    const form = event.target.closest("form");
    if (!form || !this.element.contains(form)) {
      return;
    }

    if (form === this.languageForm && this.handleLanguageChange(event.target)) {
      return;
    }

    const saveMode = form.dataset.settingsSaveMode;
    if (!saveMode) {
      return;
    }

    if (form.matches("[data-sidebar-modules-form]")) {
      this.syncSidebarModules(form);
    }

    if (form.matches("[data-bookmark-toolbar-form]")) {
      this.applyAutoHideDateLock(form);
      this.syncBookmarkToolbarModules(form);
      this.syncToolbarConfigPanels(form);
      this.syncBookmarkActions(form);
      this.syncBookmarkStatuses(form);
      this.syncBookmarkQuickEdits(form);
      this.syncBookmarkQuickTags(form);
    }

    this.applyDependentState(form);
    this.queueAdaptiveControlLayoutsUpdate();

    if (saveMode === "reload") {
      this.snapshotDraftValues();
      form.requestSubmit();
      return;
    }

    if (saveMode === "instant") {
      this.queueSubmit(form);
    }
  }

  onSubmit(event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }

    const saveMode = form.dataset.settingsSaveMode;
    const submitter = event.submitter;
    if (
      submitter instanceof HTMLElement &&
      submitter.hasAttribute("data-settings-bypass-ajax")
    ) {
      return;
    }

    if (!saveMode || saveMode === "reload") {
      return;
    }

    event.preventDefault();

    if (form.matches("[data-sidebar-modules-form]")) {
      this.syncSidebarModules(form);
    }

    if (form.matches("[data-bookmark-toolbar-form]")) {
      this.applyAutoHideDateLock(form);
      this.syncBookmarkToolbarModules(form);
      this.syncBookmarkActions(form);
      this.syncBookmarkStatuses(form);
      this.syncBookmarkQuickEdits(form);
      this.syncBookmarkQuickTags(form);
    }

    this.applyDependentState(form);
    this.queueSubmit(form, {
      showInlineStatus: saveMode === "explicit",
    });
  }

  onScroll() {
    this.queueScrollPositionSave();

    if (this.lockedDirectorySectionId) {
      const now = performance.now ? performance.now() : Date.now();
      if (now >= this.directoryLockUntil) {
        this.clearDirectoryLock();
      }
    }

    if (this.lockedDirectorySectionId) {
      this.setActiveDirectoryLink(this.lockedDirectorySectionId);
      return;
    }

    this.updateDirectoryState();
  }

  onManualScrollIntent() {
    this.clearDirectoryLock();
  }

  onHelpButtonClick(event) {
    const button = event.currentTarget;
    const wrapper = button.closest("[data-settings-help]");
    if (!wrapper) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();

    const isOpen = wrapper.dataset.settingsHelpOpen === "true";
    this.closeHelpPopovers(isOpen ? null : wrapper);
    if (!isOpen) {
      this.updateHelpPopoverPositions();
      this.setHelpPopoverOpen(wrapper, true);
    }
  }

  onDocumentPointerDown(event) {
    if (
      event.target instanceof Node &&
      this.helpPopovers.some((wrapper) => wrapper.contains(event.target))
    ) {
      return;
    }

    this.closeHelpPopovers();
  }

  onDocumentKeyDown(event) {
    if (event.key === "Escape") {
      this.closeHelpPopovers();
    }

    if (this.isScrollIntentKey(event)) {
      this.clearDirectoryLock();
    }
  }

  onWindowResize() {
    this.updateHelpPopoverPositions();
    this.queueAdaptiveControlLayoutsUpdate();
  }

  onPanelToggleClick(event) {
    const button = event.currentTarget;
    const panelId = button.getAttribute("aria-controls");
    if (!panelId) {
      return;
    }

    const panel = document.getElementById(panelId);
    if (!panel) {
      return;
    }

    const buttonTopBeforeToggle = button.getBoundingClientRect().top;
    const expanded = button.getAttribute("aria-expanded") === "true";
    panel.hidden = expanded;
    this.syncPanelToggle(button, !expanded);
    this.setStoredPanelExpanded(panelId, !expanded);
    this.stabilizePanelToggleScrollPosition(button, buttonTopBeforeToggle);
    this.queueAdaptiveControlLayoutsUpdate();
  }

  // 提交状态：通过 WeakMap 管理每个表单的请求队列，避免污染 DOM 节点。
  getFormSubmitState(form) {
    const existingState = this.formSubmitStates.get(form);
    if (existingState) {
      return existingState;
    }

    const nextState = {
      saveInFlight: false,
      savePending: false,
      pendingOptions: null,
    };
    this.formSubmitStates.set(form, nextState);
    return nextState;
  }

  queueSubmit(form, options = {}) {
    const submitState = this.getFormSubmitState(form);
    if (submitState.saveInFlight) {
      submitState.savePending = true;
      submitState.pendingOptions = options;
      return;
    }

    this.submitForm(form, options);
  }

  async submitForm(
    form,
    { showSuccessToast = false, showInlineStatus = false } = {},
  ) {
    const submitButton = form.querySelector("[type='submit']");
    const submitState = this.getFormSubmitState(form);
    this.snapshotDraftValues();
    if (showInlineStatus) {
      this.clearFormStatus(form);
    }
    submitState.saveInFlight = true;
    form.classList.add("is-saving");
    if (submitButton) {
      submitButton.disabled = true;
    }

    try {
      const response = await fetch(form.action, {
        method: form.method || "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
      });

      const payload = await response.json().catch(() => null);
      if (response.status === 422 && payload?.errors) {
        this.renderErrors(form, payload.errors);
        if (showInlineStatus) {
          this.setFormStatus(
            form,
            gettext("Please review the highlighted fields."),
            "error",
          );
        }
        return;
      }

      if (!response.ok || payload?.status !== "ok") {
        throw new Error(`Unexpected save response: ${response.status}`);
      }

      this.clearErrors(form);
      this.clearSidebarStateFromLocalStorage(form);
      if (this.isDraftForm(form)) {
        this.clearStoredDraft(form);
        this.setDraftRestored(form, false);
        this.setDraftBaseline(form);
        this.syncDraftRestoreButton(form);
      }
      if (form.dataset.settingsReloadAfterSave === "true") {
        window.location.reload();
        return;
      }

      if (showInlineStatus) {
        this.setFormStatus(form, gettext("Saved"), "success");
      } else if (showSuccessToast) {
        showToast(gettext("Saved"), { tone: "success" });
      }
    } catch (_error) {
      if (showInlineStatus) {
        this.setFormStatus(
          form,
          gettext("Couldn't save settings. Please try again."),
          "error",
        );
      } else {
        showToast(gettext("Couldn't save settings. Please try again."), { tone: "error" });
      }
    } finally {
      submitState.saveInFlight = false;
      form.classList.remove("is-saving");
      if (submitButton) {
        submitButton.disabled = false;
      }

      if (submitState.savePending) {
        const pendingOptions = submitState.pendingOptions || {};
        submitState.savePending = false;
        submitState.pendingOptions = null;
        this.submitForm(form, pendingOptions);
      }
    }
  }

  renderErrors(form, errors) {
    this.clearErrors(form);

    let hasUnhandledErrors = false;
    Object.entries(errors).forEach(([fieldName, fieldErrors]) => {
      const target = form.querySelector(
        `[data-field-error-for="${CSS.escape(fieldName)}"]`,
      );
      const message = fieldErrors.map((error) => error.message).join(" ");
      const field = form.querySelector(`[name="${CSS.escape(fieldName)}"]`);

      if (field) {
        field.classList.add("is-error");
      }

      if (!target) {
        hasUnhandledErrors = true;
        return;
      }

      target.textContent = message;
      target.classList.add("is-visible");
    });

    if (hasUnhandledErrors) {
      showToast(gettext("Please review the highlighted fields."), { tone: "error" });
    }
  }

  clearErrors(form) {
    form
      .querySelectorAll(".settings-field-errors.is-visible")
      .forEach((element) => {
        element.textContent = "";
        element.classList.remove("is-visible");
      });

    form
      .querySelectorAll("input.is-error, textarea.is-error, select.is-error")
      .forEach((field) => {
        field.classList.remove("is-error");
      });
  }

  getFormStatusElement(form) {
    const status = form.querySelector("[data-settings-form-status]");
    return status instanceof HTMLElement ? status : null;
  }

  // 行内状态：使用 WeakMap 跟踪 timeout，避免反复设置产生悬挂定时器。
  clearFormStatusTimeout(form) {
    const timeoutId = this.formStatusTimeouts.get(form);
    if (!timeoutId) {
      return;
    }

    clearTimeout(timeoutId);
    this.formStatusTimeouts.delete(form);
  }

  clearFormStatus(form) {
    const status = this.getFormStatusElement(form);
    this.clearFormStatusTimeout(form);
    if (!status) {
      return;
    }

    status.textContent = "";
    status.classList.remove("is-success", "is-error");
  }

  setFormStatus(form, message, tone) {
    const status = this.getFormStatusElement(form);
    this.clearFormStatusTimeout(form);
    if (!status) {
      return;
    }

    status.textContent = message;
    status.classList.toggle("is-success", tone === "success");
    status.classList.toggle("is-error", tone === "error");

    if (tone === "success") {
      const timeoutId = window.setTimeout(() => {
        this.clearFormStatus(form);
      }, 3000);
      this.formStatusTimeouts.set(form, timeoutId);
    }
  }

  // 依赖联动：按规则表批量匹配并更新，减少表单之间的显式耦合。
  applyDependentState(form = null) {
    if (form instanceof HTMLFormElement) {
      const hasField = (fieldName) => {
        if (!fieldName) {
          return false;
        }

        if (form.elements?.namedItem(fieldName)) {
          return true;
        }
        return Boolean(form.querySelector(`[name="${CSS.escape(fieldName)}"]`));
      };
      DEPENDENT_STATE_RULES.forEach((rule) => {
        if (rule.matches(form, hasField)) {
          rule.apply(this, form);
        }
      });
      return;
    }

    DEPENDENT_STATE_RULES.forEach((rule) => {
      rule.apply(this);
    });
  }

  updateBookmarkDescriptionState(form = null) {
    if (!(form instanceof HTMLFormElement)) {
      form = this.element.querySelector(
        'form input[name="bookmark_description_display"]',
      )?.form;
    }
    if (!form) {
      return;
    }

    const row = form.querySelector(
      '[data-setting-row="bookmark_description_max_lines"]',
    );
    const input = row?.querySelector('[name="bookmark_description_max_lines"]');
    const value = this.getCheckedRadioValue(form, "bookmark_description_display");
    const visible = value === "separate";

    this.setRowVisibility(row, visible);
    if (input) {
      input.disabled = !visible;
    }
  }

  updateSharingState(form = null) {
    if (!(form instanceof HTMLFormElement)) {
      form = this.element.querySelector("[data-sharing-settings-form]");
    }
    if (!form) {
      return;
    }

    const row = form.querySelector('[data-setting-row="default_mark_shared"]');
    const input = row?.querySelector('[name="default_mark_shared"]');
    const mode = this.getCheckedRadioValue(form, "sharing_mode");
    const visible = mode !== "disabled";

    this.setRowVisibility(row, visible);
    if (input) {
      input.disabled = !visible;
      if (!visible) {
        input.checked = false;
      }
    }
  }

  updateFaviconState(form = null) {
    if (!(form instanceof HTMLFormElement)) {
      form = this.element.querySelector(
        'form input[name="enable_favicons"]',
      )?.form;
    }
    if (!form) {
      return;
    }

    const row = form.querySelector('[data-setting-row="refresh_favicons"]');
    const enabled = Boolean(form.querySelector('[name="enable_favicons"]')?.checked);
    this.setRowVisibility(row, enabled);
  }

  updatePreviewImagePlaceholderState(form = null) {
    if (!(form instanceof HTMLFormElement)) {
      form = this.element.querySelector(
        'form input[name="enable_preview_images"]',
      )?.form;
    }
    if (!form) {
      return;
    }

    const row = form.querySelector('[data-setting-row="preview_image_placeholders"]');
    const enabled = Boolean(form.querySelector('[name="enable_preview_images"]')?.checked);
    this.setRowVisibility(row, enabled);
  }

  updateSidebarState(form = null) {
    const sidebarForm = this.element.querySelector("[data-sidebar-modules-form]");
    if (!(sidebarForm instanceof HTMLFormElement)) {
      return;
    }

    const row = sidebarForm.querySelector('[data-setting-row="sticky_side_panel"]');
    const modulesRow = sidebarForm.querySelector('[data-setting-row="sidebar_modules"]');
    const input = row?.querySelector('[name="sticky_side_panel"]');
    const showSidebarInput = sidebarForm.querySelector('[name="show_sidebar"]');
    const showSidebar =
      showSidebarInput instanceof HTMLInputElement
        ? showSidebarInput.checked
        : true;
    const visible = showSidebar;
    this.setRowVisibility(row, visible);
    this.setRowVisibility(modulesRow, true);
    // Keep the stored "Sidebar follows" value unchanged when the sidebar is hidden.
    if (input) {
      input.disabled = false;
    }

    // Modules remain editable even when sidebar is collapsed,
    // because the sidebar can still be opened via the drawer button.

  }

  updateRandomModeState(form = null) {
    if (!(form instanceof HTMLFormElement)) {
      form = this.element.querySelector(
        'form input[name="random_mode"]',
      )?.form;
    }
    if (!form) {
      return;
    }

    const enabled = Boolean(form.querySelector('[name="enable_random_button"]')?.checked);
    const modeRow = form.querySelector('[data-setting-row="random_mode"]');
    const targetRow = form.querySelector('[data-setting-row="random_target"]');
    const mode = this.getCheckedRadioValue(form, "random_mode");

    this.setRowVisibility(modeRow, enabled);
    this.setRowVisibility(targetRow, enabled && mode === "single");
  }

  // 设置页保存后，删除 localStorage 中的 sidebar 状态，
  // 让下次页面加载使用服务器设置（data-sidebar-default）。
  // 这样 toolbar toggle 的 localStorage 不会与设置页冲突。
  clearSidebarStateFromLocalStorage(form) {
    if (!form.matches("[data-sidebar-modules-form]")) return;
    try {
      localStorage.removeItem("ld:sidebar-state:bookmarks");
      localStorage.removeItem("ld:sidebar-state:highlights");
    } catch {}
  }

  // 语言设置：主选项与”其他语言”下拉的联动提交。
  handleLanguageChange(target) {
    if (!(target instanceof HTMLElement) || !this.languageForm) {
      return false;
    }

    const hiddenInput = this.languageForm.querySelector(
      "[data-settings-language-input]",
    );
    const otherContainer = this.languageForm.querySelector(
      "[data-settings-language-other]",
    );
    const otherSelect = this.languageForm.querySelector(
      "[data-settings-language-select]",
    );

    if (
      target.matches(
        '[data-settings-language-form] input[name="language_selector"]',
      )
    ) {
      if (target.value === "__other__") {
        this.setLanguageOtherVisibility(otherContainer, true);
        if (otherSelect instanceof HTMLSelectElement) {
          otherSelect.focus();
        }
        return true;
      }

      if (hiddenInput instanceof HTMLInputElement) {
        hiddenInput.value = target.value;
      }
      this.setLanguageOtherVisibility(otherContainer, false);
      this.languageForm.requestSubmit();
      return true;
    }

    if (target === otherSelect) {
      if (
        hiddenInput instanceof HTMLInputElement &&
        otherSelect instanceof HTMLSelectElement &&
        otherSelect.value
      ) {
        hiddenInput.value = otherSelect.value;
        this.languageForm.requestSubmit();
      }
      return true;
    }

    return false;
  }

  initializeLanguageControls() {
    if (!this.languageForm) {
      return;
    }

    const checkedValue = this.getCheckedRadioValue(
      this.languageForm,
      "language_selector",
    );
    const otherContainer = this.languageForm.querySelector(
      "[data-settings-language-other]",
    );
    this.setLanguageOtherVisibility(otherContainer, checkedValue === "__other__");
  }

  // 草稿管理：初始化草稿按钮状态与输入监听。
  initializeDraftForms() {
    this.draftForms.forEach((form) => {
      this.setDraftRestored(form, false);
      this.setDraftBaseline(form);
      this.syncDraftRestoreButton(form);
      const draftSyncFrame = requestAnimationFrame(() => {
        this.formDraftSyncFrames.delete(form);
        this.syncDraftRestoreButton(form);
      });
      this.formDraftSyncFrames.set(form, draftSyncFrame);
    });
    this.draftInputs.forEach((input) => {
      input.addEventListener("input", this.onDraftInput);
      input.addEventListener("pointerdown", this.onDraftTextareaPointerDown);
    });
    this.draftRestoreButtons.forEach((button) => {
      button.addEventListener("click", this.onRestoreDraftClick);
    });
  }

  // --- Highlight copy format preview ---
  initializeCopyFormatPreview() {
    const itemInput = this.element.querySelector("[data-hl-copy-preview-input]");
    const itemPreview = this.element.querySelector("[data-hl-copy-preview]");
    const sepInput = this.element.querySelector("[data-hl-sep-preview-input]");
    const sepPreview = this.element.querySelector("[data-hl-sep-preview]");
    if (!itemInput || !itemPreview) return;

    const SAMPLE_HIGHLIGHT = "The quick brown fox jumps over the lazy dog";
    const SAMPLE_NOTE = "This is an important passage worth remembering";

    const warn = (msg) => {
      const span = document.createElement("span");
      span.className = "hl-copy-warn";
      span.textContent = msg;
      return span;
    };

    const renderHighlightOnly = (fmt) => renderByAction(fmt, SAMPLE_HIGHLIGHT, "", "highlight");

    const renderNoteOnly = (fmt) => renderByAction(fmt, "", SAMPLE_NOTE, "note");

    const updateItemPreview = () => {
      const fmt = itemInput.value || DEFAULT_ITEM_FORMAT;
      const hasHL = fmt.includes("${highlight}");
      const hasAnn = fmt.includes("${annotation}");

      const hlEl = itemPreview.querySelector('[data-preview="highlight"]');
      if (hlEl) {
        hlEl.textContent = "";
        if (hasHL) hlEl.appendChild(document.createTextNode(renderHighlightOnly(fmt)));
        else hlEl.appendChild(warn(gettext("${highlight} not found")));
      }

      const noteEl = itemPreview.querySelector('[data-preview="note"]');
      if (noteEl) {
        noteEl.textContent = "";
        if (hasAnn) noteEl.appendChild(document.createTextNode(renderNoteOnly(fmt)));
        else noteEl.appendChild(warn(gettext("${annotation} not found")));
      }

      const bothEl = itemPreview.querySelector('[data-preview="both"]');
      if (bothEl) {
        bothEl.textContent = renderCopyText(fmt, SAMPLE_HIGHLIGHT, SAMPLE_NOTE);
        if (!hasHL || !hasAnn) {
          const missing = [];
          if (!hasHL) missing.push("${highlight}");
          if (!hasAnn) missing.push("${annotation}");
          bothEl.appendChild(document.createTextNode("\n"));
          bothEl.appendChild(warn(missing.join(", ") + " " + gettext("not found")));
        }
      }
    };

    const updateSepPreview = () => {
      if (!sepInput || !sepPreview) return;
      const fmt = itemInput.value || DEFAULT_ITEM_FORMAT;
      const sep = sepInput.value || DEFAULT_SEPARATOR;
      const item1 = renderCopyText(fmt, SAMPLE_HIGHLIGHT, SAMPLE_NOTE);
      const item2 = renderCopyText(fmt, "Second highlight text", "Another annotation");
      const batchEl = sepPreview.querySelector('[data-preview="batch"]');
      if (batchEl) batchEl.textContent = item1 + sep + item2;
    };

    const updateAll = () => { updateItemPreview(); updateSepPreview(); };

    // Initial render
    updateAll();

    // Focus-gated listeners
    const setupFocusPreview = (input, updateFn) => {
      if (!input) return;
      let timer = null;
      const debounced = () => { clearTimeout(timer); timer = setTimeout(updateFn, 500); };
      input.addEventListener("focus", () => { updateFn(); input.addEventListener("input", debounced); });
      input.addEventListener("blur", () => { clearTimeout(timer); input.removeEventListener("input", debounced); });
      input.addEventListener("input", (e) => { if (e.isTrusted === false) updateFn(); });
    };
    setupFocusPreview(itemInput, updateAll);
    setupFocusPreview(sepInput, updateSepPreview);
  }

  // 草稿管理：文本框原生拖拽拉伸时，稳定页面滚动位置。
  isTextareaResizeHandlePointerDown(event, input) {
    const rect = input.getBoundingClientRect();
    const handleSize = Math.max(12, Math.min(24, rect.width, rect.height));
    return (
      event.clientX >= rect.right - handleSize &&
      event.clientY >= rect.bottom - handleSize
    );
  }

  startNativeResizeStabilizer(input) {
    this.nativeResizeState = {
      input,
      anchorTop: input.getBoundingClientRect().top,
      stopRequested: false,
      settleFrames: 0,
    };

    if (!this.nativeResizeFrame) {
      this.nativeResizeFrame = requestAnimationFrame(this.runNativeResizeStabilizer);
    }
  }

  stopNativeResizeStabilizer() {
    if (this.nativeResizeFrame) {
      cancelAnimationFrame(this.nativeResizeFrame);
      this.nativeResizeFrame = null;
    }

    this.nativeResizeState = null;
  }

  runNativeResizeStabilizer() {
    const state = this.nativeResizeState;
    if (!state || !(state.input instanceof HTMLTextAreaElement) || !state.input.isConnected) {
      this.stopNativeResizeStabilizer();
      return;
    }

    const topDelta = state.input.getBoundingClientRect().top - state.anchorTop;
    if (Math.abs(topDelta) > 0.5) {
      this.setScrollTop(this.getScrollMetrics().scrollTop + topDelta);
    }

    if (state.stopRequested) {
      if (state.settleFrames <= 0) {
        this.stopNativeResizeStabilizer();
        return;
      }

      state.settleFrames -= 1;
    }

    this.nativeResizeFrame = requestAnimationFrame(this.runNativeResizeStabilizer);
  }

  // 草稿管理：持久化、恢复与按钮状态切换。
  snapshotDraftValues() {
    this.draftForms.forEach((form) => {
      const input = this.getDraftInput(form);
      if (!input) {
        return;
      }

      const baseline = this.getDraftBaseline(input);
      const storedDraft = this.getStoredDraft(form);
      if (input.value !== baseline) {
        this.setStoredDraft(form, input.value);
        return;
      }

      if (storedDraft !== null && storedDraft !== baseline) {
        return;
      }

      this.clearStoredDraft(form);
    });
  }

  isDraftForm(form) {
    return form instanceof HTMLFormElement && form.hasAttribute("data-settings-draft-form");
  }

  getDraftInput(form) {
    const input = form.querySelector("[data-settings-draft-input]");
    return input instanceof HTMLTextAreaElement ? input : null;
  }

  getDraftRestoreButton(form) {
    const button = form.querySelector("[data-settings-restore-draft]");
    return button instanceof HTMLButtonElement ? button : null;
  }

  setDraftRestoreButtonVisibility(button, visible) {
    button.hidden = !visible;
    button.setAttribute("aria-hidden", visible ? "false" : "true");
    if (visible) {
      button.style.removeProperty("display");
      return;
    }

    button.style.setProperty("display", "none", "important");
  }

  getDraftRestoreButtonIcon(button) {
    const icon = button.querySelector("[data-settings-draft-button-icon]");
    return icon instanceof HTMLElement ? icon : null;
  }

  getDraftRestoreButtonLabel(button) {
    const label = button.querySelector("[data-settings-draft-button-label]");
    return label instanceof HTMLElement ? label : null;
  }

  getDraftFormId(form) {
    const input = form.querySelector('input[name="form_id"]');
    return input instanceof HTMLInputElement ? input.value : "";
  }

  getDraftFormIdOrEmpty(form) {
    const formId = this.getDraftFormId(form);
    return formId || "";
  }

  getStoredDraft(form) {
    return getStoredSettingsDraft(this.getDraftFormIdOrEmpty(form));
  }

  setStoredDraft(form, value) {
    setStoredSettingsDraft(this.getDraftFormIdOrEmpty(form), value);
  }

  clearStoredDraft(form) {
    clearStoredSettingsDraft(this.getDraftFormIdOrEmpty(form));
  }

  setDraftRestored(form, restored) {
    form.dataset.settingsDraftRestored = restored ? "true" : "false";
  }

  isDraftRestored(form) {
    return form.dataset.settingsDraftRestored === "true";
  }

  setDraftBaseline(form) {
    const input = this.getDraftInput(form);
    if (!input) {
      return;
    }

    input.defaultValue = input.value;
    input.dataset.settingsDraftBaseline = input.defaultValue;
  }

  getDraftBaseline(input) {
    return input.dataset.settingsDraftBaseline ?? input.defaultValue ?? "";
  }

  restoreDraft(form) {
    const input = this.getDraftInput(form);
    const draft = this.getStoredDraft(form);
    if (!input || draft === null) {
      return;
    }

    input.value = draft;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
    input.setSelectionRange(draft.length, draft.length);
    this.setDraftRestored(form, true);
    this.clearFormStatus(form);
    this.clearErrors(form);
    this.syncDraftRestoreButton(form);
  }

  discardDraft(form) {
    const input = this.getDraftInput(form);
    if (!input) {
      return;
    }

    const baseline = this.getDraftBaseline(input);
    input.value = baseline;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
    input.setSelectionRange(baseline.length, baseline.length);
    this.clearStoredDraft(form);
    this.setDraftRestored(form, false);
    this.clearFormStatus(form);
    this.clearErrors(form);
    this.syncDraftRestoreButton(form);
  }

  setDraftRestoreButtonState(button, state) {
    const label = this.getDraftRestoreButtonLabel(button);
    const icon = this.getDraftRestoreButtonIcon(button);
    const restoreLabel =
      button.dataset.settingsRestoreLabel || gettext("Restore draft");
    const discardLabel =
      button.dataset.settingsDiscardLabel || gettext("Discard draft");

    button.dataset.settingsRestoreState = state;
    button.classList.toggle("btn-error", state === "discard");
    button.classList.toggle("is-discard", state === "discard");
    if (label) {
      label.textContent = state === "discard" ? discardLabel : restoreLabel;
    }
    if (icon) {
      icon.hidden = state !== "discard";
    }
  }

  syncDraftRestoreButton(form) {
    const input = this.getDraftInput(form);
    const button = this.getDraftRestoreButton(form);
    if (!input || !button) {
      return;
    }

    const baseline = this.getDraftBaseline(input);
    const currentValue = input.value;
    let draft = this.getStoredDraft(form);
    if (draft !== null && draft === baseline) {
      this.clearStoredDraft(form);
      draft = null;
    }

    if (draft === null) {
      this.setDraftRestoreButtonVisibility(button, false);
      this.setDraftRestored(form, false);
      this.setDraftRestoreButtonState(button, "restore");
      return;
    }

    if (this.isDraftRestored(form) || currentValue === draft) {
      this.setDraftRestored(form, true);
      this.setDraftRestoreButtonVisibility(button, true);
      this.setDraftRestoreButtonState(button, "discard");
      return;
    }

    if (currentValue !== baseline) {
      this.setDraftRestoreButtonVisibility(button, false);
      this.setDraftRestoreButtonState(button, "restore");
      return;
    }

    this.setDraftRestoreButtonVisibility(button, true);
    this.setDraftRestoreButtonState(
      button,
      "restore",
    );
  }

  // 通用控件显隐：统一处理 aria 与 hidden，避免重复逻辑。
  setLanguageOtherVisibility(container, visible) {
    if (!(container instanceof HTMLElement)) {
      return;
    }

    container.classList.toggle("is-hidden", !visible);
    container.toggleAttribute("hidden", !visible);
    container.setAttribute("aria-hidden", visible ? "false" : "true");
  }

  setRowVisibility(row, visible) {
    if (!row) {
      return;
    }

    row.classList.toggle("is-hidden", !visible);
    row.toggleAttribute("hidden", !visible);
    row.setAttribute("aria-hidden", visible ? "false" : "true");
  }

  getCheckedRadioValue(form, fieldName) {
    return (
      form.querySelector(`[name="${CSS.escape(fieldName)}"]:checked`)?.value || ""
    );
  }

  // 侧栏模块：将可拖拽顺序和启用状态序列化为隐藏字段。
  syncSidebarModules(form) {
    // Scope to THIS form — avoids cross-contamination when both
    // bookmarks and highlights sidebar forms exist on the same page.
    const hiddenInput = form.querySelector('input[name="sidebar_modules"], input[name="highlights_sidebar_modules"]');
    const items = Array.from(
      form.querySelectorAll("[data-sidebar-modules-list] .settings-module-item"),
    ).map((item) => ({
      key: item.dataset.moduleKey,
      enabled: Boolean(item.querySelector("[data-module-enabled]")?.checked),
    }));

    if (hiddenInput) {
      hiddenInput.value = JSON.stringify(items);
    }
  }

  // 工具栏模块：将可拖拽顺序和启用状态序列化为隐藏字段。
  syncBookmarkToolbarModules(form) {
    const hiddenInput = form.querySelector('[name="bookmark_toolbar_modules"]');
    const items = Array.from(
      form.querySelectorAll("[data-bookmark-toolbar-list] .settings-module-item"),
    ).map((item) => ({
      key: item.dataset.toolbarModuleKey,
      enabled: Boolean(item.querySelector("[data-toolbar-module-enabled]")?.checked),
    }));

    if (hiddenInput) {
      hiddenInput.value = JSON.stringify(items);
    }
  }

  // Auto-hide toolbar: force-enable "date" module when auto-hide is on.
  applyAutoHideDateLock(form) {
    const autoHide = form.querySelector("[data-toolbar-auto-hide]");
    if (!autoHide) return;
    const dateToggle = form.querySelector(
      '[data-toolbar-module-key="date"] [data-toolbar-module-enabled]',
    );
    if (!dateToggle) return;

    if (autoHide.checked) {
      dateToggle.checked = true;
      dateToggle.disabled = true;
      dateToggle.closest(".settings-switch")?.classList.add("is-disabled");
    } else {
      dateToggle.disabled = false;
      dateToggle.closest(".settings-switch")?.classList.remove("is-disabled");
    }
  }

  // 配置面板：跟随工具栏模块的顺序和启用状态。
  syncToolbarConfigPanels(form) {
    const modules = Array.from(
      form.querySelectorAll("[data-bookmark-toolbar-list] .settings-module-item"),
    ).map((el) => ({
      key: el.dataset.toolbarModuleKey,
      enabled: Boolean(el.querySelector("[data-toolbar-module-enabled]")?.checked),
    }));

    const configCard = form.querySelector("[data-toolbar-config-card]");
    if (!configCard) return;

    // 按工具栏顺序设置各配置面板的显示/隐藏。
    // 仅当面板顺序与工具栏模块顺序不一致时才执行 appendChild 重排，
    // 避免每次切换开关都无条件重排 DOM 导致视口跳动。
    const orderedPanels = modules
      .map((mod) =>
        configCard.querySelector(`[data-toolbar-config-panel="${mod.key}"]`),
      )
      .filter(Boolean);

    modules.forEach((mod, i) => {
      if (orderedPanels[i]) {
        orderedPanels[i].style.display = mod.enabled ? "" : "none";
      }
    });

    const currentPanels = Array.from(
      configCard.querySelectorAll("[data-toolbar-config-panel]"),
    );
    const needReorder = orderedPanels.some(
      (panel, i) => panel !== currentPanels[i],
    );
    if (needReorder) {
      orderedPanels.forEach((panel) => configCard.appendChild(panel));
    }

    // 日期模块禁用时，同步设置 bookmark_date_display 为 hidden
    const dateMod = modules.find((m) => m.key === "date");
    if (dateMod) {
      const hiddenRadio = form.querySelector(
        '[name="bookmark_date_display"][value="hidden"]',
      );
      const currentChecked = form.querySelector(
        '[name="bookmark_date_display"]:checked',
      );
      if (!dateMod.enabled && hiddenRadio) {
        hiddenRadio.checked = true;
      } else if (dateMod.enabled && currentChecked?.value === "hidden") {
        // 重新启用时恢复为 relative
        const relative = form.querySelector(
          '[name="bookmark_date_display"][value="relative"]',
        );
        if (relative) relative.checked = true;
      }
    }

    // 全部禁用时隐藏整个配置卡片
    const anyEnabled = modules.some((m) => m.enabled);
    configCard.style.display = anyEnabled ? "" : "none";
  }

  // 书签动作：将可拖拽顺序和启用状态序列化为隐藏字段。
  syncBookmarkActions(form) {
    const hiddenInput = form.querySelector('[name="bookmark_actions"]');
    const items = Array.from(
      form.querySelectorAll("[data-bookmark-actions-list] .settings-module-item"),
    ).map((item) => ({
      key: item.dataset.actionKey,
      enabled: Boolean(item.querySelector("[data-action-enabled]")?.checked),
    }));

    if (hiddenInput) {
      hiddenInput.value = JSON.stringify(items);
    }
  }

  // 书签状态：将可拖拽顺序和启用状态序列化为隐藏字段。
  syncBookmarkStatuses(form) {
    const hiddenInput = form.querySelector('[name="bookmark_statuses"]');
    const items = Array.from(
      form.querySelectorAll("[data-bookmark-statuses-list] .settings-module-item"),
    ).map((item) => ({
      key: item.dataset.statusKey,
      enabled: Boolean(item.querySelector("[data-status-enabled]")?.checked),
    }));

    if (hiddenInput) {
      hiddenInput.value = JSON.stringify(items);
    }
  }

  // 快捷编辑：将可拖拽顺序和启用状态序列化为隐藏字段。
  syncBookmarkQuickEdits(form) {
    const hiddenInput = form.querySelector('[name="bookmark_quick_edits"]');
    const items = Array.from(
      form.querySelectorAll("[data-bookmark-quick-edits-list] .settings-module-item"),
    ).map((item) => ({
      key: item.dataset.quickEditKey,
      enabled: Boolean(item.querySelector("[data-quick-edit-enabled]")?.checked),
    }));

    if (hiddenInput) {
      hiddenInput.value = JSON.stringify(items);
    }
  }

  // 快捷标签：序列化所有 quick tag 条目到隐藏字段。
  syncBookmarkQuickTags(form) {
    const hiddenInput = form.querySelector('[name="bookmark_quick_tags"]');
    if (!hiddenInput) return;

    const items = Array.from(
      form.querySelectorAll("[data-bookmark-quick-tags-list] .settings-qt-row"),
    ).map((row) => this._serializeQuickTag(row));

    hiddenInput.value = JSON.stringify(items);
  }

  _serializeQuickTag(row) {
    const tagNames = Array.from(row.querySelectorAll(".settings-qt-tags-view .qt-tag"))
      .map((chip) => chip.textContent.trim())
      .filter(Boolean);
    const tag_name = tagNames.join(" ");
    const labelEl = row.querySelector(".settings-qt-label-view");
    const label = labelEl ? labelEl.textContent.trim() : tag_name;
    const iconBtn = row.querySelector("[data-qt-icon]");
    const icon_name = iconBtn ? (iconBtn.dataset.qtIcon || "") : "";
    const posBtn = row.querySelector('[data-qt-field="display_position"]');
    const display_position = posBtn ? (posBtn.dataset.qtValue || "direct") : "direct";
    const modeBtn = row.querySelector('[data-qt-field="display_mode"]');
    const display_mode = modeBtn ? (modeBtn.dataset.qtValue || "icon") : "icon";
    const enabled = Boolean(row.querySelector("[data-qt-enabled]")?.checked);

    return {
      tag_name,
      tag_names: tagNames,
      label,
      short_label: tagNames[0] ? tagNames[0][0] : "",
      icon_name,
      display_position,
      display_mode,
      enabled,
    };
  }


  _renderQuickTagChips(tagsView, tagNames) {
    tagsView.replaceChildren();
    if (tagNames.length === 0) {
      tagsView.textContent = gettext("tag1 tag2");
      tagsView.classList.add("is-empty");
    } else {
      tagNames.forEach((tag) => {
        const el = document.createElement("span");
        el.classList.add("qt-tag");
        el.textContent = tag;
        tagsView.appendChild(el);
      });
      tagsView.classList.remove("is-empty");
    }
  }

  // 快捷标签：初始化按钮和事件委托。
  initializeQuickTags() {
    this.element.querySelectorAll("[data-qt-add]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const form = btn.closest("form");
        const list = form?.querySelector("[data-bookmark-quick-tags-list]");
        if (!list) return;
        const row = this._createQuickTagRow(list.children.length);
        list.appendChild(row);
        this.syncBookmarkQuickTags(form);
        this.queueSubmit(form);
      });
    });

    this._onQuickTagClick = this._onQuickTagClick.bind(this);
    this.element.addEventListener("click", this._onQuickTagClick);
  }

  _onQuickTagClick(event) {
    const target = event.target;
    if (!target || !target.closest) return;

    // tags 编辑（优先匹配，因为 .qt-tag 在 tags-view 内）
    const tagsView = target.closest(".settings-qt-tags-view");
    if (tagsView) {
      const wrapper = tagsView.closest(".settings-qt-tags-wrapper");
      if (!wrapper || wrapper.classList.contains("is-editing")) return;

      // 读取当前标签
      const currentTags = Array.from(tagsView.querySelectorAll(".qt-tag"))
        .map((el) => el.textContent.trim());

      // 切换到编辑态
      wrapper.classList.add("is-editing");

      // 创建 autocomplete（与 _startEditTags 相同模式：插入为 tagsView 的兄弟节点）
      const autocomplete = document.createElement("ld-tag-autocomplete");
      autocomplete.setAttribute("input-value", currentTags.join(" "));
      autocomplete.setAttribute("input-placeholder", gettext("tag1 tag2"));
      tagsView.parentNode.insertBefore(autocomplete, tagsView.nextSibling);

      let input = null;

      const commit = () => {
        const newTags = input
          ? (input.value || "").split(/\s+/).map((t) => t.trim()).filter(Boolean)
          : currentTags;

        autocomplete.remove();
        wrapper.classList.remove("is-editing");

        // 更新显示态
        this._renderQuickTagChips(tagsView, newTags);

        const form = wrapper.closest("form");
        this.syncBookmarkQuickTags(form);
        this.queueSubmit(form);
      };

      const cancel = () => {
        autocomplete.remove();
        wrapper.classList.remove("is-editing");
      };

      const onReady = () => {
        input = autocomplete.querySelector("input");
        if (!input) return;
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);

        input.addEventListener("blur", () => {
          setTimeout(() => {
            if (autocomplete.contains(document.activeElement)) return;
            commit();
          }, 150);
        });

        input.addEventListener("keydown", (e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            cancel();
          }
        });
      };

      if (autocomplete.updateComplete) {
        autocomplete.updateComplete.then(onReady);
      } else {
        requestAnimationFrame(onReady);
      }
      return;
    }

    const iconBtn = target.closest(".settings-qt-icon-btn");
    if (iconBtn) {
      event.preventDefault();
      this._openIconPicker(iconBtn);
      return;
    }

    const labelView = target.closest(".settings-qt-label-view");
    if (labelView) {
      event.preventDefault();
      const row = labelView.closest(".settings-qt-row");
      const input = row.querySelector(".settings-qt-label-input");
      if (!input) return;
      row.classList.add("is-label-editing");
      input.focus();
      input.select();
      const commit = () => {
        const val = input.value.trim();
        labelView.textContent = val || gettext("Name");
        labelView.classList.toggle("is-empty", !val);
        row.classList.remove("is-label-editing");
        const form = row.closest("form");
        this.syncBookmarkQuickTags(form);
        this.queueSubmit(form);
      };
      input.onblur = commit;
      input.onkeydown = (e) => {
        if (e.key === "Enter") { e.preventDefault(); input.blur(); }
        if (e.key === "Escape") { e.preventDefault(); input.value = labelView.textContent; input.blur(); }
      };
      return;
    }

    const chipBtn = target.closest(".settings-qt-chip-btn");
    if (chipBtn) {
      event.preventDefault();
      const field = chipBtn.dataset.qtField;
      const current = chipBtn.dataset.qtValue;
      const labels = { direct: gettext("direct"), submenu: gettext("submenu"), icon: gettext("icon"), text: gettext("text") };
      if (field === "display_position") {
        chipBtn.dataset.qtValue = current === "direct" ? "submenu" : "direct";
        chipBtn.textContent = labels[chipBtn.dataset.qtValue];
      } else if (field === "display_mode") {
        chipBtn.dataset.qtValue = current === "icon" ? "text" : "icon";
        chipBtn.textContent = labels[chipBtn.dataset.qtValue];
      }
      const form = chipBtn.closest("form");
      this.syncBookmarkQuickTags(form);
      this.queueSubmit(form);
      return;
    }

    const deleteBtn = target.closest(".settings-qt-delete-btn");
    if (deleteBtn) {
      event.preventDefault();
      event.stopPropagation();
      Behavior.interacting = true;
      const row = deleteBtn.closest(".settings-qt-row");
      const form = deleteBtn.closest("form");
      deleteBtn.setAttribute("ld-confirm-question", "确认删除？");
      deleteBtn.setAttribute("ld-confirm-danger", "");
      const popup = document.createElement("ld-confirm-popup");
      popup._button = deleteBtn;
      popup._onConfirm = () => {
        row.remove();
        this.syncBookmarkQuickTags(form);
        this.queueSubmit(form);
      };
      document.body.appendChild(popup);
      return;
    }
  }

  _createQuickTagRow(index) {
    const li = document.createElement("li");
    li.classList.add("settings-module-item", "settings-qt-row");
    li.dataset.qtIndex = String(index);
    li.innerHTML = quickTagRowTemplate();
    return li;
  }

  _openIconPicker(button) {
    document.querySelectorAll(".settings-qt-icon-picker-popup").forEach((el) => el.remove());

    const popup = document.createElement("div");
    popup.classList.add("settings-qt-icon-picker-popup");

    const currentIcon = button.dataset.qtIcon || "";
    popup.innerHTML = `
      <input type="text" class="form-input qt-icon-picker-search" placeholder="${gettext("Search for more icons")}" value="">
      <div class="qt-icon-picker-grid"></div>
    `;

    // 追加到主行内（absolute 相对于主行定位，跟随滚动）
    const mainRow = button.closest(".settings-qt-main");
    if (!mainRow) return;
    const mainWidth = mainRow.offsetWidth;
    const mainStyle = getComputedStyle(mainRow);
    popup.style.top = "calc(100% + 4px)";
    popup.style.left = mainStyle.paddingLeft;
    popup.style.width = `${mainWidth}px`;
    mainRow.appendChild(popup);

    const input = popup.querySelector(".qt-icon-picker-search");
    const grid = popup.querySelector(".qt-icon-picker-grid");
    let debounceTimer = null;

    const DEFAULT_ICON = "__default__";

    const selectIcon = (iconName) => {
      if (iconName && iconName !== DEFAULT_ICON) {
        button.dataset.qtIcon = iconName;
        button.innerHTML = `<iconify-icon icon="${iconName}" width="16" height="16"></iconify-icon>`;
      } else {
        button.dataset.qtIcon = "";
        button.innerHTML = hashIconSvg(16, "settings-qt-icon-placeholder");
      }
      // 先执行保存，再移除 popup —— 避免 popup.remove() 触发的 change 事件
      // (搜索框失焦/移除) 导致 onChange 内产生多余的 queueSubmit 调用。
      const form = button.closest("form");
      this.syncBookmarkQuickTags(form);
      this.queueSubmit(form);
      this._suppressChangeFromIconPicker = true;
      popup.remove();
      this._suppressChangeFromIconPicker = false;
    };

    const iconDataMap = window.__ldIconData || {};

    const renderItem = (iconName, isDefault) => {
      const item = document.createElement("button");
      item.type = "button";
      item.classList.add("qt-icon-picker-item");
      const selected = isDefault ? !currentIcon : iconName === currentIcon;
      if (selected) item.classList.add("is-selected");
      if (isDefault) {
        item.innerHTML = hashIconSvg(20, "action-icon");
      } else {
        const cached = iconDataMap[iconName];
        if (cached && cached.body) {
          const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
          svg.setAttribute("width", "20");
          svg.setAttribute("height", "20");
          svg.setAttribute("viewBox", `0 0 ${cached.width || 24} ${cached.height || 24}`);
          svg.innerHTML = cached.body;
          item.appendChild(svg);
        } else {
          const icon = document.createElement("iconify-icon");
          icon.setAttribute("icon", iconName);
          icon.setAttribute("width", "20");
          icon.setAttribute("height", "20");
          item.appendChild(icon);
        }
      }
      // 阻止 mousedown 时浏览器默认的焦点切换行为，避免干扰后续 click 事件触发
      item.addEventListener("mousedown", (e) => e.preventDefault());
      item.addEventListener("click", () => selectIcon(isDefault ? "" : iconName));
      grid.appendChild(item);
    };

    // 收集当前已使用的自定义图标
    const usedIcons = new Set();
    const form = button.closest("form");
    if (form) {
      form.querySelectorAll("[data-qt-icon]").forEach((btn) => {
        const name = btn.dataset.qtIcon;
        if (name) usedIcons.add(name);
      });
    }

    const presetIconNames = window.__ldPresetIconNames || [];

    const showPresets = () => {
      grid.innerHTML = "";
      renderItem("", true);
      // 已使用的自定义图标（非预置）
      const usedNonPreset = [...usedIcons].filter((name) => !presetIconNames.includes(name));
      if (usedNonPreset.length) {
        usedNonPreset.forEach((name) => renderItem(name, false));
      }
      // 预置图标
      presetIconNames.forEach((iconName) => renderItem(iconName, false));
    };

    const searchIcons = async (query) => {
      if (!query) {
        showPresets();
        return;
      }
      grid.innerHTML = `<div class="qt-icon-picker-hint">${gettext("Loading...")}</div>`;
      try {
        const resp = await fetch(`https://api.iconify.design/search?query=${encodeURIComponent(query)}&limit=48`);
        const data = await resp.json();
        if (!data.icons || data.icons.length === 0) {
          grid.innerHTML = `<div class="qt-icon-picker-hint">${gettext("No icons found")}</div>`;
          return;
        }
        grid.innerHTML = "";
        data.icons.forEach((iconName) => renderItem(iconName, false));
      } catch {
        grid.innerHTML = `<div class="qt-icon-picker-hint">${gettext("Search failed")}</div>`;
      }
    };

    showPresets();

    input.addEventListener("input", () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => searchIcons(input.value.trim()), 300);
    });

    const onDocClick = (e) => {
      if (!popup.contains(e.target) && e.target !== button) {
        popup.remove();
        document.removeEventListener("click", onDocClick);
      }
    };
    setTimeout(() => document.addEventListener("click", onDocClick), 0);

    input.focus();
  }

  // 目录导航：点击锚点跳转并短时锁定高亮，避免平滑滚动期间抖动。
  initializeDirectoryLinks() {
    this.directoryLinks.forEach((link) => {
      const handler = (event) => {
        const targetId = link.dataset.settingsSectionTarget;
        const target = targetId ? document.getElementById(targetId) : null;
        if (!targetId || !target) {
          return;
        }

        event.preventDefault();
        this.lockDirectorySection(targetId);
        this.scrollToSection(target);
        this.setActiveDirectoryLink(targetId);
      };
      this.directoryClickHandlers.set(link, handler);
      link.addEventListener("click", handler);
    });
  }

  // 面板折叠：读取与持久化展开状态。
  initializePanelToggles() {
    this.panelToggleButtons.forEach((button) => {
      button.addEventListener("click", this.onPanelToggleClick);
      const panelId = button.getAttribute("aria-controls");
      const panel = panelId ? document.getElementById(panelId) : null;
      const expanded = panelId ? this.getStoredPanelExpanded(panelId) : false;
      if (panel) {
        panel.hidden = !expanded;
      }
      this.syncPanelToggle(button, expanded);
    });
  }

  // 帮助提示：将说明文案收敛为可点击的 popover。
  initializeHelpPopovers() {
    this.element
      .querySelectorAll(".settings-row")
      .forEach((row) => this.enhanceRowHelp(row));
    this.element
      .querySelectorAll(".settings-stack-header")
      .forEach((header) => this.enhanceStackHeaderHelp(header));

    this.helpPopovers = Array.from(
      this.element.querySelectorAll("[data-settings-help]"),
    );
    this.helpPopovers.forEach((wrapper) => {
      const button = wrapper.querySelector("[data-settings-help-button]");
      if (button) {
        button.addEventListener("click", this.onHelpButtonClick);
      }
    });
    this.updateHelpPopoverPositions();
  }

  enhanceRowHelp(row) {
    const copy = row.querySelector(":scope > .settings-copy");
    if (!copy) {
      return;
    }

    const helpNodes = Array.from(copy.querySelectorAll(":scope > .form-input-hint"));
    if (helpNodes.length === 0) {
      return;
    }

    const label = copy.querySelector(":scope > .settings-label");
    if (!label) {
      return;
    }

    const labelRow = this.ensureLabelRow(label);
    labelRow.appendChild(this.createHelpPopover(helpNodes));
  }

  enhanceStackHeaderHelp(header) {
    const copy = header.firstElementChild;
    if (!copy) {
      return;
    }

    const helpNodes = Array.from(copy.querySelectorAll(":scope > .form-input-hint"));
    if (helpNodes.length === 0) {
      return;
    }

    const label = copy.querySelector(":scope > .settings-label");
    if (!label) {
      return;
    }

    const labelRow = this.ensureLabelRow(label);
    labelRow.appendChild(this.createHelpPopover(helpNodes));
  }

  ensureLabelRow(label) {
    const existing = label.closest(".settings-label-row");
    if (existing) {
      return existing;
    }

    const wrapper = document.createElement("div");
    wrapper.className = "settings-label-row";
    label.parentNode.insertBefore(wrapper, label);
    wrapper.appendChild(label);
    return wrapper;
  }

  createHelpPopover(helpNodes) {
    const wrapper = document.createElement("div");
    wrapper.className = "settings-help";
    wrapper.dataset.settingsHelp = "";
    wrapper.dataset.settingsHelpOpen = "false";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "settings-help-button";
    button.dataset.settingsHelpButton = "";
    button.setAttribute("aria-label", gettext("More info"));
    button.setAttribute("aria-expanded", "false");
    button.innerHTML =
      '<svg width="20" height="20"><use href="#ld-icon-info"></use></svg>';

    const popover = document.createElement("div");
    popover.className = "settings-help-popover";
    popover.setAttribute("role", "tooltip");
    this.helpPopoverCounter += 1;
    popover.id = `settings-help-popover-${this.helpPopoverCounter}`;
    button.setAttribute("aria-controls", popover.id);
    helpNodes.forEach((node) => {
      popover.appendChild(node);
    });

    wrapper.append(button, popover);
    return wrapper;
  }

  setHelpPopoverOpen(wrapper, open) {
    if (!(wrapper instanceof HTMLElement)) {
      return;
    }

    wrapper.dataset.settingsHelpOpen = open ? "true" : "false";
    const button = wrapper.querySelector("[data-settings-help-button]");
    if (button instanceof HTMLButtonElement) {
      button.setAttribute("aria-expanded", open ? "true" : "false");
    }
  }

  closeHelpPopovers(exceptWrapper = null) {
    this.helpPopovers.forEach((wrapper) => {
      if (wrapper !== exceptWrapper) {
        this.setHelpPopoverOpen(wrapper, false);
      }
    });
  }

  updateHelpPopoverPositions() {
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
    const gutter = window.matchMedia("(max-width: 720px)").matches ? 16 : 8;

    this.helpPopovers.forEach((wrapper) => {
      const popover = wrapper.querySelector(".settings-help-popover");
      if (!(popover instanceof HTMLElement)) {
        return;
      }

      const wrapperRect = wrapper.getBoundingClientRect();
      const popoverWidth = Math.min(
        popover.getBoundingClientRect().width || popover.scrollWidth || 0,
        Math.max(0, viewportWidth - gutter * 2),
      );
      const leftInViewport = Math.min(
        Math.max(wrapperRect.left, gutter),
        Math.max(gutter, viewportWidth - popoverWidth - gutter),
      );

      popover.style.left = `${leftInViewport - wrapperRect.left}px`;
      popover.style.right = "auto";
    });
  }

  getStoredPanelExpanded(panelId) {
    return getStoredSettingsPanelExpanded(panelId);
  }

  setStoredPanelExpanded(panelId, expanded) {
    setStoredSettingsPanelExpanded(panelId, expanded);
  }

  syncPanelToggle(button, expanded) {
    button.setAttribute("aria-expanded", expanded ? "true" : "false");
    const label = expanded
      ? button.dataset.settingsPanelExpandedLabel || gettext("Collapse")
      : button.dataset.settingsPanelCollapsedLabel || gettext("Expand");
    button.setAttribute("aria-label", label);
    const assistiveLabel = button.querySelector(
      "[data-settings-panel-toggle-label]",
    );
    if (assistiveLabel) {
      assistiveLabel.textContent = label;
    }
  }

  stabilizePanelToggleScrollPosition(button, buttonTopBeforeToggle) {
    if (this.panelToggleStabilizeFrame) {
      cancelAnimationFrame(this.panelToggleStabilizeFrame);
      this.panelToggleStabilizeFrame = null;
    }

    this.panelToggleStabilizeFrame = requestAnimationFrame(() => {
      this.panelToggleStabilizeFrame = requestAnimationFrame(() => {
        this.panelToggleStabilizeFrame = null;

        if (!(button instanceof HTMLElement) || !button.isConnected) {
          this.updateDirectoryState();
          return;
        }

        const buttonTopAfterToggle = button.getBoundingClientRect().top;
        const topDelta = buttonTopAfterToggle - buttonTopBeforeToggle;
        if (Math.abs(topDelta) > 0.5) {
          this.setScrollTop(this.getScrollMetrics().scrollTop + topDelta);
        }

        this.updateDirectoryState();
      });
    });
  }

  // 滚动记忆：使用 RAF 节流保存，避免频繁写入存储。
  queueScrollPositionSave() {
    if (this.scrollSaveFrame) {
      return;
    }

    this.scrollSaveFrame = requestAnimationFrame(() => {
      this.scrollSaveFrame = null;
      this.saveScrollPosition();
    });
  }

  saveScrollPosition() {
    setStoredSettingsScrollPosition(this.getScrollMetrics().scrollTop);
  }

  restoreStoredScrollPosition() {
    if (window.location.hash) {
      return;
    }

    const storedValue = getStoredSettingsScrollPosition();
    if (storedValue === null) {
      return;
    }

    this.scrollRestoreFrame = requestAnimationFrame(() => {
      this.scrollRestoreFrame = null;
      const { clientHeight, scrollHeight } = this.getScrollMetrics();
      const maxScrollTop = Math.max(0, scrollHeight - clientHeight);
      this.setScrollTop(Math.min(storedValue, maxScrollTop));
      this.updateDirectoryState();
    });
  }

  // 目录高亮：根据滚动位置计算当前激活 section。
  getScrollMetrics() {
    if (this.scrollContainer === window) {
      const scrollTop =
        window.pageYOffset || document.documentElement.scrollTop || 0;
      const clientHeight = window.innerHeight;
      const scrollHeight = document.documentElement.scrollHeight;
      return { scrollTop, clientHeight, scrollHeight };
    }

    return {
      scrollTop: this.scrollContainer.scrollTop,
      clientHeight: this.scrollContainer.clientHeight,
      scrollHeight: this.scrollContainer.scrollHeight,
    };
  }

  setActiveDirectoryLink(sectionId) {
    let activeLink = null;
    this.directoryLinks.forEach((link) => {
      const isActive = link.dataset.settingsSectionTarget === sectionId;
      link.classList.toggle("is-active", isActive);
      if (isActive) {
        link.setAttribute("aria-current", "true");
        activeLink = link;
      } else {
        link.removeAttribute("aria-current");
      }
    });

    if (activeLink) {
      this.centerDirectoryLink(activeLink);
    }
  }

  updateDirectoryState() {
    if (this.sections.length === 0) {
      return;
    }

    const { scrollTop, clientHeight, scrollHeight } = this.getScrollMetrics();
    if (scrollTop + clientHeight >= scrollHeight - 4) {
      this.setActiveDirectoryLink(this.sections[this.sections.length - 1].id);
      return;
    }

    const activationOffset = scrollTop + 120;
    let activeSection = this.sections[0];

    this.sections.forEach((section) => {
      if (this.getSectionTop(section) <= activationOffset) {
        activeSection = section;
      }
    });

    this.setActiveDirectoryLink(activeSection.id);
  }

  scrollToSection(section) {
    const top = this.getSectionTop(section);
    const options = {
      top,
      behavior: "smooth",
    };

    if (this.scrollContainer === window) {
      window.scrollTo(options);
      return;
    }

    this.scrollContainer.scrollTo(options);
  }

  getSectionTop(section) {
    const sectionRect = section.getBoundingClientRect();
    if (this.scrollContainer === window) {
      return (
        (window.pageYOffset || document.documentElement.scrollTop || 0) +
        sectionRect.top
      );
    }

    const containerRect = this.scrollContainer.getBoundingClientRect();
    return this.scrollContainer.scrollTop + sectionRect.top - containerRect.top;
  }

  setScrollTop(top) {
    if (this.scrollContainer === window) {
      window.scrollTo(0, top);
      return;
    }

    this.scrollContainer.scrollTop = top;
  }

  // 布局性能：通过 RAF 合并高频触发，避免短时间重复测量布局。
  queueAdaptiveControlLayoutsUpdate() {
    if (
      this.adaptiveLayoutFrame ||
      (this.segmentedControls.length === 0 &&
        this.inlineCheckboxGroups.length === 0)
    ) {
      return;
    }

    this.adaptiveLayoutFrame = requestAnimationFrame(() => {
      this.adaptiveLayoutFrame = null;
      this.updateAdaptiveControlLayouts();
    });
  }

  // 布局测量：移动端按实际宽度自动切换 segmented/inline 堆叠样式。
  updateAdaptiveControlLayouts() {
    const shouldEvaluateMobileLayouts = window.matchMedia(
      "(max-width: 720px)",
    ).matches;

    this.segmentedControls.forEach((segment) => {
      const row = segment.closest(".settings-row");
      const control = segment.closest(".settings-control");

      segment.classList.remove("is-stacked");
      row?.classList.remove("has-stacked-segment");
      control?.classList.remove("has-stacked-segment");

      if (!shouldEvaluateMobileLayouts || !control || !row) {
        return;
      }

      const naturalWidth = this.getSegmentNaturalWidth(segment);
      const availableWidth = control.clientWidth;
      if (naturalWidth <= availableWidth + 1) {
        return;
      }

      segment.classList.add("is-stacked");
      row.classList.add("has-stacked-segment");
      control.classList.add("has-stacked-segment");
    });

    this.inlineCheckboxGroups.forEach((group) => {
      const row = group.closest(".settings-row");

      group.classList.remove("is-stacked");
      row?.classList.remove("has-stacked-inline-group");

      if (!shouldEvaluateMobileLayouts || !row) {
        return;
      }

      const naturalWidth = this.getInlineGroupNaturalWidth(group);
      const availableWidth = group.clientWidth;
      if (naturalWidth <= availableWidth + 1) {
        return;
      }

      group.classList.add("is-stacked");
      row.classList.add("has-stacked-inline-group");
    });
  }

  getSegmentNaturalWidth(segment) {
    const styles = window.getComputedStyle(segment);
    const gap = Number.parseFloat(styles.columnGap || styles.gap || "0") || 0;
    const paddingLeft = Number.parseFloat(styles.paddingLeft || "0") || 0;
    const paddingRight = Number.parseFloat(styles.paddingRight || "0") || 0;
    const options = Array.from(
      segment.querySelectorAll(".settings-segmented-option"),
    );

    return (
      options.reduce((sum, option) => sum + option.getBoundingClientRect().width, 0) +
      Math.max(0, options.length - 1) * gap +
      paddingLeft +
      paddingRight
    );
  }

  getInlineGroupNaturalWidth(group) {
    const styles = window.getComputedStyle(group);
    const gap = Number.parseFloat(styles.columnGap || styles.gap || "0") || 0;
    const paddingLeft = Number.parseFloat(styles.paddingLeft || "0") || 0;
    const paddingRight = Number.parseFloat(styles.paddingRight || "0") || 0;
    const items = Array.from(group.querySelectorAll(".settings-inline-checkbox"));

    return (
      items.reduce((sum, item) => sum + item.getBoundingClientRect().width, 0) +
      Math.max(0, items.length - 1) * gap +
      paddingLeft +
      paddingRight
    );
  }

  // 目录锁：点击目录后优先保持目标高亮，手动滚动意图触发即释放。
  centerDirectoryLink(link) {
    const directory = link.closest("[data-settings-directory]");
    if (!(directory instanceof HTMLElement)) {
      return;
    }

    if (directory.scrollWidth <= directory.clientWidth + 4) {
      return;
    }

    const nextLeft = Math.max(
      0,
      link.offsetLeft - (directory.clientWidth - link.clientWidth) / 2,
    );
    directory.scrollLeft = nextLeft;
  }

  lockDirectorySection(sectionId) {
    this.lockedDirectorySectionId = sectionId;
    const now = performance.now ? performance.now() : Date.now();
    this.directoryLockUntil = now + this.directoryLockTimeoutMs;
  }

  clearDirectoryLock() {
    this.lockedDirectorySectionId = null;
    this.directoryLockUntil = 0;
  }

  isScrollIntentKey(event) {
    if (!(event instanceof KeyboardEvent)) {
      return false;
    }

    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) {
      return false;
    }

    const target = event.target;
    if (target instanceof HTMLElement) {
      if (
        target.closest(
          "input, textarea, select, [contenteditable='true'], [contenteditable=''], [role='textbox']",
        )
      ) {
        return false;
      }
    }

    return (
      event.key === "ArrowDown" ||
      event.key === "ArrowUp" ||
      event.key === "PageDown" ||
      event.key === "PageUp" ||
      event.key === "Home" ||
      event.key === "End" ||
      event.key === " " ||
      event.key === "Spacebar"
    );
  }

}

registerBehavior("ld-settings-page", SettingsPageBehavior);
