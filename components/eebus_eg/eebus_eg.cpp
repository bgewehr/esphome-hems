/*
 * Copyright 2025 bgewehr (bg-hems branch)
 * Licensed under the Apache License, Version 2.0
 */

#include "eebus_eg.h"

#include <cmath>
#include <cstdarg>
#include <cstring>
#include <ctime>
#include <vector>

#include <mbedtls/sha1.h>
#include <mbedtls/oid.h>

#include "esphome/core/log.h"

#include <nvs_flash.h>
#include <nvs.h>
#include <mbedtls/entropy.h>
#include <mbedtls/ctr_drbg.h>
#include <mbedtls/x509_crt.h>
#include <mbedtls/pk.h>

extern "C" {
#include "mdns.h"
#include "src/service/service/eebus_service.h"
#include "src/service/api/eebus_service_config.h"
#include "src/service/api/service_reader_interface.h"
#include "src/common/eebus_device_info.h"
#include "src/common/vector.h"
#include "src/ship/api/mdns_entry.h"
#include "src/ship/tls_certificate/tls_certificate.h"
#include "src/spine/api/device_local_interface.h"
#include "src/spine/api/device_remote_interface.h"
#include "src/spine/api/entity_remote_interface.h"
#include "src/spine/entity/entity_local.h"
#include "src/spine/model/entity_types.h"
#include "src/spine/model/node_management_types.h"
#include "src/spine/events/events.h"
#include "src/use_case/actor/eg/lpc/eg_lpc.h"
#include "src/use_case/actor/ma/mpc/ma_mpc.h"
#include "src/use_case/model/load_limit_types.h"
}

#include "port/esp32/websocket/websocket_server_esp32.h"

/* ── SPINE event handler for initial exchange logging ───────────────────── */

static const char* spine_actor_name(int a) {
  switch (a) {
    case 0:  return "Battery";
    case 1:  return "BatterySystem";
    case 2:  return "CEM";
    case 3:  return "ConfigurationAppliance";
    case 4:  return "Compressor";
    case 5:  return "ControllableSystem";
    case 6:  return "DHWCircuit";
    case 7:  return "EnergyBroker";
    case 8:  return "EnergyConsumer";
    case 9:  return "EnergyGuard";
    case 10: return "EVSE";
    case 11: return "EV";
    case 12: return "GridConnectionPoint";
    case 13: return "HeatPump";
    case 14: return "HeatingCircuit";
    case 15: return "HeatingZone";
    case 16: return "HVACRoom";
    case 17: return "Inverter";
    case 18: return "MonitoredUnit";
    case 19: return "MonitoringAppliance";
    case 20: return "OutdoorTemperatureSensor";
    case 21: return "PVString";
    case 22: return "PVSystem";
    case 23: return "SmartAppliance";
    case 24: return "VisualizationAppliance";
    default: return "?";
  }
}

static const char* spine_uc_name(int n) {
  switch (n) {
    case  0: return "configurationOfDhwSystemFunction";
    case  1: return "configurationOfDhwTemperature";
    case  4: return "configurationOfRoomHeatingSystemFunction";
    case  5: return "configurationOfRoomHeatingTemperature";
    case 12: return "flexibleLoad";
    case 13: return "flexibleStart";
    case 14: return "limitationOfPowerConsumption";
    case 15: return "limitationOfPowerProduction";
    case 16: return "incentiveTableBasedPowerConsumptionManagement";
    case 18: return "monitoringAndControlOfSmartGridReadyConditions";
    case 20: return "monitoringOfDhwSystemFunction";
    case 22: return "monitoringOfGridConnectionPoint";
    case 25: return "monitoringOfPowerConsumption";
    case 26: return "monitoringOfPvString";
    case 28: return "monitoringOfRoomHeatingSystemFunction";
    case 30: return "optimizationOfSelfConsumptionByHeatPumpCompressorFlexibility";
    default: return "?";
  }
}

static void spine_event_handler(const EventPayload* payload, void* ctx) {
  auto* self = static_cast<esphome::eebus_eg::EebusEgComponent*>(ctx);
  if (!payload) return;
  const char* ski = payload->ski ? payload->ski : "?";
  switch (payload->event_type) {
    case kEventTypeUseCaseChange: {
      const UseCaseFilterType* f = payload->use_case_filter;
      if (!f) break;
      /* Capture into local ints immediately — PublishUseCaseSupportedEvent passes a
       * pointer to a stack-local UseCaseFilterType.  When addr->entity==NULL it loops
       * over all entities and calls EventPublish for each; the stack frame (and thus
       * the filter struct) is shared across those calls, so by the time a later
       * subscriber reads f->actor/uc_name_id the values may belong to a different
       * use case.  Copying to local ints makes us immune to that reuse. */
      const int actor_id = (int)f->actor;
      const int uc_id    = (int)f->use_case_name_id;
      const char* change = (payload->change_type == kElementChangeAdd)    ? "add"
                         : (payload->change_type == kElementChangeUpdate)  ? "update"
                         :                                                   "remove";
      const char* uc_str    = spine_uc_name(uc_id);
      const char* actor_str = spine_actor_name(actor_id);
      ESP_LOGW("eebus", "SPINE use-case %s from %s: actor=%d(%s) useCase=%d(%s)",
               change, ski, actor_id, actor_str, uc_id, uc_str);
      if (self) {
        const std::string& eg_ski = self->remote_ski();
        if (!eg_ski.empty() && eg_ski == ski) {
          bool rem = (payload->change_type == kElementChangeRemove);
          /* Treat ADD and UPDATE both as "use case present" — WP may re-announce
           * via UPDATE rather than ADD after a reconnect. */
          bool add = !rem;
          self->on_remote_use_case(actor_id, uc_id, uc_str, actor_str, add);
        }
      }
      break;
    }
    case kEventTypeEntityChange:
      if (payload->change_type == kElementChangeAdd)
        ESP_LOGI("eebus", "SPINE entity added from %s", ski);
      break;
    case kEventTypeDeviceChange:
      if (payload->change_type == kElementChangeAdd)
        ESP_LOGI("eebus", "SPINE device discovered: ski=%s", ski);
      break;
    case kEventTypeDataChange: {
      /* Passive observer — log SmartEnergyManagementPs (OSSHPCF) data at WARN
       * so it is visible during testing even without verbose logging enabled.
       * All other data changes are logged at DEBUG for diagnostics. */
      struct { FunctionType fn; const char* name; } static const kSemp[] = {
        { kFunctionTypeSmartEnergyManagementPsData,                        "SempData" },
        { kFunctionTypeSmartEnergyManagementPsConfigurationRequestCall,    "SempCfgReq" },
        { kFunctionTypeSmartEnergyManagementPsPriceData,                   "SempPriceData" },
        { kFunctionTypeSmartEnergyManagementPsPriceCalculationRequestCall, "SempPriceCalcReq" },
      };
      for (const auto& e : kSemp) {
        if (payload->function_type == e.fn) {
          ESP_LOGW("eebus_eg", "OSSHPCF msg from %s: %s (fn=%d) — data=%s",
                   ski, e.name, (int)payload->function_type,
                   payload->function_data ? "present" : "null");
          break;
        }
      }
      ESP_LOGD("eebus_eg", "SPINE data change from %s: fn=%d", ski, (int)payload->function_type);
      break;
    }
    default: break;
  }
}

/* Bridge: lets openeebus C files emit logs through ESPHome's log pipeline
 * (visible in the web frontend) instead of the raw ESP-IDF serial logger. */
extern "C" void eebus_log_d(const char* tag, int line, const char* fmt, ...) {
  va_list args;
  va_start(args, fmt);
  esphome::esp_log_vprintf_(ESPHOME_LOG_LEVEL_DEBUG, tag, line, fmt, args);
  va_end(args);
}

namespace esphome {
namespace eebus_eg {

/* NVS key names (constant across all instances) */
static const char* NVS_KEY_CERT  = "cert_der";
static const char* NVS_KEY_KEY   = "key_der";
static const char* NVS_KEY_SKI   = "remote_ski";
/* Outbound heartbeat timeout declared to the remote CS device in the DeviceDiagnosis heartbeat
 * data (SPINE spec standard for HEMS). heartbeat_manager sends every
 * timeout*3/4 = 45 s, well within the 60 s window. */
static const uint32_t kHeartbeatTimeoutSeconds = 60;

/* Inbound alarm: how long without a heartbeat from the remote CS device before we warn.
 * EEBus heartbeats are sent every ~40 s; 3× that gives a safe margin. */
static const uint32_t kInboundHeartbeatAlarmMs = 120000u;

/* Pairing window: how long the explicit pairing mode stays open */
static const uint32_t kPairingWindowMs = 300000;  /* 5 minutes */

/* =========================================================================
 * ServiceReader C vtable — bridges SHIP pairing events to C++ component
 * ====================================================================== */

struct EgServiceReader {
  ServiceReaderObject obj;   /* must be first */
  EebusEgComponent*  self;
};

extern "C" {

static void SR_Destruct(ServiceReaderObject*) {}

static void SR_OnRemoteSkiConnected(
    ServiceReaderObject* o, EebusServiceObject* /*svc*/, const char* ski)
{
  auto* r = reinterpret_cast<EgServiceReader*>(o);
  ESP_LOGW("eebus_eg", "Remote SKI connected: %s", ski);
  r->self->pairing_state_ = "Verbinde: " + std::string(ski);
}

static void SR_OnRemoteSkiDisconnected(
    ServiceReaderObject* o, EebusServiceObject* /*svc*/, const char* ski)
{
  auto* r = reinterpret_cast<EgServiceReader*>(o);
  ESP_LOGW("eebus_eg", "Remote SKI disconnected: %s", ski);
  r->self->on_entity_disconnect(nullptr);
}

static void SR_OnRemoteServicesUpdate(
    ServiceReaderObject* o, EebusServiceObject* svc, const Vector* entries)
{
  (void)svc;
  /* In EEBus, both the HEMS (EG role) and the remote CS device connect to each other
   * via "auto" SHIP role.  Connection initiation happens via the startup
   * EEBUS_SERVICE_REGISTER_REMOTE_SKI call (known SKI) or inbound from the remote device
   * via IsWaitingForTrustAllowed (pairing).  Do NOT call REGISTER_REMOTE_SKI
   * here — the reference openeebus/examples/hems/hems.c does nothing in this
   * callback and triggering an outbound attempt from every mDNS update causes
   * spurious connections in the wrong direction. */
  auto* r = reinterpret_cast<EgServiceReader*>(o);
  size_t n = VectorGetSize(entries);
  ESP_LOGD("eebus", "mDNS EG1 scan: %zu entr%s visible (periodic browser, ~15 s interval)",
           n, n == 1 ? "y" : "ies");
  for (size_t i = 0; i < n; i++) {
    const MdnsEntry* entry = (const MdnsEntry*)VectorGetElement(entries, i);
    const char* ski  = MdnsEntryGetSki(entry);
    const char* host = MdnsEntryGetHost(entry) ? MdnsEntryGetHost(entry) : "?";
    if (ski && ski[0] != '\0') {
      ESP_LOGD("eebus", "  mDNS[%zu]: ski=%s host=%s", i, ski, host);
    }
  }
  if (!r->self->pairing_mode_active_) return;
  if (!r->self->remote_ski_.empty()) return;  /* already paired */

  for (size_t i = 0; i < n; i++) {
    const MdnsEntry* entry = (const MdnsEntry*)VectorGetElement(entries, i);
    const char* ski = MdnsEntryGetSki(entry);
    if (!ski || ski[0] == '\0') continue;
    if (r->self->local_ski_ == ski) continue;
    const char* host = MdnsEntryGetHost(entry) ? MdnsEntryGetHost(entry) : "?";
    ESP_LOGI("eebus_eg", "mDNS: EG1 sichtbar ski=%s host=%s — warte auf eingehende Verbindung",
             ski, host);
    r->self->pairing_state_ = "Gerät sichtbar: " + std::string(ski) + " — warte auf Verbindung";
    break;
  }
}

static void SR_OnShipIdUpdate(
    ServiceReaderObject* o, const char* ski, const char* ship_id)
{
  auto* r = reinterpret_cast<EgServiceReader*>(o);
  ESP_LOGD("eebus_eg", "SHIP ID update ski=%s id=%s", ski, ship_id ? ship_id : "");
  if (ship_id && ship_id[0] != '\0' && ski && r->self->remote_ski_ == ski) {
    r->self->device_label_ = ship_id;
    ESP_LOGI("eebus_eg", "EG1 device name: %s", ship_id);
  }
}

static void SR_OnShipStateUpdate(
    ServiceReaderObject* o, const char* ski, SmeState state)
{
  auto* r = reinterpret_cast<EgServiceReader*>(o);
  ESP_LOGW("eebus_eg", "SHIP state ski=%s state=%d", ski, (int)state);
  if (state == kDataExchange) {
    /* Reject connections where TLS peer cert extraction failed.
     * Root cause: CONFIG_MBEDTLS_SSL_KEEP_PEER_CERTIFICATE not set in sdkconfig. */
    if (!ski || strcmp(ski, "unknown") == 0 || strlen(ski) < 40) {
      ESP_LOGE("eebus_eg", "DataExchange mit ungültiger SKI '%s' — Pairing ignoriert. "
               "Prüfe CONFIG_MBEDTLS_SSL_KEEP_PEER_CERTIFICATE=y in sdkconfig.",
               ski ? ski : "null");
      return;
    }
    r->self->remote_ski_ = ski;
    r->self->pairing_state_ = "Gepairt: " + std::string(ski);
    ESP_LOGW("eebus_eg", "EG1 pairing approved, remote SKI=%s", ski);
    r->self->save_remote_ski_nvs_(ski);
    r->self->on_ship_data_exchange_(ski);
  }
}

static bool SR_IsWaitingForTrustAllowed(const ServiceReaderObject* o, const char* ski) {
  if (!ski || strcmp(ski, "unknown") == 0 || strlen(ski) < 20) return false;
  const auto* self = reinterpret_cast<const EgServiceReader*>(o)->self;
  return self->pairing_mode_active_ && millis() < self->pairing_deadline_ms_;
}

static const ServiceReaderInterface kServiceReaderMethods = {
  .destruct                   = SR_Destruct,
  .on_remote_ski_connected    = SR_OnRemoteSkiConnected,
  .on_remote_ski_disconnected = SR_OnRemoteSkiDisconnected,
  .on_remote_services_update  = SR_OnRemoteServicesUpdate,
  .on_ship_id_update          = SR_OnShipIdUpdate,
  .on_ship_state_update       = SR_OnShipStateUpdate,
  .is_waiting_for_trust_allowed = SR_IsWaitingForTrustAllowed,
};

}  // extern "C"

/* =========================================================================
 * setup()
 * ====================================================================== */

void EebusEgComponent::setup() {
  ESP_LOGI(TAG, "Setting up EEBus %s", instance_name_.c_str());

  /* Per-instance NVS namespace derived from SHIP port (e.g. "eg4713"). */
  {
    char ns_buf[16];
    snprintf(ns_buf, sizeof(ns_buf), "eg%u", (unsigned)ship_port_);
    nvs_ns_ = ns_buf;
  }

  /* Load paired remote CS device SKI from NVS if not set in YAML secrets */
  if (remote_ski_.empty()) {
    remote_ski_ = load_remote_ski_nvs_();
    if (!remote_ski_.empty()) {
      if (remote_ski_ == "unknown" || remote_ski_.length() < 20) {
        ESP_LOGW(TAG, "%s: clearing invalid SKI from NVS: '%s'", instance_name_.c_str(), remote_ski_.c_str());
        save_remote_ski_nvs_("");
        remote_ski_.clear();
      } else {
        ESP_LOGW(TAG, "%s: loaded paired remote SKI from NVS: %s", instance_name_.c_str(), remote_ski_.c_str());
      }
    }
  }

  uint8_t* cert = nullptr; size_t cl = 0;
  uint8_t* key  = nullptr; size_t kl = 0;

  if (!load_cert_nvs_(&cert, &cl, &key, &kl)) {
    ESP_LOGI(TAG, "%s: no cert in NVS — generating...", instance_name_.c_str());
    if (!load_or_generate_cert_() || !load_cert_nvs_(&cert, &cl, &key, &kl)) {
      ESP_LOGE(TAG, "%s: certificate setup failed", instance_name_.c_str());
      mark_failed(); return;
    }
  }

  if (!start_eebus_service_(cert, cl, key, kl)) {
    /* Cert may be invalid (e.g. generated by older firmware without proper SKI extension).
     * Erase it and regenerate once using the corrected cert generation. */
    ESP_LOGW(TAG, "%s: cert invalid — erasing and regenerating", instance_name_.c_str());
    free(cert); free(key); cert = nullptr; key = nullptr; cl = kl = 0;
    nvs_handle_t h_erase;
    if (nvs_open(nvs_ns_.c_str(), NVS_READWRITE, &h_erase) == ESP_OK) {
      nvs_erase_key(h_erase, NVS_KEY_CERT);
      nvs_erase_key(h_erase, NVS_KEY_KEY);
      nvs_commit(h_erase);
      nvs_close(h_erase);
    }
    if (!load_or_generate_cert_() || !load_cert_nvs_(&cert, &cl, &key, &kl)) {
      ESP_LOGE(TAG, "%s: cert regeneration failed", instance_name_.c_str());
      mark_failed(); return;
    }
    if (!start_eebus_service_(cert, cl, key, kl)) {
      ESP_LOGE(TAG, "%s: service start failed after cert regeneration", instance_name_.c_str());
      free(cert); free(key);
      mark_failed(); return;
    }
  }

  free(cert); free(key);
  startup_mdns_at_ms_ = millis() + 15000;  /* one-shot mDNS announce after services are up */
  ESP_LOGI(TAG, "EEBus %s ready — heartbeat interval: %u s", instance_name_.c_str(), kHeartbeatTimeoutSeconds);
}

/* =========================================================================
 * loop()
 * ====================================================================== */

void EebusEgComponent::loop() {
  if (!service_ || !eg_lpc_) return;

  /* Check if the remote CS device has sent us a heartbeat within 2× the declared timeout.
   * EgLpcIsHeartbeatWithinDuration is broken (passes NULL remote entity →
   * always returns false), so we track the last received heartbeat ourselves
   * via on_heartbeat_received_() / last_heartbeat_ms_.
   * Grace period: allow up to 2× timeout after connect before alarming —
   * the remote device's heartbeat timer runs continuously and the first beat can arrive
   * up to 1× timeout after our connection is established. */
  const uint32_t hb_timeout_ms = kInboundHeartbeatAlarmMs;
  const bool grace   = connected_ && ((millis() - connected_since_ms_) < hb_timeout_ms);
  const bool hb_ok   = (last_heartbeat_ms_ != 0) &&
                       ((millis() - last_heartbeat_ms_) < hb_timeout_ms);
  if (connected_ && !grace && !hb_ok) {
    if (!heartbeat_alarm_) {
      ESP_LOGW(TAG, "%s heartbeat overdue \xe2\x80\x94 connection may be stale", instance_name_.c_str());
      heartbeat_alarm_ = true;
    }
  } else {
    if (heartbeat_alarm_ && connected_) {
      ESP_LOGI(TAG, "%s heartbeat recovered", instance_name_.c_str());
    }
    heartbeat_alarm_ = false;
  }

  /* Heartbeat test: resume heartbeat and re-enable reconnect after test period */
  if (heartbeat_test_until_ms_ != 0 && millis() >= heartbeat_test_until_ms_) {
    heartbeat_test_until_ms_ = 0;
    EgLpcStartHeartbeat(eg_lpc_);
    if (service_ && !remote_ski_.empty()) {
      EEBUS_SERVICE_REGISTER_REMOTE_SKI(service_, remote_ski_.c_str(), true);
      ESP_LOGI(TAG, "Heartbeat test complete — reconnect re-enabled for %s", remote_ski_.c_str());
    } else {
      ESP_LOGI(TAG, "Heartbeat test complete — outbound heartbeat resumed");
    }
  }

  /* Pairing window timeout */
  uint32_t now = millis();
  if (pairing_mode_active_ && now >= pairing_deadline_ms_) {
    ESP_LOGW(TAG, "%s: pairing window expired", instance_name_.c_str());
    pairing_mode_active_ = false;
    pairing_deadline_ms_ = 0;
    if (service_) EEBUS_SERVICE_SET_PAIRING_POSSIBLE(service_, false);
    set_mdns_register(false);
    pairing_advert_at_ms_ = 0;
    pairing_state_ = "Pairing-Fenster abgelaufen";
  }

  /* Boot: one-shot mDNS announce 15 s after boot (eebus-go pattern: checkAutoReannounce on start) */
  if (!startup_mdns_done_ && now >= startup_mdns_at_ms_) {
    startup_mdns_done_ = true;
    if (!connected_) set_mdns_register(false);
  }

  /* Pairing: register=true every 5 s while pairing window is open */
  if (pairing_mode_active_ && pairing_advert_at_ms_ != 0 && now >= pairing_advert_at_ms_) {
    set_mdns_register(true);
    pairing_advert_at_ms_ = now + 5000;
  }

  /* Failsafe setup retry: DeviceConfiguration key descriptions arrive after on_entity_connect,
   * so the first attempt in on_entity_connect fails. Retry every 5 s until it succeeds. */
  if (connected_ && have_remote_entity_ && !failsafe_set_ && now >= failsafe_retry_ms_) {
    ScaledValue fs_limit;
    fs_limit.value = (int64_t)failsafe_limit_w_;
    fs_limit.scale = 0;
    EebusError err_limit = EgLpcSetFailsafeConsumptionActivePowerLimit(eg_lpc_, &remote_entity_addr_, &fs_limit);

    /* EebusDurationCompare() is field-by-field (not normalized) —
     * {.seconds=7200} compares as hours=0 < hours=2 and fails the 2h..24h range check.
     * Must decompose into hours/minutes/seconds. */
    EebusDuration fs_duration;
    memset(&fs_duration, 0, sizeof(fs_duration));
    fs_duration.hours   = (int32_t)(failsafe_duration_s_ / 3600u);
    fs_duration.minutes = (int32_t)((failsafe_duration_s_ % 3600u) / 60u);
    fs_duration.seconds = (int32_t)(failsafe_duration_s_ % 60u);
    EebusError err_dur = EgLpcSetFailsafeDurationMinimum(eg_lpc_, &remote_entity_addr_, &fs_duration);

    if (err_limit == kEebusErrorOk && err_dur == kEebusErrorOk) {
      ESP_LOGI(TAG, "%s failsafe configured: %.0f W / %u s", instance_name_.c_str(), failsafe_limit_w_, failsafe_duration_s_);
      failsafe_set_ = true;
    } else {
      ESP_LOGD(TAG, "%s failsafe not ready: limit_err=%d dur_err=%d — DeviceConfig keys pending, retry in 5s",
               instance_name_.c_str(), (int)err_limit, (int)err_dur);
      failsafe_retry_ms_ = now + 5000;
    }
  }

  /* 5 s after entity connect: log a consolidated use-case summary so it is
   * visible in the log even if the individual ADD messages scrolled past. */
  if (uc_dump_at_ms_ != 0 && now >= uc_dump_at_ms_) {
    uc_dump_at_ms_ = 0;
    ESP_LOGW(TAG, "%s supported UCs: %s", instance_name_.c_str(),
             remote_uc_seen_.empty() ? "(none)" : remote_uc_seen_.c_str() + 3);
  }

  /* Subscribe to SEMP data once the OSSHPCF use case has been announced */
  if (semp_subscribe_pending_ && connected_ && local_semp_feature_) {
    semp_subscribe_pending_ = false;
    subscribe_semp_();
  }

  /* Retry power limit if ACK not received within 10 s (SPINE subscription race at connect) */
  if (connected_ && have_remote_entity_ && pending_limit_w_ >= 0.0f &&
      (now - pending_limit_ms_) >= 10000u) {
    ESP_LOGW(TAG, "%s limit %.0f W unacknowledged — retrying", instance_name_.c_str(), pending_limit_w_);
    set_limit(pending_limit_w_);
  }

}

/* =========================================================================
 * dump_config()
 * ====================================================================== */

void EebusEgComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "EEBus %s Component::", instance_name_.c_str());
  ESP_LOGCONFIG(TAG, "  SHIP Port:      %d",   ship_port_);
  ESP_LOGCONFIG(TAG, "  Remote SKI:      %s",   remote_ski_.empty() ? "(auto-discover)" : remote_ski_.c_str());
  ESP_LOGCONFIG(TAG, "  Connected:      %s",   connected_ ? "yes" : "no");
  ESP_LOGCONFIG(TAG, "  Heartbeat:      every %u s", kHeartbeatTimeoutSeconds);
  ESP_LOGCONFIG(TAG, "  Failsafe:       %.0f W / %u s", failsafe_limit_w_, failsafe_duration_s_);
  ESP_LOGCONFIG(TAG, "  SPINE local:    device=EnergyManagementSystem entity=CEM(id=1) heartbeat=%us", kHeartbeatTimeoutSeconds);
  ESP_LOGCONFIG(TAG, "  SPINE use-case: HEMS=EnergyGuard(9) <-> CS=ControllableSystem(5) / limitationOfPowerConsumption(14) [EG/LPC]");
  ESP_LOGCONFIG(TAG, "  SPINE use-case: HEMS=MonitoringAppliance(19) <-> CS=MonitoredUnit(18) / monitoringOfPowerConsumption(25) [MA/MPC]");
  ESP_LOGCONFIG(TAG, "  SPINE negotiated: %s", active_use_cases().c_str());
}

/* =========================================================================
 * Public API
 * ====================================================================== */

void EebusEgComponent::start_heartbeat_test() {
  if (!eg_lpc_) {
    ESP_LOGW(TAG, "Heartbeat test: service not running");
    return;
  }
  static const uint32_t kTestDurationMs = 120000u;
  ESP_LOGW(TAG, "Heartbeat test: stopping heartbeat and blocking %s reconnect for %u s",
           instance_name_.c_str(), kTestDurationMs / 1000u);
  EgLpcStopHeartbeat(eg_lpc_);
  heartbeat_test_until_ms_ = millis() + kTestDurationMs;
  /* Unregister clears remote_ski in ship_node so SkiMatches() fails for any
   * inbound attempt from the WP — it cannot reconnect until we re-register. */
  if (service_ && !remote_ski_.empty()) {
    EEBUS_SERVICE_UNREGISTER_REMOTE_SKI(service_, remote_ski_.c_str());
  }
}

void EebusEgComponent::set_limit(float watts) {
  if (!connected_ || !have_remote_entity_ || !eg_lpc_) {
    ESP_LOGW(TAG, "set_limit(%.0f W) — %s not connected", watts, instance_name_.c_str());
    return;
  }

  /* §14a: never limit below 4200 W — WP silently ignores active limits below this threshold */
  if (watts > 0.0f && watts < 4200.0f) {
    ESP_LOGW(TAG, "Clamping %.0f W → 4200 W (WP minimum)", watts);
    watts = 4200.0f;
  }

  LoadLimit limit;
  memset(&limit, 0, sizeof(limit));

  if (watts <= 0.0f) {
    limit.value.value = 99999; /* placeholder — value is ignored when is_active=false */
    limit.value.scale = 0;
    limit.is_active   = false;
    ESP_LOGI(TAG, "Clearing %s power limit", instance_name_.c_str());
  } else {
    limit.value.value = (int64_t)watts;
    limit.value.scale = 0;
    limit.is_active   = true;
    ESP_LOGI(TAG, "Setting %s power limit: %.0f W", instance_name_.c_str(), watts);
  }

  EebusError err = EgLpcSetActiveConsumptionPowerLimit(eg_lpc_, &remote_entity_addr_, &limit);
  if (err != kEebusErrorOk) {
    ESP_LOGE(TAG, "SetActiveConsumptionPowerLimit failed: %d", (int)err);
    return;
  }

  active_limit_w_  = watts > 0.0f ? watts : 0.0f;
  pending_limit_w_ = active_limit_w_;
  pending_limit_ms_ = millis();
}

/* =========================================================================
 * EgLpListenerInterface callbacks
 * ====================================================================== */

void EebusEgComponent::on_remote_use_case(int actor, int uc_name_id, const char* /*uc_str*/, const char* /*actor_str*/, bool add) {
  /* Official EEBus SPINE abbreviations (EEBus Initiative documentation) */
  const char* a;
  switch (actor) {
    case  4: a = "Comp"; break;  /* Compressor */
    case  5: a = "CS";   break;  /* ControllableSystem */
    case  9: a = "EG";   break;  /* EnergyGuard */
    case 18: a = "MU";   break;  /* MonitoredUnit */
    case 19: a = "MA";   break;  /* MonitoringAppliance */
    default: a = "?";    break;
  }
  const char* uc;
  switch (uc_name_id) {
    case 14: uc = "LPC";     break;  /* limitationOfPowerConsumption */
    case 15: uc = "LPP";     break;  /* limitationOfPowerProduction */
    case 25: uc = "MPC";     break;  /* monitoringOfPowerConsumption */
    case 30: uc = "OSSHPCF"; break;  /* optimizationOfSelfConsumptionByHPCF */
    default: uc = "?";       break;
  }
  char buf[24];
  snprintf(buf, sizeof(buf), " | %s/%s", a, uc);
  if (add) {
    if (remote_uc_seen_.find(buf) == std::string::npos) {
      remote_uc_seen_ += buf;
      ESP_LOGW(TAG, "Use case added by remote: %s", buf + 3);  /* skip " | " */
    }
    if (actor == 4 && uc_name_id == 30)
      semp_subscribe_pending_ = true;
  } else {
    auto pos = remote_uc_seen_.find(buf);
    if (pos != std::string::npos) {
      remote_uc_seen_.erase(pos, strlen(buf));
      ESP_LOGW(TAG, "Use case removed by remote: %s", buf + 3);  /* skip " | " */
    }
  }
}

void EebusEgComponent::subscribe_semp_() {
  if (!local_semp_feature_ || !service_ || remote_ski_.empty()) return;

  DeviceLocalObject* local_dev = EEBUS_SERVICE_GET_LOCAL_DEVICE(service_);
  if (!local_dev) return;

  DeviceRemoteObject* remote_dev = DEVICE_LOCAL_GET_REMOTE_DEVICE_WITH_SKI(local_dev, remote_ski_.c_str());
  if (!remote_dev) {
    ESP_LOGW(TAG, "OSSHPCF subscribe: remote device not found (SKI=%s)", remote_ski_.c_str());
    return;
  }

  const Vector* entities = DEVICE_REMOTE_GET_ENTITIES(remote_dev);
  size_t n = entities ? VectorGetSize(entities) : 0;
  for (size_t i = 0; i < n; i++) {
    auto* ent = static_cast<EntityRemoteObject*>(VectorGetElement(entities, i));
    if (!ent) continue;
    FeatureRemoteObject* semp = ENTITY_REMOTE_GET_FEATURE_WITH_TYPE_AND_ROLE(
        ent, kFeatureTypeTypeSmartEnergyManagementPs, kRoleTypeServer);
    if (!semp) continue;
    const FeatureAddressType* addr = FEATURE_GET_ADDRESS(FEATURE_OBJECT(semp));
    if (!addr) continue;
    if (FEATURE_LOCAL_INTERFACE(local_semp_feature_)->has_subscription_to_remote(local_semp_feature_, addr)) {
      ESP_LOGD(TAG, "OSSHPCF: already subscribed to SEMP");
      return;
    }
    EebusError err = FEATURE_LOCAL_INTERFACE(local_semp_feature_)->subscribe_to_remote(local_semp_feature_, addr);
    ESP_LOGW(TAG, "OSSHPCF: subscribed to remote SEMP (entity %zu) err=%d", i, (int)err);
    return;
  }
  ESP_LOGW(TAG, "OSSHPCF: no SEMP server feature found on remote device (%zu entities)", n);
}

void EebusEgComponent::on_entity_connect(const EntityAddressType* addr) {
  ESP_LOGI(TAG, "%s entity connected", instance_name_.c_str());
  connected_          = true;
  heartbeat_alarm_    = false;
  connected_since_ms_ = millis();
  have_remote_entity_ = true;
  if (addr) remote_entity_addr_ = *addr;
  pairing_state_      = "Verbunden";
  failsafe_set_       = false;   /* loop() will retry until DeviceConfiguration data is available */
  failsafe_retry_ms_  = 0;
  uc_dump_at_ms_      = millis() + 5000;

  for (auto* t : connected_triggers_) t->trigger();
}

void EebusEgComponent::on_entity_disconnect(const EntityAddressType* /*addr*/) {
  if (!connected_ && !have_remote_entity_) return;  /* guard: SPINE and SHIP both fire disconnect */
  ESP_LOGW(TAG, "%s entity disconnected", instance_name_.c_str());
  connected_          = false;
  mpc_connected_      = false;
  last_heartbeat_ms_  = 0;
  have_remote_entity_ = false;
  active_limit_w_          = 0.0f;  /* WP applies its own failsafe while disconnected — we don't control it */
  current_power_w_         = 0.0f;  /* stale reading no longer valid */
  pending_limit_w_         = -1.0f;
  uc_dump_at_ms_           = 0;
  remote_uc_seen_          = {};
  semp_subscribe_pending_  = false;
  pairing_state_      = "Getrennt — suche Gerät...";
  /* Re-announce mDNS so the remote device reconnects immediately (eebus-go: checkAutoReannounce on disconnect) */
  set_mdns_register(false);

  /* Do NOT stop the heartbeat on disconnect — eebus-go never stops it.
   * Keeping it running ensures the feature data is always fresh for the
   * next time the remote device connects and subscribes. */

  for (auto* t : disconnected_triggers_) t->trigger();
}

void EebusEgComponent::on_power_limit_ack(float watts, bool active) {
  if (active) {
    ESP_LOGD(TAG, "%s ACK limit %.0f W active=yes", instance_name_.c_str(), watts);
    active_limit_w_ = watts;
  } else {
    ESP_LOGD(TAG, "%s ACK limit cleared (active=no)", instance_name_.c_str());
    active_limit_w_ = 0.0f;
  }
  pending_limit_w_ = -1.0f;  /* ACK received — cancel any pending retry */
}

void EebusEgComponent::on_mpc_measurement(float watts) {
  current_power_w_ = watts;
  for (auto* t : power_triggers_) t->trigger(watts);
}

/* =========================================================================
 * NVS certificate helpers
 * ====================================================================== */

bool EebusEgComponent::store_cert_nvs_(
    const uint8_t* c, size_t cl, const uint8_t* k, size_t kl)
{
  nvs_handle_t h;
  if (nvs_open(nvs_ns_.c_str(), NVS_READWRITE, &h) != ESP_OK) return false;
  bool ok = (nvs_set_blob(h, NVS_KEY_CERT, c, cl) == ESP_OK) &&
            (nvs_set_blob(h, NVS_KEY_KEY,  k, kl) == ESP_OK) &&
            (nvs_commit(h) == ESP_OK);
  nvs_close(h);
  return ok;
}

bool EebusEgComponent::load_cert_nvs_(
    uint8_t** c, size_t* cl, uint8_t** k, size_t* kl)
{
  nvs_handle_t h;
  if (nvs_open(nvs_ns_.c_str(), NVS_READONLY, &h) != ESP_OK) return false;
  size_t clen = 0, klen = 0;
  bool ok = false;
  do {
    if (nvs_get_blob(h, NVS_KEY_CERT, nullptr, &clen) != ESP_OK || clen == 0) break;
    if (nvs_get_blob(h, NVS_KEY_KEY,  nullptr, &klen) != ESP_OK || klen == 0) break;
    *c = (uint8_t*)malloc(clen); *k = (uint8_t*)malloc(klen);
    if (!*c || !*k) { free(*c); *c = nullptr; free(*k); *k = nullptr; break; }
    if (nvs_get_blob(h, NVS_KEY_CERT, *c, &clen) != ESP_OK) break;
    if (nvs_get_blob(h, NVS_KEY_KEY,  *k, &klen) != ESP_OK) break;
    *cl = clen; *kl = klen; ok = true;
  } while (false);
  nvs_close(h);
  if (!ok) { free(*c); free(*k); *c = *k = nullptr; }
  return ok;
}

void EebusEgComponent::save_remote_ski_nvs_(const char* ski) {
  if (!ski) return;
  /* Allow empty string to clear NVS; reject "unknown" and suspiciously short strings */
  if (strlen(ski) > 0 && (strcmp(ski, "unknown") == 0 || strlen(ski) < 20)) {
    ESP_LOGW(TAG, "save_remote_ski_nvs_: ignoring invalid SKI '%s'", ski);
    return;
  }
  nvs_handle_t h;
  if (nvs_open(nvs_ns_.c_str(), NVS_READWRITE, &h) != ESP_OK) return;
  nvs_set_str(h, NVS_KEY_SKI, ski);
  nvs_commit(h);
  nvs_close(h);
}

std::string EebusEgComponent::load_remote_ski_nvs_() {
  nvs_handle_t h;
  if (nvs_open(nvs_ns_.c_str(), NVS_READONLY, &h) != ESP_OK) return {};
  size_t len = 0;
  std::string result;
  if (nvs_get_str(h, NVS_KEY_SKI, nullptr, &len) == ESP_OK && len > 1) {
    result.resize(len);
    nvs_get_str(h, NVS_KEY_SKI, &result[0], &len);
    result.resize(len - 1);  /* strip NUL terminator included in len */
  }
  nvs_close(h);
  return result;
}

bool EebusEgComponent::load_or_generate_cert_() {
  mbedtls_pk_context       pk;
  mbedtls_x509write_cert   crt;
  mbedtls_entropy_context  entropy;
  mbedtls_ctr_drbg_context drbg;

  mbedtls_pk_init(&pk);
  mbedtls_x509write_crt_init(&crt);
  mbedtls_entropy_init(&entropy);
  mbedtls_ctr_drbg_init(&drbg);

  bool ok = false;
  do {
    const char* pers = "eebus_wp_eg";
    if (mbedtls_ctr_drbg_seed(&drbg, mbedtls_entropy_func, &entropy,
                               (const unsigned char*)pers, strlen(pers)) != 0) break;
    if (mbedtls_pk_setup(&pk, mbedtls_pk_info_from_type(MBEDTLS_PK_ECKEY)) != 0 ||
        mbedtls_ecp_gen_key(MBEDTLS_ECP_DP_SECP256R1, mbedtls_pk_ec(pk),
                            mbedtls_ctr_drbg_random, &drbg) != 0) break;

    uint8_t key_buf[2048]; memset(key_buf, 0, sizeof(key_buf));
    int key_len = mbedtls_pk_write_key_der(&pk, key_buf, sizeof(key_buf));
    if (key_len <= 0) break;
    uint8_t* key_p = key_buf + sizeof(key_buf) - key_len;

    char cn_buf[64];
    snprintf(cn_buf, sizeof(cn_buf), "CN=%s-%s,O=%s,C=DE",
             device_model_.c_str(), instance_name_.c_str(), device_brand_.c_str());
    mbedtls_x509write_crt_set_subject_key(&crt, &pk);
    mbedtls_x509write_crt_set_issuer_key(&crt, &pk);
    if (mbedtls_x509write_crt_set_subject_name(&crt, cn_buf) != 0) break;
    if (mbedtls_x509write_crt_set_issuer_name(&crt, cn_buf) != 0) break;
    mbedtls_x509write_crt_set_version(&crt, MBEDTLS_X509_CRT_VERSION_3);
    mbedtls_x509write_crt_set_md_alg(&crt, MBEDTLS_MD_SHA256);
    uint8_t serial_bytes[] = {0x01};
    mbedtls_x509write_crt_set_serial_raw(&crt, serial_bytes, sizeof(serial_bytes));
    mbedtls_x509write_crt_set_validity(&crt, "20250101000000", "20350101000000");

    /* Compute SHA-1 of raw EC public key point for SubjectKeyIdentifier.
     * Use mbedtls_sha1_context directly — avoids the PSA/mbedtls_md path used by
     * mbedtls_x509write_crt_set_subject_key_identifier which can silently fail and
     * generate a cert without an SKI extension, causing TlsCertificateParseX509KeyPair
     * to reject it (openeebus CheckSki() requires the extension to be present). */
    uint8_t pk_pt[100]; uint8_t* pk_pt_p = pk_pt + sizeof(pk_pt);
    int pk_pt_len = mbedtls_pk_write_pubkey(&pk_pt_p, pk_pt, &pk);
    if (pk_pt_len <= 0) break;
    uint8_t ski_hash[20];
    {
      mbedtls_sha1_context sha1;
      mbedtls_sha1_init(&sha1);
      mbedtls_sha1_starts(&sha1);
      mbedtls_sha1_update(&sha1, pk_pt_p, (size_t)pk_pt_len);
      mbedtls_sha1_finish(&sha1, ski_hash);
      mbedtls_sha1_free(&sha1);
    }
    /* SubjectKeyIdentifier value: OCTET STRING (04 14) wrapping the 20-byte SHA-1 */
    uint8_t ski_ext[22] = {0x04, 0x14};
    memcpy(ski_ext + 2, ski_hash, 20);
    if (mbedtls_x509write_crt_set_extension(&crt,
            MBEDTLS_OID_SUBJECT_KEY_IDENTIFIER,
            MBEDTLS_OID_SIZE(MBEDTLS_OID_SUBJECT_KEY_IDENTIFIER),
            0, ski_ext, sizeof(ski_ext)) != 0) break;
    /* AuthorityKeyIdentifier for self-signed cert: SEQUENCE { [0] IMPLICIT keyIdentifier } */
    uint8_t aki_ext[24] = {0x30, 0x16, 0x80, 0x14};
    memcpy(aki_ext + 4, ski_hash, 20);
    if (mbedtls_x509write_crt_set_extension(&crt,
            MBEDTLS_OID_AUTHORITY_KEY_IDENTIFIER,
            MBEDTLS_OID_SIZE(MBEDTLS_OID_AUTHORITY_KEY_IDENTIFIER),
            0, aki_ext, sizeof(aki_ext)) != 0) break;

    uint8_t cert_buf[4096];
    int cert_len = mbedtls_x509write_crt_der(&crt, cert_buf, sizeof(cert_buf),
                                              mbedtls_ctr_drbg_random, &drbg);
    if (cert_len <= 0) break;
    uint8_t* cert_p = cert_buf + sizeof(cert_buf) - cert_len;
    ok = store_cert_nvs_(cert_p, (size_t)cert_len, key_p, (size_t)key_len);
  } while (false);

  mbedtls_pk_free(&pk);
  mbedtls_x509write_crt_free(&crt);
  mbedtls_entropy_free(&entropy);
  mbedtls_ctr_drbg_free(&drbg);
  return ok;
}

/* =========================================================================
 * start_eebus_service_() — correct pattern from openeebus examples/hems
 * ====================================================================== */

bool EebusEgComponent::start_eebus_service_(
    const uint8_t* cert_der, size_t cert_len,
    const uint8_t* key_der,  size_t key_len)
{
  /* Parse TLS certificate */
  TlsCertificateObject* tls_cert = TlsCertificateParseX509KeyPair(
      (const char*)cert_der, cert_len, (const char*)key_der, key_len);
  if (!tls_cert) { ESP_LOGE(TAG, "TlsCertificateParse failed"); return false; }

  const char* local_ski = TLS_CERTIFICATE_GET_SKI(tls_cert);
  if (local_ski) {
    local_ski_ = local_ski;
  } else {
    /* Fallback: TLS_CERTIFICATE_GET_SKI returned null (unusual for correctly generated certs).
     * Compute SKI directly from the cert's public key via mbedtls. */
    mbedtls_x509_crt crt_fb;
    mbedtls_x509_crt_init(&crt_fb);
    if (mbedtls_x509_crt_parse_der(&crt_fb, cert_der, cert_len) == 0) {
      uint8_t pk_buf[100]; uint8_t* pk_p = pk_buf + sizeof(pk_buf);
      int pk_len = mbedtls_pk_write_pubkey(&pk_p, pk_buf, &crt_fb.pk);
      if (pk_len > 0) {
        uint8_t sha[20];
        mbedtls_sha1_context sha1;
        mbedtls_sha1_init(&sha1);
        mbedtls_sha1_starts(&sha1);
        mbedtls_sha1_update(&sha1, pk_p, (size_t)pk_len);
        mbedtls_sha1_finish(&sha1, sha);
        mbedtls_sha1_free(&sha1);
        char ski_hex[41];
        for (int i = 0; i < 20; i++) snprintf(ski_hex + 2 * i, 3, "%02x", sha[i]);
        ski_hex[40] = '\0';
        local_ski_ = ski_hex;
        ESP_LOGW(TAG, "%s: TLS_CERTIFICATE_GET_SKI null — mbedtls fallback SKI: %s",
                 instance_name_.c_str(), ski_hex);
      }
    }
    mbedtls_x509_crt_free(&crt_fb);
  }
  ESP_LOGI(TAG, "%s local SKI: %s", instance_name_.c_str(), local_ski_.c_str());

  /* Build service config */
  EebusServiceConfig* cfg = EebusServiceConfigCreate(
      "DIY",
      device_brand_.c_str(),
      device_model_.c_str(),
      "HEMS-EG1-01",
      "EnergyManagementSystem",
      ship_port_);
  if (!cfg) { ESP_LOGE(TAG, "EebusServiceConfigCreate failed"); return false; }

  EebusServiceConfigSetRegisterAutoAccept(cfg, false);  /* pairing requires explicit activation */

  /* Set up ServiceReader vtable */
  SERVICE_READER_INTERFACE(&service_reader_) = &kServiceReaderMethods;
  service_reader_.self = this;

  /* Role "auto" (kShipRoleAuto): HEMS can both initiate outbound and accept inbound.
   * Simultaneous-connect races are resolved by SKI tie-breaking in ship_node.c,
   * matching the ship-go v0.6.0 keepThisConnection approach. */
  service_ = EebusServiceCreate(cfg, "auto", tls_cert,
                                 SERVICE_READER_OBJECT(&service_reader_));
  EebusServiceConfigDelete(cfg);
  if (!service_) { ESP_LOGE(TAG, "EebusServiceCreate failed"); return false; }

  if (!remote_ski_.empty() && remote_ski_ != "unknown" && remote_ski_.length() >= 40) {
    EEBUS_SERVICE_REGISTER_REMOTE_SKI(service_, remote_ski_.c_str(), true);
    ESP_LOGI(TAG, "Registered remote %s SKI: %s", instance_name_.c_str(), remote_ski_.c_str());
  } else {
    if (!remote_ski_.empty()) {
      ESP_LOGW(TAG, "Ignoring invalid remote SKI from config: '%s'", remote_ski_.c_str());
      remote_ski_.clear();
    }
    ESP_LOGI(TAG, "No remote SKI — pairing mode must be activated explicitly");
  }
  pairing_mode_active_ = false;
  pairing_deadline_ms_ = 0;

  /* Get local SPINE device */
  DeviceLocalObject* device_local = EEBUS_SERVICE_GET_LOCAL_DEVICE(service_);
  if (!device_local) { ESP_LOGE(TAG, "GetLocalDevice failed"); return false; }

  /* Create local CEM entity with heartbeat timeout.
   * This is the critical step — without a proper entity the heartbeat
   * manager is never created and WP raises a connection fault. */
  uint32_t entity_id = 1;
  local_entity_ = EntityLocalCreate(
      device_local,
      kEntityTypeTypeCEM,      /* CEM = Controller Entity Manager (EG role) */
      &entity_id,
      1,
      kHeartbeatTimeoutSeconds /* ← drives the FreeRTOS heartbeat timer */
  );
  if (!local_entity_) { ESP_LOGE(TAG, "EntityLocalCreate failed"); return false; }

  /* Create EG/LPC use case against the entity (not the service) */
  EG_LP_LISTENER_INTERFACE(&eg_listener_) = &kEgListenerMethods;
  eg_listener_.self = this;

  eg_lpc_ = EgLpcUseCaseCreate(local_entity_, EG_LP_LISTENER_OBJECT(&eg_listener_));
  if (!eg_lpc_) { ESP_LOGE(TAG, "EgLpcUseCaseCreate failed"); return false; }

  /* MaMpc (Monitoring of Power Consumption): required so the WP can report its
   * power draw to the HEMS and shows "EEBus verbunden" on its own display.
   * The Go bridge (eebus-ha-bridge) registers this as its "Monitoring" use case. */
  MA_MPC_LISTENER_INTERFACE(&mpc_listener_) = &kMpcListenerMethods;
  mpc_listener_.self = this;
  ma_mpc_ = MaMpcUseCaseCreate(local_entity_, MA_MPC_LISTENER_OBJECT(&mpc_listener_));
  if (!ma_mpc_) { ESP_LOGW(TAG, "MaMpcUseCaseCreate failed — %s may not show EEBus verbunden", instance_name_.c_str()); }

  /* Add a local SmartEnergyManagementPs client feature so we can subscribe to the remote
   * Compressor entity's SEMP server when OSSHPCF use case is active. */
  local_semp_feature_ = ENTITY_LOCAL_ADD_FEATURE_WITH_TYPE_AND_ROLE(
      local_entity_, kFeatureTypeTypeSmartEnergyManagementPs, kRoleTypeClient);
  if (!local_semp_feature_)
    ESP_LOGW(TAG, "SEMP client feature init failed — OSSHPCF data unavailable");

  /* Register entity with device so it is advertised via SPINE/mDNS */
  DEVICE_LOCAL_ADD_ENTITY(device_local, local_entity_);

  /* Subscribe so remote SPINE announcements appear in log under tag "eebus" */
  EventSubscribe(kEventHandlerLevelApplication, spine_event_handler, this);

  /* Service start is deferred to refresh_heartbeat() (called on the first
   * on_time_sync event from SNTP or HA).  This guarantees the remote device cannot
   * subscribe while the stored DeviceDiagnosis heartbeat data still carries
   * an epoch timestamp — the root cause of its persistent "no connection"
   * display state. */
  ESP_LOGI(TAG, "EEBus %s setup complete — awaiting time sync before service start", instance_name_.c_str());
  return true;
}

/* =========================================================================
 * Pairing management
 * ====================================================================== */

void EebusEgComponent::on_ship_data_exchange_(const char* ski) {
  ESP_LOGW(TAG, "%s data exchange active — SKI persisted: %s", instance_name_.c_str(), ski);
  if (pairing_mode_active_) {
    pairing_mode_active_ = false;
    pairing_deadline_ms_ = 0;
    if (service_) EEBUS_SERVICE_SET_PAIRING_POSSIBLE(service_, false);
    mdns_service_txt_item_set("_ship", "_tcp", "register", "false");
    ESP_LOGW(TAG, "%s: mDNS: register TXT -> false (data exchange active)", instance_name_.c_str());
    ESP_LOGW(TAG, "Pairing mode exited after successful connection");
  }
}

void EebusEgComponent::enter_pairing_mode() {
  ESP_LOGW(TAG, "Pairing mode activated (window: %u s)", kPairingWindowMs / 1000);
  pairing_mode_active_ = true;
  pairing_deadline_ms_ = millis() + kPairingWindowMs;
  if (service_) EEBUS_SERVICE_SET_PAIRING_POSSIBLE(service_, true);
  /* immediate first advert; loop() repeats every 5 s while pairing window is open */
  set_mdns_register(true);
  pairing_advert_at_ms_ = millis() + 5000;
  pairing_state_ = "Pairing-Modus aktiv — warte auf Verbindung...";
}

void EebusEgComponent::set_mdns_register(bool val) {
  mdns_service_txt_item_set("_ship", "_tcp", "register", val ? "true" : "false");
  ESP_LOGW(TAG, "%s: mDNS: register TXT -> %s", instance_name_.c_str(), val ? "true" : "false");
}

void EebusEgComponent::forget_pairing() {
  ESP_LOGW(TAG, "%s: pairing cleared", instance_name_.c_str());
  std::string old_ski = remote_ski_;
  save_remote_ski_nvs_("");
  remote_ski_.clear();
  device_label_.clear();
  pairing_state_ = "Inaktiv";
  /* Drop the existing connection — triggers on_entity_disconnect which resets all UI state */
  if (service_ && !old_ski.empty()) {
    EEBUS_SERVICE_UNREGISTER_REMOTE_SKI(service_, old_ski.c_str());
  }
}

void EebusEgComponent::refresh_heartbeat() {
  if (!eg_lpc_) return;
  if (!time_synced_) {
    time_synced_ = true;
    EgLpcStartHeartbeat(eg_lpc_);  /* store valid timestamp before any subscriber arrives */
  }
  if (!service_started_ && service_) {
    service_started_ = true;
    /* Startup announcement logged here (after time sync) so esphome logs captures it */
    ESP_LOGD("eebus", "SPINE local: device=EnergyManagementSystem entity=CEM(id=1) heartbeat=%us",
             kHeartbeatTimeoutSeconds);
    ESP_LOGD("eebus", "SPINE local use-case: actor=EnergyGuard(9) useCase=limitationOfPowerConsumption(14) [EG/LPC]");
    ESP_LOGD("eebus", "SPINE local use-case: actor=MonitoringAppliance(19) useCase=monitoringOfPowerConsumption(25) [MA/MPC]");
    EEBUS_SERVICE_START(service_);
    pairing_state_ = "Suche Gerät via mDNS...";
    ESP_LOGI(TAG, "EEBus %s service started after time sync", instance_name_.c_str());
  }
}

}  // namespace eebus_eg
}  // namespace esphome
