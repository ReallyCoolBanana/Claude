# Vulkan Orbiting Spheres

A 3D application that renders a blue sphere with an orange sphere orbiting around it, using the Vulkan graphics API with Phong lighting and interactive camera controls.

## Prerequisites

- **Windows 11** (or any OS with Vulkan support)
- **Vulkan SDK** (LunarG) - [Download](https://vulkan.lunarg.com/sdk/home)
- **CMake 3.20+** - [Download](https://cmake.org/download/)
- **Visual Studio 2022** (or any C++17 compiler)
- **GPU with Vulkan 1.2+ support**

## Build

```bash
# Configure (generates Visual Studio solution)
cmake -B build -G "Visual Studio 17 2022" -A x64

# Build
cmake --build build --config Release
```

Or with Ninja (faster):
```bash
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

## Run

```bash
# From project root
build\Release\vulkan-orbiting-spheres.exe
```

## Controls

- **Left mouse drag**: Orbit camera around the scene
- **Scroll wheel**: Zoom in/out
- **ESC**: Close the application

## Features

- Procedurally generated UV spheres
- Blinn-Phong lighting with ambient, diffuse, and specular components
- Smooth orbital animation with self-rotation
- Interactive orbit camera
- Window resize support (swapchain recreation)
- Vulkan validation layers enabled in debug builds
- All dependencies auto-downloaded via CMake FetchContent

## Dependencies (auto-fetched)

| Library | Purpose |
|---------|---------|
| [GLFW](https://www.glfw.org/) | Window creation and input |
| [GLM](https://github.com/g-truc/glm) | Linear algebra |
| [VMA](https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator) | GPU memory allocation |
| [vk-bootstrap](https://github.com/charles-lunarg/vk-bootstrap) | Vulkan initialization |
