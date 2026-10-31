// Isolated synthetic capture: no odometry, sensors, network or actuators.
#include "observation_capture.hpp"
#include <glim/mapping/callbacks.hpp>
#include <array>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>

extern "C" int lingxi_observation_enable();
extern "C" int lingxi_observation_save(const char*, uint64_t*);
extern "C" int lingxi_observation_shutdown();
extern "C" const char* lingxi_observation_error();

void require(bool value) {
  if (!value) throw std::runtime_error("synthetic_observation_contract_failed");
}
int main(int argc, char** argv) {
  require(argc == 3);
  const std::string mode = argv[1];
  auto frame = std::make_shared<glim::EstimationFrame>();
  auto raw = std::make_shared<glim::PreprocessedFrame>();
  raw->stamp = frame->stamp = 10;
  raw->scan_end_time = 11;
  raw->times = {0, .5, 1};
  raw->points = {Eigen::Vector4d(1, 0, 0, 1), Eigen::Vector4d(1, 0, 0, 1), Eigen::Vector4d(1, 0, 0, 1)};
  frame->raw_frame = raw;
  frame->id = 7;
  frame->frame_id = glim::FrameID::IMU;
  frame->T_world_imu = frame->T_world_lidar = frame->T_lidar_imu = Eigen::Isometry3d::Identity();
  frame->imu_rate_trajectory.resize(8, 2);
  frame->imu_rate_trajectory.col(0) << 10, 0, 0, 0, 0, 0, 0, 1;
  frame->imu_rate_trajectory.col(1) << 11, 2, 0, 0, 0, 0, std::sqrt(.5), std::sqrt(.5);
  bool rejected = false;
  try { lingxi::capture_observation(*frame); } catch (const std::runtime_error&) { rejected = true; }
  require(rejected && lingxi_observation_enable() == 0);
  if (mode == "extrinsic") {
    frame->T_lidar_imu.translation() = Eigen::Vector3d(1, 2, 3);
    frame->T_lidar_imu.linear() = Eigen::AngleAxisd(std::acos(-1.) / 2, Eigen::Vector3d::UnitX()).toRotationMatrix();
    frame->T_world_lidar = frame->T_world_imu * frame->T_lidar_imu.inverse();
  }
  if (mode == "reference-shift") frame->imu_rate_trajectory.row(1).array() += 5;
  if (mode == "fallback") {
    lingxi::retain_acquisition_trajectory(*frame);
    frame->imu_rate_trajectory(0, 1) = 10.25;
  }
  if (mode == "unbracketed") raw->times.back() = 1.01;
  if (mode == "missing-trajectory") frame->imu_rate_trajectory.resize(8, 0);
  if (mode == "unbracketed" || mode == "missing-trajectory") {
    rejected = false;
    try { lingxi::capture_observation(*frame); } catch (const std::runtime_error&) { rejected = true; }
    require(rejected && lingxi_observation_shutdown() == 0);
    std::cout << "isolated invalid acquisition rejected\n";
    return 0;
  }
  lingxi::capture_observation(*frame);
  rejected = false;
  try { lingxi::capture_observation(*frame); } catch (const std::runtime_error&) { rejected = true; }
  require(rejected);
  auto dropped = frame->clone_wo_points();
  require(!dropped->raw_frame);
  dropped->T_world_imu.translation().x() = 2;
  auto submap = std::make_shared<glim::SubMap>();
  submap->id = 0;
  submap->T_world_origin = Eigen::Isometry3d::Identity();
  submap->T_world_origin.translation().x() = 10;
  submap->frames = {dropped};
  glim::GlobalMappingCallbacks::on_insert_submap(submap);
  const std::vector<glim::SubMap::Ptr> submaps{submap};
  glim::GlobalMappingCallbacks::on_update_submaps(submaps);
  if (mode == "corrected") {
    submap->T_world_origin.translation().x() = 20;
    submap->T_world_origin.linear() = Eigen::AngleAxisd(std::acos(-1.) / 2, Eigen::Vector3d::UnitZ()).toRotationMatrix();
  }
  glim::GlobalMappingCallbacks::on_update_submaps(submaps);
  std::array<uint64_t, 4> counts{};
  if (lingxi_observation_save(argv[2], counts.data()) != 0) throw std::runtime_error(lingxi_observation_error());
  require(counts == std::array<uint64_t, 4>{1, 1, 3, 2});
  require(lingxi_observation_save(argv[2], counts.data()) < 0);
  require(lingxi_observation_shutdown() == 0);
  std::cout << "isolated native observation capture passed\n";
}
