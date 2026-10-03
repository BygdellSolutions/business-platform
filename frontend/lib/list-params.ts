import { isUuid } from "@/lib/uuid";

/**
 * List pages keep their filters in the URL (`?q=anna&active=active&page=2`): a plain GET form
 * sets them, the server component reads them, and the URL is the only state. Back/forward and
 * reloading therefore always show what the address says, for the organization in the address.
 *
 * Whatever is in the URL is untrusted input: unknown or malformed values are dropped, never
 * forwarded. The backend still validates everything it receives.
 */

export const PAGE_SIZE = 25;
const MAX_QUERY_LENGTH = 255;
const MAX_PAGE = 10_000;

export type ActiveFilter = "all" | "active" | "inactive";

export interface ListParams {
  q: string;
  active: ActiveFilter;
  /** One of the allowed values for the list (e.g. an item type), or "" for any. */
  type: string;
  /** Filters on another record, keyed by the backend parameter (e.g. owner_customer_id): a UUID. */
  refs: Record<string, string>;
  /** 1-based. */
  page: number;
}

type RawParams = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value) ?? "";
}

/**
 * `allowedTypes` are the values of the `type` filter the list offers; `refKeys` are the names
 * of the record filters it offers. Anything else in the address is ignored.
 */
export function parseListParams(raw: RawParams, allowedTypes: readonly string[] = [], refKeys: readonly string[] = []): ListParams {
  const active = first(raw.active);
  const type = first(raw.type);
  const page = first(raw.page);
  const refs: Record<string, string> = {};
  for (const key of refKeys) {
    const value = first(raw[key]);
    if (isUuid(value)) refs[key] = value.toLowerCase();
  }
  return {
    q: first(raw.q).trim().slice(0, MAX_QUERY_LENGTH),
    active: active === "active" || active === "inactive" ? active : "all",
    type: allowedTypes.includes(type) ? type : "",
    refs,
    page: /^[1-9]\d{0,4}$/.test(page) ? Math.min(parseInt(page, 10), MAX_PAGE) : 1,
  };
}

/**
 * The backend query string. One extra row is requested so the page knows whether a next page
 * exists without a separate count endpoint.
 */
export function backendQuery(params: ListParams): string {
  const query = new URLSearchParams({ limit: String(PAGE_SIZE + 1), offset: String((params.page - 1) * PAGE_SIZE) });
  if (params.q) query.set("q", params.q);
  if (params.active !== "all") query.set("active", params.active === "active" ? "true" : "false");
  if (params.type) query.set("type", params.type);
  for (const [key, value] of Object.entries(params.refs)) query.set(key, value);
  return `?${query.toString()}`;
}

/** The address for this list with some parameters changed; defaults are left out of the URL. */
export function listHref(base: string, params: ListParams, change: Partial<ListParams> = {}): string {
  const next = { ...params, ...change };
  const query = new URLSearchParams();
  if (next.q) query.set("q", next.q);
  if (next.active !== "all") query.set("active", next.active);
  if (next.type) query.set("type", next.type);
  for (const [key, value] of Object.entries(next.refs)) query.set(key, value);
  if (next.page > 1) query.set("page", String(next.page));
  const text = query.toString();
  return text ? `${base}?${text}` : base;
}

/** Splits the rows fetched with `backendQuery` into the visible page and "is there more". */
export function pageOf<T>(rows: T[]): { rows: T[]; hasNext: boolean } {
  return { rows: rows.slice(0, PAGE_SIZE), hasNext: rows.length > PAGE_SIZE };
}
