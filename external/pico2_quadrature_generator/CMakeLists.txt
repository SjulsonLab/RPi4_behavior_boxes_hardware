cmake_minimum_required(VERSION 3.13)

set(PICO_BOARD pico2 CACHE STRING "Target Raspberry Pi Pico board")
if(NOT PICO_BOARD STREQUAL "pico2")
    message(FATAL_ERROR "This firmware targets the non-wireless Raspberry Pi Pico 2 (PICO_BOARD=pico2)")
endif()

include(pico_sdk_import.cmake)

project(pico2_quadrature_generator C CXX ASM)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

pico_sdk_init()

add_executable(pico2_quadrature_generator main.c)

pico_generate_pio_header(
    pico2_quadrature_generator
    ${CMAKE_CURRENT_LIST_DIR}/quadrature_burst.pio
)

target_link_libraries(
    pico2_quadrature_generator
    PRIVATE
        pico_stdlib
        hardware_clocks
        hardware_pio
)

target_compile_options(
    pico2_quadrature_generator
    PRIVATE
        -Wall
        -Wextra
        -Werror
)

pico_enable_stdio_usb(pico2_quadrature_generator 0)
pico_enable_stdio_uart(pico2_quadrature_generator 0)
pico_add_extra_outputs(pico2_quadrature_generator)
