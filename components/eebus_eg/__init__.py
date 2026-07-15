"""ESPHome external component: eebus_eg

EEBus SHIP/SPINE LPC Energy Guard (EG) actor — sends power limits to the
connected CS (Controllable System) device, e.g. a heat pump EEBus gateway.

The HEMS acts as CEM/EG, the remote CS device is discovered automatically via mDNS (_ship._tcp).

Typical EEBus use cases announced by CS devices:
  LPC  — Limitation of Power Consumption  (we send limits)
  MPC  — Monitoring of Power Consumption  (we read actual power)
  OSSHPCF — PV optimisation (compressor flexibility scheduling)

Example YAML:
    eebus_eg:
      id: eg1
      instance_name: "WP"
      remote_ski: "aabbcc..."   # SKI of remote CS device — from web UI after pairing
      on_eg_connected:
        - logger.log: "EG device connected"
      on_eg_disconnected:
        - logger.log: "EG device disconnected"
"""

import os
import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import automation
from esphome.components import socket, text_sensor
from esphome.const import CONF_ID, CONF_TRIGGER_ID

DEPENDENCIES = ["network", "esp32", "text_sensor"]
CODEOWNERS   = ["@bgewehr"]
MULTI_CONF   = True

eebus_eg_ns      = cg.esphome_ns.namespace("eebus_eg")
EebusEgComponent = eebus_eg_ns.class_("EebusEgComponent", cg.Component)

EgConnectedTrigger    = eebus_eg_ns.class_("EgConnectedTrigger",    automation.Trigger.template())
EgDisconnectedTrigger = eebus_eg_ns.class_("EgDisconnectedTrigger", automation.Trigger.template())
EgPowerReadingTrigger = eebus_eg_ns.class_("EgPowerReadingTrigger", automation.Trigger.template(cg.float_))

CONF_REMOTE_SKI          = "remote_ski"
CONF_SHIP_PORT           = "ship_port"
CONF_INSTANCE_NAME       = "instance_name"
CONF_DEVICE_BRAND        = "device_brand"
CONF_DEVICE_TYPE         = "device_type"
CONF_DEVICE_MODEL        = "device_model"
CONF_FAILSAFE_LIMIT_W    = "failsafe_limit_w"
CONF_FAILSAFE_DURATION_S = "failsafe_duration_s"
CONF_ON_EG_CONNECTED    = "on_eg_connected"
CONF_ON_EG_DISCONNECTED = "on_eg_disconnected"
CONF_ON_POWER_READING    = "on_power_reading"
CONF_SUPPORTED_USE_CASES_TEXT_SENSOR = "supported_use_cases_text_sensor"

def _consume_eebus_eg_sockets(config):
    # httpd_ssl instance: 1 HTTPS listen + 2 active WS connections + 1 ctrl_port = 4 sockets
    socket.consume_sockets(1, "eebus_eg", socket.SocketType.TCP_LISTEN)(config)
    socket.consume_sockets(3, "eebus_eg")(config)
    return config


CONFIG_SCHEMA = cv.All(
    cv.Schema({
        cv.GenerateID(): cv.declare_id(EebusEgComponent),
        cv.Optional(CONF_SHIP_PORT,           default=4713):    cv.port,
        cv.Optional(CONF_REMOTE_SKI,          default=""):      cv.string,
        cv.Optional(CONF_INSTANCE_NAME,       default="EG"):    cv.string_strict,
        cv.Optional(CONF_DEVICE_BRAND,        default="DIY"):   cv.string_strict,
        cv.Optional(CONF_DEVICE_TYPE,         default="HEMS"):  cv.string_strict,
        cv.Optional(CONF_DEVICE_MODEL,        default="ESP32-HEMS-14a"): cv.string_strict,
        cv.Optional(CONF_FAILSAFE_LIMIT_W,    default=4200.0):  cv.positive_float,
        cv.Optional(CONF_FAILSAFE_DURATION_S, default=7200):    cv.positive_int,  # 2h default
        cv.Optional(CONF_SUPPORTED_USE_CASES_TEXT_SENSOR): cv.use_id(text_sensor.TextSensor),
        cv.Optional(CONF_ON_EG_CONNECTED): automation.validate_automation({
            cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(EgConnectedTrigger),
        }),
        cv.Optional(CONF_ON_EG_DISCONNECTED): automation.validate_automation({
            cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(EgDisconnectedTrigger),
        }),
        cv.Optional(CONF_ON_POWER_READING): automation.validate_automation({
            cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(EgPowerReadingTrigger),
        }),
    }).extend(cv.COMPONENT_SCHEMA),
    _consume_eebus_eg_sockets,
)


async def to_code(config):
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    openeebus_root = os.path.join(repo_root, "openeebus")
    for path in (repo_root, openeebus_root):
        cg.add_build_flag("-I" + path.replace("\\", "/"))
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    cg.add(var.set_ship_port(config[CONF_SHIP_PORT]))
    cg.add(var.set_remote_ski(config[CONF_REMOTE_SKI]))
    cg.add(var.set_instance_name(config[CONF_INSTANCE_NAME]))
    cg.add(var.set_device_brand(config[CONF_DEVICE_BRAND]))
    cg.add(var.set_device_type(config[CONF_DEVICE_TYPE]))
    cg.add(var.set_device_model(config[CONF_DEVICE_MODEL]))
    cg.add(var.set_failsafe_limit_w(config[CONF_FAILSAFE_LIMIT_W]))
    cg.add(var.set_failsafe_duration_s(config[CONF_FAILSAFE_DURATION_S]))

    if CONF_SUPPORTED_USE_CASES_TEXT_SENSOR in config:
        supported_use_cases_sensor = await cg.get_variable(config[CONF_SUPPORTED_USE_CASES_TEXT_SENSOR])
        cg.add(var.set_supported_use_cases_text_sensor(supported_use_cases_sensor))

    for conf in config.get(CONF_ON_EG_CONNECTED, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)
        await automation.build_automation(trigger, [], conf)

    for conf in config.get(CONF_ON_EG_DISCONNECTED, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)
        await automation.build_automation(trigger, [], conf)

    for conf in config.get(CONF_ON_POWER_READING, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)
        await automation.build_automation(trigger, [(cg.float_, "x")], conf)
