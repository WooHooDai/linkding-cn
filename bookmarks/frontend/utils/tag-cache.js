import { api } from "../api.js";

// CJK Unified Ideographs and common extensions
const CJK_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;

function hasCJK(name) {
  return CJK_RE.test(name);
}

// 标签候选 / 搜索建议的最终展示顺序，以此函数为准（覆盖后端 /api/tags/ 的默认顺序）。
// 规则：按使用频次降序；频次相同时非 CJK 标签在前、CJK 标签在后；组内按名称排序。
// 要改展示顺序先改这里。后端 api/routes.py TagViewSet.get_queryset 的 order_by
// 仅作为 API 默认顺序（分页稳定性与直接调用 API 的消费者依赖它），前端会基于该
// 顺序整体重排，因此改动后端 order_by 不会影响标签自动补全。
// 本函数为纯函数（不修改入参），便于单独测试。
export function sortTagsForAutocomplete(tags) {
  return [...tags].sort((left, right) => {
    const leftCount = left.bookmark_count || 0;
    const rightCount = right.bookmark_count || 0;
    if (leftCount !== rightCount) return rightCount - leftCount;
    const leftCJK = hasCJK(left.name) ? 1 : 0;
    const rightCJK = hasCJK(right.name) ? 1 : 0;
    if (leftCJK !== rightCJK) return leftCJK - rightCJK;
    return left.name.toLowerCase().localeCompare(right.name.toLowerCase());
  });
}

class Cache {
  constructor(api) {
    this.api = api;

    // Reset cached tags after a form submission
    document.addEventListener("turbo:submit-end", () => {
      this.tagsPromise = null;
    });
  }

  getTags() {
    if (!this.tagsPromise) {
      this.tagsPromise = this.api
        .getTags({
          limit: 5000,
          offset: 0,
        })
        .then(sortTagsForAutocomplete)
        .catch((e) => {
          console.warn("Cache: Error loading tags", e);
          return [];
        });
    }

    return this.tagsPromise;
  }
}

export const cache = new Cache(api);
