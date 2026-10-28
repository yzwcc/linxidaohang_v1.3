// Offline diagnostic extension. No ROS, network, or motion interfaces.
#include <cmath>
#include <filesystem>
#include <fstream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include <gtsam/nonlinear/NonlinearFactorGraph.h>
#include <gtsam/nonlinear/Values.h>
#include <gtsam_points/types/point_cloud.hpp>
#include <glim/mapping/callbacks.hpp>
#include <glim/mapping/global_mapping.hpp>
#include <glim/mapping/global_mapping_pose_graph.hpp>
#include <glim/mapping/sub_mapping_passthrough.hpp>
#include <glim/odometry/callbacks.hpp>
#include <glim/util/config.hpp>

namespace {
std::unique_ptr<glim::SubMappingPassthrough> local;
std::unique_ptr<glim::GlobalMappingPoseGraph> global;
std::vector<glim::SubMap::Ptr> submaps;
std::string error;
int callback_id = -1;
int last_frame_id = -1;
double last_stamp = -1;
size_t frame_count = 0;
bool used = false;
bool finished = false;
double min_loop_travel = 0;
double travel = 0;

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
    error = "unknown_mapping_exception";
    return -1;
  }
}

void accept(const glim::SubMap::Ptr& submap) {
  if (!submap || !submap->frame || submap->frame->size() == 0
      || !submap->frame->has_covs() || submap->frames.empty()
      || submap->id != static_cast<int>(submaps.size())
      || !submap->T_world_origin.matrix().allFinite()) {
    throw std::runtime_error("nonempty_ordered_submap_with_covariances_required");
  }
  if (!submaps.empty()) {
    travel += (submap->T_world_origin.translation() - submaps.back()->T_world_origin.translation()).norm();
  }
  // This short diagnostic must not race an unobserved asynchronous loop search.
  if (travel >= min_loop_travel) {
    throw std::runtime_error("short_sequence_persistence_trial_exceeded_loop_travel_boundary");
  }
  global->insert_submap(submap);
  submaps.push_back(submap);
}

void write_point(std::ofstream& output, const Eigen::Vector4d& point) {
  if (!point.allFinite() || std::abs(point.w() - 1.0) > 1e-3) {
    throw std::runtime_error("nonfinite_or_nonhomogeneous_map_point");
  }
  output.write(reinterpret_cast<const char*>(point.data()), 3 * sizeof(double));
}

std::ofstream new_binary(const char* path) {
  if (!path || std::filesystem::exists(path)) {
    throw std::runtime_error("new_private_point_output_required");
  }
  std::ofstream output(path, std::ios::binary);
  output.exceptions(std::ios::failbit | std::ios::badbit);
  return output;
}
}  // namespace

extern "C" const char* lingxi_mapping_error() {
  return error.c_str();
}

extern "C" int lingxi_mapping_open() {
  return guarded([] {
    if (used) {
      throw std::runtime_error("one_mapping_trial_per_process_required");
    }
    used = true;
    local = std::make_unique<glim::SubMappingPassthrough>();
    glim::GlobalMappingPoseGraphParams params;
    min_loop_travel = params.min_travel_dist;
    if (params.registration_type != "VGICP" || min_loop_travel <= 0) {
      throw std::runtime_error("reviewed_cpu_pose_graph_configuration_required");
    }
    global = std::make_unique<glim::GlobalMappingPoseGraph>(params);
    callback_id = glim::OdometryEstimationCallbacks::on_marginalized_frames.add(
      [](const std::vector<glim::EstimationFrame::ConstPtr>& frames) {
        for (const auto& frame : frames) {
          if (!frame || frame->id <= last_frame_id || !std::isfinite(frame->stamp)
              || frame->stamp <= last_stamp) {
            throw std::runtime_error("marginalized_frames_must_be_fed_exactly_once_in_order");
          }
          local->insert_frame(frame);
          last_frame_id = frame->id;
          last_stamp = frame->stamp;
          ++frame_count;
          for (const auto& submap : local->get_submaps()) {
            accept(submap);
          }
        }
      });
  });
}

extern "C" int lingxi_mapping_finish(const char* map_path, const char* points_path, double* stats) {
  return guarded([&] {
    if (!local || !global || finished || !map_path || !stats
        || std::filesystem::exists(map_path)) {
      throw std::runtime_error("one_flush_and_new_private_map_directory_required");
    }
    finished = true;
    // Odometry close has already emitted its remaining-frame callback.
    // Never feed get_remaining_frames() a second time here.
    for (const auto& submap : local->submit_end_of_sequence()) {
      accept(submap);
    }
    if (submaps.empty()) {
      throw std::runtime_error("empty_map_is_not_a_success");
    }
    global->save(map_path);
    size_t points = 0;
    size_t saved_frames = 0;
    auto output = new_binary(points_path);
    for (const auto& submap : submaps) {
      saved_frames += submap->frames.size();
      for (size_t i = 0; i < submap->frame->size(); ++i) {
        write_point(output, submap->T_world_origin * submap->frame->points[i]);
        ++points;
      }
    }
    output.close();
    if (saved_frames != frame_count || points == 0) {
      throw std::runtime_error("every_finalized_frame_must_appear_in_saved_submaps");
    }
    stats[0] = frame_count;
    stats[1] = submaps.size();
    stats[2] = points;
    stats[3] = saved_frames;
    stats[4] = min_loop_travel;
    stats[5] = travel;
  });
}

extern "C" int lingxi_mapping_shutdown() {
  return guarded([] {
    if (callback_id >= 0) {
      glim::OdometryEstimationCallbacks::on_marginalized_frames.remove(callback_id);
      callback_id = -1;
    }
    global.reset();  // Joins the upstream loop-detection thread.
    local.reset();
    submaps.clear();
  });
}

extern "C" int lingxi_mapping_reload(
    const char* configuration, const char* map_path, const char* points_path, double* stats) {
  return guarded([&] {
    if (used || !configuration || !map_path || !stats) {
      throw std::runtime_error("fresh_process_reload_required");
    }
    used = true;
    glim::GlobalConfig::instance(configuration);
    glim::GlobalMappingParams params;
    params.enable_gpu = false;
    params.enable_imu = false;  // Loading a pose-only graph, not replaying IMU.
    params.registration_error_factor_type = "VGICP";
    glim::GlobalMapping loader(params);
    size_t factors = 0;
    size_t values = 0;
    size_t loaded_submaps = 0;
    const int graph_callback = glim::GlobalMappingCallbacks::on_smoother_update.add(
      [&](auto&, auto& graph, auto& initial) {
        factors = graph.size();
        values = initial.size();
      });
    const int submap_callback = glim::GlobalMappingCallbacks::on_insert_submap.add(
      [&](const auto& submap) {
        if (!submap || !submap->T_world_origin.matrix().allFinite()) {
          throw std::runtime_error("invalid_reloaded_submap");
        }
        ++loaded_submaps;
      });
    try {
      if (!loader.load(map_path)) {
        throw std::runtime_error("official_global_map_load_failed");
      }
      // No loop factors are eligible in this deliberately short trial.
      if (loaded_submaps == 0 || factors != loaded_submaps || values != loaded_submaps) {
        throw std::runtime_error("pose_graph_structure_did_not_round_trip");
      }
      auto cloud = loader.export_points();
      if (!cloud || cloud->size() == 0) {
        throw std::runtime_error("official_export_returned_empty_map");
      }
      auto output = new_binary(points_path);
      for (size_t i = 0; i < cloud->size(); ++i) {
        write_point(output, cloud->points[i]);
      }
      output.close();
      stats[0] = loaded_submaps;
      stats[1] = cloud->size();
      stats[2] = factors;
      stats[3] = values;
    } catch (...) {
      glim::GlobalMappingCallbacks::on_smoother_update.remove(graph_callback);
      glim::GlobalMappingCallbacks::on_insert_submap.remove(submap_callback);
      throw;
    }
    glim::GlobalMappingCallbacks::on_smoother_update.remove(graph_callback);
    glim::GlobalMappingCallbacks::on_insert_submap.remove(submap_callback);
  });
}
