(function () {
  var root = document.documentElement;
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var finePointer = window.matchMedia("(pointer: fine)").matches;
  var hasIO = "IntersectionObserver" in window;
  var clamp = function (v, a, b) { return Math.min(b, Math.max(a, v)); };
  var $ = function (s, c) { return (c || document).querySelector(s); };
  var $$ = function (s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); };

  if (!reduced && hasIO) root.classList.add("js-anim");

  var year = $("#year");
  if (year) year.textContent = new Date().getFullYear();

  /* ---------- aurora shader ---------- */
  var pointer = { x: 0.5, y: 0.5, sx: 0.5, sy: 0.5 };
  (function aurora() {
    var canvas = $("#aurora");
    var fallback = $("#aurora-fallback");
    if (!canvas) return;
    var gl = null;
    try { gl = canvas.getContext("webgl", { antialias: false, alpha: false, powerPreference: "low-power" }); } catch (e) {}
    if (!gl) { canvas.hidden = true; if (fallback) fallback.hidden = false; return; }

    var vs = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
    var fs = [
      "precision mediump float;",
      "uniform vec2 r;uniform float t;uniform vec2 m;uniform float s;",
      "float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}",
      "float n(vec2 p){vec2 i=floor(p),f=fract(p);vec2 u=f*f*(3.-2.*f);",
      "return mix(mix(h(i),h(i+vec2(1.,0.)),u.x),mix(h(i+vec2(0.,1.)),h(i+vec2(1.,1.)),u.x),u.y);}",
      "float fbm(vec2 p){float v=0.,a=.5;for(int i=0;i<5;i++){v+=a*n(p);p=p*2.02+vec2(1.7,9.2);a*=.5;}return v;}",
      "void main(){",
      " vec2 p=(gl_FragCoord.xy-.5*r)/r.y;",
      " float T=t*.035;",
      " vec2 q=vec2(fbm(p*1.3+T),fbm(p*1.3-T+3.1));",
      " vec2 w=vec2(fbm(p*1.1+2.*q+vec2(1.7,9.2)+T*1.2),fbm(p*1.1+2.*q+vec2(8.3,2.8)-T));",
      " float f=fbm(p*1.05+2.4*w);",
      " vec3 bg=vec3(.024,.027,.043);",
      " vec3 blue=vec3(.30,.49,1.);vec3 vio=vec3(.56,.42,1.);vec3 cy=vec3(.18,.83,.91);",
      " vec3 col=mix(blue,vio,clamp(w.x*1.5-.25,0.,1.));",
      " col=mix(col,cy,clamp(q.y*q.y*1.8-.4,0.,1.));",
      " float glow=smoothstep(.38,.98,f);",
      " float md=length(p-m);glow+=.35*exp(-md*md*5.);",
      " vec3 c=bg+col*glow*.5*s;",
      " c*=.55+.45*smoothstep(1.5,0.,length(p*vec2(.8,1.)));",
      " gl_FragColor=vec4(c,1.);",
      "}"
    ].join("\n");

    function sh(type, src) {
      var o = gl.createShader(type);
      gl.shaderSource(o, src);
      gl.compileShader(o);
      return gl.getShaderParameter(o, gl.COMPILE_STATUS) ? o : null;
    }
    var v = sh(gl.VERTEX_SHADER, vs), f = sh(gl.FRAGMENT_SHADER, fs);
    if (!v || !f) { canvas.hidden = true; if (fallback) fallback.hidden = false; return; }
    var prog = gl.createProgram();
    gl.attachShader(prog, v); gl.attachShader(prog, f); gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) { canvas.hidden = true; if (fallback) fallback.hidden = false; return; }
    gl.useProgram(prog);
    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    var loc = gl.getAttribLocation(prog, "p");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    var uR = gl.getUniformLocation(prog, "r"), uT = gl.getUniformLocation(prog, "t"),
        uM = gl.getUniformLocation(prog, "m"), uS = gl.getUniformLocation(prog, "s");

    var scale = 0.45;
    function resize() {
      var w = Math.max(1, Math.round(window.innerWidth * scale));
      var h = Math.max(1, Math.round(window.innerHeight * scale));
      canvas.width = w; canvas.height = h;
      gl.viewport(0, 0, w, h);
      gl.uniform2f(uR, w, h);
    }
    resize();
    window.addEventListener("resize", resize);

    var start = performance.now(), raf = 0;
    function draw(now) {
      pointer.sx += (pointer.x - pointer.sx) * 0.04;
      pointer.sy += (pointer.y - pointer.sy) * 0.04;
      var aspect = window.innerWidth / window.innerHeight;
      gl.uniform2f(uM, (pointer.sx - 0.5) * aspect, 0.5 - pointer.sy);
      gl.uniform1f(uT, (now - start) / 1000 + 20);
      var fade = 1 - 0.4 * clamp(window.scrollY / window.innerHeight, 0, 1);
      gl.uniform1f(uS, fade);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      if (!reduced) raf = requestAnimationFrame(draw);
    }
    raf = requestAnimationFrame(draw);
    document.addEventListener("visibilitychange", function () {
      if (reduced) return;
      if (document.hidden) { cancelAnimationFrame(raf); raf = 0; }
      else if (!raf) raf = requestAnimationFrame(draw);
    });
  })();

  window.addEventListener("pointermove", function (e) {
    pointer.x = e.clientX / window.innerWidth;
    pointer.y = e.clientY / window.innerHeight;
  }, { passive: true });

  /* ---------- cursor glow ---------- */
  var glowEl = $("#cursor-glow");
  if (glowEl && finePointer && !reduced) {
    glowEl.hidden = false;
    var gx = -999, gy = -999, tgx = -999, tgy = -999, gRun = false;
    var glowStep = function () {
      gx += (tgx - gx) * 0.12; gy += (tgy - gy) * 0.12;
      glowEl.style.setProperty("--cx", gx.toFixed(1) + "px");
      glowEl.style.setProperty("--cy", gy.toFixed(1) + "px");
      if (Math.abs(tgx - gx) > 0.5 || Math.abs(tgy - gy) > 0.5) requestAnimationFrame(glowStep); else gRun = false;
    };
    window.addEventListener("pointermove", function (e) {
      if (gx === -999) { gx = e.clientX; gy = e.clientY; }
      tgx = e.clientX; tgy = e.clientY;
      if (!gRun) { gRun = true; requestAnimationFrame(glowStep); }
    }, { passive: true });
  }

  /* ---------- nav ---------- */
  var nav = $("#site-nav");
  var progress = $("#nav-progress");
  var menuBtn = $("#menu-btn");
  var menu = $("#mobile-menu");
  var menuOpen = false;
  function setMenu(open) {
    menuOpen = open;
    menu.hidden = !open;
    menuBtn.setAttribute("aria-expanded", String(open));
    root.style.overflow = open ? "hidden" : "";
    if (open) nav.classList.remove("is-hidden");
  }
  if (menuBtn && menu) {
    menuBtn.addEventListener("click", function () { setMenu(!menuOpen); });
    menu.addEventListener("click", function (e) { if (e.target.closest("a")) setMenu(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && menuOpen) setMenu(false); });
  }

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
  if (root.classList.contains("js-anim")) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -6% 0px" });
    $$(".rv, .rv-mask").forEach(function (el) { io.observe(el); });
  }

  /* ---------- manifest words ---------- */
  var manifest = $("#manifest");
  var words = [];
  if (manifest && root.classList.contains("js-anim")) {
    var wrapWords = function (node) {
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
          wrapWords(child);
        }
      });
    };
    wrapWords(manifest);
  }

  /* ---------- scroll-driven scenes ---------- */
  var cards = $$(".stack-card");
  var pathFg = $("#path-art .path-fg");
  var stepsWrap = $("#steps-wrap");
  var stepsLine = stepsWrap ? $(".steps-line", stepsWrap) : null;
  var steps = $$(".step");
  var aboutImg = $("#about-photo img");
  var lastY = window.scrollY;
  var ticking = false;

  function onFrame() {
    ticking = false;
    var vh = window.innerHeight;
    var y = window.scrollY;
    var docH = document.documentElement.scrollHeight - vh;

    if (progress) progress.style.setProperty("--p", docH > 0 ? (y / docH).toFixed(4) : 0);
    if (nav && !menuOpen) {
      if (y > 420 && y - lastY > 6) nav.classList.add("is-hidden");
      else if (lastY - y > 6 || y < 200) nav.classList.remove("is-hidden");
    }
    lastY = y;

    if (reduced) return;

    if (words.length) {
      var mr = manifest.getBoundingClientRect();
      var mp = clamp((vh * 0.82 - mr.top) / (mr.height + vh * 0.25), 0, 1);
      var lit = Math.round(mp * words.length);
      for (var i = 0; i < words.length; i++) words[i].classList.toggle("on", i < lit);
    }

    for (var c = 0; c < cards.length - 1; c++) {
      var cur = cards[c].getBoundingClientRect();
      var next = cards[c + 1].getBoundingClientRect();
      var t = clamp(1 - (next.top - cur.top) / cur.height, 0, 1);
      cards[c].style.setProperty("--s", (1 - t * 0.07).toFixed(4));
      cards[c].style.setProperty("--b", (1 - t * 0.55).toFixed(3));
    }

    if (pathFg) {
      var pr = pathFg.ownerSVGElement.getBoundingClientRect();
      var pp = clamp((vh - pr.top) / (vh * 0.6), 0, 1);
      pathFg.style.setProperty("--draw", (1 - pp).toFixed(4));
    }

    if (stepsWrap && stepsLine) {
      var sr = stepsWrap.getBoundingClientRect();
      var sp = clamp((vh * 0.78 - sr.top) / (sr.height * 0.9), 0, 1);
      stepsLine.style.setProperty("--p", sp.toFixed(4));
      var vertical = window.innerWidth <= 860;
      steps.forEach(function (st) {
        var pos = vertical ? st.offsetTop / sr.height : st.offsetLeft / sr.width;
        st.classList.toggle("on", sp >= pos + 0.01);
      });
    }

    if (aboutImg) {
      var ar = aboutImg.parentNode.getBoundingClientRect();
      if (ar.bottom > 0 && ar.top < vh) {
        var off = (ar.top + ar.height / 2 - vh / 2) * -0.1;
        aboutImg.style.setProperty("--py", off.toFixed(1) + "px");
      }
    }
  }
  function requestFrame() { if (!ticking) { ticking = true; requestAnimationFrame(onFrame); } }
  window.addEventListener("scroll", requestFrame, { passive: true });
  window.addEventListener("resize", requestFrame);
  onFrame();

  /* ---------- hero portrait: clarity lens + tilt ---------- */
  var portrait = $("#hero-portrait");
  var frame = $("#portrait-frame");
  if (portrait && frame && !reduced) {
    var lx = 0.62, ly = 0.34, tlx = lx, tly = ly, hovering = false, lensRaf = 0, heroVisible = true;
    var t0 = performance.now();
    var lensStep = function (now) {
      if (!hovering) {
        var k = (now - t0) / 1000;
        tlx = 0.5 + 0.22 * Math.sin(k * 0.55);
        tly = 0.38 + 0.16 * Math.sin(k * 0.8 + 1.2);
      }
      lx += (tlx - lx) * 0.08; ly += (tly - ly) * 0.08;
      var w = frame.clientWidth, h = frame.clientHeight;
      frame.style.setProperty("--lr", Math.round(w * 0.3) + "px");
      frame.style.setProperty("--lx", (lx * 100).toFixed(2) + "%");
      frame.style.setProperty("--ly", (ly * 100).toFixed(2) + "%");
      frame.style.setProperty("--lxp", (lx * w).toFixed(1) + "px");
      frame.style.setProperty("--lyp", (ly * h).toFixed(1) + "px");
      lensRaf = heroVisible ? requestAnimationFrame(lensStep) : 0;
    };
    lensRaf = requestAnimationFrame(lensStep);
    if (hasIO) {
      new IntersectionObserver(function (en) {
        heroVisible = en[0].isIntersecting;
        if (heroVisible && !lensRaf) lensRaf = requestAnimationFrame(lensStep);
      }).observe(portrait);
    }
    frame.addEventListener("pointermove", function (e) {
      var r = frame.getBoundingClientRect();
      var x = (e.clientX - r.left) / r.width, yy = (e.clientY - r.top) / r.height;
      hovering = true; tlx = x; tly = yy;
      if (finePointer) {
        portrait.style.setProperty("--rx", ((0.5 - yy) * 8).toFixed(2) + "deg");
        portrait.style.setProperty("--ry", ((x - 0.5) * 10).toFixed(2) + "deg");
      }
    });
    frame.addEventListener("pointerleave", function () {
      hovering = false;
      portrait.style.setProperty("--rx", "0deg");
      portrait.style.setProperty("--ry", "0deg");
    });
  }

  /* ---------- magnetic buttons ---------- */
  if (finePointer && !reduced) {
    $$(".magnetic").forEach(function (el) {
      el.addEventListener("pointermove", function (e) {
        var r = el.getBoundingClientRect();
        var dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2);
        el.style.transform = "translate(" + (dx * 0.28).toFixed(1) + "px," + (dy * 0.4).toFixed(1) + "px)";
      });
      el.addEventListener("pointerleave", function () { el.style.transform = ""; });
    });
  }

  /* ---------- box breathing guide ---------- */
  var breath = $("#breath");
  if (breath && !reduced) {
    var track = $(".track", breath), runner = $(".runner", breath), rglow = $(".runner-glow", breath);
    var core = $(".core", breath), word = $(".breath-word", breath), count = $(".breath-count", breath);
    var phases = ["Nádech", "Zádrž", "Výdech", "Zádrž"];
    var total = track.getTotalLength(), cycle = 16000, bt0 = performance.now();
    var braf = 0, lastPhase = -1, lastCount = -1;
    var ease = function (x) { return x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2; };
    var bframe = function (now) {
      var t = (now - bt0) % cycle, ph = Math.floor(t / 4000), p = (t % 4000) / 4000;
      var pt = track.getPointAtLength(total * (t / cycle));
      runner.setAttribute("cx", pt.x.toFixed(2)); runner.setAttribute("cy", pt.y.toFixed(2));
      rglow.setAttribute("cx", pt.x.toFixed(2)); rglow.setAttribute("cy", pt.y.toFixed(2));
      var s = ph === 0 ? 0.55 + 0.45 * ease(p) : ph === 1 ? 1 : ph === 2 ? 1 - 0.45 * ease(p) : 0.55;
      core.style.transform = "scale(" + s.toFixed(3) + ")";
      if (ph !== lastPhase) { word.textContent = phases[ph]; lastPhase = ph; }
      var cnt = 4 - Math.floor(p * 4);
      if (cnt !== lastCount) { count.textContent = cnt; lastCount = cnt; }
      braf = requestAnimationFrame(bframe);
    };
    if (hasIO) {
      new IntersectionObserver(function (en) {
        if (en[0].isIntersecting) { if (!braf) braf = requestAnimationFrame(bframe); }
        else { cancelAnimationFrame(braf); braf = 0; }
      }).observe(breath);
    } else {
      braf = requestAnimationFrame(bframe);
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
