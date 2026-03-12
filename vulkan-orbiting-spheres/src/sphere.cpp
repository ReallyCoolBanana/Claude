#include "sphere.h"
#include <glm/glm.hpp>
#include <cmath>

SphereMesh generateUVSphere(float radius, uint32_t stacks, uint32_t slices, glm::vec3 color) {
    SphereMesh mesh;

    // Generate vertices
    for (uint32_t i = 0; i <= stacks; ++i) {
        float phi = glm::pi<float>() * static_cast<float>(i) / static_cast<float>(stacks);
        float sinPhi = std::sin(phi);
        float cosPhi = std::cos(phi);

        for (uint32_t j = 0; j <= slices; ++j) {
            float theta = 2.0f * glm::pi<float>() * static_cast<float>(j) / static_cast<float>(slices);
            float sinTheta = std::sin(theta);
            float cosTheta = std::cos(theta);

            glm::vec3 normal(sinPhi * cosTheta, cosPhi, sinPhi * sinTheta);
            glm::vec3 position = radius * normal;

            Vertex vertex{};
            vertex.position = position;
            vertex.normal = normal;
            vertex.color = color;
            mesh.vertices.push_back(vertex);
        }
    }

    // Generate indices
    for (uint32_t i = 0; i < stacks; ++i) {
        for (uint32_t j = 0; j < slices; ++j) {
            uint32_t first = i * (slices + 1) + j;
            uint32_t second = first + slices + 1;

            mesh.indices.push_back(first);
            mesh.indices.push_back(second);
            mesh.indices.push_back(first + 1);

            mesh.indices.push_back(second);
            mesh.indices.push_back(second + 1);
            mesh.indices.push_back(first + 1);
        }
    }

    return mesh;
}
