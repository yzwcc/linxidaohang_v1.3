// Explicit synthetic frames test copying and ownership, not SLAM accuracy.
#include "point_output.h"
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <thread>
#include <gtsam_points/types/point_cloud_cpu.hpp>
#include <glim/odometry/callbacks.hpp>

void require(bool value) {
  if (!value) {
    throw std::runtime_error(lingxi_points_error());
  }
}

glim::EstimationFrame::Ptr synthetic(long id) {
  auto frame = std::make_shared<glim::EstimationFrame>();
  frame->id = id;
  frame->stamp = 1000.0 + id * 0.1;
  frame->frame_id = glim::FrameID::IMU;
  frame->T_world_imu.setIdentity();
  frame->T_world_imu.linear() = Eigen::AngleAxisd(0.5, Eigen::Vector3d::UnitZ()).matrix();
  frame->T_world_imu.translation() = Eigen::Vector3d(3, 4, 5);
  frame->T_world_lidar = frame->T_world_imu;
  frame->T_world_lidar.translation().x() += 0.25;
  std::vector<Eigen::Vector4d> points{{1, 2, 3, 1}, {4, 5, 6, 1}, {7, 8, 9, 1}};
  frame->frame = std::make_shared<gtsam_points::PointCloudCPU>(points);
  return frame;
}

int main(int argc, char** argv) {
  try {
    require(argc == 2 && lingxi_points_open(2) == 0);
    auto initial = synthetic(0);
    glim::OdometryEstimationCallbacks::on_update_new_frame(initial);
    double xyz[6], pose[16], metadata[4];
    require(lingxi_points_take(2, xyz, pose, metadata) == 0);
    const std::string scenario(argv[1]);
    auto frame = synthetic(1);
    if (scenario == "invalid_frame") {
      frame->frame_id = glim::FrameID::LIDAR;
    } else if (scenario == "invalid_stamp") {
      frame->stamp = std::numeric_limits<double>::quiet_NaN();
    } else if (scenario == "invalid_points") {
      auto cloud = std::make_shared<gtsam_points::PointCloudCPU>(
          std::vector<Eigen::Vector4d>{{0, 0, 0, 0}});
      frame->frame = cloud;
    } else if (scenario == "invalid_pose") {
      frame->T_world_imu.linear()(0, 0) = 3;
    } else if (scenario == "missing_points") {
      frame->frame.reset();
    } else if (scenario == "skipped_id") {
      frame->id = 2;
    }
    if (scenario == "wrong_api_thread") {
      std::thread other([&] {
        require(lingxi_points_take(2, xyz, pose, metadata) == -1);
        require(lingxi_points_close() == -1);
      });
      other.join();
    }
    if (scenario == "wrong_thread") {
      std::thread other([&] { glim::OdometryEstimationCallbacks::on_update_new_frame(frame); });
      other.join();
    } else {
      glim::OdometryEstimationCallbacks::on_update_new_frame(frame);
    }
    if (scenario == "overflow") {
      glim::OdometryEstimationCallbacks::on_update_new_frame(synthetic(2));
    }
    if (scenario == "pending_close") {
      require(lingxi_points_close() == 0);
      uint64_t counts[5];
      require(lingxi_points_counts(counts) == 0 && counts[4] == 1);
      require(lingxi_points_take(2, xyz, pose, metadata) == -1);
      return 0;
    }
    if (scenario != "copy" && scenario != "wrong_api_thread") {
      require(lingxi_points_take(2, xyz, pose, metadata) == -1);
      require(lingxi_points_close() == 0);
      return 0;
    }
    const Eigen::Vector4d expected = frame->T_world_imu * frame->frame->points[0];
    frame->T_world_imu.translation().setZero();
    frame->T_world_lidar.translation().setZero();
    frame.reset();
    require(lingxi_points_take(1, xyz, pose, metadata) == -1);
    require(lingxi_points_take(2, xyz, pose, metadata) == 2);
    for (int axis = 0; axis < 3; ++axis) {
      require(std::abs(xyz[axis] - expected[axis]) < 1e-12);
    }
    require(std::abs(pose[3] - 3.25) < 1e-12);
    require(metadata[0] == 1 && metadata[2] == 3 && metadata[3] == 2);
    require(lingxi_points_take(2, xyz, pose, metadata) == 0);
    uint64_t counts[5];
    require(lingxi_points_counts(counts) == 0);
    require(counts[0] == 2 && counts[1] == 1 && counts[2] == 1 && counts[3] == 1 && counts[4] == 0);
    require(lingxi_points_close() == 0);
    require(lingxi_points_open(2) == -1);
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << "\n";
    return 1;
  }
}
