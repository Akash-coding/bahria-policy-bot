import { FormEvent, useEffect, useState } from "react";
import { api, type WebsiteRecord } from "../api";

const ACTIVE = new Set(["starting", "discovering", "scraping", "processing", "saving"]);

const FILE_LABELS: Record<string, string> = {
  html: "Page",
  pdf: "PDF",
  doc: "DOC",
  docx: "DOCX",
  txt: "Text",
  image: "Image",
};

function scrapeStats(site: WebsiteRecord) {
  return {
    pages: site.page_count || 0,
    documents: site.document_count || 0,
    images: site.image_count || 0,
    failed: site.failed_count || 0,
  };
}

export function AdminScraper() {
  const [url, setUrl] = useState("");
  const [sites, setSites] = useState<WebsiteRecord[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [detail, setDetail] = useState<WebsiteRecord | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const rows = await api.websites();
    setSites(Array.isArray(rows) ? rows : []);
  };

  useEffect(() => {
    load().catch((err) => setError(err instanceof Error ? err.message : "Could not load websites."));
  }, []);

  useEffect(() => {
    const running = sites.some((item) => ACTIVE.has(item.status));
    if (!running) return;
    const timer = window.setInterval(() => {
      load().catch(() => undefined);
      if (activeId) {
        api.website(activeId).then(setDetail).catch(() => undefined);
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [sites, activeId]);

  const start = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = url.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError("");
    try {
      const created = await api.scrapeWebsite(trimmed);
      setUrl("");
      setActiveId(created.id);
      setDetail(created);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start scraping.");
    } finally {
      setBusy(false);
    }
  };

  const openSite = async (id: number) => {
    setActiveId(id);
    setDetail(await api.website(id));
  };

  const rescrape = async (id: number) => {
    setError("");
    const updated = await api.rescrapeWebsite(id);
    setActiveId(id);
    setDetail(updated);
    await load();
  };

  const reindex = async (id: number) => {
    setError("");
    const updated = await api.reindexWebsite(id);
    setDetail(updated);
    await load();
  };

  const remove = async (id: number) => {
    if (!confirm("Delete this website and its embeddings from the knowledge base?")) return;
    await api.deleteWebsite(id);
    if (activeId === id) {
      setActiveId(null);
      setDetail(null);
    }
    await load();
  };

  return (
    <div>
      <div className="admin-page-head">
        <div>
          <h1 className="page-title">Website scraper</h1>
          <p className="page-subtitle">
            Crawl a university website, including linked PDFs, Word files, and readable images, then include them in chatbot answers.
          </p>
        </div>
      </div>

      <div className="panel">
        <form className="toolbar scraper-form" onSubmit={(event) => void start(event)}>
          <input
            type="url"
            placeholder="Website URL, for example https://example.com"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            required
          />
          <button className="btn btn-gold" type="submit" disabled={busy || !url.trim()}>
            Start scraping
          </button>
        </form>
        {error ? <div className="error">{error}</div> : null}
      </div>

      <div className="panel">
        <h2 className="page-title" style={{ fontSize: "1.1rem" }}>
          Scraped websites
        </h2>
        <table>
          <thead>
            <tr>
              <th>Website</th>
              <th>Pages</th>
              <th>Docs</th>
              <th>Images</th>
              <th>Failed</th>
              <th>Chunks</th>
              <th>Last scraped</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sites.length === 0 ? (
              <tr>
                <td colSpan={9}>No websites scraped yet.</td>
              </tr>
            ) : (
              sites.map((site) => (
                <tr key={site.id} className={site.id === activeId ? "row-active" : ""}>
                  <td>
                    <button className="linkish" type="button" onClick={() => void openSite(site.id)}>
                      {site.title || site.domain}
                    </button>
                    <div className="muted-url">{site.seed_url}</div>
                  </td>
                  <td>{site.page_count}</td>
                  <td>{site.document_count || 0}</td>
                  <td>{site.image_count || 0}</td>
                  <td>{site.failed_count || 0}</td>
                  <td>{site.chunk_count}</td>
                  <td>{site.last_scraped_at ? new Date(site.last_scraped_at).toLocaleString() : "—"}</td>
                  <td>
                    <span className={`badge ${site.status}`}>{site.status_label}</span>
                    {site.progress_detail ? <div className="muted-url">{site.progress_detail}</div> : null}
                  </td>
                  <td className="table-actions">
                    <button className="btn btn-navy" onClick={() => void rescrape(site.id)}>
                      Re-scrape
                    </button>
                    <button className="btn" onClick={() => void reindex(site.id)}>
                      Re-index
                    </button>
                    <button className="btn btn-danger" onClick={() => void remove(site.id)}>
                      Delete
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {detail ? (
        <div className="panel">
          <h2 className="page-title" style={{ fontSize: "1.1rem" }}>
            Sources from {detail.domain}
          </h2>
          <p className="page-subtitle">{detail.status_label}. {detail.progress_detail}</p>
          {detail.error_message ? <div className="error">{detail.error_message}</div> : null}
          <div className="scrape-stats">
            <div className="scrape-stat">
              <strong>{scrapeStats(detail).pages}</strong>
              <span>Web pages processed</span>
            </div>
            <div className="scrape-stat">
              <strong>{scrapeStats(detail).documents}</strong>
              <span>Documents found / processed</span>
            </div>
            <div className="scrape-stat">
              <strong>{scrapeStats(detail).images}</strong>
              <span>Images found / processed</span>
            </div>
            <div className="scrape-stat">
              <strong>{scrapeStats(detail).failed}</strong>
              <span>Failed files</span>
            </div>
          </div>
          <table>
            <thead>
              <tr>
                <th>Title</th>
                <th>URL</th>
                <th>Type</th>
                <th>Chunks</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {(detail.pages || []).map((page) => (
                <tr key={page.id}>
                  <td>{page.title || "Untitled source"}</td>
                  <td>
                    <a href={page.source_url || page.url} target="_blank" rel="noreferrer">
                      {page.url}
                    </a>
                    {page.error_message ? <div className="muted-url">{page.error_message}</div> : null}
                  </td>
                  <td>{FILE_LABELS[page.file_type] || page.file_type}</td>
                  <td>{page.chunk_count}</td>
                  <td>
                    <span className={`badge ${page.status}`}>{page.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
