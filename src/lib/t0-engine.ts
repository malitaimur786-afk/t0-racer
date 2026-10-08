export const SPEED_WINS = [
  {
    id: "observer",
    title: "In-page observer",
    before: "Python asked the DOM every 50 ms over CDP — a round trip after the cell already existed.",
    after: "t0.js is injected at document-start. MutationObserver clicks the green td in the same turn it appears.",
  },
  {
    id: "burst",
    title: "Burst AVANTI",
    before: "A too-early press sat in an 8 second wait_for_function — through the actual drop.",
    after: "Re-press every ~100 ms for six seconds if the gate bounces. Midnight is not idle time.",
  },
  {
    id: "clock",
    title: "Server clock, sampled",
    before: "One HTTP Date header. Resolution: one second.",
    after: "Seven HEAD samples, RTT midpoint, median offset. Fire on the server’s midnight, not yours.",
  },
  {
    id: "arm",
    title: "Armed click",
    before: "Python sleep, then evaluate, then click. Two to eight milliseconds of jitter at T-0.",
    after: "performance.now() busy-wait in the page for the last 8 ms. Sub-millisecond press.",
  },
  {
    id: "reload",
    title: "Hot reload",
    before: "Playwright page.reload every 1.2 s on an empty calendar.",
    after: "In-page location.reload every 400 ms while hot — still one session, still bounded.",
  },
  {
    id: "service",
    title: "One-shot probes",
    before: "Service pick walked 400 nodes with a CDP inner_text each.",
    after: "One evaluate. Login, calendar, receipt: same pattern.",
  },
  {
    id: "captcha",
    title: "Faster token detect",
    before: "120 ms poll on the recaptcha textarea — that delay sat on the press.",
    after: "20 ms wait_for_function. You still solve the puzzle. T0 just notices sooner.",
  },
  {
    id: "chrome",
    title: "Unthrottled Chromium",
    before: "Background timer throttling and IPC flood protection on by default.",
    after: "Those flags off, trackers aborted, images blocked after the captcha, TLS kept warm.",
  },
] as const;
