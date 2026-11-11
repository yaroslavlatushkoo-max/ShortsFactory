# generate_textures.py — own 16x16 pixel-art Minecraft-style textures (no game assets).
# Run with system python (PIL + numpy): python generate_textures.py
# Output: 16x16 PNGs next to this script.
import numpy as np
from PIL import Image
from pathlib import Path

OUT = Path(__file__).resolve().parent
S = 16
rng = np.random.RandomState(7)

def canvas(base, w=S, h=S):
    return np.full((h, w, 3), np.array(base, np.uint8), np.int16)

def jitter(img, amp):
    return np.clip(img.astype(int) + rng.randint(-amp, amp + 1, img.shape), 0, 255)

def blob(img, cx, cy, r, color, irregular=True):
    for y in range(max(0, cy - r), min(S, cy + r + 1)):
        for x in range(max(0, cx - r), min(S, cx + r + 1)):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r + (1 if irregular and rng.rand() < 0.3 else 0):
                img[y, x] = color
    return img

def save(img, name):
    Image.fromarray(img.astype(np.uint8)).save(OUT / f"{name}.png")
    print("tex:", name)

def stone():
    img = jitter(canvas((127, 127, 127)), 14)
    for cx, cy, r in ((3, 4, 2), (11, 3, 2), (6, 11, 2), (13, 12, 1), (1, 13, 1)):
        blob(img, cx, cy, r, (100, 100, 100))
    for cx, cy in ((8, 7), (2, 8), (14, 6)):
        blob(img, cx, cy, 1, (152, 152, 152))
    return img

def dirt():
    img = jitter(canvas((134, 96, 67)), 16)
    for cx, cy in ((4, 3), (12, 6), (7, 10), (2, 13), (14, 1)):
        blob(img, cx, cy, 1, (100, 70, 45))
    for cx, cy in ((9, 2), (3, 8), (13, 12)):
        blob(img, cx, cy, 1, (162, 122, 86))
    return img

def grass_top():
    img = jitter(canvas((98, 160, 55)), 13)
    for cx, cy in ((5, 5), (11, 9), (3, 12), (13, 3), (8, 14), (1, 2)):
        blob(img, cx, cy, 1, (70, 122, 38))
    for cx, cy in ((7, 2), (14, 11), (2, 6), (10, 13)):
        blob(img, cx, cy, 1, (120, 182, 70))
    return img

def grass_side():
    img = dirt()
    for x in range(S):
        h = 3 + int(rng.randint(0, 2))
        for y in range(h):
            img[y, x] = np.clip(np.array((98, 160, 55)) + rng.randint(-12, 13, 3), 0, 255)
    return img

def cobble():
    img = canvas((62, 62, 62))
    stones = [(0, 0, 7, 7), (8, 0, 7, 4), (8, 5, 7, 6), (0, 8, 6, 7), (7, 12, 8, 3)]
    for sx, sy, w, h in stones:
        c = 122 + int(rng.randint(-12, 13))
        for y in range(sy, min(S, sy + h)):
            for x in range(sx, min(S, sx + w)):
                edge = (x in (sx, sx + w - 1) or y in (sy, sy + h - 1))
                v = c - 20 if edge else c
                img[y, x] = np.clip(v + rng.randint(-7, 8), 0, 255)
    return img

def ore(base_img, gem, dark, spots):
    """Chunky MC-style gem clusters: 2x2..3x3 core + dark rim + stray pixels."""
    img = base_img.copy()
    for cx, cy in spots:
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1), (2, 0), (0, 2), (-1, 0), (0, -1)):
            if rng.rand() < (0.55 if (dx, dy) in ((2, 0), (0, 2), (-1, 0), (0, -1)) else 0.92):
                x, y = cx + dx, cy + dy
                if 0 <= x < S and 0 <= y < S:
                    img[y, x] = dark
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            if rng.rand() < 0.8:
                x, y = cx + dx, cy + dy
                if 0 <= x < S and 0 <= y < S:
                    img[y, x] = gem
    return img

def log_side():
    img = np.zeros((S, S, 3), np.int16)
    for x in range(S):
        c = (107, 81, 49) if x % 4 else (77, 58, 34)
        img[:, x] = c
    return jitter(img, 8)

def log_top():
    img = np.zeros((S, S, 3), np.int16)
    for y in range(S):
        for x in range(S):
            d = max(abs(x - 7.5), abs(y - 7.5))
            c = (155, 127, 85) if int(d) % 2 == 0 else (107, 81, 49)
            if d > 6.5: c = (77, 58, 34)
            img[y, x] = c
    return jitter(img, 6)

def leaves():
    img = jitter(canvas((58, 95, 32)), 18)
    for _ in range(10):
        img[rng.randint(0, S), rng.randint(0, S)] = (24, 44, 16)
    for _ in range(8):
        img[rng.randint(0, S), rng.randint(0, S)] = (92, 134, 50)
    return img

def planks():
    img = jitter(canvas((162, 130, 78)), 6)
    for y in (3, 7, 11, 15):
        img[y, :] = (96, 74, 42)
    for bx, x in ((0, 8), (1, 3), (2, 12), (3, 6)):
        y0 = bx * 4
        img[y0:y0 + 3, x] = (110, 86, 50)
    return img

def lava():
    img = jitter(canvas((207, 91, 16)), 16)
    for cx, cy in ((4, 3), (12, 5), (7, 11), (2, 13), (14, 10)):
        blob(img, cx, cy, 1, (255, 160, 40))
    for cx, cy in ((9, 2), (2, 8), (13, 14), (6, 6)):
        blob(img, cx, cy, 1, (140, 40, 8))
    return img

def bedrock():
    img = jitter(canvas((85, 85, 85)), 28)
    for cx, cy in ((4, 4), (12, 9), (8, 13)):
        blob(img, cx, cy, 2, (48, 48, 48))
    return img

def obsidian():
    img = jitter(canvas((20, 14, 34)), 8)
    for _ in range(14):
        img[rng.randint(0, S), rng.randint(0, S)] = (60, 40, 100)
    for _ in range(5):
        img[rng.randint(0, S), rng.randint(0, S)] = (94, 72, 140)
    return img

def diamond_block():
    img = jitter(canvas((98, 231, 217)), 12)
    for cx, cy in ((4, 4), (12, 4), (8, 9), (3, 12), (13, 12)):
        blob(img, cx, cy, 1, (62, 190, 178))
        img[cy, cx] = (235, 255, 252)
    return img

# ---------- character skins ----------
def skin_base(c): return jitter(canvas(c), 5)

def hair_rows(img, color, rows, jag=1):
    for y in range(min(rows, S)):
        img[y, :] = color
    if rows < S:
        for x in range(S):
            if rng.rand() < 0.5:
                img[rows, x] = color
    return img

def steve_face():
    img = skin_base((196, 143, 107))
    hair_rows(img, (59, 42, 26), 3)
    # eyes: white + purple pupil (classic layout)
    img[8:10, 2:4] = (255, 255, 255); img[8:10, 4:6] = (82, 61, 163)
    img[8:10, 10:12] = (82, 61, 163); img[8:10, 12:14] = (255, 255, 255)
    # nose shadow + mustache + mouth
    img[10:12, 7:9] = (168, 116, 86)
    img[11:13, 5:11] = (74, 49, 32)
    img[13:14, 7:9] = (122, 73, 60)
    return img

def steve_head_side():
    img = skin_base((196, 143, 107))
    hair_rows(img, (59, 42, 26), 5)
    img[:, 0:2] = (59, 42, 26)   # sideburn at front edge
    return img

def steve_head_top(): return hair_rows(skin_base((59, 42, 26)), (59, 42, 26), S)
def steve_head_back():
    img = skin_base((196, 143, 107))
    return hair_rows(img, (59, 42, 26), 8)

def alex_face():
    img = skin_base((224, 172, 138))
    hair_rows(img, (201, 126, 40), 3)
    img[8:10, 2:4] = (255, 255, 255); img[8:10, 4:6] = (98, 160, 66)
    img[8:10, 10:12] = (98, 160, 66); img[8:10, 12:14] = (255, 255, 255)
    img[13:14, 6:10] = (150, 96, 74)
    return img

def alex_head_side():
    img = skin_base((224, 172, 138))
    hair_rows(img, (201, 126, 40), 5)
    img[:, 0:2] = (201, 126, 40)
    return img

def alex_head_top(): return jitter(canvas((201, 126, 40)), 10)
def alex_head_back():
    img = skin_base((224, 172, 138))
    return hair_rows(img, (201, 126, 40), 9)

def torso(base, hem):
    img = jitter(canvas(base, 16, 24), 9)  # 16x24
    img[0, :] = hem
    img[15, :] = hem
    return img

def arm_img(base, sleeve=None):
    img = jitter(canvas(base, 8, 24), 5)  # 8x24
    if sleeve: img[0:9, :] = sleeve
    return img

def leg_img(base, shoe):
    img = jitter(canvas(base, 8, 24), 7)  # 8x24
    img[18:24, :] = shoe
    return img

# ---------- write all ----------
save(stone(), "stone")
save(dirt(), "dirt")
save(grass_top(), "grass_top")
save(grass_side(), "grass_side")
save(cobble(), "cobblestone")
save(ore(stone(), (35, 35, 35), (20, 20, 20), ((4, 4), (11, 5), (6, 11), (12, 12))), "coal_ore")
save(ore(stone(), (79, 237, 217), (46, 160, 148), ((4, 4), (11, 4), (7, 10), (12, 12), (3, 13))), "diamond_ore")
save(ore(stone(), (216, 175, 147), (176, 138, 112), ((4, 5), (11, 6), (6, 12))), "iron_ore")
save(log_side(), "log_side")
save(log_top(), "log_top")
save(leaves(), "leaves")
save(planks(), "planks")
save(lava(), "lava")
save(bedrock(), "bedrock")
save(obsidian(), "obsidian")
save(diamond_block(), "diamond_block")

save(steve_face(), "steve_face")
save(steve_head_side(), "steve_head_side")
save(steve_head_top(), "steve_head_top")
save(steve_head_back(), "steve_head_back")
save(torso((0, 168, 168), (0, 120, 120)), "steve_body")
save(arm_img((196, 143, 107)), "steve_arm")
save(leg_img((74, 74, 196), (76, 76, 76)), "steve_leg")

save(alex_face(), "alex_face")
save(alex_head_side(), "alex_head_side")
save(alex_head_top(), "alex_head_top")
save(alex_head_back(), "alex_head_back")
save(torso((111, 168, 78), (82, 126, 58)), "alex_body")
save(arm_img((224, 172, 138), (111, 168, 78)), "alex_arm")
save(leg_img((154, 139, 111), (94, 82, 68)), "alex_leg")


def flame():
    img = jitter(canvas((255, 180, 60)), 20)
    for cx, cy in ((7, 11), (9, 8), (8, 4), (5, 6), (11, 12)):
        blob(img, cx, cy, 1, (255, 220, 120))
    for cx, cy in ((3, 13), (12, 13), (8, 14), (2, 9)):
        blob(img, cx, cy, 1, (230, 120, 20))
    return img

save(flame(), "flame")
save(planks(), "wood")
save(leg_img((154, 139, 111), (94, 82, 68)), "shoe")
save(torso((74, 74, 196), (56, 56, 156)), "steve_pants_tx")
print("EXTRA DONE")


save(leg_img((76, 76, 76), (60, 60, 60)), "steve_shoe")
save(leg_img((94, 82, 68), (74, 62, 50)), "alex_shoe")
print("SHOES DONE")


save(jitter(canvas((89, 58, 34)), 10), "steve_hair")
save(jitter(canvas((201, 126, 40)), 12), "alex_hair")
print("HAIR DONE")

print("ALL TEXTURES DONE:", len(list(OUT.glob("*.png"))))
