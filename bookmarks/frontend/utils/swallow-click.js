/** 拦下浮层「按下即收起」后浏览器补发的那一次 click。
 *
 * 候选在 pointerdown 里就选中并把菜单收起，触屏的兼容 click 要晚一步才发；那时按坐标
 * 命中已是下面的行——打开书签链接、折叠展开行。这里只吞掉落在原候选矩形里的那一次，
 * 并带超时兜底，不误伤之后真正的点击。
 */
export function swallowNextClickIn(rect, timeout = 500) {
  const cleanup = () => {
    document.removeEventListener("click", swallow, true);
    clearTimeout(timer);
  };
  const swallow = (event) => {
    if (event.clientX < rect.left || event.clientX > rect.right) return;
    if (event.clientY < rect.top || event.clientY > rect.bottom) return;
    event.preventDefault();
    event.stopPropagation();
    cleanup();
  };
  document.addEventListener("click", swallow, true);
  const timer = setTimeout(cleanup, timeout);
}
