// PULSE content script (Track F). Runs only on the W3C Before and After Demo pages.
//
// When the side panel asks, it scrolls to one finding's element and outlines it, so the
// reviewer can see what the finding is about. It only reads the page and adds a temporary
// outline; it never changes the page's content and never sends anything anywhere.

const OUTLINE = "4px solid #d4001a";
let highlighted = null; // { element, outline, outlineOffset, owner } of the element outlined now

function clearHighlight() {
  if (!highlighted) return;
  highlighted.element.style.outline = highlighted.outline;
  highlighted.element.style.outlineOffset = highlighted.outlineOffset;
  highlighted = null;
}

// Rendered, and something a reviewer could actually see.
function isVisible(element) {
  if (element.getClientRects().length === 0) return false; // display: none, or not in the page's layout
  const box = element.getBoundingClientRect();
  if (box.width === 0 && box.height === 0) return false;
  return getComputedStyle(element).visibility !== "hidden";
}

// Answer with what happened, so the panel can tell the reviewer. Any earlier outline is removed first,
// so at most one element on the page is ever outlined.
function highlight(selector, owner) {
  clearHighlight();
  if (selector === "html") return { result: "page-level" };
  if (typeof selector !== "string" || !selector) return { result: "no-selector" };
  let element;
  try {
    element = document.querySelector(selector);
  } catch (error) {
    return { result: "bad-selector" }; // the scanner's selector isn't valid CSS here
  }
  if (!element) return { result: "not-found" };
  if (!isVisible(element)) return { result: "hidden" };

  highlighted = { element, outline: element.style.outline, outlineOffset: element.style.outlineOffset, owner };
  element.style.outline = OUTLINE;
  element.style.outlineOffset = "2px";
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  element.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "center", inline: "nearest" });
  return { result: "highlighted" };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || !message || typeof message !== "object") return false;
  if (message.type === "pulse-highlight") sendResponse(highlight(message.selector, message.owner));
  else if (message.type === "pulse-clear") {
    // A clear names the finding it is for, so closing one card never removes another card's outline.
    // A clear with no owner (the panel rebuilding its list) removes whatever is outlined.
    if (message.owner && highlighted && highlighted.owner !== message.owner) {
      sendResponse({ result: "not-owner" });
    } else {
      clearHighlight();
      sendResponse({ result: "cleared" });
    }
  }
  return false; // answered synchronously
});
