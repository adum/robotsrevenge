const MAX_PROGRAM_LIMIT = 128;
const MAX_JUMP_ABS = MAX_PROGRAM_LIMIT - 1;
const NORTH_DIR = 0;
const DIRS = [
  { dx: 0, dy: -1 },
  { dx: 1, dy: 0 },
  { dx: 0, dy: 1 },
  { dx: -1, dy: 0 },
];

function withCors(headers = {}) {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "POST, OPTIONS",
    "access-control-allow-headers": "content-type",
    ...headers,
  };
}

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: withCors({
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    }),
  });
}

function wrap(value, mod) {
  if (mod <= 0) {
    return 0;
  }
  const normalized = value % mod;
  return normalized < 0 ? normalized + mod : normalized;
}

function inBounds(x, y, width, height) {
  return x >= 0 && y >= 0 && x < width && y < height;
}

function parseProgramText(rawText) {
  const normalized = rawText.replace(/[,\n\r\t;]+/g, " ").trim();
  if (!normalized) {
    return { program: [], error: "Program is empty." };
  }
  const tokens = normalized.split(/\s+/).filter(Boolean);
  const program = [];

  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    const upper = token.toUpperCase();

    if (token === "↑" || upper === "F") {
      program.push({ op: "F", arg: 1 });
      continue;
    }
    if (token === "↺" || upper === "L") {
      program.push({ op: "L", arg: 1 });
      continue;
    }
    if (token === "↻" || upper === "R") {
      program.push({ op: "R", arg: 1 });
      continue;
    }
    if (upper === "S") {
      program.push({ op: "S", arg: 1 });
      continue;
    }
    if (upper === "J" || upper.startsWith("J")) {
      let offsetToken = token.slice(1);
      if (!offsetToken && i + 1 < tokens.length && /^[-+]?\d+$/.test(tokens[i + 1])) {
        offsetToken = tokens[i + 1];
        i += 1;
      }
      let offset = 1;
      if (offsetToken) {
        if (!/^[-+]?\d+$/.test(offsetToken)) {
          return { program: [], error: `Invalid jump token: ${token}` };
        }
        offset = parseInt(offsetToken, 10);
      }
      if (!Number.isFinite(offset) || offset === 0) {
        offset = 1;
      }
      offset = Math.max(-MAX_JUMP_ABS, Math.min(MAX_JUMP_ABS, offset));
      if (offset === 0) {
        offset = 1;
      }
      program.push({ op: "J", arg: offset });
      continue;
    }

    return { program: [], error: `Invalid instruction token: ${token}` };
  }

  return { program, error: null };
}

function canonicalInstructionText(inst) {
  if (!inst || typeof inst.op !== "string") {
    return "?";
  }
  if (inst.op !== "J") {
    return inst.op;
  }
  const offset = Number.isFinite(inst.arg) ? Math.trunc(inst.arg) : 1;
  return `J${offset >= 0 ? "+" : ""}${offset}`;
}

function canonicalProgramText(program) {
  return program.map((inst) => canonicalInstructionText(inst)).join(" ");
}

async function sha256Hex(text) {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
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
  if (!inBounds(sx, sy, width, height)) {
    throw new Error("Start coordinates are out of bounds.");
  }
  if (board[sy][sx]) {
    throw new Error("Start cell cannot be blocked.");
  }

  const programLimit = Math.max(1, Math.min(MAX_PROGRAM_LIMIT, parseIntParam("plim", 14, false)));
  const executionLimit = Math.max(1, parseIntParam("elim", 420, false));
  const levelId = params.get("id") || params.get("level") || "";
  return {
    id: levelId,
    width,
    height,
    board,
    start: { x: sx, y: sy, dir: NORTH_DIR },
    programLimit,
    executionLimit,
  };
}

function simulateProgram(program, level) {
  const width = level.width;
  const height = level.height;
  let x = level.start.x;
  let y = level.start.y;
  let dir = level.start.dir;
  let pc = 0;
  let steps = 0;

  while (steps < level.executionLimit) {
    const inst = program[pc];
    if (!inst || typeof inst.op !== "string") {
      return { outcome: "invalid", steps };
    }
    if (inst.op === "F") {
      const vec = DIRS[dir];
      const nx = x + vec.dx;
      const ny = y + vec.dy;
      steps += 1;
      if (!inBounds(nx, ny, width, height)) {
        return { outcome: "escape", steps };
      }
      if (level.board[ny][nx]) {
        return { outcome: "crash", steps };
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
      const blocked = inBounds(nx, ny, width, height) && level.board[ny][nx];
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
    return { outcome: "invalid", steps };
  }

  return { outcome: "timeout", steps };
}

function normalizePlayerId(raw) {
  if (typeof raw !== "string") {
    return "";
  }
  const cleaned = raw.trim();
  if (cleaned.length < 6 || cleaned.length > 128) {
    return "";
  }
  return cleaned;
}

async function loadLevelText(context, levelNumber) {
  const levelUrl = new URL(`/levels/${levelNumber}.level`, context.request.url);
  const request = new Request(levelUrl.toString(), { method: "GET" });
  const response =
    context.env && context.env.ASSETS && typeof context.env.ASSETS.fetch === "function"
      ? await context.env.ASSETS.fetch(request)
      : await fetch(request);
  if (!response.ok) {
    throw new Error(`Level ${levelNumber} not found.`);
  }
  return response.text();
}

function getDb(context) {
  const db = context && context.env ? context.env.DB : null;
  if (!db || typeof db.prepare !== "function") {
    throw new Error("D1 binding DB is not configured.");
  }
  return db;
}

async function persistSubmission(context, data) {
  const db = getDb(context);
  await db
    .prepare(
      `INSERT INTO submission_results (
        player_id,
        level_number,
        program,
        result,
        submitted_at,
        solution_hash
      ) VALUES (?, ?, ?, ?, ?, ?)`
    )
    .bind(
      data.playerId,
      data.levelNumber,
      data.program,
      data.result,
      data.submittedAt,
      data.solutionHash
    )
    .run();
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: withCors() });
}

export async function onRequestPost(context) {
  try {
    const body = await context.request.json();
    const playerId = normalizePlayerId(body.player_id ?? body.playerId ?? "");
    const levelNumber = parseInt(body.level, 10);
    const programRaw = typeof body.program === "string" ? body.program : "";

    if (!playerId) {
      return json({ ok: false, error: "Invalid player_id." }, 400);
    }
    if (!Number.isFinite(levelNumber) || levelNumber < 1 || levelNumber > 5000) {
      return json({ ok: false, error: "Invalid level number." }, 400);
    }
    if (!programRaw || programRaw.length > 8000) {
      return json({ ok: false, error: "Invalid program payload." }, 400);
    }

    const { program, error: programError } = parseProgramText(programRaw);
    if (programError) {
      return json({ ok: false, error: programError }, 400);
    }
    if (program.length < 1) {
      return json({ ok: false, error: "Program is empty." }, 400);
    }

    const levelText = await loadLevelText(context, levelNumber);
    const level = parseLevelText(levelText);
    if (program.length > level.programLimit) {
      return json(
        {
          ok: false,
          error: `Program length ${program.length} exceeds level limit ${level.programLimit}.`,
        },
        400
      );
    }

    const result = simulateProgram(program, level);
    const canonicalProgram = canonicalProgramText(program);
    const solutionHash = await sha256Hex(canonicalProgram);
    const submittedAt = new Date().toISOString();

    await persistSubmission(context, {
      playerId,
      levelNumber,
      program: canonicalProgram,
      result: result.outcome,
      submittedAt,
      solutionHash,
    });

    return json({
      ok: true,
      player_id: playerId,
      level: levelNumber,
      solved: result.outcome === "escape",
      accepted: result.outcome === "escape",
      outcome: result.outcome,
      steps: result.steps,
      program_length: program.length,
      submitted_at: submittedAt,
      solution_hash: solutionHash,
    });
  } catch (error) {
    return json(
      {
        ok: false,
        error: `Unable to verify submission: ${error && error.message ? error.message : String(error)}`,
      },
      500
    );
  }
}
