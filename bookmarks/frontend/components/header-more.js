import { Behavior, registerBehavior } from "./runtime.js";
import { getCSRFToken } from "../utils/csrf.js";
import { gettext } from "../utils/i18n.js";
import { showToast } from "./toast.js";
import { applyToolbarAutoHide } from "./toolbar-auto-hide.js";
import { saveColumnsFromPanel } from "./bookmark-table.js";

async function savePref(url, field, value, { appendEmpty = true } = {}) {
  const body = new FormData();
  body.append("csrfmiddlewaretoken", getCSRFToken());
  body.append("form_id", "profile_quick");
  body.append("form_fields", field);
  // 复选框为空表示未勾选，不能提交空值；其余字段要提交空串让服务端报「必填」
  if (value !== null && value !== undefined && (appendEmpty || value !== "")) {
    body.append(field, value);
  }
  const response = await fetch(url, {
    method: "POST",
    body,
    headers: {
      "X-CSRFToken": getCSRFToken(),
      "X-Requested-With": "XMLHttpRequest",
    },
  });
  if (response.ok) return { ok: true, errors: null };

  // 422 会带上字段错误（form.errors 的 JSON 形式），其余状态码没有可展示的细节
  const data = await response.json().catch(() => null);
  return { ok: false, errors: data?.errors?.[field] || null };
}

function applyStickyPref(field, enabled) {
  const selector =
    field === "sticky_header_controls"
      ? ".section-header[data-sticky-on]"
      : ".bookmark-pagination[data-sticky-on]";
  document
    .querySelectorAll(selector)
    .forEach((el) =>
      el.setAttribute("data-sticky-on", enabled ? "true" : "false"),
    );
}

// 「显示侧边栏」存的是默认值，实际开合在 localStorage（优先）与 cookie（服务端据此决定
// 是否懒加载）里。不先对齐这两处，重载后会被旧状态顶回去。
function syncSidebarState(enabled) {
  try {
    localStorage.removeItem("ld:sidebar-state:bookmarks");
    localStorage.removeItem("ld:sidebar-state:highlights");
  } catch {
    // 隐私模式下 localStorage 不可用，忽略
  }
  const cookieName = document.querySelector(".highlights-page")
    ? "ld_sidebar_highlights"
    : "ld_sidebar_bookmarks";
  document.cookie = `${cookieName}=${enabled ? "1" : "0"}; path=/; max-age=31536000; SameSite=Lax`;
}

// 随机控件两种模式各渲染一份，这里只切显隐，不改服务端渲染结果
function applyRandomMode(root, mode) {
  const single = mode === "single";
  document.querySelectorAll("[data-random-control]").forEach((control) => {
    control.hidden = control.matches("[ld-random-single]") !== single;
  });

  const targetRow = root.querySelector('[data-pref-row="random_target"]');
  if (targetRow) targetRow.hidden = !single;
}

function applyRandomTarget(target) {
  const button = document.querySelector("[ld-random-single]");
  if (button) button.dataset.randomTarget = target;
}

class HeaderMoreMenu extends Behavior {
  constructor(element) {
    super(element);
    this.url = this.element.dataset.saveUrl;
    this.onActionClick = this.onActionClick.bind(this);
    this.onPrefChange = this.onPrefChange.bind(this);
    this.onPrefInput = this.onPrefInput.bind(this);
    this.init();
  }

  init() {
    this.element.addEventListener("click", this.onActionClick);
    this.element.addEventListener("change", this.onPrefChange);
    this.element.addEventListener("input", this.onPrefInput);
  }

  fieldErrors(field) {
    return this.element.querySelector(
      `[data-field-error-for="${CSS.escape(field)}"]`,
    );
  }

  // 优先原地红字（复用设置页的字段错误样式），没有容器的字段退回 toast
  showFieldError(field, errors) {
    const target = this.fieldErrors(field);
    this.element
      .querySelector(`[name="${CSS.escape(field)}"]`)
      ?.classList.add("is-error");
    if (!target) return false;

    const message = (errors || []).map((error) => error.message).join(" ");
    target.textContent =
      message || gettext("Please review the highlighted fields.");
    target.classList.add("is-visible");
    return true;
  }

  clearFieldError(field) {
    const target = this.fieldErrors(field);
    if (target) {
      target.textContent = "";
      target.classList.remove("is-visible");
    }
    this.element
      .querySelector(`[name="${CSS.escape(field)}"]`)
      ?.classList.remove("is-error");
  }

  onPrefInput(event) {
    const input = event.target.closest("[data-pref]");
    if (input) this.clearFieldError(input.dataset.pref);
  }

  onActionClick(event) {
    const actionButton = event.target.closest(".header-menu-action");
    if (!actionButton) return;
    const action = actionButton.dataset.menuAction;

    if (action === "bulk-edit") {
      document.querySelector(".bulk-edit-active-toggle")?.click();
    } else if (action === "random-sort") {
      // 两种随机模式各有一份控件，点当前可见的那个
      const single = document.querySelector("[ld-random-single]:not([hidden])");
      if (single) {
        single.click();
      } else {
        document.querySelector("form[data-random-control]")?.requestSubmit();
      }
    }

    this.element.closest("ld-dropdown")?.close?.();
  }

  async onPrefChange(event) {
    // 表格列显隐：整组勾选打包成一个 JSON 字段保存，不走单字段通道。
    // 只收集触发变化的面板——宽屏布局菜单与窄屏更多菜单各渲染一份，跨面板合并会用旧状态顶回去。
    const tableCol = event.target.closest("[data-table-col]");
    if (tableCol) {
      if (!event.target.disabled) {
        saveColumnsFromPanel(tableCol.closest("[data-table-columns]"));
      }
      return;
    }
    const input = event.target.closest("[data-pref]");
    if (!input) return;
    const field = input.dataset.pref;
    const isCheckbox = input.type === "checkbox";
    const value = isCheckbox ? (input.checked ? "on" : "") : input.value;

    let response;
    try {
      response = await savePref(this.url, field, value, {
        appendEmpty: !isCheckbox,
      });
    } catch (error) {
      console.error(error);
      showToast(gettext("Couldn't save settings. Please try again."), {
        tone: "error",
      });
      return;
    }

    const { ok, errors } = response;
    if (!ok) {
      if (!errors || !this.showFieldError(field, errors)) {
        showToast(gettext("Couldn't save settings. Please try again."), {
          tone: "error",
        });
      }
      return;
    }
    this.clearFieldError(field);

    // 参与服务端渲染的字段在模板上标了 data-pref-reload，改完只能重载
    if (input.hasAttribute("data-pref-reload")) {
      if (field === "show_sidebar") syncSidebarState(Boolean(value));
      window.location.reload();
      return;
    }

    const enabled = Boolean(value);
    if (field === "bookmark_toolbar_auto_hide") {
      applyToolbarAutoHide(enabled);
    } else if (field === "random_mode") {
      applyRandomMode(this.element, value);
    } else if (field === "random_target") {
      applyRandomTarget(value);
    } else if (
      field === "sticky_header_controls" ||
      field === "sticky_pagination"
    ) {
      applyStickyPref(field, enabled);
    }
  }
}

registerBehavior("data-header-more", HeaderMoreMenu);
registerBehavior("data-header-prefs", HeaderMoreMenu);
