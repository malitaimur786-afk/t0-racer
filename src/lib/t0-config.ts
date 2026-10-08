export type RacerConfig = {
  email: string;
  password: string;
  passport_number: string;
  service_match: string;
  service_url: string;
  drop_time: string;
  timezone: string;
  submit: "at_drop" | "early";
  burst_s: number;
  reload_hot_s: number;
  race_window_s: number;
};

export const DEFAULT_CONFIG: RacerConfig = {
  email: "",
  password: "",
  passport_number: "",
  service_match: "legaliz|legalizz|d\\.o\\.v|legalis",
  service_url: "",
  drop_time: "00:00",
  timezone: "Europe/Rome",
  submit: "at_drop",
  burst_s: 6,
  reload_hot_s: 0.4,
  race_window_s: 120,
};

const KEY = "t0-racer-config-v1";

export function loadConfig(): RacerConfig {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULT_CONFIG };
    return { ...DEFAULT_CONFIG, ...JSON.parse(raw) };
  } catch {
    return { ...DEFAULT_CONFIG };
  }
}

export function saveConfig(cfg: RacerConfig): void {
  localStorage.setItem(KEY, JSON.stringify(cfg));
}

export function toConfigJson(cfg: RacerConfig): string {
  const body = {
    base_url: "https://prenotami.esteri.it",
    email: cfg.email || "you@example.com",
    password: cfg.password || "your-password-here",
    skip_login: false,
    login_assist_s: 420,
    service_match: cfg.service_match,
    service_url: cfg.service_url,
    selects: [
      { match: "", pick: "first" },
      { match: "tunisi", pick: "option_re:tunisi" },
    ],
    passport_number: cfg.passport_number || "AB1234567",
    drop: { time: cfg.drop_time, timezone: cfg.timezone, start_early_s: 0.0 },
    strategy: {
      submit: cfg.submit,
      early_lead_s: 25.0,
      race_window_s: cfg.race_window_s,
      poll_ms: 200,
      hot_poll_ms: 16,
      hot_window_s: 40.0,
      reload_if_empty_s: 1.5,
      reload_hot_s: cfg.reload_hot_s,
      burst_s: cfg.burst_s,
      advance_poll_ms: 16,
    },
    captcha: { wait_s: 900.0 },
    captcha_open_s: 150.0,
    warmup_lead_s: 480.0,
    keepalive_s: 12,
    block_assets: true,
    clock: { apply_server_skew: true, samples: 7 },
    browser: { headless: false, profile_dir: "./profile", nav_timeout_s: 45.0 },
    screenshots_dir: "./shots",
    keep_open_s: 60,
    on_success: "",
  };
  return JSON.stringify(body, null, 2);
}
