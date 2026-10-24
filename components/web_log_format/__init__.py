from pathlib import Path

import esphome.codegen as cg
import esphome.config_validation as cv

DEPENDENCIES = ["web_server"]
CONFIG_SCHEMA = cv.Schema({})


async def to_code(config):
    script = Path(__file__).with_name("embed_script.py").resolve().as_posix()
    cg.add_platformio_option("extra_scripts", [f"pre:{script}"])