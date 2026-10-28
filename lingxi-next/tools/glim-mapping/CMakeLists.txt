cmake_minimum_required(VERSION 3.22)
project(lingxi_glim_mapping_diagnostic LANGUAGES CXX)

find_package(glim REQUIRED)
add_library(lingxi_glim_mapping SHARED mapping.cpp)
target_compile_features(lingxi_glim_mapping PRIVATE cxx_std_17)
target_link_libraries(lingxi_glim_mapping PRIVATE
  glim::glim glim::sub_mapping_passthrough
  glim::global_mapping_pose_graph glim::global_mapping)
target_compile_options(lingxi_glim_mapping PRIVATE -Wall -Wextra -Wpedantic)
