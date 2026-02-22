const MAX_PROGRAM_LIMIT = 128;
const MAX_JUMP_ABS = MAX_PROGRAM_LIMIT - 1;
const DIRS = [
  { name: "N", dx: 0, dy: -1, className: "dir-n" },
  { name: "E", dx: 1, dy: 0, className: "dir-e" },
  { name: "S", dx: 0, dy: 1, className: "dir-s" },
  { name: "W", dx: -1, dy: 0, className: "dir-w" },
];
const NORTH_DIR = 0;
const PLAYER_ID_KEY = "rr.player_id.v1";
const PROGRESS_KEY = "rr.progress.v1";
const CURRENT_LEVEL_KEY = "rr.current_level.v1";

const speedRange = document.getElementById("speedRange");
const speedNumber = document.getElementById("speedNumber");
const addFBtn = document.getElementById("addFBtn");
const addLBtn = document.getElementById("addLBtn");
const addRBtn = document.getElementById("addRBtn");
const addSBtn = document.getElementById("addSBtn");
const addJBtn = document.getElementById("addJBtn");
const deleteSelectedBtn = document.getElementById("deleteSelectedBtn");
const selectedInfoEl = document.getElementById("selectedInfo");
const clearProgramBtn = document.getElementById("clearProgramBtn");
const copyProgramBtn = document.getElementById("copyProgramBtn");
const pasteProgramBtn = document.getElementById("pasteProgramBtn");
const stepBtn = document.getElementById("stepBtn");
const runBtn = document.getElementById("runBtn");
const resetBtn = document.getElementById("resetBtn");
const clearTrailBtn = document.getElementById("clearTrailBtn");
const arenaEl = document.getElementById("arena");
const boardWrap = document.getElementById("boardWrap");
const execCountdownEl = document.getElementById("execCountdown");
const boardEl = document.getElementById("board");
const programStripEl = document.getElementById("programStrip");
const programSummaryEl = document.getElementById("programSummary");
const statusEl = document.getElementById("status");
const runtimeMetaEl = document.getElementById("runtimeMeta");
const levelMetaEl = document.getElementById("levelMeta");
const currentLevelTopEl = document.getElementById("currentLevelTop");
const campaignMetaEl = document.getElementById("campaignMeta");
const playerMetaEl = document.getElementById("playerMeta");
const prevLevelBtn = document.getElementById("prevLevelBtn");
const nextLevelBtn = document.getElementById("nextLevelBtn");
const submitSolutionBtn = document.getElementById("submitSolutionBtn");
const loadSelectedLevelBtn = document.getElementById("loadSelectedLevelBtn");
const levelJumpRange = document.getElementById("levelJumpRange");
const levelJumpNumber = document.getElementById("levelJumpNumber");

const state = {
  width: 11,
  height: 11,
  board: [],
  start: { x: 1, y: 1, dir: NORTH_DIR },
  robot: { x: 1, y: 1, dir: NORTH_DIR },
  pc: 0,
  lastPc: -1,
  stepCount: 0,
  trail: new Set(),
  userProgram: [],
  selectedProgramIndex: -1,
  timer: null,
  runDelay: 140,
  status: "ready",
  programLimit: 14,
  maxSteps: 420,
  cells: [],
  levelId: "",
  levelCount: 0,
  currentLevel: 1,
  selectedLevel: 1,
  highestUnlocked: 1,
  playerId: "",
  localSolved: false,
  submitting: false,
};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function wrap(value, mod) {
  if (mod <= 0) {
    return 0;
  }
  const normalized = value % mod;
  return normalized < 0 ? normalized + mod : normalized;
}

function keyForCell(x, y) {
  return `${x},${y}`;
}

function isInBounds(x, y) {
  return x >= 0 && y >= 0 && x < state.width && y < state.height;
}

function peekAhead(robot) {
  const vec = DIRS[robot.dir];
  const x = robot.x + vec.dx;
  const y = robot.y + vec.dy;
  return { x, y, outside: !isInBounds(x, y) };
}

function cloneProgram(program) {
  return program.map((inst) => ({ op: inst.op, arg: typeof inst.arg === "number" ? inst.arg : 1 }));
}

function blockCount(board) {
  let count = 0;
  for (const row of board) {
    for (const cell of row) {
      if (cell) {
        count += 1;
      }
    }
  }
  return count;
}

function instructionText(inst) {
  if (!inst) {
    return "?";
  }
  if (inst.op !== "J") {
    return inst.op;
  }
  const offset = Number.isFinite(inst.arg) ? inst.arg : 1;
  return `J${offset >= 0 ? "+" : ""}${offset}`;
}

function instructionDisplay(inst) {
  if (!inst) {
    return "?";
  }
  if (inst.op === "F") {
    return "↑";
  }
  if (inst.op === "L") {
    return "↺";
  }
  if (inst.op === "R") {
    return "↻";
  }
  if (inst.op === "S") {
    return "S";
  }
  const offset = Number.isFinite(inst.arg) ? inst.arg : 1;
  return `J${offset >= 0 ? "+" : ""}${offset}`;
}

function instructionTooltip(inst) {
  if (!inst) {
    return "Unknown instruction.";
  }
  if (inst.op === "F") {
    return "F (Forward): move one cell forward; exiting the board wins.";
  }
  if (inst.op === "L") {
    return "L (Left): rotate 90 degrees counter-clockwise.";
  }
  if (inst.op === "R") {
    return "R (Right): rotate 90 degrees clockwise.";
  }
  if (inst.op === "S") {
    return "S (Sense): if blocked ahead, execute next; otherwise skip next.";
  }
  if (inst.op === "J") {
    const offset = Number.isFinite(inst.arg) ? inst.arg : 1;
    const signed = offset >= 0 ? `+${offset}` : String(offset);
    return `J${signed} (Jump): shift instruction pointer by ${signed}.`;
  }
  return `${inst.op}: instruction.`;
}

function programText(program) {
  return program.map((inst) => instructionText(inst)).join(" ");
}

function parseProgramText(rawText) {
  const normalized = rawText.replace(/[,\n\r\t;]+/g, " ").trim();
  if (!normalized) {
    return { program: [], clampedOffsets: 0, error: null };
  }
  const rawTokens = normalized.split(/\s+/).filter(Boolean);
  const parsed = [];
  let clampedOffsets = 0;

  for (let i = 0; i < rawTokens.length; i += 1) {
    const token = rawTokens[i];
    const upper = token.toUpperCase();

    if (token === "↑" || upper === "F") {
      parsed.push({ op: "F", arg: 1 });
      continue;
    }
    if (token === "↺" || upper === "L") {
      parsed.push({ op: "L", arg: 1 });
      continue;
    }
    if (token === "↻" || upper === "R") {
      parsed.push({ op: "R", arg: 1 });
      continue;
    }
    if (upper === "S") {
      parsed.push({ op: "S", arg: 1 });
      continue;
    }
    if (upper === "J" || upper.startsWith("J")) {
      let offsetToken = token.slice(1);
      if (!offsetToken && i + 1 < rawTokens.length && /^[-+]?\d+$/.test(rawTokens[i + 1])) {
        offsetToken = rawTokens[i + 1];
        i += 1;
      }
      let offset = 1;
      if (offsetToken) {
        if (!/^[-+]?\d+$/.test(offsetToken)) {
          return { program: [], clampedOffsets: 0, error: `Invalid instruction token: ${token}` };
        }
        offset = parseInt(offsetToken, 10);
      }
      if (!Number.isFinite(offset) || offset === 0) {
        offset = 1;
      }
      const clamped = clamp(offset, -MAX_JUMP_ABS, MAX_JUMP_ABS);
      if (clamped !== offset) {
        clampedOffsets += 1;
      }
      parsed.push({ op: "J", arg: clamped === 0 ? 1 : clamped });
      continue;
    }
    return { program: [], clampedOffsets: 0, error: `Invalid instruction token: ${token}` };
  }

  return { program: parsed, clampedOffsets, error: null };
}

function setStatus(text, tone) {
  statusEl.textContent = text;
  statusEl.classList.remove("ok", "bad");
  if (tone) {
    statusEl.classList.add(tone);
  }
}

function stopAutoRun() {
  if (state.timer) {
    clearInterval(state.timer);
    state.timer = null;
  }
  runBtn.textContent = "Auto Run";
}

function setArenaState(stateName) {
  arenaEl.classList.remove("solved", "crashed");
  if (stateName === "solved") {
    arenaEl.classList.add("solved");
  } else if (stateName === "crashed") {
    arenaEl.classList.add("crashed");
  }
}

function buildBoardNodes() {
  boardEl.innerHTML = "";
  state.cells = [];
  for (let y = 0; y < state.height; y += 1) {
    for (let x = 0; x < state.width; x += 1) {
      const cell = document.createElement("div");
      cell.className = "cell";
      cell.dataset.x = String(x);
      cell.dataset.y = String(y);
      state.cells.push(cell);
      boardEl.appendChild(cell);
    }
  }
  adjustBoardSize();
}

function renderBoard() {
  let index = 0;
  for (let y = 0; y < state.height; y += 1) {
    for (let x = 0; x < state.width; x += 1) {
      const cell = state.cells[index];
      const classes = ["cell"];
      if (state.board[y][x]) {
        classes.push("blocked");
      }
      if (state.trail.has(keyForCell(x, y))) {
        classes.push("trail");
      }
      if (x === state.start.x && y === state.start.y) {
        classes.push("start");
      }
      if (isInBounds(state.robot.x, state.robot.y) && x === state.robot.x && y === state.robot.y) {
        classes.push("robot", DIRS[state.robot.dir].className);
      }
      cell.className = classes.join(" ");
      index += 1;
    }
  }
}

function adjustBoardSize() {
  const bounds = boardWrap.getBoundingClientRect();
  const maxWidth = Math.max(180, bounds.width - 36);
  const maxHeight = Math.max(180, bounds.height - 36);
  const size = Math.floor(clamp(Math.min(maxWidth / state.width, maxHeight / state.height), 18, 56));
  boardEl.style.setProperty("--cell-size", `${size}px`);
  boardEl.style.gridTemplateColumns = `repeat(${state.width}, ${size}px)`;
  boardEl.style.gridTemplateRows = `repeat(${state.height}, ${size}px)`;
}

function resetRun(keepTrail) {
  stopAutoRun();
  setArenaState(null);
  state.robot = { x: state.start.x, y: state.start.y, dir: state.start.dir };
  state.pc = 0;
  state.lastPc = -1;
  state.stepCount = 0;
  state.status = "ready";
  state.localSolved = false;
  if (!keepTrail) {
    state.trail.clear();
  }
  renderBoard();
  renderProgramEditor();
  updateRuntimeMeta();
  setStatus(`Level ${state.currentLevel} ready.`);
}

function ensureSelectionIsValid() {
  if (state.userProgram.length === 0) {
    state.selectedProgramIndex = -1;
    return;
  }
  if (state.selectedProgramIndex < 0 || state.selectedProgramIndex >= state.userProgram.length) {
    state.selectedProgramIndex = state.userProgram.length - 1;
  }
}

function getSelectedJumpTargetIndex() {
  ensureSelectionIsValid();
  const sourceIndex = state.selectedProgramIndex;
  if (sourceIndex < 0 || sourceIndex >= state.userProgram.length) {
    return -1;
  }
  const inst = state.userProgram[sourceIndex];
  if (!inst || inst.op !== "J") {
    return -1;
  }
  if (state.userProgram.length === 0) {
    return -1;
  }
  let offset = Number.isFinite(inst.arg) ? Math.trunc(inst.arg) : 1;
  if (offset === 0) {
    offset = 1;
  }
  return wrap(sourceIndex + offset, state.userProgram.length);
}

function updateSelectedInstructionUI() {
  ensureSelectionIsValid();
  const index = state.selectedProgramIndex;
  if (index < 0) {
    selectedInfoEl.textContent = "Selected: none";
    deleteSelectedBtn.disabled = true;
    return;
  }
  const inst = state.userProgram[index];
  if (inst.op === "J") {
    const targetIndex = getSelectedJumpTargetIndex();
    selectedInfoEl.textContent = `Selected: ${instructionDisplay(inst)} -> ${targetIndex} (Shift+Click target)`;
  } else {
    selectedInfoEl.textContent = `Selected: ${instructionDisplay(inst)}`;
  }
  deleteSelectedBtn.disabled = false;
}

function applyShiftJumpTarget(targetIndex) {
  const sourceIndex = state.selectedProgramIndex;
  if (sourceIndex < 0 || sourceIndex >= state.userProgram.length) {
    return false;
  }
  const sourceInst = state.userProgram[sourceIndex];
  if (!sourceInst || sourceInst.op !== "J") {
    return false;
  }
  if (targetIndex === sourceIndex) {
    setStatus("Shift+Click another instruction to retarget jump.", "");
    return true;
  }
  let offset = targetIndex - sourceIndex;
  offset = clamp(offset, -MAX_JUMP_ABS, MAX_JUMP_ABS);
  sourceInst.arg = offset;
  state.selectedProgramIndex = sourceIndex;
  resetRun(false);
  setStatus(`Jump retargeted: ${instructionDisplay(sourceInst)} -> slot ${targetIndex}.`, "");
  return true;
}

function renderProgramEditor() {
  programStripEl.innerHTML = "";
  ensureSelectionIsValid();
  const jumpTargetIndex = getSelectedJumpTargetIndex();
  for (let index = 0; index < state.programLimit; index += 1) {
    if (index < state.userProgram.length) {
      const inst = state.userProgram[index];
      const token = document.createElement("button");
      token.type = "button";
      token.className = "program-token";
      token.dataset.op = inst.op;
      token.title = instructionTooltip(inst);
      if (inst.op === "J") {
        const jumpOp = document.createElement("span");
        jumpOp.className = "jump-op";
        jumpOp.textContent = "J";
        const jumpArg = document.createElement("span");
        jumpArg.className = "jump-arg";
        const offset = Number.isFinite(inst.arg) ? inst.arg : 1;
        jumpArg.textContent = offset >= 0 ? `+${offset}` : String(offset);
        token.appendChild(jumpOp);
        token.appendChild(jumpArg);
      } else {
        token.textContent = instructionDisplay(inst);
      }
      if (
        index === state.pc &&
        state.userProgram.length > 0 &&
        state.status !== "escaped" &&
        state.status !== "crashed" &&
        state.status !== "timeout"
      ) {
        token.classList.add("active");
      }
      if (index === state.selectedProgramIndex) {
        token.classList.add("selected");
      }
      if (index === jumpTargetIndex) {
        token.classList.add("jump-target");
      }
      token.addEventListener("click", (event) => {
        if (event.shiftKey && applyShiftJumpTarget(index)) {
          return;
        }
        state.selectedProgramIndex = index;
        renderProgramEditor();
      });
      programStripEl.appendChild(token);
    } else {
      const slot = document.createElement("div");
      slot.className = "program-slot";
      slot.setAttribute("aria-hidden", "true");
      programStripEl.appendChild(slot);
    }
  }
  const limitTag = document.createElement("div");
  limitTag.className = "program-limit-tag";
  limitTag.textContent = "Limit";
  programStripEl.appendChild(limitTag);
  updateSelectedInstructionUI();
  updateProgramSummary();
}

function updateProgramSummary() {
  if (state.userProgram.length === 0) {
    programSummaryEl.textContent = `Length 0 / ${state.programLimit}`;
    return;
  }
  programSummaryEl.textContent = `Length ${state.userProgram.length} / ${state.programLimit}`;
}

function updateRuntimeMeta() {
  const position = isInBounds(state.robot.x, state.robot.y) ? `${state.robot.x}, ${state.robot.y}` : "outside";
  const facing = DIRS[state.robot.dir].name;
  const currentInstruction =
    state.userProgram.length > 0 ? instructionDisplay(state.userProgram[state.pc]) : "n/a";
  runtimeMetaEl.innerHTML =
    `<div><strong>Robot:</strong> ${position} facing ${facing}</div>` +
    `<div><strong>PC:</strong> ${state.pc} (<strong>Instr:</strong> ${currentInstruction})</div>` +
    `<div><strong>Step:</strong> ${state.stepCount} / ${state.maxSteps}</div>`;
  updateExecutionCountdown();
}

function updateExecutionCountdown() {
  const remaining = Math.max(0, state.maxSteps - state.stepCount);
  execCountdownEl.textContent = `Exec left: ${remaining}`;
  execCountdownEl.classList.remove("warn", "low");
  const ratio = state.maxSteps > 0 ? remaining / state.maxSteps : 0;
  if (remaining <= 15 || ratio <= 0.1) {
    execCountdownEl.classList.add("low");
  } else if (remaining <= 40 || ratio <= 0.25) {
    execCountdownEl.classList.add("warn");
  }
}

function updateLevelMeta() {
  const totalCells = state.width * state.height;
  const blocked = blockCount(state.board);
  const ratio = totalCells > 0 ? Math.round((100 * blocked) / totalCells) : 0;
  levelMetaEl.innerHTML =
    `<div><strong>Level:</strong> ${state.currentLevel}${state.levelId ? ` (id=${state.levelId})` : ""}</div>` +
    `<div><strong>Grid:</strong> ${state.width} x ${state.height}</div>` +
    `<div><strong>Blocks:</strong> ${blocked} (${ratio}%)</div>` +
    `<div><strong>Start:</strong> ${state.start.x}, ${state.start.y} (facing North)</div>` +
    `<div><strong>Program Length Limit:</strong> ${state.programLimit}</div>` +
    `<div><strong>Execution Limit:</strong> ${state.maxSteps}</div>`;
}

function updateCampaignMeta() {
  const total = state.levelCount > 0 ? state.levelCount : "?";
  currentLevelTopEl.textContent = `Level ${state.currentLevel} / ${total}`;
  campaignMetaEl.innerHTML =
    `<div><strong>Current:</strong> ${state.currentLevel}</div>` +
    `<div><strong>Unlocked:</strong> ${state.highestUnlocked}</div>` +
    `<div><strong>Total:</strong> ${total}</div>`;
  playerMetaEl.innerHTML = `<div><strong>Player ID:</strong> <code>${state.playerId}</code></div>`;

  prevLevelBtn.disabled = state.currentLevel <= 1;
  nextLevelBtn.disabled = state.currentLevel >= state.highestUnlocked || state.currentLevel >= state.levelCount;
  submitSolutionBtn.disabled = state.submitting || state.userProgram.length === 0;

  levelJumpRange.min = "1";
  levelJumpNumber.min = "1";
  const maxUnlocked = Math.max(1, state.highestUnlocked);
  levelJumpRange.max = String(maxUnlocked);
  levelJumpNumber.max = String(maxUnlocked);
  state.selectedLevel = clamp(state.selectedLevel || state.currentLevel, 1, maxUnlocked);
  levelJumpRange.value = String(state.selectedLevel);
  levelJumpNumber.value = String(state.selectedLevel);
}

function addInstruction(op) {
  if (state.userProgram.length >= state.programLimit) {
    setStatus(`Program length limit is ${state.programLimit}.`, "bad");
    return;
  }
  const selected = state.selectedProgramIndex;
  const hasValidSelection = selected >= 0 && selected < state.userProgram.length;
  const insertIndex = hasValidSelection ? selected + 1 : state.userProgram.length;
  state.userProgram.splice(insertIndex, 0, { op, arg: op === "J" ? 1 : 1 });
  state.selectedProgramIndex = insertIndex;
  resetRun(false);
  updateCampaignMeta();
}

function deleteSelectedInstruction() {
  const index = state.selectedProgramIndex;
  if (index < 0 || index >= state.userProgram.length) {
    return;
  }
  state.userProgram.splice(index, 1);
  if (state.selectedProgramIndex >= state.userProgram.length) {
    state.selectedProgramIndex = state.userProgram.length - 1;
  }
  resetRun(false);
  updateCampaignMeta();
}

function stepProgram() {
  if (state.userProgram.length === 0) {
    setStatus("Add instructions first.", "bad");
    return false;
  }
  if (state.userProgram.length > state.programLimit) {
    setStatus(`Program exceeds level limit (${state.programLimit}).`, "bad");
    return false;
  }
  if (state.status === "escaped") {
    setStatus("Already escaped. Verification submitted for this run.", "ok");
    return false;
  }
  if (state.status === "crashed") {
    setStatus("Robot crashed. Reset to run again.", "bad");
    return false;
  }
  if (state.stepCount >= state.maxSteps) {
    state.status = "timeout";
    setArenaState(null);
    updateRuntimeMeta();
    setStatus("Execution limit reached.", "bad");
    return false;
  }

  state.trail.add(keyForCell(state.robot.x, state.robot.y));
  const inst = state.userProgram[state.pc];
  state.lastPc = state.pc;
  const n = state.userProgram.length;

  if (inst.op === "F") {
    const ahead = peekAhead(state.robot);
    state.stepCount += 1;
    if (ahead.outside) {
      state.robot.x = ahead.x;
      state.robot.y = ahead.y;
      state.status = "escaped";
      state.localSolved = true;
      setArenaState("solved");
      renderBoard();
      renderProgramEditor();
      updateRuntimeMeta();
      setStatus(`Escaped in ${state.stepCount} steps. Verifying solution...`, "ok");
      updateCampaignMeta();
      void submitCurrentProgram();
      return false;
    }
    state.robot.x = ahead.x;
    state.robot.y = ahead.y;
    if (state.board[ahead.y][ahead.x]) {
      state.status = "crashed";
      setArenaState("crashed");
      renderBoard();
      renderProgramEditor();
      updateRuntimeMeta();
      setStatus(`Crashed at ${ahead.x}, ${ahead.y}.`, "bad");
      return false;
    }
    state.pc = wrap(state.pc + 1, n);
  } else if (inst.op === "L") {
    state.robot.dir = wrap(state.robot.dir - 1, 4);
    state.pc = wrap(state.pc + 1, n);
    state.stepCount += 1;
  } else if (inst.op === "R") {
    state.robot.dir = wrap(state.robot.dir + 1, 4);
    state.pc = wrap(state.pc + 1, n);
    state.stepCount += 1;
  } else if (inst.op === "S") {
    const ahead = peekAhead(state.robot);
    const blocked = !ahead.outside && state.board[ahead.y][ahead.x];
    state.pc = wrap(state.pc + (blocked ? 1 : 2), n);
    state.stepCount += 1;
  } else if (inst.op === "J") {
    let offset = parseInt(inst.arg, 10);
    if (!Number.isFinite(offset) || offset === 0) {
      offset = 1;
    }
    state.pc = wrap(state.pc + offset, n);
    state.stepCount += 1;
  } else {
    state.pc = wrap(state.pc + 1, n);
    state.stepCount += 1;
  }

  if (state.stepCount >= state.maxSteps) {
    state.status = "timeout";
    setArenaState(null);
    renderBoard();
    renderProgramEditor();
    updateRuntimeMeta();
    setStatus("Execution limit reached.", "bad");
    return false;
  }

  state.status = "running";
  renderBoard();
  renderProgramEditor();
  updateRuntimeMeta();
  setStatus("Running...");
  return true;
}

function toggleRun() {
  if (state.timer) {
    stopAutoRun();
    state.status = "paused";
    setStatus("Paused.");
    return;
  }
  if (state.userProgram.length === 0) {
    setStatus("Add instructions first.", "bad");
    return;
  }
  runBtn.textContent = "Stop";
  state.timer = setInterval(() => {
    const canContinue = stepProgram();
    if (!canContinue) {
      stopAutoRun();
    }
  }, state.runDelay);
}

function parseLevelText(rawText) {
  const trimmed = rawText.trim();
  if (!trimmed) {
    throw new Error("Level file is empty.");
  }
  const cleaned = trimmed.replace(/^[\"']|[\"']$/g, "").replace(/^[?#]/, "");
  const params = new URLSearchParams(cleaned);
  const parseIntParam = (name, fallback, required) => {
    const raw = params.get(name);
    if (raw === null || raw === "") {
      if (required) {
        throw new Error(`Missing required parameter: ${name}`);
      }
      return fallback;
    }
    const value = parseInt(raw, 10);
    if (!Number.isFinite(value)) {
      throw new Error(`Invalid integer for ${name}: ${raw}`);
    }
    return value;
  };

  const width = parseIntParam("x", 0, true);
  const height = parseIntParam("y", 0, true);
  if (width <= 0 || height <= 0) {
    throw new Error("x and y must be positive integers.");
  }

  const boardRaw = params.get("board");
  if (!boardRaw) {
    throw new Error("Missing required parameter: board");
  }
  const boardFlat = boardRaw.replace(/[,\s]/g, "");
  const expected = width * height;
  if (boardFlat.length !== expected) {
    throw new Error(`Board length ${boardFlat.length} does not match x*y (${expected}).`);
  }

  const board = [];
  for (let y = 0; y < height; y += 1) {
    const row = [];
    for (let x = 0; x < width; x += 1) {
      const ch = boardFlat[y * width + x];
      if (ch === "." || ch === "_") {
        row.push(false);
      } else if (ch === "X" || ch === "x" || ch === "#") {
        row.push(true);
      } else {
        throw new Error(`Invalid board character '${ch}' at index ${y * width + x}.`);
      }
    }
    board.push(row);
  }

  const sx = parseIntParam("sx", Math.floor(width / 2), false);
  const sy = parseIntParam("sy", Math.floor(height / 2), false);
  if (sx < 0 || sy < 0 || sx >= width || sy >= height) {
    throw new Error("Start coordinates are out of bounds.");
  }
  if (params.has("sd")) {
    throw new Error("Parameter sd is no longer supported; start direction is always North.");
  }

  if (board[sy][sx]) {
    throw new Error("Start cell cannot be blocked.");
  }

  const programLimit = clamp(parseIntParam("plim", 14, false), 4, MAX_PROGRAM_LIMIT);
  const maxSteps = Math.max(1, parseIntParam("elim", 420, false));
  const levelId = params.get("id") || params.get("level") || "";
  return {
    id: levelId,
    width,
    height,
    board,
    start: { x: sx, y: sy, dir: NORTH_DIR },
    programLimit,
    maxSteps,
  };
}

function bindPair(rangeInput, numberInput, onChange) {
  const applyValue = (raw) => {
    const min = parseInt(rangeInput.min, 10);
    const max = parseInt(rangeInput.max, 10);
    let value = parseInt(raw, 10);
    if (!Number.isFinite(value)) {
      value = parseInt(rangeInput.value, 10);
    }
    value = clamp(value, min, max);
    rangeInput.value = String(value);
    numberInput.value = String(value);
    onChange(value);
  };
  rangeInput.addEventListener("input", () => applyValue(rangeInput.value));
  numberInput.addEventListener("change", () => applyValue(numberInput.value));
}

function getOrCreatePlayerId() {
  try {
    const existing = localStorage.getItem(PLAYER_ID_KEY);
    if (existing) {
      return existing;
    }
    const generated =
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `rr-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    localStorage.setItem(PLAYER_ID_KEY, generated);
    return generated;
  } catch (error) {
    return `rr-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  }
}

function loadProgress(levelCount) {
  let highestUnlocked = 1;
  try {
    const raw = localStorage.getItem(PROGRESS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      const value = parseInt(parsed.highestUnlocked, 10);
      if (Number.isFinite(value)) {
        highestUnlocked = value;
      }
    }
  } catch (error) {
    // Ignore storage/parsing failures.
  }
  return clamp(highestUnlocked, 1, Math.max(1, levelCount));
}

function saveProgress() {
  try {
    localStorage.setItem(
      PROGRESS_KEY,
      JSON.stringify({
        highestUnlocked: state.highestUnlocked,
      })
    );
  } catch (error) {
    // Ignore storage failures.
  }
}

function loadSavedCurrentLevel(maxLevel) {
  try {
    const raw = localStorage.getItem(CURRENT_LEVEL_KEY);
    if (!raw) {
      return 1;
    }
    const value = parseInt(raw, 10);
    if (!Number.isFinite(value)) {
      return 1;
    }
    return clamp(value, 1, maxLevel);
  } catch (error) {
    return 1;
  }
}

function saveCurrentLevel(levelNumber) {
  try {
    localStorage.setItem(CURRENT_LEVEL_KEY, String(levelNumber));
  } catch (error) {
    // Ignore storage failures.
  }
}

async function fetchLevelText(levelNumber) {
  const response = await fetch(`./levels/${levelNumber}.level`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Could not load level ${levelNumber} (${response.status}).`);
  }
  return response.text();
}

function applyLoadedLevel(loaded, levelNumber) {
  stopAutoRun();
  state.currentLevel = levelNumber;
  state.levelId = loaded.id || String(levelNumber);
  state.width = loaded.width;
  state.height = loaded.height;
  state.board = loaded.board.map((row) => row.slice());
  state.start = { x: loaded.start.x, y: loaded.start.y, dir: NORTH_DIR };
  state.programLimit = loaded.programLimit;
  state.maxSteps = loaded.maxSteps;
  state.userProgram = [];
  state.selectedProgramIndex = -1;
  state.selectedLevel = levelNumber;
  buildBoardNodes();
  resetRun(false);
  updateLevelMeta();
  updateCampaignMeta();
  saveCurrentLevel(levelNumber);
}

async function loadLevelByNumber(levelNumber) {
  if (levelNumber < 1 || levelNumber > state.highestUnlocked || levelNumber > state.levelCount) {
    setStatus("That level is locked.", "bad");
    return;
  }
  setStatus(`Loading level ${levelNumber}...`);
  try {
    const text = await fetchLevelText(levelNumber);
    const loaded = parseLevelText(text);
    applyLoadedLevel(loaded, levelNumber);
    setStatus(`Loaded level ${levelNumber}.`, "ok");
  } catch (error) {
    setStatus(`Could not load level ${levelNumber}: ${error.message || error}`, "bad");
  }
}

async function discoverLevelCount(maxScan = 200) {
  let count = 0;
  for (let levelNumber = 1; levelNumber <= maxScan; levelNumber += 1) {
    const response = await fetch(`./levels/${levelNumber}.level`, { cache: "no-store" });
    if (!response.ok) {
      break;
    }
    count = levelNumber;
  }
  return count;
}

async function loadManifestLevelCount() {
  try {
    const response = await fetch("./levels/manifest.json", { cache: "no-store" });
    if (!response.ok) {
      return 0;
    }
    const manifest = await response.json();
    if (!manifest || typeof manifest !== "object") {
      return 0;
    }
    const countValue = parseInt(manifest.count, 10);
    if (!Number.isFinite(countValue) || countValue < 1) {
      return 0;
    }
    return countValue;
  } catch (error) {
    return 0;
  }
}

function unlockProgressForLevel(levelNumber) {
  if (levelNumber !== state.highestUnlocked) {
    return false;
  }
  if (state.highestUnlocked >= state.levelCount) {
    return false;
  }
  state.highestUnlocked += 1;
  saveProgress();
  return true;
}

async function submitCurrentProgram() {
  if (state.userProgram.length === 0) {
    setStatus("Add instructions before submitting.", "bad");
    return;
  }
  if (state.userProgram.length > state.programLimit) {
    setStatus(`Program exceeds level limit (${state.programLimit}).`, "bad");
    return;
  }
  if (state.submitting) {
    return;
  }

  state.submitting = true;
  updateCampaignMeta();
  setStatus("Submitting program for verification...");

  const payload = {
    player_id: state.playerId,
    level: state.currentLevel,
    program: programText(state.userProgram),
  };

  try {
    const response = await fetch("/api/submit", {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data || data.ok !== true) {
      const message = data && data.error ? data.error : `Verification failed (${response.status}).`;
      setStatus(message, "bad");
      return;
    }

    if (data.solved) {
      const unlocked = unlockProgressForLevel(state.currentLevel);
      updateCampaignMeta();
      if (unlocked) {
        setStatus(`Verified. Level ${state.currentLevel} cleared. Level ${state.highestUnlocked} unlocked.`, "ok");
      } else {
        setStatus(`Verified. Level ${state.currentLevel} already cleared.`, "ok");
      }
      return;
    }

    const outcome = data.outcome ? ` (${data.outcome})` : "";
    setStatus(`Program did not solve level ${state.currentLevel}${outcome}.`, "bad");
  } catch (error) {
    setStatus(`Could not submit solution: ${error.message || error}`, "bad");
  } finally {
    state.submitting = false;
    updateCampaignMeta();
  }
}

async function copyProgramToClipboard() {
  if (state.userProgram.length === 0) {
    setStatus("Program is empty.", "bad");
    return;
  }
  const text = programText(state.userProgram);
  try {
    await navigator.clipboard.writeText(text);
    setStatus(`Copied ${state.userProgram.length} instructions.`, "ok");
  } catch (error) {
    setStatus("Clipboard unavailable in this browser context.", "bad");
  }
}

async function pasteProgramFromClipboard() {
  let rawText = "";
  try {
    rawText = await navigator.clipboard.readText();
  } catch (error) {
    setStatus("Clipboard unavailable in this browser context.", "bad");
    return;
  }

  const parsed = parseProgramText(rawText);
  if (parsed.error) {
    setStatus(parsed.error, "bad");
    return;
  }
  if (parsed.program.length === 0) {
    setStatus("Clipboard is empty.", "bad");
    return;
  }
  if (parsed.program.length > state.programLimit) {
    setStatus(`Pasted program has ${parsed.program.length} instructions; limit is ${state.programLimit}.`, "bad");
    return;
  }

  state.userProgram = parsed.program;
  state.selectedProgramIndex = state.userProgram.length > 0 ? 0 : -1;
  resetRun(false);
  if (parsed.clampedOffsets > 0) {
    setStatus(`Pasted ${parsed.program.length} instructions (clamped ${parsed.clampedOffsets} jump offsets).`, "");
  } else {
    setStatus(`Pasted ${parsed.program.length} instructions.`, "ok");
  }
  updateCampaignMeta();
}

function configureEventHandlers() {
  bindPair(speedRange, speedNumber, (value) => {
    state.runDelay = value;
    if (state.timer) {
      stopAutoRun();
      setStatus("Updated run delay. Press Auto Run again.", "");
    }
  });

  bindPair(levelJumpRange, levelJumpNumber, (value) => {
    state.selectedLevel = value;
  });

  addFBtn.addEventListener("click", () => addInstruction("F"));
  addLBtn.addEventListener("click", () => addInstruction("L"));
  addRBtn.addEventListener("click", () => addInstruction("R"));
  addSBtn.addEventListener("click", () => addInstruction("S"));
  addJBtn.addEventListener("click", () => addInstruction("J"));
  deleteSelectedBtn.addEventListener("click", deleteSelectedInstruction);
  clearProgramBtn.addEventListener("click", () => {
    state.userProgram = [];
    state.selectedProgramIndex = -1;
    resetRun(false);
    updateCampaignMeta();
  });
  copyProgramBtn.addEventListener("click", copyProgramToClipboard);
  pasteProgramBtn.addEventListener("click", pasteProgramFromClipboard);
  stepBtn.addEventListener("click", () => {
    stopAutoRun();
    stepProgram();
  });
  runBtn.addEventListener("click", toggleRun);
  resetBtn.addEventListener("click", () => resetRun(false));
  clearTrailBtn.addEventListener("click", () => {
    state.trail.clear();
    renderBoard();
  });

  submitSolutionBtn.addEventListener("click", submitCurrentProgram);
  prevLevelBtn.addEventListener("click", () => loadLevelByNumber(state.currentLevel - 1));
  nextLevelBtn.addEventListener("click", () => loadLevelByNumber(state.currentLevel + 1));
  loadSelectedLevelBtn.addEventListener("click", () => loadLevelByNumber(state.selectedLevel));

  window.addEventListener("resize", adjustBoardSize);
  window.addEventListener("keydown", (event) => {
    if (event.target && ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(event.target.tagName)) {
      return;
    }
    const key = event.key.toLowerCase();
    if (key === "w") {
      addInstruction("F");
      event.preventDefault();
    } else if (key === "a") {
      addInstruction("L");
      event.preventDefault();
    } else if (key === "d") {
      addInstruction("R");
      event.preventDefault();
    } else if (key === "s") {
      addInstruction("S");
      event.preventDefault();
    } else if (key === "j") {
      addInstruction("J");
      event.preventDefault();
    } else if (key === "backspace" || key === "delete") {
      deleteSelectedInstruction();
      event.preventDefault();
    } else if (key === "enter") {
      stopAutoRun();
      stepProgram();
      event.preventDefault();
    } else if (key === " ") {
      toggleRun();
      event.preventDefault();
    }
  });
}

async function init() {
  configureEventHandlers();
  state.playerId = getOrCreatePlayerId();

  let levelCount = await loadManifestLevelCount();
  if (levelCount < 1) {
    levelCount = await discoverLevelCount(200);
  }
  if (levelCount < 1) {
    throw new Error("No playable levels found under ./levels/.");
  }
  state.levelCount = levelCount;
  state.highestUnlocked = loadProgress(state.levelCount);
  const savedCurrent = loadSavedCurrentLevel(state.levelCount);
  state.selectedLevel = clamp(savedCurrent, 1, state.highestUnlocked);
  updateCampaignMeta();

  await loadLevelByNumber(state.selectedLevel);
}

init().catch((error) => {
  setStatus(`Failed to initialize campaign: ${error.message || error}`, "bad");
});
