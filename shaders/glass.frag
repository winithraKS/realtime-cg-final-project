#version 330 core
in vec3 v_normal_w;
in vec3 v_world;

uniform vec3 u_cam_pos;

out vec4 out_color;

void main() {
    vec3 N = normalize(v_normal_w);
    vec3 V = normalize(u_cam_pos - v_world);
    float rim     = pow(1.0 - max(dot(N, V), 0.0), 3.2);
    float fresnel = 0.04 + 0.96 * rim;
    vec3  glass   = vec3(0.70, 0.91, 0.97);
    out_color = vec4(glass, fresnel * 0.38 + 0.04);
}