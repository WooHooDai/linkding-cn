// Track mouse position for elementFromPoint lookups
let _lastMouseX = 0;
let _lastMouseY = 0;

document.addEventListener("mousemove", (e) => {
  _lastMouseX = e.clientX;
  _lastMouseY = e.clientY;
}, { passive: true });

document.addEventListener("keydown", (event) => {
  const targetNodeName = event.target.nodeName;
  const isInputTarget =
    targetNodeName === "INPUT" ||
    targetNodeName === "SELECT" ||
    targetNodeName === "TEXTAREA";

  if (isInputTarget) {
    return;
  }

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
  }

  if (event.key === "e") {
    const list = document.querySelector(".bookmark-list");
    if (!list) return;
    const current = list.dataset.notesGlobal === "true";
    const next = !current;
    list.dataset.notesGlobal = String(next);
    list.querySelectorAll("li[ld-bookmark-item]").forEach((item) => {
      item.dataset.notesEnabled = String(next);
      item.classList.toggle("show-notes", next);
    });
  }

  if (event.key === "s") {
    const searchInput = document.querySelector('input[type="search"]');
    if (searchInput) {
      searchInput.focus();
      event.preventDefault();
    }
  }

  if (event.key === "q") {
    const target = document.elementFromPoint(_lastMouseX, _lastMouseY);
    if (!target) return;

    const li = target.closest("li[ld-bookmark-item]");
    if (!li) return;

    let fieldType;
    if (target.closest(".inline-edit-notes, .toggle-notes")) {
      fieldType = "notes";
    } else if (target.closest(".tags")) {
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

  if (event.key === "n") {
    window.location.assign("/bookmarks/new");
  }

  // 专键 a：直接进入当前书签的作者编辑（键盘导航以焦点项为准，鼠标操作回退 hover 项）
  if (event.key === "a") {
    const list = document.querySelector(".bookmark-list");
    if (!list) return;

    const activeElement = document.activeElement;
    let li =
      activeElement?.closest?.("li[ld-bookmark-item]") || null;
    if (!li) {
      const target = document.elementFromPoint(_lastMouseX, _lastMouseY);
      li = target?.closest?.("li[ld-bookmark-item]") || null;
    }
    if (!li) return;

    const item = li.__behaviors?.find(
      (b) => typeof b.startQuickEdit === "function",
    );
    if (item) {
      event.preventDefault();
      item.startQuickEdit("author");
    }
  }
});
