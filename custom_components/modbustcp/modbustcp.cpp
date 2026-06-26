#include "modbustcp.h"
#include "esphome/core/application.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"

#include <cerrno>
#include <sys/poll.h>

namespace esphome::modbustcp {

static const char *const TAG = "modbustcp";

void ModbusTCP::setup() {
  // Connection will be established on first use in loop
}

void ModbusTCP::connect_() {
  if (sock_ >= 0) {
    disconnect_();
  }

  struct sockaddr_in dest;
  memset(&dest, 0, sizeof(dest));
  dest.sin_family = AF_INET;
  dest.sin_port = htons(port_);

  struct hostent *he = lwip_gethostbyname(host_.c_str());
  if (he == nullptr) {
    ESP_LOGW(TAG, "DNS resolve failed for %s", host_.c_str());
    return;
  }
  memcpy(&dest.sin_addr, he->h_addr_list[0], he->h_length);

  sock_ = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  if (sock_ < 0) {
    ESP_LOGE(TAG, "Socket creation failed: %d", errno);
    return;
  }

  // Non-blocking connect
  int flags = fcntl(sock_, F_GETFL, 0);
  fcntl(sock_, F_SETFL, flags | O_NONBLOCK);

  int ret = ::connect(sock_, (struct sockaddr *)&dest, sizeof(dest));
  if (ret < 0 && errno != EINPROGRESS) {
    ESP_LOGW(TAG, "Connect failed: %d", errno);
    ::close(sock_);
    sock_ = -1;
    return;
  }

  // Single poll with 500ms timeout - doesn't block the loop badly
  struct pollfd pfd = {sock_, POLLOUT, 0};
  ret = ::poll(&pfd, 1, 500);
  if (ret <= 0) {
    ESP_LOGW(TAG, "Connect timeout to %s:%d", host_.c_str(), port_);
    ::close(sock_);
    sock_ = -1;
    return;
  }

  // Check for socket error
  int sockerr = 0;
  socklen_t errlen = sizeof(sockerr);
  getsockopt(sock_, SOL_SOCKET, SO_ERROR, &sockerr, &errlen);
  if (sockerr != 0) {
    ESP_LOGW(TAG, "Connect error: %d", sockerr);
    ::close(sock_);
    sock_ = -1;
    return;
  }

  // Keep non-blocking + disable Nagle
  int nodelay = 1;
  setsockopt(sock_, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));

  connected_ = true;
  rx_pos_ = 0;
  ESP_LOGI(TAG, "Connected to %s:%d (fd=%d)", host_.c_str(), port_, sock_);
}

void ModbusTCP::disconnect_() {
  if (sock_ >= 0) {
    ::close(sock_);
    sock_ = -1;
  }
  connected_ = false;
  rx_pos_ = 0;
}

void ModbusTCP::on_shutdown() {
  disconnect_();
}

void ModbusTCP::loop() {
  // Try to connect if not connected
  if (!connected_) {
    uint32_t now = millis();
    if (now - last_attempt_ > 5000) {
      last_attempt_ = now;
      connect_();
    }
    return;
  }

  // Non-blocking read of any pending response data
  if (waiting_for_response != 0) {
    read_available_();

    // Timeout waiting for response
    if (millis() - last_send_ > 1500) {
      ESP_LOGW(TAG, "Response timeout for unit %d", waiting_for_response);
      waiting_for_response = 0;
      rx_pos_ = 0;
    }
  }
}

void ModbusTCP::read_available_() {
  // Poll socket - 0ms timeout = non-blocking check
  struct pollfd pfd = {sock_, POLLIN, 0};
  int ready = ::poll(&pfd, 1, 0);

  if (ready < 0) {
    ESP_LOGW(TAG, "poll error: %d", errno);
    disconnect_();
    return;
  }

  if (ready == 0) return;  // No data available yet

  // Read whatever is available into buffer
  int n = ::recv(sock_, rx_buf_ + rx_pos_, sizeof(rx_buf_) - rx_pos_, 0);
  if (n > 0) {
    rx_pos_ += n;
    try_parse_response_();
  } else if (n == 0) {
    ESP_LOGW(TAG, "Connection closed by peer");
    disconnect_();
  } else {
    if (errno != EAGAIN && errno != EWOULDBLOCK) {
      ESP_LOGW(TAG, "recv error: %d", errno);
      disconnect_();
    }
  }
}

void ModbusTCP::try_parse_response_() {
  // Need at least MBAP header (7 bytes)
  if (rx_pos_ < 7) return;

  uint16_t pdu_len = (rx_buf_[4] << 8) | rx_buf_[5];
  if (pdu_len < 2 || pdu_len > 253) {
    ESP_LOGW(TAG, "Invalid PDU length: %d, resetting", pdu_len);
    rx_pos_ = 0;
    waiting_for_response = 0;
    return;
  }

  // Total frame = 6 (MBAP header without unit) + pdu_len
  size_t frame_len = 6 + pdu_len;
  if (rx_pos_ < frame_len) return;  // Not enough data yet

  // Full frame received - dispatch it
  dispatch_message_(rx_buf_, frame_len);

  // Remove processed frame from buffer
  size_t remaining = rx_pos_ - frame_len;
  if (remaining > 0) {
    memmove(rx_buf_, rx_buf_ + frame_len, remaining);
  }
  rx_pos_ = remaining;
  waiting_for_response = 0;
}

void ModbusTCP::dispatch_message_(uint8_t *buf, size_t len) {
  if (len < 8) return;

  uint8_t address = buf[6];
  uint8_t function_code = buf[7];

  if ((function_code & 0x80) != 0) {
    uint8_t exception_code = (len > 8) ? buf[8] : 0;
    ESP_LOGE(TAG, "Modbus error unit=%d FC=0x%02X exc=0x%02X", address, function_code, exception_code);
    for (auto *device : this->devices_) {
      if (device->address_ == address) {
        device->on_modbus_error(function_code & 0x7F, exception_code);
        break;
      }
    }
    return;
  }

  // Build data vector
  size_t data_start;
  size_t data_len;

  if (function_code == 0x03 || function_code == 0x04 ||
      function_code == 0x01 || function_code == 0x02) {
    if (len < 9) return;
    data_start = 9;
    data_len = buf[8];
  } else {
    data_start = 8;
    data_len = len - 8;
  }

  if (data_start + data_len > len) return;

  std::vector<uint8_t> data(buf + data_start, buf + data_start + data_len);

  ESP_LOGD(TAG, "<<< Unit=%d FC=0x%02X len=%zu", address, function_code, data_len);

  for (auto *device : this->devices_) {
    if (device->address_ == address) {
      device->on_modbus_data(data);
      return;
    }
  }
  ESP_LOGW(TAG, "No device for unit %d", address);
}

void ModbusTCP::dump_config() {
  ESP_LOGCONFIG(TAG, "Modbus TCP (async non-blocking):");
  ESP_LOGCONFIG(TAG, "  Host: %s", host_.c_str());
  ESP_LOGCONFIG(TAG, "  Port: %d", port_);
  ESP_LOGCONFIG(TAG, "  Send Wait Time: %d ms", this->send_wait_time_);
}

float ModbusTCP::get_setup_priority() const { return setup_priority::AFTER_WIFI - 1.0f; }

void ModbusTCP::send(uint8_t address, uint8_t function_code, uint16_t start_address, uint16_t number_of_entities,
                     uint8_t payload_len, const uint8_t *payload) {
  if (number_of_entities > 128 && function_code <= 0x10) {
    ESP_LOGE(TAG, "Too many registers: %d", number_of_entities);
    return;
  }

  if (!connected_) {
    connect_();
    if (!connected_) {
      ESP_LOGW(TAG, "Cannot send, not connected");
      return;
    }
  }

  // If still waiting for previous response, skip this request
  if (waiting_for_response != 0) {
    ESP_LOGD(TAG, "Still waiting for unit %d, skipping send to unit %d", waiting_for_response, address);
    return;
  }

  uint8_t frame[256];
  size_t pos = 0;

  // MBAP Header
  frame[pos++] = Transaction_Identifier >> 8;
  frame[pos++] = Transaction_Identifier & 0xFF;
  frame[pos++] = 0x00;  // Protocol ID
  frame[pos++] = 0x00;
  size_t len_pos = pos;
  pos += 2;  // Length placeholder
  frame[pos++] = address;
  frame[pos++] = function_code;
  frame[pos++] = start_address >> 8;
  frame[pos++] = start_address & 0xFF;

  if (function_code != 0x05 && function_code != 0x06) {
    frame[pos++] = number_of_entities >> 8;
    frame[pos++] = number_of_entities & 0xFF;
  }

  if (payload != nullptr) {
    if (function_code == 0x10 || function_code == 0x0F) {
      frame[pos++] = payload_len;
    } else {
      payload_len = 2;
    }
    for (int i = 0; i < payload_len; i++) {
      frame[pos++] = payload[i];
    }
  }

  // Fill MBAP length
  uint16_t mbap_len = pos - 6;
  frame[len_pos] = mbap_len >> 8;
  frame[len_pos + 1] = mbap_len & 0xFF;

  ESP_LOGD(TAG, ">>> Unit=%d FC=0x%02X addr=0x%04X cnt=%d", address, function_code, start_address, number_of_entities);

  // Non-blocking send
  int sent = ::send(sock_, frame, pos, MSG_DONTWAIT);
  if (sent != (int)pos) {
    ESP_LOGW(TAG, "Send failed: %d/%zu errno=%d", sent, pos, errno);
    disconnect_();
    return;
  }

  Transaction_Identifier++;
  waiting_for_response = address;
  last_send_ = millis();
  // Response will be read asynchronously in loop() - NO blocking here
}

void ModbusTCP::send_raw(const std::vector<uint8_t> &payload) {
  if (payload.empty()) return;
  if (!connected_) {
    connect_();
    if (!connected_) return;
  }
  ::send(sock_, payload.data(), payload.size(), MSG_DONTWAIT);
  waiting_for_response = payload[0];
  last_send_ = millis();
}

}  // namespace esphome::modbustcp
