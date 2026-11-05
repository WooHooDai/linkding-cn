import Sortable from "sortablejs";
import { Behavior, registerBehavior } from "./runtime.js";
import { getCSRFToken } from "../utils/csrf.js";
import { gettext } from "../utils/i18n.js";
import { showToast } from "./toast.js";
import { applyToolbarAutoHide } from "./bookmark-list.js";
import { panelsFitTogether, syncPanelSpace } from "../utils/panel-space.js";
import {
  getSidebarScope,
  syncSidebarStateCache,
} from "../state/sidebar-state.js";

// 布局抽屉：桌面端让位（内容平移不重排），窄屏全宽 + 遮罩。
// 偏好保存走 settings.save 的单字段通道；改完按 data-pref-apply 决定
// 就地生效（dom）/ 只换列表（fragment）/ 重载（reload）。

const TAB_KEY = "ld:layout-drawer:tab";
const SCROLL_KEY = "ld:layout-drawer:scroll";
// 主题要整页重载，先把「开着的页签」记下来，重载后自动展开
const REOPEN_KEY = "ld:layout-drawer:reopen";
// 「模块设置」这类可折叠列表的开合记忆
const PANELS_KEY = "ld:layout-drawer:panels";
// 分组（界面 / 视图 / …）的折叠记忆：只记收起的，未记录的按展开算
const GROUPS_KEY = "ld:layout-drawer:groups";
// 抽屉是否开着：刷新后保持
const OPEN_KEY = "ld:layout-drawer:open";
const MOBILE_QUERY = "(max-width: 840px)";
// 连点多个开关时合并成一次片段请求
const REFRESH_DELAY = 120;
const LEGACY_TABS = { view: "page", toolbar: "bookmark" };
// 吸顶/吸底开关对应的容器：书签列表页与高亮页各一套
const STICKY_SELECTORS = {
  sticky_header_controls: ".section-header[data-sticky-on]",
  sticky_pagination: ".bookmark-pagination[data-sticky-on]",
  highlights_sticky_header_controls:
    ".highlights-sticky-header[data-sticky-on]",
  highlights_sticky_pagination: ".highlights-pagination[data-sticky-on]",
};
// 改完要整页重载的偏好：高亮页的分页大小与分组都会改变服务端渲染的列表结构
const HIGHLIGHTS_RELOAD_FIELDS = {
  highlights_per_page: ["page"],
  highlights_group_by: ["page", "group_by"],
};

function isMobile() {
  return window.matchMedia(MOBILE_QUERY).matches;
}

function readJSON(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
}

class LayoutDrawer extends Behavior {
  constructor(element) {
    super(element);
    this.url = element.dataset.saveUrl;
    this.fragmentUrl = element.dataset.fragmentUrl;
    this.quickTagsUrl = element.dataset.quickTagsUrl;
    this.page = document.querySelector(".bookmarks-page, .highlights-page");
    this.scroller = element.querySelector("[data-layout-scroll]");
    this.sortables = [];
    this.refreshTimer = null;
    this.refreshSeq = 0;
    this.resetPage = false;
    this.scrollTimer = null;
    this.lastTrigger = null;
    this.helpPopover = null;
    this.helpTrigger = null;

    this.onDocumentClick = this.onDocumentClick.bind(this);
    this.onKeydown = this.onKeydown.bind(this);
    this.onChange = this.onChange.bind(this);
    this.onInput = this.onInput.bind(this);
    this.onTabKeydown = this.onTabKeydown.bind(this);
    this.onDrawerClick = this.onDrawerClick.bind(this);
    this.onScroll = this.onScroll.bind(this);
    this.onPointerOver = this.onPointerOver.bind(this);
    this.onPointerOut = this.onPointerOut.bind(this);
    this.onSidebarModulesReordered = this.onSidebarModulesReordered.bind(this);
    this.closeHelpTimer = null;
    // 收起动画期间还要锁着页面滚动，等面板滑出去再解锁
    this.unlockTimer = null;

    this.init();
  }

  destroy() {
    document.removeEventListener("click", this.onDocumentClick);
    document.removeEventListener("keydown", this.onKeydown);
    this.element.removeEventListener("change", this.onChange);
    this.element.removeEventListener("input", this.onInput);
    this.element.removeEventListener("click", this.onDrawerClick);
    this.element
      .querySelector("[role='tablist']")
      ?.removeEventListener("keydown", this.onTabKeydown);
    this.scroller?.removeEventListener("scroll", this.onScroll);
    document.removeEventListener("pointerover", this.onPointerOver);
    document.removeEventListener("pointerout", this.onPointerOut);
    document.removeEventListener(
      "sidebar-modules-reordered",
      this.onSidebarModulesReordered,
    );
    clearTimeout(this.closeHelpTimer);
    clearTimeout(this.unlockTimer);
    this.sortables.forEach((instance) => instance.destroy());
    this.sortables = [];
    this.helpPopover?.remove();
    this.helpPopover = null;
    clearTimeout(this.refreshTimer);
    clearTimeout(this.scrollTimer);
  }

  init() {
    // 触发布局按钮在抽屉外，用文档级委托
    document.addEventListener("click", this.onDocumentClick);
    document.addEventListener("keydown", this.onKeydown);
    this.element.addEventListener("change", this.onChange);
    this.element.addEventListener("input", this.onInput);
    this.element.addEventListener("click", this.onDrawerClick);
    this.element
      .querySelector("[role='tablist']")
      ?.addEventListener("keydown", this.onTabKeydown);
    this.scroller?.addEventListener("scroll", this.onScroll, { passive: true });
    document.addEventListener("pointerover", this.onPointerOver);
    document.addEventListener("pointerout", this.onPointerOut);
    document.addEventListener(
      "sidebar-modules-reordered",
      this.onSidebarModulesReordered,
    );

    this.initSortables();
    this.restorePanels();
    this.initGroups();
    this.selectTab(this.readSavedTab(), { focus: false });

    // 深链：?layout=page / ?layout=bookmark（兼容旧的 view / toolbar）
    const requested = new URLSearchParams(window.location.search).get("layout");
    if (requested) {
      this.open(LEGACY_TABS[requested] || requested);
      return;
    }
    // 刚从主题切换重载回来：恢复展开态
    let reopen = null;
    try {
      reopen = sessionStorage.getItem(REOPEN_KEY);
      if (reopen) sessionStorage.removeItem(REOPEN_KEY);
    } catch {
      reopen = null;
    }
    if (reopen) {
      this.open(LEGACY_TABS[reopen] || reopen);
      return;
    }
    // 刷新后保持上次的开合状态
    try {
      if (localStorage.getItem(OPEN_KEY) === "1") this.open(null, { focus: false });
    } catch {
      // 忽略
    }
  }

  // ---------- 开合 ----------

  isOpen() {
    return this.element.classList.contains("is-open");
  }

  toggleButtons() {
    return document.querySelectorAll("[data-layout-drawer-toggle]");
  }

  open(tab = null, { focus = true } = {}) {
    this.lastTrigger = document.activeElement;
    this.element.classList.add("is-open");
    this.element.removeAttribute("inert");
    this.element.setAttribute("aria-hidden", "false");
    // 上一次收起的解锁计时器作废，否则会在这次展开中途把滚动放回去
    clearTimeout(this.unlockTimer);
    document.body.classList.add("layout-drawer-open");
    this.toggleButtons().forEach((button) =>
      button.setAttribute("aria-expanded", "true"),
    );
    // 宽度够就让位（内容平移不重排），不够就浮层覆盖
    syncPanelSpace();

    if (tab) this.selectTab(tab);

    // 窄屏两层遮罩会叠在一起；宽屏但两个面板放不下时也会把列表夹成一条缝，
    // 两种情况下都只留一个面板
    if (
      this.page?.classList.contains("sidebar-open") &&
      (isMobile() || !panelsFitTogether())
    ) {
      document.querySelector("[data-sidebar-close]")?.click();
    }

    try {
      localStorage.setItem(OPEN_KEY, "1");
    } catch {
      // 忽略
    }
    if (focus) this.element.querySelector(".layout-drawer-close")?.focus();
  }

  close({ restoreFocus = true } = {}) {
    if (!this.isOpen()) return;
    this.closeHelp();
    this.saveScroll();
    this.element.classList.remove("is-open");
    this.element.setAttribute("inert", "");
    this.element.setAttribute("aria-hidden", "true");
    // 抽屉还在滑出去：这时候把滚动解锁，滚动条会突然回来、列表跟着横向跳一下，
    // 所以等过渡走完再解锁；期间可能又被打开，解锁前再确认一次是收着的。
    // 时长从实际样式里取（窄屏全宽面板比宽屏走得久），减弱动态时取到 0 就立即解锁
    clearTimeout(this.unlockTimer);
    const transitionMs = parseFloat(
      getComputedStyle(this.element).transitionDuration,
    );
    const unlockDelay = Number.isFinite(transitionMs) ? transitionMs * 1000 + 20 : 220;
    this.unlockTimer = setTimeout(() => {
      if (!this.isOpen()) document.body.classList.remove("layout-drawer-open");
    }, unlockDelay);
    this.toggleButtons().forEach((button) =>
      button.setAttribute("aria-expanded", "false"),
    );
    syncPanelSpace();
    try {
      localStorage.setItem(OPEN_KEY, "0");
    } catch {
      // 忽略
    }
    if (restoreFocus && this.lastTrigger instanceof HTMLElement) {
      this.lastTrigger.focus();
    }
  }

  onDocumentClick(event) {
    const helpButton = event.target.closest("[data-drawer-help] .ld-help-button");
    if (helpButton && this.isHelpScope(helpButton)) {
      this.toggleHelp(helpButton);
      return;
    }
    if (!event.target.closest("[data-drawer-help]")) this.closeHelp();
    if (event.target.closest("[data-layout-drawer-toggle]")) {
      this.isOpen() ? this.close() : this.open();
      return;
    }
    if (event.target.closest("[data-layout-drawer-close]")) {
      this.close();
      return;
    }
    // 点抽屉以外不关闭：抽屉常驻在页面上，方便边看列表边改偏好；
    // 只有关闭按钮（以及触发按钮、Esc）会收起来。
  }

  onKeydown(event) {
    if (event.key !== "Escape") return;
    // 先收说明浮层，再收抽屉
    if (this.helpTrigger) {
      event.preventDefault();
      this.closeHelp();
      return;
    }
    if (this.isOpen()) {
      event.preventDefault();
      this.close();
    }
  }

  // 抽屉内部的点击：只处理关闭、切页与两个入口按钮，不拦冒泡
  //（拦住会让 Turbo 收不到链接点击，页内跳转退化成整页刷新）
  onDrawerClick(event) {
    if (event.target.closest("[data-layout-drawer-close]")) {
      this.close();
      return;
    }
    const tab = event.target.closest("[data-layout-tab]");
    if (tab) {
      this.selectTab(tab.dataset.layoutTab);
      return;
    }
    const disclosure = event.target.closest("[data-drawer-disclosure]");
    if (disclosure) {
      this.togglePanel(disclosure);
      return;
    }
    if (event.target.closest("[data-layout-groups-expand]")) {
      this.setAllGroups(true);
      return;
    }
    if (event.target.closest("[data-layout-groups-collapse]")) {
      this.setAllGroups(false);
      return;
    }
    if (event.target.closest("[data-drawer-quick-tags]")) {
      this.openQuickTags();
      return;
    }
  }

  // ---------- 页签与滚动位置 ----------

  tabs() {
    return Array.from(this.element.querySelectorAll("[data-layout-tab]"));
  }

  currentTab() {
    return (
      this.element.querySelector("[data-layout-tab].is-active")?.dataset
        .layoutTab || "page"
    );
  }

  readSavedTab() {
    const raw = localStorage.getItem(TAB_KEY);
    const saved = LEGACY_TABS[raw] || raw;
    return this.tabs().some((tab) => tab.dataset.layoutTab === saved)
      ? saved
      : "page";
  }

  selectTab(name, { focus = true, restoreScroll = true } = {}) {
    const tabs = this.tabs();
    if (!tabs.some((tab) => tab.dataset.layoutTab === name)) return;
    this.closeHelp();
    this.saveScroll();

    tabs.forEach((tab) => {
      const active = tab.dataset.layoutTab === name;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
      tab.tabIndex = active ? 0 : -1;
      if (active && focus) tab.focus();
    });

    this.element.querySelectorAll("[data-layout-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.layoutPanel !== name;
    });

    try {
      localStorage.setItem(TAB_KEY, name);
    } catch {
      // 隐私模式下不可用，忽略
    }

    if (restoreScroll && this.scroller) {
      const positions = readJSON(SCROLL_KEY, {});
      this.scroller.scrollTop = positions[name] || 0;
    }
    // 面板切换后内容高度变了，重算一下让位判据
    this.syncGroupActions();
    syncPanelSpace();
  }

  onTabKeydown(event) {
    const tabs = this.tabs();
    const index = tabs.indexOf(document.activeElement.closest("[data-layout-tab]"));
    if (index === -1) return;
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;

    event.preventDefault();
    const delta = event.key === "ArrowRight" ? 1 : -1;
    const next = tabs[(index + delta + tabs.length) % tabs.length];
    this.selectTab(next.dataset.layoutTab);
  }

  saveScroll() {
    if (!this.scroller) return;
    const positions = readJSON(SCROLL_KEY, {});
    positions[this.currentTab()] = Math.round(this.scroller.scrollTop);
    try {
      localStorage.setItem(SCROLL_KEY, JSON.stringify(positions));
    } catch {
      // 忽略
    }
  }

  onScroll() {
    this.closeHelp();
    clearTimeout(this.scrollTimer);
    this.scrollTimer = setTimeout(() => this.saveScroll(), 150);
  }

  // ---------- 说明浮层 ----------

  // 悬浮即显示，移开稍等一下再收，方便鼠标移到浮层里读长文案
  onPointerOver(event) {
    if (event.pointerType === "touch") return;
    const help = event.target.closest("[data-drawer-help]");
    if (!help || !this.isHelpScope(help)) return;
    clearTimeout(this.closeHelpTimer);
    const button = help.querySelector(".ld-help-button");
    if (button) this.openHelp(button);
  }

  onPointerOut(event) {
    if (event.pointerType === "touch") return;
    const help = event.target.closest("[data-drawer-help]");
    if (!help || !this.isHelpScope(help)) return;
    const next = event.relatedTarget;
    if (next && (help.contains(next) || this.helpPopover?.contains(next))) return;
    this.scheduleCloseHelp();
  }

  // 帮助图标只认抽屉与快捷标签弹窗里的那几处
  isHelpScope(element) {
    return (
      this.element.contains(element) ||
      Boolean(element.closest(".modal-container.ld-modal-wide"))
    );
  }

  // 可折叠列表（当前只有侧边栏的「模块设置」）
  togglePanel(button) {
    const name = button.dataset.drawerDisclosure;
    const panel = this.element.querySelector(`[data-drawer-panel="${name}"]`);
    if (!panel) return;
    const open = panel.hidden;
    panel.hidden = !open;
    button.setAttribute("aria-expanded", open ? "true" : "false");

    const stored = readJSON(PANELS_KEY, {});
    stored[name] = open;
    try {
      localStorage.setItem(PANELS_KEY, JSON.stringify(stored));
    } catch {
      // 忽略
    }
  }

  restorePanels() {
    const stored = readJSON(PANELS_KEY, {});
    this.element.querySelectorAll("[data-drawer-disclosure]").forEach((button) => {
      const name = button.dataset.drawerDisclosure;
      if (!stored[name]) return;
      const panel = this.element.querySelector(`[data-drawer-panel="${name}"]`);
      if (!panel) return;
      panel.hidden = false;
      button.setAttribute("aria-expanded", "true");
    });
  }

  scheduleCloseHelp() {
    clearTimeout(this.closeHelpTimer);
    this.closeHelpTimer = setTimeout(() => this.closeHelp(), 120);
  }

  // ---------- 分组折叠 ----------

  // 批量开关在标题栏，作用于当前页签的面板；高亮页只有一块面板
  activePanel() {
    const name = this.currentTab();
    return this.element.querySelector(`[data-layout-panel="${name}"]`) || this.element;
  }

  groups() {
    return Array.from(this.activePanel().querySelectorAll("[data-drawer-group]"));
  }

  allGroups() {
    return Array.from(this.element.querySelectorAll("[data-drawer-group]"));
  }

  // 只记收起的组：新加的分组没有记录，按展开呈现
  initGroups() {
    const stored = readJSON(GROUPS_KEY, {});
    this.allGroups().forEach((group) => {
      if (stored[group.dataset.drawerGroup]) group.open = false;
      group.addEventListener("toggle", () => {
        this.saveGroups();
        this.syncGroupActions();
      });
    });
    this.syncGroupActions();
  }

  saveGroups() {
    const collapsed = {};
    this.allGroups().forEach((group) => {
      if (!group.open) collapsed[group.dataset.drawerGroup] = true;
    });
    try {
      localStorage.setItem(GROUPS_KEY, JSON.stringify(collapsed));
    } catch {
      // 隐私模式下不可用，忽略
    }
  }

  setAllGroups(open) {
    this.groups().forEach((group) => {
      group.open = open;
    });
    this.saveGroups();
    this.syncGroupActions();
  }

  // 全部展开 / 全部折叠按钮：没有可做的动作时置灰
  syncGroupActions() {
    const groups = this.groups();
    const expand = this.element.querySelector("[data-layout-groups-expand]");
    const collapse = this.element.querySelector("[data-layout-groups-collapse]");
    if (expand) expand.disabled = groups.every((group) => group.open);
    if (collapse) collapse.disabled = groups.every((group) => !group.open);
  }

  toggleHelp(button) {
    if (this.helpTrigger === button && !this.helpPopover?.hidden) {
      this.closeHelp();
      return;
    }
    this.openHelp(button);
  }

  openHelp(button) {
    const text = button.parentElement.querySelector(".ld-help-text");
    if (!text) return;

    if (!this.helpPopover) {
      this.helpPopover = document.createElement("div");
      this.helpPopover.className = "ld-help-popover";
      this.helpPopover.setAttribute("role", "tooltip");
      this.helpPopover.hidden = true;
      this.helpPopover.addEventListener("pointerenter", () =>
        clearTimeout(this.closeHelpTimer),
      );
      this.helpPopover.addEventListener("pointerleave", () =>
        this.scheduleCloseHelp(),
      );
      document.body.appendChild(this.helpPopover);
    }

    const popover = this.helpPopover;
    // 文案是模板里的可信内容：允许 <code> 之类的行内标签，§ 作为换行标记
    popover.innerHTML = text.textContent.trim().replace(/§/g, "<br>");
    popover.hidden = false;

    // 贴在图标下方，放不下就翻到上方；左右夹在视口内
    const gap = 6;
    const rect = button.getBoundingClientRect();
    const width = popover.offsetWidth;
    const height = popover.offsetHeight;
    const left = Math.max(
      8,
      Math.min(rect.left, window.innerWidth - width - 8),
    );
    let top = rect.bottom + gap;
    if (top + height > window.innerHeight - 8) {
      top = Math.max(8, rect.top - height - gap);
    }
    popover.style.left = `${left}px`;
    popover.style.top = `${top}px`;

    this.helpTrigger = button;
    button.setAttribute("aria-expanded", "true");
  }

  closeHelp() {
    if (!this.helpPopover || this.helpPopover.hidden) return;
    this.helpPopover.hidden = true;
    this.helpTrigger?.setAttribute("aria-expanded", "false");
    this.helpTrigger = null;
  }

  // ---------- 拖拽排序 ----------

  initSortables() {
    this.element.querySelectorAll("[data-drawer-list]").forEach((list) => {
      this.sortables.push(
        new Sortable(list, {
          animation: 120,
          draggable: "[data-item-key]",
          handle: ".ld-module-handle",
          ghostClass: "ld-module-ghost",
          onEnd: () => {
            const field = list.dataset.drawerList;
            this.save(field, this.serializeList(list), "fragment");
            if (field.endsWith("sidebar_modules")) this.syncSidebarModules();
          },
        }),
      );
    });
  }

  serializeList(list) {
    const items = Array.from(list.querySelectorAll("[data-item-key]")).map(
      (item) => ({
        key: item.dataset.itemKey,
        enabled: Boolean(item.querySelector("[data-item-enabled]")?.checked),
      }),
    );
    return JSON.stringify(items);
  }

  // ---------- 保存 ----------

  onInput(event) {
    const input = event.target.closest("[data-pref]");
    if (input) this.clearFieldError(input.dataset.pref);
  }

  onChange(event) {
    const otherToggle = event.target.closest("[data-language-other-toggle]");
    if (otherToggle) {
      // 「...」只是展开更多语言，选中具体语言才提交
      const row = this.element.querySelector("[data-language-other]");
      const select = row?.querySelector("[data-language-select]");
      if (row) row.hidden = false;
      if (select) {
        select.disabled = false;
        select.focus();
      }
      return;
    }
    if (event.target.matches("[data-language-select]")) {
      event.target.closest("form")?.requestSubmit();
      return;
    }
    if (event.target.matches(".ld-form input[name='language']")) {
      // 选常用语言时把「其他语言」下拉禁用，否则它会作为同一字段的第二个值提交
      const select = this.element.querySelector("[data-language-select]");
      if (select) select.disabled = true;
      event.target.closest("form")?.requestSubmit();
      return;
    }

    const enabledInput = event.target.closest("[data-item-enabled]");
    if (enabledInput) {
      const list = enabledInput.closest("[data-drawer-list]");
      if (!list) return;
      if (list.dataset.drawerList === "bookmark_toolbar_modules") {
        // 模块关掉后，对应的设置项一起收起
        this.syncModuleGates();
      }
      this.save(list.dataset.drawerList, this.serializeList(list), "fragment");
      return;
    }

    const input = event.target.closest("[data-pref]");
    if (!input) return;

    const field = input.dataset.pref;
    const isCheckbox = input.type === "checkbox";
    const value = isCheckbox ? (input.checked ? "on" : "") : input.value;
    this.save(field, value, input.dataset.prefApply || "dom", {
      appendEmpty: !isCheckbox,
      formId: input.dataset.prefForm,
    });
  }

  fieldErrorTarget(field) {
    return this.element.querySelector(
      `[data-field-error-for="${CSS.escape(field)}"]`,
    );
  }

  clearFieldError(field) {
    const target = this.fieldErrorTarget(field);
    if (!target) return;
    target.textContent = "";
    target.classList.remove("is-visible");
    this.element
      .querySelector(`[name="${CSS.escape(field)}"]`)
      ?.classList.remove("is-error");
  }

  showFieldError(field, errors) {
    const target = this.fieldErrorTarget(field);
    const input = this.element.querySelector(`[name="${CSS.escape(field)}"]`);
    input?.classList.add("is-error");
    if (!target) return false;
    target.textContent =
      (errors || []).map((error) => error.message).join(" ") ||
      gettext("Please review the highlighted fields.");
    target.classList.add("is-visible");
    return true;
  }

  async save(field, value, apply, { appendEmpty = true, formId } = {}) {
    const id = formId || "profile_quick";
    const body = new FormData();
    body.append("csrfmiddlewaretoken", getCSRFToken());
    body.append("form_id", id);
    body.append("form_fields", field);
    // 复选框为空表示未勾选，不能提交空值；其余字段要提交空串让服务端报「必填」
    if (value !== "" || appendEmpty) body.append(field, value);
    if (id === "global_quick") {
      // 全局表单要带上未展示字段，否则保存会把它们清空
      this.element
        .querySelectorAll("[data-global-field]")
        .forEach((input) => body.append(input.name, input.value));
    }

    let response;
    try {
      response = await fetch(this.url, {
        method: "POST",
        body,
        headers: {
          "X-CSRFToken": getCSRFToken(),
          "X-Requested-With": "XMLHttpRequest",
        },
      });
    } catch (error) {
      console.error(error);
      this.reportFailure();
      return;
    }

    if (!response.ok) {
      const data = await response.json().catch(() => null);
      const errors = data?.errors?.[field];
      if (!errors || !this.showFieldError(field, errors)) {
        this.reportFailure();
      }
      return;
    }

    this.clearFieldError(field);
    this.applyLocalState(field, value, apply);
  }

  reportFailure() {
    showToast(gettext("Couldn't save settings. Please try again."), {
      tone: "error",
    });
  }

  // ---------- 生效 ----------

  applyLocalState(field, value, apply) {
    this.syncConditionalRows();
    // 头部控件不在列表片段里（随机按钮、随机书签的链接目标），先就地更新
    this.applyDomPref(field, value);

    if (apply === "reload") {
      this.prepareReload(field);
      this.reloadKeepingDrawer();
      return;
    }
    if (field.endsWith("sidebar_modules")) this.syncSidebarModules();
    if (apply === "fragment") {
      // 每页数量变了还停在原页号会落到越界页
      if (field === "items_per_page" || field === "highlights_per_page") {
        this.resetPage = true;
      }
      this.scheduleListRefresh();
    }
  }

  // 整页重载前先清掉会被新偏好覆盖的 URL 参数：
  // 页号会越界；分组留在 URL 里会顶掉刚改的默认值。
  prepareReload(field) {
    const drop = HIGHLIGHTS_RELOAD_FIELDS[field];
    if (!drop) return;
    const params = new URLSearchParams(window.location.search);
    if (!drop.some((name) => params.has(name))) return;
    drop.forEach((name) => params.delete(name));
    const rest = params.toString();
    window.history.replaceState(
      {},
      "",
      `${window.location.pathname}${rest ? `?${rest}` : ""}`,
    );
  }

  // 侧边栏模块列表：书签页是 sidebar_modules，高亮页是 highlights_sidebar_modules
  sidebarModulesList() {
    return this.element.querySelector('[data-drawer-list$="sidebar_modules"]');
  }

  // 侧边栏模块顺序/开关变了：就地改侧边栏里的 section，不重新取内容
  syncSidebarModules() {
    const list = this.sidebarModulesList();
    const sidebar = document.querySelector(".sidebar");
    if (!list || !sidebar) return;

    const items = Array.from(list.querySelectorAll("[data-item-key]"));
    const sections = [];
    items.forEach((item) => {
      const section = sidebar.querySelector(
        `[data-sidebar-module="${item.dataset.itemKey}"]`,
      );
      if (!section) return;
      section.hidden = !item.querySelector("[data-item-enabled]")?.checked;
      sections.push(section);
    });

    // 统一父级才重排，避免把 section 挪出各自的容器
    const parent = sections[0]?.parentElement;
    if (!parent || sections.some((section) => section.parentElement !== parent)) {
      return;
    }
    sections.forEach((section) => parent.appendChild(section));
  }

  // 反过来：在侧边栏里直接拖了模块，抽屉里的同一份列表要跟着换顺序，
  // 否则在这里动一个开关会把旧顺序写回去
  onSidebarModulesReordered(event) {
    const list = this.sidebarModulesList();
    if (!list) return;
    const items = new Map(
      Array.from(list.querySelectorAll("[data-item-key]")).map((item) => [
        item.dataset.itemKey,
        item,
      ]),
    );
    (event.detail?.keys || []).forEach((key) => {
      const item = items.get(key);
      if (item) list.appendChild(item);
    });
  }

  reloadKeepingDrawer() {
    try {
      sessionStorage.setItem(REOPEN_KEY, this.currentTab());
    } catch {
      // 忽略
    }
    window.location.reload();
  }

  applyDomPref(field, value) {
    const enabled = Boolean(value);

    const stickySelector = STICKY_SELECTORS[field];
    if (stickySelector) {
      document
        .querySelectorAll(stickySelector)
        .forEach((el) =>
          el.setAttribute("data-sticky-on", enabled ? "true" : "false"),
        );
      return;
    }
    if (field === "bookmark_toolbar_auto_hide") {
      applyToolbarAutoHide(enabled);
      return;
    }
    if (field === "bookmark_link_target") {
      const single = document.querySelector("[ld-random-single]");
      if (single) single.dataset.linkTarget = value;
      return;
    }
    if (field === "show_sidebar" || field === "show_highlights_sidebar") {
      // 「常驻显示」存的是默认值：同步 cookie 与开合记忆，页面按新默认切换
      if (!this.page) return;
      this.page.dataset.sidebarDefault = enabled ? "1" : "0";
      syncSidebarStateCache(getSidebarScope(), enabled);
      syncPanelSpace();
      return;
    }
    if (
      field === "sticky_side_panel" ||
      field === "highlights_sticky_side_panel"
    ) {
      const sidebar = document.querySelector(".sidebar");
      if (sidebar) sidebar.dataset.stickyOn = enabled ? "true" : "false";
      return;
    }
    if (field === "highlights_enable_random_button") {
      document
        .querySelectorAll("[data-hl-random]")
        .forEach((el) => (el.hidden = !enabled));
      // 与书签列表一致：窄屏工具栏按这个类在「随机 + 更多」与三个按钮平铺之间切换
      document
        .querySelector(".highlights-toolbar")
        ?.classList.toggle("has-random", enabled);
      return;
    }
    // 复制设置只影响已渲染页面的复制行为，不必重载：通知页面就地换格式
    if (
      field === "highlight_copy_default_action" ||
      field === "item_format" ||
      field === "separator"
    ) {
      document.dispatchEvent(
        new CustomEvent("highlights-copy-settings", {
          detail: { field, value },
        }),
      );
      return;
    }
    if (
      field === "enable_random_button" ||
      field === "random_mode" ||
      field === "random_target"
    ) {
      this.syncRandomControls();
    }
  }

  // 行是否可见：视图能力（data-drawer-views）+ 其它字段条件，一次算完
  syncConditionalRows() {
    const mode =
      this.element.querySelector('[name="bookmark_view_mode"]:checked')?.value ||
      "list";
    const listOnly = mode === "list";
    const previewOn = Boolean(
      this.element.querySelector('[name="enable_preview_images"]')?.checked,
    );
    const randomOn = Boolean(
      this.element.querySelector('[name="enable_random_button"]')?.checked,
    );
    const randomMode =
      this.element.querySelector('[name="random_mode"]')?.value || "list";
    const grouped = Boolean(
      this.element.querySelector('[name="group_by_date"]')?.checked,
    );

    const visible = {
      preview_placeholders: listOnly && previewOn,
      toolbar_auto_hide: listOnly,
      random_mode: randomOn,
      random_target: randomOn && randomMode === "single",
      collapse_groups: grouped,
    };
    Object.entries(visible).forEach(([name, shown]) =>
      this.setRowHidden(name, !shown),
    );

    // 只受视图影响的项（显示 URL、展开笔记、预览图等）
    this.element.querySelectorAll("[data-drawer-views]").forEach((row) => {
      if (row.dataset.drawerRow) return;
      const views = (row.dataset.drawerViews || "").split(",");
      row.hidden = !views.includes(mode);
    });

    this.syncModuleGates();
  }

  // 工具栏模块关掉后，对应的设置项一起收起
  syncModuleGates() {
    const list = this.element.querySelector(
      '[data-drawer-list="bookmark_toolbar_modules"]',
    );
    if (!list) return;
    const states = new Map(
      Array.from(list.querySelectorAll("[data-item-key]")).map((item) => [
        item.dataset.itemKey,
        Boolean(item.querySelector("[data-item-enabled]")?.checked),
      ]),
    );
    this.element.querySelectorAll("[data-drawer-gate]").forEach((section) => {
      const enabled = states.get(section.dataset.drawerGate);
      section.hidden = enabled === false;
    });
  }

  setRowHidden(name, hidden) {
    const row = this.element.querySelector(`[data-drawer-row="${name}"]`);
    if (row) row.hidden = hidden;
  }

  syncRandomControls() {
    const enabled = this.element.querySelector(
      '[name="enable_random_button"]',
    )?.checked;
    const mode =
      this.element.querySelector('[name="random_mode"]')?.value || "list";
    const target =
      this.element.querySelector('[name="random_target"]')?.value || "url";

    document.querySelectorAll("[data-random-control]").forEach((control) => {
      const single = control.hasAttribute("ld-random-single");
      control.hidden = !enabled || (single ? mode !== "single" : mode === "single");
    });

    // 窄屏工具栏按这个类在「随机 + 更多」与「批量编辑 / 布局 / 侧边栏」之间切换
    document
      .querySelector(".header-controls")
      ?.classList.toggle("has-random", Boolean(enabled));

    const single = document.querySelector("[ld-random-single]");
    if (single) single.dataset.randomTarget = target;
  }

  // ---------- 两个入口按钮 ----------

  async openQuickTags() {
    if (!this.quickTagsUrl) return;
    let html;
    try {
      const response = await fetch(this.quickTagsUrl, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      });
      if (!response.ok) return;
      html = await response.text();
    } catch (error) {
      console.error(error);
      return;
    }

    const title = this.element.dataset.labelQuickTags || gettext("Quick tags");
    const helpText = this.element.dataset.helpQuickTags || "";
    const modal = document.createElement("ld-modal");
    modal.className = "modal active";
    modal.innerHTML = `
      <div class="modal-overlay" data-close-modal></div>
      <div class="modal-container ld-modal-wide" role="dialog" aria-modal="true">
        <div class="modal-header">
          <h2>${title}${
            helpText
              ? `<span class="ld-help" data-drawer-help>
                   <button type="button" class="ld-help-button"
                           aria-label="${this.element.dataset.labelMoreInfo || gettext("More info")}" aria-expanded="false">
                     <svg width="16" height="16" aria-hidden="true"><use href="#ld-icon-info"></use></svg>
                   </button>
                   <span class="ld-help-text" hidden>${helpText}</span>
                 </span>`
              : ""
          }</h2>
          <button type="button" class="btn btn-noborder close" data-close-modal
                  aria-label="${this.element.dataset.labelClose || gettext("Close")}">
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"
                 stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round"
                 stroke-linejoin="round" aria-hidden="true">
              <path d="M18 6l-12 12"></path><path d="M6 6l12 12"></path>
            </svg>
          </button>
        </div>
        <div class="modal-body settings-page" ld-settings-page>${html}</div>
      </div>`;

    document.querySelector(".modals")?.appendChild(modal);
    // innerHTML 插入的 <script> 不会执行，重新插一遍让图标数据生效
    modal.querySelectorAll("script").forEach((script) => {
      const replacement = document.createElement("script");
      replacement.textContent = script.textContent;
      script.replaceWith(replacement);
    });
    // 关掉弹窗后刷新列表，快捷标签改动才会出现在行工具栏上
    modal.addEventListener("modal:close", () => this.scheduleListRefresh());
  }

  // ---------- 列表局部刷新 ----------

  scheduleListRefresh() {
    clearTimeout(this.refreshTimer);
    this.refreshTimer = setTimeout(() => this.refreshList(), REFRESH_DELAY);
  }

  async refreshList() {
    // 高亮页没有列表片段通道（改完直接重载），没有 URL 就什么都不做
    if (!this.fragmentUrl) return;
    const container = document.getElementById("bookmark-list-container");
    if (!container) return;

    const params = new URLSearchParams(window.location.search);
    params.delete("layout");
    if (this.resetPage) {
      params.delete("page");
      this.resetPage = false;
      // 地址栏也要丢掉页号，否则下次刷新又回到越界页
      const rest = params.toString();
      window.history.replaceState(
        {},
        "",
        `${window.location.pathname}${rest ? `?${rest}` : ""}`,
      );
    }
    params.set("ctx", this.page?.dataset.ctx || "active");

    const seq = ++this.refreshSeq;
    let html;
    try {
      const response = await fetch(`${this.fragmentUrl}?${params.toString()}`, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      });
      if (!response.ok) return;
      html = await response.text();
    } catch (error) {
      console.error(error);
      return;
    }

    // 后发先至时丢弃旧响应，避免把列表刷回上一版偏好
    if (seq !== this.refreshSeq) return;

    const template = document.createElement("template");
    template.innerHTML = html.trim();
    const next = template.content.querySelector("#bookmark-list-container");
    if (!next) return;

    container.replaceWith(next);
    document.dispatchEvent(new CustomEvent("bookmark-list-updated"));
    // 表格列宽按替换时的可用宽度算，切换视图后要等布局稳定再算一次
    requestAnimationFrame(() =>
      window.dispatchEvent(new Event("resize")),
    );
  }
}

registerBehavior("data-layout-drawer", LayoutDrawer);

// 抽屉是页面级浮层，缓存快照里不能带着展开态
document.addEventListener("turbo:before-cache", () => {
  document
    .querySelectorAll(".layout-drawer.is-open")
    .forEach((el) => el.classList.remove("is-open"));
  document.body.classList.remove("layout-drawer-open");
});
