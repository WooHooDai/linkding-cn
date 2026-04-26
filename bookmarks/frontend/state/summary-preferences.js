import {
  applyRequestHeaders,
  stripPreferenceQueryParams,
} from "./helpers";

const SUMMARY_MODE_STORAGE_KEY = "ld:user-summary-mode";
const SUMMARY_MONTH_STORAGE_KEY = "ld:user-summary-month";
const SUMMARY_WEEK_STORAGE_KEY = "ld:user-summary-week";
const SUMMARY_WEEKDAYS_STORAGE_KEY = "ld:user-summary-show-weekdays";
const SUMMARY_DETAILS_STORAGE_KEY = "ld:user-summary-show-details";
const SUMMARY_SELECTOR = "[ld-sidebar-user-summary]";
const SUMMARY_QUERY_PARAMS = [
  "summary_mode",
  "summary_month",
  "summary_week",
  "summary_year",
  "summary_month_number",
  "summary_show_weekdays",
  "summary_show_details",
];

export function parseStoredBooleanPreference(value) {
  if (value === "1") {
    return true;
  }
  if (value === "0") {
    return false;
  }
  return null;
}

export function stripSummaryPreferenceParams(href) {
  return stripPreferenceQueryParams(href, SUMMARY_QUERY_PARAMS);
}

export function getSummaryStateFromElement(summaryElement) {
  if (!summaryElement) {
    return null;
  }

  return {
    currentMode: summaryElement.dataset.summaryMode,
    currentMonth: summaryElement.dataset.summaryMonth,
    currentWeek: summaryElement.dataset.summaryWeek,
    currentShowWeekdays: summaryElement.dataset.summaryShowWeekdays === "1",
    currentShowDetails: summaryElement.dataset.summaryShowDetails === "1",
  };
}

export function buildStoredSummaryPreferenceState({
  currentMode,
  currentMonth,
  currentWeek,
  currentShowWeekdays,
  currentShowDetails,
  hasSelectedRange,
  storedMode,
  storedMonth,
  storedWeek,
  storedShowWeekdays,
  storedShowDetails,
}) {
  let changed = false;
  const targetState = {
    mode: currentMode,
    month: currentMonth,
    week: currentWeek,
    showWeekdays: currentShowWeekdays,
    showDetails: currentShowDetails,
  };

  if (storedMode && storedMode !== currentMode) {
    targetState.mode = storedMode;
    changed = true;
  }

  if (!hasSelectedRange) {
    if (
      targetState.mode === "calendar" &&
      storedMonth &&
      storedMonth !== currentMonth
    ) {
      targetState.month = storedMonth;
      changed = true;
    }

    if (
      targetState.mode === "heatmap" &&
      storedWeek &&
      storedWeek !== currentWeek
    ) {
      targetState.week = storedWeek;
      changed = true;
    }
  }

  if (storedShowWeekdays !== null && storedShowWeekdays !== currentShowWeekdays) {
    targetState.showWeekdays = storedShowWeekdays;
    changed = true;
  }

  if (storedShowDetails !== null && storedShowDetails !== currentShowDetails) {
    targetState.showDetails = storedShowDetails;
    changed = true;
  }

  return changed ? targetState : null;
}

export function getStoredSummaryState() {
  return {
    storedMode: window.localStorage.getItem(SUMMARY_MODE_STORAGE_KEY),
    storedMonth: window.localStorage.getItem(SUMMARY_MONTH_STORAGE_KEY),
    storedWeek: window.localStorage.getItem(SUMMARY_WEEK_STORAGE_KEY),
    storedShowWeekdays: parseStoredBooleanPreference(
      window.localStorage.getItem(SUMMARY_WEEKDAYS_STORAGE_KEY),
    ),
    storedShowDetails: parseStoredBooleanPreference(
      window.localStorage.getItem(SUMMARY_DETAILS_STORAGE_KEY),
    ),
  };
}

export function buildSummaryRequestHeaders({
  currentMode,
  currentMonth,
  currentWeek,
  currentShowWeekdays,
  currentShowDetails,
  storedMode,
  storedMonth,
  storedWeek,
  storedShowWeekdays,
  storedShowDetails,
}) {
  const mode = storedMode || currentMode;
  const month = storedMonth || currentMonth;
  const week = storedWeek || currentWeek;
  const showWeekdays = storedShowWeekdays ?? currentShowWeekdays;
  const showDetails = storedShowDetails ?? currentShowDetails;

  if (!mode) {
    return null;
  }

  const headers = {
    "X-Linkding-Summary-Mode": mode,
    "X-Linkding-Summary-Show-Weekdays": showWeekdays ? "1" : "0",
    "X-Linkding-Summary-Show-Details": showDetails ? "1" : "0",
  };

  if (mode === "heatmap") {
    if (week) {
      headers["X-Linkding-Summary-Week"] = week;
    }
    return headers;
  }

  if (month) {
    headers["X-Linkding-Summary-Month"] = month;
  }
  return headers;
}

export function getRenderedSummaryState() {
  return getSummaryStateFromElement(document.querySelector(SUMMARY_SELECTOR));
}

export function applySummaryRequestHeaders(headerBag, summaryHeaders) {
  applyRequestHeaders(headerBag, summaryHeaders);
}

export function storeSummaryPreferences({
  mode,
  month,
  week,
  showWeekdays,
  showDetails,
}) {
  if (mode) {
    window.localStorage.setItem(SUMMARY_MODE_STORAGE_KEY, mode);
  }
  if (month !== undefined) {
    window.localStorage.setItem(SUMMARY_MONTH_STORAGE_KEY, month || "");
  }
  if (week !== undefined) {
    window.localStorage.setItem(SUMMARY_WEEK_STORAGE_KEY, week || "");
  }
  if (showWeekdays !== undefined && showWeekdays !== null) {
    window.localStorage.setItem(
      SUMMARY_WEEKDAYS_STORAGE_KEY,
      showWeekdays ? "1" : "0",
    );
  }
  if (showDetails !== undefined && showDetails !== null) {
    window.localStorage.setItem(
      SUMMARY_DETAILS_STORAGE_KEY,
      showDetails ? "1" : "0",
    );
  }
}

export function storeRenderedSummaryPreferences(summaryState) {
  storeSummaryPreferences({
    mode: summaryState?.currentMode,
    month: summaryState?.currentMonth,
    week: summaryState?.currentWeek,
    showWeekdays: summaryState?.currentShowWeekdays,
    showDetails: summaryState?.currentShowDetails,
  });
}

export function storeSummaryPreferenceTargets({
  targetMode,
  targetMonth,
  targetWeek,
  targetShowWeekdays,
  targetShowDetails,
}) {
  const targetState = {};

  if (targetMode) {
    targetState.mode = targetMode;
  }
  if (targetMonth) {
    targetState.month = targetMonth;
  }
  if (targetWeek) {
    targetState.week = targetWeek;
  }
  if (targetShowWeekdays === "1" || targetShowWeekdays === "0") {
    targetState.showWeekdays = targetShowWeekdays === "1";
  }
  if (targetShowDetails === "1" || targetShowDetails === "0") {
    targetState.showDetails = targetShowDetails === "1";
  }

  storeSummaryPreferences(targetState);
}

let summaryDisplayPreferencesRegistered = false;

export function registerSummaryDisplayPreferences() {
  if (summaryDisplayPreferencesRegistered) {
    return;
  }

  document.addEventListener("turbo:before-fetch-request", (event) => {
    const summaryHeaders = buildSummaryRequestHeaders({
      ...getRenderedSummaryState(),
      ...getStoredSummaryState(),
    });

    if (!summaryHeaders) {
      return;
    }

    event.detail.fetchOptions.headers ||= {};
    applySummaryRequestHeaders(event.detail.fetchOptions.headers, summaryHeaders);
  });

  summaryDisplayPreferencesRegistered = true;
}
