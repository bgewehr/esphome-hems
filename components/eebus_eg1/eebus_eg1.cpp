/*
 * Copyright 2025 bgewehr (bg-hems branch)
 * Licensed under the Apache License, Version 2.0
 */

#include "eebus_eg1.h"

#include <cmath>
#include <cstdarg>
#include <cstring>
#include <ctime>

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
#include "src/ship/api/mdns_entry.h"
#include "src/ship/tls_certificate/tls_certificate.h"
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
  auto* self = static_cast<esphome::eebus_eg1::EebusEg1Component*>(ctx);
  if (!payload) return;
  const char* ski = payload->ski ? payload->ski : "?";
  switch (payload->event_type) {
    case kEventTypeUseCaseChange: {
      const UseCaseFilterType* f = payload->use_case_filter;
      if (!f) break;
      const char* change = (payload->change_type == kElementChangeAdd)    ? "add"
                         : (payload->change_type == kElementChangeUpdate)  ? "update"
                         :                                                   "remove";
      const char* uc_str    = spine_uc_name(f->use_case_name_id);
      const char* actor_str = spine_actor_name(f->actor);
      ESP_LOGW("eebus", "SPINE use-case %s from %s: actor=%d(%s) useCase=%d(%s)",
               change, ski,
               (int)f->actor, actor_str,
               (int)f->use_case_name_id, uc_str);
      if (self) {
        const std::string& eg_ski = self->remote_ski();
        if (!eg_ski.empty() && eg_ski == ski) {
          bool add = (payload->change_type == kElementChangeAdd);
          bool rem = (payload->change_type == kElementChangeRemove);
          if (add || rem)
            self->on_remote_use_case(f->actor, f->use_case_name_id, uc_str, actor_str, add);
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
          ESP_LOGW("eebus_eg1", "OSSHPCF msg from %s: %s (fn=%d) — data=%s",
                   ski, e.name, (int)payload->function_type,
                   payload->function_data ? "present" : "null");
          break;
        }
      }
      ESP_LOGD("eebus_eg1", "SPINE data change from %s: fn=%d", ski, (int)payload->function_type);
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
namespace eebus_eg1 {

/* NVS key names (constant across all instances) */
static const char* NVS_KEY_CERT  = "cert_der";
static const char* NVS_KEY_KEY   = "key_der";
static const char* NVS_KEY_SKI   = "remote_ski";
/* Legacy NVS namespace used before multi-instance support — kept for migration only */
static const char* NVS_NS_LEGACY = "eebus_wp";

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

struct Eg1ServiceReader {
  ServiceReaderObject obj;   /* must be first */
  EebusEg1Component*  self;
};

extern "C" {

static void SR_Destruct(ServiceReaderObject*) {}

static void SR_OnRemoteSkiConnected(
    ServiceReaderObject* o, EebusServiceObject* /*svc*/, const char* ski)
{
  auto* r = reinterpret_cast<Eg1ServiceReader*>(o);
  ESP_LOGW("eebus_eg1", "Remote SKI connected: %s", ski);
  r->self->pairing_state_ = "Verbinde: " + std::string(ski);
}

static void SR_OnRemoteSkiDisconnected(
    ServiceReaderObject* o, EebusServiceObject* /*svc*/, const char* ski)
{
  auto* r = reinterpret_cast<Eg1ServiceReader*>(o);
  ESP_LOGW("eebus_eg1", "Remote SKI disconnected: %s", ski);
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
  auto* r = reinterpret_cast<Eg1ServiceReader*>(o);
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
    ESP_LOGI("eebus_eg1", "mDNS: EG1 sichtbar ski=%s host=%s — warte auf eingehende Verbindung",
             ski, host);
    r->self->pairing_state_ = "Gerät sichtbar: " + std::string(ski) + " — warte auf Verbindung";
    break;
  }
}

static void SR_OnShipIdUpdate(
    ServiceReaderObject* o, const char* ski, const char* ship_id)
{
  auto* r = reinterpret_cast<Eg1ServiceReader*>(o);
  ESP_LOGD("eebus_eg1", "SHIP ID update ski=%s id=%s", ski, ship_id ? ship_id : "");
  if (ship_id && ship_id[0] != '\0' && ski && r->self->remote_ski_ == ski) {
    r->self->device_label_ = ship_id;
    ESP_LOGI("eebus_eg1", "EG1 device name: %s", ship_id);
  }
}

static void SR_OnShipStateUpdate(
    ServiceReaderObject* o, const char* ski, SmeState state)
{
  auto* r = reinterpret_cast<Eg1ServiceReader*>(o);
  ESP_LOGW("eebus_eg1", "SHIP state ski=%s state=%d", ski, (int)state);
  if (state == kDataExchange) {
    /* Reject connections where TLS peer cert extraction failed.
     * Root cause: CONFIG_MBEDTLS_SSL_KEEP_PEER_CERTIFICATE not set in sdkconfig. */
    if (!ski || strcmp(ski, "unknown") == 0 || strlen(ski) < 40) {
      ESP_LOGE("eebus_eg1", "DataExchange mit ungültiger SKI '%s' — Pairing ignoriert. "
               "Prüfe CONFIG_MBEDTLS_SSL_KEEP_PEER_CERTIFICATE=y in sdkconfig.",
               ski ? ski : "null");
      return;
    }
    r->self->remote_ski_ = ski;
    r->self->pairing_state_ = "Gepairt: " + std::string(ski);
    ESP_LOGW("eebus_eg1", "EG1 pairing approved, remote SKI=%s", ski);
    r->self->save_remote_ski_nvs_(ski);
    r->self->on_ship_data_exchange_(ski);
  }
}

static bool SR_IsWaitingForTrustAllowed(const ServiceReaderObject* o, const char* ski) {
  if (!ski || strcmp(ski, "unknown") == 0 || strlen(ski) < 20) return false;
  const auto* self = reinterpret_cast<const Eg1ServiceReader*>(o)->self;
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

void EebusEg1Component::setup() {
  ESP_LOGI(TAG, "Setting up EEBus EG1 controller");

  /* Derive per-instance NVS namespace from SHIP port (e.g. "eg4713").
   * Keeps instances isolated when MULTI_CONF is used.
   * Auto-migrates cert+SKI from the legacy "eebus_wp" namespace on first boot. */
  {
    char ns_buf[16];
    snprintf(ns_buf, sizeof(ns_buf), "eg%u", (unsigned)ship_port_);
    nvs_ns_ = ns_buf;

    uint8_t* mc = nullptr; size_t mcl = 0;
    uint8_t* mk = nullptr; size_t mkl = 0;
    if (!load_cert_nvs_(&mc, &mcl, &mk, &mkl)) {
      /* No cert in port-based namespace — try legacy "eebus_wp" */
      std::string new_ns = nvs_ns_;
      nvs_ns_ = NVS_NS_LEGACY;
      if (load_cert_nvs_(&mc, &mcl, &mk, &mkl)) {
        std::string old_ski = load_remote_ski_nvs_();
        nvs_ns_ = new_ns;
        store_cert_nvs_(mc, mcl, mk, mkl);
        if (!old_ski.empty()) save_remote_ski_nvs_(old_ski.c_str());
        ESP_LOGI(TAG, "Migrated NVS cert+SKI from '%s' to '%s'", NVS_NS_LEGACY, nvs_ns_.c_str());
        free(mc); free(mk); mc = nullptr; mk = nullptr; mcl = mkl = 0;
      } else {
        nvs_ns_ = new_ns;
      }
    }
    if (mc) { free(mc); free(mk); }
  }

  /* Load paired remote CS device SKI from NVS if not set in YAML secrets */
  if (remote_ski_.empty()) {
    remote_ski_ = load_remote_ski_nvs_();
    if (!remote_ski_.empty()) {
      if (remote_ski_ == "unknown" || remote_ski_.length() < 20) {
        ESP_LOGW(TAG, "Clearing invalid SKI from NVS: '%s'", remote_ski_.c_str());
        save_remote_ski_nvs_("");
        remote_ski_.clear();
      } else {
        ESP_LOGW(TAG, "Loaded paired EG1 SKI from NVS: %s", remote_ski_.c_str());
      }
    }
  }

  uint8_t* cert = nullptr; size_t cl = 0;
  uint8_t* key  = nullptr; size_t kl = 0;

  if (!load_cert_nvs_(&cert, &cl, &key, &kl)) {
    ESP_LOGI(TAG, "Generating certificate...");
    if (!load_or_generate_cert_() || !load_cert_nvs_(&cert, &cl, &key, &kl)) {
      ESP_LOGE(TAG, "Certificate setup failed");
      mark_failed(); return;
    }
  }

  if (!start_eebus_service_(cert, cl, key, kl)) {
    ESP_LOGE(TAG, "Failed to start openeebus service");
    free(cert); free(key);
    mark_failed(); return;
  }

  free(cert); free(key);
  startup_mdns_at_ms_ = millis() + 15000;  /* one-shot mDNS announce after services are up */
  ESP_LOGI(TAG, "EEBus %s ready — heartbeat interval: %u s", instance_name_.c_str(), kHeartbeatTimeoutSeconds);
}

/* =========================================================================
 * loop()
 * ====================================================================== */

void EebusEg1Component::loop() {
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
    ESP_LOGW(TAG, "Pairing window expired");
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

void EebusEg1Component::dump_config() {
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

void EebusEg1Component::start_heartbeat_test() {
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

void EebusEg1Component::set_limit(float watts) {
  if (!connected_ || !have_remote_entity_ || !eg_lpc_) {
    ESP_LOGW(TAG, "set_limit(%.0f W) — %s not connected", watts, instance_name_.c_str());
    return;
  }

  /* §14a: never limit below 4200 W (WP also enforces this internally) */
  if (watts > 0.0f && watts < 4200.0f) {
    ESP_LOGW(TAG, "Clamping %.0f W → 4200 W (§14a minimum)", watts);
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

void EebusEg1Component::on_remote_use_case(int actor, int uc_name_id, const char* /*uc_str*/, const char* /*actor_str*/, bool add) {
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
    if (remote_uc_seen_.find(buf) == std::string::npos)
      remote_uc_seen_ += buf;
  } else {
    auto pos = remote_uc_seen_.find(buf);
    if (pos != std::string::npos) {
      remote_uc_seen_.erase(pos, strlen(buf));
      ESP_LOGW(TAG, "Use case removed by remote: %s", buf + 3);  /* skip " | " */
    }
  }
}

void EebusEg1Component::on_entity_connect(const EntityAddressType* addr) {
  ESP_LOGI(TAG, "%s entity connected", instance_name_.c_str());
  connected_          = true;
  heartbeat_alarm_    = false;
  connected_since_ms_ = millis();
  have_remote_entity_ = true;
  if (addr) remote_entity_addr_ = *addr;
  pairing_state_      = "Verbunden";
  failsafe_set_       = false;   /* loop() will retry until DeviceConfiguration data is available */
  failsafe_retry_ms_  = 0;

  for (auto* t : connected_triggers_) t->trigger();
}

void EebusEg1Component::on_entity_disconnect(const EntityAddressType* /*addr*/) {
  if (!connected_ && !have_remote_entity_) return;  /* guard: SPINE and SHIP both fire disconnect */
  ESP_LOGW(TAG, "%s entity disconnected", instance_name_.c_str());
  connected_          = false;
  mpc_connected_      = false;
  last_heartbeat_ms_  = 0;
  have_remote_entity_ = false;
  active_limit_w_     = 0.0f;  /* WP applies its own failsafe while disconnected — we don't control it */
  current_power_w_    = 0.0f;  /* stale reading no longer valid */
  pending_limit_w_    = -1.0f;
  remote_uc_seen_     = {};
  pairing_state_      = "Getrennt — suche Gerät...";
  /* Re-announce mDNS so the remote device reconnects immediately (eebus-go: checkAutoReannounce on disconnect) */
  set_mdns_register(false);

  /* Do NOT stop the heartbeat on disconnect — eebus-go never stops it.
   * Keeping it running ensures the feature data is always fresh for the
   * next time the remote device connects and subscribes. */

  for (auto* t : disconnected_triggers_) t->trigger();
}

void EebusEg1Component::on_power_limit_ack(float watts, bool active) {
  if (active) {
    ESP_LOGD(TAG, "%s ACK limit %.0f W active=yes", instance_name_.c_str(), watts);
    active_limit_w_ = watts;
  } else {
    ESP_LOGD(TAG, "%s ACK limit cleared (active=no)", instance_name_.c_str());
    active_limit_w_ = 0.0f;
  }
  pending_limit_w_ = -1.0f;  /* ACK received — cancel any pending retry */
}

void EebusEg1Component::on_mpc_measurement(float watts) {
  current_power_w_ = watts;
  for (auto* t : power_triggers_) t->trigger(watts);
}

/* =========================================================================
 * NVS certificate helpers
 * ====================================================================== */

bool EebusEg1Component::store_cert_nvs_(
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

bool EebusEg1Component::load_cert_nvs_(
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

void EebusEg1Component::save_remote_ski_nvs_(const char* ski) {
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

std::string EebusEg1Component::load_remote_ski_nvs_() {
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

bool EebusEg1Component::load_or_generate_cert_() {
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

    std::string subj = "CN=" + device_model_ + "-EG1,O=" + device_brand_ + ",C=DE";
    mbedtls_x509write_crt_set_subject_key(&crt, &pk);
    mbedtls_x509write_crt_set_issuer_key(&crt, &pk);
    mbedtls_x509write_crt_set_subject_name(&crt, subj.c_str());
    mbedtls_x509write_crt_set_issuer_name(&crt, subj.c_str());
    mbedtls_x509write_crt_set_version(&crt, MBEDTLS_X509_CRT_VERSION_3);
    mbedtls_x509write_crt_set_md_alg(&crt, MBEDTLS_MD_SHA256);
    // Use non-deprecated serial API (mbedTLS 3.x / ESP-IDF 5.x)
    uint8_t serial_bytes[] = {0x01};
    mbedtls_x509write_crt_set_serial_raw(&crt, serial_bytes, sizeof(serial_bytes));
    mbedtls_x509write_crt_set_validity(&crt, "20250101000000", "20350101000000");
    mbedtls_x509write_crt_set_subject_key_identifier(&crt);
    mbedtls_x509write_crt_set_authority_key_identifier(&crt);

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

bool EebusEg1Component::start_eebus_service_(
    const uint8_t* cert_der, size_t cert_len,
    const uint8_t* key_der,  size_t key_len)
{
  /* Parse TLS certificate */
  TlsCertificateObject* tls_cert = TlsCertificateParseX509KeyPair(
      (const char*)cert_der, cert_len, (const char*)key_der, key_len);
  if (!tls_cert) { ESP_LOGE(TAG, "TlsCertificateParse failed"); return false; }

  const char* local_ski = TLS_CERTIFICATE_GET_SKI(tls_cert);
  if (local_ski) local_ski_ = local_ski;
  ESP_LOGI(TAG, "%s local SKI: %s", instance_name_.c_str(), local_ski ? local_ski : "(null)");

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

void EebusEg1Component::on_ship_data_exchange_(const char* ski) {
  ESP_LOGW(TAG, "%s data exchange active — SKI persisted: %s", instance_name_.c_str(), ski);
  if (pairing_mode_active_) {
    pairing_mode_active_ = false;
    pairing_deadline_ms_ = 0;
    if (service_) EEBUS_SERVICE_SET_PAIRING_POSSIBLE(service_, false);
    mdns_service_txt_item_set("_ship", "_tcp", "register", "false");
    ESP_LOGW(TAG, "mDNS: register TXT -> false (data exchange active)");
    ESP_LOGW(TAG, "Pairing mode exited after successful connection");
  }
}

void EebusEg1Component::enter_pairing_mode() {
  ESP_LOGW(TAG, "Pairing mode activated (window: %u s)", kPairingWindowMs / 1000);
  pairing_mode_active_ = true;
  pairing_deadline_ms_ = millis() + kPairingWindowMs;
  if (service_) EEBUS_SERVICE_SET_PAIRING_POSSIBLE(service_, true);
  /* immediate first advert; loop() repeats every 5 s while pairing window is open */
  set_mdns_register(true);
  pairing_advert_at_ms_ = millis() + 5000;
  pairing_state_ = "Pairing-Modus aktiv — warte auf Verbindung...";
}

void EebusEg1Component::set_mdns_register(bool val) {
  mdns_service_txt_item_set("_ship", "_tcp", "register", val ? "true" : "false");
  ESP_LOGW(TAG, "mDNS: register TXT -> %s", val ? "true" : "false");
}

void EebusEg1Component::forget_pairing() {
  ESP_LOGW(TAG, "Pairing forgotten");
  std::string old_ski = remote_ski_;
  save_remote_ski_nvs_("");
  remote_ski_.clear();
  device_label_.clear();
  /* Drop the existing connection — triggers on_entity_disconnect which resets all UI state */
  if (service_ && !old_ski.empty()) {
    EEBUS_SERVICE_UNREGISTER_REMOTE_SKI(service_, old_ski.c_str());
  }
  enter_pairing_mode();
}

void EebusEg1Component::refresh_heartbeat() {
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

}  // namespace eebus_eg1
}  // namespace esphome
