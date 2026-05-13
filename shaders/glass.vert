#version 330 core
in vec3 in_vert;
in vec3 in_normal;

uniform mat4 u_vp;
uniform mat4 u_flip;

out vec3 v_normal_w;
out vec3 v_world;

void main() {
    vec4 wp     = u_flip * vec4(in_vert, 1.0);
    gl_Position = u_vp * wp;
    v_normal_w  = mat3(u_flip) * in_normal;
    v_world     = wp.xyz;
}