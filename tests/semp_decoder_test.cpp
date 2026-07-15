#include "components/eebus_eg/semp_decoder.h"

#include <cstdlib>
#include <iostream>
#include <string>

namespace {

using esphome::eebus_eg::SempDecodeResult;
using esphome::eebus_eg::decode_semp_data;

bool contains(const SempDecodeResult &result, const std::string &expected) {
  for (const auto &line : result.lines) {
    if (line.find(expected) != std::string::npos)
      return true;
  }
  return false;
}

void require(bool condition, const char *message) {
  if (condition)
    return;
  std::cerr << "FAILED: " << message << '\n';
  std::exit(EXIT_FAILURE);
}

void test_null_and_empty_payloads() {
  const auto null_result = decode_semp_data(nullptr);
  require(null_result.lines.size() == 1, "null payload emits one diagnostic");
  require(contains(null_result, "SEMP data: null"), "null payload is identified");

  const SmartEnergyManagementPsDataType empty{};
  const auto empty_result = decode_semp_data(&empty);
  require(empty_result.alternatives == 0, "empty payload has no alternatives");
  require(contains(empty_result, "node: remote=?"), "missing node fields remain unknown");
  require(contains(empty_result, "summary: alternatives=0 sequences=0 slots=0 values=0 truncated=false"),
          "empty payload has a complete summary");
}

void test_partial_payload() {
  SmartEnergyManagementPsDataType partial{};
  partial.alternatives_size = 1;

  const auto result = decode_semp_data(&partial);
  require(result.alternatives == 0, "null alternative array is not counted");
  require(contains(result, "alt[0]: null"), "null alternative is diagnosed");
  require(!result.truncated, "small partial payload is not truncated");
}

void test_complete_and_unknown_payload() {
  const bool enabled = true;
  const bool disabled = false;
  const uint32_t alternatives_count = 1;
  const uint32_t sequences_max = 1;
  const PowerSequenceNodeScheduleInformationDataType node{
      &enabled, &disabled, &alternatives_count, &sequences_max, &enabled};

  const AlternativesIdType alternative_id = 7;
  const PowerSequenceIdType sequence_id = 42;
  const PowerSequenceIdType *related_sequence_ids[] = {&sequence_id};
  const SmartEnergyManagementPsAlternativesRelationType relation{
      &alternative_id, related_sequence_ids, 1};

  PowerSequenceDescriptionDataType description{};
  description.sequence_id = &sequence_id;

  const PowerSequenceStateType unknown_state = 99;
  const PowerTimeSlotNumberType slot_number = 3;
  PowerSequenceStateDataType state{};
  state.sequence_id = &sequence_id;
  state.state = &unknown_state;
  state.active_slot_number = &slot_number;
  state.sequence_remote_controllable = &enabled;

  const PowerTimeSlotValueTypeType unknown_value_type = 99;
  const NumberType raw_number = 2300;
  const ScaleType scale = -1;
  const ScaledNumberType scaled_value{&raw_number, &scale};
  const PowerTimeSlotValueDataType value{
      &sequence_id, &slot_number, &unknown_value_type, &scaled_value};
  const PowerTimeSlotValueDataType *values[] = {&value};
  const SmartEnergyManagementPsPowerTimeSlotValueListType value_list{values, 1};

  PowerTimeSlotScheduleDataType slot_schedule{};
  slot_schedule.sequence_id = &sequence_id;
  slot_schedule.slot_number = &slot_number;
  slot_schedule.slot_activated = &enabled;

  SmartEnergyManagementPsPowerTimeSlotType slot{};
  slot.schedule = &slot_schedule;
  slot.value_list = &value_list;
  SmartEnergyManagementPsPowerTimeSlotType *slots[] = {&slot};

  SmartEnergyManagementPsPowerSequenceType sequence{};
  sequence.description = &description;
  sequence.state = &state;
  sequence.power_time_slot = slots;
  sequence.power_time_slot_size = 1;
  const SmartEnergyManagementPsPowerSequenceType *sequences[] = {&sequence};

  const SmartEnergyManagementPsAlternativesType alternative{&relation, sequences, 1};
  const SmartEnergyManagementPsAlternativesType *alternatives[] = {&alternative};
  const SmartEnergyManagementPsDataType data{&node, alternatives, 1};

  const auto result = decode_semp_data(&data);
  require(result.alternatives == 1, "complete payload counts alternatives");
  require(result.sequences == 1, "complete payload counts sequences");
  require(result.slots == 1, "complete payload counts slots");
  require(result.values == 1, "complete payload counts values");
  require(contains(result, "state=unknown(99)"), "unknown sequence state is retained numerically");
  require(contains(result, "type=unknown(99)"), "unknown value type is retained numerically");
  require(contains(result, "value=2300 x 10^-1"), "scaled raw value is preserved");
  require(!contains(result, "SKI"), "decoder output contains no device identifier");
  require(!contains(result, "serial"), "decoder output contains no serial number");
}

void test_bounded_payload() {
  const SmartEnergyManagementPsAlternativesType *alternatives[33]{};
  const SmartEnergyManagementPsDataType data{nullptr, alternatives, 33};

  const auto result = decode_semp_data(&data);
  require(result.truncated, "oversized alternatives list is marked truncated");
  require(result.alternatives == 0, "null alternatives are not counted");
  require(contains(result, "summary: alternatives=0 sequences=0 slots=0 values=0 truncated=true"),
          "truncation is visible in summary");
}

}  // namespace

int main() {
  test_null_and_empty_payloads();
  test_partial_payload();
  test_complete_and_unknown_payload();
  test_bounded_payload();
  std::cout << "SEMP decoder tests passed\n";
  return EXIT_SUCCESS;
}