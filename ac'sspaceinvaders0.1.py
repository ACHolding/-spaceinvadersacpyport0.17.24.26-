"""
SPACE INVADERS - py port by ac 0.1
A Famicom-style Space Invaders port built with Python + pygame.

Improvements:
  - Persistent high score (JSON file, robust load/save)
  - Smarter alien AI (weighted column selection biased toward player X)
  - Light combo system (rapid successive kills award bonus points)
  - "NEW HIGH SCORE!" flash feedback
  - Slightly more aggressive late-wave fire scaling
  - All original visuals, sounds, controls, particles, shields preserved

Controls:
  Left/Right or A/D : Move
  SPACE / UP        : Fire
  P                 : Pause
  R                 : Toggle reduced motion
  M                 : Mute audio
  F1                : Debug overlay
  ESC               : Pause / Back
  ENTER             : Start / Restart
"""

import pygame
import numpy as np
import random
import math
import json
import os
from dataclasses import dataclass
from enum import Enum, auto

# ============================================================
# CONFIG
# ============================================================
SEED = 1337
random.seed(SEED)
np.random.seed(SEED)

WIDTH, HEIGHT = 448, 528
FPS = 60

C_BG       = (8, 8, 20)
C_GREEN    = (88, 220, 88)
C_RED      = (230, 60, 60)
C_YELLOW   = (245, 220, 80)
C_CYAN     = (90, 200, 240)
C_WHITE    = (240, 240, 240)
C_GRAY     = (130, 130, 130)
C_ORANGE   = (240, 140, 40)

HISCORE_FILE = "space_invaders_hiscore.json"

# ============================================================
# SPRITE PATTERNS (X = pixel on)
# ============================================================
SPR_SQUID = [
    ["..XX..", ".XXXX.", "XXXXXX", "X.XX.X",
     "XXXXXX", ".X..X.", "X.XX.X", "X.XX.X"],
    ["..XX..", ".XXXX.", "XXXXXX", "X.XX.X",
     "XXXXXX", "..XX..", ".X..X.", "X....X"],
]
SPR_CRAB = [
    [".X.....X.", "..X...X..", ".XXXXXXX.", "XX.XXX.XX",
     "XXXXXXXXX", "X.XXXXX.X", "X.X...X.X", "...XX.XX."],
    [".X.....X.", "X.X...X.X", "XXXXXXXXX", "XXX.X.XXX",
     "XXXXXXXXX", ".XXXXXXX.", "..X...X..", ".X.....X."],
]
SPR_OCTO = [
    ["...XXXX...", ".XXXXXXXX.", "XXXXXXXXXX", "XXX..XX..X",
     "XXXXXXXXXX", "..XX..XX..", ".XX.XX.XX.", "XX......XX"],
    ["...XXXX...", ".XXXXXXXX.", "XXXXXXXXXX", "XXX..XX..X",
     "XXXXXXXXXX", "...X..X...", "..X.XX.X..", ".X.X..X.X."],
]
SPR_PLAYER = [
    "......X......",
    ".....XXX.....",
    ".....XXX.....",
    ".XXXXXXXXXXX.",
    "XXXXXXXXXXXXX",
    "XXXXXXXXXXXXX",
    "XXXXXXXXXXXXX",
    "XXXXXXXXXXXXX",
]
SPR_UFO = [
    "....XXXXXX....",
    "..XXXXXXXXXX..",
    ".XXXXXXXXXXXX.",
    "XX.XX.XX.XX.XX",
    "XXXXXXXXXXXXXX",
    "..XXX..XXX....",
    "...X....X.....",
]
SPR_PLAYER_EXP = [
    "X..X.X..X.X..X",
    ".X.X..XX..X.X.",
    "..X.X.XX.X.X..",
    "X.XX.X..X.XX.X",
    ".X.X.X.X.X.X..",
    "X..X..X..X..X.",
    ".X..X.X.X..X.X",
    "X.X..X.X..X.X.",
]
SPR_ALIEN_EXP = [
    ".X...X...X.",
    "..X.X.X.X..",
    "X.XXXXXXX.X",
    ".XXX.X.XXX.",
    "XX.XXXXX.XX",
    ".XXX.X.XXX.",
    "X.XXXXXXX.X",
    "..X.X.X.X..",
    ".X...X...X.",
]
SHIELD_PATTERN = [
    "....XXXXXXXXXXXX....",
    "...XXXXXXXXXXXXXX...",
    "..XXXXXXXXXXXXXXXX..",
    ".XXXXXXXXXXXXXXXXXX.",
    "XXXXXXXXXXXXXXXXXXXX",
    "XXXXXXXXXXXXXXXXXXXX",
    "XXXXXXXXXXXXXXXXXXXX",
    "XXXXXXXXXXXXXXXXXXXX",
    "XXXXXXXXXXXXXXXXXXXX",
    "XXXXXXXXXXXXXXXXXXXX",
    "XXXXXXXXX..XXXXXXXXX",
    "XXXXXXXX....XXXXXXXX",
    "XXXXXXX......XXXXXXX",
    "XXXXXX........XXXXXX",
    "XXXXX..........XXXXX",
    "XXXXX..........XXXXX",
]

# ============================================================
# SPRITE GENERATION
# ============================================================
def make_sprite(pattern, color, scale=3):
    h = len(pattern)
    w = max(len(row) for row in pattern)
    surf = pygame.Surface((w * scale, h * scale), pygame.SRCALPHA)
    for y, row in enumerate(pattern):
        for x, ch in enumerate(row):
            if ch == 'X':
                pygame.draw.rect(surf, color,
                                 (x * scale, y * scale, scale, scale))
    return surf.convert_alpha()

# ============================================================
# SOUND SYNTHESIS
# ============================================================
class SoundBank:
    def __init__(self):
        self.muted = False
        self.enabled = True
        self.sr = 22050
        self.channels = 2
        try:
            mixer_spec = pygame.mixer.get_init()
            if mixer_spec is None:
                pygame.mixer.init(
                    frequency=self.sr, size=-16, channels=2, buffer=256)
                mixer_spec = pygame.mixer.get_init()
            if mixer_spec is None:
                raise pygame.error("The audio mixer could not be initialized")
            self.sr = mixer_spec[0]
            self.channels = mixer_spec[2]
            self.sounds = self._build()
        except Exception:
            self.enabled = False
            self.sounds = {}

    def _make_sound(self, wave):
        samples = np.clip(wave * 32767, -32768, 32767).astype(np.int16)
        if self.channels == 1:
            audio = samples
        else:
            audio = np.repeat(samples[:, np.newaxis], self.channels, axis=1)
        return pygame.sndarray.make_sound(np.ascontiguousarray(audio))

    def _wave(self, freq, dur, vol=0.4, decay=8, square=False):
        t = np.arange(int(self.sr * dur)) / self.sr
        wave = (np.sign(np.sin(2 * np.pi * freq * t)) if square
                else np.sin(2 * np.pi * freq * t))
        env = np.exp(-t * decay)
        wave = wave * env * vol
        return self._make_sound(wave)

    def _sweep(self, f0, f1, dur, vol=0.4, decay=4, square=False):
        t = np.arange(int(self.sr * dur)) / self.sr
        freq = f0 + (f1 - f0) * (t / dur)
        phase = 2 * np.pi * np.cumsum(freq) / self.sr
        wave = np.sign(np.sin(phase)) if square else np.sin(phase)
        env = np.exp(-t * decay)
        wave = wave * env * vol
        return self._make_sound(wave)

    def _noise(self, dur, vol=0.4, decay=8, rumble=0):
        t = np.arange(int(self.sr * dur)) / self.sr
        noise = np.random.uniform(-1, 1, len(t))
        env = np.exp(-t * decay)
        wave = noise * env * vol
        if rumble > 0:
            wave = wave + np.sin(2 * np.pi * rumble * t) * env * vol * 0.5
        return self._make_sound(wave)

    def _build(self):
        s = {}
        s['shoot'] = self._sweep(880, 220, 0.15, vol=0.2, decay=10, square=True)
        s['march1'] = self._wave(110, 0.08, vol=0.25, decay=20, square=True)
        s['march2'] = self._wave(98, 0.08, vol=0.25, decay=20, square=True)
        s['march3'] = self._wave(87, 0.08, vol=0.25, decay=20, square=True)
        s['march4'] = self._wave(73, 0.08, vol=0.25, decay=20, square=True)
        s['alien_boom'] = self._noise(0.2, vol=0.35, decay=12)
        s['player_boom'] = self._noise(0.6, vol=0.5, decay=5, rumble=60)
        s['ufo_hit'] = self._sweep(1200, 100, 0.4, vol=0.4, decay=5, square=True)
        s['level_up'] = self._sweep(440, 1320, 0.5, vol=0.3, decay=4, square=True)
        t = np.arange(int(self.sr * 0.4)) / self.sr
        freq = 400 + 200 * np.sin(2 * np.pi * 12 * t)
        phase = 2 * np.pi * np.cumsum(freq) / self.sr
        wave = np.sign(np.sin(phase)) * 0.2
        s['ufo'] = self._make_sound(wave)
        return s

    def play(self, name, volume=1.0):
        if not self.enabled or self.muted:
            return
        snd = self.sounds.get(name)
        if snd:
            snd.set_volume(volume)
            snd.play()

    def toggle_mute(self):
        self.muted = not self.muted
        if self.muted and self.enabled:
            pygame.mixer.stop()

# ============================================================
# ENTITIES
# ============================================================
@dataclass
class Bullet:
    x: float
    y: float
    vy: float
    friendly: bool
    alive: bool = True

@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    life: float
    max_life: float
    color: tuple
    size: float

@dataclass
class ScorePopup:
    x: float
    y: float
    text: str
    life: float = 0.8
    max_life: float = 0.8
    color: tuple = C_YELLOW

class ParticlePool:
    def __init__(self):
        self.particles = []

    def burst(self, x, y, color, count=12, speed=120, life=0.4, size=3):
        for _ in range(count):
            angle = random.uniform(0, 2 * math.pi)
            s = random.uniform(0.3, 1.0) * speed
            l = life * random.uniform(0.7, 1.2)
            self.particles.append(Particle(
                x=x, y=y,
                vx=math.cos(angle) * s,
                vy=math.sin(angle) * s,
                life=l, max_life=l,
                color=color,
                size=size * random.uniform(0.7, 1.3),
            ))

    def update(self, dt):
        damp = 0.94 ** (dt * 60)
        for p in self.particles:
            p.x += p.vx * dt
            p.y += p.vy * dt
            p.vx *= damp
            p.vy *= damp
            p.life -= dt
        self.particles = [p for p in self.particles if p.life > 0]

    def draw(self, screen):
        for p in self.particles:
            alpha = max(0, min(1, p.life / p.max_life))
            r, g, b = p.color
            color = (int(r * alpha), int(g * alpha), int(b * alpha))
            s = max(1, int(p.size * alpha))
            pygame.draw.rect(screen, color,
                             (int(p.x - s / 2), int(p.y - s / 2), s, s))

class Shield:
    def __init__(self, x, y, scale=2):
        self.x = x
        self.y = y
        self.scale = scale
        self.w = len(SHIELD_PATTERN[0])
        self.h = len(SHIELD_PATTERN)
        self.pixels = [[SHIELD_PATTERN[y][x] == 'X'
                        for x in range(self.w)]
                       for y in range(self.h)]
        self.surf = pygame.Surface((self.w * scale, self.h * scale),
                                   pygame.SRCALPHA)
        self.dirty = True

    def _redraw(self):
        self.surf.fill((0, 0, 0, 0))
        for y in range(self.h):
            for x in range(self.w):
                if self.pixels[y][x]:
                    pygame.draw.rect(self.surf, C_GREEN,
                                     (x * self.scale, y * self.scale,
                                      self.scale, self.scale))
        self.dirty = False

    def damage(self, hit_x, hit_y, radius=3):
        lx = int((hit_x - self.x) // self.scale)
        ly = int((hit_y - self.y) // self.scale)
        damaged = False
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy <= radius * radius:
                    px, py = lx + dx, ly + dy
                    if 0 <= px < self.w and 0 <= py < self.h:
                        if self.pixels[py][px]:
                            self.pixels[py][px] = False
                            damaged = True
        if damaged:
            self.dirty = True
        return damaged

    def check_hit(self, x, y):
        lx = int((x - self.x) // self.scale)
        ly = int((y - self.y) // self.scale)
        if 0 <= lx < self.w and 0 <= ly < self.h:
            return self.pixels[ly][lx]
        return False

    def draw(self, screen):
        if self.dirty:
            self._redraw()
        screen.blit(self.surf, (self.x, self.y))

class Player:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.w = 13 * 3
        self.h = 8 * 3
        self.speed = 200
        self.alive = True
        self.explode_timer = 0
        self.scale_x = 1.0
        self.scale_y = 1.0
        self.invuln_timer = 0
        self.sprite = make_sprite(SPR_PLAYER, C_CYAN, scale=3)
        self.explode_sprite = make_sprite(SPR_PLAYER_EXP, C_ORANGE, scale=3)

    def update(self, dt, keys):
        if not self.alive:
            self.explode_timer += dt
            return
        if self.invuln_timer > 0:
            self.invuln_timer -= dt
        dx = 0
        if keys[pygame.K_LEFT] or keys[pygame.K_a]:
            dx -= 1
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
            dx += 1
        self.x += dx * self.speed * dt
        self.x = max(8, min(WIDTH - self.w - 8, self.x))

    def update_squash(self, dt):
        self.scale_x += (1.0 - self.scale_x) * min(1, dt * 14)
        self.scale_y += (1.0 - self.scale_y) * min(1, dt * 14)

    def kill(self):
        if self.alive:
            self.alive = False
            self.explode_timer = 0

    def respawn(self, x):
        self.alive = True
        self.explode_timer = 0
        self.x = x
        self.scale_x = 1.0
        self.scale_y = 1.0
        self.invuln_timer = 1.5

    def draw(self, screen):
        if not self.alive:
            if int(self.explode_timer * 16) % 2 == 0:
                rect = self.explode_sprite.get_rect(
                    center=(self.x + self.w // 2,
                            self.y + self.h // 2))
                screen.blit(self.explode_sprite, rect)
            return
        if self.invuln_timer > 0 and int(self.invuln_timer * 16) % 2 == 0:
            return
        if (abs(self.scale_x - 1.0) > 0.01 or
                abs(self.scale_y - 1.0) > 0.01):
            sw = max(1, int(self.w * self.scale_x))
            sh = max(1, int(self.h * self.scale_y))
            scaled = pygame.transform.scale(self.sprite, (sw, sh))
            screen.blit(scaled,
                        (self.x + (self.w - sw) // 2,
                         self.y + (self.h - sh) // 2))
        else:
            screen.blit(self.sprite, (self.x, self.y))

class Formation:
    def __init__(self, sound, start_y=80):
        self.sound = sound
        self.cols = 11
        self.rows = 5
        self.cell_w = 32
        self.cell_h = 28
        self.x = 48
        self.y = start_y
        self.dir = 1
        self.step_timer = 0
        self.march_idx = 0
        self.aliens = []
        for row in range(self.rows):
            for col in range(self.cols):
                if row == 0:
                    kind = 'squid'; points = 30; pw = 6
                elif row < 3:
                    kind = 'crab'; points = 20; pw = 9
                else:
                    kind = 'octo'; points = 10; pw = 10
                self.aliens.append({
                    'col': col, 'row': row, 'kind': kind,
                    'points': points, 'alive': True,
                    'w': pw * 3, 'h': 24,
                })
        self.sprites = {
            'squid': (make_sprite(SPR_SQUID[0], C_GREEN, scale=3),
                      make_sprite(SPR_SQUID[1], C_GREEN, scale=3)),
            'crab':  (make_sprite(SPR_CRAB[0], C_GREEN, scale=3),
                      make_sprite(SPR_CRAB[1], C_GREEN, scale=3)),
            'octo':  (make_sprite(SPR_OCTO[0], C_GREEN, scale=3),
                      make_sprite(SPR_OCTO[1], C_GREEN, scale=3)),
        }
        self.explode_sprite = make_sprite(SPR_ALIEN_EXP, C_YELLOW, scale=3)
        self.explosions = []
        self.alive_count = self.cols * self.rows
        self.anim_frame = 0

    def alive_aliens(self):
        return [a for a in self.aliens if a['alive']]

    def alien_pos(self, a):
        return (self.x + a['col'] * self.cell_w +
                (self.cell_w - a['w']) // 2,
                self.y + a['row'] * self.cell_h)

    def step_interval(self):
        n = max(1, self.alive_count)
        return 0.04 + (n / 55.0) * 0.7

    def update(self, dt):
        for e in self.explosions[:]:
            e[2] += dt
            if e[2] > 0.25:
                self.explosions.remove(e)
        self.step_timer += dt
        if self.step_timer >= self.step_interval():
            self.step_timer = 0
            self._march_step()
            self.anim_frame = 1 - self.anim_frame

    def _march_step(self):
        aliens = self.alive_aliens()
        if not aliens:
            return
        min_x = min(self.alien_pos(a)[0] for a in aliens)
        max_x = max(self.alien_pos(a)[0] + a['w'] for a in aliens)
        step_x = 4
        drop_y = 12
        if self.dir > 0 and max_x + step_x > WIDTH - 8:
            self.y += drop_y
            self.dir = -1
        elif self.dir < 0 and min_x - step_x < 8:
            self.y += drop_y
            self.dir = 1
        else:
            self.x += self.dir * step_x
        self.march_idx = (self.march_idx + 1) % 4
        self.sound.play(f'march{self.march_idx + 1}')

    def kill_alien(self, a):
        a['alive'] = False
        self.alive_count -= 1
        pos = self.alien_pos(a)
        cx = pos[0] + a['w'] // 2
        cy = pos[1] + a['h'] // 2
        self.explosions.append([cx, cy, 0])

    def lowest_in_col(self, col):
        candidates = [a for a in self.aliens
                      if a['col'] == col and a['alive']]
        if not candidates:
            return None
        return max(candidates, key=lambda a: a['row'])

    def any_reached_player(self, player_y):
        for a in self.alive_aliens():
            _, y = self.alien_pos(a)
            if y + a['h'] >= player_y:
                return True
        return False

    def erode_shields(self, shields):
        for a in self.alive_aliens():
            ax, ay = self.alien_pos(a)
            if ay + a['h'] < 390:
                continue
            for s in shields:
                if (ay + a['h'] > s.y and ax + a['w'] > s.x and
                        ax < s.x + s.w * s.scale and
                        ay < s.y + s.h * s.scale):
                    for py in range(int(ay), int(ay + a['h']), 3):
                        for px in range(int(ax), int(ax + a['w']), 3):
                            s.damage(px, py, radius=1)

    def draw(self, screen):
        for a in self.aliens:
            if not a['alive']:
                continue
            x, y = self.alien_pos(a)
            screen.blit(self.sprites[a['kind']][self.anim_frame], (x, y))
        for e in self.explosions:
            rect = self.explode_sprite.get_rect(
                center=(int(e[0]), int(e[1])))
            screen.blit(self.explode_sprite, rect)

class UFO:
    def __init__(self):
        self.active = False
        self.x = 0
        self.y = 32
        self.dir = 1
        self.w = 14 * 3
        self.h = 7 * 3
        self.sprite = make_sprite(SPR_UFO, C_RED, scale=3)
        self.timer = 0
        self.next_spawn = random.uniform(15, 25)
        self.sound_timer = 0

    def update(self, dt, sound):
        if not self.active:
            self.timer += dt
            if self.timer >= self.next_spawn:
                self.active = True
                self.dir = random.choice([-1, 1])
                self.x = -self.w if self.dir > 0 else WIDTH
                self.timer = 0
                self.sound_timer = 0
            return
        self.x += self.dir * 90 * dt
        self.sound_timer += dt
        if self.sound_timer > 0.3:
            self.sound_timer = 0
            sound.play('ufo')
        if ((self.dir > 0 and self.x > WIDTH) or
                (self.dir < 0 and self.x < -self.w)):
            self.active = False
            self.timer = 0
            self.next_spawn = random.uniform(15, 25)

    def check_hit(self, bullet):
        if not self.active:
            return False
        return (bullet.x >= self.x and bullet.x <= self.x + self.w and
                bullet.y >= self.y and bullet.y <= self.y + self.h)

    def destroy(self):
        self.active = False
        self.timer = 0
        self.next_spawn = random.uniform(15, 25)

    def draw(self, screen):
        if self.active:
            screen.blit(self.sprite, (self.x, self.y))

# ============================================================
# GAME (FSM)
# ============================================================
class GameState(Enum):
    MENU = auto()
    PLAYING = auto()
    PAUSED = auto()
    GAME_OVER = auto()
    WAVE_CLEAR = auto()

class Game:
    def __init__(self):
        pygame.mixer.pre_init(22050, -16, 2, 256)
        pygame.init()
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption(
            "SPACE INVADERS - py port by ac 0.1")
        self.clock = pygame.time.Clock()
        self.sound = SoundBank()

        self.font_big = pygame.font.SysFont(
            "consolas,couriernew,monospace", 32, bold=True)
        self.font_med = pygame.font.SysFont(
            "consolas,couriernew,monospace", 16, bold=True)
        self.font_sm = pygame.font.SysFont(
            "consolas,couriernew,monospace", 12, bold=True)

        self.state = GameState.MENU
        self.reduced_motion = False
        self.debug = False
        self.hi_score = self._load_hi_score()

        self.mini_player = make_sprite(SPR_PLAYER, C_CYAN, scale=1)
        self.menu_sprites = {
            'squid': make_sprite(SPR_SQUID[0], C_GREEN, scale=2),
            'crab':  make_sprite(SPR_CRAB[0], C_GREEN, scale=2),
            'octo':  make_sprite(SPR_OCTO[0], C_GREEN, scale=2),
            'ufo':   make_sprite(SPR_UFO, C_RED, scale=2),
        }
        self._world_surf = pygame.Surface((WIDTH, HEIGHT)).convert()
        self.reset()

    def _load_hi_score(self):
        try:
            if os.path.exists(HISCORE_FILE):
                with open(HISCORE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return max(0, int(data.get("hi_score", 0)))
        except Exception:
            pass
        return 0

    def _save_hi_score(self):
        try:
            with open(HISCORE_FILE, "w", encoding="utf-8") as f:
                json.dump({"hi_score": int(self.hi_score)}, f)
        except Exception:
            pass

    def reset(self):
        self.score = 0
        self.lives = 3
        self.wave = 1
        self.shake_amp = 0
        self.shake_t = 0
        self.shake_max = 0.5
        self.hitstop = 0
        self.particles = ParticlePool()
        self.bullets = []
        self.player = Player(WIDTH // 2 - 20, HEIGHT - 50)
        self.formation = Formation(self.sound)
        self.ufo = UFO()
        self.shields = self._make_shields()
        self.popups = []
        self.alien_shoot_cd = 1.5
        self.shoot_buffer = False
        self.input_buffer_timer = 0
        self.wave_clear_timer = 0
        self.game_over_timer = 0
        self.game_over_pending = False
        # Combo system
        self.combo = 0
        self.last_kill_time = -10.0
        self.combo_window = 0.55
        # New hi-score flash
        self.new_hi_timer = 0.0
        self.new_hi_flash = False

    def _make_shields(self):
        shields = []
        n = 4
        sw = 20 * 2
        gap = (WIDTH - n * sw) // (n + 1)
        for i in range(n):
            x = gap * (i + 1) + sw * i
            y = HEIGHT - 130
            shields.append(Shield(x, y, scale=2))
        return shields

    def run(self):
        running = True
        while running:
            dt_raw = self.clock.tick(FPS) / 1000.0
            dt = min(dt_raw, 1 / 30)
            running = self._events()
            if self.hitstop > 0:
                self.hitstop -= dt_raw
                effective_dt = 0
            else:
                effective_dt = dt
            self._update(effective_dt)
            self._draw()
            pygame.display.flip()
        pygame.quit()

    def _events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if self.state == GameState.PLAYING:
                        self.state = GameState.PAUSED
                    elif self.state == GameState.PAUSED:
                        self.state = GameState.PLAYING
                    elif self.state == GameState.MENU:
                        return False
                    else:
                        self.state = GameState.MENU
                        self.reset()
                elif event.key == pygame.K_RETURN:
                    if self.state == GameState.MENU:
                        self.reset()
                        self.state = GameState.PLAYING
                    elif self.state == GameState.GAME_OVER:
                        self.reset()
                        self.state = GameState.PLAYING
                elif event.key == pygame.K_p:
                    if self.state == GameState.PLAYING:
                        self.state = GameState.PAUSED
                    elif self.state == GameState.PAUSED:
                        self.state = GameState.PLAYING
                elif event.key == pygame.K_m:
                    self.sound.toggle_mute()
                elif event.key == pygame.K_r:
                    self.reduced_motion = not self.reduced_motion
                    if self.reduced_motion:
                        self.shake_amp = 0
                        self.shake_t = 0
                elif event.key == pygame.K_F1:
                    self.debug = not self.debug
                elif event.key in (pygame.K_SPACE, pygame.K_UP):
                    if self.state == GameState.PLAYING:
                        self.shoot_buffer = True
                        self.input_buffer_timer = 0.14
        return True

    def _update(self, dt):
        if self.state != GameState.PAUSED:
            self.particles.update(dt)
            for popup in self.popups:
                popup.y -= 24 * dt
                popup.life -= dt
            self.popups = [p for p in self.popups if p.life > 0]
            if self.new_hi_timer > 0:
                self.new_hi_timer = max(0.0, self.new_hi_timer - dt)

        if self.shake_t > 0:
            self.shake_t = max(0, self.shake_t - dt)
            if self.shake_t == 0:
                self.shake_amp = 0

        if self.state == GameState.PLAYING:
            self._update_playing(dt)
        elif self.state == GameState.WAVE_CLEAR:
            self.wave_clear_timer += dt
            if self.wave_clear_timer >= 1.35:
                self._start_next_wave()
        elif self.state == GameState.GAME_OVER:
            self.game_over_timer += dt

    def _update_playing(self, dt):
        keys = pygame.key.get_pressed()
        self.player.update(dt, keys)
        self.player.update_squash(dt)

        if self.input_buffer_timer > 0:
            self.input_buffer_timer -= dt
        else:
            self.shoot_buffer = False
        if self.shoot_buffer:
            self._try_player_shoot()

        self.formation.update(dt)
        self.ufo.update(dt, self.sound)
        self._update_bullets(dt)
        self._resolve_collisions()
        self.formation.erode_shields(self.shields)

        if self.player.alive:
            self.alien_shoot_cd -= dt
            if self.alien_shoot_cd <= 0:
                self._alien_shoot()
        elif self.player.explode_timer >= 1.0:
            if self.game_over_pending:
                self.state = GameState.GAME_OVER
                self.game_over_timer = 0
                if self.score > self.hi_score:
                    self.hi_score = self.score
                    self._save_hi_score()
            else:
                self.player.respawn(WIDTH // 2 - self.player.w // 2)
                self.bullets = [b for b in self.bullets if b.friendly]
                self.combo = 0  # reset combo on death

        if (self.state == GameState.PLAYING and
                self.formation.any_reached_player(self.player.y)):
            self._invasion_game_over()

        if (self.state == GameState.PLAYING and
                self.formation.alive_count == 0):
            self.state = GameState.WAVE_CLEAR
            self.wave_clear_timer = 0
            self.bullets = [b for b in self.bullets if b.friendly]
            self.sound.play('level_up')

        # Live hi-score update
        if self.score > self.hi_score:
            self.hi_score = self.score
            self._save_hi_score()
            if not self.new_hi_flash:
                self.new_hi_flash = True
                self.new_hi_timer = 2.5

    def _try_player_shoot(self):
        if not self.player.alive:
            return
        if any(b.alive and b.friendly for b in self.bullets):
            return
        self.bullets.append(Bullet(
            self.player.x + self.player.w / 2,
            self.player.y - 5,
            -390,
            True,
        ))
        self.player.scale_x = 1.12
        self.player.scale_y = 0.82
        self.sound.play('shoot')
        self.shoot_buffer = False
        self.input_buffer_timer = 0

    def _choose_shoot_column(self, live_columns, player_cx):
        """Weighted random biased toward the player's X position."""
        if not live_columns:
            return None
        weights = []
        for col in live_columns:
            col_cx = (self.formation.x + col * self.formation.cell_w +
                      self.formation.cell_w // 2)
            dist = abs(col_cx - player_cx)
            # Soft inverse-distance
            base = 1.0 / (1.0 + (dist / 50.0) ** 0.85)
            # Aggression grows with wave and fewer remaining aliens
            aggression = 1.0 + self.wave * 0.12 + max(
                0.0, (40 - self.formation.alive_count) / 35.0)
            w = max(0.18, base * aggression)  # floor keeps classic chaos
            weights.append(w)
        return random.choices(live_columns, weights=weights, k=1)[0]

    def _alien_shoot(self):
        live_columns = sorted({
            a['col'] for a in self.formation.aliens if a['alive']
        })
        if not live_columns:
            return
        # More aggressive max bullets in later waves
        max_bullets = min(6, 2 + self.wave // 2 + (1 if self.wave >= 5 else 0))
        enemy_bullets = sum(
            1 for bullet in self.bullets
            if bullet.alive and not bullet.friendly
        )
        if enemy_bullets < max_bullets:
            player_cx = self.player.x + self.player.w / 2
            col = self._choose_shoot_column(live_columns, player_cx)
            if col is not None:
                alien = self.formation.lowest_in_col(col)
                if alien is not None:
                    x, y = self.formation.alien_pos(alien)
                    # Slightly faster bullets in higher waves
                    speed = min(290, 155 + self.wave * 10)
                    self.bullets.append(Bullet(
                        x + alien['w'] / 2,
                        y + alien['h'] + 3,
                        speed,
                        False,
                    ))
        low = max(0.22, 0.78 - self.wave * 0.038)
        high = max(low + 0.08, 1.25 - self.wave * 0.048)
        self.alien_shoot_cd = random.uniform(low, high)

    def _update_bullets(self, dt):
        for bullet in self.bullets:
            bullet.y += bullet.vy * dt
            if bullet.y < -16 or bullet.y > HEIGHT + 16:
                bullet.alive = False
        self.bullets = [b for b in self.bullets if b.alive]

    def _resolve_collisions(self):
        friendly = [b for b in self.bullets if b.alive and b.friendly]
        hostile = [b for b in self.bullets if b.alive and not b.friendly]

        for shot in friendly:
            for bomb in hostile:
                if (bomb.alive and abs(shot.x - bomb.x) <= 4 and
                        abs(shot.y - bomb.y) <= 9):
                    shot.alive = False
                    bomb.alive = False
                    self.particles.burst(
                        (shot.x + bomb.x) / 2,
                        (shot.y + bomb.y) / 2,
                        C_WHITE, count=5, speed=55, life=0.2, size=2)
                    break

        for bullet in friendly:
            if not bullet.alive:
                continue
            if self.ufo.check_hit(bullet):
                bullet.alive = False
                points = random.choice((50, 100, 150, 300))
                cx = self.ufo.x + self.ufo.w / 2
                cy = self.ufo.y + self.ufo.h / 2
                self.score += points
                self.popups.append(ScorePopup(
                    cx, cy, str(points), color=C_RED))
                self.particles.burst(
                    cx, cy, C_RED, count=24, speed=145, life=0.55, size=4)
                self.ufo.destroy()
                self.sound.play('ufo_hit')
                self._trigger_shake(4, 0.25)
                self.hitstop = max(self.hitstop, 0.045)
                continue

            for alien in self.formation.aliens:
                if not alien['alive']:
                    continue
                ax, ay = self.formation.alien_pos(alien)
                if (ax <= bullet.x <= ax + alien['w'] and
                        ay <= bullet.y <= ay + alien['h']):
                    bullet.alive = False
                    self.formation.kill_alien(alien)

                    # Combo logic
                    now = pygame.time.get_ticks() / 1000.0
                    if now - self.last_kill_time <= self.combo_window:
                        self.combo += 1
                    else:
                        self.combo = 1
                    self.last_kill_time = now

                    base = alien['points']
                    bonus = min(40, (self.combo - 1) * 10) if self.combo > 1 else 0
                    total = base + bonus
                    self.score += total

                    cx = ax + alien['w'] / 2
                    cy = ay + alien['h'] / 2
                    if bonus > 0:
                        self.popups.append(ScorePopup(
                            cx, cy, f"{base}+{bonus}", color=C_YELLOW))
                    else:
                        self.popups.append(ScorePopup(
                            cx, cy, str(base)))
                    self.particles.burst(
                        cx, cy, C_GREEN,
                        count=14, speed=105, life=0.38, size=3)
                    self.sound.play('alien_boom')
                    self._trigger_shake(2.5, 0.16)
                    self.hitstop = max(self.hitstop, 0.025)
                    break

            if bullet.alive:
                self._bullet_hits_shield(bullet, radius=2)

        for bullet in hostile:
            if not bullet.alive:
                continue
            if self._bullet_hits_shield(bullet, radius=3):
                continue
            if (self.player.alive and self.player.invuln_timer <= 0 and
                    self.player.x <= bullet.x <= self.player.x + self.player.w and
                    self.player.y <= bullet.y <= self.player.y + self.player.h):
                bullet.alive = False
                self._hit_player()

        self.bullets = [b for b in self.bullets if b.alive]

    def _bullet_hits_shield(self, bullet, radius):
        for shield in self.shields:
            if shield.check_hit(bullet.x, bullet.y):
                shield.damage(bullet.x, bullet.y, radius=radius)
                bullet.alive = False
                self.particles.burst(
                    bullet.x, bullet.y, C_GREEN,
                    count=5, speed=45, life=0.18, size=2)
                return True
        return False

    def _hit_player(self):
        if not self.player.alive or self.player.invuln_timer > 0:
            return
        self.player.kill()
        self.lives = max(0, self.lives - 1)
        self.game_over_pending = self.lives == 0
        self.bullets = [b for b in self.bullets if b.friendly]
        self.combo = 0  # reset combo on hit
        cx = self.player.x + self.player.w / 2
        cy = self.player.y + self.player.h / 2
        self.particles.burst(
            cx, cy, C_CYAN, count=36, speed=180, life=0.7, size=4)
        self.sound.play('player_boom')
        self._trigger_shake(8, 0.45)
        self.hitstop = max(self.hitstop, 0.09)

    def _invasion_game_over(self):
        self.lives = 0
        self.game_over_pending = True
        if self.player.alive:
            self._hit_player()
            self.lives = 0
            self.game_over_pending = True
        else:
            self.state = GameState.GAME_OVER
            self.game_over_timer = 0
            if self.score > self.hi_score:
                self.hi_score = self.score
                self._save_hi_score()

    def _start_next_wave(self):
        self.wave += 1
        start_y = 80 + min(48, (self.wave - 1) * 6)
        self.formation = Formation(self.sound, start_y=start_y)
        self.ufo = UFO()
        self.shields = self._make_shields()
        self.bullets.clear()
        self.player.respawn(WIDTH // 2 - self.player.w // 2)
        self.alien_shoot_cd = max(0.45, 1.25 - self.wave * 0.05)
        self.combo = 0
        self.state = GameState.PLAYING

    def _trigger_shake(self, amplitude, duration):
        if self.reduced_motion:
            return
        self.shake_amp = max(self.shake_amp, amplitude)
        self.shake_t = max(self.shake_t, duration)
        self.shake_max = max(0.001, duration)

    def _text(self, font, text, color=C_WHITE, center=None, pos=None):
        rendered = font.render(str(text), False, color)
        rect = rendered.get_rect()
        if center is not None:
            rect.center = center
        elif pos is not None:
            rect.topleft = pos
        self._world_surf.blit(rendered, rect)
        return rect

    def _draw(self):
        self._world_surf.fill(C_BG)
        if self.state == GameState.MENU:
            self._draw_menu()
        else:
            self._draw_game()
            if self.state == GameState.PAUSED:
                self._draw_overlay("PAUSED", "P / ESC  RESUME")
            elif self.state == GameState.GAME_OVER:
                self._draw_overlay("GAME OVER", "ENTER  RESTART")
            elif self.state == GameState.WAVE_CLEAR:
                self._draw_overlay(
                    f"WAVE {self.wave} CLEAR", "GET READY")

        if self.sound.muted:
            self._text(self.font_sm, "MUTE", C_RED, pos=(WIDTH - 42, 27))
        if self.reduced_motion:
            self._text(self.font_sm, "RM", C_YELLOW, pos=(WIDTH - 68, 27))
        if self.debug:
            self._draw_debug()

        offset_x = 0
        offset_y = 0
        if self.shake_t > 0 and not self.reduced_motion:
            strength = self.shake_amp * min(1.0, self.shake_t / self.shake_max)
            offset_x = int(random.uniform(-strength, strength))
            offset_y = int(random.uniform(-strength, strength))
        self.screen.fill((0, 0, 0))
        self.screen.blit(self._world_surf, (offset_x, offset_y))

    def _draw_game(self):
        self._text(
            self.font_sm, f"SCORE  {self.score:05d}", C_WHITE, pos=(12, 8))
        score_text = self.font_sm.render(
            f"HI  {self.hi_score:05d}", False, C_WHITE)
        self._world_surf.blit(
            score_text, (WIDTH // 2 - score_text.get_width() // 2, 8))
        self._text(
            self.font_sm, f"WAVE  {self.wave:02d}", C_WHITE,
            pos=(WIDTH - 82, 8))

        # NEW HIGH SCORE flash
        if self.new_hi_timer > 0 and self.new_hi_flash:
            alpha_flash = 0.5 + 0.5 * math.sin(self.new_hi_timer * 12)
            col = (int(245 * alpha_flash), int(220 * alpha_flash), 40)
            self._text(self.font_sm, "NEW HIGH SCORE!", col,
                       center=(WIDTH // 2, 28))

        pygame.draw.line(
            self._world_surf, C_GREEN,
            (8, HEIGHT - 20), (WIDTH - 8, HEIGHT - 20), 2)
        self.formation.draw(self._world_surf)
        self.ufo.draw(self._world_surf)
        for shield in self.shields:
            shield.draw(self._world_surf)
        for bullet in self.bullets:
            color = C_CYAN if bullet.friendly else C_WHITE
            if bullet.friendly:
                rect = (int(bullet.x) - 1, int(bullet.y) - 7, 3, 10)
            else:
                rect = (int(bullet.x) - 2, int(bullet.y) - 2, 4, 9)
            pygame.draw.rect(self._world_surf, color, rect)
        self.player.draw(self._world_surf)
        self.particles.draw(self._world_surf)
        for popup in self.popups:
            alpha = max(0.0, min(1.0, popup.life / popup.max_life))
            color = tuple(int(channel * alpha) for channel in popup.color)
            self._text(
                self.font_sm, popup.text, color,
                center=(int(popup.x), int(popup.y)))

        self._text(self.font_sm, f"{self.lives}", C_WHITE, pos=(12, HEIGHT - 17))
        for index in range(self.lives):
            self._world_surf.blit(
                self.mini_player, (30 + index * 18, HEIGHT - 16))

        # Optional combo indicator
        if self.combo > 1:
            self._text(self.font_sm, f"COMBO x{self.combo}", C_YELLOW,
                       pos=(WIDTH - 90, HEIGHT - 17))

    def _draw_menu(self):
        self._text(
            self.font_big, "SPACE INVADERS", C_GREEN,
            center=(WIDTH // 2, 76))
        self._text(
            self.font_sm, "PY PORT BY AC 0.1", C_CYAN,
            center=(WIDTH // 2, 108))

        rows = (
            ('squid', '= 30 PTS'),
            ('crab', '= 20 PTS'),
            ('octo', '= 10 PTS'),
            ('ufo', '= ???'),
        )
        y = 158
        for name, label in rows:
            sprite = self.menu_sprites[name]
            self._world_surf.blit(
                sprite, (WIDTH // 2 - 74, y - sprite.get_height() // 2))
            self._text(
                self.font_med, label, C_WHITE,
                pos=(WIDTH // 2 - 24, y - 9))
            y += 42

        # Show persistent hi-score on menu
        self._text(
            self.font_med, f"HI-SCORE  {self.hi_score:05d}", C_YELLOW,
            center=(WIDTH // 2, 320))

        blink_on = (pygame.time.get_ticks() // 450) % 2 == 0
        if blink_on:
            self._text(
                self.font_med, "PRESS ENTER", C_YELLOW,
                center=(WIDTH // 2, 356))
        self._text(
            self.font_sm, "MOVE  LEFT/RIGHT OR A/D", C_GRAY,
            center=(WIDTH // 2, 405))
        self._text(
            self.font_sm, "FIRE  SPACE OR UP", C_GRAY,
            center=(WIDTH // 2, 424))
        self._text(
            self.font_sm, "P PAUSE   M MUTE   R REDUCED MOTION", C_GRAY,
            center=(WIDTH // 2, 451))
        self._text(
            self.font_sm, "ESC EXIT", C_GRAY,
            center=(WIDTH // 2, 476))

    def _draw_overlay(self, title, subtitle):
        shade = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 176))
        self._world_surf.blit(shade, (0, 0))
        color = C_RED if title == "GAME OVER" else C_YELLOW
        self._text(
            self.font_big, title, color,
            center=(WIDTH // 2, HEIGHT // 2 - 18))
        self._text(
            self.font_med, subtitle, C_WHITE,
            center=(WIDTH // 2, HEIGHT // 2 + 28))
        if title == "GAME OVER":
            self._text(
                self.font_sm, "ESC  MAIN MENU", C_GRAY,
                center=(WIDTH // 2, HEIGHT // 2 + 58))
            if self.score >= self.hi_score and self.hi_score > 0:
                self._text(
                    self.font_sm, "NEW HIGH SCORE!", C_YELLOW,
                    center=(WIDTH // 2, HEIGHT // 2 + 80))

    def _draw_debug(self):
        fps = self.clock.get_fps()
        lines = (
            f"FPS {fps:5.1f}",
            f"ALIENS {self.formation.alive_count:02d}",
            f"BULLETS {len(self.bullets):02d}",
            f"PARTICLES {len(self.particles.particles):03d}",
            f"STATE {self.state.name}",
            f"COMBO {self.combo}",
        )
        panel = pygame.Surface((132, 90), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 190))
        self._world_surf.blit(panel, (5, 28))
        for index, line in enumerate(lines):
            self._text(
                self.font_sm, line, C_CYAN,
                pos=(10, 32 + index * 13))


def main():
    game = None
    try:
        game = Game()
        game.run()
    except pygame.error as exc:
        print(f"pygame error: {exc}")
        return 1
    finally:
        pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
