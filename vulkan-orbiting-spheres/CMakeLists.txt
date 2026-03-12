cmake_minimum_required(VERSION 3.20)
project(vulkan-orbiting-spheres VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# --- Find Vulkan SDK ---
find_package(Vulkan REQUIRED)
message(STATUS "Vulkan found: ${Vulkan_INCLUDE_DIRS}")

# Find glslc for shader compilation
find_program(GLSLC_EXECUTABLE glslc HINTS
    "$ENV{VULKAN_SDK}/Bin"
    "$ENV{VULKAN_SDK}/bin"
)
if(NOT GLSLC_EXECUTABLE)
    message(WARNING "glslc not found - shaders must be compiled manually")
endif()

# --- Fetch dependencies ---
include(FetchContent)

FetchContent_Declare(
    glfw
    GIT_REPOSITORY https://github.com/glfw/glfw.git
    GIT_TAG        3.4
    GIT_SHALLOW    TRUE
)
set(GLFW_BUILD_DOCS OFF CACHE BOOL "" FORCE)
set(GLFW_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(GLFW_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)

FetchContent_Declare(
    glm
    GIT_REPOSITORY https://github.com/g-truc/glm.git
    GIT_TAG        1.0.1
    GIT_SHALLOW    TRUE
)

FetchContent_Declare(
    vk-bootstrap
    GIT_REPOSITORY https://github.com/charles-lunarg/vk-bootstrap.git
    GIT_TAG        v1.3.296
    GIT_SHALLOW    TRUE
)

FetchContent_Declare(
    VulkanMemoryAllocator
    GIT_REPOSITORY https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator.git
    GIT_TAG        v3.1.0
    GIT_SHALLOW    TRUE
)

FetchContent_MakeAvailable(glfw glm vk-bootstrap VulkanMemoryAllocator)

# --- Compile shaders ---
set(SHADER_DIR "${CMAKE_SOURCE_DIR}/shaders")
set(SHADER_BIN_DIR "${CMAKE_BINARY_DIR}/shaders")
file(MAKE_DIRECTORY ${SHADER_BIN_DIR})

set(SHADER_SOURCES
    ${SHADER_DIR}/shader.vert
    ${SHADER_DIR}/shader.frag
)

set(SHADER_OUTPUTS "")
if(GLSLC_EXECUTABLE)
    foreach(SHADER_SRC ${SHADER_SOURCES})
        get_filename_component(SHADER_NAME ${SHADER_SRC} NAME)
        set(SHADER_OUT "${SHADER_BIN_DIR}/${SHADER_NAME}.spv")
        add_custom_command(
            OUTPUT ${SHADER_OUT}
            COMMAND ${GLSLC_EXECUTABLE} ${SHADER_SRC} -o ${SHADER_OUT}
            DEPENDS ${SHADER_SRC}
            COMMENT "Compiling shader ${SHADER_NAME}"
        )
        list(APPEND SHADER_OUTPUTS ${SHADER_OUT})
    endforeach()
    add_custom_target(shaders ALL DEPENDS ${SHADER_OUTPUTS})
endif()

# --- Main executable ---
add_executable(${PROJECT_NAME}
    src/main.cpp
    src/vulkan_app.cpp
    src/pipeline.cpp
    src/sphere.cpp
    src/camera.cpp
)

target_include_directories(${PROJECT_NAME} PRIVATE
    ${CMAKE_SOURCE_DIR}/src
)

target_link_libraries(${PROJECT_NAME} PRIVATE
    Vulkan::Vulkan
    glfw
    glm::glm
    vk-bootstrap::vk-bootstrap
    GPUOpen::VulkanMemoryAllocator
)

target_compile_definitions(${PROJECT_NAME} PRIVATE
    SHADER_DIR="${SHADER_BIN_DIR}"
    GLM_FORCE_RADIANS
    GLM_FORCE_DEPTH_ZERO_TO_ONE
)

if(GLSLC_EXECUTABLE)
    add_dependencies(${PROJECT_NAME} shaders)
endif()

# On Windows, use console subsystem for easier debugging
if(WIN32)
    set_target_properties(${PROJECT_NAME} PROPERTIES
        WIN32_EXECUTABLE FALSE
    )
endif()
