/**
 * Admin page: match setup (players, title, race length), manual score
 * correction, resets and player roster management. Everything is sent to
 * POST /api/admin; the server answers with the full admin snapshot, which
 * is rendered back into the form.
 */

const $ = (id) => document.getElementById(id);
const CUSTOM_NAME_OPTION = "(custom name below)";

let snapshot = null;

function setStatus(message, isError) {
  const el = $("status");
  el.textContent = message;
  el.classList.toggle("error", Boolean(isError));
}

async function post(body, okMessage) {
  try {
    const response = await fetch("/api/admin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      setStatus(`Error: ${data.error || response.statusText}`, true);
      return false;
    }
    snapshot = data;
    render(data, true);
    setStatus(okMessage || "Saved");
    return true;
  } catch (err) {
    setStatus("Network error - is the overlay server running?", true);
    return false;
  }
}

function fillPlayerSelect(select, players, selectedId) {
  select.innerHTML = "";
  const custom = document.createElement("option");
  custom.value = "";
  custom.textContent = CUSTOM_NAME_OPTION;
  select.appendChild(custom);
  players.forEach((player) => {
    const option = document.createElement("option");
    option.value = player.id;
    option.textContent = player.country ? `${player.name} (${player.country})` : player.name;
    select.appendChild(option);
  });
  select.value = selectedId || "";
}

// Inputs the user may be typing in are only overwritten on a full render
// (after an action), not by the background refresh.
function setIfIdle(input, value, force) {
  if (force || document.activeElement !== input) {
    input.value = value;
  }
}

function renderRoster(data) {
  const body = $("roster-body");
  body.innerHTML = "";
  data.players.forEach((player) => {
    const row = document.createElement("tr");
    row.innerHTML =
      '<td><input class="r-name" type="text" /></td>' +
      '<td class="country"><input class="r-country" type="text" list="flag-list" maxlength="3" /></td>' +
      '<td class="row-actions"><button class="r-save">Save</button><button class="danger r-delete">Delete</button></td>';
    row.querySelector(".r-name").value = player.name;
    row.querySelector(".r-country").value = player.country || "";
    row.querySelector(".r-save").addEventListener("click", () => {
      post({
        action: "update_player",
        id: player.id,
        name: row.querySelector(".r-name").value,
        country: row.querySelector(".r-country").value,
      }, "Player saved");
    });
    row.querySelector(".r-delete").addEventListener("click", () => {
      if (confirm(`Delete ${player.name}?`)) {
        post({ action: "remove_player", id: player.id }, "Player deleted");
      }
    });
    body.appendChild(row);
  });
}

function render(data, force) {
  if (force || document.activeElement.tagName !== "SELECT") {
    fillPlayerSelect($("player1-id"), data.players, data.config.player1_id);
    fillPlayerSelect($("player2-id"), data.players, data.config.player2_id);
  }
  setIfIdle($("player1-name"), data.config.player1_custom_name, force);
  setIfIdle($("player2-name"), data.config.player2_custom_name, force);
  setIfIdle($("match-title-input"), data.match_title, force);
  setIfIdle($("frames-to-win"), data.frames_to_win, force);

  $("corr-name1").textContent = data.player1_name;
  $("corr-name2").textContent = data.player2_name;
  setIfIdle($("score1"), data.player1_frame_score, force);
  setIfIdle($("score2"), data.player2_frame_score, force);
  setIfIdle($("frames1"), data.player1_frames, force);
  setIfIdle($("frames2"), data.player2_frames, force);
  setIfIdle($("break"), data.current_break, force);
  $("break-owner").textContent = `Break of ${data.current_player === 1 ? data.player1_name : data.player2_name}`;

  const flags = $("flag-list");
  if (flags.children.length !== data.flags.length) {
    flags.innerHTML = "";
    data.flags.forEach((code) => {
      const option = document.createElement("option");
      option.value = code;
      flags.appendChild(option);
    });
  }

  // Roster rows contain inputs, so only rebuild them when the list changed.
  const signature = JSON.stringify(data.players);
  if (force || signature !== renderRoster.last) {
    if (force || !$("roster-body").contains(document.activeElement)) {
      renderRoster(data);
      renderRoster.last = signature;
    }
  }
  $("players-count").textContent = `(${data.players.length})`;
  $("add-player").disabled = !data.roster_available;
  renderFoul(data, force);
}

function renderFoul(data, force) {
  $("foul-card").hidden = !data.foul;
  if (!data.foul) {
    return;
  }
  const select = $("foul-source");
  if (force || document.activeElement !== select) {
    select.innerHTML = "";
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "(none selected)";
    select.appendChild(none);
    data.foul.sources.forEach((name) => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      select.appendChild(option);
    });
    select.value = data.foul.camera_source;
  }
  setIfIdle($("foul-opacity"), data.foul.opacity, force);
  const labels = {
    off: "Scoreboard window shows the normal scoreboard.",
    reference: "Scoreboard window shows the reference screenshot.",
    stored: "Reference stored. Press 2 to show the comparison.",
    compare: "Scoreboard window shows the current screenshot over the reference.",
  };
  const key = data.foul.mode === "off" && data.foul.has_reference ? "stored" : data.foul.mode;
  $("foul-state").textContent = labels[key] || "";
}

async function refresh() {
  try {
    const response = await fetch("/api/admin", { cache: "no-store" });
    if (response.ok) {
      snapshot = await response.json();
      render(snapshot, false);
    }
  } catch (err) {
    setStatus("Network error - is the overlay server running?", true);
  }
}

$("apply-match").addEventListener("click", () => {
  post({
    action: "set_match",
    player1_id: $("player1-id").value,
    player2_id: $("player2-id").value,
    player1_name: $("player1-name").value,
    player2_name: $("player2-name").value,
    match_title: $("match-title-input").value,
    frames_to_win: Number($("frames-to-win").value),
  }, "Match setup applied");
});

document.querySelectorAll("[data-set]").forEach((button) => {
  button.addEventListener("click", () => {
    const body = { action: button.dataset.set, value: Number($(button.dataset.input).value) };
    if (button.dataset.player) {
      body.player = Number(button.dataset.player);
    }
    post(body, "Score updated");
  });
});

document.querySelectorAll("[data-reset]").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.dataset.confirm && !confirm(button.dataset.confirm)) {
      return;
    }
    post({ action: button.dataset.reset }, "Done");
  });
});

function foulSettings() {
  return {
    action: "foul_settings",
    camera_source: $("foul-source").value,
    opacity: Number($("foul-opacity").value),
  };
}

$("foul-save").addEventListener("click", () => post(foulSettings(), "Settings saved"));

document.querySelectorAll("[data-foul]").forEach((button) => {
  button.addEventListener("click", async () => {
    const action = button.dataset.foul;
    if (action !== "foul_hide") {
      // Make sure the screenshot uses the currently selected camera.
      if (!(await post(foulSettings(), "Capturing..."))) {
        return;
      }
    }
    if (await post({ action }, action === "foul_hide" ? "Scoreboard restored" : "Capturing screenshot...")) {
      // The image arrives a moment later; the periodic refresh shows the new mode.
      setTimeout(refresh, 1500);
    }
  });
});

$("add-player").addEventListener("click", async () => {
  const ok = await post({
    action: "add_player",
    name: $("new-player-name").value,
    country: $("new-player-country").value,
  }, "Player added");
  if (ok) {
    $("new-player-name").value = "";
    $("new-player-country").value = "";
  }
});

refresh().then(() => snapshot && render(snapshot, true));
setInterval(refresh, 2000);
