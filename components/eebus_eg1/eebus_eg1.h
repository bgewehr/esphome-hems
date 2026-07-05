/*
 * Copyright 2025 bgewehr (bg-hems branch)
 * Licensed under the Apache License, Version 2.0
 */
/**
 * @file eebus_eg1.h
 * @brief ESPHome component: EEBus EG/LPC sender to heat pump EG gateway.
 *
 * Heartbeat: openeebus drives the heartbeat automatically via its internal
 * FreeRTOS 1-second tick (DeviceLocal1sTickCallback → HEARTBEAT_MANAGER_TICK).
 * EgLpcStartHeartbeat() activates the HeartbeatManager; the timeout is set
 * at EntityLocalCreate() time (kHeartbeatTimeoutSeconds = 60 s, SPINE spec standard).
 * WP raises a fault if no heartbeat is received within 2× the timeout.
 *
 * API for YAML lambdas:
 *   id(hems_eg1).set_limit(watts)     — send LPC limit now (min 4200 W)
 *   id(hems_eg1).clear_limit()        — remove limit (full power)
 *   id(hems_eg1).current_power_w()    — last MPC reading from WP (W)
 *   id(hems_eg1).is_connected()       — WP connection state
 *   id(hems_eg1).remote_ski()         — Remote WP SKI
 */

#pragma once

#include <string>
#include <vector>

#include "esphome/core/component.h"
#include "esphome/core/automation.h"
#include "esphome/core/log.h"

extern "C" {
#include "src/service/api/eebus_service_interface.h"
#include "src/service/api/eebus_service_config.h"
#include "src/service/api/service_reader_interface.h"
#include "src/ship/model/types.h"
#include "src/use_case/api/eg_lp_listener_interface.h"
#include "src/use_case/actor/eg/lpc/eg_lpc.h"
#include "src/use_case/actor/ma/mpc/ma_mpc.h"
#include "src/use_case/api/ma_mpc_listener_interface.h"
#include "src/use_case/model/mpc_types.h"
#include "src/use_case/model/load_limit_types.h"
#include "src/use_case/model/scaled_value.h"
#include "src/spine/api/entity_local_interface.h"
}

namespace esphome {
namespace eebus_eg1 {

static const char* const TAG = "eebus_eg1";

class EebusEg1Component;

/* -------------------------------------------------------------------------
 * Triggers
 * ---------------------------------------------------------------------- */
class Eg1ConnectedTrigger    : public Trigger<>      { public: explicit Eg1ConnectedTrigger(EebusEg1Component* p); };
class Eg1DisconnectedTrigger : public Trigger<>      { public: explicit Eg1DisconnectedTrigger(EebusEg1Component* p); };
class Eg1PowerReadingTrigger : public Trigger<float> { public: explicit Eg1PowerReadingTrigger(EebusEg1Component* p); };

/* -------------------------------------------------------------------------
 * Main component
 * ---------------------------------------------------------------------- */

class EebusEg1Component : public Component {
 public:
  void setup() override;
  void loop() override;
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI; }
  void dump_config() override;

  /* Config setters */
  void set_ship_port(uint16_t p)               { ship_port_           = p; }
  void set_remote_ski(const std::string& s)    { remote_ski_          = s; }
  void set_instance_name(const std::string& n) { instance_name_       = n; }
  void set_device_brand(const std::string& b)  { device_brand_        = b; }
  void set_device_type(const std::string& t)   { device_type_         = t; }
  void set_device_model(const std::string& m)  { device_model_        = m; }
  void set_failsafe_limit_w(float w)           { failsafe_limit_w_    = w; }
  void set_failsafe_duration_s(uint32_t s)     { failsafe_duration_s_ = s; }

  /* Runtime update from UI — re-triggers failsafe negotiation with new value */
  void update_failsafe_limit_w(float w) {
    failsafe_limit_w_   = w;
    failsafe_set_       = false;
    failsafe_retry_ms_  = 0;
  }
  void update_failsafe_duration_s(uint32_t s) {
    failsafe_duration_s_ = s;
    failsafe_set_        = false;
    failsafe_retry_ms_   = 0;
  }

  /* Trigger registration */
  void add_on_eg1_connected_trigger(Eg1ConnectedTrigger* t)       { connected_triggers_.push_back(t); }
  void add_on_eg1_disconnected_trigger(Eg1DisconnectedTrigger* t) { disconnected_triggers_.push_back(t); }
  void add_on_power_reading_trigger(Eg1PowerReadingTrigger* t)    { power_triggers_.push_back(t); }

  /* -----------------------------------------------------------------------
   * Public API — callable from YAML lambdas
   * -------------------------------------------------------------------- */
  void set_limit(float watts);
  void clear_limit() { set_limit(0.0f); }

  /** Call from on_time_sync (SNTP or HA) to push a fresh heartbeat timestamp.
   *  Prevents the remote CS device from seeing an epoch timestamp on its first subscription. */
  void refresh_heartbeat();

  /* Pairing management */
  void enter_pairing_mode();
  void forget_pairing();
  void set_mdns_register(bool val);
  bool is_pairing_mode() const { return pairing_mode_active_; }

  /* Diagnostic test: stop outbound heartbeat and block reconnect for 120 s.
   * The remote CS device will apply its failsafe after its heartbeat timeout (2× 60 s),
   * and cannot reconnect until the test period ends.
   * After the pause the heartbeat is restarted and the SKI re-registered. */
  void start_heartbeat_test();

  /* State accessors */
  bool        is_connected()       const { return connected_; }
  bool        is_heartbeat_alarm() const { return heartbeat_alarm_; }
  bool        mpc_connected()      const { return mpc_connected_; }
  float       current_power_w()    const { return current_power_w_; }
  float       active_limit_w()     const { return active_limit_w_; }
  std::string remote_ski()         const { return remote_ski_; }
  std::string local_ski()          const { return local_ski_; }
  std::string pairing_state()      const { return pairing_state_; }
  std::string device_label()       const { return device_label_; }
  std::string supported_use_cases() const {
    if (!remote_uc_seen_.empty())
      return remote_uc_seen_.size() > 3 ? remote_uc_seen_.substr(3) : remote_uc_seen_;
    if (connected_ || mpc_connected_) return std::string("(ausstehend)");
    return std::string("(keine)");
  }
  std::string active_use_cases() const {
    std::string r;
    if (connected_)     r += " | CS/LPC";
    if (mpc_connected_) r += " | MU/MPC";
    return r.empty() ? std::string("(keine)") : r.substr(3);
  }
  void on_remote_use_case(int actor, int uc_name_id, const char* uc_str, const char* actor_str);

  /* Called from C vtable (public for Eg1ServiceReader friend access) */
  void on_entity_connect(const EntityAddressType* addr);
  void on_entity_disconnect(const EntityAddressType* addr);
  void on_power_limit_ack(float watts, bool active);
  void on_mpc_measurement(float watts);
  void on_mpc_state_(bool connected) { mpc_connected_ = connected; }
  void on_heartbeat_received_() { last_heartbeat_ms_ = millis(); }

  /* Public for ServiceReader vtable access */
  std::string pairing_state_       {};
  std::string remote_ski_          {};
  std::string local_ski_           {};
  std::string device_label_        {};
  bool        pairing_mode_active_ {false};
  uint32_t    pairing_deadline_ms_ {0};
  uint32_t    connected_since_ms_  {0};
  void save_remote_ski_nvs_(const char* ski);
  void on_ship_data_exchange_(const char* ski);  /* called by vtable on kDataExchange */

 protected:
  bool load_or_generate_cert_();
  bool store_cert_nvs_(const uint8_t* c, size_t cl, const uint8_t* k, size_t kl);
  bool load_cert_nvs_(uint8_t** c, size_t* cl, uint8_t** k, size_t* kl);
  std::string load_remote_ski_nvs_();
  bool start_eebus_service_(const uint8_t* cert, size_t cl,
                             const uint8_t* key,  size_t kl);

  /* Config */
  uint16_t    ship_port_          {4713};
  std::string instance_name_      {"EG"};
  std::string device_brand_       {"DIY"};
  std::string device_type_        {"HEMS"};
  std::string device_model_       {"ESP32-HEMS-14a"};
  float       failsafe_limit_w_   {4200.0f};
  uint32_t    failsafe_duration_s_{7200};

  /* Runtime — nvs_ns_ computed in setup() from ship_port_; migrates legacy "eebus_wp" namespace */
  std::string nvs_ns_             {};
  bool        connected_          {false};
  bool        mpc_connected_      {false};
  std::string remote_uc_seen_     {};
  bool        heartbeat_alarm_    {false};
  uint32_t    heartbeat_test_until_ms_ {0}; /* non-zero while failsafe test is active */
  bool        time_synced_        {false};
  bool        service_started_    {false};
  bool        failsafe_set_       {false};
  uint32_t    failsafe_retry_ms_  {0};
  bool        startup_mdns_done_  {false};
  uint32_t    startup_mdns_at_ms_ {0};
  uint32_t    pairing_advert_at_ms_{0};
  float       current_power_w_    {0.0f};
  float       active_limit_w_     {0.0f};
  uint32_t    last_heartbeat_ms_  {0};

  EntityAddressType remote_entity_addr_{};
  bool              have_remote_entity_{false};

  /* openeebus objects */
  EebusServiceObject*  service_      {nullptr};
  EntityLocalObject*   local_entity_ {nullptr};
  EgLpUseCaseObject*   eg_lpc_       {nullptr};
  MaMpcUseCaseObject*  ma_mpc_       {nullptr};

  /* Trigger lists */
  std::vector<Eg1ConnectedTrigger*>    connected_triggers_;
  std::vector<Eg1DisconnectedTrigger*> disconnected_triggers_;
  std::vector<Eg1PowerReadingTrigger*> power_triggers_;

  /* C-vtable wrappers */
  struct Eg1ServiceReader {
    ServiceReaderObject obj;   /* must be first */
    EebusEg1Component*  self;
  } service_reader_{};

 public:  /* public so free C-linkage vtable functions can reinterpret_cast */
  struct EgListener {
    EgLpListenerObject obj;   /* must be first */
    EebusEg1Component* self;
  } eg_listener_{};

  struct MpcListener {
    MaMpcListenerObject obj;  /* must be first */
    EebusEg1Component*  self;
  } mpc_listener_{};

};

/* -------------------------------------------------------------------------
 * C vtable for EgLpListenerInterface
 * ---------------------------------------------------------------------- */

extern "C" {

/* ---- MaMpc listener vtable ---- */

static void MpcL_Destruct(MaMpcListenerObject*) {}

static void MpcL_OnRemoteEntityConnect(MaMpcListenerObject* o, const EntityAddressType* addr) {
  auto* self = reinterpret_cast<EebusEg1Component::MpcListener*>(o)->self;
  // Remote CS device may announce MU/MPC on multiple SPINE entities; suppress duplicates.
  if (self->mpc_connected()) return;
  ESP_LOGW("eebus_eg1", "MPC: remote measurement unit connected");
  ESP_LOGI("eebus", "SPINE remote MA/MPC entity connected: ski=%s",
           (addr && addr->device) ? addr->device : "?");
  self->on_mpc_state_(true);
}
static void MpcL_OnRemoteEntityDisconnect(MaMpcListenerObject* o, const EntityAddressType*) {
  ESP_LOGW("eebus_eg1", "MPC: remote measurement unit disconnected");
  reinterpret_cast<EebusEg1Component::MpcListener*>(o)->self->on_mpc_state_(false);
}
static void MpcL_OnMeasurementReceive(
    MaMpcListenerObject* o,
    MuMpcMeasurementNameId name_id,
    const ScaledValue* val,
    const EntityAddressType* entity_addr)
{
  if (!val) return;
  float v = (float)val->value * powf(10.0f, (float)val->scale);
  const char* name = MuMpcMeasurementGetName(name_id);
  /* Log raw ScaledValue (value * 10^scale) so diagnostics survive float truncation */
  ESP_LOGI("eebus_eg1", "MPC measurement [id=%d]: %s = %lld * 10^%d = %.3f W (from: %s)",
           (int)name_id,
           name ? name : "unknown",
           (long long)val->value, (int)val->scale,
           (double)v,
           (entity_addr && entity_addr->device) ? entity_addr->device : "?");
  if (name_id == kMpcPowerTotal) {
    reinterpret_cast<EebusEg1Component::MpcListener*>(o)->self->on_mpc_measurement(v);
  }
}

static const MaMpcListenerInterface kMpcListenerMethods = {
  .destruct                  = MpcL_Destruct,
  .on_remote_entity_connect  = MpcL_OnRemoteEntityConnect,
  .on_remote_entity_disconnect = MpcL_OnRemoteEntityDisconnect,
  .on_measurement_receive    = MpcL_OnMeasurementReceive,
};

/* ---- EgLp listener vtable ---- */

static void EgL_Destruct(EgLpListenerObject*) {}

static void EgL_OnRemoteEntityConnect(EgLpListenerObject* o, const EntityAddressType* addr) {
  ESP_LOGI("eebus", "SPINE remote EG/LPC entity connected: ski=%s",
           (addr && addr->device) ? addr->device : "?");
  reinterpret_cast<EebusEg1Component::EgListener*>(o)->self->on_entity_connect(addr);
}
static void EgL_OnRemoteEntityDisconnect(EgLpListenerObject* o, const EntityAddressType* addr) {
  reinterpret_cast<EebusEg1Component::EgListener*>(o)->self->on_entity_disconnect(addr);
}
static void EgL_OnPowerLimitReceive(
    EgLpListenerObject* o, const EntityAddressType*,
    const ScaledValue* limit, const DurationType*, bool active)
{
  float w = (float)limit->value * powf(10.0f, (float)limit->scale);
  reinterpret_cast<EebusEg1Component::EgListener*>(o)->self->on_power_limit_ack(w, active);
}
static void EgL_OnFailsafePowerLimitReceive(EgLpListenerObject*, const EntityAddressType*, const ScaledValue*) {}
static void EgL_OnFailsafeDurationReceive(EgLpListenerObject*, const EntityAddressType*, const DurationType*) {}
static void EgL_OnHeartbeatReceive(EgLpListenerObject* o, const EntityAddressType*, uint64_t /*hb_counter*/) {
  reinterpret_cast<EebusEg1Component::EgListener*>(o)->self->on_heartbeat_received_();
}

static const EgLpListenerInterface kEgListenerMethods = {
  .destruct                        = EgL_Destruct,
  .on_remote_entity_connect        = EgL_OnRemoteEntityConnect,
  .on_remote_entity_disconnect     = EgL_OnRemoteEntityDisconnect,
  .on_power_limit_receive          = EgL_OnPowerLimitReceive,
  .on_failsafe_power_limit_receive = EgL_OnFailsafePowerLimitReceive,
  .on_failsafe_duration_receive    = EgL_OnFailsafeDurationReceive,
  .on_heartbeat_receive            = EgL_OnHeartbeatReceive,
};

}  // extern "C"

/* Trigger constructors */
inline Eg1ConnectedTrigger::Eg1ConnectedTrigger(EebusEg1Component* p)
  { p->add_on_eg1_connected_trigger(this); }
inline Eg1DisconnectedTrigger::Eg1DisconnectedTrigger(EebusEg1Component* p)
  { p->add_on_eg1_disconnected_trigger(this); }
inline Eg1PowerReadingTrigger::Eg1PowerReadingTrigger(EebusEg1Component* p)
  { p->add_on_power_reading_trigger(this); }

}  // namespace eebus_eg1
}  // namespace esphome
