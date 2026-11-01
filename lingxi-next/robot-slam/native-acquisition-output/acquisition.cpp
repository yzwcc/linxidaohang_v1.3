// Source-backed per-return geometry, copied before finalized submap processing.
#include "acquisition.h"
#include <glim/odometry/callbacks.hpp>
#include <algorithm>
#include <array>
#include <cmath>
#include <mutex>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>

namespace {
bool opened = false, active = false;
int callback_id = -1;
long previous_id = -1;
double previous_stamp = -1;
std::thread::id owner;
std::mutex state_mutex;
std::string failure, pending;
thread_local std::string api_error, error_snapshot;
std::array<uint64_t, 4> metadata{};
std::array<uint64_t, 5> counts{};

void check(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
void require_owner() {
  check(active && std::this_thread::get_id() == owner, "active_single_acquisition_owner_required");
}
void rigid(const Eigen::Isometry3d& value) {
  const auto& m = value.matrix();
  check(m.allFinite() && (m.row(3) - Eigen::RowVector4d(0, 0, 0, 1)).norm() < 1e-8 &&
    (value.linear().transpose() * value.linear() - Eigen::Matrix3d::Identity()).norm() < 1e-6 &&
    std::abs(value.linear().determinant() - 1) < 1e-6, "finite_rigid_acquisition_transform_required");
}
template <typename T> void scalar(std::ostream& out, T value) {
  static_assert(sizeof(T) == 8);
  out.write(reinterpret_cast<const char*>(&value), sizeof(value));
}
void matrix(std::ostream& out, const Eigen::Isometry3d& value) {
  for (int row = 0; row < 4; ++row) for (int col = 0; col < 4; ++col) scalar(out, value.matrix()(row, col));
}

void corrected(const glim::EstimationFrame::ConstPtr& frame) noexcept {
  std::lock_guard<std::mutex> lock(state_mutex);
  try {
    require_owner();
    if (!failure.empty()) return;
    ++counts[0];
    check(frame && frame->id == previous_id + 1 && frame->id < 9007199254740992L && std::isfinite(frame->stamp) &&
      frame->stamp > previous_stamp && frame->frame_id == glim::FrameID::IMU,
      "ordered_pinned_acquisition_frame_required");
    previous_id = frame->id;
    previous_stamp = frame->stamp;
    // GLIM's first initialized frame has no intra-scan IMU trajectory.
    if (frame->id == 0) { ++counts[1]; return; }
    check(pending.empty(), "unconsumed_acquisition_frame");
    const auto raw = frame->raw_frame;
    const auto& trajectory = frame->imu_rate_trajectory;
    check(raw && raw->stamp == frame->stamp && std::isfinite(raw->scan_end_time) &&
      raw->scan_end_time >= raw->stamp && !raw->points.empty() && raw->points.size() <= 65536 &&
      raw->points.size() == raw->times.size(), "bounded_complete_raw_acquisition_returns_required");
    check(trajectory.cols() >= 2 && trajectory.cols() <= 4096 && trajectory.allFinite() &&
      trajectory(0, 0) == frame->stamp && trajectory(0, trajectory.cols() - 1) >= raw->scan_end_time,
      "enabled_complete_intrascan_imu_trajectory_required");
    rigid(frame->T_world_imu);
    rigid(frame->T_lidar_imu);
    rigid(frame->T_world_lidar);
    check((frame->T_world_imu * frame->T_lidar_imu.inverse()).matrix().isApprox(
      frame->T_world_lidar.matrix(), 1e-8), "matching_corrected_acquisition_lidar_pose_required");
    for (int i = 0; i < trajectory.cols(); ++i) {
      check((i == 0 || trajectory(0, i) > trajectory(0, i - 1)) &&
        std::abs(trajectory.col(i).tail<4>().norm() - 1) < 1e-6,
        "ordered_unit_acquisition_trajectory_required");
    }
    for (size_t i = 0; i < raw->points.size(); ++i) {
      const auto& point = raw->points[i];
      const double offset = raw->times[i], time = raw->stamp + offset;
      check(std::isfinite(offset) && offset >= 0 && (i == 0 || offset >= raw->times[i - 1]) &&
        point.allFinite() && std::abs(point.w() - 1) < 1e-8 &&
        time >= trajectory(0, 0) && time <= trajectory(0, trajectory.cols() - 1) && time <= raw->scan_end_time,
        "finite_bracketed_acquisition_return_required");
    }
    Eigen::Isometry3d reference = Eigen::Isometry3d::Identity();
    reference.translation() = trajectory.col(0).segment<3>(1);
    reference.linear() = Eigen::Quaterniond(trajectory(7, 0), trajectory(4, 0),
      trajectory(5, 0), trajectory(6, 0)).toRotationMatrix();
    // Reuse the strict LXOBS002 parser with a single identity submap in odometry.
    std::ostringstream out(std::ios::binary | std::ios::out);
    out.exceptions(std::ios::failbit | std::ios::badbit);
    out.write("LXOBS002", 8);
    for (uint64_t value : {uint64_t(1), uint64_t(1), uint64_t(raw->points.size()), uint64_t(1), uint64_t(0)})
      scalar(out, value);
    matrix(out, Eigen::Isometry3d::Identity()); scalar(out, uint64_t(1));
    scalar(out, uint64_t(frame->id)); scalar(out, frame->stamp); scalar(out, raw->scan_end_time);
    scalar(out, uint64_t(raw->points.size())); scalar(out, uint64_t(trajectory.cols())); scalar(out, uint64_t(2));
    matrix(out, reference); matrix(out, frame->T_lidar_imu); matrix(out, frame->T_world_imu);
    for (int col = 0; col < trajectory.cols(); ++col)
      for (int row = 0; row < 8; ++row) scalar(out, trajectory(row, col));
    for (size_t i = 0; i < raw->points.size(); ++i) {
      scalar(out, raw->times[i]);
      for (int axis = 0; axis < 3; ++axis) scalar(out, raw->points[i][axis]);
    }
    pending = out.str();
    check(pending.size() == 616 + trajectory.cols() * 64 + raw->points.size() * 32,
      "complete_acquisition_archive_accounting_required");
    metadata = {uint64_t(frame->id), uint64_t(raw->points.size()), uint64_t(trajectory.cols()), uint64_t(pending.size())};
    ++counts[2];
  } catch (const std::exception& error) {
    failure = error.what(); pending.clear();
  } catch (...) {
    failure = "unknown_acquisition_copy_failure"; pending.clear();
  }
}
template <typename Function> int guarded(Function function) {
  std::lock_guard<std::mutex> lock(state_mutex);
  try { api_error.clear(); return function(); }
  catch (const std::exception& error) { api_error = error.what(); return -1; }
  catch (...) { api_error = "unknown_acquisition_api_failure"; return -1; }
}
}  // namespace

extern "C" const char* lingxi_acquisition_error(void) {
  std::lock_guard<std::mutex> lock(state_mutex);
  error_snapshot = failure.empty() ? api_error : failure;
  return error_snapshot.c_str();
}
extern "C" int lingxi_acquisition_open(void) {
  return guarded([] {
    const uint16_t endian = 1;
    check(!opened && *reinterpret_cast<const uint8_t*>(&endian) == 1 && sizeof(double) == 8 &&
      std::numeric_limits<double>::is_iec559,
      "one_little_endian_acquisition_session_per_process_required");
    opened = active = true; owner = std::this_thread::get_id();
    callback_id = glim::OdometryEstimationCallbacks::on_update_new_frame.add(corrected);
    return 0;
  });
}
extern "C" int lingxi_acquisition_take(size_t capacity, unsigned char* bytes, uint64_t* destination) {
  return guarded([&] {
    require_owner();
    check(failure.empty(), "native_acquisition_callback_failed");
    check(destination, "acquisition_metadata_destination_required");
    std::fill(destination, destination + 4, 0);
    if (pending.empty()) return 0;
    std::copy(metadata.begin(), metadata.end(), destination);
    if (!bytes) return 1;
    check(capacity >= pending.size(), "complete_acquisition_copy_capacity_required");
    std::copy(pending.begin(), pending.end(), bytes);
    pending.clear(); ++counts[3]; return 1;
  });
}
extern "C" int lingxi_acquisition_counts(uint64_t* destination) {
  return guarded([&] {
    check(opened && std::this_thread::get_id() == owner && destination, "acquisition_counter_owner_required");
    std::copy(counts.begin(), counts.end(), destination); return 0;
  });
}
extern "C" int lingxi_acquisition_close(void) {
  return guarded([] {
    require_owner();
    glim::OdometryEstimationCallbacks::on_update_new_frame.remove(callback_id);
    counts[4] += pending.empty() ? 0 : 1;
    active = false; pending.clear(); return 0;
  });
}
