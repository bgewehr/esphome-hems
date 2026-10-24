#include "components/eebus_eg/semp_decoder.h"
#include "components/eebus_eg/semp_diagnostics.h"

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

void test_use_case_activation() {
  esphome::eebus_eg::SempUseCaseGate gate;
  const int hpcf = kUseCaseNameTypeOptimizationOfSelfConsumptionByHeatPumpCompressorFlexibility;
  require(!gate.available(), "SEMP starts inactive without HPCF discovery");
  require(!gate.update(kUseCaseActorTypeCEM, hpcf, true), "wrong remote actor cannot enable SEMP");
  require(!gate.update(kUseCaseActorTypeCompressor, kUseCaseNameTypeLimitationOfPowerConsumption, true),
          "LPC availability cannot enable SEMP");
  require(gate.update(kUseCaseActorTypeCompressor, hpcf, true), "HPCF addition enables SEMP");
  require(gate.available(), "SEMP is active while HPCF is offered");
  const auto generation = gate.generation();
  require(!gate.update(kUseCaseActorTypeCompressor, hpcf, true), "unchanged announcement does not repeat setup");
  require(gate.generation() == generation, "duplicate announcement preserves pending request generation");
  require(!gate.update(kUseCaseActorTypeCEM, hpcf, false), "unrelated removal leaves SEMP active");
  require(gate.available(), "unrelated removal preserves HPCF availability");
  require(gate.update(kUseCaseActorTypeCompressor, hpcf, false), "HPCF withdrawal disables SEMP");
  require(!gate.available(), "withdrawn HPCF prevents reads and registration");
  require(gate.generation() != generation, "withdrawal invalidates old table confirmations");
  gate.update(kUseCaseActorTypeCompressor, hpcf, true);
  gate.reset();
  require(!gate.available(), "disconnect clears stale HPCF availability");
  require(gate.update(kUseCaseActorTypeCompressor, hpcf, true), "reconnect discovery permits setup again");
  esphome::eebus_eg::SempRegistrationRetry retry;
  require(!retry.due(1, 5000, false, false), "no retry before initial setup");
  retry.record_attempt(1, 1000);
  require(!retry.due(1, 5999, false, false), "retry waits five seconds");
  require(retry.due(1, 6000, false, false), "absent registration triggers bounded retry");
  require(!retry.due(1, 6000, true, false), "pending callbacks are not overlapped");
  require(!retry.due(1, 6000, false, true), "confirmed registrations stop retries");
  require(!retry.due(2, 6000, false, false), "capability change invalidates old retry");
  retry.record_attempt(1, 6000);
  require(retry.due(1, 11000, false, false), "second retry is permitted");
  retry.record_attempt(1, 11000);
  require(!retry.due(1, 16000, false, false), "three attempts exhaust automatic retries");
  retry.record_attempt(1, 16000);
  require(!retry.due(1, 21000, false, false), "manual reads do not restart endless retries");
  retry.record_attempt(2, UINT32_MAX - 1000);
  require(retry.attempts() == 1, "new capability generation resets attempt budget");
  require(!retry.due(2, 3998, false, false), "wraparound still observes retry delay");
  require(retry.due(2, 3999, false, false), "retry timer handles millis wraparound");
}

void test_message_classifiers_and_registration_matching() {
  using namespace esphome::eebus_eg;
  SempNodeLogFilter node_log;
  require(node_log.should_log("node: remote=true", 1), "first node properties are logged");
  require(!node_log.should_log("node: remote=true", 1), "unchanged node properties are suppressed");
  require(node_log.should_log("summary: alternatives=0", 1), "message summaries remain visible");
  require(node_log.should_log("summary: alternatives=0", 1), "every response keeps its summary");
  require(node_log.should_log("node: remote=false", 1), "changed node properties are logged");
  require(node_log.should_log("node: remote=false", 2), "new connection logs node properties again");
  const CommandClassifierType notify = kCommandClassifierTypeNotify;
  const CommandClassifierType reply = kCommandClassifierTypeReply;
  require(std::string(semp_command_name(&notify)) == "notify", "notify is distinct from reply");
  require(std::string(semp_command_name(&reply)) == "reply", "reply is identified");
  require(std::string(semp_command_name(nullptr)) == "unknown", "missing classifier stays unknown");

  const uint32_t entity_id = 2;
  const uint32_t *entity[] = {&entity_id};
  const uint32_t feature_id = 7;
  FeatureAddressType client{};
  client.device = "client";
  client.entity = entity;
  client.entity_size = 1;
  client.feature = &feature_id;
  FeatureAddressType server = client;
  server.device = "server";
  SubscriptionManagementEntryDataType subscription{};
  subscription.client_address = &client;
  subscription.server_address = &server;
  const SubscriptionManagementEntryDataType *subscriptions[] = {nullptr, &subscription};
  require(semp_registration_present(subscriptions, 2, &client, &server), "exact subscription pair matches");
  require(!semp_registration_present(subscriptions, 2, &server, &client), "reversed pair does not match");
  FeatureAddressType other = server;
  other.device = "other";
  require(!semp_registration_present(subscriptions, 2, &client, &other), "foreign peer does not match");
  other = server;
  other.feature = &entity_id;
  require(!semp_registration_present(subscriptions, 2, &client, &other), "other feature does not match");
  other = server;
  other.device = nullptr;
  require(!semp_address_equal(&other, &server), "incomplete address cannot prove registration");
    require(std::string(semp_address_match_reason(&other, &server)) == "missing-device",
      "relative address is distinguished from a different device");
        require(semp_address_description(&other) == "device=<relative-unresolved> entity=[2] feature=7",
          "relative address without context is explicit");
        require(semp_address_description(&other, "server") == "device=server (relative) entity=[2] feature=7",
          "relative address names its table owner");
        require(semp_address_description(&server, "other") == "device=server entity=[2] feature=7",
          "explicit device is never replaced by context");
        require(semp_address_description(&other, "") == "device=<relative-unresolved> entity=[2] feature=7",
          "empty context cannot resolve relative device");
        other.device = "";
        require(semp_address_description(&other, "server") == "device=<empty> entity=[2] feature=7",
          "explicit empty device is not relative");
    require(semp_address_description(nullptr) == "null", "null address is printable");
    require(std::string(semp_address_match_reason(nullptr, &server)) == "missing-address", "null match reason");
    other = server;
    other.device = "other";
    require(std::string(semp_address_match_reason(&other, &server)) == "different-device", "foreign device reason");
    other = server;
    other.feature = &entity_id;
    require(std::string(semp_address_match_reason(&other, &server)) == "different-feature", "feature mismatch reason");
    other = server;
    const std::string long_device(200, 'x');
    other.device = long_device.c_str();
    require(semp_address_description(&other).size() < 150, "address output is bounded");
    require(semp_address_description(&other).find("truncated") != std::string::npos, "address truncation is explicit");
    other.device = "line\nbreak";
    require(semp_address_description(&other).find('\n') == std::string::npos, "device cannot inject log lines");
  BindingManagementEntryDataType binding{};
  binding.client_address = &client;
  binding.server_address = &server;
  const BindingManagementEntryDataType *bindings[] = {&binding};
  require(semp_registration_present(bindings, 1, &client, &server), "exact binding pair matches");
  require(!semp_registration_present(bindings, 0, &client, &server), "empty table does not confirm binding");
    FeatureAddressType relative_server = server;
    relative_server.device = nullptr;
    subscription.server_address = &relative_server;
    binding.server_address = &relative_server;
    require(semp_registration_present(subscriptions, 2, &client, &server, "server"),
      "relative subscription server resolves against table owner");
    require(semp_registration_present(bindings, 1, &client, &server, "server"),
      "relative binding server resolves against table owner");
    require(std::string(semp_address_match_reason(&relative_server, &server, "server")) == "match-relative-device",
      "relative match remains visible in diagnostics");
    require(!semp_registration_present(bindings, 1, &client, &server), "relative address needs table context");
    require(!semp_registration_present(bindings, 1, &client, &server, "other"), "foreign table cannot confirm");
    require(!semp_registration_present(bindings, 1, &client, &server, ""), "empty context cannot confirm");
    relative_server.device = "other";
    require(!semp_registration_present(bindings, 1, &client, &server, "server"), "explicit foreign device is not replaced");
    relative_server.device = "";
    require(!semp_registration_present(bindings, 1, &client, &server, "server"), "empty device is not an omitted device");
    relative_server.device = nullptr;
    relative_server.feature = &entity_id;
    require(!semp_registration_present(bindings, 1, &client, &server, "server"), "relative wrong feature is rejected");
    relative_server.feature = nullptr;
    require(!semp_registration_present(bindings, 1, &client, &server, "server"), "missing feature is rejected");
    relative_server.feature = &feature_id;
    const uint32_t *wrong_entity[] = {&feature_id};
    relative_server.entity = wrong_entity;
    require(!semp_registration_present(bindings, 1, &client, &server, "server"), "relative wrong entity is rejected");
    relative_server.entity = nullptr;
    require(!semp_registration_present(bindings, 1, &client, &server, "server"), "missing entity is rejected");
    const uint32_t *nested_entity[] = {&entity_id, &feature_id};
    server.entity = nested_entity;
    server.entity_size = 2;
    relative_server.entity = entity;
    require(!semp_registration_present(bindings, 1, &client, &server, "server"), "entity prefix is not a match");
    relative_server.entity = nested_entity;
    relative_server.entity_size = 2;
    require(semp_registration_present(bindings, 1, &client, &server, "server"), "full nested entity matches");
    const uint32_t *incomplete_entity[] = {&entity_id, nullptr};
    relative_server.entity = incomplete_entity;
    require(!semp_registration_present(bindings, 1, &client, &server, "server"), "missing entity id is rejected");
    relative_server.entity = nested_entity;
    FeatureAddressType relative_client = client;
    relative_client.device = nullptr;
    binding.client_address = &relative_client;
    require(!semp_registration_present(bindings, 1, &client, &server, "server"),
      "relative client belongs to table owner, not implicitly to local client");
    binding.client_address = &client;
    require(!semp_registration_present(bindings, 1, &server, &client, "server"), "relative reversed pair is rejected");
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
  test_use_case_activation();
  test_message_classifiers_and_registration_matching();
  test_null_and_empty_payloads();
  test_partial_payload();
  test_complete_and_unknown_payload();
  test_bounded_payload();
  std::cout << "SEMP decoder tests passed\n";
  return EXIT_SUCCESS;
}