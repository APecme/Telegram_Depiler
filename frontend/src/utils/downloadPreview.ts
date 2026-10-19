export const PREVIEW_PAGE_SIZE = 30;

type PreviewRecord = { id: number; created_at: string };

export type PreviewHistory<T extends PreviewRecord> = {
  items: T[];
  page: number;
  hasMore: boolean;
};

export function applyPreviewPage<T extends PreviewRecord>(
  previous: PreviewHistory<T>, items: T[], page: number, total: number,
  received: PreviewRecord[] = items,
): PreviewHistory<T> {
  if (total === 0) return { items: [], page: 1, hasMore: false };
  const newestFirst = (a: PreviewRecord, b: PreviewRecord) =>
    b.created_at.localeCompare(a.created_at) || b.id - a.id;
  const newest = received[0];
  const oldest = received[received.length - 1];
  // Only replace the range covered by this response. Older loaded pages stay
  // visible, while deleted or unavailable records in the refreshed range leave.
  const retained = previous.items.filter(item => {
    if (page === 1 && total <= PREVIEW_PAGE_SIZE) return false;
    if (!newest || !oldest) return true;
    const inRange = (page === 1 || newestFirst(item, newest) >= 0)
      && (page * PREVIEW_PAGE_SIZE >= total || newestFirst(item, oldest) <= 0);
    return !inRange;
  });
  const byId = new Map(retained.map(item => [item.id, item]));
  items.forEach(item => byId.set(item.id, item));
  const loadedPage = Math.min(Math.max(previous.page, page), Math.ceil(total / PREVIEW_PAGE_SIZE));
  return {
    items: Array.from(byId.values()).sort(newestFirst),
    page: loadedPage,
    hasMore: loadedPage * PREVIEW_PAGE_SIZE < total,
  };
}
