#pragma once

#include "types.h"
#include <vector>

struct SphereMesh {
    std::vector<Vertex> vertices;
    std::vector<uint32_t> indices;
};

// Generate a UV sphere with given parameters
SphereMesh generateUVSphere(float radius, uint32_t stacks, uint32_t slices, glm::vec3 color);
