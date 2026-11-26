// CommandParser.h - parses the line-based serial protocol (see docs/PROTOCOL.md).
//
//   "N 12345"   "n12345"   "F=1234.5"   "C -3.25"   "?"
//
// Each line is one letter command plus an optional decimal argument. The
// argument is returned in thousandths ("milli" units) so firmware never needs
// floating point. Has no hardware dependencies, so it is unit-tested on a PC.
#pragma once
#include <stdint.h>

namespace nixie {

struct Command {
  char op;         // upper-case command letter, or 0 if the line is empty
  bool hasArg;     // an argument was present
  int64_t milli;   // argument x 1000, truncated past 3 decimals
};

enum ParseResult : uint8_t { PARSE_OK = 0, PARSE_EMPTY, PARSE_BAD };

inline bool isSpace_(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }

inline ParseResult parseCommand(const char *s, Command &cmd) {
  cmd.op = 0;
  cmd.hasArg = false;
  cmd.milli = 0;
  while (*s && isSpace_(*s)) ++s;
  if (!*s) return PARSE_EMPTY;

  char op = *s++;
  if (op >= 'a' && op <= 'z') op = (char)(op - 'a' + 'A');
  if (!((op >= 'A' && op <= 'Z') || op == '?')) return PARSE_BAD;
  cmd.op = op;

  while (*s && (isSpace_(*s) || *s == '=' || *s == ':')) ++s;
  if (!*s) return PARSE_OK;

  bool neg = false;
  if (*s == '+' || *s == '-') neg = (*s++ == '-');

  int64_t whole = 0;
  int64_t frac = 0;
  int fracDigits = 0;
  bool anyDigit = false;
  while (*s >= '0' && *s <= '9') {
    whole = whole * 10 + (*s++ - '0');
    anyDigit = true;
    if (whole > 1000000000000LL) return PARSE_BAD;  // way past anything useful
  }
  if (*s == '.') {
    ++s;
    while (*s >= '0' && *s <= '9') {
      if (fracDigits < 3) {
        frac = frac * 10 + (*s - '0');
        ++fracDigits;
      }
      ++s;
      anyDigit = true;
    }
  }
  while (*s && isSpace_(*s)) ++s;
  if (!anyDigit || *s) return PARSE_BAD;
  while (fracDigits < 3) {
    frac *= 10;
    ++fracDigits;
  }
  cmd.hasArg = true;
  cmd.milli = (whole * 1000 + frac) * (neg ? -1 : 1);
  return PARSE_OK;
}

}  // namespace nixie
