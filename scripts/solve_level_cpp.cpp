// C++ brute-force solver for Robot's Revenge / SenseJump levels.
// Focuses on depth-bounded search with pruning of obviously useless programs.

#include <algorithm>
#include <chrono>
#include <cctype>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

struct Instruction {
  char op = 'F';
  int arg = 1;
};

struct Level {
  int width = 0;
  int height = 0;
  std::vector<uint8_t> board;  // 1 = blocked, 0 = open
  int start_x = 0;
  int start_y = 0;
  int program_limit = 14;
  int execution_limit = 420;
  std::string level_id;
};

enum class Outcome { Escape, Crash, Timeout, Invalid };

struct RunResult {
  Outcome outcome = Outcome::Invalid;
  int steps = 0;
  int x = 0;
  int y = 0;
  int dir = 0;
  int pc = 0;
  int jump_exec_count = 0;
  int sense_exec_count = 0;
};

struct SearchStats {
  uint64_t tested_templates = 0;
  uint64_t simulated_programs = 0;
  uint64_t pruned_turn_cancel_templates = 0;
  uint64_t pruned_meaningless_jump = 0;
  uint64_t pruned_unreachable = 0;
  bool stopped_by_timeout = false;
  bool stopped_by_budget = false;
  double elapsed_seconds = 0.0;
};

struct SolverConfig {
  int min_depth = 1;
  int max_depth = 1;
  std::string ops = "FLRSJ";
  int max_jump_distance = 3;
  bool full_jump_range = true;
  bool require_sense = false;
  bool require_jump = false;
  double timeout_seconds = 0.0;
  uint64_t max_programs = 0;  // 0 = unlimited
  bool verbose = false;
};

struct SolveResult {
  bool found = false;
  std::vector<Instruction> program;
  SearchStats stats;
};

struct ParsedArgs {
  SolverConfig cfg;
  std::string level_input;
  bool read_stdin = false;
  bool show_help = false;
};

int wrap(int value, int mod) {
  if (mod <= 0) {
    return 0;
  }
  int r = value % mod;
  return (r < 0) ? (r + mod) : r;
}

bool in_bounds(int x, int y, int w, int h) {
  return x >= 0 && x < w && y >= 0 && y < h;
}

int64_t parse_int64(const std::string& text, bool* ok) {
  *ok = false;
  if (text.empty()) {
    return 0;
  }
  size_t idx = 0;
  try {
    long long value = std::stoll(text, &idx, 10);
    if (idx != text.size()) {
      return 0;
    }
    *ok = true;
    return value;
  } catch (...) {
    return 0;
  }
}

std::string trim(const std::string& s) {
  size_t a = 0;
  while (a < s.size() && std::isspace(static_cast<unsigned char>(s[a]))) {
    ++a;
  }
  size_t b = s.size();
  while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) {
    --b;
  }
  return s.substr(a, b - a);
}

std::string url_decode(const std::string& s) {
  std::string out;
  out.reserve(s.size());
  for (size_t i = 0; i < s.size(); ++i) {
    char c = s[i];
    if (c == '+') {
      out.push_back(' ');
      continue;
    }
    if (c == '%' && i + 2 < s.size()) {
      auto hex = [](char ch) -> int {
        if (ch >= '0' && ch <= '9') return ch - '0';
        if (ch >= 'a' && ch <= 'f') return 10 + (ch - 'a');
        if (ch >= 'A' && ch <= 'F') return 10 + (ch - 'A');
        return -1;
      };
      int hi = hex(s[i + 1]);
      int lo = hex(s[i + 2]);
      if (hi >= 0 && lo >= 0) {
        out.push_back(static_cast<char>((hi << 4) | lo));
        i += 2;
        continue;
      }
    }
    out.push_back(c);
  }
  return out;
}

std::unordered_map<std::string, std::string> parse_query_params(const std::string& raw) {
  std::unordered_map<std::string, std::string> params;
  std::string s = trim(raw);
  if (!s.empty() && (s.front() == '"' || s.front() == '\'')) {
    if (s.back() == s.front() && s.size() >= 2) {
      s = s.substr(1, s.size() - 2);
    }
  }
  while (!s.empty() && (s.front() == '?' || s.front() == '#')) {
    s.erase(s.begin());
  }

  size_t start = 0;
  while (start <= s.size()) {
    size_t end = s.find('&', start);
    if (end == std::string::npos) {
      end = s.size();
    }
    std::string pair = s.substr(start, end - start);
    if (!pair.empty()) {
      size_t eq = pair.find('=');
      std::string key = (eq == std::string::npos) ? pair : pair.substr(0, eq);
      std::string val = (eq == std::string::npos) ? "" : pair.substr(eq + 1);
      key = url_decode(key);
      val = url_decode(val);
      params[key] = val;
    }
    if (end == s.size()) {
      break;
    }
    start = end + 1;
  }
  return params;
}

bool parse_level(const std::string& raw, Level* level, std::string* error) {
  auto params = parse_query_params(raw);
  if (params.empty()) {
    *error = "Level string is empty or malformed.";
    return false;
  }

  auto get_int = [&](const std::string& key, int* out, bool required, int default_value) -> bool {
    auto it = params.find(key);
    if (it == params.end() || it->second.empty()) {
      if (required) {
        *error = "Missing required parameter: " + key;
        return false;
      }
      *out = default_value;
      return true;
    }
    bool ok = false;
    int64_t value = parse_int64(it->second, &ok);
    if (!ok || value < std::numeric_limits<int>::min() || value > std::numeric_limits<int>::max()) {
      *error = "Invalid integer for " + key + ": " + it->second;
      return false;
    }
    *out = static_cast<int>(value);
    return true;
  };

  int w = 0;
  int h = 0;
  if (!get_int("x", &w, true, 0) || !get_int("y", &h, true, 0)) {
    return false;
  }
  if (w <= 0 || h <= 0) {
    *error = "x and y must be positive integers.";
    return false;
  }

  auto board_it = params.find("board");
  if (board_it == params.end() || board_it->second.empty()) {
    *error = "Missing required parameter: board";
    return false;
  }
  std::string board_flat;
  board_flat.reserve(board_it->second.size());
  for (char c : board_it->second) {
    if (c == ',' || std::isspace(static_cast<unsigned char>(c))) {
      continue;
    }
    board_flat.push_back(c);
  }
  const size_t expected = static_cast<size_t>(w) * static_cast<size_t>(h);
  if (board_flat.size() != expected) {
    *error = "Board length " + std::to_string(board_flat.size()) + " does not match x*y (" +
             std::to_string(expected) + ").";
    return false;
  }

  int sx = w / 2;
  int sy = h / 2;
  int plim = 14;
  int elim = 420;
  if (!get_int("sx", &sx, false, sx) ||
      !get_int("sy", &sy, false, sy) ||
      !get_int("plim", &plim, false, plim) ||
      !get_int("elim", &elim, false, elim)) {
    return false;
  }
  if (!in_bounds(sx, sy, w, h)) {
    *error = "Start position out of bounds.";
    return false;
  }
  if (plim <= 0) {
    *error = "plim must be > 0.";
    return false;
  }
  if (elim <= 0) {
    *error = "elim must be > 0.";
    return false;
  }

  level->width = w;
  level->height = h;
  level->start_x = sx;
  level->start_y = sy;
  level->program_limit = plim;
  level->execution_limit = elim;
  level->level_id = "";
  auto id_it = params.find("id");
  if (id_it != params.end()) {
    level->level_id = id_it->second;
  } else {
    auto lv_it = params.find("level");
    if (lv_it != params.end()) {
      level->level_id = lv_it->second;
    }
  }
  level->board.assign(expected, 0);
  for (size_t i = 0; i < expected; ++i) {
    char c = board_flat[i];
    if (c == 'X' || c == 'x' || c == '1') {
      level->board[i] = 1;
    } else if (c == '.' || c == '0') {
      level->board[i] = 0;
    } else {
      *error = "Invalid board character at index " + std::to_string(i) + ": " + std::string(1, c);
      return false;
    }
  }
  if (level->board[static_cast<size_t>(sy) * static_cast<size_t>(w) + static_cast<size_t>(sx)] != 0) {
    *error = "Start cell is blocked.";
    return false;
  }
  return true;
}

bool read_file_text(const std::string& path, std::string* out) {
  std::ifstream in(path, std::ios::binary);
  if (!in) {
    return false;
  }
  std::string data((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
  *out = data;
  return true;
}

RunResult simulate_program(const Level& level, const std::vector<Instruction>& program) {
  if (program.empty()) {
    return RunResult{
        Outcome::Invalid, 0, level.start_x, level.start_y, 0, 0, 0, 0,
    };
  }

  static constexpr int DX[4] = {0, 1, 0, -1};
  static constexpr int DY[4] = {-1, 0, 1, 0};

  int x = level.start_x;
  int y = level.start_y;
  int dir = 0;  // North
  int pc = 0;
  int steps = 0;
  int jump_exec = 0;
  int sense_exec = 0;
  const int n = static_cast<int>(program.size());
  int limit = level.execution_limit;
  if (limit <= 0) {
    limit = 1;
  }

  std::unordered_set<uint64_t> seen;
  seen.reserve(static_cast<size_t>(limit) * 2);
  while (steps < limit) {
    uint64_t state_key = (static_cast<uint64_t>(static_cast<uint32_t>(x)) << 32U) ^
                         (static_cast<uint64_t>(static_cast<uint32_t>(y)) << 16U) ^
                         (static_cast<uint64_t>(static_cast<uint32_t>(dir)) << 8U) ^
                         static_cast<uint64_t>(static_cast<uint32_t>(pc));
    if (!seen.insert(state_key).second) {
      return RunResult{Outcome::Timeout, steps, x, y, dir, pc, jump_exec, sense_exec};
    }

    const Instruction& inst = program[pc];
    switch (inst.op) {
      case 'F': {
        const int nx = x + DX[dir];
        const int ny = y + DY[dir];
        ++steps;
        if (!in_bounds(nx, ny, level.width, level.height)) {
          return RunResult{Outcome::Escape, steps, nx, ny, dir, wrap(pc + 1, n), jump_exec, sense_exec};
        }
        const size_t idx = static_cast<size_t>(ny) * static_cast<size_t>(level.width) + static_cast<size_t>(nx);
        if (level.board[idx] != 0) {
          return RunResult{Outcome::Crash, steps, nx, ny, dir, pc, jump_exec, sense_exec};
        }
        x = nx;
        y = ny;
        pc = wrap(pc + 1, n);
        break;
      }
      case 'L':
        dir = wrap(dir - 1, 4);
        pc = wrap(pc + 1, n);
        ++steps;
        break;
      case 'R':
        dir = wrap(dir + 1, 4);
        pc = wrap(pc + 1, n);
        ++steps;
        break;
      case 'S': {
        const int nx = x + DX[dir];
        const int ny = y + DY[dir];
        bool blocked = false;
        if (in_bounds(nx, ny, level.width, level.height)) {
          const size_t idx = static_cast<size_t>(ny) * static_cast<size_t>(level.width) + static_cast<size_t>(nx);
          blocked = level.board[idx] != 0;
        }
        pc = wrap(pc + (blocked ? 1 : 2), n);
        ++steps;
        ++sense_exec;
        break;
      }
      case 'J': {
        int offset = inst.arg;
        if (offset == 0) {
          offset = 1;
        }
        pc = wrap(pc + offset, n);
        ++steps;
        ++jump_exec;
        break;
      }
      default:
        return RunResult{Outcome::Invalid, steps, x, y, dir, pc, jump_exec, sense_exec};
    }
  }
  return RunResult{Outcome::Timeout, steps, x, y, dir, pc, jump_exec, sense_exec};
}

bool has_meaningless_jump_instruction(const std::vector<Instruction>& program) {
  const int n = static_cast<int>(program.size());
  if (n <= 0) {
    return false;
  }
  for (const auto& inst : program) {
    if (inst.op != 'J') {
      continue;
    }
    int offset = inst.arg;
    if (offset == 0) {
      offset = 1;
    }
    const int effective = wrap(offset, n);
    if (effective == 0 || effective == 1) {
      return true;
    }
  }
  return false;
}

bool has_unreachable_instruction(const std::vector<Instruction>& program) {
  const int n = static_cast<int>(program.size());
  if (n <= 0) {
    return false;
  }
  std::vector<uint8_t> reachable(static_cast<size_t>(n), 0);
  std::vector<int> queue;
  queue.reserve(static_cast<size_t>(n));
  queue.push_back(0);
  reachable[0] = 1;

  for (size_t qi = 0; qi < queue.size(); ++qi) {
    const int pc = queue[qi];
    const Instruction& inst = program[pc];
    auto push = [&](int np) {
      np = wrap(np, n);
      if (!reachable[static_cast<size_t>(np)]) {
        reachable[static_cast<size_t>(np)] = 1;
        queue.push_back(np);
      }
    };

    switch (inst.op) {
      case 'F':
      case 'L':
      case 'R':
        push(pc + 1);
        break;
      case 'S':
        push(pc + 1);
        push(pc + 2);
        break;
      case 'J': {
        int offset = inst.arg;
        if (offset == 0) {
          offset = 1;
        }
        push(pc + offset);
        break;
      }
      default:
        break;
    }
  }

  for (uint8_t v : reachable) {
    if (!v) {
      return true;
    }
  }
  return false;
}

bool is_turn_cancel_pair(char a, char b) {
  return (a == 'L' && b == 'R') || (a == 'R' && b == 'L');
}

std::string format_program(const std::vector<Instruction>& program) {
  std::string out;
  for (size_t i = 0; i < program.size(); ++i) {
    const Instruction& inst = program[i];
    if (i > 0) out.push_back(' ');
    if (inst.op == 'J') {
      int offset = inst.arg;
      if (offset == 0) offset = 1;
      out.push_back('J');
      if (offset >= 0) out.push_back('+');
      out += std::to_string(offset);
    } else {
      out.push_back(inst.op);
    }
  }
  return out;
}

bool is_valid_ops(const std::string& ops, std::string* error) {
  if (ops.empty()) {
    *error = "Operation set cannot be empty.";
    return false;
  }
  for (char c : ops) {
    if (c != 'F' && c != 'L' && c != 'R' && c != 'S' && c != 'J') {
      *error = std::string("Invalid op in --ops: ") + c + ". Allowed: FLRSJ";
      return false;
    }
  }
  return true;
}

std::vector<int> jump_offsets_for_length(int length, int max_jump_distance, bool full_jump_range) {
  if (length <= 1) {
    return {1};
  }
  int bound = full_jump_range ? (length - 1) : std::min(length - 1, std::max(1, max_jump_distance));
  std::vector<int> offsets;
  offsets.reserve(static_cast<size_t>(bound) * 2);
  for (int d = 1; d <= bound; ++d) {
    offsets.push_back(-d);
    offsets.push_back(d);
  }
  return offsets;
}

class BruteForceSolver {
 public:
  BruteForceSolver(const Level& level, const SolverConfig& cfg)
      : level_(level), cfg_(cfg), start_(std::chrono::steady_clock::now()) {}

  SolveResult solve() {
    std::string ops = cfg_.ops;
    for (char& c : ops) {
      c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    }

    for (int depth = cfg_.min_depth; depth <= cfg_.max_depth; ++depth) {
      if (limits_hit()) {
        break;
      }

      current_depth_ = depth;
      template_.assign(static_cast<size_t>(depth), 'F');
      jump_positions_.clear();
      jump_offsets_ = jump_offsets_for_length(depth, cfg_.max_jump_distance, cfg_.full_jump_range);

      std::vector<int> filtered;
      filtered.reserve(jump_offsets_.size());
      for (int off : jump_offsets_) {
        int e = wrap(off == 0 ? 1 : off, depth);
        if (e == 0 || e == 1) {
          continue;
        }
        filtered.push_back(off);
      }
      jump_offsets_.swap(filtered);

      dfs_template(ops, 0, false, false, false, '\0');
      if (found_) {
        break;
      }
    }

    stats_.elapsed_seconds = elapsed_seconds();
    return SolveResult{found_, solution_, stats_};
  }

 private:
  const Level& level_;
  const SolverConfig& cfg_;
  SearchStats stats_{};
  std::chrono::steady_clock::time_point start_;

  int current_depth_ = 0;
  std::vector<char> template_;
  std::vector<int> jump_positions_;
  std::vector<int> jump_offsets_;
  std::vector<Instruction> candidate_;

  bool found_ = false;
  std::vector<Instruction> solution_;
  double last_progress_report_ = 0.0;

  double elapsed_seconds() const {
    using namespace std::chrono;
    return duration<double>(steady_clock::now() - start_).count();
  }

  bool limits_hit() {
    const double elapsed = elapsed_seconds();
    if (cfg_.timeout_seconds > 0.0 && elapsed >= cfg_.timeout_seconds) {
      stats_.stopped_by_timeout = true;
      return true;
    }
    if (cfg_.max_programs > 0 && stats_.simulated_programs >= cfg_.max_programs) {
      stats_.stopped_by_budget = true;
      return true;
    }
    return false;
  }

  void maybe_progress_report() {
    if (!cfg_.verbose) {
      return;
    }
    const double elapsed = elapsed_seconds();
    if (elapsed - last_progress_report_ < 0.2) {
      return;
    }
    last_progress_report_ = elapsed;
    double rate = (elapsed > 0.0) ? (static_cast<double>(stats_.simulated_programs) / elapsed) : 0.0;
    std::cerr << "[search] depth=" << current_depth_ << "/" << cfg_.max_depth
              << " templates=" << stats_.tested_templates
              << " simulated=" << stats_.simulated_programs
              << " rate=" << static_cast<uint64_t>(rate) << "/s"
              << " pruned(unreach/jump/turn)="
              << stats_.pruned_unreachable << "/"
              << stats_.pruned_meaningless_jump << "/"
              << stats_.pruned_turn_cancel_templates
              << " elapsed=" << elapsed << "s\n";
  }

  void dfs_template(const std::string& ops, int index, bool has_f, bool has_s, bool has_j, char prev) {
    if (found_ || limits_hit()) {
      return;
    }
    if (index == current_depth_) {
      ++stats_.tested_templates;
      if (!has_f) {
        return;
      }
      if (cfg_.require_sense && !has_s) {
        return;
      }
      if (cfg_.require_jump && !has_j) {
        return;
      }
      jump_positions_.clear();
      for (int i = 0; i < current_depth_; ++i) {
        if (template_[static_cast<size_t>(i)] == 'J') {
          jump_positions_.push_back(i);
        }
      }
      if (!jump_positions_.empty() && jump_offsets_.empty()) {
        return;
      }

      candidate_.assign(static_cast<size_t>(current_depth_), Instruction{});
      for (int i = 0; i < current_depth_; ++i) {
        candidate_[static_cast<size_t>(i)] = Instruction{template_[static_cast<size_t>(i)], 1};
      }

      if (jump_positions_.empty()) {
        evaluate_candidate();
      } else {
        dfs_jump_args(0);
      }
      maybe_progress_report();
      return;
    }

    const int remaining = current_depth_ - index;
    if (!has_f && remaining <= 0) {
      return;
    }

    for (char op : ops) {
      if (prev != '\0' && is_turn_cancel_pair(prev, op)) {
        ++stats_.pruned_turn_cancel_templates;
        continue;
      }
      template_[static_cast<size_t>(index)] = op;
      dfs_template(
          ops,
          index + 1,
          has_f || op == 'F',
          has_s || op == 'S',
          has_j || op == 'J',
          op);
      if (found_ || limits_hit()) {
        return;
      }
    }
  }

  void dfs_jump_args(int jump_index) {
    if (found_ || limits_hit()) {
      return;
    }
    if (jump_index == static_cast<int>(jump_positions_.size())) {
      evaluate_candidate();
      return;
    }
    const int pos = jump_positions_[static_cast<size_t>(jump_index)];
    for (int offset : jump_offsets_) {
      candidate_[static_cast<size_t>(pos)].arg = offset;
      dfs_jump_args(jump_index + 1);
      if (found_ || limits_hit()) {
        return;
      }
    }
  }

  void evaluate_candidate() {
    if (found_ || limits_hit()) {
      return;
    }
    if (has_meaningless_jump_instruction(candidate_)) {
      ++stats_.pruned_meaningless_jump;
      return;
    }
    if (has_unreachable_instruction(candidate_)) {
      ++stats_.pruned_unreachable;
      return;
    }

    ++stats_.simulated_programs;
    RunResult result = simulate_program(level_, candidate_);
    if (result.outcome == Outcome::Escape) {
      found_ = true;
      solution_ = candidate_;
    }
  }
};

void print_help() {
  std::cout
      << "Usage: solve_level_cpp [LEVEL_OR_PATH] --max-depth N [options]\n"
      << "Brute-force C++ solver with pruning for useless programs.\n\n"
      << "Options:\n"
      << "  --min-depth N           Minimum program length (default: 1)\n"
      << "  --max-depth N           Maximum program length to search up to (required)\n"
      << "  --ops STR               Instruction alphabet subset of FLRSJ (default: FLRSJ)\n"
      << "  --max-jump-distance N   Jump bound when --no-full-jump-range (default: 3)\n"
      << "  --full-jump-range       Use full jump range 1..length-1 (default)\n"
      << "  --no-full-jump-range    Respect --max-jump-distance\n"
      << "  --timeout SEC           Stop search after SEC seconds (default: 0 = no timeout)\n"
      << "  --max-programs N        Stop after simulating N programs (default: 0 = unlimited)\n"
      << "  --require-sense         Only test programs containing S\n"
      << "  --require-jump          Only test programs containing J\n"
      << "  --verbose               Print progress and summary to stderr\n"
      << "  -h, --help              Show this help\n";
}

bool parse_args(int argc, char** argv, ParsedArgs* out, std::string* error) {
  ParsedArgs parsed;
  bool have_level_positional = false;
  bool have_max_depth = false;

  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if (arg == "-h" || arg == "--help") {
      parsed.show_help = true;
      *out = parsed;
      return true;
    }

    auto need_value = [&](const std::string& name) -> std::string {
      if (i + 1 >= argc) {
        *error = "Missing value for " + name;
        return "";
      }
      ++i;
      return std::string(argv[i]);
    };

    if (arg == "--min-depth") {
      bool ok = false;
      int64_t value = parse_int64(need_value(arg), &ok);
      if (!ok) {
        *error = "Invalid integer for --min-depth";
        return false;
      }
      parsed.cfg.min_depth = static_cast<int>(value);
      continue;
    }
    if (arg == "--max-depth") {
      bool ok = false;
      int64_t value = parse_int64(need_value(arg), &ok);
      if (!ok) {
        *error = "Invalid integer for --max-depth";
        return false;
      }
      parsed.cfg.max_depth = static_cast<int>(value);
      have_max_depth = true;
      continue;
    }
    if (arg == "--ops") {
      parsed.cfg.ops = need_value(arg);
      continue;
    }
    if (arg == "--max-jump-distance") {
      bool ok = false;
      int64_t value = parse_int64(need_value(arg), &ok);
      if (!ok) {
        *error = "Invalid integer for --max-jump-distance";
        return false;
      }
      parsed.cfg.max_jump_distance = static_cast<int>(value);
      continue;
    }
    if (arg == "--full-jump-range") {
      parsed.cfg.full_jump_range = true;
      continue;
    }
    if (arg == "--no-full-jump-range") {
      parsed.cfg.full_jump_range = false;
      continue;
    }
    if (arg == "--timeout") {
      bool ok = false;
      std::string v = need_value(arg);
      try {
        parsed.cfg.timeout_seconds = std::stod(v);
        ok = true;
      } catch (...) {
        ok = false;
      }
      if (!ok) {
        *error = "Invalid number for --timeout";
        return false;
      }
      continue;
    }
    if (arg == "--max-programs") {
      bool ok = false;
      int64_t value = parse_int64(need_value(arg), &ok);
      if (!ok || value < 0) {
        *error = "Invalid integer for --max-programs";
        return false;
      }
      parsed.cfg.max_programs = static_cast<uint64_t>(value);
      continue;
    }
    if (arg == "--require-sense") {
      parsed.cfg.require_sense = true;
      continue;
    }
    if (arg == "--require-jump") {
      parsed.cfg.require_jump = true;
      continue;
    }
    if (arg == "--verbose") {
      parsed.cfg.verbose = true;
      continue;
    }
    if (!arg.empty() && arg[0] == '-') {
      *error = "Unknown option: " + arg;
      return false;
    }
    if (have_level_positional) {
      *error = "Only one level positional argument is allowed.";
      return false;
    }
    have_level_positional = true;
    parsed.level_input = arg;
  }

  if (!have_max_depth) {
    *error = "--max-depth is required.";
    return false;
  }
  if (!have_level_positional) {
    parsed.read_stdin = true;
  }
  *out = parsed;
  return true;
}

}  // namespace

int main(int argc, char** argv) {
  ParsedArgs args;
  std::string arg_error;
  if (!parse_args(argc, argv, &args, &arg_error)) {
    std::cerr << "Error: " << arg_error << "\n";
    print_help();
    return 2;
  }
  if (args.show_help) {
    print_help();
    return 0;
  }

  if (args.cfg.min_depth < 1) {
    std::cerr << "Error: --min-depth must be >= 1\n";
    return 2;
  }
  if (args.cfg.max_depth < 1) {
    std::cerr << "Error: --max-depth must be >= 1\n";
    return 2;
  }
  if (args.cfg.max_depth < args.cfg.min_depth) {
    std::cerr << "Error: --max-depth must be >= --min-depth\n";
    return 2;
  }
  if (args.cfg.max_jump_distance < 1) {
    std::cerr << "Error: --max-jump-distance must be >= 1\n";
    return 2;
  }
  if (args.cfg.timeout_seconds < 0.0) {
    std::cerr << "Error: --timeout must be >= 0\n";
    return 2;
  }

  std::string ops_error;
  if (!is_valid_ops(args.cfg.ops, &ops_error)) {
    std::cerr << "Error: " << ops_error << "\n";
    return 2;
  }

  std::string level_raw;
  if (args.read_stdin) {
    std::string line;
    while (std::getline(std::cin, line)) {
      if (!level_raw.empty()) {
        level_raw.push_back('\n');
      }
      level_raw += line;
    }
  } else {
    if (!read_file_text(args.level_input, &level_raw)) {
      level_raw = args.level_input;
    }
  }
  level_raw = trim(level_raw);
  if (level_raw.empty()) {
    std::cerr << "Error: empty level input\n";
    return 2;
  }

  Level level;
  std::string level_error;
  if (!parse_level(level_raw, &level, &level_error)) {
    std::cerr << "Error: " << level_error << "\n";
    return 2;
  }

  args.cfg.max_depth = std::min(args.cfg.max_depth, level.program_limit);
  if (args.cfg.max_depth < args.cfg.min_depth) {
    std::cerr << "Error: --max-depth is below --min-depth after clamping to level plim=" << level.program_limit
              << "\n";
    return 2;
  }

  if (args.cfg.verbose) {
    std::cerr << "[level] id=" << (level.level_id.empty() ? "?" : level.level_id)
              << " size=" << level.width << "x" << level.height
              << " start=(" << level.start_x << "," << level.start_y << ",N)"
              << " plim=" << level.program_limit
              << " elim=" << level.execution_limit
              << " search_depth=" << args.cfg.min_depth << "-" << args.cfg.max_depth
              << " ops=" << args.cfg.ops
              << " jump=" << (args.cfg.full_jump_range ? "full" : std::to_string(args.cfg.max_jump_distance))
              << " timeout=" << (args.cfg.timeout_seconds <= 0.0 ? "none" : std::to_string(args.cfg.timeout_seconds))
              << " max_programs=" << args.cfg.max_programs
              << "\n";
  }

  BruteForceSolver solver(level, args.cfg);
  SolveResult result = solver.solve();

  if (!result.found) {
    std::cout << "No solution found\n";
    if (args.cfg.verbose) {
      std::string reason = "complete search";
      if (result.stats.stopped_by_timeout) {
        reason = "timeout";
      } else if (result.stats.stopped_by_budget) {
        reason = "program budget";
      }
      std::cerr << "[done] reason=" << reason
                << " templates=" << result.stats.tested_templates
                << " simulated=" << result.stats.simulated_programs
                << " pruned(unreach/jump/turn)="
                << result.stats.pruned_unreachable << "/"
                << result.stats.pruned_meaningless_jump << "/"
                << result.stats.pruned_turn_cancel_templates
                << " elapsed=" << result.stats.elapsed_seconds << "s\n";
    }
    return 1;
  }

  std::cout << format_program(result.program) << "\n";
  if (args.cfg.verbose) {
    RunResult rr = simulate_program(level, result.program);
    std::cerr << "[done] solved steps=" << rr.steps
              << " templates=" << result.stats.tested_templates
              << " simulated=" << result.stats.simulated_programs
              << " pruned(unreach/jump/turn)="
              << result.stats.pruned_unreachable << "/"
              << result.stats.pruned_meaningless_jump << "/"
              << result.stats.pruned_turn_cancel_templates
              << " elapsed=" << result.stats.elapsed_seconds << "s\n";
  }
  return 0;
}

