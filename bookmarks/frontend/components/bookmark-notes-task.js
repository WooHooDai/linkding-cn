/** 笔记里的待办勾选框。
 *
 *  三个视图（默认 / 紧凑 / 表格）的渲染层结构一致：`.markdown` 里的
 *  `span.task-checkbox` + 一个隐藏 textarea 存原始 Markdown。渲染层每个
 *  复选框对应源文件一条待办行、顺序一致，因此按序数定位源里的那一行。
 */
import { patchBookmark } from "./bookmark-row-editor.js";
import { gettext } from "../utils/i18n.js";
import { showToast } from "./toast.js";

/** 待办行：`- [ ]` / `* [x]` / `1. [x]`，缩进与前后缀原样保留 */
const TASK_LINE = /^(\s*(?:[-*+]|\d+[.)])\s+\[)([ xX])(\])/;

/** 切换第 index 条待办行的完成态；围栏代码块里同样的写法不算待办。 */
export function toggleTaskLine(markdown, index, checked) {
  const lines = markdown.split("\n");
  let seen = 0;
  let fenced = false;
  for (let i = 0; i < lines.length; i += 1) {
    if (/^\s*(```|~~~)/.test(lines[i])) {
      fenced = !fenced;
      continue;
    }
    if (fenced) continue;
    const match = lines[i].match(TASK_LINE);
    if (!match) continue;
    if (seen === index) {
      lines[i] = lines[i].replace(
        TASK_LINE,
        (_line, head, _state, tail) => `${head}${checked ? "x" : " "}${tail}`,
      );
      return lines.join("\n");
    }
    seen += 1;
  }
  return markdown;
}

function paint(box, checked) {
  box.classList.toggle("checked", checked);
  box.closest("li")?.classList.toggle("is-done", checked);
}

/**
 * 点勾选框：就地切换完成态并保存笔记源。
 * 先改界面再发请求，失败按原值回滚（含隐藏源），避免等待期间无反馈。
 *
 * @param {Element} box 被点的 .task-checkbox
 * @param {Element} source 存原始 Markdown 的 textarea
 * @param {string} bookmarkId
 * @param {(markdown: string) => void} [onChange] 源变化后同步调用方的副本
 */
export async function toggleTaskCheckbox(
  box,
  { bookmarkId, source, onChange },
) {
  const scope = box.closest(".markdown");
  if (!scope || !source || !bookmarkId) return;

  const index = [...scope.querySelectorAll(".task-checkbox")].indexOf(box);
  if (index < 0) return;

  const previous = source.value;
  const checked = !box.classList.contains("checked");
  const next = toggleTaskLine(previous, index, checked);
  if (next === previous) return;

  const apply = (markdown) => {
    source.value = markdown;
    onChange?.(markdown);
  };

  paint(box, checked);
  apply(next);

  try {
    await patchBookmark(bookmarkId, { notes: next });
  } catch (error) {
    console.error("Failed to save task state:", error);
    paint(box, !checked);
    apply(previous);
    showToast(gettext("Couldn't save changes. Please try again."), {
      tone: "error",
    });
  }
}
