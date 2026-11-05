#include "components/eebus_cs/lpc_state.h"

#include <cstdlib>
#include <iostream>
#include <limits>

using esphome::eebus_cs::LpcState;

static void require(bool condition, const char *message) {
  if (condition) return;
  std::cerr << "FAILED: " << message << '\n';
  std::exit(EXIT_FAILURE);
}

int main() {
  require(!LpcState::valid_failsafe_duration(7199999), "failsafe duration below 2 hours rejected");
  require(LpcState::valid_failsafe_duration(7200000), "2 hour failsafe duration accepted");
  require(LpcState::valid_failsafe_duration(86400000), "24 hour failsafe duration accepted");
  require(!LpcState::valid_failsafe_duration(86400001), "failsafe duration above 24 hours rejected");
  LpcState state;
  state.begin(100);
  require(state.active() && state.watts() == 4200, "init applies failsafe");
  require(!state.limit(6000, true, 101), "initial limit requires heartbeat");
  state.heartbeat(200);
  require(!state.accepts_configuration(), "heartbeat alone cannot enable configuration");
  require(state.limit(0, true, 201) && state.active() && state.watts() == 0,
          "zero is an active limit");
  require(state.limit(6000, true, 300), "normal limit accepted");
  state.tick(60200);
  require(state.watts() == 6000, "60 seconds without heartbeat retains limit");
  state.begin(90000);
  require(state.watts() == 6000, "reconnect does not reset state or timers");
  require(state.limit(5000, true, 119000), "limit writes do not count as heartbeat");
  state.tick(120200);
  require(state.state() == LpcState::State::FAILSAFE && state.watts() == 4200,
          "120 seconds without heartbeat applies failsafe");
  require(!state.limit(0, false, 120201), "stale heartbeat cannot release failsafe");
  state.heartbeat(120300);
  state.begin(120301);
  require(state.active() && state.watts() == 4200, "heartbeat alone retains failsafe");
  require(state.limit(0, false, 120301) && !state.active(), "explicit release after heartbeat");
  state.heartbeat(130000);
  require(state.limit(2000, true, 130001, 1000, true), "timed limit accepted");
  state.tick(131000);
  require(state.active(), "limit remains until duration expires");
  state.tick(131001);
  require(!state.active(), "duration expiry releases controlled limit");
  state.tick(250000);
  require(state.active(), "unlimited controlled still enters failsafe");
  state.heartbeat(250100);
  require(!state.limit(-1, true, 250101), "negative limit rejected");
  require(!state.limit(std::numeric_limits<float>::quiet_NaN(), true, 250101), "NaN rejected");
  require(!state.limit(1, true, 310101), "init/failsafe acceptance window is 60 seconds");
  require(state.limit(1, true, 310100), "60 second recovery boundary accepted");
  state.reset();
  state.begin(0xfffffff0u);
  state.heartbeat(0xfffffff0u);
  require(state.limit(1000, true, 0xfffffff1u), "limit accepted near clock wrap");
  state.tick(119984u);
  require(state.state() == LpcState::State::FAILSAFE, "heartbeat timeout survives clock wrap");
  std::cout << "PASS: LPC state transitions\n";
}