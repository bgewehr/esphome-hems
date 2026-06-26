#pragma once

#include "esphome/core/component.h"
#include "esphome/core/log.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/number/number.h"
#include "esphome/components/switch/switch.h"
#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/components/socket/socket.h"

#include <string>
#include <vector>
#include <functional>
#include <cstring>

namespace esphome {
namespace eebus_lpc {

static const char *const TAG = "eebus_lpc";

// ---------------------------------------------------------------------------
// Minimal protobuf wire-format encoder/decoder
// ---------------------------------------------------------------------------

class ProtoEncoder {
 public:
  void reset() { buf_.clear(); }

  void write_varint_field(uint32_t field_num, uint64_t value) {
    write_tag(field_num, 0);
    write_varint(value);
  }

  void write_double_field(uint32_t field_num, double value) {
    write_tag(field_num, 1);
    uint8_t bytes[8];
    memcpy(bytes, &value, 8);
    for (int i = 0; i < 8; i++) buf_.push_back(bytes[i]);
  }

  void write_string_field(uint32_t field_num, const std::string &s) {
    write_tag(field_num, 2);
    write_varint(s.size());
    for (char c : s) buf_.push_back((uint8_t) c);
  }

  std::vector<uint8_t> grpc_frame() const {
    std::vector<uint8_t> frame(5 + buf_.size());
    frame[0] = 0;  // no compression
    uint32_t len = buf_.size();
    frame[1] = (len >> 24) & 0xFF;
    frame[2] = (len >> 16) & 0xFF;
    frame[3] = (len >> 8) & 0xFF;
    frame[4] = len & 0xFF;
    memcpy(frame.data() + 5, buf_.data(), buf_.size());
    return frame;
  }

  const std::vector<uint8_t> &raw() const { return buf_; }

 private:
  std::vector<uint8_t> buf_;

  void write_tag(uint32_t field_num, uint8_t wire_type) {
    write_varint((field_num << 3) | wire_type);
  }

  void write_varint(uint64_t v) {
    do {
      uint8_t b = v & 0x7F;
      v >>= 7;
      if (v) b |= 0x80;
      buf_.push_back(b);
    } while (v);
  }
};

struct ProtoField {
  uint32_t field_num;
  uint8_t wire_type;
  uint64_t varint_val;
  double double_val;
  std::string string_val;
};

class ProtoDecoder {
 public:
  explicit ProtoDecoder(const uint8_t *data, size_t len) : data_(data), pos_(0), len_(len) {}

  bool next(ProtoField &f) {
    if (pos_ >= len_) return false;
    uint64_t tag;
    if (!read_varint(tag)) return false;
    f.field_num = tag >> 3;
    f.wire_type = tag & 0x07;
    switch (f.wire_type) {
      case 0:
        if (!read_varint(f.varint_val)) return false;
        break;
      case 1:
        if (pos_ + 8 > len_) return false;
        memcpy(&f.double_val, data_ + pos_, 8);
        pos_ += 8;
        break;
      case 2: {
        uint64_t slen;
        if (!read_varint(slen)) return false;
        if (pos_ + slen > len_) return false;
        f.string_val.assign((const char *) (data_ + pos_), slen);
        pos_ += slen;
        break;
      }
      case 5:
        if (pos_ + 4 > len_) return false;
        pos_ += 4;
        break;
      default:
        return false;
    }
    return true;
  }

 private:
  const uint8_t *data_;
  size_t pos_;
  size_t len_;

  bool read_varint(uint64_t &out) {
    out = 0;
    int shift = 0;
    while (pos_ < len_) {
      uint8_t b = data_[pos_++];
      out |= (uint64_t) (b & 0x7F) << shift;
      if (!(b & 0x80)) return true;
      shift += 7;
      if (shift >= 64) return false;
    }
    return false;
  }
};

// ---------------------------------------------------------------------------
// HTTP/2 frame builder helpers (static methods, no state needed)
// ---------------------------------------------------------------------------
class Http2Builder {
 public:
  static std::vector<uint8_t> preface_and_settings() {
    const char *preface = "PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n";
    std::vector<uint8_t> out(preface, preface + 24);
    uint8_t settings[] = {0, 0, 0, 0x04, 0x00, 0, 0, 0, 0};
    out.insert(out.end(), settings, settings + 9);
    return out;
  }

  static std::vector<uint8_t> headers_frame(uint32_t stream_id,
                                             const std::string &path,
                                             const std::string &host) {
    std::vector<uint8_t> hpack;
    auto add_literal = [&](const std::string &name, const std::string &value) {
      hpack.push_back(0x00);
      hpack.push_back(name.size());
      hpack.insert(hpack.end(), name.begin(), name.end());
      hpack.push_back(value.size());
      hpack.insert(hpack.end(), value.begin(), value.end());
    };
    hpack.push_back(0x83);  // :method POST
    hpack.push_back(0x86);  // :scheme http
    hpack.push_back(0x04);  // :path indexed key
    hpack.push_back(path.size());
    hpack.insert(hpack.end(), path.begin(), path.end());
    add_literal(":authority", host);
    add_literal("content-type", "application/grpc");
    add_literal("te", "trailers");

    uint32_t flen = hpack.size();
    std::vector<uint8_t> frame;
    frame.push_back((flen >> 16) & 0xFF);
    frame.push_back((flen >> 8) & 0xFF);
    frame.push_back(flen & 0xFF);
    frame.push_back(0x01);  // HEADERS
    frame.push_back(0x04);  // END_HEADERS
    frame.push_back((stream_id >> 24) & 0xFF);
    frame.push_back((stream_id >> 16) & 0xFF);
    frame.push_back((stream_id >> 8) & 0xFF);
    frame.push_back(stream_id & 0xFF);
    frame.insert(frame.end(), hpack.begin(), hpack.end());
    return frame;
  }

  static std::vector<uint8_t> data_frame(uint32_t stream_id,
                                          const std::vector<uint8_t> &grpc_msg,
                                          bool end_stream = true) {
    uint32_t flen = grpc_msg.size();
    std::vector<uint8_t> frame;
    frame.push_back((flen >> 16) & 0xFF);
    frame.push_back((flen >> 8) & 0xFF);
    frame.push_back(flen & 0xFF);
    frame.push_back(0x00);  // DATA
    frame.push_back(end_stream ? 0x01 : 0x00);
    frame.push_back((stream_id >> 24) & 0xFF);
    frame.push_back((stream_id >> 16) & 0xFF);
    frame.push_back((stream_id >> 8) & 0xFF);
    frame.push_back(stream_id & 0xFF);
    frame.insert(frame.end(), grpc_msg.begin(), grpc_msg.end());
    return frame;
  }
};

// ---------------------------------------------------------------------------
// EebusLpcComponent – uses ESPHome socket API (VFS-safe)
// Non-blocking state machine: connect → send → receive → parse → idle
// ---------------------------------------------------------------------------
class EebusLpcComponent : public Component {
 public:
  void set_bridge_host(const std::string &host) { bridge_host_ = host; }
  void set_bridge_port(uint16_t port) { bridge_port_ = port; }
  void set_poll_interval_ms(uint32_t ms) { poll_interval_ms_ = ms; }
  void set_device_ski(const std::string &ski) { device_ski_ = ski; }

  void set_power_sensor(sensor::Sensor *s) { power_sensor_ = s; }
  void set_limit_sensor(sensor::Sensor *s) { limit_sensor_ = s; }
  void set_failsafe_limit_sensor(sensor::Sensor *s) { failsafe_limit_sensor_ = s; }
  void set_failsafe_duration_sensor(sensor::Sensor *s) { failsafe_duration_sensor_ = s; }
  void set_connected_sensor(binary_sensor::BinarySensor *s) { connected_sensor_ = s; }
  void set_heartbeat_sensor(binary_sensor::BinarySensor *s) { heartbeat_sensor_ = s; }
  void set_lpc_active_sensor(binary_sensor::BinarySensor *s) { lpc_active_sensor_ = s; }
  void set_brand_sensor(text_sensor::TextSensor *s) { brand_sensor_ = s; }
  void set_model_sensor(text_sensor::TextSensor *s) { model_sensor_ = s; }
  void set_serial_sensor(text_sensor::TextSensor *s) { serial_sensor_ = s; }

  void setup() override {
    ESP_LOGI(TAG, "EEBUS LPC component initialising (bridge=%s:%d, ski=%s)",
             bridge_host_.c_str(), bridge_port_, device_ski_.c_str());
    last_poll_ms_ = millis();
    connected_ = false;
  }

  float get_setup_priority() const override { return setup_priority::AFTER_WIFI; }

  void loop() override {
    if (state_ != STATE_IDLE) {
      handle_active_connection_();
      return;
    }

    uint32_t now = millis();
    uint32_t effective_interval = poll_interval_ms_ * (1u << std::min(connect_failures_, (uint8_t) 4));
    if (now - last_poll_ms_ >= effective_interval) {
      last_poll_ms_ = now;
      start_poll_();
    }
  }

  // --- Public API (called from switch/number entities) ---
  void set_lpc_limit(float watts) {
    ESP_LOGI(TAG, "SetLpcLimit -> %.0f W", watts);
    pending_limit_ = watts;
    pending_active_ = true;  // writing a limit always activates it (like HA)
    pending_write_ = true;
  }

  void set_lpc_active(bool active) {
    ESP_LOGI(TAG, "SetLpcActive -> %s", active ? "true" : "false");
    pending_active_ = active;
    // Use the device's current limit value (like HA: read current then write back)
    pending_limit_ = last_known_limit_;
    pending_write_ = true;
  }

 private:
  // Configuration
  std::string bridge_host_{"192.168.1.100"};
  uint16_t bridge_port_{50051};
  uint32_t poll_interval_ms_{30000};
  std::string device_ski_;

  // State
  uint32_t last_poll_ms_{0};
  bool connected_{false};
  uint8_t connect_failures_{0};
  uint8_t poll_phase_{0};  // alternates between LPC and Monitoring
  float last_known_limit_{4200.0f};  // cached for active toggle

  // Pending write command
  bool pending_write_{false};
  float pending_limit_{4200.0f};
  bool pending_active_{true};
  bool pending_heartbeat_start_{true};  // start heartbeat on first connection
  bool heartbeat_running_{false};       // track heartbeat state

  // Sensors
  sensor::Sensor *power_sensor_{nullptr};
  sensor::Sensor *limit_sensor_{nullptr};
  sensor::Sensor *failsafe_limit_sensor_{nullptr};
  sensor::Sensor *failsafe_duration_sensor_{nullptr};
  binary_sensor::BinarySensor *connected_sensor_{nullptr};
  binary_sensor::BinarySensor *heartbeat_sensor_{nullptr};
  binary_sensor::BinarySensor *lpc_active_sensor_{nullptr};
  text_sensor::TextSensor *brand_sensor_{nullptr};
  text_sensor::TextSensor *model_sensor_{nullptr};
  text_sensor::TextSensor *serial_sensor_{nullptr};
  bool device_info_fetched_{false};

  // Connection state machine
  enum State {
    STATE_IDLE,
    STATE_CONNECTING,
    STATE_HANDSHAKE_SEND,  // Send HTTP/2 preface + SETTINGS
    STATE_HANDSHAKE_RECV,  // Wait for server SETTINGS, send ACK
    STATE_SENDING,         // Send HEADERS + DATA (gRPC request)
    STATE_RECEIVING,       // Wait for gRPC response
  };
  State state_{STATE_IDLE};
  std::unique_ptr<socket::Socket> sock_{nullptr};
  uint32_t state_deadline_ms_{0};
  std::vector<uint8_t> tx_buf_;
  size_t tx_offset_{0};
  std::vector<uint8_t> rx_buf_;
  std::vector<uint8_t> request_frames_;  // HEADERS + DATA, sent after handshake
  bool settings_ack_sent_{false};
  std::string current_rpc_path_;

  // ---------------------------------------------------------------------------
  // Start a poll or process pending commands
  // ---------------------------------------------------------------------------
  void start_poll_() {
    // Process pending write command first
    if (pending_write_) {
      pending_write_ = false;
      float limit = pending_limit_;
      bool active = pending_active_;
      start_rpc_("/eebus.v1.LPCService/WriteConsumptionLimit",
                 [this, limit, active](ProtoEncoder &enc) {
                   enc.write_string_field(1, device_ski_);
                   enc.write_double_field(2, (double) limit);
                   enc.write_varint_field(4, active ? 1 : 0);
                 });
      return;
    }
    // Ensure heartbeat is always running
    if (pending_heartbeat_start_) {
      pending_heartbeat_start_ = false;
      start_rpc_("/eebus.v1.LPCService/StartHeartbeat",
                 [this](ProtoEncoder &enc) {
                   enc.write_string_field(1, device_ski_);
                 });
      return;
    }
    // One-time device info fetch
    if (!device_info_fetched_) {
      device_info_fetched_ = true;
      start_rpc_("/eebus.v1.DeviceService/ListPairedDevices",
                 [](ProtoEncoder &) {});
      return;
    }
    // 4-phase poll: LPC limit, power, heartbeat, failsafe
    if (poll_phase_ == 0) {
      start_rpc_("/eebus.v1.LPCService/GetConsumptionLimit",
                 [this](ProtoEncoder &enc) {
                   enc.write_string_field(1, device_ski_);
                 });
    } else if (poll_phase_ == 1) {
      start_rpc_("/eebus.v1.MonitoringService/GetPowerConsumption",
                 [this](ProtoEncoder &enc) {
                   enc.write_string_field(1, device_ski_);
                 });
    } else if (poll_phase_ == 2) {
      start_rpc_("/eebus.v1.LPCService/GetHeartbeatStatus",
                 [this](ProtoEncoder &enc) {
                   enc.write_string_field(1, device_ski_);
                 });
    } else {
      start_rpc_("/eebus.v1.LPCService/GetFailsafeLimit",
                 [this](ProtoEncoder &enc) {
                   enc.write_string_field(1, device_ski_);
                 });
    }
    poll_phase_ = (poll_phase_ + 1) % 4;
  }

  // ---------------------------------------------------------------------------
  // Initiate a gRPC unary call (non-blocking)
  // ---------------------------------------------------------------------------
  void start_rpc_(const std::string &path, std::function<void(ProtoEncoder &)> build_req) {
    sock_ = socket::socket_ip(SOCK_STREAM, 0);
    if (!sock_) {
      ESP_LOGW(TAG, "socket creation failed");
      on_connect_failed_();
      return;
    }

    // Non-blocking mode
    sock_->setblocking(false);

    // Build address
    struct sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(bridge_port_);
    uint32_t ip = 0;
    if (!parse_ipv4_(bridge_host_, ip)) {
      ESP_LOGW(TAG, "Invalid IP: %s", bridge_host_.c_str());
      sock_->close();
      sock_.reset();
      on_connect_failed_();
      return;
    }
    addr.sin_addr.s_addr = ip;

    // Pre-build request frames (HEADERS + DATA) - will be sent after handshake
    current_rpc_path_ = path;
    build_request_frames_(path, build_req);

    // tx_buf_ starts with just the HTTP/2 preface + SETTINGS
    tx_buf_.clear();
    auto preface = Http2Builder::preface_and_settings();
    tx_buf_.insert(tx_buf_.end(), preface.begin(), preface.end());

    int err = sock_->connect((struct sockaddr *) &addr, sizeof(addr));
    if (err == 0) {
      // Immediate connection - start handshake
      state_ = STATE_HANDSHAKE_SEND;
      tx_offset_ = 0;
      state_deadline_ms_ = millis() + 2000;
      return;
    }
    if (errno == EINPROGRESS) {
      state_ = STATE_CONNECTING;
      state_deadline_ms_ = millis() + 2000;
      return;
    }
    // Immediate failure (ECONNREFUSED etc.)
    ESP_LOGW(TAG, "connect(%s:%d) refused: errno %d",
             bridge_host_.c_str(), bridge_port_, errno);
    sock_->close();
    sock_.reset();
    on_connect_failed_();
  }

  // ---------------------------------------------------------------------------
  // Non-blocking state machine handler (called every loop iteration)
  // ---------------------------------------------------------------------------
  void handle_active_connection_() {
    if (millis() > state_deadline_ms_) {
      ESP_LOGW(TAG, "Timeout in state %d", (int) state_);
      close_and_fail_();
      return;
    }

    switch (state_) {
      case STATE_CONNECTING: {
        int sock_err = 0;
        socklen_t elen = sizeof(sock_err);
        sock_->getsockopt(SOL_SOCKET, SO_ERROR, &sock_err, &elen);
        if (sock_err == 0) {
          ssize_t probe = sock_->write(nullptr, 0);
          if (probe == 0 || (probe < 0 && errno == EAGAIN)) {
            // Connected! Start sending HTTP/2 preface
            state_ = STATE_HANDSHAKE_SEND;
            tx_offset_ = 0;
            state_deadline_ms_ = millis() + 2000;
          }
        } else if (sock_err != EINPROGRESS) {
          ESP_LOGW(TAG, "connect SO_ERROR=%d", sock_err);
          close_and_fail_();
        }
        break;
      }

      case STATE_HANDSHAKE_SEND: {
        // Send HTTP/2 preface + SETTINGS
        if (tx_offset_ < tx_buf_.size()) {
          ssize_t n = sock_->write(tx_buf_.data() + tx_offset_,
                                    tx_buf_.size() - tx_offset_);
          if (n > 0) {
            tx_offset_ += n;
          } else if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK) {
            close_and_fail_();
            return;
          }
        }
        if (tx_offset_ >= tx_buf_.size()) {
          // Preface sent, wait for server SETTINGS
          state_ = STATE_HANDSHAKE_RECV;
          rx_buf_.clear();
          settings_ack_sent_ = false;
          state_deadline_ms_ = millis() + 3000;
        }
        break;
      }

      case STATE_HANDSHAKE_RECV: {
        // Read server's SETTINGS frame and send ACK
        uint8_t tmp[512];
        ssize_t n = sock_->read(tmp, sizeof(tmp));
        if (n > 0) {
          rx_buf_.insert(rx_buf_.end(), tmp, tmp + n);
          // Scan for a SETTINGS frame (type 0x04) without ACK flag
          if (!settings_ack_sent_) {
            size_t pos = 0;
            while (pos + 9 <= rx_buf_.size()) {
              uint32_t flen = ((uint32_t) rx_buf_[pos] << 16) |
                              ((uint32_t) rx_buf_[pos + 1] << 8) |
                              rx_buf_[pos + 2];
              uint8_t ftype = rx_buf_[pos + 3];
              uint8_t flags = rx_buf_[pos + 4];
              if (pos + 9 + flen > rx_buf_.size()) break;  // incomplete

              if (ftype == 0x04 && !(flags & 0x01)) {
                // Server SETTINGS received - send ACK
                uint8_t ack[] = {0, 0, 0, 0x04, 0x01, 0, 0, 0, 0};
                sock_->write(ack, sizeof(ack));
                // WINDOW_UPDATE connection level (stream 0)
                uint8_t winup0[] = {0, 0, 4, 0x08, 0x00, 0, 0, 0, 0,
                                    0x00, 0x0F, 0xFF, 0xFF};
                sock_->write(winup0, sizeof(winup0));
                settings_ack_sent_ = true;
                // Now send the gRPC request (HEADERS + DATA)
                tx_buf_ = std::move(request_frames_);
                tx_offset_ = 0;
                state_ = STATE_SENDING;
                rx_buf_.clear();
                state_deadline_ms_ = millis() + 2000;
                return;
              }
              pos += 9 + flen;
            }
          }
        } else if (n == 0) {
          close_and_fail_();
          return;
        } else if (errno != EAGAIN && errno != EWOULDBLOCK) {
          close_and_fail_();
          return;
        }
        break;
      }

      case STATE_SENDING: {
        // Send HEADERS + DATA (gRPC request)
        if (tx_offset_ < tx_buf_.size()) {
          ssize_t n = sock_->write(tx_buf_.data() + tx_offset_,
                                    tx_buf_.size() - tx_offset_);
          if (n > 0) {
            tx_offset_ += n;
          } else if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK) {
            ESP_LOGW(TAG, "write error: errno %d", errno);
            close_and_fail_();
            return;
          }
        }
        if (tx_offset_ >= tx_buf_.size()) {
          state_ = STATE_RECEIVING;
          rx_buf_.clear();
          state_deadline_ms_ = millis() + 5000;  // 5s for gRPC response
        }
        break;
      }

      case STATE_RECEIVING: {
        uint8_t tmp[512];
        ssize_t n = sock_->read(tmp, sizeof(tmp));
        if (n > 0) {
          rx_buf_.insert(rx_buf_.end(), tmp, tmp + n);
          process_h2_frames_();
          if (try_parse_grpc_response_()) {
            close_connection_();
            return;
          }
        } else if (n == 0) {
          try_parse_grpc_response_();
          close_connection_();
          return;
        } else if (errno != EAGAIN && errno != EWOULDBLOCK) {
          ESP_LOGW(TAG, "read error: errno %d", errno);
          close_and_fail_();
          return;
        }
        break;
      }

      default:
        break;
    }
  }

  // ---------------------------------------------------------------------------
  // Process HTTP/2 control frames in rx_buf_ (SETTINGS, WINDOW_UPDATE, PING)
  // Sends SETTINGS ACK when server SETTINGS received.
  // Removes processed control frames from rx_buf_, leaves DATA/HEADERS for parsing.
  // ---------------------------------------------------------------------------
  void process_h2_frames_() {
    size_t pos = 0;
    std::vector<uint8_t> keep;  // frames to keep (DATA, HEADERS)

    while (pos + 9 <= rx_buf_.size()) {
      uint32_t flen = ((uint32_t) rx_buf_[pos] << 16) |
                      ((uint32_t) rx_buf_[pos + 1] << 8) |
                      rx_buf_[pos + 2];
      uint8_t ftype = rx_buf_[pos + 3];
      uint8_t flags = rx_buf_[pos + 4];

      if (pos + 9 + flen > rx_buf_.size()) {
        // Incomplete frame - keep remainder
        keep.insert(keep.end(), rx_buf_.begin() + pos, rx_buf_.end());
        break;
      }

      if (ftype == 0x04 && !(flags & 0x01)) {
        // SETTINGS frame (not ACK) - must reply with SETTINGS ACK
        uint8_t ack[] = {0, 0, 0, 0x04, 0x01, 0, 0, 0, 0};
        sock_->write(ack, sizeof(ack));
        // Also send WINDOW_UPDATE to allow server to send data
        uint8_t winup[] = {0, 0, 4, 0x08, 0x00, 0, 0, 0, 0,
                           0x00, 0x0F, 0xFF, 0xFF};  // 1MB window
        sock_->write(winup, sizeof(winup));
      } else if (ftype == 0x04 && (flags & 0x01)) {
        // SETTINGS ACK - ignore
      } else if (ftype == 0x08) {
        // WINDOW_UPDATE - ignore
      } else if (ftype == 0x06) {
        // PING - reply with PING ACK
        if (!(flags & 0x01) && flen == 8) {
          uint8_t ping_ack[9 + 8];
          ping_ack[0] = 0; ping_ack[1] = 0; ping_ack[2] = 8;
          ping_ack[3] = 0x06;  // PING
          ping_ack[4] = 0x01;  // ACK flag
          ping_ack[5] = ping_ack[6] = ping_ack[7] = ping_ack[8] = 0;
          memcpy(ping_ack + 9, rx_buf_.data() + pos + 9, 8);
          sock_->write(ping_ack, sizeof(ping_ack));
        }
      } else {
        // DATA (0x00), HEADERS (0x01), RST_STREAM (0x03), GOAWAY (0x07) etc.
        if (ftype == 0x07 && flen >= 8) {
          const uint8_t *p = rx_buf_.data() + pos + 9;
          uint32_t last_stream = ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
          uint32_t error_code = ((uint32_t)p[4] << 24) | ((uint32_t)p[5] << 16) | ((uint32_t)p[6] << 8) | p[7];
          ESP_LOGW(TAG, "GOAWAY: last_stream=%u error=%u", last_stream, error_code);
        } else if (ftype == 0x03 && flen >= 4) {
          const uint8_t *p = rx_buf_.data() + pos + 9;
          uint32_t error_code = ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
          ESP_LOGW(TAG, "RST_STREAM: error=%u", error_code);
        }
        keep.insert(keep.end(), rx_buf_.begin() + pos, rx_buf_.begin() + pos + 9 + flen);
      }

      pos += 9 + flen;
    }

    // If we didn't process everything, keep the unprocessed remainder
    if (pos < rx_buf_.size() && keep.empty()) {
      keep.insert(keep.end(), rx_buf_.begin() + pos, rx_buf_.end());
    }

    rx_buf_ = std::move(keep);
  }

  // ---------------------------------------------------------------------------
  // Build HEADERS + DATA frames for the gRPC request (sent after handshake)
  // ---------------------------------------------------------------------------
  void build_request_frames_(const std::string &path,
                              std::function<void(ProtoEncoder &)> build_req) {
    request_frames_.clear();

    ProtoEncoder req_enc;
    build_req(req_enc);
    auto grpc_msg = req_enc.grpc_frame();

    auto headers = Http2Builder::headers_frame(1, path, bridge_host_);
    request_frames_.insert(request_frames_.end(), headers.begin(), headers.end());

    auto data = Http2Builder::data_frame(1, grpc_msg, true);
    request_frames_.insert(request_frames_.end(), data.begin(), data.end());
  }

  // ---------------------------------------------------------------------------
  // Try to extract a gRPC response from rx_buf_
  // ---------------------------------------------------------------------------
  bool try_parse_grpc_response_() {
    size_t pos = 0;
    std::vector<uint8_t> data_payload;
    bool got_end_stream = false;
    bool got_headers_end_stream = false;

    while (pos + 9 <= rx_buf_.size()) {
      uint32_t flen = ((uint32_t) rx_buf_[pos] << 16) |
                      ((uint32_t) rx_buf_[pos + 1] << 8) |
                      rx_buf_[pos + 2];
      uint8_t ftype = rx_buf_[pos + 3];
      uint8_t flags = rx_buf_[pos + 4];

      if (pos + 9 + flen > rx_buf_.size()) {
        return false;  // Incomplete frame
      }

      if (ftype == 0x00) {
        // DATA frame
        data_payload.insert(data_payload.end(),
                            rx_buf_.begin() + pos + 9,
                            rx_buf_.begin() + pos + 9 + flen);
        if (flags & 0x01) got_end_stream = true;
      } else if (ftype == 0x01) {
        // HEADERS frame (initial response headers or trailers)
        if (flags & 0x01) {
          got_end_stream = true;
          got_headers_end_stream = true;
        }
      }
      pos += 9 + flen;
    }

    // If we have DATA + end_stream (successful response)
    if (!data_payload.empty() && got_end_stream) {
      if (data_payload.size() >= 5) {
        uint32_t grpc_len = ((uint32_t) data_payload[1] << 24) |
                            ((uint32_t) data_payload[2] << 16) |
                            ((uint32_t) data_payload[3] << 8) |
                            data_payload[4];
        if (data_payload.size() >= 5 + grpc_len) {
          parse_rpc_response_(data_payload.data() + 5, grpc_len);
          connect_failures_ = 0;
          publish_connected_(true);
          return true;
        }
      }
    }

    // Trailers-only response (HEADERS with END_STREAM, no DATA) = error or empty OK
    if (got_headers_end_stream && data_payload.empty()) {
      ESP_LOGW(TAG, "RPC %s: trailers-only response (likely error)",
               current_rpc_path_.c_str());
      connect_failures_ = 0;  // server is reachable
      publish_connected_(true);
      return true;
    }

    return false;
  }

  // ---------------------------------------------------------------------------
  // Parse the gRPC response based on which RPC was called
  // ---------------------------------------------------------------------------
  void parse_rpc_response_(const uint8_t *data, size_t len) {
    if (current_rpc_path_.find("GetConsumptionLimit") != std::string::npos) {
      // LoadLimit { double value_watts=1; int32 duration_seconds=2; bool is_active=3; bool is_changeable=4; }
      ProtoDecoder dec(data, len);
      ProtoField f;
      float limit_w = 0;
      bool is_active = false;
      bool has_is_active = false;
      while (dec.next(f)) {
        switch (f.field_num) {
          case 1: limit_w = (f.wire_type == 1) ? (float) f.double_val : (float) f.varint_val; break;
          case 3: is_active = (bool) f.varint_val; has_is_active = true; break;
          default: break;
        }
      }
      last_known_limit_ = limit_w;
      if (limit_sensor_) limit_sensor_->publish_state(limit_w);
      // Only update lpc_active from device if the device reports it;
      // otherwise keep the local switch state (Bosch doesn't report is_active)
      if (has_is_active && lpc_active_sensor_) lpc_active_sensor_->publish_state(is_active);
      ESP_LOGI(TAG, "LPC: limit=%.0fW active=%d (reported=%d)", limit_w, is_active, has_is_active);
    } else if (current_rpc_path_.find("GetPowerConsumption") != std::string::npos) {
      // PowerMeasurement { double watts=1; Timestamp timestamp=2; }
      ProtoDecoder dec(data, len);
      ProtoField f;
      float watts = 0;
      while (dec.next(f)) {
        if (f.field_num == 1) {
          watts = (f.wire_type == 1) ? (float) f.double_val : (float) f.varint_val;
        }
      }
      if (power_sensor_) power_sensor_->publish_state(watts);
      ESP_LOGI(TAG, "Power: %.0fW", watts);
    } else if (current_rpc_path_.find("GetHeartbeatStatus") != std::string::npos) {
      // HeartbeatStatus { bool running=1; bool within_duration=2; }
      ProtoDecoder dec(data, len);
      ProtoField f;
      bool running = false;
      bool within_duration = false;
      while (dec.next(f)) {
        if (f.field_num == 1) running = (bool) f.varint_val;
        if (f.field_num == 2) within_duration = (bool) f.varint_val;
      }
      heartbeat_running_ = running;
      if (heartbeat_sensor_) heartbeat_sensor_->publish_state(running && within_duration);
      if (!running) {
        // Heartbeat stopped unexpectedly – restart it next poll
        ESP_LOGW(TAG, "Heartbeat not running – restarting");
        pending_heartbeat_start_ = true;
      }
    } else if (current_rpc_path_.find("StartHeartbeat") != std::string::npos) {
      ESP_LOGI(TAG, "StartHeartbeat: OK");
      heartbeat_running_ = true;
      if (heartbeat_sensor_) heartbeat_sensor_->publish_state(true);
    } else if (current_rpc_path_.find("GetFailsafeLimit") != std::string::npos) {
      // FailsafeLimit { double value_watts=1; double duration_minimum_seconds=2; }
      ProtoDecoder dec(data, len);
      ProtoField f;
      float fs_watts = 0;
      float fs_duration = 0;
      while (dec.next(f)) {
        if (f.field_num == 1) fs_watts = (f.wire_type == 1) ? (float) f.double_val : (float) f.varint_val;
        if (f.field_num == 2) fs_duration = (f.wire_type == 1) ? (float) f.double_val : (float) f.varint_val;
      }
      if (failsafe_limit_sensor_) failsafe_limit_sensor_->publish_state(fs_watts);
      if (failsafe_duration_sensor_) failsafe_duration_sensor_->publish_state(fs_duration);
      ESP_LOGI(TAG, "Failsafe: %.0fW / %.0fs", fs_watts, fs_duration);
    } else if (current_rpc_path_.find("ListPairedDevices") != std::string::npos) {
      // ListPairedDevicesResponse { repeated PairedDevice devices=1; }
      // PairedDevice { string ski=1; string brand=2; string model=3; string serial=4; }
      ProtoDecoder dec(data, len);
      ProtoField f;
      while (dec.next(f)) {
        if (f.field_num == 1 && f.wire_type == 2) {
          // Parse nested PairedDevice
          ProtoDecoder inner((const uint8_t *) f.string_val.data(), f.string_val.size());
          ProtoField pf;
          while (inner.next(pf)) {
            if (pf.wire_type != 2) continue;
            switch (pf.field_num) {
              case 2: if (brand_sensor_ && !pf.string_val.empty()) brand_sensor_->publish_state(pf.string_val); break;
              case 3: if (model_sensor_ && !pf.string_val.empty()) model_sensor_->publish_state(pf.string_val); break;
              case 4: if (serial_sensor_ && !pf.string_val.empty()) serial_sensor_->publish_state(pf.string_val); break;
              default: break;
            }
          }
          break;  // only first device
        }
      }
      ESP_LOGI(TAG, "Device info fetched");
    } else if (current_rpc_path_.find("WriteConsumptionLimit") != std::string::npos) {
      ESP_LOGI(TAG, "WriteConsumptionLimit: OK (limit=%.0f active=%d)", pending_limit_, pending_active_);
      // Update sensors with what we sent (device may not echo is_active)
      if (lpc_active_sensor_) lpc_active_sensor_->publish_state(pending_active_);
      if (limit_sensor_) limit_sensor_->publish_state(pending_limit_);
      last_known_limit_ = pending_limit_;
    } else {
      ESP_LOGD(TAG, "RPC %s: OK (%d bytes)", current_rpc_path_.c_str(), (int) len);
    }
  }

  // ---------------------------------------------------------------------------
  // Connection lifecycle helpers
  // ---------------------------------------------------------------------------
  void close_connection_() {
    if (sock_) {
      sock_->close();
      sock_.reset();
    }
    state_ = STATE_IDLE;
    tx_buf_.clear();
    rx_buf_.clear();
  }

  void close_and_fail_() {
    close_connection_();
    on_connect_failed_();
  }

  void on_connect_failed_() {
    connect_failures_ = std::min((uint8_t) (connect_failures_ + 1), (uint8_t) 4);
    publish_connected_(false);
  }

  // ---------------------------------------------------------------------------
  // Proto parsing / publishing
  // ---------------------------------------------------------------------------
  void publish_connected_(bool c) {
    connected_ = c;
    if (connected_sensor_) connected_sensor_->publish_state(c);
  }

  // ---------------------------------------------------------------------------
  // IPv4 parse helper (avoids needing inet_pton from lwip headers)
  // ---------------------------------------------------------------------------
  static bool parse_ipv4_(const std::string &ip_str, uint32_t &out) {
    uint8_t octets[4];
    int idx = 0;
    uint32_t val = 0;
    for (char c : ip_str) {
      if (c == '.') {
        if (idx >= 3 || val > 255) return false;
        octets[idx++] = (uint8_t) val;
        val = 0;
      } else if (c >= '0' && c <= '9') {
        val = val * 10 + (c - '0');
        if (val > 255) return false;
      } else {
        return false;
      }
    }
    if (idx != 3 || val > 255) return false;
    octets[3] = (uint8_t) val;
    memcpy(&out, octets, 4);
    return true;
  }
};

// ---------------------------------------------------------------------------
// LPC Limit Number entity
// ---------------------------------------------------------------------------
class EebusLpcLimitNumber : public number::Number, public Component {
 public:
  void set_parent(EebusLpcComponent *parent) { parent_ = parent; }

 protected:
  void control(float value) override {
    publish_state(value);
    if (parent_) parent_->set_lpc_limit(value);
  }

 private:
  EebusLpcComponent *parent_{nullptr};
};

// ---------------------------------------------------------------------------
// LPC Active Switch entity
// ---------------------------------------------------------------------------
class EebusLpcActiveSwitch : public switch_::Switch, public Component {
 public:
  void set_parent(EebusLpcComponent *parent) { parent_ = parent; }

 protected:
  void write_state(bool state) override {
    publish_state(state);
    if (parent_) parent_->set_lpc_active(state);
  }

 private:
  EebusLpcComponent *parent_{nullptr};
};

}  // namespace eebus_lpc
}  // namespace esphome
