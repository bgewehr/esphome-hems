#pragma once

#include <cstddef>
#include <cstring>
#include <atomic>
#include <string>
#include <string_view>

extern "C" {
#include "src/spine/model/command_frame_types.h"
#include "src/spine/model/node_management_types.h"
}

namespace esphome {
namespace eebus_eg {

class SempUseCaseGate {
 public:
  bool update(int actor, int use_case, bool available) {
    if (actor != kUseCaseActorTypeCompressor ||
        use_case != kUseCaseNameTypeOptimizationOfSelfConsumptionByHeatPumpCompressorFlexibility)
      return false;
    if (available_.exchange(available) == available) return false;
    ++generation_;
    return true;
  }
  bool available() const { return available_.load(); }
  uint32_t generation() const { return generation_.load(); }
  void reset() { available_ = false; ++generation_; }

 private:
  std::atomic<bool> available_{false};
  std::atomic<uint32_t> generation_{0};
};

class SempRegistrationRetry {
 public:
  void record_attempt(uint32_t generation, uint32_t now) {
    if (generation_ != generation) attempts_ = 0;
    generation_ = generation;
    last_attempt_ms_ = now;
    if (attempts_ < 3) ++attempts_;
  }
  bool due(uint32_t generation, uint32_t now, bool pending, bool confirmed) const {
    return generation == generation_ && attempts_ > 0 && attempts_ < 3 &&
           !pending && !confirmed && now - last_attempt_ms_ >= 5000;
  }
  uint8_t attempts() const { return attempts_; }

 private:
  uint32_t generation_{0};
  uint32_t last_attempt_ms_{0};
  uint8_t attempts_{0};
};

class SempNodeLogFilter {
 public:
  bool should_log(const std::string &line, uint32_t generation) {
    if (line.compare(0, 5, "node:") != 0) return true;
    if (generation == generation_ && line == last_node_) return false;
    generation_ = generation;
    last_node_ = line;
    return true;
  }

 private:
  uint32_t generation_{0};
  std::string last_node_;
};

inline const char *semp_command_name(const CommandClassifierType *command) {
  if (command == nullptr) return "unknown";
  switch (*command) {
    case kCommandClassifierTypeRead: return "read";
    case kCommandClassifierTypeReply: return "reply";
    case kCommandClassifierTypeNotify: return "notify";
    case kCommandClassifierTypeWrite: return "write";
    case kCommandClassifierTypeCall: return "call";
    case kCommandClassifierTypeResult: return "result";
    default: return "unknown";
  }
}

inline const char *semp_address_match_reason(const FeatureAddressType *actual, const FeatureAddressType *expected,
                                             const char *relative_device = nullptr) {
  if (actual == nullptr || expected == nullptr) return "missing-address";
  const char *actual_device = actual->device != nullptr ? actual->device : relative_device;
  if (actual_device == nullptr || expected->device == nullptr) return "missing-device";
  if (*actual_device == '\0' || *expected->device == '\0') return "missing-device";
  if (actual->feature == nullptr || expected->feature == nullptr) return "missing-feature";
  if (actual->entity == nullptr || expected->entity == nullptr || actual->entity_size == 0 ||
      expected->entity_size == 0) return "missing-entity";
  if (std::strcmp(actual_device, expected->device) != 0) return "different-device";
  if (*actual->feature != *expected->feature) return "different-feature";
  if (actual->entity_size != expected->entity_size) return "different-entity-length";
  for (size_t index = 0; index < actual->entity_size; ++index) {
    if (actual->entity[index] == nullptr || expected->entity[index] == nullptr) return "missing-entity-id";
    if (*actual->entity[index] != *expected->entity[index]) return "different-entity";
  }
  return actual->device == nullptr ? "match-relative-device" : "match";
}

inline bool semp_address_equal(const FeatureAddressType *actual, const FeatureAddressType *expected,
                               const char *relative_device = nullptr) {
  const std::string_view reason = semp_address_match_reason(actual, expected, relative_device);
  return reason == "match" || reason == "match-relative-device";
}

inline std::string semp_address_description(const FeatureAddressType *address, const char *relative_device = nullptr) {
  if (address == nullptr) return "null";
  std::string result = "device=";
  const char *device = address->device != nullptr ? address->device : relative_device;
  if (device == nullptr || *device == '\0') {
    result += address->device == nullptr ? "<relative-unresolved>" : "<empty>";
  } else {
    size_t index = 0;
    for (; index < 80 && device[index] != '\0'; ++index) {
      const unsigned char character = device[index];
      result += character >= 32 && character < 127 ? static_cast<char>(character) : '?';
    }
    if (device[index] != '\0') result += "...(truncated)";
    if (address->device == nullptr) result += " (relative)";
  }
  result += " entity=[";
  if (address->entity == nullptr) {
    result += "?";
  } else {
    for (size_t index = 0; index < address->entity_size && index < 8; ++index) {
      if (index != 0) result += ",";
      result += address->entity[index] ? std::to_string(*address->entity[index]) : "?";
    }
    if (address->entity_size > 8) result += ",...(truncated)";
  }
  result += "] feature=";
  result += address->feature ? std::to_string(*address->feature) : "?";
  return result;
}

template<typename Entry>
bool semp_registration_present(const Entry *const *entries, size_t count,
                               const FeatureAddressType *client, const FeatureAddressType *server,
                               const char *table_device = nullptr) {
  if (entries == nullptr) return false;
  for (size_t index = 0; index < count; ++index) {
    const auto *entry = entries[index];
    if (entry != nullptr && semp_address_equal(entry->client_address, client, table_device) &&
      semp_address_equal(entry->server_address, server, table_device))
      return true;
  }
  return false;
}

}  // namespace eebus_eg
}  // namespace esphome