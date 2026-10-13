// PULSE side panel (Track F). A thin client for the review API in pulse/api.py.
//
// Everything that comes from the API or the page is put on screen with textContent,
// never as HTML, so a finding's HTML snippet is shown as text and nothing in it can run.

const API = "http://localhost:8000";
const BAD_PAGE = /^https:\/\/www\.w3\.org\/WAI\/demos\/bad\/(before|after)\/(home|news|tickets|survey)\.html$/;
const BUCKET_ORDER = ["grey", "human-only", "machine"]; // grey first: those need a human most
const BUCKET_TITLES = {
  grey: "Grey area: needs your decision",
  "human-only": "Human-only checks",
  machine: "Machine-detected",
};
const DECISIONS = [
  ["fail", "Fail"],
  ["pass", "Pass"],
  ["not_applicable", "Not applicable"],
];
const DECISION_LABELS = Object.fromEntries(DECISIONS);

let token = ""; // kept in memory after it is read from chrome.storage.local; never shown
const drafts = new Map(); // finding_id -> comment being typed, so a reload never throws it away
let loadCounter = 0; // lets a newer load cancel an older one that is still waiting
let shownUrl = null; // the page the list on screen belongs to
const TIMEOUT_MS = 15000;

// ---------------------------------------------------------------- small helpers

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

function setStatus(text, isError = false) {
  const status = document.getElementById("status");
  status.textContent = text;
  status.classList.toggle("error", isError);
}

function stripUrl(url) {
  return String(url || "").split("#")[0].split("?")[0];
}

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status; // 0 means the API could not be reached at all
  }
}

// FastAPI puts the reason in "detail": a sentence, or a list of field problems for a 422.
function detailText(body) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .filter((item) => item && typeof item === "object")
      .map((item) => {
        const field = Array.isArray(item.loc) ? item.loc.slice(1).join(".") : "";
        return field ? `${field}: ${item.msg}` : String(item.msg);
      })
      .join("; ");
  }
  return "";
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(API + path, { ...options, signal: AbortSignal.timeout(TIMEOUT_MS) });
  } catch (error) {
    if (error && error.name === "TimeoutError") {
      throw new ApiError(0, `The review API at ${API} did not answer within ${TIMEOUT_MS / 1000} seconds. Try again.`);
    }
    throw new ApiError(0, `Can't reach the review API at ${API}, or it hit an error. If it isn't running, start it with: uvicorn pulse.api:app --port 8000. If it is, look in its terminal window for the error.`);
  }
  let body = null;
  try {
    body = await response.json();
  } catch (error) {
    body = null; // not JSON; the status code still tells us what happened
  }
  if (!response.ok) throw new ApiError(response.status, detailText(body) || `The API answered ${response.status}.`);
  return { status: response.status, body };
}

async function callApi(path, options = {}) {
  return (await request(path, options)).body;
}

function formatWhen(iso) {
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? String(iso || "") : when.toLocaleString();
}

// ---------------------------------------------------------------- one finding

function badge(decision) {
  if (!decision) return el("span", { class: "badge none", text: "No decision yet" });
  const label = DECISION_LABELS[decision.decision] || decision.decision;
  return el("span", { class: `badge ${decision.decision}`, text: `Decided: ${label}` });
}

function decisionSummary(decision, currentRunId) {
  if (!decision) return "No human decision recorded yet.";
  const label = DECISION_LABELS[decision.decision] || decision.decision;
  let text = `Human decision: ${label}, by ${decision.reviewer}, ${formatWhen(decision.decided_at)}.`;
  if (decision.comment) text += ` Comment: ${decision.comment}`;
  if (decision.run_id && decision.run_id !== currentRunId) text += ` (Recorded on an earlier run, ${decision.run_id}.)`;
  return text;
}

function aiSection(ai) {
  const data = ai || {};
  // Track C has not fixed the name of the "leaning" field yet; show it when one of these appears.
  const leaning = data.leaning || data.suggestion || data.recommendation || "";
  const isStub = data.case_for === "STUB" || data.prompt_version === "STUB";
  let suggestion = leaning ? String(leaning) : "none given";
  if (typeof data.confidence === "number") suggestion += ` (confidence ${data.confidence})`;

  return el("section", { class: "ai", "aria-label": "AI suggestion" }, [
    el("p", { class: "ai-label", text: "AI suggestion: written by AI, not a decision" }),
    isStub ? el("p", { class: "muted", text: "Placeholder text: the FOR and AGAINST agents are not built yet." }) : null,
    el("p", { text: `AI leaning: ${suggestion}` }),
    el("div", { class: "cases" }, [
      el("div", {}, [el("h4", { text: "Case FOR a failure" }), el("p", { text: data.case_for || "None" })]),
      el("div", {}, [el("h4", { text: "Case AGAINST a failure" }), el("p", { text: data.case_against || "None" })]),
    ]),
  ]);
}

function decisionForm(finding, runId, onSaved) {
  let saving = false;
  const commentId = `comment-${finding.finding_id}`;
  const comment = el("textarea", { id: commentId });
  comment.value = drafts.get(finding.finding_id) || "";
  comment.addEventListener("input", () => drafts.set(finding.finding_id, comment.value));
  const message = el("p", { class: "message", role: "status", "aria-live": "polite" });
  const buttons = DECISIONS.map(([value, label]) => el("button", { type: "button", "data-decision": value, text: label }));

  function say(text, kind) {
    message.textContent = text;
    message.className = `message ${kind}`;
  }

  async function save(value) {
    if (saving) return; // a save is already on its way
    if (!token) {
      say("Add your reviewer token in Settings (top of this panel) first.", "error");
      return;
    }
    saving = true;
    for (const button of buttons) button.disabled = true;
    say("Saving…", "");
    try {
      const sent = comment.value;
      const { status, body: saved } = await request("/decisions", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ finding_id: finding.finding_id, run_id: runId, decision: value, comment: sent }),
      });
      if (!saved || typeof saved !== "object") {
        throw new ApiError(status, "The API answered, but not in a way this panel understands. Press Refresh to see whether the decision was recorded.");
      }
      // The sent text is on file now. Keep the draft only if more was typed while the save was under way.
      if (comment.value === sent) drafts.delete(finding.finding_id);
      if (status === 200) {
        // The API already had this exact decision as this reviewer's latest, so it recorded nothing new.
        say(`Already saved: ${DECISION_LABELS[saved.decision]} with this comment is your latest decision. Nothing new was recorded.`, "ok");
      } else {
        say(`Saved: ${DECISION_LABELS[saved.decision]}, recorded for ${saved.reviewer}.`, "ok");
      }
      onSaved(saved);
    } catch (error) {
      if (error.status === 401) {
        say("Not signed in: the API did not recognise your token. Check it in Settings. Nothing was saved.", "error");
      } else if (error.status === 422) {
        say(`The API rejected this decision, so nothing was saved: ${error.message}`, "error");
      } else if (error.status >= 200 && error.status < 300) {
        say(error.message, "error");
      } else {
        say(`Not saved. ${error.message}`, "error");
      }
    } finally {
      saving = false;
      for (const button of buttons) button.disabled = false;
    }
  }

  for (const button of buttons) button.addEventListener("click", () => save(button.dataset.decision));

  return el("section", { class: "decide", "aria-label": "Your decision" }, [
    el("h3", { text: "Your decision" }),
    el("label", { for: commentId, text: "Comment (optional)" }),
    comment,
    el("div", { class: "row" }, buttons),
    message,
  ]);
}

function findingCard(finding, runId) {
  const mapping = finding.mapping || {};
  const evidence = finding.evidence || {};
  const criterion = mapping.sc || (finding.wcag || [])[0] || "?";
  const title = mapping.name ? `${criterion} ${mapping.name}` : criterion;
  const level = mapping.level ? ` (Level ${mapping.level})` : "";

  const badgeSlot = el("span", {}, [badge(finding.decision)]);
  const current = el("p", { class: "muted", text: decisionSummary(finding.decision, runId) });
  const pageLevel = evidence.selector === "html";

  let shot;
  if (evidence.screenshot) {
    const name = String(evidence.screenshot).split("/").pop();
    shot = el("img", {
      class: "shot",
      loading: "lazy",
      src: `${API}/shots/${encodeURIComponent(name)}`,
      alt: `Screenshot of the element for finding ${finding.finding_id}`,
    });
    shot.addEventListener("error", () => shot.replaceWith(el("p", { class: "muted", text: "Screenshot could not be loaded." })));
  } else {
    shot = el("p", { class: "muted", text: "No screenshot" });
  }

  const details = Array.isArray(finding.scanner_details) ? finding.scanner_details : [];
  const scannerSaid = details.filter((d) => d && typeof d === "object").map((d) => `${d.scanner}: ${d.message}`).join("\n");

  const body = el("div", { class: "body" }, [
    current,
    el("p", { class: "muted", text: `${finding.finding_id} · scanner status: ${finding.scanner_status || "unknown"}` }),
    mapping.rejected ? el("p", { class: "message error", text: `Mapper rejected this criterion: ${mapping.rejected}` }) : null,
    el("h3", { text: pageLevel ? "Where: the whole page" : "Where" }),
    pageLevel ? null : el("pre", {}, [el("code", { text: evidence.selector || "No selector" })]),
    el("h3", { text: "HTML snippet" }),
    el("pre", {}, [el("code", { text: evidence.html_snippet || "No snippet" })]),
    el("h3", { text: "Screenshot" }),
    shot,
    scannerSaid ? el("h3", { text: "Scanner said" }) : null,
    scannerSaid ? el("p", { text: scannerSaid }) : null,
    aiSection(finding.ai),
    decisionForm(finding, runId, (saved) => {
      badgeSlot.replaceChildren(badge(saved));
      current.textContent = decisionSummary(saved, runId);
    }),
  ]);

  const summary = el("summary", {}, [el("span", { class: "criterion", text: title + level }), badgeSlot]);
  return el("details", { class: "finding" }, [summary, body]);
}

// ---------------------------------------------------------------- the list for the current tab

function renderFindings(payload, pageUrl) {
  const list = document.getElementById("findings");
  list.replaceChildren();
  const all = Array.isArray(payload && payload.findings) ? payload.findings : [];
  const matching = all.filter((f) => f && typeof f === "object" && stripUrl(f.evidence && f.evidence.page_url) === pageUrl);

  const groups = new Map();
  for (const finding of matching) {
    const bucket = String(finding.bucket || "unrouted");
    if (!groups.has(bucket)) groups.set(bucket, []);
    groups.get(bucket).push(finding);
  }
  const buckets = [...groups.keys()].sort((a, b) => {
    const rank = (name) => (BUCKET_ORDER.includes(name) ? BUCKET_ORDER.indexOf(name) : BUCKET_ORDER.length);
    return rank(a) - rank(b) || a.localeCompare(b);
  });

  for (const bucket of buckets) {
    const findings = groups.get(bucket).sort((a, b) => (a.evidence?.dom_index ?? 0) - (b.evidence?.dom_index ?? 0));
    list.append(el("h2", { text: `${BUCKET_TITLES[bucket] || bucket} (${findings.length})` }));
    for (const finding of findings) {
      try {
        list.append(findingCard(finding, payload.run_id));
      } catch (error) {
        // One malformed finding must not blank the list for all the others.
        console.error("PULSE: could not show a finding", error);
        list.append(el("p", { class: "message error", text: `This finding could not be shown: ${String(finding.finding_id || "no id")}` }));
      }
    }
  }
  return matching.length;
}

// The address of this window's active tab; empty for pages this extension has no permission for.
async function activeTabUrl() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return stripUrl(tab && tab.url);
}

// Tab events fire often (any tab finishing loading, any tab switch). Reload only when this window
// is now showing a different page, so a comment being typed is never thrown away.
async function reloadIfPageChanged() {
  if ((await activeTabUrl()) !== shownUrl) load();
}

async function load() {
  const mine = ++loadCounter;
  const pageLine = document.getElementById("page-line");
  document.getElementById("findings").replaceChildren();
  setStatus("Loading…");

  const pageUrl = await activeTabUrl();
  if (mine !== loadCounter) return; // a newer load started while we were waiting
  shownUrl = pageUrl;
  const match = BAD_PAGE.exec(pageUrl);
  if (!match) {
    pageLine.textContent = "This tab is not a Before and After Demo page.";
    setStatus("Open a demo page, for example https://www.w3.org/WAI/demos/bad/before/home.html, then press Refresh.");
    return;
  }
  const [, version, page] = match;
  pageLine.textContent = `Page: ${version} / ${page}`;

  try {
    const run = await callApi(`/runs/latest?version=${version}&page=${page}`);
    const payload = await callApi(`/findings?run_id=${encodeURIComponent(run.run_id)}`);
    if (mine !== loadCounter) return; // the tab changed while we were waiting
    const shown = renderFindings(payload, pageUrl);
    const stubs = Array.isArray(payload.stubs) && payload.stubs.length ? ` Not built yet, so placeholder output: ${payload.stubs.join(", ")}.` : "";
    pageLine.textContent = `Page: ${version} / ${page} · run ${payload.run_id}`;
    setStatus(`${shown} finding${shown === 1 ? "" : "s"} for this page.${stubs}${token ? "" : " Add your reviewer token in Settings to record decisions."}`);
  } catch (error) {
    if (mine !== loadCounter) return;
    if (error.status === 404) {
      setStatus(`No pipeline run for ${version}/${page} yet. Run: python -m agents.graph --version ${version} --page ${page}`, true);
    } else {
      setStatus(error.message, true);
    }
  }
}

// ---------------------------------------------------------------- settings

function showTokenState() {
  document.getElementById("token-state").textContent = token
    ? "A token is saved in this browser."
    : "No token saved yet. You can read findings, but not record decisions.";
}

async function initSettings() {
  document.getElementById("api-base").textContent = API;
  document.getElementById("origin-line").textContent = `ADJ_EXTENSION_ORIGIN=chrome-extension://${chrome.runtime.id}`;

  const stored = await chrome.storage.local.get("reviewerToken");
  token = typeof stored.reviewerToken === "string" ? stored.reviewerToken : "";
  chrome.storage.local.remove("reviewerName"); // left over from an earlier version of this panel
  showTokenState();

  const input = document.getElementById("token");
  document.getElementById("token-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = input.value.trim();
    if (!value) return;
    if (!/^[\x21-\x7e]+$/.test(value)) {
      document.getElementById("token-state").textContent = "That doesn't look like a token: it has spaces or unusual characters. Nothing was saved.";
      return;
    }
    await chrome.storage.local.set({ reviewerToken: value });
    token = value;
    input.value = ""; // don't leave the token sitting in the field
    showTokenState();
    load();
  });
  document.getElementById("token-remove").addEventListener("click", async () => {
    await chrome.storage.local.remove("reviewerToken");
    token = "";
    showTokenState();
    load();
  });
}

// ---------------------------------------------------------------- start

document.getElementById("refresh").addEventListener("click", load);
chrome.tabs.onActivated.addListener(reloadIfPageChanged);
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "complete") reloadIfPageChanged();
});

initSettings().then(load);
