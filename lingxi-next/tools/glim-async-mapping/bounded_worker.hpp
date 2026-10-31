#pragma once

#include <algorithm>
#include <array>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <exception>
#include <functional>
#include <mutex>
#include <stdexcept>
#include <thread>

namespace lingxi {
// Admission never waits for computation. Limits include the task currently running.
class BoundedWorker {
public:
  using Counts = std::array<size_t, 2>;
  struct Status {
    Counts accepted{}, completed{}, outstanding{}, peak{}, capacity{};
    size_t rejected = 0, discarded = 0;
    double maximumWaitMs = 0, maximumExecutionMs = 0;
  };

  explicit BoundedWorker(Counts capacity) {
    if (!capacity[0] || !capacity[1]) throw std::invalid_argument("positive_worker_capacity_required");
    status.capacity = capacity;
    thread = std::thread([this] { run(); });
  }
  BoundedWorker(const BoundedWorker&) = delete;
  BoundedWorker& operator=(const BoundedWorker&) = delete;
  ~BoundedWorker() { try { finish(); } catch (...) {} }

  void submit(Counts cost, std::function<void()> function) {
    std::lock_guard<std::mutex> lock(mutex);
    rethrow_locked();
    if (closed) throw std::runtime_error("worker_admission_closed");
    if (!function || (!cost[0] && !cost[1])) throw std::invalid_argument("nonempty_worker_task_required");
    for (size_t i = 0; i < 2; ++i) {
      if (cost[i] > status.capacity[i] - status.outstanding[i]) {
        ++status.rejected;
        closed = true;
        fault = std::make_exception_ptr(std::runtime_error("mapping_backlog_capacity_exceeded"));
        changed.notify_one();
        rethrow_locked();
      }
    }
    tasks.push_back({cost, std::move(function), Clock::now()});
    for (size_t i = 0; i < 2; ++i) {
      status.accepted[i] += cost[i];
      status.outstanding[i] += cost[i];
      status.peak[i] = std::max(status.peak[i], status.outstanding[i]);
    }
    changed.notify_one();
  }

  // Only the owner calls finish; producers must already be stopped.
  void finish(std::function<void()> after_drain = {}) {
    {
      std::lock_guard<std::mutex> lock(mutex);
      if (!closed) { closed = true; finalizer = std::move(after_drain); }
    }
    changed.notify_one();
    if (thread.joinable()) thread.join();
    check();
  }
  void check() const { std::lock_guard<std::mutex> lock(mutex); rethrow_locked(); }
  Status inspect() const { std::lock_guard<std::mutex> lock(mutex); return status; }

private:
  using Clock = std::chrono::steady_clock;
  struct Task { Counts cost; std::function<void()> function; Clock::time_point admitted; };
  void rethrow_locked() const { if (fault) std::rethrow_exception(fault); }
  void run() noexcept {
    try {
      while (true) {
        Task task;
        {
          std::unique_lock<std::mutex> lock(mutex);
          changed.wait(lock, [this] { return closed || !tasks.empty(); });
          if (tasks.empty()) break;
          task = std::move(tasks.front());
          tasks.pop_front();
          status.maximumWaitMs = std::max(status.maximumWaitMs,
            std::chrono::duration<double, std::milli>(Clock::now() - task.admitted).count());
        }
        const auto started = Clock::now();
        task.function();
        {
          std::lock_guard<std::mutex> lock(mutex);
          for (size_t i = 0; i < 2; ++i) {
            status.completed[i] += task.cost[i];
            status.outstanding[i] -= task.cost[i];
          }
          status.maximumExecutionMs = std::max(status.maximumExecutionMs,
            std::chrono::duration<double, std::milli>(Clock::now() - started).count());
        }
      }
      std::function<void()> ending;
      { std::lock_guard<std::mutex> lock(mutex); if (!fault) ending = std::move(finalizer); }
      if (ending) ending();
    } catch (...) {
      std::lock_guard<std::mutex> lock(mutex);
      if (!fault) fault = std::current_exception();
      closed = true;
      status.discarded += status.outstanding[0] + status.outstanding[1];
      status.outstanding = {};
      tasks.clear();
    }
  }
  mutable std::mutex mutex;
  std::condition_variable changed;
  std::deque<Task> tasks;
  std::function<void()> finalizer;
  std::exception_ptr fault;
  Status status;
  bool closed = false;
  std::thread thread;
};
}  // namespace lingxi
