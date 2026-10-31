#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <memory>
#include "nav2_navfn_planner/navfn.hpp"

extern "C" int lingxi_navfn_abi_version() { return 1; }
extern "C" const char* lingxi_navfn_commit() { return LINGXI_NAVFN_COMMIT; }

// Each call owns a fresh upstream planner. No ROS node, motion or global state.
extern "C" int lingxi_navfn_plan(const std::uint8_t* costs, std::size_t cells,
    int width, int height, int sx, int sy, int gx, int gy,
    float* path_xy, int capacity) {
  if (!costs || !path_xy || width < 8 || height < 8 || width > 2048 || height > 2048 ||
      cells != static_cast<std::size_t>(width) * height || cells > 1048576 ||
      capacity < 2 || capacity > 65536 || sx < 1 || sy < 1 || gx < 1 || gy < 1 ||
      sx >= width - 1 || gx >= width - 1 || sy >= height - 1 || gy >= height - 1) {
    return -1;
  }
  if (costs[sy * width + sx] >= 253 || costs[gy * width + gx] >= 253) return 0;
  try {
    if (sx == gx && sy == gy) {
      path_xy[0] = sx;
      path_xy[1] = sy;
      return 1;
    }
    nav2_navfn_planner::NavFn planner(width, height);
    int start[] = {sx, sy}, goal[] = {gx, gy};
    planner.setCostmap(costs, true, false);
    planner.setStart(start);
    planner.setGoal(goal);
    planner.setupNavFn(true);
    // An exhausted propagation/path budget is a failure, never a partial plan.
    if (!planner.propNavFnDijkstra(static_cast<int>(cells), true)) return 0;
    const int count = planner.calcPath(capacity);
    if (count < 1 || count > capacity) return 0;
    const float* x = planner.getPathX();
    const float* y = planner.getPathY();
    for (int i = 0; i < count; ++i) {
      if (!std::isfinite(x[i]) || !std::isfinite(y[i]) || x[i] < 1 || y[i] < 1 ||
          x[i] >= width - 1 || y[i] >= height - 1 ||
          costs[static_cast<int>(std::floor(y[i] + .5f)) * width +
                static_cast<int>(std::floor(x[i] + .5f))] >= 253) return -3;
    }
    if (x[count - 1] != gx || y[count - 1] != gy) return -3;
    for (int i = 0; i < count; ++i) {
      path_xy[i * 2] = x[i];
      path_xy[i * 2 + 1] = y[i];
    }
    return count;
  } catch (...) {
    return -2;
  }
}
