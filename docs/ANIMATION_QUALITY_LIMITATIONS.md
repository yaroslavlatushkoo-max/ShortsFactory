# Animation Quality Limitations (Mission 2)

These limits are implementation facts, not planned work presented as complete.

## Motion and rig

- ShortsFactory is a 2D front-view rig. The new temporal path controls the
  character root x and applies a bounded vertical visual lift from y. It does
  not control individual joints in 3D and does not provide a physically correct
  walk cycle.
- The vertical component is clamped to ±0.12 scene units (`path_lift`). A Lab
  trajectory with a large y excursion is compressed, and the pseudo-shadow stays
  at the original ground plane.
- Bezier, waypoint, and arc shapes are evaluated in the renderer, but the
  character remains a layered 2D paper rig. Curvature is visible as root motion,
  not as a turning body or foot-plant solver.
- The existing `walk`/`run` cycle amplitude is unchanged. Linear root motion is
  still not a full locomotion controller.

## Interactions

- Animation Lab models an attached prop at the owner's root position. The
  Factory renderer attaches the prop to `hand_r`. The rendered hand distance is
  checked independently; the two coordinate models are not unified.
- `reach` in the legacy storyboard compiler does not propagate a per-hand point
  into the action. Receive therefore uses the default `hand_r`.
- `give`/`receive` are validated as one transfer plus a receive action. The
  visual transfer occurs at 25% of the give duration; the semantic receive window
  is preserved in Story Studio but is not a second physical handoff.
- Interaction checks sample rendered hand distances and ownership state, but do
  not prove finger contact or object grasp pressure. There is no physics engine.
- Exit is clamped to x = 0.16 / 0.84 so characters remain inside the framing
  tolerance. They walk to the edge but do not fully leave the frame.

## Silhouette QC

- The silhouette check uses 2D alpha masks at six samples per second. It counts
  pixel overlap, not 3D depth or anatomical intersection.
- Intentional contact windows (reach/take/give/offer/drop/carry/pickup/putdown,
  ±0.35 s) downgrade small overlaps to `WARNING`. This can hide a small genuine
  penetration during a handoff.
- Objects hidden behind a character (`object.z < character.z`) are skipped
  because they are occluded rather than intersecting.
- Diagnostics are PNG frames for flagged samples, not a complete per-frame audit.
  The sampler can miss a very short overlap between sample times.
- 01/02/03/04/06 therefore finish with silhouette `WARNING`, not a clean `PASS`.
  05 has no contact and is `PASS`.

## Audio / lip-sync

- Piper supplies the voice; Factory supplies procedural ambience/music. The
  integration does not pass Story Studio's music style, tempo, or ambience
  metadata into the Factory audio mixer.
- Lip-sync is envelope-based mouth opening, not phoneme/viseme alignment.
  It must not be described as phoneme lip-sync.
- 05 has no dialogue, so `lip_sync=NOT_CHECKED`. A `NOT_CHECKED` result is not a
  pass, and it keeps the overall 05 result at `WARNING`.
- There is no automated check that a muxed voice is not clipped at the file
  boundary beyond the existing audio duration/mux checks.

## Language and parser

- The text parser is rule-based and narrow. It supports Milo, Nina, the glowing
  orb, and a fixed vocabulary for look/approach/take/give/receive/surprise/
  thanks/return/leave. It does not understand arbitrary prose, new characters,
  new props, negation, or complex temporal clauses.
- Unsupported entities or actions raise `IntegrationError`; they are not
  silently ignored.
- Multi-line dialogue supports up to four quoted lines and assigns speakers by
  the nearest preceding known name. Pronoun-only dialogue is not resolved.
- Curved/waypoint profiles are selected by keywords, not by semantic planning.
  A scenario without those keywords uses linear trajectories.

## QC semantics

- `silhouette_intersections` measures visual overlap, not causal contact.
- Hand-object contact is allowed for the current holder; a non-holder overlap
  outside a contact window is a failure. This is a heuristic.
- The trajectory check verifies that a path reached Factory, not that the
  encoded motion is aesthetically good.
- Determinism is checked on three sampled frames inside one render; it is not a
  byte-for-byte proof across separate renders.
- Manual visual review is still required for acting quality, composition, and
  whether a gesture reads as intended.

## Compatibility

- Old storyboard v1 files without `path` still use the previous linear behavior.
- Previous `output/integrated_pilot` and old CLI modes were not overwritten.
- The three projects remain separate. Story Studio runs on the system Python
  because it requires pydantic; Animation Lab runs in its own virtual
  environment; Factory runs in its own virtual environment.
