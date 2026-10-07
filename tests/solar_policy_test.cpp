#include "components/power_distribution/solar_policy.h"
#include "components/modbus_server/xemex_control.h"

#include <cstdlib>
#include <iostream>

namespace {
using namespace esphome::power_distribution;

void require(bool condition, const char *message) {
  if (condition) return;
  std::cerr << "FAILED: " << message << '\n';
  std::exit(EXIT_FAILURE);
}

SolarInputs readings(uint32_t now, float pv = 6000, float loads = 1000,
                     float charge = 0, float capacity = NAN) {
        return {{pv, now}, {pv, now}, {loads, now}, {charge, now}, {capacity, now}};
}

SolarDecision advance(SolarPolicy &policy, SolarOptions options, uint32_t start,
                      uint32_t duration, float pv, float loads = 1000,
                      float charge = 0, float capacity = NAN) {
  SolarDecision result;
  for (uint32_t elapsed = 0; elapsed <= duration; elapsed += 1000)
    result = policy.step(readings(start + elapsed, pv, loads, charge, capacity),
                         options, start + elapsed);
  return result;
}

void allocation() {
        require(!solar_blocked_load_active({0, 1000}, false, 1000), "idle forbidden EV needs no forced battery charge");
        require(!solar_blocked_load_active({6, 1000}, false, 1000), "wallbox standby needs no forced battery charge");
        require(!solar_blocked_load_active({30, 1000}, false, 1000), "30 W boundary does not enforce charging");
        require(solar_blocked_load_active({31, 1000}, false, 1000), "active forbidden consumer needs enforcement");
        require(!solar_blocked_load_active({6000, 1000}, true, 1000), "battery-permitted consumer needs no enforcement");
        require(!solar_blocked_load_active({6000, 1000}, false, 11001), "stale load cannot request forced charging");
        require(!solar_blocked_load_active({6000, 0}, false, 1000), "missing load cannot request forced charging");
        require(!solar_blocked_load_active({NAN, 1000}, false, 1000), "invalid load cannot request forced charging");
        require(!solar_blocked_load_active({-100, 1000}, false, 1000), "negative load cannot request forced charging");
        constexpr float minimum = esphome::modbus_server::XEMEX_MIN_CHARGE_POWER;
        require(solar_charge_limit(11000, true, true, minimum) == minimum, "solar limit selects stable wallbox minimum");
        require(solar_charge_limit(11500, true, true, minimum, true) == 11500, "qualified low-solar grid mode bypasses solar cap");
        require(solar_charge_limit(4200, true, true, minimum, true) == 4200, "grid mode preserves paragraph 14a limit");
        require(solar_charge_limit(0, true, true, minimum, true) == 0, "grid mode preserves stop and stale permission gate");
        require(std::isnan(solar_charge_limit(NAN, true, true, minimum, true)), "grid mode preserves invalid request");
        require(solar_charge_limit(11000, true, false, minimum) == 11000, "disabled solar limit preserves requested power");
        require(solar_charge_limit(11000, false, true, minimum) == 11000, "solar limit requires solar-only mode");
        require(solar_charge_limit(6000, false, false, minimum) == 6000, "both options off preserve user request");
        require(solar_charge_limit(4200, true, true, minimum) == 4200, "solar limit never raises grid or user limit");
        require(solar_charge_limit(0, true, true, minimum) == 0, "solar limit preserves manual and PV stop");
        require(std::isnan(solar_charge_limit(NAN, true, true, minimum)), "invalid request remains invalid");
        require(solar_charge_limit(11000, true, true, NAN) == 0, "unknown minimum fails closed");
        require(storage_target_valid(-7, true, 1000, 2000, -6), "PV decrease does not cancel in-flight charge verification");
        require(storage_target_valid(-7, true, 1000, 2000, -8), "PV increase preserves in-flight verification");
        require(!storage_target_valid(-7, false, 1000, 2000, -6), "priority disabled cancels charge transaction");
        require(!storage_target_valid(-7, true, 1000, 11001, -6), "stale policy cancels charge transaction");
        require(!storage_target_valid(-7, true, 1000, 2000, NAN), "invalid policy cancels charge transaction");
        require(storage_ev_limit(6000, false, true, 1000, 1000) == 0, "storage fault blocks forbidden battery EV supply");
        require(storage_ev_limit(6000, false, false, 0, 1000) == 0, "startup requires verified storage protection");
        require(storage_ev_limit(6000, false, false, 1000, 11001) == 0, "expired verification blocks EV");
        require(storage_ev_limit(6000, false, false, 1000, 10000) == 6000, "verified storage preserves user request");
        require(storage_ev_limit(6000, true, true, 0, 1000) == 6000, "explicit battery permission remains valid");
        require(storage_ev_limit(0, false, false, 1000, 1000) == 0, "storage recovery cannot override user stop");
        require(solar_ev_limit(11000, true, false, 1000, 1000) == 0, "PV pause gates EV request");
        require(solar_ev_limit(0, true, true, 1000, 1000) == 0, "PV cannot override user stop");
        require(solar_ev_limit(4200, true, true, 1000, 1000) == 4200, "PV preserves grid limit");
        require(solar_ev_limit(11000, true, true, 1000, 11001) == 0, "stale permission stops EV");
        require(solar_ev_limit(11000, false, false, 0, 1000) == 11000, "disabled policy preserves request");
        require(solar_grid_budget(4200, 6000, 1000, 0, 4000) == 5000, "charge reserved once before solar credit");
        require(solar_grid_budget(4200, 0, 1000, 0, 1000) == 3200, "grid charging consumes grid budget");
        require(solar_grid_budget(4200, 0, 1000, 0, 5000) == 0, "grid budget cannot go negative");
        require(solar_grid_budget(4200, 6000, 1000, 0, NAN) == 0, "unknown reservation fails closed");
  for (unsigned mask = 0; mask < 8; ++mask) {
    SolarPolicy policy;
    SolarOptions options{bool(mask & 1), bool(mask & 2), bool(mask & 4)};
    const auto result = advance(policy, options, 1000, 60000, 6000, 1000, 0);
    require(result.valid, "all option combinations have valid allocation");
    require(result.battery_target_ac_w == (options.battery_first ? 5000 : 0),
            "battery reservation depends only on battery priority");
    require(result.ev_permitted == (!options.solar_only || !options.battery_first),
            "PV-only and priority compose without blocking unrestricted EV");
  }
  SolarPolicy policy;
  auto result = policy.step(readings(1000, 6000, 1000, 2000, 2000), {true, true, false}, 1000);
  require(result.ev_support_ac_w == 3000, "verified capacity releases unusable reservation");
  result = policy.step(readings(2000, 6000, 1000, 0, 0), {true, true, false}, 2000);
  require(result.ev_support_ac_w == 5000, "verified full battery releases PV");
  result = policy.step(readings(3000, 6000, 1000, 4000, 2000), {true, true, false}, 3000);
  require(result.ev_support_ac_w == 1000, "actual charging above request remains accounted");
  result = policy.step(readings(4000, 6000, 1000, 5000), {false, true, false}, 4000);
  require(result.ev_support_ac_w == 5000, "natural charging does not deadlock EV start");
  require(result.budget_charge_reservation_ac_w == 5000, "actual charging still reduces budget");
  result = policy.step(readings(5000, 0, 1000), {true, false, false}, 5000);
  require(result.battery_target_ac_w == 0 && result.ev_permitted,
          "night ends forced charge without stopping unrestricted EV");
  require(solar_outwrte(2500, 10000) == -25, "negative OutWRte scales by reference");
  require(solar_outwrte(12000, 10000) == -100, "reference caps requested percentage");
  require(std::isnan(solar_outwrte(100, 0)) && std::isnan(solar_outwrte(NAN, 10000)),
          "invalid reference or target cannot create a command");
}

void timing() {
  SolarPolicy policy;
  const SolarOptions options{false, true, true};
  require(!advance(policy, options, 1000, 59000, 1600).ev_permitted, "start requires 60 seconds");
  require(policy.step(readings(61000, 1600), options, 61000).ev_permitted,
          "500 W support is not the EV minimum charging power");
  require(advance(policy, options, 62000, 119000, 1499).ev_permitted,
          "support below 500 W retains charging for less than 120 seconds");
  require(!policy.step(readings(182000, 1499), options, 182000).ev_permitted,
          "support below 500 W pauses after 120 seconds");
  require(!advance(policy, options, 183000, 478000, 1499).ev_permitted,
          "low solar requires 10 minutes of low residual support");
  auto result = policy.step(readings(662000, 1499), options, 662000);
  require(result.ev_permitted && result.reason == SolarReason::LOW_SOLAR,
          "499 W support permits grid charging even with high total generation");
  require(solar_charge_limit(11500, true, true, esphome::modbus_server::XEMEX_MIN_CHARGE_POWER,
                            result.reason == SolarReason::LOW_SOLAR) == 11500,
          "qualified grid decision releases normal requested power");
  require(advance(policy, options, 663000, 300000, 1499).reason == SolarReason::LOW_SOLAR,
          "total generation above 500 W cannot revoke low-support permission");
  result = advance(policy, options, 964000, 300000, 1501);
  require(result.reason == SolarReason::PV_SUPPORT,
          "support above 500 W restores PV mode");
  require(solar_charge_limit(11500, true, true, esphome::modbus_server::XEMEX_MIN_CHARGE_POWER,
                            result.reason == SolarReason::LOW_SOLAR) == esphome::modbus_server::XEMEX_MIN_CHARGE_POWER,
          "PV mode restores solar cap after grid permission");
  require(!advance(policy, options, 1265000, 120000, 1499).ev_permitted,
          "five minutes of PV support cleared the previous grid exception");

  SolarPolicy priority;
  require(advance(priority, {true, true, true}, 1000, 600000, 6000).reason == SolarReason::LOW_SOLAR,
          "battery priority can leave low residual support and permit grid charging");
  SolarPolicy boundary;
  require(!advance(boundary, options, 1000, 601000, 1500).ev_permitted,
          "exactly 500 W qualifies neither PV start nor low solar");
  require(advance(boundary, options, 603000, 60000, 1501).ev_permitted,
          "501 W qualifies PV support");
  require(advance(boundary, options, 664000, 601000, 1500).reason == SolarReason::PV_SUPPORT,
          "exactly 500 W preserves existing PV permission");
  SolarPolicy no_grid;
  require(!advance(no_grid, {false, true, false}, 1000, 700000, 1499).ev_permitted,
          "low support cannot enable a disabled grid exception");

        SolarPolicy converted;
        for (uint32_t now = 1000; now <= 602000; now += 1000) {
                auto inputs = readings(now, 0);
                inputs.pv_generation.watts = 6000;
                require(converted.step(inputs, options, now).ev_permitted == (now >= 601000),
                                                "low residual AC support controls grid qualification");
        }
}

void faults_and_precedence() {
  SolarPolicy policy;
  const SolarOptions options{false, true, false};
  require(advance(policy, options, 1000, 60000, 6000).ev_permitted, "initial PV permission");
  require(!policy.step(readings(72000), options, 72000).ev_permitted,
          "execution gaps cannot retain permission");
  auto bad = readings(73000);
  bad.pv_ac.updated_ms = 1;
  require(!policy.step(bad, options, 73000).ev_permitted, "stale readings revoke permission");
  bad = readings(73500);
  bad.pv_generation.watts = NAN;
  require(!policy.step(bad, {false, true, true}, 73500).valid,
          "missing generation cannot qualify the low-solar exception");
  bad = readings(74000);
  bad.non_ev_load_ac.watts = NAN;
  require(!policy.step(bad, options, 74000).valid, "invalid loads revoke allocation");
  require(policy.step(bad, {}, 75000).ev_permitted, "disabled feature adds no stop on bad data");
  require(!policy.step(readings(76000), options, 76000).ev_permitted,
          "enabling PV-only requires fresh qualification");
  SolarPolicy rollover;
  require(advance(rollover, options, UINT32_MAX - 30500, 60000, 6000).ev_permitted,
          "timers work across millis rollover");
  auto old_capacity = readings(100000, 6000, 1000, 0, 0);
  old_capacity.charge_capacity_ac.updated_ms = 1;
  require(policy.step(old_capacity, {true, true, false}, 100000).ev_support_ac_w == 0,
          "stale full indication cannot release priority reservation");

  using namespace esphome::modbus_server;
  require(xemex_limit(0, 11500, false, -1, false) == 0,
          "policy permission cannot override user stop");
  require(xemex_limit(11500, 11500, true, 4200, false) == 4200,
          "policy permission cannot override paragraph 14a");
  XemexChargeGuard guard;
  const XemexReading current{{9, 9, 9}, {1000, 1000, 1000}, 6000, 1000};
  require(guard.step(0, current, 1000) == 0 && guard.stop_count() == 0,
          "policy pause is an intentional stop");
  guard.latched = true;
  require(guard.step(11500, current, 1000) == 0, "permission does not reset protection latch");
}
}

int main() {
  allocation();
  timing();
  faults_and_precedence();
  std::cout << "solar policy tests passed\n";
}