// Native ESP-IDF compatible Modbus RTU server.
// Implements FC03 (Read Holding Registers), FC04 (Read Input Registers),
// FC06 (Write Single Register), FC16 (Write Multiple Registers).
// CRC-16/IBM per Modbus spec. No external Arduino library required.

#include "modbus_server.h"
#include "esphome/core/log.h"
#include "esphome/core/application.h"

namespace esphome {
namespace modbus_server {

static const char *const TAG = "modbus_server";

float ModbusServer::get_setup_priority() const { return setup_priority::BUS; }

void ModbusServer::setup() {
  if (re_pin_) {
    re_pin_->setup();
    re_pin_->digital_write(false);
  }
  if (de_pin_) {
    de_pin_->setup();
    de_pin_->digital_write(false);
  }
  rx_pos_ = 0;
  ESP_LOGI(TAG, "Modbus RTU server, address=0x%02X", address_);
}

void ModbusServer::dump_config() {
  ESP_LOGCONFIG(TAG, "Modbus RTU Server:");
  ESP_LOGCONFIG(TAG, "  Address: 0x%02X", address_);
  ESP_LOGCONFIG(TAG, "  Holding registers: %u", (unsigned) holding_regs_.size());
  ESP_LOGCONFIG(TAG, "  Input registers:   %u", (unsigned) input_regs_.size());
}

void ModbusServer::loop() {
  uint32_t now = millis();

  // Collect bytes
  while (available()) {
    uint8_t byte;
    if (!read_byte(&byte))
      break;
    if (rx_pos_ >= sizeof(rx_buf_)) {
      ESP_LOGW(TAG, "RX buffer overflow – resetting");
      rx_pos_ = 0;
    }
    rx_buf_[rx_pos_++] = byte;
    last_rx_ms_ = now;
  }

  // Process once frame timeout has elapsed (Modbus 3.5-char silence)
  if (rx_pos_ > 0 && (now - last_rx_ms_) >= FRAME_TIMEOUT_MS) {
    process_frame_();
    rx_pos_ = 0;
  }
}

// ─── frame processing ──────────────────────────────────────────────────────

void ModbusServer::process_frame_() {
  if (rx_pos_ < 4) {
    // Minimum: addr(1) + fc(1) + data(>=1) + crc(2), but data could be 0 for
    // some function codes – treat anything <4 as noise
    return;
  }

  // Not for us
  if (rx_buf_[0] != address_)
    return;

  // CRC check (last two bytes, little-endian)
  uint16_t crc_recv =
      (uint16_t) rx_buf_[rx_pos_ - 1] << 8 | rx_buf_[rx_pos_ - 2];
  uint16_t crc_calc = crc16_(rx_buf_, rx_pos_ - 2);
  if (crc_recv != crc_calc) {
    ESP_LOGW(TAG, "CRC mismatch: calc=0x%04X recv=0x%04X", crc_calc, crc_recv);
    return;
  }

  uint8_t fc = rx_buf_[1];

  switch (fc) {
    case 0x03: {  // Read Holding Registers
      if (rx_pos_ != 8) { send_exception_(fc, 0x03); return; }
      uint16_t start = (uint16_t) rx_buf_[2] << 8 | rx_buf_[3];
      uint16_t count = (uint16_t) rx_buf_[4] << 8 | rx_buf_[5];
      if (count == 0 || count > 125) { send_exception_(fc, 0x03); return; }
      for (uint32_t addr = start; addr < (uint32_t) start + count; addr++) {
        if (addr > UINT16_MAX || holding_regs_.find((uint16_t) addr) == holding_regs_.end()) {
          send_exception_(fc, 0x02);
          return;
        }
      }
      uint8_t resp[3 + count * 2];  // addr(1)+fc(1)+byte_count(1)+data — CRC appended by send_response_
      resp[0] = address_;
      resp[1] = fc;
      resp[2] = (uint8_t)(count * 2);
      for (uint16_t i = 0; i < count; i++) {
        uint16_t addr = start + i;
        uint16_t val = 0;
        if (holding_regs_.count(addr))
          val = holding_regs_[addr];
        if (hreg_read_cbs_.count(addr))
          val = hreg_read_cbs_[addr](addr, val);
        resp[3 + i * 2] = (uint8_t)(val >> 8);
        resp[4 + i * 2] = (uint8_t)(val & 0xFF);
      }
      send_response_(resp, sizeof(resp));
      break;
    }
    case 0x04: {  // Read Input Registers
      if (rx_pos_ != 8) { send_exception_(fc, 0x03); return; }
      uint16_t start = (uint16_t) rx_buf_[2] << 8 | rx_buf_[3];
      uint16_t count = (uint16_t) rx_buf_[4] << 8 | rx_buf_[5];
      if (count == 0 || count > 125) { send_exception_(fc, 0x03); return; }
      for (uint32_t addr = start; addr < (uint32_t) start + count; addr++) {
        if (addr > UINT16_MAX || input_regs_.find((uint16_t) addr) == input_regs_.end()) {
          send_exception_(fc, 0x02);
          return;
        }
      }
      uint8_t resp[3 + count * 2];  // addr(1)+fc(1)+byte_count(1)+data — CRC appended by send_response_
      resp[0] = address_;
      resp[1] = fc;
      resp[2] = (uint8_t)(count * 2);
      for (uint16_t i = 0; i < count; i++) {
        uint16_t addr = start + i;
        uint16_t val = 0;
        if (input_regs_.count(addr))
          val = input_regs_[addr];
        if (ireg_read_cbs_.count(addr))
          val = ireg_read_cbs_[addr](addr, val);
        resp[3 + i * 2] = (uint8_t)(val >> 8);
        resp[4 + i * 2] = (uint8_t)(val & 0xFF);
      }
      send_response_(resp, sizeof(resp));
      break;
    }
    case 0x06: {  // Write Single Holding Register
      if (rx_pos_ != 8) { send_exception_(fc, 0x03); return; }
      uint16_t addr = (uint16_t) rx_buf_[2] << 8 | rx_buf_[3];
      uint16_t val  = (uint16_t) rx_buf_[4] << 8 | rx_buf_[5];
      if (holding_regs_.find(addr) == holding_regs_.end()) {
        send_exception_(fc, 0x02);
        return;
      }
      if (hreg_write_cbs_.count(addr))
        val = hreg_write_cbs_[addr](addr, val);
      holding_regs_[addr] = val;
      // Echo the request payload (addr+fc+reg_addr+value, strip received CRC)
      send_response_(rx_buf_, rx_pos_ - 2);
      break;
    }
    case 0x10: {  // Write Multiple Holding Registers
      if (rx_pos_ < 9) { send_exception_(fc, 0x03); return; }
      uint16_t start     = (uint16_t) rx_buf_[2] << 8 | rx_buf_[3];
      uint16_t reg_count = (uint16_t) rx_buf_[4] << 8 | rx_buf_[5];
      uint8_t  byte_cnt  = rx_buf_[6];
      if (reg_count == 0 || reg_count > 123 || byte_cnt != reg_count * 2 ||
          rx_pos_ != (uint16_t)(9 + byte_cnt)) {
        send_exception_(fc, 0x03);
        return;
      }
      for (uint32_t addr = start; addr < (uint32_t) start + reg_count; addr++) {
        if (addr > UINT16_MAX || holding_regs_.find((uint16_t) addr) == holding_regs_.end()) {
          send_exception_(fc, 0x02);
          return;
        }
      }
      for (uint16_t i = 0; i < reg_count; i++) {
        uint16_t addr = start + i;
        uint16_t val  = (uint16_t) rx_buf_[7 + i * 2] << 8 | rx_buf_[8 + i * 2];
        if (hreg_write_cbs_.count(addr))
          val = hreg_write_cbs_[addr](addr, val);
        holding_regs_[addr] = val;
      }
      // Response: addr + fc + start(2) + reg_count(2) + crc(2)
      uint8_t resp[6];
      resp[0] = address_;
      resp[1] = fc;
      resp[2] = rx_buf_[2];
      resp[3] = rx_buf_[3];
      resp[4] = rx_buf_[4];
      resp[5] = rx_buf_[5];
      send_response_(resp, sizeof(resp));
      break;
    }
    default:
      ESP_LOGD(TAG, "Unsupported FC=0x%02X", fc);
      send_exception_(fc, 0x01);
      break;
  }
}

// ─── helpers ───────────────────────────────────────────────────────────────

void ModbusServer::send_response_(const uint8_t *buf, size_t len) {
  // buf contains the payload WITHOUT CRC; send_response_ appends CRC.
  // All callers must pass payload-only length (strip received CRC before calling).
  uint8_t frame[len + 2];
  memcpy(frame, buf, len);
  uint16_t crc = crc16_(frame, len);
  frame[len]     = (uint8_t)(crc & 0xFF);
  frame[len + 1] = (uint8_t)(crc >> 8);

  if (re_pin_) re_pin_->digital_write(true);
  if (de_pin_) de_pin_->digital_write(true);

  for (size_t i = 0; i < sizeof(frame); i++)
    write_byte(frame[i]);

  flush();

  if (re_pin_) re_pin_->digital_write(false);
  if (de_pin_) de_pin_->digital_write(false);
}

void ModbusServer::send_exception_(uint8_t fc, uint8_t code) {
  uint8_t resp[3];
  resp[0] = address_;
  resp[1] = fc | 0x80;
  resp[2] = code;
  send_response_(resp, sizeof(resp));
}

// CRC-16/IBM (Modbus): poly 0xA001, init 0xFFFF
uint16_t ModbusServer::crc16_(const uint8_t *buf, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; i++) {
    crc ^= buf[i];
    for (int b = 0; b < 8; b++) {
      if (crc & 0x0001)
        crc = (crc >> 1) ^ 0xA001;
      else
        crc >>= 1;
    }
  }
  return crc;
}

// ─── register management ───────────────────────────────────────────────────

bool ModbusServer::add_holding_register(uint16_t start, uint16_t value, uint16_t count) {
  for (uint16_t i = 0; i < count; i++)
    holding_regs_[start + i] = value;
  return true;
}
bool ModbusServer::write_holding_register(uint16_t addr, uint16_t value) {
  holding_regs_[addr] = value;
  return true;
}
uint16_t ModbusServer::read_holding_register(uint16_t addr) {
  auto it = holding_regs_.find(addr);
  return (it != holding_regs_.end()) ? it->second : 0;
}
void ModbusServer::on_read_holding_register(uint16_t addr, cbOnReadWrite cb, uint16_t count) {
  for (uint16_t i = 0; i < count; i++)
    hreg_read_cbs_[addr + i] = cb;
}
void ModbusServer::on_write_holding_register(uint16_t addr, cbOnReadWrite cb, uint16_t count) {
  for (uint16_t i = 0; i < count; i++)
    hreg_write_cbs_[addr + i] = cb;
}

bool ModbusServer::add_input_register(uint16_t start, uint16_t value, uint16_t count) {
  for (uint16_t i = 0; i < count; i++)
    input_regs_[start + i] = value;
  return true;
}
bool ModbusServer::write_input_register(uint16_t addr, uint16_t value) {
  input_regs_[addr] = value;
  return true;
}
uint16_t ModbusServer::read_input_register(uint16_t addr) {
  auto it = input_regs_.find(addr);
  return (it != input_regs_.end()) ? it->second : 0;
}
void ModbusServer::on_read_input_register(uint16_t addr, cbOnReadWrite cb, uint16_t count) {
  for (uint16_t i = 0; i < count; i++)
    ireg_read_cbs_[addr + i] = cb;
}
void ModbusServer::on_write_input_register(uint16_t addr, cbOnReadWrite cb, uint16_t count) {
  for (uint16_t i = 0; i < count; i++)
    ireg_write_cbs_[addr + i] = cb;
}

}  // namespace modbus_server
}  // namespace esphome
