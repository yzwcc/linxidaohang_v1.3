cmake_minimum_required(VERSION 3.22)
project(lingxi_glim_observations LANGUAGES CXX)
find_package(glim REQUIRED)
find_package(Threads REQUIRED)
add_library(lingxi_glim_full_mapping SHARED async/async_mapping.cpp observation_capture.cpp
  upstream/src/glim/mapping/sub_mapping.cpp)
target_compile_features(lingxi_glim_full_mapping PRIVATE cxx_std_17)
target_compile_options(lingxi_glim_full_mapping PRIVATE -Wall -Wextra -Wpedantic)
target_include_directories(lingxi_glim_full_mapping BEFORE PRIVATE upstream/include ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_definitions(lingxi_glim_full_mapping PRIVATE
  SubMapping=LingxiSubMappingEof SubMappingParams=LingxiSubMappingEofParams)
target_link_libraries(lingxi_glim_full_mapping PRIVATE glim::glim glim::global_mapping Threads::Threads)
