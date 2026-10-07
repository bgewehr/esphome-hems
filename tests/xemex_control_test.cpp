#include "components/modbus_server/xemex_control.h"

#include <cstdlib>
#include <iostream>
#include <limits>

namespace {

using namespace esphome::modbus_server;

void require(bool condition, const char *message) {
  if (condition)
    return;
  std::cerr << "FAILED: " << message << '\n';
  std::exit(EXIT_FAILURE);
}

void test_calibrated_control() {
  XemexReading reading{{15.89f, 15.38f, 15.40f}, {100, 100, 100}, 10530.0f, 100};
  const auto report = xemex_report(6000, reading, 100);
  require(std::abs(report[0] - 48.3664f) < 0.001f, "6 kW uses the calibrated proportional controller");
  require(report[1] < report[0], "independent phase currents are preserved");
  require(xemex_report(6000, reading, 101) == report, "setpoint and sensor updates use the same calculation");
  require(std::abs(xemex_report(4200, reading, 100)[0] - 63.29f) < 0.001f, "4.2 kW stops without damping");
  require(std::abs(xemex_report(11500, reading, 100)[0] - 16.89f) < 0.001f, "unlimited mode is unchanged");
  require(xemex_addon(5543) == 47.4f && xemex_addon(5544) < 47.3f, "8 A threshold is unchanged");
  reading.current.fill(0);
  reading.power = 6;
  require(std::abs(xemex_report(6000, reading, 100)[0] - 36.342f) < 0.001f, "restart feedforward is unchanged");
  require(xemex_report(0, reading, 100)[0] == 47.4f, "idle stop remains asserted");
}

void test_watchdog() {
  XemexReading reading;
  require(xemex_report(11500, reading, 1)[0] == 80, "boot is fail closed");
  reading = {{9, 9, 9}, {100, 100, 100}, 6000, 100};
  require(reading.fresh(5100), "TTL boundary is inclusive");
  require(xemex_report(6000, reading, 5101)[0] == 80, "stale readings revoke all headroom");
  reading.updated[1] = 0;
  require(xemex_report(6000, reading, 100)[0] == 80, "one missing phase stops all phases");
  reading.updated[1] = 100;
  reading.power_updated = 0;
  require(xemex_report(6000, reading, 100)[0] == 80, "power must also be fresh");
  reading.power_updated = 100;
  reading.current[2] = std::numeric_limits<float>::quiet_NaN();
  require(xemex_report(6000, reading, 100)[0] == 80, "NaN cannot grant charging headroom");
  reading.current[2] = 33;
  require(xemex_report(6000, reading, 100)[0] == 80, "implausible current fails closed");
  reading.current[2] = 9;
  require(xemex_report(std::numeric_limits<float>::quiet_NaN(), reading, 100)[0] == 80,
          "invalid limit fails closed");
  require(xemex_fresh(100, UINT32_MAX - 100), "millis rollover preserves recent samples");
}

void test_limit_precedence() {
  require(xemex_limit(6000, 11500, false, -1, false) == 6000, "user ceiling cannot be bypassed");
  require(xemex_limit(11500, 11000, true, 6000, false) == 6000, "budget ceiling cannot be bypassed");
  require(xemex_limit(11500, 11000, true, -1, false) == 0, "missing active budget fails closed");
  require(xemex_limit(11500, 11000, true, 6000, true) == 0, "DI1 and simulation override all budgets");
  require(xemex_limit(6000, 6000, false, -1, false) == 6000, "cleared budget restores user request");
  require(std::isnan(xemex_limit(NAN, 6000, false, -1, false)), "invalid user limit is not hidden by min");
}

float tick(XemexChargeGuard &guard, uint32_t now, float current, float requested = 6000) {
  const XemexReading reading{{current, current, current}, {now, now, now}, current * 693, now};
  return guard.step(requested, reading, now);
}

float observe(XemexChargeGuard &guard, uint32_t start, uint32_t duration, float current, float requested = 6000) {
  for (uint32_t elapsed = 0; elapsed < duration; elapsed += 1000)
    tick(guard, start + elapsed, current, requested);
  return tick(guard, start + duration, current, requested);
}

void test_guard_execution_gap() {
  XemexChargeGuard guard;
  for (uint32_t now = 1000; now <= 9000; now += 1000)
    tick(guard, now, 9);
  tick(guard, 10000, 0);
  require(tick(guard, 31000, 0) == 6000, "execution gap cannot confirm continuous loss of current");
  require(guard.stop_count() == 0, "unobserved interval does not count as an abort");
  observe(guard, 32000, 20000, 0);
  require(guard.stop_count() == 0, "charging must be confirmed again after an execution gap");

  guard = XemexChargeGuard{};
  tick(guard, 1000, 9);
  tick(guard, 10000, 9);
  require(std::string(guard.status) == "Start angefordert", "execution gap cannot confirm continuous charging");
  observe(guard, 11000, 20000, 0);
  require(guard.stop_count() == 0, "isolated high readings cannot arm abort detection");
}

void test_charge_guard() {
  XemexChargeGuard guard;
  require(tick(guard, 100, 0) == 6000, "idle vehicle may receive initial request");
  observe(guard, 100, 180000, 0);
  require(!guard.latched && guard.stop_count() == 0, "vehicle that never charged is not counted as an abort");
  require(std::string(guard.status) == "Start ohne Stromfluss", "unsuccessful start is visible");
  observe(guard, 181000, 8000, 9);
  require(observe(guard, 190000, 19999, 0) == 6000, "short DLB pauses do not revoke request");
  require(tick(guard, 210000, 0) == 0, "confirmed abort requests stop");
  require(guard.stop_count() == 1, "one continuous pause counts once");
  require(tick(guard, 239999, 0) == 0, "retry waits 30 seconds");
  require(tick(guard, 240000, 0) == 6000, "retry restores request after cooldown");

  guard = XemexChargeGuard{};
  uint32_t now = UINT32_MAX - 100000;
  for (unsigned attempt = 0; attempt < 5; ++attempt) {
    observe(guard, now, 8000, 9);
    require(observe(guard, now + 9000, 20000, 0) == 0, "each confirmed abort stops");
    now += 59000;
  }
  require(guard.latched && guard.stop_count() == 5, "five aborts within five minutes latch across rollover");
  require(tick(guard, now, 0) == 0, "latched guard cannot resume automatically");
  XemexReading stopped{{0, 0, 0}, {now, now, now}, 6, now};
  require(!guard.reset(6000, stopped, now), "reset requires explicit user stop");
  require(!guard.reset(0, stopped, now + 5001), "reset requires fresh feedback");
  stopped.current[2] = 1;
  require(!guard.reset(0, stopped, now), "reset requires all phases below 1 A");
  stopped.current[2] = 0;
  require(guard.reset(0, stopped, now) && !guard.latched, "safe manual reset clears latch");
  require(tick(guard, now, 0, 0) == 0, "reset does not start charging");
}

void test_guard_stop_and_stale() {
  XemexChargeGuard guard;
  observe(guard, 100, 8000, 9);
  tick(guard, 9000, 0, 0);
  tick(guard, 40000, 0, 0);
  require(guard.stop_count() == 0, "intentional stop is not an abort");
  observe(guard, 41000, 90000, 9, 0);
  require(std::string(guard.status) == "Stopp nicht bestaetigt", "failed physical stop is visible");
  const XemexReading missing;
  require(std::isnan(guard.step(6000, missing, 140000)), "stale guard output selects 80 A failsafe");
  tick(guard, 141000, 0);
  tick(guard, 170000, 0);
  require(guard.stop_count() == 0, "missing data cannot confirm an abort");
}

}

int main() {
  test_calibrated_control();
  test_watchdog();
  test_limit_precedence();
  test_guard_execution_gap();
  test_charge_guard();
  test_guard_stop_and_stale();
  std::cout << "Xemex control tests passed\n";
}