import { queryBookmarkList } from "./utils/bookmark-list.js";

// Track mouse position for elementFromPoint lookups
let _lastMouseX = 0;
let _lastMouseY = 0;

document.addEventListener(
  "mousemove",
  (e) => {
    _lastMouseX = e.clientX;
    _lastMouseY = e.clientY;
  },
  { passive: true },
);

// 默认按键：与 UserProfile.DEFAULT_SHORTCUT_KEYS 保持一致，
// 在服务端未注入配置（如静态缓存页）时作为兜底。
// 方向键导航（↑/↓）固定不变，不参与配置。
const DEFAULT_KEYS = {
  new_bookmark: "n",
  focus_search: "s",
  quick_edit: "q",
  toggle_notes: "e",
};

function loadShortcutKeys() {
  const el = document.getElementById("ld-shortcut-keys");
  if (!el) return { ...DEFAULT_KEYS };
  try {
    const parsed = JSON.parse(el.textContent);
    if (!parsed || typeof parsed !== "object") return { ...DEFAULT_KEYS };
    return { ...DEFAULT_KEYS, ...parsed };
  } catch {
    return { ...DEFAULT_KEYS };
  }
}

// 空字符串表示禁用。
const shortcutKeys = loadShortcutKeys();
const keyByAction = {};
for (const [action, key] of Object.entries(shortcutKeys)) {
  if (key) {
    keyByAction[key] = action;
  }
}

document.addEventListener("keydown", (event) => {
  const targetNodeName = event.target.nodeName;
  const isInputTarget =
    targetNodeName === "INPUT" ||
    targetNodeName === "SELECT" ||
    targetNodeName === "TEXTAREA";

  if (isInputTarget) {
    return;
  }

  // 方向键导航：固定不变
  const isArrowUp = event.key === "ArrowUp";
  const isArrowDown = event.key === "ArrowDown";
  if (isArrowUp || isArrowDown) {
    event.preventDefault();

    const items = [
      ...document.querySelectorAll("ul.bookmark-list > li[ld-bookmark-item]"),
    ];
    const path = event.composedPath();
    const currentItem = path.find((item) => items.includes(item));

    let nextItem;
    if (currentItem) {
      nextItem = isArrowUp
        ? currentItem.previousElementSibling
        : currentItem.nextElementSibling;
    } else {
      nextItem = items[0];
    }
    nextItem?.querySelector("a.title-link")?.focus();
    return;
  }

  const action = keyByAction[event.key];
  if (!action) {
    return;
  }

  if (action === "toggle_notes") {
    const list = queryBookmarkList();
    if (!list) return;

    // 紧凑视图：笔记常驻会撑破单行，e 切换「hover 行时显示笔记」
    if (list.classList.contains("view-compact")) {
      list.classList.toggle("notes-hover");
      return;
    }

    const current = list.dataset.notesGlobal === "true";
    const next = !current;
    list.dataset.notesGlobal = String(next);
    list.querySelectorAll("li[ld-bookmark-item]").forEach((item) => {
      item.dataset.notesEnabled = String(next);
      item.classList.toggle("show-notes", next);
    });
    return;
  }

  if (action === "focus_search") {
    const searchInput = document.querySelector('input[type="search"]');
    if (searchInput) {
      searchInput.focus();
      event.preventDefault();
    }
    return;
  }

  if (action === "quick_edit") {
    const target = document.elementFromPoint(_lastMouseX, _lastMouseY);
    if (!target) return;

    const li = target.closest("li[ld-bookmark-item]");
    if (!li) return;

    // 表格视图：就地编辑所在单元格（标签走详情弹窗，需要补全菜单）
    const table = target.closest("ul.bookmark-list.view-table");
    if (table) {
      const field = target.closest(".cell-title, .title-link")
        ? "title"
        : target.closest(".cell-tags")
          ? "tags"
          : target.closest(".cell-desc")
            ? "description"
            : target.closest(".cell-notes")
              ? "notes"
              : target.closest(".cell-author")
                ? "author"
                : null;
      const behavior = table.__behaviors?.find(
        (b) => typeof b.editCell === "function",
      );
      if (field && behavior) {
        event.preventDefault();
        behavior.editCell(li, field);
      }
      return;
    }

    let fieldType;
    if (target.closest(".inline-edit-notes, .toggle-notes")) {
      fieldType = "notes";
    } else if (target.closest(".tags")) {
      // 内联描述里的标签也属于 .description-container，必须先判标签
      fieldType = "tags";
    } else if (target.closest(".author")) {
      fieldType = "author";
    } else if (target.closest(".description-container")) {
      fieldType = "description";
    } else if (target.closest(".title, .title-link")) {
      fieldType = "title";
    }
    if (!fieldType) return;

    const item = li.__behaviors?.find(
      (b) => typeof b.startQuickEdit === "function",
    );
    if (item) {
      event.preventDefault();
      item.startQuickEdit(fieldType);
    }
    return;
  }

  if (action === "new_bookmark") {
    window.location.assign("/bookmarks/new");
  }
});
