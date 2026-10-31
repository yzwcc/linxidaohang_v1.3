// Reuse the source-bound synchronous algorithm, executing all mapping on one worker.
#define lingxi_full_error lingxi_sync_error
#define lingxi_full_open lingxi_sync_open
#define lingxi_full_imu lingxi_sync_imu
#define lingxi_full_finish lingxi_sync_finish
#define lingxi_full_shutdown lingxi_sync_shutdown
#define lingxi_full_reload lingxi_sync_reload
#include "sync/full_mapping.cpp"
#undef lingxi_full_error
#undef lingxi_full_open
#undef lingxi_full_imu
#undef lingxi_full_finish
#undef lingxi_full_shutdown
#undef lingxi_full_reload

#include "bounded_worker.hpp"
#include <atomic>
#include <mutex>

namespace {
std::unique_ptr<lingxi::BoundedWorker> worker;
thread_local std::string async_error;
int snapshot_callback = -1;
struct FrozenSubmap {
  Eigen::Isometry3d pose;
  std::shared_ptr<const std::vector<Eigen::Vector4d>> points;
};
struct Snapshot {
  size_t revision = 0, points = 0, matching = 0, nonadjacent = 0, corrected = 0;
  std::vector<FrozenSubmap> submaps;
};
std::mutex snapshot_mutex;
std::shared_ptr<const Snapshot> latest;
std::atomic<double> maximum_enqueue_ms{0};
bool async_finishing = false;

template <typename Function> int async_guarded(Function function) {
  try { async_error.clear(); function(); return 0; }
  catch (const std::exception& exception) { async_error = exception.what(); return -1; }
  catch (...) { async_error = "unknown_async_mapping_exception"; return -1; }
}
void require_sync(int result) {
  if (result < 0) throw std::runtime_error(lingxi_sync_error());
}
void enqueue(lingxi::BoundedWorker::Counts cost, std::function<void()> function) {
  if (!worker) throw std::runtime_error("open_async_mapping_required");
  const auto start = std::chrono::steady_clock::now();
  worker->submit(cost, std::move(function));
  const double elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
  double maximum = maximum_enqueue_ms.load();
  while (maximum < elapsed && !maximum_enqueue_ms.compare_exchange_weak(maximum, elapsed)) {}
}
void publish(const std::vector<glim::SubMap::Ptr>& submaps) {
  auto next = std::make_shared<Snapshot>();
  next->revision = latest ? latest->revision + 1 : 1;
  next->matching = matching;
  next->nonadjacent = nonadjacent;
  next->corrected = latest ? latest->corrected : 0;
  for (size_t i = 0; i < submaps.size(); ++i) {
    const auto& map = submaps[i];
    if (!map || map->id != static_cast<int>(i) || !map->T_world_origin.matrix().allFinite()) {
      throw std::runtime_error("ordered_finite_corrected_snapshot_required");
    }
    std::shared_ptr<const std::vector<Eigen::Vector4d>> geometry;
    if (latest && i < latest->submaps.size()) {
      geometry = latest->submaps[i].points;
      if ((latest->submaps[i].pose.matrix() - map->T_world_origin.matrix()).cwiseAbs().maxCoeff() > 1e-12) {
        ++next->corrected;
      }
    } else {
      auto copy = std::make_shared<std::vector<Eigen::Vector4d>>();
      copy->reserve(map->frame->size());
      for (size_t p = 0; p < map->frame->size(); ++p) {
        if (!map->frame->points[p].allFinite()) throw std::runtime_error("finite_snapshot_points_required");
        copy->push_back(map->frame->points[p]);
      }
      geometry = std::move(copy);
    }
    if (geometry->size() != map->frame->size()) throw std::runtime_error("immutable_submap_geometry_required");
    next->points += geometry->size();
    next->submaps.push_back({map->T_world_origin, std::move(geometry)});
  }
  std::lock_guard<std::mutex> lock(snapshot_mutex);
  latest = std::move(next);
}
}  // namespace

extern "C" const char* lingxi_full_error() { return async_error.c_str(); }
extern "C" int lingxi_full_open() {
  return async_guarded([] {
    require_sync(lingxi_sync_open());
    // CallbackSlot is not thread-safe: register before producers/workers start.
    glim::OdometryEstimationCallbacks::on_marginalized_frames.remove(frame_callback);
    frame_callback = glim::OdometryEstimationCallbacks::on_marginalized_frames.add([](const auto& frames) {
      std::vector<glim::EstimationFrame::Ptr> owned;
      for (const auto& frame : frames) {
        if (!frame) throw std::runtime_error("nonempty_async_finalized_frame_required");
        owned.push_back(frame->clone());
      }
      if (owned.empty()) return;
      const size_t size = owned.size();
      enqueue({size, 0}, [owned = std::move(owned)] {
        for (const auto& frame : owned) {
          if ((!finalized_ids.empty() && frame->id <= finalized_ids.back()) ||
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
    snapshot_callback = glim::GlobalMappingCallbacks::on_update_submaps.add(publish);
    worker = std::make_unique<lingxi::BoundedWorker>(lingxi::BoundedWorker::Counts{128, 8192});
  });
}
extern "C" int lingxi_full_imu(double stamp, const double* acceleration, const double* angular) {
  return async_guarded([&] {
    if (!acceleration || !angular || !std::isfinite(stamp)) throw std::runtime_error("async_imu_vectors_required");
    const Eigen::Vector3d acc = Eigen::Map<const Eigen::Vector3d>(acceleration);
    const Eigen::Vector3d gyro = Eigen::Map<const Eigen::Vector3d>(angular);
    if (!acc.allFinite() || !gyro.allFinite()) throw std::runtime_error("finite_async_imu_required");
    enqueue({0, 1}, [stamp, acc, gyro] { require_sync(lingxi_sync_imu(stamp, acc.data(), gyro.data())); });
  });
}
extern "C" int lingxi_full_finish(const char* map, const char* points, const char* events, double* stats) {
  return async_guarded([&] {
    if (!worker || async_finishing) throw std::runtime_error("one_async_finish_required");
    async_finishing = true;
    worker->finish([&] { require_sync(lingxi_sync_finish(map, points, events, stats)); });
  });
}
extern "C" int lingxi_async_status(double* stats) {
  return async_guarded([&] {
    if (!worker || !stats) throw std::runtime_error("async_worker_status_required");
    const auto status = worker->inspect();
    const double values[] = {double(status.accepted[0]), double(status.completed[0]), double(status.peak[0]),
      double(status.capacity[0]), double(status.accepted[1]), double(status.completed[1]), double(status.peak[1]),
      double(status.capacity[1]), double(status.rejected), double(status.discarded), maximum_enqueue_ms.load(),
      status.maximumWaitMs, status.maximumExecutionMs};
    std::copy(std::begin(values), std::end(values), stats);
    worker->check();
  });
}
extern "C" int lingxi_async_snapshot(double* header, double* xyz, size_t capacity) {
  int retry = 0;
  const int result = async_guarded([&] {
    if (!header || !worker) throw std::runtime_error("open_snapshot_reader_required");
    worker->check();
    std::shared_ptr<const Snapshot> frozen;
    { std::lock_guard<std::mutex> lock(snapshot_mutex); frozen = latest; }
    std::fill(header, header + 6, 0);
    if (!frozen) return;
    const double values[] = {double(frozen->revision), double(frozen->submaps.size()), double(frozen->points),
      double(frozen->matching), double(frozen->nonadjacent), double(frozen->corrected)};
    std::copy(std::begin(values), std::end(values), header);
    if (!xyz) return;
    if (capacity < frozen->points) { retry = 1; return; }
    size_t index = 0;
    for (const auto& map : frozen->submaps) {
      for (const auto& point : *map.points) {
        const Eigen::Vector4d world = map.pose * point;
        std::copy(world.data(), world.data() + 3, xyz + 3 * index++);
      }
    }
  });
  return result < 0 ? result : retry;
}
extern "C" int lingxi_full_shutdown() {
  return async_guarded([] {
    std::exception_ptr failure;
    if (worker) { try { worker->finish(); } catch (...) { failure = std::current_exception(); } }
    // No callback registration changes until the worker and LIO producer are stopped.
    if (snapshot_callback >= 0) glim::GlobalMappingCallbacks::on_update_submaps.remove(snapshot_callback);
    snapshot_callback = -1;
    require_sync(lingxi_sync_shutdown());
    worker.reset();
    { std::lock_guard<std::mutex> lock(snapshot_mutex); latest.reset(); }
    if (failure) std::rethrow_exception(failure);
  });
}
extern "C" int lingxi_full_reload(const char* config, const char* map, const char* points, double* stats) {
  return async_guarded([&] { require_sync(lingxi_sync_reload(config, map, points, stats)); });
}
