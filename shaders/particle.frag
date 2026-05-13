#version 330 core
in vec3 v_normal_w;
in vec3 v_world;
in float v_speed;

uniform vec3 u_light;
uniform vec3 u_cam_pos;

out vec4 out_color;

void main() {
    float t   = clamp(v_speed / 4.5, 0.0, 1.0);
    vec3 slow = vec3(0.94, 0.78, 0.32);
    vec3 fast = vec3(1.00, 0.42, 0.08);
    vec3 base = mix(slow, fast, t);

    vec3 N = normalize(v_normal_w);
    vec3 L = normalize(u_light - v_world);
    vec3 V = normalize(u_cam_pos - v_world);
    vec3 H = normalize(L + V);

    float diff = max(dot(N, L), 0.0) * 0.72 + 0.28;
    float spec = pow(max(dot(N, H), 0.0), 36.0) * 0.55;
    float rim  = pow(1.0 - max(dot(N, V), 0.0), 2.8) * 0.22;

    out_color = vec4(base * diff + vec3(spec) + base * rim, 1.0);
}