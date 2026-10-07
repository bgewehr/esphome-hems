#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>

namespace esphome {
namespace power_distribution {

struct SolarSample {
  float watts{NAN};
  uint32_t updated_ms{0};

  bool valid(uint32_t now, float maximum = 100000.0f) const {
    return updated_ms != 0 && now - updated_ms <= 10000 &&
           std::isfinite(watts) && watts >= 0.0f && watts <= maximum;
  }
};

struct SolarOptions {
  bool battery_first{false};
  bool solar_only{false};
  bool low_solar_grid{false};
};

struct SolarInputs {
  SolarSample pv_generation;
  SolarSample pv_ac;
  SolarSample non_ev_load_ac;
  SolarSample battery_charge_ac;
  SolarSample charge_capacity_ac;
};

enum class SolarReason { UNRESTRICTED, INVALID_DATA, WAITING_PV, PV_SUPPORT, LOW_SOLAR };

inline const char *solar_reason_text(SolarReason reason) {
  switch (reason) {
    case SolarReason::UNRESTRICTED: return "Unrestricted";
    case SolarReason::INVALID_DATA: return "Invalid data";
    case SolarReason::WAITING_PV: return "PV pause";
    case SolarReason::PV_SUPPORT: return "PV support";
    case SolarReason::LOW_SOLAR: return "Low solar grid permission";
  }
  return "Invalid data";
}

struct SolarDecision {
  bool valid{false};
  bool ev_permitted{false};
  float battery_target_ac_w{0.0f};
  float ev_support_ac_w{0.0f};
  float budget_charge_reservation_ac_w{NAN};
  SolarReason reason{SolarReason::INVALID_DATA};
};

inline float solar_outwrte(float target_dc_w, float reference_w) {
  if (!std::isfinite(target_dc_w) || target_dc_w < 0.0f ||
      !std::isfinite(reference_w) || reference_w <= 0.0f || reference_w > 100000.0f)
    return NAN;
  return -100.0f * std::min(target_dc_w / reference_w, 1.0f);
}

inline float storage_ev_limit(float requested, bool battery_allowed, bool writer_fault,
                              uint32_t verified_ms, uint32_t now) {
  if (!battery_allowed && (writer_fault || verified_ms == 0 || now - verified_ms > 10000))
    return 0.0f;
  return requested;
}

inline bool storage_target_valid(float requested_pct, bool priority_enabled,
                                 uint32_t updated_ms, uint32_t now, float latest_pct) {
  return requested_pct >= 0.0f ||
         (priority_enabled && updated_ms != 0 && now - updated_ms <= 10000 &&
          std::isfinite(latest_pct) && latest_pct <= 0.0f);
}

inline float solar_ev_limit(float requested, bool solar_only, bool permitted,
                            uint32_t updated_ms, uint32_t now) {
  if (solar_only && (!permitted || updated_ms == 0 || now - updated_ms > 10000))
    return 0.0f;
  return requested;
}

inline float solar_charge_limit(float requested, bool solar_only, bool limited, float minimum_w,
                                bool low_solar_permission = false) {
  if (!solar_only || !limited || low_solar_permission) return requested;
  if (!std::isfinite(minimum_w) || minimum_w <= 0.0f) return 0.0f;
  return std::min(requested, minimum_w);
}

inline float solar_grid_budget(float limit, float pv_ac, float house_ac,
                               float battery_discharge_ac, float charge_reservation_ac) {
  if (!std::isfinite(limit) || limit < 0.0f || !std::isfinite(pv_ac) || pv_ac < 0.0f ||
      !std::isfinite(house_ac) || house_ac < 0.0f ||
      !std::isfinite(battery_discharge_ac) || battery_discharge_ac < 0.0f ||
      !std::isfinite(charge_reservation_ac) || charge_reservation_ac < 0.0f)
    return 0.0f;
  const float generation = pv_ac + battery_discharge_ac;
  const float grid_charge = std::max(0.0f, charge_reservation_ac - generation);
  const float surplus = std::max(0.0f, generation - charge_reservation_ac - house_ac);
  return std::max(0.0f, limit - grid_charge + 0.8f * surplus);
}

class SolarPolicy {
 public:
  SolarDecision step(const SolarInputs &inputs, const SolarOptions &options, uint32_t now) {
    if ((stepped_ && now - last_step_ > 10000) || !same_options_(options))
      reset_();
    stepped_ = true;
    last_step_ = now;
    options_ = options;

    SolarDecision result;
    result.ev_permitted = !options.solar_only;
    result.reason = options.solar_only ? SolarReason::INVALID_DATA : SolarReason::UNRESTRICTED;
    if (!inputs.pv_generation.valid(now, 25000.0f) || !inputs.pv_ac.valid(now, 25000.0f) ||
        !inputs.non_ev_load_ac.valid(now) ||
        !inputs.battery_charge_ac.valid(now, 25000.0f)) {
      reset_();
      return result;
    }

    result.valid = true;
    const float available = std::max(0.0f, inputs.pv_ac.watts - inputs.non_ev_load_ac.watts);
    if (options.battery_first) {
      result.battery_target_ac_w = inputs.charge_capacity_ac.valid(now, 25000.0f)
                                       ? std::min(available, inputs.charge_capacity_ac.watts)
                                       : available;
    }
    result.budget_charge_reservation_ac_w = std::max(inputs.battery_charge_ac.watts,
                                                    result.battery_target_ac_w);
    const float priority_reservation = options.battery_first
                                          ? result.budget_charge_reservation_ac_w : 0.0f;
    result.ev_support_ac_w = std::max(0.0f, available - priority_reservation);

    if (!options.solar_only) {
      reset_();
      return result;
    }

    constexpr float support_threshold_w = 500.0f;
    const bool pv_support = result.ev_support_ac_w > support_threshold_w;
    const bool low_support = result.ev_support_ac_w < support_threshold_w;
    if (!pv_allowed_) {
      if (pv_timer_.held(pv_support, now, 60000)) {
        pv_allowed_ = true;
        pv_timer_.reset();
      }
    } else if (pv_timer_.held(low_support, now, 120000)) {
      pv_allowed_ = false;
      pv_timer_.reset();
    }

    if (!options.low_solar_grid) {
      low_solar_ = false;
      low_timer_.reset();
    } else if (!low_solar_) {
      if (low_timer_.held(low_support, now, 600000)) {
        low_solar_ = true;
        low_timer_.reset();
      }
    } else if (low_timer_.held(pv_support, now, 300000)) {
      low_solar_ = false;
      low_timer_.reset();
    }

    result.ev_permitted = pv_allowed_ || low_solar_;
    result.reason = pv_allowed_ ? SolarReason::PV_SUPPORT
                               : low_solar_ ? SolarReason::LOW_SOLAR : SolarReason::WAITING_PV;
    return result;
  }

 private:
  struct HoldTimer {
    bool active{false};
    uint32_t since{0};

    bool held(bool condition, uint32_t now, uint32_t duration) {
      if (!condition) {
        reset();
        return false;
      }
      if (!active) {
        active = true;
        since = now;
      }
      return now - since >= duration;
    }

    void reset() { active = false; }
  };

  bool same_options_(const SolarOptions &options) const {
    return options.battery_first == options_.battery_first &&
           options.solar_only == options_.solar_only &&
           options.low_solar_grid == options_.low_solar_grid;
  }

  void reset_() {
    pv_allowed_ = false;
    low_solar_ = false;
    pv_timer_.reset();
    low_timer_.reset();
  }

  SolarOptions options_;
  HoldTimer pv_timer_;
  HoldTimer low_timer_;
  bool pv_allowed_{false};
  bool low_solar_{false};
  bool stepped_{false};
  uint32_t last_step_{0};
};

}
}