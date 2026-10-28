// Recorded-input diagnostic only. No odometry, mapping, ROS, network, or motion.
#include <cmath>
#include <memory>
#include <stdexcept>
#include <string>

#include <gtsam_points/util/parallelism.hpp>
#include <glim/preprocess/cloud_preprocessor.hpp>
#include <glim/util/config.hpp>

namespace {
std::unique_ptr<glim::CloudPreprocessor> preprocessor;
std::string error;
bool used = false;
double last_stamp = -1;
constexpr int neighbors_per_point = 10;

template <typename Function>
int guarded(Function function) {
  try {
    error.clear();
    return function();
  } catch (const std::exception& exception) {
    error = exception.what();
    return -1;
  } catch (...) {
    error = "unknown_preprocessing_exception";
    return -1;
  }
}
}  // namespace

extern "C" const char* lingxi_preprocess_error() {
  return error.c_str();
}

extern "C" int lingxi_preprocess_open(const char* config, int diagnostic_threads) {
  return guarded([&] {
    if (used || !config || (diagnostic_threads != 1 && diagnostic_threads != 2)) {
      throw std::runtime_error("fresh_process_and_predeclared_thread_condition_required");
    }
    used = true;
    glim::GlobalConfig::instance(config);
    glim::CloudPreprocessorParams params;
    if (!params.use_random_grid_downsampling || params.num_threads != 2
        || params.downsample_target != 10000 || params.downsample_resolution != 1.0
        || params.distance_near_thresh != 0.5 || params.distance_far_thresh != 100.0
        || params.enable_outlier_removal || params.enable_cropbox_filter
        || params.global_shutter || params.k_correspondences != neighbors_per_point
        || !gtsam_points::is_omp_default()) {
      throw std::runtime_error("reviewed_unchanged_preprocess_configuration_required");
    }
    // Explicit single-thread observation is a separate diagnostic condition.
    params.num_threads = diagnostic_threads;
    preprocessor = std::make_unique<glim::CloudPreprocessor>(params);
    return 0;
  });
}

extern "C" int lingxi_preprocess_frame(
    double stamp, int count, const double* xyz, const double* offsets,
    double* xyzt, int* neighbors, int capacity) {
  return guarded([&] {
    if (!preprocessor || !xyz || !offsets || !xyzt || !neighbors || count < 10
        || count > 2000000 || capacity < count || !std::isfinite(stamp)
        || stamp <= last_stamp) {
      throw std::runtime_error("ordered_complete_recorded_preprocess_input_required");
    }
    auto raw = std::make_shared<glim::RawPoints>();
    raw->stamp = stamp;
    raw->points.reserve(count);
    raw->times.reserve(count);
    for (int index = 0; index < count; ++index) {
      Eigen::Vector4d point(xyz[index * 3], xyz[index * 3 + 1], xyz[index * 3 + 2], 1);
      if (!point.allFinite() || !std::isfinite(offsets[index])
          || offsets[index] < 0 || offsets[index] > 0.2) {
        throw std::runtime_error("finite_recorded_points_and_second_offsets_required");
      }
      raw->points.push_back(point);
      raw->times.push_back(offsets[index]);
    }
    const auto prepared = preprocessor->preprocess(raw);
    const auto size = prepared->size();
    if (size < 50 || size > static_cast<size_t>(capacity) || prepared->times.size() != size
        || prepared->neighbors.size() != size * neighbors_per_point) {
      throw std::runtime_error("complete_nonempty_prepared_cloud_required");
    }
    for (size_t index = 0; index < size; ++index) {
      const auto& point = prepared->points[index];
      if (!point.allFinite() || std::abs(point.w() - 1) > 1e-12
          || !std::isfinite(prepared->times[index])) {
        throw std::runtime_error("finite_homogeneous_prepared_points_required");
      }
      for (int axis = 0; axis < 3; ++axis) {
        xyzt[index * 4 + axis] = point[axis];
      }
      xyzt[index * 4 + 3] = prepared->times[index];
    }
    for (size_t index = 0; index < prepared->neighbors.size(); ++index) {
      const int neighbor = prepared->neighbors[index];
      if (neighbor < 0 || neighbor >= static_cast<int>(size)) {
        throw std::runtime_error("valid_prepared_neighbor_index_required");
      }
      neighbors[index] = neighbor;
    }
    last_stamp = stamp;
    return static_cast<int>(size);
  });
}

extern "C" int lingxi_preprocess_close() {
  return guarded([] {
    preprocessor.reset();
    return 0;
  });
}
