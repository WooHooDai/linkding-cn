import Sortable from "sortablejs";
import { Behavior, registerBehavior } from "./runtime.js";
import { getCSRFToken } from "../utils/csrf.js";
import { gettext } from "../utils/i18n.js";
import { showToast } from "./toast.js";
import { patchBookmark } from "./bookmark-page.js";

const MIN_FR = 0.35;
const MAX_FR = 8.0;
/** 每列「可读」的最小像素宽度：低于它就切横向滚动，而不是继续压缩 */
const MIN_COL_PX = 120;

/** 列 key → 单元格 class 后缀 */
const CELL_CLASS = {
  title: "title",
  domain: "domain",
  tags: "tags",
  desc: "desc",
  notes: "notes",
  date: "date",
  status: "status",
  author: "author",
};

/** 可就地编辑的字段 → 单元格选择器 */
const EDIT_CELL = {
  title: ".cell-title",
  description: ".cell-desc",
  author: ".cell-author",
  notes: ".cell-notes",
};

/** 字段 → 需要回写的文本节点 */
const EDIT_TEXT = {
  title: ".title-link span",
  description: ".description-text",
  notes: ".note-text",
  author: ".author-inner, .author",
};

function parseConfig(ul) {
  try {
    const config = JSON.parse(ul?.dataset.tableConfig || "[]");
    return Array.isArray(config) ? config : [];
  } catch {
    return [];
  }
}

function clampW(value) {
  if (!Number.isFinite(value)) return 1;
  return Math.min(MAX_FR, Math.max(MIN_FR, Math.round(value * 100) / 100));
}

function visibleColumns(config) {
  return config.filter((c) => c.on !== false);
}

function applyGrid(ul, visible) {
  ul.style.setProperty(
    "--ld-table-cols",
    visible.map((c) => `minmax(0, ${c.w}fr)`).join(" "),
  );
}

function saveUrl() {
  return document.querySelector("[data-header-prefs], [data-header-more]")
    ?.dataset.saveUrl;
}

/** 列配置保存：显隐变化需要服务端重渲染，故调用方决定是否重载 */
export async function saveTableConfig(config) {
  const url = saveUrl();
  if (!url) return false;
  const body = new FormData();
  body.append("csrfmiddlewaretoken", getCSRFToken());
  body.append("form_id", "profile_quick");
  body.append("form_fields", "bookmark_table_columns");
  body.append("bookmark_table_columns", JSON.stringify(config));
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

/** 就地保存（宽/序）：界面已由前端更新，无需重载页面。
    同载荷在短窗口内只发一次——拖拽可能因重复事件回调多次触发。 */
let lastSave = { key: "", at: 0 };
function saveQuiet(config) {
  if (!Array.isArray(config) || !config.length) return;
  const key = JSON.stringify(config);
  const now = Date.now();
  if (key === lastSave.key && now - lastSave.at < 800) return;
  lastSave = { key, at: now };
  saveTableConfig(config).catch(() => {
    showToast(gettext("Couldn't save settings. Please try again."), {
      tone: "error",
    });
  });
}

/** 工具条勾选变化：显隐要服务端重渲染列结构，保存后重载 */
export function saveColumnsFromPanel(panel) {
  const checkboxes = panel
    ? panel.querySelectorAll("[data-table-col]")
    : document.querySelectorAll("[data-table-col]");
  if (!checkboxes.length) return;
  const checked = new Set(
    [...checkboxes].filter((el) => el.checked).map((el) => el.dataset.tableCol),
  );
  const ul = document.querySelector("ul.bookmark-list.view-table");
  const config = parseConfig(ul).map((c) => ({
    ...c,
    on: checked.has(c.key) || c.key === "title",
  }));
  if (!config.length) return;
  saveTableConfig(config).then((ok) => {
    if (ok) {
      window.location.reload();
    } else {
      showToast(gettext("Couldn't save settings. Please try again."), {
        tone: "error",
      });
    }
  });
}

/** 单行省略文本是否真的被截断（未截断的格点击不响应） */
function isTruncated(el) {
  const box = el.closest(".truncate") || el;
  return box.scrollWidth > box.clientWidth + 1;
}

class BookmarkTable extends Behavior {
  constructor(element) {
    super(element);
    this.onPointerDown = this.onPointerDown.bind(this);
    this.onClick = this.onClick.bind(this);
    this.onDblClick = this.onDblClick.bind(this);
    this.onToolbarEdit = this.onToolbarEdit.bind(this);
    this.syncStickyTop = this.syncStickyTop.bind(this);
    this.syncScrollMode = this.syncScrollMode.bind(this);
    this.init();
  }

  init() {
    const ul = this.element;
    // Turbo 局部重渲染下 behavior 可能被再挂一次（模块双实例会绕过 instanceof 去重），
    // 事件与 Sortable 只允许绑定一次，否则一次拖拽会发出多次保存
    if (ul.dataset.ldTableBound === "1") return;
    ul.dataset.ldTableBound = "1";
    ul.addEventListener("pointerdown", this.onPointerDown);
    ul.addEventListener("click", this.onClick);
    ul.addEventListener("dblclick", this.onDblClick);
    // 快捷编辑按钮在捕获阶段接管：直接进对应单元格的编辑态
    ul.addEventListener("click", this.onToolbarEdit, true);
    // 健康弹层：状态列与工具栏浮层里都有，统一按可用空间决定向左/向右、向上/向下展开
    const placeHealth = (event) => {
      const indicator = event.target.closest(".health-status");
      if (indicator && ul.contains(indicator)) {
        this.positionHealthPopover(indicator);
      }
    };
    ul.addEventListener("pointerover", placeHealth);
    ul.addEventListener("focusin", placeHealth);
    // 点击展开时（自动隐藏模式下浮层会“在光标底下冒出来”，可能没有新的 pointerover）补一次定位
    ul.addEventListener("click", (event) => {
      const indicator = event.target.closest(".health-status");
      if (indicator && ul.contains(indicator)) {
        requestAnimationFrame(() => this.positionHealthPopover(indicator));
      }
    });
    this.syncStickyTop();
    this.syncScrollMode();
    window.addEventListener("resize", this.syncStickyTop);
    window.addEventListener("resize", this.syncScrollMode);
    // 侧边栏开合等布局变化不一定触发 window.resize，用 ResizeObserver 兜住
    const container = ul.closest("#bookmark-list-container");
    if (container && "ResizeObserver" in window) {
      this.resizeObserver = new ResizeObserver(this.syncScrollMode);
      this.resizeObserver.observe(container);
    }
    // 搜索栏跟随开关切换会改 section-header 的吸顶状态，跟着重算垫高
    const sectionHeader = document.querySelector(".section-header");
    if (sectionHeader) {
      new MutationObserver(this.syncStickyTop).observe(sectionHeader, {
        attributes: true,
        attributeFilter: ["data-sticky-on"],
      });
    }
    this.initColumnSortable();
  }

  destroy() {
    window.removeEventListener("resize", this.syncStickyTop);
    window.removeEventListener("resize", this.syncScrollMode);
    this.resizeObserver?.disconnect();
    delete this.element.dataset.ldTableBound;
  }

  /* 表头吸顶：跟随 section-header（吸顶时垫在其下，高度动态测得） */
  syncStickyTop() {
    const stickyHeader = document.querySelector(
      '.section-header[data-sticky-on="true"]',
    );
    const top = stickyHeader ? stickyHeader.offsetHeight : 0;
    this.element.style.setProperty("--ld-table-top", `${top}px`);
  }

  /* 可用宽度不足（含桌面窄窗口）时切横向滚动：按列数算最小可读宽度，
     够放就不出滚动条（表头粘性保留），放不下才让容器可横向拖动 */
  syncScrollMode() {
    const ul = this.element;
    const container = ul.closest("#bookmark-list-container");
    if (!container) return;
    const cols = ul.querySelectorAll(".table-header .th").length;
    if (!cols) return;
    const minWidth = cols * MIN_COL_PX + 26;
    const need = minWidth > container.clientWidth;
    container.classList.toggle("ld-table-hscroll", need);
    ul.style.minWidth = need ? `${minWidth}px` : "";
    const bar = this.ensureHScrollBar(container);
    bar.hidden = !need;
    this.hscrollWidth = need ? minWidth : container.clientWidth;
    if (!need) this.hscrollX = 0;
    this.element.style.setProperty(
      "--ld-hscroll-x",
      `${-(this.hscrollX || 0)}px`,
    );
    this.updateHScrollThumb();
  }

  /* 常驻视口底部的横向滚动条（自绘滑块）：
     拖动滑块等于平移表格，容器本身保持 clip 不滚动，sticky 才不受影响 */
  ensureHScrollBar(container) {
    if (!this.hbar) {
      const bar = document.createElement("div");
      bar.className = "ld-hscroll-bar";
      bar.hidden = true;
      bar.innerHTML = '<div class="ld-hscroll-thumb"></div>';
      container.insertAdjacentElement("afterend", bar);
      this.hbar = bar;
      this.hthumb = bar.firstElementChild;
      this.bindHScrollBar(bar, this.hthumb);
    }
    return this.hbar;
  }

  bindHScrollBar(bar, thumb) {
    const ratio = () => {
      const overflow = this.hscrollWidth - bar.clientWidth;
      const travel = bar.clientWidth - thumb.offsetWidth;
      return { overflow, travel };
    };

    const onMove = (event) => {
      const { overflow, travel } = ratio();
      if (overflow <= 0 || travel <= 0) return;
      const startLeft =
        this._dragStartLeft + (event.clientX - this._dragStartX);
      const left = Math.max(0, Math.min(travel, startLeft));
      this.setScrollX((left / travel) * overflow);
    };
    const onUp = () => {
      thumb.classList.remove("dragging");
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };

    thumb.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      this._dragStartX = event.clientX;
      this._dragStartLeft = thumb.offsetLeft;
      thumb.classList.add("dragging");
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    });

    // 点轨道空白处：滑块中心跳到点击位置
    bar.addEventListener("pointerdown", (event) => {
      if (event.target === thumb) return;
      const { overflow, travel } = ratio();
      if (overflow <= 0 || travel <= 0) return;
      const left = Math.max(
        0,
        Math.min(
          travel,
          event.clientX -
            bar.getBoundingClientRect().left -
            thumb.offsetWidth / 2,
        ),
      );
      this.setScrollX((left / travel) * overflow);
    });
  }

  setScrollX(x) {
    const clamped = Math.max(
      0,
      Math.min(this.hscrollWidth - this.hbar.clientWidth, x),
    );
    this.hscrollX = clamped;
    this.element.style.setProperty("--ld-hscroll-x", `${-clamped}px`);
    this.updateHScrollThumb();
  }

  updateHScrollThumb() {
    const bar = this.hbar;
    const thumb = this.hthumb;
    if (!bar || !thumb || bar.hidden) return;
    const trackW = bar.clientWidth;
    const overflow = this.hscrollWidth - trackW;
    const ratio = trackW / this.hscrollWidth;
    const width = Math.max(40, Math.round(trackW * ratio));
    const travel = trackW - width;
    const left = overflow > 0 ? (this.hscrollX / overflow) * travel : 0;
    thumb.style.width = `${width}px`;
    thumb.style.left = `${Math.max(0, Math.min(travel, Math.round(left)))}px`;
  }

  /* ── 列宽拖拽：实时改 CSS 变量，松手静默保存（不重载） ── */
  onPointerDown(event) {
    const handle = event.target.closest(".col-rz");
    if (!handle) return;
    event.preventDefault();

    const ul = this.element;
    const fullConfig = parseConfig(ul);
    const visible = visibleColumns(fullConfig);
    const headerCells = ul.querySelectorAll(".table-header .th");
    const index = [...headerCells].findIndex((th) => th.contains(handle));
    if (index < 0 || !visible[index]) return;

    handle.classList.add("dragging");
    handle.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startPx = headerCells[index].getBoundingClientRect().width;
    const startFr = visible[index].w;
    let dirty = false;
    let finished = false;

    const onMove = (moveEvent) => {
      const px = Math.max(40, startPx + moveEvent.clientX - startX);
      const nextW = clampW((startFr * px) / startPx);
      if (nextW === visible[index].w) return;
      visible[index].w = nextW;
      applyGrid(ul, visible);
      dirty = true;
    };
    const onUp = () => {
      // 幂等：捕获丢失等情况下 pointerup 可能多次到达，保证一次拖拽只保存一次
      if (finished) return;
      finished = true;
      handle.classList.remove("dragging");
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
      if (!dirty) return;
      const merged = fullConfig.map(
        (c) => visible.find((v) => v.key === c.key) || c,
      );
      saveQuiet(merged);
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
  }

  /* ── 表头拖拽排序：就地重排 DOM + 静默保存，不刷新页面 ── */
  initColumnSortable() {
    const headerRow = this.element.querySelector(".table-header");
    if (!headerRow) return;
    Sortable.create(headerRow, {
      draggable: ".th",
      filter: ".col-rz, a",
      animation: 150,
      ghostClass: "is-dragging",
      onEnd: () => {
        const order = [...headerRow.querySelectorAll(".th")].map(
          (th) => th.dataset.key,
        );
        const config = parseConfig(this.element);
        const current = visibleColumns(config).map((c) => c.key);
        // 顺序没变（拖了又落回原位）：不发请求、不重排
        if (order.join() === current.join()) return;
        const byKey = Object.fromEntries(config.map((c) => [c.key, c]));
        const reordered = order.map((key) => byKey[key]).filter(Boolean);
        const hidden = config.filter((c) => c.on === false);
        const next = [...reordered, ...hidden];
        applyGrid(this.element, reordered);
        this.element.dataset.tableConfig = JSON.stringify(next);
        this.applyColumnOrder(reordered.map((c) => c.key));
        saveQuiet(next);
      },
    });
  }

  /** 数据行按新列序重排：格子 DOM 顺序 + 显式轨道号，浮层与操作入口保持末尾 */
  applyColumnOrder(keys) {
    const ul = this.element;
    ul.querySelectorAll("li[ld-bookmark-item]").forEach((li) => {
      keys.forEach((key, i) => {
        const suffix = CELL_CLASS[key];
        const cell = suffix && li.querySelector(`:scope > .cell-${suffix}`);
        if (!cell) return;
        cell.style.gridColumn = String(i + 2);
        li.appendChild(cell);
      });
      const actions = li.querySelector(":scope > .actions");
      if (actions) li.appendChild(actions);
      const ops = li.querySelector(":scope > .row-ops-btn");
      if (ops) li.appendChild(ops);
    });
  }

  /** 健康弹层定位：表格会裁切内容，宽高与展开方向都按「表格可视区 ∩ 视口」算 */
  positionHealthPopover(indicator) {
    const popover = indicator.querySelector(".health-popover");
    if (!popover) return;
    const rect = indicator.getBoundingClientRect();
    const ulRect = this.element.getBoundingClientRect();
    const GAP = 8;

    const boundsLeft = Math.max(ulRect.left, 0);
    const boundsRight = Math.min(ulRect.right, window.innerWidth);
    const boundsTop = Math.max(ulRect.top, 0);
    const boundsBottom = Math.min(ulRect.bottom, window.innerHeight);

    const spaceRight = boundsRight - rect.left - GAP;
    const spaceLeft = rect.right - boundsLeft - GAP;
    const maxWidth = Math.max(
      120,
      Math.min(320, Math.max(spaceRight, spaceLeft)),
    );

    // display:none 时量不到尺寸，先临时显形
    const hidden = getComputedStyle(popover).display === "none";
    if (hidden) {
      popover.style.display = "flex";
      popover.style.visibility = "hidden";
    }
    popover.style.width = "max-content";
    popover.style.maxWidth = `${Math.round(maxWidth)}px`;
    popover.style.maxHeight = "";
    const box = popover.getBoundingClientRect();
    if (hidden) {
      popover.style.display = "";
      popover.style.visibility = "";
    }

    popover.style.position = "absolute";
    popover.style.zIndex = "80";
    if (spaceRight >= box.width) {
      popover.style.left = "0";
      popover.style.right = "auto";
    } else {
      popover.style.left = "auto";
      popover.style.right = "0";
    }

    // 纵向：优先向下；下方空间不足时换向上；两侧都不够就限高滚动
    const spaceBelow = boundsBottom - rect.bottom - GAP - 6;
    const spaceAbove = rect.top - boundsTop - GAP - 6;
    if (spaceBelow >= box.height || spaceBelow >= spaceAbove) {
      popover.style.top = "calc(100% + 6px)";
      popover.style.bottom = "auto";
      if (spaceBelow < box.height) {
        popover.style.maxHeight = `${Math.max(56, Math.round(spaceBelow))}px`;
        popover.style.overflowY = "auto";
      } else {
        popover.style.overflowY = "";
      }
    } else {
      popover.style.top = "auto";
      popover.style.bottom = "calc(100% + 6px)";
      if (spaceAbove < box.height) {
        popover.style.maxHeight = `${Math.max(56, Math.round(spaceAbove))}px`;
        popover.style.overflowY = "auto";
      } else {
        popover.style.overflowY = "";
      }
    }
  }

  /* ── 单元格内编辑 ─────────────────────────
     编辑器绝对定位覆盖所在格子，不参与 grid 轨道计算——行高稳定；
     多行输入向下溢出（编辑时格子放开 overflow）。 */
  editCell(li, field) {
    if (field === "tags") {
      this.editTags(li);
      return;
    }
    const cell = li?.querySelector(EDIT_CELL[field]);
    if (!cell || cell.classList.contains("is-editing")) return;
    const current = (
      cell.querySelector(EDIT_TEXT[field])?.textContent || ""
    ).trim();
    const multiline = field === "description" || field === "notes";

    const editor = document.createElement(multiline ? "textarea" : "input");
    editor.className = "table-cell-editor";
    if (multiline) {
      // 单行起步：高度完全由内容决定（textarea 默认两行会多出一行空白）
      editor.rows = 1;
    } else {
      editor.type = "text";
    }
    editor.value = current;

    const MAX_HEIGHT = 240;
    // 高度跟随内容、封顶 MAX_HEIGHT：box-sizing 是 border-box，
    // 直接取 scrollHeight 会少算边框，导致内容不满也出现滚动条
    const grow = () => {
      if (!multiline) return;
      editor.style.height = "auto";
      const border = editor.offsetHeight - editor.clientHeight;
      const contentHeight = editor.scrollHeight + border;
      editor.style.height = `${Math.min(contentHeight, MAX_HEIGHT)}px`;
      editor.style.overflowY = contentHeight > MAX_HEIGHT ? "auto" : "hidden";
    };

    cell.classList.add("is-editing");
    cell.appendChild(editor);
    editor.focus();
    // 统一把光标放在末尾（与列表视图的编辑体验一致）
    editor.setSelectionRange(current.length, current.length);
    // 先定宽再量高：宽度变了换行数就变，顺序反过来会量出偏高的高度
    requestAnimationFrame(() => {
      this.placeEditor(editor, cell, field);
      grow();
    });

    let closed = false;
    const close = (save) => {
      if (closed) return;
      closed = true;
      const value = editor.value.trim();
      editor.remove();
      cell.classList.remove("is-editing");
      if (!save || value === current) return;
      patchBookmark(li.dataset.bookmarkId, { [field]: value })
        .then(() => {
          let target = cell.querySelector(EDIT_TEXT[field]);
          // 原本为空的格没有文本节点，补一个再写入，避免要刷新才见
          if (!target && field === "description") {
            target = document.createElement("span");
            target.className = "description-text";
            cell.querySelector(".description-container")?.appendChild(target);
          } else if (!target && field === "notes") {
            target = document.createElement("span");
            target.className = "note-text";
            cell.querySelector(".note-container")?.appendChild(target);
          }
          if (target) target.textContent = value;
          if (field === "title") {
            cell.querySelector(".title-link")?.setAttribute("title", value);
          }
        })
        .catch(() =>
          showToast(gettext("Couldn't save changes. Please try again."), {
            tone: "error",
          }),
        );
    };

    editor.addEventListener("input", grow);
    editor.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close(false);
      } else if (
        event.key === "Enter" &&
        (!multiline || event.metaKey || event.ctrlKey)
      ) {
        // 单行：回车保存；多行：回车换行，Ctrl/Cmd+Enter 才保存
        event.preventDefault();
        close(true);
      }
    });
    editor.addEventListener("blur", () => close(true));
  }

  /** 编辑器统一尺寸：至少与标签编辑器同宽（420px），右侧放不下就向左展开 */
  placeEditor(editor, cell, field) {
    const ulRect = this.element.getBoundingClientRect();
    const cellRect = cell.getBoundingClientRect();
    const PREFERRED = 420;
    const GAP = 12;
    const availRight = ulRect.right - cellRect.left - GAP;
    const availLeft = cellRect.right - ulRect.left - GAP;
    const maxWidth = Math.max(availRight, availLeft, 160);
    let width = Math.min(maxWidth, Math.max(PREFERRED, cellRect.width));

    if (field === "title") {
      // 标题：内容更长时继续放宽，保证一行显示完整
      const ctx = (this._measure ||=
        document.createElement("canvas")).getContext("2d");
      const cs = getComputedStyle(editor);
      ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
      width = Math.min(
        maxWidth,
        Math.max(width, ctx.measureText(editor.value).width + 32),
      );
    }

    const left = availRight >= width ? 0 : Math.round(cellRect.width - width);
    editor.style.right = "auto";
    editor.style.left = `${left}px`;
    editor.style.width = `${Math.round(width)}px`;
  }

  /** 标签：复用列表的标签编辑器（带补全菜单），保存后由它自己重建 chips */
  editTags(li) {
    const item = li?.__behaviors?.find(
      (b) => typeof b.startQuickEdit === "function",
    );
    if (!item) return;
    item.startQuickEdit("tags");
    // 编辑器插入后再定位：靠右的列向左展开，避免右侧被表格边缘裁掉
    requestAnimationFrame(() => this.placeTagsEditor(li));
  }

  placeTagsEditor(li) {
    const editor = li?.querySelector(".quick-edit-tags-container");
    const cell = editor?.closest(".cell-tags");
    if (!editor || !cell) return;
    editor.style.position = "absolute";
    editor.style.top = "0";
    editor.style.zIndex = "25";
    this.placeEditor(editor, cell, "tags");
  }

  /* 操作栏里的快捷编辑按钮：直接进对应单元格的编辑态 */
  onToolbarEdit(event) {
    const btn = event.target.closest(".quick-edit-btn, .toggle-notes");
    if (!btn) return;
    const li = btn.closest("li[ld-bookmark-item]");
    if (!li) return;
    const field =
      btn.dataset.quickEdit ||
      (btn.classList.contains("toggle-notes") ? "notes" : null);
    if (!field) return;
    event.preventDefault();
    event.stopPropagation();
    this.editCell(li, field);
  }

  onClick(event) {
    if (event.target.closest(".table-cell-editor")) return;

    const opsBtn = event.target.closest(".row-ops-btn");
    if (opsBtn) {
      const li = opsBtn.closest("li[ld-bookmark-item]");
      hideAllToolbarFloats(li);
      li.classList.toggle("toolbar-visible");
      opsBtn.setAttribute(
        "aria-expanded",
        li.classList.contains("toolbar-visible") ? "true" : "false",
      );
      return;
    }

    // 描述/笔记：整格点击切换展开（截断才展开）；点链接不参与
    const expandCell = event.target.closest(".cell-desc, .cell-notes");
    if (expandCell && !event.target.closest("a")) {
      const text = expandCell.querySelector(".description-text, .note-text");
      if (text) {
        if (text.classList.contains("expanded")) {
          text.classList.remove("expanded");
        } else if (isTruncated(text) || text.textContent.includes("\n")) {
          // 有换行的内容即使一行放得下也值得展开（阅读体验更好）
          text.classList.add("expanded");
        }
        return;
      }
    }

    const tagsCell = event.target.closest(".cell-tags");
    if (tagsCell) {
      const chip = event.target.closest("a");
      if (chip) {
        const cellRight = tagsCell.getBoundingClientRect().right;
        if (chip.getBoundingClientRect().right > cellRight - 8) {
          event.preventDefault();
          tagsCell.classList.add("wrap");
          return;
        }
      } else if (isTruncated(tagsCell.querySelector(".tags"))) {
        tagsCell.classList.add("wrap");
        return;
      } else if (tagsCell.classList.contains("wrap")) {
        tagsCell.classList.remove("wrap");
      }
    }
  }

  onDblClick(event) {
    if (event.target.closest(".table-cell-editor")) return;
    const li = event.target.closest("li[ld-bookmark-item]");
    if (!li) return;
    const cell = event.target.closest(
      ".cell-title, .cell-desc, .cell-notes, .cell-author",
    );
    if (!cell) return;
    const field = cell.classList.contains("cell-title")
      ? "title"
      : cell.classList.contains("cell-desc")
        ? "description"
        : cell.classList.contains("cell-notes")
          ? "notes"
          : "author";
    event.preventDefault();
    this.editCell(li, field);
  }
}

function hideAllToolbarFloats(keep) {
  document
    .querySelectorAll("li[ld-bookmark-item].toolbar-visible")
    .forEach((el) => {
      if (el !== keep) el.classList.remove("toolbar-visible");
    });
}

// 点表格行外时收起已展开的操作栏浮层
document.addEventListener("click", (event) => {
  if (!event.target.closest("ul.bookmark-list.view-table")) {
    hideAllToolbarFloats(null);
  }
});

registerBehavior("ld-bookmark-table", BookmarkTable);
