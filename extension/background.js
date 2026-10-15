// Clicking the toolbar icon opens the side panel. Nothing else runs in the background.
chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch((error) => console.error("PULSE: could not set the side panel behaviour", error));
