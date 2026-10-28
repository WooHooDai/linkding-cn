/** 数据列表选择器：表头/底部横滚条两条轨道也带 .bookmark-list 且排在前面，取数据列表走这里。 */
export const BOOKMARK_LIST_SELECTOR =
  "ul.bookmark-list:not(.table-header-list):not(.table-footer-list)";

export function queryBookmarkList(root = document) {
  return root.querySelector(BOOKMARK_LIST_SELECTOR);
}

export function queryBookmarkLists(root = document) {
  return root.querySelectorAll(BOOKMARK_LIST_SELECTOR);
}