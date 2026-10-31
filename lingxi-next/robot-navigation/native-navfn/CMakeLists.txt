cmake_minimum_required(VERSION 3.16)
project(lingxi_navfn LANGUAGES CXX)
find_package(rclcpp REQUIRED)
if(NOT DEFINED NAVFN_UPSTREAM OR NOT DEFINED NAVFN_COMMIT)
  message(FATAL_ERROR "Explicit hash-verified upstream directory and commit required")
endif()
add_library(lingxi_navfn SHARED navfn_bridge.cpp
  "${NAVFN_UPSTREAM}/nav2_navfn_planner/src/navfn.cpp")
target_include_directories(lingxi_navfn PRIVATE
  "${NAVFN_UPSTREAM}/nav2_navfn_planner/include")
target_link_libraries(lingxi_navfn PRIVATE rclcpp::rclcpp)
target_compile_features(lingxi_navfn PRIVATE cxx_std_17)
target_compile_options(lingxi_navfn PRIVATE -Wall -Wextra -Wpedantic)
target_compile_definitions(lingxi_navfn PRIVATE LINGXI_NAVFN_COMMIT="${NAVFN_COMMIT}")
