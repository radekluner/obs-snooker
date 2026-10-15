/**
 * Control panel logic: wires every button's click to POST /api/action, and
 * polls /api/score (same endpoint the broadcast overlay and the scoreboard
 * window use) to keep the live score/turn display up to date, so the
 * operator gets immediate feedback that a click actually registered.
 */

const POLL_INTERVAL_MS = 500;

const BALL_ORDER = ["red", "yellow", "green", "brown", "blue", "pink", "black"];

function renderBreakBalls(el, balls) {
  const key = balls.join(",");
  if (el.dataset.key === key) {
    return;
  }
  el.dataset.key = key;
  el.innerHTML = "";
  BALL_ORDER.forEach((ball) => {
    const count = balls.filter((b) => b === ball).length;
    if (count) {
      const item = document.createElement("span");
      item.className = "ball-count";
      item.innerHTML = `<i class="ball ${ball}"></i><em>${count}</em>`;
      el.appendChild(item);
    }
  });
}

const els = {
  player1Name: document.getElementById("player1-name"),
  player2Name: document.getElementById("player2-name"),
  player1Break: document.getElementById("player1-break-value"),
  player2Break: document.getElementById("player2-break-value"),
  player1FrameScore: document.getElementById("player1-frame-score"),
  player2FrameScore: document.getElementById("player2-frame-score"),
  turnIndicator: document.getElementById("turn-indicator"),
  toast: document.getElementById("toast"),
};

let toastTimer = null;
els.toast.addEventListener("click", () => { els.toast.hidden = true; });

// Errors are shown as a large banner at the top of the screen, so they are not overlooked.
function showError(message) {
  const toast = els.toast;
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 6000);
}

async function sendAction(body) {
  try {
    const response = await fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      showError(data.error || response.statusText);
      return;
    }
    const data = await response.json();
    applyScore(data);
    els.toast.hidden = true;
  } catch (err) {
    showError("Network error - is the overlay server running?");
  }
}

let currentPlayer = 1;
const lastScores = { 1: 0, 2: 0 };

function applyScore(data) {
  currentPlayer = data.current_player;
  lastScores[1] = data.player1_frame_score;
  lastScores[2] = data.player2_frame_score;
  els.player1Name.textContent = data.player1_name || "Player 1";
  els.player2Name.textContent = data.player2_name || "Player 2";
  els.player1FrameScore.textContent = data.player1_frame_score;
  els.player2FrameScore.textContent = data.player2_frame_score;
  els.player1Break.textContent = data.current_player === 1 ? data.current_break : "\u2013";
  els.player2Break.textContent = data.current_player === 2 ? data.current_break : "\u2013";
  renderBreakBalls(document.getElementById("player1-balls"), data.current_player === 1 ? data.break_balls || [] : []);
  renderBreakBalls(document.getElementById("player2-balls"), data.current_player === 2 ? data.break_balls || [] : []);
  document.querySelectorAll(".player-column").forEach((column) => {
    const isActive = Number(column.dataset.player) === data.current_player;
    column.classList.toggle("active", isActive);
    // Everything is locked for the player who is not at the table.
    // "Wins frame" stays available for both players: the frame is often won while the opponent is at the table.
    column.querySelectorAll("button").forEach((button) => {
      button.disabled = !isActive;
    });
  });
  els.turnIndicator.textContent = data.current_player === 1 ? "\u25c0" : "\u25b6";
  els.turnIndicator.setAttribute(
    "aria-label",
    `${data.current_player === 1 ? data.player1_name : data.player2_name} is at the table`,
  );
}

async function refresh() {
  try {
    const response = await fetch("/api/score", { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    applyScore(await response.json());
  } catch (err) {
    // Network hiccup - keep showing the last known state, retry next poll.
  }
}

document.querySelectorAll("button[data-action]").forEach((button) => {
  button.addEventListener("click", () => {
    const action = button.dataset.action;
    const columnEl = button.closest("[data-player]");
    let player = columnEl ? Number(columnEl.dataset.player) : undefined;
    if (action === "undo_ball") {
      player = currentPlayer;
    }
    if (action === "win_frame") {
      player = currentPlayer;
      const own = lastScores[player];
      const other = lastScores[player === 1 ? 2 : 1];
      if (own < other) {
        alert(`Hráč na tahu má méně bodů (${own}:${other}), takže tento frame nemohl vyhrát.`);
        return;
      }
    }
    const body = { action };
    if (player) {
      body.player = player;
    }
    if (button.dataset.ball) {
      body.ball = button.dataset.ball;
    }
    if (button.dataset.value !== undefined) {
      body.value = Number(button.dataset.value);
    }
    sendAction(body);
  });
});

let lastPanelSwitch = 0;
// Tapping the panel of the player who is not at the table hands the turn over to them.
document.querySelectorAll(".player-column").forEach((column) => {
  column.addEventListener("click", (event) => {
    // The short lock stops a double tap from switching the turn back again.
    if (!column.classList.contains("active") && Date.now() - lastPanelSwitch > 800) {
      lastPanelSwitch = Date.now();
      sendAction({ action: "switch_turn" });
    }
  });
});

refresh();
setInterval(refresh, POLL_INTERVAL_MS);

// Fullscreen / PWA support: the button hides the browser bars (needs a tap, so it can't be automatic).
const fullscreenButton = document.getElementById("fullscreen-button");
const isInstalledApp = window.matchMedia("(display-mode: fullscreen), (display-mode: standalone)").matches;
if (fullscreenButton && document.documentElement.requestFullscreen && !isInstalledApp) {
  fullscreenButton.hidden = false;
  fullscreenButton.addEventListener("click", () => {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      document.documentElement.requestFullscreen({ navigationUI: "hide" }).catch(() => {});
    }
  });
  document.addEventListener("fullscreenchange", () => {
    fullscreenButton.textContent = document.fullscreenElement ? "Exit fullscreen" : "Fullscreen";
  });
}

// iPhone Safari has no Fullscreen API for web pages: the only way is "Add to Home Screen".
if (fullscreenButton && !document.documentElement.requestFullscreen && !isInstalledApp) {
  fullscreenButton.hidden = false;
  fullscreenButton.addEventListener("click", () => {
    alert("Na iPhonu: klepni na Sdílet (čtvereček se šipkou) a zvol „Přidat na plochu“. Po otevření z plochy poběží aplikace na celou obrazovku.");
  });
}

if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

// Tapping the club logo shows QR codes that open /control and /admin on a phone.
const qrModal = document.getElementById("qr-modal");

function renderQr(target, url) {
  const qr = qrcode(0, "M");
  qr.addData(url);
  qr.make();
  target.innerHTML = qr.createSvgTag({ cellSize: 5, margin: 2, scalable: true });
  target.title = url;
}

document.getElementById("logo-button").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/links", { cache: "no-store" });
    const links = await response.json();
    renderQr(document.getElementById("qr-control"), links.control);
    renderQr(document.getElementById("qr-admin"), links.admin);
    qrModal.hidden = false;
  } catch (err) {
    // Server unreachable - nothing to show.
  }
});

qrModal.addEventListener("click", (event) => {
  if (!event.target.closest(".qr-items")) {
    qrModal.hidden = true;
  }
});
