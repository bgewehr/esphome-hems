"""ESPHome native Modbus RTU server component.

Replaces thomase1234/esphome-fake-xemex-csmb which depends on the
emelianov/modbus-esp8266 Arduino library (incompatible with ESP-IDF).
This implementation uses ESPHome's uart::UARTDevice directly – no Arduino
library, works with both esp-idf and arduino frameworks.
"""
import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.cpp_helpers import gpio_pin_expression
from esphome.components import uart
from esphome.const import CONF_ADDRESS, CONF_ID
from esphome import pins

CONF_START_ADDRESS = "start_address"
CONF_DEFAULT = "default"
CONF_NUMBER = "number"
CONF_RE_PIN = "re_pin"
CONF_DE_PIN = "de_pin"
CONF_ON_READ = "on_read"
CONF_ON_WRITE = "on_write"

modbus_server_ns = cg.esphome_ns.namespace("modbus_server")
ModbusServer = modbus_server_ns.class_("ModbusServer", cg.Component)

DEPENDENCIES = ["uart"]
MULTI_CONF = True
CODEOWNERS = ["@bgewehr"]

_REGISTER_SCHEMA = cv.Schema(
    {
        cv.Required(CONF_START_ADDRESS): cv.positive_int,
        cv.Optional(CONF_DEFAULT, default=0): cv.positive_int,
        cv.Optional(CONF_NUMBER, default=1): cv.positive_int,
        cv.Optional(CONF_ON_READ): cv.returning_lambda,
        cv.Optional(CONF_ON_WRITE): cv.returning_lambda,
    }
)

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(ModbusServer),
            cv.Required(CONF_ADDRESS): cv.positive_int,
            cv.Optional(CONF_RE_PIN): pins.gpio_output_pin_schema,
            cv.Optional(CONF_DE_PIN): pins.gpio_output_pin_schema,
            cv.Optional("holding_registers"): cv.ensure_list(_REGISTER_SCHEMA),
            cv.Optional("input_registers"): cv.ensure_list(_REGISTER_SCHEMA),
        }
    )
    .extend(uart.UART_DEVICE_SCHEMA)
    .extend(cv.COMPONENT_SCHEMA)
)


async def _add_registers(server, regs, add_fn, read_fn, write_fn):
    for reg in regs:
        cg.add(
            getattr(server, add_fn)(
                reg[CONF_START_ADDRESS], reg[CONF_DEFAULT], reg[CONF_NUMBER]
            )
        )
        if CONF_ON_READ in reg:
            tmpl = await cg.process_lambda(
                reg[CONF_ON_READ],
                [(cg.uint16, "address"), (cg.uint16, "value")],
                return_type=cg.uint16,
            )
            cg.add(
                getattr(server, read_fn)(
                    reg[CONF_START_ADDRESS], tmpl, reg[CONF_NUMBER]
                )
            )
        if CONF_ON_WRITE in reg:
            tmpl = await cg.process_lambda(
                reg[CONF_ON_WRITE],
                [(cg.uint16, "address"), (cg.uint16, "value")],
                return_type=cg.uint16,
            )
            cg.add(
                getattr(server, write_fn)(
                    reg[CONF_START_ADDRESS], tmpl, reg[CONF_NUMBER]
                )
            )


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await uart.register_uart_device(var, config)
    cg.add(var.set_address(config[CONF_ADDRESS]))

    if CONF_RE_PIN in config:
        pin = await gpio_pin_expression(config[CONF_RE_PIN])
        cg.add(var.set_re_pin(pin))
    if CONF_DE_PIN in config:
        pin = await gpio_pin_expression(config[CONF_DE_PIN])
        cg.add(var.set_de_pin(pin))

    await _add_registers(
        var,
        config.get("holding_registers", []),
        "add_holding_register",
        "on_read_holding_register",
        "on_write_holding_register",
    )
    await _add_registers(
        var,
        config.get("input_registers", []),
        "add_input_register",
        "on_read_input_register",
        "on_write_input_register",
    )
