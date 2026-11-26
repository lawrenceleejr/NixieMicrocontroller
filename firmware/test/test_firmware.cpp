// Host-side tests for the hardware-independent firmware logic.
//
//   make -C firmware/test
//
// The key test simulates the timer: it builds the exact sequence of output
// edges (in CPU clock ticks) that Timer1 would produce, then slides a
// one-second counter gate across it and checks that every gate position counts
// exactly N edges, including when the crystal is off by a calibrated amount.
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <random>
#include <vector>

#include "../NixieCounter/CommandParser.h"
#include "../NixieCounter/FracDivider.h"

using namespace nixie;

static int g_failures = 0;
#define CHECK(cond)                                                     \
  do {                                                                  \
    if (!(cond)) {                                                      \
      std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);       \
      ++g_failures;                                                     \
    }                                                                   \
  } while (0)

static const uint32_t kF = 16000000UL;
static const uint16_t kMinCycles = 160;

// Edge times (CPU ticks) for `seconds` seconds of output.
static std::vector<uint64_t> simulateEdges(const PeriodPlan &plan, double seconds,
                                           double ticksPerSecond) {
  FracDivider div;
  div.load(plan.whole, plan.frac);
  div.reset();
  const uint64_t scale = kPrescalers[plan.prescIndex];
  std::vector<uint64_t> edges;
  uint64_t t = 0;
  const double end = seconds * ticksPerSecond;
  while ((double)t < end) {
    edges.push_back(t);
    t += scale * div.next();
  }
  return edges;
}

// Count edges in [t0, t0 + gate) for many gate positions; return min and max,
// and the fraction of gates that counted exactly `expect`.
static double gateCounts(const std::vector<uint64_t> &edges, double gate, int trials,
                         uint64_t &minCount, uint64_t &maxCount, std::mt19937_64 &rng,
                         uint64_t expect = 0) {
  minCount = ~0ULL;
  maxCount = 0;
  int exact = 0;
  const double span = (double)edges.back() - gate;
  std::uniform_real_distribution<double> u(0.0, span);
  for (int i = 0; i < trials; ++i) {
    const double t0 = u(rng);
    const auto lo = std::lower_bound(edges.begin(), edges.end(), (uint64_t)std::ceil(t0));
    const auto hi = std::lower_bound(edges.begin(), edges.end(), (uint64_t)std::ceil(t0 + gate));
    const uint64_t n = (uint64_t)(hi - lo);
    minCount = std::min(minCount, n);
    maxCount = std::max(maxCount, n);
    exact += (n == expect);
  }
  return (double)exact / trials;
}

static void testExactCounts() {
  std::mt19937_64 rng(12345);
  const uint32_t numbers[] = {1, 2, 7, 59, 997, 1234, 12345, 31415, 54321, 99999, 100000};
  const int32_t ppbs[] = {0, 12345, -48000, 3000000};
  double worstExact = 1.0;
  for (int32_t ppb : ppbs) {
    for (uint32_t n : numbers) {
      uint32_t mHz;
      CHECK(numberToMilliHz(n, 1000, mHz));
      PeriodPlan plan;
      CHECK(planPeriod(kF, ppb, mHz, kMinCycles, plan) == PLAN_OK);
      // The board's crystal really runs at kF * (1 + ppb): that many ticks is
      // one true second, which is what the counter's gate measures.
      const double trueTicksPerSecond = kF * (1.0 + ppb * 1e-9);
      const double seconds = n < 10 ? 40.0 : 3.0;
      const auto edges = simulateEdges(plan, seconds, trueTicksPerSecond);
      uint64_t lo, hi;
      const double exact = gateCounts(edges, trueTicksPerSecond, 4000, lo, hi, rng, n);
      // The 16-bit fraction leaves at most ~0.05 ppm of rate error, so at
      // full scale a rare gate can catch one edge too many or too few.
      if (lo + 1 < n || hi > n + 1 || exact < 0.98) {
        std::printf("FAIL N=%u ppb=%d counts %llu..%llu exact %.4f\n", n, ppb,
                    (unsigned long long)lo, (unsigned long long)hi, exact);
        ++g_failures;
      }
      worstExact = std::min(worstExact, exact);
    }
  }
  std::printf("exact-count test: worst case %.2f%% of gates exact\n", 100 * worstExact);
}

// Without correction, a crystal that is 20 ppm fast makes every period 20 ppm
// short in true time, so at full scale the counter sees 99999 * (1 + 20e-6)
// = 100001 edges. This checks that the simulation really shows the error that
// calibration removes.
static void testUncalibratedDrifts() {
  std::mt19937_64 rng(7);
  uint32_t mHz;
  numberToMilliHz(99999, 1000, mHz);
  PeriodPlan plan;
  CHECK(planPeriod(kF, 0, mHz, kMinCycles, plan) == PLAN_OK);
  const double trueTicks = kF * (1.0 + 20e-6);
  const auto edges = simulateEdges(plan, 3.0, trueTicks);
  uint64_t lo, hi;
  gateCounts(edges, trueTicks, 2000, lo, hi, rng);
  CHECK(lo >= 100001 && hi <= 100002);
}

static void testPlanEdges() {
  PeriodPlan p;
  CHECK(planPeriod(kF, 0, 1000000, kMinCycles, p) == PLAN_OK);  // 1 kHz
  CHECK(p.prescIndex == 0 && p.whole == 16000 && p.frac == 0);

  CHECK(planPeriod(kF, 0, 7000, kMinCycles, p) == PLAN_OK);  // 7 Hz -> /64
  CHECK(p.prescIndex == 2 && p.whole == 35714);
  CHECK(std::fabs(p.frac / 65536.0 - 0.2857142857) < 1.0 / 65536);

  CHECK(planPeriod(kF, 0, 100000000, kMinCycles, p) == PLAN_OK);   // 100 kHz
  CHECK(planPeriod(kF, 0, 100001000, kMinCycles, p) == PLAN_TOO_FAST);
  CHECK(planPeriod(kF, 0, 239, kMinCycles, p) == PLAN_OK);         // 0.239 Hz
  CHECK(planPeriod(kF, 0, 238, kMinCycles, p) == PLAN_TOO_SLOW);
  CHECK(planPeriod(kF, 0, 0, kMinCycles, p) == PLAN_OFF);
  CHECK(planPeriod(kF, 60000000, 1000, kMinCycles, p) == PLAN_BAD_CAL);
  CHECK(planPeriod(kF, 0, 0xFFFFFFFFUL, kMinCycles, p) == PLAN_TOO_FAST);
}

static void testHelpers() {
  uint32_t mHz;
  CHECK(numberToMilliHz(12345, 1000, mHz) && mHz == 12345000UL);
  CHECK(numberToMilliHz(12345, 100, mHz) && mHz == 123450000UL);   // 0.1 s gate
  CHECK(numberToMilliHz(12345, 10000, mHz) && mHz == 1234500UL);   // 10 s gate
  CHECK(!numberToMilliHz(1, 0, mHz));
  CHECK(!numberToMilliHz(999999, 1, mHz));

  // 10 000 edges of a 1 kHz reference should take 10 s; 100 us extra = +10 ppm
  CHECK(clockErrorPpb(10000, 1000, 10000100UL) == 10000);
  CHECK(clockErrorPpb(10000, 1000, 9999900UL) == -10000);
  CHECK(clockErrorPpb(10, 1, 10000000UL) == 0);  // GPS 1PPS, perfect clock
  CHECK(clockErrorPpb(0, 1000, 5) == 0);
}

static void testParser() {
  Command c;
  CHECK(parseCommand("N 12345", c) == PARSE_OK && c.op == 'N' && c.hasArg && c.milli == 12345000);
  CHECK(parseCommand("n12345\r", c) == PARSE_OK && c.op == 'N' && c.milli == 12345000);
  CHECK(parseCommand("F=1234.5", c) == PARSE_OK && c.op == 'F' && c.milli == 1234500);
  CHECK(parseCommand("c -3.25", c) == PARSE_OK && c.op == 'C' && c.milli == -3250);
  CHECK(parseCommand("C +0.0015", c) == PARSE_OK && c.milli == 1);  // truncates
  CHECK(parseCommand("  ?  ", c) == PARSE_OK && c.op == '?' && !c.hasArg);
  CHECK(parseCommand("", c) == PARSE_EMPTY);
  CHECK(parseCommand("   \r\n", c) == PARSE_EMPTY);
  CHECK(parseCommand("N 12x", c) == PARSE_BAD);
  CHECK(parseCommand("N -", c) == PARSE_BAD);
  CHECK(parseCommand("N .", c) == PARSE_BAD);
  CHECK(parseCommand("5", c) == PARSE_BAD);
  CHECK(parseCommand("N 99999999999999999", c) == PARSE_BAD);
  CHECK(parseCommand("G .5", c) == PARSE_OK && c.milli == 500);
}

int main() {
  testPlanEdges();
  testHelpers();
  testParser();
  testExactCounts();
  testUncalibratedDrifts();
  if (g_failures) {
    std::printf("%d failure(s)\n", g_failures);
    return 1;
  }
  std::printf("all firmware tests passed\n");
  return 0;
}
