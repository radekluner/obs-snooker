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
  matchTitle: document.getElementById("match-title"),
  player1Name: document.getElementById("player1-name"),
  player2Name: document.getElementById("player2-name"),
  player1Break: document.getElementById("player1-break-value"),
  player2Break: document.getElementById("player2-break-value"),
  player1FrameScore: document.getElementById("player1-frame-score"),
  player2FrameScore: document.getElementById("player2-frame-score"),
  turnIndicator: document.getElementById("turn-indicator"),
  status: document.getElementById("status"),
};

function setStatus(message, isError) {
  els.status.textContent = message;
  els.status.classList.toggle("error", Boolean(isError));
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
      setStatus(`Error: ${data.error || response.statusText}`, true);
      return;
    }
    const data = await response.json();
    applyScore(data);
    setStatus("");
  } catch (err) {
    setStatus("Network error - is the overlay server running?", true);
  }
}

function applyScore(data) {
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
    column.querySelectorAll("button").forEach((button) => {
      button.disabled = !isActive;
    });
  });
  els.turnIndicator.textContent = data.current_player === 1
    ? `\u25c0 ${data.player1_name || "Player 1"} at the table`
    : `${data.player2_name || "Player 2"} at the table \u25b6`;

  if (data.match_title) {
    els.matchTitle.textContent = data.match_title;
  } else {
    els.matchTitle.textContent = "";
  }
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
    const player = columnEl ? Number(columnEl.dataset.player) : undefined;
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

refresh();
setInterval(refresh, POLL_INTERVAL_MS);
