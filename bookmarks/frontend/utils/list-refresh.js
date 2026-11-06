// 列表局部刷新：请求页面自身 URL + X-LD-List-Partial，只换 #bookmark-list-container。
// 分页链接按 request.path 生成，所以必须复用页面 URL，不能另开端点。
const LIST_PARTIAL_HEADER = "X-LD-List-Partial";
let refreshSeq = 0;

export async function refreshBookmarkList({ resetPage = false } = {}) {
  const container = document.getElementById("bookmark-list-container");
  if (!container) return false;

  const url = new URL(window.location.href);
  url.searchParams.delete("layout");
  if (resetPage) {
    url.searchParams.delete("page");
    // 地址栏也要丢掉页号，否则下次刷新又回到越界页
    window.history.replaceState({}, "", `${url.pathname}${url.search}`);
  }

  const seq = ++refreshSeq;
  let html;
  try {
    const response = await fetch(url, {
      headers: { [LIST_PARTIAL_HEADER]: "1" },
      credentials: "same-origin",
    });
    if (!response.ok) return false;
    html = await response.text();
  } catch (error) {
    console.error(error);
    return false;
  }

  // 后发先至时丢弃旧响应，避免把列表刷回上一版偏好
  if (seq !== refreshSeq) return false;

  const template = document.createElement("template");
  template.innerHTML = html.trim();
  const next = template.content.querySelector("#bookmark-list-container");
  if (!next) return false;

  container.replaceWith(next);
  document.dispatchEvent(new CustomEvent("bookmark-list-updated"));
  // 表格列宽按替换时的可用宽度算，切换视图后要等布局稳定再算一次
  requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
  return true;
}
