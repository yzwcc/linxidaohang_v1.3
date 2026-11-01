// Synthetic frames exercise the real GLIM callback bus, not physical SLAM accuracy.
#include "acquisition.h"
#include <glim/odometry/callbacks.hpp>
#include <cmath>
#include <cstring>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

void require(bool condition) {
  if (!condition) throw std::runtime_error(std::string("acquisition_contract: ") + lingxi_acquisition_error());
}

glim::EstimationFrame::Ptr synthetic(long id) {
  auto frame = std::make_shared<glim::EstimationFrame>();
  frame->id = id;
  frame->stamp = 1000.0 + id * 2.0;
  frame->frame_id = glim::FrameID::IMU;
  frame->T_world_imu.setIdentity();
  frame->T_world_imu.linear() = Eigen::AngleAxisd(-M_PI / 12, Eigen::Vector3d::UnitY()).matrix();
  frame->T_world_imu.translation() = Eigen::Vector3d(-2, 1, 3);
  frame->T_lidar_imu.setIdentity();
  frame->T_lidar_imu.linear() = Eigen::AngleAxisd(M_PI / 6, Eigen::Vector3d::UnitX()).matrix();
  frame->T_lidar_imu.translation() = Eigen::Vector3d(.2, -.1, .3);
  frame->T_world_lidar = frame->T_world_imu * frame->T_lidar_imu.inverse();
  auto raw = std::make_shared<glim::PreprocessedFrame>();
  raw->stamp = frame->stamp;
  raw->scan_end_time = frame->stamp + 1;
  raw->times = {0, .5, 1};
  raw->points = {{1, 0, -.2, 1}, {1, 0, .5, 1}, {1, 0, 2, 1}};
  frame->raw_frame = raw;
  frame->imu_rate_trajectory.resize(8, 2);
  for (int i = 0; i < 2; ++i) {
    const Eigen::Quaterniond rotation(Eigen::AngleAxisd((20 + i * 90) * M_PI / 180, Eigen::Vector3d::UnitZ()));
    frame->imu_rate_trajectory.col(i) << frame->stamp + i, 2 + i * 2, 3 + i * 2, 4 + i * 2,
      rotation.x(), rotation.y(), rotation.z(), rotation.w();
  }
  return frame;
}

template <typename T> T at(const std::vector<unsigned char>& bytes, size_t offset) {
  T value;
  require(offset + sizeof(value) <= bytes.size());
  std::memcpy(&value, bytes.data() + offset, sizeof(value));
  return value;
}

int main(int argc, char** argv) {
  try {
    require(argc == 2 && lingxi_acquisition_open() == 0);
    const std::string scenario(argv[1]);
    glim::OdometryEstimationCallbacks::on_update_new_frame(synthetic(0));
    uint64_t metadata[4] = {};
    require(lingxi_acquisition_take(0, nullptr, metadata) == 0 && metadata[0] == 0);
    auto frame = synthetic(1);
    auto raw = std::const_pointer_cast<glim::PreprocessedFrame>(frame->raw_frame);
    if (scenario == "missing_raw") frame->raw_frame.reset();
    else if (scenario == "missing_trajectory") frame->imu_rate_trajectory.resize(8, 0);
    else if (scenario == "incomplete_trajectory") frame->imu_rate_trajectory(0, 1) -= .1;
    else if (scenario == "unordered_trajectory") frame->imu_rate_trajectory(0, 1) = frame->stamp;
    else if (scenario == "quaternion") frame->imu_rate_trajectory(7, 1) = 2;
    else if (scenario == "points") raw->points.clear();
    else if (scenario == "times") raw->times.pop_back();
    else if (scenario == "pose") frame->T_world_imu.linear()(0, 0) = 2;
    else if (scenario == "lidar_pose") frame->T_world_lidar.translation().x() += 1;
    else if (scenario == "skipped_id") frame->id = 2;
    else if (scenario == "invalid_stamp") frame->stamp = std::numeric_limits<double>::quiet_NaN();
    else if (scenario == "invalid_frame") frame->frame_id = glim::FrameID::LIDAR;
    else if (scenario == "return_overflow") raw->points.resize(65537, Eigen::Vector4d(1, 0, 0, 1));
    else if (scenario == "trajectory_overflow") frame->imu_rate_trajectory.resize(8, 4097);
    else if (scenario == "negative_time") raw->times[0] = -.01;
    else if (scenario == "unordered_time") raw->times[2] = .1;
    else if (scenario == "nonfinite_point") raw->points[0].x() = std::numeric_limits<double>::infinity();
    else if (scenario == "nonhomogeneous_point") raw->points[0].w() = 0;
    else if (scenario == "extrinsic") frame->T_lidar_imu.linear()(0, 0) = 2;
    else if (scenario == "raw_stamp") raw->stamp += .01;
    else if (scenario == "null_frame") frame.reset();
    if (scenario == "wrong_api_thread") {
      int take = 0, close = 0, counters = 0;
      std::thread other([&] {
        uint64_t values[5];
        take = lingxi_acquisition_take(0, nullptr, metadata);
        close = lingxi_acquisition_close();
        counters = lingxi_acquisition_counts(values);
      });
      other.join();
      require(take == -1 && close == -1 && counters == -1);
    }
    if (scenario == "wrong_thread") {
      std::thread other([&] { glim::OdometryEstimationCallbacks::on_update_new_frame(frame); });
      other.join();
    } else glim::OdometryEstimationCallbacks::on_update_new_frame(frame);
    if (scenario == "overflow") glim::OdometryEstimationCallbacks::on_update_new_frame(synthetic(2));
    if (scenario == "duplicate_id") glim::OdometryEstimationCallbacks::on_update_new_frame(frame);
    if (scenario == "pending_close") {
      require(lingxi_acquisition_close() == 0);
      uint64_t counts[5];
      require(lingxi_acquisition_counts(counts) == 0 && counts[4] == 1);
      require(lingxi_acquisition_take(0, nullptr, metadata) == -1);
      return 0;
    }
    const bool valid = scenario == "copy" || scenario == "export" || scenario == "capacity" ||
      scenario == "null_metadata" || scenario == "wrong_api_thread";
    if (!valid) {
      require(lingxi_acquisition_take(0, nullptr, metadata) == -1);
      require(std::strlen(lingxi_acquisition_error()) > 0);
      require(lingxi_acquisition_close() == 0);
      return 0;
    }
    if (scenario == "null_metadata") require(lingxi_acquisition_take(0, nullptr, nullptr) == -1);
    require(lingxi_acquisition_take(0, nullptr, metadata) == 1);
    require(metadata[0] == 1 && metadata[1] == 3 && metadata[2] == 2 && metadata[3] == 840);
    std::vector<unsigned char> bytes(metadata[3]);
    // The native copy owns every source value after the callback returns.
    raw->points[0].x() = 999;
    frame->imu_rate_trajectory.setZero();
    frame->T_world_imu.setIdentity();
    frame->T_lidar_imu.setIdentity();
    frame.reset(); raw.reset();
    require(lingxi_acquisition_take(bytes.size() - 1, bytes.data(), metadata) == -1);
    require(lingxi_acquisition_take(bytes.size(), bytes.data(), metadata) == 1);
    require(std::memcmp(bytes.data(), "LXOBS002", 8) == 0);
    require(at<uint64_t>(bytes, 184) == 1 && at<double>(bytes, 192) == 1002);
    require(at<double>(bytes, 624) == 2 && at<double>(bytes, 752) == 1);
    require(at<double>(bytes, 512) == -2 && at<double>(bytes, 384) == .2);
    require(lingxi_acquisition_take(0, nullptr, metadata) == 0);
    uint64_t counts[5];
    require(lingxi_acquisition_counts(counts) == 0);
    require(counts[0] == 2 && counts[1] == 1 && counts[2] == 1 && counts[3] == 1 && counts[4] == 0);
    if (scenario != "export") {
      glim::OdometryEstimationCallbacks::on_update_new_frame(synthetic(2));
      require(lingxi_acquisition_take(0, nullptr, metadata) == 1 && metadata[0] == 2);
      require(lingxi_acquisition_take(bytes.size(), bytes.data(), metadata) == 1);
    }
    require(lingxi_acquisition_close() == 0 && lingxi_acquisition_open() == -1);
    if (scenario == "export") std::cout.write(reinterpret_cast<const char*>(bytes.data()), bytes.size());
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
