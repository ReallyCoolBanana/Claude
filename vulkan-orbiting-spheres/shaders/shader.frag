#version 450

layout(location = 0) in vec3 fragColor;
layout(location = 1) in vec3 fragNormal;
layout(location = 2) in vec3 fragWorldPos;
layout(location = 3) in vec3 fragLightPos;

layout(location = 0) out vec4 outColor;

void main() {
    vec3 normal = normalize(fragNormal);
    vec3 lightDir = normalize(fragLightPos - fragWorldPos);
    vec3 viewDir = normalize(-fragWorldPos);

    // Ambient
    vec3 ambient = 0.15 * fragColor;

    // Diffuse (Lambertian)
    float diff = max(dot(normal, lightDir), 0.0);
    vec3 diffuse = diff * fragColor;

    // Specular (Blinn-Phong)
    vec3 halfDir = normalize(lightDir + viewDir);
    float spec = pow(max(dot(normal, halfDir), 0.0), 64.0);
    vec3 specular = 0.4 * spec * vec3(1.0);

    outColor = vec4(ambient + diffuse + specular, 1.0);
}
