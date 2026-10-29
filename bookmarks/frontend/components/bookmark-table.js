import Sortable from "sortablejs";
import { Behavior, registerBehavior } from "./runtime.js";
import { getCSRFToken } from "../utils/csrf.js";
import { gettext } from "../utils/i18n.js";
import { showToast } from "./toast.js";
import { patchBookmark } from "./bookmark-page.js";

// 拖拽的硬下限：再窄就看不出内容了
const MIN_COL_PX = 60;
// 单击展开的延迟：留出双击判定窗口，双击改为编辑
const EXPAND_DELAY = 200;
// 服务端没给基准宽时的兜底（正常配置不会走到）
const DEFAULT_BASE_PX = 120;

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
  highlights: "highlights",
  annotations: "annotations",
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

function visibleColumns(config) {
  return config.filter((c) => c.on !== false);
}

/** 各列像素宽：未拖过的列以基准宽为底、按 w 分余量（收不下就停在基准宽转横滚）；
 *  拖过的列用保存的 px、不参与余量分配。逐列取整误差用累计余量吃掉。 */
function columnWidths(columns, available) {
  const base = columns.map((c) => c.px ?? c.base ?? DEFAULT_BASE_PX);
  const surplus = available - base.reduce((sum, w) => sum + w, 0);
  if (surplus <= 0) return base;

  const weightSum = columns.reduce((sum, c) => sum + (c.px ? 0 : c.w || 1), 0);
  if (!weightSum) return base;

  let rest = 0;
  return columns.map((c, i) => {
    if (c.px) return base[i];
    rest += (surplus * (c.w || 1)) / weightSum;
    const extra = Math.round(rest);
    rest -= extra;
    return base[i] + extra;
  });
}

/** 三轨共用一份列宽：像素宽度 + 行最小宽度（行宽不足时行底色/底线会被截断）。 */
function applyWidths(list, grid, minWidth) {
  if (!list) return;
  list.style.setProperty("--ld-table-cols", grid);
  list.style.minWidth = minWidth;
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
  const ul = document.querySelector("ul.bookmark-list.view-table[ld-bookmark-table]");
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

class BookmarkTable extends Behavior {
  constructor(element) {
    super(element);
    this.onPointerDown = this.onPointerDown.bind(this);
    this.onClick = this.onClick.bind(this);
    this.onDblClick = this.onDblClick.bind(this);
    this.onToolbarEdit = this.onToolbarEdit.bind(this);
    this.syncColumnWidth = this.syncColumnWidth.bind(this);
    this.syncStickyTop = this.syncStickyTop.bind(this);
    this.onBodyScroll = this.onBodyScroll.bind(this);
    this.onHeaderScroll = this.onHeaderScroll.bind(this);
    this.onFooterScroll = this.onFooterScroll.bind(this);
    this.onResize = this.onResize.bind(this);
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
    // 三横向轨道：主体轨道承载数据行，表头轨道独立在 sticky 外层内，底部轨道为常驻可见横滚条
    this.bodyScroll = ul.closest(".table-body-scroll");
    this.headerSticky = document.querySelector(".table-header-sticky");
    this.headerScroll = this.headerSticky?.querySelector(".table-header-scroll");
    this.headerList = this.headerSticky?.querySelector("ul.table-header-list");
    this.headerRow = this.headerSticky?.querySelector(".table-header");
    this.footerScroll = document.querySelector(".table-footer-scroll");
    this.footerList = this.footerScroll?.querySelector("ul.table-footer-list");

    this.syncColumnWidth();

    // 列宽手柄位于独立表头轨道（主体 ul 的兄弟），需在表头行同样捕获 pointerdown
    this.headerRow?.addEventListener("pointerdown", this.onPointerDown);

    window.addEventListener("resize", this.onResize, { passive: true });
    window.addEventListener("orientationchange", this.onResize, {
      passive: true,
    });

    // 三条轨道 scrollLeft 双向同步；标志位阻断事件回环
    this._syncingScroll = false;
    this.bodyScroll?.addEventListener("scroll", this.onBodyScroll, { passive: true });
    this.headerScroll?.addEventListener("scroll", this.onHeaderScroll, { passive: true });
    this.footerScroll?.addEventListener("scroll", this.onFooterScroll, { passive: true });

    // 表头吸顶顶部：搜索栏吸顶时紧贴其下方，未吸顶/未启用时 top:0
    this.syncStickyTop();
    // 底部悬浮横滚条避让吸底分页器
    this.syncStickyPager();

    // 搜索栏吸顶开关切换属性时，兜底重算一次避让高度
    const sectionHeader = document.querySelector(".section-header");
    if (sectionHeader) {
      this.stickyObserver = new MutationObserver(this.syncStickyTop);
      this.stickyObserver.observe(sectionHeader, {
        attributes: true,
        attributeFilter: ["data-sticky-on", "class"],
      });
    }

    // 工具条换行、批量栏折叠展开、侧边栏开合、分页器高度变化等不一定触发 window.resize，
    // 由 ResizeObserver 兜住重算列宽、表头避让高度与底部横滚条避让高度。
    const observed = [
      document.querySelector(".section-header"),
      document.querySelector(".bulk-edit-bar"),
      document.querySelector(".table-toolbar"),
      document.querySelector(".bookmark-pagination"),
    ];
    if ("ResizeObserver" in window) {
      this.geometryObserver = new ResizeObserver(() => {
        this.syncColumnWidth();
        this.syncStickyTop();
        this.syncStickyPager();
      });
      observed.forEach((el) => el && this.geometryObserver.observe(el));
    }

    this.initColumnSortable();
  }

  destroy() {
    window.removeEventListener("resize", this.onResize);
    window.removeEventListener("orientationchange", this.onResize);
    this.headerRow?.removeEventListener("pointerdown", this.onPointerDown);
    this.bodyScroll?.removeEventListener("scroll", this.onBodyScroll);
    this.headerScroll?.removeEventListener("scroll", this.onHeaderScroll);
    this.footerScroll?.removeEventListener("scroll", this.onFooterScroll);
    this.geometryObserver?.disconnect();
    this.stickyObserver?.disconnect();
    delete this.element.dataset.ldTableBound;
  }

  /* 列宽只在这里算一次，三条轨道共用；底部轨道再同步选择框列宽以对齐 thumb 比例。 */
  syncColumnWidth() {
    const visible = visibleColumns(parseConfig(this.element));
    if (!visible.length || !this.bodyScroll) return;

    const { check, ops } = this.trackExtras();
    const available = Math.max(0, this.bodyScroll.clientWidth - check - ops);
    const widths = columnWidths(visible, available);
    const grid = widths.map((w) => `${w}px`).join(" ");
    const total = widths.reduce((sum, w) => sum + w, 0) + check + ops;
    const minWidth = `${total}px`;

    applyWidths(this.element, grid, minWidth);
    applyWidths(this.headerList, grid, minWidth);
    applyWidths(this.footerList, grid, minWidth);
    this.footerList?.style.setProperty("--ld-check-col", `${check}px`);

    this._contentWidth = total;
    // 操作栏宽度上限按可视宽给，放不下就折行（否则会被表格左缘裁掉）
    this.element.style.setProperty(
      "--ld-actions-max-w",
      `${Math.max(240, this.bodyScroll.clientWidth - ops - 8)}px`,
    );
    this.syncOverflowState();
  }

  /* 选择框列（批量编辑模式下才出现）与操作列的宽度由 CSS 变量决定 */
  trackExtras() {
    const cs = getComputedStyle(this.element);
    return {
      check: parseFloat(cs.getPropertyValue("--ld-check-col")) || 0,
      ops: parseFloat(cs.getPropertyValue("--ld-ops-col")) || 0,
    };
  }

  /* 用算出的内容宽度判断溢出，不读 scrollWidth（省一次布局读取）；结果决定底栏是否放出。 */
  syncOverflowState() {
    if (!this.bodyScroll) return;
    const overflowing = (this._contentWidth ?? 0) > this.bodyScroll.clientWidth;
    [this.bodyScroll, this.headerScroll, this.footerScroll].forEach((el) =>
      el?.classList.toggle("is-overflowing", overflowing),
    );
    // 放得下时把残留的横滚位置归零：否则刚改成放得下（如收窄列宽）会停在偏移态
    if (!overflowing) {
      [this.bodyScroll, this.headerScroll, this.footerScroll].forEach((el) => {
        if (el && el.scrollLeft) el.scrollLeft = 0;
      });
    }
  }

  /* 底部悬浮横滚条避让吸底分页器：分页器处于跟随吸底态时取其实际高度，
     横滚条 bottom 贴在分页器上方；未开启跟随或不可见时回到 0。 */
  syncStickyPager() {
    if (!this.footerScroll) return;
    const pager = document.querySelector(
      ".bookmark-pagination[data-sticky-on='true']",
    );
    const h = pager ? Math.round(pager.offsetHeight) : 0;
    this.footerScroll.style.setProperty("--ld-pager-h", `${h}px`);
  }

  /* 窗口尺寸变化：列宽、表头避让高度、底部横滚条避让高度一并重算 */
  onResize() {
    this.syncColumnWidth();
    this.syncStickyTop();
    this.syncStickyPager();
  }

  /* 表头吸顶顶部避让：搜索栏处于吸顶态时取其实际高度，表头紧贴其下；否则回到 top:0 */
  syncStickyTop() {
    if (!this.headerSticky) return;
    const headerBar = document.querySelector(
      ".section-header[data-sticky-on='true']",
    );
    const top = headerBar ? Math.round(headerBar.offsetHeight) : 0;
    this.headerSticky.style.setProperty("--ld-table-top", `${top}px`);
  }

  /* 主体轨道横滚 → 同步表头轨道与底部悬浮轨道；行尾操作槽的补偿偏移跟着更新 */
  onBodyScroll() {
    if (this._syncingScroll || !this.bodyScroll) return;
    this._syncingScroll = true;
    const left = this.bodyScroll.scrollLeft;
    if (this.headerScroll) this.headerScroll.scrollLeft = left;
    if (this.footerScroll) this.footerScroll.scrollLeft = left;
    this._syncingScroll = false;
  }

  /* 表头轨道横滚 → 同步主体轨道（双向同步，允许直接拖表头轨道） */
  onHeaderScroll() {
    if (this._syncingScroll || !this.bodyScroll) return;
    this._syncingScroll = true;
    this.bodyScroll.scrollLeft = this.headerScroll.scrollLeft;
    this._syncingScroll = false;
  }

  /* 底部悬浮轨道横滚 → 回写主体轨道（主体滚动事件再连带同步表头轨道） */
  onFooterScroll() {
    if (this._syncingScroll || !this.bodyScroll) return;
    this._syncingScroll = true;
    this.bodyScroll.scrollLeft = this.footerScroll.scrollLeft;
    this._syncingScroll = false;
  }

  /* ── 列宽拖拽：实时改 CSS 变量，松手静默保存（不重载） ── */
  onPointerDown(event) {
    const handle = event.target.closest(".col-rz");
    if (!handle) return;
    event.preventDefault();

    const ul = this.element;
    const fullConfig = parseConfig(ul);
    const visible = visibleColumns(fullConfig);
    const headerCells = this.headerRow?.querySelectorAll(".th") || [];
    const index = [...headerCells].findIndex((th) => th.contains(handle));
    if (index < 0 || !visible[index]) return;

    handle.classList.add("dragging");
    handle.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startPx = headerCells[index].getBoundingClientRect().width;
    let dirty = false;
    let finished = false;

    const onMove = (moveEvent) => {
      const px = Math.round(
        Math.max(MIN_COL_PX, startPx + moveEvent.clientX - startX),
      );
      if (visible[index].px === px) return;
      // 拖过就转成显式像素宽；配置要回写 dataset，syncColumnWidth 每次从那里解析
      visible[index].px = px;
      ul.dataset.tableConfig = JSON.stringify(fullConfig);
      this.syncColumnWidth();
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
    const headerRow = this.headerRow;
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
        this.element.dataset.tableConfig = JSON.stringify(next);
        this.applyColumnOrder(reordered.map((c) => c.key));
        this.syncColumnWidth();
        saveQuiet(next);
      },
    });
  }

// 数据行按新列序重排：格子布局完全由显式 grid-column 决定，只改轨道号、不动 DOM。
  applyColumnOrder(keys) {
    this.element.querySelectorAll("li[ld-bookmark-item]").forEach((li) => {
      keys.forEach((key, i) => {
        const suffix = CELL_CLASS[key];
        const cell = suffix && li.querySelector(`:scope > .cell-${suffix}`);
        if (!cell) return;
        cell.style.gridColumn = String(i + 2);
      });
    });
  }

  /** 健康弹层定位：表格视口会裁切内容，宽高与展开方向都按「视口可视区 ∩ 窗口」算 */
  positionHealthPopover(indicator) {
    const popover = indicator.querySelector(".health-popover");
    if (!popover) return;
    const rect = indicator.getBoundingClientRect();
    const viewportRect = (this.bodyScroll || this.element).getBoundingClientRect();
    const GAP = 8;

    const boundsLeft = Math.max(viewportRect.left, 0);
    const boundsRight = Math.min(viewportRect.right, window.innerWidth);
    // 上界扣掉吸顶表头，否则弹层会被表头盖住
    const headerBottom = this.headerSticky?.getBoundingClientRect().bottom ?? 0;
    const boundsTop = Math.max(viewportRect.top, headerBottom, 0);
    const boundsBottom = Math.min(viewportRect.bottom, window.innerHeight);

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
    // 输入多行（desc/notes 用 Enter 换行）vs 软换行显示（title 用 Enter 保存但超宽自动折行）
    const multiline = field === "description" || field === "notes";
    const softWrap = field === "title";

    const editor = document.createElement(
      multiline || softWrap ? "textarea" : "input",
    );
    editor.className = "table-cell-editor";
    if (multiline || softWrap) {
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
      if (!multiline && !softWrap) return;
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
    const viewportRect = (this.bodyScroll || this.element).getBoundingClientRect();
    const cellRect = cell.getBoundingClientRect();
    const PREFERRED = 420;
    const GAP = 12;
    // 可视区（表格主体横滚视口）内的可用宽度：编辑器绝不允许溢出它，
    // 否则标签列横滚出可视区时，编辑器会跟着跑到屏幕外被裁切
    const viewportWidth = viewportRect.right - viewportRect.left;
    const availRight = viewportRect.right - cellRect.left - GAP;
    const availLeft = cellRect.right - viewportRect.left - GAP;
    const maxWidth = Math.min(
      Math.max(availRight, availLeft, 160),
      viewportWidth - 2 * GAP,
    );
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

    let left = availRight >= width ? 0 : Math.round(cellRect.width - width);
    // 视口约束：编辑器（相对 cell 定位）最终渲染位置必须落在可视区内。
    // cell 横滚出可视区时（如通过操作栏快捷编辑靠右的标签列），left 要回拉，
    // 否则编辑器右缘/左缘被视口裁掉。
    const editorViewportLeft = cellRect.left + left;
    const editorViewportRight = editorViewportLeft + width;
    const maxLeft = viewportRect.right - GAP - cellRect.left - width;
    const minLeft = viewportRect.left + GAP - cellRect.left;
    if (editorViewportRight > viewportRect.right - GAP) {
      left = Math.round(maxLeft);
    }
    if (editorViewportLeft < viewportRect.left + GAP) {
      left = Math.round(minLeft);
    }

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
    this.placeEditor(editor, cell, "tags");
    // 宽度确定后再让多行编辑器重算高度（lit 首次 render 可能尚未完成）
    requestAnimationFrame(() => editor.autoResize?.());
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
    // 进入编辑态即收起操作栏：编辑框覆盖在行上，操作栏继续悬浮会挡内容/被表头盖住
    if (li.classList.contains("toolbar-visible")) {
      hideAllToolbarFloats(li);
      li.classList.remove("toolbar-visible");
      li.classList.add("toolbar-suppressed");
      li.addEventListener(
        "pointerleave",
        () => li.classList.remove("toolbar-suppressed"),
        { once: true },
      );
    }
    this.editCell(li, field);
  }

  onClick(event) {
    if (event.target.closest(".table-cell-editor")) return;

    const opsBtn = event.target.closest(".row-ops-btn");
    if (opsBtn) {
      const li = opsBtn.closest("li[ld-bookmark-item]");
      const open = li.classList.contains("toolbar-visible");
      hideAllToolbarFloats(li);
      li.classList.toggle("toolbar-visible", !open);
      // 收起后指针还在行内：临时压掉「悬停即显示」，否则会立刻被 :hover 顶回来
      li.classList.toggle("toolbar-suppressed", open);
      if (open) {
        li.addEventListener(
          "pointerleave",
          () => li.classList.remove("toolbar-suppressed"),
          { once: true },
        );
      }
      opsBtn.setAttribute("aria-expanded", open ? "false" : "true");
      return;
    }

    const li = event.target.closest("li[ld-bookmark-item]");
    if (!li) return;
    const cell = event.target.closest(
      ".cell-title, .cell-author, .cell-desc, .cell-notes, .cell-tags",
    );
    if (!cell) return;
    const link = event.target.closest("a");

    // 双击（detail≥2）：快速连点当「编辑」，间隔超过切换延迟的说明切换已生效、不再动作
    const now = Date.now();
    const interval = now - (this._lastClickAt ?? 0);
    this._lastClickAt = now;
    if (event.detail >= 2) {
      this.cancelPendingToggle();
      if (interval < EXPAND_DELAY) {
        if (link) event.preventDefault();
        this.editCell(li, FIELD_OF_CELL(cell));
      }
      return;
    }

    // 跳转/筛选只认「点在内容上」；右缘死区与空白处一律展开折叠，杜绝误触跳转
    if (!this.hitsContent(cell, link, event)) {
      if (link) event.preventDefault();
      this.toggleRow(li);
      return;
    }

    if (link) return;
    this.toggleRow(li);
  }

  /** 点击是否落在可跳转/可筛选的内容上。
   *  链接盒普遍被 flex 撑满格子（标题/作者），只有文字本身才算内容，
   *  空白处（含行展开后的充裕留白）是展开折叠的安全区。 */
  hitsContent(cell, link, event) {
    if (!link) return false;
    if (cell.classList.contains("cell-tags")) {
      // 芯片被右缘裁掉时只露出半个，点它多半是想展开而非筛标签
      const right = cell.getBoundingClientRect().right;
      return link.getBoundingClientRect().right <= right - 8;
    }
    // favicon 等图标链接是实体按钮，命中即算
    if (!link.matches(".title-link, .author")) return true;
    const li = cell.closest("li[ld-bookmark-item]");
    // 折叠态作者被截断时整格都是开关，点文字只展开（未截断时点文字筛作者）
    if (
      cell.classList.contains("cell-author") &&
      !li.classList.contains("is-expanded") &&
      this.isAuthorTruncated(li)
    ) {
      return false;
    }
    return this.hitsText(link, event.clientX, event.clientY);
  }

  /** 点位是否落在链接文字上：按 Range 行盒逐行判定（链接盒宽 ≠ 文字宽） */
  hitsText(link, x, y) {
    const range = document.createRange();
    range.selectNodeContents(link);
    for (const rect of range.getClientRects()) {
      if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
        return true;
      }
    }
    return false;
  }

  /** 整行展开/折叠：切换都延迟 EXPAND_DELAY，快速双击时取消切换改为编辑 */
  toggleRow(li) {
    this.cancelPendingToggle();
    const next = !li.classList.contains("is-expanded");
    this._pendingToggle = {
      li,
      timer: setTimeout(() => {
        this._pendingToggle = null;
        li.classList.toggle("is-expanded", next);
      }, EXPAND_DELAY),
    };
  }

  cancelPendingToggle() {
    if (!this._pendingToggle) return;
    clearTimeout(this._pendingToggle.timer);
    this._pendingToggle = null;
  }

  /** 作者列是否被截断（.author 是 flex 项，能反映内部文本溢出） */
  isAuthorTruncated(li) {
    const link = li.querySelector(".cell-author .author");
    return !!link && link.scrollWidth > link.clientWidth + 1;
  }

  onDblClick(event) {
    // 编辑已在 click 里按 detail 处理；这里只挡掉双击的默认行为（选词等）
    if (event.target.closest(".table-cell-editor")) return;
    if (event.target.closest(".cell-title, .cell-author, .cell-desc, .cell-notes, .cell-tags")) {
      event.preventDefault();
    }
  }
}

/** 单元格 → 可就地编辑的字段 */
function FIELD_OF_CELL(cell) {
  if (cell.classList.contains("cell-title")) return "title";
  if (cell.classList.contains("cell-tags")) return "tags";
  if (cell.classList.contains("cell-desc")) return "description";
  if (cell.classList.contains("cell-notes")) return "notes";
  return "author";
}

function hideAllToolbarFloats(keep) {
  document
    .querySelectorAll("li[ld-bookmark-item].toolbar-visible")
    .forEach((el) => {
      // 紧凑视图的开合状态由自身脚本独立管理，不在此排他收起
      if (el === keep || el.closest("ul.view-compact")) return;
      el.classList.remove("toolbar-visible");
    });
}

// 点表格行外时收起已展开的操作栏浮层
document.addEventListener("click", (event) => {
  if (!event.target.closest("ul.bookmark-list.view-table")) {
    hideAllToolbarFloats(null);
  }
});

registerBehavior("ld-bookmark-table", BookmarkTable);
