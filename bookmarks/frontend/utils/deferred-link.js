/** 链接单击的挂起执行：同一块区域既要点一下就跳走，又要能双击改成编辑时，
 *  第一下必须先等一个双击窗口——窗口内的第二下会把它改判成编辑。
 *
 *  NAV_DELAY 取系统双击间隔的常见上限偏小值：短了会吃掉慢一点的连击，
 *  长了单击显得迟滞。
 */
export const NAV_DELAY = 350;

let pending = null;

export function cancelDeferredFollow() {
  if (!pending) return;
  clearTimeout(pending.timer);
  pending = null;
}

/** 立即按链接自身的打开方式跳转（与原生点击一致）。 */
export function followNow(link) {
  const href = link.href;
  const target = link.getAttribute("target");
  if (target && target !== "_self") {
    // 弹窗被拦时退化为当前页跳转，至少不会什么都没发生
    const opened = window.open(href, target);
    if (opened) {
      opened.opener = null;
      return;
    }
  }
  if (window.Turbo?.visit) {
    window.Turbo.visit(href);
  } else {
    window.location.assign(href);
  }
}

export function deferFollow(link) {
  cancelDeferredFollow();
  pending = {
    link,
    timer: setTimeout(() => {
      pending = null;
      followNow(link);
    }, NAV_DELAY),
  };
}

let bound = false;

/** 任何一次新的按下（含连击的第二下）、或开始新一次渲染，排队中的跳转都作废。 */
export function bindDeferredFollowCancel() {
  if (bound) return;
  bound = true;
  document.addEventListener("pointerdown", cancelDeferredFollow, true);
  document.addEventListener("turbo:before-render", cancelDeferredFollow);
}
