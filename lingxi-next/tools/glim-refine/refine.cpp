// Offline saved-map processing through the pinned upstream CPU mapper. No ROS.
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <sys/resource.h>
#include <unistd.h>

#include <gtsam_points/types/point_cloud.hpp>
#include <glim/mapping/callbacks.hpp>
#include <glim/mapping/global_mapping.hpp>
#include <glim/util/config.hpp>

namespace fs = std::filesystem;

bool within(const fs::path& child, const fs::path& parent) {
  const auto relative = child.lexically_relative(parent);
  return relative.empty() || *relative.begin() != "..";
}

int main(int argc, char** argv) {
  try {
    if (argc != 8 || (std::string(argv[1]) != "refine" && std::string(argv[1]) != "reload")) {
      throw std::runtime_error("mode_config_input_output_points_report_overlap_required");
    }
    const bool refine = std::string(argv[1]) == "refine";
    const fs::path input = fs::canonical(argv[3]);
    const fs::path output = fs::absolute(argv[4]).lexically_normal();
    const fs::path points = fs::absolute(argv[5]).lexically_normal();
    const fs::path report = fs::absolute(argv[6]).lexically_normal();
    size_t parsed = 0;
    const std::string overlap_text(argv[7]);
    const double overlap = std::stod(overlap_text, &parsed);
    if (parsed != overlap_text.size() || !std::isfinite(overlap) || overlap <= 0 || overlap > 1 ||
        fs::exists(points) || fs::exists(report) || points == report ||
        within(points, input) || within(report, input) ||
        (refine && (fs::exists(output) || within(output, input) || within(input, output)))) {
      throw std::runtime_error("new_separate_outputs_and_explicit_overlap_required");
    }
    glim::GlobalConfig::instance(argv[2]);
    glim::GlobalMappingParams params;
    params.enable_gpu = false;
    params.enable_imu = false;  // The saved source graph is pose-only; do not invent IMU samples.
    params.enable_optimization = refine;
    params.registration_error_factor_type = "VGICP";
    params.randomsampling_rate = 1.0;
    glim::GlobalMapping mapper(params);
    size_t loaded = 0;
    const int callback = glim::GlobalMappingCallbacks::on_insert_submap.add([&](const auto&) { ++loaded; });
    const auto started = std::chrono::steady_clock::now();
    const bool success = mapper.load(input.string());
    glim::GlobalMappingCallbacks::on_insert_submap.remove(callback);
    if (!success || loaded == 0) {
      throw std::runtime_error("nonempty_official_map_load_required");
    }
    if (refine) {
      mapper.find_overlapping_submaps(overlap);
      mapper.save(output.string());
    }
    const auto cloud = mapper.export_points();
    if (!cloud || cloud->size() == 0) {
      throw std::runtime_error("nonempty_official_point_export_required");
    }
    std::ofstream binary(points, std::ios::binary);
    binary.exceptions(std::ios::failbit | std::ios::badbit);
    for (size_t i = 0; i < cloud->size(); ++i) {
      if (!cloud->points[i].allFinite() || std::abs(cloud->points[i].w() - 1) > 1e-3) {
        throw std::runtime_error("finite_homogeneous_export_points_required");
      }
      binary.write(reinterpret_cast<const char*>(cloud->points[i].data()), 3 * sizeof(double));
    }
    binary.close();
    const auto seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
    struct rusage usage {};
    if (getrusage(RUSAGE_SELF, &usage) != 0 || usage.ru_maxrss <= 0) {
      throw std::runtime_error("process_memory_accounting_required");
    }
    std::ofstream json(report);
    json.exceptions(std::ios::failbit | std::ios::badbit);
    json << "{\"pid\":" << getpid() << ",\"loadedSubmaps\":" << loaded
         << ",\"points\":" << cloud->size() << ",\"refined\":" << (refine ? "true" : "false")
         << ",\"wallSeconds\":" << seconds << ",\"peakRssKiB\":" << usage.ru_maxrss << "}\n";
    json.close();
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
