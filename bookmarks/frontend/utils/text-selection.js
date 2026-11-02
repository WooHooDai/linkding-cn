/** 选区是否落在 container 内。
 *
 * 拖选文字松手时浏览器会补一次 click，用它把「选文字」与「点空白」区分开，
 * 否则选中一段描述文字就会连带触发展开/折叠。
 */
export function hasSelectionWithin(container) {
  if (!container) return false;
  const selection = window.getSelection?.();
  if (!selection || selection.isCollapsed) return false;

  const isInside = (node) => !!node && container.contains(node);
  return isInside(selection.anchorNode) || isInside(selection.focusNode);
}
