(function () {
  var root = document.documentElement;
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var year = document.getElementById("year");
  if (year) year.textContent = new Date().getFullYear();

  // Theme toggle
  var themeBtn = document.getElementById("theme-btn");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var dark = root.dataset.theme
        ? root.dataset.theme === "dark"
        : window.matchMedia("(prefers-color-scheme: dark)").matches;
      var next = dark ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem("mc-theme", next); } catch (e) {}
    });
  }

  // Mobile menu
  var menuBtn = document.getElementById("menu-btn");
  var menu = document.getElementById("mobile-menu");
  function setMenu(open) {
    menu.hidden = !open;
    menuBtn.setAttribute("aria-expanded", String(open));
  }
  if (menuBtn && menu) {
    menuBtn.addEventListener("click", function () { setMenu(menu.hidden); });
    menu.addEventListener("click", function (e) { if (e.target.closest("a")) setMenu(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") setMenu(false); });
  }

  // Nav gets denser glass once the page scrolls
  var pill = document.querySelector(".nav-pill");
  function onScroll() { pill.classList.toggle("is-scrolled", window.scrollY > 8); }
  if (pill) {
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  // Scrollspy for nav links
  var links = Array.prototype.slice.call(document.querySelectorAll(".nav-links a"));
  if ("IntersectionObserver" in window && links.length) {
    var byId = {};
    links.forEach(function (a) { byId[a.getAttribute("href").slice(1)] = a; });
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        links.forEach(function (l) { l.classList.remove("is-active"); });
        if (byId[en.target.id]) byId[en.target.id].classList.add("is-active");
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    Object.keys(byId).forEach(function (id) {
      var s = document.getElementById(id);
      if (s) spy.observe(s);
    });
  }

  // Specular highlight follows the pointer across glass cards
  var specs = Array.prototype.slice.call(document.querySelectorAll(".spec"));
  specs.forEach(function (el) {
    el.addEventListener("pointermove", function (e) {
      var r = el.getBoundingClientRect();
      el.style.setProperty("--mx", (e.clientX - r.left) + "px");
      el.style.setProperty("--my", (e.clientY - r.top) + "px");
    });
  });

  // A light sweep crosses each card once when it scrolls into view
  if (!reduced && "IntersectionObserver" in window) {
    var sweep = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        var sib = Array.prototype.indexOf.call(en.target.parentNode.children, en.target);
        en.target.style.setProperty("--sd", (sib % 4) * 110 + "ms");
        en.target.classList.add("lit");
        sweep.unobserve(en.target);
      });
    }, { threshold: 0.3 });
    specs.forEach(function (el) { sweep.observe(el); });
  }

  // Hero orbs drift toward the pointer
  var orbs = Array.prototype.slice.call(document.querySelectorAll(".orb-wrap"));
  if (!reduced && orbs.length && window.matchMedia("(pointer: fine)").matches) {
    var tx = 0, ty = 0, cx = 0, cy = 0, running = false;
    var step = function () {
      cx += (tx - cx) * 0.06;
      cy += (ty - cy) * 0.06;
      orbs.forEach(function (o) {
        var d = parseFloat(o.dataset.depth) || 20;
        o.style.setProperty("--px", (cx * d).toFixed(2) + "px");
        o.style.setProperty("--py", (cy * d).toFixed(2) + "px");
      });
      if (Math.abs(tx - cx) > 0.001 || Math.abs(ty - cy) > 0.001) {
        requestAnimationFrame(step);
      } else {
        running = false;
      }
    };
    window.addEventListener("pointermove", function (e) {
      tx = e.clientX / window.innerWidth - 0.5;
      ty = e.clientY / window.innerHeight - 0.5;
      if (!running) { running = true; requestAnimationFrame(step); }
    }, { passive: true });
  }

  // Box breathing guide: 4 s inhale, hold, exhale, hold
  var breath = document.querySelector(".breath");
  if (breath && !reduced) {
    var track = breath.querySelector(".track");
    var runner = breath.querySelector(".runner");
    var glow = breath.querySelector(".runner-glow");
    var core = breath.querySelector(".core");
    var word = breath.querySelector(".breath-word");
    var count = breath.querySelector(".breath-count");
    var phases = ["Nádech", "Zádrž", "Výdech", "Zádrž"];
    var total = track.getTotalLength();
    var cycle = 16000;
    var t0 = performance.now();
    var raf = 0, lastPhase = -1, lastCount = -1;
    var ease = function (x) { return x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2; };

    var frame = function (now) {
      var t = (now - t0) % cycle;
      var ph = Math.floor(t / 4000);
      var p = (t % 4000) / 4000;
      var pt = track.getPointAtLength(total * (t / cycle));
      runner.setAttribute("cx", pt.x.toFixed(2));
      runner.setAttribute("cy", pt.y.toFixed(2));
      glow.setAttribute("cx", pt.x.toFixed(2));
      glow.setAttribute("cy", pt.y.toFixed(2));
      var s = ph === 0 ? 0.55 + 0.45 * ease(p) : ph === 1 ? 1 : ph === 2 ? 1 - 0.45 * ease(p) : 0.55;
      core.style.transform = "scale(" + s.toFixed(3) + ")";
      if (ph !== lastPhase) { word.textContent = phases[ph]; lastPhase = ph; }
      var c = 4 - Math.floor(p * 4);
      if (c !== lastCount) { count.textContent = c; lastCount = c; }
      raf = requestAnimationFrame(frame);
    };

    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (entries) {
        if (entries[0].isIntersecting) {
          if (!raf) raf = requestAnimationFrame(frame);
        } else {
          cancelAnimationFrame(raf);
          raf = 0;
        }
      }).observe(breath);
    } else {
      raf = requestAnimationFrame(frame);
    }
  }

  // Copy e-mail address
  var copyBtn = document.getElementById("copy-email");
  if (copyBtn) {
    var label = copyBtn.querySelector(".copy-label");
    var emailEl = document.getElementById("email-text");
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
        copyBtn.classList.add("done");
        setTimeout(function () {
          label.textContent = "Kopírovat";
          copyBtn.classList.remove("done");
        }, 1800);
      };
      try {
        navigator.clipboard.writeText(emailEl.textContent.trim()).then(done, selectEmail);
      } catch (e) {
        selectEmail();
      }
    });
  }
})();
