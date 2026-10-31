// Observed returns only. OctoMap owns ray traversal and probabilistic updates.
#include <octomap/octomap.h>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
thread_local std::string error;
constexpr size_t maximum_rays = 262144, maximum_nodes = 2000000;
void require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
bool aligned(double value, double resolution) {
  return std::isfinite(value) && std::abs(value) <= 500 &&
    std::abs(value / resolution - std::round(value / resolution)) < 1e-7;
}
}  // namespace

extern "C" int lingxi_octomap_abi_version() { return 1; }
extern "C" const char* lingxi_octomap_version() { return LINGXI_OCTOMAP_VERSION; }
extern "C" const char* lingxi_octomap_error() { return error.c_str(); }

extern "C" int lingxi_octomap_project(
    const double* rays, size_t count, const uint32_t* scan_ends, size_t scans,
    double resolution, double maximum_range, double origin_x, double origin_y,
    double minimum_z, double maximum_z, int width, int height, uint64_t work_limit,
    uint8_t* output, size_t capacity, uint64_t* stats) {
  try {
    error.clear();
    require(rays && scan_ends && output && stats && count && count <= maximum_rays &&
      scans && scans <= 4096, "bounded_complete_observed_rays_required");
    require(std::isfinite(resolution) && resolution >= .025 && resolution <= 1 &&
      std::isfinite(maximum_range) && maximum_range >= .1 && maximum_range <= 30 &&
      aligned(origin_x, resolution) && aligned(origin_y, resolution) &&
      aligned(minimum_z, resolution) && aligned(maximum_z, resolution) && maximum_z > minimum_z &&
      width >= 8 && width <= 1024 && height >= 8 && height <= 1024 &&
      capacity == size_t(width) * height && capacity <= 1048576 &&
      work_limit >= 1 && work_limit <= 16000000, "bounded_aligned_metric_projection_required");
    const size_t levels = std::llround((maximum_z - minimum_z) / resolution);
    require(levels >= 1 && levels <= 128 && capacity * levels <= 4000000,
      "bounded_clearance_column_required");
    uint64_t work = capacity * levels;
    require(work <= work_limit, "octomap_work_budget_exhausted");
    size_t previous = 0;
    for (size_t scan = 0; scan < scans; ++scan) {
      require(scan_ends[scan] > previous && scan_ends[scan] <= count &&
        scan_ends[scan] - previous <= 8192, "ordered_complete_scan_boundaries_required");
      previous = scan_ends[scan];
    }
    require(previous == count, "all_observed_rays_accounted_required");
    for (size_t i = 0; i < count; ++i) {
      double squared = 0;
      for (size_t j = 0; j < 6; ++j)
        require(std::isfinite(rays[i * 6 + j]) && std::abs(rays[i * 6 + j]) <= 500,
          "finite_bounded_ray_coordinates_required");
      for (size_t j = 0; j < 3; ++j) squared += std::pow(rays[i * 6 + j + 3] - rays[i * 6 + j], 2);
      require(squared > 1e-10 && squared <= 40000, "finite_nonzero_lidar_return_required");
      // Bound native ray work before calling the upstream traversal.
      work += uint64_t(std::ceil(std::sqrt(3.) * std::min(std::sqrt(squared), maximum_range) / resolution)) + 4;
      require(work <= work_limit, "octomap_work_budget_exhausted");
    }
    octomap::OcTree tree(resolution);
    tree.setProbHit(.7);
    tree.setProbMiss(.4);
    tree.setClampingThresMin(.1192);
    tree.setClampingThresMax(.971);
    tree.setOccupancyThres(.5);
    size_t start = 0;
    for (size_t scan = 0; scan < scans; ++scan) {
      octomap::KeySet free_cells, occupied_cells;
      for (size_t i = start; i < scan_ends[scan]; ++i) {
        const double* ray = rays + i * 6;
        octomap::point3d origin(ray[0], ray[1], ray[2]);
        octomap::Pointcloud point;
        point.push_back(ray[3], ray[4], ray[5]);
        octomap::KeySet ray_free, ray_occupied;
        tree.computeUpdate(point, origin, ray_free, ray_occupied, maximum_range);
        free_cells.insert(ray_free.begin(), ray_free.end());
        occupied_cells.insert(ray_occupied.begin(), ray_occupied.end());
        require(free_cells.size() + occupied_cells.size() <= 524288,
          "octomap_scan_cell_budget_exhausted");
      }
      // A hit anywhere in this scan must win over another beam's free traversal.
      for (const auto& key : free_cells) {
        if (occupied_cells.count(key)) continue;
        tree.updateNode(key, false, true);
        require(tree.size() <= maximum_nodes, "octomap_node_budget_exhausted");
      }
      for (const auto& key : occupied_cells) {
        tree.updateNode(key, true, true);
        require(tree.size() <= maximum_nodes, "octomap_node_budget_exhausted");
      }
      start = scan_ends[scan];
    }
    tree.updateInnerOccupancy();
    std::vector<uint8_t> projected(capacity, 255);
    uint64_t free_count = 0, occupied_count = 0, unknown_count = 0;
    for (int y = 0; y < height; ++y) {
      for (int x = 0; x < width; ++x) {
        bool unknown = false, occupied = false;
        for (size_t z = 0; z < levels; ++z) {
          const auto* node = tree.search(origin_x + (x + .5) * resolution,
            origin_y + (y + .5) * resolution, minimum_z + (z + .5) * resolution);
          if (!node) unknown = true;
          else if (tree.isNodeOccupied(node)) { occupied = true; break; }
        }
        const uint8_t cost = occupied ? 254 : unknown ? 255 : 0;
        projected[size_t(y) * width + x] = cost;
        free_count += cost == 0;
        occupied_count += cost == 254;
        unknown_count += cost == 255;
      }
    }
    const uint64_t values[] = {count, scans, tree.size(), free_count, occupied_count, unknown_count, work};
    // No caller-visible partial grid or counters on any failure.
    std::copy(projected.begin(), projected.end(), output);
    std::copy(std::begin(values), std::end(values), stats);
    return 0;
  } catch (const std::exception& exception) { error = exception.what(); }
  catch (...) { error = "unknown_octomap_projection_exception"; }
  return -1;
}
