#pragma once

#include <glm/glm.hpp>
#include <glm/gtc/matrix_transform.hpp>

class Camera {
public:
    Camera();

    glm::mat4 getViewMatrix() const;
    glm::mat4 getProjectionMatrix(float aspectRatio) const;

    void processMouseDrag(float dx, float dy);
    void processScroll(float offset);
    void update();

private:
    float m_yaw = -90.0f;    // horizontal angle (degrees)
    float m_pitch = 20.0f;   // vertical angle (degrees)
    float m_distance = 6.0f; // distance from target
    float m_fov = 45.0f;

    glm::vec3 m_target = glm::vec3(0.0f);
    glm::vec3 m_position;

    static constexpr float SENSITIVITY = 0.3f;
    static constexpr float ZOOM_SPEED = 0.5f;
    static constexpr float MIN_DISTANCE = 2.0f;
    static constexpr float MAX_DISTANCE = 20.0f;
    static constexpr float MIN_PITCH = -89.0f;
    static constexpr float MAX_PITCH = 89.0f;
};
