/*
 * Keeps the phone/tablet screen on while a page is open. Uses the Screen Wake
 * Lock API where available (https/localhost only) and falls back to NoSleep.js
 * (a hidden looping video) on plain http, which needs a first tap to start.
 */
(function () {
  let noSleep = null;

  async function requestWakeLock() {
    try {
      if (window.isSecureContext && navigator.wakeLock) {
        await navigator.wakeLock.request("screen");
        return true;
      }
    } catch (err) {
      // Denied (e.g. low battery) - fall through to the video fallback.
    }
    return false;
  }

  function enableNoSleep() {
    if (typeof NoSleep !== "function") {
      return;
    }
    noSleep = noSleep || new NoSleep();
    noSleep.enable().catch(() => {});
  }

  function start() {
    requestWakeLock().then((ok) => {
      if (ok) {
        return;
      }
      const arm = () => {
        enableNoSleep();
        document.removeEventListener("touchend", arm);
        document.removeEventListener("click", arm);
      };
      document.addEventListener("touchend", arm);
      document.addEventListener("click", arm);
    });
  }

  start();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      requestWakeLock().then((ok) => {
        if (!ok && noSleep) {
          enableNoSleep();
        }
      });
    }
  });
})();
