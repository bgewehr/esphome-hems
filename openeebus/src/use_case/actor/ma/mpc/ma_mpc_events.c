/*
 * Copyright 2025 NIBE AB
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
/**
 * @file
 * @brief MA MPC events handling implementation
 *
 * bg-patch: trigger Subscribe/RequestDescriptions on kEventTypeEntityChange
 * (entity discovery, like eebus-go's IsEntityConnected) instead of on
 * kEventTypeUseCaseChange (use case data, later in session).  The K40RF goes
 * to state=39 when it receives Subscribe commands after the use-case-data
 * phase; sending them during entity-discovery matches the Go bridge timing and
 * avoids that problem.
 */

#include "src/spine/events/events.h"
#include "src/spine/api/events.h"
#include "src/use_case/actor/ma/ma_events.h"
#include "src/use_case/actor/ma/mpc/ma_mpc_internal.h"
#include "src/use_case/actor/ma/mpc/ma_mpc_measurement.h"
#include "src/use_case/specialization/electrical_connection/electrical_connection_client.h"
#include "src/use_case/specialization/measurement/measurement_client.h"

static void OnEntityConnected(MaMpcUseCase* self, const EventPayload* payload);
static void OnEntityDisconnected(const MaMpcUseCase* self, const EventPayload* payload);
static void OnMeasurementDataUpdate(MaMpcUseCase* self, const EventPayload* payload);
static void OnDataChange(MaMpcUseCase* self, const EventPayload* payload);

/* Triggered by kEventTypeEntityChange + kElementChangeAdd (after DetailedDiscovery).
 * Does NOT check use-case compatibility — entity-type guard in MaMpcHandleEvent suffices,
 * matching eebus-go which also skips the use-case check in its IsEntityConnected path. */
static void OnEntityConnected(MaMpcUseCase* self, const EventPayload* payload) {
  EntityRemoteObject* const entity = payload->entity;

  MaOnEntityAddedHandleElectricalConnection(USE_CASE(self), entity);
  MaOnEntityAddedHandleMeasurement(USE_CASE(self), entity);

  if (self->ma_mpc_listener != NULL) {
    const EntityAddressType* const entity_addr = ENTITY_GET_ADDRESS(ENTITY_OBJECT(entity));
    MA_MPC_LISTENER_ON_REMOTE_ENTITY_CONNECT(self->ma_mpc_listener, entity_addr);
  }
}

static void OnEntityDisconnected(const MaMpcUseCase* self, const EventPayload* payload) {
  if (self->ma_mpc_listener != NULL) {
    const EntityAddressType* const entity_addr = ENTITY_GET_ADDRESS(ENTITY_OBJECT(payload->entity));
    MA_MPC_LISTENER_ON_REMOTE_ENTITY_DISCONNECT(self->ma_mpc_listener, entity_addr);
  }
}

static void OnMeasurementDataUpdate(MaMpcUseCase* self, const EventPayload* payload) {
  const UseCase* const use_case = USE_CASE(self);

  MeasurementClient mcl;
  if (MeasurementClientConstruct(&mcl, use_case->local_entity, payload->entity) != kEebusErrorOk) {
    return;
  }

  ElectricalConnectionClient ecl;
  if (ElectricalConnectionClientConstruct(&ecl, use_case->local_entity, payload->entity) != kEebusErrorOk) {
    return;
  }

  const MeasurementListDataType* const measurement_list = payload->function_data;
  if (measurement_list == NULL) {
    return;
  }

  const EntityAddressType* const entity_addr = ENTITY_GET_ADDRESS(ENTITY_OBJECT(payload->entity));

  for (size_t i = 0; i < measurement_list->measurement_data_size; ++i) {
    const MeasurementDataType* const measurement = measurement_list->measurement_data[i];

    const MaMeasurementObject* const mpc_measurement = MaMpcMeasurementGetInstance(&mcl, &ecl, measurement);
    if (mpc_measurement == NULL) {
      continue;
    }

    ScaledValue value = {0};
    if (MA_MEASUREMENT_GET_DATA(mpc_measurement, use_case->local_entity, payload->entity, &value) != kEebusErrorOk) {
      continue;
    }

    const EebusMeasurementNameId name_id = MA_MEASUREMENT_GET_NAME(mpc_measurement);
    if (self->ma_mpc_listener != NULL) {
      MA_MPC_LISTENER_ON_MEASUREMENT_RECEIVE(self->ma_mpc_listener, name_id, &value, entity_addr);
    }
  }
}

static void OnDataChange(MaMpcUseCase* self, const EventPayload* payload) {
  switch (payload->function_type) {
    case kFunctionTypeMeasurementDescriptionListData: {
      MaOnMeasurementDescriptionDataUpdate(USE_CASE(self), payload);
      break;
    }

    case kFunctionTypeMeasurementListData: {
      OnMeasurementDataUpdate(self, payload);
      break;
    }

    default: break;
  }
}

void MaMpcHandleEvent(const EventPayload* payload, void* ctx) {
  MaMpcUseCase* ma_mpc_use_case = (MaMpcUseCase*)ctx;

  if (payload->event_type == kEventTypeEntityChange) {
    /* At entity-change time K40RF's use case list is still empty
     * (populated later via NodeManagementUseCaseData), so the usual
     * USE_CASE_IS_ENTITY_COMPATIBLE would always return false and
     * OnEntityConnected would never run.  We check entity TYPE only —
     * this is safe because the type comes from NodeManagementDetailedDiscovery
     * which fires first, and we must subscribe before K40RF closes its
     * subscription window (the original kEventTypeUseCaseChange path arrived
     * too late and caused state=39). */
    const UseCase* const use_case = USE_CASE(ma_mpc_use_case);
    const EntityTypeType entity_type = ENTITY_GET_TYPE(ENTITY_OBJECT(payload->entity));
    bool type_ok = false;
    for (size_t i = 0; i < use_case->info->valid_entity_types_size; i++) {
      if (entity_type == use_case->info->valid_entity_types[i]) {
        type_ok = true;
        break;
      }
    }
    if (!type_ok) {
      return;
    }

    if (payload->change_type == kElementChangeAdd) {
      OnEntityConnected(ma_mpc_use_case, payload);
    } else if (payload->change_type == kElementChangeRemove) {
      OnEntityDisconnected(ma_mpc_use_case, payload);
    }
  } else if ((payload->event_type == kEventTypeDataChange) && (payload->change_type == kElementChangeUpdate)) {
    /* By data-change time the use case list IS populated — safe to use the full check. */
    if (!USE_CASE_IS_ENTITY_COMPATIBLE(USE_CASE_OBJECT(ma_mpc_use_case), payload->entity)) {
      return;
    }
    OnDataChange(ma_mpc_use_case, payload);
  }
}
