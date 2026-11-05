#pragma once

#include <cmath>
#include <cstdint>

namespace esphome {
namespace eebus_cs {

class LpcState {
 public:
  enum class State { INACTIVE, INIT, CONTROLLED, FAILSAFE };

  void begin(uint32_t now) {
    if (state_ != State::INACTIVE) return;
    state_ = State::INIT;
    last_heartbeat_ = now;
    have_heartbeat_ = false;
  }

  void heartbeat(uint32_t now) {
    last_heartbeat_ = now;
    have_heartbeat_ = true;
  }

  bool accepts_limit(uint32_t now) const {
    return state_ != State::INACTIVE &&
        (state_ == State::CONTROLLED ||
         (have_heartbeat_ && now - last_heartbeat_ <= 60000u));
  }

  bool accepts_configuration() const { return state_ == State::CONTROLLED; }

  static bool valid_failsafe_duration(uint32_t duration_ms) {
    return duration_ms >= 7200000u && duration_ms <= 86400000u;
  }

  bool limit(float watts, bool active, uint32_t now, uint32_t duration_ms = 0,
             bool has_duration = false) {
    tick(now);
    if (!std::isfinite(watts) || watts < 0 || !accepts_limit(now)) return false;
    state_ = State::CONTROLLED;
    watts_ = watts;
    active_ = active;
    limit_at_ = now;
    duration_ms_ = duration_ms;
    has_duration_ = has_duration;
    tick(now);
    return true;
  }

  void tick(uint32_t now) {
    if (state_ == State::CONTROLLED && now - last_heartbeat_ >= 120000u) {
      state_ = State::FAILSAFE;
    }
    if (state_ == State::CONTROLLED && active_ && has_duration_ &&
        now - limit_at_ >= duration_ms_) active_ = false;
  }

  void set_failsafe(float watts) {
    if (std::isfinite(watts) && watts >= 0) failsafe_watts_ = watts;
  }
  void reset() { *this = LpcState{}; }
  State state() const { return state_; }
  bool active() const {
    return state_ == State::INIT || state_ == State::FAILSAFE ||
        (state_ == State::CONTROLLED && active_);
  }
  float watts() const {
    return state_ == State::INIT || state_ == State::FAILSAFE ? failsafe_watts_ :
        (active() ? watts_ : 0.0f);
  }
  bool heartbeat_lost(uint32_t now) const {
    return !have_heartbeat_ || now - last_heartbeat_ >= 120000u;
  }

 private:
  State state_{State::INACTIVE};
  bool have_heartbeat_{false}, active_{false}, has_duration_{false};
  uint32_t last_heartbeat_{0}, limit_at_{0}, duration_ms_{0};
  float watts_{0}, failsafe_watts_{4200};
};

}
}