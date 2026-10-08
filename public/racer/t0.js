/* T0 in-page racer — injected at document start on every navigation.
 * Clicks happen in the same turn a green cell appears (MutationObserver),
 * with rAF as backup. Python never polls the DOM in the hot path.
 * Top-frame only; disabled unless window.__T0_MODE === 'race'.
 */
(function () {
  if (window !== window.top) return;
  if (window.__T0) return;

  var MODE = window.__T0_MODE || "off";
  var GREEN_RX = /green|dispon|available|prenotab|bookable|\bok\b|open|attivo|success|libero/i;
  var RED_RX = /red|rosso|non[\-]?disp|unavail|disabled|closed|full|passat|error|scadut|busy|occupat/i;
  var DAY_RX = /^\d{1,2}$/;
  var SLOT_RX = /^\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2}$/;
  var AVANTI_RX = /^avanti\b/i;
  var PRENOTA_RX = /^prenot(a|ami|are|azione)\b/i;
  var CONFERMA_RX = /^conferma\b/i;
  var RECEIPT_RX = /prenotazione\s+confermat|prenotato\s+confermat|conferma\s+di\s+prenotaz|booking\s+confirm|riepilogo\s+della\s+prenotazione/i;
  var EMPTY_RX = /nessun[ae]?\s+(appuntamento|risultat|disponibil)|non\s+sono\s+disponibil|non\s+ci\s+sono\s+posti|al\s+momento\s+non/i;

  var T0 = (window.__T0 = {
    enabled: MODE === "race",
    booked: false,
    phase: "idle",
    clickAt: 0,
    lastDay: "",
    lastSlot: "",
    lastPrenotaWall: 0,
    avantiFired: false,
    reloadCount: 0,
    cfg: { reloadHotMs: 400, reloadSteadyMs: 1200, hotWindowMs: 40000, dropEpochMs: 0, maxReloads: 180 },
    stats: { start: 0, dayMs: 0, slotMs: 0, bookMs: 0 },
  });

  function say(msg) {
    try {
      console.info("[T0] " + msg);
    } catch (e) {}
  }

  function rgb(str) {
    var m = (str || "").match(/[\d.]+/g);
    return m && m.length >= 3 ? [+m[0], +m[1], +m[2]] : null;
  }
  function greenish(cs) {
    var c = rgb(cs.backgroundColor);
    return !!(c && c[1] > 90 && c[1] > c[0] + 25 && c[1] > c[2] + 25);
  }
  function reddish(cs) {
    var c = rgb(cs.backgroundColor);
    return !!(c && c[0] > 140 && c[1] < 110 && c[2] < 110);
  }
  function isDead(el, holder) {
    if (holder.classList && holder.classList.contains("disabled")) return true;
    if (el.classList && el.classList.contains("disabled")) return true;
    if (holder.getAttribute && holder.getAttribute("aria-disabled") === "true") return true;
    if (el.getAttribute && el.getAttribute("aria-disabled") === "true") return true;
    if (el.disabled) return true;
    return false;
  }
  function isGreenHolder(el, holder) {
    var cls = String(holder.className || "") + " " + String(el.className || "");
    if (RED_RX.test(cls) || isDead(el, holder)) return false;
    if (GREEN_RX.test(cls)) return true;
    try {
      var cs = getComputedStyle(holder);
      if (reddish(cs)) return false;
      if (greenish(cs)) return true;
      if (holder !== el) {
        var cs2 = getComputedStyle(el);
        if (greenish(cs2) && !reddish(cs2)) return true;
      }
    } catch (e) {}
    return false;
  }
  function visible(el) {
    if (!el) return false;
    if (el.disabled) return false;
    var st = el.style && el.style.display;
    if (st === "none") return false;
    return true;
  }
  function fire(el) {
    if (!el || !visible(el)) return false;
    try {
      el.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true, pointerType: "mouse" }));
      el.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, cancelable: true, pointerType: "mouse" }));
    } catch (e) {}
    try {
      el.click();
      if (el.tagName === "BUTTON" && el.type === "submit" && el.form) {
        try {
          el.form.requestSubmit(el);
        } catch (e2) {}
      }
      return true;
    } catch (e) {
      try {
        el.click();
        return true;
      } catch (e3) {
        return false;
      }
    }
  }
  function labelOf(el) {
    return ((el && (el.value || el.textContent)) || "").replace(/\s+/g, " ").trim();
  }
  function findByRx(rx, extra) {
    var nodes = document.querySelectorAll("button, input[type='submit'], a" + (extra || ""));
    for (var i = 0; i < nodes.length; i++) {
      var t = labelOf(nodes[i]);
      if (rx.test(t) && visible(nodes[i])) return nodes[i];
    }
    return null;
  }
  function findGreenDay() {
    var nodes = document.querySelectorAll("td, a, button, li, span, div, .day");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var t = (el.textContent || "").trim();
      if (!DAY_RX.test(t)) continue;
      var n = +t;
      if (n < 1 || n > 31) continue;
      if (el.children && el.children.length > 2) continue;
      var holder = (el.closest && (el.closest("td") || el.closest(".day"))) || el;
      if (isGreenHolder(el, holder)) return el;
    }
    return null;
  }
  function findGreenSlot() {
    var nodes = document.querySelectorAll("td, a, button, li, span, div, option, label, .slot");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var t = (el.textContent || "").replace(/\s+/g, " ").trim();
      if (!SLOT_RX.test(t)) continue;
      if (el.children && el.children.length > 2) continue;
      var holder = (el.closest && (el.closest(".slot") || el.closest("li") || el.closest("td"))) || el;
      if (isGreenHolder(el, holder)) return el;
    }
    return null;
  }
  function bodyText() {
    return document.body ? document.body.innerText || "" : "";
  }
  function isReceipt() {
    return RECEIPT_RX.test(bodyText());
  }
  function looksLikeCalendar() {
    var n = document.querySelectorAll("table td, .day, .datepicker td, .ui-datepicker-calendar td").length;
    if (n >= 7) return true;
    return n > 0 && /calendario|calendar/i.test(bodyText());
  }
  function emptyCal() {
    return EMPTY_RX.test(bodyText());
  }
  function captchaOk() {
    var sels = [
      'textarea[name="g-recaptcha-response"]',
      'input[name="g-recaptcha-response"]',
      'textarea[name="h-captcha-response"]',
      'input[name="h-captcha-response"]',
      '[name="cf-turnstile-response"]',
    ];
    for (var i = 0; i < sels.length; i++) {
      var el = document.querySelector(sels[i]);
      if (el && el.value && String(el.value).trim().length > 15) return true;
    }
    var b = document.body;
    return !!(b && b.getAttribute("data-captcha-ok") === "true");
  }
  function captchaWidget() {
    return !!document.querySelector(
      'iframe[src*="recaptcha"], iframe[title*="reCAPTCHA"], iframe[title*="captcha"], ' +
        'iframe[src*="hcaptcha"], .g-recaptcha, .h-captcha, .cf-turnstile, .grecaptcha-badge'
    );
  }

  function maybeReload() {
    if (!T0.enabled || T0.booked || T0.lastDay) return;
    if (!looksLikeCalendar() && !emptyCal()) return;
    var now = Date.now();
    var hotUntil = T0.cfg.dropEpochMs ? T0.cfg.dropEpochMs + T0.cfg.hotWindowMs : now + T0.cfg.hotWindowMs;
    var gap = now < hotUntil ? T0.cfg.reloadHotMs : T0.cfg.reloadSteadyMs;
    var last = 0;
    try {
      last = +(sessionStorage.getItem("__t0_rl") || 0);
    } catch (e) {}
    if (now - last < gap) return;
    if (!emptyCal() && looksLikeCalendar()) return;
    if (T0.reloadCount >= T0.cfg.maxReloads) return;
    T0.reloadCount += 1;
    try {
      sessionStorage.setItem("__t0_rl", String(now));
    } catch (e2) {}
    say("calendar empty — reloading (gap " + gap + " ms)");
    try {
      location.reload();
    } catch (e3) {}
  }

  function tick() {
    if (!T0.enabled || T0.booked) return;
    if (isReceipt()) {
      T0.booked = true;
      T0.phase = "booked";
      T0.stats.bookMs = performance.now() - (T0.stats.start || performance.now());
      say("BOOKING CONFIRMED");
      return;
    }

    if (T0.phase === "armed" && T0.clickAt) {
      var left = T0.clickAt - performance.now();
      if (left <= 8) {
        if (left > 0) {
          var target = T0.clickAt;
          while (performance.now() < target) {}
        }
        if (!T0.avantiFired) {
          var btn = findByRx(AVANTI_RX);
          if (btn && fire(btn)) {
            T0.avantiFired = true;
            T0.phase = "racing";
            T0.stats.start = performance.now();
            say("AVANTI pressed at " + stamp());
          }
        }
      }
    }

    if (looksLikeCalendar()) {
      if (T0.phase !== "racing" && T0.phase !== "booked") {
        T0.phase = "racing";
        T0.stats.start = T0.stats.start || performance.now();
      }
    }

    if (T0.phase === "racing") {
      if (!T0.lastDay) {
        var day = findGreenDay();
        if (day && fire(day)) {
          T0.lastDay = (day.textContent || "").trim();
          T0.stats.dayMs = performance.now() - T0.stats.start;
          say("GREEN DATE picked: " + T0.lastDay);
        }
      }
      if (!T0.lastSlot) {
        var slot = findGreenSlot();
        if (slot && fire(slot)) {
          T0.lastSlot = (slot.textContent || "").replace(/\s+/g, " ").trim();
          T0.stats.slotMs = performance.now() - T0.stats.start;
          say("GREEN SLOT picked: " + T0.lastSlot);
        }
      }
      if (T0.lastSlot && !T0.lastPrenotaWall) {
        var pren = findByRx(PRENOTA_RX);
        if (pren && fire(pren)) {
          T0.lastPrenotaWall = Date.now();
          say("PRENOTA pressed at " + stamp());
        }
      }
      var conf = findByRx(CONFERMA_RX);
      if (conf) fire(conf);
      maybeReload();
    }
  }

  function stamp() {
    var d = new Date();
    function z(n, w) {
      var s = String(n);
      while (s.length < w) s = "0" + s;
      return s;
    }
    return z(d.getHours(), 2) + ":" + z(d.getMinutes(), 2) + ":" + z(d.getSeconds(), 2) + "." + z(d.getMilliseconds(), 3);
  }

  function loop() {
    tick();
    if (!T0.booked) requestAnimationFrame(loop);
  }

  var mo = new MutationObserver(function () {
    tick();
  });
  function armObserver() {
    try {
      mo.observe(document.documentElement, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["class", "style", "disabled", "aria-disabled"],
      });
    } catch (e) {}
  }

  T0.configure = function (c) {
    if (!c) return;
    if (c.reloadHotMs) T0.cfg.reloadHotMs = +c.reloadHotMs;
    if (c.reloadSteadyMs) T0.cfg.reloadSteadyMs = +c.reloadSteadyMs;
    if (c.hotWindowMs) T0.cfg.hotWindowMs = +c.hotWindowMs;
    if (c.dropEpochMs) T0.cfg.dropEpochMs = +c.dropEpochMs;
    if (c.maxReloads) T0.cfg.maxReloads = +c.maxReloads;
  };
  T0.arm = function (clickAtPerf) {
    T0.enabled = true;
    T0.booked = false;
    T0.avantiFired = false;
    T0.clickAt = clickAtPerf || 0;
    T0.phase = clickAtPerf ? "armed" : "racing";
    T0.stats = { start: performance.now(), dayMs: 0, slotMs: 0, bookMs: 0 };
  };
  T0.raceNow = function () {
    T0.enabled = true;
    T0.phase = "racing";
    T0.stats.start = T0.stats.start || performance.now();
    tick();
  };
  T0.status = function () {
    return {
      booked: T0.booked,
      phase: T0.phase,
      lastDay: T0.lastDay,
      lastSlot: T0.lastSlot,
      lastPrenotaWall: T0.lastPrenotaWall,
      avantiFired: T0.avantiFired,
      stats: T0.stats,
      captchaOk: captchaOk(),
      captchaWidget: captchaWidget(),
    };
  };
  T0.captchaOk = captchaOk;
  T0.tick = tick;
  T0.say = say;

  if (document.documentElement) armObserver();
  requestAnimationFrame(loop);
  tick();
})();
