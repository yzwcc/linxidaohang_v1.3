cmake_minimum_required(VERSION 3.22)
project(lingxi_glim_offline_replay LANGUAGES CXX)

find_package(glim REQUIRED)
add_library(lingxi_glim_offline SHARED offline.cpp)
target_compile_features(lingxi_glim_offline PRIVATE cxx_std_17)
target_link_libraries(lingxi_glim_offline PRIVATE glim::glim)
target_compile_options(lingxi_glim_offline PRIVATE -Wall -Wextra -Wpedantic)
