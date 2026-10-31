#include "bounded_worker.hpp"
#include <future>
#include <iostream>
#include <string>
#include <vector>

void require(bool condition) { if (!condition) throw std::runtime_error("worker_contract_failed"); }
template <typename Function> void rejects(Function function, const std::string& message) {
  bool rejected = false;
  try { function(); }
  catch (const std::exception& error) { rejected = std::string(error.what()).find(message) != std::string::npos; }
  require(rejected);
}

int main() {
  using lingxi::BoundedWorker;
  {
    std::vector<size_t> order;
    BoundedWorker worker({1024, 1024});
    const auto producer = std::this_thread::get_id();
    for (size_t i = 0; i < 1000; ++i) worker.submit({1, 1}, [&, i] {
      require(std::this_thread::get_id() != producer);
      order.push_back(i);
    });
    worker.finish([&] { require(order.size() == 1000); order.push_back(1000); });
    worker.finish([&] { throw std::runtime_error("second_finalizer_must_not_run"); });
    require(order.size() == 1001);
    for (size_t i = 0; i < order.size(); ++i) require(order[i] == i);
    const auto status = worker.inspect();
    require(status.accepted == status.completed && status.accepted[0] == 1000);
    require(status.outstanding == BoundedWorker::Counts{} && !status.rejected && !status.discarded);
    rejects([&] { worker.submit({1, 0}, [] {}); }, "admission_closed");
  }
  {
    std::promise<void> release, entered;
    auto gate = release.get_future().share();
    BoundedWorker worker({2, 2});
    worker.submit({1, 1}, [&] { entered.set_value(); gate.wait(); });
    entered.get_future().wait();
    // Admission succeeds while computation is deliberately blocked.
    worker.submit({1, 1}, [] {});
    require(worker.inspect().outstanding[0] == 2);
    rejects([&] { worker.submit({1, 0}, [] {}); }, "backlog_capacity_exceeded");
    rejects([&] { worker.submit({0, 1}, [] {}); }, "backlog_capacity_exceeded");
    release.set_value();
    bool finalized = false;
    rejects([&] { worker.finish([&] { finalized = true; }); }, "backlog_capacity_exceeded");
    const auto status = worker.inspect();
    require(!finalized && status.rejected == 1 && status.peak[0] == 2 && status.peak[1] == 2);
    require(status.accepted == status.completed && !status.discarded);
  }
  {
    std::promise<void> release, entered;
    auto gate = release.get_future().share();
    BoundedWorker worker({3, 3});
    worker.submit({1, 0}, [&] { entered.set_value(); gate.wait(); throw std::runtime_error("injected_worker_failure"); });
    entered.get_future().wait();
    worker.submit({0, 1}, [] { throw std::runtime_error("must_not_run_after_failure"); });
    release.set_value();
    rejects([&] { worker.finish(); }, "injected_worker_failure");
    rejects([&] { worker.check(); }, "injected_worker_failure");
    rejects([&] { worker.submit({1, 0}, [] {}); }, "injected_worker_failure");
    const auto status = worker.inspect();
    require(status.discarded == 2 && status.completed == BoundedWorker::Counts{});
    require(status.outstanding == BoundedWorker::Counts{});
  }
  {
    BoundedWorker worker({1, 1});
    rejects([&] { worker.finish([] { throw std::runtime_error("finalization_failure"); }); }, "finalization_failure");
    require(worker.inspect().discarded == 0);
  }
  rejects([] { BoundedWorker worker({0, 1}); }, "positive_worker_capacity");
  std::cout << "bounded worker FIFO, drain, overflow, failure and ownership contracts passed\n";
}
