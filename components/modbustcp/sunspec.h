#pragma once

#include <cmath>

namespace esphome::modbustcp {

constexpr int SUNSPEC_SCALE_UNKNOWN = 100;

inline int sunspec_scale_factor(float raw) {
  return std::isfinite(raw) && raw >= -10.0f && raw <= 10.0f && std::floor(raw) == raw
             ? static_cast<int>(raw)
             : SUNSPEC_SCALE_UNKNOWN;
}

inline float sunspec_scaled_value(float raw, int scale, bool signed_word,
                                 float minimum = -INFINITY, float maximum = INFINITY) {
  if (scale < -10 || scale > 10 || !std::isfinite(raw) || std::floor(raw) != raw ||
      raw < (signed_word ? -32767.0f : 0.0f) || raw > (signed_word ? 32767.0f : 65534.0f))
    return NAN;
  const float value = raw * std::pow(10.0f, static_cast<float>(scale));
  return std::isfinite(value) && value >= minimum && value <= maximum ? value : NAN;
}

}