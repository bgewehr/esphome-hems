#pragma once

#include <cstddef>
#include <string>
#include <vector>

extern "C" {
#include "src/spine/model/smart_energy_management_ps_types.h"
}

namespace esphome {
namespace eebus_eg {

struct SempDecodeResult {
  std::vector<std::string> lines;
  size_t alternatives{0};
  size_t sequences{0};
  size_t slots{0};
  size_t values{0};
  bool truncated{false};
};

SempDecodeResult decode_semp_data(const SmartEnergyManagementPsDataType *data);

}  // namespace eebus_eg
}  // namespace esphome
