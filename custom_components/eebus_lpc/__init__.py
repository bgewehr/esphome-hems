"""ESPHome external component: eebus_lpc
Kommuniziert mit der eebus-ha-bridge (volschin/eebus-ha-bridge) über gRPC/HTTP2.
Ziel: LPC-Steuerung für EEBUS-fähige Wärmepumpen (Bosch, Vaillant).
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import sensor, binary_sensor, number, switch, text_sensor
from esphome.const import (
    CONF_ID,
    DEVICE_CLASS_POWER,
    DEVICE_CLASS_CONNECTIVITY,
    UNIT_WATT,
    STATE_CLASS_MEASUREMENT,
    ENTITY_CATEGORY_DIAGNOSTIC,
)

CODEOWNERS = ["@volschin"]
AUTO_LOAD = ["sensor", "binary_sensor", "number", "switch", "socket", "text_sensor"]
DEPENDENCIES = ["network"]

# Namespace
eebus_lpc_ns = cg.esphome_ns.namespace("eebus_lpc")
EebusLpcComponent = eebus_lpc_ns.class_("EebusLpcComponent", cg.Component)
EebusLpcLimitNumber = eebus_lpc_ns.class_(
    "EebusLpcLimitNumber", number.Number, cg.Component
)
EebusLpcActiveSwitch = eebus_lpc_ns.class_(
    "EebusLpcActiveSwitch", switch.Switch, cg.Component
)

# Config keys
CONF_BRIDGE_HOST = "bridge_host"
CONF_BRIDGE_PORT = "bridge_port"
CONF_DEVICE_SKI = "device_ski"
CONF_POLL_INTERVAL = "poll_interval"
CONF_POWER_SENSOR = "power_sensor"
CONF_LIMIT_SENSOR = "limit_sensor"
CONF_CONNECTED_SENSOR = "connected_sensor"
CONF_HEARTBEAT_SENSOR = "heartbeat_sensor"
CONF_LPC_ACTIVE_SENSOR = "lpc_active_sensor"
CONF_LPC_LIMIT_NUMBER = "lpc_limit_number"
CONF_LPC_ACTIVE_SWITCH = "lpc_active_switch"
CONF_FAILSAFE_LIMIT_SENSOR = "failsafe_limit_sensor"
CONF_FAILSAFE_DURATION_SENSOR = "failsafe_duration_sensor"
CONF_BRAND_SENSOR = "brand_sensor"
CONF_MODEL_SENSOR = "model_sensor"
CONF_SERIAL_SENSOR = "serial_sensor"

# Sub-schemas for optional entities
SENSOR_SCHEMA = sensor.sensor_schema(
    unit_of_measurement=UNIT_WATT,
    accuracy_decimals=0,
    device_class=DEVICE_CLASS_POWER,
    state_class=STATE_CLASS_MEASUREMENT,
)

LIMIT_SENSOR_SCHEMA = sensor.sensor_schema(
    unit_of_measurement=UNIT_WATT,
    accuracy_decimals=0,
    device_class=DEVICE_CLASS_POWER,
    state_class=STATE_CLASS_MEASUREMENT,
    entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
)

BINARY_SENSOR_SCHEMA = binary_sensor.binary_sensor_schema(
    device_class=DEVICE_CLASS_CONNECTIVITY,
    entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
)

NUMBER_SCHEMA = number.number_schema(
    EebusLpcLimitNumber,
).extend(
    {
        cv.Optional("min_value", default=0.0): cv.float_,
        cv.Optional("max_value", default=25000.0): cv.float_,
        cv.Optional("step", default=100.0): cv.float_,
    }
)

SWITCH_SCHEMA = switch.switch_schema(EebusLpcActiveSwitch)

FAILSAFE_LIMIT_SCHEMA = sensor.sensor_schema(
    unit_of_measurement=UNIT_WATT,
    accuracy_decimals=0,
    device_class=DEVICE_CLASS_POWER,
    state_class=STATE_CLASS_MEASUREMENT,
    entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
)

FAILSAFE_DURATION_SCHEMA = sensor.sensor_schema(
    unit_of_measurement="s",
    accuracy_decimals=0,
    entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
)

TEXT_SENSOR_SCHEMA = text_sensor.text_sensor_schema(
    entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
)

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(EebusLpcComponent),
        cv.Required(CONF_BRIDGE_HOST): cv.string,
        cv.Optional(CONF_BRIDGE_PORT, default=50051): cv.port,
        cv.Required(CONF_DEVICE_SKI): cv.string,
        cv.Optional(CONF_POLL_INTERVAL, default="30s"): cv.positive_time_period_milliseconds,
        # Optional entities – all can be omitted if not needed
        cv.Optional(CONF_POWER_SENSOR): SENSOR_SCHEMA,
        cv.Optional(CONF_LIMIT_SENSOR): LIMIT_SENSOR_SCHEMA,
        cv.Optional(CONF_CONNECTED_SENSOR): BINARY_SENSOR_SCHEMA,
        cv.Optional(CONF_HEARTBEAT_SENSOR): BINARY_SENSOR_SCHEMA,
        cv.Optional(CONF_LPC_ACTIVE_SENSOR): BINARY_SENSOR_SCHEMA,
        cv.Optional(CONF_LPC_LIMIT_NUMBER): NUMBER_SCHEMA,
        cv.Optional(CONF_LPC_ACTIVE_SWITCH): SWITCH_SCHEMA,
        cv.Optional(CONF_FAILSAFE_LIMIT_SENSOR): FAILSAFE_LIMIT_SCHEMA,
        cv.Optional(CONF_FAILSAFE_DURATION_SENSOR): FAILSAFE_DURATION_SCHEMA,
        cv.Optional(CONF_BRAND_SENSOR): TEXT_SENSOR_SCHEMA,
        cv.Optional(CONF_MODEL_SENSOR): TEXT_SENSOR_SCHEMA,
        cv.Optional(CONF_SERIAL_SENSOR): TEXT_SENSOR_SCHEMA,
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    cg.add(var.set_bridge_host(config[CONF_BRIDGE_HOST]))
    cg.add(var.set_bridge_port(config[CONF_BRIDGE_PORT]))
    cg.add(var.set_device_ski(config[CONF_DEVICE_SKI]))
    cg.add(var.set_poll_interval_ms(config[CONF_POLL_INTERVAL]))

    if CONF_POWER_SENSOR in config:
        sens = await sensor.new_sensor(config[CONF_POWER_SENSOR])
        cg.add(var.set_power_sensor(sens))

    if CONF_LIMIT_SENSOR in config:
        sens = await sensor.new_sensor(config[CONF_LIMIT_SENSOR])
        cg.add(var.set_limit_sensor(sens))

    if CONF_CONNECTED_SENSOR in config:
        sens = await binary_sensor.new_binary_sensor(config[CONF_CONNECTED_SENSOR])
        cg.add(var.set_connected_sensor(sens))

    if CONF_HEARTBEAT_SENSOR in config:
        sens = await binary_sensor.new_binary_sensor(config[CONF_HEARTBEAT_SENSOR])
        cg.add(var.set_heartbeat_sensor(sens))

    if CONF_LPC_ACTIVE_SENSOR in config:
        sens = await binary_sensor.new_binary_sensor(config[CONF_LPC_ACTIVE_SENSOR])
        cg.add(var.set_lpc_active_sensor(sens))

    if CONF_LPC_LIMIT_NUMBER in config:
        num_conf = config[CONF_LPC_LIMIT_NUMBER]
        num = cg.new_Pvariable(num_conf[CONF_ID])
        await cg.register_component(num, num_conf)
        await number.register_number(
            num,
            num_conf,
            min_value=num_conf.get("min_value", 0.0),
            max_value=num_conf.get("max_value", 25000.0),
            step=num_conf.get("step", 100.0),
        )
        cg.add(num.set_parent(var))

    if CONF_LPC_ACTIVE_SWITCH in config:
        sw_conf = config[CONF_LPC_ACTIVE_SWITCH]
        sw = cg.new_Pvariable(sw_conf[CONF_ID])
        await cg.register_component(sw, sw_conf)
        await switch.register_switch(sw, sw_conf)
        cg.add(sw.set_parent(var))

    if CONF_FAILSAFE_LIMIT_SENSOR in config:
        sens = await sensor.new_sensor(config[CONF_FAILSAFE_LIMIT_SENSOR])
        cg.add(var.set_failsafe_limit_sensor(sens))

    if CONF_FAILSAFE_DURATION_SENSOR in config:
        sens = await sensor.new_sensor(config[CONF_FAILSAFE_DURATION_SENSOR])
        cg.add(var.set_failsafe_duration_sensor(sens))

    if CONF_BRAND_SENSOR in config:
        sens = await text_sensor.new_text_sensor(config[CONF_BRAND_SENSOR])
        cg.add(var.set_brand_sensor(sens))

    if CONF_MODEL_SENSOR in config:
        sens = await text_sensor.new_text_sensor(config[CONF_MODEL_SENSOR])
        cg.add(var.set_model_sensor(sens))

    if CONF_SERIAL_SENSOR in config:
        sens = await text_sensor.new_text_sensor(config[CONF_SERIAL_SENSOR])
        cg.add(var.set_serial_sensor(sens))
