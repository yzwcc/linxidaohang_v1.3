// A bounded copy at the corrected-frame callback, never a retained GLIM frame.
#include "point_output.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <glim/odometry/callbacks.hpp>

namespace {
bool opened_once = false, active = false, pending = false;
int callback_id = -1, maximum = 0;
long previous_id = -1;
double previous_stamp = -1.0;
std::thread::id owner;
std::mutex state_mutex;
std::string failure;
thread_local std::string api_error, error_snapshot;
std::vector<double> xyz;
std::array<double, 16> pose;
std::array<double, 4> metadata;
std::array<uint64_t, 5> counts{};

void require_owner() {
  if (!active || std::this_thread::get_id() != owner) {
    throw std::runtime_error("active_single_native_point_owner_required");
  }
}

void require_rigid(const Eigen::Isometry3d& transform) {
  const auto& matrix = transform.matrix();
  if (!matrix.allFinite() ||
      (matrix.row(3) - Eigen::RowVector4d(0, 0, 0, 1)).norm() > 1e-7 ||
      (transform.linear().transpose() * transform.linear() - Eigen::Matrix3d::Identity()).norm() > 1e-5 ||
      std::abs(transform.linear().determinant() - 1.0) > 1e-5) {
    throw std::runtime_error("finite_rigid_native_point_transform_required");
  }
}

void corrected(const glim::EstimationFrame::ConstPtr& frame) noexcept {
  std::lock_guard<std::mutex> lock(state_mutex);
  try {
    require_owner();
    if (!failure.empty()) {
      return;
    }
    ++counts[0];
    if (!frame || frame->id != previous_id + 1 || !std::isfinite(frame->stamp) ||
        frame->stamp <= previous_stamp || frame->frame_id != glim::FrameID::IMU) {
      throw std::runtime_error("ordered_pinned_cpu_imu_frame_required");
    }
    previous_id = frame->id;
    previous_stamp = frame->stamp;
    // Pinned GLIM's ID 0 is transformed raw points, before its first smoother update.
    if (frame->id == 0) {
      ++counts[1];
      return;
    }
    if (pending) {
      throw std::runtime_error("unconsumed_native_point_frame");
    }
    if (!frame->frame || !frame->frame->points || frame->frame->size() == 0 ||
        frame->frame->size() > 2000000) {
      throw std::runtime_error("bounded_native_cpu_points_required");
    }
    require_rigid(frame->T_world_imu);
    require_rigid(frame->T_world_lidar);
    const size_t native_count = frame->frame->size();
    const size_t copied = std::min(native_count, static_cast<size_t>(maximum));
    xyz.resize(copied * 3);
    for (size_t i = 0; i < copied; ++i) {
      const auto& point = frame->frame->points[i * native_count / copied];
      if (!point.allFinite() || std::abs(point.w() - 1.0) > 1e-7) {
        throw std::runtime_error("finite_homogeneous_native_points_required");
      }
      const Eigen::Vector4d world = frame->T_world_imu * point;
      if (!world.allFinite()) {
        throw std::runtime_error("finite_world_points_required");
      }
      for (int axis = 0; axis < 3; ++axis) {
        xyz[i * 3 + axis] = world[axis];
      }
    }
    Eigen::Map<Eigen::Matrix<double, 4, 4, Eigen::RowMajor>> destination(pose.data());
    destination = frame->T_world_lidar.matrix();
    metadata = {static_cast<double>(frame->id), frame->stamp,
                static_cast<double>(native_count), static_cast<double>(copied)};
    pending = true;
    ++counts[2];
  } catch (const std::exception& error) {
    failure = error.what();
    pending = false;
    xyz.clear();
  } catch (...) {
    failure = "unknown_native_point_copy_failure";
    pending = false;
    xyz.clear();
  }
}

template <typename Function>
int guarded(Function function) {
  std::lock_guard<std::mutex> lock(state_mutex);
  try {
    api_error.clear();
    return function();
  } catch (const std::exception& error) {
    api_error = error.what();
    return -1;
  } catch (...) {
    api_error = "unknown_native_point_api_failure";
    return -1;
  }
}
}  // namespace

extern "C" const char* lingxi_points_error(void) {
  std::lock_guard<std::mutex> lock(state_mutex);
  error_snapshot = failure.empty() ? api_error : failure;
  return error_snapshot.c_str();
}

extern "C" int lingxi_points_open(int maximum_points) {
  return guarded([&]() {
    if (opened_once || maximum_points < 1 || maximum_points > 8000) {
      throw std::runtime_error("one_bounded_point_session_per_process_required");
    }
    opened_once = true;
    maximum = maximum_points;
    owner = std::this_thread::get_id();
    xyz.reserve(static_cast<size_t>(maximum) * 3);
    callback_id = glim::OdometryEstimationCallbacks::on_update_new_frame.add(corrected);
    active = true;
    return 0;
  });
}

extern "C" int lingxi_points_take(int capacity, double* world_xyz, double* lidar_pose, double* output_metadata) {
  return guarded([&]() {
    require_owner();
    if (!failure.empty()) {
      throw std::runtime_error("native_point_callback_failed");
    }
    if (!pending) {
      return 0;
    }
    if (!world_xyz || !lidar_pose || !output_metadata || capacity < static_cast<int>(xyz.size() / 3)) {
      throw std::runtime_error("complete_point_output_capacity_required");
    }
    std::copy(xyz.begin(), xyz.end(), world_xyz);
    std::copy(pose.begin(), pose.end(), lidar_pose);
    std::copy(metadata.begin(), metadata.end(), output_metadata);
    pending = false;
    xyz.clear();
    ++counts[3];
    return static_cast<int>(metadata[3]);
  });
}

extern "C" int lingxi_points_counts(uint64_t* destination) {
  return guarded([&]() {
    if (!opened_once || std::this_thread::get_id() != owner || !destination) {
      throw std::runtime_error("single_point_counter_owner_required");
    }
    std::copy(counts.begin(), counts.end(), destination);
    return 0;
  });
}

extern "C" int lingxi_points_close(void) {
  return guarded([&]() {
    require_owner();
    glim::OdometryEstimationCallbacks::on_update_new_frame.remove(callback_id);
    counts[4] += pending ? 1 : 0;
    active = pending = false;
    xyz.clear();
    return 0;
  });
}
