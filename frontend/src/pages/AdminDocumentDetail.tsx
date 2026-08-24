import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type DocumentRecord } from "../api";

export function AdminDocumentDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [doc, setDoc] = useState<DocumentRecord | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    if (!id) return;
    setDoc(await api.document(Number(id)));
  };

  useEffect(() => {
    load().catch((err) => setError(err instanceof Error ? err.message : "Not found"));
  }, [id]);

  if (!doc) return error ? <div className="error">{error}</div> : <p>Loading…</p>;

  return (
    <div>
      <p>
        <Link to="/admin-panel/documents">← All documents</Link>
      </p>
      <h1 className="page-title">{doc.title}</h1>
      <div className="panel">
        <p>
          <span className={`badge ${doc.status}`}>{doc.status_label}</span> · {doc.category_label} · v
          {doc.version}
        </p>
        <p>{doc.description || "No description"}</p>
        <p>
          Department: {doc.department || "—"} · Uploaded by {doc.uploaded_by_username || "unknown"} ·{" "}
          {new Date(doc.created_at).toLocaleString()}
        </p>
        <p>Indexed chunks: {doc.chunk_count}</p>
        {doc.error_message ? <div className="error">{doc.error_message}</div> : null}
        <div className="toolbar">
          <button
            className="btn btn-navy"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                setDoc(await api.reprocess(doc.id));
              } catch (err) {
                setError(err instanceof Error ? err.message : "Reprocess failed");
              } finally {
                setBusy(false);
              }
            }}
          >
            Re-process
          </button>
          <button
            className="btn btn-danger"
            onClick={async () => {
              if (!confirm("Delete this document?")) return;
              await api.deleteDocument(doc.id);
              navigate("/admin-panel/documents");
            }}
          >
            Delete
          </button>
        </div>
      </div>
      <div className="panel">
        <h2>Chunk preview</h2>
        {(doc.chunks || []).map((chunk) => (
          <div className="source-card" key={chunk.id} style={{ marginBottom: 10 }}>
            <strong>
              Chunk {chunk.chunk_index}
              {chunk.page_number ? ` · page ${chunk.page_number}` : ""}
            </strong>
            <div>{chunk.content.slice(0, 500)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
