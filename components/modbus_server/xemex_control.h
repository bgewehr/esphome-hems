#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>

namespace esphome {
namespace modbus_server {

constexpr uint32_t XEMEX_METER_TTL_MS = 5000;
constexpr float XEMEX_FAILSAFE_CURRENT = 80.0f;

inline bool xemex_fresh(uint32_t now, uint32_t updated) {
  return updated != 0 && now - updated <= XEMEX_METER_TTL_MS;
}

inline float xemex_addon(float watts) {
  if (!std::isfinite(watts) || watts < 8.0f * 693.0f)
    return 47.4f;
  if (watts >= 11400.0f)
    return 1.0f;
  return 50.0f - watts / 693.0f - 2.6f - 2.40f;
}

inline float xemex_limit(float user, float requested, bool budget_active, float budget, bool forced_stop) {
  if (forced_stop)
    return 0.0f;
  if (!std::isfinite(user) || !std::isfinite(requested) || user < 0 || user > 11500 ||
      requested < 0 || requested > 11500)
    return NAN;
  if (budget_active && (!std::isfinite(budget) || budget < 0))
    return 0.0f;
  return budget_active ? std::min({user, requested, budget}) : std::min(user, requested);
}

struct XemexReading {
  std::array<float, 3> current{};
  std::array<uint32_t, 3> updated{};
  float power{0.0f};
  uint32_t power_updated{0};

  bool fresh(uint32_t now) const {
    if (!xemex_fresh(now, power_updated) || !std::isfinite(power) || std::abs(power) > 30000.0f)
      return false;
    for (size_t phase = 0; phase < current.size(); ++phase) {
      if (!xemex_fresh(now, updated[phase]) || !std::isfinite(current[phase]) ||
          current[phase] < 0.0f || current[phase] > 32.0f)
        return false;
    }
    return true;
  }
};

inline std::array<float, 3> xemex_report(float watts, const XemexReading &reading, uint32_t now) {
  std::array<float, 3> report{};
  if (!reading.fresh(now) || !std::isfinite(watts) || watts < 0.0f || watts > 11500.0f) {
    report.fill(XEMEX_FAILSAFE_CURRENT);
    return report;
  }
  const float addon = xemex_addon(watts);
  for (size_t phase = 0; phase < report.size(); ++phase) {
    const float direct = reading.current[phase] + addon;
    report[phase] = addon <= 1.0f || addon >= 47.3f || std::abs(reading.power) < 5000.0f
                        ? direct : 47.4f + (direct - 47.4f) * 0.2f;
  }
  return report;
}

class XemexChargeGuard {
 public:
  bool latched{false};
  const char *status{"Messwerte fehlen"};

  float step(float requested, const XemexReading &reading, uint32_t now) {
    if (has_step_ && now - last_step_ > XEMEX_METER_TTL_MS) {
      reset_observation_();
      stopping_ = false;
    }
    has_step_ = true;
    last_step_ = now;
    size_t retained = 0;
    for (size_t index = 0; index < stop_count_; ++index) {
      if (now - stops_[index] < 300000)
        stops_[retained++] = stops_[index];
    }
    stop_count_ = retained;
    if (!reading.fresh(now) || !std::isfinite(requested) || requested < 0 || requested > 11500) {
      reset_observation_();
      status = "Messwerte oder Sollwert ungueltig";
      return NAN;
    }
    const float peak = *std::max_element(reading.current.begin(), reading.current.end());
    if (latched) {
      status = "Gesperrt: wiederholte Ladeabbrueche";
      return 0;
    }
    if (requested < 5544.0f) {
      reset_observation_();
      if (peak < 1.0f) {
        stopping_ = false;
        status = "Stillstand bestaetigt";
      } else {
        if (!stopping_) {
          stopping_ = true;
          stopping_at_ = now;
        }
        status = now - stopping_at_ >= 90000 ? "Stopp nicht bestaetigt" : "Stopp angefordert";
      }
      return 0;
    }
    stopping_ = false;
    if (retrying_ && now - retry_at_ < 30000) {
      status = "Wiederanlaufwartezeit";
      return 0;
    }
    retrying_ = false;
    if (!starting_) {
      starting_ = true;
      start_at_ = now;
    }
    if (peak >= 6.5f) {
      if (!high_) {
        high_ = true;
        high_at_ = now;
      }
      if (now - high_at_ >= 8000)
        seen_charging_ = true;
    } else {
      high_ = false;
    }
    if (peak >= 1.0f || !seen_charging_) {
      low_ = false;
    } else {
      if (!low_) {
        low_ = true;
        low_at_ = now;
      }
      if (now - low_at_ >= 20000) {
        stops_[stop_count_++] = now;
        latched = stop_count_ == stops_.size();
        reset_observation_();
        retrying_ = true;
        retry_at_ = now;
        status = latched ? "Gesperrt: wiederholte Ladeabbrueche" : "Wiederanlaufwartezeit";
        return 0;
      }
    }
    status = seen_charging_ ? (peak < 1 ? "Strompause beobachtet" : "Laedt")
                           : (now - start_at_ >= 180000 ? "Start ohne Stromfluss" : "Start angefordert");
    return requested;
  }

  bool reset(float user_limit, const XemexReading &reading, uint32_t now) {
    if (user_limit != 0.0f || !reading.fresh(now) ||
        *std::max_element(reading.current.begin(), reading.current.end()) >= 1.0f)
      return false;
    *this = XemexChargeGuard{};
    return true;
  }

  size_t stop_count() const { return stop_count_; }

 private:
  void reset_observation_() {
    seen_charging_ = false;
    high_ = false;
    low_ = false;
    starting_ = false;
  }

  std::array<uint32_t, 5> stops_{};
  size_t stop_count_{0};
  bool seen_charging_{false}, high_{false}, low_{false}, starting_{false};
  bool stopping_{false}, retrying_{false};
  bool has_step_{false};
  uint32_t last_step_{0};
  uint32_t high_at_{0}, low_at_{0}, start_at_{0}, stopping_at_{0}, retry_at_{0};
};

}
}