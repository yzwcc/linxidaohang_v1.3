// Isolated synthetic timing check, not robot calibration or a SLAM benchmark.
#include <cmath>
#include <iomanip>
#include <iostream>
#include <memory>
#include <dlfcn.h>

#include <glim/util/config.hpp>
#include <glim/util/raw_points.hpp>
#include <glim/util/time_keeper.hpp>

int main(int argc, char** argv) {
  if (argc != 5) {
    std::cerr << "An explicit synthetic configuration and three module paths are required\n";
    return 2;
  }
  for (int index = 2; index < argc; ++index) {
    void* module = dlopen(argv[index], RTLD_NOW | RTLD_LOCAL);
    if (!module) {
      std::cerr << "CPU module cannot be loaded: " << dlerror() << "\n";
      return 1;
    }
    dlclose(module);
  }
  glim::GlobalConfig::instance(argv[1]);
  glim::TimeKeeper keeper;
  auto points = std::make_shared<glim::RawPoints>();
  points->stamp = 1000.0;
  points->points = {
    Eigen::Vector4d(1.0, 0.0, 0.0, 1.0),
    Eigen::Vector4d(2.0, 0.0, 0.0, 1.0),
  };
  points->times = {332000.0, 95181000.0};
  if (!keeper.process(points)
      || std::abs(points->stamp - 1000.0) > 1e-12
      || std::abs(points->times[0] - 0.000332) > 1e-12
      || std::abs(points->times[1] - 0.095181) > 1e-12) {
    std::cerr << "Synthetic relative-nanosecond conversion failed\n";
    return 1;
  }
  std::cout << std::setprecision(12)
            << "{\"inputClassification\":\"synthetic_fixture\","
            << "\"nativeModulesLoaded\":3,"
            << "\"robotCalibrationValidated\":false,\"navigationValidated\":false,"
            << "\"frameSeconds\":" << points->stamp
            << ",\"firstSeconds\":" << points->times[0]
            << ",\"lastSeconds\":" << points->times[1] << "}\n";
  return 0;
}
