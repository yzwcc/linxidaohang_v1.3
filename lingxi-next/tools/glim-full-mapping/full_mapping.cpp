// Independent CPU local/global mapping adapter. No ROS, network, or motion API.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include <gtsam/inference/Symbol.h>
#include <gtsam_points/factors/integrated_vgicp_factor.hpp>
#include <gtsam_points/optimizers/isam2_ext.hpp>
#include <gtsam_points/types/point_cloud.hpp>
#include <glim/mapping/callbacks.hpp>
#include <glim/mapping/global_mapping.hpp>
#include <glim/mapping/sub_mapping.hpp>
#include <glim/odometry/callbacks.hpp>
#include <glim/util/config.hpp>

namespace {
std::unique_ptr<glim::SubMapping> local;
std::unique_ptr<glim::GlobalMapping> global;
std::vector<glim::SubMap::Ptr> saved_submaps;
std::vector<int> finalized_ids;
std::string error;
bool used = false, finished = false;
int frame_callback = -1, local_callback = -1, graph_callback = -1, update_callback = -1;
double last_frame_stamp = -1, last_imu_stamp = -1, maximum_insert_ms = 0;
size_t imu_count = 0, local_optimizations = 0, matching = 0, nonadjacent = 0;
struct Event { size_t submaps, matching, nonadjacent; };
std::vector<Event> events;

template <typename Function>
int guarded(Function function) {
  try {
    error.clear();
    function();
    return 0;
  } catch (const std::exception& exception) {
    error = exception.what();
    return -1;
  } catch (...) {
    error = "unknown_full_mapping_exception";
    return -1;
  }
}

void accept(const glim::SubMap::Ptr& submap) {
  if (!submap || !submap->frame || !submap->frame->size() || submap->frames.empty()
      || submap->id != static_cast<int>(saved_submaps.size()) || !submap->frame->has_covs()) {
    throw std::runtime_error("ordered_complete_cpu_submap_required");
  }
  const auto start = std::chrono::steady_clock::now();
  global->insert_submap(submap);
  maximum_insert_ms = std::max(maximum_insert_ms,
    std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count());
  saved_submaps.push_back(submap);
}

size_t export_points(const char* path, const gtsam_points::PointCloud::Ptr& cloud) {
  if (!path || std::filesystem::exists(path) || !cloud || !cloud->size()) {
    throw std::runtime_error("new_nonempty_point_export_required");
  }
  std::ofstream stream(path, std::ios::binary);
  stream.exceptions(std::ios::failbit | std::ios::badbit);
  for (size_t i = 0; i < cloud->size(); ++i) {
    if (!cloud->points[i].allFinite() || std::abs(cloud->points[i].w() - 1) > 1e-3) {
      throw std::runtime_error("finite_homogeneous_point_export_required");
    }
    stream.write(reinterpret_cast<const char*>(cloud->points[i].data()), 3 * sizeof(double));
  }
  stream.close();
  return cloud->size();
}
}  // namespace

extern "C" const char* lingxi_full_error() { return error.c_str(); }

extern "C" int lingxi_full_open() {
  return guarded([] {
    if (used) throw std::runtime_error("one_full_mapping_session_per_process_required");
    used = true;
    glim::SubMappingParams sub;
    glim::GlobalMappingParams world;
    if (!sub.enable_imu || !sub.enable_optimization || sub.enable_gpu ||
        !world.enable_imu || !world.enable_optimization || world.enable_gpu ||
        sub.registration_error_factor_type != "VGICP" || world.registration_error_factor_type != "VGICP" ||
        world.randomsampling_rate != 1.0) {
      throw std::runtime_error("explicit_cpu_local_global_imu_optimization_required");
    }
    local = std::make_unique<glim::SubMapping>(sub);
    global = std::make_unique<glim::GlobalMapping>(world);
    local_callback = glim::SubMappingCallbacks::on_optimize_submap.add([](auto&, auto&) { ++local_optimizations; });
    graph_callback = glim::GlobalMappingCallbacks::on_smoother_update_result.add(
      [](auto& smoother, const auto&) {
        matching = nonadjacent = 0;
        for (const auto& factor : smoother.getFactorsUnsafe()) {
          if (!factor || !dynamic_cast<const gtsam_points::IntegratedVGICPFactor*>(factor.get())) continue;
          if (factor->keys().size() != 2) throw std::runtime_error("binary_matching_factor_required");
          const gtsam::Symbol a(factor->keys()[0]), b(factor->keys()[1]);
          if (a.chr() != 'x' || b.chr() != 'x' || a.index() >= b.index()) {
            throw std::runtime_error("ordered_global_submap_matching_keys_required");
          }
          ++matching;
          if (b.index() > a.index() + 1) ++nonadjacent;
        }
      });
    update_callback = glim::GlobalMappingCallbacks::on_update_submaps.add([](const auto& submaps) {
      if (submaps.empty()) throw std::runtime_error("nonempty_updated_submaps_required");
      for (const auto& map : submaps) {
        if (!map || !map->T_world_origin.matrix().allFinite()) throw std::runtime_error("finite_updated_pose_required");
      }
      events.push_back({submaps.size(), matching, nonadjacent});
    });
    frame_callback = glim::OdometryEstimationCallbacks::on_marginalized_frames.add([](const auto& frames) {
      for (const auto& frame : frames) {
        if (!frame || (!finalized_ids.empty() && frame->id <= finalized_ids.back()) ||
            !std::isfinite(frame->stamp) || frame->stamp <= last_frame_stamp) {
          throw std::runtime_error("finalized_frames_exactly_once_in_order_required");
        }
        finalized_ids.push_back(frame->id);
        last_frame_stamp = frame->stamp;
        local->insert_frame(frame);
        for (const auto& map : local->get_submaps()) accept(map);
      }
    });
  });
}

extern "C" int lingxi_full_imu(double stamp, const double* acceleration, const double* angular) {
  return guarded([&] {
    if (!local || !global || finished || !acceleration || !angular || !std::isfinite(stamp) || stamp <= last_imu_stamp) {
      throw std::runtime_error("ordered_full_mapping_imu_required");
    }
    const Eigen::Map<const Eigen::Vector3d> acc(acceleration), gyro(angular);
    if (!acc.allFinite() || !gyro.allFinite()) throw std::runtime_error("finite_full_mapping_imu_required");
    local->insert_imu(stamp, acc, gyro);
    global->insert_imu(stamp, acc, gyro);
    last_imu_stamp = stamp;
    ++imu_count;
  });
}

extern "C" int lingxi_full_finish(const char* map_path, const char* points_path, const char* events_path, double* stats) {
  return guarded([&] {
    if (!local || !global || finished || !map_path || !events_path || !stats ||
        std::filesystem::exists(map_path) || std::filesystem::exists(events_path)) {
      throw std::runtime_error("one_complete_full_mapping_finish_required");
    }
    finished = true;
    for (const auto& map : local->submit_end_of_sequence()) accept(map);
    if (saved_submaps.empty()) throw std::runtime_error("nonempty_full_map_required");
    std::vector<int> saved_ids;
    for (const auto& map : saved_submaps) {
      for (const auto& frame : map->frames) saved_ids.push_back(frame->id);
    }
    if (saved_ids != finalized_ids) throw std::runtime_error("all_finalized_frames_including_terminal_frame_required");
    global->optimize();
    global->save(map_path);
    const size_t points = export_points(points_path, global->export_points());
    std::ofstream trace(events_path);
    trace.exceptions(std::ios::failbit | std::ios::badbit);
    for (const auto& event : events) {
      trace << "{\"submaps\":" << event.submaps << ",\"matchingFactors\":" << event.matching
            << ",\"nonAdjacentMatchingFactors\":" << event.nonadjacent << "}\n";
    }
    trace.close();
    const double counters[] = {static_cast<double>(finalized_ids.size()), static_cast<double>(saved_submaps.size()),
      static_cast<double>(points), static_cast<double>(saved_ids.size()), static_cast<double>(imu_count),
      static_cast<double>(events.size()), static_cast<double>(matching), static_cast<double>(nonadjacent),
      static_cast<double>(local_optimizations), maximum_insert_ms};
    std::copy(std::begin(counters), std::end(counters), stats);
  });
}

extern "C" int lingxi_full_shutdown() {
  return guarded([] {
    if (frame_callback >= 0) glim::OdometryEstimationCallbacks::on_marginalized_frames.remove(frame_callback);
    if (local_callback >= 0) glim::SubMappingCallbacks::on_optimize_submap.remove(local_callback);
    if (graph_callback >= 0) glim::GlobalMappingCallbacks::on_smoother_update_result.remove(graph_callback);
    if (update_callback >= 0) glim::GlobalMappingCallbacks::on_update_submaps.remove(update_callback);
    frame_callback = local_callback = graph_callback = update_callback = -1;
    global.reset();
    local.reset();
    saved_submaps.clear();
  });
}

extern "C" int lingxi_full_reload(const char* config, const char* map, const char* points, double* stats) {
  return guarded([&] {
    if (used || !config || !map || !stats) throw std::runtime_error("fresh_full_map_reload_required");
    used = true;
    glim::GlobalConfig::instance(config);
    glim::GlobalMappingParams params;
    params.enable_gpu = false;
    params.enable_optimization = false;
    params.registration_error_factor_type = "VGICP";
    params.randomsampling_rate = 1.0;
    glim::GlobalMapping reader(params);
    size_t submaps = 0, frames = 0;
    const int callback = glim::GlobalMappingCallbacks::on_insert_submap.add([&](const auto& submap) {
      ++submaps;
      frames += submap->frames.size();
    });
    const bool loaded = reader.load(map);
    glim::GlobalMappingCallbacks::on_insert_submap.remove(callback);
    if (!loaded || !submaps) throw std::runtime_error("complete_full_map_reload_required");
    stats[0] = submaps;
    stats[1] = export_points(points, reader.export_points());
    stats[2] = frames;
  });
}
