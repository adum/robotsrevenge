const MAX_PROGRAM_LIMIT = 128;
const MAX_JUMP_ABS = MAX_PROGRAM_LIMIT - 1;
const MAX_STRAIGHT_RUN = 10;
const GENERATOR_SETTINGS_KEY = "sensejump.generator.settings.v1";
const DIRS = [
  { name: "N", dx: 0, dy: -1, className: "dir-n" },
  { name: "E", dx: 1, dy: 0, className: "dir-e" },
  { name: "S", dx: 0, dy: 1, className: "dir-s" },
  { name: "W", dx: -1, dy: 0, className: "dir-w" },
];
const NORTH_DIR = 0;
const OP_CODES = ["F", "L", "R", "S", "J"];

const sizeRange = document.getElementById("sizeRange");
const sizeNumber = document.getElementById("sizeNumber");
const densityRange = document.getElementById("densityRange");
const densityNumber = document.getElementById("densityNumber");
const solLenRange = document.getElementById("solLenRange");
const solLenNumber = document.getElementById("solLenNumber");
const progLimitRange = document.getElementById("progLimitRange");
const progLimitNumber = document.getElementById("progLimitNumber");
const maxStepsRange = document.getElementById("maxStepsRange");
const maxStepsNumber = document.getElementById("maxStepsNumber");
const speedRange = document.getElementById("speedRange");
const speedNumber = document.getElementById("speedNumber");
const generateBtn = document.getElementById("generateBtn");
const showSolutionBtn = document.getElementById("showSolutionBtn");
const loadLevelBtn = document.getElementById("loadLevelBtn");
const loadLevelInput = document.getElementById("loadLevelInput");
const playModeBtn = document.getElementById("playModeBtn");
const designModeBtn = document.getElementById("designModeBtn");
const newDesignLevelBtn = document.getElementById("newDesignLevelBtn");
const bruteForceStartBtn = document.getElementById("bruteForceStartBtn");
const bruteForceStopBtn = document.getElementById("bruteForceStopBtn");
const bruteForceMetaEl = document.getElementById("bruteForceMeta");
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
  hiddenSolution: [],
  timer: null,
  runDelay: 140,
  status: "ready",
  programLimit: 14,
  maxSteps: 420,
  cells: [],
  designMode: false,
  bruteForce: {
    running: false,
    stopRequested: false,
    length: 1,
    maxLength: 1,
    tokens: [],
    digits: [],
    triedInLength: 0n,
    totalAttempts: 0n,
    totalForLength: 0n,
    startedAtMs: 0,
    lastRateAtMs: 0,
    lastRateAttempts: 0n,
    ratePerSecond: 0,
    chunkSize: 1200,
    timer: null,
  },
};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function assignPairValue(rangeInput, numberInput, rawValue) {
  const min = parseInt(rangeInput.min, 10);
  const max = parseInt(rangeInput.max, 10);
  let value = parseInt(rawValue, 10);
  if (!Number.isFinite(value)) {
    value = parseInt(rangeInput.value, 10);
  }
  value = clamp(value, min, max);
  rangeInput.value = String(value);
  numberInput.value = String(value);
}

function saveGeneratorSettings() {
  const payload = {
    size: parseInt(sizeRange.value, 10),
    density: parseInt(densityRange.value, 10),
    solverLength: parseInt(solLenRange.value, 10),
    programLimit: parseInt(progLimitRange.value, 10),
    maxSteps: parseInt(maxStepsRange.value, 10),
  };
  try {
    localStorage.setItem(GENERATOR_SETTINGS_KEY, JSON.stringify(payload));
  } catch (error) {
    // Ignore storage failures in private/restricted browsing contexts.
  }
}

function loadGeneratorSettings() {
  try {
    const raw = localStorage.getItem(GENERATOR_SETTINGS_KEY);
    if (!raw) {
      return;
    }
    const parsed = JSON.parse(raw);
    const parsedSize = parsed.size ?? parsed.width ?? parsed.height;
    assignPairValue(sizeRange, sizeNumber, parsedSize);
    assignPairValue(densityRange, densityNumber, parsed.density);
    assignPairValue(solLenRange, solLenNumber, parsed.solverLength);
    assignPairValue(progLimitRange, progLimitNumber, parsed.programLimit);
    assignPairValue(maxStepsRange, maxStepsNumber, parsed.maxSteps);
    state.programLimit = parseInt(progLimitRange.value, 10);
    state.maxSteps = parseInt(maxStepsRange.value, 10);
  } catch (error) {
    // Ignore bad local storage payloads.
  }
}

function randomInt(min, max) {
  return min + Math.floor(Math.random() * (max - min + 1));
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

function parseCellKey(key) {
  const [xText, yText] = key.split(",");
  return { x: parseInt(xText, 10), y: parseInt(yText, 10) };
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

function formatBigInt(value) {
  const text = value.toString();
  return text.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function updateDesignModeUI() {
  playModeBtn.classList.toggle("mode-active", !state.designMode);
  designModeBtn.classList.toggle("mode-active", state.designMode);
  boardEl.classList.toggle("editable", state.designMode);
}

function setDesignMode(enabled) {
  state.designMode = !!enabled;
  updateDesignModeUI();
}

function createBlankDesignedLevel() {
  stopAutoRun();
  stopBruteForceSearch(false);

  const size = parseInt(sizeRange.value, 10);
  const width = clamp(Number.isFinite(size) ? size : state.width, 2, 256);
  const height = width;

  state.width = width;
  state.height = height;
  state.board = Array.from({ length: height }, () => Array.from({ length: width }, () => false));
  state.start = {
    x: Math.floor(width / 2),
    y: Math.floor(height / 2),
    dir: NORTH_DIR,
  };
  state.hiddenSolution = [];
  state.programLimit = clamp(parseInt(progLimitRange.value, 10), 4, MAX_PROGRAM_LIMIT);
  state.maxSteps = Math.max(1, parseInt(maxStepsRange.value, 10));
  state.userProgram = [];
  state.selectedProgramIndex = -1;

  fitPairToValue(sizeRange, sizeNumber, width);
  fitPairToValue(progLimitRange, progLimitNumber, state.programLimit);
  fitPairToValue(maxStepsRange, maxStepsNumber, state.maxSteps);
  if (parseInt(solLenRange.value, 10) > state.programLimit) {
    assignPairValue(solLenRange, solLenNumber, state.programLimit);
  }

  buildBoardNodes();
  resetRun(false);
  setDesignMode(true);
  updateLevelMeta();
  saveGeneratorSettings();
  setStatus(`Design mode enabled. Created blank ${width}x${height} board.`, "ok");
}

function buildBruteForceTokens(length) {
  const tokens = [
    { op: "F", arg: 1 },
    { op: "L", arg: 1 },
    { op: "R", arg: 1 },
    { op: "S", arg: 1 },
  ];

  if (length <= 1) {
    return tokens;
  }

  for (let distance = 1; distance < length; distance += 1) {
    const offsets = [distance, -distance];
    for (const offset of offsets) {
      const effective = wrap(offset, length);
      if (effective === 0 || effective === 1) {
        continue;
      }
      tokens.push({ op: "J", arg: offset });
    }
  }
  return tokens;
}

function incrementBruteForceDigits(digits, base) {
  if (base <= 0 || digits.length === 0) {
    return false;
  }
  for (let i = digits.length - 1; i >= 0; i -= 1) {
    digits[i] += 1;
    if (digits[i] < base) {
      return true;
    }
    digits[i] = 0;
  }
  return false;
}

function currentBruteForceProgram() {
  const solver = state.bruteForce;
  const program = new Array(solver.length);
  for (let i = 0; i < solver.length; i += 1) {
    program[i] = solver.tokens[solver.digits[i]];
  }
  return program;
}

function refreshBruteForceMeta(extraLine) {
  const solver = state.bruteForce;
  if (!solver.running && !extraLine) {
    bruteForceMetaEl.textContent = "Idle.";
    return;
  }
  const elapsedMs = solver.startedAtMs > 0 ? Math.max(1, performance.now() - solver.startedAtMs) : 0;
  const elapsedSec = elapsedMs / 1000;
  const averageRate = elapsedSec > 0 ? Number(solver.totalAttempts) / elapsedSec : solver.ratePerSecond;
  const percent = solver.totalForLength > 0n
    ? Math.floor(Number((solver.triedInLength * 10000n) / solver.totalForLength)) / 100
    : 0;
  const lines = [
    `Length: ${solver.length} / ${solver.maxLength}`,
    `Tried this length: ${formatBigInt(solver.triedInLength)} / ${formatBigInt(solver.totalForLength)} (${percent.toFixed(2)}%)`,
    `Total tried: ${formatBigInt(solver.totalAttempts)} | Rate: ${solver.ratePerSecond.toFixed(0)}/s avg ${averageRate.toFixed(0)}/s`,
  ];
  if (extraLine) {
    lines.push(extraLine);
  }
  bruteForceMetaEl.textContent = lines.join("\n");
}

function updateBruteForceButtons() {
  bruteForceStartBtn.disabled = state.bruteForce.running;
  bruteForceStopBtn.disabled = !state.bruteForce.running;
}

function stopBruteForceSearch(notifyUser) {
  const solver = state.bruteForce;
  solver.stopRequested = true;
  solver.running = false;
  if (solver.timer) {
    clearTimeout(solver.timer);
    solver.timer = null;
  }
  updateBruteForceButtons();
  if (notifyUser) {
    setStatus("Brute-force search stopped.", "");
  }
  refreshBruteForceMeta(notifyUser ? "Stopped." : "");
}

function prepareBruteForceLength(length) {
  const solver = state.bruteForce;
  if (length > solver.maxLength) {
    return false;
  }
  solver.length = length;
  solver.tokens = buildBruteForceTokens(length);
  solver.digits = Array.from({ length }, () => 0);
  solver.triedInLength = 0n;
  solver.totalForLength = BigInt(solver.tokens.length) ** BigInt(length);
  return true;
}

function bruteForceTick() {
  const solver = state.bruteForce;
  if (!solver.running || solver.stopRequested) {
    return;
  }

  const started = performance.now();
  let chunkIterations = 0;
  while (solver.running && !solver.stopRequested) {
    const program = currentBruteForceProgram();
    const result = simulateProgram(program, state.board, state.start, state.maxSteps);
    solver.totalAttempts += 1n;
    solver.triedInLength += 1n;
    chunkIterations += 1;

    if (result.result === "escape") {
      solver.running = false;
      state.userProgram = cloneProgram(program);
      state.selectedProgramIndex = state.userProgram.length > 0 ? 0 : -1;
      resetRun(false);
      updateBruteForceButtons();
      refreshBruteForceMeta(
        `Found solution at length ${solver.length} in ${formatBigInt(solver.totalAttempts)} tries.`
      );
      setStatus(
        `Brute-force found solution length ${solver.length} after ${formatBigInt(solver.totalAttempts)} tries.`,
        "ok"
      );
      return;
    }

    const hasNext = incrementBruteForceDigits(solver.digits, solver.tokens.length);
    if (!hasNext) {
      if (!prepareBruteForceLength(solver.length + 1)) {
        solver.running = false;
        updateBruteForceButtons();
        refreshBruteForceMeta(`No solution found up to length ${solver.maxLength}.`);
        setStatus(`No solution found up to length ${solver.maxLength}.`, "bad");
        return;
      }
    }

    if (chunkIterations >= Math.max(1, solver.chunkSize)) {
      break;
    }
    if (performance.now() - started > 16) {
      break;
    }
  }

  const now = performance.now();
  const attemptsDelta = solver.totalAttempts - solver.lastRateAttempts;
  const elapsedDelta = now - solver.lastRateAtMs;
  if (elapsedDelta > 50) {
    solver.ratePerSecond = (1000 * Number(attemptsDelta)) / elapsedDelta;
    solver.lastRateAtMs = now;
    solver.lastRateAttempts = solver.totalAttempts;
  }
  refreshBruteForceMeta();
  solver.timer = setTimeout(bruteForceTick, 0);
}

function startBruteForceSearch() {
  if (state.bruteForce.running) {
    return;
  }
  if (!state.board || state.board.length === 0) {
    setStatus("No board loaded.", "bad");
    return;
  }
  stopAutoRun();
  stopBruteForceSearch(false);

  const solver = state.bruteForce;
  solver.running = true;
  solver.stopRequested = false;
  solver.maxLength = clamp(state.programLimit, 1, MAX_PROGRAM_LIMIT);
  solver.totalAttempts = 0n;
  solver.ratePerSecond = 0;
  solver.startedAtMs = performance.now();
  solver.lastRateAtMs = solver.startedAtMs;
  solver.lastRateAttempts = 0n;
  if (!prepareBruteForceLength(1)) {
    solver.running = false;
    updateBruteForceButtons();
    setStatus("Cannot start brute-force search with current limits.", "bad");
    refreshBruteForceMeta("Invalid search bounds.");
    return;
  }

  updateBruteForceButtons();
  refreshBruteForceMeta("Searching...");
  setStatus(`Brute-force search started (length 1..${solver.maxLength}).`, "");
  solver.timer = setTimeout(bruteForceTick, 0);
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

function programDisplayText(program) {
  return program.map((inst) => instructionDisplay(inst)).join(" ");
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
      cell.addEventListener("click", () => {
        if (!state.designMode) {
          return;
        }
        if (x === state.start.x && y === state.start.y) {
          return;
        }
        stopBruteForceSearch(false);
        state.board[y][x] = !state.board[y][x];
        resetRun(false);
        renderBoard();
        updateLevelMeta();
      });
      state.cells.push(cell);
      boardEl.appendChild(cell);
    }
  }
  adjustBoardSize();
  updateDesignModeUI();
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
  stopBruteForceSearch(false);
  setArenaState(null);
  state.robot = { x: state.start.x, y: state.start.y, dir: state.start.dir };
  state.pc = 0;
  state.lastPc = -1;
  state.stepCount = 0;
  state.status = "ready";
  if (!keepTrail) {
    state.trail.clear();
  }
  renderBoard();
  renderProgramEditor();
  updateRuntimeMeta();
  setStatus("Ready.");
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
    `<div><strong>Grid:</strong> ${state.width} x ${state.height}</div>` +
    `<div><strong>Blocks:</strong> ${blocked} (${ratio}%)</div>` +
    `<div><strong>Start:</strong> ${state.start.x}, ${state.start.y} (facing North)</div>` +
    `<div><strong>Edit Mode:</strong> ${state.designMode ? "Design" : "Play"}</div>` +
    `<div><strong>Program Length Limit:</strong> ${state.programLimit}</div>` +
    `<div><strong>Execution Limit:</strong> ${state.maxSteps}</div>` +
    `<div><strong>Hidden Solver Length:</strong> ${state.hiddenSolution.length}</div>`;
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
    setStatus("Already escaped. Reset to run again.", "ok");
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
      setArenaState("solved");
      renderBoard();
      renderProgramEditor();
      updateRuntimeMeta();
      setStatus(`Escaped in ${state.stepCount} steps.`, "ok");
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

function simulateProgram(program, board, start, maxSteps) {
  const width = board[0].length;
  const height = board.length;
  const isInside = (x, y) => x >= 0 && y >= 0 && x < width && y < height;
  let x = start.x;
  let y = start.y;
  let dir = start.dir;
  let pc = 0;
  let steps = 0;
  let jumpExecCount = 0;
  let senseExecCount = 0;

  for (let safety = 0; safety < maxSteps; safety += 1) {
    const inst = program[pc];
    if (!inst) {
      return { result: "invalid", steps, jumpExecCount, senseExecCount };
    }
    if (inst.op === "F") {
      const vec = DIRS[dir];
      const nx = x + vec.dx;
      const ny = y + vec.dy;
      steps += 1;
      if (!isInside(nx, ny)) {
        return { result: "escape", steps, jumpExecCount, senseExecCount };
      }
      if (board[ny][nx]) {
        return { result: "crash", steps, jumpExecCount, senseExecCount };
      }
      x = nx;
      y = ny;
      pc = wrap(pc + 1, program.length);
    } else if (inst.op === "L") {
      dir = wrap(dir - 1, 4);
      pc = wrap(pc + 1, program.length);
      steps += 1;
    } else if (inst.op === "R") {
      dir = wrap(dir + 1, 4);
      pc = wrap(pc + 1, program.length);
      steps += 1;
    } else if (inst.op === "S") {
      const vec = DIRS[dir];
      const nx = x + vec.dx;
      const ny = y + vec.dy;
      const blocked = isInside(nx, ny) && board[ny][nx];
      pc = wrap(pc + (blocked ? 1 : 2), program.length);
      steps += 1;
      senseExecCount += 1;
    } else if (inst.op === "J") {
      let offset = inst.arg;
      if (!Number.isFinite(offset) || offset === 0) {
        offset = 1;
      }
      pc = wrap(pc + offset, program.length);
      steps += 1;
      jumpExecCount += 1;
    } else {
      pc = wrap(pc + 1, program.length);
      steps += 1;
    }
    if (steps >= maxSteps) {
      break;
    }
  }
  return { result: "timeout", steps, jumpExecCount, senseExecCount };
}

function randomProgram(length) {
  const maxJumpDistance = Math.min(5, Math.max(1, length - 1));
  const jumpOffsets = [];
  for (let d = 1; d <= maxJumpDistance; d += 1) {
    jumpOffsets.push(-d, d);
  }

  const program = [];
  for (let i = 0; i < length; i += 1) {
    const roll = Math.random();
    if (roll < 0.34) {
      program.push({ op: "F", arg: 1 });
    } else if (roll < 0.52) {
      program.push({ op: "L", arg: 1 });
    } else if (roll < 0.7) {
      program.push({ op: "R", arg: 1 });
    } else if (roll < 0.86) {
      program.push({ op: "S", arg: 1 });
    } else {
      const offset = jumpOffsets[randomInt(0, jumpOffsets.length - 1)];
      program.push({ op: "J", arg: offset });
    }
  }

  if (!program.some((inst) => inst.op === "S")) {
    const index = randomInt(0, length - 1);
    program[index] = { op: "S", arg: 1 };
  }
  if (!program.some((inst) => inst.op === "J")) {
    const index = randomInt(0, length - 1);
    program[index] = { op: "J", arg: jumpOffsets[randomInt(0, jumpOffsets.length - 1)] };
  }
  const forwardCount = program.filter((inst) => inst.op === "F").length;
  if (forwardCount < 2) {
    const first = randomInt(0, length - 1);
    const second = randomInt(0, length - 1);
    program[first] = { op: "F", arg: 1 };
    program[second] = { op: "F", arg: 1 };
  }
  return program;
}

function buildConstraintTrace(program, start, width, height, maxSteps) {
  const requirements = new Map();
  requirements.set(keyForCell(start.x, start.y), false);

  let x = start.x;
  let y = start.y;
  let dir = start.dir;
  let pc = 0;
  let steps = 0;
  let senseExecCount = 0;
  let jumpExecCount = 0;
  let senseTrue = 0;
  let senseFalse = 0;
  const stateVisits = new Map();

  while (steps < maxSteps) {
    const visitKey = `${x},${y},${dir},${pc}`;
    const nextVisit = (stateVisits.get(visitKey) || 0) + 1;
    stateVisits.set(visitKey, nextVisit);
    if (nextVisit > 10) {
      return null;
    }

    const inst = program[pc];
    if (!inst) {
      return null;
    }

    if (inst.op === "F") {
      const vec = DIRS[dir];
      const nx = x + vec.dx;
      const ny = y + vec.dy;
      steps += 1;
      if (nx < 0 || ny < 0 || nx >= width || ny >= height) {
        return {
          requirements,
          steps,
          senseExecCount,
          jumpExecCount,
          senseTrue,
          senseFalse,
        };
      }
      const key = keyForCell(nx, ny);
      const forced = requirements.get(key);
      if (forced === true) {
        return null;
      }
      requirements.set(key, false);
      x = nx;
      y = ny;
      pc = wrap(pc + 1, program.length);
    } else if (inst.op === "L") {
      dir = wrap(dir - 1, 4);
      pc = wrap(pc + 1, program.length);
      steps += 1;
    } else if (inst.op === "R") {
      dir = wrap(dir + 1, 4);
      pc = wrap(pc + 1, program.length);
      steps += 1;
    } else if (inst.op === "S") {
      senseExecCount += 1;
      steps += 1;
      const vec = DIRS[dir];
      const nx = x + vec.dx;
      const ny = y + vec.dy;
      if (nx < 0 || ny < 0 || nx >= width || ny >= height) {
        senseFalse += 1;
        pc = wrap(pc + 2, program.length);
      } else {
        const key = keyForCell(nx, ny);
        const forced = requirements.get(key);
        let blocked = false;
        if (typeof forced === "boolean") {
          blocked = forced;
        } else {
          const distToEdge = Math.min(nx, width - 1 - nx, ny, height - 1 - ny);
          const chanceBlocked = distToEdge <= 1 ? 0.2 : 0.42;
          blocked = Math.random() < chanceBlocked;
          requirements.set(key, blocked);
        }
        if (blocked) {
          senseTrue += 1;
          pc = wrap(pc + 1, program.length);
        } else {
          senseFalse += 1;
          pc = wrap(pc + 2, program.length);
        }
      }
    } else if (inst.op === "J") {
      let offset = inst.arg;
      if (!Number.isFinite(offset) || offset === 0) {
        offset = 1;
      }
      pc = wrap(pc + offset, program.length);
      jumpExecCount += 1;
      steps += 1;
    } else {
      pc = wrap(pc + 1, program.length);
      steps += 1;
    }
  }
  return null;
}

function materializeBoard(width, height, density, requirements, start) {
  const board = [];
  for (let y = 0; y < height; y += 1) {
    const row = [];
    for (let x = 0; x < width; x += 1) {
      row.push(Math.random() < density);
    }
    board.push(row);
  }
  for (const [key, blocked] of requirements.entries()) {
    const cell = parseCellKey(key);
    if (cell.x >= 0 && cell.y >= 0 && cell.x < width && cell.y < height) {
      board[cell.y][cell.x] = blocked;
    }
  }
  board[start.y][start.x] = false;
  return board;
}

function hasStraightEscapeLaneFromStart(board, start) {
  const width = board[0].length;
  const height = board.length;
  for (let dir = 0; dir < 4; dir += 1) {
    const vec = DIRS[dir];
    let x = start.x;
    let y = start.y;
    while (true) {
      x += vec.dx;
      y += vec.dy;
      if (x < 0 || y < 0 || x >= width || y >= height) {
        return true;
      }
      if (board[y][x]) {
        break;
      }
    }
  }
  return false;
}

function hasOneTurnEscapePathFromStart(board, start) {
  const width = board[0].length;
  const height = board.length;

  const clearHorizontal = (y, x0, x1) => {
    if (x0 === x1) {
      return true;
    }
    const step = x1 > x0 ? 1 : -1;
    let x = x0 + step;
    while (true) {
      if (board[y][x]) {
        return false;
      }
      if (x === x1) {
        return true;
      }
      x += step;
    }
  };

  const clearVertical = (x, y0, y1) => {
    if (y0 === y1) {
      return true;
    }
    const step = y1 > y0 ? 1 : -1;
    let y = y0 + step;
    while (true) {
      if (board[y][x]) {
        return false;
      }
      if (y === y1) {
        return true;
      }
      y += step;
    }
  };

  const clearToEdgeVertical = (x, y, step) => {
    let ny = y + step;
    while (ny >= 0 && ny < height) {
      if (board[ny][x]) {
        return false;
      }
      ny += step;
    }
    return true;
  };

  const clearToEdgeHorizontal = (x, y, step) => {
    let nx = x + step;
    while (nx >= 0 && nx < width) {
      if (board[y][nx]) {
        return false;
      }
      nx += step;
    }
    return true;
  };

  for (let x = 0; x < width; x += 1) {
    if (x === start.x) {
      continue;
    }
    if (!clearHorizontal(start.y, start.x, x)) {
      continue;
    }
    if (clearToEdgeVertical(x, start.y, -1) || clearToEdgeVertical(x, start.y, 1)) {
      return true;
    }
  }

  for (let y = 0; y < height; y += 1) {
    if (y === start.y) {
      continue;
    }
    if (!clearVertical(start.x, start.y, y)) {
      continue;
    }
    if (clearToEdgeHorizontal(start.x, y, -1) || clearToEdgeHorizontal(start.x, y, 1)) {
      return true;
    }
  }

  return false;
}

function hasEasyTwoDirectionProgram(board, start, programLimit, maxSteps) {
  const maxSeg = Math.min(
    Math.max(board[0].length, board.length),
    Math.max(3, programLimit - 2)
  );
  const prefixOptions = [[], ["L"], ["R"], ["L", "L"]];
  const turnPairs = [
    ["L", "R"],
    ["R", "L"],
  ];
  for (const prefix of prefixOptions) {
    const segBudget = programLimit - prefix.length - 2;
    if (segBudget < 2) {
      continue;
    }
    for (const pair of turnPairs) {
      const maxSegA = Math.min(maxSeg, segBudget - 1);
      for (let segA = 1; segA <= maxSegA; segA += 1) {
        const maxSegB = Math.min(maxSeg, segBudget - segA);
        for (let segB = 1; segB <= maxSegB; segB += 1) {
          const program = [];
          for (const op of prefix) {
            program.push({ op, arg: 1 });
          }
          for (let i = 0; i < segA; i += 1) {
            program.push({ op: "F", arg: 1 });
          }
          program.push({ op: pair[0], arg: 1 });
          for (let i = 0; i < segB; i += 1) {
            program.push({ op: "F", arg: 1 });
          }
          program.push({ op: pair[1], arg: 1 });
          const result = simulateProgram(program, board, start, maxSteps);
          if (result.result === "escape") {
            return true;
          }
        }
      }
    }
  }
  return false;
}

function hasStraightRunAtLeast(board, start, program, maxSteps, runLimit) {
  if (!Array.isArray(program) || program.length === 0 || runLimit <= 0) {
    return false;
  }

  const width = board[0].length;
  const height = board.length;
  let x = start.x;
  let y = start.y;
  let dir = start.dir;
  let pc = 0;
  let steps = 0;
  let lastMoveDir = -1;
  let straightRun = 0;

  while (steps < maxSteps) {
    const inst = program[pc];
    if (!inst || typeof inst.op !== "string") {
      return false;
    }

    if (inst.op === "F") {
      if (dir === lastMoveDir) {
        straightRun += 1;
      } else {
        lastMoveDir = dir;
        straightRun = 1;
      }
      if (straightRun >= runLimit) {
        return true;
      }

      const vec = DIRS[dir];
      const nx = x + vec.dx;
      const ny = y + vec.dy;
      steps += 1;
      if (nx < 0 || ny < 0 || nx >= width || ny >= height) {
        return false;
      }
      if (board[ny][nx]) {
        return false;
      }
      x = nx;
      y = ny;
      pc = wrap(pc + 1, program.length);
      continue;
    }

    if (inst.op === "L") {
      dir = wrap(dir - 1, 4);
      pc = wrap(pc + 1, program.length);
      steps += 1;
      continue;
    }

    if (inst.op === "R") {
      dir = wrap(dir + 1, 4);
      pc = wrap(pc + 1, program.length);
      steps += 1;
      continue;
    }

    if (inst.op === "S") {
      const vec = DIRS[dir];
      const nx = x + vec.dx;
      const ny = y + vec.dy;
      const blocked = nx >= 0 && ny >= 0 && nx < width && ny < height && board[ny][nx];
      pc = wrap(pc + (blocked ? 1 : 2), program.length);
      steps += 1;
      continue;
    }

    if (inst.op === "J") {
      let offset = Number.isFinite(inst.arg) ? Math.trunc(inst.arg) : 1;
      if (offset === 0) {
        offset = 1;
      }
      pc = wrap(pc + offset, program.length);
      steps += 1;
      continue;
    }

    return false;
  }

  return false;
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
  const dir = NORTH_DIR;

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
    start: { x: sx, y: sy, dir },
    programLimit,
    maxSteps,
  };
}

function fitPairToValue(rangeInput, numberInput, value) {
  const min = parseInt(rangeInput.min, 10);
  const max = parseInt(rangeInput.max, 10);
  if (Number.isFinite(min) && value < min) {
    rangeInput.min = String(value);
    numberInput.min = String(value);
  }
  if (Number.isFinite(max) && value > max) {
    rangeInput.max = String(value);
    numberInput.max = String(value);
  }
  assignPairValue(rangeInput, numberInput, value);
}

function applyLoadedLevel(loaded) {
  stopAutoRun();
  state.width = loaded.width;
  state.height = loaded.height;
  state.board = loaded.board.map((row) => row.slice());
  state.start = { x: loaded.start.x, y: loaded.start.y, dir: NORTH_DIR };
  state.hiddenSolution = [];
  state.programLimit = loaded.programLimit;
  state.maxSteps = loaded.maxSteps;
  state.userProgram = [];
  state.selectedProgramIndex = -1;

  const blocked = blockCount(state.board);
  const densityPercent = Math.round((100 * blocked) / (state.width * state.height));

  fitPairToValue(sizeRange, sizeNumber, Math.max(state.width, state.height));
  fitPairToValue(densityRange, densityNumber, densityPercent);
  fitPairToValue(progLimitRange, progLimitNumber, state.programLimit);
  fitPairToValue(maxStepsRange, maxStepsNumber, state.maxSteps);
  if (parseInt(solLenRange.value, 10) > state.programLimit) {
    assignPairValue(solLenRange, solLenNumber, state.programLimit);
  }

  buildBoardNodes();
  resetRun(false);
  updateLevelMeta();
  saveGeneratorSettings();

  const idText = loaded.id ? ` ${loaded.id}` : "";
  setStatus(`Loaded level${idText}: ${state.width}x${state.height}.`, "ok");
}

async function loadLevelFromFileInput() {
  const file = loadLevelInput.files && loadLevelInput.files[0];
  if (!file) {
    return;
  }
  try {
    const text = await file.text();
    const loaded = parseLevelText(text);
    applyLoadedLevel(loaded);
  } catch (error) {
    setStatus(`Could not load level: ${error.message || error}`, "bad");
  } finally {
    loadLevelInput.value = "";
  }
}

function recordRejection(rejectionCounts, reason) {
  if (!reason) {
    return;
  }
  rejectionCounts[reason] = (rejectionCounts[reason] || 0) + 1;
}

function summarizeTopRejections(rejectionCounts, limit) {
  const entries = Object.entries(rejectionCounts).sort((a, b) => b[1] - a[1]).slice(0, limit);
  if (entries.length === 0) {
    return "";
  }
  return entries.map(([reason, count]) => `${reason}=${count}`).join(", ");
}

function buildGenerationPhases(options) {
  const perimeter = options.width + options.height;
  return [
    {
      name: "strict",
      attempts: 500,
      minInterestingSteps: Math.max(10, Math.floor(perimeter * 0.75)),
      requireJumpAndSense: true,
      requireSenseTrueAndFalse: true,
      rejectOneTurnEscape: true,
      rejectLongStraightRun: true,
      rejectEasyTwoDirection: true,
      minFillRatio: 0.08,
      maxFillRatio: 0.7,
    },
    {
      name: "relaxed",
      attempts: 500,
      minInterestingSteps: Math.max(8, Math.floor(perimeter * 0.6)),
      requireJumpAndSense: false,
      requireSenseTrueAndFalse: true,
      rejectOneTurnEscape: false,
      rejectLongStraightRun: true,
      rejectEasyTwoDirection: true,
      minFillRatio: 0.06,
      maxFillRatio: 0.74,
    },
    {
      name: "forgiving",
      attempts: 500,
      minInterestingSteps: Math.max(6, Math.floor(perimeter * 0.5)),
      requireJumpAndSense: false,
      requireSenseTrueAndFalse: false,
      rejectOneTurnEscape: false,
      rejectLongStraightRun: false,
      rejectEasyTwoDirection: false,
      minFillRatio: 0.04,
      maxFillRatio: 0.78,
    },
  ];
}

function tryGenerateLevel(options, phase, rejectionCounts) {
  const reject = (reason) => {
    recordRejection(rejectionCounts, reason);
    return null;
  };
  const start = {
    x: Math.floor(options.width / 2),
    y: Math.floor(options.height / 2),
    dir: NORTH_DIR,
  };
  const hiddenSolution = randomProgram(options.solutionLen);
  const trace = buildConstraintTrace(hiddenSolution, start, options.width, options.height, options.maxSteps);
  if (!trace) {
    return reject("trace_unsat");
  }

  if (trace.steps < phase.minInterestingSteps) {
    return reject("trace_too_short");
  }
  if (phase.requireJumpAndSense && (trace.jumpExecCount === 0 || trace.senseExecCount === 0)) {
    return reject("trace_no_jump_or_sense");
  }
  if (phase.requireSenseTrueAndFalse && (trace.senseTrue === 0 || trace.senseFalse === 0)) {
    return reject("trace_no_sense_split");
  }

  const board = materializeBoard(options.width, options.height, options.density, trace.requirements, start);
  if (hasStraightEscapeLaneFromStart(board, start)) {
    return reject("straight_escape_lane");
  }
  if (phase.rejectOneTurnEscape && hasOneTurnEscapePathFromStart(board, start)) {
    return reject("one_turn_escape");
  }
  const result = simulateProgram(hiddenSolution, board, start, options.maxSteps);
  if (result.result !== "escape") {
    return reject("solver_not_escape");
  }
  if (phase.requireJumpAndSense && (result.jumpExecCount === 0 || result.senseExecCount === 0)) {
    return reject("solver_no_jump_or_sense");
  }
  if (phase.requireSenseTrueAndFalse && (result.senseTrue === 0 || result.senseFalse === 0)) {
    return reject("solver_no_sense_split");
  }
  if (result.steps < phase.minInterestingSteps) {
    return reject("solver_too_short");
  }
  if (
    phase.rejectLongStraightRun &&
    hasStraightRunAtLeast(board, start, hiddenSolution, options.maxSteps, MAX_STRAIGHT_RUN)
  ) {
    return reject("long_straight_run");
  }
  const blocked = blockCount(board);
  const ratio = blocked / (options.width * options.height);
  if (ratio < phase.minFillRatio || ratio > phase.maxFillRatio) {
    return reject("density_out_of_range");
  }
  if (phase.rejectEasyTwoDirection && hasEasyTwoDirectionProgram(board, start, options.programLimit, options.maxSteps)) {
    return reject("easy_two_direction_escape");
  }

  return {
    width: options.width,
    height: options.height,
    board,
    start,
    hiddenSolution,
    programLimit: options.programLimit,
    maxSteps: options.maxSteps,
  };
}

function generateLevel() {
  stopAutoRun();
  stopBruteForceSearch(false);
  saveGeneratorSettings();
  setStatus("Generating...", "");
  const size = parseInt(sizeRange.value, 10);
  const options = {
    width: size,
    height: size,
    density: parseInt(densityRange.value, 10) / 100,
    solutionLen: parseInt(solLenRange.value, 10),
    programLimit: parseInt(progLimitRange.value, 10),
    maxSteps: parseInt(maxStepsRange.value, 10),
  };
  options.programLimit = clamp(options.programLimit, 4, MAX_PROGRAM_LIMIT);
  if (options.solutionLen > options.programLimit) {
    setStatus("Hidden solver length cannot exceed program length limit.", "bad");
    return;
  }

  let level = null;
  let selectedPhase = "";
  let attemptsTried = 0;
  const rejectionCounts = {};
  const phases = buildGenerationPhases(options);
  for (const phase of phases) {
    selectedPhase = phase.name;
    for (let attempt = 0; attempt < phase.attempts; attempt += 1) {
      attemptsTried += 1;
      level = tryGenerateLevel(options, phase, rejectionCounts);
      if (level) {
        break;
      }
    }
    if (level) {
      break;
    }
  }

  if (!level) {
    const topRejects = summarizeTopRejections(rejectionCounts, 3);
    const rejectText = topRejects ? ` Top rejects: ${topRejects}.` : "";
    setStatus(
      `Generator could not find a valid level after ${attemptsTried} attempts.${rejectText} Try lower density or shorter solver length.`,
      "bad"
    );
    return;
  }

  state.width = level.width;
  state.height = level.height;
  state.board = level.board;
  state.start = { x: level.start.x, y: level.start.y, dir: NORTH_DIR };
  state.hiddenSolution = cloneProgram(level.hiddenSolution);
  state.programLimit = level.programLimit;
  state.maxSteps = level.maxSteps;
  state.userProgram = [];
  state.selectedProgramIndex = -1;
  progLimitRange.value = String(level.programLimit);
  progLimitNumber.value = String(level.programLimit);
  maxStepsRange.value = String(level.maxSteps);
  maxStepsNumber.value = String(level.maxSteps);

  buildBoardNodes();
  resetRun(false);
  updateLevelMeta();
  setStatus(
    `Generated ${state.width}x${state.height} (${selectedPhase} mode, ${attemptsTried} tries). Build your program.`,
    "ok"
  );
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
    return;
  }
  setStatus(`Pasted ${parsed.program.length} instructions.`, "ok");
}

function loadHiddenSolution() {
  if (state.hiddenSolution.length === 0) {
    setStatus("No generated solver available yet.", "bad");
    return;
  }
  if (state.hiddenSolution.length > state.programLimit) {
    setStatus("Hidden solver does not fit current program length limit.", "bad");
    return;
  }
  state.userProgram = cloneProgram(state.hiddenSolution);
  state.selectedProgramIndex = state.userProgram.length > 0 ? 0 : -1;
  resetRun(false);
  setStatus("Loaded hidden solver into the editor.", "ok");
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

loadGeneratorSettings();

bindPair(sizeRange, sizeNumber, () => {
  saveGeneratorSettings();
});
bindPair(densityRange, densityNumber, () => {
  saveGeneratorSettings();
});
bindPair(solLenRange, solLenNumber, () => {
  const currentLimit = parseInt(progLimitRange.value, 10);
  if (parseInt(solLenRange.value, 10) > currentLimit) {
    assignPairValue(solLenRange, solLenNumber, currentLimit);
  }
  saveGeneratorSettings();
});
bindPair(progLimitRange, progLimitNumber, (value) => {
  stopBruteForceSearch(false);
  state.programLimit = clamp(value, 4, MAX_PROGRAM_LIMIT);
  if (state.userProgram.length > state.programLimit) {
    state.userProgram = state.userProgram.slice(0, state.programLimit);
    if (state.selectedProgramIndex >= state.userProgram.length) {
      state.selectedProgramIndex = state.userProgram.length - 1;
    }
    resetRun(false);
    setStatus(`Program truncated to new limit (${state.programLimit}).`, "");
  } else {
    updateProgramSummary();
    updateLevelMeta();
  }
  if (parseInt(solLenRange.value, 10) > state.programLimit) {
    assignPairValue(solLenRange, solLenNumber, state.programLimit);
  }
  saveGeneratorSettings();
});
bindPair(maxStepsRange, maxStepsNumber, (value) => {
  stopBruteForceSearch(false);
  state.maxSteps = value;
  if (state.stepCount > state.maxSteps) {
    state.stepCount = state.maxSteps;
  }
  updateRuntimeMeta();
  updateLevelMeta();
  saveGeneratorSettings();
});
bindPair(speedRange, speedNumber, (value) => {
  state.runDelay = value;
  if (state.timer) {
    stopAutoRun();
    setStatus("Updated run delay. Press Auto Run again.", "");
  }
});

generateBtn.addEventListener("click", generateLevel);
showSolutionBtn.addEventListener("click", loadHiddenSolution);
loadLevelBtn.addEventListener("click", () => loadLevelInput.click());
loadLevelInput.addEventListener("change", loadLevelFromFileInput);
playModeBtn.addEventListener("click", () => {
  setDesignMode(false);
  updateLevelMeta();
  setStatus("Play mode enabled.", "");
});
designModeBtn.addEventListener("click", () => {
  setDesignMode(true);
  updateLevelMeta();
  setStatus("Design mode enabled. Click cells to toggle blocks.", "");
});
newDesignLevelBtn.addEventListener("click", createBlankDesignedLevel);
bruteForceStartBtn.addEventListener("click", startBruteForceSearch);
bruteForceStopBtn.addEventListener("click", () => stopBruteForceSearch(true));
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
});
copyProgramBtn.addEventListener("click", copyProgramToClipboard);
pasteProgramBtn.addEventListener("click", pasteProgramFromClipboard);
stepBtn.addEventListener("click", () => {
  stopAutoRun();
  stopBruteForceSearch(false);
  stepProgram();
});
runBtn.addEventListener("click", () => {
  stopBruteForceSearch(false);
  toggleRun();
});
resetBtn.addEventListener("click", () => resetRun(false));
clearTrailBtn.addEventListener("click", () => {
  state.trail.clear();
  renderBoard();
});

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
    stopBruteForceSearch(false);
    stepProgram();
    event.preventDefault();
  } else if (key === " ") {
    stopBruteForceSearch(false);
    toggleRun();
    event.preventDefault();
  }
});

setDesignMode(false);
updateBruteForceButtons();
refreshBruteForceMeta();
generateLevel();
