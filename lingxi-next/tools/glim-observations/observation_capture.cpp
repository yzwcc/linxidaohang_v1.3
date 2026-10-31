#include "observation_capture.hpp"
#include <glim/mapping/callbacks.hpp>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <map>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {
constexpr size_t max_scans = 4096, max_points = 16777216, max_scan_points = 65536;
constexpr size_t max_trajectory_samples = 1048576;
struct Scan {
  uint64_t id;
  uint64_t trajectory_kind;
  double stamp, end;
  Eigen::Isometry3d reference, lidar_imu, local_imu;
  Eigen::Matrix<double, 8, -1> trajectory;
  std::vector<std::array<double, 4>> points;
};
struct Submap {
  Eigen::Isometry3d pose;
  std::vector<Scan> scans;
};
std::map<long, Scan> pending;
std::vector<Submap> archived;
size_t scans = 0, points = 0, revision = 0, trajectory_samples = 0;
long last_id = -1;
double last_stamp = -1;
bool enabled = false, exported = false;
int insert_callback = -1, update_callback = -1;
std::thread::id owner;
thread_local std::string error;

template <typename Function> int guarded(Function function) {
  try { error.clear(); function(); return 0; }
  catch (const std::exception& exception) { error = exception.what(); return -1; }
  catch (...) { error = "unknown_observation_capture_exception"; return -1; }
}
void check(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
void same_owner() {
  if (owner == std::thread::id{}) owner = std::this_thread::get_id();
  check(owner == std::this_thread::get_id(), "single_mapping_observation_owner_required");
}
void rigid(const Eigen::Isometry3d& pose) {
  const auto& m = pose.matrix();
  check(m.allFinite() && (m.row(3) - Eigen::RowVector4d(0, 0, 0, 1)).norm() < 1e-8 &&
    (pose.linear().transpose() * pose.linear() - Eigen::Matrix3d::Identity()).norm() < 1e-6 &&
    std::abs(pose.linear().determinant() - 1) < 1e-6, "finite_rigid_observation_pose_required");
}
void insert(const glim::SubMap::ConstPtr& submap) {
  same_owner();
  check(submap && submap->id == static_cast<int>(archived.size()) && !submap->frames.empty(),
    "ordered_observation_submaps_required");
  rigid(submap->T_world_origin);
  Submap next{submap->T_world_origin, {}};
  for (const auto& frame : submap->frames) {
    check(frame && pending.count(frame->id), "every_submap_frame_needs_source_observation");
    auto scan = std::move(pending.at(frame->id));
    pending.erase(frame->id);
    scan.local_imu = submap->T_world_origin.inverse() * frame->T_world_imu;
    rigid(scan.local_imu);
    next.scans.push_back(std::move(scan));
  }
  archived.push_back(std::move(next));
}
void update(const std::vector<glim::SubMap::Ptr>& submaps) {
  same_owner();
  check(submaps.size() == archived.size() && !submaps.empty(), "complete_observation_pose_revision_required");
  for (size_t i = 0; i < submaps.size(); ++i) {
    check(submaps[i] && submaps[i]->id == static_cast<int>(i), "ordered_observation_pose_revision_required");
    rigid(submaps[i]->T_world_origin);
    archived[i].pose = submaps[i]->T_world_origin;
  }
  ++revision;
}
template <typename T> void scalar(std::ofstream& out, T value) {
  static_assert(sizeof(T) == 8);
  out.write(reinterpret_cast<const char*>(&value), sizeof(value));
}
void matrix(std::ofstream& out, const Eigen::Isometry3d& value) {
  for (int row = 0; row < 4; ++row) for (int col = 0; col < 4; ++col) scalar(out, value.matrix()(row, col));
}
}  // namespace

namespace lingxi {
void retain_acquisition_trajectory(glim::EstimationFrame& frame) {
  frame.custom_data["lingxi.original_imu_trajectory"] =
    std::make_shared<Eigen::Matrix<double, 8, -1>>(frame.imu_rate_trajectory);
}
void capture_observation(const glim::EstimationFrame& frame) {
  check(enabled && !exported, "explicit_observation_capture_enable_required");
  same_owner();
  const auto raw = frame.raw_frame;
  const auto* trajectory_source = &frame.imu_rate_trajectory;
  const auto* original = frame.get_custom_data<Eigen::Matrix<double, 8, -1>>("lingxi.original_imu_trajectory");
  uint64_t trajectory_kind = original ? 1 : 2;
  if (raw && (trajectory_source->cols() < 2 || (*trajectory_source)(0, 0) > frame.stamp ||
      (*trajectory_source)(0, trajectory_source->cols() - 1) < raw->scan_end_time) && original) {
    trajectory_source = original;
    trajectory_kind = 2;
  }
  const auto& trajectory = *trajectory_source;
  check(frame.frame_id == glim::FrameID::IMU && frame.id > last_id &&
    std::isfinite(frame.stamp) && frame.stamp > last_stamp && raw &&
    raw->stamp == frame.stamp && std::isfinite(raw->scan_end_time) &&
    raw->scan_end_time >= raw->stamp && !raw->points.empty() &&
    raw->points.size() == raw->times.size() && raw->points.size() <= max_scan_points &&
    scans < max_scans && raw->points.size() <= max_points - points,
    "bounded_ordered_raw_observations_required");
  check(trajectory.cols() >= 2 && trajectory.cols() <= 4096 && trajectory.allFinite() &&
    static_cast<size_t>(trajectory.cols()) <= max_trajectory_samples - trajectory_samples,
    "measured_scan_trajectory_required");
  rigid(frame.T_world_imu);
  rigid(frame.T_lidar_imu);
  for (int i = 0; i < trajectory.cols(); ++i) {
    check((i == 0 || trajectory(0, i) > trajectory(0, i - 1)) &&
      std::abs(trajectory.col(i).tail<4>().norm() - 1) < 1e-6,
      "ordered_unit_quaternion_trajectory_required");
  }
  check(trajectory(0, 0) == frame.stamp, "trajectory_scan_reference_stamp_required");
  Eigen::Isometry3d acquisition_reference = Eigen::Isometry3d::Identity();
  acquisition_reference.translation() = trajectory.col(0).segment<3>(1);
  acquisition_reference.linear() = Eigen::Quaterniond(trajectory(7, 0), trajectory(4, 0),
    trajectory(5, 0), trajectory(6, 0)).toRotationMatrix();
  Scan scan{static_cast<uint64_t>(frame.id), trajectory_kind, frame.stamp, raw->scan_end_time,
    acquisition_reference, frame.T_lidar_imu, Eigen::Isometry3d::Identity(), trajectory, {}};
  scan.points.reserve(raw->points.size());
  for (size_t i = 0; i < raw->points.size(); ++i) {
    const double offset = raw->times[i], time = raw->stamp + offset;
    const auto& point = raw->points[i];
    check(std::isfinite(offset) && offset >= 0 && (i == 0 || offset >= raw->times[i - 1]) &&
      point.allFinite() && std::abs(point.w() - 1) < 1e-8, "finite_ordered_raw_return_required");
    check(time >= trajectory(0, 0) && time <= trajectory(0, trajectory.cols() - 1) && time <= scan.end,
      "every_return_needs_bracketed_acquisition_time_without_extrapolation");
    scan.points.push_back({offset, point.x(), point.y(), point.z()});
  }
  points += scan.points.size();
  trajectory_samples += trajectory.cols();
  ++scans;
  last_id = frame.id;
  last_stamp = frame.stamp;
  check(pending.emplace(frame.id, std::move(scan)).second, "unique_raw_scan_required");
}
}  // namespace lingxi

extern "C" const char* lingxi_observation_error() { return error.c_str(); }
extern "C" int lingxi_observation_enable() {
  return guarded([] {
    check(!enabled, "one_observation_archive_per_process_required");
    enabled = true;
    insert_callback = glim::GlobalMappingCallbacks::on_insert_submap.add(insert);
    update_callback = glim::GlobalMappingCallbacks::on_update_submaps.add(update);
  });
}
extern "C" int lingxi_observation_save(const char* path, uint64_t* counts) {
  return guarded([&] {
    // Caller must first finish/drain the async mapper; no concurrent mutation.
    check(enabled && !exported && path && counts && !std::filesystem::exists(path) &&
      pending.empty() && !archived.empty() && revision > 0 && scans > 0,
      "complete_drained_observation_archive_required");
    const uint16_t endian = 1;
    check(*reinterpret_cast<const uint8_t*>(&endian) == 1 && sizeof(double) == 8,
      "little_endian_ieee754_archive_host_required");
    size_t saved_scans = 0, saved_points = 0;
    std::ofstream out(path, std::ios::binary);
    out.exceptions(std::ios::failbit | std::ios::badbit);
    out.write("LXOBS002", 8);
    for (uint64_t value : {uint64_t(archived.size()), uint64_t(scans), uint64_t(points), uint64_t(revision)}) scalar(out, value);
    for (size_t i = 0; i < archived.size(); ++i) {
      const auto& map = archived[i];
      scalar(out, uint64_t(i)); matrix(out, map.pose); scalar(out, uint64_t(map.scans.size()));
      for (const auto& scan : map.scans) {
        scalar(out, scan.id); scalar(out, scan.stamp); scalar(out, scan.end);
        scalar(out, uint64_t(scan.points.size())); scalar(out, uint64_t(scan.trajectory.cols()));
        scalar(out, scan.trajectory_kind);
        matrix(out, scan.reference); matrix(out, scan.lidar_imu); matrix(out, scan.local_imu);
        for (int col = 0; col < scan.trajectory.cols(); ++col)
          for (int row = 0; row < 8; ++row) scalar(out, scan.trajectory(row, col));
        for (const auto& point : scan.points) for (double value : point) scalar(out, value);
        ++saved_scans; saved_points += scan.points.size();
      }
    }
    out.close();
    check(saved_scans == scans && saved_points == points, "complete_observation_write_accounting_required");
    const uint64_t values[] = {uint64_t(archived.size()), uint64_t(scans), uint64_t(points), uint64_t(revision)};
    std::copy(std::begin(values), std::end(values), counts);
    exported = true;
  });
}
extern "C" int lingxi_observation_shutdown() {
  return guarded([] {
    if (insert_callback >= 0) glim::GlobalMappingCallbacks::on_insert_submap.remove(insert_callback);
    if (update_callback >= 0) glim::GlobalMappingCallbacks::on_update_submaps.remove(update_callback);
    insert_callback = update_callback = -1;
    pending.clear(); archived.clear();
  });
}
