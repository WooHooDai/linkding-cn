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

export function stripPagePreferenceParams(href) {
  return stripDomainPreferenceParams(stripSummaryPreferenceParams(href));
}
