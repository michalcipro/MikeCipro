(function () {
  var root = document.documentElement;
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var finePointer = window.matchMedia("(pointer: fine)").matches;
  var hasIO = "IntersectionObserver" in window;
  var clamp = function (v, a, b) { return Math.min(b, Math.max(a, v)); };
  var $ = function (s, c) { return (c || document).querySelector(s); };
  var $$ = function (s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); };

  if (!reduced && hasIO) root.classList.add("js");

  var year = $("#year");
  if (year) year.textContent = new Date().getFullYear();

  var clocks = $$("[data-clock]");
  if (clocks.length) {
    var tick = function () {
      var t = "";
      try { t = new Intl.DateTimeFormat("cs-CZ", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Prague" }).format(new Date()); } catch (e) {}
      clocks.forEach(function (c) { c.textContent = t ? "Praha " + t : "Praha"; });
    };
    tick();
    setInterval(tick, 20000);
  }

  /* ---------- mobile menu ---------- */
  var menuBtn = $("#menu-btn"), menu = $("#mobile-menu");
  function setMenu(open) {
    menu.hidden = !open;
    menuBtn.setAttribute("aria-expanded", String(open));
  }
  if (menuBtn && menu) {
    menuBtn.addEventListener("click", function () { setMenu(menu.hidden); });
    menu.addEventListener("click", function (e) { if (e.target.closest("a")) setMenu(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") setMenu(false); });
  }

  /* ---------- scrollspy ---------- */
  var links = $$(".nav-links a");
  if (hasIO && links.length) {
    var byId = {};
    links.forEach(function (a) { byId[a.getAttribute("href").slice(1)] = a; });
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        links.forEach(function (l) { l.classList.remove("is-active"); });
        if (byId[en.target.id]) byId[en.target.id].classList.add("is-active");
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    Object.keys(byId).forEach(function (id) { var s = document.getElementById(id); if (s) spy.observe(s); });
  }

  /* ---------- reveals ---------- */
  if (root.classList.contains("js")) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -5% 0px" });
    $$(".rv").forEach(function (el) { io.observe(el); });
  }

  /* ---------- manifest: words light up while scrolling ---------- */
  var manifest = $("#manifest");
  var words = [];
  if (manifest && root.classList.contains("js")) {
    var wrap = function (node) {
      Array.prototype.slice.call(node.childNodes).forEach(function (child) {
        if (child.nodeType === 3) {
          var frag = document.createDocumentFragment();
          child.textContent.split(/(\s+)/).forEach(function (part) {
            if (!part) return;
            if (/^\s+$/.test(part)) { frag.appendChild(document.createTextNode(part)); return; }
            var s = document.createElement("span");
            s.className = "w";
            s.textContent = part;
            frag.appendChild(s);
            words.push(s);
          });
          node.replaceChild(frag, child);
        } else if (child.nodeType === 1) {
          wrap(child);
        }
      });
    };
    wrap(manifest);
  }

  /* ---------- scroll scenes ---------- */
  var stage = $("#stage");
  var chips = $$(".chip[data-depth]");
  var pathFg = $("#path-art .path-fg");
  var aboutImg = $("#about-photo img");
  var ticking = false;

  function onFrame() {
    ticking = false;
    if (reduced) return;
    var vh = window.innerHeight;

    if (stage) {
      var sr = stage.getBoundingClientRect();
      var sp = clamp((vh - sr.top) / (vh * 0.9), 0, 1);
      stage.style.setProperty("--st", (0.9 + 0.1 * sp).toFixed(4));
      var drift = (sr.top + sr.height / 2 - vh / 2) / vh;
      chips.forEach(function (c) {
        c.style.setProperty("--py", (drift * parseFloat(c.getAttribute("data-depth"))).toFixed(1) + "px");
      });
    }

    if (words.length) {
      var mr = manifest.getBoundingClientRect();
      var mp = clamp((vh * 0.8 - mr.top) / (mr.height + vh * 0.2), 0, 1);
      var lit = Math.round(mp * words.length);
      for (var i = 0; i < words.length; i++) words[i].classList.toggle("on", i < lit);
    }

    if (pathFg) {
      var pr = pathFg.ownerSVGElement.getBoundingClientRect();
      pathFg.style.setProperty("--draw", (1 - clamp((vh - pr.top) / (vh * 0.55), 0, 1)).toFixed(4));
    }

    if (aboutImg) {
      var ar = aboutImg.parentNode.getBoundingClientRect();
      if (ar.bottom > 0 && ar.top < vh) {
        aboutImg.style.setProperty("--py", ((ar.top + ar.height / 2 - vh / 2) * -0.08 - ar.height * 0.06).toFixed(1) + "px");
      }
    }
  }
  function request() { if (!ticking) { ticking = true; requestAnimationFrame(onFrame); } }
  window.addEventListener("scroll", request, { passive: true });
  window.addEventListener("resize", request);
  onFrame();

  /* ---------- focus field: chaos of thoughts settles into a target ---------- */
  var fStage = $("#focus-stage"), fCanvas = $("#focus-canvas");
  var fVal = $("#fm-val"), fFill = $("#fm-fill");
  if (fStage && fCanvas && fCanvas.getContext) {
    var ctx = fCanvas.getContext("2d");
    var W = 0, H = 0, parts = [], order = reduced ? 1 : 0, orderTarget = reduced ? 1 : 0;
    var mouse = { x: -9999, y: -9999, on: false };
    var palette = [[22, 184, 217], [10, 132, 255], [124, 92, 255], [194, 100, 255], [255, 111, 174]];
    var rings = [[1, 0.44], [0.7, 0.3], [0.4, 0.18], [0.1, 0.08]];
    var mix = function (a, b, k) { return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k]; };
    var colorAt = function (t) {
      var x = ((t % 1) + 1) % 1 * (palette.length - 1), i = Math.floor(x);
      return mix(palette[i], palette[Math.min(i + 1, palette.length - 1)], x - i);
    };
    var build = function () {
      var dpr = Math.min(2, window.devicePixelRatio || 1);
      W = fStage.clientWidth; H = fStage.clientHeight;
      if (!W || !H) return;
      fCanvas.width = Math.round(W * dpr); fCanvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var n = W < 520 ? 280 : 480;
      parts = [];
      rings.forEach(function (ring, ri) {
        var count = Math.round(n * ring[1]);
        for (var j = 0; j < count; j++) {
          var ang = (j / count) * Math.PI * 2 + ri * 0.4;
          var c = colorAt(j / count * 0.9 + ri * 0.12);
          parts.push({
            ring: ri, rf: ring[0], ang: ang,
            x: Math.random() * W, y: Math.random() * H,
            vx: (Math.random() - 0.5) * 2.4, vy: (Math.random() - 0.5) * 2.4,
            dx: 0, dy: 0, delay: Math.random(),
            size: 1.3 + Math.random() * 1.7,
            col: "rgba(" + (c[0] | 0) + "," + (c[1] | 0) + "," + (c[2] | 0) + ","
          });
        }
      });
    };
    var ease = function (x) { return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; };
    var fRaf = 0, fStart = performance.now();
    var draw = function (now) {
      var t = (now - fStart) / 1000;
      order += (orderTarget - order) * 0.05;
      var cx = W / 2, cy = H / 2, R = Math.min(W, H) * 0.38;
      ctx.clearRect(0, 0, W, H);

      ctx.lineWidth = 1;
      for (var r = 0; r < 3; r++) {
        ctx.beginPath();
        ctx.strokeStyle = "rgba(124,92,255," + (0.1 * order).toFixed(3) + ")";
        ctx.arc(cx, cy, R * rings[r][0], 0, Math.PI * 2);
        ctx.stroke();
      }

      for (var i = 0; i < parts.length; i++) {
        var p = parts[i];
        if (!reduced) {
          p.vx += (Math.random() - 0.5) * 0.35; p.vy += (Math.random() - 0.5) * 0.35;
          p.vx *= 0.97; p.vy *= 0.97;
          p.x += p.vx; p.y += p.vy;
          if (p.x < 0 || p.x > W) { p.vx *= -1; p.x = clamp(p.x, 0, W); }
          if (p.y < 0 || p.y > H) { p.vy *= -1; p.y = clamp(p.y, 0, H); }
        }
        var spin = reduced ? 0 : t * (p.ring % 2 ? -0.12 : 0.09);
        var wob = reduced ? 0 : Math.sin(t * 1.6 + p.ang * 3) * 2.2;
        var tx = cx + Math.cos(p.ang + spin) * (R * p.rf + wob);
        var ty = cy + Math.sin(p.ang + spin) * (R * p.rf + wob);
        var k = ease(clamp((order - p.delay * 0.35) / 0.65, 0, 1));
        var px = p.x + (tx - p.x) * k + p.dx;
        var py = p.y + (ty - p.y) * k + p.dy;
        if (mouse.on) {
          var mdx = px - mouse.x, mdy = py - mouse.y, d2 = mdx * mdx + mdy * mdy;
          if (d2 < 9000) {
            var d = Math.sqrt(d2) || 1, f = (95 - d) * 0.35;
            p.dx += (mdx / d) * f; p.dy += (mdy / d) * f;
          }
        }
        p.dx *= 0.9; p.dy *= 0.9;
        ctx.beginPath();
        ctx.fillStyle = p.col + (0.55 + 0.4 * k).toFixed(2) + ")";
        ctx.arc(px, py, p.size * (0.8 + 0.35 * k), 0, Math.PI * 2);
        ctx.fill();
      }

      var pct = Math.round(order * 100);
      if (fVal) fVal.textContent = pct + " %";
      if (fFill) fFill.parentNode.style.setProperty("--f", order.toFixed(3));
      if (fFill) fFill.style.transform = "scaleX(" + order.toFixed(3) + ")";
      if (!reduced) fRaf = requestAnimationFrame(draw);
    };
    var updateOrder = function () {
      if (reduced) return;
      var r = fStage.getBoundingClientRect(), vh = window.innerHeight;
      orderTarget = clamp((vh * 0.95 - r.top) / (Math.min(r.height, vh * 0.6) * 1.1), 0, 1);
    };
    build();
    updateOrder();
    window.addEventListener("scroll", updateOrder, { passive: true });
    window.addEventListener("resize", function () { build(); updateOrder(); });
    fStage.addEventListener("pointermove", function (e) {
      var r = fStage.getBoundingClientRect();
      mouse.x = e.clientX - r.left; mouse.y = e.clientY - r.top; mouse.on = true;
    });
    fStage.addEventListener("pointerleave", function () { mouse.on = false; });
    if (reduced) {
      requestAnimationFrame(draw);
    } else if (hasIO) {
      new IntersectionObserver(function (en) {
        if (en[0].isIntersecting) { if (!fRaf) fRaf = requestAnimationFrame(draw); }
        else { cancelAnimationFrame(fRaf); fRaf = 0; }
      }).observe(fStage);
    } else {
      fRaf = requestAnimationFrame(draw);
    }
  }

  /* ---------- copy e-mail ---------- */
  var copyBtn = $("#copy-email");
  if (copyBtn) {
    var label = $(".copy-label", copyBtn);
    var emailEl = $("#email-text");
    var selectEmail = function () {
      var range = document.createRange();
      range.selectNodeContents(emailEl);
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    };
    copyBtn.addEventListener("click", function () {
      var done = function () {
        label.textContent = "Zkopírováno";
        setTimeout(function () { label.textContent = "Kopírovat"; }, 1800);
      };
      try { navigator.clipboard.writeText(emailEl.textContent.trim()).then(done, selectEmail); }
      catch (e) { selectEmail(); }
    });
  }
})();
