#include <algorithm>
#include <cmath>
#include <cstdint>
#include <exception>
#include <iostream>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include "patchwork/patchworkpp.h"

namespace {
thread_local std::string last_error;
std::mutex estimator_mutex;
struct QuietConstructor {
  std::ostringstream sink;
  std::streambuf* previous = std::cout.rdbuf(sink.rdbuf());
  ~QuietConstructor() { std::cout.rdbuf(previous); }
};
}

extern "C" {
int lingxi_ground_abi() { return 1; }
const char* lingxi_ground_revision() { return LINGXI_PATCHWORKPP_REVISION; }
const char* lingxi_ground_error() { return last_error.c_str(); }

// Stateless per scan: adaptive history never leaks across maps, resets or callers.
int lingxi_ground_segment(const double* xyz, std::size_t count, double height,
    double minimum_range, double maximum_range, std::uint8_t* labels,
    std::size_t capacity, std::uint64_t* statistics) {
  try {
    if (!xyz || !labels || !statistics || count < 3 || count > 8192 || capacity != count ||
        !std::isfinite(height) || height < .2 || height > 3. ||
        !std::isfinite(minimum_range) || minimum_range < .1 || minimum_range > 5. ||
        !std::isfinite(maximum_range) || maximum_range < 2. || maximum_range > 30. ||
        maximum_range - minimum_range < 1.)
      throw std::invalid_argument("bounded_explicit_ground_input_required");
    Eigen::MatrixXf cloud(count, 3);
    for (std::size_t i = 0; i < count; ++i) {
      for (int axis = 0; axis < 3; ++axis) {
        const double value = xyz[i * 3 + axis];
        if (!std::isfinite(value) || std::abs(value) > 100.)
          throw std::invalid_argument("finite_bounded_ground_coordinates_required");
        cloud(i, axis) = static_cast<float>(value);
      }
    }
    patchwork::Params parameters;
    parameters.sensor_height = height;
    parameters.min_range = minimum_range;
    parameters.max_range = maximum_range;
    parameters.enable_RNR = false;  // No fabricated intensity for XYZ-only scans.
    parameters.enable_TGR = false;  // No temporal reversion across unrelated scans.
    parameters.th_seeds = .08;
    parameters.th_dist = .05;
    parameters.uprightness_thr = std::cos(15. * std::acos(-1.) / 180.);
    // Upstream writes to process-global cout; serialize across C ABI callers.
    std::lock_guard<std::mutex> lock(estimator_mutex);
    QuietConstructor quiet;
    patchwork::PatchWorkpp estimator(parameters);
    estimator.estimateGround(cloud);
    std::vector<std::uint8_t> result(count, 255);
    const auto ground = estimator.getGroundIndices();
    const auto other = estimator.getNongroundIndices();
    auto assign = [&](const Eigen::VectorXi& indices, std::uint8_t label) {
      for (Eigen::Index i = 0; i < indices.size(); ++i) {
        const auto index = indices[i];
        if (index < 0 || static_cast<std::size_t>(index) >= count || result[index] != 255)
          throw std::runtime_error("complete_unique_patchwork_indices_required");
        result[index] = label;
      }
    };
    assign(ground, 0);
    assign(other, 254);
    // Commit only a complete successful result; callers retain no partial success.
    std::copy(result.begin(), result.end(), labels);
    statistics[0] = ground.size();
    statistics[1] = other.size();
    statistics[2] = count - ground.size() - other.size();
    last_error.clear();
    return 0;
  } catch (const std::exception& error) {
    last_error = error.what();
    return -1;
  } catch (...) {
    last_error = "patchwork_unknown_failure";
    return -1;
  }
}
}
