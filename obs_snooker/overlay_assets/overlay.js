/**
 * Polls the overlay server's JSON score endpoint and updates the DOM.
 *
 * Polling (rather than a persistent connection) keeps this compatible with
 * OBS's embedded browser without extra dependencies, and is simple enough
 * to reason about and test in a regular browser.
 */

const POLL_INTERVAL_MS = 400;

const els = {
  matchTitle: document.getElementById("match-title"),
  raceLabel: document.getElementById("race-label"),
  player1Name: document.getElementById("player1-name"),
  player2Name: document.getElementById("player2-name"),
  player1Flag: document.getElementById("player1-flag"),
  player2Flag: document.getElementById("player2-flag"),
  player1Frames: document.getElementById("player1-frames"),
  player2Frames: document.getElementById("player2-frames"),
  player1FrameScore: document.getElementById("player1-frame-score"),
  player2FrameScore: document.getElementById("player2-frame-score"),
  player1Plate: document.getElementById("player1-plate"),
  player2Plate: document.getElementById("player2-plate"),
  player1Break: document.getElementById("player1-break"),
  player2Break: document.getElementById("player2-break"),
  player1Balls: document.getElementById("player1-balls"),
  player2Balls: document.getElementById("player2-balls"),
  player1Turn: document.getElementById("player1-turn"),
  player2Turn: document.getElementById("player2-turn"),
};

let lastFrames = { player1_frames: null, player2_frames: null };
let lastFrameScores = { player1_frame_score: null, player2_frame_score: null };

// Thresholds (in characters) at which the player-name font size starts
// shrinking, so long names don't stretch the scoreboard bug or overflow it.
// `overlay.css`'s `max-width` + `text-overflow: ellipsis` on `.player-name`
// is the last-resort safety net if a name is still too wide even at the
// smallest size.
const BALL_ORDER = ["red", "yellow", "green", "brown", "blue", "pink", "black"];

// Renders the balls potted in the current break as coloured dots with a
// count, e.g. red x3, black x1. Rebuilt only when the content changes.
function renderBreakBalls(el, balls, show) {
  el.hidden = !show || balls.length === 0;
  if (el.hidden) {
    el.dataset.key = "";
    return;
  }
  const key = balls.join(",");
  if (el.dataset.key === key) {
    return;
  }
  el.dataset.key = key;
  el.innerHTML = "";
  BALL_ORDER.forEach((ball) => {
    const count = balls.filter((b) => b === ball).length;
    if (!count) {
      return;
    }
    const item = document.createElement("span");
    item.className = "ball-count";
    item.innerHTML = `<i class="ball ${ball}"></i><em>${count}</em>`;
    el.appendChild(item);
  });
}

const LONG_NAME_THRESHOLD = 14;
const VERY_LONG_NAME_THRESHOLD = 20;

// Shows the country flag (served from /flags/<code>.svg) or hides the image
// if there is no country or no bundled flag for it.
function setFlag(img, country) {
  const code = (country || "").trim().toLowerCase();
  if (!/^[a-z]{2,3}$/.test(code)) {
    img.hidden = true;
    img.removeAttribute("src");
    return;
  }
  const url = "/flags/" + code + ".svg";
  if (img.getAttribute("src") !== url) {
    img.hidden = false;
    img.onerror = () => { img.hidden = true; };
    img.setAttribute("src", url);
  }
}

function applyNameSizing(el, length) {
  el.classList.toggle("long", length > LONG_NAME_THRESHOLD && length <= VERY_LONG_NAME_THRESHOLD);
  el.classList.toggle("very-long", length > VERY_LONG_NAME_THRESHOLD);
}

function bump(el) {
  el.classList.remove("bump");
  // Force a reflow so the animation can be retriggered on consecutive bumps.
  void el.offsetWidth;
  el.classList.add("bump");
}

async function refresh() {
  try {
    const response = await fetch("/api/score", { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    const data = await response.json();

    if (data.match_title) {
      els.matchTitle.textContent = data.match_title;
      els.matchTitle.classList.add("visible");
    } else {
      els.matchTitle.classList.remove("visible");
    }

    if (data.frames_to_win > 1) {
      const bestOf = 2 * data.frames_to_win - 1;
      els.raceLabel.textContent = `Race to ${data.frames_to_win} \u00b7 Best of ${bestOf}`;
      els.raceLabel.classList.add("visible");
    } else {
      els.raceLabel.classList.remove("visible");
    }

    els.player1Name.textContent = data.player1_name;
    els.player2Name.textContent = data.player2_name;
    // Both names use the same font size (driven by whichever name is
    // longer), so the two name plates stay visually balanced instead of
    // one looking bigger than the other.
    const longestNameLength = Math.max(data.player1_name.length, data.player2_name.length);
    applyNameSizing(els.player1Name, longestNameLength);
    applyNameSizing(els.player2Name, longestNameLength);
    setFlag(els.player1Flag, data.player1_country);
    setFlag(els.player2Flag, data.player2_country);

    // The in-progress frame score is the big, dominant number (it changes
    // shot-by-shot), so it gets the "pop" bump animation on every change.
    if (lastFrameScores.player1_frame_score !== null && data.player1_frame_score !== lastFrameScores.player1_frame_score) {
      bump(els.player1FrameScore);
    }
    if (lastFrameScores.player2_frame_score !== null && data.player2_frame_score !== lastFrameScores.player2_frame_score) {
      bump(els.player2FrameScore);
    }
    els.player1FrameScore.textContent = data.player1_frame_score;
    els.player2FrameScore.textContent = data.player2_frame_score;
    lastFrameScores = {
      player1_frame_score: data.player1_frame_score,
      player2_frame_score: data.player2_frame_score,
    };

    if (lastFrames.player1_frames !== null && data.player1_frames !== lastFrames.player1_frames) {
      bump(els.player1Frames);
    }
    if (lastFrames.player2_frames !== null && data.player2_frames !== lastFrames.player2_frames) {
      bump(els.player2Frames);
    }

    els.player1Frames.textContent = data.player1_frames;
    els.player2Frames.textContent = data.player2_frames;
    lastFrames = { player1_frames: data.player1_frames, player2_frames: data.player2_frames };

    // Highlight the plate of whoever is at the table and show their
    // current break as a chip.
    [1, 2].forEach((number) => {
      const isActive = data.current_player === number;
      els[`player${number}Plate`].classList.toggle("active", isActive);
      els[`player${number}Plate`].classList.toggle("inactive", !isActive);
      const chip = els[`player${number}Break`];
      const showBreak = isActive && data.current_break > 0;
      chip.hidden = !showBreak;
      renderBreakBalls(els[`player${number}Balls`], data.break_balls || [], isActive);
      if (showBreak) {
        const value = chip.querySelector("b");
        if (value.textContent !== String(data.current_break)) {
          value.textContent = data.current_break;
          bump(value);
        }
      }
    });

    els.player1Turn.classList.toggle("active", data.current_player === 1);
    els.player2Turn.classList.toggle("active", data.current_player === 2);
  } catch (err) {
    // Network hiccup (e.g. OBS still starting the browser source); retry on
    // the next poll instead of throwing.
  }
}

refresh();
setInterval(refresh, POLL_INTERVAL_MS);
