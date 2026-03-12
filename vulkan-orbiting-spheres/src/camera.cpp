#include "camera.h"
#include <cmath>
#include <algorithm>

Camera::Camera() {
    update();
}

void Camera::update() {
    float yawRad = glm::radians(m_yaw);
    float pitchRad = glm::radians(m_pitch);

    m_position.x = m_target.x + m_distance * std::cos(pitchRad) * std::cos(yawRad);
    m_position.y = m_target.y + m_distance * std::sin(pitchRad);
    m_position.z = m_target.z + m_distance * std::cos(pitchRad) * std::sin(yawRad);
}

glm::mat4 Camera::getViewMatrix() const {
    return glm::lookAt(m_position, m_target, glm::vec3(0.0f, 1.0f, 0.0f));
}

glm::mat4 Camera::getProjectionMatrix(float aspectRatio) const {
    auto proj = glm::perspective(glm::radians(m_fov), aspectRatio, 0.1f, 100.0f);
    proj[1][1] *= -1; // Vulkan has inverted Y
    return proj;
}

void Camera::processMouseDrag(float dx, float dy) {
    m_yaw += dx * SENSITIVITY;
    m_pitch += dy * SENSITIVITY;
    m_pitch = std::clamp(m_pitch, MIN_PITCH, MAX_PITCH);
    update();
}

void Camera::processScroll(float offset) {
    m_distance -= offset * ZOOM_SPEED;
    m_distance = std::clamp(m_distance, MIN_DISTANCE, MAX_DISTANCE);
    update();
}
