/** 默认/紧凑视图：单击跳转（筛标签）、双击进入就地编辑。
 *
 * 判定用「第二次 click 的 detail」而不是 dblclick 事件：两次点击之间版面可能已经
 * 变了（描述第一下就展开、文字整体重排），此时 dblclick 的命中元素与第一下不同，
 * 甚至根本不触发。
 * 标题文字与标签芯片本身是链接，第一下先挂起跳转（见 utils/deferred-link.js），
 * 窗口内来了第二下就取消跳转改编辑；标题末尾的空白区点不到文字 span，仍是原生即时跳转。
 */

import {
  bindDeferredFollowCancel,
  cancelDeferredFollow,
  deferFollow,
} from "../utils/deferred-link.js";

export const bookmarkRowDblClick = {
  _initDblClickEdit() {
    // 格子行（表格视图）的双击由表格行为统一处理：同一个双击会开出两个编辑器
    if (this.element.querySelector(".cell")) return;
    bindDeferredFollowCancel();

    this.onRowClick = (event) => this._onRowClick(event);
    this.element.addEventListener("click", this.onRowClick, true);
  },

  _teardownDblClickEdit() {
    if (this.onRowClick) {
      this.element.removeEventListener("click", this.onRowClick, true);
      this.onRowClick = null;
    }
    this._firstClickField = null;
  },

  _onRowClick(event) {
    if (event.defaultPrevented || event.button !== 0) return;
    // 带修饰键是「新窗口打开」之类的系统手势，不接管
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }

    if (event.detail >= 2) {
      // 第二下按第一下记住的字段算：重排后当前落点可能已经换元素了
      const field = this._firstClickField || this._dblClickField(event.target);
      if (field) {
        event.preventDefault();
        event.stopPropagation();
        cancelDeferredFollow();
        this._openEditor(field);
        return;
      }
    } else {
      this._firstClickField = this._dblClickField(event.target);
    }

    this._deferLinkNav(event);
  },

  _openEditor(field) {
    // 第一下已按单击语义把描述展开了：进编辑前还原，免得编辑完留个展开态
    if (field === "description") this._setDescriptionExpanded(false);
    // 双击顺带选中了词：进编辑前清掉，否则残留高亮跟着输入框一起显示
    const selection = window.getSelection?.();
    if (selection && !selection.isCollapsed) selection.removeAllRanges();
    this.startQuickEdit(field);
  },

  /** 标题文字 / 标签芯片的单击：先挂起，等双击窗口过去再跳转 */
  _deferLinkNav(event) {
    const titleLink = event.target.closest("a.title-link");
    if (titleLink && this.element.contains(titleLink)) {
      const text = titleLink.querySelector("span");
      // 点在链接末尾的空白区：留给原生单击，立刻跳转
      if (!text || !text.contains(event.target)) return;
      event.preventDefault();
      event.stopPropagation();
      deferFollow(titleLink);
      return;
    }

    const tagLink = event.target.closest(".tags a");
    if (tagLink && this._tagsEditable()) {
      event.preventDefault();
      event.stopPropagation();
      deferFollow(tagLink);
    }
  },

  /** 点击/双击目标 → 可编辑字段；笔记里的链接、待办勾选框是独立交互目标，不做转换 */
  _dblClickField(target) {
    if (!target?.closest) return null;
    // 编辑器自身（含标签补全）里的双击是选词，不是再进一次编辑
    if (target.closest("input, textarea, .quick-edit-tags-container")) {
      return null;
    }
    if (target.closest(".task-checkbox")) return null;

    // 标题：Safari 下标题链接被置为 pointer-events:none，落点是 .title 本身
    if (target.closest(".title-link")) return "title";
    if (this._isTitleArea(target)) return "title";

    const link = target.closest("a");
    if (target.closest(".inline-edit-notes")) return link ? null : "notes";
    if (target.closest(".description")) {
      // 同行模式的标签芯片是筛选链接，双击不做编辑（分行模式标签另占一行，见下）
      return link?.closest(".tags") ? null : "description";
    }
    if (target.closest(".tags")) return this._tagsEditable() ? "tags" : null;
    return null;
  },

  /** 标题区里除图标、复选框、作者以外的部分都算标题文字 */
  _isTitleArea(target) {
    const title = target.closest(".title");
    if (!title || !this.element.contains(title)) return false;
    return !target.closest(".favicon-link, .bulk-edit-checkbox, .author");
  },

  /** 标签只在「分行」描述样式下才占独立一行，同行样式里它是描述的一部分 */
  _tagsEditable() {
    return !this.element.querySelector(".description.inline");
  },
};
