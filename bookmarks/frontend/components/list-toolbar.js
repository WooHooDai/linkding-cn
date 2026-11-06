import { Behavior, registerBehavior } from "./runtime.js";
import { getCSRFToken } from "../utils/csrf.js";
import { gettext } from "../utils/i18n.js";
import { showToast } from "./toast.js";
import { refreshBookmarkList } from "../utils/list-refresh.js";

// 列表工具栏第二行：排序 / 分组 / 全部展开折叠 + 操作按钮。
// 开合状态只记在本地，不进 profile——它跟视口宽度和当下任务走，不是长期偏好。
const EXPANDED_KEY = "ld:list-toolbar:expanded";

function readExpanded() {
  try {
    return localStorage.getItem(EXPANDED_KEY) !== "false";
  } catch {
    return true;
  }
}

function writeExpanded(expanded) {
  try {
    localStorage.setItem(EXPANDED_KEY, expanded ? "true" : "false");
  } catch {}
}

async function savePref(url, field, value) {
  const body = new FormData();
  body.append("csrfmiddlewaretoken", getCSRFToken());
  body.append("form_id", "profile_quick");
  body.append("form_fields", field);
  // 布尔字段的空值不能提交（BooleanField 认不了空串），字段缺席即表示未勾选
  if (value !== "") body.append(field, value);

  const response = await fetch(url, {
    method: "POST",
    body,
    headers: {
      "X-CSRFToken": getCSRFToken(),
      "X-Requested-With": "XMLHttpRequest",
    },
  });
  return response.ok;
}

class ListToolbar extends Behavior {
  constructor(element) {
    super(element);
    this.url = element.dataset.saveUrl;
    this.toggle = element.querySelector("[data-tools-toggle]");
    this.tools = element.querySelector("[data-tools-row]");
    this.peeked = false;
    this.onChange = this.onChange.bind(this);
    this.onListUpdated = this.onListUpdated.bind(this);
    this.onPointerLeave = this.onPointerLeave.bind(this);
    this.init();
  }

  init() {
    this.element.addEventListener("change", this.onChange);
    // 片段刷新会重渲染列表，分组状态以容器上的 data-grouped 为准
    document.addEventListener("bookmark-list-updated", this.onListUpdated);
    this.toggle?.addEventListener("click", () => {
      this.setExpanded(!this.isExpanded());
      this.clearPeek();
    });
    // 悬浮临时展开：收起态下扫一眼第二行不用先点开
    this.toggle?.addEventListener("pointerenter", (event) => {
      if (event.pointerType === "mouse") this.peekOpen();
    });
    this.element.addEventListener("pointerleave", this.onPointerLeave);
    this.setExpanded(readExpanded(), { persist: false });
    this.syncGroupState();
  }

  destroy() {
    this.element.removeEventListener("change", this.onChange);
    this.element.removeEventListener("pointerleave", this.onPointerLeave);
    document.removeEventListener("bookmark-list-updated", this.onListUpdated);
  }

  // 操作按钮在窄屏属于第二行，收起时一并隐藏——交给 CSS 按断点处理，
  // 免得视口跨断点时还要重新分配一遍。
  applyVisibility(visible) {
    if (this.tools) this.tools.hidden = !visible;
  }

  isExpanded() {
    return this.toggle?.getAttribute("aria-expanded") === "true";
  }

  setExpanded(expanded, { persist = true } = {}) {
    if (!this.tools || !this.toggle) return;
    this.applyVisibility(expanded);
    this.toggle.setAttribute("aria-expanded", String(expanded));
    this.element.classList.toggle("tools-collapsed", !expanded);
    if (persist) writeExpanded(expanded);
  }

  peekOpen() {
    if (this.isExpanded() || !this.tools) return;
    this.peeked = true;
    this.applyVisibility(true);
    this.element.classList.add("tools-peek");
  }

  // 指针在标题/工具行内任意移动都算「还在看」，整体移出才收起
  onPointerLeave() {
    if (!this.peeked) return;
    this.clearPeek();
    if (this.isExpanded()) return;
    this.applyVisibility(false);
  }

  clearPeek() {
    this.peeked = false;
    this.element.classList.remove("tools-peek");
  }

  // 分组开关同时存在于布局抽屉与这里，两边都改同一份 profile 状态；
  // 列表换成新片段后以 data-grouped 回填控件，避免两处显示不一致。
  syncGroupState() {
    // 高亮页没有列表片段（分组在服务端渲染时已定），无需回填
    const container = document.getElementById("bookmark-list-container");
    if (!container) return;
    const grouped = container.hasAttribute("data-grouped");
    this.element
      .querySelectorAll('[data-toolbar-pref="group_by_date"]')
      .forEach((select) => (select.value = grouped ? "on" : ""));
    this.element
      .querySelectorAll(".header-group-actions")
      .forEach((actions) => (actions.hidden = !grouped));
  }

  onListUpdated() {
    this.syncGroupState();
  }

  async onChange(event) {
    const dropdown = event.target.closest("ld-dropdown");

    // 排序 / 分组这类改完就走的导航：只换这一个参数，其余筛选原样带走
    const nav = event.target.closest("[data-toolbar-nav]");
    if (nav) {
      dropdown?.close?.();
      const url = new URL(window.location.href);
      url.searchParams.set(nav.dataset.toolbarNav, nav.value);
      url.searchParams.delete("page");
      if (window.Turbo) window.Turbo.visit(url.toString());
      else window.location.assign(url.toString());
      return;
    }

    const input = event.target.closest("[data-toolbar-pref]");
    if (!input) return;
    // 选项已经生效，弹层继续挡着列表没有意义
    dropdown?.close?.();

    const field = input.dataset.toolbarPref;
    const apply = input.dataset.toolbarPrefApply;
    const ok = await savePref(this.url, field, input.value);
    if (!ok) {
      showToast(gettext("Couldn't save settings. Please try again."), {
        tone: "error",
      });
      return;
    }
    // 布局抽屉里还有同一份设置，打开着的抽屉要跟着改
    document.dispatchEvent(
      new CustomEvent("ld:pref-changed", {
        detail: { field, value: input.value },
      }),
    );
    if (apply === "fragment") await refreshBookmarkList();
  }
}

registerBehavior("ld-list-toolbar", ListToolbar);
