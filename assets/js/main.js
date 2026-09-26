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

  /* ---------- box breathing guide ---------- */
  var breath = $("#breath");
  if (breath && !reduced) {
    var track = $(".track", breath), runner = $(".runner", breath), rglow = $(".runner-glow", breath);
    var core = $(".core", breath), word = $(".breath-word", breath), count = $(".breath-count", breath);
    var phases = ["Nádech", "Zádrž", "Výdech", "Zádrž"];
    var total = track.getTotalLength(), cycle = 16000, t0 = performance.now();
    var raf = 0, lastPhase = -1, lastCount = -1;
    var ease = function (x) { return x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2; };
    var frame = function (now) {
      var t = (now - t0) % cycle, ph = Math.floor(t / 4000), p = (t % 4000) / 4000;
      var pt = track.getPointAtLength(total * (t / cycle));
      runner.setAttribute("cx", pt.x.toFixed(2)); runner.setAttribute("cy", pt.y.toFixed(2));
      rglow.setAttribute("cx", pt.x.toFixed(2)); rglow.setAttribute("cy", pt.y.toFixed(2));
      var s = ph === 0 ? 0.55 + 0.45 * ease(p) : ph === 1 ? 1 : ph === 2 ? 1 - 0.45 * ease(p) : 0.55;
      core.style.transform = "scale(" + s.toFixed(3) + ")";
      if (ph !== lastPhase) { word.textContent = phases[ph]; lastPhase = ph; }
      var c = 4 - Math.floor(p * 4);
      if (c !== lastCount) { count.textContent = c; lastCount = c; }
      raf = requestAnimationFrame(frame);
    };
    if (hasIO) {
      new IntersectionObserver(function (en) {
        if (en[0].isIntersecting) { if (!raf) raf = requestAnimationFrame(frame); }
        else { cancelAnimationFrame(raf); raf = 0; }
      }).observe(breath);
    } else {
      raf = requestAnimationFrame(frame);
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
