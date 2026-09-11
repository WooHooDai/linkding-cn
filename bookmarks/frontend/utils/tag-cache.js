import { api } from "../api.js";

// CJK Unified Ideographs and common extensions
const CJK_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;

function hasCJK(name) {
  return CJK_RE.test(name);
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
        .then((tags) => {
          // 候选列表完全按使用频次降序排列，不区分中英文；
          // 频次相同时非 CJK 标签在前，CJK 标签在后，组内按名称排序
          tags.sort((left, right) => {
            const leftCount = left.bookmark_count || 0;
            const rightCount = right.bookmark_count || 0;
            if (leftCount !== rightCount) return rightCount - leftCount;
            const leftCJK = hasCJK(left.name) ? 1 : 0;
            const rightCJK = hasCJK(right.name) ? 1 : 0;
            if (leftCJK !== rightCJK) return leftCJK - rightCJK;
            return left.name.toLowerCase().localeCompare(right.name.toLowerCase());
          });
          return tags;
        })
        .catch((e) => {
          console.warn("Cache: Error loading tags", e);
          return [];
        });
    }

    return this.tagsPromise;
  }
}

export const cache = new Cache(api);
