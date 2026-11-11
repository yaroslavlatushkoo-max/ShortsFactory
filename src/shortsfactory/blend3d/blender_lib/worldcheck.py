# blend3d/blender_lib/worldcheck.py — geometry integrity audit for block worlds.
# Detects duplicate cells, holes in declared floor regions, and off-grid blocks.
# Runs inside Blender after the world is built; logs issues via core.log.

def audit_blocks(scene_objects, floor_region=None, floor_z=0):
    """Audit all mesh objects carrying a '_cell' custom prop.
    floor_region: optional (x0, x1, y0, y1) — every cell in this rect at floor_z
    must contain a block, otherwise it is reported as a hole.
    Returns dict: duplicates[], holes[], offgrid[], counts."""
    cells = {}
    duplicates = []
    offgrid = []
    for o in scene_objects:
        if o.type != 'MESH' or '_cell' not in o.keys(): continue
        cx, cy, cz = o['_cell']
        key = (int(round(cx)), int(round(cy)), int(round(cz)))
        off = any(abs(a - b) > 0.01 for a, b in zip((cx, cy, cz), key))
        if off: offgrid.append((o.name, (cx, cy, cz)))
        if key in cells: duplicates.append((o.name, key, cells[key]))
        else: cells[key] = o.name
    holes = []
    if floor_region:
        x0, x1, y0, y1 = floor_region
        for i in range(x0, x1 + 1):
            for j in range(y0, y1 + 1):
                if (i, j, floor_z) not in cells:
                    holes.append((i, j, floor_z))
    return {"duplicates": duplicates, "holes": holes[:20], "offgrid": offgrid[:20],
            "blocks": len(cells)}

def report_issues(audit, tag="WORLDCHECK"):
    n_issues = len(audit['duplicates']) + len(audit['holes']) + len(audit['offgrid'])
    if n_issues == 0:
        print(f"[{tag}] OK: {audit['blocks']} blocks, no duplicates/holes/offgrid")
        return 0
    for d in audit['duplicates'][:5]:
        print(f"[{tag}] DUPLICATE cell {d[1]}: {d[2]} / {d[0]}")
    for h in audit['holes'][:5]:
        print(f"[{tag}] HOLE at {h}")
    for g in audit['offgrid'][:5]:
        print(f"[{tag}] OFFGRID {g[0]} at {g[1]}")
    print(f"[{tag}] ISSUES: {n_issues}")
    return n_issues
