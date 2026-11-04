export type OcrMode = "ai" | "local" | "hybrid";

export interface PageInfo {
  id: string;
  number: number;
  position: number;
  label: string;
  width: number;
  height: number;
  rotation: number;
  content_type: "image" | "text" | "mixed" | "image_only" | "empty";
  source_kind: "image" | "pdf" | null;
  existing_text_chars: number;
  ocr_status: "none" | "done" | "failed" | "skipped";
  ocr_engine: string | null;
  ocr_model: string | null;
  ocr_error: string | null;
  warnings: string[];
  regions: number;
  uncertain: number;
  edited: number;
  unaligned: number;
  thumb_url: string;
  image_url: string;
  processed_url: string | null;
}

export interface ApiUsage {
  calls: number;
  succeeded: number;
  failed: number;
  uncertain: number;
  in_flight: number;
  retries: number;
  input_tokens: number;
  output_tokens: number;
  thinking_tokens: number;
  cost_usd: number;
  uncertain_cost_estimate_usd: number;
  model: string | null;
}

export interface JobProgress {
  id: string;
  document_id: string;
  status: "queued" | "running" | "paused" | "cancelled" | "completed" | "completed_with_errors" | "failed";
  mode: OcrMode;
  stop_reason: string | null;
  error: string | null;
  total_pages: number;
  completed_pages: number;
  failed_pages: number;
  pending_pages: number;
  running_pages: number;
  cancelled_pages: number;
  from_cache_pages: number;
  current_pages: number[];
  progress: number;
  remaining_pages: number;
  api: ApiUsage;
  cost_limit_usd: number | null;
  failed: { page_id: string; page_number: number; error: string }[];
  created_at: string | null;
  finished_at: string | null;
}

export interface ExportInfo {
  id: string;
  format: "pdf" | "txt" | "md" | "json";
  status: "queued" | "running" | "done" | "failed";
  size_bytes: number | null;
  error: string | null;
  report: {
    items?: number;
    position_ok?: number;
    position_ok_ratio?: number | null;
    extract_similarity_min?: number | null;
    unaligned_lines_excluded?: number;
    note?: string;
    pages?: { number: number; placed: number; position_ok: number; skipped_existing_text: number }[];
  };
  options: Record<string, string | null>;
  created_at: string;
  finished_at: string | null;
  download_url: string | null;
}

export interface DocumentInfo {
  id: string;
  title: string;
  page_size_mode: "original" | "a4";
  pdf_image_source: "original" | "enhanced";
  created_at: string;
  updated_at: string;
  page_count: number;
  ocr_done: number;
  ocr_failed: number;
  files: { id: string; name: string; kind: string; size: number; pages: number }[];
  pages?: PageInfo[];
  active_job?: JobProgress | null;
  exports?: ExportInfo[];
  thumb_url?: string | null;
  upload?: { added_pages: number; errors: { file: string; error: string }[]; note?: string };
}

export interface Region {
  id: string;
  key: string;
  order: number;
  block: number;
  block_type: string;
  polygon: [number, number][] | null;
  position_source: string;
  text: string;
  text_ocr: string;
  text_claude: string | null;
  text_local: string | null;
  text_user: string | null;
  engine: string;
  uncertain: boolean;
  reviewed: boolean;
  edited: boolean;
  aligned: boolean;
  flags: string[];
  alternatives: string[];
  uncertain_parts: string[];
  local_score: number | null;
}

export interface PageOcr {
  page: PageInfo;
  regions: Region[];
  preprocess: { steps?: { step: string; [k: string]: unknown }[]; timings?: Record<string, number> };
}

export interface Estimate {
  pages: number;
  model: string | null;
  estimated_cost_usd: number;
  is_estimate: boolean;
  per_page_usd: number;
  api_calls: number;
  basis: string;
  pricing_known?: boolean;
  pricing_checked?: string;
  price_per_mtok?: { input: number; output: number } | null;
}

export interface SettingsInfo {
  app_env: string;
  auth_mode: "none" | "password";
  claude: {
    configured: boolean;
    model: string;
    effort: string;
    fallbacks: string | null;
    pricing: { input: number; output: number; cache_read: number } | null;
    pricing_known: boolean;
    pricing_checked: string;
    image_limits: { long_edge_px: number; max_visual_tokens: number };
  };
  local_ocr: { available: boolean; det_model?: string; rec_model?: string; supports_hangul?: boolean; note?: string };
  limits: {
    max_file_mb: number;
    max_pages_per_document: number;
    max_pdf_pages_per_file: number;
    default_job_cost_limit_usd: number;
    daily_cost_limit_usd: number;
    retention_days: number;
  };
  notices: string[];
}
