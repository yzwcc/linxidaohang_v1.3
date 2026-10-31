cmake_minimum_required(VERSION 3.22)
project(lingxi_glim_saved_map_refinement LANGUAGES CXX)

find_package(glim REQUIRED)
add_executable(lingxi_glim_refine refine.cpp)
target_compile_features(lingxi_glim_refine PRIVATE cxx_std_17)
target_compile_options(lingxi_glim_refine PRIVATE -Wall -Wextra -Wpedantic)
target_link_libraries(lingxi_glim_refine PRIVATE glim::glim glim::global_mapping)
