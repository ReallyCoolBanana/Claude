# Vulkan 3D Orbiting Spheres App — Implementation Plan

## Overview
A Windows 11 desktop application that renders a 3D sphere with another 3D sphere orbiting around it, using the Vulkan graphics API. The user will download the folder from GitHub and build/launch it locally.

## Target Platform
- **OS**: Windows 11
- **Build System**: CMake 3.20+
- **Compiler**: MSVC (Visual Studio 2022) or MinGW-w64
- **Graphics API**: Vulkan 1.3+
- **Required**: Vulkan SDK installed (LunarG)

## Architecture

### Project Structure
```
vulkan-orbiting-spheres/
├── CMakeLists.txt              # Root build file with FetchContent deps
├── README.md                   # Build & run instructions
├── PLAN.md                     # This file
├── src/
│   ├── main.cpp                # Entry point, window creation, main loop
│   ├── vulkan_app.h            # VulkanApp class declaration
│   ├── vulkan_app.cpp          # Vulkan initialization, cleanup, render loop
│   ├── pipeline.h              # Graphics pipeline creation
│   ├── pipeline.cpp            # Pipeline, render pass, shaders
│   ├── sphere.h                # Sphere mesh generation (UV sphere)
│   ├── sphere.cpp              # Vertex/index buffer creation
│   ├── camera.h                # Orbit camera with mouse/keyboard input
│   ├── camera.cpp              # View/projection matrix updates
│   └── types.h                 # Shared types (Vertex, UniformBufferObject)
├── shaders/
│   ├── shader.vert             # Vertex shader (MVP transform + lighting)
│   ├── shader.frag             # Fragment shader (Phong lighting + color)
│   ├── compile_shaders.bat     # Windows batch script to compile SPIR-V
│   └── compile_shaders.sh      # Linux/macOS script (convenience)
└── .gitignore                  # Build artifacts, .spv files (generated)
```

### Dependencies (via CMake FetchContent — auto-downloaded)
| Library | Purpose | Version |
|---------|---------|---------|
| GLFW | Window creation + input | 3.4+ |
| GLM | Linear algebra (vec3, mat4, perspective) | 1.0+ |
| VMA | GPU memory allocation | 3.1+ |
| vk-bootstrap | Vulkan init boilerplate | 0.7+ |

**NOT bundled** (user must install):
- Vulkan SDK (LunarG) — provides vulkan headers, loader, glslc, validation layers

### Rendering Architecture
```
Per Frame:
  1. Acquire swapchain image
  2. Update uniform buffer (time, MVP matrices for both spheres)
  3. Record command buffer:
     a. Begin render pass (clear color + depth)
     b. Bind graphics pipeline
     c. Draw central sphere (bind vertex/index buffer, push model matrix)
     d. Draw orbiting sphere (bind same buffers, push different model matrix)
     e. End render pass
  4. Submit to graphics queue (wait on image-available semaphore)
  5. Present (signal render-finished semaphore)
```

## Implementation Steps

### Phase 1: Project Skeleton & Build System
1. Create `CMakeLists.txt` with:
   - `cmake_minimum_required(VERSION 3.20)`
   - `find_package(Vulkan REQUIRED)` for SDK
   - `FetchContent` for GLFW, GLM, VMA, vk-bootstrap
   - Shader compilation custom command (glslc)
2. Create `main.cpp` with GLFW window + basic event loop
3. Create `compile_shaders.bat` for manual shader compilation
4. Create `.gitignore` (build/, *.spv, .vs/)
5. Create `README.md` with build instructions

### Phase 2: Vulkan Initialization
1. Instance creation (via vk-bootstrap)
   - Enable validation layers in debug
   - Request `VK_KHR_surface` + `VK_KHR_win32_surface`
2. Surface creation (GLFW)
3. Physical device selection (vk-bootstrap, prefer discrete GPU)
4. Logical device + queues (graphics + present)
5. VMA allocator initialization
6. Swapchain creation (FIFO present mode, BGRA8 format)
7. Depth buffer (VK_FORMAT_D32_SFLOAT)
8. Render pass (1 color attachment + 1 depth attachment)
9. Framebuffers (one per swapchain image)
10. Command pool + command buffers
11. Sync objects (2 frames in flight: semaphores + fences)

### Phase 3: Graphics Pipeline
1. Write vertex shader (`shader.vert`):
   - Input: position (vec3), normal (vec3), color (vec3)
   - Uniform: model, view, projection matrices, light position
   - Output: world-space position, normal, color to fragment shader
2. Write fragment shader (`shader.frag`):
   - Phong lighting: ambient + diffuse + specular
   - Input color from vertex data
3. Compile shaders to SPIR-V (glslc)
4. Create pipeline layout:
   - One descriptor set: UBO with matrices + light data
   - One push constant: per-object model matrix (mat4)
5. Create graphics pipeline:
   - Vertex input: pos(vec3) + normal(vec3) + color(vec3) = 36 bytes/vertex
   - Topology: triangle list
   - Depth test: enabled, write enabled
   - Cull mode: back face
   - Front face: counter-clockwise

### Phase 4: Sphere Geometry
1. Generate UV sphere mesh procedurally:
   - Parameters: radius, stacks (latitude), slices (longitude)
   - Central sphere: radius=1.0, 32 stacks, 64 slices, color=blue
   - Orbiting sphere: radius=0.3, 16 stacks, 32 slices, color=orange
2. Compute vertices: position + normal + color per vertex
   - Position: (r·sin(φ)·cos(θ), r·cos(φ), r·sin(φ)·sin(θ))
   - Normal: normalized position (for unit sphere)
3. Generate index buffer (triangle list)
4. Upload to GPU via VMA (staging buffer → device-local buffer)

### Phase 5: Orbit Animation
1. Uniform buffer object structure:
   ```cpp
   struct UBO {
       mat4 view;
       mat4 proj;
       vec4 lightPos;    // point light position
       float time;       // elapsed time for animation
   };
   ```
2. Push constant for per-object model matrix:
   ```cpp
   struct PushConstants {
       mat4 model;
   };
   ```
3. Central sphere: identity model matrix (sits at origin)
4. Orbiting sphere: `translate(orbit_radius, 0, 0) * rotate(time * speed, Y_AXIS)`
   - Orbit radius: 3.0 units
   - Speed: 1 radian/second
   - Also self-rotates on its own axis
5. Camera: perspective projection, positioned at (0, 2, 5), looking at origin

### Phase 6: Camera & Input
1. Mouse drag to orbit camera around scene
2. Scroll wheel to zoom in/out
3. ESC to close window
4. Window resize handling (swapchain recreation)

### Phase 7: Polish & Distribution
1. Add basic Phong lighting (directional + ambient)
2. Grid or subtle background for depth perception
3. README with clear build instructions:
   ```
   Prerequisites: Vulkan SDK, CMake 3.20+, Visual Studio 2022
   Build:
     cmake -B build -G "Visual Studio 17 2022" -A x64
     cmake --build build --config Release
   Run:
     build\Release\vulkan-orbiting-spheres.exe
   ```
4. Test on clean Windows 11 with Vulkan SDK installed

## Shader Details

### Vertex Shader (shader.vert)
```glsl
#version 450

layout(binding = 0) uniform UBO {
    mat4 view;
    mat4 proj;
    vec4 lightPos;
    float time;
} ubo;

layout(push_constant) uniform PushConstants {
    mat4 model;
} push;

layout(location = 0) in vec3 inPosition;
layout(location = 1) in vec3 inNormal;
layout(location = 2) in vec3 inColor;

layout(location = 0) out vec3 fragColor;
layout(location = 1) out vec3 fragNormal;
layout(location = 2) out vec3 fragWorldPos;
layout(location = 3) out vec3 fragLightPos;

void main() {
    vec4 worldPos = push.model * vec4(inPosition, 1.0);
    gl_Position = ubo.proj * ubo.view * worldPos;
    fragWorldPos = worldPos.xyz;
    fragNormal = mat3(transpose(inverse(push.model))) * inNormal;
    fragColor = inColor;
    fragLightPos = ubo.lightPos.xyz;
}
```

### Fragment Shader (shader.frag)
```glsl
#version 450

layout(location = 0) in vec3 fragColor;
layout(location = 1) in vec3 fragNormal;
layout(location = 2) in vec3 fragWorldPos;
layout(location = 3) in vec3 fragLightPos;

layout(location = 0) out vec4 outColor;

void main() {
    vec3 normal = normalize(fragNormal);
    vec3 lightDir = normalize(fragLightPos - fragWorldPos);

    // Ambient
    vec3 ambient = 0.15 * fragColor;

    // Diffuse
    float diff = max(dot(normal, lightDir), 0.0);
    vec3 diffuse = diff * fragColor;

    // Specular
    vec3 viewDir = normalize(-fragWorldPos);
    vec3 reflectDir = reflect(-lightDir, normal);
    float spec = pow(max(dot(viewDir, reflectDir), 0.0), 32.0);
    vec3 specular = 0.5 * spec * vec3(1.0);

    outColor = vec4(ambient + diffuse + specular, 1.0);
}
```

## Key Design Decisions

1. **UV sphere over icosphere**: Simpler to implement, good enough for this demo, trivial UV mapping
2. **Push constants for model matrix**: Avoids per-object descriptor sets, 128-byte minimum guaranteed
3. **2 frames in flight**: Standard double-buffering for smooth rendering
4. **FetchContent over git submodules**: Simpler for users downloading from GitHub (no `--recursive` needed)
5. **Shader pre-compilation**: Ship `.spv` files AND source `.vert/.frag` with compile script
6. **No texture**: Vertex colors + Phong lighting keeps the demo focused and dependency-light

## Team Assignments (for multi-agent implementation)

| Team | Role | Files |
|------|------|-------|
| TT-A (Build) | CMake, FetchContent, build scripts | CMakeLists.txt, compile_shaders.bat, .gitignore, README.md |
| TT-B (Core) | Vulkan init, swapchain, sync | vulkan_app.h/cpp, types.h |
| TT-C (Pipeline) | Shaders, pipeline, render pass | pipeline.h/cpp, shader.vert, shader.frag |
| TT-D (Geometry) | Sphere generation, buffers | sphere.h/cpp |
| TT-E (Scene) | Camera, orbit animation, input | camera.h/cpp, main.cpp |

## Success Criteria
- [ ] Builds on Windows 11 with `cmake -B build && cmake --build build`
- [ ] Shows a blue sphere at center
- [ ] Orange sphere orbits around it smoothly
- [ ] Basic Phong lighting visible
- [ ] Mouse orbit camera works
- [ ] ESC closes the window
- [ ] No validation layer errors
