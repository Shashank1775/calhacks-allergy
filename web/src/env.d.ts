/// <reference types="vite/client" />

/** Build-time settings (web-framework decision). Never put a secret in one. */
interface ImportMetaEnv {
  readonly WEB_STATE_SOURCE?: string;
  readonly WEB_SCENARIO?: string;
  readonly WEB_STATE_URL?: string;
  readonly WEB_ORDERS_URL?: string;
  readonly WEB_VISION_URL?: string;
  readonly WEB_SUMMARY_URL?: string;
  readonly WEB_MOCK_API?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
