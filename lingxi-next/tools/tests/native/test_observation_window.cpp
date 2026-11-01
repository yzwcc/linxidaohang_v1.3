// Actual callback and concurrent reader fixture. No odometry, sensors or actuators.
#include "observation_capture.hpp"
#include <glim/mapping/callbacks.hpp>
#include <array>
#include <atomic>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include <thread>

extern "C" int lingxi_observation_enable();
extern "C" int lingxi_observation_window(uint64_t*, unsigned char*, size_t, uint64_t);
extern "C" int lingxi_observation_save(const char*, uint64_t*);
extern "C" int lingxi_observation_shutdown();

void require(bool value) {
  if (!value) throw std::runtime_error("native_observation_window_contract_failed");
}
void save_window(const std::string& path) {
  std::array<uint64_t, 7> header{};
  require(lingxi_observation_window(header.data(), nullptr, 0, 0) == 0 && header[0] > 0);
  std::vector<unsigned char> bytes(header[6], 0x5a);
  require(lingxi_observation_window(header.data(), bytes.data(), bytes.size(), header[0] - 1) == 1);
  for (auto value : bytes) require(value == 0x5a);
  require(lingxi_observation_window(header.data(), bytes.data(), bytes.size() - 1, header[0]) == 1);
  for (auto value : bytes) require(value == 0x5a);
  require(lingxi_observation_window(header.data(), bytes.data(), bytes.size(), header[0]) == 0);
  std::ofstream out(path, std::ios::binary);
  out.exceptions(std::ios::failbit | std::ios::badbit);
  out.write(reinterpret_cast<const char*>(header.data()), sizeof(header));
  out.write(reinterpret_cast<const char*>(bytes.data()), bytes.size());
}
int main(int argc, char** argv) {
  require(argc == 3 && lingxi_observation_enable() == 0);
  const std::string mode = argv[1], path = argv[2];
  std::array<uint64_t, 7> empty{};
  require(lingxi_observation_window(empty.data(), nullptr, 0, 0) == 0);
  require(empty == std::array<uint64_t, 7>{});
  std::atomic<bool> stop{false}, failed{false};
  std::atomic<size_t> reads{0};
  std::thread reader([&] {
    while (!stop) {
      std::array<uint64_t, 7> header{};
      if (lingxi_observation_window(header.data(), nullptr, 0, 0) != 0) { failed = true; break; }
      if (!header[0]) continue;
      std::vector<unsigned char> bytes(header[6]);
      const int result = lingxi_observation_window(header.data(), bytes.data(), bytes.size(), header[0]);
      if (result == 1) continue;
      if (result != 0 || bytes.size() < 40) { failed = true; break; }
      uint64_t payload_revision = 0;
      std::memcpy(&payload_revision, bytes.data() + 32, 8);
      if (result != 0 || header[6] != bytes.size() || std::memcmp(bytes.data(), "LXOBS002", 8) != 0 ||
          payload_revision != header[0]) { failed = true; break; }
      ++reads;
    }
  });
  std::vector<glim::SubMap::Ptr> submaps;
  try {
    for (int i = 0; i < 12; ++i) {
      auto raw = std::make_shared<glim::PreprocessedFrame>();
      auto frame = std::make_shared<glim::EstimationFrame>();
      raw->stamp = frame->stamp = 10 + 2 * i;
      raw->scan_end_time = frame->stamp + 1;
      const size_t count = mode == "point-limit" || mode == "joint-limits" ? 65536 : 3;
      for (size_t p = 0; p < count; ++p) {
        raw->times.push_back(double(p) / (count - 1));
        raw->points.push_back(Eigen::Vector4d(1, 0, 0, 1));
      }
      frame->id = i; frame->frame_id = glim::FrameID::IMU; frame->raw_frame = raw;
      frame->T_world_imu = frame->T_world_lidar = frame->T_lidar_imu = Eigen::Isometry3d::Identity();
      const int samples = mode == "joint-limits" ? 4096 : 2;
      frame->imu_rate_trajectory.resize(8, samples);
      for (int j = 0; j < samples; ++j) {
        const double offset = double(j) / (samples - 1);
        frame->imu_rate_trajectory.col(j) << frame->stamp + offset, offset, 0, 0, 0, 0, 0, 1;
      }
      lingxi::capture_observation(*frame);
      if (i % 3 == 0) {
        auto submap = std::make_shared<glim::SubMap>();
        submap->id = i / 3; submap->T_world_origin = Eigen::Isometry3d::Identity();
        submaps.push_back(submap);
      }
      submaps.back()->frames.push_back(frame->clone_wo_points());
      if (i % 3 == 2) {
        glim::GlobalMappingCallbacks::on_insert_submap(submaps.back());
        glim::GlobalMappingCallbacks::on_update_submaps(submaps);
        if (i == 2) save_window(path + ".first");
      }
    }
    save_window(path);
    submaps.back()->T_world_origin.translation().x() = 5;
    glim::GlobalMappingCallbacks::on_update_submaps(submaps);
    save_window(path + ".corrected");
    std::array<uint64_t, 4> counts{};
    require(lingxi_observation_save((path + ".archive").c_str(), counts.data()) == 0);
    require(counts[0] == 4 && counts[1] == 12 && counts[3] == 5);
  } catch (...) {
    stop = true; reader.join(); throw;
  }
  stop = true; reader.join();
  require(!failed && reads > 0);
  require(lingxi_observation_shutdown() == 0);
  require(lingxi_observation_window(empty.data(), nullptr, 0, 0) < 0);
}
