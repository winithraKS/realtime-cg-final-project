#version 330 core
in vec3 in_vert;
in vec3 in_normal;
in vec3 in_offset;
in float in_speed;
in float in_radius;

uniform mat4 u_vp;
uniform mat4 u_flip;
uniform vec3 u_cam_pos;

out vec3 v_normal_w;
out vec3 v_world;
out float v_speed;

void main() {
    vec4 world_pos = vec4(in_vert * in_radius + in_offset, 1.0);
    gl_Position  = u_vp * world_pos;
    v_normal_w   = mat3(u_flip) * in_normal;
    v_world      = world_pos.xyz;
    v_speed      = in_speed;
}