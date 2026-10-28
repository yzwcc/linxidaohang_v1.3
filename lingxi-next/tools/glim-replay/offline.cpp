// Single-process, single-trial C ABI. No ROS, network, or robot command interfaces.
#include <chrono>
#include <cmath>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include <glim/util/config.hpp>
#include <glim/preprocess/cloud_preprocessor.hpp>
#include <glim/odometry/odometry_estimation_base.hpp>

namespace {
std::shared_ptr<glim::OdometryEstimationBase> odometry;
std::unique_ptr<glim::CloudPreprocessor> preprocessor;
std::string error;
double last_imu = -1;
double last_frame = -1;
bool initialized_once = false;

template <typename Function>
int guarded(Function function) {
  try {
    error.clear();
    return function();
  } catch (const std::exception& exception) {
    error = exception.what();
    return -1;
  } catch (...) {
    error = "unknown_native_exception";
    return -1;
  }
}
}  // namespace

extern "C" const char* lingxi_error() {
  return error.c_str();
}

extern "C" int lingxi_open(const char* configuration, const char* module) {
  return guarded([&]() {
    if (initialized_once || !configuration || !module) {
      throw std::runtime_error("one_explicit_trial_per_process_required");
    }
    initialized_once = true;
    glim::GlobalConfig::instance(configuration);
    preprocessor = std::make_unique<glim::CloudPreprocessor>();
    odometry = glim::OdometryEstimationBase::load_module(module);
    if (!odometry || !odometry->requires_imu()) {
      throw std::runtime_error("imu_odometry_module_required");
    }
    return 0;
  });
}

extern "C" int lingxi_imu(double stamp, const double* acceleration, const double* angular) {
  return guarded([&]() {
    if (!odometry || !acceleration || !angular || !std::isfinite(stamp) || stamp <= last_imu) {
      throw std::runtime_error("ordered_imu_required");
    }
    Eigen::Map<const Eigen::Vector3d> acc(acceleration);
    Eigen::Map<const Eigen::Vector3d> gyro(angular);
    if (!acc.allFinite() || !gyro.allFinite()) {
      throw std::runtime_error("finite_imu_required");
    }
    odometry->insert_imu(stamp, acc, gyro);
    last_imu = stamp;
    return 0;
  });
}

extern "C" int lingxi_frame(
    double stamp, int count, const double* xyz, const double* offsets,
    double* pose_row_major, double* statistics) {
  return guarded([&]() {
    if (!odometry || !preprocessor || !xyz || !offsets || !pose_row_major || !statistics
        || count < 10 || count > 2000000 || !std::isfinite(stamp) || stamp <= last_frame) {
      throw std::runtime_error("ordered_finite_frame_required");
    }
    auto raw = std::make_shared<glim::RawPoints>();
    raw->stamp = stamp;
    raw->points.reserve(count);
    raw->times.reserve(count);
    double latest = stamp;
    for (int i = 0; i < count; ++i) {
      Eigen::Vector4d point(xyz[i * 3], xyz[i * 3 + 1], xyz[i * 3 + 2], 1.0);
      if (!point.allFinite() || !std::isfinite(offsets[i]) || offsets[i] < 0 || offsets[i] > 0.2) {
        throw std::runtime_error("finite_points_and_explicit_second_offsets_required");
      }
      raw->points.push_back(point);
      raw->times.push_back(offsets[i]);
      latest = std::max(latest, stamp + offsets[i]);
    }
    if (last_imu < latest) {
      throw std::runtime_error("imu_must_bracket_entire_scan");
    }
    auto start = std::chrono::steady_clock::now();
    auto prepared = preprocessor->preprocess(raw);
    auto after_preprocess = std::chrono::steady_clock::now();
    if (prepared->size() < 50) {
      throw std::runtime_error("insufficient_preprocessed_points");
    }
    std::vector<glim::EstimationFrame::ConstPtr> marginalized;
    auto frame = odometry->insert_frame(prepared, marginalized);
    auto end = std::chrono::steady_clock::now();
    statistics[0] = std::chrono::duration<double, std::milli>(after_preprocess - start).count();
    statistics[1] = std::chrono::duration<double, std::milli>(end - after_preprocess).count();
    statistics[2] = prepared->size();
    statistics[3] = marginalized.size();
    last_frame = stamp;
    if (!frame) {
      return 0;  // Initializer is still collecting data, not a fabricated pose.
    }
    if (!frame->T_world_lidar.matrix().allFinite()) {
      throw std::runtime_error("nonfinite_estimated_pose");
    }
    Eigen::Map<Eigen::Matrix<double, 4, 4, Eigen::RowMajor>> destination(pose_row_major);
    destination = frame->T_world_lidar.matrix();
    return 1;
  });
}

extern "C" int lingxi_close() {
  return guarded([]() {
    int remaining = odometry ? static_cast<int>(odometry->get_remaining_frames().size()) : 0;
    odometry.reset();
    preprocessor.reset();
    return remaining;
  });
}
