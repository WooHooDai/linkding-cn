import { applyRequestHeaders } from "./helpers";
import {
  buildDomainRequestHeaders,
  getRenderedDomainState,
  getStoredDomainState,
  stripDomainPreferenceParams,
} from "./domain-preferences";
import {
  buildSummaryRequestHeaders,
  getRenderedSummaryState,
  getStoredSummaryState,
  stripSummaryPreferenceParams,
} from "./summary-preferences";

const BOOKMARK_PAGE_STREAM_HEADER = "X-Linkding-Bookmark-Page-Stream";

export function buildPagePreferenceRequestHeaders() {
  const headers = {};
  const summaryHeaders = buildSummaryRequestHeaders({
    ...getRenderedSummaryState(),
    ...getStoredSummaryState(),
  });
  const domainHeaders = buildDomainRequestHeaders({
    ...getRenderedDomainState(),
    ...getStoredDomainState(),
  });

  if (summaryHeaders) {
    applyRequestHeaders(headers, summaryHeaders);
  }
  if (domainHeaders) {
    applyRequestHeaders(headers, domainHeaders);
  }

  return Object.keys(headers).length > 0 ? headers : null;
}

export function buildBookmarkPageStreamRequestHeaders() {
  const headers = {
    Accept: "text/vnd.turbo-stream.html",
    [BOOKMARK_PAGE_STREAM_HEADER]: "1",
  };
  const pageHeaders = buildPagePreferenceRequestHeaders();

  if (pageHeaders) {
    applyRequestHeaders(headers, pageHeaders);
  }

  return headers;
}

export function stripPagePreferenceParams(href) {
  return stripDomainPreferenceParams(stripSummaryPreferenceParams(href));
}
