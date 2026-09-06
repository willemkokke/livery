/* Playable casts.
 *
 * The recordings are self-contained animated SVGs: every frame of the
 * session stacked in one file, cycled by CSS keyframes that share a single
 * duration. That plays fine on its own — but an <img> is a sealed document,
 * so nothing outside it can pause or seek, and a viewer who blinks has to
 * reload the page to see the moment again.
 *
 * So each cast is fetched and inlined, which puts its animations in this
 * document's timeline, where the Web Animations API can drive them:
 * pause(), play(), and a settable currentTime that is all a scrubber needs.
 * No player library, and the SVG on disk is unchanged — it still animates
 * by itself anywhere else it is opened.
 *
 * Progressive enhancement throughout: if the fetch fails, if the browser
 * has no getAnimations(), or if anything below throws, the original <img>
 * is left exactly where it was and still plays.
 */

function onEachPage(fn) {
  const run = () => {
    try {
      fn();
    } catch (err) {
      console.warn("footman casts:", err);
    }
  };
  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(run); // instant navigation: fires per page
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
}

const PLAY = "M8 5v14l11-7z";
const PAUSE = "M6 5h4v14H6zm8 0h4v14h-4z";
const REPLAY =
  "M12 5V1L7 6l5 5V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z";
const PREV = "M6 6h2v12H6zm3.5 6L18 6v12z";
const NEXT = "M16 6h2v12h-2zM6 18l8.5-6L6 6z";

/* Where each frame begins, in ms.
 *
 * A recording is one frame per keypress, so stepping through it is the
 * natural way to read one. The recorder stamps the boundaries onto the SVG
 * (`data-cast-frames`) rather than leaving them to be re-derived: parsing
 * them back out of the keyframes meant reading CSS whose shape varies — the
 * final frame carries no closing `opacity:0`, because it holds until the
 * loop — and that one silently went missing, so the last frame could not be
 * stepped to. Falls back to even spacing for a recording made before the
 * stamp existed. */
function frameStarts(root, cycle) {
  // The stamp is on the <svg>, which is a child of the figure the caller
  // holds — asking the figure for it silently fell through to the fallback.
  const svg = root.querySelector("svg") || root;
  const stamped = svg.getAttribute("data-cast-frames");
  if (stamped) {
    const times = stamped
      .split(",")
      .map(Number)
      .filter((n) => Number.isFinite(n));
    if (times.length) return times;
  }
  const count = svg.querySelectorAll(".cast-frame").length || 1;
  return Array.from({ length: count }, (_, i) => (i * cycle) / count);
}

/* The cycle's last instant is also its first: at exactly `cycle` the
 * animation has wrapped and frame 0 is showing again. Dragging the scrubber
 * to its maximum landed there, so the end of the timeline showed the
 * beginning of the recording. Stay a millisecond short of the wrap. */
function within(ms, cycle) {
  return Math.max(0, Math.min(ms, cycle - 1));
}

function icon(path) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", path);
  svg.appendChild(p);
  return svg;
}

/* Turn one <img src="…-cast.svg"> into a player. */
async function enhance(img) {
  const source = img.getAttribute("src");
  if (!source) return;

  // Always revalidate. These files are regenerated on every docs build and
  // their URL never changes, so a cached copy is indistinguishable from the
  // current one — which cost an evening of looking at a recording that had
  // already been replaced. The server answers 304 when it really is
  // unchanged, so this costs a conditional request, not a download.
  const response = await fetch(source, { cache: "no-cache" });
  if (!response.ok) return;
  const markup = await response.text();

  const parsed = new DOMParser().parseFromString(markup, "image/svg+xml");
  const svg = parsed.documentElement;
  if (!svg || svg.nodeName.toLowerCase() !== "svg") return;

  const figure = document.createElement("figure");
  figure.className = "cast";
  // The alt text described the session for anyone who could not see it, and
  // inlining must not lose that: it becomes the figure's accessible name.
  const description = img.getAttribute("alt") || "Terminal recording";
  figure.setAttribute("role", "group");
  figure.setAttribute("aria-label", description);

  const stage = document.createElement("div");
  stage.className = "cast-stage";
  stage.appendChild(document.importNode(svg, true));
  figure.appendChild(stage);


  img.replaceWith(figure);

  // A recording inside a closed tab is `display: none`, so its CSS
  // animations do not exist yet and getAnimations() finds nothing to drive.
  // Wiring the controls has to wait until the tab is actually opened —
  // otherwise only the tab that happens to be open on page load gets them.
  if (!wire(figure)) {
    const seen = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && wire(figure)) {
        seen.disconnect();
      }
    });
    seen.observe(figure);
  }
}

/* Hang the controls off an inlined recording. Returns false when the
 * animations are not live yet (a hidden tab), so the caller can retry. */
function wire(figure) {
  if (figure.dataset.wired) return true;

  const animations = figure.getAnimations
    ? figure.getAnimations({ subtree: true })
    : [];
  if (!animations.length) return false; // hidden, or animating on its own

  const timing = animations[0].effect.getComputedTiming();
  const cycle = Number(timing.duration) || 0;
  if (!cycle) return false;
  figure.dataset.wired = "1";

  /* ---- controls ---- */

  const controls = document.createElement("div");
  controls.className = "cast-controls";

  const starts = frameStarts(figure, cycle);

  // The frame showing at a given moment: the last boundary at or before it,
  // compared exactly. A tolerance here read a clock sitting a fraction short
  // of a boundary — which is where playback leaves it — as already being in
  // the next frame, so "next" had nowhere left to go.
  const frameAt = (ms) => {
    let index = 0;
    for (let i = 0; i < starts.length; i += 1) {
      if (starts[i] <= ms) index = i;
    }
    return index;
  };
  const now = () => frameAt(at() % cycle);
  const clamp = (i) => Math.min(Math.max(i, 0), starts.length - 1);

  const back = document.createElement("button");
  back.type = "button";
  back.title = "Previous keypress";
  back.setAttribute("aria-label", "Previous keypress");
  back.appendChild(icon(PREV));
  controls.appendChild(back);

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "cast-toggle";
  controls.appendChild(toggle);

  const forward = document.createElement("button");
  forward.type = "button";
  forward.title = "Next keypress";
  forward.setAttribute("aria-label", "Next keypress");
  forward.appendChild(icon(NEXT));
  controls.appendChild(forward);

  const scrub = document.createElement("input");
  scrub.type = "range";
  scrub.className = "cast-scrub";
  // Indexed by frame, not by milliseconds. A recording is a sequence of
  // keypresses played at an arbitrary pace, so a clock was measuring the
  // wrong thing — and reporting it in whole seconds meant the two ends
  // never quite agreed: dragging to the finish sat a millisecond short of
  // the loop and so read one second short of the total, for ever.
  scrub.min = "0";
  scrub.max = String(starts.length - 1);
  scrub.step = "1";
  scrub.value = "0";
  scrub.setAttribute("aria-label", "Step through the recording");
  controls.appendChild(scrub);

  const time = document.createElement("span");
  time.className = "cast-time";
  controls.appendChild(time);

  const replay = document.createElement("button");
  replay.type = "button";
  replay.className = "cast-replay";
  replay.title = "Play from the start";
  replay.setAttribute("aria-label", "Play from the start");
  replay.appendChild(icon(REPLAY));
  controls.appendChild(replay);

  figure.appendChild(controls);

  /* ---- driving them ---- */

  // Re-queried every time, never cached. A frame's animation can start after
  // this ran — a tab that was closed on load, a font arriving late — and one
  // that was missed keeps running while the rest are paused. They drift, and
  // then two frames are painted at once: the newer frame's highlight bars sit
  // over the older frame's text, which reads exactly like text rendered the
  // colour of its own background.
  const live = () => figure.getAnimations({ subtree: true });
  const at = () => Number(live()[0]?.currentTime) || 0;
  const seek = (ms) => {
    for (const a of live()) a.currentTime = ms;
  };
  const playing = () => live()[0]?.playState === "running";

  let raf = 0;
  let dragging = false;
  let shown = null; // which icon the button is currently showing

  const label = (index) =>
    (time.textContent = `${index + 1} / ${starts.length}`);

  // Only touched when the state actually flips. Repainting the button every
  // animation frame replaced the <svg> under the pointer between mousedown
  // and mouseup, so the click never completed and Pause did nothing.
  const paintButton = () => {
    const now = playing();
    if (now === shown) return;
    shown = now;
    toggle.title = now ? "Pause" : "Play";
    toggle.setAttribute("aria-label", now ? "Pause" : "Play");
    toggle.replaceChildren(icon(now ? PAUSE : PLAY));
  };

  const tick = () => {
    // Keep every frame's animation on one clock. They start independently,
    // so any that began late runs permanently offset from the rest — and two
    // frames painted at once put one frame's highlight bars over another's
    // text, which looks exactly like text the colour of its own background.
    const all = live();
    if (all.length > 1) {
      const t = Number(all[0].currentTime) || 0;
      for (const a of all) {
        if (Math.abs(Number(a.currentTime) - t) > 1) a.currentTime = t;
      }
    }
    if (!dragging) {
      const index = now();
      scrub.value = String(index);
      label(index);
    }
    paintButton();
    raf = playing() ? requestAnimationFrame(tick) : 0;
  };

  const pause = () => {
    for (const a of live()) a.pause();
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    paintButton();
  };
  const play = () => {
    for (const a of live()) a.play();
    paintButton();
    if (!raf) raf = requestAnimationFrame(tick);
  };

  // Stepping is a reading action, not a playback one: it pauses, so the
  // frame you asked for is the frame you get to look at.
  const goto = (index) => {
    const at = clamp(index);
    const to = at + 1 < starts.length ? starts[at + 1] : cycle;
    pause();
    // Seek to the middle of the frame. The animation's own boundaries are
    // percentages rounded to three decimals, so a frame starting at exactly
    // 12000 ms turns on at 12000.045 — seeking to the stamped boundary
    // painted the *previous* frame while the stepper had already counted
    // the new one, and "next" then had nowhere left to go.
    seek(within((starts[at] + to) / 2, cycle));
    scrub.value = String(at);
    label(at);
  };
  back.addEventListener("click", () => goto(now() - 1));
  forward.addEventListener("click", () => goto(now() + 1));

  toggle.addEventListener("click", () => (playing() ? pause() : play()));
  replay.addEventListener("click", () => {
    seek(0);
    label(0);
    scrub.value = "0";
    play();
  });

  // Order matters: read the slider, *then* pause. Pausing used to repaint
  // the slider from the animation clock first, overwriting the value being
  // dragged, so every drag seeked back to where it started.
  const scrubTo = () => {
    const index = clamp(Number(scrub.value));
    if (playing()) pause();
    const to = index + 1 < starts.length ? starts[index + 1] : cycle;
    seek(within((starts[index] + to) / 2, cycle));
    label(index);
  };
  scrub.addEventListener("pointerdown", () => {
    dragging = true;
  });
  scrub.addEventListener("input", scrubTo);
  const endDrag = () => {
    dragging = false;
  };
  scrub.addEventListener("pointerup", endDrag);
  scrub.addEventListener("pointercancel", endDrag);
  scrub.addEventListener("blur", endDrag);

  // A recording that starts moving is the point of the front page — but a
  // reader who has asked the OS for less motion gets a still first frame
  // and a play button, not a surprise.
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (still) {
    seek(0);
    scrub.value = "0";
    label(0);
    pause();
  } else {
    play();
  }
  return true;
}

onEachPage(() => {
  for (const img of document.querySelectorAll('img[src$="cast.svg"]')) {
    enhance(img).catch((err) => console.warn("footman casts:", err));
  }
});
