"""
Real-Time 3D Hourglass Simulation  –  hourglass3.py
====================================================
Key improvement: F now physically ROTATES the hourglass 180° around the Z-axis.
Gravity always points world-down.  Particle physics runs in the hourglass's LOCAL
frame so cone collision stays simple, then positions are rotated back to world
space for rendering.  Result: you see the glass barrel-roll over, sand tumbles
to the new "bottom" wall, then falls normally once upright.

Controls
--------
  Drag mouse   – orbit camera
  Scroll wheel – zoom
  F            – flip hourglass (180° rotation)
  R            – reset particles to upper chamber
  Space        – pause / resume
  Esc          – quit
"""

import math
import numpy as np
import pygame
import moderngl
import pyrr
from pygame.locals import *

# ───────────────────────────── CONFIG ──────────────────────────────
WINDOW_W, WINDOW_H = 900, 700
FPS_CAP            = 60
NUM_PARTICLES      = 200
DT                 = 0.016
RESTITUTION        = 0.15
FRICTION           = 0.96
PARTICLE_RADIUS    = 0.04
Y_NECK             = 0.0
R_NECK             = 0.1
Y_TOP              = 0.8
Y_BOT              = -1.2
R_CONE             = 0.5
CELL_SIZE          = PARTICLE_RADIUS * 2.5
FLIP_DURATION      = 5          # seconds for one 180 deg flip
WORLD_GRAVITY      = np.array([0.0, -9.8, 0.0], dtype=np.float64)
SUB_STEPS          = 5          # physics sub-steps per frame (for stability)
SUB_STEPS_2        = 3          # collision passes per sub-step (for stability)
PUSH_FACTOR        = 0.8        # how much to separate overlapping particles

# MARK: Shaders
# ───────────────────────────── SHADERS ─────────────────────────────
def load_shader(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        return f.read()
    
VERT_SRC = load_shader("shaders/particle.vert")
FRAG_SRC = load_shader("shaders/particle.frag")
GLASS_VERT = load_shader("shaders/glass.vert")
GLASS_FRAG = load_shader("shaders/glass.frag")

# MARK: Mesh
# ──────────────────────── GEOMETRY HELPERS ─────────────────────────
def make_hourglass_mesh(segments=72):
    verts, norms = [], []

    def cone_section(y_bot, r_bot, y_top, r_top):
        slope_y = r_bot - r_top
        slope_r = y_top - y_bot
        nlen    = math.sqrt(slope_y**2 + slope_r**2) + 1e-9
        ny_base = slope_r / nlen
        nr_base = slope_y / nlen
        for j in range(segments):
            a0 = 2*math.pi*j/segments
            a1 = 2*math.pi*(j+1)/segments
            p00 = [r_bot*math.cos(a0), y_bot, r_bot*math.sin(a0)]
            p10 = [r_top*math.cos(a0), y_top, r_top*math.sin(a0)]
            p01 = [r_bot*math.cos(a1), y_bot, r_bot*math.sin(a1)]
            p11 = [r_top*math.cos(a1), y_top, r_top*math.sin(a1)]
            n00 = [nr_base*math.cos(a0), ny_base, nr_base*math.sin(a0)]
            n10 = [nr_base*math.cos(a0), ny_base, nr_base*math.sin(a0)]
            n01 = [nr_base*math.cos(a1), ny_base, nr_base*math.sin(a1)]
            n11 = [nr_base*math.cos(a1), ny_base, nr_base*math.sin(a1)]
            for p, n in [(p00,n00),(p10,n10),(p11,n11),(p00,n00),(p11,n11),(p01,n01)]:
                verts.extend(p); norms.extend(n)

    cone_section(Y_NECK, R_NECK, Y_TOP,  R_CONE)
    cone_section(Y_BOT,  R_CONE, Y_NECK, R_NECK)

    def disc(y, r, up):
        ny = 1.0 if up else -1.0
        for j in range(segments):
            a0 = 2*math.pi*j/segments
            a1 = 2*math.pi*(j+1)/segments
            p0 = [r*math.cos(a0), y, r*math.sin(a0)]
            p1 = [r*math.cos(a1), y, r*math.sin(a1)]
            pc = [0.0, y, 0.0]
            tri = [pc, p0, p1] if up else [pc, p1, p0]
            for p in tri:
                verts.extend(p); norms.extend([0, ny, 0])
    disc(Y_TOP, R_CONE, True)
    disc(Y_BOT, R_CONE, False)

    return np.array(verts, np.float32), np.array(norms, np.float32)

def make_sphere(radius=1.0, stacks=10, slices=14):
    verts, norms = [], []
    for i in range(stacks):
        phi0 = math.pi * i / stacks - math.pi / 2
        phi1 = math.pi * (i + 1) / stacks - math.pi / 2
        for j in range(slices):
            th0 = 2 * math.pi * j / slices
            th1 = 2 * math.pi * (j + 1) / slices
            def pt(phi, th):
                return [radius*math.cos(phi)*math.cos(th),
                        radius*math.sin(phi),
                        radius*math.cos(phi)*math.sin(th)]
            p00, p10 = pt(phi0, th0), pt(phi1, th0)
            p01, p11 = pt(phi0, th1), pt(phi1, th1)
            for p in [p00, p10, p11, p00, p11, p01]:
                verts.extend(p)
                norms.extend([v / radius for v in p])
    return np.array(verts, np.float32), np.array(norms, np.float32)

# MARK: Helpers
# ──────────────────────── HOURGLASS BOUNDS ─────────────────────────
sin_top = Y_TOP / math.sqrt((R_CONE - R_NECK)**2 + Y_TOP**2)
sin_bot = abs(Y_BOT) / math.sqrt((R_CONE - R_NECK)**2 + Y_BOT**2)
print(f"Hourglass cone slope check: top={sin_top:.3f}  bot={sin_bot:.3f}  neck={R_NECK:.3f}")
def hourglass_radius_at(y, r = PARTICLE_RADIUS):
    if y >= Y_NECK:
        t = (y - Y_NECK) / (Y_TOP - Y_NECK + 1e-9)
        a = r / sin_top
    else:
        t = (Y_NECK - y) / (Y_NECK - Y_BOT + 1e-9)
        a = r / sin_bot
    return R_NECK + t * (R_CONE - R_NECK) - a


def clamp_to_hourglass(pos, vel, r = PARTICLE_RADIUS):
    """Collision in LOCAL hourglass frame (Y is hourglass long axis)."""
    x, y, z = pos
    if y - r < Y_BOT:
        y = Y_BOT + r
        vel[1] = abs(vel[1]) * RESTITUTION
        vel[0] *= FRICTION; vel[2] *= FRICTION
    if y + r > Y_TOP:
        y = Y_TOP - r
        vel[1] = -abs(vel[1]) * RESTITUTION
        vel[0] *= FRICTION; vel[2] *= FRICTION
    r_lim = hourglass_radius_at(y, r)
    if r_lim <= 0: return
    xz_d  = math.sqrt(x*x + z*z)
    if xz_d > r_lim:
        nx, nz = x/(xz_d+1e-9), z/(xz_d+1e-9)
        x, z   = nx*r_lim, nz*r_lim
        rv     = vel[0]*nx + vel[2]*nz
        if rv > 0:
            vel[0] -= (1+RESTITUTION)*rv*nx
            vel[2] -= (1+RESTITUTION)*rv*nz
        vel[0] *= FRICTION; vel[2] *= FRICTION
    pos[0], pos[1], pos[2] = x, y, z


# ──────────────────────── ROTATION HELPERS ─────────────────────────
def rot_z_mat4(angle_rad):
    """4x4 rotation matrix around Z axis (column-major, for OpenGL)."""
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return np.array([
        [ c, -s, 0, 0],
        [ s,  c, 0, 0],
        [ 0,  0, 1, 0],
        [ 0,  0, 0, 1],
    ], dtype=np.float32)

# ───────────────────────── SPATIAL HASH ────────────────────────────
class SpatialHash:
    def __init__(self, cell_size):
        self.cs    = cell_size
        self.table = {}

    def _key(self, p):
        return (int(math.floor(p[0]/self.cs)),
                int(math.floor(p[1]/self.cs)),
                int(math.floor(p[2]/self.cs)))

    def build(self, positions):
        self.table.clear()
        for i, p in enumerate(positions):
            k = self._key(p)
            self.table.setdefault(k, []).append(i)

    def neighbors(self, pos):
        cx, cy, cz = self._key(pos)
        out = []
        for dx in (-1,0,1):
            for dy in (-1,0,1):
                for dz in (-1,0,1):
                    k = (cx+dx, cy+dy, cz+dz)
                    if k in self.table:
                        out.extend(self.table[k])
        return out

# MARK: Particle
# ──────────────────────── PARTICLE SYSTEM (Update) ──────────────────────────
class ParticleSystem:
    def __init__(self, n):
        self.n = n
        # เปลี่ยนมาเก็บพิกัดแบบ LOCAL space เป็นหลัก (นาฬิกาทรายตั้งตรงเสมอในหน่วยความจำ)
        self.pos  = np.zeros((n, 3), dtype=np.float64)
        self.vel  = np.zeros((n, 3), dtype=np.float64)
        self.prev = np.zeros((n, 3), dtype=np.float64)
        self.hash = SpatialHash(CELL_SIZE)
        self.radius = np.random.uniform(
            PARTICLE_RADIUS * 0.9,
            PARTICLE_RADIUS * 1.1,
            self.n
        )
        self.reset()

    # MARK: Step()
    def step(self, flip_angle: float):

        # LOCAL GRAVITY
        c_inv = math.cos(-flip_angle)
        s_inv = math.sin(-flip_angle)

        R_inv = np.array([
            [ c_inv, -s_inv, 0.0],
            [ s_inv,  c_inv, 0.0],
            [  0.0 ,   0.0 , 1.0],
        ], dtype=np.float64)

        g_local = R_inv @ WORLD_GRAVITY

        # SUBSTEPS
        sub_dt = DT / SUB_STEPS
        for _ in range(SUB_STEPS):

            old_pos = self.pos.copy()

            # VERLET INTEGRATION
            self.pos += ( (self.pos - self.prev) + g_local * (sub_dt * sub_dt))
            self.prev[:] = old_pos

            # BUILD HASH
            self.hash.build(self.pos)

            # COLLISION ITERATIONS
            for _ in range(SUB_STEPS_2):

                # PARTICLE COLLISION
                for i in range(self.n):

                    neighbors = self.hash.neighbors(self.pos[i])

                    for j in neighbors:

                        if j <= i: continue

                        delta = self.pos[i] - self.pos[j]
                        dist_sq = np.dot(delta, delta)
                        if dist_sq < 1e-8: continue

                        min_d = self.radius[i] + self.radius[j]
                        if dist_sq < (min_d * min_d):
                            dist = math.sqrt(dist_sq)
                            n_hat = delta / dist
                            overlap = min_d - dist
                            SLOP = 0.002
                            if overlap < SLOP: continue

                            # POSITION CORRECTION
                            correction = n_hat * (overlap - SLOP) * 0.5

                            self.pos[i] += correction
                            self.pos[j] -= correction

                            # RELATIVE VELOCITY
                            rel_vel = self.vel[i] - self.vel[j]

                            # TANGENTIAL VELOCITY
                            tangent_vel = ( rel_vel - np.dot(rel_vel, n_hat) * n_hat )
                            tangent_speed = np.linalg.norm(tangent_vel)

                            if tangent_speed < 0.02: tangent_vel[:] = 0.0

                            # STATIC / DYNAMIC FRICTION
                            friction_strength = 0.12
                            self.vel[i] -= tangent_vel * friction_strength
                            self.vel[j] += tangent_vel * friction_strength

                # WALL COLLISION
                for i in range(self.n):
                    clamp_to_hourglass( self.pos[i], self.vel[i], self.radius[i])

            # FINAL VELOCITY UPDATE
            new_vel = (self.pos - self.prev) / sub_dt
            self.vel = self.vel * 0.1 + new_vel * 0.9

            # GLOBAL DAMPING
            damping = 0.99
            self.vel *= damping

            # RESYNC VERLET
            self.prev[:] = ( self.pos - self.vel * sub_dt )

    def reset(self):
        rng = np.random.default_rng(411)

        self.pos[:] = 0.0
        self.vel[:] = 0.0

        for i in range(self.n):
            radius = self.radius[i]
            placed = False

            for _ in range(100):
                y = rng.uniform(0.3, Y_TOP - radius)
                r_lim = max( hourglass_radius_at(y, radius), radius)

                r = rng.uniform(0, r_lim)
                a = rng.uniform(0, 2 * math.pi)
                candidate = np.array([ r * math.cos(a), y, r * math.sin(a) ])

                ok = True
                for j in range(i):
                    min_d = ( self.radius[i] + self.radius[j] )
                    delta = candidate - self.pos[j]
                    if np.dot(delta, delta) < (min_d * min_d):
                        ok = False
                        break

                if ok:
                    self.pos[i] = candidate
                    placed = True
                    break

            if not placed: self.pos[i] = [0, Y_TOP - radius, 0]

        self.prev[:] = self.pos.copy()

    # เพิ่ม Property เพื่อส่งค่าไป Render (หมุนเฉพาะตอนวาดภาพ)
    def get_world_pos_f32(self, flip_angle):
        c, s = math.cos(flip_angle), math.sin(flip_angle)
        R = np.array([
            [ c, -s, 0.0],
            [ s,  c, 0.0],
            [ 0.0, 0.0, 1.0],
        ], dtype=np.float32)
        # นำ Local Pos มาหมุนให้ตรงกับมุมบนจอภาพ
        return (R @ self.pos.T).T.astype(np.float32)
    
    @property
    def speeds_f32(self):
        return np.linalg.norm(self.vel, axis=1).astype(np.float32)

# ── HUD overlay shaders ──
HUD_VERT = """
#version 330 core
in vec2 in_pos;
in vec2 in_uv;
out vec2 v_uv;
void main() {
    gl_Position = vec4(in_pos, 0.0, 1.0);
    v_uv = in_uv;
}
"""

HUD_FRAG = """
#version 330 core
in vec2 v_uv;
uniform sampler2D u_tex;
out vec4 out_color;
void main() {
    out_color = texture(u_tex, v_uv);
}
"""

# MARK: HUD
# ──────────────────────────── HUD RENDERER ─────────────────────────
class HudRenderer:
    """Renders Pygame text to an OpenGL texture, blitted as a fullscreen quad.
    Works correctly with DOUBLEBUF|OPENGL where screen.blit() has no effect."""
    def __init__(self, ctx, width, height):
        self.ctx    = ctx
        self.w      = width
        self.h      = height
        self.font   = pygame.font.SysFont("monospace", 18, bold=True)
        self.prog   = ctx.program(vertex_shader=HUD_VERT, fragment_shader=HUD_FRAG)
        # Fullscreen quad in NDC, UV flipped vertically (Pygame origin top-left)
        quad = np.array([
            -1,-1, 0,1,   1,-1, 1,1,   1,1, 1,0,
            -1,-1, 0,1,   1, 1, 1,0,  -1,1, 0,0,
        ], dtype=np.float32)
        vbo = ctx.buffer(quad.tobytes())
        self.vao = ctx.vertex_array(self.prog, [(vbo, '2f 2f', 'in_pos', 'in_uv')])
        self.tex  = ctx.texture((width, height), 4)
        self.tex.filter = moderngl.LINEAR, moderngl.LINEAR

    def draw(self, lines):
        surf = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        surf.fill((0, 0, 0, 0))
        y = 12
        for line in lines:
            # Shadow pass
            shadow = self.font.render(line, True, (0, 0, 0))
            surf.blit(shadow, (12, y + 1))
            # Main text
            text = self.font.render(line, True, (230, 215, 170))
            surf.blit(text, (11, y))
            y += 26
        raw = pygame.image.tostring(surf, "RGBA", False)
        self.tex.write(raw)
        self.ctx.disable(moderngl.DEPTH_TEST)
        self.tex.use(0)
        self.prog['u_tex'].value = 0
        self.vao.render(moderngl.TRIANGLES)
        self.ctx.enable(moderngl.DEPTH_TEST)

def reset_camera():
    return 0.0, 5.0, 4.2, 0.0, 0.0

# MARK: Main
# ──────────────────────────── MAIN ─────────────────────────────────
def main():
    pygame.init()
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
    screen = pygame.display.set_mode(
        (WINDOW_W, WINDOW_H), DOUBLEBUF | OPENGL | RESIZABLE
    )
    pygame.display.set_caption("Real-Time 3D Hourglass  –  F = flip  R = reset")
    ctx = moderngl.create_context()
    ctx.enable(moderngl.DEPTH_TEST)
    ctx.enable(moderngl.BLEND)
    ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA

    prog       = ctx.program(vertex_shader=VERT_SRC,   fragment_shader=FRAG_SRC)
    glass_prog = ctx.program(vertex_shader=GLASS_VERT, fragment_shader=GLASS_FRAG)

    sv, sn       = make_sphere(1.0, stacks=8, slices=12)
    sphere_vbo   = ctx.buffer(sv.tobytes())
    sphere_nbo   = ctx.buffer(sn.tobytes())
    inst_pos_buf = ctx.buffer(reserve=NUM_PARTICLES * 12)
    inst_spd_buf = ctx.buffer(reserve=NUM_PARTICLES * 4)
    inst_radius_buf = ctx.buffer(reserve=NUM_PARTICLES * 4)

    vao = ctx.vertex_array(prog, [
        (sphere_vbo,   '3f',   'in_vert'),
        (sphere_nbo,   '3f',   'in_normal'),
        (inst_pos_buf, '3f/i', 'in_offset'),
        (inst_spd_buf, '1f/i', 'in_speed'),
        (inst_radius_buf, '1f/i', 'in_radius'),
    ])

    hv, hn    = make_hourglass_mesh(segments=72)
    glass_vao = ctx.vertex_array(glass_prog, [
        (ctx.buffer(hv.tobytes()), '3f', 'in_vert'),
        (ctx.buffer(hn.tobytes()), '3f', 'in_normal'),
    ])

    particles = ParticleSystem(NUM_PARTICLES)

    # camera
    cam_yaw, cam_pitch, cam_dist, pan_x, pan_y = reset_camera()
    dragging, last_mouse = False, (0, 0)

    # flip state
    flip_angle  = 0.0            # current hourglass Z-rotation (radians)
    flip_target = 0.0            # target angle (advances by pi each press)
    flip_speed  = math.pi / FLIP_DURATION   # rad/s

    paused = False
    clock  = pygame.time.Clock()
    hud    = HudRenderer(ctx, WINDOW_W, WINDOW_H)

    def get_vp():
        nonlocal pan_x, pan_y
        w, h   = pygame.display.get_surface().get_size()
        aspect = w / max(h, 1)
        proj   = pyrr.matrix44.create_perspective_projection(
                    45, aspect, 0.1, 100.0, dtype=np.float32)
        yr = math.radians(cam_yaw)
        pr = math.radians(cam_pitch)
        cx = cam_dist * math.cos(pr) * math.sin(yr) + pan_x
        cy = cam_dist * math.sin(pr)                + pan_y
        cz = cam_dist * math.cos(pr) * math.cos(yr)
        eye    = pyrr.Vector3([cx, cy, cz])
        target = pyrr.Vector3([pan_x, pan_y, 0.0])
        view = pyrr.matrix44.create_look_at(
                    eye, target, pyrr.Vector3([0,1,0]),
                    dtype=np.float32)
        vp = pyrr.matrix44.multiply(view, proj)
        return vp, np.array([cx, cy, cz], dtype=np.float32)
    
    def get_hud_lines(fps):
        flipping = not math.isclose(flip_angle, flip_target, abs_tol=0.01)
        status   = "FLIPPING..." if flipping else ("PAUSED" if paused else "running")
        return [
            f"FPS: {fps:.0f}   Particles: {NUM_PARTICLES}   [{status}]",
            f"Angle: {math.degrees(flip_angle % (2*math.pi)):.1f} deg",
            "Drag=orbit  Scroll=zoom  F=flip  R=reset  Space=pause",
        ]
    
    # MARK: Loop
    running = True
    while running:
        dt_real = clock.tick(FPS_CAP) / 1000.0
        fps     = clock.get_fps()

        for ev in pygame.event.get():
            if ev.type == QUIT:
                running = False
            elif ev.type == KEYDOWN:
                if ev.key == K_ESCAPE:
                    running = False
                elif ev.key == K_r:
                    particles.reset()
                    flip_angle  = 0.0
                    flip_target = 0.0
                    cam_yaw, cam_pitch, cam_dist, pan_x, pan_y = reset_camera()
                elif ev.key == K_SPACE:
                    paused = not paused
                elif ev.key == K_f:
                    flip_target += math.pi   # queue next 180 deg rotation
            elif ev.type == MOUSEBUTTONDOWN:
                if ev.button == 1:
                    dragging, last_mouse = True, ev.pos
                elif ev.button == 4:
                    cam_dist = max(1.5, cam_dist - 0.2)
                elif ev.button == 5:
                    cam_dist = min(12.0, cam_dist + 0.2)
            elif ev.type == MOUSEBUTTONUP:
                if ev.button == 1:
                    dragging = False
            elif ev.type == MOUSEMOTION and dragging:
                dx = ev.pos[0] - last_mouse[0]
                dy = ev.pos[1] - last_mouse[1]
                if pygame.key.get_pressed()[K_LSHIFT]:
                    # Shift+drag: pan camera target in screen-aligned XY plane
                    pan_scale = cam_dist * 0.0012
                    yr = math.radians(cam_yaw)
                    pr = math.radians(cam_pitch)
                    right_x =  math.cos(yr)   # screen-right in world X
                    up_y    =  math.cos(pr)   # screen-up in world Y
                    pan_x  += -dx * right_x * pan_scale
                    pan_y  +=  dy * up_y    * pan_scale
                else:
                    cam_yaw  += dx * 0.4
                    cam_pitch = max(-80, min(80, cam_pitch - dy*0.4))
                last_mouse = ev.pos

        # Advance flip rotation smoothly
        diff = flip_target - flip_angle
        if abs(diff) > 0.001:
            step = flip_speed * dt_real
            flip_angle += math.copysign(min(step, abs(diff)), diff)

        # Physics — world positions, local collision, world output
        if not paused:
            particles.step(flip_angle)

        # Render
        ctx.clear(0.07, 0.06, 0.08, 1.0)
        vp, cam_pos = get_vp()
        light       = np.array([3.0, 5.0, 4.0], dtype=np.float32)
        flip_mat    = rot_z_mat4(flip_angle).T

        # Particles (already in world space)
        world_pos_data = particles.get_world_pos_f32(flip_angle)
        inst_pos_buf.write(world_pos_data.tobytes())
        # inst_pos_buf.write(particles.world_pos_f32.tobytes())
        inst_radius_buf.write(particles.radius.astype(np.float32).tobytes())
        inst_spd_buf.write(particles.speeds_f32.tobytes())
        prog['u_vp'].write(vp.tobytes())
        prog['u_flip'].write(flip_mat.tobytes())
        prog['u_light'].write(light.tobytes())
        prog['u_cam_pos'].write(cam_pos.tobytes())
        vao.render(moderngl.TRIANGLES, instances=NUM_PARTICLES)

        # Glass shell (two-pass for transparency)
        glass_prog['u_vp'].write(vp.tobytes())
        glass_prog['u_flip'].write(flip_mat.tobytes())
        glass_prog['u_cam_pos'].write(cam_pos.tobytes())
        ctx.front_face = 'cw'
        glass_vao.render(moderngl.TRIANGLES)
        ctx.front_face = 'ccw'
        glass_vao.render(moderngl.TRIANGLES)

        hud.draw(get_hud_lines(fps))
        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()