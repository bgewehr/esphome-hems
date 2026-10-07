#pragma once

// Native ESP-IDF compatible Modbus RTU server.
// Replaces the Arduino-dependent thomase1234/esphome-fake-xemex-csmb component.
// No external library dependency – Modbus RTU framing is implemented directly
// on top of esphome::uart::UARTDevice.

#include "esphome/core/component.h"
#include "esphome/core/hal.h"
#include "esphome/components/uart/uart.h"
#include "xemex_control.h"

#include <functional>
#include <map>

namespace esphome {
namespace modbus_server {

using cbOnReadWrite = std::function<uint16_t(uint16_t addr, uint16_t val)>;

class ModbusServer : public uart::UARTDevice, public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override;

  void set_address(uint8_t address) { address_ = address; }
  void set_re_pin(GPIOPin *pin) { re_pin_ = pin; }
  void set_de_pin(GPIOPin *pin) { de_pin_ = pin; }
  void set_read_only(bool read_only) { read_only_ = read_only; }
  uint32_t last_request_ms() const { return last_request_ms_; }
  uint32_t request_count() const { return request_count_; }
  uint32_t crc_errors() const { return crc_errors_; }
  void on_holding_response(std::function<void(uint16_t, uint16_t, const uint8_t *)> callback) {
    holding_response_callback_ = std::move(callback);
  }

  // Holding registers (FC03)
  bool add_holding_register(uint16_t start, uint16_t value, uint16_t count = 1);
  bool write_holding_register(uint16_t addr, uint16_t value);
  uint16_t read_holding_register(uint16_t addr);
  void on_read_holding_register(uint16_t addr, cbOnReadWrite cb, uint16_t count = 1);
  void on_write_holding_register(uint16_t addr, cbOnReadWrite cb, uint16_t count = 1);

  // Input registers (FC04)
  bool add_input_register(uint16_t start, uint16_t value, uint16_t count = 1);
  bool write_input_register(uint16_t addr, uint16_t value);
  uint16_t read_input_register(uint16_t addr);
  void on_read_input_register(uint16_t addr, cbOnReadWrite cb, uint16_t count = 1);
  void on_write_input_register(uint16_t addr, cbOnReadWrite cb, uint16_t count = 1);

 protected:
  void process_frame_();
  void send_response_(const uint8_t *buf, size_t len);
  void send_exception_(uint8_t fc, uint8_t code);
  static uint16_t crc16_(const uint8_t *buf, size_t len);

  uint8_t address_{1};
  GPIOPin *re_pin_{nullptr};
  GPIOPin *de_pin_{nullptr};

  std::map<uint16_t, uint16_t> holding_regs_;
  std::map<uint16_t, uint16_t> input_regs_;
  std::map<uint16_t, cbOnReadWrite> hreg_read_cbs_;
  std::map<uint16_t, cbOnReadWrite> hreg_write_cbs_;
  std::map<uint16_t, cbOnReadWrite> ireg_read_cbs_;
  std::map<uint16_t, cbOnReadWrite> ireg_write_cbs_;

  uint8_t rx_buf_[256]{};
  uint16_t rx_pos_{0};
  uint32_t last_rx_ms_{0};
  bool read_only_{false};
  uint32_t last_request_ms_{0}, request_count_{0}, crc_errors_{0};
  std::function<void(uint16_t, uint16_t, const uint8_t *)> holding_response_callback_;

  // 3.5 character times at 9600 8E1 (11 bits/char) ≈ 4 ms
  static constexpr uint32_t FRAME_TIMEOUT_MS = 5;
};

}  // namespace modbus_server
}  // namespace esphome
