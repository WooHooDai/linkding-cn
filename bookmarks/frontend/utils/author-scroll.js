// 作者名自动滚动
//
// 列表项标题行内的 author 采用固定宽度（见 bookmark-page.css `.title .author`）。
// 内容超出固定宽度时，启用左右往复自动滚动（auto-scroll）：
//   - 外层 .author 负责固定宽度与 overflow 裁切
//   - 内层 .author-inner 承载文本，通过 translateX 动画在 0 ↔ -overflow 间往复
//   - hover 暂停动画（便于阅读/进入快捷编辑），prefers-reduced-motion 下禁用动画
//
// 调用时机：BookmarkItem 构造时、author 快捷编辑保存后、详情弹窗同步列表字段后。
// 重复调用安全：会先移除旧结构/动画状态再重建。

export function initAuthorScroll(authorEl) {
  if (!authorEl || !(authorEl instanceof HTMLElement)) {
    return;
  }

  // 重建内部结构：<span class="author"><span class="author-inner">text</span></span>
  let inner = authorEl.querySelector(":scope > .author-inner");
  if (!inner) {
    inner = document.createElement("span");
    inner.className = "author-inner";
    inner.textContent = authorEl.textContent;
    authorEl.textContent = "";
    authorEl.appendChild(inner);
  }

  // 清除旧的滚动状态
  authorEl.classList.remove("auto-scroll");
  authorEl.style.removeProperty("--author-overflow");
  authorEl.style.removeProperty("--author-duration");

  // 布局可能尚未完成（行为在 DOM 就绪时构造），延后到下一帧再测量
  requestAnimationFrame(() => {
    if (!authorEl.isConnected) {
      return;
    }
    inner = authorEl.querySelector(":scope > .author-inner");
    if (!inner) {
      return;
    }
    const overflow = inner.scrollWidth - authorEl.clientWidth;
    if (overflow > 0) {
      authorEl.classList.add("auto-scroll");
      authorEl.style.setProperty("--author-overflow", `${overflow}px`);
      // 约 40px/s，至少 4s，最多 10s
      const duration = Math.min(10, Math.max(4, Math.round(overflow / 40)));
      authorEl.style.setProperty("--author-duration", `${duration}s`);
    }
  });
}
