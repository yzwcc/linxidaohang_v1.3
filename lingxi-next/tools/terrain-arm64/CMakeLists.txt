cmake_minimum_required(VERSION 3.16)
project(lingxi_declared_terrain LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_SKIP_BUILD_RPATH TRUE)
add_compile_options(-O2 -Wall -Wextra -Wpedantic)
find_package(Threads REQUIRED)
add_library(lingxi_ground SHARED ground_bridge.cpp
  upstream/cpp/patchworkpp/src/patchworkpp.cpp upstream/cpp/common/src/plane_fit.cpp)
target_include_directories(lingxi_ground PRIVATE eigen
  upstream/cpp/patchworkpp/include upstream/cpp/common/include)
target_compile_definitions(lingxi_ground PRIVATE
  LINGXI_PATCHWORKPP_REVISION="${LINGXI_PATCHWORKPP_REVISION}")
target_link_libraries(lingxi_ground PRIVATE Threads::Threads)
target_link_options(lingxi_ground PRIVATE -Wl,--no-undefined
  "-Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/ground.exports")
add_library(lingxi_octomap SHARED octomap_bridge.cpp)
target_include_directories(lingxi_octomap PRIVATE include)
target_compile_definitions(lingxi_octomap PRIVATE LINGXI_OCTOMAP_VERSION="1.9.7")
target_link_libraries(lingxi_octomap PRIVATE
  "${CMAKE_CURRENT_SOURCE_DIR}/lib/liboctomap.so.1.9.7"
  "${CMAKE_CURRENT_SOURCE_DIR}/lib/liboctomath.so.1.9.7")
target_link_options(lingxi_octomap PRIVATE -Wl,--no-undefined
  "-Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/octomap.exports"
  "-Wl,-rpath,$ORIGIN/lib")
