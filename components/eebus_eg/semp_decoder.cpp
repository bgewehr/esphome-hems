#include "semp_decoder.h"

#include <cstdarg>
#include <cstdio>

namespace esphome {
namespace eebus_eg {
namespace {

constexpr size_t kMaxAlternatives = 32;
constexpr size_t kMaxSequences = 64;
constexpr size_t kMaxSlots = 128;
constexpr size_t kMaxValues = 256;

void append_line(SempDecodeResult &result, const char *format, ...) {
  char buffer[384];
  va_list args;
  va_start(args, format);
  vsnprintf(buffer, sizeof(buffer), format, args);
  va_end(args);
  result.lines.emplace_back(buffer);
}

const char *bool_value(const bool *value) {
  if (value == nullptr)
    return "?";
  return *value ? "true" : "false";
}

const char *sequence_state_name(const PowerSequenceStateType *state) {
  if (state == nullptr)
    return "?";
  switch (*state) {
    case kPowerSequenceStateTypeRunning: return "running";
    case kPowerSequenceStateTypePaused: return "paused";
    case kPowerSequenceStateTypeScheduled: return "scheduled";
    case kPowerSequenceStateTypeScheduledPaused: return "scheduledPaused";
    case kPowerSequenceStateTypePending: return "pending";
    case kPowerSequenceStateTypeInactive: return "inactive";
    case kPowerSequenceStateTypeCompleted: return "completed";
    case kPowerSequenceStateTypeInvalid: return "invalid";
    default: return "unknown";
  }
}

const char *value_type_name(const PowerTimeSlotValueTypeType *type) {
  if (type == nullptr)
    return "?";
  switch (*type) {
    case kPowerTimeSlotValueTypeTypePower: return "power";
    case kPowerTimeSlotValueTypeTypePowerMin: return "powerMin";
    case kPowerTimeSlotValueTypeTypePowerMax: return "powerMax";
    case kPowerTimeSlotValueTypeTypePowerExpectedValue: return "powerExpected";
    case kPowerTimeSlotValueTypeTypePowerStandardDeviation: return "powerStdDev";
    case kPowerTimeSlotValueTypeTypePowerSkewness: return "powerSkewness";
    case kPowerTimeSlotValueTypeTypeEnergy: return "energy";
    case kPowerTimeSlotValueTypeTypeEnergyMin: return "energyMin";
    case kPowerTimeSlotValueTypeTypeEnergyMax: return "energyMax";
    case kPowerTimeSlotValueTypeTypeEnergyExpectedValue: return "energyExpected";
    case kPowerTimeSlotValueTypeTypeEnergyStandardDeviation: return "energyStdDev";
    case kPowerTimeSlotValueTypeTypeEnergySkewness: return "energySkewness";
    default: return "unknown";
  }
}

std::string format_time(const AbsoluteOrRelativeTimeType *time) {
  if (time == nullptr)
    return "?";
  char buffer[128];
  if (time->type == kAbsoluteOrRelativeTimeTypeDateTime) {
    const auto &date = time->date_time.date;
    const auto &clock = time->date_time.time;
    snprintf(buffer, sizeof(buffer), "%04ld-%02ld-%02ldT%02ld:%02ld:%02ld",
             static_cast<long>(date.year), static_cast<long>(date.month), static_cast<long>(date.day),
             static_cast<long>(clock.hour), static_cast<long>(clock.min), static_cast<long>(clock.sec));
    return buffer;
  }
  if (time->type == kAbsoluteOrRelativeTimeTypeDuration) {
    const auto &duration = time->duration;
    snprintf(buffer, sizeof(buffer), "P%ldY%ldM%ldDT%ldH%ldM%ld.%03ldS",
             static_cast<long>(duration.years), static_cast<long>(duration.months),
             static_cast<long>(duration.days), static_cast<long>(duration.hours),
             static_cast<long>(duration.minutes), static_cast<long>(duration.seconds),
             static_cast<long>(duration.milliseconds));
    return buffer;
  }
  snprintf(buffer, sizeof(buffer), "unknown-time-type(%ld)", static_cast<long>(time->type));
  return buffer;
}

std::string format_duration(const DurationType *duration) {
  if (duration == nullptr)
    return "?";
  char buffer[128];
  snprintf(buffer, sizeof(buffer), "P%ldY%ldM%ldDT%ldH%ldM%ld.%03ldS",
           static_cast<long>(duration->years), static_cast<long>(duration->months),
           static_cast<long>(duration->days), static_cast<long>(duration->hours),
           static_cast<long>(duration->minutes), static_cast<long>(duration->seconds),
           static_cast<long>(duration->milliseconds));
  return buffer;
}

std::string format_scaled_number(const ScaledNumberType *value) {
  if (value == nullptr || value->number == nullptr)
    return "?";
  char buffer[96];
  if (value->scale != nullptr)
    snprintf(buffer, sizeof(buffer), "%lld x 10^%d", static_cast<long long>(*value->number),
             static_cast<int>(*value->scale));
  else
    snprintf(buffer, sizeof(buffer), "%lld x 10^?", static_cast<long long>(*value->number));
  return buffer;
}

template<typename T> const char *number_or_unknown(const T *value, char *buffer, size_t size) {
  if (value == nullptr)
    return "?";
  snprintf(buffer, size, "%llu", static_cast<unsigned long long>(*value));
  return buffer;
}

void decode_slot(SempDecodeResult &result, const SmartEnergyManagementPsPowerTimeSlotType *slot,
                 size_t alternative_index, size_t sequence_index, size_t slot_index) {
  if (slot == nullptr) {
    append_line(result, "alt[%u].seq[%u].slot[%u]: null", static_cast<unsigned>(alternative_index),
                static_cast<unsigned>(sequence_index), static_cast<unsigned>(slot_index));
    return;
  }

  result.slots++;
  const auto *schedule = slot->schedule;
  char sequence_id[24];
  char slot_number[24];
  append_line(result,
              "alt[%u].seq[%u].slot[%u]: sequence=%s number=%s active=%s duration=%s period=%s..%s",
              static_cast<unsigned>(alternative_index), static_cast<unsigned>(sequence_index),
              static_cast<unsigned>(slot_index),
              schedule ? number_or_unknown(schedule->sequence_id, sequence_id, sizeof(sequence_id)) : "?",
              schedule ? number_or_unknown(schedule->slot_number, slot_number, sizeof(slot_number)) : "?",
              schedule ? bool_value(schedule->slot_activated) : "?",
              schedule ? format_duration(schedule->default_duration).c_str() : "?",
              schedule && schedule->time_period ? format_time(schedule->time_period->start_time).c_str() : "?",
              schedule && schedule->time_period ? format_time(schedule->time_period->end_time).c_str() : "?");

  const auto *constraints = slot->schedule_constraints;
  if (constraints != nullptr) {
    append_line(result, "alt[%u].seq[%u].slot[%u].constraints: earliest=%s latest=%s min=%s max=%s optional=%s",
                static_cast<unsigned>(alternative_index), static_cast<unsigned>(sequence_index),
                static_cast<unsigned>(slot_index), format_time(constraints->earliest_start_time).c_str(),
                format_time(constraints->latest_end_time).c_str(), format_duration(constraints->min_duration).c_str(),
                format_duration(constraints->max_duration).c_str(), bool_value(constraints->optional_slot));
  }

  const auto *values = slot->value_list;
  if (values == nullptr)
    return;
  size_t value_count = values->value_size;
  if (value_count > kMaxValues - result.values) {
    value_count = kMaxValues - result.values;
    result.truncated = true;
  }
  for (size_t value_index = 0; value_index < value_count; value_index++) {
    const auto *value = values->value != nullptr ? values->value[value_index] : nullptr;
    if (value == nullptr) {
      append_line(result, "alt[%u].seq[%u].slot[%u].value[%u]: null",
                  static_cast<unsigned>(alternative_index), static_cast<unsigned>(sequence_index),
                  static_cast<unsigned>(slot_index), static_cast<unsigned>(value_index));
      continue;
    }
    result.values++;
    char value_sequence_id[24];
    char value_slot_number[24];
    append_line(result, "alt[%u].seq[%u].slot[%u].value[%u]: sequence=%s number=%s type=%s(%ld) value=%s",
                static_cast<unsigned>(alternative_index), static_cast<unsigned>(sequence_index),
                static_cast<unsigned>(slot_index), static_cast<unsigned>(value_index),
                number_or_unknown(value->sequence_id, value_sequence_id, sizeof(value_sequence_id)),
                number_or_unknown(value->slot_number, value_slot_number, sizeof(value_slot_number)),
                value_type_name(value->value_type),
                value->value_type ? static_cast<long>(*value->value_type) : -1L,
                format_scaled_number(value->value).c_str());
  }
}

void decode_sequence(SempDecodeResult &result, const SmartEnergyManagementPsPowerSequenceType *sequence,
                     size_t alternative_index, size_t sequence_index) {
  if (sequence == nullptr) {
    append_line(result, "alt[%u].seq[%u]: null", static_cast<unsigned>(alternative_index),
                static_cast<unsigned>(sequence_index));
    return;
  }

  result.sequences++;
  const auto *description = sequence->description;
  const auto *state = sequence->state;
  const auto *schedule = sequence->schedule;
  const auto *constraints = sequence->schedule_constraints;
  const PowerSequenceIdType *sequence_id = description ? description->sequence_id : nullptr;
  if (sequence_id == nullptr && state != nullptr)
    sequence_id = state->sequence_id;
  if (sequence_id == nullptr && schedule != nullptr)
    sequence_id = schedule->sequence_id;
  char id[24];
  char active_slot[24];
  append_line(result,
              "alt[%u].seq[%u]: id=%s state=%s(%ld) remote=%s active_slot=%s slots=%u power_unit=%ld energy_unit=%ld",
              static_cast<unsigned>(alternative_index), static_cast<unsigned>(sequence_index),
              number_or_unknown(sequence_id, id, sizeof(id)), state ? sequence_state_name(state->state) : "?",
              state && state->state ? static_cast<long>(*state->state) : -1L,
              state ? bool_value(state->sequence_remote_controllable) : "?",
              state ? number_or_unknown(state->active_slot_number, active_slot, sizeof(active_slot)) : "?",
              static_cast<unsigned>(sequence->power_time_slot_size),
              description && description->power_unit ? static_cast<long>(*description->power_unit) : -1L,
              description && description->energy_unit ? static_cast<long>(*description->energy_unit) : -1L);

  if (schedule != nullptr) {
    append_line(result, "alt[%u].seq[%u].schedule: start=%s end=%s", static_cast<unsigned>(alternative_index),
                static_cast<unsigned>(sequence_index), format_time(schedule->start_time).c_str(),
                format_time(schedule->end_time).c_str());
  }
  if (constraints != nullptr) {
    append_line(result,
                "alt[%u].seq[%u].constraints: earliest_start=%s latest_start=%s earliest_end=%s latest_end=%s optional=%s",
                static_cast<unsigned>(alternative_index), static_cast<unsigned>(sequence_index),
                format_time(constraints->earliest_start_time).c_str(),
                format_time(constraints->latest_start_time).c_str(),
                format_time(constraints->earliest_end_time).c_str(),
                format_time(constraints->latest_end_time).c_str(), bool_value(constraints->optional_sequence));
  }
  if (sequence->schedule_preference != nullptr) {
    append_line(result, "alt[%u].seq[%u].preference: greenest=%s cheapest=%s",
                static_cast<unsigned>(alternative_index), static_cast<unsigned>(sequence_index),
                bool_value(sequence->schedule_preference->greenest),
                bool_value(sequence->schedule_preference->cheapest));
  }
  if (sequence->operating_constraints_interrupt != nullptr ||
      sequence->operating_constraints_duration != nullptr ||
      sequence->operating_constraints_resume_implication != nullptr) {
    append_line(result, "alt[%u].seq[%u].operating_constraints: interrupt=%s duration=%s resume=%s",
                static_cast<unsigned>(alternative_index), static_cast<unsigned>(sequence_index),
                sequence->operating_constraints_interrupt ? "present" : "absent",
                sequence->operating_constraints_duration ? "present" : "absent",
                sequence->operating_constraints_resume_implication ? "present" : "absent");
  }

  size_t slot_count = sequence->power_time_slot_size;
  if (slot_count > kMaxSlots - result.slots) {
    slot_count = kMaxSlots - result.slots;
    result.truncated = true;
  }
  for (size_t slot_index = 0; slot_index < slot_count; slot_index++) {
    const auto *slot = sequence->power_time_slot != nullptr ? sequence->power_time_slot[slot_index] : nullptr;
    decode_slot(result, slot, alternative_index, sequence_index, slot_index);
  }
}

}  // namespace

SempDecodeResult decode_semp_data(const SmartEnergyManagementPsDataType *data) {
  SempDecodeResult result;
  if (data == nullptr) {
    append_line(result, "SEMP data: null");
    return result;
  }

  const auto *node = data->node_schedule_information;
  char alternatives_count[24];
  char sequences_max[24];
  append_line(result, "node: remote=%s single_slot_only=%s alternatives=%s sequences_max=%s reselection=%s",
              node ? bool_value(node->node_remote_controllable) : "?",
              node ? bool_value(node->supports_single_slot_scheduling_only) : "?",
              node ? number_or_unknown(node->alternatives_count, alternatives_count, sizeof(alternatives_count)) : "?",
              node ? number_or_unknown(node->total_sequences_count_max, sequences_max, sizeof(sequences_max)) : "?",
              node ? bool_value(node->supports_reselection) : "?");

  size_t alternative_count = data->alternatives_size;
  if (alternative_count > kMaxAlternatives) {
    alternative_count = kMaxAlternatives;
    result.truncated = true;
  }
  for (size_t alternative_index = 0; alternative_index < alternative_count; alternative_index++) {
    const auto *alternative = data->alternatives != nullptr ? data->alternatives[alternative_index] : nullptr;
    if (alternative == nullptr) {
      append_line(result, "alt[%u]: null", static_cast<unsigned>(alternative_index));
      continue;
    }
    result.alternatives++;
    const auto *relation = alternative->relation;
    char alternatives_id[24];
    append_line(result, "alt[%u]: id=%s related_sequences=%u sequences=%u",
                static_cast<unsigned>(alternative_index),
                relation ? number_or_unknown(relation->alternatives_id, alternatives_id, sizeof(alternatives_id)) : "?",
                relation ? static_cast<unsigned>(relation->sequence_id_size) : 0U,
                static_cast<unsigned>(alternative->power_sequence_size));

    size_t sequence_count = alternative->power_sequence_size;
    if (sequence_count > kMaxSequences - result.sequences) {
      sequence_count = kMaxSequences - result.sequences;
      result.truncated = true;
    }
    for (size_t sequence_index = 0; sequence_index < sequence_count; sequence_index++) {
      const auto *sequence = alternative->power_sequence != nullptr ? alternative->power_sequence[sequence_index] : nullptr;
      decode_sequence(result, sequence, alternative_index, sequence_index);
    }
  }

  append_line(result, "summary: alternatives=%u sequences=%u slots=%u values=%u truncated=%s",
              static_cast<unsigned>(result.alternatives), static_cast<unsigned>(result.sequences),
              static_cast<unsigned>(result.slots), static_cast<unsigned>(result.values),
              result.truncated ? "true" : "false");
  return result;
}

}  // namespace eebus_eg
}  // namespace esphome
