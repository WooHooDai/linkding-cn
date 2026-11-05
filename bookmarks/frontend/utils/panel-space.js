// 面板让位策略（左侧「布局」抽屉 / 右侧侧边栏）：
//   1. 面板能整个落进页面左右留白里 → 什么都不动（既不推也不盖，零位移）
//   2. 否则，若让位后列表仍有最小可用宽度 → 平移内容让位（宽度不变，不重排）
//   3. 再否则 → 浮层覆盖（宁可盖住，也不把列表挤窄触发重排）
// 顺序原则是「先保证列表不被挤压」：强行让位会让列宽重算、标题换行，看起来就是整页在晃。
// 低于这个宽度列表会挤到「标题 + 操作栏」抢行，继续让位不如盖住
const MIN_CONTENT_WIDTH = 720;
const DRAWER_WIDTH = 360;
const SIDEBAR_WIDTH = 340;
// 面板贴边留一点呼吸位，正好塞满会让边界粘在一起
const FIT_GAP = 16;
// 与 bookmark-page.css 的窄屏断点一致：窄屏面板本身就是全宽浮层
const MOBILE_QUERY = "(max-width: 840px)";

function isMobile() {
  return window.matchMedia(MOBILE_QUERY).matches;
}

// 页面主体的自然宽度：受 .container 的 max-width 约束，与当前内边距无关
function naturalContentWidth(viewport) {
  const container = document.querySelector(".content.container, .container");
  if (!container) return viewport;
  const maxWidth = parseFloat(getComputedStyle(container).maxWidth);
  return Number.isFinite(maxWidth) ? Math.min(viewport, maxWidth) : viewport;
}

// 两个面板能否并存：都落进留白，或者同时让位后列表仍够宽
export function panelsFitTogether() {
  const viewport = document.documentElement.clientWidth;
  if (isMobile()) return false;

  const slack = Math.max(0, (viewport - naturalContentWidth(viewport)) / 2);
  const bothInGutter =
    DRAWER_WIDTH + FIT_GAP <= slack && SIDEBAR_WIDTH + FIT_GAP <= slack;
  if (bothInGutter) return true;

  return viewport - DRAWER_WIDTH - SIDEBAR_WIDTH >= MIN_CONTENT_WIDTH;
}

let lastSignature = null;

export function syncPanelSpace() {
  const body = document.body;
  const page = document.querySelector(".bookmarks-page, .highlights-page");
  const viewport = document.documentElement.clientWidth;
  const contentWidth = naturalContentWidth(viewport);

  // 已让位占用的宽度（push）与两侧剩余留白
  let used = 0;
  const slackFor = () => Math.max(0, (viewport - used - contentWidth) / 2);
  let leftSlack = slackFor();
  const drawerOpen = Boolean(document.querySelector(".layout-drawer.is-open"));

  let drawerPush = false;
  if (drawerOpen && !isMobile()) {
    if (DRAWER_WIDTH + FIT_GAP <= leftSlack) {
      leftSlack -= DRAWER_WIDTH;
    } else if (viewport - used - DRAWER_WIDTH >= MIN_CONTENT_WIDTH) {
      drawerPush = true;
      used += DRAWER_WIDTH;
    }
  }
  body.classList.toggle("layout-drawer-push", drawerPush);

  // 「常驻侧边栏」本来就是网格列，不走这套；只有按需打开的浮层模式需要判断。
  // 收起动画期间 sidebar-open 还没撤（面板还在屏幕上滑出），但让位要立刻还回去，
  // 所以在 closing 阶段也算「已收起」。
  const sidebarOpen = Boolean(
    page?.dataset.sidebarDefault === "0" &&
      page.classList.contains("sidebar-open") &&
      !page.classList.contains("sidebar-closing"),
  );
  let sidebarPush = false;
  if (sidebarOpen && !isMobile()) {
    const rightSlack = slackFor();
    if (SIDEBAR_WIDTH + FIT_GAP > rightSlack) {
      sidebarPush = viewport - used - SIDEBAR_WIDTH >= MIN_CONTENT_WIDTH;
    }
  }
  body.classList.toggle("sidebar-push", sidebarPush);

  // 让位宽度变了（含 0.2s 过渡）后要重排表格列宽，否则表格按旧宽度算完就停在那儿
  const signature = `${drawerPush}|${sidebarPush}`;
  if (lastSignature !== null && lastSignature !== signature) {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      document.body.classList.remove("panel-space-no-anim");
      window.dispatchEvent(new Event("resize"));
    }, 260);
  }
  lastSignature = signature;
}

let scheduled = false;
let resizeTimer = null;

function schedule() {
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(() => {
    scheduled = false;
    syncPanelSpace();
  });
}

// 拖动窗口时开关让位不该带 0.2s 过渡，否则内容会「追着」窗口走
function scheduleResize() {
  document.body.classList.add("panel-space-no-anim");
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(
    () => document.body.classList.remove("panel-space-no-anim"),
    180,
  );
  schedule();
}

window.addEventListener("resize", scheduleResize);
document.addEventListener("turbo:load", schedule);
document.addEventListener("turbo:render", schedule);

// 缓存快照里不能带着让位态，否则返回上一页时内容会被顶偏
document.addEventListener("turbo:before-cache", () => {
  document.body.classList.remove("layout-drawer-push", "sidebar-push");
});
