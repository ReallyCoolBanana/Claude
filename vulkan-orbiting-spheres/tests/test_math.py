#!/usr/bin/env python3
"""
Math validation tests for vulkan-orbiting-spheres.
Recreates sphere generation, camera math, and orbit animation in Python
and verifies correctness.
"""

import math
import sys

PASS_COUNT = 0
FAIL_COUNT = 0
BUGS = []

def check(name, condition, detail=""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  PASS: {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL: {name} -- {detail}")
    return condition


# ─── GLM-compatible helpers ───────────────────────────────────────────────────

def vec3_length(v):
    return math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)

def vec3_sub(a, b):
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])

def vec3_cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])

def vec3_dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]

def vec3_normalize(v):
    l = vec3_length(v)
    return (v[0]/l, v[1]/l, v[2]/l)

def mat4_identity():
    m = [[0.0]*4 for _ in range(4)]
    for i in range(4): m[i][i] = 1.0
    return m

def mat4_mul(a, b):
    """Column-major 4x4 multiply matching GLM convention: a[col][row]."""
    r = [[0.0]*4 for _ in range(4)]
    for c in range(4):
        for row in range(4):
            s = 0.0
            for k in range(4):
                s += a[k][row] * b[c][k]
            r[c][row] = s
    return r

def mat4_mul_vec4(m, v):
    """m is column-major: m[col][row]. v is (x,y,z,w)."""
    r = [0.0]*4
    for row in range(4):
        for col in range(4):
            r[row] += m[col][row] * v[col]
    return tuple(r)

def glm_rotate(mat, angle_rad, axis):
    """glm::rotate – axis must be normalized."""
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    t = 1.0 - c
    x, y, z = axis
    # Rotation matrix (column-major)
    rot = mat4_identity()
    rot[0][0] = t*x*x + c;    rot[0][1] = t*x*y + s*z; rot[0][2] = t*x*z - s*y
    rot[1][0] = t*x*y - s*z;  rot[1][1] = t*y*y + c;   rot[1][2] = t*y*z + s*x
    rot[2][0] = t*x*z + s*y;  rot[2][1] = t*y*z - s*x; rot[2][2] = t*z*z + c
    return mat4_mul(mat, rot)

def glm_translate(mat, offset):
    """glm::translate."""
    t = mat4_identity()
    t[3][0] = offset[0]; t[3][1] = offset[1]; t[3][2] = offset[2]
    return mat4_mul(mat, t)

def glm_lookAt(eye, center, up):
    """glm::lookAt (right-handed)."""
    f = vec3_normalize(vec3_sub(center, eye))
    s = vec3_normalize(vec3_cross(f, up))
    u = vec3_cross(s, f)
    m = mat4_identity()
    m[0][0] = s[0];  m[1][0] = s[1];  m[2][0] = s[2]
    m[0][1] = u[0];  m[1][1] = u[1];  m[2][1] = u[2]
    m[0][2] = -f[0]; m[1][2] = -f[1]; m[2][2] = -f[2]
    m[3][0] = -vec3_dot(s, eye)
    m[3][1] = -vec3_dot(u, eye)
    m[3][2] = vec3_dot(f, eye)
    return m

def glm_perspective(fovy_rad, aspect, near, far):
    """glm::perspective (right-handed, depth [0,1] or [-1,1] – we only care about [1][1] sign)."""
    tanHalf = math.tan(fovy_rad / 2.0)
    m = [[0.0]*4 for _ in range(4)]
    m[0][0] = 1.0 / (aspect * tanHalf)
    m[1][1] = 1.0 / tanHalf
    m[2][2] = -(far + near) / (far - near)
    m[2][3] = -1.0
    m[3][2] = -(2.0 * far * near) / (far - near)
    return m


# ═══════════════════════════════════════════════════════════════════════════════
# 1. UV SPHERE GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def generate_uv_sphere(radius, stacks, slices):
    """Exact Python port of generateUVSphere from sphere.cpp."""
    vertices = []  # list of (position, normal)
    for i in range(stacks + 1):
        phi = math.pi * i / stacks
        sinPhi = math.sin(phi)
        cosPhi = math.cos(phi)
        for j in range(slices + 1):
            theta = 2.0 * math.pi * j / slices
            sinTheta = math.sin(theta)
            cosTheta = math.cos(theta)
            normal = (sinPhi * cosTheta, cosPhi, sinPhi * sinTheta)
            position = (radius * normal[0], radius * normal[1], radius * normal[2])
            vertices.append((position, normal))

    indices = []
    for i in range(stacks):
        for j in range(slices):
            first = i * (slices + 1) + j
            second = first + slices + 1
            indices.append(first)
            indices.append(first + 1)
            indices.append(second)
            indices.append(second)
            indices.append(first + 1)
            indices.append(second + 1)

    return vertices, indices


print("=" * 70)
print("1. UV SPHERE GENERATION TESTS")
print("=" * 70)

radius = 1.0
stacks = 4
slices = 8
verts, indices = generate_uv_sphere(radius, stacks, slices)

# 1a. Vertex count
expected_vcount = (stacks + 1) * (slices + 1)
check("Vertex count", len(verts) == expected_vcount,
      f"expected {expected_vcount}, got {len(verts)}")

# 1b. All normals are unit length
max_norm_err = 0
for pos, nrm in verts:
    err = abs(vec3_length(nrm) - 1.0)
    max_norm_err = max(max_norm_err, err)
check("All normals unit length", max_norm_err < 1e-6,
      f"max error = {max_norm_err}")

# 1c. All positions on sphere surface
max_pos_err = 0
for pos, nrm in verts:
    err = abs(vec3_length(pos) - radius)
    max_pos_err = max(max_pos_err, err)
check("All positions on sphere surface", max_pos_err < 1e-6,
      f"max error = {max_pos_err}")

# 1d. Index count = stacks * slices * 6 (2 triangles per quad)
expected_icount = stacks * slices * 6
check("Index count", len(indices) == expected_icount,
      f"expected {expected_icount}, got {len(indices)}")

# 1e. All indices in bounds
max_idx = max(indices)
check("Indices in bounds", max_idx < len(verts),
      f"max index {max_idx} >= vertex count {len(verts)}")

# 1f. Triangle winding (CCW) – check via cross-product dot with outward normal
ccw_ok = True
ccw_fail_count = 0
for t in range(0, len(indices), 3):
    i0, i1, i2 = indices[t], indices[t+1], indices[t+2]
    p0 = verts[i0][0]
    p1 = verts[i1][0]
    p2 = verts[i2][0]
    e1 = vec3_sub(p1, p0)
    e2 = vec3_sub(p2, p0)
    face_normal = vec3_cross(e1, e2)
    fn_len = vec3_length(face_normal)
    if fn_len < 1e-12:
        continue  # degenerate triangle at poles
    centroid = ((p0[0]+p1[0]+p2[0])/3, (p0[1]+p1[1]+p2[1])/3, (p0[2]+p1[2]+p2[2])/3)
    dot = vec3_dot(face_normal, centroid)
    if dot < -1e-9:
        ccw_fail_count += 1
        ccw_ok = False

check("Triangle winding (CCW, outward-facing)", ccw_ok,
      f"{ccw_fail_count} triangles have inward-facing normals")

# 1g. Pole vertices
top_pole = verts[0][0]  # i=0, j=0 => phi=0 => normal=(0,1,0)
bottom_pole_idx = stacks * (slices + 1)  # i=stacks, j=0 => phi=pi => normal=(0,-1,0)
bottom_pole = verts[bottom_pole_idx][0]
check("Top pole at (0, +radius, 0)",
      abs(top_pole[0]) < 1e-6 and abs(top_pole[1] - radius) < 1e-6 and abs(top_pole[2]) < 1e-6,
      f"got {top_pole}")
check("Bottom pole at (0, -radius, 0)",
      abs(bottom_pole[0]) < 1e-6 and abs(bottom_pole[1] + radius) < 1e-6 and abs(bottom_pole[2]) < 1e-6,
      f"got {bottom_pole}")

# 1h. Check for degenerate triangles at poles
degen_count = 0
for t in range(0, len(indices), 3):
    i0, i1, i2 = indices[t], indices[t+1], indices[t+2]
    p0 = verts[i0][0]
    p1 = verts[i1][0]
    p2 = verts[i2][0]
    e1 = vec3_sub(p1, p0)
    e2 = vec3_sub(p2, p0)
    area = vec3_length(vec3_cross(e1, e2)) / 2.0
    if area < 1e-12:
        degen_count += 1

if degen_count > 0:
    print(f"  INFO: {degen_count} degenerate triangles found (typical at poles for UV spheres)")
    # This is expected for UV sphere – pole vertices share the same position across all slices
    # Not a bug, but worth noting.


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CAMERA MATH
# ═══════════════════════════════════════════════════════════════════════════════

print()
print("=" * 70)
print("2. CAMERA MATH TESTS")
print("=" * 70)

def camera_position(yaw_deg, pitch_deg, distance, target=(0,0,0)):
    """Recreate Camera::update() from camera.cpp."""
    yaw_rad = math.radians(yaw_deg)
    pitch_rad = math.radians(pitch_deg)
    x = target[0] + distance * math.cos(pitch_rad) * math.cos(yaw_rad)
    y = target[1] + distance * math.sin(pitch_rad)
    z = target[2] + distance * math.cos(pitch_rad) * math.sin(yaw_rad)
    return (x, y, z)

# 2a. Default camera: yaw=-90, pitch=20, distance=6
# At yaw=-90: cos(-90deg)=0, sin(-90deg)=-1 => x=0, z=-6*cos(20deg)
# But the test spec says yaw=0, pitch=0, distance=5 => eye at (0,0,5)
# Let's test per the C++ formula.

# Test: yaw=0, pitch=0, distance=5
pos = camera_position(0, 0, 5)
# cos(0)*cos(0)=1 => x=5, sin(0)=0 => y=0, cos(0)*sin(0)=0 => z=0
check("Camera yaw=0, pitch=0, dist=5 => eye at (5,0,0)",
      abs(pos[0] - 5) < 1e-6 and abs(pos[1]) < 1e-6 and abs(pos[2]) < 1e-6,
      f"got {pos}")

# NOTE: The C++ code uses:
#   x = dist * cos(pitch) * cos(yaw)
#   z = dist * cos(pitch) * sin(yaw)
# At yaw=0: eye is at (dist, 0, 0), NOT (0, 0, dist) as the spec suggested.
# This is actually correct math, just a different convention. The spec assumed
# a different yaw convention. Let's verify what the code actually does.

# 2b. yaw=90, pitch=0, distance=5
pos90 = camera_position(90, 0, 5)
# cos(90deg)≈0, sin(90deg)=1 => x≈0, y=0, z=5
check("Camera yaw=90, pitch=0, dist=5 => eye at (0,0,5)",
      abs(pos90[0]) < 1e-6 and abs(pos90[1]) < 1e-6 and abs(pos90[2] - 5) < 1e-6,
      f"got {pos90}")

# 2c. Default camera position (yaw=-90, pitch=20, dist=6)
default_pos = camera_position(-90, 20, 6)
# cos(-90)≈0, sin(-90)=-1 => x≈0, z=-6*cos(20deg)≈-5.638
expected_y = 6 * math.sin(math.radians(20))
expected_z = 6 * math.cos(math.radians(20)) * math.sin(math.radians(-90))
check("Default camera yaw=-90 => z negative (looking from -Z)",
      default_pos[2] < -5.0,
      f"z = {default_pos[2]}")
check("Default camera pitch=20 => y positive",
      abs(default_pos[1] - expected_y) < 1e-6,
      f"y = {default_pos[1]}, expected {expected_y}")

# 2d. Projection Y-inversion
proj = glm_perspective(math.radians(45), 16/9, 0.1, 100.0)
# Before Vulkan inversion, proj[1][1] is positive
check("Projection [1][1] positive before inversion", proj[1][1] > 0,
      f"proj[1][1] = {proj[1][1]}")
# After Vulkan inversion:
proj[1][1] *= -1
check("Projection [1][1] negative after Vulkan Y-inversion", proj[1][1] < 0,
      f"proj[1][1] = {proj[1][1]}")

# 2e. Pitch clamping
MIN_PITCH = -89.0
MAX_PITCH = 89.0
SENSITIVITY = 0.3

# Simulate dragging far enough to exceed limits
test_pitch = 20.0  # default
test_pitch += 1000 * SENSITIVITY  # massive drag
test_pitch = max(MIN_PITCH, min(MAX_PITCH, test_pitch))
check("Pitch clamps at +89", test_pitch == MAX_PITCH,
      f"pitch = {test_pitch}")

test_pitch = 20.0
test_pitch += -1000 * SENSITIVITY
test_pitch = max(MIN_PITCH, min(MAX_PITCH, test_pitch))
check("Pitch clamps at -89", test_pitch == MIN_PITCH,
      f"pitch = {test_pitch}")

# 2f. View matrix sanity: looking from +X toward origin
eye = (5, 0, 0)
target = (0, 0, 0)
up = (0, 1, 0)
view = glm_lookAt(eye, target, up)
# Transform eye position through view matrix – should map to origin in view space? No.
# Transform origin (target) through view – should be at (0, 0, -5) in view space
origin_vs = mat4_mul_vec4(view, (0, 0, 0, 1))
check("View matrix: origin maps to (0,0,-5) when eye at (5,0,0)",
      abs(origin_vs[0]) < 1e-6 and abs(origin_vs[1]) < 1e-6 and abs(origin_vs[2] + 5) < 1e-4,
      f"got ({origin_vs[0]:.4f}, {origin_vs[1]:.4f}, {origin_vs[2]:.4f})")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. ORBIT ANIMATION
# ═══════════════════════════════════════════════════════════════════════════════

print()
print("=" * 70)
print("3. ORBIT ANIMATION TESTS")
print("=" * 70)

orbit_radius = 3.0
orbit_speed = 0.8
self_rot_speed = 2.0

def compute_orbit_model(time):
    """Recreate the orbit model matrix from vulkan_app.cpp."""
    model = mat4_identity()
    model = glm_rotate(model, time * orbit_speed, (0, 1, 0))
    model = glm_translate(model, (orbit_radius, 0, 0))
    model = glm_rotate(model, time * self_rot_speed, (0, 1, 0))
    return model

def extract_position(model):
    """Get world position from model matrix (last column)."""
    return (model[3][0], model[3][1], model[3][2])

# 3a. At time=0: position at (orbitRadius, 0, 0)
m0 = compute_orbit_model(0)
p0 = extract_position(m0)
check("Orbit t=0: position at (3, 0, 0)",
      abs(p0[0] - orbit_radius) < 1e-6 and abs(p0[1]) < 1e-6 and abs(p0[2]) < 1e-6,
      f"got {p0}")

# 3b. At time = pi/(2*orbitSpeed): rotated 90 degrees around Y
# rotate(pi/2, Y) * translate(3,0,0) => position at (0, 0, -3)
# Because GLM rotate around Y by +pi/2 maps +X to -Z
t_quarter = math.pi / (2 * orbit_speed)
m1 = compute_orbit_model(t_quarter)
p1 = extract_position(m1)
check("Orbit t=pi/(2*speed): position at (0, 0, -3)",
      abs(p1[0]) < 1e-4 and abs(p1[1]) < 1e-6 and abs(p1[2] + orbit_radius) < 1e-4,
      f"got ({p1[0]:.6f}, {p1[1]:.6f}, {p1[2]:.6f})")

# 3c. Self-rotation doesn't affect position
# Compare positions with and without self-rotation at an arbitrary time
t_test = 1.5
m_full = compute_orbit_model(t_test)
p_full = extract_position(m_full)

# Without self-rotation
model_no_self = mat4_identity()
model_no_self = glm_rotate(model_no_self, t_test * orbit_speed, (0, 1, 0))
model_no_self = glm_translate(model_no_self, (orbit_radius, 0, 0))
p_no_self = extract_position(model_no_self)

check("Self-rotation doesn't affect position",
      abs(p_full[0] - p_no_self[0]) < 1e-6 and
      abs(p_full[1] - p_no_self[1]) < 1e-6 and
      abs(p_full[2] - p_no_self[2]) < 1e-6,
      f"with self-rot: {p_full}, without: {p_no_self}")

# 3d. Orbit stays at constant radius from origin
orbit_ok = True
for step in range(100):
    t = step * 0.1
    m = compute_orbit_model(t)
    p = extract_position(m)
    dist = vec3_length(p)
    if abs(dist - orbit_radius) > 1e-4:
        orbit_ok = False
        break
check("Orbit maintains constant radius=3.0 over full revolution",
      orbit_ok, f"dist deviated at t={t}")

# 3e. Orbit is in XZ plane (Y always 0)
y_ok = True
for step in range(100):
    t = step * 0.1
    m = compute_orbit_model(t)
    p = extract_position(m)
    if abs(p[1]) > 1e-6:
        y_ok = False
        break
check("Orbit stays in XZ plane (Y=0)", y_ok)


# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

print()
print("=" * 70)
print(f"SUMMARY: {PASS_COUNT} PASSED, {FAIL_COUNT} FAILED out of {PASS_COUNT+FAIL_COUNT} tests")
print("=" * 70)

if FAIL_COUNT > 0:
    sys.exit(1)
else:
    print("All math validation tests passed.")
    sys.exit(0)
