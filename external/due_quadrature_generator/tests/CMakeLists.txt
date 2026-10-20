cmake_minimum_required(VERSION 3.13)

project(due_quadrature_timing_tests CXX)

enable_testing()

add_executable(test_quadrature_timing test_quadrature_timing.cpp)
target_compile_features(test_quadrature_timing PRIVATE cxx_std_11)
target_compile_options(
    test_quadrature_timing
    PRIVATE
        -Wall
        -Wextra
        -Werror
        -Wconversion
        -Wsign-conversion
)

add_test(NAME due_quadrature_timing_contract COMMAND test_quadrature_timing)
