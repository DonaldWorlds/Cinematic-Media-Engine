# DO NOT IMPLEMENT YET — Post-v1 Future Work

⚠️ This file describes the AI editorial / cinematic / creativity layer.
⚠️ It is OUT OF SCOPE until v1 (Phases 2-6 of SPEC.md) is complete and battle-tested.
⚠️ Do not reference this file when making implementation decisions.
⚠️ Do not let Claude Code pull from this file during Phase 2-6 work.

When to revisit: After v1 ships AND the engine has been used on real content for 3-4 weeks AND specific gaps are identified in real output.

[rest of content below]


Right call. Lock in the three modes before they drift in your head, then we add the creativity layer thinking to KNOWLEDGE.md.

---

**The three modes in your system:**

**1. standard_longform**

What it's for: Stream uploads, gameplay videos, casual vlogs, podcasts, commentary. The "I streamed for 6 hours, give me a watchable 25-minute upload" use case.

Editing style: Minimal. Hard cuts, preserve chronology, keep conversational flow, drop dead air, no fancy transitions or color grading. Subtitles as a separate SRT file (not burned in). Transcription is OFF by default unless you flag --transcribe.

The point: get raw streams into uploadable form fast. No creativity needed. This mode prioritizes throughput over aesthetics.

---

**2. cinematic_longform**

What it's for: Music process videos, artistic vlogs, documentaries, storytelling pieces. The "I want this to feel like a piece of art" use case.

Editing style: This is where creativity lives. Cinematic transitions (cross-dissolves, match cuts), atmospheric pacing, LUT-based color grading, motion graphics, music synchronization. 4K 24fps. Slower cuts that breathe. Beat-aware editing when music is present. Refined branding.

The point: emotional immersion and aesthetic consistency. Slower volume, higher quality bar. This mode is where kkthedon's artistic identity gets expressed.

---

**3. shortform**

What it's for: TikTok clips, Instagram Reels, YouTube Shorts. The "I need 20 clips a day for distribution" use case.

Editing style: Aggressive pacing, rapid cuts, dense editing. 9:16 vertical aspect ratio. Burned-in captions with word-by-word animation. Punch-in zooms, speed ramps, hook-first ordering. Prominent watermark. First 3 seconds must contain a viral hook.

The point: maximize retention and viral potential on platforms that reward density. Volume play.

---

**Quick summary table:**

| Mode | Use case | Aspect | Pacing | Captions | Color grade | Volume |
|------|----------|--------|--------|----------|-------------|--------|
| standard_longform | Streams, casual content | 16:9 | Natural | SRT file, optional | None | High |
| cinematic_longform | Music, art, story | 16:9 (4K) | Slow, atmospheric | Optional styled | LUT-based | Low |
| shortform | TikTok/Reels/Shorts | 9:16 | Aggressive | Burned in | Light | High |

---

**On the creativity layer thinking:**

You correctly identified that the creativity question is about cinematic_longform mode specifically. Standard_longform and shortform have different goals — neither needs "creativity" in the Descript/Runway sense:

- standard_longform = mechanical throughput
- shortform = pattern-matched virality
- cinematic_longform = where creativity and aesthetic matters

So when we talk about Descript-like polish, Runway-like motion, or any "make it feel cinematic" tooling, that's a cinematic_longform-only concern.

---

**Here's the prompt to give Claude Code:**

```
Pause Phase 2 work for a quick KNOWLEDGE.md update, then continue.

Add a new section to KNOWLEDGE.md called "Creativity & Cinematic Layer (Post-v1, cinematic_longform only)" with this content:

---

## Creativity & Cinematic Layer (Post-v1, cinematic_longform only)

The three content modes have different creativity requirements:

- standard_longform: NO creativity layer needed. Mechanical throughput. Stream content doesn't need to feel artistic.
- shortform: NO creativity layer needed. Engine's burned-in captions, hook-first ordering, and aggressive pacing handle this. Caption templating optional via CapCut Pro.
- cinematic_longform: THIS is where creativity matters. The mode is specifically for music process, artistic vlogs, documentaries. Where kkthedon's aesthetic identity lives.

### For cinematic_longform, consider these external tools as a POST-engine polish layer:

Descript (for audio + transcript polish):
- Studio Sound for audio cleanup (huge upgrade over raw mic capture)
- Word-level caption styling with timing animation
- Filler word removal ("uh", "you know") cleanly
- Cost: ~$24-30/mo

Runway (for motion + visual style):
- Subtle camera motion on static shots
- Style transfer for consistent cinematic look across pieces
- Color grading consistency
- Cost: ~$15-30/mo

CapCut Pro (for shortform caption templates if needed):
- Caption template library
- Cost: ~$8/mo

### Architecture decision:
These tools are NOT integrated into the engine. They are a manual or API-driven post-processing layer that runs on engine output. The engine produces rough cuts; these tools add the creative polish.

### Workflow when used:
1. Engine exports cinematic_longform to src/output/cinematic/
2. Manual: Open in Descript → Studio Sound + caption polish + filler removal
3. Manual: Pass to Runway for motion/style work (if needed)
4. Final color/sound in DaVinci Resolve or Final Cut on M1
5. Upload

### When to actually add this:
Do NOT subscribe to these tools yet. Build the engine through Phase 6 first. Use it on real content for 3-4 weeks. Then evaluate which tools actually fill gaps in real output. Sign up for free trials first to test on real engine output before committing to subscriptions.

### Cost reality if all three:
$50-75/mo for the polish stack on top of TwelveLabs API costs. Worth it IF cinematic_longform is a significant content pillar. Skip entirely if cinematic is occasional.

### API automation (Phase 7+):
Descript and Runway both have APIs. Once manual workflow is proven valuable, automation of the handoff between engine and these tools becomes its own project. Not in v1.

---

After adding this section, do NOT commit yet — it lands with the Phase 2 commit at the end.

Continue Phase 2 librosa_analysis.py work where you left off.
```

---

**Send that to Claude Code and it'll:**

1. Add the creativity/cinematic notes to KNOWLEDGE.md
2. Return to the librosa work
3. Keep Phase 2 scope locked

The thinking is preserved. The build continues. You're not adding scope mid-Phase 2 — you're documenting future direction so it's not lost.

When you eventually get to Phase 6 and start producing real cinematic_longform output, you'll come back to this section. By then you'll know whether Descript/Runway are worth the money based on actual output quality.

That's the pattern: document the idea, defer the action, decide based on real evidence later. Send the prompt and continue Phase 2.



Add a vision section to KNOWLEDGE.md (does not change v1 scope — captures the north star and v2 roadmap).

## Project North Star — "Human-Made / Adobe-Quality" Clips

The end goal: clips indistinguishable from human-edited Adobe Premiere output, in both LOOK and AUDIO. This is the sum of multiple layers built across versions, NOT a single feature.

### LOOK layers and where they land:
- Composition / caption zoning → v1 Phase 4 (full-bleed + zoned captions; the amateur-tell fix)
- Face-aware framing (eyes upper-third, face 40-60%) → Phase 5 (needs face detection)
- Aesthetic moment selection (pick visually good moments, not just audio-loud) → Phase 5 (OpenCLIP)
- Motion (slow push-in, micro-drift, living frame) → Phase 4 polish / v2 (effects.py)
- Color grade → v2 (color_pipeline.py + LUTs, or Descript)
- Upscale / facial sharpness → v2+ (generative)

### AUDIO layers (the underestimated half of "professional"):
- Basic loudness normalization → v1 (ffmpeg)
- Studio-quality cleanup, leveling, filler-word removal, noise reduction → v2 (Descript API backend)
- Adobe-quality audio is primarily a Descript-backend concern in v2

### Reference-Mirror feature (v2, builds ON OpenCLIP):
- Feed the engine professional reference clips the creator admires
- Extract their compositional + pacing signature (framing, cut rhythm, motion patterns)
- Bias engine output toward that signature — the "blueprint/mirror" concept
- REQUIRES the Phase 5 vision layer (OpenCLIP) to function — cannot be done with RAG or an MCP, because comparison requires a vision model reading frames, not text retrieval

### Build discipline:
- Each layer sits on top of the previous — you cannot polish output that doesn't exist yet
- v1 gets clips to "clean, intentional, well-composed, watchable"
- v2 closes the gap to "human-made Adobe-quality" via Descript (audio + color) + motion + reference-mirror
- Source footage quality (lighting, framing at capture) is a prerequisite the engine cannot manufacture

This section is the WHY behind the roadmap. It does not authorize building any of it ahead of its phase.

After adding this, return to the v1 Phase 4 caption-zoning work in progress.